"""ingestion_jobs 어댑터 검증 (feature6 Phase 2).

db-schema §2.3 정합 — 7필드 (page_id / attachment_id / stage / status / started_at /
finished_at / error) 적재 어댑터. ABC + Fake(in-memory) + Mongo(insert_one/insert_many)
3종을 chunk_lookup Phase 1 패턴으로 검증. 실 MongoDB 없이 pymongo collection을 mock
으로 대체해 외부 의존성 0.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

import pytest

from app.schemas.enums import IngestionStage, IngestionStatus
from app.storage.jobs import (
    FakeIngestionJobsRepository,
    IngestionJobRecord,
    IngestionJobsRepository,
    MongoIngestionJobsRepository,
)

# --- 픽스처 헬퍼 ---


def _record(
    *,
    page_id: str = "P1",
    attachment_id: str | None = None,
    stage: IngestionStage = IngestionStage.CHUNK,
    status: IngestionStatus = IngestionStatus.SUCCESS,
    error: str | None = None,
) -> IngestionJobRecord:
    started = datetime.fromisoformat("2026-05-18T08:00:00+00:00")
    finished = datetime.fromisoformat("2026-05-18T08:00:01+00:00")
    return IngestionJobRecord(
        page_id=page_id,
        attachment_id=attachment_id,
        stage=stage,
        status=status,
        started_at=started,
        finished_at=finished,
        error=error,
    )


# --- ABC / 값 객체 회귀 ---


def test_repository_abc_contracts_are_implemented() -> None:
    # 본 ABC 계약 자체에 대한 회귀 보호 — 향후 abstractmethod 추가 시 즉시 실패.
    assert issubclass(FakeIngestionJobsRepository, IngestionJobsRepository)
    assert issubclass(MongoIngestionJobsRepository, IngestionJobsRepository)


def test_record_is_frozen_dataclass() -> None:
    record = _record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.status = IngestionStatus.LOW_QUALITY_ATTACH  # type: ignore[misc]


def test_record_carries_all_seven_fields() -> None:
    """db-schema §2.3 의 7필드가 모두 정상 보존되어 회귀 차단."""
    record = _record(
        page_id="CONF-PAGE-1",
        attachment_id="CONF-ATT-2",
        stage=IngestionStage.UPSERT,
        status=IngestionStatus.LOW_QUALITY_ATTACH,
        error="text length 50 < 200",
    )
    assert record.page_id == "CONF-PAGE-1"
    assert record.attachment_id == "CONF-ATT-2"
    assert record.stage is IngestionStage.UPSERT
    assert record.status is IngestionStatus.LOW_QUALITY_ATTACH
    assert record.error == "text length 50 < 200"
    assert isinstance(record.started_at, datetime)
    assert isinstance(record.finished_at, datetime)


# --- Fake 동작 ---


def test_fake_record_appends_in_call_order() -> None:
    repo = FakeIngestionJobsRepository()
    repo.record(_record(page_id="P1"))
    repo.record(_record(page_id="P2"))
    assert [r.page_id for r in repo.records] == ["P1", "P2"]


def test_fake_record_many_appends_batch_in_order() -> None:
    repo = FakeIngestionJobsRepository()
    batch = [_record(page_id=f"P{i}") for i in range(3)]
    repo.record_many(batch)
    assert [r.page_id for r in repo.records] == ["P0", "P1", "P2"]


def test_fake_record_many_empty_input_is_noop() -> None:
    repo = FakeIngestionJobsRepository()
    repo.record_many([])
    assert repo.records == []


def test_fake_records_attribute_is_a_copy_to_prevent_external_mutation() -> None:
    """외부에서 records 리스트를 수정해도 어댑터 내부 상태가 오염되지 않아야 한다."""
    repo = FakeIngestionJobsRepository()
    repo.record(_record(page_id="P1"))
    snapshot = repo.records
    snapshot.clear()
    # snapshot 변경이 내부 적재 기록을 흐트러뜨리지 않음 (방어 복사 회귀 보호)
    assert len(repo.records) == 1


# --- Mongo 어댑터 (pymongo mock) ---


class _FakeCollection:
    """pymongo collection 최소 mock — insert_one + insert_many.

    호출 인자를 그대로 캡처해 단언한다 (실 pymongo 내부 동작 의존성 회피).
    """

    def __init__(self) -> None:
        self.insert_one_calls: list[dict[str, Any]] = []
        self.insert_many_calls: list[list[dict[str, Any]]] = []

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.insert_one_calls.append(dict(doc))

    def insert_many(self, docs: list[dict[str, Any]]) -> None:
        self.insert_many_calls.append([dict(d) for d in docs])


class _FakeDB:
    def __init__(self, collection: _FakeCollection) -> None:
        self._collection = collection

    def __getitem__(self, collection_name: str) -> _FakeCollection:
        return self._collection


class _DictStyleClient:
    """`client[db_name][collection_name]` 두 단계 인덱싱 지원 (chunk_lookup 패턴 재사용)."""

    def __init__(self, collection: _FakeCollection) -> None:
        self._db = _FakeDB(collection)

    def __getitem__(self, db_name: str) -> _FakeDB:
        return self._db


def _new_collection_and_repo() -> tuple[_FakeCollection, MongoIngestionJobsRepository]:
    collection = _FakeCollection()
    client = _DictStyleClient(collection)
    repo = MongoIngestionJobsRepository(client=client, db_name="lina_rag")
    return collection, repo


def test_mongo_record_calls_insert_one_with_seven_fields() -> None:
    collection, repo = _new_collection_and_repo()
    record = _record(
        page_id="CONF-PAGE-1",
        attachment_id="CONF-ATT-2",
        stage=IngestionStage.EMBED,
        status=IngestionStatus.SUCCESS,
    )

    repo.record(record)

    assert len(collection.insert_one_calls) == 1
    doc = collection.insert_one_calls[0]
    # 7필드 모두 doc에 동봉 + enum은 문자열로 직렬화
    assert doc["page_id"] == "CONF-PAGE-1"
    assert doc["attachment_id"] == "CONF-ATT-2"
    assert doc["stage"] == "embed"
    assert doc["status"] == "SUCCESS"
    assert isinstance(doc["started_at"], datetime)
    assert isinstance(doc["finished_at"], datetime)
    assert doc["error"] is None


def test_mongo_record_preserves_null_attachment_id_for_body_jobs() -> None:
    collection, repo = _new_collection_and_repo()
    repo.record(_record(attachment_id=None))
    assert collection.insert_one_calls[0]["attachment_id"] is None


def test_mongo_record_many_uses_insert_many_in_batch() -> None:
    collection, repo = _new_collection_and_repo()
    batch = [_record(page_id=f"P{i}") for i in range(3)]

    repo.record_many(batch)

    # insert_many 1회 + 배치 안에 3 docs.
    assert len(collection.insert_many_calls) == 1
    docs = collection.insert_many_calls[0]
    assert [d["page_id"] for d in docs] == ["P0", "P1", "P2"]
    # insert_one 은 호출되지 않음 (배치 효율)
    assert collection.insert_one_calls == []


def test_mongo_record_many_empty_input_short_circuits() -> None:
    """빈 입력에서는 pymongo insert_many 가 InvalidOperation 을 던지므로 어댑터가
    short-circuit 해 호출자(그래프 노드)가 별도 분기 없이 호출하도록 한다."""
    collection, repo = _new_collection_and_repo()

    repo.record_many([])

    assert collection.insert_many_calls == []
    assert collection.insert_one_calls == []


def test_mongo_record_serializes_error_string() -> None:
    """error 필드는 IngestionStatus 가 SUCCESS 가 아닌 경우의 상세 사유 문구를 보존한다."""
    collection, repo = _new_collection_and_repo()
    repo.record(
        _record(
            status=IngestionStatus.UNSUPPORTED_ATTACH_TYPE,
            error="미지원 mime: image/png",
        )
    )
    doc = collection.insert_one_calls[0]
    assert doc["status"] == "UNSUPPORTED_ATTACH_TYPE"
    assert doc["error"] == "미지원 mime: image/png"
