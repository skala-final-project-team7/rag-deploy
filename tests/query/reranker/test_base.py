"""Cross-Encoder Reranker base — ABC + Fake 구현 검증 (feature9-B-1).

FakeCrossEncoderReranker의 결정론·점수 범위·shape 계약을 확인한다. 외부 의존성 0.
"""

from app.query.reranker.base import CrossEncoderReranker, FakeCrossEncoderReranker


def test_fake_reranker_is_instance_of_abstract_base() -> None:
    assert isinstance(FakeCrossEncoderReranker(), CrossEncoderReranker)


def test_fake_reranker_returns_list_same_length_as_passages() -> None:
    reranker = FakeCrossEncoderReranker()
    scores = reranker.score("EKS 노드 장애 대응 절차", ["passage A", "passage B", "passage C"])
    assert len(scores) == 3


def test_fake_reranker_scores_in_unit_interval() -> None:
    # select_reranked의 임계 정합을 위해 [0.0, 1.0] 범위.
    reranker = FakeCrossEncoderReranker()
    scores = reranker.score(
        "쿼리",
        ["a", "b", "c", "한글 passage", "longer passage with more tokens"],
    )
    for score in scores:
        assert 0.0 <= score < 1.0


def test_fake_reranker_is_deterministic() -> None:
    first = FakeCrossEncoderReranker().score("query", ["alpha", "beta"])
    second = FakeCrossEncoderReranker().score("query", ["alpha", "beta"])
    assert first == second


def test_fake_reranker_different_passages_have_different_scores() -> None:
    reranker = FakeCrossEncoderReranker()
    scores = reranker.score("query", ["alpha", "beta"])
    assert scores[0] != scores[1]


def test_fake_reranker_different_queries_have_different_scores() -> None:
    reranker = FakeCrossEncoderReranker()
    [a] = reranker.score("query A", ["passage"])
    [b] = reranker.score("query B", ["passage"])
    assert a != b


def test_fake_reranker_empty_passages_returns_empty() -> None:
    reranker = FakeCrossEncoderReranker()
    assert reranker.score("any query", []) == []


def test_fake_reranker_integrates_with_select_reranked() -> None:
    """어댑터 출력 → 9-A select_reranked 통합 흐름 — 본 어댑터의 의도된 사용처."""
    from app.query.rerank import select_reranked

    reranker = FakeCrossEncoderReranker()
    passages = {f"chunk-{idx}": f"passage {idx}" for idx in range(10)}
    scores = reranker.score("EKS 운영", list(passages.values()))
    scored = dict(zip(passages.keys(), scores, strict=True))
    result = select_reranked(scored)
    # 빈 입력은 아니므로 top이 채워진다 — 정렬·임계 처리는 9-A 책임
    assert len(result.top) > 0
    assert all(0.0 <= score < 1.0 for _, score in result.top)
