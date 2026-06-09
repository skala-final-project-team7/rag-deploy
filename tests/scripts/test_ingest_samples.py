"""scripts/ingest_samples.py 회귀 — feature17c-4 첨부 청크 인덱싱 wiring.

collect_chunks 가 본문(chunk_page) + 첨부(chunk_attachment) 청크를 함께 수집하고,
미지원 유형(PDF/CSV)·파싱 실패는 적재를 중단하지 않고 안전하게 skip 하는지 검증한다.

NOTE: 첨부 청킹은 python-docx / openpyxl 로 실 sample 파일을 직접 읽는다(local_path).
sentence-transformers 등 embedding extras 는 불필요하므로 Qdrant·모델 없이 실행된다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

# 저장소 루트의 samples 디렉토리 (tests/scripts/ → parents[2] = repo root).
_SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


def test_collect_chunks_includes_attachment_chunks() -> None:
    """실 samples 적재 시 본문 청크에 더해 첨부 docx/xlsx 청크가 함께 수집된다."""
    from app.adapters.json_fixture import JsonFixtureSourceAdapter
    from app.schemas.enums import SourceType
    from scripts.ingest_samples import collect_chunks

    adapter = JsonFixtureSourceAdapter(samples_dir=_SAMPLES_DIR)
    collected = collect_chunks(adapter)

    # 본문 청크는 그대로 존재한다(첨부는 추가만).
    assert collected.body_chunk_count > 0
    # 첨부 4건(docx 2 / xlsx 2)에서 첨부 청크가 생성된다.
    assert collected.attachment_chunk_count > 0
    # 미지원 유형이 없으므로 skip 은 발생하지 않는다(샘플 첨부는 모두 docx/xlsx).
    assert collected.skipped_attachments == []

    attachment_chunks = [
        chunk for chunk in collected.chunks if chunk.metadata.source_type is SourceType.ATTACHMENT
    ]
    assert len(attachment_chunks) == collected.attachment_chunk_count
    # 첨부 청크는 부모 페이지로부터 ACL·webui_link 를 상속하고 attachment_filename 을 가진다.
    assert all(chunk.metadata.attachment_filename for chunk in attachment_chunks)

    # 4개 첨부 파일이 모두 1건 이상 청크를 생성했는지 확인한다.
    filenames = {chunk.metadata.attachment_filename for chunk in attachment_chunks}
    assert filenames == {
        "EKS_운영_상세_매뉴얼_v2.3.docx",
        "모니터링_메트릭_정의서_v1.4.xlsx",
        "EKS_노드_월간_사용량_통계_2026Q1.xlsx",
        "신규입사자_온보딩_체크리스트_2026.docx",
    }


def test_collect_chunks_total_is_body_plus_attachment() -> None:
    """수집 청크 총합은 본문 + 첨부 청크 수와 정확히 일치한다."""
    from app.adapters.json_fixture import JsonFixtureSourceAdapter
    from scripts.ingest_samples import collect_chunks

    adapter = JsonFixtureSourceAdapter(samples_dir=_SAMPLES_DIR)
    collected = collect_chunks(adapter)

    assert len(collected.chunks) == (collected.body_chunk_count + collected.attachment_chunk_count)
    # download_url 매핑은 페이지가 보유한 모든 첨부를 포함한다(skip 여부와 무관).
    assert len(collected.attachment_download_urls) >= 4


def _fake_adapter_with_attachment(attachment_id: str) -> SimpleNamespace:
    """fetch_pages() 가 첨부 1건을 가진 페이지 1건을 반환하는 최소 fake 어댑터."""
    attachment = SimpleNamespace(
        attachment_id=attachment_id,
        download_url=f"file:///tmp/{attachment_id}",
    )
    page = SimpleNamespace(
        page_id="P1",
        version_number=1,
        attachments=[attachment],
    )
    return SimpleNamespace(fetch_pages=lambda: [page])


def test_collect_chunks_skips_unsupported_attachment_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """미지원 유형(PDF/CSV → ValueError)은 적재를 중단하지 않고 skip 으로 기록된다."""
    import app.ingestion.chunker as chunker
    from scripts.ingest_samples import collect_chunks

    def _raise_unsupported(_attachment: object, _page: object) -> list:
        raise ValueError("feature4-A는 docx/xlsx만 지원한다")

    monkeypatch.setattr(chunker, "chunk_page", lambda _page: [])
    monkeypatch.setattr(chunker, "chunk_attachment", _raise_unsupported)

    collected = collect_chunks(_fake_adapter_with_attachment("att-pdf"))

    assert collected.attachment_chunk_count == 0
    assert len(collected.skipped_attachments) == 1
    assert collected.skipped_attachments[0][0] == "att-pdf"
    # download_url 은 skip 여부와 무관하게 수집된다.
    assert "att-pdf" in collected.attachment_download_urls


def test_collect_chunks_skips_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """파일 파싱 실패(임의 예외)도 적재 중단 없이 skip 으로 기록된다."""
    import app.ingestion.chunker as chunker
    from scripts.ingest_samples import collect_chunks

    def _raise_parse_error(_attachment: object, _page: object) -> list:
        raise RuntimeError("손상된 docx")

    monkeypatch.setattr(chunker, "chunk_page", lambda _page: [])
    monkeypatch.setattr(chunker, "chunk_attachment", _raise_parse_error)

    collected = collect_chunks(_fake_adapter_with_attachment("att-broken"))

    assert collected.attachment_chunk_count == 0
    assert len(collected.skipped_attachments) == 1
    assert collected.skipped_attachments[0][0] == "att-broken"
    assert "파싱 실패" in collected.skipped_attachments[0][1]
