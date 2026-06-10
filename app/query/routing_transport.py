"""질의 라우터 OpenAI transport — system prompt 보강 + schema 강제 [Storage].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature17a (라우터 의도 분류 분석) 의 후속 fix — vendoring 한 ``query
          _routing_agent`` 의 기본 prompt 가 4종 의도 (incident_response /
          operations_guide / policy_procedure / history_lookup) 의 정의·구분
          기준을 LLM 에 알려주지 않아 GPT-4o-mini 가 4건 중 3건을 operations
          _guide 로 오분류하던 문제를 본 transport 보강으로 해결한다. ``OpenAI
          RoutingLLMProvider`` 에 본 transport 를 주입하면 vendoring 패키지의
          ``RoutingClassificationRequest.to_openai_payload`` 를 무시하고 본 모듈
          이 만든 messages 로 호출한다 — vendoring 코드 무수정 보존 + 라우팅
          정확도 보강.

          설계서 §4.4.2 (4종 의도 정의) + §4.4.3 (검색 친화적 쿼리 확장 3종) +
          §4.4.4 (의도별 메타필터 + Pool 가중치) + §4.4.5 (Function Calling 강제)
          정합으로 prompt 를 구성한다. 출력은 ``response_format={"type":"json
          _object"}`` 으로 강제하고 agent 의 ``parse_routing_llm_response`` 가
          그대로 파싱 가능한 schema 를 따른다.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, feature17a 후속 — 라우터 의도 분류 prompt 보강.
--------------------------------------------------
[호환성]
  - Python 3.11.x, openai>=1.30, query_routing_agent vendoring 패키지.
  - NOTE: 본 모듈은 [Storage] 분류 — 외부 OpenAI HTTP transport 어댑터. agent
          provider 의 ``_default_transport`` 를 대체하며 동일 시그니처
          (``Callable[[RoutingClassificationRequest], str]``) 를 따른다. 본
          transport 는 raw content string (JSON 직렬화된 분류 결과) 을 반환하므
          로 agent 의 후속 파싱(``parse_routing_llm_response``)은 그대로 동작.
--------------------------------------------------
"""

from __future__ import annotations

from collections.abc import Callable

from query_routing_agent.llm.providers import (
    OpenAITransportError,
    RoutingClassificationRequest,
)

# 설계서 §4.4.2 4종 의도 정의·예시·구분 기준. 한국어 질의 정합으로 한국어/영어
# 혼용 가이드를 둔다. GPT-4o-mini 가 라벨만 보고 추정하지 않도록 각 의도에 (1)
# 정의 (2) 대표 질의 예시 2~3건 (3) 구분 기준을 명시한다.
_SYSTEM_PROMPT = """\
당신은 사내 Confluence 기반 RAG 챗봇의 질의 라우터입니다. 사용자 질문을 분석해
4종 의도 중 하나로 분류하고, 검색 친화적 확장 쿼리·메타데이터 필터·신뢰도를
JSON 객체로 반환해야 합니다. **한국어 질의가 기본**이며, 영어가 섞여도 그대로
의미를 해석하세요.

# 4종 의도 정의 (정확히 이 4개 라벨 중 하나로 분류 — 다른 값 금지)

## 1. incident_response (장애 대응)
정의: 실제 발생한 시스템 장애·인시던트의 원인 파악·대응 절차·복구 방법을 묻는 질의.
대표 신호: "장애", "NotReady", "오류", "실패", "쓰로틀링", "타임아웃", "메모리 초과",
"왜 안 되나요", "복구 절차", "사고 보고", "RCA".
예시:
  - "EKS Worker Node가 NotReady 상태가 되었을 때 어떻게 대응해야 하나요?"
  - "Redis 메모리 초과로 세션 유실 사고는 어떻게 처리했나요?"
  - "Lambda Cold Start 로 인한 API 타임아웃 사례는?"

## 2. operations_guide (운영 가이드)
정의: 도입·설정·운영 절차, 도구 사용법, 일상 운영 시 단계별 작업 방법을 묻는 질의.
대표 신호: "설정", "도입", "단계", "어떻게 하나요" (장애 맥락 X), "운영", "배포",
"백업/복구 (장애 아닌 정상 절차)", "가이드", "매뉴얼".
예시:
  - "EKS 에 Karpenter 를 도입할 때 어떤 단계를 거쳐야 하나요?"
  - "ArgoCD 로 GitOps 배포를 운영할 때 주요 명령은 무엇인가요?"
  - "RDS PostgreSQL 의 백업과 복구 절차는?"

## 3. policy_procedure (정책 / 절차)
정의: 사내 규정·표준·승인 절차·컴플라이언스·네이밍 컨벤션 등 **준수해야 할 규칙**
을 묻는 질의.
대표 신호: "정책", "규정", "표준", "컨벤션", "절차 (변경/승인 등 행정)", "기준",
"네이밍", "감사", "보안 기준선", "RBAC".
예시:
  - "IAM 정책 변경 절차는 어떻게 진행되어야 하나요?"
  - "S3 버킷 네이밍 컨벤션과 운영 정책은?"
  - "사내 Secret 관리 가이드는?"

## 4. history_lookup (이력 조회)
정의: 과거 사건·결정·작업·리포트의 **이력·요약·시간순 정리**를 묻는 질의.
대표 신호: "지난", "이전", "최근", "분기/월별/연도별 리포트", "회의록", "ADR",
"변경 이력", "업그레이드 이력", "스프린트 회고", "킥오프".
예시:
  - "지난 분기 클라우드 비용 증가 원인은?"
  - "EKS 1.28 → 1.29 업그레이드 작업 이력은?"
  - "Vector DB 선정 ADR 내용은?"

# 구분 기준 (헷갈리기 쉬운 경계 케이스)

- 질문에 **"NotReady / 오류 / 실패 / 사고 / RCA / 쓰로틀링"** 같은 장애 시그널이
  있으면 → **incident_response** (operations_guide 가 아님).
- 질문이 **"지난 / 분기 / 회고 / 이력 / 리포트"** 같은 시간성 시그널을 포함하면
  → **history_lookup**.
- 질문이 **"정책 / 규정 / 표준 / 컨벤션 / 감사"** 같은 규범 시그널을 포함하면
  → **policy_procedure**.
- 위 3종에 해당하지 않고 단계별 작업·도입·운영 절차를 묻는 경우에만
  **operations_guide**.

# 의도별 메타필터 / Pool 가중치 힌트

각 의도에 매핑된 기본 메타필터 (labels) 와 Pool 가중치는 시스템이 후처리하므로
**본 응답에 별도 명시 불필요**. 다만 질문에서 **특정 space / label / 문서 유형**
이 명시되면 ``metadata_filters`` 에 채워주세요.

# 출력 schema (정확히 이 형식 — JSON object, 다른 키 추가 금지)

{
  "intent": "<incident_response | operations_guide | policy_procedure | history_lookup>",
  "confidence": <0.0 ~ 1.0 사이 부동소수>,
  "reason": "<왜 그 의도로 분류했는지 한국어 1~2 문장 근거>",
  "expanded_queries": [
    "<원본 질문>",
    "<검색 친화적 동의어/축약/영문혼용 확장 1>",
    "<검색 친화적 동의어/축약/영문혼용 확장 2>"
  ],
  "metadata_filters": {
    "space_keys": [],
    "labels": [],
    "document_types": [],
    "date_range": {"from": null, "to": null}
  }
}

# 출력 규칙

1. **반드시 valid JSON** 만 반환하세요. 코드 블록·주석·텍스트 prefix 금지.
2. ``expanded_queries`` 는 정확히 3개. 첫 항목은 원본 질문 그대로, 나머지 2개는
   검색 hit 률을 높이기 위한 동의어·축약·영문 혼용 표현.
3. ``confidence`` 는 분류 신뢰도. 4종 의도 정의가 명백히 맞으면 0.9 이상,
   경계 케이스면 0.7 이하.
4. ``metadata_filters`` 는 추출 가능한 항목만 채우고, 추정하지 마세요.
"""


def build_openai_routing_transport(
    *,
    api_key: str,
) -> Callable[[RoutingClassificationRequest], str]:
    """OpenAIRoutingLLMProvider 에 주입할 transport callable 을 생성한다.

    반환된 callable 은 ``RoutingClassificationRequest`` 를 받아 OpenAI Chat
    Completions API 를 호출하고 raw content string (JSON 직렬화된 분류 결과) 을
    반환한다. agent 의 ``parse_routing_llm_response`` 가 그대로 파싱 가능한
    schema 정합. agent 의 ``_default_transport`` 를 대체한다.

    Args:
        api_key: OpenAI API key (build_real_deps 에서 settings.openai_api_key
            로 명시 주입).

    Returns:
        Callable[[RoutingClassificationRequest], str] — provider 가 호출하는
        transport 함수. 내부적으로 ``openai`` 클라이언트를 lazy import 한다.
    """

    def _transport(request: RoutingClassificationRequest) -> str:
        # lazy import — openai 없는 환경 (PoC) 에서도 모듈 로드 가능.
        from openai import APIError, OpenAI, RateLimitError

        # 호출마다 생성하는 클라이언트는 finally 에서 close — 커넥션 풀 누수 방지
        # (openai_transport.py A7 정합. 배포 전 점검 2026-06-10 에 누락 보완).
        client = OpenAI(api_key=api_key, timeout=float(request.timeout_seconds))
        try:
            try:
                response = client.chat.completions.create(
                    model=request.model,
                    temperature=request.temperature,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        # agent 가 만든 사용자 prompt (history_decision / context_summary /
                        # entities / turn_refs 정보 포함) 를 그대로 user 메시지로 전달.
                        {"role": "user", "content": request.prompt},
                    ],
                    response_format={"type": "json_object"},
                )
            except RateLimitError as exc:
                # OpenAI RateLimitError 는 agent 의 OpenAITransportError(429) 로 매핑해
                # 상위 provider 가 routing fallback 으로 흡수하도록 한다.
                raise OpenAITransportError(429, "OpenAI rate limit") from exc
            except APIError as exc:
                status = getattr(exc, "status_code", None) or 500
                raise OpenAITransportError(status, "OpenAI API error") from exc
            try:
                return str(response.choices[0].message.content or "")
            except (AttributeError, IndexError, TypeError) as exc:
                raise OpenAITransportError(500, "OpenAI response schema error") from exc
        finally:
            client.close()

    return _transport


__all__ = ["build_openai_routing_transport"]
