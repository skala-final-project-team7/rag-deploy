"""E5DenseEmbedder 어댑터 검증 (feature5-B-1).

sentence-transformers 미설치 환경에서는 importorskip로 우회한다. 무거운 모델
다운로드(약 2.24 GB)를 피하기 위해 단위 테스트에서는 ``SentenceTransformer`` 를
모방하는 stub을 ``__new__`` + 직접 속성 주입으로 끼워 넣는다.
"""

from typing import Any

import pytest

# sentence-transformers는 무거운 의존성(embedding extra) — 미설치 환경에서는 스킵.
pytest.importorskip("sentence_transformers")
pytest.importorskip("numpy")

from app.ingestion.embedder.dense import E5DenseEmbedder  # noqa: E402


class _StubSentenceTransformer:
    """SentenceTransformer를 모방한 stub — 실 모델 다운로드 회피."""

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension
        self.captured_inputs: list[str] = []
        self.normalize_called: bool | None = None
        self.last_batch_size: int | None = None

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize_embeddings: bool = False,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ) -> Any:
        import numpy as np  # 지연 import — importorskip 통과 후 안전

        self.captured_inputs.extend(texts)
        self.normalize_called = normalize_embeddings
        self.last_batch_size = batch_size
        # 더미 정규화 벡터 (첫 원소 1.0, 나머지 0.0) — L2 norm = 1.0
        vectors = np.zeros((len(texts), self._dimension), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors


def _make_embedder(stub: _StubSentenceTransformer, *, batch_size: int = 32) -> E5DenseEmbedder:
    """E5DenseEmbedder의 SentenceTransformer를 stub으로 교체한 인스턴스 생성."""
    embedder = E5DenseEmbedder.__new__(E5DenseEmbedder)
    embedder._model = stub  # type: ignore[attr-defined]
    embedder._batch_size = batch_size  # type: ignore[attr-defined]
    embedder._dimension = stub.get_sentence_embedding_dimension()  # type: ignore[attr-defined]
    return embedder


def test_encode_passages_prepends_passage_prefix() -> None:
    stub = _StubSentenceTransformer(dimension=8)
    embedder = _make_embedder(stub)
    embedder.encode_passages(["EKS 노드 장애 대응"])
    assert stub.captured_inputs == ["passage: EKS 노드 장애 대응"]


def test_encode_queries_prepends_query_prefix() -> None:
    stub = _StubSentenceTransformer(dimension=8)
    embedder = _make_embedder(stub)
    embedder.encode_queries(["EKS 노드 장애 대응"])
    assert stub.captured_inputs == ["query: EKS 노드 장애 대응"]


def test_encode_passes_normalize_embeddings_true() -> None:
    # Cosine 검색 정합 — 어댑터는 항상 normalize_embeddings=True를 강제한다.
    stub = _StubSentenceTransformer(dimension=8)
    embedder = _make_embedder(stub)
    embedder.encode_passages(["text"])
    assert stub.normalize_called is True


def test_encode_passes_configured_batch_size() -> None:
    stub = _StubSentenceTransformer(dimension=8)
    embedder = _make_embedder(stub, batch_size=4)
    embedder.encode_passages(["a", "b", "c", "d", "e"])
    assert stub.last_batch_size == 4


def test_dimension_property_reflects_model() -> None:
    stub = _StubSentenceTransformer(dimension=1024)
    embedder = _make_embedder(stub)
    assert embedder.dimension == 1024


def test_encode_empty_list_returns_empty_without_calling_model() -> None:
    stub = _StubSentenceTransformer(dimension=8)
    embedder = _make_embedder(stub)
    assert embedder.encode_passages([]) == []
    assert embedder.encode_queries([]) == []
    # 빈 입력에서는 모델 호출이 일어나지 않아야 한다 (불필요한 비용 회피)
    assert stub.captured_inputs == []


def test_encode_returns_list_of_lists_with_correct_shape() -> None:
    stub = _StubSentenceTransformer(dimension=4)
    embedder = _make_embedder(stub)
    result = embedder.encode_passages(["a", "b"])
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(vector, list) for vector in result)
    assert all(len(vector) == 4 for vector in result)
    # stub은 첫 원소 1.0, 나머지 0.0 벡터를 만든다
    assert result[0] == [1.0, 0.0, 0.0, 0.0]
