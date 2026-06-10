# INTEGRATION — rag-deploy (인프라 통합 가이드)

이 레포는 `rag`에서 **vendored 에이전트 4종을 발라낸** 배포 전용 버전이다. 에이전트는 별도 `ai-agent` 레포에서 설치된다. (원본: `skala-final-project-team7/rag`)

## 1. 무엇이 바뀌었나
- **제거**(이 레포에 없음): `query_routing_agent/`, `history_manager_agent/`, `answer_generation_agent/`, `answer_verification_agent/` (+ `tests/history_manager_agent/`)
- **pyproject**: vendoring 노출/제외 설정(`packages.find`의 에이전트, ruff/mypy 제외·override) 제거 → `[project].dependencies`에 ai-agent 의존성 1줄 추가
- **app/ 코드·import·테스트: 무변경** — `from query_routing_agent ...`처럼 top-level import라 공급원(in-repo 사본 → 설치된 ai-agent)만 바뀐다
- **제외**: `Dockerfile`·`.dockerignore`·`docker-compose.yml`·`.env.example`은 의도적으로 미포함(인프라가 별도 관리)

## 2. 에이전트 의존성
```toml
# pyproject.toml [project].dependencies
"lina-ai-agents @ git+https://github.com/skala-final-project-team7/ai-agent.git@v0.1.0",
```
app은 4개 에이전트를 **top-level 패키지명**으로 import한다(소스 30곳): `query_routing_agent`, `history_manager_agent`, `answer_generation_agent`, `answer_verification_agent`. ai-agent가 이 이름을 그대로 노출하면 import는 **무변경**으로 해결된다.

## 3. ✅ ai-agent 레포 선행 수정 — 해소됨 (2026-06-10)
`skala-final-project-team7/ai-agent` 커밋 `54055df` · 태그 **`v0.1.0`** 에서 아래 3건이 모두 반영됐다
(태그 시점 pyproject 직접 확인 — name/`packages.find` 검증 완료):

| # | 문제(당시 main `f9f458c`) | 적용된 수정 (v0.1.0) |
|---|---|---|
| 1 | 배포 이름 충돌 — `name = "lina-rag-pipeline"` (= rag-deploy 와 동일) | `[project].name = "lina-ai-agents"` ✅ |
| 2 | `app` 패키지 충돌 — `packages.find.include`에 `"app*"` 포함 | `app*` 제거, 6개 에이전트 패키지만 노출 ✅ |
| 3 | 핀 가변 — release tag 없음(`@main`) | 태그 `v0.1.0` 발행, 본 레포 의존성 핀 `@v0.1.0` 으로 교체 완료 ✅ |

> top-level 에이전트 패키지명 6종은 무변경 — app 코드의 `from query_routing_agent ...` import 그대로 동작.

## 4. 빌드/검증 (Python 3.11)
```bash
pip install -e '.[embedding,ingestion,dev]'   # ai-agent v0.1.0 + 청커 파서(fitz 등) + ruff/pytest
python -c "import app.api.main"    # 에이전트 import 해결 = ai-agent 설치 확인
./scripts/verify.sh                # format → lint → test
```

> `embedding`만으로는 부팅 불가 — `app.api.main` import 체인이 청커(`fitz`/`openpyxl`/
> `python-docx`)를 모듈 레벨에서 로드한다(`ingestion` extra). `verify.sh` 는 ruff·pytest
> (`dev` extra)를 요구한다.

## 5. 참고 문서 (원본 워크스페이스)
- `HANDOFF-ML-2026-06-09.md` — 인프라 seam(RabbitMQ/Qdrant/Mongo/MySQL)·운영 기본값 전환 §4·§5
- `SHARED-SURFACE-2026-06-09.md` — `lina-shared` 공유표면 추출(별개 작업, 미포함)
- `docs/api-spec.md` (v2.6.0) — 정본 계약
