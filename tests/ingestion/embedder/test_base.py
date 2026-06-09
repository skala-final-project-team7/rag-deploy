"""Embedder 어댑터 base — Protocol + Fake 구현 검증 (feature5-B-1).

FakeDenseEmbedder / FakeSparseEmbedder의 결정론·정규화·shape 계약 + SparseVector
값 객체 불변성·길이 검증을 확인한다. 외부 의존성 0.
"""

import dataclasses
import math

import pytest

from app.ingestion.embedder.base import (
    DenseEmbedder,
    FakeDenseEmbedder,
    FakeSparseEmbedder,
    SparseEmbedder,
    SparseVector,
)

# --- SparseVector 값 객체 ---


def test_sparse_vector_accepts_matching_lengths() -> None:
    sv = SparseVector(indices=(3, 7, 11), values=(0.5, 1.5, 2.5))
    assert sv.indices == (3, 7, 11)
    assert sv.values == (0.5, 1.5, 2.5)


def test_sparse_vector_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="길이 불일치"):
        SparseVector(indices=(1, 2), values=(0.5,))


def test_sparse_vector_is_frozen() -> None:
    sv = SparseVector(indices=(1,), values=(0.5,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        sv.indices = (2,)  # type: ignore[misc]


def test_sparse_vector_supports_empty() -> None:
    sv = SparseVector(indices=(), values=())
    assert sv.indices == ()
    assert sv.values == ()


# --- FakeDenseEmbedder ---


def test_fake_dense_embedder_is_instance_of_abstract_base() -> None:
    embedder = FakeDenseEmbedder(dimension=64)
    assert isinstance(embedder, DenseEmbedder)


def test_fake_dense_embedder_returns_normalized_vectors() -> None:
    embedder = FakeDenseEmbedder(dimension=64)
    [vector] = embedder.encode_passages(["EKS 노드 장애 대응 절차"])
    assert len(vector) == 64
    norm = math.sqrt(sum(value * value for value in vector))
    assert math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6)


def test_fake_dense_embedder_is_deterministic_across_instances() -> None:
    first = FakeDenseEmbedder(dimension=32)
    second = FakeDenseEmbedder(dimension=32)
    [vector_first] = first.encode_passages(["hello"])
    [vector_second] = second.encode_passages(["hello"])
    assert vector_first == vector_second


def test_fake_dense_embedder_passage_and_query_differ_for_same_text() -> None:
    # e5는 passage / query 프리픽스가 다른 임베딩을 만든다 — Fake도 같은 계약을 따른다.
    embedder = FakeDenseEmbedder(dimension=32)
    [passage_vector] = embedder.encode_passages(["hello"])
    [query_vector] = embedder.encode_queries(["hello"])
    assert passage_vector != query_vector


def test_fake_dense_embedder_batch_returns_distinct_vectors() -> None:
    embedder = FakeDenseEmbedder(dimension=32)
    vectors = embedder.encode_passages(["alpha", "beta", "gamma"])
    assert len(vectors) == 3
    # 서로 다른 입력은 서로 다른 출력을 만든다
    assert vectors[0] != vectors[1]
    assert vectors[1] != vectors[2]
    assert vectors[0] != vectors[2]


def test_fake_dense_embedder_empty_list_returns_empty() -> None:
    embedder = FakeDenseEmbedder(dimension=32)
    assert embedder.encode_passages([]) == []
    assert embedder.encode_queries([]) == []


def test_fake_dense_embedder_dimension_property() -> None:
    assert FakeDenseEmbedder(dimension=128).dimension == 128


def test_fake_dense_embedder_rejects_tiny_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        FakeDenseEmbedder(dimension=2)


# --- FakeSparseEmbedder ---


def test_fake_sparse_embedder_is_instance_of_abstract_base() -> None:
    assert isinstance(FakeSparseEmbedder(), SparseEmbedder)


def test_fake_sparse_embedder_returns_sparse_vector() -> None:
    embedder = FakeSparseEmbedder()
    [sv] = embedder.encode_passages(["EKS 노드 장애 대응 절차"])
    assert isinstance(sv, SparseVector)
    assert len(sv.indices) == len(sv.values)


def test_fake_sparse_embedder_indices_are_sorted_ascending() -> None:
    embedder = FakeSparseEmbedder()
    [sv] = embedder.encode_passages(["alpha beta gamma delta epsilon"])
    assert list(sv.indices) == sorted(sv.indices)


def test_fake_sparse_embedder_aggregates_term_frequency() -> None:
    # 같은 토큰이 N번 등장하면 그 인덱스의 값은 최소 N 이상
    embedder = FakeSparseEmbedder()
    [sv] = embedder.encode_passages(["alpha alpha alpha beta"])
    assert any(value >= 3.0 for value in sv.values)


def test_fake_sparse_embedder_empty_text_returns_empty_vector() -> None:
    embedder = FakeSparseEmbedder()
    [sv] = embedder.encode_passages([""])
    assert sv.indices == ()
    assert sv.values == ()


def test_fake_sparse_embedder_empty_list_returns_empty() -> None:
    embedder = FakeSparseEmbedder()
    assert embedder.encode_passages([]) == []
    assert embedder.encode_queries([]) == []


def test_fake_sparse_embedder_is_deterministic_across_instances() -> None:
    [first] = FakeSparseEmbedder().encode_passages(["alpha beta gamma"])
    [second] = FakeSparseEmbedder().encode_passages(["alpha beta gamma"])
    assert first == second


def test_fake_sparse_embedder_passage_and_query_same_for_same_text() -> None:
    # BM25는 passage/query 토크나이즈가 같다 — Fake도 같은 결과를 만든다
    embedder = FakeSparseEmbedder()
    [passage_sv] = embedder.encode_passages(["EKS 운영"])
    [query_sv] = embedder.encode_queries(["EKS 운영"])
    assert passage_sv == query_sv


def test_fake_sparse_embedder_rejects_tiny_vocab() -> None:
    with pytest.raises(ValueError, match="vocab_size"):
        FakeSparseEmbedder(vocab_size=4)
