"""samples/*.json → 운영 Qdrant 1회 적재 CLI [Pipeline 데모 도구].

--------------------------------------------------
작성자 : 최태성
작성목적 : Mode B 시연을 위한 1회용 CLI. ``build_real_deps`` 는 query path 만
          wiring 하고 samples 자동 인덱싱은 수행하지 않으므로 (운영은 별도 ingestion
          파이프라인이 적재 가정), 시연 환경에서는 본 스크립트로 samples 데이터를
          운영 Qdrant 에 한 번 적재한다. PoC ``_ingest_samples`` 와 동일 흐름을
          운영 E5 / BM25 / Qdrant.from_settings 어댑터에 적용한다.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, Mode B 시연용 1회 적재 CLI.
  - 2026-05-20, feature17c-4, 첨부 청크 인덱싱 wiring — 기존에는 chunk_page(본문)만
    적재하고 chunk_attachment(첨부)를 호출하지 않아 첨부 내용이 Qdrant 에 전혀
    인덱싱되지 않았다(첨부 활용 평가 8건 중 6건 검색 0건). 청크 수집 로직을
    테스트 가능한 collect_chunks 헬퍼로 분리하고, 각 첨부에 chunk_attachment 를
    호출한다. 미지원 유형(PDF/CSV=feature4-B 미구현)·파싱 실패는 안전 skip 한다.
    NOTE: attachment_analyzer 게이트는 extracted_text 가 채워졌다고 가정하는데
    데모 어댑터는 이를 비워 두므로(실 운영 ingestion 그래프의 추출 어댑터 책임),
    데모/평가 1회 적재는 chunk_attachment 가 local_path 로 파일을 직접 읽는
    feature4-A 데모 용법을 따른다(분석기 게이트 우회, 분석기 무수정).
--------------------------------------------------
[호환성]
  - Python 3.11.x, embedding extras 필수 (sentence-transformers + fastembed).
  - 사용법:
        cd ~/skala-final/rag
        source .venv/bin/activate
        docker compose up -d qdrant        # Qdrant 컨테이너 (docker-compose.yml)
        python scripts/ingest_samples.py   # samples → 운영 Qdrant 적재
        # 그 후 RAG_USE_REAL_ADAPTERS=true uvicorn app.api.main:app --port 8000
  - NOTE: 본 스크립트는 운영 ingestion 파이프라인 대체가 아니다. 시연/평가용
          1회 적재만 수행하며, 실 운영은 RabbitMQ Worker / data-ingestion-agent
          별도 진입점이 담당한다 (설계서 §6).
--------------------------------------------------
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import DocumentSourceAdapter
    from app.schemas.chunk import Chunk


@dataclass
class CollectedChunks:
    """``collect_chunks`` 결과 — 본문·첨부 청크와 인덱싱 부수 정보를 함께 담는다.

    Attributes:
        chunks: 본문(chunk_page) + 첨부(chunk_attachment) 청크 합본.
        version_by_page_id: page_id → version_number (멱등성 판정용).
        attachment_download_urls: attachment_id → download_url (첨부 청크 payload용).
        page_count: 순회한 PageObject 수.
        body_chunk_count: 본문 청크 수.
        attachment_chunk_count: 첨부 청크 수.
        skipped_attachments: 미지원 유형·파싱 실패로 건너뛴 (attachment_id, 사유) 목록.
    """

    chunks: list[Chunk] = field(default_factory=list)
    version_by_page_id: dict[str, int] = field(default_factory=dict)
    attachment_download_urls: dict[str, str] = field(default_factory=dict)
    page_count: int = 0
    body_chunk_count: int = 0
    attachment_chunk_count: int = 0
    skipped_attachments: list[tuple[str, str]] = field(default_factory=list)


def collect_chunks(adapter: DocumentSourceAdapter) -> CollectedChunks:
    """어댑터의 모든 PageObject 를 본문 + 첨부 청크로 변환해 수집한다.

    본문은 ``chunk_page`` 로, 첨부는 ``chunk_attachment`` 로 청킹한다. 첨부 청킹은
    docx/xlsx 만 지원하므로(feature4-A), 미지원 유형(PDF/CSV=feature4-B)·파싱 실패는
    ``skipped_attachments`` 에 사유와 함께 기록하고 건너뛴다(적재는 계속 진행).

    Args:
        adapter: ``fetch_pages()`` 로 PageObject 를 제공하는 Document Source Adapter.

    Returns:
        본문·첨부 청크와 인덱싱 부수 정보를 담은 ``CollectedChunks``.
    """
    # lazy import — embedding extras 미설치 환경에서 모듈 import 자체는 가능하게.
    from app.ingestion.chunker import chunk_attachment, chunk_page

    collected = CollectedChunks()
    for page in adapter.fetch_pages():
        collected.page_count += 1
        collected.version_by_page_id[page.page_id] = page.version_number

        body_chunks = chunk_page(page)
        collected.chunks.extend(body_chunks)
        collected.body_chunk_count += len(body_chunks)

        for attachment in page.attachments:
            collected.attachment_download_urls[attachment.attachment_id] = attachment.download_url
            try:
                attachment_chunks = chunk_attachment(attachment, page)
            except ValueError as exc:
                # 미지원 유형(PDF/CSV) 또는 유형 판별 실패 — 적재는 계속한다.
                collected.skipped_attachments.append((attachment.attachment_id, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 — 파일 파싱 실패도 적재 중단 없이 skip.
                collected.skipped_attachments.append(
                    (attachment.attachment_id, f"파싱 실패: {exc}")
                )
                continue
            collected.chunks.extend(attachment_chunks)
            collected.attachment_chunk_count += len(attachment_chunks)

    return collected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="samples/*.json 을 운영 Qdrant 에 적재한다 (Mode B 시연용).",
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=None,
        help="samples 디렉토리. 기본값은 settings.samples_dir (보통 'samples').",
    )
    parser.add_argument(
        "--use-mongo-cache",
        action="store_true",
        help="MongoEmbeddingCache / MongoChunkTextLookup 사용 (docker compose 의 mongo 필요).",
    )
    args = parser.parse_args()

    # lazy import — embedding extras 미설치 환경에서 도움말 출력은 가능하게.
    from app.adapters.json_fixture import JsonFixtureSourceAdapter
    from app.config import get_settings
    from app.ingestion.embedder.dense import E5DenseEmbedder
    from app.ingestion.embedder.sparse import BM25SparseEmbedder
    from app.ingestion.indexer import index_chunks
    from app.storage.chunk_lookup import FakeChunkTextLookup
    from app.storage.mongo_cache import FakeEmbeddingCache
    from app.storage.qdrant_client import QdrantPoolStore

    settings = get_settings()
    samples_dir = args.samples_dir or Path(settings.samples_dir)

    print(f"[ingest] samples_dir = {samples_dir.resolve()}")
    print(f"[ingest] qdrant = {settings.qdrant_host}:{settings.qdrant_port}")
    print(f"[ingest] dense model = {settings.dense_embedding_model}")
    print("[ingest] 모델 다운로드 (최초 1회) 및 Qdrant 컬렉션 부트스트랩 중...")

    dense = E5DenseEmbedder(settings.dense_embedding_model)
    sparse = BM25SparseEmbedder()
    store = QdrantPoolStore.from_settings(settings, dense_dimension=dense.dimension)
    store.bootstrap_collections()
    print(f"[ingest] dense_dimension = {dense.dimension}")

    if args.use_mongo_cache:
        from app.storage.chunk_lookup import MongoChunkTextLookup
        from app.storage.mongo_cache import MongoEmbeddingCache

        cache = MongoEmbeddingCache.from_settings(settings)
        chunk_lookup = MongoChunkTextLookup.from_settings(settings)
        print("[ingest] MongoDB cache + chunk_lookup 사용 (docker compose mongo 필요)")
    else:
        cache = FakeEmbeddingCache()
        chunk_lookup = FakeChunkTextLookup()
        print("[ingest] Fake cache + chunk_lookup 사용 (Qdrant 외 의존성 0)")

    adapter = JsonFixtureSourceAdapter(samples_dir=samples_dir)
    collected = collect_chunks(adapter)

    print(
        f"[ingest] PageObject {collected.page_count}건 → "
        f"Chunk {len(collected.chunks)}건 생성 "
        f"(본문 {collected.body_chunk_count} + 첨부 {collected.attachment_chunk_count})"
    )
    if collected.skipped_attachments:
        print(f"[ingest] 첨부 {len(collected.skipped_attachments)}건 skip (미지원 유형/파싱 실패):")
        for attachment_id, reason in collected.skipped_attachments:
            print(f"  - {attachment_id}: {reason}")

    if not collected.chunks:
        print("[ingest] 적재할 청크가 없습니다. samples_dir 확인 필요.")
        return 1

    print("[ingest] Dense + Sparse 임베딩 + Qdrant upsert 진행 중... (수십 초 소요)")
    index_chunks(
        collected.chunks,
        version_by_page_id=collected.version_by_page_id,
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=cache,
        chunk_lookup=chunk_lookup,
        attachment_download_urls=collected.attachment_download_urls,
    )
    print(f"[ingest] 완료 — Qdrant 3 Pool 에 {len(collected.chunks)}건 적재")
    print("[ingest] 이제 RAG_USE_REAL_ADAPTERS=true uvicorn 으로 시연 가능합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
