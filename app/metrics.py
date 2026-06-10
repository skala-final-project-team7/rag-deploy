"""LLM 커스텀 Prometheus 메트릭 — RAG Pipeline 운영 관측 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature17a — 설계서 §6.4 KPI 표 (환각 비율 / 응답 시간 P95 / Precision@3
          / 사용자 만족도) 와 feature16 smoke 에서 발견된 운영 관측 빈틈 (라우터
          의도 분류 분포 / 답변 생성기 fallback 빈도) 을 Prometheus 메트릭으로
          가시화한다. prometheus_client 의 default registry 를 사용해 ``app/api/
          main.py`` 의 instrumentator 가 ``/metrics`` 로 자동 노출한다 (별도 wiring
          불필요).
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, feature17a — 4종 LLM 커스텀 메트릭 정의:
    llm_fallback_total / verification_status_total /
    answer_generation_latency_seconds / intent_classification_total.
  - 2026-06-10, 코드 리뷰 재점검(A6) — verifier_provider_failure_total 카운터 추가
    (검증 2단계 provider 실패 fail-open 관측 지점).
--------------------------------------------------
[호환성]
  - Python 3.11.x, prometheus_client>=0.20 (prometheus-fastapi-instrumentator 의 transitive).
  - NOTE: 본 모듈은 [Pipeline] 분류 — 결정론적 메트릭 누적. 모든 메트릭은
          module-level 단일 인스턴스 (process singleton) 로 보유되며 default
          CollectorRegistry 에 자동 등록된다.
  - histogram bucket: 설계서 §6.4 KPI 임계 (5초 P95) 가시화 정합으로 0.1/0.25/
          0.5/1.0/2.5/5.0/10.0/30.0/60.0/+Inf 를 사용한다 (app/api/main.py 의
          HTTP latency histogram 과 동일 경계).
--------------------------------------------------
"""

from prometheus_client import Counter, Histogram

# 답변 생성기 latency 히스토그램의 bucket — 설계서 §6.4 KPI 5초/30초 임계 정합.
# (app/api/main.py 의 HTTP latency_highr_buckets 와 동일 — Prometheus 쿼리 일관성.)
_ANSWER_LATENCY_BUCKETS: tuple[float, ...] = (
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    float("inf"),
)

# --- 메트릭 정의 ---
# 모든 메트릭은 default CollectorRegistry 에 자동 등록 — Instrumentator 가 /metrics
# 노출 시 함께 scrape 된다 (app/api/main.py 의 Instrumentator.expose).

# 답변 생성기 모델 다운그레이드 (Rate Limit fallback) 카운터.
# - from_model: 다운그레이드 전 모델 (예: gpt-4o)
# - to_model:   다운그레이드 후 모델 (예: gpt-4o-mini)
# - reason:     다운그레이드 트리거 (rate_limit_error / server_error / ...)
# 운영 시 Prometheus 쿼리 ``increase(llm_fallback_total[5m])`` 로 Rate Limit 빈도 관측.
llm_fallback_total: Counter = Counter(
    "llm_fallback_total",
    "Answer generator fallback to fallback_model (e.g. GPT-4o-mini) due to LLM error",
    labelnames=("from_model", "to_model", "reason"),
)

# 답변 검증 결과 분포 카운터 — 설계서 §6.4 환각 비율 (NOT_SUPPORTED) KPI 관측 지점.
# - status: PASS / SUPPORTED / NOT_SUPPORTED (VerificationStatus enum 3종).
# 운영 쿼리 ``sum(rate(verification_status_total{status="NOT_SUPPORTED"}[5m])) /
#           sum(rate(verification_status_total[5m]))`` 로 환각 비율 산출.
verification_status_total: Counter = Counter(
    "verification_status_total",
    "Answer verification results by sentence-level status",
    labelnames=("status",),
)

# 답변 생성기 자체 latency 히스토그램 — HTTP latency 와 별개로 답변 생성 단계만 측정.
# 설계서 §6.4 KPI "응답 시간 P95 5초" 의 답변 생성 기여분을 분리 관측한다.
# Prometheus 쿼리: ``histogram_quantile(0.95, rate(answer_generation_latency_seconds
# _bucket[5m]))``.
answer_generation_latency_seconds: Histogram = Histogram(
    "answer_generation_latency_seconds",
    "Answer generator latency from prompt build to final token in seconds",
    buckets=_ANSWER_LATENCY_BUCKETS,
)

# 라우터 의도 분류 분포 카운터 — feature16 smoke 발견 #2 (4종 질의 모두 운영가이드
# 분류) 의 원인 분석 + 설계서 §6.1 의도 분류 정확도 90% 임계 관측을 위해 도입.
# - intent: Intent enum 값 (장애대응 / 운영가이드 / 정책절차 / 이력조회) 또는
#           "fallback" (라우터 안전 분기로 떨어진 경우).
# 운영 쿼리 ``sum by (intent) (rate(intent_classification_total[15m]))`` 로 분포 관측.
intent_classification_total: Counter = Counter(
    "intent_classification_total",
    "Routing intent classifications by intent label (or 'fallback' for safe branch)",
    labelnames=("intent",),
)

# 검증 2단계 LLM provider 호출 실패 카운터 — 코드 리뷰 A6. provider 실패 시 해당
# 의심 문장은 stub 정합 정책으로 SUPPORTED fallback 되므로(verifier_evaluator),
# 환각 차단이 조용히 비활성화되는 빈도를 반드시 관측해야 한다.
# 운영 알림 권장: ``increase(verifier_provider_failure_total[5m]) > 0``.
verifier_provider_failure_total: Counter = Counter(
    "verifier_provider_failure_total",
    "Verification stage-2 evaluator provider failures (sentence fell back to SUPPORTED)",
)


__all__ = [
    "answer_generation_latency_seconds",
    "intent_classification_total",
    "llm_fallback_total",
    "verification_status_total",
    "verifier_provider_failure_total",
]
