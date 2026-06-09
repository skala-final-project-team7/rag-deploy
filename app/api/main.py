"""FastAPI 앱 entrypoint — lifespan에서 Query 그래프 컴파일 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합 Phase 2 — FastAPI 애플리케이션의 진입점. lifespan에서
          ``build_poc_deps`` (또는 후속 ``build_real_deps``)를 호출해 Query 그래프
          의존성을 부트스트랩하고, ``build_query_graph`` 로 컴파일한 그래프를
          ``app.state.graph`` 에 저장한다. 라우트는 ``app.api.routes`` 의 라우터를
          마운트한다. CORS·인증 미들웨어는 BFF가 담당하므로 본 앱은 추가하지
          않는다 (docs/api-spec.md NOTE).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 Phase 2 — create_app + lifespan +
    헬스 라우트(/healthz)
  - 2026-05-18, build_real_deps 후속 — lifespan이 settings.use_real_adapters
    토글을 읽어 build_real_deps / build_poc_deps 분기. 기본값(False)에서는
    동작 변화 없음.
  - 2026-05-19, feature12 (PDF 0518_RAG.pdf #4) — Prometheus 운영 모니터링
    instrumentator wiring 추가. create_app 시점에 ``Instrumentator().instrument
    (app).expose(app, endpoint="/metrics", include_in_schema=False)`` 를 1회
    호출해 HTTP 표준 메트릭(요청 수·지연 히스토그램·상태 코드별 카운터)을
    자동 수집하고 ``/metrics`` 엔드포인트로 노출한다. ``/metrics`` 는 BFF
    인증을 우회하는 Prometheus scraper 직접 접근 경로 (CORS·인증 미들웨어는
    BFF 가 담당 — docs/api-spec.md NOTE 정합). LLM 커스텀 메트릭 (환각
    비율·Precision@3 등) 은 feature17 (평가 세션) 으로 이관.
  - 2026-05-19, feature14 SSE token streaming — lifespan 이 build_query_graph
    뿐 아니라 ``build_query_graph_for_streaming`` 도 함께 컴파일해 ``app.state
    .streaming_graph`` 에 저장한다. SSE 라우트가 ``stream=True`` 요청에 대해
    rerank 까지만 실행한 뒤 OpenAI streaming + 검증을 직접 수행하는 흐름
    (설계서 §4.6.4).
  - 2026-05-19, feature16 운영 라이브 smoke 반영 — Prometheus histogram bucket
    조정. 기존 ``metrics.default()`` 의 lowr_buckets (0.1/0.5/1.0) 만으로는
    설계서 §6.4 KPI P95 5초 / latency 30초 임계 가시화가 불가능했다 (smoke
    측정 결과 5건 모두 ``le=+Inf`` 에만 누적). ``Instrumentator.add(metrics
    .default(latency_highr_buckets=..., latency_lowr_buckets=...))`` 로 KPI
    임계 (1·2.5·5·10·30·60 초) 가 포함된 bucket 을 명시 등록한다.
--------------------------------------------------
[호환성]
  - Python 3.11.x, FastAPI 0.111+
  - 실행 예시: ``uvicorn app.api.main:app --host 0.0.0.0 --port 8000``
--------------------------------------------------
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from app.api.deps import build_poc_deps, build_real_deps
from app.api.routes import router as query_router
from app.config import get_settings
from app.pipeline.query_graph import (
    QueryGraphDeps,
    build_query_graph,
    build_query_graph_for_streaming,
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 시작 시 deps + 그래프 컴파일을 한 번 수행해 app.state에 보관한다.

    분기 — ``Settings.use_real_adapters`` 토글(``RAG_USE_REAL_ADAPTERS=true``):
      - True : ``build_real_deps`` (E5 + BM25 + Qdrant from_settings + CrossEncoder
        실 모델). 모델 다운로드(약 2.4 GB) + Qdrant 서버 접속 필요. 운영 진입점.
      - False(기본): ``build_poc_deps`` (:memory: Qdrant + Fake everything + samples
        자동 인덱싱). 외부 컨테이너·모델 없이 즉시 응답.
    """
    settings = get_settings()
    deps: QueryGraphDeps = (
        build_real_deps(settings) if settings.use_real_adapters else build_poc_deps(settings)
    )
    app.state.deps = deps
    app.state.settings = settings
    app.state.graph = build_query_graph(deps)
    # streaming_graph 는 SSE 라우트의 ``stream=True`` 분기 전용 — rerank 까지만
    # 실행하고 답변 생성·검증은 라우트가 직접 OpenAI streaming + verify_pipeline_node
    # 로 수행한다. 운영 모드에서만 사용되지만 lifespan 부담이 적어 PoC 에서도 함께
    # 컴파일한다 (PoC 경로는 stream=true 라도 자동 fallback 으로 비-streaming 실행).
    app.state.streaming_graph = build_query_graph_for_streaming(deps)
    try:
        yield
    finally:
        # Qdrant `:memory:` 클라이언트는 GC에 맡긴다 — 명시 close 없음.
        # 운영 from_settings 클라이언트도 별도 세션 종료 절차가 없어 GC에 맡긴다.
        app.state.graph = None
        app.state.streaming_graph = None
        app.state.deps = None
        app.state.settings = None


def create_app() -> FastAPI:
    """FastAPI 앱 인스턴스를 생성한다 — 운영·테스트 공통 팩토리.

    테스트는 ``create_app()`` 후 ``app.dependency_overrides`` 로 그래프 의존성을
    교체하거나, lifespan을 건너뛰고 ``app.state.graph`` 를 수동 설정한다.
    """
    app = FastAPI(
        title="LINA RAG Pipeline",
        version="0.1.0",
        description="척척학사(LINA) Confluence 기반 RAG 챗봇 서비스의 RAG 파이프라인",
        lifespan=_lifespan,
    )
    app.include_router(query_router)

    # 운영 모니터링 — Prometheus instrumentator (feature12, PDF 0518_RAG.pdf #4).
    # ``/metrics`` 는 OpenAPI 스키마에서 제외(include_in_schema=False)하며 BFF 인증을
    # 우회하는 Prometheus scraper 직접 접근 경로. 본 저장소가 비인증으로 동작하므로
    # 별도 미들웨어 분기 불필요 (CORS·인증은 BFF 담당 — docs/api-spec.md NOTE).
    #
    # histogram bucket — 설계서 §6.4 KPI P95 5초 / latency 30초 임계 가시화 (feature16
    # smoke 반영). default lowr_buckets (0.1/0.5/1.0) 만으로는 우리 LLM 응답 latency
    # (3~10초) 분포가 모두 ``le=+Inf`` bucket 에만 누적돼 P95 측정 불가했다.
    instrumentator = Instrumentator()
    instrumentator.add(
        metrics.default(
            latency_highr_buckets=(
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
                30.0,
                60.0,
                float("inf"),
            ),
            latency_lowr_buckets=(1.0, 5.0, 30.0),
        )
    )
    instrumentator.instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """기본 헬스 체크 — Kubernetes readiness probe 대상."""
        return {"status": "ok"}

    return app


# uvicorn 진입점 (``uvicorn app.api.main:app``).
app = create_app()
