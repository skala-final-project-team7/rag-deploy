"""Ingestion LangGraph 조립 검증 (feature6 Phase 4).

설계서 §3.1 + Big Picture 정합 — 단일 PageObject 를 analyze → chunk → embed_upsert
3 노드를 거쳐 Qdrant + chunk_lookup 에 적재한다. 각 단계 종료 시 IngestionJobs 적재.
Agent 노드(문서 분석기)는 stub. 본문/첨부 모두 동일 그래프에서 처리.

외부 의존성 0 — :memory: Qdrant + Fake everything + chunk_attachment 주입 가능 (파일
시스템 의존성 회피).
"""

from __future__ import annotations

import warnings
from datetime import datetime

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("langgraph")

from app.config import Settings  # noqa: E402
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder  # noqa: E402
from app.pipeline.ingestion_graph import (  # noqa: E402
    IngestionGraphDeps,
    build_ingestion_graph,
    manage_document_analyzer,
    run_ingestion,
)
from app.schemas.chunk import Chunk, ChunkMetadata  # noqa: E402
from app.schemas.enums import (  # noqa: E402
    AttachmentType,
    ExtractedFormat,
    IngestionStage,
    IngestionStatus,
    SourceType,
)
from app.schemas.page_object import Attachment, PageObject  # noqa: E402
from app.schemas.rag_state import IngestionState  # noqa: E402
from app.storage.chunk_lookup import FakeChunkTextLookup  # noqa: E402
from app.storage.jobs import FakeIngestionJobsRepository  # noqa: E402
from app.storage.mongo_cache import FakeEmbeddingCache  # noqa: E402
from app.storage.qdrant_client import QdrantPoolStore  # noqa: E402

warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 픽스처 헬퍼 ---


def _settings() -> Settings:
    return Settings(_env_file=None)


def _page(
    *,
    page_id: str = "P1",
    title: str = "EKS 운영 가이드",
    body_html: str = (
        "<h1>개요</h1><p>본 페이지는 EKS 클러스터 운영 절차를 정의합니다. "
        "워커 노드 추가·롤링 업데이트·배포 분리 등 핵심 작업의 단계별 흐름을 다룹니다. "
        "이 문단은 본문 청킹 검증을 위한 최소 길이를 충족합니다.</p>"
    ),
    attachments: list[Attachment] | None = None,
    allowed_groups: list[str] | None = None,
    allowed_users: list[str] | None = None,
) -> PageObject:
    return PageObject(
        page_id=page_id,
        space_key="CLOUD",
        title=title,
        body_html=body_html,
        version_number=1,
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        allowed_groups=allowed_groups if allowed_groups is not None else ["space:CLOUD"],
        allowed_users=allowed_users if allowed_users is not None else [],
        webui_link="/display/CLOUD/eks",
        labels=["eks", "운영"],
        ancestors=["Cloud 운영 문서"],
        attachments=attachments or [],
    )


def _attachment(
    *,
    attachment_id: str = "ATT-1",
    filename: str = "runbook.docx",
    mime_type: str = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    extracted_text: str | None = None,
) -> Attachment:
    text = (
        extracted_text
        if extracted_text is not None
        else ("본 첨부는 운영 매뉴얼의 발췌입니다. " * 10)
    )
    return Attachment(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        extracted_text=text,
        extracted_format=ExtractedFormat.RAW_TEXT,
        download_url=f"https://confluence/download/{attachment_id}",
        parent_page_id="P1",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
    )


def _fake_attachment_chunk(
    attachment: Attachment, page: PageObject, attachment_type: AttachmentType | str | None
) -> list[Chunk]:
    """파일 시스템 의존성 회피용 fake chunk_attachment — 1 청크 반환."""
    resolved = (
        attachment_type if isinstance(attachment_type, AttachmentType) else AttachmentType.DOCX
    )
    if resolved in (AttachmentType.PDF, AttachmentType.CSV):
        # ValueError 격리 경로 검증용 — 실제 chunk_attachment 는 pdf/csv 를 지원하지만
        # (feature4-B 완료), 미지원·판별 실패 시 동일하게 ValueError 를 던진다.
        raise ValueError(f"{resolved.value} 첨부는 본 fake 가 미지원으로 시뮬레이션")
    meta = ChunkMetadata(
        chunk_id="b" * 40,
        page_id=page.page_id,
        page_title=page.title,
        section_header="첨부",
        section_path=f"{page.title} > 첨부",
        chunk_index=0,
        labels=page.labels,
        doc_type=resolved,
        space_key=page.space_key,
        allowed_groups=page.allowed_groups,
        allowed_users=page.allowed_users,
        webui_link=page.webui_link,
        last_modified=page.last_modified,
        source_type=SourceType.ATTACHMENT,
        attachment_id=attachment.attachment_id,
        attachment_filename=attachment.filename,
        attachment_mime=attachment.mime_type,
        extracted_format=attachment.extracted_format,
        token_count=80,
    )
    return [Chunk(text=attachment.extracted_text[:200], metadata=meta)]


@pytest.fixture()
def deps() -> IngestionGraphDeps:
    settings = _settings()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    return IngestionGraphDeps(
        dense_embedder=FakeDenseEmbedder(dimension=8),
        sparse_embedder=FakeSparseEmbedder(),
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=FakeChunkTextLookup(),
        jobs=FakeIngestionJobsRepository(),
        chunk_attachment_fn=_fake_attachment_chunk,
    )


# --- IngestionGraphDeps 기본값 회귀 ---


def test_deps_default_document_analyzer_is_real_adapter() -> None:
    """document_analyzer_node 기본값이 실 어댑터(manage_document_analyzer) — Agent 통합 4/4."""
    settings = _settings()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    deps = IngestionGraphDeps(
        dense_embedder=FakeDenseEmbedder(dimension=8),
        sparse_embedder=FakeSparseEmbedder(),
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=FakeChunkTextLookup(),
        jobs=FakeIngestionJobsRepository(),
    )
    assert deps.document_analyzer_node is manage_document_analyzer


# --- 본문만 (첨부 없음) ---


def test_run_ingestion_indexes_body_only_page(deps: IngestionGraphDeps) -> None:
    """첨부 없는 페이지 → 본문 청크만 적재."""
    state = IngestionState(page=_page())

    final = run_ingestion(state, graph=build_ingestion_graph(deps))

    assert len(final.chunks) >= 1
    # 모든 청크가 본문(source_type=page)
    assert all(c.metadata.source_type is SourceType.PAGE for c in final.chunks)
    # Qdrant 적재 확인
    assert deps.store.scroll_page_ids() == {"P1"}
    assert deps.store.scroll_attachment_ids() == set()


# --- 본문 + 유효 docx 첨부 ---


def test_run_ingestion_indexes_body_and_valid_attachment(deps: IngestionGraphDeps) -> None:
    """유효 docx 첨부 → 본문 + 첨부 청크 모두 적재."""
    page = _page(attachments=[_attachment(attachment_id="ATT-1")])
    state = IngestionState(page=page)

    final = run_ingestion(state, graph=build_ingestion_graph(deps))

    source_types = {c.metadata.source_type for c in final.chunks}
    assert SourceType.PAGE in source_types
    assert SourceType.ATTACHMENT in source_types
    # Qdrant 적재 확인 — 본문 + 첨부 모두
    assert deps.store.scroll_page_ids() == {"P1"}
    assert deps.store.scroll_attachment_ids() == {"ATT-1"}


# --- 미지원 첨부 (UNSUPPORTED_ATTACH_TYPE) ---


def test_unsupported_mime_attachment_skipped_with_jobs_record(deps: IngestionGraphDeps) -> None:
    """미지원 mime(png) 첨부 → 본문만 적재 + jobs 에 UNSUPPORTED_ATTACH_TYPE 기록."""
    page = _page(
        attachments=[
            _attachment(attachment_id="ATT-png", filename="diagram.png", mime_type="image/png")
        ]
    )
    state = IngestionState(page=page)

    run_ingestion(state, graph=build_ingestion_graph(deps))

    # 첨부 청크는 적재되지 않음
    assert deps.store.scroll_attachment_ids() == set()
    # jobs 에 첨부 단위 잡 기록
    attach_jobs = [r for r in deps.jobs.records if r.attachment_id == "ATT-png"]
    assert any(r.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE for r in attach_jobs)
    assert any(r.stage is IngestionStage.ANALYZE for r in attach_jobs)


# --- 저품질 첨부 (LOW_QUALITY_ATTACH) ---


def test_low_quality_attachment_skipped_with_jobs_record(deps: IngestionGraphDeps) -> None:
    """200자 미만 첨부 → 본문만 적재 + jobs 에 LOW_QUALITY_ATTACH 기록."""
    page = _page(attachments=[_attachment(attachment_id="ATT-tiny", extracted_text="너무 짧음")])
    state = IngestionState(page=page)

    run_ingestion(state, graph=build_ingestion_graph(deps))

    assert deps.store.scroll_attachment_ids() == set()
    attach_jobs = [r for r in deps.jobs.records if r.attachment_id == "ATT-tiny"]
    assert any(r.status is IngestionStatus.LOW_QUALITY_ATTACH for r in attach_jobs)


# --- chunk_attachment ValueError catch (첨부 단위 격리) ---


def test_pdf_attachment_skipped_with_jobs_record(deps: IngestionGraphDeps) -> None:
    """첨부 청킹 ValueError → catch 후 잡 기록 + 본문은 정상 적재.

    fake 청커가 PDF 를 미지원으로 시뮬레이션한다(실제 chunk_attachment 는 pdf 지원 —
    feature4-B 완료. 본 테스트의 검증 대상은 ValueError 격리 경로다)."""
    page = _page(
        attachments=[
            _attachment(
                attachment_id="ATT-pdf",
                filename="runbook.pdf",
                mime_type="application/pdf",
            )
        ]
    )
    state = IngestionState(page=page)

    run_ingestion(state, graph=build_ingestion_graph(deps))

    # 본문은 정상 적재
    assert deps.store.scroll_page_ids() == {"P1"}
    # 첨부 청크는 적재 안 됨
    assert deps.store.scroll_attachment_ids() == set()
    # jobs 에 첨부 단위 잡 기록 — 청킹 실패라 status 는 SUCCESS 가 아니어야 한다.
    attach_jobs = [r for r in deps.jobs.records if r.attachment_id == "ATT-pdf"]
    assert attach_jobs, "PDF 첨부 잡 기록이 필요하다"
    assert all(r.status is not IngestionStatus.SUCCESS for r in attach_jobs)
    # ATTACH_ENCRYPTED 표식 없는 일반 ValueError 는 UNSUPPORTED_ATTACH_TYPE 로 기록(P4 대비).
    assert any(r.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE for r in attach_jobs)


# --- 암호화 첨부 (ATTACH_ENCRYPTED — 2026-06-10 코드 리뷰 P4) ---


def _encrypted_attachment_chunk(
    attachment: Attachment, page: PageObject, attachment_type: AttachmentType | str | None
) -> list[Chunk]:
    """암호화 PDF 상당 — 실제 chunk_attachment 의 ATTACH_ENCRYPTED 표식 ValueError 재현."""
    raise ValueError("ATTACH_ENCRYPTED: 암호화된 PDF — 텍스트 추출 불가")


def test_encrypted_attachment_recorded_as_attach_encrypted() -> None:
    """첨부 청킹 ValueError 에 ATTACH_ENCRYPTED 표식이 있으면 UNSUPPORTED_ATTACH_TYPE
    대신 ATTACH_ENCRYPTED 로 잡 기록한다(본문은 정상 적재 — ingestion 워커 매핑 정합)."""
    settings = _settings()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    deps = IngestionGraphDeps(
        dense_embedder=FakeDenseEmbedder(dimension=8),
        sparse_embedder=FakeSparseEmbedder(),
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=FakeChunkTextLookup(),
        jobs=FakeIngestionJobsRepository(),
        chunk_attachment_fn=_encrypted_attachment_chunk,
    )
    page = _page(attachments=[_attachment(attachment_id="ATT-enc")])
    state = IngestionState(page=page)

    run_ingestion(state, graph=build_ingestion_graph(deps))

    # 본문은 정상 적재, 암호화 첨부는 미적재.
    assert deps.store.scroll_page_ids() == {"P1"}
    assert deps.store.scroll_attachment_ids() == set()
    attach_jobs = [r for r in deps.jobs.records if r.attachment_id == "ATT-enc"]
    assert attach_jobs, "암호화 첨부 잡 기록이 필요하다"
    assert any(r.status is IngestionStatus.ATTACH_ENCRYPTED for r in attach_jobs)
    assert all(r.status is not IngestionStatus.UNSUPPORTED_ATTACH_TYPE for r in attach_jobs)
    # 에러 원문(표식 포함)이 잡 기록에 보존된다 — 운영 디버깅용.
    encrypted_job = next(r for r in attach_jobs if r.status is IngestionStatus.ATTACH_ENCRYPTED)
    assert "ATTACH_ENCRYPTED" in (encrypted_job.error or "")


# --- INVALID_ACL 게이트 (2026-06-10 코드 리뷰 P2-8) ---


def test_acl_missing_page_skips_chunk_and_upsert_with_invalid_acl_record(
    deps: IngestionGraphDeps,
) -> None:
    """ACL 누락 페이지(allowed_groups·allowed_users 모두 빈 배열) → analyze 단계에서
    INVALID_ACL 로 기록하고 조건부 엣지로 chunk/upsert 를 건너뛴다(fail-closed)."""
    page = _page(allowed_groups=[], allowed_users=[])
    assert page.is_acl_missing  # 전제 — PageObject 의 INVALID_ACL 식별 신호.
    state = IngestionState(page=page)

    final = run_ingestion(state, graph=build_ingestion_graph(deps))

    # 상태 — analyze 에서 INVALID_ACL 로 종료, 청크 미생성.
    assert final.status is IngestionStatus.INVALID_ACL
    assert final.stage is IngestionStage.ANALYZE
    assert final.chunks == []
    # 적재 자체가 일어나지 않는다 — upsert 스킵(검색 노출 차단).
    assert deps.store.scroll_page_ids() == set()
    assert deps.store.scroll_attachment_ids() == set()
    # 잡 기록 — ANALYZE/INVALID_ACL 1건뿐, CHUNK/UPSERT stage 기록 없음.
    assert [(r.stage, r.status) for r in deps.jobs.records] == [
        (IngestionStage.ANALYZE, IngestionStatus.INVALID_ACL)
    ]
    assert deps.jobs.records[0].error  # 색인 제외 사유 문구 동봉.


def test_acl_present_page_not_gated_by_invalid_acl(deps: IngestionGraphDeps) -> None:
    """allowed_users 만 있어도(그룹 없음) ACL 보유로 간주 — 게이트 미적용 회귀 보호."""
    page = _page(allowed_groups=[], allowed_users=["taesung"])
    assert not page.is_acl_missing
    state = IngestionState(page=page)

    final = run_ingestion(state, graph=build_ingestion_graph(deps))

    assert final.status is IngestionStatus.SUCCESS
    assert final.chunks
    assert deps.store.scroll_page_ids() == {"P1"}


# --- jobs 적재 stage 회귀 ---


def test_jobs_record_all_three_stages_on_success(deps: IngestionGraphDeps) -> None:
    """첨부 없는 정상 페이지 → analyze + chunk + upsert 3 stage 모두 SUCCESS 기록."""
    state = IngestionState(page=_page())

    run_ingestion(state, graph=build_ingestion_graph(deps))

    # 페이지 단위 잡 (attachment_id=None) — 3 stage 모두 SUCCESS
    page_jobs = [r for r in deps.jobs.records if r.attachment_id is None]
    stages = {r.stage for r in page_jobs}
    assert IngestionStage.ANALYZE in stages
    assert IngestionStage.CHUNK in stages
    assert IngestionStage.UPSERT in stages
    assert all(r.status is IngestionStatus.SUCCESS for r in page_jobs)


def test_jobs_record_started_and_finished_timestamps(deps: IngestionGraphDeps) -> None:
    """각 잡 기록은 started_at ≤ finished_at 인 datetime 을 보유."""
    state = IngestionState(page=_page())

    run_ingestion(state, graph=build_ingestion_graph(deps))

    assert deps.jobs.records  # 잡 0건 회귀 방지
    for record in deps.jobs.records:
        assert isinstance(record.started_at, datetime)
        assert isinstance(record.finished_at, datetime)
        assert record.started_at <= record.finished_at


# --- IngestionState 흐름 회귀 ---


def test_final_state_carries_doc_type_and_chunks(deps: IngestionGraphDeps) -> None:
    """그래프 종료 후 IngestionState 에 doc_type 채워짐 + chunks 적재."""
    state = IngestionState(page=_page())

    final = run_ingestion(state, graph=build_ingestion_graph(deps))

    assert final.doc_type == "operation"  # 기본 Fake 분류기 → operation (직전 stub 동작 정합)
    assert final.chunks  # 본문 청크가 있어야 함
    assert final.page.page_id == "P1"  # 입력 페이지 보존


# --- 그래프 컴파일 회귀 ---


def test_build_ingestion_graph_compiles_without_node_state_key_collision(
    deps: IngestionGraphDeps,
) -> None:
    """LangGraph 1.x 제약 — 노드명이 IngestionState 필드명과 같으면 ValueError.

    회귀 보호: 본 호출이 그대로 통과해야 한다. 향후 노드 추가 시 IngestionState
    필드(page/doc_type/chunks/stage/status/error) 와 같은 이름을 쓰면 본 테스트가
    즉시 실패한다.
    """
    graph = build_ingestion_graph(deps)
    assert graph is not None


# --- 비-ValueError 라이브러리 예외 격리 (배포 전 점검 2026-06-10 — worker P1-2 정합) ---


def _crashing_attachment_chunk(
    attachment: Attachment, page: PageObject, attachment_type: AttachmentType | str | None
) -> list[Chunk]:
    """손상 파일 상당 — 추출 라이브러리 고유 예외(비 ValueError)를 재현한다."""
    raise RuntimeError("PackageNotFoundError 상당 — 손상 docx")


def test_non_value_error_attachment_isolated_with_jobs_record() -> None:
    """첨부 청킹의 비-ValueError 예외도 첨부 단위로 격리된다(본문·잡은 정상).

    손상 파일은 openpyxl InvalidFileException / python-docx PackageNotFoundError /
    PyMuPDF FileDataError 등 ValueError 가 아닌 예외를 던진다 — 격리 없이는 첨부 1건이
    페이지 전체(본문 포함) 적재를 죽인다(ingestion chunking_worker P1-2 와 동일 뿌리).
    """
    settings = _settings()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    deps = IngestionGraphDeps(
        dense_embedder=FakeDenseEmbedder(dimension=8),
        sparse_embedder=FakeSparseEmbedder(),
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=FakeChunkTextLookup(),
        jobs=FakeIngestionJobsRepository(),
        chunk_attachment_fn=_crashing_attachment_chunk,
    )
    page = _page(
        attachments=[
            _attachment(
                attachment_id="ATT-broken",
                filename="broken.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ]
    )
    run_ingestion(IngestionState(page=page), graph=build_ingestion_graph(deps))

    # 본문은 정상 적재되고 첨부 청크만 빠진다.
    assert deps.store.scroll_page_ids() == {"P1"}
    assert deps.store.scroll_attachment_ids() == set()
    attach_jobs = [r for r in deps.jobs.records if r.attachment_id == "ATT-broken"]
    assert attach_jobs, "격리된 첨부도 잡 기록은 남아야 한다"
    assert any(r.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE for r in attach_jobs)
    assert any("RuntimeError" in (r.error or "") for r in attach_jobs)
