# SSE 이벤트 계약 — 프론트엔드 핸드오프

`POST /ml/query` 의 SSE 응답 계약을 정리한 문서다(BFF → ML). BFF 는 이 응답을 FE 의
`POST /api/conversations/{conversationId}/chat` 으로 그대로 중계하며 `done` 에 `messageId` 를
주입한다(api-spec v2.2.0 §1-1/§2-1). 근거 코드: `app/api/routes.py`, `app/schemas/response.py`,
`app/schemas/enums.py`, `app/query/formatter.py`, `app/api/errors.py`. 정본 계약은
`docs/api-spec.md`(현행 **v2.5.0**) — SSE 7종 이벤트 계약은 v2.2.0 이후 불변이다.

---

## 1. 엔드포인트

- **Method / Path**: `POST /ml/query`
- **응답 Content-Type**: `text/event-stream` (SSE)
- **인증**: ML 은 JWT 를 직접 검증하지 않는다. BFF 가 JWT 에서 추출한 `userId`/`groups` 를
  본문으로 전달한다(2단계 데모는 고정값).

### Request Body

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `question` | string | Y | — | 사용자 자연어 질문 (최소 1자) |
| `userId` | string | Y | — | ACL Pre-filtering 사용자 식별자 |
| `groups` | string[] | Y | `[]` | 사용자 그룹 — ACL `should`-OR 필터 |
| `conversationId` | string | N | null | 멀티턴 대화 컨텍스트 ID |
| `history` | array | N | `[]` | 이전 대화 이력 `[{ "role": "user"\|"assistant", "content": "..." }]` (BFF가 DB에서 조회) |
| `stream` | boolean | N | `false` | true면 토큰 단위 스트리밍, false면 단일 `token` 1회 송신. **BFF는 항상 true로 호출**(api-spec v2.4.0 §2-1). PoC 환경(OpenAI 키 없음)에서는 true여도 자동으로 비스트리밍 fallback |

> `accessToken`/`cloudId` 는 `/ml/query` 에 전달하지 않는다(v2.2.0 — 질의 경로는 라이브
> Confluence 호출이 없어 토큰 불필요, 토큰은 수집 `/ml/ingest` 단계에서만 사용).

---

## 2. SSE 이벤트 순서 (핵심 5종 + 진행 `status`)

핵심 5종은 항상 아래 순서로 전송된다.

| # | event | 횟수 | data 형식 | 비고 |
|---|---|---|---|---|
| 1 | `token` | n회 | **JSON 객체** `{"content": "..."}` | 비스트리밍: 1회(전체 답변) / 스트리밍: 토큰 단위 다회 |
| 2 | `sources` | 1회 | **JSON 객체** `{"sources": [...]}` | `JSON.parse` 필요 |
| 3 | `verification` | 1회 | **JSON 객체** (집계) | `JSON.parse` 필요 |
| 4 | `meta` | 1회 | **JSON 객체** | `JSON.parse` 필요 (현재 구현 호환용, 추후 제거 예정) |
| 5 | `done` | 1회 | **JSON 객체** `{}` | 종료 마커 (BFF가 `messageId` 주입) |

> 모든 이벤트의 `data` 는 JSON 문자열이다(`token` 포함). 프론트는 각 `data` 를 `JSON.parse`
> 한 뒤 사용한다. `token` 은 `parsed.content` 를 누적 렌더링한다.

추가로, **스트리밍 모드(`stream=true`)** 에서는 진행 표시용 `status` 이벤트(feature19)가
위 핵심 이벤트들 사이사이에 끼어 들어온다. `status` 는 *추가 전용* 이벤트라, 이를 무시하는
클라이언트는 핵심 5종만으로 기존과 동일하게 동작한다. PoC 환경(OpenAI 키 없음)에서는
`stream=true` 여도 비스트리밍으로 fallback 되므로 `status` 가 나오지 않는다(아래 3.6 / 8번 참고).

---

## 3. 각 이벤트 페이로드

### 3.1 `token`

답변 텍스트 청크. `parsed.content` 를 누적해서 화면에 렌더링한다.

```
event: token
data: {"content": "장애 발생 시 먼저 #infra-alert 채널을 확인하세요. [#1]"}
```

- 답변 문장에는 `[#n]` 형식의 근거 청크 번호가 포함될 수 있다.
- **빈 content (`{"content": ""}`) = 버퍼 클리어 신호** (스트리밍 모드 한정). 아래 6번 참고.

### 3.2 `sources` — 출처 카드 배열

```jsonc
{
  "sources": [
    {
      "title": "S3 트러블슈팅 가이드",
      "pageId": "12345",
      "spaceId": "98310",
      "spaceName": "Cloud Control Center",
      "url": "https://confluence.../pages/12345",
      "sourceUpdatedAt": "2026-04-15T18:30:00+09:00",  // KST(+09:00)
      "relevanceScore": 0.92                            // 0~1 (Cross-Encoder score/100)
    }
  ]
}
```

- 배열은 `{"sources": [...]}` 로 래핑된다.
- 첨부 출처 전용 필드(`attachment_*`/`download_url`)는 현재 BE `sources` 스키마에 미정의 —
  첨부 검색 노출 시 BE와 필드 확정 필요(`docs/api-spec.md` TBD).

### 3.3 `verification` — 집계 검증 결과

```jsonc
{
  "confidenceScore": 0.85,             // 0~1 (PASS+SUPPORTED 문장 / 전체 문장)
  "verificationResult": "SUPPORTED"    // "SUPPORTED" | "PARTIALLY_SUPPORTED" | "NOT_SUPPORTED"
}
```

- 문장별 결과를 단일 값으로 집계한다(집계 규칙은 `docs/api-spec.md` "verification" 절).
- `confidenceScore` 가 낮으면 FE 가 저신뢰 경고 배지를 표시한다.

### 3.4 `meta` (현재 구현 호환용, 추후 제거 예정)

```jsonc
{
  "intent": "장애대응",                // 4종 (아래 enum 참고)
  "used_llm": "gpt-4o",                // "gpt-4o" | "gpt-4o-mini"
  "feedback_enabled": true,            // false면 저신뢰 응답 (경고 배지 권장)
  "latency_ms": 4120
}
```

- api-spec v2.2.0 §1-1 정합. `title`(LLM 생성 대화 제목)은 스펙상 optional 이며 **본 ML 구현은
  생성하지 않아 송신하지 않는다.** FE 는 `title` 이 없으면 대화 제목을 갱신하지 않는다.
- 추후 BE 통합 목표 계약에서 제거될 수 있다(그때 값은 ML 내부 메트릭 / `confidenceScore` 로 대체).

### 3.5 `done`

```
event: done
data: {}
```

- ML 은 `done` 을 빈 객체 `{}` 로 emit 한다. **`messageId` 는 BFF 가 DB 메시지 UUID 로 주입**해
  FE 에 `{"messageId": "msg-uuid-001"}` 형태로 중계한다. 스트림 종료, 이후 추가 이벤트 없음.

### 3.6 `status` — 진행 표시 (스트리밍 모드 한정)

답변 토큰 전/중에 RAG 라이프사이클 단계 진입을 알리는 진행 표시용 이벤트다. 핵심 5종과
별개로 *추가* 송신되며, `data` 는 `{"phase": "...", "message": "..."}` JSON 문자열이다
(`JSON.parse` 필요).

```
event: status
data: {"phase": "searching", "message": "관련 문서를 검색하고 있어요"}
```

정상 흐름 순서: `connecting` → `acl_filtering` → `searching` → `answering` → `streaming` →
`verifying` → `formatting`. 검색 0건(`RETRIEVAL_EMPTY`) 분기는 `answering`/`streaming`/
`verifying` 를 건너뛰고 `searching` 다음 `formatting` 으로 직행한다. `done`/`error` 는
별도 `status` phase 로 만들지 않는다(기존 `done` 이벤트 + 7번 에러 처리로 표현). phase
목록·메시지·송신 시점의 정본은 `docs/api-spec.md` "진행 status 이벤트" 절이다.

> 그래프 내부 4단계(history/router/search/rerank)는 현재 단일 블로킹 호출이라 절충안으로
> `searching` 단일 phase 로 통합 송신된다.

---

## 4. Enum 값 (프론트 분기용)

| 대상 | 가능한 값 |
|---|---|
| `meta.intent` | `장애대응`, `운영가이드`, `정책절차`, `이력조회` |
| `meta.used_llm` | `gpt-4o`, `gpt-4o-mini` |
| `verification.verificationResult` | `SUPPORTED`, `PARTIALLY_SUPPORTED`, `NOT_SUPPORTED` |

---

## 5. 저신뢰 / 차단 분기 (200 SSE 내부에서 처리)

오류가 아니라 정상 200 SSE 안에서 처리된다. 프론트는 `meta.feedback_enabled` 와
답변 내용으로 판단한다.

| 상황 | 동작 | 프론트 처리 |
|---|---|---|
| 검색 결과 0건 | "권한 범위 내 문서를 찾지 못했습니다" 표준 답변, LLM 미호출 | 일반 답변처럼 렌더 |
| Cross-Encoder 최고 점수 < 55 | 저신뢰 분기, `feedback_enabled=false` | 출처를 '참고용' + 경고 배지 |
| `NOT_SUPPORTED` 비율 > 50% | 답변 차단, 안내문으로 대체, `feedback_enabled=false` | 차단 안내문 렌더, 출처 직접 확인 유도 |

차단 안내문 원문:
> "검증 결과 답변의 상당 부분이 출처로 뒷받침되지 않아 답변 제공을 보류합니다. 아래 참고 출처를 직접 확인해 주세요."

---

## 6. 스트리밍 모드(`stream=true`) 특수 동작 ⚠️

프론트가 반드시 처리해야 하는 두 케이스.

1. **빈 content token = 누적 버퍼 클리어**
   OpenAI Rate Limit 발생 시 fallback 모델로 재시도하며, 이미 보낸 부분 답변을
   덮어쓰도록 `{"content": ""}` (빈 content) token 을 1회 보낸다. 프론트는 빈 content token을
   받으면 지금까지 누적한 답변 텍스트를 비우고 이후 token부터 다시 누적해야 한다.

2. **차단 시 token 재전송(overwrite)**
   토큰 스트리밍이 끝난 뒤 답변이 차단 분기로 판정되면, 차단 안내문 전체를 담은
   `token` 이벤트가 1회 더 온다. 프론트는 이 token으로 기존 답변을 교체해야 한다.
   그 다음 `sources` / `verification` / `meta` / `done` 순으로 이어진다.

---

## 7. 에러 (SSE `error` 이벤트)

오류는 HTTP 에러가 아니라 **SSE `error` 이벤트**로 전달하고 스트림을 종료한다(api-spec §1-1/§2-1).

```
event: error
data: {"errorCode": "ML_SERVER_ERROR", "message": "답변 생성 중 오류가 발생했습니다"}
```

| errorCode | 상황 |
|---|---|
| `ML_SERVER_ERROR` | ML 서버 5xx · 내부 처리 오류(ACL 시스템 오류 포함) |
| `ML_TIMEOUT` | ML 응답 / 스트림 타임아웃 |
| `ML_CONNECTION_ERROR` | ML 연결 실패 / 스트림 중단 |

- payload 키는 단일 `errorCode` 다(RAG·BFF·FE passthrough — `code`→`errorCode` 매핑 없음).
- 요청 본문 검증 실패(필수 필드 누락 등)는 FastAPI 기본 422 로 응답한다(SSE 진입 전).
- `RETRIEVAL_EMPTY`, `LOW_CONFIDENCE`, `VERIFICATION_BLOCKED` 코드는 정의돼 있으나 현재
  구현에서는 **에러가 아니라 정상 200 SSE 내부 분기**로 처리된다(5번 표 참고).

---

## 8. 참고

- **진행 상태(progress) `status` 이벤트는 스트리밍 모드(`stream=true`)에서 구현돼 있다**
  (feature19, 위 3.6 참고). 다만 PoC 환경(OpenAI 키 없음)에서는 `stream=true` 여도
  비스트리밍으로 fallback 되어 `status` 가 송신되지 않는다 — 실 스트리밍(OpenAI 키 연동)
  경로에서만 "검색 중 / 생성 중" 등의 단계가 push 된다.
- 상태성 정보는 `verification.confidenceScore`/`verificationResult`, `meta.feedback_enabled`,
  진행 `status.phase`, 에러 `code` 로 나뉘어 있다.
