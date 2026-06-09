# LINA API Spec

> 버전: v2.5.0
> 기준: 중간 발표(4주차) 데모 범위 + 이후 확장 계획
> 전제: 중간 발표 시 인증 하드코딩, 스페이스 고정, 로그인 제외
> 기획서 버전: v2.1.7 (Authorization Server 분리, 사용자 단위 검색 반영)

---

## 변경 이력

> 상단 `버전` 은 본 API 명세 **문서 자체의 버전**이며, `기획서 버전`(v2.1.7)과 독립적으로 관리한다.

| 버전   | 일자       | 주요 변경                                                                                                                                                                                                                                                                                                                                                                              |
| ------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v2.2.0 | 2026-05-29 | **SSE 계약 전면 정리**: `status`/`meta` 포함 **7종 이벤트 정본화**, 이벤트 순서 불변식·스트림 종료/영속·0건 처리·`error`(`errorCode` + 코드 enum) 명문화, idle 기준 타임아웃·`status` keep-alive, SSE 응답 헤더(`text/event-stream` 등), 재연결(`Last-Event-ID`) 미지원. 챗 엔드포인트는 항상 스트리밍(`stream=false` 모드 제거). `meta.title` → 첫 응답 1회 자동 제목 설정 규칙. **채팅방 고정 `isPinned`**(목록 응답·`PATCH` 확장·고정 우선 정렬). **Enum 값 `UPPER_SNAKE` 정책** 확정 및 `role`/`rating` 대문자 정정. 에러 응답 봉투 4필드 고정(`ErrorResponse` 정합). 스페이스 식별자(`spaceKey`/`spaceId`/`spaceName`) 구분 명시. ACL 질의 필드 `userId` camelCase 통일. `feature13` 미정의 마커 서술형 교체. `## 변경 이력` 신설·상단 이동. 미리보기 쿼리 파라미터 `page_id`→`pageId` 정합. |
| v2.3.0 | 2026-05-29 | **§4(5~7주차) API 명세 작성** — 구현 전 명세 완성. 인증(`/api/auth/login`·`/api/auth/callback`·`/api/auth/refresh`·`/api/auth/logout`·`/api/users/me`)을 **FE-facing 계약**으로 작성: **`Authorization: Bearer` 세션 JWT**(로그인/갱신 응답으로 access+refresh 발급, HttpOnly 쿠키 미사용), Confluence OAuth 위임(기획서 §6.5), JWT 서명·access TTL·Refresh 저장은 `TBD(3단계)`. 관리자 대시보드: `GET /api/admin/feedback` 응답 신설(긍정/부정 비율·추이·부정 원문 QCA 매핑), `users` 에 접근 가능 스페이스/페이지/첨부 수 보강, `/api/admin/*` ADMIN 전용(미인증 401·일반 403), 공통 쿼리 파라미터(`period`/`from`/`to`/`page`/`size`, **제안**)(기획서 §6.7). `/api/admin/*` ADMIN 권한을 §1-4 수집 API 에도 명시. §3 호출 흐름 다이어그램을 §4(인증·관리자·미리보기)까지 포함해 진짜 '전체'로 확장. 대화 목록 응답에서 `messageCount` 제거(기획서·FE 실수요 근거 약함 — 필요 시 재도입). **§2-1 RAG 질의 입력 명세 정밀화**: 요청/응답 분리 표기, `Request Header` 표·필드 표(Required) 정형화, `stream`(기본 false, BFF는 항상 true) 필드 명시, `history[].role` 을 RAG 관용 **소문자**(`user`/`assistant`)로 매핑(Enum 정책 예외 추가, BFF boundary 변환), `groups`/`spaceKey` **fail-closed**, RAG `done: {}` → BFF `messageId` 채움(경계 가공). SSE `error` 이벤트는 RAG·BFF·FE 모두 `errorCode` 단일 키 동일 — passthrough(이전 "code→errorCode 매핑" 노트는 ML팀 spec의 generic placeholder를 잘못 읽은 것, 정정). 메시지 `role` 저장 표기를 `USER`/`ASSISTANT` → **`user`/`assistant`** (LLM/OpenAI 산업 표준)로 통일 — Enum 정책 예외 재분류, RAG boundary 매핑 제거. **admin-only ingestion 자격증명 모델 확정**: admin 도 동일 Confluence OAuth 로 로그인하고, ingestion 도 admin OAuth access_token + Atlassian Admin Key 헤더(`Atl-Confluence-With-Admin-Key: true`) 조합 사용. §1-4 에 `POST /api/admin/key/activate` (Admin Key 60분 활성화) 신설, §2-2 `/ml/ingest` `accessToken` 시맨틱을 "admin OAuth access_token" 으로 명확화. OAuth Bearer + Admin Key 헤더 동작은 3단계 구현 시 검증 게이트. **대화 검색 endpoint 신설**: `GET /api/conversations/search` (§1-2) — 본인 대화의 `messages.content` 본문 검색, 결과는 대화 단위로 묶고 매칭 메시지 샘플(최대 3개) + `matchCount` 동반. 하이라이트는 **plain `snippet` + `matchPositions: [[start, end]]`** (서버는 HTML 미생성, FE 렌더 책임 — XSS 안전성). `q` 검증: trim 후 길이 2~50 (미만/초과 시 `400 INVALID_SEARCH_QUERY`). Common `ErrorCode` enum 에 `INVALID_SEARCH_QUERY` 추가 (도메인 특화 코드 최초 사례 — 사용처 명확할 때만 허용 정책). **`/ml/query` `spaceKey` Required → Optional**: RAG 챗봇 UX 는 "사용자가 매번 스페이스를 고르지 않고, 질문만 던지면 알아서 권한 가능한 모든 콘텐츠에서 답변" 이 자연스러움. 따라서 `spaceKey` 는 누락 시 cross-space 검색(`userId`/`groups` ACL 만 적용), 지정 시 특정 스페이스로 좁힘. **ACL fail-closed 게이트에서 `spaceKey` 제거** — ACL 이 아닌 스코프 필드이므로 누락은 차단 사유가 아님. `userId`/`groups` 만 fail-closed. Common §spaceKey 정의에 질의(선택)/수집(필수) 차이 명시. `/ml/ingest`·`/api/admin/ingest` 의 `spaceKey` 는 admin 이 수집 대상 명시해야 하므로 **Required 유지**. 관련: ML 팀과 `/ml/query` body schema 변경 협의 필요 (2단계 demo 는 `lina.demo.fixed-space-key=CPC` 전달 유지 — 기존 색인 데이터와 정합). **2026-06-02 회의 결정 추가 반영**: (1) **`/api/admin/ingest` 가 내부적으로 key activate 묶음 처리** — admin "데이터 인제스천 파이프라인" 버튼 하나로 키 발급+수집 일괄 트리거(BFF 가 key 활성 미확인 시 자동 `POST /api/v2/admin-key` 호출 후 ingest). `/api/admin/key/activate` 는 수동/테스트용 endpoint 로 명시. (2) **Admin Key 말소 책임 = ML(Data Ingestion) 측** — ingestion 완료 직후 ML 이 Atlassian admin-key deactivate 호출, 60분 TTL 은 fallback. BE 는 deactivate 책임 없음 — ML 팀 협의 필요. (3) **`/api/auth/login?mode=admin` 쿼리 파라미터 도입** — FE "Continue with Confluence for Admin" 버튼이 `?mode=admin` 전달. callback 에서 `state` 에 보관된 mode 확인해 `users.role != ADMIN` 이면 `403 FORBIDDEN` 으로 차단(클라이언트 우회 방지 위해 BE 가 state 에 mode 직렬화). |
| v2.4.0 | 2026-06-04 | **`spaceKey` 전면 제거 — LINA API 표면에서 사용 안 함**. v2.3.0 에서 `/ml/query` 를 Optional 로 만들었던 결정의 연장: 실제 검토 결과 `/ml/ingest`·`/api/admin/ingest` 도 admin Key 로 admin 이 접근 가능한 **모든 스페이스를 일괄 크롤**하는 모델이라 spaceKey 가 불필요(2026-06-04 결정). 결과: (1) `/ml/query` Request Body 에서 `spaceKey` 필드 삭제. cross-space 검색이 유일한 모드(`userId`/`groups` ACL 적용). (2) `/ml/ingest` Request Body 에서 `spaceKey` 필드 삭제. ML 이 `accessToken`+`cloudId` 와 Admin Key 헤더로 접근 가능 스페이스 iterate. (3) `/api/admin/ingest` Request Body 가 `{ mode }` 로 축소(생략 시 `"full"`). 버튼 1회 = 전체 수집. (4) Common §스페이스 식별자 갱신 — `spaceKey` 항목 제거, `spaceId`/`spaceName` 만 유지(messages.sources 출처 표시용). spaceKey 는 Confluence URL/내부 식별자로만 존재한다는 노트 추가. (5) `groups`/`spaceKey` fail-closed 표현 정리 — `userId`/`groups` 만 fail-closed. **ML 팀 협의 필요**: `/ml/query`·`/ml/ingest` body schema 변경(`spaceKey` 제거). ML PoC 의 `allowed_groups = ["space:{key}"]` 합성 모델은 ADR 0001 §2.1 의 페이지-단위 권한 모델로 자연스럽게 마이그레이션됨. 2단계 demo 영향: `lina.demo.fixed-space-key` 설정 deprecation. **Admin Key deactivate 책임 ML → BE 이동 (2026-06-04)**: 회의(2026-06-02) 에서 ML 담당으로 결정됐던 admin-key deactivate 호출을 BE 로 이전 — 깔끔한 책임 분리. 구현: BFF 가 `/api/admin/ingest` 트리거 직후 Virtual Thread watcher 를 띄워 `/ml/ingest/status/{jobId}` 를 폴링하다가(`lina.admin.ingest-watch-interval-ms`, 기본 30s) `COMPLETED`/`FAILED` 감지 시 auth-server 내부 `POST /internal/admin/key/deactivate` 호출. ML 인터페이스는 변경 없음. BFF 재시작 시 watcher 손실 — 60분 TTL 이 fallback (영속 watcher 는 PoC 범위 밖). |
| v2.5.0 | 2026-06-05 | **Admin Key 말소 흐름을 BFF polling watcher 에서 RabbitMQ completion event 기반으로 대체**. `/api/admin/ingest` 는 RabbitMQ 기반 비동기 수집 플로우로 정의한다. BFF 는 요청 수신 시 auth-server 내부 API 로 Admin Key 를 activate 한 뒤 BFF 또는 Data Ingestion Pipeline 을 통해 ingest job 을 RabbitMQ 에 발행하고, completion event consumer 가 `COMPLETED`/`FAILED` 이벤트를 consume 하면 auth-server `POST /internal/admin/key/deactivate` 를 호출한다. Data Ingestion Worker 는 MQ payload 에서 credential 을 받지 않고, `adminUserId` 로 auth-server 내부 credential 조회 API 를 호출해 admin OAuth `accessToken` + `cloudId` 를 함께 얻는다. RabbitMQ payload 는 `jobId`, `adminUserId`, `mode`, `status`, timestamp, error 요약 등 작업 식별/상태 정보만 포함하며 `accessToken`/`refreshToken`/`cloudId` 같은 Confluence credential set 을 포함하지 않는다. deactivate 대상은 OAuth token 이 아니라 Atlassian Admin Key 활성 상태이며, `jobId` 기준 중복 completion event 에 대해 idempotent 하게 처리한다. BFF 재시작/consumer 장애는 RabbitMQ durable queue 의 completion event 재처리로 복구하고, Admin Key 60분 TTL 은 최종 fallback 으로 유지한다. deactivate 실패는 초안 기준 최대 5회 재시도 후 DLQ 이동, DLQ 는 원인 조치 뒤 동일 event 재발행 또는 운영자 수동 deactivate 로 복구한다. **§2-5 에 Data Ingestion Worker → auth-server 내부 credential 조회 API 계약**(`GET /internal/auth/admin-confluence-credential`)을 추가했다. |

---

## Common Response Wrapper

모든 API는 아래 Wrapper 구조를 따른다.

**성공**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "요청 성공 메시지",
  "data": { ... }
}
```

**에러**

```json
{
  "isSuccess": false,
  "code": 404,
  "errorCode": "RESOURCE_NOT_FOUND",
  "message": "해당 대화를 찾을 수 없습니다"
}
```

> 에러 응답은 `isSuccess` / `code` / `errorCode` / `message` **4필드 고정**이며 `data` 를 포함하지 않는다(성공 응답만 `data` 포함). 구현: `common` 모듈 `ErrorResponse`(`@JsonInclude(ALWAYS)`) / `ApiResponse`. `errorCode` 값은 `ErrorCode` enum 을 따른다(`INVALID_REQUEST` / `INVALID_SEARCH_QUERY` / `UNAUTHORIZED` / `FORBIDDEN` / `RESOURCE_NOT_FOUND` / `EXTERNAL_SERVICE_ERROR` / `INTERNAL_ERROR`). 도메인 특화 코드는 사용처가 명확할 때만 추가(예: `INVALID_SEARCH_QUERY` 는 `GET /api/conversations/search` 의 `q` 검증 전용).

> **예외**: SSE 스트리밍 응답(`/api/conversations/{id}/chat`)은 Wrapper 미적용, 이벤트 스트림으로 전달.

**시간 표기 정책 (2026-05-21 확정)**

- 저장은 UTC(`Instant`)로 통일하고, **응답 JSON 의 모든 timestamp 는 KST(`+09:00`) 로 절대 전환해 반환한다.**
- 직렬화 예: `Instant` → `ZonedDateTime kst = instant.atZone(ZoneId.of("Asia/Seoul"))` → `2026-05-06T19:00:00+09:00`
- 본 문서의 모든 응답 예시는 KST 표기로 작성한다.

**Enum 값 표기 정책 (2026-05-29 확정)**

- 도메인/저장 enum 값은 기본적으로 `UPPER_SNAKE_CASE` 로 표기하며 `docs/db-schema.md` 저장 값과 일치시킨다 — `rating`(`LIKE`/`DISLIKE`), `verificationResult`(`SUPPORTED`/`PARTIALLY_SUPPORTED`/`NOT_SUPPORTED`), 수집·동기화 `status`(`STARTED`/`IN_PROGRESS`/…), 사용자 `role`(`USER`/`ADMIN`).
- 예외(`lower`/`lower_snake`): 메시지 `role`(`user`/`assistant` — LLM/OpenAI 산업 표준, **저장·와이어 동일** — boundary 변환 없음), 수집 `mode`(`full`/`delta`), SSE `status.phase`(`acl_filtering` 등). 이들은 관용·외부 표준 표기를 그대로 따른다.
- 필드 *이름* 표기(camelCase)와는 별개 규칙이다 — 이름은 camelCase(`verificationResult`), 값은 UPPER(`SUPPORTED`).

**스페이스 식별자**

Confluence 페이지 출처 표시에 사용하는 스페이스 속성은 두 가지다 — 혼용 금지.

- `spaceId` (예 `"98310"`): 숫자 **내부 ID**. 출처 메타데이터(`messages.sources[].spaceId`)에 저장.
- `spaceName` (예 `"Cloud Control Center"`): **표시용 이름**(변경 가능, 고유성 보장 안 됨). 출처 카드 화면 라벨(`messages.sources[].spaceName`).

> **`spaceKey` 는 LINA API 표면에서 사용하지 않는다 (2026-06-04 결정)**. 질의(`/ml/query`)는 ACL(`userId`/`groups`)만으로 cross-space 검색하며, 수집(`/ml/ingest`·`/api/admin/ingest`)은 admin Key 로 접근 가능한 모든 스페이스를 일괄 크롤한다. spaceKey 는 Confluence 내부 URL/식별자로만 존재 — Confluence REST API 호출 시 ML 측이 필요하면 spaceId 로부터 조회.

**공통 Request Header**
| Name | Type | Description | Required |
|------|------|-------------|----------|
| Content-Type | String | application/json | ✅ |
| Authorization | String | Bearer {JWT 토큰} | ✅ (3단계 이후) |

> ※ **2단계(중간 발표) 데모 범위에서는 인증이 비활성화**되어 있어 `Authorization` 헤더를 사용하지 않는다 (`DemoSecurityConfig`의 `permitAll` + 고정 데모 사용자 `lina.demo.fixed-user-id`). 3단계(Authorization Server) 도입 이후부터 JWT 발급/검증이 활성화되며 본 헤더가 Required가 된다. 상세는 `backend/bff-server/current-plans.md` §2단계 인증 부재 처리 방침 참조.

---

# 1. 외부 API (Frontend → BFF)

## 1-1. 챗봇 질의 (핵심) — SSE 스트리밍

| 항목   | 내용                                                        |
| ------ | ----------------------------------------------------------- |
| Method | `POST`                                                      |
| URL    | `/api/conversations/{conversationId}/chat`                  |
| 설명   | 사용자 질문을 ML 서버로 전달하고 SSE 스트리밍으로 응답 중계 |

**Request Body**

```json
{ "question": "지난번 S3 버킷 권한 오류 때 어떻게 해결했어?" }
```

**Response (SSE 이벤트 스트림)** — Wrapper 미적용

```
event: status
data: {"phase":"connecting","message":"연결 중이에요"}

event: status
data: {"phase":"acl_filtering","message":"접근 권한을 확인하고 있어요"}

event: status
data: {"phase":"searching","message":"관련 문서를 검색하고 있어요"}

event: status
data: {"phase":"answering","message":"답변을 준비하고 있어요"}

event: status
data: {"phase":"streaming","message":"답변을 작성하고 있어요"}

event: token
data: {"content": "S3 권한 오류는"}

event: token
data: {"content": " IAM 정책을 수정하여"}

event: status
data: {"phase":"verifying","message":"답변 근거를 검증하고 있어요"}

event: status
data: {"phase":"formatting","message":"답변을 정리하고 있어요"}

event: sources
data: {
  "sources": [
    {
      "title": "S3 트러블슈팅 가이드",
      "pageId": "12345",
      "spaceId": "98310",
      "spaceName": "Cloud Control Center",
      "url": "https://confluence.example.com/pages/12345",
      "sourceUpdatedAt": "2026-04-15T18:30:00+09:00",
      "relevanceScore": 0.92
    }
  ]
}

event: verification
data: {
  "confidenceScore": 0.85,
  "verificationResult": "SUPPORTED"
}

event: meta
data: {"intent":"운영가이드","used_llm":"gpt-4o","feedback_enabled":true,"latency_ms":1234,"title":"S3 권한 오류 해결 방법"}

event: done
data: {"messageId": "msg-uuid-001"}

event: error
data: {"errorCode": "ML_SERVER_ERROR", "message": "답변 생성 중 오류가 발생했습니다"}
```

**이벤트 타입**

> 아래 7종이 SSE 이벤트 **정본**이다. 내부 중계(§2-1)와 `backend/rules/rag-pipeline.md` §4 도 동일 집합을 따른다.

- `status` — RAG 파이프라인 진행 상태 메시지 (`message`는 프론트 표시 문구로 그대로 사용)
- `token` — 답변 청크 (스트리밍)
- `sources` — RAG 참조 문서 목록
- `verification` — 답변 신뢰도 검증 결과 (`SUPPORTED` / `PARTIALLY_SUPPORTED` / `NOT_SUPPORTED`)
- `meta` — 현재 ML 구현 호환용 응답 메타데이터. 답변 본문이 아니며 `done` 직전 1회 송신된다. BE 통합 목표 계약에서는 제거 예정이며, `intent` / `used_llm` / `latency_ms`는 ML 내부 메트릭으로 관측하고 저신뢰 신호는 `verification.confidenceScore`로 표현한다.
- `done` — 스트림 정상 종료, `messageId` 반환
- `error` — 스트림 오류 종료. `errorCode` / `message` 전달 (코드는 아래 "`error` 이벤트 / 에러 코드" 표)

**status 이벤트 phase**

| 순서 | phase           | 설명                                                  |
| ---- | --------------- | ----------------------------------------------------- |
| 1    | `connecting`    | 스트림 연결 중                                        |
| 2    | `acl_filtering` | 접근 권한 확인 중                                     |
| 3    | `searching`     | 관련 문서 검색 중                                     |
| 4    | `answering`     | 답변 준비 중                                          |
| 5    | `streaming`     | 답변 작성 중. 직후 첫 `token` 이벤트 시작             |
| 6    | `verifying`     | 답변 근거 검증 중                                     |
| 7    | `formatting`    | UI 응답 정리 중. 직후 `sources` / `verification` 송신 |

**status 이벤트 처리 규칙**

- `status.data`는 `{ "phase": string, "message": string }` JSON이다.
- 각 phase 진입 시 1회 송신된다.
- 검색 결과가 0건이면 `answering` / `streaming` / `verifying` phase가 오지 않을 수 있다. 이 경우 `connecting` → `acl_filtering` → `searching` → `formatting` 순으로 단축된다.
- `done` / `error`는 별도 `status` 이벤트로 보내지 않고 기존 `done` / `error` 이벤트를 사용한다.
- `message` 문구는 운영 중 변경될 수 있으므로 UI 분기 로직은 `message`가 아니라 `phase` 기준으로 처리한다.
- 이 엔드포인트는 항상 SSE 스트리밍으로 응답한다 — 비-스트리밍 모드(`stream=false`)는 제공하지 않는다.
- 알 수 없는 phase 값은 무시하거나 직전 상태를 유지한다.

**이벤트 순서 보장**

정상 흐름의 이벤트 순서는 다음 불변식을 따른다(클라이언트 상태머신은 이 순서에 의존해도 된다).

1. `status` 이벤트는 phase 표 순서대로 진입 시 1회씩 송신된다(0건 단축은 위 처리 규칙 참조).
2. `token` 이벤트들은 `streaming` phase 이후 ~ `verifying` phase 이전 사이에 **연속**으로 온다(중간에 `sources` / `verification` / `meta` 가 끼어들지 않는다).
3. 본문 종료 후 `sources` → `verification` → `meta` 순으로 각 최대 1회 송신된다.
4. 스트림은 `done` 또는 `error` 로 정확히 한 번 종료한다.
5. 정의되지 않은/추가된 이벤트 타입은 무시한다(전방 호환).

**스트림 종료 · 영속 규칙**

- 모든 스트림은 `done` 또는 `error` **정확히 하나**로 종료된다(상호 배타). 클라이언트는 둘 중 하나 수신 시 연결을 닫는다.
- user 메시지는 질의 시작 시 선저장한다. `done` 수신 시 BFF 가 assistant 메시지(+`sources`+`verification`)를 저장하고 그 `messageId` 를 `done` 으로 반환한다 (`backend/bff-server/current-plans.md` Feature 5).
- `error` 로 종료되면 assistant 메시지는 저장하지 않는다(선저장된 user 메시지는 유지). 따라서 `error` 에는 `messageId` 가 없다.
- `token` 청크는 수신 순서대로 **그대로 이어 붙인다**(트림·구분자 삽입 금지). 예: `"S3 권한 오류는"` + `" IAM 정책을"`.

**0건(검색 결과 없음) 처리**

- phase 단축은 위 "status 이벤트 처리 규칙" 참조(`answering` / `streaming` / `verifying` 생략).
- `sources` 는 **빈 배열로 1회** 전송하고, `verification` 은 생략한다(검증할 근거 없음).
- 고정 안내 문구를 `token` 으로 보낼 수 있다(문구·전송 여부는 ML 구현). 0건도 `done` + `messageId` 로 **정상 종료**하며 assistant 메시지(빈 `sources`)를 저장한다.

**`error` 이벤트 / 에러 코드**

`error.data` 는 `{ "errorCode": string, "message": string }` 이다. `errorCode` 는 공통 Wrapper 의 `errorCode` 와 동일한 문자열 체계를 따른다(SSE 에는 HTTP 정수 `code` 가 없다 — Wrapper 의 `code`(int)와 혼동 금지).

| errorCode             | 의미                                            |
| --------------------- | ----------------------------------------------- |
| `ML_SERVER_ERROR`     | ML 서버 5xx·내부 처리 오류                      |
| `ML_TIMEOUT`          | ML 응답/스트림 타임아웃 (`lina.rag.sse-timeout-ms`) |
| `ML_CONNECTION_ERROR` | ML 연결 실패·스트림 중단                        |

**meta 이벤트 payload (현재 구현 호환용, 추후 제거 예정)**

```json
{
  "intent": "운영가이드",
  "used_llm": "gpt-4o",
  "feedback_enabled": true,
  "latency_ms": 1234,
  "title": "S3 권한 오류 해결 방법"
}
```

| Field              | Type    | Required | Description                                   |
| ------------------ | ------- | -------- | --------------------------------------------- |
| `intent`           | string  | Y        | 질의 라우터가 분류한 질문 의도                |
| `used_llm`         | string  | Y        | 실제 답변 생성에 사용한 모델명                |
| `feedback_enabled` | boolean | Y        | 이 답변에 피드백 UI를 노출할지 여부           |
| `latency_ms`       | number  | Y        | 그래프 진입부터 응답 산출까지의 처리 지연(ms) |
| `title`            | string  | N        | LLM이 생성한 현재 대화 제목                   |

> `meta`는 현재 RAG 구현에서 송신되므로 프론트 파서는 수신 가능해야 한다. FE는 현재 `title`만 대화 제목 갱신에 사용하고 나머지 필드는 UI 상태에 반영하지 않는다. ML(RAG) 응답을 BE 통합 목표 계약으로 마이그레이션한 이후 이벤트 계약에서 제거될 수 있다.
> +) 나머지 필드는 필요하다면 관리자 화면에 적용되도록 설정

**대화 제목 자동 설정 규칙**

- 새 대화는 `title = "새 대화"`(기본값)로 생성된다(§1-2 새 대화 생성).
- BFF 는 **첫 assistant 응답**을 저장할 때 현재 `title` 이 기본값(`"새 대화"`)이면 `meta.title` 로 제목을 1회 자동 설정한다.
- 제목이 이미 변경돼 있으면(자동·수동 불문) `meta.title` 을 무시한다 → 사용자의 `PATCH /api/conversations/{conversationId}` 제목 수정이 항상 우선한다.

**연결 · 타임아웃 · keep-alive · 재연결**

- **응답 헤더**: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`(프록시 버퍼링 비활성).
- **타임아웃은 idle 기준**(총 처리 시간 제한이 아니다). BFF↔ML 스트림은 `lina.rag.sse-timeout-ms`(기본 60s) 동안 **이벤트가 하나도 없으면** `error`(`ML_TIMEOUT`)로 종료한다. Gateway/프록시의 SSE idle 타임아웃도 ≥60s 로 맞춘다.
- **keep-alive**: 장시간 phase(검색·LLM 생성 등) 동안 연결 유지를 위해 진행 중 `status` 이벤트(또는 SSE 주석 라인 `:keep-alive`)를 idle 타임아웃 내 1회 이상 보낸다.
- **재연결 미지원**: 이벤트에 `id:` 를 부여하지 않으며 `Last-Event-ID` 기반 재개를 지원하지 않는다(응답 스트림은 비멱등). 연결이 끊기면 클라이언트가 질의를 새로 요청한다.

---

## 1-2. 대화 관리

### 새 대화 생성

| 항목   | 내용                 |
| ------ | -------------------- |
| Method | `POST`               |
| URL    | `/api/conversations` |

**Response**

```json
{
  "isSuccess": true,
  "code": 201,
  "message": "새 대화 생성 성공",
  "data": {
    "conversationId": "conv-uuid-001",
    "title": "새 대화",
    "isPinned": false,
    "createdAt": "2026-05-06T19:00:00+09:00"
  }
}
```

### 대화 목록 조회

| 항목   | 내용                        |
| ------ | --------------------------- |
| Method | `GET`                       |
| URL    | `/api/conversations`        |
| 설명   | 사이드바에 표시할 대화 목록 |

**Query Parameter**
| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| page | int | N | 0 | 페이지 번호 |
| size | int | N | 20 | 페이지 크기 |

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "대화 목록 조회 성공",
  "data": {
    "conversations": [
      {
        "conversationId": "conv-uuid-001",
        "title": "S3 권한 오류 해결 방법",
        "lastMessageAt": "2026-05-06T19:05:00+09:00",
        "isPinned": true
      }
    ],
    "totalCount": 15,
    "page": 0,
    "size": 20
  }
}
```

- 정렬: 고정(`isPinned`) 우선 → `lastMessageAt` 최신순으로 페이징한다(고정 대화는 항상 상단).
- `isPinned`: 채팅방 고정 여부. 기본 `false`. 토글은 `PATCH /api/conversations/{conversationId}` 참조.

### 대화 검색

| 항목   | 내용                                                             |
| ------ | ---------------------------------------------------------------- |
| Method | `GET`                                                            |
| URL    | `/api/conversations/search`                                      |
| 설명   | 본인 대화 중 메시지 본문(`messages.content`)에 매칭되는 대화 검색 |

**Query Parameter**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `q` | string | ✅ | — | 검색어. **trim 후 길이 2~50자**. 위반 시 `400`(`errorCode: INVALID_SEARCH_QUERY`) |
| `page` | int | N | 0 | 0-based 페이지 번호 |
| `size` | int | N | 20 | 페이지 크기 (최대 50) |

**Response (성공 200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "대화 검색 성공",
  "data": {
    "results": [
      {
        "conversationId": "conv-uuid-001",
        "title": "S3 권한 오류 해결 방법",
        "lastMessageAt": "2026-05-06T19:05:00+09:00",
        "isPinned": false,
        "matchedMessages": [
          {
            "messageId": "msg-uuid-002",
            "role": "assistant",
            "snippet": "...IAM 정책을 수정하여 S3 권한 오류를 해결했습니다...",
            "matchPositions": [[14, 20]],
            "createdAt": "2026-05-06T19:00:05+09:00"
          }
        ],
        "matchCount": 3
      }
    ],
    "totalCount": 5,
    "page": 0,
    "size": 20
  }
}
```

**필드 설명**

- `results[]`: 매칭 대화. 정렬은 `lastMessageAt` 최신순 (PoC — 관련도 점수 미적용).
- `results[].matchedMessages[]`: 매칭 메시지 샘플. **대화당 최대 3개**까지 노출하며, 더 많은 매칭은 `matchCount` 로 총수 표기.
- `results[].matchedMessages[].snippet`: 매칭 위치 주변 발췌 (좌우 약 40자, **plain text**). 본문이 잘린 경우 `...` 부착 — `...` 도 `snippet` 문자열에 포함되며, 아래 `matchPositions` 인덱스는 이를 포함한 `snippet` 기준이다.
- `results[].matchedMessages[].matchPositions`: `snippet` 문자열 내 매칭 구간 배열 `[[start, end], ...]`. 인덱스는 **UTF-16 code unit**, `end` 는 **exclusive** (JS `String.slice(start, end)` 호환). FE 가 이 구간을 하이라이트 표시. **하이라이트 HTML 은 서버가 만들지 않는다**(XSS 안전성).
- `results[].matchedMessages[].role`: `user` / `assistant` 모두 검색 대상.
- `results[].matchCount`: 해당 대화 내 매칭 메시지 총 개수 (`matchedMessages.length` 와 다를 수 있음 — 위 3개 cap).
- `totalCount`: 검색 결과 대화 총 개수 (페이지 무관).

**Response (실패)**

- `400` — `errorCode: INVALID_SEARCH_QUERY` — `q` 누락 / trim 후 길이 < 2 / trim 후 길이 > 50 / `size` 범위 초과.
- `401` — 미인증 (3단계 이후, 2단계 데모는 인증 비활성).

**구현 노트 (PoC)**

- **검색 범위**: 본인 대화의 `messages.content` 본문 — `role` 무관(user 질문·assistant 답변 모두 매칭).
- **권한 격리**: `conversations.userId == 현재 사용자` 필터 필수 (2단계 데모는 `lina.demo.fixed-user-id`). 타 사용자 대화 노출 금지.
- **soft delete**: `conversations.deletedAt == null` AND `messages.deletedAt == null`.
- **매칭**: MongoDB `$regex` (case-insensitive) on `messages.content`. 검색어는 정규식 메타문자를 **escape** 한 뒤 사용. `messages.content` 텍스트 인덱스는 후속 도입(`docs/db-schema.md` §3.2).
- **snippet 추출**: 첫 매칭 위치 기준 좌우 ~40자 잘라 ~80~100자 출력. 시작/끝에 잘림이 있으면 `...` prefix/suffix. `matchPositions` 는 추출된 `snippet` 기준 재계산.

### 대화 메시지 이력 조회

| 항목   | 내용                                           |
| ------ | ---------------------------------------------- |
| Method | `GET`                                          |
| URL    | `/api/conversations/{conversationId}/messages` |
| 설명   | 멀티턴 복원용 전체 메시지 이력                 |

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "메시지 이력 조회 성공",
  "data": {
    "conversationId": "conv-uuid-001",
    "messages": [
      {
        "messageId": "msg-uuid-001",
        "role": "user",
        "content": "지난번 S3 버킷 권한 오류 때 어떻게 해결했어?",
        "createdAt": "2026-05-06T19:00:00+09:00"
      },
      {
        "messageId": "msg-uuid-002",
        "role": "assistant",
        "content": "S3 권한 오류는 IAM 정책을 수정하여 해결했습니다...",
        "sources": [
          {
            "title": "S3 트러블슈팅 가이드",
            "pageId": "12345",
            "spaceId": "98310",
            "spaceName": "Cloud Control Center",
            "url": "https://confluence.example.com/pages/12345",
            "sourceUpdatedAt": "2026-04-15T18:30:00+09:00",
            "relevanceScore": 0.92
          }
        ],
        "confidenceScore": 0.85,
        "verificationResult": "SUPPORTED",
        "createdAt": "2026-05-06T19:00:05+09:00"
      }
    ]
  }
}
```

### 대화 수정 (제목 / 고정)

| 항목   | 내용                                   |
| ------ | -------------------------------------- |
| Method | `PATCH`                                |
| URL    | `/api/conversations/{conversationId}`  |
| 설명   | 제목 변경·채팅방 고정 토글 (부분 수정) |

**Request Body** — `title` / `isPinned` 각각 선택. 전달된 필드만 수정하며, 둘 중 하나 이상은 필수다.

```json
{ "title": "S3 권한 오류 트러블슈팅" }
```

```json
{ "isPinned": true }
```

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "대화 수정 성공",
  "data": {
    "conversationId": "conv-uuid-001",
    "title": "S3 권한 오류 트러블슈팅",
    "isPinned": true,
    "updatedAt": "2026-05-06T19:10:00+09:00"
  }
}
```

### 대화 삭제

| 항목   | 내용                                  |
| ------ | ------------------------------------- |
| Method | `DELETE`                              |
| URL    | `/api/conversations/{conversationId}` |

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "대화 삭제 성공",
  "data": null
}
```

---

## 1-3. 피드백

| 항목   | 내용                                 |
| ------ | ------------------------------------ |
| Method | `POST`                               |
| URL    | `/api/messages/{messageId}/feedback` |
| 설명   | 답변에 대한 좋아요/싫어요 피드백     |

**Request Body**

```json
{
  "rating": "LIKE",
  "comment": "정확한 답변이었어요"
}
```

- `rating`: `"LIKE"` | `"DISLIKE"`
- `comment`: 선택 사항

**Response**

```json
{
  "isSuccess": true,
  "code": 201,
  "message": "피드백 등록 성공",
  "data": {
    "feedbackId": "fb-uuid-001",
    "messageId": "msg-uuid-002",
    "rating": "LIKE",
    "createdAt": "2026-05-06T19:06:00+09:00"
  }
}
```

---

## 1-4. 데이터 수집 (관리자용)

> **권한**: `/api/admin/*` 는 ADMIN 역할 전용이다 — 미인증 `401`(`errorCode: UNAUTHORIZED`), 일반 사용자(USER) `403`(`errorCode: FORBIDDEN`). §4-2 공통 권한과 동일. 2단계 데모는 인증 비활성(Common Request Header 참조).
>
> **수집 자격증명 모델 (2026-06-02 확정)**: admin 도 일반 사용자와 동일하게 Confluence OAuth 3LO 로 로그인하며(§4-1), ingestion 도 같은 admin OAuth access_token 을 사용한다(별도 API Token 미사용). page-level read restriction 우회를 위해 Atlassian **Admin Key** 가 활성화되어 있어야 하고, Data Ingestion Pipeline 이 Atlassian REST 호출 시 `Atl-Confluence-With-Admin-Key: true` 헤더를 부여한다. 자세한 ACL 적재 흐름은 `docs/adr/0001-page-level-acl-source.md` §2.1 참조.
>
> **Admin Key 활성화 시점 (2026-06-02 결정)**: 기본 동선은 **`POST /api/admin/ingest` 가 내부적으로 key activate 를 묶어 처리** — admin 이 "데이터 인제스천 파이프라인" 버튼 하나로 키 발급 + 수집 시작을 일괄 트리거. BFF/auth-server 가 key 활성 상태를 확인해 만료/미활성이면 자동 `POST /api/v2/admin-key` 호출 후 ingest 진행. 별도의 `POST /api/admin/key/activate` endpoint 는 **수동/테스트용** 으로 남긴다(검증·디버깅·운영 점검).
>
> **Admin Key 말소 (보안, 2026-06-05 결정)**: 2026-06-04 의 BFF Virtual Thread watcher + `/ml/ingest/status` polling 방식은 **RabbitMQ completion event 방식으로 대체**한다. Data Ingestion Pipeline 은 job 완료/실패 시 completion event 를 RabbitMQ 에 발행하고, BFF consumer 가 이를 consume 해 auth-server 내부 `POST /internal/admin/key/deactivate` 를 호출한다. 60분 TTL 자동 만료는 BFF consumer 장애·DLQ 미복구 시의 최종 fallback 으로만 둔다. 말소 대상은 OAuth token 이 아니라 **Atlassian Admin Key 활성 상태**이며, auth-server deactivate 내부 API 는 `jobId` 기준 중복 completion event 가 와도 안전하도록 idempotent 하게 취급한다.
>
> **RabbitMQ 보안 원칙**: job/completion payload 에는 `jobId`, `adminUserId`, `mode`, `status`, timestamp, error 요약 등 작업 식별/상태 정보만 둔다. `accessToken`, `refreshToken`, `cloudId` 등 Confluence credential set 은 RabbitMQ payload 에 절대 포함하지 않는다. `cloudId` 는 MQ 가 아니라 Data Ingestion Worker 가 auth-server 내부 credential 조회 API 로 admin OAuth `accessToken` 과 함께 조회한다.

### Admin Key 활성화 (수동/테스트용)

> **일반 사용 경로 아님** — 기본 admin UX 는 `POST /api/admin/ingest` 가 내부적으로 key activate 를 묶어 처리(상단 노트). 본 endpoint 는 **수동 검증·디버깅·운영 점검** 시 명시적으로 키만 활성화하고 싶을 때 사용.

| 항목   | 내용                                                                                                                              |
| ------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Method | `POST`                                                                                                                            |
| URL    | `/api/admin/key/activate`                                                                                                         |
| 설명   | admin 의 Confluence Admin Key 60분 명시적 활성화 (일반 동선은 `/api/admin/ingest` 가 자동 처리)                                   |

Request Body 없음 (admin 의 OAuth access_token 은 서버 측에서 사용).

**Response (성공 200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "Admin Key 활성화 성공",
  "data": {
    "activatedUntil": "2026-06-02T13:56:43+09:00"
  }
}
```

**흐름**

- FE → `POST /api/admin/key/activate` (Bearer JWT, ADMIN 전용)
- BFF → auth-server 내부 API 호출 → auth-server 가 admin 의 저장된 OAuth access_token 으로 Atlassian `POST /api/v2/admin-key` 활성화
- 응답 `activatedUntil` 을 FE 가 표시(만료 시각·count-down). 만료 후 admin 이 재활성화

> **검증 게이트 (3단계 구현 시):** OAuth Bearer + `Atl-Confluence-With-Admin-Key: true` 헤더 조합이 Atlassian 측에서 정상 작동하는지 첫 admin OAuth 토큰 확보 직후 curl 로 검증한다. 실패 시 admin API Token 을 별도 보관해 ingestion 자격증명을 분리하는 fallback 으로 전환(plan 한 행 정정). 팀 사전 테스트(`confluence_admin_key_test_summary.md`, 2026-06-02)는 API Token + Admin Key 조합으로 동작 확인됨.

### 수집 트리거

| 항목   | 내용                                                                                |
| ------ | ----------------------------------------------------------------------------------- |
| Method | `POST`                                                                              |
| URL    | `/api/admin/ingest`                                                                 |
| 설명   | admin 이 접근 가능한 **모든 Confluence 스페이스** 일괄 수집 비동기 트리거 (버튼 1회) |

**Request Body**

```json
{ "mode": "full" }
```

- `mode`: `"full"` (전체 재색인, 기본) | `"delta"` (변경분만). 생략 시 `"full"`.
- spaceKey 등 스페이스 스코프 파라미터 **없음** — admin Key 로 admin 이 접근 가능한 전체 스페이스를 ML 이 iterate (2026-06-04 결정).

**처리 흐름**

1. BFF 는 ADMIN 권한을 검증하고 `jobId` 를 생성한다.
2. BFF 는 auth-server 내부 `POST /internal/admin/key/activate` 로 Atlassian Admin Key 를 활성화한다(이미 유효하면 idempotent 하게 성공 처리).
3. BFF 또는 Data Ingestion Pipeline 은 RabbitMQ 에 ingest job 을 발행한다. payload 는 `jobId`, `adminUserId`, `mode`, `requestedAt` 등 식별/상태 정보만 포함한다.
4. Data Ingestion Worker 는 job 을 consume한 뒤 auth-server 내부 credential 조회 API 로 `adminUserId` 기준 admin OAuth `accessToken` + `cloudId` 를 함께 조회한다.
5. Data Ingestion Worker 는 Confluence REST 호출 시 `Authorization: Bearer {admin accessToken}` + `Atl-Confluence-With-Admin-Key: true` 헤더를 사용한다.
6. 완료/실패 시 Data Ingestion Pipeline 은 RabbitMQ completion event 를 발행한다. BFF consumer 가 event 를 consume해 auth-server `POST /internal/admin/key/deactivate` 를 호출한다.
7. BFF consumer 는 `jobId` 기준 중복 completion event 를 idempotent 하게 처리한다.

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "데이터 수집 작업 시작",
  "data": {
    "jobId": "job-uuid-001",
    "status": "STARTED",
    "startedAt": "2026-05-06T19:00:00+09:00"
  }
}
```

### 수집 상태 조회

| 항목   | 내용                               |
| ------ | ---------------------------------- |
| Method | `GET`                              |
| URL    | `/api/admin/ingest/status/{jobId}` |

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "수집 상태 조회 성공",
  "data": {
    "jobId": "job-uuid-001",
    "status": "IN_PROGRESS",
    "totalPages": 150,
    "processedPages": 87,
    "failedPages": 2,
    "startedAt": "2026-05-06T19:00:00+09:00"
  }
}
```

- `status`: `"STARTED"` | `"IN_PROGRESS"` | `"COMPLETED"` | `"FAILED"`

---

# 2. 내부 API

> Wrapper 적용 여부는 AI 담당 팀원과 합의 필요. §2-1~§2-4 는 BFF → ML 서버 계약이고, §2-5 는 Data Ingestion Worker → auth-server 내부 계약이다.

## 2-1. RAG 질의

| 항목   | 내용                                                                                                  |
| ------ | ----------------------------------------------------------------------------------------------------- |
| Method | `POST`                                                                                                |
| URL    | `/ml/query`                                                                                           |
| 설명   | BFF 가 사용자 질의를 RAG(ML)로 전달하는 입력 API. 응답은 동일 요청에 대한 **SSE 스트림**으로 돌아온다 |

> **엔드포인트는 `POST /ml/query` 하나다.** 본 절은 **요청(Request) 측**을 정의하며, 응답 이벤트 계약은 §1-1 의 SSE 7종 정본을 BFF 가 그대로 중계한다(아래 "Response" 참조).
> ML 은 JWT 를 직접 검증하지 않는다 — BFF 가 JWT 에서 추출한 `userId`/`groups` 를 본문으로 넘겨 RAG 가 ACL Pre-filtering 을 시스템 단에서 강제한다. Confluence `accessToken`/`cloudId` 는 본 엔드포인트가 아니라 수집 단계(`/ml/ingest`, §2-2)에서 auth-server 내부 credential 조회를 통해서만 사용한다.

**Request Header**

| Name           | Type   | Description        | Required |
| -------------- | ------ | ------------------ | -------- |
| `Content-Type` | String | `application/json` | ✅       |

**Request Body**

> ACL(`userId`/`groups`)만으로 cross-space 검색한다 — 사용자가 접근 가능한 모든 콘텐츠에서 답변. 스페이스 스코프 파라미터는 없다(2026-06-04 결정).

```json
{
  "question": "지난번 S3 버킷 권한 오류 때 어떻게 해결했어?",
  "userId": "user-001",
  "groups": ["Cloud-Control-Center"],
  "conversationId": "conv-uuid-001",
  "history": [
    { "role": "user", "content": "S3 관련 장애 이력 알려줘" },
    { "role": "assistant", "content": "최근 S3 관련 장애는 3건이 있었습니다..." }
  ],
  "stream": true
}
```

| Name             | Type     | Required | Description                                                                                                                                                                                          |
| ---------------- | -------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `question`       | String   | ✅       | 사용자 자연어 질문(최소 1자)                                                                                                                                                                         |
| `userId`         | String   | ✅       | JWT `sub` 에서 추출한 사용자 식별자(ACL Pre-filtering)                                                                                                                                               |
| `groups`         | String[] | ✅       | 사용자 그룹(ACL `should`-OR 필터). **빈 배열 금지** — BFF fail-closed                                                                                                                                |
| `conversationId` | String   | —        | 대화 컨텍스트. 없으면 단발성 질의(new topic)로 처리                                                                                                                                                  |
| `history`        | Object[] | —        | 이전 대화 이력(멀티턴). BFF 가 MongoDB `messages` 에서 `lina.rag.history-turns`(기본 10) 만큼 조회해 전달                                                                                            |
| `stream`         | Boolean  | —        | 기본 `false`. **BFF 는 항상 `true` 로 호출**(토큰 스트리밍). PoC 환경(OpenAI 키/generator 없음)에서는 `true` 라도 자동 비-streaming fallback 으로 `token` 이벤트가 1회로 내려올 수 있음 — §1-1 참조 |

**`history[]` 객체**

| Name      | Type   | Required | Description                                            |
| --------- | ------ | -------- | ------------------------------------------------------ |
| `role`    | String | ✅       | `"user"` / `"assistant"` (lowercase — RAG/OpenAI 관용) |
| `content` | String | ✅       | 발화 내용                                              |

> **`history[].role` 값 체계**: 메시지 `role` 은 저장(`messages.role`)·외부 응답(§1-2)·RAG 와이어(`/ml/query` `history[].role`) 모두 `user`/`assistant` lowercase 로 통일한다 — LLM/OpenAI 산업 표준(Common Enum 값 표기 정책의 명시된 예외). **boundary 변환 없음** — 저장값을 그대로 RAG 에 전달.

> **ACL fail-closed (BFF 측 강제)**: `userId` 가 비어 있거나 `groups` 가 비어 있으면(`[]`) BFF 는 `/ml/query` 호출을 **막고** SSE `error`(`errorCode: UNAUTHORIZED`)로 종료한다 (`backend/CLAUDE.md` §6 "ACL 필터 없이 RAG 호출 금지"). RAG 자체 검증이 빈 값을 허용하더라도 BFF 가 게이트한다.

> **camelCase**: 와이어 필드는 모두 camelCase. RAG(FastAPI)는 `populate_by_name=True` 로 테스트 편의상 snake_case 도 허용하나, 생산 클라이언트(BFF)는 camelCase 만 사용한다.

> **Confluence 토큰 미포함 (2026-05-22 결정, 2026-06-05 갱신)**: 권한은 수집 시 Qdrant payload(`allowed_groups`/`allowed_users`)에 ACL 로 저장되고, 질의 시 JWT 의 `userId`/`groups` 로 필터링한다 (기획서 §6.4/§6.6). 따라서 `/ml/query` 는 라이브 Confluence 호출이 없어 `accessToken`/`cloudId` 가 불필요하다. 수집 단계(`/ml/ingest`, §2-2)에서도 HTTP/RabbitMQ payload 로 전달하지 않고, Data Ingestion Worker 가 auth-server 내부 credential 조회 API 로 가져와 사용한다.
>
> ※ ML 확인 대기: `/ml/query` 가 실시간 Confluence 호출을 일절 하지 않음을 ML 팀과 확인한 뒤 본 결정을 확정한다.

**Response: SSE 스트리밍**

응답 이벤트 계약은 §1-1 의 SSE 7종 정본(`status` / `token` / `sources` / `verification` / `meta` / `done` / `error`)을 따른다. BFF 는 RAG 스트림을 외부 API(`POST /api/conversations/{conversationId}/chat`)로 그대로 중계하며, **한 곳에서만 boundary 가공**이 일어난다:

- **`error` 이벤트**: RAG·BFF·FE 모두 `{ "errorCode": "...", "message": "..." }` 동일 키 사용 — BFF 는 키 매핑 없이 그대로 passthrough(`errorCode` 값이 §1-1 에러 코드 표와 일치하는지만 검증).
- **`done` 이벤트**: RAG 는 `{}` (빈 객체)로 종료한다. BFF 가 assistant 메시지를 DB 저장 → 생성된 `messageId` 를 채워 FE 로 중계한다(`{ "messageId": "msg-uuid-..." }`).

## 2-2. 데이터 수집 트리거

| 항목   | 내용                                                 |
| ------ | ---------------------------------------------------- |
| Method | `POST`                                               |
| URL    | `/ml/ingest`                                         |
| 설명   | Confluence 문서 수집 → 청킹 → 임베딩 파이프라인 job 발행 또는 실행 |

**Request Body**

```json
{
  "jobId": "job-uuid-001",
  "adminUserId": "admin-account-id",
  "mode": "full"
}
```

- `mode`: `"full"` (전체) | `"delta"` (변경분만)
- 스페이스 스코프 파라미터 **없음** — admin Key 로 admin 이 접근 가능한 전체 스페이스를 ML 이 iterate 하며 수집 (2026-06-04 결정, `/api/admin/ingest` 와 동일).
- `jobId`: BFF 가 생성하거나 Data Ingestion Pipeline 이 생성해 반환하는 작업 식별자. completion event, status 조회, Admin Key deactivate idempotency 의 기준이다.
- `adminUserId`: auth-server 에 저장된 admin OAuth credential 을 조회하기 위한 사용자 식별자. credential 자체가 아니다.
- `accessToken` / `refreshToken` / `cloudId` 는 본문에 포함하지 않는다. Data Ingestion Worker 는 job consume 후 auth-server 내부 credential 조회 API 로 admin OAuth `accessToken` + `cloudId` 를 함께 조회한다. Confluence REST 호출에는 `Authorization: Bearer {admin accessToken}` + `Atl-Confluence-With-Admin-Key: true` 를 사용한다.

> **RabbitMQ job payload 원칙:** `/ml/ingest` HTTP 호출이 내부적으로 MQ job 을 발행하든 BFF 가 직접 RabbitMQ 에 발행하든, MQ payload 는 작업 식별/상태 정보만 포함한다. `cloudId` 는 payload 로 전달하지 않고 auth-server 내부 credential 조회 응답에서 `accessToken` 과 함께 반환된다.

**RabbitMQ completion event**

```json
{
  "eventType": "INGEST_COMPLETED",
  "jobId": "job-uuid-001",
  "adminUserId": "admin-account-id",
  "mode": "full",
  "status": "COMPLETED",
  "completedAt": "2026-06-05T19:00:00+09:00",
  "errorCode": null,
  "message": null
}
```

- `eventType`: `"INGEST_COMPLETED"` 또는 `"INGEST_FAILED"` (초안)
- `status`: `"COMPLETED"` | `"FAILED"`
- 실패 event 는 `errorCode`/`message` 에 credential 이 아닌 오류 요약만 포함한다.
- BFF consumer 는 completion event 를 consume한 뒤 auth-server `POST /internal/admin/key/deactivate` 를 호출한다.

**BFF consumer / DLQ 정책 초안**

- queue/exchange 는 durable 로 구성하고 consumer ack 는 Admin Key deactivate 성공 또는 idempotent 완료 확인 후 수행한다.
- BFF 재시작·consumer 장애 시 RabbitMQ 에 남은 completion event 를 재처리한다. 60분 TTL 은 completion event 처리 실패 시에도 Admin Key 가 최종 만료되는 fallback 이다.
- 동일 `jobId` completion event 중복 수신은 정상 가능성으로 보고 idempotent 처리한다.
- auth-server deactivate 실패는 초안 기준 최대 5회 재시도(backoff 적용) 후 DLQ 로 이동한다.
- DLQ 이동 조건: payload schema 오류, `adminUserId`/`jobId` 누락, deactivate 5회 실패, 재처리해도 복구되지 않는 auth-server 4xx.
- DLQ 수동 복구: 운영자가 원인 확인 → 필요 시 auth-server 내부 deactivate 를 `jobId`/`adminUserId` 로 수동 호출 → 성공 확인 후 DLQ event 폐기 또는 수정한 event 를 원 queue 로 재발행한다.

> **보안 주의:** RabbitMQ 와 HTTP 요청/응답 본문에 Confluence credential set 을 노출하지 않도록 다음 운영 규칙을 함께 강제한다.
>
> - ML/BFF 로그·tracing 본문에 token 미수집 (마스킹 또는 본문 제외)
> - RabbitMQ 메시지·이벤트 페이로드에 `accessToken`/`refreshToken`/`cloudId` 미포함
> - actuator `env`/`heapdump`/`threaddump` 등 민감 endpoint 비노출
> - Data Ingestion Pipeline Pod에 NetworkPolicy 적용해 호출자 제한

## 2-3. 수집 상태 조회

| 항목   | 내용                        |
| ------ | --------------------------- |
| Method | `GET`                       |
| URL    | `/ml/ingest/status/{jobId}` |

Response: 외부 API `/api/admin/ingest/status/{jobId}`의 `data` 내부와 동일.

## 2-4. 헬스체크

ML 서버는 책임이 다른 두 파이프라인으로 분리되어 있으며, 각각의 헬스 엔드포인트도 분리해 관리한다.

### 2-4-1. RAG Pipeline 헬스체크

| 항목   | 내용                                                                                   |
| ------ | -------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                  |
| URL    | `/ml/rag/health`                                                                       |
| 목적   | BFF 가 RAG Pipeline 서버(질의/응답 생성·AI Agent 워크플로)가 정상 응답 가능한지만 확인 |

**Response**

```json
{ "status": "UP" }
```

### 2-4-2. Data Ingestion Pipeline 헬스체크

| 항목   | 내용                                                                                         |
| ------ | -------------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                        |
| URL    | `/ml/ingest/health`                                                                          |
| 목적   | BFF 가 Data Ingestion Pipeline 서버(Confluence 수집/청킹/임베딩)가 정상 응답 가능한지만 확인 |

**Response**

```json
{ "status": "UP" }
```

### 공통 규칙

| status | 의미                |
| ------ | ------------------- |
| `UP`   | 대상 서버 정상 응답 |
| `DOWN` | 응답 불가 또는 오류 |

- BFF 는 Vector DB, LLM, Confluence API, RabbitMQ 등 ML 내부 의존성을 **전역적으로 health check 하지 않는다**. 각 서버(RAG Pipeline / Data Ingestion Pipeline)가 요청을 받아 응답할 수 있는 상태(reachable & responsive)인지만 확인한다.
- 내부 컴포넌트의 상세 상태 점검 책임은 각 ML 서버 자체에 둔다. BFF 는 그 상세 상태를 응답으로 받지 않는다.
- 두 엔드포인트는 독립적으로 평가된다. 한쪽이 `DOWN` 이어도 다른 한쪽이 `UP` 이면 해당 기능만 영향을 받는다 (예: Ingestion 다운 시 신규 수집만 차단, 기존 검색·질의는 정상).

> **Spring Boot 측 적용 노트**: 각 ML 서버 호출 경로에는 Resilience4j 등의 Circuit Breaker 를 적용해, ML 서버 장애가 BFF 전체로 전파되지 않도록 격리한다. Circuit Breaker 는 RAG Pipeline / Data Ingestion Pipeline 호출에 각각 독립적으로 적용하며, BFF 자체 헬스체크와는 분리한다.

## 2-5. Admin Confluence credential 조회 (Data Ingestion Worker → auth-server)

> **내부 API 전용**: FE-facing 계약이 아니며, Data Ingestion Worker 가 RabbitMQ ingest job 을 consume한 뒤 Confluence 호출 직전에 사용한다. BFF 는 `/api/admin/ingest` 경로에서 `accessToken`/`refreshToken`/`cloudId` 를 조회하거나 전달하지 않는다.

| 항목   | 내용                                                                  |
| ------ | --------------------------------------------------------------------- |
| Method | `GET`                                                                 |
| URL    | `/internal/auth/admin-confluence-credential?adminUserId={adminUserId}` |
| 설명   | admin OAuth `accessToken` + `cloudId` 를 함께 조회                    |

**Request**

| Name          | Type   | Required | Description                                                                     |
| ------------- | ------ | -------- | ------------------------------------------------------------------------------- |
| `adminUserId` | String | ✅       | RabbitMQ ingest job payload 의 `adminUserId`. credential 자체가 아닌 사용자 식별자 |

**Response (200 OK)**

```json
{
  "accessToken": "<admin-oauth-access-token>",
  "cloudId": "11111111-2222-3333-4444-555555555555",
  "expiresAt": "2026-06-05T20:00:00+09:00"
}
```

- `refreshToken` 은 응답하지 않는다.
- auth-server 는 `adminUserId` 로 사용자/토큰 레코드를 조회하고 `users.role == ADMIN` 을 확인한다.
- access token 이 만료됐거나 만료 임박이면 auth-server 가 저장된 refresh token 으로 Atlassian token refresh 를 수행하고 DB 를 최신 access/refresh token 으로 갱신한 뒤 응답한다.
- `cloudId` 는 RabbitMQ payload 가 아니라 이 응답에서 `accessToken` 과 함께 반환된다.
- Data Ingestion Worker 는 Confluence REST 호출 시 `Authorization: Bearer {admin accessToken}` + `Atl-Confluence-With-Admin-Key: true` 헤더를 사용한다.

**Error**

| HTTP | errorCode | 조건 |
| ---- | --------- | ---- |
| 400 | `INVALID_REQUEST` | `adminUserId` 누락/blank |
| 401 | `UNAUTHORIZED` | Atlassian refresh 실패(`invalid_grant`) 등 재로그인 필요 |
| 403 | `FORBIDDEN` | `adminUserId` 사용자가 ADMIN 이 아님 |
| 404 | `RESOURCE_NOT_FOUND` | 사용자 또는 저장된 Confluence credential 없음 |
| 502 | `EXTERNAL_SERVICE_ERROR` | Atlassian refresh 일시 장애 |

**보안 원칙**

- 호출 주체는 Data Ingestion Worker 로 제한한다(NetworkPolicy 또는 내부 service auth).
- 응답 body 로그/tracing 은 마스킹하거나 수집하지 않는다.
- RabbitMQ job/completion payload 에 `accessToken`/`refreshToken`/`cloudId` 를 넣지 않는다.

---

# 3. 전체 호출 흐름

```
[프론트엔드]
  │
  │ ─── 2단계: 대화·피드백·수집 ───────────────────────────────
  ├─ POST   /api/conversations                       → BFF → DB 대화 생성
  ├─ GET    /api/conversations                       → BFF → DB 대화 목록 조회 (고정 우선·최신순)
  ├─ GET    /api/conversations/search                → BFF → DB 메시지 본문 검색 (본인 대화·최신순)
  ├─ GET    /api/conversations/{id}/messages         → BFF → DB 메시지 이력 조회
  ├─ PATCH  /api/conversations/{id}                  → BFF → DB 대화 수정(제목/고정)
  ├─ DELETE /api/conversations/{id}                  → BFF → DB 대화 삭제(soft)
  ├─ POST   /api/conversations/{id}/chat             → BFF → POST /ml/query (SSE)
  │                                                    ├─ DB 에서 이전 이력 조회 → ML 전달
  │                                                    └─ ML 응답을 DB 저장 + SSE 중계
  ├─ POST   /api/messages/{id}/feedback              → BFF → DB 피드백 저장(upsert)
  ├─ POST   /api/admin/key/activate                  → BFF → Auth Server → Atlassian POST /api/v2/admin-key (60분, 수동/테스트용)
  ├─ POST   /api/admin/ingest                        → BFF: key 활성 미확인 시 자동 activate → RabbitMQ ingest job 발행 또는 POST /ml/ingest
  │                                                    ├─ Data Ingestion Worker → Auth Server 내부 credential 조회(accessToken+cloudId) → Confluence(Admin Key 헤더)
  │                                                    └─ RabbitMQ completion event → BFF consumer → Auth Server Admin Key deactivate(보안)
  ├─ GET    /api/admin/ingest/status/{jobId}         → BFF → GET /ml/ingest/status/{jobId}
  │
  │ ─── §4-1 인증 (5주차, Confluence OAuth 2.0) ─────────────────
  ├─ GET    /api/auth/login[?mode=admin]             → BFF → 302 https://auth.atlassian.com/authorize (mode 는 state 에 직렬화)
  ├─ GET    /api/auth/callback                       → BFF → Auth Server: code 교환·users upsert·JWT 발급
  │                                                    ├─ mode=admin + users.role!=ADMIN → 403 FORBIDDEN, 토큰 미발급
  │                                                    └─ 정상: JSON 으로 access/refresh 토큰 반환(FE 보관)
  ├─ POST   /api/auth/refresh                        → BFF → Auth Server: refresh 회전·새 JWT 발급
  ├─ POST   /api/auth/logout                         → BFF → Auth Server: refresh 무효화
  ├─ GET    /api/users/me                            → BFF → DB 사용자 정보 조회 (Bearer)
  │
  │ ─── §4-2 관리자 대시보드 (6주차, ADMIN 전용) ────────────────
  ├─ GET    /api/admin/stats                         → BFF → MySQL 통계 집계
  ├─ GET    /api/admin/users                         → BFF → MySQL 사용자 현황 + ACL 카운트
  ├─ GET    /api/admin/data                          → BFF → MongoDB(읽기) 페이지·청크·동기화 현황
  ├─ GET    /api/admin/feedback                      → BFF → MySQL 피드백 집계·부정 원문(QCA)
  ├─ GET    /api/admin/sync                          → BFF → MongoDB(읽기) 동기화 이력
  │
  │ ─── §4-3 Confluence 미리보기 (5주차 이후) ───────────────────
  └─ GET    /api/confluence/pages/preview            → BFF → Confluence REST (서버 보관 OAuth 토큰)
```

---

# 4. 중간 발표 이후 추가 예정 (5~7주차)

## 4-1. 인증 (5주차)

인증은 Confluence OAuth 2.0에 위임한다(별도 회원가입 없음). 전체 흐름·주체는 기획서 §6.5 / `backend/rules/auth.md` §1 참조. BFF는 토큰을 직접 교환하지 않고 Authorization Server에 위임한다.

> **본 절은 FE-facing 계약을 정의한다.** 인증된 요청은 세션 JWT 를 **`Authorization: Bearer {accessToken}` 헤더**로 전송한다(HttpOnly 쿠키 미사용 — 공통 Request Header 참조). 로그인/갱신 응답 `data` 로 access JWT + refresh token 을 FE 에 전달하며 FE 가 보관한다. **Confluence OAuth 토큰(Atlassian access/refresh)은 FE 에 노출하지 않고 서버(MySQL)에만 보관**한다. JWT 서명 알고리즘·access TTL·Refresh Token 저장/회전 방식 등 Authorization Server 내부 구현은 **TBD**이며 3단계(`backend/auth-server/current-plans.md`)에서 확정한다. auth-server 내부 API(토큰 교환, `accessible-resources`, `confluence-token`)는 FE 계약 범위 밖이다.

| API                      | 설명                               |
| ------------------------ | ---------------------------------- |
| `GET /api/auth/login`    | Confluence OAuth 로그인 리다이렉트 |
| `GET /api/auth/callback` | OAuth 콜백 처리, JWT 발급          |
| `POST /api/auth/refresh` | Refresh Token 기반 JWT 자동 갱신   |
| `POST /api/auth/logout`  | 로그아웃, JWT 무효화               |
| `GET /api/users/me`      | 현재 로그인 사용자 정보 조회       |

**세션 토큰 (공통)**

- 인증이 필요한 모든 API 는 `Authorization: Bearer {accessToken}` 헤더의 세션 JWT 를 검증한다. JWT claim: `userId`(Confluence accountId), `groups`, `role`(`USER`/`ADMIN`, MySQL `users.role` 단일 source), `iss`, `exp`, `iat`(`backend/rules/auth.md` §2, `docs/db-schema.md` §6.1). 누락·만료·서명 오류 시 `401`(`errorCode: UNAUTHORIZED`). `/api/admin/*` 는 `role=ADMIN` 추가 검사.
- 로그인 성공 시 BFF 는 **access JWT + refresh token** 을 응답 `data` 로 발급한다. FE 가 보관해 access JWT 는 Bearer 헤더로 전송하고, 만료 시 `POST /api/auth/refresh` 로 갱신한다. (여기서 `refreshToken` 은 LINA 발급 세션 토큰이며 Confluence OAuth 토큰이 아니다.)
- (TBD, 3단계) JWT 서명 알고리즘 / access TTL / Refresh Token 저장·회전·전달 정책.

### `GET /api/auth/login`

Confluence OAuth 2.0 Authorization URL로 리다이렉트한다. CSRF 방지를 위해 `state`를 생성·저장한 뒤 전달한다.

**Query Parameter**
| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `returnTo` | string | N | 로그인 완료 후 돌아갈 FE 내부 경로(기본 `/`). 오픈 리다이렉트 방지를 위해 **내부 경로만 허용**한다. |
| `mode` | string | N | `"admin"` 이면 관리자 로그인 요청 — callback 에서 `users.role != ADMIN` 이면 `403 FORBIDDEN` 으로 차단. 생략·기타값이면 일반 사용자 로그인. FE 의 "Continue with Confluence for Admin" 버튼이 `?mode=admin` 을 붙여 호출(2026-06-02 회의 결정). |

> `mode` 값은 `state` 에 함께 직렬화되어 callback 까지 전달된다(또는 서버 측 세션에 저장). FE 가 임의로 클라이언트에서 결정하는 것이 아니라 login → callback 한 사이클 동안 BE 가 보관한다 — 사용자가 callback URL 만 조작해 admin 으로 우회하는 것을 막기 위함.

**Response** — `302 Found` (Wrapper 미적용)

```
Location: https://auth.atlassian.com/authorize?audience=api.atlassian.com&client_id=...&scope=...&redirect_uri=...&state=<csrf-state>&response_type=code&prompt=consent
```

### `GET /api/auth/callback`

Confluence 인가 후 redirect_uri(FE 콜백 라우트)가 받은 `code`/`state` 로 세션을 교환한다. BFF → Auth Server 가 Confluence Access/Refresh Token 교환 → ACL(스페이스 접근 권한) 조회 → **세션 JWT 발급**을 수행하고, FE 가 보관할 access/refresh 토큰을 반환한다(기획서 §6.5). Confluence OAuth 토큰은 서버에만 보관하고 응답에 포함하지 않는다.

`state` 에 보관된 `mode=admin` 표식이 있으면 `users.role == ADMIN` 인지 검증한 뒤 통과시킨다. `mode=admin` 인데 `users.role != ADMIN` 이면 `403`(`errorCode: FORBIDDEN`, message: "관리자 권한이 없는 계정입니다") 으로 거부하고 JWT 를 발급하지 않는다.

**Query Parameter**
| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `code` | string | ✅ | Confluence Authorization Code |
| `state` | string | ✅ | `login`에서 발급한 CSRF state. 불일치 시 거부. `mode=admin` 표식 보관 |

**Response (성공 200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "로그인 성공",
  "data": {
    "accessToken": "<jwt>",
    "refreshToken": "<refresh-token>",
    "expiresAt": "2026-05-20T19:00:00+09:00"
  }
}
```

- FE 는 `accessToken` 을 보관해 이후 요청에 `Authorization: Bearer` 로 전송한다.

**Response (실패)** — `state` 불일치는 `400`(`errorCode: INVALID_REQUEST`), `code` 무효·Confluence 오류는 `401`(`errorCode: UNAUTHORIZED`), **`mode=admin` 인데 `users.role != ADMIN`** 은 `403`(`errorCode: FORBIDDEN`, message: "관리자 권한이 없는 계정입니다"). 모든 실패 케이스에서 토큰을 발급하지 않는다.

### `POST /api/auth/refresh`

세션 JWT 만료(임박) 시 refresh token 으로 새 access JWT 를 발급받는다. refresh token 은 요청 Body 로 전달한다. **Rotating**: 갱신 시 새 refresh token 이 발급되고 이전 것은 무효화된다(`backend/auth-server/current-plans.md` AUTH-03).

**Request Body**

```json
{ "refreshToken": "<refresh-token>" }
```

**Response (성공 200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "세션 갱신 성공",
  "data": {
    "accessToken": "<new-jwt>",
    "refreshToken": "<new-refresh-token>",
    "expiresAt": "2026-05-20T19:00:00+09:00"
  }
}
```

**Response (실패 401)** — Refresh Token 만료·무효 시 `401`(`errorCode: UNAUTHORIZED`). 재로그인이 필요하다.

### `POST /api/auth/logout`

FE 는 보관한 access/refresh 토큰을 폐기하고, BFF 는 Authorization Server 에 해당 refresh token 무효화를 요청한다. 요청은 `Authorization: Bearer {accessToken}` 로 식별한다.

**Response (200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "로그아웃 성공",
  "data": null
}
```

### `GET /api/users/me`

현재 로그인 사용자 정보를 조회한다. 미인증 시 `401`(`errorCode: UNAUTHORIZED`).

**Response (200)**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "사용자 정보 조회 성공",
  "data": {
    "userId": "user-001",
    "name": "이다연",
    "email": "dayeon@example.com",
    "role": "USER",
    "profileImageUrl": "https://...",
    "lastLoginAt": "2026-05-20T18:00:00+09:00"
  }
}
```

- `role`: `"USER"` | `"ADMIN"`. JWT `role` claim 과 응답 `role` 모두 MySQL `users.role` 컬럼에서 발급(단일 source). 최초 admin 은 마이그레이션 스크립트로 시드 — `docs/db-schema.md` §6.1·`backend/auth-server/current-plans.md` Feature A 참조.

## 4-2. 관리자 대시보드 (6주차)

| API                       | 설명                                                |
| ------------------------- | --------------------------------------------------- |
| `GET /api/admin/stats`    | 일간 질의 수, 평균 응답 시간, 시간대별 접속 추이    |
| `GET /api/admin/users`    | 일일/전체 사용자 수, 사용자별 활동 요약             |
| `GET /api/admin/data`     | 스페이스/페이지 수, VectorDB 용량, 최종 동기화 일시 |
| `GET /api/admin/feedback` | 긍정/부정 비율, 부정 피드백 원문, 피드백 추이       |
| `GET /api/admin/sync`     | 동기화 이력 (성공/실패/소요 시간)                   |

> **공통 — 권한**: `/api/admin/*` 는 **ADMIN 역할 전용**이다. 미인증 시 `401`(`errorCode: UNAUTHORIZED`), 일반 사용자(USER) 접근 시 `403`(`errorCode: FORBIDDEN`). 모든 응답은 공통 Wrapper 를 적용한다. 항목별 데이터 소스는 `backend/rules/domains.md` §4 참조(인원·사용추이·피드백=MySQL, 데이터=MongoDB 읽기). 기획서 §6.7 의 관리 항목(인원/데이터/사용추이/피드백)에 대응한다.

**공통 Query Parameter (제안 — 6주차 확정)**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `period` | string | N | `daily` | 추이 집계 단위(`daily` / `hourly`) — `stats` / `feedback` |
| `from` / `to` | string (ISO-8601, KST) | N | 최근 7일 | 기간 필터 — `stats` / `feedback` / `sync` |
| `page` / `size` | int | N | `0` / `20` | 목록 페이지네이션 — `users` / `feedback` / `sync` |

> 위 파라미터·기본값은 **제안**이며 6주차 구현 시 확정한다.

**`GET /api/admin/stats` Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "서비스 통계 조회 성공",
  "data": {
    "dailyQueryCount": 142,
    "avgResponseTime": 3.2,
    "totalConversations": 856,
    "hourlyAccessTrend": [
      { "hour": 9, "count": 23 },
      { "hour": 10, "count": 45 }
    ]
  }
}
```

**`GET /api/admin/users` Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "사용자 현황 조회 성공",
  "data": {
    "totalUsers": 48,
    "dailyActiveUsers": 12,
    "users": [
      {
        "userId": "user-001",
        "name": "이다연",
        "accessibleSpaceCount": 5,
        "accessiblePageCount": 320,
        "accessibleAttachmentCount": 48,
        "conversationCount": 35,
        "lastAccessAt": "2026-05-20T18:00:00+09:00"
      }
    ]
  }
}
```

- `users` 는 `page` / `size` 페이지네이션 대상이다. `accessibleSpaceCount` / `accessiblePageCount` / `accessibleAttachmentCount` 는 사용자가 접근 가능한(ACL) 스페이스·페이지·첨부 수(기획서 §6.7 인원 관리).

**`GET /api/admin/data` Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "데이터 현황 조회 성공",
  "data": {
    "totalSpaces": 5,
    "totalPages": 1230,
    "totalAttachments": 187,
    "vectorDbSize": "2.3 GB",
    "totalChunks": 8940,
    "lastSyncAt": "2026-05-20T17:00:00+09:00"
  }
}
```

**`GET /api/admin/feedback` Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "피드백 현황 조회 성공",
  "data": {
    "totalCount": 320,
    "likeCount": 256,
    "dislikeCount": 64,
    "positiveRatio": 0.8,
    "trend": [
      { "date": "2026-05-19", "likeCount": 40, "dislikeCount": 8 },
      { "date": "2026-05-20", "likeCount": 52, "dislikeCount": 11 }
    ],
    "negativeFeedbacks": [
      {
        "feedbackId": "fb-uuid-101",
        "messageId": "msg-uuid-200",
        "comment": "출처가 질문과 관련 없었어요",
        "question": "S3 권한 오류 원인이 뭐야?",
        "answer": "IAM 정책을 확인하세요...",
        "createdAt": "2026-05-20T18:30:00+09:00"
      }
    ],
    "page": 0,
    "size": 20
  }
}
```

- 집계는 `LIKE` / `DISLIKE` 기준(§1-3). `positiveRatio` = `likeCount / totalCount`. `trend` 는 `period` / `from` / `to` 로 집계 단위·범위를 조정한다.
- `negativeFeedbacks` 는 `DISLIKE` 원문 목록으로 `page` / `size` 페이지네이션한다. `question` / `answer` 는 QCA 추적(assistant `messageId` → 직전 `USER` 메시지, `backend/rules/domains.md` §2)으로 매핑한다.

**`GET /api/admin/sync` Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "동기화 이력 조회 성공",
  "data": {
    "syncHistory": [
      {
        "syncId": "sync-uuid-001",
        "status": "COMPLETED",
        "updatedPages": 12,
        "deletedPages": 1,
        "duration": 45,
        "completedAt": "2026-05-20T17:00:00+09:00"
      }
    ]
  }
}
```

## 4-3. Confluence 페이지 미리보기 (5주차 이후 — 인증 의존)

> 출처 hover preview 및 Chat 메인 추천 문서 preview에 사용한다.
> **선행 조건:** 3단계(Authorization Server)에서 OAuth Access Token이 MySQL에 암호화 저장된 이후에만 동작한다. 인증이 없는 중간발표(2단계) 범위에는 포함하지 않는다.

| 항목   | 내용                                                      |
| ------ | --------------------------------------------------------- |
| Method | `GET`                                                     |
| URL    | `/api/confluence/pages/preview`                           |
| 설명   | 출처 문서의 Confluence 페이지 HTML 본문 + 메타데이터 조회 |

**Query Parameter**
| Key | Type | Required | Description |
|-----|------|----------|-------------|
| pageId | string | ✅ | Confluence page ID |

**처리 방식**

- Frontend는 출처 목록 문서 hover 시 `pageId`를 담아 BFF에 요청한다.
- BFF는 서버에 저장된 OAuth 토큰으로 Confluence REST API를 호출한다.
- BFF는 Confluence 응답의 `body.view.value` HTML 문자열을 `bodyViewValue`로 변환해 반환한다.
- BFF는 Confluence 응답의 `space.name`, `ancestors[].title`, `title`을 조합해 `breadcrumbs`를 파생한다.
- Frontend는 `bodyViewValue`를 DOMPurify로 sanitize한 뒤 `v-html`로 렌더링한다.
- 원본 열기 아이콘 클릭 시 `pageUrl`을 새 탭에서 연다.

> **TBD (3단계에서 결정):** 저장된 OAuth 토큰으로 Confluence를 호출하는 주체 — (a) BFF가 토큰을 사용해 직접 호출 vs (b) Authorization Server 프록시. `backend/CLAUDE.md` §6(BFF는 Confluence 토큰을 직접 교환하지 않는다) / `docs/architecture.md`와 함께 3단계 착수 시 확정하고 본 절을 갱신한다.

**Response**

```json
{
  "isSuccess": true,
  "code": 200,
  "message": "Confluence 페이지 미리보기 조회 성공",
  "data": {
    "pageId": "12345",
    "title": "S3 트러블슈팅 가이드",
    "spaceName": "Cloud Control Center",
    "authorName": "Platform Team",
    "updatedAt": "2026-04-15T18:30:00+09:00",
    "breadcrumbs": ["Cloud Control Center", "AWS", "S3", "S3 트러블슈팅 가이드"],
    "pageUrl": "https://confluence.example.com/pages/12345",
    "bodyViewValue": "<h1>S3 트러블슈팅 가이드</h1><p>S3 권한 오류는...</p>"
  }
}
```

**Error Response**

```json
{
  "isSuccess": false,
  "code": 404,
  "errorCode": "RESOURCE_NOT_FOUND",
  "message": "Confluence 페이지 미리보기를 찾을 수 없습니다"
}
```

> **보안 주의:** BFF는 OAuth token을 Frontend에 노출하지 않는다. Frontend는 HTML 렌더링 전 반드시 sanitize한다.
