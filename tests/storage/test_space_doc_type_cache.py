"""space_doc_type_cache 어댑터 단위 테스트 — FakeSpaceDocTypeCache get/set/upsert.

MySQL 실구현(MySQLSpaceDocTypeCache)은 sqlalchemy/DB 의존이라 본 단위 테스트는 외부
의존성 0 의 Fake 만 검증한다(문서 분석기 [Agent] 통합 4/4 의존 자산, featureI-4b 백포트).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.enums import DocType
from app.storage.space_doc_type_cache import FakeSpaceDocTypeCache, SpaceDocTypeEntry


def _entry(space_key: str = "ENG", *, dominant: DocType = DocType.INCIDENT) -> SpaceDocTypeEntry:
    return SpaceDocTypeEntry(
        space_key=space_key,
        dominant_doc_type=dominant,
        confidence=0.9,
        analyzed_at=datetime.now(UTC),
        sample_count=1,
        secondary_doc_types=[DocType.OPERATION],
    )


def test_get_miss_returns_none() -> None:
    cache = FakeSpaceDocTypeCache()
    assert cache.get("UNKNOWN") is None


def test_set_then_get_roundtrip() -> None:
    cache = FakeSpaceDocTypeCache()
    cache.set(_entry("ENG", dominant=DocType.FAQ))

    entry = cache.get("ENG")

    assert entry is not None
    assert entry.dominant_doc_type is DocType.FAQ
    assert entry.secondary_doc_types == [DocType.OPERATION]


def test_set_is_idempotent_upsert() -> None:
    """같은 space_key 재기록 시 갱신(멱등 upsert) — 엔트리 1건만 유지."""
    cache = FakeSpaceDocTypeCache()
    cache.set(_entry("ENG", dominant=DocType.FAQ))
    cache.set(_entry("ENG", dominant=DocType.ADR))

    entry = cache.get("ENG")

    assert entry is not None
    assert entry.dominant_doc_type is DocType.ADR  # 마지막 set 이 이긴다
    assert len(cache.entries) == 1
