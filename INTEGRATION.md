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
"lina-ai-agents @ git+https://github.com/skala-final-project-team7/ai-agent.git@main",
```
app은 4개 에이전트를 **top-level 패키지명**으로 import한다(소스 30곳): `query_routing_agent`, `history_manager_agent`, `answer_generation_agent`, `answer_verification_agent`. ai-agent가 이 이름을 그대로 노출하면 import는 **무변경**으로 해결된다.

## 3. ⚠️ ai-agent 레포 선행 수정 (이게 안 되면 `pip install` 충돌)
`skala-final-project-team7/ai-agent`(현 main `f9f458c`) 패키징을 직접 조사한 결과, 현재 상태로는 충돌한다:

| # | 문제 | 현재 ai-agent | 필요한 수정 |
|---|---|---|---|
| 1 | **배포 이름 충돌** | 루트 pyproject `name = "lina-rag-pipeline"` (= rag-deploy 자신과 동일) | ai-agent `[project].name`을 `lina-ai-agents`로 변경 |
| 2 | **`app` 패키지 충돌** | `packages.find.include`에 `"app*"` 포함 | ai-agent에서 `app*` 제외 → 6개 에이전트 패키지만 노출 |
| 3 | **핀 가변** | release tag 없음 (이 의존성은 `@main`) | 위 수정 후 tag 발행 → 여기 `@main`을 `@<tag>`로 교체 |

> 1·2를 ai-agent에서 끝내야 rag-deploy가 `lina-ai-agents`를 self-collision/`app` 충돌 없이 설치한다.

## 4. 빌드/검증 (Python 3.11)
```bash
pip install -e '.[embedding]'      # ai-agent 의존성 포함 (§3 선행 필요)
python -c "import app.api.main"    # 에이전트 import 해결 = ai-agent 설치 확인
./scripts/verify.sh                # format → lint → test
```

## 5. 참고 문서 (원본 워크스페이스)
- `HANDOFF-ML-2026-06-09.md` — 인프라 seam(RabbitMQ/Qdrant/Mongo/MySQL)·운영 기본값 전환 §4·§5
- `SHARED-SURFACE-2026-06-09.md` — `lina-shared` 공유표면 추출(별개 작업, 미포함)
- `docs/api-spec.md` (v2.5.0) — 정본 계약
