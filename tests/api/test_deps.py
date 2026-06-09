"""FastAPI 의존성 부트스트랩 검증 — build_real_deps + build_poc_deps.

build_real_deps는 sentence-transformers / fastembed 모델 다운로드(약 2.4 GB) +
Qdrant 서버 접속을 요구한다. 테스트는 monkeypatch로 실 어댑터 클래스 4종을
가짜로 대체해 함수 로직(어댑터 wiring + Qdrant from_settings 호출 + samples
인덱싱 미실행)만 검증한다. 모듈 import 단계에서 sentence-transformers가
끌어와지지 않음(lazy import)도 함께 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.config import Settings
from app.ingestion.embedder.base import (
    DenseEmbedder,
    FakeDenseEmbedder,
    FakeSparseEmbedder,
    SparseEmbedder,
    SparseVector,
)
from app.pipeline.query_graph import QueryGraphDeps
from app.query.reranker.base import CrossEncoderReranker, FakeCrossEncoderReranker
from app.storage.qdrant_client import QdrantPoolStore


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[arg-type]


# --- Fake 운영 어댑터 — sentence-transformers / fastembed / 실 Qdrant 회피 ---


@dataclass
class _FakeRealDense(DenseEmbedder):
    """E5DenseEmbedder 대체. 실 모델 다운로드 없이 dimension만 보고한다."""

    _dimension: int = 1024

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode_passages(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        return [[0.0] * self._dimension for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        return [[0.0] * self._dimension for _ in texts]


class _FakeRealSparse(SparseEmbedder):
    """BM25SparseEmbedder 대체."""

    def encode_passages(self, texts: list[str]) -> list[SparseVector]:  # pragma: no cover
        return [SparseVector(indices=(), values=()) for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[SparseVector]:  # pragma: no cover
        return [SparseVector(indices=(), values=()) for _ in texts]


class _FakeRealReranker(CrossEncoderReranker):
    """CrossEncoderRerankerImpl 대체."""

    def score(self, query: str, passages: list[str]) -> list[float]:  # pragma: no cover
        return [0.5 for _ in passages]


# --- 픽스처 ---


@pytest.fixture()
def patched_real_adapters(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """build_real_deps가 호출하는 실 어댑터 6종을 모두 가짜로 대체한다.

    실 어댑터들은 build_real_deps 함수 본문 내 lazy import이므로 원본 모듈
    (``app.ingestion.embedder.dense`` 등)의 클래스 자체를 교체한다. Qdrant
    ``from_settings`` 도 :memory: 클라이언트로 우회해 외부 컨테이너 없이 검증.
    Query Routing Agent 의 ``OpenAIRoutingLLMProvider`` 도 sentinel 클래스로
    대체해 OPENAI_API_KEY 환경변수 없이 회귀 보호 — feature12 에서 ``from_config``
    (env fallback) → ``__init__(config, api_key)`` 직접 호출로 변경됐다. Answer
    Verification Agent 의 ``OpenAIEvaluatorProvider`` 도 sentinel 로 대체.
    """
    import query_routing_agent.llm as routing_llm_module

    captured: dict[str, Any] = {
        "dense_init": None,
        "sparse_init": None,
        "reranker_init": None,
        "store_from_settings": None,
        "routing_provider_init": None,
        "verifier_provider_init": None,
        "generator_provider_init": None,
        "generator_transport_init": None,
    }

    def _fake_dense_factory(*args: Any, **kwargs: Any) -> _FakeRealDense:
        captured["dense_init"] = {"args": args, "kwargs": kwargs}
        return _FakeRealDense()

    def _fake_sparse_factory(*args: Any, **kwargs: Any) -> _FakeRealSparse:
        captured["sparse_init"] = {"args": args, "kwargs": kwargs}
        return _FakeRealSparse()

    def _fake_reranker_factory(*args: Any, **kwargs: Any) -> _FakeRealReranker:
        captured["reranker_init"] = {"args": args, "kwargs": kwargs}
        return _FakeRealReranker()

    def _fake_from_settings(
        cls: type[QdrantPoolStore], settings: Settings, *, dense_dimension: int = 1024
    ) -> QdrantPoolStore:
        # classmethod descriptor가 cls를 자동 주입하므로 첫 인자에 cls를 받는다.
        captured["store_from_settings"] = {"dense_dimension": dense_dimension}
        return QdrantPoolStore.in_memory(settings, dense_dimension=dense_dimension)

    class _FakeOpenAIRouting:
        """OpenAIRoutingLLMProvider 대체 — API key 검증·실 HTTP transport 회피.

        feature12 에서 build_real_deps 가 ``from_config`` → ``__init__(config, api_key)``
        직접 호출로 변경됐다. 본 sentinel 은 settings.openai_api_key 가 명시 전달되는지
        captured 에 기록해 회귀 보호한다.
        """

        def __init__(self, *, config: Any, api_key: str, transport: Any | None = None) -> None:
            captured["routing_provider_init"] = {
                "config": config,
                "api_key_provided": bool(api_key),
                "transport": transport,
            }

    class _FakeOpenAIEvaluator:
        """OpenAIEvaluatorProvider 대체 — API key 검증·실 HTTP transport 회피."""

        def __init__(self, *, config: Any, transport: Any | None = None) -> None:
            captured["verifier_provider_init"] = {"config": config, "transport": transport}
            self.config = config

    class _FakeOpenAIAnswer:
        """OpenAIAnswerLLMProvider 대체 — API key 검증·실 HTTP transport 회피."""

        def __init__(self, *, api_key: str, transport: Any | None = None) -> None:
            captured["generator_provider_init"] = {
                "api_key_provided": bool(api_key),
                "transport": transport,
            }

    def _fake_build_openai_chat_transport(*, api_key: str, **kwargs: Any) -> object:
        # build_openai_chat_transport 도 sentinel callable 로 대체 — 실 OpenAI client
        # 생성 회피. transport 가 wiring 됐는지만 검증한다.
        captured["generator_transport_init"] = {
            "api_key_provided": bool(api_key),
            "kwargs": kwargs,
        }
        return lambda payload: {"answer": "stub"}

    import answer_generation_agent.generation.answer_generation as generation_module
    import answer_verification_agent.evaluator.providers as verifier_providers_module
    import app.ingestion.embedder.dense as dense_module
    import app.ingestion.embedder.sparse as sparse_module
    import app.query.openai_transport as openai_transport_module
    import app.query.reranker.cross_encoder as reranker_module

    monkeypatch.setattr(dense_module, "E5DenseEmbedder", _fake_dense_factory)
    monkeypatch.setattr(sparse_module, "BM25SparseEmbedder", _fake_sparse_factory)
    monkeypatch.setattr(reranker_module, "CrossEncoderRerankerImpl", _fake_reranker_factory)
    monkeypatch.setattr(QdrantPoolStore, "from_settings", classmethod(_fake_from_settings))
    monkeypatch.setattr(routing_llm_module, "OpenAIRoutingLLMProvider", _FakeOpenAIRouting)
    monkeypatch.setattr(verifier_providers_module, "OpenAIEvaluatorProvider", _FakeOpenAIEvaluator)
    monkeypatch.setattr(generation_module, "OpenAIAnswerLLMProvider", _FakeOpenAIAnswer)
    monkeypatch.setattr(
        openai_transport_module,
        "build_openai_chat_transport",
        _fake_build_openai_chat_transport,
    )

    return captured


# --- build_real_deps 테스트 ---


def test_build_real_deps_wires_real_adapter_classes(
    patched_real_adapters: dict[str, Any],
) -> None:
    """build_real_deps 가 5 종 운영 어댑터(임베더 2 + Qdrant + reranker + 라우터
    provider)를 모두 호출해 QueryGraphDeps 에 박는다."""
    from app.api.deps import build_real_deps

    deps = build_real_deps(_settings())

    assert isinstance(deps, QueryGraphDeps)
    assert patched_real_adapters["dense_init"] is not None
    assert patched_real_adapters["sparse_init"] is not None
    assert patched_real_adapters["reranker_init"] is not None
    assert patched_real_adapters["store_from_settings"] is not None
    assert patched_real_adapters["routing_provider_init"] is not None
    # dense_dimension은 어댑터가 보고한 값으로 전달돼야 한다 (E5 = 1024)
    assert patched_real_adapters["store_from_settings"]["dense_dimension"] == 1024
    # 라우터 provider 는 GPT-4o-mini 로 설정돼야 한다 (app/CLAUDE.md §5 라우팅 정책).
    assert patched_real_adapters["routing_provider_init"]["config"].model == "gpt-4o-mini"
    # Fake 어댑터는 PoC 경로에서만 사용되어야 한다 — 운영 모드는 Fake 사용 금지
    assert not isinstance(deps.dense_embedder, FakeDenseEmbedder)
    assert not isinstance(deps.sparse_embedder, FakeSparseEmbedder)
    assert not isinstance(deps.reranker, FakeCrossEncoderReranker)
    # 라우터 provider / config 가 QueryGraphDeps 에 박혔는지 회귀 보호 (router_node
    # 가 manage_router 기본값일 때 partial 로 노드에 주입되는 경로).
    assert deps.routing_provider is not None
    assert deps.routing_config is not None
    # 답변 생성기 provider / config 는 (B) 운영 OpenAI HTTP transport 도입으로
    # 운영 모드에 wiring 된다 (Plan v2 §2.6, 설계서 §4.6.3). build_openai_chat
    # _transport 가 OpenAI client 를 만들어 OpenAIAnswerLLMProvider 의 transport 로
    # 주입된다. 모델은 settings.llm_answer_model (default GPT-4o).
    # NOTE: 단위 테스트는 Settings(_env_file=None) 으로 openai_api_key 가 빈 SecretStr
    # 이므로 api_key_provided 자체는 False 가 정상이다 — 운영에서는 OPENAI_API_KEY
    # 환경변수로 채워진다. 본 단언은 transport wiring 자체와 모델명만 검증한다.
    assert patched_real_adapters["generator_provider_init"] is not None
    assert patched_real_adapters["generator_provider_init"]["transport"] is not None
    assert patched_real_adapters["generator_transport_init"] is not None
    assert deps.generator_provider is not None
    assert deps.generator_config is not None
    assert deps.generator_config.model == _settings().llm_answer_model
    # 답변 검증 2단계 평가자 provider / config 는 Agent 통합 3/4 에서 OpenAIEvaluator
    # Provider 로 주입된다 (agent 자체 urllib HTTP transport 가 있어 즉시 wiring).
    # GPT-4o-mini 모델로 설정 (설계서 §4.7.2 / app/CLAUDE.md §5 라우팅 정책).
    verifier_init = patched_real_adapters["verifier_provider_init"]
    assert verifier_init is not None
    assert verifier_init["config"].evaluator_model == "gpt-4o-mini"
    assert deps.verifier_provider is not None
    assert deps.verifier_config is not None
    assert deps.verifier_config.evaluator_model == "gpt-4o-mini"


def test_build_real_deps_passes_openai_api_key_to_all_providers(
    patched_real_adapters: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings.openai_api_key`` 가 라우터·검증기·답변 생성기 3종 provider 에 명시
    전달되는지 검증 (feature12 회귀 보호).

    이전에는 라우터·검증기 provider 가 ``os.environ.get("OPENAI_API_KEY")`` fallback
    에 의존했다. 본 fix 로 settings 의 값을 직접 주입하므로 ``OPENAI_API_KEY``
    환경변수가 부재해도 provider 생성이 성공해야 한다 (의존성 명시화).

    ``Settings(_env_file=None)`` 의 기본 ``openai_api_key`` 는 빈 SecretStr 이므로
    본 테스트는 임의 sentinel 키를 환경변수로 주입 후 ``Settings()`` 가 그 값을
    읽어 provider 에 전달하는 경로를 검증한다.
    """
    # OPENAI_API_KEY 자체는 비워 두고, settings 의 RAG_OPENAI_API_KEY 만으로 동작 확인.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "sk-feature12-sentinel")

    from app.api.deps import build_real_deps

    build_real_deps(Settings(_env_file=None))  # type: ignore[arg-type]

    # 라우터 — __init__ 직접 호출 + api_key kwarg 로 전달.
    routing_init = patched_real_adapters["routing_provider_init"]
    assert routing_init is not None
    assert routing_init["api_key_provided"] is True
    # feature17a 후속 — transport callable (build_openai_routing_transport 결과) 가
    # 명시 주입돼 vendoring 패키지의 _default_transport 를 대체한다.
    assert routing_init["transport"] is not None
    assert callable(routing_init["transport"])
    # 검증기 — AnswerVerificationConfig.openai_api_key 에 채워서 전달 (env fallback 회피).
    verifier_init = patched_real_adapters["verifier_provider_init"]
    assert verifier_init is not None
    assert verifier_init["config"].openai_api_key == "sk-feature12-sentinel"
    # 답변 생성기 — OpenAIAnswerLLMProvider(api_key=...) 직접 주입 + transport 도 api_key 전달.
    generator_init = patched_real_adapters["generator_provider_init"]
    assert generator_init is not None
    assert generator_init["api_key_provided"] is True
    transport_init = patched_real_adapters["generator_transport_init"]
    assert transport_init is not None
    assert transport_init["api_key_provided"] is True


def test_build_real_deps_passes_model_names_from_settings(
    patched_real_adapters: dict[str, Any],
) -> None:
    """settings의 dense_embedding_model / cross_encoder_model이 어댑터 생성자에 전달된다."""
    from app.api.deps import build_real_deps

    settings = _settings()
    build_real_deps(settings)

    dense_kwargs = patched_real_adapters["dense_init"]
    reranker_kwargs = patched_real_adapters["reranker_init"]
    dense_passed = list(dense_kwargs["args"]) + list(dense_kwargs["kwargs"].values())
    reranker_passed = list(reranker_kwargs["args"]) + list(reranker_kwargs["kwargs"].values())
    assert settings.dense_embedding_model in dense_passed
    assert settings.cross_encoder_model in reranker_passed


def test_build_real_deps_omits_conservative_guard_by_default(
    patched_real_adapters: dict[str, Any],
) -> None:
    """feature17c-14 — 기본(generator_conservative_guard=False)은 transport 에
    system_prompt_suffix 를 주입하지 않는다 (None=기존 동작)."""
    from app.api.deps import build_real_deps

    build_real_deps(_settings())

    transport_kwargs = patched_real_adapters["generator_transport_init"]["kwargs"]
    assert transport_kwargs.get("system_prompt_suffix") is None


def test_build_real_deps_injects_conservative_guard_when_enabled(
    patched_real_adapters: dict[str, Any],
) -> None:
    """feature17c-14 — generator_conservative_guard=True 면 CONSERVATIVE_SYSTEM_GUARD
    가 transport 의 system_prompt_suffix 로 주입된다 (opt-in)."""
    from app.api.deps import build_real_deps
    from app.query.openai_transport import CONSERVATIVE_SYSTEM_GUARD

    build_real_deps(Settings(_env_file=None, generator_conservative_guard=True))  # type: ignore[arg-type]

    transport_kwargs = patched_real_adapters["generator_transport_init"]["kwargs"]
    assert transport_kwargs.get("system_prompt_suffix") == CONSERVATIVE_SYSTEM_GUARD


def test_build_real_deps_does_not_ingest_samples(
    patched_real_adapters: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """운영 모드는 samples 자동 인덱싱을 수행하지 않는다 (매 startup마다 재임베딩 회피)."""
    from app.api import deps as deps_module

    called = {"ingest": False}

    def _spy_ingest(**kwargs: Any) -> None:
        called["ingest"] = True

    monkeypatch.setattr(deps_module, "_ingest_samples", _spy_ingest)

    deps_module.build_real_deps(_settings())

    assert called["ingest"] is False


def test_build_real_deps_does_not_eagerly_import_sentence_transformers() -> None:
    """app.api.deps 모듈 import 단계에서 sentence-transformers가 끌어와지지 않는다.

    embedding extra 미설치 환경에서도 PoC 경로(build_poc_deps)는 동작해야 하므로,
    실 어댑터 모듈은 build_real_deps 함수 본문 내 lazy import여야 한다.

    docstring·주석에 모듈명이 등장하는 것은 허용하므로 AST로 실제 import 노드만
    검사한다 (문자열 매칭은 false positive 발생).
    """
    import ast
    import inspect

    import app.api.deps as deps_module

    tree = ast.parse(inspect.getsource(deps_module))
    # 모듈 최상단(함수·클래스 본문 내부 제외) Import / ImportFrom 노드의 대상만 모은다.
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top_level_imports.add(module)
            for alias in node.names:
                top_level_imports.add(f"{module}.{alias.name}")

    forbidden_at_top = {
        "sentence_transformers",
        "fastembed",
        "app.ingestion.embedder.dense",
        "app.ingestion.embedder.sparse",
        "app.query.reranker.cross_encoder",
    }
    leaked = top_level_imports & forbidden_at_top
    assert not leaked, f"실 어댑터/heavy 의존성이 모듈 최상단에서 import됨: {leaked}"


# --- build_poc_deps 회귀 (기존 동작 보존 확인) ---


def test_build_poc_deps_uses_fake_adapters_unchanged() -> None:
    """build_poc_deps는 기존대로 Fake 어댑터 + samples 인덱싱을 사용한다 (회귀 보호)."""
    from app.api.deps import build_poc_deps

    deps = build_poc_deps()

    assert isinstance(deps, QueryGraphDeps)
    assert isinstance(deps.dense_embedder, FakeDenseEmbedder)
    assert isinstance(deps.sparse_embedder, FakeSparseEmbedder)
    assert isinstance(deps.reranker, FakeCrossEncoderReranker)
    # 답변 생성기 provider / config 는 PoC 에서도 None 유지 (manage_generator 가
    # FakeAnswerLLMProvider 자동 주입). Agent 통합 2/4 회귀 보호.
    assert deps.generator_provider is None
    assert deps.generator_config is None
    # 답변 검증 2단계 평가자 provider / config 는 PoC 에서 None 유지
    # (manage_verifier_evaluator 가 FakeEvaluatorProvider 자동 주입). Agent 통합 3/4
    # 회귀 보호.
    assert deps.verifier_provider is None
    assert deps.verifier_config is None


def test_build_poc_deps_shares_chunk_lookup_with_ingest_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_poc_deps는 단일 FakeChunkTextLookup 을 _ingest_samples 와 QueryGraphDeps
    양쪽에 동일 인스턴스로 주입한다 (Phase 2 적재 통합 회귀 보호).

    이 공유가 깨지면 인덱싱 시 적재된 chunk_lookup 과 검색 시 조회하는 chunk_lookup 이
    서로 다른 인스턴스가 돼 download_url 채움이 실패한다.
    """
    from app.api import deps as deps_module
    from app.storage.chunk_lookup import FakeChunkTextLookup

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> None:
        captured["chunk_lookup"] = kwargs.get("chunk_lookup")

    monkeypatch.setattr(deps_module, "_ingest_samples", _capture)

    deps = deps_module.build_poc_deps()

    assert isinstance(deps.chunk_lookup, FakeChunkTextLookup)
    # _ingest_samples 가 받은 인스턴스와 QueryGraphDeps 에 박힌 인스턴스가 동일해야 함.
    assert captured["chunk_lookup"] is deps.chunk_lookup


def test_ingest_samples_includes_attachment_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ingest_samples 는 본문뿐 아니라 첨부(docx/xlsx) 청크도 적재한다 (feature17c-4 PoC).

    index_chunks 를 mock 해 적재 직전 chunks 를 캡처하고, ATTACHMENT source_type 청크가
    포함되는지 + 4개 첨부 파일명이 모두 청크를 생성하는지 검증한다 (Qdrant I/O 불요).
    """
    from pathlib import Path

    from app.api import deps as deps_module
    from app.schemas.enums import SourceType
    from app.storage.chunk_lookup import FakeChunkTextLookup

    captured: dict[str, Any] = {}

    def _capture_index_chunks(chunks: Any, **_kwargs: Any) -> None:
        captured["chunks"] = list(chunks)

    monkeypatch.setattr(deps_module, "index_chunks", _capture_index_chunks)

    samples_dir = Path(__file__).resolve().parents[2] / "samples"
    store = QdrantPoolStore.in_memory(_settings(), dense_dimension=8)
    deps_module._ingest_samples(
        store=store,
        dense=FakeDenseEmbedder(dimension=8),
        sparse=FakeSparseEmbedder(),
        samples_dir=samples_dir,
        chunk_lookup=FakeChunkTextLookup(),
    )

    chunks = captured["chunks"]
    attachment_chunks = [c for c in chunks if c.metadata.source_type is SourceType.ATTACHMENT]
    assert attachment_chunks, "첨부 청크가 적재 대상에 포함되어야 한다"
    body_chunks = [c for c in chunks if c.metadata.source_type is SourceType.PAGE]
    assert body_chunks, "본문 청크도 그대로 유지되어야 한다"
    filenames = {c.metadata.attachment_filename for c in attachment_chunks}
    assert filenames == {
        "EKS_운영_상세_매뉴얼_v2.3.docx",
        "모니터링_메트릭_정의서_v1.4.xlsx",
        "EKS_노드_월간_사용량_통계_2026Q1.xlsx",
        "신규입사자_온보딩_체크리스트_2026.docx",
    }


# --- build_poc_ingestion_deps / build_real_ingestion_deps (feature6 후속) ---


def test_build_poc_ingestion_deps_returns_all_fake_adapters() -> None:
    """PoC ingestion 부트스트랩 — 모든 어댑터가 Fake 인스턴스 (외부 의존성 0)."""
    from app.api.deps import build_poc_ingestion_deps
    from app.ingestion.chunker import chunk_attachment as real_chunk_attachment
    from app.pipeline.ingestion_graph import IngestionGraphDeps, manage_document_analyzer
    from app.storage.chunk_lookup import FakeChunkTextLookup
    from app.storage.jobs import FakeIngestionJobsRepository
    from app.storage.mongo_cache import FakeEmbeddingCache

    deps = build_poc_ingestion_deps()

    assert isinstance(deps, IngestionGraphDeps)
    assert isinstance(deps.dense_embedder, FakeDenseEmbedder)
    assert isinstance(deps.sparse_embedder, FakeSparseEmbedder)
    assert isinstance(deps.cache, FakeEmbeddingCache)
    assert isinstance(deps.chunk_lookup, FakeChunkTextLookup)
    assert isinstance(deps.jobs, FakeIngestionJobsRepository)
    # 문서 분석기 노드는 실 어댑터(manage_document_analyzer) 기본값 — Agent 통합 4/4
    assert deps.document_analyzer_node is manage_document_analyzer
    # chunk_attachment_fn 은 실 함수 default — 운영 시점에 fake 주입 가능
    assert deps.chunk_attachment_fn is real_chunk_attachment


def test_build_poc_ingestion_deps_bootstrap_collections() -> None:
    """PoC ingestion 부트스트랩 시 :memory: Qdrant 3 Pool 컬렉션이 생성된다."""
    from app.api.deps import build_poc_ingestion_deps

    deps = build_poc_ingestion_deps()

    settings = _settings()
    actual = {c.name for c in deps.store._client.get_collections().collections}
    assert settings.qdrant_title_pool in actual
    assert settings.qdrant_content_pool in actual
    assert settings.qdrant_label_pool in actual


@pytest.fixture()
def patched_real_ingestion_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """build_real_ingestion_deps 가 호출하는 운영 어댑터 6종을 가짜로 대체.

    - E5DenseEmbedder / BM25SparseEmbedder (lazy import — 기존 patched_real_adapters와 동일 패턴)
    - QdrantPoolStore.from_settings → :memory:
    - MongoEmbeddingCache.from_settings → FakeEmbeddingCache
    - MongoChunkTextLookup.from_settings → FakeChunkTextLookup
    - MongoIngestionJobsRepository.from_settings → FakeIngestionJobsRepository
    """
    from app.storage.chunk_lookup import FakeChunkTextLookup, MongoChunkTextLookup
    from app.storage.jobs import (
        FakeIngestionJobsRepository,
        MongoIngestionJobsRepository,
    )
    from app.storage.mongo_cache import FakeEmbeddingCache, MongoEmbeddingCache

    captured: dict[str, Any] = {
        "dense_init": None,
        "sparse_init": None,
        "store_from_settings": None,
        "cache_from_settings": None,
        "lookup_from_settings": None,
        "jobs_from_settings": None,
    }

    def _fake_dense_factory(*args: Any, **kwargs: Any) -> _FakeRealDense:
        captured["dense_init"] = {"args": args, "kwargs": kwargs}
        return _FakeRealDense()

    def _fake_sparse_factory(*args: Any, **kwargs: Any) -> _FakeRealSparse:
        captured["sparse_init"] = {"args": args, "kwargs": kwargs}
        return _FakeRealSparse()

    def _fake_store_from_settings(
        cls: type[QdrantPoolStore], settings: Settings, *, dense_dimension: int = 1024
    ) -> QdrantPoolStore:
        captured["store_from_settings"] = {"dense_dimension": dense_dimension}
        return QdrantPoolStore.in_memory(settings, dense_dimension=dense_dimension)

    def _fake_cache_from_settings(
        cls: type[MongoEmbeddingCache], settings: Settings
    ) -> FakeEmbeddingCache:
        captured["cache_from_settings"] = {"settings": settings}
        return FakeEmbeddingCache()

    def _fake_lookup_from_settings(
        cls: type[MongoChunkTextLookup], settings: Settings, **kwargs: Any
    ) -> FakeChunkTextLookup:
        captured["lookup_from_settings"] = {"settings": settings, "kwargs": kwargs}
        return FakeChunkTextLookup()

    def _fake_jobs_from_settings(
        cls: type[MongoIngestionJobsRepository], settings: Settings, **kwargs: Any
    ) -> FakeIngestionJobsRepository:
        captured["jobs_from_settings"] = {"settings": settings, "kwargs": kwargs}
        return FakeIngestionJobsRepository()

    import app.ingestion.embedder.dense as dense_module
    import app.ingestion.embedder.sparse as sparse_module

    monkeypatch.setattr(dense_module, "E5DenseEmbedder", _fake_dense_factory)
    monkeypatch.setattr(sparse_module, "BM25SparseEmbedder", _fake_sparse_factory)
    monkeypatch.setattr(QdrantPoolStore, "from_settings", classmethod(_fake_store_from_settings))
    monkeypatch.setattr(
        MongoEmbeddingCache, "from_settings", classmethod(_fake_cache_from_settings)
    )
    monkeypatch.setattr(
        MongoChunkTextLookup, "from_settings", classmethod(_fake_lookup_from_settings)
    )
    monkeypatch.setattr(
        MongoIngestionJobsRepository, "from_settings", classmethod(_fake_jobs_from_settings)
    )

    return captured


def test_build_real_ingestion_deps_wires_all_real_adapter_classes(
    patched_real_ingestion_adapters: dict[str, Any],
) -> None:
    """운영 ingestion 부트스트랩 — 6 어댑터 모두 호출 + IngestionGraphDeps 시그니처 정합."""
    from app.api.deps import build_real_ingestion_deps
    from app.pipeline.ingestion_graph import IngestionGraphDeps

    deps = build_real_ingestion_deps(_settings())

    assert isinstance(deps, IngestionGraphDeps)
    captured = patched_real_ingestion_adapters
    assert captured["dense_init"] is not None
    assert captured["sparse_init"] is not None
    assert captured["store_from_settings"] is not None
    assert captured["cache_from_settings"] is not None
    assert captured["lookup_from_settings"] is not None
    assert captured["jobs_from_settings"] is not None
    # dense_dimension 은 어댑터 보고 값으로 전달 (E5 = 1024).
    assert captured["store_from_settings"]["dense_dimension"] == 1024
    # 운영 모드는 Fake 임베더 미사용 (PoC 경로 분리 회귀 보호).
    assert not isinstance(deps.dense_embedder, FakeDenseEmbedder)
    assert not isinstance(deps.sparse_embedder, FakeSparseEmbedder)


def test_build_real_ingestion_deps_passes_dense_model_name(
    patched_real_ingestion_adapters: dict[str, Any],
) -> None:
    """settings.dense_embedding_model 이 E5DenseEmbedder 생성자에 전달된다."""
    from app.api.deps import build_real_ingestion_deps

    settings = _settings()
    build_real_ingestion_deps(settings)

    dense_kwargs = patched_real_ingestion_adapters["dense_init"]
    passed = list(dense_kwargs["args"]) + list(dense_kwargs["kwargs"].values())
    assert settings.dense_embedding_model in passed


def test_build_real_ingestion_deps_does_not_eagerly_import_sentence_transformers() -> None:
    """app.api.deps 모듈 import 단계에서 sentence-transformers / fastembed 가 끌어와지지 않는다.

    embedding extra 미설치 환경에서도 PoC 경로(build_poc_ingestion_deps + build_poc_deps)
    는 동작해야 하므로 운영 어댑터는 함수 본문 내 lazy import 여야 한다 (build_real_deps
    와 동일 정책 — 회귀 보호).
    """
    import ast
    import inspect

    import app.api.deps as deps_module

    tree = ast.parse(inspect.getsource(deps_module))
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top_level_imports.add(module)
            for alias in node.names:
                top_level_imports.add(f"{module}.{alias.name}")

    forbidden_at_top = {
        "sentence_transformers",
        "fastembed",
        "app.ingestion.embedder.dense",
        "app.ingestion.embedder.sparse",
    }
    leaked = top_level_imports & forbidden_at_top
    assert not leaked, f"실 어댑터/heavy 의존성이 모듈 최상단에서 import됨: {leaked}"
