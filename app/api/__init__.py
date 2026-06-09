"""app.api — FastAPI 앱 및 라우트.

RAG 파이프라인을 BFF에 노출하는 HTTP 계층. API 계약은 docs/api-spec.md.

모듈:
- main.py    FastAPI 앱 생성(create_app), 미들웨어, 헬스 체크(/healthz), lifespan
             (settings.use_real_adapters 토글로 build_real_deps / build_poc_deps
             분기 후 build_query_graph). uvicorn 진입점.
- routes.py  POST /ml/query — Query 그래프 호출 + SSE(EventSourceResponse) 스트리밍
- errors.py  공통 예외 → Error Response 변환 (UNAUTHORIZED / UPSTREAM_LLM_ERROR 등)
- deps.py    QueryGraphDeps 부트스트랩 — build_poc_deps (:memory: Qdrant + Fake +
             samples 자동 인덱싱) / build_real_deps (E5 + BM25 + Qdrant from_settings
             + CrossEncoderRerankerImpl, 운영 모드)

이 계층은 요청 검증·응답 변환만 담당하고 비즈니스 로직은 app.pipeline / app.query에 둔다.

구현 상태:
- main.py    create_app + lifespan + /healthz   [feature11 통합 Phase 2 + 토글 분기]
- routes.py  POST /ml/query SSE 라우트  [feature11 통합 Phase 2 / feature13 마이그레이션]
- errors.py  ErrorCode + ErrorResponse           [feature11 통합 Phase 2]
- deps.py    build_poc_deps + build_real_deps    [feature11 통합 Phase 2 + 후속]
"""

from app.api.errors import ErrorCode, ErrorResponse, error_response
from app.api.main import app, create_app

__all__ = [
    "ErrorCode",
    "ErrorResponse",
    "app",
    "create_app",
    "error_response",
]
