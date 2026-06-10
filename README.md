# LINA RAG Pipeline

![Last commit](https://img.shields.io/github/last-commit/skala-final-project-team7/rag-deploy?style=flat-square)
![Issues](https://img.shields.io/github/issues/skala-final-project-team7/rag-deploy?style=flat-square)
![PRs](https://img.shields.io/github/issues-pr/skala-final-project-team7/rag-deploy?style=flat-square)
![Top language](https://img.shields.io/github/languages/top/skala-final-project-team7/rag-deploy?style=flat-square)

![Python 3.11](https://img.shields.io/badge/Python_3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-DC244C?style=flat-square&logo=qdrant&logoColor=white)
![Hugging Face](https://img.shields.io/badge/Hugging_Face-FFD21E?style=flat-square&logo=huggingface&logoColor=black)
![Cross-Encoder](https://img.shields.io/badge/Cross--Encoder-Reranking-555?style=flat-square)

척척학사(LINA) Confluence 기반 RAG 챗봇 서비스의 RAG 파이프라인.

본 레포는 SKALA Final Project Team 7의 RAG 파이프라인 **배포용** 모듈이다. 답변 생성기·
질의 라우터 등 LLM 호출이 일어나는 Agent 노드는 별도 `ai-agent` 레포가 소유하며,
`lina-ai-agents @ v0.1.0` 외부 의존성으로 설치된다(vendoring 없음 — `INTEGRATION.md` §1~§3).

---

## 빠른 시작

### 사전 요구

- Python **3.11.x** (`pyproject.toml`에 `requires-python = ">=3.11,<3.12"`)
- Git
- (선택) Docker Desktop — `feature5-B` 이후 외부 서비스가 필요해질 때

### 설치 (Windows PowerShell)

```powershell
# 1) 가상환경 생성 및 활성화
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 의존성 설치
python -m pip install --upgrade pip
python -m pip install -e ".[embedding,ingestion,dev]"

# 3) 환경 변수 (.env.example 미포함 — 인프라 관리)
#    필요한 키는 app/config.py(Settings) 참조. 로컬은 .env 직접 생성 후 RAG_OPENAI_API_KEY 등 입력
```

### 설치 (macOS / Linux)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[embedding,ingestion,dev]"
# 환경변수: .env.example 미포함(인프라 관리). 필요한 키는 app/config.py 참조 — 로컬은 .env 직접 생성
```

> **부팅 필수 extra:** `pip install -e .` (extra 없이) 만으로는 **앱이 기동되지 않는다**.
> `app.api.main` import 시 chunker 가 PyMuPDF·python-docx·openpyxl·BeautifulSoup4(= `ingestion`
> extra)를 끌어오기 때문이다. 따라서 **최소 `pip install -e ".[ingestion]"`** 이 필요하다.
> 무거운 `embedding` extra(torch·sentence-transformers, 약 2.4GB)는 lazy 로딩이라 운영(real)
> 모드(`RAG_USE_REAL_ADAPTERS=true`)에서만 필요하다.

### 사전 진단 (Windows)

```powershell
.\scripts\preflight.ps1
```

Python 버전, 가상환경, 핵심 의존성, `.env`, Docker 설치 여부까지 한 번에 확인한다.

---

## 동작 확인

### 테스트

```powershell
# Windows
.\scripts\test.ps1

# 또는 직접
pytest
```

현재 테스트 **725개**(2026-06-10 pytest 수집 기준 — 에이전트 테스트는 ai-agent 레포 소관).
정확한 통과 수는 `./scripts/test.sh`(Python 3.11) 실행 결과를 따른다.

### 데모 — 데이터 계층 + 청킹

```bash
python -m examples.demo_data_layer
```

`samples/` 의 92개 페이지를 PageObject로 로드한 뒤 379개 청크로 분할하는 과정을
콘솔에 요약 출력한다. 외부 서비스 없이 동작한다.

### 종합 검증

```powershell
.\scripts\verify.ps1   # format → lint → test
```

---

## 실행 · 통합 계약 (인프라 담당자용)

> 컨테이너화·CI·배포는 인프라에서 담당한다. 통합에 필요한 계약을 아래 한 곳에 모은다.

| 항목 | 값 |
|---|---|
| 기동 | `uvicorn app.api.main:app --host 0.0.0.0 --port 8000` |
| 엔드포인트 | `POST /ml/query` · `GET /ml/rag/health` · `GET /healthz` · `GET /metrics`(Prometheus) |
| Python | 3.11.x (`>=3.11,<3.12`) |
| 부팅 필수 설치 | `pip install -e ".[ingestion]"` — **base 설치(`pip install -e .`)만으로는 부팅 불가** |
| 운영(real) 모드 추가 | `[embedding]`(torch·sentence-transformers 약 2.4GB, lazy). `RAG_USE_REAL_ADAPTERS=true` 일 때만 |
| 외부 의존 | Qdrant · MongoDB · MySQL · OpenAI |
| 환경변수 | `app/config.py`(Settings) 참조 — 시크릿은 `RAG_OPENAI_API_KEY` (`.env.example`은 미포함 — 인프라 관리) |
| env 프리픽스 | `RAG_` — **`ingestion` 레포와 동일 env 네임스페이스를 의도적으로 공유**(같은 Qdrant/Mongo/MySQL 을 가리킴). 두 서비스를 하나의 ConfigMap/`.env` 로 합칠 경우 값이 동일해야 충돌하지 않는다 |
| 인증 · CORS | 본 앱은 미들웨어 없음 — **BFF 가 담당** |
| 헬스 체크 성격 | `/healthz`·`/ml/rag/health` 는 **liveness 전용**(항상 `UP`/`ok`, 의존성 끊김은 보고하지 않음) |
| PoC 토글 | `RAG_USE_REAL_ADAPTERS=false`(기본): 외부 컨테이너·모델 없이 인메모리로 즉시 응답 |

---

## 외부 서비스 (선택)

RAG 파이프라인은 다음 3종의 저장소를 사용한다. 기본 PoC 모드(`RAG_USE_REAL_ADAPTERS=false`)는
인메모리로 동작해 이 3종 없이도 테스트/데모가 통과하며, 운영(real) 모드에서 실제로 접속한다.

- **Qdrant** — Multi-Pool Vector Store (`title_pool` / `content_pool` / `label_pool`)
- **MongoDB** — `rag_mock.pages` · `rag_mock.attachments` · `ingestion_jobs` · `embedding_cache`
- **MySQL** — `space_doc_type_cache`

스키마 상세는 [`docs/db-schema.md`](docs/db-schema.md).

### 로컬 백킹 서비스

`docker-compose.yml`은 이 배포 레포에 **미포함**(인프라 관리). 위 3종은 `app/config.py` 기본값
(`localhost:6333`, `localhost:27017`, `localhost:3306`, DB명 `lina_rag`)에 맞춰 인프라가 띄운다.

---

## 레포 구조

```
app/
  adapters/       데이터 공급원 어댑터 (JSON 픽스처 / Atlassian)
  ingestion/      청킹·임베딩·벡터 스토어
  query/          ACL · 히스토리 · 검색 · 재순위화 · 검증 · 포맷터
  schemas/        공통 Pydantic 모델 (PageObject, Chunk, RagState 등)
  api/            FastAPI 진입점 (`app.api.main:app`, 포트 8000 — POST /ml/query · /healthz · /metrics)
  pipeline/       LangGraph 그래프 조립 (Query 그래프 — `build_query_graph`)
  llm/            (예약 패키지) LLM 래퍼 자리 — 실 transport 는 query/*_transport.py·titler.py
  config.py       pydantic-settings 기반 환경 설정

(에이전트 4종 — query_routing/history_manager/answer_generation/answer_verification —
 은 `lina-ai-agents` 설치로 들어온다. in-repo 디렉터리 없음 — INTEGRATION.md §1)

docs/
  architecture.md          전체 아키텍처
  rag-pipeline-design.md   RAG 파이프라인 설계서
  chunking-strategy.md     청킹 전략
  api-spec.md              API 명세
  db-schema.md             Qdrant / MongoDB / MySQL 스키마
  conventions.md           코딩 컨벤션
  atlassian-api.md         Confluence API 명세
  history-manager-agent.md History Manager 통합
  adr/                     Architecture Decision Records
  ai/                      Claude Code 작업 플로우 · 진행 로그

examples/   데모 entrypoint
samples/    PoC용 Confluence/Datadog JSON 픽스처 + 첨부 파일
scripts/    포맷·린트·테스트·검증 스크립트 (.sh + .ps1)
tests/      pytest
```

---

## 개발 가이드

작업 전 반드시 다음을 확인한다.

- 최상위 [`CLAUDE.md`](CLAUDE.md) — 절대 규칙 및 작업 플로우
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/conventions.md`](docs/conventions.md)
- [`docs/ai/workflow.md`](docs/ai/workflow.md) — Claude Code 사용 시 플로우
- [`docs/ai/current-plan.md`](docs/ai/current-plan.md) — 현재 진행 중인 작업

작업 영역에 따라 추가로 다음 문서를 확인한다.

- RAG Pipeline: `docs/db-schema.md`
- API: `docs/api-spec.md`
- 청킹: `docs/chunking-strategy.md`

---

## 라이선스

내부 프로젝트. 외부 배포 금지.
