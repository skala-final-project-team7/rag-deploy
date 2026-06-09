"""Multi-Pool Hybrid Search 핵심 로직 검증 (feature9-A) — rag-pipeline-design.md §6 4.5.

reciprocal_rank_fusion / merge_pools / select_top_candidates / fuse_and_rank 순수 함수.
"""

import pytest

from app.query.search import (
    RRF_K,
    fuse_and_rank,
    merge_pools,
    reciprocal_rank_fusion,
    select_top_candidates,
)


def test_rrf_accumulates_across_lists() -> None:
    # 두 순위 목록 모두 1위인 항목이 가장 높은 점수를 받는다
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]])
    assert set(scores) == {"a", "b", "c"}
    assert scores["a"] == pytest.approx(2 / (RRF_K + 1))
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_higher_rank_scores_more() -> None:
    scores = reciprocal_rank_fusion([["x", "y", "z"]])
    assert scores["x"] > scores["y"] > scores["z"]


def test_rrf_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[]]) == {}


def test_merge_pools_weighted_sum() -> None:
    pool_scores = {
        "title_pool": {"a": 1.0, "b": 1.0},
        "content_pool": {"a": 1.0},
    }
    pool_weights = {"title_pool": 0.4, "content_pool": 0.5, "label_pool": 0.1}
    merged = merge_pools(pool_scores, pool_weights)
    assert merged["a"] == pytest.approx(0.9)  # 1.0*0.4 + 1.0*0.5
    assert merged["b"] == pytest.approx(0.4)  # 1.0*0.4


def test_merge_pools_missing_weight_contributes_zero() -> None:
    # 가중치 dict에 없는 Pool은 0으로 간주된다
    merged = merge_pools({"unknown_pool": {"a": 1.0}}, {"title_pool": 0.4})
    assert merged["a"] == 0.0


def test_select_top_candidates_orders_and_limits() -> None:
    scores = {"a": 0.9, "b": 0.4, "c": 0.7}
    assert select_top_candidates(scores, limit=2) == ["a", "c"]


def test_select_top_candidates_tie_break_is_deterministic() -> None:
    # 동점은 item 오름차순으로 결정론 정렬 (Pipeline 회귀 보호)
    assert select_top_candidates({"b": 0.5, "a": 0.5, "c": 0.5}) == ["a", "b", "c"]


def test_fuse_and_rank_end_to_end() -> None:
    pool_rankings = {
        "title_pool": {"dense": ["a", "b"], "sparse": ["a", "b"]},
        "content_pool": {"dense": ["c", "a"], "sparse": ["c", "a"]},
    }
    pool_weights = {"title_pool": 0.4, "content_pool": 0.5, "label_pool": 0.1}
    ranked = fuse_and_rank(pool_rankings, pool_weights)
    # a는 두 Pool 모두 상위 → 최상위
    assert ranked[0] == "a"
    assert set(ranked) == {"a", "b", "c"}


def test_fuse_and_rank_respects_limit() -> None:
    pool_rankings = {"content_pool": {"dense": ["a", "b", "c", "d", "e"]}}
    ranked = fuse_and_rank(pool_rankings, {"content_pool": 1.0}, limit=3)
    assert ranked == ["a", "b", "c"]


def test_fuse_and_rank_empty() -> None:
    assert fuse_and_rank({}, {}) == []
