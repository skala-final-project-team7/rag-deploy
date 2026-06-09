"""Pipeline 노드 래퍼 테스트 — empty_retrieval / verify_pipeline / 분기 함수.

본 테스트는 그래프 조립 단계에서 추가된 Pipeline 노드 래퍼들이 RagState 계약을
올바르게 다루는지 검증한다. 외부 의존성 0 — Fake 검증 평가자만 주입한다.
"""

from datetime import datetime

from app.pipeline.nodes import (
    after_search_branch,
    empty_retrieval_node,
    verify_pipeline_node,
)
from app.query.verifier import SentenceCheck
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import DocType, Intent, LlmModel, SourceType, VerificationStatus
from app.schemas.rag_state import RagState
from app.schemas.response import Verification


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


# --- empty_retrieval_node ---


def test_empty_retrieval_node_fills_standard_message() -> None:
    """RETRIEVAL_EMPTY 표준 분기 — LLM 호출 없이 표준 응답으로 분기 (api-spec.md)."""
    state = RagState(query="q", user_id="u")
    state.intent = Intent.OPERATION_GUIDE
    state.target_llm = LlmModel.GPT_4O
    result = empty_retrieval_node(state)
    # api-spec.md "표준 분기 응답" — RETRIEVAL_EMPTY 메시지.
    assert "권한 범위" in (result.answer or "")
    assert result.sources == []
    assert result.verification == []
    assert result.top_chunks == []
    # 라우터가 정한 intent / target_llm은 보존된다.
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.used_llm is LlmModel.GPT_4O


def test_empty_retrieval_node_intent_fallback_when_unset() -> None:
    """라우터가 intent를 안 채웠으면 OPERATION_GUIDE fallback (rag-pipeline-design.md §8)."""
    state = RagState(query="q", user_id="u")
    result = empty_retrieval_node(state)
    assert result.intent is Intent.OPERATION_GUIDE
    # target_llm이 없어도 used_llm은 보조 모델로 채운다 (LLM 미호출이지만 응답 객체에 필드 필요).
    assert result.used_llm is LlmModel.GPT_4O_MINI


def test_empty_retrieval_node_returns_same_state_instance() -> None:
    state = RagState(query="q", user_id="u")
    result = empty_retrieval_node(state)
    assert result is state


# --- verify_pipeline_node ---


def _fake_supported_evaluator(
    *,
    answer: str,
    top_chunks: list[Chunk],
    suspicious_sentences: list[SentenceCheck],
) -> list[Verification]:
    del answer, top_chunks
    return [
        Verification(
            sentence_id=check.sentence_id,
            status=VerificationStatus.SUPPORTED,
            cited_chunks=list(check.cited_chunks),
        )
        for check in suspicious_sentences
    ]


def _fake_not_supported_evaluator(
    *,
    answer: str,
    top_chunks: list[Chunk],
    suspicious_sentences: list[SentenceCheck],
) -> list[Verification]:
    del answer, top_chunks
    return [
        Verification(
            sentence_id=check.sentence_id,
            status=VerificationStatus.NOT_SUPPORTED,
            cited_chunks=list(check.cited_chunks),
        )
        for check in suspicious_sentences
    ]


def test_verify_pipeline_node_all_pass_when_no_suspicious() -> None:
    """모든 문장이 1단계에서 PASS면 2단계는 호출되지 않고 PASS만 verification에 담긴다."""
    # answer의 검증 토큰("90")이 청크 텍스트("CPU 임계 90% 초과")에 정확히 매칭 → PASS.
    state = RagState(
        query="q",
        user_id="u",
        answer="[#1] CPU 임계 90% 초과 시 점검한다.",
        top_chunks=[_make_chunk()],
    )
    calls: list[int] = []

    def spy(*, answer: str, top_chunks: list[Chunk], suspicious_sentences: list[SentenceCheck]):
        calls.append(len(suspicious_sentences))
        return []

    result = verify_pipeline_node(state, llm_evaluator=spy)
    assert calls == []  # 2단계 호출 0회
    assert len(result.verification) == 1
    assert result.verification[0].status is VerificationStatus.PASS
    assert result.verification[0].sentence_id == 1


def test_verify_pipeline_node_calls_llm_for_suspicious_and_merges() -> None:
    """1단계 의심 문장이 있으면 2단계 호출 + PASS/SUPPORTED 병합 + sentence_id 정렬."""
    # 두 문장: 첫 문장은 PASS(인용·토큰 일치), 두 번째는 미인용 토큰 → suspicious.
    answer = "[#1] CPU 임계 90% 초과 시 점검한다. 별도로 메모리 한계 80% 초과를 확인해야 한다."
    state = RagState(query="q", user_id="u", answer=answer, top_chunks=[_make_chunk()])
    result = verify_pipeline_node(state, llm_evaluator=_fake_supported_evaluator)
    assert [v.sentence_id for v in result.verification] == [1, 2]
    assert result.verification[0].status is VerificationStatus.PASS
    assert result.verification[1].status is VerificationStatus.SUPPORTED


def test_verify_pipeline_node_not_supported_passthrough() -> None:
    """2단계가 NOT_SUPPORTED를 내면 verification에 그대로 담긴다 (차단 분기는 포맷터 책임)."""
    answer = "[#1] CPU 임계 90% 초과 시 점검한다. 메모리 한계 80% 초과를 확인해야 한다."
    state = RagState(query="q", user_id="u", answer=answer, top_chunks=[_make_chunk()])
    result = verify_pipeline_node(state, llm_evaluator=_fake_not_supported_evaluator)
    statuses = {v.status for v in result.verification}
    assert VerificationStatus.NOT_SUPPORTED in statuses


def test_verify_pipeline_node_empty_answer_yields_empty_verification() -> None:
    """답변이 비어있거나 None이면 verification은 빈 목록 — 검증할 문장이 없다."""
    state = RagState(query="q", user_id="u", answer="", top_chunks=[_make_chunk()])
    result = verify_pipeline_node(state, llm_evaluator=_fake_supported_evaluator)
    assert result.verification == []


def test_verify_pipeline_node_none_answer_is_safe() -> None:
    state = RagState(query="q", user_id="u", top_chunks=[_make_chunk()])
    result = verify_pipeline_node(state, llm_evaluator=_fake_supported_evaluator)
    assert result.verification == []


def test_verify_pipeline_node_returns_same_state_instance() -> None:
    state = RagState(query="q", user_id="u", answer="[#1] 90% 초과.", top_chunks=[_make_chunk()])
    result = verify_pipeline_node(state, llm_evaluator=_fake_supported_evaluator)
    assert result is state


# --- after_search_branch (그래프 conditional edges 키 분기) ---


def test_after_search_branch_returns_rerank_when_candidates_present() -> None:
    state = RagState(query="q", user_id="u", candidates=[_make_chunk()])
    assert after_search_branch(state) == "rerank"


def test_after_search_branch_returns_empty_when_candidates_missing() -> None:
    state = RagState(query="q", user_id="u")
    assert after_search_branch(state) == "empty"
