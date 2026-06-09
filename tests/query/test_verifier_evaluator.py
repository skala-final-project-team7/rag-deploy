"""답변 검증 2단계 LLM 평가자 어댑터 검증 (Agent 통합 3/4).

manage_verifier_evaluator: vendoring 한 answer-verification-agent 의 evaluator 모듈을
in-process 로 호출해 의심 문장을 SUPPORTED / NOT_SUPPORTED 로 판정한다. 1단계 규칙
매칭(`verify_answer_rules`, Pipeline)이 본 어댑터 호출 전에 수행되어 SentenceCheck
list 를 채워주는 흐름이며, 본 어댑터는 stub 시그니처와 동일한 keyword 전용 호출
계약을 유지한다 (rag-pipeline-design.md §4.7.2).
"""

from datetime import datetime

import pytest

from answer_verification_agent.config import AnswerVerificationConfig
from answer_verification_agent.evaluator.providers import (
    EvaluatorProviderError,
    FakeEvaluatorProvider,
)
from app.query.verifier import SentenceCheck
from app.query.verifier_evaluator import manage_verifier_evaluator
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import DocType, SourceType, VerificationStatus


def _make_chunk(
    *,
    chunk_id: str = "chunk-1",
    page_title: str = "운영 가이드",
    text: str = "EKS 노드 장애 대응 절차는 다음과 같다.",
    space_key: str = "CLOUD",
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id="page-1",
        page_title=page_title,
        section_header="개요",
        section_path="개요",
        chunk_index=0,
        labels=["ops"],
        doc_type=DocType.OPERATION,
        space_key=space_key,
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="https://confluence.example.com/page-1",
        last_modified=datetime(2026, 5, 1, 9, 0, 0),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _check(
    *,
    sentence_id: int = 1,
    sentence: str = "장애 대응 절차를 안내합니다.",
    cited_chunks: list[int] | None = None,
    unverified_tokens: list[str] | None = None,
) -> SentenceCheck:
    return SentenceCheck(
        sentence_id=sentence_id,
        sentence=sentence,
        cited_chunks=cited_chunks if cited_chunks is not None else [1],
        unverified_tokens=unverified_tokens if unverified_tokens is not None else ["v1.29"],
    )


def _context_id(chunk: Chunk, *, index: int = 1) -> str:
    """app/query/verifier_evaluator.py 의 _chunk_to_context_id 정합."""
    return f"ctx-{index:03d}-{chunk.metadata.chunk_id[:8]}"


# --- 시그니처·기본 동작 ---


def test_empty_suspicious_sentences_returns_empty_list() -> None:
    # suspicious 비면 provider 호출 자체 없이 빈 list 반환 (stub 정합).
    result = manage_verifier_evaluator(
        answer="장애 대응 절차를 안내합니다.",
        top_chunks=[_make_chunk()],
        suspicious_sentences=[],
    )
    assert result == []


def test_default_fake_provider_returns_low_confidence_mapped_to_not_supported() -> None:
    # provider=None 분기 — FakeEvaluatorProvider 가 scripted 없이 호출되면
    # LOW_CONFIDENCE 기본 응답. LOW_CONFIDENCE → NOT_SUPPORTED 보수적 매핑 (사용자 결정).
    result = manage_verifier_evaluator(
        answer="장애 대응 절차를 안내합니다.",
        top_chunks=[_make_chunk()],
        suspicious_sentences=[_check()],
    )
    assert len(result) == 1
    assert result[0].sentence_id == 1
    assert result[0].status is VerificationStatus.NOT_SUPPORTED
    assert result[0].cited_chunks == [1]


def test_supported_label_maps_to_supported() -> None:
    chunk = _make_chunk()
    provider = FakeEvaluatorProvider(
        scripted_results={
            "s1": {
                "label": "SUPPORTED",
                "score": 0.95,
                "reason": "Context fully supports the sentence.",
                "unsupported_claims": [],
            }
        }
    )
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[chunk],
        suspicious_sentences=[_check()],
        provider=provider,
    )
    assert len(result) == 1
    assert result[0].status is VerificationStatus.SUPPORTED


def test_unsupported_label_maps_to_not_supported() -> None:
    chunk = _make_chunk()
    provider = FakeEvaluatorProvider(
        scripted_results={
            "s1": {
                "label": "UNSUPPORTED",
                "score": 0.1,
                "reason": "Context does not mention this claim.",
                "unsupported_claims": ["v1.29 rollback"],
            }
        }
    )
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[chunk],
        suspicious_sentences=[_check()],
        provider=provider,
    )
    assert len(result) == 1
    assert result[0].status is VerificationStatus.NOT_SUPPORTED


def test_low_confidence_maps_to_not_supported_conservative() -> None:
    # Plan v2 LOW_CONFIDENCE 매핑 — 보수적 NOT_SUPPORTED. 환각 차단 우선.
    chunk = _make_chunk()
    provider = FakeEvaluatorProvider(
        scripted_results={
            "s1": {
                "label": "LOW_CONFIDENCE",
                "score": 0.5,
                "reason": "Partial overlap but evidence is ambiguous.",
                "unsupported_claims": [],
            }
        }
    )
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[chunk],
        suspicious_sentences=[_check()],
        provider=provider,
    )
    assert result[0].status is VerificationStatus.NOT_SUPPORTED


# --- 다중 문장 / N회 호출 ---


def test_multiple_suspicious_sentences_invoke_evaluator_n_times() -> None:
    # 2개 문장 → evaluator 2회 호출, 각 sentence_id 정합으로 매핑.
    chunk = _make_chunk()
    provider = FakeEvaluatorProvider(
        scripted_results={
            "s1": {
                "label": "SUPPORTED",
                "score": 0.9,
                "reason": "supported",
                "unsupported_claims": [],
            },
            "s2": {
                "label": "UNSUPPORTED",
                "score": 0.2,
                "reason": "unsupported",
                "unsupported_claims": [],
            },
        }
    )
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[chunk],
        suspicious_sentences=[
            _check(sentence_id=1, sentence="문장 1"),
            _check(sentence_id=2, sentence="문장 2"),
        ],
        provider=provider,
    )
    assert [v.sentence_id for v in result] == [1, 2]
    assert result[0].status is VerificationStatus.SUPPORTED
    assert result[1].status is VerificationStatus.NOT_SUPPORTED


# --- cited_chunks 보존 / context_id 정합 ---


def test_cited_chunks_preserved_in_verification_output() -> None:
    # cited_chunks 의 1-based 정수는 Verification 에 그대로 보존된다 (api-spec.md 정합).
    chunks = [_make_chunk(chunk_id="chunk-1"), _make_chunk(chunk_id="chunk-2")]
    provider = FakeEvaluatorProvider(
        scripted_results={
            "s1": {
                "label": "SUPPORTED",
                "score": 0.9,
                "reason": "ok",
                "unsupported_claims": [],
            }
        }
    )
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=chunks,
        suspicious_sentences=[_check(cited_chunks=[1, 2])],
        provider=provider,
    )
    assert result[0].cited_chunks == [1, 2]


# --- 안전 fallback ---


class _FailingProvider:
    """EvaluatorProviderError 를 던지는 provider — 안전 fallback 회귀 보호."""

    def evaluate_sentence(self, target, contexts):  # noqa: ANN001 — protocol shape
        raise EvaluatorProviderError(
            "transient",
            error_type="timeout",
            retryable=True,
        )


def test_provider_failure_falls_back_to_supported() -> None:
    # provider 가 EvaluatorProviderError 를 던지면 stub 정합 SUPPORTED 로 흡수.
    # 환각 차단 대신 stub 의 기본 동작(모두 SUPPORTED)을 유지 — provider 실패 자체는
    # 호출자(verify_pipeline_node)가 alert 로 별도 처리.
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[_make_chunk()],
        suspicious_sentences=[_check()],
        provider=_FailingProvider(),
    )
    assert len(result) == 1
    assert result[0].status is VerificationStatus.SUPPORTED


# --- top_chunks 비었을 때 ---


def test_empty_top_chunks_still_evaluates() -> None:
    # top_chunks 가 비어도 evaluator 호출은 시도된다 (agent prompt builder 가
    # "No valid cited context" 안내문으로 prompt 채움). 기본 fake = LOW_CONFIDENCE.
    result = manage_verifier_evaluator(
        answer="...",
        top_chunks=[],
        suspicious_sentences=[_check(cited_chunks=[])],
    )
    assert len(result) == 1
    assert result[0].status is VerificationStatus.NOT_SUPPORTED


# --- 시그니처 정합 (keyword 전용) ---


def test_signature_matches_stub_keyword_only() -> None:
    # 본 어댑터는 stub 과 동일한 keyword 전용 시그니처를 유지한다.
    # positional 호출은 TypeError 가 떨어진다 (verify_pipeline_node 정합 회귀 보호).
    with pytest.raises(TypeError):
        manage_verifier_evaluator(
            "...",  # type: ignore[misc]
            [_make_chunk()],
            [_check()],
        )


# --- config 외부 주입 / validate ---


def test_custom_config_is_validated() -> None:
    # config 유효성 검증은 어댑터 호출 시점에 강제된다.
    invalid_config = AnswerVerificationConfig.__new__(AnswerVerificationConfig)
    invalid_config.evaluator_model = ""
    invalid_config.temperature = 0.0
    invalid_config.timeout_seconds = 30
    invalid_config.max_retries = 2
    invalid_config.evaluate_suspicious_only = True
    invalid_config.min_overall_score = 0.7
    invalid_config.min_sentence_score = 0.6
    invalid_config.qca_output_enabled = True
    invalid_config.openai_api_key = None
    with pytest.raises(ValueError):
        manage_verifier_evaluator(
            answer="...",
            top_chunks=[_make_chunk()],
            suspicious_sentences=[_check()],
            config=invalid_config,
        )


# --- feature17c-19 full_context grounding 토글 ---


class _RecordingProvider:
    """evaluate_sentence 가 받은 target 을 기록하는 stub provider (full_context 회귀)."""

    def __init__(self, label: str = "SUPPORTED") -> None:
        self.label = label
        self.targets: list[object] = []

    def evaluate_sentence(self, target, normalized_contexts):  # noqa: ANN001 — protocol shape
        from answer_verification_agent.evaluator.providers import SentenceEvaluation
        from answer_verification_agent.schemas import SentenceLabel

        self.targets.append(target)
        return SentenceEvaluation(
            sentence_id=target.sentence_id,
            label=SentenceLabel(self.label),
            score=0.9,
            reason="recorded",
        )


def test_full_context_false_uses_only_cited_chunks() -> None:
    """기본(full_context=False)은 인용 청크 context_id 만 target.citations 로 쓴다."""
    chunks = [_make_chunk(chunk_id="c1"), _make_chunk(chunk_id="c2"), _make_chunk(chunk_id="c3")]
    provider = _RecordingProvider()
    manage_verifier_evaluator(
        answer="...",
        top_chunks=chunks,
        suspicious_sentences=[_check(cited_chunks=[1])],
        provider=provider,
    )
    target = provider.targets[0]
    assert target.citations == [_context_id(chunks[0], index=1)]


def test_full_context_true_uses_all_topk_context_ids() -> None:
    """full_context=True 면 인용과 무관하게 전체 top-k context_id 를 평가 근거로 쓴다."""
    chunks = [_make_chunk(chunk_id="c1"), _make_chunk(chunk_id="c2"), _make_chunk(chunk_id="c3")]
    provider = _RecordingProvider()
    manage_verifier_evaluator(
        answer="...",
        top_chunks=chunks,
        suspicious_sentences=[_check(cited_chunks=[1])],
        provider=provider,
        full_context=True,
    )
    target = provider.targets[0]
    expected = [_context_id(c, index=i) for i, c in enumerate(chunks, start=1)]
    assert target.citations == expected
    assert target.matched_context_ids == expected
