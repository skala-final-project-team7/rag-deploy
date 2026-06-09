# Agent 담당자 요청 — 문장별 출처 정밀도 개선 (FR-009)

- 작성자: 최태성 (RAG Pipeline + Storage)
- 작성일: 2026-05-21
- 대상: Answer Generation Agent 담당자 (`answer_generation_agent` 패키지 소유자)
- 관련: 요구사항정의서 v0.2.0 FR-009 / FR-010 / 환각 KPI, 설계서 421 §4.6
- 근거 세션: `docs/ai/working-log.md` feature17c-16 ~ 17c-20

---

## 1. 요청 한 줄 요약

생성기가 **여러 청크를 종합한 문장에 대해 인용을 첫 청크(`[#1]`) 하나로만 다는 문제**를
프롬프트에서 고쳐 주세요. 구체적으로 (a) 다중 청크 종합 문장은 근거가 된 **모든** context_id를
병기(`[1][2]`), (b) 인용 근거가 없는 도입/요약/연결 문장은 생성하지 않거나 명시적으로 제한
사항으로 분리. 이는 FR-009("문장별 출처 명시") 정합 개선이며, 현재 환각 KPI 목표(15%) 미달의
**유일하게 남은 실질 원인**입니다.

> **중요**: 이 문제는 우리 어댑터(`app/query/generator.py`) 또는 citation 매핑
> (`citation_mapping.py`)의 버그가 **아닙니다**. 아래 §3에서 진단으로 위치를 확정했습니다.
> 두 계층 모두 LLM이 emit한 문장별 인용을 **그대로 보존**하며, 검증기(FR-010)도 사양대로
> 올바르게 동작합니다. 근본 원인은 **LLM 출력 자체**(= 프롬프트가 단일 인용을 유도)입니다.

---

## 2. 왜 우리 영역에서 못 고치는가 (책임 경계)

| 계층 | 파일 | 현재 동작 | 판단 |
|------|------|-----------|------|
| 검증기 1·2단계 | `app/query/verifier*.py` | 문장별 (문장, 인용청크) 페어 검증. 미인용 문장 자동 UNSUPPORTED | FR-010 정합. **약화 금지** |
| citation 매핑 | `answer_generation_agent/.../citation_mapping.py` | LLM이 emit한 `citations` 배열을 그대로 보존(중복만 제거). 0개일 때만 단일 컨텍스트면 fallback | collapse 아님. 무수정 |
| 어댑터 합성 | `app/query/generator.py` `_compose_answer_with_citations` | 문장별 citations를 `[#N1][#N2]`로 충실히 렌더링 | 정상. 무수정 |
| **LLM 출력** | `answer_generation_agent/.../prompt_template.py` (프롬프트) | **종합 문장에도 단일 `[#1]`만 emit** | **← 여기가 원인** |

vendoring 무수정 원칙(우리 측 CLAUDE.md 절대 규칙)상 우리는 `answer_generation_agent`
프롬프트를 직접 수정할 수 없어, 담당자께 요청드립니다.

---

## 3. 진단 근거 (실측, 무료 진단 도구)

### 3.1 환각 KPI 현황 (per-cited-chunk = 사양 정합 정의)

요구사항 환각 정의 = "**인용 출처에 근거하지 않는** 답변 비율"(전체 검색 근거가 아니라 그 문장이
인용한 출처 기준). 최소 25% / 목표 15% / 도전 8%.

| 단계 | 환각 answerable | 환각 delivered(사용자 노출) | 차단 항목 |
|------|-----------------|------------------------------|-----------|
| baseline (citation off-by-one fix 전) | 38.7% | — | — |
| **현 baseline `evaluation_20260521_011314`** | **31.1%** (51/164) | **20.1%** (28/139) | 10 |

→ 최소(25%) 통과, **목표(15%) 미달**. 검증기 측 버그(인용 마커 off-by-one, feature17c-16)는
이미 우리가 고쳤고 38.7→31.1%로 기여. 남은 격차는 전적으로 아래 출처 정밀도 결함입니다.

### 3.2 잔존 NOT_SUPPORTED = 100% 출처 정밀도 아티팩트

post-fix delivered 항목 4건(EVAL S3/CI·CD/IAM/EKS)을 `--debug-verify`로 재진단:

- NOT_SUPPORTED 문장의 미확인 토큰 위치: **거의 전부 `in_other_topk`** (인용한 #1 청크 밖,
  그러나 검색된 다른 top-k 청크 안에 존재).
- 같은 문장들을 **전체 top-k 근거로 재평가**하면 **12문장 전부(12/12)가 SUPPORTED(score 1.0)로
  flip**, still_unsupported = 0.
- 결론: 잔존 환각은 진짜 날조도 검색 실패(recall 부재)도 아니라, **사실은 검색됐으나 생성기가
  엉뚱한(또는 일부) 청크만 인용**한 출처 정밀도 결함입니다.

### 3.3 대표 사례 (질의: "IAM 정책 변경 절차는 어떻게 진행되어야 하나요?")

생성기 답변(4단계 절차, 명백히 여러 청크 종합):

```
1) 변경 요청 — Jira 티켓 생성, Role/Policy ARN·사유·영향범위 명시. [#1]
2) 영향 분석 — IAM Access Analyzer, CloudTrail 30일 Action 분석. [#1]
3) 변경 적용 — Terraform 코드화, PR, 2인 승인. [#1]
4) 검증 — 변경 후 30분 CloudWatch, AccessDenied 확인. [#1]
```

- **4개 문장이 모두 `[#1]`만 인용.** 그러나 문장 1·4의 핵심 토큰(Role/Policy, ARN, 30분,
  CloudWatch)은 인용한 #1 청크엔 없고 **다른 top-k 청크에 존재**(`in_other_topk`).
- 검증기는 (사양대로) 문장 1·4를 NOT_SUPPORTED 처리 → 환각으로 집계.
- 만약 생성기가 각 단계의 근거 청크를 정확히 인용(`[#2]`, `[#3]` 등 병기)했다면 NOT_SUPPORTED가
  아니었을 것입니다 → 12/12 flip이 이를 증명.

---

## 4. 근본 원인 (프롬프트 분석)

`answer_generation_agent/generation/prompt_template.py`:

1. **system prompt** (`_build_system_prompt`, L175~186):
   - "모든 핵심 문장은 sentence-level citation을 포함해야 한다."
   - "citation은 반드시 제공된 context_id만 참조한다."
   - → 인용을 **요구**하지만, "한 문장이 **여러** 청크에 근거하면 **모두** 인용하라"는 지침이
     **없음**. 또 인용 없는 도입/요약 문장을 **억제**하라는 지침도 없음.

2. **출력 스키마 예시** (`_structured_output_instruction`, L198~210):
   ```json
   {"sentence_id": "s1", "text": "string", "citations": ["context_id"]}
   ```
   - → 예시의 `citations` 배열에 **context_id가 단 1개**. LLM이 이 예시를 따라 문장당 인용을
     **하나로 anchoring**하는 강한 유인. 다중 인용 예시 부재가 단일 인용 행동을 고착.

---

## 5. 요청 (구체 수정안)

아래는 제안이며, 최종 문구·구현은 담당자 재량입니다. **목표는 다중 청크 종합 문장의 모든 근거
인용 + 미근거 문장 억제**입니다.

### 5.1 system prompt에 다중 인용·억제 지침 추가 (`_build_system_prompt`)

추가 권장 문장(예):

```
- 한 문장이 여러 context에 근거하면, 근거가 된 모든 context_id를 인용한다 (예: [#1][#3]).
- 어떤 context로도 뒷받침되지 않는 문장(일반적 도입·요약·연결 문장 포함)은 생성하지 않거나,
  unsupported_gaps(제한 사항)로만 분리한다.
- 인용은 그 문장이 실제로 근거한 context_id만 가리킨다. 무관한 context를 채워 넣지 않는다.
```

### 5.2 출력 스키마 예시를 다중 인용으로 변경 (`_structured_output_instruction`)

```json
{
  "answer": "string",
  "sentences": [
    {"sentence_id": "s1", "text": "string", "citations": ["ctx-001", "ctx-003"]}
  ],
  "unsupported_gaps": ["context로 확인할 수 없는 제한 사항"]
}
```

→ 예시 배열에 **2개 이상**의 context_id를 보여 다중 인용을 정상 패턴으로 학습시킵니다.

> **★실측 업데이트 (2026-05-21, 중요)**: 우리 측에서 5.1·5.2(system prompt 다중 인용·억제
> 지침 + 스키마 예시 다중화)를 **직접 적용해 실측했으나 효과가 없었습니다**(Agent 담당자
> 1회 예외 승인, feature17c-22). few-shot 모범/안티패턴 예시까지 추가(17c-23)했으나 결과는
> 동일. **3종 프롬프트 개입 모두에서 GPT-4o 가 다중 인용을 emit 한 사례 0건**(debug-verify
> 3회 전부 문장당 `[#1]` 단일). full 50건 재평가에서 per-cited-chunk 환각도 개선 없음
> (answerable 31→31~35%, few-shot 은 차단만 13→17 증가시켜 delivered 가 낮아지는 착시 +
> 답변 품질 P@3·ROUGE-L 퇴행). → **프롬프트 텍스트로는 단일 인용 습관을 못 바꿈을 실증.**
> 변경은 무효로 **롤백**(17c-24)했고, 아래 5.3 을 **주(主) 권장**으로 승격합니다.

> **★구현 업데이트 (2026-05-22, feature17c-25)**: Agent 담당자가 환각 개선 권한을 RAG
> 담당자에게 위임함에 따라, 아래 §5.3 의 구조적 강제를 **transport 경계에서 OpenAI Structured
> Outputs(json_schema, strict)로 구현**했습니다(vendored 프롬프트·provider 무수정,
> `app/query/openai_transport.py` `GROUNDED_CITATION_RESPONSE_FORMAT`). `sentences[].citations`
> 를 문장마다 필수 배열로 강제하고 다중 인용을 schema description 으로 유도. opt-in 토글
> `RAG_GENERATOR_FORCE_CITATION_SCHEMA=true`. **한계**: strict json_schema 는 `minItems`
> 미지원이라 "≥1 인용"을 완전 강제하진 못함(빈 배열 valid). 효과는 §6 재평가로 확인 예정이며,
> 미흡 시 문장별 tool 호출 또는 생성-후 모델 기반 재귀속을 차선으로 검토.

### 5.3 ★주 권장★ Function Calling tools schema 로 citations 구조적 강제

설계서 §4.6.1 (D) 정합. 현재 agent 는 prompt instruction 으로만 JSON 출력을 요청하고
OpenAI `tools=`(structured output)를 설정하지 않습니다. 5.1·5.2 실측 실패로 볼 때, **인용
구조를 프롬프트 지침이 아니라 tools schema 로 강제**해야 합니다:

- `sentences[].citations` 를 `{"type": "array", "items": {"type": "string"}, "minItems": 1}`
  로 정의 → 미인용 문장 자체를 구조적으로 차단.
- 가능하면 "여러 근거 시 모든 context_id" 를 schema description 에 + tool 강제(`tool_choice`)
  로 모델이 인용 배열을 반드시 채우도록.
- 이는 agent provider 호출 경로(OpenAIAnswerLLMProvider / transport) 변경이라 **Agent
  담당자 영역**입니다. 우리 어댑터·검증기는 사양대로 유지.

대안(병행 가능): 생성 후 인용 정밀도를 후처리로 보정하는 것은 환각 정의(인용 출처 기준)상
위험(없는 인용을 추정해 채우면 오인용 은폐) → 권장하지 않음. 근본은 생성 단계 구조적 강제.

---

## 6. 검증 방법 (개선 후, 우리가 수행)

프롬프트 개선 적용 후, 우리 측에서 동일 평가 인프라로 재측정하겠습니다(비용 ≈ $0.5~2/회):

1. **단건 진단(무료)**: 위 IAM/S3/CI·CD/EKS 4건에 `--debug-verify`로 문장별 인용이 다중화
   됐는지, NOT_SUPPORTED가 줄었는지 확인.
2. **full 50건 재평가**: `python scripts/run_evaluation.py --use-real-adapters --rouge-l
   --bert-score` → `not_supported_ratio_answerable`(현 31.1%) /
   `not_supported_ratio_delivered`(현 20.1%) / `blocked_n_items`(현 10) 변화 측정.
3. **합격 기준**: per-cited-chunk delivered 환각 ≤ 15%(목표), 차단 항목 감소, Precision@3·
   ROUGE-L 비퇴행. 미달 시 추가 협의.

> 주의: 우리 검증기는 사양(FR-010)대로 유지하며 약화하지 않습니다. 즉 이 개선의 효과는
> "검증기를 느슨하게 해서"가 아니라 "생성기가 정확히 인용해서" 나와야 합니다. (참고: 전체
> top-k grounding 토글 `verifier_full_context_grounding`은 환각을 2%로 낮추지만 요구사항
> 환각 정의(인용 출처 기준)와 부정합 → 기본 OFF·내부 진단 전용으로 결론. feature17c-19/20.)

---

## 7. 참고 자료

- 진단 도구: `scripts/run_evaluation.py --debug-verify "<질의>" --use-real-adapters`
  (문장별 1단계 토큰·미확인토큰 위치 + 2단계 라벨 + 전체 top-k 재평가 flip).
- 저장된 진단 리포트: `reports/debug_verify_20260521_0136*.json`(IAM 등 4건 flip 12/12),
  `reports/evaluation_20260521_011314.json`(현 baseline).
- 설계 판단 상세: `docs/ai/working-log.md` feature17c-16 ~ 17c-20.
