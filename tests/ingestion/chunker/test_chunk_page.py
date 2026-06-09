"""chunk_page — 페이지 → Chunk 목록 엔트리 + samples 92페이지 통합 검증.

데이터 → 청크 단계가 실제 샘플 데이터에서 동작하는지 확인한다.
"""

from pathlib import Path

from app.adapters.json_fixture import JsonFixtureSourceAdapter
from app.ingestion.chunker.body import chunk_page, infer_doc_type
from app.schemas.chunk import Chunk
from app.schemas.enums import DocType, SourceType
from app.schemas.page_object import PageObject

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"

# incident 블록은 원자성 유지 → 작아도 병합되지 않아 분할 개수가 결정적이다
_INCIDENT_PAGE = PageObject(
    page_id="CONF-PAGE-1",
    space_key="CLOUD",
    title="EKS 노드 장애 대응",
    body_html="<h2>타임라인</h2><p>14:02 장애 발생</p><h2>원인</h2><p>IAM 정책 누락</p>",
    version_number=1,
    last_modified="2026-04-22T08:15:00+09:00",
    allowed_groups=["space:CLOUD"],
    allowed_users=[],
    webui_link="/display/CLOUD/eks",
    labels=["eks", "장애대응"],
    ancestors=["Cloud 운영 문서"],
)


def test_chunk_page_returns_chunks_with_sequential_index() -> None:
    chunks = chunk_page(_INCIDENT_PAGE, doc_type=DocType.INCIDENT)
    assert len(chunks) == 2
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.metadata.chunk_index for c in chunks] == [0, 1]
    assert all(c.metadata.source_type is SourceType.PAGE for c in chunks)
    assert all(c.metadata.page_id == "CONF-PAGE-1" for c in chunks)
    assert all(c.metadata.doc_type == "incident" for c in chunks)


def test_chunk_page_infers_doc_type_when_omitted() -> None:
    # doc_type 미지정 시 라벨 기반 추정 (PoC). labels=["eks","장애대응"] → incident
    chunks = chunk_page(_INCIDENT_PAGE)
    assert len(chunks) >= 1
    assert all(c.metadata.doc_type == "incident" for c in chunks)


def test_infer_doc_type_from_labels() -> None:
    operation_page = _INCIDENT_PAGE.model_copy(update={"labels": ["eks", "운영매뉴얼"]})
    adr_page = _INCIDENT_PAGE.model_copy(update={"labels": ["adr"]})
    assert infer_doc_type(_INCIDENT_PAGE) is DocType.INCIDENT
    assert infer_doc_type(adr_page) is DocType.ADR
    # 매칭 라벨이 없으면 operation 기본값
    assert infer_doc_type(operation_page) is DocType.OPERATION


def test_samples_all_92_pages_chunk_without_error() -> None:
    adapter = JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)
    pages = list(adapter.fetch_pages())
    assert len(pages) == 92

    total_chunks = 0
    for page in pages:
        chunks = chunk_page(page)  # doc_type 라벨 기반 추정
        assert len(chunks) >= 1, f"page {page.page_id} produced no chunks"
        for index, chunk in enumerate(chunks):
            assert chunk.text.strip(), f"page {page.page_id} chunk {index} is empty"
            meta = chunk.metadata
            assert meta.chunk_index == index
            assert meta.section_header, "section_header must not be empty"
            assert meta.page_id == page.page_id
            assert meta.token_count > 0
        total_chunks += len(chunks)

    # 92개 페이지가 그보다 많은 청크로 분할된다
    assert total_chunks > 92
