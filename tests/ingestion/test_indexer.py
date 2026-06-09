"""Ingestion Indexer 검증 (feature5-B-3).

청크 → 임베딩 → Multi-Pool upsert → embedding_cache 의 끝-끝 흐름을 Fake everything
으로 검증한다. 외부 의존성 0 — 실 모델·실 Qdrant·실 MongoDB 모두 불필요.

`:memory:` Qdrant + FakeDenseEmbedder + FakeSparseEmbedder + FakeEmbeddingCache 조합으로
멱등성·배치 효율·부분 cache hit·버전 갱신·KeyError까지 모두 통합 검증한다.
"""

import warnings
from datetime import datetime

import pytest

from app.config import Settings
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder
from app.ingestion.indexer import IndexerResult, index_chunks
from app.ingestion.vector_store import CONTENT_POOL, POOL_NAMES, TITLE_POOL
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import AttachmentType, ExtractedFormat, SourceType
from app.storage.chunk_lookup import FakeChunkTextLookup
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_client import QdrantPoolStore

# 로컬 :memory: payload 인덱스 noop 경고 — 본 테스트에서는 무관.
warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 픽스처·헬퍼 ---


def _settings() -> Settings:
    return Settings(_env_file=None)


def _chunk(
    *, chunk_id: str, page_id: str = "P1", chunk_index: int = 0, text: str = "alpha"
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id=page_id,
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=chunk_index,
        labels=["eks", "운영"],
        doc_type="operation",
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _attachment_chunk(
    *,
    chunk_id: str,
    attachment_id: str,
    page_id: str = "P1",
    chunk_index: int = 0,
    text: str = "첨부 청크 본문",
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id=page_id,
        page_title="EKS 운영 가이드",
        section_header="첨부",
        section_path="Cloud 운영 문서 > 첨부",
        chunk_index=chunk_index,
        labels=["eks"],
        doc_type=AttachmentType.PDF,
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.ATTACHMENT,
        attachment_id=attachment_id,
        attachment_filename="runbook.pdf",
        attachment_mime="application/pdf",
        extracted_format=ExtractedFormat.RAW_TEXT,
        token_count=80,
    )
    return Chunk(text=text, metadata=metadata)


@pytest.fixture()
def store() -> QdrantPoolStore:
    s = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    s.bootstrap_collections()
    return s


@pytest.fixture()
def dense() -> FakeDenseEmbedder:
    return FakeDenseEmbedder(dimension=8)


@pytest.fixture()
def sparse() -> FakeSparseEmbedder:
    return FakeSparseEmbedder()


@pytest.fixture()
def cache() -> FakeEmbeddingCache:
    return FakeEmbeddingCache()


# --- 단건 인덱싱 ---


def test_index_single_chunk_upserts_to_all_pools_and_caches(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunk = _chunk(chunk_id="a" * 40)
    result = index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert result.upserted_count == 1
    assert result.skipped_count == 0
    assert result.upserted_chunk_ids == ["a" * 40]

    # 모든 Pool에 적재됨 — TITLE/CONTENT/LABEL 각각에서 검색되어야 함
    [q_vec] = dense.encode_passages([chunk.text])
    for pool in POOL_NAMES:
        hits = store.search(
            pool,
            acl_filter={"should": [{"key": "allowed_groups", "match": {"any": ["space:CLOUD"]}}]},
            dense_vector=q_vec,
        )
        # 적어도 같은 chunk_id가 결과에 포함됨 (CONTENT_POOL은 정확 매칭, 다른 Pool은 다른 텍스트
        # 임베딩이지만 단일 chunk라 그것만 반환됨)
        assert "a" * 40 in {hit.chunk_id for hit in hits}

    # cache가 채워짐
    assert cache.get_cached_version("a" * 40) == 1
    entry = cache.entries["a" * 40]
    assert entry.version_number == 1
    assert entry.dense_hash  # non-empty
    assert entry.sparse_hash  # non-empty


# --- 멱등성 ---


def test_reindex_same_version_skips_all(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunks = [_chunk(chunk_id=letter * 40, chunk_index=i) for i, letter in enumerate("abc")]
    version_map = {"P1": 1}

    first = index_chunks(
        chunks,
        version_by_page_id=version_map,
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert first.upserted_count == 3
    assert first.skipped_count == 0

    # 같은 입력 + 같은 version → 모두 cache hit으로 스킵
    second = index_chunks(
        chunks,
        version_by_page_id=version_map,
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert second.upserted_count == 0
    assert second.skipped_count == 3
    assert set(second.skipped_chunk_ids) == {chunk.metadata.chunk_id for chunk in chunks}


def test_reindex_with_new_version_reindexes_all(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunks = [_chunk(chunk_id=letter * 40, chunk_index=i) for i, letter in enumerate("ab")]

    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )

    # version 변경 → 재인덱싱
    result = index_chunks(
        chunks,
        version_by_page_id={"P1": 2},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert result.upserted_count == 2
    assert result.skipped_count == 0

    # cache 버전이 2로 갱신됨
    for chunk in chunks:
        assert cache.get_cached_version(chunk.metadata.chunk_id) == 2


# --- 부분 cache hit ---


def test_partial_cache_hit_only_indexes_missing(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunk_a = _chunk(chunk_id="a" * 40, chunk_index=0)
    chunk_b = _chunk(chunk_id="b" * 40, chunk_index=1)
    chunk_c = _chunk(chunk_id="c" * 40, chunk_index=2)

    # chunk_a만 사전 인덱싱
    index_chunks(
        [chunk_a],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )

    # 세 청크 모두 요청 → a는 스킵, b/c는 새로 인덱싱
    result = index_chunks(
        [chunk_a, chunk_b, chunk_c],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert result.upserted_count == 2
    assert result.skipped_count == 1
    assert result.skipped_chunk_ids == ["a" * 40]
    assert set(result.upserted_chunk_ids) == {"b" * 40, "c" * 40}


# --- 빈 입력 ---


def test_empty_chunk_list_returns_empty_result(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    result = index_chunks(
        [],
        version_by_page_id={},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    assert isinstance(result, IndexerResult)
    assert result.upserted_count == 0
    assert result.skipped_count == 0


def test_all_chunks_cached_short_circuits_embed_call(
    store: QdrantPoolStore, cache: FakeEmbeddingCache
) -> None:
    """모든 청크가 cache hit이면 임베더는 호출되지 않아야 한다 (배치 효율)."""
    chunk = _chunk(chunk_id="a" * 40)
    # 사전 캐시 채우기
    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=1,
        dense_hash="pre",
        sparse_hash="pre",
        computed_at=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
    )

    # 호출 횟수 추적용 spy 임베더
    class _SpyDense(FakeDenseEmbedder):
        def __init__(self) -> None:
            super().__init__(dimension=8)
            self.call_count = 0

        def encode_passages(self, texts: list[str]) -> list[list[float]]:
            self.call_count += 1
            return super().encode_passages(texts)

    class _SpySparse(FakeSparseEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0

        def encode_passages(self, texts):  # type: ignore[no-untyped-def]
            self.call_count += 1
            return super().encode_passages(texts)

    dense_spy = _SpyDense()
    sparse_spy = _SpySparse()

    result = index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense_spy,
        sparse_embedder=sparse_spy,
        store=store,
        cache=cache,
    )
    assert result.skipped_count == 1
    assert dense_spy.call_count == 0, "cache hit 시 임베더 호출 불필요"
    assert sparse_spy.call_count == 0


# --- 배치 효율: Pool 수만큼만 임베드 호출 ---


def test_embed_called_once_per_pool_not_per_chunk(
    store: QdrantPoolStore, cache: FakeEmbeddingCache
) -> None:
    """3 Pool × 1 dense + 3 Pool × 1 sparse = dense·sparse 각 3번 호출 (배치, 청크 수 무관)."""

    class _CountingDense(FakeDenseEmbedder):
        def __init__(self) -> None:
            super().__init__(dimension=8)
            self.batch_sizes: list[int] = []

        def encode_passages(self, texts: list[str]) -> list[list[float]]:
            self.batch_sizes.append(len(texts))
            return super().encode_passages(texts)

    class _CountingSparse(FakeSparseEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.batch_sizes: list[int] = []

        def encode_passages(self, texts):  # type: ignore[no-untyped-def]
            self.batch_sizes.append(len(texts))
            return super().encode_passages(texts)

    dense_c = _CountingDense()
    sparse_c = _CountingSparse()
    chunks = [_chunk(chunk_id=letter * 40, chunk_index=i) for i, letter in enumerate("abcde")]

    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense_c,
        sparse_embedder=sparse_c,
        store=store,
        cache=cache,
    )
    # 각 임베더는 Pool 수(3)만큼만 호출되어야 함
    assert len(dense_c.batch_sizes) == len(POOL_NAMES)
    assert len(sparse_c.batch_sizes) == len(POOL_NAMES)
    # 각 호출의 batch size는 청크 수(5)
    assert all(size == 5 for size in dense_c.batch_sizes)
    assert all(size == 5 for size in sparse_c.batch_sizes)


# --- 다중 페이지 ---


def test_multiple_pages_use_correct_versions(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunk_p1 = _chunk(chunk_id="a" * 40, page_id="P1")
    chunk_p2 = _chunk(chunk_id="b" * 40, page_id="P2")

    index_chunks(
        [chunk_p1, chunk_p2],
        version_by_page_id={"P1": 1, "P2": 7},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
    )
    # 각 청크의 부모 페이지 버전이 cache에 기록됨
    assert cache.get_cached_version("a" * 40) == 1
    assert cache.get_cached_version("b" * 40) == 7

    # Qdrant payload의 version_number 도 일치
    [q_vec] = dense.encode_passages([chunk_p1.text])
    hits = store.search(
        CONTENT_POOL,
        acl_filter={"should": [{"key": "allowed_groups", "match": {"any": ["space:CLOUD"]}}]},
        dense_vector=q_vec,
    )
    payloads_by_chunk_id = {hit.chunk_id: hit.payload for hit in hits}
    assert payloads_by_chunk_id["a" * 40]["version_number"] == 1
    assert payloads_by_chunk_id["b" * 40]["version_number"] == 7


# --- 에러 처리 ---


def test_missing_page_id_in_version_map_raises_key_error(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    chunk = _chunk(chunk_id="a" * 40, page_id="UNKNOWN")
    with pytest.raises(KeyError):
        index_chunks(
            [chunk],
            version_by_page_id={"P1": 1},  # UNKNOWN 누락
            dense_embedder=dense,
            sparse_embedder=sparse,
            store=store,
            cache=cache,
        )


# --- TITLE_POOL 텍스트 구성 검증 (5-A 통합) ---


def test_title_pool_uses_page_title_plus_section_header(
    store: QdrantPoolStore,
    cache: FakeEmbeddingCache,
) -> None:
    """5-A의 pool_embedding_texts(title_pool)가 어떤 텍스트로 들어갔는지 — 우회 검증."""
    received: list[list[str]] = []

    class _CapturingDense(FakeDenseEmbedder):
        def __init__(self) -> None:
            super().__init__(dimension=8)

        def encode_passages(self, texts: list[str]) -> list[list[float]]:
            received.append(list(texts))
            return super().encode_passages(texts)

    dense_capture = _CapturingDense()
    chunk = _chunk(chunk_id="a" * 40, text="본문 텍스트")

    index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense_capture,
        sparse_embedder=FakeSparseEmbedder(),
        store=store,
        cache=cache,
    )

    # 3 Pool — 호출 순서는 POOL_NAMES 순(title/content/label)
    title_idx = POOL_NAMES.index(TITLE_POOL)
    content_idx = POOL_NAMES.index(CONTENT_POOL)
    assert received[title_idx] == ["EKS 운영 가이드 개요"]  # page_title + section_header
    assert received[content_idx] == ["본문 텍스트"]  # 청크 본문


# --- chunk_lookup 적재 통합 (Phase 2) ---


def test_chunk_lookup_records_page_chunk_text_without_download_url(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """본문 청크는 chunk_lookup에 text는 풀 텍스트 그대로, download_url=None으로 적재."""
    lookup = FakeChunkTextLookup()
    chunk = _chunk(chunk_id="a" * 40, text="본문 풀 텍스트입니다.")

    index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=lookup,
    )

    record = lookup.fetch("a" * 40)
    assert record is not None
    assert record.chunk_id == "a" * 40
    assert record.text == "본문 풀 텍스트입니다."
    assert record.download_url is None


def test_chunk_lookup_records_attachment_chunk_with_download_url(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """첨부 청크는 attachment_download_urls 매핑에서 download_url을 가져와 적재."""
    lookup = FakeChunkTextLookup()
    chunk = _attachment_chunk(
        chunk_id="b" * 40,
        attachment_id="P1-att-0",
        text="첨부 풀 텍스트",
    )

    index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=lookup,
        attachment_download_urls={"P1-att-0": "https://confluence/download/att-0"},
    )

    record = lookup.fetch("b" * 40)
    assert record is not None
    assert record.text == "첨부 풀 텍스트"
    assert record.download_url == "https://confluence/download/att-0"


def test_chunk_lookup_attachment_without_url_mapping_falls_back_to_none(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """첨부 청크지만 매핑에 attachment_id가 없으면 download_url=None으로 안전 fallback."""
    lookup = FakeChunkTextLookup()
    chunk = _attachment_chunk(
        chunk_id="c" * 40,
        attachment_id="P1-att-1",
    )

    index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=lookup,
        attachment_download_urls={},  # P1-att-1 없음
    )

    record = lookup.fetch("c" * 40)
    assert record is not None
    assert record.download_url is None


def test_chunk_lookup_default_none_keeps_legacy_behavior(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """chunk_lookup=None(default) 호출자는 적재가 일어나지 않는다 (legacy 호환)."""
    chunk = _chunk(chunk_id="a" * 40)
    result = index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        # chunk_lookup 미주입
    )
    # 기존 동작 유지: Qdrant + cache는 갱신, chunk_lookup 적재 없음(별도 dep 없음).
    assert result.upserted_count == 1


def test_chunk_lookup_skips_when_cache_hits(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """cache hit으로 스킵된 청크는 chunk_lookup에도 적재되지 않는다 (멱등성)."""
    chunk = _chunk(chunk_id="a" * 40)
    lookup = FakeChunkTextLookup()

    # 1차 적재 — chunk_lookup에도 적재됨
    index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=lookup,
    )
    assert lookup.fetch("a" * 40) is not None

    # 동일 청크를 다른 text로 호출 (테스트 명료성 목적 — 실제로는 동일 chunk_id면 동일 text)
    # cache hit이므로 chunk_lookup도 재호출되지 않아야 함 — spy로 검증.
    upsert_calls: list[int] = []

    class _SpyLookup(FakeChunkTextLookup):
        def upsert_many(self, records):  # type: ignore[no-untyped-def]
            upsert_calls.append(len(records))
            super().upsert_many(records)

    spy_lookup = _SpyLookup()
    result = index_chunks(
        [chunk],
        version_by_page_id={"P1": 1},  # 동일 version → cache hit
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=spy_lookup,
    )
    assert result.skipped_count == 1
    assert upsert_calls == [], "cache hit 시 chunk_lookup upsert_many 호출 불필요"


def test_chunk_lookup_batches_upsert_many_in_single_call(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    cache: FakeEmbeddingCache,
) -> None:
    """다수 청크 적재 시 chunk_lookup.upsert_many는 1회 호출(배치 효율)."""
    upsert_calls: list[int] = []

    class _SpyLookup(FakeChunkTextLookup):
        def upsert_many(self, records):  # type: ignore[no-untyped-def]
            upsert_calls.append(len(records))
            super().upsert_many(records)

    lookup = _SpyLookup()
    chunks = [_chunk(chunk_id=letter * 40, chunk_index=i) for i, letter in enumerate("abc")]

    index_chunks(
        chunks,
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=lookup,
    )

    # 3 청크 → upsert_many 단일 호출, batch size=3.
    assert upsert_calls == [3]
