"""Cross-Encoder 재순위화 선정 로직 검증 (feature9-A) — rag-pipeline-design.md §6 4.5, §8.

select_reranked: Top-5 선정, 5위 < NARROW(0.65) 시 Top-3 축소, 최고 < LOW(0.55) 시
저신뢰 분기. 임계값은 feature17c-2(2026-05-20) temperature scaling(T=4) 도입으로
NARROW 0.30→0.65 / LOW 0.20→0.55 로 재조정됨.
"""

from app.query.rerank import (
    LOW_CONFIDENCE_THRESHOLD,
    NARROW_SCORE_THRESHOLD,
    RerankResult,
    select_reranked,
)


def test_select_reranked_keeps_top_5() -> None:
    # c0..c7: 0.95, 0.90, 0.85, 0.80, 0.75, ... — 5위(c4)=0.75 ≥ NARROW(0.65) → 축소 없음
    scored = {f"c{i}": 0.95 - i * 0.05 for i in range(8)}
    result = select_reranked(scored)
    assert isinstance(result, RerankResult)
    assert [item for item, _ in result.top] == ["c0", "c1", "c2", "c3", "c4"]
    assert result.is_low_confidence is False


def test_select_reranked_narrows_to_top_3_when_fifth_is_low() -> None:
    # 5위 점수가 NARROW(0.65) 미만이면 Top-3로 축소한다
    scored = {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6, "e": 0.55}
    result = select_reranked(scored)
    assert [item for item, _ in result.top] == ["a", "b", "c"]
    assert result.is_low_confidence is False


def test_select_reranked_keeps_top_5_when_fifth_at_threshold() -> None:
    # 5위 점수가 정확히 NARROW(0.65)이면 축소하지 않는다 (< 0.65 만 축소)
    scored = {"a": 0.9, "b": 0.8, "c": 0.75, "d": 0.7, "e": NARROW_SCORE_THRESHOLD}
    result = select_reranked(scored)
    assert len(result.top) == 5


def test_select_reranked_low_confidence_when_best_below_threshold() -> None:
    # 최고 점수가 LOW(0.55) 미만이면 저신뢰 분기
    scored = {"a": 0.54, "b": 0.4, "c": 0.3}
    result = select_reranked(scored)
    assert result.is_low_confidence is True
    assert [item for item, _ in result.top] == ["a", "b", "c"]


def test_select_reranked_not_low_confidence_at_low_threshold() -> None:
    # 최고 점수가 정확히 LOW(0.55)이면 저신뢰가 아니다 (< 0.55 만 저신뢰)
    scored = {"a": LOW_CONFIDENCE_THRESHOLD, "b": 0.4, "c": 0.3}
    result = select_reranked(scored)
    assert result.is_low_confidence is False


def test_select_reranked_empty_is_low_confidence() -> None:
    result = select_reranked({})
    assert result.top == []
    assert result.is_low_confidence is True


def test_select_reranked_tie_break_is_deterministic() -> None:
    # 동점은 item 오름차순으로 결정론 정렬
    result = select_reranked({"b": 0.5, "a": 0.5, "c": 0.5})
    assert [item for item, _ in result.top] == ["a", "b", "c"]
