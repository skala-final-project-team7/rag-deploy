"""삭제 동기화 (Reconciliation) 검증 — feature6 Phase 3.

설계서 §3.7 Phase 1 흐름 정합:
- source.list_active_ids() vs Qdrant scroll → ghost 차집합 → cascade 삭제

`:memory:` Qdrant + Fake DocumentSourceAdapter 조합으로 외부 의존성 0. 본문/첨부 청크
혼합 + ghost 케이스를 모두 통합 검증한다.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Iterator
from datetime import datetime

import pytest

pytest.importorskip("qdrant_client")

from app.adapters.base import ActiveIds, ChangeEvent, DocumentSourceAdapter  # noqa: E402
from app.config import Settings  # noqa: E402
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder  # noqa: E402
from app.ingestion.sync import ReconciliationResult, reconcile_deletions  # noqa: E402
from app.ingestion.vector_store import CONTENT_POOL  # noqa: E402
from app.schemas.chunk import Chunk, ChunkMetadata  # noqa: E402
from app.schemas.enums import SourceType  # noqa: E402
from app.schemas.page_object import PageObject  # noqa: E402
from app.storage.qdrant_client import QdrantPoolStore  # noqa: E402

# :memory: Qdrant 의 payload 인덱스 noop 경고는 본 테스트에서는 무관.
warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 픽스처 헬퍼 ---


def _settings() -> Settings:
    return Settings(_env_file=None)


def _chunk(
    *,
    chunk_id: str,
    page_id: str,
    attachment_id: str | None = None,
    chunk_index: int = 0,
) -> Chunk:
    meta = ChunkMetadata(
        chunk_id=chunk_id,
        page_id=page_id,
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=chunk_index,
        labels=["eks"],
        doc_type="operation",
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.PAGE if attachment_id is None else SourceType.ATTACHMENT,
        attachment_id=attachment_id,
        token_count=120,
    )
    return Chunk(text="text", metadata=meta)


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


def _index(
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    chunk: Chunk,
) -> None:
    [d_vec] = dense.encode_passages([chunk.text])
    [s_vec] = sparse.encode_passages([chunk.text])
    store.upsert_chunk(
        CONTENT_POOL, chunk, version_number=1, dense_vector=d_vec, sparse_vector=s_vec
    )


class _FakeSource(DocumentSourceAdapter):
    """최소 mock — list_active_ids 만 사용 (fetch_pages/watch_changes 는 sync 호출 안 함)."""

    def __init__(self, pages: set[str], attachments: set[str]) -> None:
        self._ids = ActiveIds(pages=pages, attachments=attachments)

    def fetch_pages(self, since: datetime | None = None) -> Iterator[PageObject]:
        yield from ()  # pragma: no cover — sync 는 본 메서드 호출 안 함

    def list_active_ids(self) -> ActiveIds:
        return self._ids

    def watch_changes(self) -> Iterator[ChangeEvent]:
        yield from ()  # pragma: no cover


# --- ReconciliationResult 값 객체 회귀 ---


def test_result_is_frozen_dataclass() -> None:
    result = ReconciliationResult(deleted_pages=[], deleted_attachments=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.deleted_pages = ["P1"]  # type: ignore[misc]


def test_result_carries_deleted_lists() -> None:
    result = ReconciliationResult(deleted_pages=["P1", "P2"], deleted_attachments=["ATT-1"])
    assert result.deleted_pages == ["P1", "P2"]
    assert result.deleted_attachments == ["ATT-1"]


# --- reconcile_deletions: ghost page 삭제 ---


def test_reconcile_deletes_ghost_pages(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """source 에 없는 page_id 의 본문 청크는 cascade 삭제 (3 Pool 모두)."""
    _index(store, dense, sparse, _chunk(chunk_id="a" * 40, page_id="P1"))
    _index(store, dense, sparse, _chunk(chunk_id="b" * 40, page_id="P2"))
    # source 에는 P1 만 살아있음 → P2 는 ghost
    source = _FakeSource(pages={"P1"}, attachments=set())

    result = reconcile_deletions(source=source, store=store)

    assert "P2" in result.deleted_pages
    assert "P1" not in result.deleted_pages
    # 적재 결과 검증 — P2 는 Qdrant 에서 사라졌고 P1 은 남아있음
    assert store.scroll_page_ids() == {"P1"}


def test_reconcile_deletes_ghost_attachments(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """source 에 없는 attachment_id 의 첨부 청크는 cascade 삭제."""
    _index(
        store,
        dense,
        sparse,
        _chunk(chunk_id="a" * 40, page_id="P1", attachment_id="ATT-1"),
    )
    _index(
        store,
        dense,
        sparse,
        _chunk(chunk_id="b" * 40, page_id="P1", attachment_id="ATT-2", chunk_index=1),
    )
    source = _FakeSource(pages={"P1"}, attachments={"ATT-1"})

    result = reconcile_deletions(source=source, store=store)

    assert result.deleted_attachments == ["ATT-2"]
    assert store.scroll_attachment_ids() == {"ATT-1"}


# --- false positive 회귀 ---


def test_reconcile_keeps_all_when_active_is_superset(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """source ⊇ Qdrant 적재 ID 면 ghost 0 — 정상 동작 회귀 보호."""
    _index(store, dense, sparse, _chunk(chunk_id="a" * 40, page_id="P1"))
    _index(store, dense, sparse, _chunk(chunk_id="b" * 40, page_id="P2"))
    # source 가 더 많이 알고 있음 (P1, P2, P3)
    source = _FakeSource(pages={"P1", "P2", "P3"}, attachments=set())

    result = reconcile_deletions(source=source, store=store)

    assert result.deleted_pages == []
    assert result.deleted_attachments == []
    assert store.scroll_page_ids() == {"P1", "P2"}


def test_reconcile_with_empty_qdrant_returns_empty_result(
    store: QdrantPoolStore,
) -> None:
    """Qdrant 에 적재 0건이면 ghost 도 없음 — scroll 결과가 빈 set 인 회귀 보호."""
    source = _FakeSource(pages={"P1"}, attachments={"ATT-1"})
    result = reconcile_deletions(source=source, store=store)
    assert result.deleted_pages == []
    assert result.deleted_attachments == []


# --- 모든 ID 가 ghost ---


def test_reconcile_deletes_all_when_active_is_empty(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """source 가 빈 ActiveIds 를 반환하면 적재된 모든 청크가 ghost.

    설계서 §3.7 정합 — 운영 시나리오에서 첨부는 부모 페이지에 본문이 있는 경우에만
    적재된다 (Phase 1 ``analyze_attachment`` 분기 정합). 첨부만 있고 본문이 없는
    페이지는 page-level scroll 에 잡히지 않으므로, 본 테스트는 본문 + 첨부 동반
    적재의 운영 시나리오로 검증한다.
    """
    _index(store, dense, sparse, _chunk(chunk_id="a" * 40, page_id="P1"))
    _index(store, dense, sparse, _chunk(chunk_id="b" * 40, page_id="P2"))
    _index(
        store,
        dense,
        sparse,
        _chunk(chunk_id="c" * 40, page_id="P2", attachment_id="ATT-1", chunk_index=1),
    )
    source = _FakeSource(pages=set(), attachments=set())

    result = reconcile_deletions(source=source, store=store)

    assert set(result.deleted_pages) == {"P1", "P2"}
    assert set(result.deleted_attachments) == {"ATT-1"}
    assert store.scroll_page_ids() == set()
    assert store.scroll_attachment_ids() == set()


# --- 본문 + 첨부 혼합 ghost ---


def test_reconcile_mixed_body_and_attachment_ghosts(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """본문 P2·P3 + 첨부 ATT-2 가 모두 ghost — 한 호출에서 둘 다 cascade 삭제.

    설계서 §3.7 정합 — 첨부는 부모 페이지 본문과 함께 적재된다고 가정 (P3 에도 본문
    적재). 본 시나리오에서 active.pages={P1} 이면 P2·P3 가 page-level ghost,
    active.attachments={ATT-1} 이면 ATT-2 가 attachment-level ghost.
    """
    _index(store, dense, sparse, _chunk(chunk_id="a" * 40, page_id="P1"))
    _index(store, dense, sparse, _chunk(chunk_id="b" * 40, page_id="P2"))
    _index(store, dense, sparse, _chunk(chunk_id="c" * 40, page_id="P3"))
    _index(
        store,
        dense,
        sparse,
        _chunk(chunk_id="d" * 40, page_id="P1", attachment_id="ATT-1", chunk_index=1),
    )
    _index(
        store,
        dense,
        sparse,
        _chunk(chunk_id="e" * 40, page_id="P3", attachment_id="ATT-2", chunk_index=1),
    )
    source = _FakeSource(pages={"P1"}, attachments={"ATT-1"})

    result = reconcile_deletions(source=source, store=store)

    assert set(result.deleted_pages) == {"P2", "P3"}
    assert result.deleted_attachments == ["ATT-2"]


def test_reconcile_does_not_call_delete_when_no_ghosts(
    store: QdrantPoolStore, dense: FakeDenseEmbedder, sparse: FakeSparseEmbedder
) -> None:
    """ghost 가 0 이면 delete 호출 자체를 회피 — false positive + 운영 비용 차단.

    spy 로 delete_by_page_id / delete_by_attachment_id 호출 횟수를 검증한다.
    """
    _index(store, dense, sparse, _chunk(chunk_id="a" * 40, page_id="P1"))
    source = _FakeSource(pages={"P1"}, attachments=set())

    page_calls: list[str] = []
    attach_calls: list[str] = []
    original_page = store.delete_by_page_id
    original_attach = store.delete_by_attachment_id

    def _spy_page(pid: str) -> None:
        page_calls.append(pid)
        original_page(pid)

    def _spy_attach(aid: str) -> None:
        attach_calls.append(aid)
        original_attach(aid)

    store.delete_by_page_id = _spy_page  # type: ignore[method-assign]
    store.delete_by_attachment_id = _spy_attach  # type: ignore[method-assign]

    reconcile_deletions(source=source, store=store)

    assert page_calls == []
    assert attach_calls == []
