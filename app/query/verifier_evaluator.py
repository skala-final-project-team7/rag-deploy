"""답변 검증 2단계 LLM 평가자 — answer-verification-agent 통합 어댑터 [Agent].

--------------------------------------------------
작성자 : 최태성
작성목적 : Agent 담당자가 전달한 Answer Verification Agent(vendoring한
          ``answer_verification_agent`` 패키지)의 evaluator 모듈을 RAG Query
          파이프라인의 답변 검증 2단계에 통합한다. 본 저장소는 검증 1단계
          (rule-based, ``verify_answer_rules``)를 이미 Pipeline 으로 수행하고
          있으므로 agent 의 rule-based / sentence parser / overall label 집계는
          사용하지 않는다. agent 의 ``AnswerEvaluatorProvider.evaluate_sentence`` 만
          in-process 로 호출해 의심 문장을 SUPPORTED / NOT_SUPPORTED 로 판정한다
          (rag-pipeline-design.md §4.7.2, ai-agent/answer-verification-agent/
          answer-verification-agent.md).
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, Agent 통합 3/4 — manage_verifier_evaluator 어댑터
  - 2026-06-10, 코드 리뷰 재점검(A6) — provider 실패 fail-open(SUPPORTED fallback)
    경로에 warning 로그 + ``verifier_provider_failure_total`` 메트릭 추가. 종전
    주석의 "호출자가 alert 처리" 전제는 실제 호출자에 alert 가 없어 제거(본 모듈이
    직접 관측 책임을 진다).
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: answer_verification_agent 는 ai-agent 저장소에서 vendoring 한 별도 패키지
          이며 무수정 보존한다. 본 어댑터만 RAG 컨벤션을 따른다. LLM provider
          기본값은 FakeEvaluatorProvider (PoC·테스트). 운영은
          OpenAIEvaluatorProvider — agent 의 ``_default_transport`` (urllib 기반)
          가 transport=None 분기에서 OpenAI Chat Completions 를 직접 호출하므로
          본 어댑터는 transport 미주입 OK (answer-generation-agent 와 차이점).

[Agent 서브컴포넌트 미사용(설계상)·후속 이관 항목 (rag-pipeline-design.md §4.7 정합)]
  - (A) agent rule-based verifier 사용 — 본 저장소 ``verify_answer_rules`` 와 중복
        이므로 사용하지 않음. 다음 단계: 두 구현의 정합 (같은 의심 판정) 평가
        세션에서 비교.
  - (B) agent sentence parser — 본 저장소 generator 가 이미 ``[#N]`` 마커를 합성
        하므로 사용하지 않음.
  - (C) agent overall label / score 집계 — 본 저장소 ``app/query/formatter.py`` 가
        NOT_SUPPORTED 비율 기반 BLOCKED 정책을 이미 수행. agent 집계는 사용 X.
  - (D) UI warning metadata / QCA / regeneration recommendation — api-spec.md 에
        없는 확장 영역. 다음 단계: BFF/저장소 책임 확정 후.
  - (E) ``verification.note`` 답변 생성기 다운그레이드 기록 (설계서 §4.6.5) —
        답변 생성기 (B) 운영 transport 도입 후 같이.
  - (F) all-sentence evaluation mode (``evaluate_suspicious_only=False``) — 비용
        우선 정책으로 본 세션은 suspicious only.
  - (G) agent rule-based 정합 검증 — 본 저장소 1단계와 agent 1단계가 같은 의심
        판정을 내리는지 평가 세션에서 비교.
--------------------------------------------------
"""

import logging

from answer_verification_agent.config import AnswerVerificationConfig
from answer_verification_agent.evaluator.providers import (
    AnswerEvaluatorProvider,
    EvaluatorProviderError,
    FakeEvaluatorProvider,
)
from answer_verification_agent.schemas import SentenceLabel
from answer_verification_agent.verification.input_normalization import (
    NormalizedContext,
)
from answer_verification_agent.verification.suspicious_selector import (
    SuspiciousSentenceTarget,
)
from app.metrics import verifier_provider_failure_total
from app.query.verifier import SentenceCheck
from app.schemas.chunk import Chunk
from app.schemas.enums import VerificationStatus
from app.schemas.response import Verification

_LOGGER = logging.getLogger(__name__)

# agent SentenceLabel → rag VerificationStatus 매핑. 설계서 §4.7 은 PASS /
# SUPPORTED / NOT_SUPPORTED 3종만 정의 — LOW_CONFIDENCE / NOT_CHECKED 는 본
# 어댑터에서 보수적으로 NOT_SUPPORTED 로 흡수 (사용자 결정 — Plan v2 §3 LOW_
# CONFIDENCE 매핑). 환각 차단을 우선시한다 (설계서 §3 "정확성 우선" 원칙).
_LABEL_MAP: dict[SentenceLabel, VerificationStatus] = {
    SentenceLabel.SUPPORTED: VerificationStatus.SUPPORTED,
    SentenceLabel.UNSUPPORTED: VerificationStatus.NOT_SUPPORTED,
    SentenceLabel.LOW_CONFIDENCE: VerificationStatus.NOT_SUPPORTED,
    SentenceLabel.NOT_CHECKED: VerificationStatus.NOT_SUPPORTED,
}


def manage_verifier_evaluator(
    *,
    answer: str,
    top_chunks: list[Chunk],
    suspicious_sentences: list[SentenceCheck],
    provider: AnswerEvaluatorProvider | None = None,
    config: AnswerVerificationConfig | None = None,
    full_context: bool = False,
) -> list[Verification]:
    """답변 검증 2단계 LLM 평가자 [Agent] — 의심 문장을 의미적 일치로 판정한다.

    본 저장소의 ``verify_pipeline_node`` 가 1단계 규칙 매칭 결과(`SentenceCheck`)
    중 ``unverified_tokens`` 이 있는 의심 문장만 본 함수에 전달한다. 본 어댑터는
    agent ``AnswerEvaluatorProvider.evaluate_sentence`` 를 문장별로 1회씩 호출해
    SUPPORTED / NOT_SUPPORTED 판정을 받고, 본 저장소의 ``Verification`` 으로
    변환한다 (rag-pipeline-design.md §4.7.2).

    설계서 §4.7.2 의 "Top-5 청크 전체 전달" 정합은 agent prompt builder 에 위임 —
    agent 는 ``matched_context_ids + citations`` 의 cited contexts 만 prompt 에 포함
    하지만, 본 어댑터는 ``top_chunks`` 전체를 ``NormalizedContext`` 로 변환해 전달
    하므로 agent prompt builder 가 cited 만 선별한다 (False Negative 방지 정책은
    agent 책임).

    Args:
        answer: 생성기가 만든 답변 텍스트. agent evaluator 는 sentence 단위로 평가
            하므로 본 인자는 직접 사용하지 않음 (시그니처 정합 — stub 호환).
        top_chunks: Cross-Encoder Top-K 청크. ``NormalizedContext`` 로 변환되어
            agent prompt builder 에 전달된다.
        suspicious_sentences: 1단계 규칙 매칭에서 의심으로 FLAG 된 문장.
        provider: 답변 검증 평가 LLM provider. None 이면 FakeEvaluatorProvider 를
            쓴다 (PoC·테스트). 운영은 OpenAIEvaluatorProvider 를 주입한다.
        config: 답변 검증 실행 설정. None 이면 기본값 (evaluator_model=
            "configurable", temperature=0.0, timeout_seconds=30).
        full_context: feature17c-19 (opt-in, 기본 False). True 면 의심 문장의 평가 근거를
            인용 청크가 아니라 **검색된 전체 top-k** 로 한다(target.citations/matched
            _context_ids 를 전체 context_id 로 채움). 환각/차단을 "어느 retrieved 근거로도
            미지원"으로만 판정 — 진단(17c-18)에서 잔존 NOT_SUPPORTED 가 사실은 검색 근거에
            있으나 단일 청크만 인용된 citation 정밀도 문제임을 확인한 데 따른 교정. 기본
            False 는 기존 per-cited-chunk 동작 보존.

    Returns:
        ``suspicious_sentences`` 와 같은 길이·sentence_id 정합의 Verification 목록.
        provider 실패 시 stub 정합 모두 SUPPORTED 안전 fallback.
    """
    del answer  # agent evaluator 는 sentence 단위로 평가 — answer 텍스트 직접 사용 X.

    if not suspicious_sentences:
        return []

    selected_provider = provider or FakeEvaluatorProvider()
    selected_config = config or AnswerVerificationConfig()
    # config 자체는 어댑터 본체에서 직접 사용하지 않지만, 호출자가 score_threshold /
    # timeout 등을 외부에서 조정할 수 있도록 시그니처에 포함. validate 로 일관성 보호.
    selected_config.validate()

    normalized_contexts = _chunks_to_normalized_contexts(top_chunks)
    # feature17c-19 — full_context 면 전체 top-k context_id 를 평가 근거로 오버라이드.
    full_context_ids = (
        [_chunk_to_context_id(chunk, index=i) for i, chunk in enumerate(top_chunks, start=1)]
        if full_context
        else None
    )

    verifications: list[Verification] = []
    for check in suspicious_sentences:
        target = _sentence_check_to_target(
            check, top_chunks=top_chunks, context_ids_override=full_context_ids
        )
        try:
            evaluation = selected_provider.evaluate_sentence(target, normalized_contexts)
        except EvaluatorProviderError:
            # provider 실패 — stub 정합 SUPPORTED 로 fallback(회귀 테스트 정합).
            # 환각 차단을 우선시한다면 NOT_SUPPORTED 가 맞지만 정책 변경은 BFF/FE
            # 합의 필요. 대신 fail-open 이 조용히 지나가지 않도록 본 모듈이 직접
            # warning + 메트릭으로 관측한다(코드 리뷰 A6 — 운영 알림:
            # increase(verifier_provider_failure_total[5m]) > 0).
            _LOGGER.warning(
                "verifier evaluator provider failed; sentence_id=%s falls back to "
                "SUPPORTED (hallucination gate inactive for this sentence)",
                check.sentence_id,
                exc_info=True,
            )
            verifier_provider_failure_total.inc()
            verifications.append(
                Verification(
                    sentence_id=check.sentence_id,
                    status=VerificationStatus.SUPPORTED,
                    cited_chunks=list(check.cited_chunks),
                )
            )
            continue
        status = _LABEL_MAP.get(evaluation.label, VerificationStatus.NOT_SUPPORTED)
        verifications.append(
            Verification(
                sentence_id=check.sentence_id,
                status=status,
                cited_chunks=list(check.cited_chunks),
            )
        )
    return verifications


def _chunks_to_normalized_contexts(chunks: list[Chunk]) -> list[NormalizedContext]:
    """RAG Chunk 목록을 agent NormalizedContext 목록으로 변환한다.

    context_id 합성은 ``app/query/generator.py`` 와 동일 패턴 (``ctx-{index:03d}-
    {chunk_id[:8]}``) — 두 어댑터 사이의 일관성을 보장해 향후 통합 평가 세션에서
    sentence citation 추적이 가능하다.
    """
    return [
        NormalizedContext(
            context_id=_chunk_to_context_id(chunk, index=index),
            document_id=chunk.metadata.page_id,
            chunk_id=chunk.metadata.chunk_id,
            title=chunk.metadata.page_title
            or chunk.metadata.attachment_filename
            or chunk.metadata.chunk_id,
            space_key=chunk.metadata.space_key or "UNKNOWN",
            source_url=chunk.metadata.webui_link or f"chunk://{chunk.metadata.chunk_id}",
            content=chunk.text,
            score=0.0,
            rerank_score=1.0 - 0.001 * index,
            metadata={
                "page_id": chunk.metadata.page_id,
                "attachment_filename": chunk.metadata.attachment_filename,
                "source_type": chunk.metadata.source_type.value,
            },
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _sentence_check_to_target(
    check: SentenceCheck,
    *,
    top_chunks: list[Chunk],
    context_ids_override: list[str] | None = None,
) -> SuspiciousSentenceTarget:
    """본 저장소 SentenceCheck 를 agent SuspiciousSentenceTarget 으로 변환한다.

    1-based ``cited_chunks`` 의 정수 번호를 ``context_id`` 문자열로 환원한다
    (``top_chunks`` 의 동일 1-based 순서 정합).

    feature17c-19 — ``context_ids_override`` 가 주어지면(full_context 모드) 인용 청크
    대신 그 목록(전체 top-k context_id)을 citations/matched_context_ids 로 사용해
    2단계 평가 근거를 전체 검색 근거로 확장한다.
    """
    if context_ids_override is not None:
        citations = list(context_ids_override)
    else:
        citations = [
            _chunk_to_context_id(top_chunks[number - 1], index=number)
            for number in check.cited_chunks
            if 1 <= number <= len(top_chunks)
        ]
    failed_rules = ["token_overlap"] if check.unverified_tokens else []
    reasons = ["low_token_overlap"] if check.unverified_tokens else []
    return SuspiciousSentenceTarget(
        sentence_id=f"s{check.sentence_id}",
        text=check.sentence,
        score=0.0,
        preliminary_label=SentenceLabel.LOW_CONFIDENCE.value,
        reasons=reasons,
        citations=list(citations),
        matched_context_ids=list(citations),
        invalid_citations=[],
        failed_rules=failed_rules,
    )


def _chunk_to_context_id(chunk: Chunk, *, index: int) -> str:
    """``app/query/generator.py`` 와 동일한 context_id 합성 (1-based index 정합)."""
    return f"ctx-{index:03d}-{chunk.metadata.chunk_id[:8]}"


__all__ = ["manage_verifier_evaluator"]
