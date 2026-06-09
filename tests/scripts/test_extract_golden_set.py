"""scripts/extract_golden_set.py — 3 조건 AND 필터 회귀 (feature17b).

설계서 §6.3 의 Golden Set 채택 기준 3 조건을 결정론적으로 검증.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _make_result(
    *,
    eval_id: str,
    verification: list[dict[str, str]] | None,
    top1_score: int | None,
) -> dict[str, Any]:
    """평가 결과 JSON 의 results[i] 형식 — 본 회귀에 필요한 필드만 채운다."""
    return {
        "id": eval_id,
        "query": f"q-{eval_id}",
        "actual_intent": "운영가이드",
        "n_sources": 3,
        "top1_score": top1_score,
        "verification": verification,
        "answer_excerpt": "answer",
        "latency_ms": 100,
    }


# ---------------------------------------------------------------------------
# _check_verification_pass — 조건 #1
# ---------------------------------------------------------------------------


def test_check_verification_pass_all_pass_or_supported_returns_true() -> None:
    from scripts.extract_golden_set import _check_verification_pass

    r = _make_result(
        eval_id="EVAL-001",
        verification=[
            {"sentence_id": 0, "status": "PASS"},
            {"sentence_id": 1, "status": "SUPPORTED"},
        ],
        top1_score=90,
    )
    assert _check_verification_pass(r) is True


def test_check_verification_pass_with_not_supported_returns_false() -> None:
    from scripts.extract_golden_set import _check_verification_pass

    r = _make_result(
        eval_id="EVAL-002",
        verification=[
            {"sentence_id": 0, "status": "PASS"},
            {"sentence_id": 1, "status": "NOT_SUPPORTED"},
        ],
        top1_score=95,
    )
    assert _check_verification_pass(r) is False


def test_check_verification_pass_empty_verification_returns_false() -> None:
    """verification 자체가 비면 '검증 완료' 가 아니므로 Golden Set 제외."""
    from scripts.extract_golden_set import _check_verification_pass

    r = _make_result(eval_id="EVAL-003", verification=[], top1_score=99)
    assert _check_verification_pass(r) is False


# ---------------------------------------------------------------------------
# _check_top1_score + _normalize_threshold — 조건 #2 + 스케일 자동 인식
# ---------------------------------------------------------------------------


def test_normalize_threshold_0_to_1_scale_converts_to_0_100() -> None:
    from scripts.extract_golden_set import _normalize_threshold

    assert _normalize_threshold(0.85) == 85
    assert _normalize_threshold(0.0) == 0
    assert _normalize_threshold(1.0) == 100


def test_normalize_threshold_0_to_100_scale_passes_through() -> None:
    from scripts.extract_golden_set import _normalize_threshold

    assert _normalize_threshold(85.0) == 85
    assert _normalize_threshold(90) == 90


def test_check_top1_score_above_threshold_passes() -> None:
    from scripts.extract_golden_set import _check_top1_score

    r = _make_result(eval_id="EVAL-001", verification=None, top1_score=87)
    assert _check_top1_score(r, threshold_score=85) is True


def test_check_top1_score_below_threshold_fails() -> None:
    from scripts.extract_golden_set import _check_top1_score

    r = _make_result(eval_id="EVAL-002", verification=None, top1_score=80)
    assert _check_top1_score(r, threshold_score=85) is False


def test_check_top1_score_missing_returns_false() -> None:
    from scripts.extract_golden_set import _check_top1_score

    r = _make_result(eval_id="EVAL-003", verification=None, top1_score=None)
    assert _check_top1_score(r, threshold_score=85) is False


# ---------------------------------------------------------------------------
# _check_feedback — 조건 #3 (Positive / 미제출 통과, Negative 미통과)
# ---------------------------------------------------------------------------


def test_check_feedback_positive_returns_true() -> None:
    from scripts.extract_golden_set import _check_feedback

    assert _check_feedback("EVAL-001", {"EVAL-001": "positive"}) is True


def test_check_feedback_negative_returns_false() -> None:
    from scripts.extract_golden_set import _check_feedback

    assert _check_feedback("EVAL-002", {"EVAL-002": "negative"}) is False


def test_check_feedback_unsubmitted_returns_true() -> None:
    """피드백 미제출 → 통과 (피드백 인프라 미보유 환경 fallback)."""
    from scripts.extract_golden_set import _check_feedback

    assert _check_feedback("EVAL-003", {}) is True


# ---------------------------------------------------------------------------
# _extract_golden — 3 조건 AND 통합
# ---------------------------------------------------------------------------


def test_extract_golden_all_three_conditions_pass() -> None:
    from scripts.extract_golden_set import _extract_golden

    results = [
        _make_result(
            eval_id="EVAL-001",
            verification=[{"sentence_id": 0, "status": "PASS"}],
            top1_score=90,
        ),
    ]
    extracted, breakdown = _extract_golden(results, feedback_map={}, threshold_score=85)
    assert len(extracted) == 1
    assert extracted[0]["id"] == "EVAL-001"
    assert breakdown == {"verification_pass": 1, "top1_score": 1, "feedback": 1}


def test_extract_golden_excludes_not_supported_item() -> None:
    from scripts.extract_golden_set import _extract_golden

    results = [
        _make_result(
            eval_id="EVAL-001",
            verification=[{"sentence_id": 0, "status": "NOT_SUPPORTED"}],
            top1_score=99,
        ),
    ]
    extracted, breakdown = _extract_golden(results, feedback_map={}, threshold_score=85)
    assert extracted == []
    assert breakdown["verification_pass"] == 0
    assert breakdown["top1_score"] == 1
    assert breakdown["feedback"] == 1


def test_extract_golden_excludes_negative_feedback() -> None:
    from scripts.extract_golden_set import _extract_golden

    results = [
        _make_result(
            eval_id="EVAL-001",
            verification=[{"sentence_id": 0, "status": "PASS"}],
            top1_score=99,
        ),
    ]
    extracted, _ = _extract_golden(
        results, feedback_map={"EVAL-001": "negative"}, threshold_score=85
    )
    assert extracted == []


def test_extract_golden_excludes_low_top1_score() -> None:
    from scripts.extract_golden_set import _extract_golden

    results = [
        _make_result(
            eval_id="EVAL-001",
            verification=[{"sentence_id": 0, "status": "PASS"}],
            top1_score=70,
        ),
    ]
    extracted, _ = _extract_golden(results, feedback_map={}, threshold_score=85)
    assert extracted == []


def test_extract_golden_mixed_results() -> None:
    """4건 입력 — 3 조건 모두 충족 1건만 추출."""
    from scripts.extract_golden_set import _extract_golden

    results = [
        _make_result(  # 통과
            eval_id="EVAL-001",
            verification=[{"sentence_id": 0, "status": "PASS"}],
            top1_score=90,
        ),
        _make_result(  # NOT_SUPPORTED 미통과
            eval_id="EVAL-002",
            verification=[
                {"sentence_id": 0, "status": "PASS"},
                {"sentence_id": 1, "status": "NOT_SUPPORTED"},
            ],
            top1_score=95,
        ),
        _make_result(  # top1_score 미통과
            eval_id="EVAL-003",
            verification=[{"sentence_id": 0, "status": "SUPPORTED"}],
            top1_score=70,
        ),
        _make_result(  # feedback negative 미통과 — verification + top1 은 통과
            eval_id="EVAL-004",
            verification=[{"sentence_id": 0, "status": "PASS"}],
            top1_score=99,
        ),
    ]
    feedback_map = {"EVAL-004": "negative"}
    extracted, _ = _extract_golden(results, feedback_map=feedback_map, threshold_score=85)
    assert [it["id"] for it in extracted] == ["EVAL-001"]


# ---------------------------------------------------------------------------
# _load_feedback_map — 피드백 파일 처리
# ---------------------------------------------------------------------------


def test_load_feedback_map_none_returns_empty() -> None:
    from scripts.extract_golden_set import _load_feedback_map

    assert _load_feedback_map(None) == {}


def test_load_feedback_map_normalizes_to_lowercase(tmp_path: Path) -> None:
    from scripts.extract_golden_set import _load_feedback_map

    fb = tmp_path / "feedback.json"
    fb.write_text(json.dumps({"EVAL-001": "Positive", "EVAL-002": "NEGATIVE"}))
    assert _load_feedback_map(fb) == {"EVAL-001": "positive", "EVAL-002": "negative"}


def test_load_feedback_map_non_dict_raises(tmp_path: Path) -> None:
    from scripts.extract_golden_set import _load_feedback_map

    fb = tmp_path / "feedback.json"
    fb.write_text(json.dumps(["not", "a", "dict"]))
    with pytest.raises(ValueError, match="dict"):
        _load_feedback_map(fb)
