"""scripts/backfill_chunk_ids.py 회귀 — feature17b 인프라.

Qdrant scroll 결과 mock 으로 expected_chunk_ids 자동 채움 + dry-run + 백업 생성.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_eval_set(tmp_path: Path, items: list[dict]) -> Path:
    """임시 evaluation_set.json 파일을 만든다."""
    data = {"items": items}
    path = tmp_path / "eval_set.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def test_backfill_fills_chunk_ids_from_qdrant_scroll(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """page_id 별 chunk_id 매핑이 expected_chunk_ids 에 채워진다."""
    from scripts import backfill_chunk_ids

    # Qdrant scroll 결과를 mock — page_id 별 chunk_id 매핑.
    def _fake_scroll(page_ids: set[str]) -> dict[str, set[str]]:
        return {
            "P1": {"chunk-a-001", "chunk-a-002"},
            "P2": {"chunk-b-001"},
        }

    monkeypatch.setattr(backfill_chunk_ids, "_scroll_chunk_ids_by_page_ids", _fake_scroll)
    eval_set = _write_eval_set(
        tmp_path,
        items=[
            {"id": "E1", "expected_page_ids": ["P1"], "expected_chunk_ids": []},
            {"id": "E2", "expected_page_ids": ["P2"], "expected_chunk_ids": []},
            {"id": "E3", "expected_page_ids": ["P1", "P2"], "expected_chunk_ids": []},
        ],
    )
    monkeypatch.setattr("sys.argv", ["backfill_chunk_ids.py", "--eval-set", str(eval_set)])
    rc = backfill_chunk_ids.main()
    assert rc == 0

    updated = json.loads(eval_set.read_text())
    items = {it["id"]: it for it in updated["items"]}
    assert items["E1"]["expected_chunk_ids"] == ["chunk-a-001", "chunk-a-002"]
    assert items["E2"]["expected_chunk_ids"] == ["chunk-b-001"]
    # E3 는 P1 ∪ P2 → 3 개.
    assert items["E3"]["expected_chunk_ids"] == [
        "chunk-a-001",
        "chunk-a-002",
        "chunk-b-001",
    ]
    # 백업 파일 생성.
    assert eval_set.with_suffix(".json.bak").exists()


def test_backfill_dry_run_does_not_modify_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--dry-run 시 파일이 수정되지 않고 백업도 생성되지 않는다."""
    from scripts import backfill_chunk_ids

    monkeypatch.setattr(
        backfill_chunk_ids,
        "_scroll_chunk_ids_by_page_ids",
        lambda page_ids: {"P1": {"chunk-a-001"}},
    )

    eval_set = _write_eval_set(
        tmp_path,
        items=[{"id": "E1", "expected_page_ids": ["P1"], "expected_chunk_ids": []}],
    )
    original_content = eval_set.read_text()
    monkeypatch.setattr(
        "sys.argv",
        ["backfill_chunk_ids.py", "--eval-set", str(eval_set), "--dry-run"],
    )
    rc = backfill_chunk_ids.main()
    assert rc == 0

    # 파일 미수정.
    assert eval_set.read_text() == original_content
    # 백업 미생성.
    assert not eval_set.with_suffix(".json.bak").exists()


def test_backfill_returns_zero_when_no_target_page_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """expected_page_ids 가 모두 빈 경우 scroll 자체를 호출하지 않는다."""
    from scripts import backfill_chunk_ids

    called = {"n": 0}

    def _fake_scroll(page_ids: set[str]) -> dict[str, set[str]]:
        called["n"] += 1
        return {}

    monkeypatch.setattr(backfill_chunk_ids, "_scroll_chunk_ids_by_page_ids", _fake_scroll)
    eval_set = _write_eval_set(
        tmp_path,
        items=[{"id": "E1", "expected_page_ids": [], "expected_chunk_ids": []}],
    )
    monkeypatch.setattr("sys.argv", ["backfill_chunk_ids.py", "--eval-set", str(eval_set)])
    rc = backfill_chunk_ids.main()
    assert rc == 0
    assert called["n"] == 0
