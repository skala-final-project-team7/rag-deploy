"""OpenTelemetry tracing 초기화 [Pipeline].

작성자 : 이다연
담당 영역 : rag

--------------------------------------------------
작성목적 : FastAPI / httpx / SQLAlchemy / PyMongo 자동 계측을 한 곳에서 초기화한다.
          앱 코드는 ``initialize_tracing(app, settings)`` 만 호출하고, exporter endpoint
          / service name / enable 토글은 환경 변수 기반 Settings 로 제어한다.
작성일 : 2026-06-19
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - OpenTelemetry SDK 1.x / instrumentation 0.x beta 계열
--------------------------------------------------
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any, TypeVar

from fastapi import FastAPI

from app.config import Settings

_LOGGER = logging.getLogger(__name__)
_PROCESS_INSTRUMENTED = False
_TRACER_NAME = "lina-rag-pipeline"
_F = TypeVar("_F", bound=Callable[..., Any])


def initialize_tracing(app: FastAPI, settings: Settings) -> None:
    """RAG API 프로세스의 OpenTelemetry tracing 을 초기화한다.

    ``RAG_OTEL_ENABLED`` 가 false 이고 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 도 없으면 no-op.
    OTel 패키지가 런타임 이미지에 아직 없더라도 앱 부팅을 막지 않고 경고만 남긴다.
    """
    if not _is_enabled(settings):
        return

    try:
        _configure_sdk(settings)
        _instrument_process_once()
        _instrument_fastapi(app)
    except ImportError as err:
        _LOGGER.warning("OpenTelemetry 패키지가 없어 tracing 을 비활성화한다: %s", err)
    except Exception as err:
        _LOGGER.warning("OpenTelemetry tracing 초기화 실패: %s", err)


def _is_enabled(settings: Settings) -> bool:
    return settings.otel_enabled or bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _configure_sdk(settings: Settings) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or settings.otel_exporter_otlp_endpoint
    service_name = os.environ.get("OTEL_SERVICE_NAME") or settings.otel_service_name
    environment = _resource_attribute("deployment.environment") or settings.otel_environment
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            DEPLOYMENT_ENVIRONMENT: environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(tracer_provider)


def _instrument_process_once() -> None:
    global _PROCESS_INSTRUMENTED
    if _PROCESS_INSTRUMENTED:
        return

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    HTTPXClientInstrumentor().instrument()
    PymongoInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument()
    _PROCESS_INSTRUMENTED = True


def _instrument_fastapi(app: FastAPI) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if getattr(app.state, "otel_instrumented", False):
        return
    FastAPIInstrumentor.instrument_app(app)
    app.state.otel_instrumented = True


def traced_callable(span_name: str, func: _F) -> _F:
    """callable 실행 시간을 현재 trace 의 child span 으로 기록한다."""

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        state_before = args[0] if args else None
        with start_span(span_name, _state_attributes(state_before, prefix="before")) as span:
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _record_exception(span, exc)
                raise
            _set_span_attributes(span, _state_attributes(result, prefix="after"))
            return result

    _wrapped.__name__ = f"traced_{span_name.replace('.', '_')}"
    return _wrapped  # type: ignore[return-value]


@contextmanager
def start_span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
    """현재 trace 에 수동 span 을 추가한다. OTel API 부재 시 no-op 으로 동작한다."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(_TRACER_NAME)
        with tracer.start_as_current_span(name) as span:
            _set_span_attributes(span, attributes or {})
            yield span
    except ImportError:
        with nullcontext() as span:
            yield span


def _set_span_attributes(span: Any, attributes: Mapping[str, Any]) -> None:
    set_attribute = getattr(span, "set_attribute", None)
    if set_attribute is None:
        return
    for key, value in attributes.items():
        if value is not None:
            set_attribute(key, value)


def _record_exception(span: Any, exc: Exception) -> None:
    record_exception = getattr(span, "record_exception", None)
    if record_exception is not None:
        record_exception(exc)
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    except Exception:  # noqa: BLE001 — span status 기록 실패는 원 예외를 가리면 안 된다.
        return


def _state_attributes(state: Any, *, prefix: str) -> dict[str, Any]:
    if state is None:
        return {}
    attributes: dict[str, Any] = {}
    _maybe_set(attributes, f"rag.{prefix}.intent", _enum_value(getattr(state, "intent", None)))
    _maybe_set(attributes, f"rag.{prefix}.used_llm", _enum_value(getattr(state, "used_llm", None)))
    _maybe_set(
        attributes,
        f"rag.{prefix}.candidate_count",
        _len(getattr(state, "candidates", None)),
    )
    _maybe_set(
        attributes,
        f"rag.{prefix}.top_chunk_count",
        _len(getattr(state, "top_chunks", None)),
    )
    _maybe_set(attributes, f"rag.{prefix}.source_count", _len(getattr(state, "sources", None)))
    _maybe_set(
        attributes,
        f"rag.{prefix}.rewritten_query_count",
        _len(getattr(state, "rewritten_queries", None)),
    )
    _maybe_set(
        attributes,
        f"rag.{prefix}.verification_count",
        _len(getattr(state, "verification", None)),
    )
    conversation_id = getattr(state, "conversation_id", None)
    if conversation_id:
        attributes[f"rag.{prefix}.conversation_id"] = str(conversation_id)
    return attributes


def _maybe_set(attributes: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        attributes[key] = value


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _len(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return len(value)
    except TypeError:
        return None


def _resource_attribute(name: str) -> str | None:
    raw = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip() == name:
            return value.strip() or None
    return None


__all__: list[str] = ["initialize_tracing", "start_span", "traced_callable"]
