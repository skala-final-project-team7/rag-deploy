"""답변 생성기 어댑터 검증 (Agent 통합 2/4) — answer-generation-agent ↔ RagState.

manage_generator: vendoring 한 answer-generation-agent 로직(normalize → prompt →
generate → citation_mapping → output build)을 in-process 로 호출해 답변 텍스트·
sources·used_llm 을 채우고 RagState 에 담는다. agent LLM 호출은 FakeAnswerLLMProvider
로 대체. 답변 텍스트는 검증 1단계가 인식하는 ``[#N]`` 인용 마커가 합성된다
(rag-pipeline-design.md §4.6.1).
"""

from datetime import datetime

import pytest

from answer_generation_agent.config import AnswerGenerationConfig
from answer_generation_agent.generation.answer_generation import (
    AnswerProviderError,
    FakeAnswerLLMProvider,
)
from app.query.generator import manage_generator
from app.query.verifier import verify_answer_rules
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import DocType, Intent, LlmModel, SourceType
from app.schemas.rag_state import HistoryDecision, RagState


def _make_chunk(
    *,
    chunk_id: str = "chunk-1",
    page_title: str = "운영 가이드",
    text: str = "EKS 노드 장애 대응 절차는 다음과 같다. CPU 임계 90% 초과 시 점검한다.",
    space_key: str = "CLOUD",
    attachment_filename: str | None = None,
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
        source_type=SourceType.ATTACHMENT if attachment_filename else SourceType.PAGE,
        attachment_filename=attachment_filename,
        attachment_mime="application/pdf" if attachment_filename else None,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _state(
    *,
    conversation_id: str | None = "conv-1",
    query: str = "EKS 노드 장애 대응 절차는?",
    intent: Intent | None = Intent.OPERATION_GUIDE,
    top_chunks: list[Chunk] | None = None,
    target_llm: LlmModel | None = None,
    history_decision: HistoryDecision | None = None,
    rewritten_queries: list[str] | None = None,
    rerank_scores: dict[str, float] | None = None,
) -> RagState:
    return RagState(
        query=query,
        user_id="user-1",
        conversation_id=conversation_id,
        intent=intent,
        rewritten_queries=rewritten_queries if rewritten_queries is not None else [query],
        top_chunks=top_chunks if top_chunks is not None else [_make_chunk()],
        target_llm=target_llm,
        history_decision=history_decision,
        rerank_scores=rerank_scores if rerank_scores is not None else {},
    )


def _fake_response(
    answer: str = "장애 대응 절차를 다음과 같이 안내합니다.",
    citations: list[str] | None = None,
) -> dict[str, object]:
    return {
        "answer": answer,
        "sentences": [{"text": answer, "citations": citations if citations is not None else []}],
        "unsupported_gaps": [],
    }


# --- 시그니처·기본 동작 ---


def test_empty_top_chunks_returns_empty_answer() -> None:
    # top_chunks 가 비면 stub 정합으로 빈 답변 (그래프 검색 0건 분기에서는 도달 X).
    result = manage_generator(_state(top_chunks=[]))
    assert result.answer == ""
    assert result.used_llm is LlmModel.GPT_4O
    assert result.sources == []


def test_default_fake_provider_produces_cited_answer() -> None:
    # provider=None 분기 — FakeAnswerLLMProvider 가 자동 주입되어 답변·sources 채움.
    result = manage_generator(_state())
    assert result.answer
    assert "[#1]" in result.answer  # 검증 1단계가 인식하는 인용 마커 합성 (§4.6.1)
    assert result.used_llm is LlmModel.GPT_4O
    assert len(result.sources) >= 1


def test_used_llm_prefers_state_target_llm_over_default() -> None:
    # 라우터가 정책·이력 의도에서 GPT-4o-mini 로 동적 라우팅한 경우 (§4.6.3).
    result = manage_generator(_state(target_llm=LlmModel.GPT_4O_MINI))
    assert result.used_llm is LlmModel.GPT_4O_MINI


def test_used_llm_defaults_to_gpt_4o_when_state_target_unset() -> None:
    # target_llm 미설정 시 답변 생성기 기본 모델 GPT-4o (§4.6.3).
    result = manage_generator(_state(target_llm=None))
    assert result.used_llm is LlmModel.GPT_4O


def test_state_query_is_not_mutated() -> None:
    state = _state()
    original_query = state.query
    manage_generator(state)
    assert state.query == original_query


# --- Intent → TaskPromptType 매핑 (rag-pipeline-design.md §4.6.2 / §6.6) ---


@pytest.mark.parametrize(
    "intent,expected_marker",
    [
        (Intent.INCIDENT_RESPONSE, "timeline"),
        (Intent.OPERATION_GUIDE, "step_by_step"),
        (Intent.POLICY_PROCEDURE, "evidence_first"),
        (Intent.HISTORY_LOOKUP, "history_summary"),
    ],
)
def test_intent_maps_to_task_prompt_type(intent: Intent, expected_marker: str) -> None:
    # 각 의도가 agent prompt builder 에 정확한 task_prompt_type 으로 전달되는지를
    # 검증한다. provider 가 받은 prompt 의 developer_prompt 에 task type 마커가 포함.
    provider = FakeAnswerLLMProvider(response=_fake_response())
    state = _state(intent=intent)
    manage_generator(state, provider=provider)
    assert len(provider.requests) == 1
    developer_prompt = provider.requests[0].prompt.developer_prompt
    assert expected_marker in developer_prompt


# --- [#N] 인용 마커 합성 (§4.6.1) ---


def test_citation_markers_synthesized_in_answer() -> None:
    # agent 가 sentence 별로 citations=[context_id] 를 채우면, 어댑터가 답변에
    # used_context_ids 순서(1-based)로 [#N] 마커를 합성한다.
    chunks = [
        _make_chunk(chunk_id="chunk-1", page_title="운영 가이드"),
        _make_chunk(chunk_id="chunk-2", page_title="장애 대응 가이드"),
    ]
    ctx_id_1 = "ctx-001-" + chunks[0].metadata.chunk_id[:8]
    ctx_id_2 = "ctx-002-" + chunks[1].metadata.chunk_id[:8]
    fake = FakeAnswerLLMProvider(
        response={
            "answer": "장애 대응 절차 안내. 추가 점검 사항.",
            "sentences": [
                {"text": "장애 대응 절차 안내.", "citations": [ctx_id_1]},
                {"text": "추가 점검 사항.", "citations": [ctx_id_2]},
            ],
            "unsupported_gaps": [],
        }
    )
    result = manage_generator(_state(top_chunks=chunks), provider=fake)
    assert "[#1]" in result.answer
    # 두 번째 문장이 두 번째 컨텍스트를 인용하면 [#2] 가 등장.
    assert "[#2]" in result.answer


def test_verify_answer_rules_compatibility() -> None:
    # 생성기가 만든 답변이 검증 1단계 입력으로 정상 동작하는지 회귀 보호.
    # 단순한 답변(검증 토큰 없음)이면 모든 문장이 PASS 로 떨어진다.
    fake = FakeAnswerLLMProvider(
        response={
            "answer": "장애 대응 절차를 안내합니다.",
            "sentences": [{"text": "장애 대응 절차를 안내합니다.", "citations": []}],
            "unsupported_gaps": [],
        }
    )
    state = _state()
    manage_generator(state, provider=fake)
    rule_result = verify_answer_rules(state.answer or "", state.top_chunks)
    # 답변에 검증 토큰이 없으므로 모든 문장이 PASS 통과 (suspicious 없음).
    assert not rule_result.has_suspicious_sentences()
    assert len(rule_result.passed_verifications()) >= 1


# --- Chunk → TopContext / GeneratedSource → Source 변환 ---


def test_sources_built_from_top_chunks() -> None:
    chunks = [
        _make_chunk(chunk_id="chunk-1", page_title="운영 가이드"),
        _make_chunk(
            chunk_id="chunk-2",
            page_title="첨부 자료",
            attachment_filename="manual.pdf",
        ),
    ]
    # context 2개 이상일 때는 agent fallback citation 이 적용되지 않으므로
    # (단일 context 일 때만 자동 채움), 명시적으로 첫 컨텍스트를 인용한다.
    ctx_id_1 = f"ctx-001-{chunks[0].metadata.chunk_id[:8]}"
    fake = FakeAnswerLLMProvider(
        response={
            "answer": "절차 안내.",
            "sentences": [{"text": "절차 안내.", "citations": [ctx_id_1]}],
            "unsupported_gaps": [],
        }
    )
    result = manage_generator(_state(top_chunks=chunks), provider=fake)
    assert len(result.sources) >= 1
    first = result.sources[0]
    # score 는 rerank_score(0~1) * 100 의 0~100 정수 (api-spec.md Source.score).
    assert 0 <= first.score <= 100
    # space_key 가 보존된다 (스페이스 단위 출처 카드 — 설계서 §4.8).
    assert first.space_key == "CLOUD"
    # source_type 매핑 — 첫 청크는 PAGE.
    assert first.source_type is SourceType.PAGE


def test_chunk_to_top_context_payload_uses_provided_rerank_score() -> None:
    """feature17c-3: rerank_score 가 주어지면 그대로 전달 (실제 Cross-Encoder 점수)."""
    from app.query.generator import _chunk_to_top_context_payload

    chunk = _make_chunk(chunk_id="chunk-1")
    payload = _chunk_to_top_context_payload(chunk, index=1, rerank_score=0.89)
    assert payload["rerank_score"] == 0.89


def test_chunk_to_top_context_payload_fallback_when_no_score() -> None:
    """rerank_score 가 None 이면 순서 보존용 fallback(1 - 0.001*index) — 기존 동작."""
    from app.query.generator import _chunk_to_top_context_payload

    chunk = _make_chunk(chunk_id="chunk-1")
    payload = _chunk_to_top_context_payload(chunk, index=3, rerank_score=None)
    assert payload["rerank_score"] == 1.0 - 0.001 * 3


def test_sources_reflect_real_rerank_scores() -> None:
    """feature17c-3: rerank_scores 가 있으면 Source.score 에 실제 rerank 점수가 반영된다.

    이전에는 generator 가 1-0.001*index 가짜값을 부여해 Top-1 이 항상 100 으로
    saturate 됐다. rerank_node 가 채운 실제 점수(예: T=4 의 0.89)가 도달하면
    Source.score = round(0.89*100) = 89.
    """
    chunks = [_make_chunk(chunk_id="chunk-1", page_title="운영 가이드")]
    ctx_id_1 = f"ctx-001-{chunks[0].metadata.chunk_id[:8]}"
    fake = FakeAnswerLLMProvider(
        response={
            "answer": "절차 안내.",
            "sentences": [{"text": "절차 안내.", "citations": [ctx_id_1]}],
            "unsupported_gaps": [],
        }
    )
    state = _state(top_chunks=chunks, rerank_scores={"chunk-1": 0.89})
    result = manage_generator(state, provider=fake)
    assert result.sources[0].score == 89  # round(0.89 * 100) — 가짜 100 아님


def test_attachment_source_metadata_preserved() -> None:
    # 첨부 청크의 attachment_filename / attachment_mime 가 Source 에 보존된다.
    chunks = [
        _make_chunk(
            chunk_id="chunk-1",
            page_title="첨부 자료",
            attachment_filename="manual.pdf",
        )
    ]
    fake = FakeAnswerLLMProvider(
        response={
            "answer": "절차 안내.",
            "sentences": [{"text": "절차 안내.", "citations": []}],
            "unsupported_gaps": [],
        }
    )
    result = manage_generator(_state(top_chunks=chunks), provider=fake)
    assert len(result.sources) >= 1
    src = result.sources[0]
    assert src.attachment_filename == "manual.pdf"
    assert src.attachment_mime == "application/pdf"
    assert src.source_type is SourceType.ATTACHMENT


# --- 안전 fallback (§4.6.5 정합) ---


def test_provider_failure_falls_back_safely() -> None:
    # provider 가 AnswerProviderError 를 던지면 안전 fallback (stub-like [#1] 답변).
    failing = FakeAnswerLLMProvider(
        error=AnswerProviderError(
            message="transient provider error",
            retryable=True,
            error_type="server_error",
        )
    )
    state = _state()
    result = manage_generator(state, provider=failing)
    assert result.answer is not None
    assert "[#1]" in result.answer
    # used_llm 도 채워져야 (그래프 후속 노드 — 검증·포맷터 입력 정합).
    assert result.used_llm is LlmModel.GPT_4O


def test_invalid_llm_payload_falls_back_safely() -> None:
    # answer 가 없는 잘못된 LLM 응답 → AnswerProviderError → 안전 fallback.
    invalid = FakeAnswerLLMProvider(response={"answer": "", "sentences": []})
    state = _state()
    result = manage_generator(state, provider=invalid)
    assert result.answer is not None
    assert "[#1]" in result.answer


# --- feature15: Rate Limit fallback (§4.6.5) ---


class _SequencedProvider:
    """순차 응답을 반환하는 fake provider — 첫 호출은 raise, 두 번째 호출은 정상.

    feature15 회귀용. agent ``AnswerLLMProvider`` 프로토콜에 정합 — ``generate
    _answer(request)`` 와 ``provider_name`` / ``has_credentials`` 만 충족하면 된다.
    """

    provider_name = "sequenced_fake"

    def __init__(self, responses: list[object]) -> None:
        # responses 는 Exception 또는 정상 응답 dict 의 시퀀스.
        from answer_generation_agent.generation.answer_generation import (
            FakeAnswerLLMProvider as _Fake,
        )

        self._delegates = [
            r if isinstance(r, Exception) else _Fake(response=r)  # type: ignore[arg-type]
            for r in responses
        ]
        self._call_index = 0

    def has_credentials(self) -> bool:
        return True

    def generate_answer(self, request: object) -> object:
        index = self._call_index
        self._call_index += 1
        delegate = self._delegates[index]
        if isinstance(delegate, Exception):
            raise delegate
        return delegate.generate_answer(request)  # type: ignore[attr-defined]


def test_rate_limit_error_triggers_fallback_model_retry() -> None:
    """1차 호출 RateLimitError → fallback_model 로 1회 재시도 후 정상 응답.

    feature15 회귀 보호 — 설계서 §4.6.5 정합. provider 가 첫 호출에 ``error_type=
    'rate_limit_error'`` 를 raise 하면 manage_generator 가 use_fallback_model=True
    로 재호출해 fallback_model (GPT-4o-mini) 로 답변을 생성한다.
    """
    rate_limit_error = AnswerProviderError(
        message="rate limit exceeded",
        retryable=True,
        error_type="rate_limit_error",
    )
    success_response = _fake_response(answer="장애 대응 절차를 안내합니다.")
    provider = _SequencedProvider(responses=[rate_limit_error, success_response])
    config = AnswerGenerationConfig(model="gpt-4o", fallback_model="gpt-4o-mini")
    state = _state()
    result = manage_generator(state, provider=provider, generation_config=config)
    # 1회 재시도 후 정상 답변 채워짐 (안전 fallback 의 stub-like 답변이 아님).
    assert result.answer
    assert "장애 대응 절차" in result.answer
    # provider 가 정확히 2회 호출됐는지 — 첫 호출 raise + 재시도 1회.
    assert provider._call_index == 2
    # used_llm 이 fallback model (GPT_4O_MINI) 로 정합화 (다운그레이드 인지).
    assert result.used_llm is LlmModel.GPT_4O_MINI


def test_non_rate_limit_error_does_not_trigger_fallback() -> None:
    """rate_limit_error 가 아닌 다른 AnswerProviderError 는 안전 fallback 으로 흡수.

    feature15 회귀 보호 — server_error/auth_error 등에 fallback_model 재시도는
    무의미하므로 기존 _apply_fallback (stub-like [#1] 답변) 로 떨어진다. provider
    가 1회만 호출돼야 한다 (재시도 없음).
    """
    server_error = AnswerProviderError(
        message="server error",
        retryable=True,
        error_type="server_error",
    )
    provider = _SequencedProvider(responses=[server_error, _fake_response()])
    config = AnswerGenerationConfig(model="gpt-4o", fallback_model="gpt-4o-mini")
    state = _state()
    result = manage_generator(state, provider=provider, generation_config=config)
    # provider 가 1회만 호출됐다 — fallback_model 재시도 없음.
    assert provider._call_index == 1
    # 답변은 _apply_fallback 의 stub-like [#1] 답변.
    assert result.answer is not None
    assert "[#1]" in result.answer
    # used_llm 은 정상 분기 GPT_4O (fallback 트리거 안 됨).
    assert result.used_llm is LlmModel.GPT_4O


# --- conversation_id None 시 결정론 합성 ---


def test_missing_conversation_id_synthesizes_deterministic_id() -> None:
    # SSE 라우트에서 conversation_id 가 None 으로 들어와도 안전 fallback 없이 동작.
    result = manage_generator(_state(conversation_id=None))
    assert result.answer  # 빈 답변 fallback 이 아니라 정상 생성 경로.
    assert "[#1]" in result.answer


# --- history_decision 보존 / contextualized_question 사용 ---


def test_history_decision_contextualized_question_used() -> None:
    # history_decision.contextualized_question 이 있으면 agent query 로 전달된다.
    decision = HistoryDecision(
        decision="follow_up",
        contextualized_question="EKS 노드 장애 시 자동 롤백은?",
        preserved_context={"summary": "", "entities": [], "turn_refs": []},
        reset_required=False,
        confidence=0.8,
        reason="follow_up 판정",
    )
    provider = FakeAnswerLLMProvider(response=_fake_response())
    state = _state(history_decision=decision)
    manage_generator(state, provider=provider)
    # provider 에 들어간 user_prompt 에 contextualized_question 이 포함.
    assert len(provider.requests) == 1
    user_prompt = provider.requests[0].prompt.user_prompt
    assert "EKS 노드 장애 시 자동 롤백은?" in user_prompt


# --- config 기본값 / 외부 주입 ---


def test_custom_config_max_contexts_limits_top_contexts() -> None:
    # config.max_contexts 로 agent normalization 단계에서 컨텍스트가 제한된다.
    chunks = [_make_chunk(chunk_id=f"chunk-{i}") for i in range(5)]
    provider = FakeAnswerLLMProvider(response=_fake_response())
    state = _state(top_chunks=chunks)
    manage_generator(
        state,
        provider=provider,
        generation_config=AnswerGenerationConfig(max_contexts=2),
    )
    assert len(provider.requests) == 1
    # prompt 의 context_count 가 max_contexts 로 제한된다.
    assert provider.requests[0].prompt.context_count == 2
