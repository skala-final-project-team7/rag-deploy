"""Hybrid Search 노드 검증 (feature9-B-2).

`:memory:` Qdrant + FakeDenseEmbedder + FakeSparseEmbedder 조합으로 hybrid_search 노드의
끝-끝 흐름을 통합 검증한다. ACL 강제 / multi-query / pool_weights 분기 /
metadata_filters / Chunk 재구성 / 0건 처리까지 모두 외부 컨테이너·모델 없이 검증.
"""

import warnings
from datetime import datetime

import pytest

from app.config import Settings
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder
from app.ingestion.indexer import index_chunks
from app.query.acl import ACLViolationError, build_acl_filter
from app.query.search_node import hybrid_search
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import SourceType
from app.schemas.rag_state import RagState
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_client import QdrantPoolStore

# 로컬 :memory: payload 인덱스 noop 경고 차단.
warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 픽스처·헬퍼 ---


def _settings() -> Settings:
    return Settings(_env_file=None)


def _chunk(
    *,
    chunk_id: str,
    page_id: str = "P1",
    chunk_index: int = 0,
    text: str = "alpha",
    allowed_groups: list[str] | None = None,
    allowed_users: list[str] | None = None,
    doc_type: str = "operation",
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id=page_id,
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=chunk_index,
        labels=["eks", "운영"],
        doc_type=doc_type,
        space_key="CLOUD",
        allowed_groups=allowed_groups or ["space:CLOUD"],
        allowed_users=allowed_users or [],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _acl_for_cloud() -> dict[str, list[dict[str, object]]]:
    return {"should": [{"key": "allowed_groups", "match": {"any": ["space:CLOUD"]}}]}


@pytest.fixture()
def dense() -> FakeDenseEmbedder:
    return FakeDenseEmbedder(dimension=8)


@pytest.fixture()
def sparse() -> FakeSparseEmbedder:
    return FakeSparseEmbedder()


@pytest.fixture()
def store(dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder) -> QdrantPoolStore:
    """3 청크가 미리 인덱싱된 :memory: 저장소."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, chunk_index=0, text="alpha"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="beta"),
        _chunk(chunk_id="c" * 40, chunk_index=2, text="gamma"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )
    return s


# sentinel — 호출자가 명시 None을 넘긴 경우와 default 적용 경우를 구분한다.
_UNSET: object = object()


def _make_state(
    *,
    query: str = "alpha",
    acl_filter: object = _UNSET,
    rewritten_queries: list[str] | None = None,
    pool_weights: dict[str, float] | None = None,
    metadata_filters: dict[str, object] | None = None,
) -> RagState:
    return RagState(
        query=query,
        user_id="user-test",
        acl_filter=_acl_for_cloud() if acl_filter is _UNSET else acl_filter,  # type: ignore[arg-type]
        rewritten_queries=rewritten_queries or [],
        pool_weights=pool_weights,
        metadata_filters=metadata_filters,
    )


# --- 단일 query 정상 동작 ---


def test_hybrid_search_populates_candidates(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    state = _make_state(query="alpha")
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)

    assert result is state  # in-place mutation
    assert len(result.candidates) > 0
    candidate_ids = {chunk.metadata.chunk_id for chunk in result.candidates}
    # 모든 후보가 인덱스된 3개 청크 중 하나여야 함
    assert candidate_ids <= {"a" * 40, "b" * 40, "c" * 40}


def test_hybrid_search_returns_chunks_with_reconstructed_metadata(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    state = _make_state(query="alpha")
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)

    candidate = result.candidates[0]
    # payload → ChunkMetadata 재구성이 정상적으로 동작
    assert candidate.metadata.page_id == "P1"
    assert candidate.metadata.page_title == "EKS 운영 가이드"
    assert candidate.metadata.section_header == "개요"
    assert candidate.metadata.space_key == "CLOUD"
    assert candidate.metadata.source_type is SourceType.PAGE
    assert candidate.metadata.doc_type.value == "operation"
    # text는 payload의 풀 텍스트 text (feature17c-7). 픽스처 본문이 짧아 프리뷰와 동일.
    assert candidate.text in {"alpha", "beta", "gamma"}
    # token_count는 5-A 후속(2026-05-18)에서 payload에 동봉 — 인덱싱한 청크의
    # token_count(120)가 재구성 후에도 보존되어야 한다.
    assert candidate.metadata.token_count == 120


def test_hybrid_search_top_k_limit(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    state = _make_state(query="alpha")
    result = hybrid_search(
        state, dense_embedder=dense, sparse_embedder=sparse, store=store, top_k=2
    )
    assert len(result.candidates) <= 2


# --- ACL ---


def test_hybrid_search_rejects_when_acl_filter_is_none(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    state = _make_state(query="alpha", acl_filter=None)
    with pytest.raises(ACLViolationError):
        hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)


def test_hybrid_search_rejects_when_acl_filter_is_empty(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    # _is_valid_acl_filter는 should 절 구조까지 검사 — 빈 dict는 무효
    state = _make_state(query="alpha", acl_filter={})
    with pytest.raises(ACLViolationError):
        hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)


def test_hybrid_search_filters_out_other_groups(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    # CCC 그룹 청크와 CLOUD 그룹 청크 혼합
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", allowed_groups=["space:CLOUD"]),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", allowed_groups=["space:CCC"]),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    state = _make_state(query="alpha", acl_filter=_acl_for_cloud())
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40}


def test_hybrid_search_enforces_per_user_isolation(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    # 끝-끝 증명: build_acl_filter 가 만든 실제 운영 ACL 필터가 hybrid_search 노드를 통과해
    # 같은 스페이스 안에서도 페이지(allowed_users) 단위로 사용자를 격리한다. 검색 측 코드
    # 변경 없이 per-user 권한이 동작함을 보장한다(db-schema §1.4 대안 B 회귀 가드).
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(
            chunk_id="a" * 40,
            page_id="PAGE-ALICE",
            text="alpha",
            allowed_groups=["space:CLOUD"],
            allowed_users=["alice"],
        ),
        _chunk(
            chunk_id="b" * 40,
            chunk_index=1,
            page_id="PAGE-BOB",
            text="alpha",
            allowed_groups=["space:CLOUD"],
            allowed_users=["bob"],
        ),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"PAGE-ALICE": 1, "PAGE-BOB": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    # alice 는 space:CLOUD 그룹 없이 user_id 로만 매칭 → 자신 페이지만 후보로 올라온다.
    alice_state = _make_state(query="alpha", acl_filter=build_acl_filter("alice", []))
    alice_result = hybrid_search(alice_state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in alice_result.candidates} == {"a" * 40}

    bob_state = _make_state(query="alpha", acl_filter=build_acl_filter("bob", []))
    bob_result = hybrid_search(bob_state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in bob_result.candidates} == {"b" * 40}


# --- 빈 결과 ---


def test_hybrid_search_returns_empty_candidates_when_acl_matches_nothing(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    nonexistent_acl: dict[str, list[dict[str, object]]] = {
        "should": [{"key": "allowed_groups", "match": {"any": ["space:NONEXIST"]}}]
    }
    state = _make_state(query="alpha", acl_filter=nonexistent_acl)
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    assert result.candidates == []


# --- multi-query (rewritten_queries) ---


def test_hybrid_search_uses_rewritten_queries_when_present(
    sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """rewritten_queries가 있으면 모든 query에 대해 임베딩·검색이 일어난다."""

    class _Spy(FakeDenseEmbedder):
        def __init__(self) -> None:
            super().__init__(dimension=8)
            self.encoded_batches: list[list[str]] = []

        def encode_queries(self, texts: list[str]) -> list[list[float]]:
            self.encoded_batches.append(list(texts))
            return super().encode_queries(texts)

    dense_spy = _Spy()
    state = _make_state(query="alpha", rewritten_queries=["alpha 확장", "alpha 원본"])
    hybrid_search(state, dense_embedder=dense_spy, sparse_embedder=sparse, store=store)
    # rewritten_queries 둘 다 한 번에 배치 임베딩됨
    assert dense_spy.encoded_batches == [["alpha 확장", "alpha 원본"]]


def test_hybrid_search_falls_back_to_query_when_no_rewritten(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    class _Spy(FakeDenseEmbedder):
        def __init__(self) -> None:
            super().__init__(dimension=8)
            self.encoded_batches: list[list[str]] = []

        def encode_queries(self, texts: list[str]) -> list[list[float]]:
            self.encoded_batches.append(list(texts))
            return super().encode_queries(texts)

    dense_spy = _Spy()
    state = _make_state(query="alpha", rewritten_queries=[])
    hybrid_search(state, dense_embedder=dense_spy, sparse_embedder=sparse, store=store)
    assert dense_spy.encoded_batches == [["alpha"]]


# --- pool_weights / metadata_filters ---


def test_hybrid_search_uses_default_pool_weights_when_none(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """라우터가 pool_weights를 안 채워도 등가 fallback으로 동작한다."""
    state = _make_state(query="alpha", pool_weights=None)
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    # candidates가 정상적으로 채워진다 (가중치 fallback이 작동했다는 증거)
    assert len(result.candidates) > 0


def test_hybrid_search_uses_provided_pool_weights(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    state = _make_state(
        query="alpha",
        pool_weights={"title_pool": 1.0, "content_pool": 5.0, "label_pool": 0.5},
    )
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    assert len(result.candidates) > 0


def test_hybrid_search_passes_metadata_filters_to_store(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """metadata_filters가 store.search에 정확히 전달되는지 — doc_type으로 좁힘."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    state = _make_state(query="alpha", metadata_filters={"doc_type": "incident"})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40}


def test_hybrid_search_drops_invalid_metadata_filter_types(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """비-str/list 타입의 metadata filter는 무시 — 강건 fallback."""
    state = _make_state(query="alpha", metadata_filters={"version_number": 42})
    # 잘못된 필터로 검색이 깨지지 않고 정상 결과 반환 (필터 미적용)
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    assert len(result.candidates) > 0


def test_hybrid_search_accepts_list_metadata_filter(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
        _chunk(chunk_id="c" * 40, chunk_index=2, text="alpha", doc_type="faq"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    state = _make_state(query="alpha", metadata_filters={"doc_type": ["incident", "operation"]})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40, "b" * 40}


# ---------------------------------------------------------------------------
# 2026-05-20 fix — 빈 list / 빈 문자열 metadata filter 회귀
#
# 라우터가 빈 배열 (space_keys=[], labels=[] 등) 을 metadata_filters 에 채우면
# _coerce_metadata_filters 가 그대로 통과시켜 Qdrant MatchAny(any=[]) 가 생성
# 되고 must 결합 시 모든 검색 결과가 차단되던 버그. 본 fix 는 빈 list / 빈
# 문자열을 거른다.
# ---------------------------------------------------------------------------


def test_hybrid_search_drops_empty_list_metadata_filter(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """빈 list 만 채워진 metadata_filters — 필터 미적용 효과 (검색 정상 반환)."""
    state = _make_state(
        query="alpha",
        metadata_filters={
            "space_keys": [],
            "labels": [],
            "document_types": [],
            "source_types": [],
        },
    )
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    # 모든 metadata_filters 값이 빈 list 라 필터 미적용 → 검색 결과 정상.
    assert len(result.candidates) > 0


def test_hybrid_search_drops_empty_string_metadata_filter(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """빈 문자열 metadata_filters — 필터 미적용 효과."""
    state = _make_state(query="alpha", metadata_filters={"doc_type": ""})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    assert len(result.candidates) > 0


def test_hybrid_search_mixed_empty_and_valid_metadata_filters(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """빈 list 는 거르고 유효 list 만 적용 — 라우터 실 출력 패턴."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    state = _make_state(
        query="alpha",
        metadata_filters={
            "space_keys": [],  # 거름
            "labels": [],  # 거름
            "doc_type": ["incident"],  # 유효 적용
        },
    )
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    # doc_type=incident 만 적용 → "a" 청크만 매칭.
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40}


# ---------------------------------------------------------------------------
# 2026-05-20 feature17c-5 — 라우터 복수형 키 → payload 단수형 필드명 매핑 회귀
#
# 라우터 MetadataFilter.to_dict() 는 space_keys / document_types / source_types
# (복수형) 를 emit 하는데 Qdrant payload 인덱스 필드는 space_key / doc_type /
# source_type (단수형) 이다. 키가 그대로 통과하면 존재하지 않는 payload 필드로
# must 필터가 만들어져 검색 0건이 된다. _coerce_metadata_filters 가 복수형 키를
# rename 하면 정상 필터링된다.
# ---------------------------------------------------------------------------


def test_hybrid_search_maps_plural_document_types_to_doc_type(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """라우터 복수형 document_types → payload doc_type 으로 매핑되어 정상 필터링된다."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    # 복수형 키. rename 전에는 존재하지 않는 "document_types" 필드 필터 → 0건.
    state = _make_state(query="alpha", metadata_filters={"document_types": ["incident"]})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40}


def test_coerce_metadata_filters_renames_plural_keys_to_payload_fields() -> None:
    """라우터 복수형 키 → payload 단수형 필드명 rename (결정론적 단위 검증).

    end-to-end candidate 수로는 feature17c-6 fallback(0건 시 filter 완화 재검색)이
    개입해 rename 여부를 분리 검증하기 어렵다. 키 매핑은 `_coerce_metadata_filters`
    수준에서 직접 단언한다(fallback 무관).
    """
    from app.query.search_node import _coerce_metadata_filters

    # 복수형 → 단수형 rename.
    assert _coerce_metadata_filters({"space_keys": ["CLOUD"]}) == {"space_key": ["CLOUD"]}
    assert _coerce_metadata_filters({"document_types": ["incident"]}) == {"doc_type": ["incident"]}
    assert _coerce_metadata_filters({"source_types": ["page"]}) == {"source_type": ["page"]}
    # labels 는 양쪽 동일, 단수형 payload 키 직접 전달은 그대로 통과(후방 호환).
    assert _coerce_metadata_filters({"labels": ["eks"]}) == {"labels": ["eks"]}
    assert _coerce_metadata_filters({"doc_type": "incident"}) == {"doc_type": "incident"}
    # payload match 대상이 아닌 키(dict/bool)는 거른다.
    non_payload = {"date_range": {"from": "x"}, "attachment_required": True}
    assert _coerce_metadata_filters(non_payload) is None
    # 빈 list/문자열도 거른다(필터 미적용).
    assert _coerce_metadata_filters({"space_keys": [], "document_types": []}) is None


def test_hybrid_search_maps_plural_source_types_to_source_type(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """라우터 복수형 source_types → payload source_type 매핑 (PAGE 청크에 'page' 매칭)."""
    state = _make_state(query="alpha", metadata_filters={"source_types": ["page"]})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    assert len(result.candidates) > 0


def test_hybrid_search_drops_non_payload_filter_keys(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder, store: QdrantPoolStore
) -> None:
    """date_range(dict) / attachment_required(bool) 은 단순 match 대상이 아니라 거른다."""
    state = _make_state(
        query="alpha",
        metadata_filters={
            "date_range": {"from": "2026-01-01", "to": "2026-12-31"},
            "attachment_required": True,
        },
    )
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=store)
    # 두 키 모두 거름 → 필터 미적용 → 검색 정상 반환.
    assert len(result.candidates) > 0


# ---------------------------------------------------------------------------
# 2026-05-20 feature17c-6 — metadata filter 0건 fallback 회귀
#
# 라우터의 LLM 추출 metadata filter 가 payload 와 불일치하면 must 결합으로 전체
# 검색이 0건이 된다(첨부/공간 명시 질의가 통째로 검색 실패). filter 결과가 0건이면
# ACL 은 유지한 채 filter 만 완화해 1회 재검색하는 fallback 으로 방지한다.
# ---------------------------------------------------------------------------


def test_hybrid_search_falls_back_when_metadata_filter_matches_nothing(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """payload 에 없는 doc_type 으로 0건이 되면 filter 완화 재검색으로 후보를 복구한다."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    # 어떤 청크의 doc_type 과도 일치하지 않는 필터 → 1차 검색 0건 → fallback 발동.
    state = _make_state(query="alpha", metadata_filters={"doc_type": "does_not_exist"})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    # fallback(metadata filter 완화)으로 ACL 통과 후보가 복구된다.
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40, "b" * 40}


def test_hybrid_search_no_fallback_when_metadata_filter_matches_subset(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """유효 필터가 일부라도 매칭하면 fallback 없이 그 부분집합만 반환(필터 결과 보존)."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    chunks = [
        _chunk(chunk_id="a" * 40, text="alpha", doc_type="incident"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="alpha", doc_type="operation"),
    ]
    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    # doc_type=incident 는 "a" 만 매칭 → 0건 아님 → fallback 미발동 → "a" 만 반환.
    state = _make_state(query="alpha", metadata_filters={"doc_type": "incident"})
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    assert {c.metadata.chunk_id for c in result.candidates} == {"a" * 40}


# ---------------------------------------------------------------------------
# 2026-05-20 feature17c-7 — payload 풀텍스트 재구성 회귀
#
# payload 에 풀 텍스트(text)를 저장하고, _chunk_from_search_hit 가 200자 프리뷰가
# 아닌 풀 텍스트로 candidate 를 재구성해 rerank·생성기가 풀 텍스트로 동작하게 한다.
# legacy 인덱스(text 없음)는 text_preview 로 fallback.
# ---------------------------------------------------------------------------


def test_hybrid_search_reconstructs_full_text_not_preview(
    dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """인덱싱한 청크 본문이 200자를 넘어도 candidate.text 는 풀 텍스트로 복원된다."""
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    long_text = "alpha " + "메모리 limits 상향 또는 애플리케이션 메모리 사용 분석 " * 30
    assert len(long_text) > 200  # 프리뷰(200자) 한계를 넘는 본문.
    index_chunks(
        [_chunk(chunk_id="a" * 40, text=long_text)],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=s,
        cache=FakeEmbeddingCache(),
    )

    state = _make_state(query="alpha")
    result = hybrid_search(state, dense_embedder=dense, sparse_embedder=sparse, store=s)
    candidate = next(c for c in result.candidates if c.metadata.chunk_id == "a" * 40)
    # 200자 프리뷰가 아니라 풀 텍스트가 복원되어야 한다 (rerank·생성기 입력).
    assert candidate.text == long_text
    assert len(candidate.text) > 200


def test_chunk_from_search_hit_falls_back_to_preview_for_legacy_payload() -> None:
    """legacy 인덱스(payload 에 text 없음)는 text_preview(200자)로 fallback 한다."""
    from app.query.search_node import _chunk_from_search_hit
    from app.storage.qdrant_client import SearchHit

    payload = {
        "chunk_id": "a" * 40,
        "page_id": "P1",
        "page_title": "EKS 운영 가이드",
        "section_header": "개요",
        "section_path": "Cloud 운영 문서 > 개요",
        "chunk_index": 0,
        "labels": ["eks"],
        "doc_type": "operation",
        "space_key": "CLOUD",
        "allowed_groups": ["space:CLOUD"],
        "allowed_users": [],
        "webui_link": "/display/CLOUD/eks",
        "last_modified": "2026-04-22T08:15:00+09:00",
        "source_type": "page",
        "attachment_id": None,
        "attachment_filename": None,
        "attachment_mime": None,
        "extracted_format": None,
        "token_count": 120,
        # text 키 없음 (legacy) — text_preview 만 존재.
        "text_preview": "레거시 프리뷰 본문",
    }
    chunk = _chunk_from_search_hit(SearchHit(chunk_id="a" * 40, score=0.9, payload=payload))
    assert chunk.text == "레거시 프리뷰 본문"
