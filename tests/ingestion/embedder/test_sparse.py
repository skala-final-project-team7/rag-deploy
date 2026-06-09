"""BM25SparseEmbedder 어댑터 검증 (feature5-B-1).

fastembed 미설치 환경에서는 importorskip로 우회한다. fastembed 모델 다운로드를
피하기 위해 단위 테스트에서는 ``SparseTextEmbedding`` 을 모방하는 stub을 직접
주입한다.
"""

from collections.abc import Iterator

import pytest

# fastembed는 무거운 의존성(embedding extra) — 미설치 환경에서는 스킵.
pytest.importorskip("fastembed")

from app.ingestion.embedder.base import SparseVector  # noqa: E402
from app.ingestion.embedder.sparse import BM25SparseEmbedder  # noqa: E402


class _StubSparseEmbedding:
    """fastembed.SparseEmbedding을 모방한 단순 객체 — .indices / .values 노출."""

    def __init__(self, indices: list[int], values: list[float]) -> None:
        self.indices = indices
        self.values = values


class _StubSparseModel:
    """SparseTextEmbedding을 모방한 stub — query_embed 메서드 보유."""

    def __init__(self) -> None:
        self.captured_embed: list[str] = []
        self.captured_query_embed: list[str] = []

    def embed(self, texts: list[str]) -> Iterator[_StubSparseEmbedding]:
        self.captured_embed.extend(texts)
        for text in texts:
            tokens = text.lower().split()
            yield _StubSparseEmbedding(
                indices=list(range(len(tokens))),
                values=[1.0] * len(tokens),
            )

    def query_embed(self, texts: list[str]) -> Iterator[_StubSparseEmbedding]:
        self.captured_query_embed.extend(texts)
        for text in texts:
            tokens = text.lower().split()
            yield _StubSparseEmbedding(
                indices=list(range(len(tokens))),
                values=[2.0] * len(tokens),
            )


def _make_embedder(stub: object) -> BM25SparseEmbedder:
    embedder = BM25SparseEmbedder.__new__(BM25SparseEmbedder)
    embedder._model = stub  # type: ignore[attr-defined]
    return embedder


def test_encode_passages_calls_embed_and_returns_sparse_vectors() -> None:
    stub = _StubSparseModel()
    embedder = _make_embedder(stub)
    results = embedder.encode_passages(["EKS 노드 장애 대응"])
    assert stub.captured_embed == ["EKS 노드 장애 대응"]
    assert len(results) == 1
    assert isinstance(results[0], SparseVector)


def test_encode_queries_prefers_query_embed_when_available() -> None:
    stub = _StubSparseModel()
    embedder = _make_embedder(stub)
    [result] = embedder.encode_queries(["검색어 입력"])
    assert stub.captured_query_embed == ["검색어 입력"]
    # query_embed의 stub은 모든 값을 2.0으로 채운다 — embed(1.0)과 구별
    assert all(value == 2.0 for value in result.values)


def test_encode_queries_falls_back_to_embed_when_no_query_embed() -> None:
    """fastembed 일부 버전은 query_embed가 없을 수 있다 — embed로 fallback해야 한다."""

    class _NoQueryEmbedModel:
        def __init__(self) -> None:
            self.captured: list[str] = []

        def embed(self, texts: list[str]) -> Iterator[_StubSparseEmbedding]:
            self.captured.extend(texts)
            for _ in texts:
                yield _StubSparseEmbedding(indices=[0], values=[1.0])

    stub = _NoQueryEmbedModel()
    embedder = _make_embedder(stub)
    [result] = embedder.encode_queries(["검색어"])
    assert stub.captured == ["검색어"]
    assert result.values == (1.0,)


def test_encode_empty_list_returns_empty_without_calling_model() -> None:
    stub = _StubSparseModel()
    embedder = _make_embedder(stub)
    assert embedder.encode_passages([]) == []
    assert embedder.encode_queries([]) == []
    assert stub.captured_embed == []
    assert stub.captured_query_embed == []


def test_to_sparse_vector_converts_numpy_arrays_to_python_primitives() -> None:
    """fastembed가 numpy array를 반환해도 호출자가 numpy 의존을 갖지 않도록 변환한다."""
    numpy = pytest.importorskip("numpy")

    embedding = _StubSparseEmbedding(indices=[], values=[])
    embedding.indices = numpy.array([3, 7, 11], dtype=numpy.int64)
    embedding.values = numpy.array([0.5, 1.5, 2.5], dtype=numpy.float32)

    sv = BM25SparseEmbedder._to_sparse_vector(embedding)
    assert sv.indices == (3, 7, 11)
    assert sv.values == (0.5, 1.5, 2.5)
    assert all(isinstance(index, int) for index in sv.indices)
    assert all(isinstance(value, float) for value in sv.values)


def test_to_sparse_vector_rejects_object_without_indices_or_values() -> None:
    class _Broken:
        pass

    with pytest.raises(TypeError, match="형식 오류"):
        BM25SparseEmbedder._to_sparse_vector(_Broken())
