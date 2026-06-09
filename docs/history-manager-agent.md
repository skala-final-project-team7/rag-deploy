# History Manager Agent

공통 규칙은 루트 `AGENTS.md`와 `ai-agent/AGENTS.md`를 따른다. 이 문서는 History Manager Agent 고유 개발 명세만 정의한다.

---

## Agent 목표

사용자의 현재 질문이 이전 대화의 후속 질문인지, 새로운 주제인지, 또는 판단이 애매한 질문인지를 분류하고, 후속 RAG 단계가 사용할 대화 컨텍스트를 정리한다.

History Manager Agent는 RAG 파이프라인의 Entry Agent다. 출력은 Query Routing Agent가 바로 소비할 수 있어야 한다.

초기 MVP는 **BFF가 전달한 conversation history JSON을 입력으로 받아 history decision과 contextualized question을 생성하는 CLI 기반 workflow**를 구현한다.

---

## MVP 범위

포함:

- CLI 수동 실행
- LangGraph workflow
- local JSON input/output
- BFF가 전달한 conversation history 입력 처리
- OpenAI API 직접 호출 provider 구현
- `OPENAI_API_KEY` 외부 주입
- 테스트용 fake LLM provider
- 최근 N개 turn 기반 context trimming
- follow-up / new-topic / ambiguous 분류
- preserved context 생성
- reset policy 생성
- contextualized question 생성
- Query Routing Agent 입력 호환 output 생성
- fixture 기반 테스트
- token/API key 비노출 safety test

제외:

- BFF API 직접 호출
- DB 직접 조회
- 대화 이력 DB 저장/갱신
- 사용자 인증/인가 처리
- RAG 검색 실행
- Query Routing Agent 구현
- Answer Generation Agent 구현
- Answer Verification Agent 구현
- SSE streaming
- production prompt tuning 자동화

후속 확장:

- BFF adapter
- conversation DB repository adapter
- Redis/Valkey cache adapter
- label taxonomy 확장
- long-term memory summary
- per-tenant prompt/policy 설정
- model routing
- live evaluation set 기반 prompt regression test

---

## 책임 범위

책임진다:

- conversation history schema 정의
- 현재 질문과 이전 대화의 관계 판단
- `follow_up`, `new_topic`, `ambiguous` label 분류
- 판단 confidence와 reason 생성
- context preservation/reset policy 생성
- 최근 N개 turn과 최대 문자 수 기반 context trimming
- 핵심 entity/context summary 생성
- contextualized question 생성
- Query Routing Agent용 output schema 생성
- OpenAI LLM provider adapter
- fake LLM provider
- LangGraph workflow와 CLI
- local JSON output
- fixture/safety tests

책임지지 않는다:

- BFF 대화 이력 저장소 구현
- DB schema 확정
- 사용자 인증 token 검증
- ACL filtering
- Vector DB 검색
- 답변 생성
- 답변 검증
- UI warning/formatting

---

## 실행 모델

MVP는 CLI 기반 단일 history decision job이다.

```bash
python ai-agent/history-manager-agent/scripts/run_history_manager.py \
  --input ai-agent/history-manager-agent/tests/fixtures/follow_up_input.json \
  --output ai-agent/history-manager-agent/data/output/history_decision.json
```

실행 전 `OPENAI_API_KEY`는 로컬 shell environment, 로컬 `.env`, 또는 런타임 secret provider로 주입한다.

OpenAI API key 처리 방식:

- 개발자는 로컬에서 `.env` 또는 shell environment로 `OPENAI_API_KEY`를 주입할 수 있다.
- 실제 `.env` 파일은 생성하거나 커밋하지 않는다.
- 필요한 경우 `.env.example`만 생성하며 실제 key 값은 포함하지 않는다.
- CLI 인자로 raw API key를 직접 받는 방식은 shell history/process 노출 위험이 있으므로 기본 방식으로 사용하지 않는다.
- code, fixture, log, output, docs 예시에 실제 API key를 저장하지 않는다.

MVP 기본값:

```json
{
  "history_window_turns": 5,
  "max_context_chars": 4000,
  "default_model": "configurable",
  "temperature": 0.0,
  "timeout_seconds": 30,
  "max_retries": 2
}
```

모델명은 config/env로 교체 가능해야 한다. 기획서상 GPT 계열 모델을 사용하되, 코드에는 모델 교체가 어렵게 고정하지 않는다.

---

## 외부 연동 계약

### BFF Input Contract

MVP에서는 BFF가 아래 형태의 JSON을 Agent에 전달한다고 가정한다.

```json
{
  "conversation_id": "string",
  "user_id": "string",
  "current_question": "그럼 롤백 절차는?",
  "history": [
    {
      "turn_id": "turn-001",
      "role": "user",
      "content": "IAM 정책 수정 중 장애가 났을 때 어떻게 대응해?",
      "created_at": "ISO-8601"
    },
    {
      "turn_id": "turn-002",
      "role": "assistant",
      "content": "IAM 정책 수정 장애 대응은 영향 범위 확인 후...",
      "created_at": "ISO-8601",
      "citations": []
    }
  ],
  "metadata": {
    "locale": "ko-KR",
    "timezone": "Asia/Seoul"
  }
}
```

규칙:

- `history`는 오래된 turn에서 최신 turn 순서로 정렬된 입력을 권장한다.
- 입력 순서가 깨져 있으면 `created_at` 기준 정렬을 시도한다.
- `system` role은 MVP에서 보존하되 LLM 판단 입력에는 제한적으로만 사용한다.
- 빈 history는 `new_topic`으로 처리한다.
- malformed turn은 failed item 또는 warning으로 기록하고 가능한 범위에서 처리한다.

### LLM Provider Contract

MVP에서 실제 OpenAI API 호출 provider를 구현한다. 단, 테스트는 fake provider를 기본으로 사용한다.

```text
HistoryLLMProvider
  -> classify_history(input) -> HistoryClassification
  -> contextualize_question(input) -> ContextualizedQuestion
```

구현 원칙:

- provider interface와 OpenAI provider 구현을 분리한다.
- API key는 환경변수 또는 secret provider에서만 읽는다.
- OpenAI client error는 safe error로 변환한다.
- prompt, request, response logging에서 API key와 Authorization header를 제거한다.
- live OpenAI 호출 테스트는 기본 test suite에 포함하지 않는다.
- 실제 API smoke test가 필요하면 별도 opt-in flag 또는 별도 script로 분리한다.

---

## History 판단 기준

MVP label:

| Label | 의미 | 처리 |
| --- | --- | --- |
| `follow_up` | 현재 질문이 직전 대화 또는 최근 맥락을 전제로 함 | context 보존, contextualized question 생성 |
| `new_topic` | 현재 질문이 새로운 주제임 | 이전 context reset, 원문 질문 유지 |
| `ambiguous` | 후속/신규 판단이 불명확함 | 보수적으로 최소 context만 보존하고 confidence 낮게 표시 |

확장 원칙:

- label enum은 추후 확장 가능해야 한다.
- 새로운 label 추가 시 Query Routing Agent 입력 계약을 깨지 않도록 unknown-safe 처리한다.
- confidence와 reason은 항상 포함한다.

---

## Context Policy

MVP 기본 정책:

| 조건 | preserved_context | reset_required | contextualized_question |
| --- | --- | --- | --- |
| `follow_up` | 최근 5 turn + 핵심 context summary | false | 이전 맥락을 반영해 독립 질문으로 재작성 |
| `new_topic` | 비움 또는 최소 current question만 유지 | true | current question 원문 유지 |
| `ambiguous` | 최근 1~2 turn의 최소 context | false | 과도한 추론 없이 보수적으로 재작성 또는 원문 유지 |

Context trimming:

- 기본 최근 `5` turns만 사용한다.
- `max_context_chars`를 초과하면 오래된 turn부터 제거한다.
- 중요한 entity가 summary에 포함되도록 한다.
- 원본 history 전체를 output에 그대로 복제하지 않는다.

---

## Workflow

```text
load_config
  -> load_input
  -> normalize_history
  -> trim_history
  -> classify_history
  -> apply_context_policy
  -> build_contextualized_question
  -> build_routing_input
  -> write_output
  -> write_report
```

핵심 규칙:

- history normalization과 trimming은 LLM 호출 전 deterministic 처리로 수행한다.
- LLM provider 장애 시 safe failure를 반환하고 raw exception에 secret을 포함하지 않는다.
- `new_topic`이면 이전 대화 맥락을 Query Routing Agent로 넘기지 않는다.
- `follow_up`이면 Query Routing Agent가 검색에 사용할 수 있는 독립 질문을 생성한다.
- `ambiguous`이면 confidence를 낮게 표시하고 downstream에서 보수적으로 처리할 수 있게 한다.

---

## Canonical Schema

### Conversation Turn

```json
{
  "turn_id": "string",
  "role": "user|assistant|system",
  "content": "string",
  "created_at": "ISO-8601",
  "citations": [],
  "metadata": {}
}
```

### History Manager Input

```json
{
  "conversation_id": "string",
  "user_id": "string",
  "current_question": "string",
  "history": [],
  "metadata": {
    "locale": "ko-KR",
    "timezone": "Asia/Seoul"
  }
}
```

### History Decision

```json
{
  "conversation_id": "string",
  "user_id": "string",
  "original_question": "string",
  "contextualized_question": "string",
  "history_decision": "follow_up|new_topic|ambiguous",
  "reset_required": false,
  "confidence": 0.0,
  "reason": "string",
  "preserved_context": {
    "summary": "string",
    "entities": [],
    "turn_refs": []
  },
  "warnings": []
}
```

### Query Routing Input

History Manager output은 Query Routing Agent의 입력으로 그대로 사용할 수 있어야 한다.

```json
{
  "conversation_id": "string",
  "user_id": "string",
  "original_question": "string",
  "query": "string",
  "history_decision": "follow_up|new_topic|ambiguous",
  "preserved_context": {
    "summary": "string",
    "entities": [],
    "turn_refs": []
  },
  "reset_required": false,
  "metadata": {}
}
```

### History Report

```json
{
  "job_id": "string",
  "conversation_id": "string",
  "status": "success|partial_success|failed",
  "decision": "follow_up|new_topic|ambiguous",
  "input_turn_count": 0,
  "used_turn_count": 0,
  "warnings_count": 0,
  "created_at": "ISO-8601"
}
```

---

## Error Handling

| 상황 | 처리 |
| --- | --- |
| input JSON 없음 | failed, non-retryable |
| malformed JSON | failed, non-retryable |
| current_question 없음 | failed, non-retryable |
| 빈 history | `new_topic` |
| malformed turn | warning 기록 후 가능한 turn만 사용 |
| history_window 초과 | trimming |
| max_context_chars 초과 | 오래된 turn부터 trimming |
| OpenAI API key 없음 | provider configuration error |
| OpenAI timeout/5xx | retryable safe error |
| OpenAI auth error | non-retryable auth failure |
| LLM output schema invalid | fallback 또는 failed item |

기본 권장값:

```json
{
  "history_window_turns": 5,
  "max_context_chars": 4000,
  "max_retries": 2,
  "timeout_seconds": 30,
  "temperature": 0.0
}
```

---

## 권장 구현 구조

```text
ai-agent/history-manager-agent/
  history-manager-agent.md
  src/history_manager_agent/
    app/
    graph/
    history/
    llm/
    storage/
    schemas/
    config/
    utils/
  tests/
    fixtures/
    unit/
    integration/
  data/
    input/
    output/
    reports/
    failed/
  scripts/
```

---

## Feature Breakdown

### feature1_project_skeleton_and_schema

- package 구조 생성
- `pyproject.toml` 설정
- config schema 정의
- conversation turn/input/output/report schema 정의
- Query Routing Agent 입력 호환 schema 정의
- CLI skeleton 작성
- schema/config 단위 테스트 작성

### feature2_history_input_normalization

- input JSON loader 구현
- conversation turn normalization 구현
- role validation 구현
- created_at 기반 정렬 구현
- empty history 처리
- malformed turn warning 처리
- 최근 N개 turn trimming 구현
- max_context_chars trimming 구현
- normalization/trimming 테스트 작성

### feature3_llm_provider_and_classification

- LLM provider interface 정의
- OpenAI provider 구현
- fake LLM provider 구현
- follow_up/new_topic/ambiguous classification 구현
- confidence/reason parsing 구현
- LLM output schema validation 구현
- retry/safe error 처리 구현
- provider/classification 테스트 작성

### feature4_context_policy

- decision별 context preservation policy 구현
- follow_up context summary 생성
- new_topic reset policy 구현
- ambiguous conservative policy 구현
- preserved_context turn_refs/entities 구조 생성
- context policy 테스트 작성

### feature5_contextualized_question

- follow_up 질문 재작성 구현
- new_topic 원문 유지 구현
- ambiguous fallback 구현
- contextualized question validation 구현
- Query Routing Agent input builder 구현
- contextualized question 테스트 작성

### feature6_langgraph_workflow_and_cli

- LangGraph workflow 구성
- sequential fallback 구성
- CLI 실행 스크립트 구현
- local output 저장
- report 저장
- fake provider 기반 workflow integration test 작성

### feature7_fixture_and_safety_tests

- synthetic follow-up fixture 작성
- synthetic new-topic fixture 작성
- synthetic ambiguous fixture 작성
- long history trimming fixture 작성
- malformed history fixture 작성
- OpenAI API key/token safety 테스트
- output schema 검증
- boundary test 작성

---

## 수용 기준

- CLI로 History Manager workflow를 실행할 수 있다.
- BFF가 전달한 conversation history JSON을 입력으로 처리할 수 있다.
- OpenAI API key는 외부 주입으로만 사용한다.
- API key가 코드, fixture, log, output file에 저장되지 않는다.
- 테스트는 기본적으로 fake LLM provider를 사용한다.
- 실제 OpenAI provider 구현은 provider interface 뒤에 분리되어 있다.
- 빈 history는 `new_topic`으로 처리된다.
- 후속 질문은 `follow_up`으로 분류되고 context를 보존한다.
- 새 주제 질문은 `new_topic`으로 분류되고 reset_required가 true가 된다.
- 애매한 질문은 `ambiguous`로 분류될 수 있으며 confidence가 낮게 표현된다.
- 최근 N개 turn과 max_context_chars 기준 trimming이 동작한다.
- contextualized question이 생성된다.
- Query Routing Agent가 바로 소비 가능한 output schema가 생성된다.
- LangGraph workflow가 전체 단계를 orchestration한다.
- fixture 기반 integration test가 통과한다.

---

## 후속 개발 메모

- BFF 연동이 확정되면 `storage/` 또는 `adapters/` 하위에 BFF conversation history client를 추가한다.
- DB 직접 조회 구조가 확정되면 repository interface를 유지한 채 MongoDB/MySQL adapter를 추가한다.
- label taxonomy 확장 시 Query Routing Agent 입력 schema와 backward compatibility를 먼저 확인한다.
- model routing이 필요하면 OpenAI provider 내부가 아니라 provider factory/config 계층에서 처리한다.
- live OpenAI smoke test는 기본 CI에서 제외하고 명시적 opt-in으로만 실행한다.
