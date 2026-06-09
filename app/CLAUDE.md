# app/CLAUDE.md — RAG Pipeline 전용 규칙

이 문서는 RAG Pipeline 영역(`app/`, `tests/`)에서만 적용되는 규칙을 정의한다.
작업 시 루트 `CLAUDE.md`의 공통 규칙을 먼저 적용하고, 이 문서의 규칙을 추가로 따른다.

설계 기준 문서: `docs/rag-pipeline-design.md`, `docs/chunking-strategy.md`,
`docs/db-schema.md`, `docs/api-spec.md`, `docs/architecture.md`.

> `app/schemas`·`app/ingestion/{chunker,embedder,embedding,vector_store,indexer}`·`app/storage`·`app/adapters`는
> `../ingestion`과 **바이트 동일로 공유하는 자산**이며 본 레포(`rag`)가 owning source다. **한쪽만 수정하지 않는다** —
> 변경 시 양 레포를 같은 change-set로 동기화한다(루트 `CLAUDE.md` "공유 자산 변경 동기화" +
> `docs/adr/0003-ingestion-rag-shared-contracts.md`). 정확한 파일 목록·추출 가이드는 공유 표면 매니페스트 참조.

> Codex를 사용하는 팀원은 이 파일을 `app/AGENTS.md`로 복사하거나 심볼릭 링크로 연결해 사용한다.

---

## 1. 컴포넌트 분류 규칙 (Agent / Pipeline / Storage)

모든 컴포넌트는 `[Agent]` / `[Pipeline]` / `[Storage]` 중 하나로 분류하고, 모듈 docstring과
클래스/함수 주석에 명시한다. 이 분류는 비용 모니터링·테스트 전략의 기준이다.

- **Agent** — LLM을 호출해 판단하는 컴포넌트. 비결정론적. 프롬프트는 역할·입력·출력·제약을 포함하고, 변경 시 의도·기대 효과·부작용 가능성을 문서화한다.
- **Pipeline** — 사전 정의 규칙으로 결정론적으로 처리. 동일 입력 → 동일 출력. 반드시 단위 테스트로 회귀를 보호한다.
- **Storage** — 데이터 저장·조회. 외부 의존성은 어댑터/클라이언트 계층으로 분리한다.

`app/` 디렉토리별 분류는 각 패키지의 `__init__.py` docstring을 따른다.

## 2. 파이프라인 단계 분리

- Ingestion 단계(문서 분석기 → 첨부 분석기 → Adaptive Chunker → Dual Embedding → Multi-Pool Store)와 Query 단계(ACL Pre-filter → 히스토리 → 라우터 → 검색·재순위화 → 답변 생성 → 검증 → 포맷터)를 명확히 분리한다.
- 각 단계는 LangGraph 노드 단위로 단일 책임을 갖고, 노드 입출력 상태는 `app/schemas`의 `RagState`(Query)·`IngestionState`(Ingestion)로 통일한다.
- 한 단계가 다른 단계의 책임(예: 검색이 생성을, 생성이 검증을)을 침범하지 않는다.

## 3. 보안·정확성 (절대 규칙)

- **ACL Pre-filtering을 우회하지 않는다.** Qdrant 검색 호출은 `@enforce_acl` 데코레이터를 통과해야 하며, ACL 필터가 없는 호출은 `ACLViolationError`로 거부한다.
- ACL 필터링을 LLM 프롬프트에 위임하지 않는다 (Prompt Injection 우회 방지).
- ACL 필드 모델은 **`allowed_groups`/`allowed_users` 기반으로 확정**되었다 (api-spec v2.5 / ADR 0003, `docs/db-schema.md` §1.4). 검색은 `app/query/acl.py`의 `build_acl_filter`가 사용자 `groups`에 `allow_authenticated` 공개 sentinel `"*"`(`PUBLIC_ACL_GROUP`)를 주입해 page-level ACL과 매칭한다. PoC fixture 경로는 `space:{key}` 합성을 fallback으로 유지한다. `@enforce_acl` 강제 원칙은 모든 경우에 유지한다.
- 출처 없는 답변을 생성하는 방향으로 수정하지 않는다. 검색 결과 0건이면 LLM을 호출하지 않고 표준 분기 응답을 반환한다.
- 답변 검증(1단계 규칙 + 2단계 LLM 평가자)을 우회하거나 비활성화하지 않는다.
- ACL 정보가 전혀 없는 PageObject·청크는 색인하지 않는다 (`INVALID_ACL`).
- Secret·API Key·`access_token`은 코드·로그·테스트 픽스처에 포함하지 않는다. 설정은 `app/config.py`에서 환경 변수로 주입한다.

## 4. 결정론·멱등성

- Pipeline 컴포넌트는 결정론을 유지한다. `chunk_id`는 SHA1(`page_id`+`chunk_index`+`attachment_id`)로 계산하며 임의 UUID를 쓰지 않는다.
- 동일 `chunk_id` + `version_number`는 재임베딩·재upsert를 스킵한다 (`embedding_cache`).
- 청킹·임베딩 설정 변경 시 변경 이유·기대 효과와 평가 쿼리 결과를 `docs/ai/working-log.md`에 기록한다.

## 5. LLM 호출 규칙

- 모델 라우팅을 준수한다: 답변 생성기는 GPT-4o(라우터 결정 시 4o-mini), 라우터·검증 2단계·히스토리 관리자·문서 분석기는 GPT-4o-mini.
- 구조화 출력이 필요한 호출(라우터, 답변 생성기, 문서 분석기)은 Function Calling으로 스키마를 강제한다.
- LLM 호출에는 타임아웃과 Fallback을 정의한다 (`docs/rag-pipeline-design.md` §8).
- 실험성 프롬프트·모델은 production path에 직접 연결하지 않는다.

## 6. 테스트 규칙

- 구현 전 테스트 케이스를 먼저 정리한다 (`docs/ai/workflow.md`의 테스트 우선 절차).
- Pipeline 컴포넌트(Chunker, ACL 필터, Hybrid Search 스코어 융합, 응답 포맷터 등)는 Unit Test를 필수로 작성한다.
- Agent 컴포넌트는 LLM 응답을 mock/fake로 대체하고, 입출력 스키마 계약과 Fallback 분기를 테스트한다.
- LLM·Qdrant·MongoDB·MySQL 등 외부 의존성은 테스트에서 mock/fake로 대체한다.
- 버그 수정 시 재현 테스트를 먼저 작성한다.

## 7. 평가 규칙

- 검색·청킹·프롬프트 변경 후 최소 평가 질문 세트(Evaluation Set)를 실행한다.
- Precision@k, 응답 지연(latency), 출처 정확도, 환각 비율 중 변경 영향이 있는 항목을 기록한다.
- 평가 결과는 `docs/ai/working-log.md`에 남긴다.

## 8. 코딩 컨벤션

- Python 3.11 기준. `docs/conventions.md`의 표준 주석 블록을 주요 모듈·클래스·public 함수에 작성한다.
- 외부 호출(LLM, Qdrant, MongoDB, MySQL)은 어댑터/클라이언트 계층으로 분리하고 파이프라인 노드에서 직접 호출하지 않는다.
- 데이터 모델은 `app/schemas`의 Pydantic 모델로 정의하고 계층 간 dict를 그대로 전달하지 않는다.
