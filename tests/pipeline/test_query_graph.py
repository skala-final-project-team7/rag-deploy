"""Query LangGraph end-to-end 통합 테스트 — :memory: Qdrant + Fake everything.

본 테스트는 feature11 통합의 핵심 — Pipeline 노드(완료) + Agent stub 3종이 LangGraph
StateGraph로 끝-끝 동작함을 외부 컨테이너·모델 없이 검증한다. api-spec.md의 표준 분기
응답(RETRIEVAL_EMPTY / LOW_CONFIDENCE / VERIFICATION_BLOCKED)이 그래프 흐름과 포맷터
연동으로 올바르게 산출되는지가 검증 초점이다.
"""

import warnings
from datetime import datetime

import pytest

from app.config import Settings
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder
from app.ingestion.indexer import index_chunks
from app.pipeline.nodes import RETRIEVAL_EMPTY_ANSWER
from app.pipeline.query_graph import (
    QueryGraphDeps,
    build_query_graph,
    build_query_graph_for_streaming,
    run_query,
    run_query_with_state,
)
from app.pipeline.stubs import generator_stub, verify_llm_evaluator_stub
from app.query.acl import ACLViolationError, build_acl_filter
from app.query.formatter import BLOCKED_ANSWER_MESSAGE
from app.query.reranker.base import CrossEncoderReranker, FakeCrossEncoderReranker
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import Intent, LlmModel, SourceType, VerificationStatus
from app.schemas.rag_state import RagState
from app.schemas.response import Verification
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_client import QdrantPoolStore

# Qdrant `:memory:` 모드의 payload-index noop 경고 차단.
warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 픽스처·헬퍼 ---


def _settings() -> Settings:
    return Settings(_env_file=None)


def _chunk(
    *,
    chunk_id: str,
    page_id: str = "P1",
    chunk_index: int = 0,
    text: str = "alpha bravo charlie",
    allowed_groups: list[str] | None = None,
    page_title: str = "EKS 운영 가이드",
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id=page_id,
        page_title=page_title,
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=chunk_index,
        labels=["eks", "운영"],
        doc_type="operation",
        space_key="CLOUD",
        allowed_groups=allowed_groups or ["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


@pytest.fixture()
def dense() -> FakeDenseEmbedder:
    return FakeDenseEmbedder(dimension=8)


@pytest.fixture()
def sparse() -> FakeSparseEmbedder:
    return FakeSparseEmbedder()


@pytest.fixture()
def populated_store(dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder) -> QdrantPoolStore:
    """3 청크를 미리 인덱싱한 `:memory:` Qdrant 저장소."""
    store = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    store.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, chunk_index=0, text="alpha bravo charlie"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="bravo delta echo"),
        _chunk(chunk_id="c" * 40, chunk_index=2, text="charlie echo foxtrot"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=FakeEmbeddingCache(),
    )
    return store


@pytest.fixture()
def empty_store(dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder) -> QdrantPoolStore:
    """청크 0건의 `:memory:` Qdrant 저장소 — RETRIEVAL_EMPTY 분기 검증용."""
    del dense, sparse
    store = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    store.bootstrap_collections()
    return store


def _initial_state(
    *,
    query: str = "alpha",
    groups: list[str] | None = None,
    acl_filter_override: object = ...,
) -> RagState:
    actual_groups = groups if groups is not None else ["space:CLOUD"]
    if acl_filter_override is ...:
        acl_filter = build_acl_filter("taesung", actual_groups)
    else:
        acl_filter = acl_filter_override  # type: ignore[assignment]
    return RagState(
        query=query,
        user_id="taesung",
        groups=actual_groups,
        acl_filter=acl_filter,  # type: ignore[arg-type]
    )


def _deps(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    store: QdrantPoolStore,
    *,
    reranker: CrossEncoderReranker | None = None,
    **overrides: object,
) -> QueryGraphDeps:
    return QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=reranker or FakeCrossEncoderReranker(),
        **overrides,  # type: ignore[arg-type]
    )


# --- 노드명 / state 필드 네임스페이스 회귀 보호 ---


def test_build_query_graph_compiles_without_node_state_key_collision(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """LangGraph 1.x는 노드명과 state field가 네임스페이스를 공유한다.

    히스토리 관리자 노드명을 RagState.history 필드와 다르게(`manage_history`) 두지
    않으면 ``ValueError: 'history' is already being used as a state key`` 가 발생한다.
    본 테스트는 그 회귀를 막는다 (실 환경 pytest로 발견된 버그, 2026-05-18).
    """
    # 컴파일 자체가 통과하면 노드명·필드명 충돌이 없음 — 별도 단언 불필요.
    build_query_graph(_deps(dense, sparse, populated_store))


# --- feature14: SSE streaming 용 partial graph 회귀 ---


def test_build_query_graph_for_streaming_populates_top_chunks_and_sources(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """streaming 용 partial graph 는 rerank 까지만 실행하고 종료한다.

    feature14 회귀 — generate/verify 노드 미포함 + top_chunks/sources 가 채워진 채
    종료. answer/verification 은 비어 있어야 한다 (라우트가 사후 streaming + 검증
    을 직접 수행하는 흐름).
    """
    graph = build_query_graph_for_streaming(_deps(dense, sparse, populated_store))
    result_dict = graph.invoke(_initial_state(query="alpha"))
    state = RagState.model_validate(result_dict)
    # rerank 결과가 채워져야 한다.
    assert state.top_chunks
    assert state.sources
    # generate/verify 가 실행되지 않았으므로 answer/verification 은 비어 있다.
    assert not state.answer
    assert not state.verification


def test_build_query_graph_for_streaming_handles_empty_retrieval(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    empty_store: QdrantPoolStore,
) -> None:
    """streaming 용 partial graph 도 검색 0건 분기에서 RETRIEVAL_EMPTY 표준 응답.

    rerank 노드를 건너뛰고 empty_retrieval 노드가 answer 를 표준 메시지로 채운다.
    라우트는 top_chunks 가 비었음을 확인하고 5 이벤트 시퀀스 그대로 송신.
    """
    graph = build_query_graph_for_streaming(_deps(dense, sparse, empty_store))
    result_dict = graph.invoke(_initial_state(query="anything"))
    state = RagState.model_validate(result_dict)
    assert state.top_chunks == []
    assert state.answer == RETRIEVAL_EMPTY_ANSWER


# --- 정상 흐름 ---


def test_run_query_normal_flow_populates_sources_and_verification(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """ACL 매칭 청크가 있으면 sources / verification이 채워지고 정상 응답이 나온다.

    Agent 통합 3/4 + LangGraph generation_config rename (2026-05-19) 정합 —
    generator_node / verify_llm_evaluator 모두 stub 명시 주입으로 PoC 정상 흐름
    시나리오를 검증한다. (default 가 agent 어댑터로 바뀌었으므로 stub 의 [#1]
    마커·all SUPPORTED 동작에 의존하는 본 회귀 테스트는 stub 명시 주입 패턴 정합.)
    """
    graph = build_query_graph(
        _deps(
            dense,
            sparse,
            populated_store,
            generator_node=generator_stub,
            verify_llm_evaluator=verify_llm_evaluator_stub,
        )
    )
    response = run_query(_initial_state(query="alpha"), graph=graph)
    # 답변은 generator_stub이 [#1] 마커를 단 stub 답변.
    assert response.answer
    # api-spec.md 정합 — 출처 카드는 1건 이상, 모두 score 0~100 int.
    assert len(response.sources) >= 1
    assert all(0 <= s.score <= 100 for s in response.sources)
    # latency_ms 는 wrapper에서 측정 — 음수가 아닐 것.
    assert response.latency_ms >= 0
    # 답변 차단 메시지가 아니어야 한다 (정상 흐름).
    assert response.answer != BLOCKED_ANSWER_MESSAGE


def test_run_query_with_state_exposes_final_state(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """run_query_with_state 는 (response, 최종 RagState) 를 돌려주고, state 는 top_chunks/
    answer 등 내부 상태를 노출한다 — 평가의 faithfulness 재검증에 필요(feature17c-26)."""
    graph = build_query_graph(
        _deps(
            dense,
            sparse,
            populated_store,
            generator_node=generator_stub,
            verify_llm_evaluator=verify_llm_evaluator_stub,
        )
    )
    response, final = run_query_with_state(_initial_state(query="alpha"), graph=graph)
    # response 는 run_query 와 동일한 QueryResponse.
    assert response.answer and response.latency_ms >= 0
    # 최종 state 는 top_chunks(검색·재순위 결과)와 원본 answer 를 노출한다.
    assert isinstance(final, RagState)
    assert len(final.top_chunks) >= 1
    assert final.answer
    # run_query 와 동일 답변(델리게이션) — 회귀.
    assert run_query(_initial_state(query="alpha"), graph=graph).answer == response.answer


def test_run_query_uses_router_stub_intent_and_target_llm(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """라우터 stub은 운영가이드 의도 + GPT-4o target_llm을 fallback으로 채운다."""
    graph = build_query_graph(_deps(dense, sparse, populated_store))
    response = run_query(_initial_state(), graph=graph)
    assert response.intent is Intent.OPERATION_GUIDE
    assert response.used_llm is LlmModel.GPT_4O


# --- RETRIEVAL_EMPTY ---


def test_run_query_empty_retrieval_returns_standard_message(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    empty_store: QdrantPoolStore,
) -> None:
    """청크 0건이면 LLM 미호출 표준 응답으로 분기 (api-spec.md RETRIEVAL_EMPTY)."""
    graph = build_query_graph(_deps(dense, sparse, empty_store))
    response = run_query(_initial_state(), graph=graph)
    assert response.answer == RETRIEVAL_EMPTY_ANSWER
    assert response.sources == []
    assert response.verification == []
    # 출처가 없으면 포맷터의 _is_low_confidence가 True → feedback_enabled=False.
    assert response.feedback_enabled is False


def test_run_query_acl_mismatch_yields_empty_retrieval(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """ACL 그룹이 일치하지 않으면 검색 결과 0건 → RETRIEVAL_EMPTY 분기."""
    graph = build_query_graph(_deps(dense, sparse, populated_store))
    response = run_query(_initial_state(query="alpha", groups=["space:OTHER"]), graph=graph)
    assert response.answer == RETRIEVAL_EMPTY_ANSWER
    assert response.sources == []


# --- 저신뢰 분기 ---


class _AlwaysLowReranker(CrossEncoderReranker):
    """모든 (query, passage) 쌍에 0.1 점수를 부여하는 reranker — 저신뢰 분기 시뮬."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        del query
        return [0.1 for _ in passages]


def test_run_query_low_confidence_sets_feedback_enabled_false(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """Cross-Encoder 최고 점수가 LOW_CONFIDENCE_SCORE(20) 미만이면 feedback_enabled=False.

    Agent 통합 3/4 정합 — 저신뢰 시나리오의 본래 의도는 LOW_CONFIDENCE 분기
    (낮은 score → feedback_enabled=False)이지 BLOCKED 분기가 아니므로 stub 의
    "all SUPPORTED" 검증 결과를 명시 주입해 BLOCKED 정책이 trigger 되지 않도록
    한다.
    """
    graph = build_query_graph(
        _deps(
            dense,
            sparse,
            populated_store,
            reranker=_AlwaysLowReranker(),
            verify_llm_evaluator=verify_llm_evaluator_stub,
        )
    )
    response = run_query(_initial_state(), graph=graph)
    # raw 0.1 → Source.score = round(0.1 * 100) = 10 < 20.
    assert all(s.score < 20 for s in response.sources)
    assert response.feedback_enabled is False
    # 답변 자체는 차단되지 않고 '참고용'으로 유지.
    assert response.answer != BLOCKED_ANSWER_MESSAGE


# --- 검증 차단 분기 ---


def _generator_with_suspicious(state: RagState) -> RagState:
    """모든 문장이 1단계에서 suspicious가 되도록 미인용 수치를 포함한 stub 답변."""
    # 인용 청크 텍스트에 없는 수치 "9999"를 포함 → 1단계에서 검증 토큰 불일치 → suspicious.
    state.answer = "[#1] 매우 중요한 수치 9999가 명시되어 있습니다."
    state.used_llm = state.target_llm or LlmModel.GPT_4O
    return state


def _evaluator_all_not_supported(
    *,
    answer: str,
    top_chunks: list,
    suspicious_sentences: list,
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


def test_run_query_verification_block_replaces_answer(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """NOT_SUPPORTED 비율 > 50%면 답변을 BLOCKED_ANSWER_MESSAGE로 교체 (api-spec.md)."""
    graph = build_query_graph(
        _deps(
            dense,
            sparse,
            populated_store,
            generator_node=_generator_with_suspicious,
            verify_llm_evaluator=_evaluator_all_not_supported,
        )
    )
    response = run_query(_initial_state(), graph=graph)
    assert response.answer == BLOCKED_ANSWER_MESSAGE
    assert response.feedback_enabled is False
    # 검증 결과는 NOT_SUPPORTED 포함.
    assert any(v.status is VerificationStatus.NOT_SUPPORTED for v in response.verification)


# --- ACL 미주입 ---


def test_run_query_missing_acl_filter_raises(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """acl_filter=None으로 진입하면 hybrid_search 노드의 @enforce_acl이 거부한다."""
    graph = build_query_graph(_deps(dense, sparse, populated_store))
    state = _initial_state(acl_filter_override=None)
    with pytest.raises(ACLViolationError):
        run_query(state, graph=graph)


def test_run_query_empty_acl_filter_raises(
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    populated_store: QdrantPoolStore,
) -> None:
    """무효 ACL 필터(빈 dict)도 거부된다 — should 절 누락."""
    graph = build_query_graph(_deps(dense, sparse, populated_store))
    state = _initial_state(acl_filter_override={})
    with pytest.raises(ACLViolationError):
        run_query(state, graph=graph)
