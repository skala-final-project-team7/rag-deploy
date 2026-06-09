"""LINA RAG Pipeline 애플리케이션 패키지.

척척학사(LINA) Confluence 기반 RAG 챗봇 서비스의 RAG 파이프라인.
설계 기준: docs/rag-pipeline-design.md, docs/chunking-strategy.md,
docs/db-schema.md, docs/api-spec.md.

패키지 구조 (각 단계의 [Agent]/[Pipeline]/[Storage] 분류는 하위 패키지 docstring 참조):

    app/
    ├── config.py        설정 (환경 변수, source.type 스위치)  ─ 미생성, env-setup feature
    ├── schemas/         계층 간 데이터 계약 (PageObject, Chunk, RagState, 응답 스키마)
    ├── adapters/        Document Source Adapter 인터페이스 (파이프라인 경계)
    ├── llm/             LLM 클라이언트 래퍼 (GPT-4o / GPT-4o-mini)
    ├── ingestion/       Ingestion 파이프라인 (문서 분석 → 청킹 → 임베딩 → 적재 → 동기화)
    │   └── chunker/     Adaptive Chunker (본문 6유형 + 첨부 3유형)
    ├── query/           Query 파이프라인 (ACL → 히스토리 → 라우터 → 검색 → 생성 → 검증 → 포맷)
    ├── pipeline/        LangGraph 그래프 조립 (Ingestion / Query 그래프)
    └── api/             FastAPI 앱 및 라우트 (POST /ml/query, SSE)

구현은 docs/ai/current-plan.md의 Plan 확정 후 feature 단위로 진행한다 (루트 CLAUDE.md: Plan 우선).
"""
