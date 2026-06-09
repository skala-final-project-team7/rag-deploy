"""LLM 커스텀 Prometheus 메트릭 회귀 — feature17a.

app/metrics.py 의 Counter / Histogram 이 default registry 에 등록되어 inc /
observe 시 누적되는지 + generator / verifier / router hook 이 실제로 카운터를
증가시키는지 회귀 보호.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from prometheus_client import REGISTRY

from answer_generation_agent.config import AnswerGenerationConfig
from answer_generation_agent.generation.answer_generation import (
    AnswerProviderError,
    FakeAnswerLLMProvider,
)
from app.metrics import (
    answer_generation_latency_seconds,
    intent_classification_total,
    llm_fallback_total,
    verification_status_total,
)
from app.pipeline.nodes import verify_pipeline_node
from app.query.generator import manage_generator
from app.query.router import manage_router
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import DocType, Intent, SourceType
from app.schemas.rag_state import RagState
from app.schemas.response import Verification


def _sample_value(counter: Any, **labels: str) -> float:
    """default REGISTRY 에서 특정 라벨 조합의 카운터 값을 가져온다 (없으면 0.0).

    prometheus_client 의 Counter 는 내부적으로 ``_value._value`` 에 누적 — 라벨
    인스턴스가 없으면 0. ``labels(...)`` 호출이 멤버를 생성하므로 inc 전에는 sample
    이 0 이다. 본 헬퍼는 회귀 테스트에서 inc 전후 차분을 보장하기 위해 사용한다.
    """
    if not labels:
        # 라벨 없는 카운터 (없음 — 본 모듈은 모두 라벨 있음).
        return float(counter._value.get())  # type: ignore[attr-defined]
    metric = counter.labels(**labels)
    return float(metric._value.get())  # type: ignore[attr-defined]


def _make_chunk() -> Chunk:
    metadata = ChunkMetadata(
        chunk_id="chunk-1",
        page_id="page-1",
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="개요",
        chunk_index=0,
        labels=["eks"],
        doc_type=DocType.OPERATION,
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="https://confluence.example.com/page-1",
        last_modified=datetime(2026, 5, 1, 9, 0, 0),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text="EKS Worker Node 장애 대응 절차.", metadata=metadata)


# --- 메트릭 자체 동작 ---


def test_llm_fallback_total_increments_on_inc() -> None:
    """라벨 별 카운터가 inc 호출 시 정확히 1 증가한다."""
    before = _sample_value(
        llm_fallback_total, from_model="gpt-4o", to_model="gpt-4o-mini", reason="rate_limit_error"
    )
    llm_fallback_total.labels(
        from_model="gpt-4o", to_model="gpt-4o-mini", reason="rate_limit_error"
    ).inc()
    after = _sample_value(
        llm_fallback_total, from_model="gpt-4o", to_model="gpt-4o-mini", reason="rate_limit_error"
    )
    assert after - before == pytest.approx(1.0)


def test_verification_status_total_increments_by_status() -> None:
    """status 라벨 별로 카운터가 분리되어 누적된다."""
    before_pass = _sample_value(verification_status_total, status="PASS")
    before_ns = _sample_value(verification_status_total, status="NOT_SUPPORTED")
    verification_status_total.labels(status="PASS").inc()
    verification_status_total.labels(status="PASS").inc()
    verification_status_total.labels(status="NOT_SUPPORTED").inc()
    assert _sample_value(verification_status_total, status="PASS") - before_pass == pytest.approx(
        2.0
    )
    assert _sample_value(
        verification_status_total, status="NOT_SUPPORTED"
    ) - before_ns == pytest.approx(1.0)


def test_answer_generation_latency_seconds_observes() -> None:
    """histogram observe 호출 시 count 가 증가한다."""
    before_count = answer_generation_latency_seconds._sum.get()  # type: ignore[attr-defined]
    answer_generation_latency_seconds.observe(0.5)
    answer_generation_latency_seconds.observe(2.5)
    after_count = answer_generation_latency_seconds._sum.get()  # type: ignore[attr-defined]
    # _sum 누적 — 두 observe 값의 합 (0.5 + 2.5 = 3.0) 만큼 증가.
    assert after_count - before_count == pytest.approx(3.0)


def test_metrics_registered_in_default_registry() -> None:
    """4종 메트릭이 default CollectorRegistry 에 등록되어 /metrics 노출 대상이 된다.

    REGISTRY.collect() 가 process collector (GCCollector 등) + 본 모듈의 사용자
    정의 메트릭을 모두 yield 하므로 metric name 집합을 모은 뒤 본 모듈의 4종이
    포함되는지 확인한다. Counter 는 prometheus_client 0.20+ 에서 metric.name 에
    ``_total`` 접미사가 포함되지 않으므로 두 표기 모두 허용한다.
    """
    metric_names = {metric.name for metric in REGISTRY.collect()}
    assert "llm_fallback" in metric_names or "llm_fallback_total" in metric_names
    assert "verification_status" in metric_names or "verification_status_total" in metric_names
    assert "answer_generation_latency_seconds" in metric_names
    assert "intent_classification" in metric_names or "intent_classification_total" in metric_names


# --- hook 동작 ---


def test_generator_rate_limit_fallback_increments_llm_fallback_total() -> None:
    """manage_generator 의 rate_limit_error 분기에서 카운터 +1."""

    class _SequencedProvider:
        provider_name = "sequenced_fake"

        def __init__(self) -> None:
            self._n = 0
            self._success = FakeAnswerLLMProvider(
                response={
                    "answer": "장애 대응 절차를 안내합니다.",
                    "sentences": [{"text": "장애 대응 절차를 안내합니다.", "citations": []}],
                    "unsupported_gaps": [],
                }
            )

        def has_credentials(self) -> bool:
            return True

        def generate_answer(self, request: Any) -> Any:
            self._n += 1
            if self._n == 1:
                raise AnswerProviderError(
                    message="rate limit", retryable=True, error_type="rate_limit_error"
                )
            return self._success.generate_answer(request)

    before = _sample_value(
        llm_fallback_total,
        from_model="gpt-4o",
        to_model="gpt-4o-mini",
        reason="rate_limit_error",
    )
    state = RagState(
        query="EKS 절차",
        user_id="u",
        conversation_id="c-1",
        intent=Intent.OPERATION_GUIDE,
        rewritten_queries=["EKS 절차"],
        top_chunks=[_make_chunk()],
    )
    manage_generator(
        state,
        provider=_SequencedProvider(),
        generation_config=AnswerGenerationConfig(model="gpt-4o", fallback_model="gpt-4o-mini"),
    )
    after = _sample_value(
        llm_fallback_total,
        from_model="gpt-4o",
        to_model="gpt-4o-mini",
        reason="rate_limit_error",
    )
    assert after - before == pytest.approx(1.0)


def test_verify_pipeline_node_increments_verification_status_total() -> None:
    """verify_pipeline_node 가 verification 결과 분포만큼 카운터를 누적한다."""
    before_pass = _sample_value(verification_status_total, status="PASS")

    state = RagState(
        query="q",
        user_id="u",
        conversation_id="c",
        top_chunks=[_make_chunk()],
        answer="단순 답변입니다.",  # 검증 토큰이 없어 모두 PASS.
    )

    def _no_op_evaluator(**_kwargs: Any) -> list[Verification]:
        return []

    verify_pipeline_node(state, llm_evaluator=_no_op_evaluator)
    assert state.verification  # 1 문장 PASS.
    after_pass = _sample_value(verification_status_total, status="PASS")
    assert after_pass > before_pass


def test_manage_router_increments_intent_classification_total() -> None:
    """manage_router fallback 분기 (conversation_id 없음) 에서 intent='fallback' 카운터 +1."""
    before = _sample_value(intent_classification_total, intent="fallback")
    state = RagState(
        query="아무 질문",
        user_id="u",
        conversation_id=None,  # _apply_fallback 트리거.
        groups=["space:CLOUD"],
    )
    manage_router(state)
    after = _sample_value(intent_classification_total, intent="fallback")
    assert after - before == pytest.approx(1.0)
