"""embedding_cache 어댑터 검증 (feature5-B-3).

FakeEmbeddingCache 동작 + EmbeddingCacheEntry 불변성 + MongoEmbeddingCache의 pymongo
호출 시그니처 검증(unittest.mock — 실 MongoDB 불필요).
"""

import dataclasses
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.storage.mongo_cache import (
    EmbeddingCacheEntry,
    FakeEmbeddingCache,
    MongoEmbeddingCache,
)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


# --- EmbeddingCacheEntry ---


def test_cache_entry_is_frozen() -> None:
    entry = EmbeddingCacheEntry(
        chunk_id="a" * 40,
        version_number=1,
        dense_hash="dense-h",
        sparse_hash="sparse-h",
        computed_at=_now(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.version_number = 2  # type: ignore[misc]


# --- FakeEmbeddingCache ---


def test_fake_cache_miss_returns_none() -> None:
    assert FakeEmbeddingCache().get_cached_version("nonexistent") is None


def test_fake_cache_set_then_get() -> None:
    cache = FakeEmbeddingCache()
    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=3,
        dense_hash="d",
        sparse_hash="s",
        computed_at=_now(),
    )
    assert cache.get_cached_version("a" * 40) == 3


def test_fake_cache_overwrite_updates_version() -> None:
    cache = FakeEmbeddingCache()
    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=1,
        dense_hash="d1",
        sparse_hash="s1",
        computed_at=_now(),
    )
    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=2,
        dense_hash="d2",
        sparse_hash="s2",
        computed_at=_now(),
    )
    assert cache.get_cached_version("a" * 40) == 2
    # 마지막 set의 hash가 보존됨
    assert cache.entries["a" * 40].dense_hash == "d2"
    assert cache.entries["a" * 40].sparse_hash == "s2"


def test_fake_cache_isolates_chunk_ids() -> None:
    cache = FakeEmbeddingCache()
    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=1,
        dense_hash="da",
        sparse_hash="sa",
        computed_at=_now(),
    )
    cache.set_cached_version(
        chunk_id="b" * 40,
        version_number=5,
        dense_hash="db",
        sparse_hash="sb",
        computed_at=_now(),
    )
    assert cache.get_cached_version("a" * 40) == 1
    assert cache.get_cached_version("b" * 40) == 5


# --- MongoEmbeddingCache (pymongo 호출 시그니처 검증, 실 MongoDB 불필요) ---


def _make_mock_mongo_cache() -> tuple[MongoEmbeddingCache, MagicMock]:
    """pymongo client/db/collection을 MagicMock으로 대체한 MongoEmbeddingCache."""
    collection = MagicMock(name="collection")
    db = MagicMock(name="db")
    db.__getitem__.return_value = collection
    client = MagicMock(name="client")
    client.__getitem__.return_value = db

    cache = MongoEmbeddingCache(client=client, db_name="lina_rag")
    return cache, collection


def test_mongo_cache_get_calls_find_one_with_projection() -> None:
    cache, collection = _make_mock_mongo_cache()
    collection.find_one.return_value = {"version_number": 7}

    version = cache.get_cached_version("a" * 40)

    assert version == 7
    collection.find_one.assert_called_once_with(
        {"chunk_id": "a" * 40},
        projection={"version_number": 1, "_id": 0},
    )


def test_mongo_cache_get_returns_none_when_not_found() -> None:
    cache, collection = _make_mock_mongo_cache()
    collection.find_one.return_value = None

    assert cache.get_cached_version("nonexistent") is None


def test_mongo_cache_set_uses_upsert() -> None:
    cache, collection = _make_mock_mongo_cache()
    now = _now()

    cache.set_cached_version(
        chunk_id="a" * 40,
        version_number=3,
        dense_hash="d",
        sparse_hash="s",
        computed_at=now,
    )

    # update_one은 멱등 upsert로 호출되어야 함
    collection.update_one.assert_called_once_with(
        {"chunk_id": "a" * 40},
        {
            "$set": {
                "version_number": 3,
                "dense_hash": "d",
                "sparse_hash": "s",
                "computed_at": now,
            }
        },
        upsert=True,
    )


def test_mongo_cache_from_settings_imports_pymongo() -> None:
    # pymongo 미설치 환경에서는 ImportError, 설치된 환경에서는 MongoClient 생성.
    pytest.importorskip("pymongo")

    cache = MongoEmbeddingCache.from_settings(Settings(_env_file=None))
    # 생성만 검증 — 실제 연결은 lazy(MongoClient는 호출 시 연결).
    assert cache is not None
