"""Agent stub 3종(라우터·답변 생성기·검증 2단계 LLM 평가자) 테스트.

본 테스트는 stubs가 RagState의 필드 계약을 올바르게 채우고, Agent 코드 전달 시 교체
지점이 단일 모듈에 모여 있음을 보장한다. 외부 의존성 0 — Fake/Pydantic만 사용한다.
"""

from datetime import datetime

from app.pipeline.stubs import (
    generator_stub,
    router_stub,
    verify_llm_evaluator_stub,
)
from app.query.verifier import SentenceCheck
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import (
    DocType,
    Intent,
    LlmModel,
    SourceType,
    VerificationStatus,
)
from app.schemas.rag_state import RagState


def _make_chunk(chunk_id: str = "chunk-1", page_title: str = "운영 가이드") -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id="page-1",
        page_title=page_title,
        section_header="개요",
        section_path="개요",
        chunk_index=0,
        labels=["ops"],
        doc_type=DocType.OPERATION,
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="https://confluence.example.com/page-1",
        last_modified=datetime(2026, 5, 1, 9, 0, 0),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(
        text="EKS 노드 장애 대응 절차는 다음과 같다. CPU 임계 90% 초과 시 점검한다.",
        metadata=metadata,
    )


# --- router_stub ---


def test_router_stub_fills_intent_and_pool_weights_with_operation_fallback() -> None:
    state = RagState(query="EKS 노드 장애", user_id="taesung", groups=["space:CLOUD"])
    result = router_stub(state)
    # rag-pipeline-design.md §8 — 라우터 fallback은 의도=운영가이드 + 원본 쿼리.
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.rewritten_queries == ["EKS 노드 장애"]
    assert result.target_llm is LlmModel.GPT_4O
    # 운영가이드 의도 Pool 가중치(rag-pipeline-design.md §6 4.5): 0.2 / 0.7 / 0.1.
    weights = result.pool_weights or {}
    assert weights.get("title_pool") == 0.2
    assert weights.get("content_pool") == 0.7
    assert weights.get("label_pool") == 0.1
    # metadata_filters는 fallback에서 비움 — 잘못된 라우터 출력으로 검색 망가지지 않도록.
    assert result.metadata_filters is None or result.metadata_filters == {}


def test_router_stub_preserves_existing_history_decision() -> None:
    """라우터는 history 노드 결과를 덮어쓰지 않는다."""
    from app.schemas.rag_state import HistoryDecision

    state = RagState(
        query="후속",
        user_id="taesung",
        history_decision=HistoryDecision(
            decision="follow_up", contextualized_question="EKS 후속 절차", confidence=0.9
        ),
    )
    result = router_stub(state)
    assert result.history_decision is not None
    assert result.history_decision.decision == "follow_up"


def test_router_stub_returns_same_state_instance() -> None:
    """노드 시그니처 (state) -> state — in-place mutation 정합."""
    state = RagState(query="q", user_id="u")
    result = router_stub(state)
    assert result is state


# --- generator_stub ---


def test_generator_stub_fills_answer_with_citation_marker() -> None:
    state = RagState(query="EKS 장애", user_id="taesung", top_chunks=[_make_chunk()])
    state.target_llm = LlmModel.GPT_4O
    result = generator_stub(state)
    assert result.answer is not None
    # 검증 1단계 규칙 매칭이 동작하려면 답변에 [#n] 인용 마커가 필요하다.
    assert "[#1]" in result.answer
    # target_llm 그대로 사용 — 라우터가 결정한 모델을 따른다.
    assert result.used_llm is LlmModel.GPT_4O


def test_generator_stub_defaults_used_llm_when_target_missing() -> None:
    state = RagState(query="q", user_id="u", top_chunks=[_make_chunk()])
    result = generator_stub(state)
    assert result.used_llm is LlmModel.GPT_4O  # 답변 생성기 기본 모델


def test_generator_stub_with_empty_top_chunks_produces_empty_answer() -> None:
    """top_chunks가 비어도 안전하게 처리(이 분기는 그래프에서 검색 0건 처리로 우회됨)."""
    state = RagState(query="q", user_id="u")
    result = generator_stub(state)
    assert result.answer == ""
    assert result.used_llm is LlmModel.GPT_4O


def test_generator_stub_returns_same_state_instance() -> None:
    state = RagState(query="q", user_id="u", top_chunks=[_make_chunk()])
    result = generator_stub(state)
    assert result is state


# --- verify_llm_evaluator_stub ---


def test_verify_llm_evaluator_stub_marks_all_suspicious_as_supported() -> None:
    """2단계 fake는 보수적으로 모두 SUPPORTED — Pipeline 흐름 동작 확인 목적."""
    suspicious = [
        SentenceCheck(
            sentence_id=2,
            sentence="CPU 임계 95% 초과 시 점검.",
            cited_chunks=[1],
            unverified_tokens=["95"],
        ),
        SentenceCheck(
            sentence_id=4,
            sentence="복구 시간 12분.",
            cited_chunks=[1],
            unverified_tokens=["12"],
        ),
    ]
    verifications = verify_llm_evaluator_stub(
        answer="...", top_chunks=[_make_chunk()], suspicious_sentences=suspicious
    )
    assert [v.status for v in verifications] == [
        VerificationStatus.SUPPORTED,
        VerificationStatus.SUPPORTED,
    ]
    assert [v.sentence_id for v in verifications] == [2, 4]
    assert verifications[0].cited_chunks == [1]


def test_verify_llm_evaluator_stub_empty_input_returns_empty() -> None:
    result = verify_llm_evaluator_stub(answer="", top_chunks=[], suspicious_sentences=[])
    assert result == []
