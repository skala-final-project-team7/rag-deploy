"""문서 분석기 [Agent] 단위 테스트 — doc_type 판별·캐싱·폴백 + 그래프 노드 어댑터.

LLM(OpenAI)·MySQL 은 Fake 로 대체한다(FakeDocTypeClassifier / FakeSpaceDocTypeCache).
스페이스 단위 1회 판별(캐시 히트 시 LLM 재호출 없음)·저신뢰/예외 폴백, 그리고
``manage_document_analyzer`` 노드가 state.doc_type 을 채우는지(기본 Fake / 주입 analyzer)
를 검증한다 (Agent 통합 4/4).
"""

from __future__ import annotations

from datetime import datetime

from app.ingestion.document_analyzer import (
    FALLBACK_DOC_TYPE,
    DocTypeClassification,
    DocumentAnalyzer,
    FakeDocTypeClassifier,
)
from app.pipeline.ingestion_graph import manage_document_analyzer
from app.schemas.enums import DocType
from app.schemas.page_object import PageObject
from app.schemas.rag_state import IngestionState
from app.storage.space_doc_type_cache import FakeSpaceDocTypeCache


def _page(page_id: str = "page-1", *, space_key: str = "ENG") -> PageObject:
    return PageObject(
        page_id=page_id,
        space_key=space_key,
        title="Incident report",
        body_html="<h2>Outage</h2><p>The service went down at 02:00 and was restored.</p>",
        version_number=1,
        last_modified=datetime.fromisoformat("2026-05-14T01:00:00+00:00"),
        allowed_groups=["space:ENG"],
        allowed_users=[],
        webui_link="/wiki/page-1",
        labels=["operation"],  # 라벨 폴백이면 operation 이 되지만, resolver 가 우선한다.
    )


def _analyzer(classifier: FakeDocTypeClassifier, cache: FakeSpaceDocTypeCache) -> DocumentAnalyzer:
    return DocumentAnalyzer(classifier=classifier, cache=cache)


# --- DocumentAnalyzer.resolve_doc_type ---


def test_cache_miss_classifies_and_caches() -> None:
    classifier = FakeDocTypeClassifier(
        result=DocTypeClassification(dominant=DocType.INCIDENT, confidence=0.92)
    )
    cache = FakeSpaceDocTypeCache()
    analyzer = _analyzer(classifier, cache)

    resolved = analyzer.resolve_doc_type(_page())

    assert resolved is DocType.INCIDENT
    assert classifier.calls == 1
    entry = cache.get("ENG")
    assert entry is not None
    assert entry.dominant_doc_type is DocType.INCIDENT
    assert entry.sample_count == 1


def test_cache_hit_reuses_without_reclassifying() -> None:
    classifier = FakeDocTypeClassifier(
        result=DocTypeClassification(dominant=DocType.FAQ, confidence=0.9)
    )
    cache = FakeSpaceDocTypeCache()
    analyzer = _analyzer(classifier, cache)

    first = analyzer.resolve_doc_type(_page("page-1", space_key="ENG"))
    second = analyzer.resolve_doc_type(_page("page-2", space_key="ENG"))

    assert first is DocType.FAQ
    assert second is DocType.FAQ
    # 스페이스 1회 판별 — 두 번째 페이지는 캐시 히트라 LLM 재호출이 없다.
    assert classifier.calls == 1


def test_low_confidence_falls_back_to_operation_and_caches() -> None:
    classifier = FakeDocTypeClassifier(
        result=DocTypeClassification(dominant=DocType.ADR, confidence=0.3)
    )
    cache = FakeSpaceDocTypeCache()
    analyzer = _analyzer(classifier, cache)

    resolved = analyzer.resolve_doc_type(_page())

    assert resolved is FALLBACK_DOC_TYPE  # operation
    entry = cache.get("ENG")
    assert entry is not None
    assert entry.dominant_doc_type is FALLBACK_DOC_TYPE
    assert entry.confidence == 0.3


def test_classifier_failure_falls_back_without_caching() -> None:
    classifier = FakeDocTypeClassifier(error=RuntimeError("llm timeout"))
    cache = FakeSpaceDocTypeCache()
    analyzer = _analyzer(classifier, cache)

    resolved = analyzer.resolve_doc_type(_page())

    assert resolved is FALLBACK_DOC_TYPE
    # 일시적 실패는 캐싱하지 않아 다음 페이지에서 재시도된다.
    assert cache.get("ENG") is None


# --- manage_document_analyzer 노드 어댑터 (Agent 통합 4/4) ---


def test_node_default_fills_operation_via_real_analyzer() -> None:
    """analyzer 미주입(PoC) — 결정론 Fake 분류기로 doc_type="operation" (직전 stub 정합)."""
    state = IngestionState(page=_page())

    result = manage_document_analyzer(state)

    assert result.doc_type == DocType.OPERATION.value
    assert result.page.page_id == "page-1"  # 입력 페이지 보존


def test_node_uses_injected_analyzer_classification() -> None:
    """analyzer 주입(운영 경로 정합) — 주입한 분류 결과(incident)가 state.doc_type 에 반영."""
    analyzer = _analyzer(
        FakeDocTypeClassifier(
            result=DocTypeClassification(dominant=DocType.INCIDENT, confidence=0.95)
        ),
        FakeSpaceDocTypeCache(),
    )
    state = IngestionState(page=_page())

    result = manage_document_analyzer(state, analyzer=analyzer)

    assert result.doc_type == DocType.INCIDENT.value


def test_node_low_confidence_injected_analyzer_falls_back() -> None:
    """주입 analyzer 가 저신뢰(<0.6) → operation 폴백이 state.doc_type 에 반영."""
    analyzer = _analyzer(
        FakeDocTypeClassifier(result=DocTypeClassification(dominant=DocType.ADR, confidence=0.2)),
        FakeSpaceDocTypeCache(),
    )
    state = IngestionState(page=_page())

    result = manage_document_analyzer(state, analyzer=analyzer)

    assert result.doc_type == DocType.OPERATION.value
