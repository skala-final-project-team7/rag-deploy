"""Evaluation Set 의 expected_chunk_ids 자동 backfill CLI [Pipeline 평가 도구].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature17b — ``samples/evaluation_set.json`` 의 각 항목이 ``expected
          _page_ids`` 만 라벨링하고 ``expected_chunk_ids`` 는 빈 배열인 상태에서,
          운영 Qdrant 에 적재된 chunk payload 를 scroll 해 expected_page_ids 와
          매칭되는 모든 chunk_id 를 자동으로 채운다. chunking 결과가 결정론
          (SHA1(page_id+chunk_index+attachment_id)) 이므로 한 번 backfill 한
          chunk_id 는 동일 데이터셋에서 stable 하다 (재인덱싱해도 동일).

          본 스크립트는 ``scripts/run_evaluation.py --rouge-l --bert-score`` 의
          정밀 Precision@k 매칭 (chunk_id 직접 비교) 의 전제 조건이다. 라벨링
          담당자가 expected_page_ids 만 채워두면 본 스크립트가 chunk_id 집합을
          자동 산출한다.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, feature17b 인프라 — expected_chunk_ids 자동 backfill.
--------------------------------------------------
[호환성]
  - Python 3.11.x, qdrant-client (이미 runtime 의존성).
  - 사용법:
        # dry-run (실제 파일 수정 없이 매칭 결과만 출력)
        python scripts/backfill_chunk_ids.py --dry-run

        # 실제 수정 (samples/evaluation_set.json.bak 백업 생성 후 갱신)
        python scripts/backfill_chunk_ids.py

        # 다른 eval-set 파일
        python scripts/backfill_chunk_ids.py --eval-set custom_eval.json
  - 전제: docker compose 의 Qdrant 가 기동 + scripts/ingest_samples.py 가 실행
          되어 chunk payload 가 적재된 상태 (실 chunking 후).
--------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "samples/evaluation_set.json 의 expected_chunk_ids 를 Qdrant scroll 결과로 채운다."
        ),
    )
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=Path("samples/evaluation_set.json"),
        help="Evaluation Set JSON 경로 (기본: samples/evaluation_set.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 파일 수정 없이 매칭 결과만 콘솔에 출력.",
    )
    args = parser.parse_args()

    if not args.eval_set.exists():
        print(f"[err] eval-set not found: {args.eval_set}")
        return 1

    with args.eval_set.open() as fp:
        data = json.load(fp)
    items: list[dict[str, Any]] = data["items"]

    # 모든 항목의 expected_page_ids 합집합 — 1회 scroll 로 전체 매핑 산출.
    target_page_ids: set[str] = set()
    for item in items:
        target_page_ids.update(item.get("expected_page_ids", []))

    if not target_page_ids:
        print("[info] 모든 항목의 expected_page_ids 가 비어 있다 — backfill 할 대상 없음.")
        return 0

    print(f"[backfill] eval-set = {args.eval_set}")
    print(f"[backfill] target page_ids ({len(target_page_ids)}) = {sorted(target_page_ids)}")

    page_id_to_chunk_ids = _scroll_chunk_ids_by_page_ids(target_page_ids)
    matched_count = sum(len(v) for v in page_id_to_chunk_ids.values())
    print(f"[backfill] Qdrant scroll 결과 — page_id 별 chunk_id 총 {matched_count} 건 매칭")
    for page_id in sorted(target_page_ids):
        n = len(page_id_to_chunk_ids.get(page_id, set()))
        marker = "✓" if n > 0 else "✗"
        print(f"  {marker} page_id={page_id:>10}  →  chunk_ids: {n}")

    # 각 항목의 expected_page_ids 합집합을 expected_chunk_ids 로 산출.
    updates_applied = 0
    for item in items:
        page_ids = item.get("expected_page_ids", [])
        if not page_ids:
            continue
        chunk_ids: set[str] = set()
        for page_id in page_ids:
            chunk_ids.update(page_id_to_chunk_ids.get(page_id, set()))
        previous = list(item.get("expected_chunk_ids") or [])
        new_value = sorted(chunk_ids)
        if previous != new_value:
            item["expected_chunk_ids"] = new_value
            updates_applied += 1

    print(f"[backfill] 갱신 대상 항목: {updates_applied}/{len(items)}")

    if args.dry_run:
        print("[backfill] --dry-run 이라 파일 미수정. 결과 미리보기:")
        print(json.dumps(items, ensure_ascii=False, indent=2)[:2000])
        return 0

    backup_path = args.eval_set.with_suffix(args.eval_set.suffix + ".bak")
    shutil.copy(args.eval_set, backup_path)
    print(f"[backfill] 백업 = {backup_path}")

    args.eval_set.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"[backfill] 갱신 완료 = {args.eval_set}")
    return 0


def _scroll_chunk_ids_by_page_ids(page_ids: set[str]) -> dict[str, set[str]]:
    """Qdrant CONTENT_POOL 을 scroll 해 page_id 별 chunk_id set 을 반환한다.

    page_id in (target set) 필터로 한 번 scroll — page_id 별 그룹핑은 클라이언트 측
    에서 진행. 본 스크립트는 ``app.storage.qdrant_client`` 의 내부 API 를 침범하지
    않고 ``QdrantPoolStore.from_settings`` 로 client 만 얻은 뒤 직접 scroll 한다.
    """
    from qdrant_client.http.models import (
        FieldCondition,
        Filter,
        MatchAny,
    )

    from app.config import get_settings
    from app.ingestion.vector_store import CONTENT_POOL
    from app.storage.qdrant_client import QdrantPoolStore, _pool_name_to_collection

    settings = get_settings()
    # dense_dimension 은 임의값 — scroll 자체는 dimension 무관, 컬렉션이 이미 존재.
    store = QdrantPoolStore.from_settings(settings, dense_dimension=1024)

    collection_name = _pool_name_to_collection(settings, CONTENT_POOL)
    scroll_filter = Filter(
        must=[FieldCondition(key="page_id", match=MatchAny(any=sorted(page_ids)))]
    )
    result: dict[str, set[str]] = defaultdict(set)
    offset: Any = None
    batch_size = 1000
    while True:
        records, next_offset = store._client.scroll(  # type: ignore[attr-defined]
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=batch_size,
            offset=offset,
            with_payload=["page_id", "chunk_id"],
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            page_id = payload.get("page_id")
            chunk_id = payload.get("chunk_id")
            if page_id is not None and chunk_id is not None:
                result[str(page_id)].add(str(chunk_id))
        if next_offset is None:
            break
        offset = next_offset
    return result


if __name__ == "__main__":
    raise SystemExit(main())
