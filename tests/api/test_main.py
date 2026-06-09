"""FastAPI 앱 entrypoint 회귀 — /metrics + /healthz 노출 검증.

--------------------------------------------------
작성자 : 최태성
작성목적 : feature12 (PDF 0518_RAG.pdf #4) — Prometheus instrumentator wiring
          회귀 보호. ``Instrumentator().instrument(app).expose(app,
          endpoint="/metrics", include_in_schema=False)`` 가 ``/metrics``
          엔드포인트에서 Prometheus text format 응답을 200 으로 반환하는지,
          기존 ``/healthz`` 동작이 영향 받지 않는지 확인한다.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, feature12 — /metrics 회귀 + OpenAPI 스키마 제외 검증
--------------------------------------------------
"""

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.main import create_app
from app.api.routes import get_graph


def _client_without_graph() -> httpx.AsyncClient:
    """lifespan 우회 + 그래프 없는 ASGITransport 클라이언트.

    ``/metrics`` 와 ``/healthz`` 는 그래프 의존성을 사용하지 않으므로
    dependency override 도 빈 dummy 로 둔다.
    """
    app = create_app()

    def _no_graph() -> Any:
        # 본 테스트는 그래프를 호출하지 않는 엔드포인트만 검증한다.
        raise RuntimeError("graph should not be invoked in this test")

    app.dependency_overrides[get_graph] = _no_graph
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text() -> None:
    """``/metrics`` 가 Prometheus text format 응답을 200 으로 반환한다."""
    async with _client_without_graph() as client:
        # 일부 메트릭은 첫 요청 이후 채워지므로 사전 요청 1회 송신.
        await client.get("/healthz")
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    # prometheus_client 의 text exposition format 은 text/plain; version=0.0.4 형식.
    assert content_type.startswith("text/plain")
    # 표준 HTTP 메트릭이 적어도 1개 노출돼야 한다 — instrumentator 의 기본 metric 명.
    body = resp.text
    assert "http_requests_total" in body or "http_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_endpoint_excluded_from_openapi_schema() -> None:
    """``/metrics`` 는 ``include_in_schema=False`` 로 OpenAPI 스키마에서 제외된다."""
    async with _client_without_graph() as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    assert "/metrics" not in paths


@pytest.mark.asyncio
async def test_healthz_still_returns_ok_after_metrics_wiring() -> None:
    """``/healthz`` 는 instrumentator wiring 이후에도 정상 200 응답."""
    async with _client_without_graph() as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
