"""FastAPI 의존성 부트스트랩 — Query 그래프 의존성 구성 헬퍼 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : FastAPI 앱이 시작할 때 한 번 호출되어 Query 그래프 의존성
          (``QueryGraphDeps``)을 부트스트랩한다. PoC 기본(``build_poc_deps``)은
          :memory: Qdrant + Fake embedder/reranker + samples 자동 인덱싱으로 외부
          컨테이너·모델 없이 서버가 즉시 응답 가능하도록 한다. 운영 모드
          (``build_real_deps``)는 E5DenseEmbedder + BM25SparseEmbedder + Qdrant
          from_settings + CrossEncoderRerankerImpl로 실 어댑터를 부트스트랩한다.
          분기는 ``Settings.use_real_adapters`` 토글(``RAG_USE_REAL_ADAPTERS=true``)
          이 결정한다.
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 Phase 2 — build_poc_deps + samples
    자동 인덱싱
  - 2026-05-18, build_real_deps 후속 — 운영 어댑터 부트스트랩 함수 추가
    (E5 / BM25 / Qdrant from_settings / CrossEncoderRerankerImpl). 실 모델
    import는 함수 본문 내 lazy로 처리해 embedding extra 미설치 환경에서도
    PoC 경로(build_poc_deps)와 모듈 import는 동작하도록 한다.
  - 2026-05-18, 풀 텍스트 lookup 후속 — chunk_lookup 어댑터 wiring 추가. PoC는
    빈 FakeChunkTextLookup(QueryGraphDeps 기본값), 운영은 MongoChunkTextLookup
    .from_settings로 chunk_lookup 컬렉션(db-schema §2.5)을 가리키도록 한다.
  - 2026-05-18, 풀 텍스트 lookup Phase 2 — build_poc_deps 가 FakeChunkTextLookup
    1 인스턴스를 _ingest_samples 와 QueryGraphDeps 양쪽에 공유 주입. samples
    인덱싱 시점에 attachment_download_urls 매핑을 page.attachments 에서 합성해
    indexer 에 전달, 첨부 청크의 Source.download_url 이 검색 시점에 채워지도록 한다.
  - 2026-05-18, feature6 후속 — build_poc_ingestion_deps + build_real_ingestion_deps
    추가. Ingestion 그래프(feature6 Phase 4)의 부트스트랩 진입점. PoC 는 모든 어댑터
    가 Fake, 운영은 E5/BM25/Qdrant.from_settings + Mongo 3종(embedding_cache /
    chunk_lookup / ingestion_jobs) 모두 lazy import. Ingestion 은 query 와 별도 진입점
    (RabbitMQ Worker / 별도 트리거 시스템) 책임이므로 lifespan 에 자동 wire 하지 않음.
  - 2026-05-18, Agent 통합 1/4 — query-routing-agent 어댑터 wiring.
    build_poc_deps 는 QueryGraphDeps 기본값(routing_provider=None → fake provider 자동)
    그대로 사용 — 외부 API 키 없이 PoC 경로 동작 유지. build_real_deps 는
    OpenAIRoutingLLMProvider 를 lazy import 해 OPENAI_API_KEY 환경변수 기반으로 주입.
  - 2026-05-19, Agent 통합 2/4 — answer-generation-agent 어댑터 wiring.
    본 세션(2/4)에서는 build_poc_deps·build_real_deps 모두 generator_provider=None
    (→ FakeAnswerLLMProvider) 을 사용했다. 이후 (B) 항목에서 build_real_deps 에
    OpenAIAnswerLLMProvider + build_openai_chat_transport 를 주입하도록 갱신됨.
  - 2026-05-19, Agent 통합 3/4 — answer-verification-agent 어댑터 wiring.
    build_poc_deps 는 QueryGraphDeps 기본값(verifier_provider=None →
    FakeEvaluatorProvider 자동) 그대로 사용 — 외부 API 키 없이 PoC 경로 동작.
    build_real_deps 는 OpenAIEvaluatorProvider 를 lazy import 해 OPENAI_API_KEY
    환경변수 기반으로 주입 (agent 자체 urllib HTTP transport 가 있어 운영 즉시
    호출 가능 — answer-generation-agent 와 차이점). 모델은 GPT-4o-mini (설계서
    §4.7.2 / app/CLAUDE.md §5 라우팅 정책).
  - 2026-05-19, (B) 운영 OpenAI HTTP transport — build_real_deps 에 답변 생성기
    OpenAIAnswerLLMProvider + ``build_openai_chat_transport`` 주입. 설계서 §4.6.3
    GPT-4o 운영 호출 정합. ``settings.openai_api_key`` / ``settings.llm_answer
    _model`` 사용. build_poc_deps 는 기존대로 fake 자동 (외부 키 없이 동작).
  - 2026-05-19, feature12 — 라우터·검증기 provider 에 ``settings.openai_api
    _key.get_secret_value()`` 명시 전달. 라우터는 ``OpenAIRoutingLLMProvider.
    from_config`` (env 의존) 대신 ``__init__(config, api_key)`` 직접 호출로
    변경. 검증기는 ``AnswerVerificationConfig(evaluator_model=..., openai_api
    _key=...)`` 로 config 객체에 키를 채워 ``OpenAIEvaluatorProvider`` 가
    ``os.environ.get("OPENAI_API_KEY")`` fallback 을 거치지 않도록 한다. 답변
    생성기는 이미 명시 주입 중. CLAUDE.md 절대 규칙 "Secret 은 ``app/config.py``
    에서 환경 변수로 주입" 정합 + ``.env`` 의 ``OPENAI_API_KEY`` 중복 환경변수
    제거.
  - 2026-05-19, feature17a 후속 라우터 prompt 보강 — OpenAIRoutingLLMProvider
    에 ``build_openai_routing_transport`` 를 주입한다. agent 의 빈약한 default
    transport 가 만든 system prompt ("You classify RAG query routing intent.")
    를 본 저장소가 만든 4종 의도 정의·예시·구분 기준·출력 schema 강제 prompt
    로 교체해 의도 분류 정확도 보강 (feature16 발견 #2 fix). vendoring 패키지
    무수정 보존 정합.
--------------------------------------------------
[호환성]
  - Python 3.11.x, FastAPI 0.111+
  - NOTE: 본 모듈 최상단 import는 외부 의존성(qdrant-client `:memory:`)만 사용한다.
          sentence-transformers / fastembed는 build_real_deps 호출 시점에 lazy
          import되며, embedding extra 미설치 환경에서는 build_real_deps 호출 시
          ImportError로 빨리 실패한다.
--------------------------------------------------
"""

from functools import partial
from pathlib import Path

from app.adapters.json_fixture import JsonFixtureSourceAdapter
from app.config import Settings, get_settings
from app.ingestion.chunker import chunk_attachment, chunk_page
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder
from app.ingestion.indexer import index_chunks
from app.pipeline.ingestion_graph import IngestionGraphDeps, manage_document_analyzer
from app.pipeline.query_graph import QueryGraphDeps
from app.query.reranker.base import FakeCrossEncoderReranker
from app.schemas.chunk import Chunk
from app.storage.chunk_lookup import FakeChunkTextLookup
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_client import QdrantPoolStore

# PoC 임베딩 차원 — Fake에서는 코사인 유사도 계산만 정합하면 충분하므로 64로 가볍게.
# 실 어댑터(E5)는 1024차원으로 별도 부트스트랩.
_POC_DENSE_DIMENSION = 64


def build_poc_deps(settings: Settings | None = None) -> QueryGraphDeps:
    """PoC 기본 QueryGraphDeps — :memory: Qdrant + Fake everything + samples 인덱싱.

    1. Fake Dense / Sparse 임베더 인스턴스화 (모델 다운로드 없음).
    2. ``QdrantPoolStore.in_memory`` 로 :memory: 클라이언트 + 3 Pool 컬렉션 부트스트랩.
    3. ``JsonFixtureSourceAdapter`` 로 ``samples/`` PageObject 로드.
    4. ``chunk_page`` 로 본문 청크 생성 (PoC ACL 합성 포함).
    5. ``index_chunks`` 로 3 Pool 모두에 적재 (멱등성 캐시는 Fake).
    6. Agent stub 3종은 ``QueryGraphDeps`` 기본값을 그대로 사용.

    Args:
        settings: 환경 설정. None이면 ``get_settings()`` 로 lazy 로드.

    Returns:
        부트스트랩이 끝난 ``QueryGraphDeps`` — FastAPI lifespan에서 그래프 빌더에 주입.
    """
    settings = settings or get_settings()

    dense = FakeDenseEmbedder(dimension=_POC_DENSE_DIMENSION)
    sparse = FakeSparseEmbedder()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=_POC_DENSE_DIMENSION)
    store.bootstrap_collections()

    # samples 자동 인덱싱 — 외부 데이터·모델 없이 ACL 매칭 검색이 동작하도록.
    # chunk_lookup은 _ingest_samples 와 QueryGraphDeps 양쪽에 1 인스턴스 공유 — 인덱싱
    # 시 적재한 풀 텍스트·첨부 download_url 을 검색 시 rerank 노드가 그대로 조회한다.
    chunk_lookup = FakeChunkTextLookup()
    _ingest_samples(
        store=store,
        dense=dense,
        sparse=sparse,
        samples_dir=Path(settings.samples_dir),
        chunk_lookup=chunk_lookup,
    )

    return QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=FakeCrossEncoderReranker(),
        chunk_lookup=chunk_lookup,
    )


def build_real_deps(settings: Settings | None = None) -> QueryGraphDeps:
    """운영 어댑터 부트스트랩 — E5 + BM25 + Qdrant from_settings + CrossEncoder.

    PoC 부트스트랩(``build_poc_deps``)과 동일한 ``QueryGraphDeps`` 시그니처를 반환
    한다. 실 모델 import는 함수 본문 내 lazy — embedding extra 미설치 환경에서도
    PoC 경로와 모듈 import는 영향 받지 않는다. 운영 모드는 모델 다운로드(약
    2.4 GB: e5-large 2.24 GB + cross-encoder 130 MB) + Qdrant 서버 접속을 요구
    하므로 ``RAG_USE_REAL_ADAPTERS=true`` 환경 변수로 명시 활성화 후 사용한다.

    samples 자동 인덱싱은 수행하지 않는다 — 운영 환경은 별도 ingestion 파이프라인이
    Qdrant에 적재했다고 가정한다. 컬렉션이 비어 있으면 검색 0건으로 떨어져 그래프
    의 ``empty_retrieval_node`` 가 표준 RETRIEVAL_EMPTY 응답을 반환한다.

    Args:
        settings: 환경 설정. None이면 ``get_settings()`` 로 lazy 로드.

    Returns:
        실 어댑터 4종이 wiring된 ``QueryGraphDeps``.

    Raises:
        ImportError: sentence-transformers / fastembed 미설치 시 (embedding extra
            누락). 운영 모드 활성화 전 ``pip install -e .[embedding]`` 필요.
    """
    settings = settings or get_settings()

    # 실 어댑터 import는 lazy — embedding extra 미설치 환경에서도 build_poc_deps와
    # 본 모듈 import는 동작해야 한다. import 실패 시 호출자에게 ImportError 전파.
    from answer_generation_agent.config import AnswerGenerationConfig
    from answer_generation_agent.generation.answer_generation import OpenAIAnswerLLMProvider
    from answer_verification_agent.config import AnswerVerificationConfig
    from answer_verification_agent.evaluator.providers import OpenAIEvaluatorProvider
    from app.ingestion.embedder.dense import E5DenseEmbedder
    from app.ingestion.embedder.sparse import BM25SparseEmbedder
    from app.query.openai_transport import (
        CONSERVATIVE_SYSTEM_GUARD,
        build_openai_chat_transport,
        select_generator_response_format,
    )
    from app.query.reranker.cross_encoder import CrossEncoderRerankerImpl
    from app.query.routing_transport import build_openai_routing_transport
    from app.storage.chunk_lookup import MongoChunkTextLookup
    from query_routing_agent.config import QueryRoutingConfig
    from query_routing_agent.llm import OpenAIRoutingLLMProvider

    dense = E5DenseEmbedder(settings.dense_embedding_model)
    sparse = BM25SparseEmbedder()
    # dense_dimension은 어댑터가 모델 로드 후 보고한 값을 사용 (E5-large = 1024).
    store = QdrantPoolStore.from_settings(settings, dense_dimension=dense.dimension)
    store.bootstrap_collections()
    reranker = CrossEncoderRerankerImpl(
        settings.cross_encoder_model,
        device=settings.cross_encoder_device,
        temperature=settings.cross_encoder_temperature,
    )
    # chunk_lookup은 운영 모드에서 MongoDB `chunk_lookup` 컬렉션을 가리킨다 (db-schema §2.5).
    # 컬렉션이 비어 있어도 fetch가 None을 반환하므로 download_url=None으로 안전 fallback.
    chunk_lookup = MongoChunkTextLookup.from_settings(settings)
    # OpenAI API key 는 settings 에서 1회 추출해 라우터·검증기·답변 생성기 3종에 명시
    # 전달한다. CLAUDE.md 절대 규칙 "Secret 은 ``app/config.py`` 에서 환경 변수로
    # 주입" 정합 — provider 가 ``os.environ.get("OPENAI_API_KEY")`` fallback 을 거치지
    # 않도록 직접 주입한다 (feature12, 2026-05-19).
    openai_api_key = settings.openai_api_key.get_secret_value()
    # 라우터는 운영 모드에서 OpenAI provider 를 사용한다. ``__init__(config, api_key,
    # transport)`` 로 settings 키 + 본 저장소가 보강한 transport 를 직접 주입한다.
    # build_openai_routing_transport 가 4종 의도 정의·예시·구분 기준·출력 schema 를
    # system prompt 로 강제해 분류 정확도를 보강한다 (feature16 발견 #2 fix).
    # vendoring 한 query_routing_agent 의 ``_default_transport`` 를 대체 — agent 무수정
    # 보존 + 본 저장소가 prompt 책임. GPT-4o-mini (app/CLAUDE.md §5 라우팅 정책).
    routing_config = QueryRoutingConfig(model="gpt-4o-mini")
    routing_provider = OpenAIRoutingLLMProvider(
        config=routing_config,
        api_key=openai_api_key,
        transport=build_openai_routing_transport(api_key=openai_api_key),
    )
    # 답변 검증 2단계 평가자도 운영 모드는 OpenAI 직접 호출 — agent OpenAIEvaluator
    # Provider 는 자체 urllib transport (default) 가 있어 transport 미주입 OK.
    # ``AnswerVerificationConfig.openai_api_key`` 에 settings 의 키를 채워 env
    # fallback 을 거치지 않도록 한다. 비어 있으면 EvaluatorProviderError 즉시 발생.
    # GPT-4o-mini (설계서 §4.7.2).
    verifier_config = AnswerVerificationConfig(
        evaluator_model="gpt-4o-mini",
        openai_api_key=openai_api_key,
    )
    verifier_provider = OpenAIEvaluatorProvider(config=verifier_config)
    # 답변 생성기는 운영 모드에서 OpenAI Chat Completions 를 직접 호출한다 — agent
    # OpenAIAnswerLLMProvider 는 transport callable (Callable[[dict], dict]) 주입을
    # 요구하므로 본 저장소의 ``build_openai_chat_transport`` 로 동기 HTTP transport
    # 를 만들어 주입한다 (Plan v2 §3 (B), 설계서 §4.6.3). 모델은 GPT-4o (app/
    # CLAUDE.md §5 라우팅 정책). OPENAI_API_KEY 가 비어 있으면 ProviderConfiguration
    # Error 즉시 발생 — 운영 lifespan 진입 직전에 누락 명확히 드러남.
    generator_config = AnswerGenerationConfig(
        model=settings.llm_answer_model,
        fallback_model=settings.llm_aux_model,
    )
    # feature17c-14 — 환각 보수성 guard (opt-in). settings.generator_conservative_guard
    # True 일 때만 CONSERVATIVE_SYSTEM_GUARD 를 transport 에 주입(기본 None=기존 동작).
    generator_guard = CONSERVATIVE_SYSTEM_GUARD if settings.generator_conservative_guard else None
    # feature17c-25 — 문장별 인용 구조 강제 (opt-in). settings.generator_force_citation_schema
    # True 일 때만 Structured Outputs 스키마를 주입(기본 None=기존 json_object 동작).
    generator_response_format = select_generator_response_format(
        settings.generator_force_citation_schema
    )
    generator_provider = OpenAIAnswerLLMProvider(
        api_key=openai_api_key,
        transport=build_openai_chat_transport(
            api_key=openai_api_key,
            response_format=generator_response_format,
            system_prompt_suffix=generator_guard,
        ),
    )

    return QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=reranker,
        chunk_lookup=chunk_lookup,
        routing_provider=routing_provider,
        routing_config=routing_config,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        verifier_full_context=settings.verifier_full_context_grounding,
        generator_provider=generator_provider,
        generator_config=generator_config,
    )


def _ingest_samples(
    *,
    store: QdrantPoolStore,
    dense: FakeDenseEmbedder,
    sparse: FakeSparseEmbedder,
    samples_dir: Path,
    chunk_lookup: FakeChunkTextLookup,
) -> None:
    """``samples/*.json`` 을 PageObject → Chunk → Qdrant + chunk_lookup 에 적재한다 (멱등).

    PoC 픽스처(`JsonFixtureSourceAdapter`)는 page.attachments[*].download_url 을 file://
    URI로 채워둔다. 이 매핑을 indexer 에 전달해 첨부 청크의 chunk_lookup 적재 시점에
    Source.download_url 이 함께 채워지도록 한다.

    본문(chunk_page)뿐 아니라 첨부(chunk_attachment, docx/xlsx)도 적재한다 — 적재
    누락 시 PoC(Mode A) 데모에서 첨부 활용 질의가 검색 0건이 되던 문제(feature17c-4
    의 PoC 경로 대응). 미지원 유형(PDF/CSV)·파싱 실패는 적재를 중단하지 않고 건너뛴다.
    실 운영 ingestion 그래프(`app/pipeline/ingestion_graph.py`)는 이미 첨부를 청킹한다.
    """
    adapter = JsonFixtureSourceAdapter(samples_dir=samples_dir)
    chunks: list[Chunk] = []
    version_by_page_id: dict[str, int] = {}
    attachment_download_urls: dict[str, str] = {}
    for page in adapter.fetch_pages():
        version_by_page_id[page.page_id] = page.version_number
        chunks.extend(chunk_page(page))
        for attachment in page.attachments:
            attachment_download_urls[attachment.attachment_id] = attachment.download_url
            try:
                chunks.extend(chunk_attachment(attachment, page))
            except ValueError:
                # 미지원·암호화 등 ValueError — 적재 계속.
                continue
            except Exception:  # noqa: BLE001 — 파싱 실패도 적재 중단 없이 skip.
                continue

    if not chunks:
        return
    index_chunks(
        chunks,
        version_by_page_id=version_by_page_id,
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=chunk_lookup,
        attachment_download_urls=attachment_download_urls,
    )


# --- Ingestion 그래프 부트스트랩 (feature6 후속) ---


def build_poc_ingestion_deps(settings: Settings | None = None) -> IngestionGraphDeps:
    """PoC IngestionGraphDeps — :memory: Qdrant + Fake 어댑터 6종 (외부 의존성 0).

    Ingestion 그래프(`app/pipeline/ingestion_graph.py`)를 외부 컨테이너·모델·DB 없이
    실행하기 위한 부트스트랩. 본 함수는 samples 자동 인덱싱을 수행하지 않는다 — 그래프
    실행은 호출자가 명시 PageObject 를 전달해 진행한다 (`run_ingestion` 직접 호출).

    Args:
        settings: 환경 설정. None 이면 ``get_settings()`` 로 lazy 로드.

    Returns:
        모든 Fake 어댑터가 wiring 된 ``IngestionGraphDeps``. 문서 분석기[Agent] 노드는
        manage_document_analyzer 기본값(결정론 Fake 분류기 → doc_type="operation").
    """
    # Fake 어댑터들은 함수 본문 내 import — 본 모듈 최상단을 가볍게 유지.
    from app.storage.chunk_lookup import FakeChunkTextLookup
    from app.storage.jobs import FakeIngestionJobsRepository

    settings = settings or get_settings()

    dense = FakeDenseEmbedder(dimension=_POC_DENSE_DIMENSION)
    sparse = FakeSparseEmbedder()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=_POC_DENSE_DIMENSION)
    store.bootstrap_collections()

    return IngestionGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=FakeEmbeddingCache(),
        chunk_lookup=FakeChunkTextLookup(),
        jobs=FakeIngestionJobsRepository(),
    )


def build_real_ingestion_deps(settings: Settings | None = None) -> IngestionGraphDeps:
    """운영 IngestionGraphDeps — E5 + BM25 + Qdrant.from_settings + Mongo 3종.

    feature6 Phase 4 종결에 따른 운영 진입점. Ingestion 그래프 6 어댑터 모두 운영
    어댑터로 wiring:
        - E5DenseEmbedder (sentence-transformers, lazy import)
        - BM25SparseEmbedder (fastembed, lazy import)
        - QdrantPoolStore.from_settings (실 Qdrant 서버)
        - MongoEmbeddingCache.from_settings (db-schema §2.4)
        - MongoChunkTextLookup.from_settings (db-schema §2.5)
        - MongoIngestionJobsRepository.from_settings (db-schema §2.3)

    Ingestion 은 query 와 별도 진입점(RabbitMQ Worker / 운영 트리거 시스템) 책임
    이므로 FastAPI lifespan 에 자동 wire 하지 않는다. 운영 Worker 가 본 함수를 1회
    호출해 deps 를 받고, 매 메시지마다 ``run_ingestion(state, graph=...)`` 으로 단일
    페이지 적재를 수행하는 패턴을 가정한다.

    Args:
        settings: 환경 설정. None 이면 ``get_settings()`` 로 lazy 로드.

    Returns:
        운영 어댑터 6종 + 문서 분석기[Agent]가 wiring 된 ``IngestionGraphDeps``. 문서
        분석기는 OpenAIDocTypeClassifier + MySQLSpaceDocTypeCache 로 조립해
        ``manage_document_analyzer`` 에 partial 로 주입한다(Agent 통합 4/4 완료).

    Raises:
        ImportError: sentence-transformers / fastembed 미설치 시 (embedding extra
            누락). 운영 모드 활성화 전 ``pip install -e .[embedding]`` 필요.
    """
    settings = settings or get_settings()

    # 실 어댑터 import 는 모두 lazy — embedding extra 미설치 환경에서도 PoC 경로와
    # 본 모듈 import 는 동작해야 한다 (build_real_deps 정책 정합).
    from app.ingestion.document_analyzer import DocumentAnalyzer, OpenAIDocTypeClassifier
    from app.ingestion.embedder.dense import E5DenseEmbedder
    from app.ingestion.embedder.sparse import BM25SparseEmbedder
    from app.storage.chunk_lookup import MongoChunkTextLookup
    from app.storage.jobs import MongoIngestionJobsRepository
    from app.storage.mongo_cache import MongoEmbeddingCache
    from app.storage.space_doc_type_cache import MySQLSpaceDocTypeCache

    dense = E5DenseEmbedder(settings.dense_embedding_model)
    sparse = BM25SparseEmbedder()
    # dense_dimension 은 어댑터가 모델 로드 후 보고한 값 (E5-large = 1024).
    store = QdrantPoolStore.from_settings(settings, dense_dimension=dense.dimension)
    store.bootstrap_collections()

    # 문서 분석기 [Agent 통합 4/4] — 운영은 GPT-4o-mini Function Calling 분류기 +
    # MySQL space_doc_type_cache. manage_document_analyzer 에 partial 로 주입한다
    # (라우터/생성기/검증기와 동일 패턴 — 그래프·노드 코드는 무변경).
    document_analyzer = DocumentAnalyzer(
        classifier=OpenAIDocTypeClassifier(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.llm_aux_model,
        ),
        cache=MySQLSpaceDocTypeCache.from_settings(settings),
    )

    return IngestionGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=MongoEmbeddingCache.from_settings(settings),
        chunk_lookup=MongoChunkTextLookup.from_settings(settings),
        jobs=MongoIngestionJobsRepository.from_settings(settings),
        document_analyzer_node=partial(manage_document_analyzer, analyzer=document_analyzer),
    )
