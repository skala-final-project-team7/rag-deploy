"""Chunk Text Lookup 어댑터 검증 — ChunkTextLookup ABC + Fake + Mongo.

ABC 계약 + Fake in-memory 동작 + Mongo find_one/find/replace_one/bulk_write 응답 변환을
검증한다. 실 MongoDB 없이 pymongo collection을 mock으로 대체해 외부 의존성 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from app.storage.chunk_lookup import (
    ChunkLookupRecord,
    ChunkTextLookup,
    FakeChunkTextLookup,
    MongoChunkTextLookup,
)


def _record(chunk_id: str = "a" * 40, *, download_url: str | None = None) -> ChunkLookupRecord:
    return ChunkLookupRecord(
        chunk_id=chunk_id,
        text=f"풀 텍스트 {chunk_id[:4]}",
        download_url=download_url,
    )


# --- ABC / Fake 동작 ---


def test_fake_lookup_fetch_returns_record() -> None:
    lookup = FakeChunkTextLookup({"a" * 40: _record("a" * 40)})
    record = lookup.fetch("a" * 40)
    assert record is not None
    assert record.chunk_id == "a" * 40
    assert record.text.startswith("풀 텍스트")


def test_fake_lookup_fetch_missing_returns_none() -> None:
    lookup = FakeChunkTextLookup()
    assert lookup.fetch("missing") is None


def test_fake_lookup_fetch_many_filters_missing() -> None:
    lookup = FakeChunkTextLookup({"a" * 40: _record("a" * 40), "b" * 40: _record("b" * 40)})
    result = lookup.fetch_many(["a" * 40, "b" * 40, "missing"])
    assert set(result.keys()) == {"a" * 40, "b" * 40}
    assert all(isinstance(r, ChunkLookupRecord) for r in result.values())


def test_fake_lookup_add_overwrites_existing() -> None:
    lookup = FakeChunkTextLookup()
    lookup.add(_record("a" * 40, download_url="http://example/old"))
    lookup.add(_record("a" * 40, download_url="http://example/new"))
    record = lookup.fetch("a" * 40)
    assert record is not None
    assert record.download_url == "http://example/new"


def test_fake_lookup_implements_abc() -> None:
    # 본 ABC 계약 자체에 대한 회귀 보호 — 향후 abstractmethod 추가 시 즉시 실패.
    assert issubclass(FakeChunkTextLookup, ChunkTextLookup)
    assert issubclass(MongoChunkTextLookup, ChunkTextLookup)


def test_fake_lookup_upsert_inserts_new_record() -> None:
    lookup = FakeChunkTextLookup()
    record = ChunkLookupRecord(chunk_id="a" * 40, text="hello", download_url=None)
    lookup.upsert(record)
    fetched = lookup.fetch("a" * 40)
    assert fetched is not None
    assert fetched.text == "hello"


def test_fake_lookup_upsert_replaces_existing_record() -> None:
    lookup = FakeChunkTextLookup(
        {"a" * 40: ChunkLookupRecord(chunk_id="a" * 40, text="old", download_url=None)}
    )
    lookup.upsert(ChunkLookupRecord(chunk_id="a" * 40, text="new", download_url="http://x"))
    fetched = lookup.fetch("a" * 40)
    assert fetched is not None
    assert fetched.text == "new"
    assert fetched.download_url == "http://x"


def test_fake_lookup_upsert_many_inserts_batch() -> None:
    lookup = FakeChunkTextLookup()
    records = [
        ChunkLookupRecord(chunk_id="a" * 40, text="A", download_url=None),
        ChunkLookupRecord(chunk_id="b" * 40, text="B", download_url="http://b"),
    ]
    lookup.upsert_many(records)
    result = lookup.fetch_many(["a" * 40, "b" * 40])
    assert set(result.keys()) == {"a" * 40, "b" * 40}
    assert result["b" * 40].download_url == "http://b"


def test_fake_lookup_upsert_many_empty_input_is_noop() -> None:
    lookup = FakeChunkTextLookup()
    lookup.upsert_many([])
    assert lookup.fetch_many(["a" * 40]) == {}


# --- Mongo 어댑터 (외부 mock) ---


class _FakeCollection:
    """pymongo collection 최소 mock — find_one + find + replace_one + bulk_write."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self.replace_one_calls: list[dict[str, Any]] = []
        self.bulk_write_calls: list[list[Any]] = []

    def find_one(
        self, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        target = query.get("chunk_id")
        for doc in self._docs:
            if doc["chunk_id"] == target:
                return dict(doc)
        return None

    def find(
        self, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        wanted = set(query["chunk_id"]["$in"])
        return [dict(doc) for doc in self._docs if doc["chunk_id"] in wanted]

    def replace_one(
        self,
        filter_: dict[str, Any],
        replacement: dict[str, Any],
        upsert: bool = False,
    ) -> None:
        self.replace_one_calls.append(
            {"filter": dict(filter_), "replacement": dict(replacement), "upsert": upsert}
        )
        target = filter_.get("chunk_id")
        for index, doc in enumerate(self._docs):
            if doc["chunk_id"] == target:
                self._docs[index] = dict(replacement)
                return
        if upsert:
            self._docs.append(dict(replacement))

    def bulk_write(self, operations: list[Any]) -> None:
        self.bulk_write_calls.append(list(operations))
        for op in operations:
            self.replace_one(op.filter, op.replacement, upsert=op.upsert)


class _FakeDB:
    def __init__(self, collection: _FakeCollection) -> None:
        self._collection = collection

    def __getitem__(self, collection_name: str) -> _FakeCollection:
        return self._collection


class _DictStyleClient:
    """`client[db_name][collection_name]` 두 단계 인덱싱 지원."""

    def __init__(self, collection: _FakeCollection) -> None:
        self._db = _FakeDB(collection)

    def __getitem__(self, db_name: str) -> _FakeDB:
        return self._db


@pytest.fixture()
def mongo_lookup() -> MongoChunkTextLookup:
    docs = [
        {
            "chunk_id": "a" * 40,
            "text": "본문 풀 텍스트",
            "download_url": None,
        },
        {
            "chunk_id": "b" * 40,
            "text": "첨부 풀 텍스트",
            "download_url": "https://confluence/download/att-1",
        },
        {
            "chunk_id": "c" * 40,
            "text": "",
            # download_url 필드 누락 — 후방 호환 검증용
        },
    ]
    client = _DictStyleClient(_FakeCollection(docs))
    return MongoChunkTextLookup(client=client, db_name="lina_rag")


def test_mongo_lookup_fetch_attachment_record(mongo_lookup: MongoChunkTextLookup) -> None:
    record = mongo_lookup.fetch("b" * 40)
    assert record is not None
    assert record.text == "첨부 풀 텍스트"
    assert record.download_url == "https://confluence/download/att-1"


def test_mongo_lookup_fetch_page_record_has_no_download_url(
    mongo_lookup: MongoChunkTextLookup,
) -> None:
    record = mongo_lookup.fetch("a" * 40)
    assert record is not None
    assert record.text == "본문 풀 텍스트"
    assert record.download_url is None


def test_mongo_lookup_fetch_missing_returns_none(mongo_lookup: MongoChunkTextLookup) -> None:
    assert mongo_lookup.fetch("z" * 40) is None


def test_mongo_lookup_fetch_handles_missing_download_url_field(
    mongo_lookup: MongoChunkTextLookup,
) -> None:
    # download_url 필드 자체가 누락된 legacy 문서도 정상 처리 (None으로 떨어짐)
    record = mongo_lookup.fetch("c" * 40)
    assert record is not None
    assert record.download_url is None
    assert record.text == ""


def test_mongo_lookup_fetch_many_batches(mongo_lookup: MongoChunkTextLookup) -> None:
    result = mongo_lookup.fetch_many(["a" * 40, "b" * 40, "z" * 40])
    assert set(result.keys()) == {"a" * 40, "b" * 40}
    assert result["b" * 40].download_url == "https://confluence/download/att-1"


def test_mongo_lookup_fetch_many_empty_input(mongo_lookup: MongoChunkTextLookup) -> None:
    # 빈 입력에서는 Mongo 호출 자체를 회피한다 (find({}, ...)는 모든 문서를 반환할 위험).
    assert mongo_lookup.fetch_many([]) == {}


# --- Mongo upsert / upsert_many ---


def _new_collection_and_lookup() -> tuple[_FakeCollection, MongoChunkTextLookup]:
    collection = _FakeCollection([])
    client = _DictStyleClient(collection)
    lookup = MongoChunkTextLookup(client=client, db_name="lina_rag")
    return collection, lookup


def test_mongo_lookup_upsert_calls_replace_one_with_upsert_true() -> None:
    collection, lookup = _new_collection_and_lookup()
    record = ChunkLookupRecord(chunk_id="a" * 40, text="hello", download_url="http://x")

    lookup.upsert(record)

    assert len(collection.replace_one_calls) == 1
    call = collection.replace_one_calls[0]
    assert call["filter"] == {"chunk_id": "a" * 40}
    assert call["upsert"] is True
    replacement = call["replacement"]
    assert replacement["chunk_id"] == "a" * 40
    assert replacement["text"] == "hello"
    assert replacement["download_url"] == "http://x"
    # updated_at은 어댑터가 적재 시점에 자동 부여한다 (db-schema §2.5 정합).
    assert isinstance(replacement["updated_at"], datetime)


def test_mongo_lookup_upsert_overwrites_existing_document() -> None:
    collection, lookup = _new_collection_and_lookup()
    lookup.upsert(ChunkLookupRecord(chunk_id="a" * 40, text="old", download_url=None))
    lookup.upsert(ChunkLookupRecord(chunk_id="a" * 40, text="new", download_url="http://y"))

    fetched = lookup.fetch("a" * 40)
    assert fetched is not None
    assert fetched.text == "new"
    assert fetched.download_url == "http://y"


def test_mongo_lookup_upsert_persists_null_download_url_for_page_chunks() -> None:
    collection, lookup = _new_collection_and_lookup()
    record = ChunkLookupRecord(chunk_id="a" * 40, text="본문", download_url=None)
    lookup.upsert(record)

    fetched = lookup.fetch("a" * 40)
    assert fetched is not None
    assert fetched.download_url is None


@dataclass
class _FakeReplaceOne:
    """``pymongo.ReplaceOne`` 대체 — 실 클래스의 내부 속성(``_filter`` 등)에 의존하지
    않고 호출 인자를 그대로 캡처해 검증한다. ``_FakeCollection.bulk_write`` 도 본 객체
    의 공개 속성(``filter``/``replacement``/``upsert``)을 사용한다.
    """

    filter: dict[str, Any]
    replacement: dict[str, Any]
    upsert: bool = False


def test_mongo_lookup_upsert_many_uses_bulk_write_with_replace_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pymongo

    # 실 ``pymongo.ReplaceOne`` 을 인자 캡처 dataclass 로 교체 — chunk_lookup.upsert_many
    # 가 함수 본문 내 lazy import 이므로 monkeypatch 가 효과를 본다. 다른 테스트는
    # bulk_write 경로를 사용하지 않아 영향 없음.
    monkeypatch.setattr(pymongo, "ReplaceOne", _FakeReplaceOne)

    collection, lookup = _new_collection_and_lookup()
    records = [
        ChunkLookupRecord(chunk_id="a" * 40, text="A", download_url=None),
        ChunkLookupRecord(chunk_id="b" * 40, text="B", download_url="http://b"),
    ]

    lookup.upsert_many(records)

    # bulk_write를 1회 호출하고, 각 record당 ReplaceOne(upsert=True) 1개씩 전달.
    assert len(collection.bulk_write_calls) == 1
    ops = collection.bulk_write_calls[0]
    assert len(ops) == 2
    for op in ops:
        assert op.upsert is True
        assert isinstance(op.replacement["updated_at"], datetime)
    chunk_ids = {op.filter["chunk_id"] for op in ops}
    assert chunk_ids == {"a" * 40, "b" * 40}


def test_mongo_lookup_upsert_many_empty_input_short_circuits() -> None:
    collection, lookup = _new_collection_and_lookup()

    lookup.upsert_many([])

    # 빈 입력은 bulk_write 호출 자체를 회피한다 (pymongo는 빈 ops에서 InvalidOperation).
    assert collection.bulk_write_calls == []
