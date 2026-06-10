"""POST /ml/query — httpx ASGITransport 통합 테스트.

본 테스트는 FastAPI 라우트가 (1) SSE 이벤트 5종 시퀀스(token/sources/verification/meta/done)를
정확히 송신하고, (2) 표준 분기 응답 / 예외 → SSE error 이벤트 매핑을 api-spec.md(BE 통합
스펙 /ml/query) 정합으로 처리하는지를 in-process httpx 클라이언트로 검증한다. 외부
컨테이너·모델 없이 동작 — :memory: Qdrant + Fake everything + samples 자동 인덱싱 기본값을
활용한다.

feature13 마이그레이션 정합 (api-spec v2.2.0):
  - 엔드포인트 ``/ml/query`` (구 ``/api/v1/rag/query`` 대체).
  - 요청 본문 ``question``/``userId``/``groups``/``conversationId``/``history``/``stream``
    (구 ``query``/``jwt`` 대체 — JWT 미수신, userId/groups 직접 전달). 명세 v2.4.0 정합:
    ``spaceKey`` 제거, ``stream`` 기본 False(BFF는 항상 true), ``history[].role`` lowercase.
  - SSE: ``token``=``{"content": ...}``, ``sources``=``{"sources": [...]}``(sourceUpdatedAt),
    ``verification``=집계 ``{"confidenceScore", "verificationResult"}``(검색 0건이면 생략),
    ``meta``(현재 구현 호환용 — intent/used_llm/feedback_enabled/latency_ms + title),
    ``done``=``{}``.

2026-06-10 코드 리뷰 재점검(A14·A15) 정합:
  - 비-streaming 경로도 ``status`` 4종(connecting/acl_filtering/searching/formatting)을
    송신한다(스펙 §1-1 불변식 #1) — connecting~searching 은 run_query 직전, formatting 직후.
  - SSE ``error.message`` 는 내부 예외 원문이 아니라 errorCode 별 고정 안내 문구다
    (내부 상세는 서버 로그 전용 — ``_classify_ml_error`` 가 ML_* 3종으로 분류).
"""

import json
import warnings
from datetime import datetime
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.errors import ErrorCode
from app.api.main import create_app
from app.api.routes import _classify_ml_error, get_graph
from app.config import Settings
from app.ingestion.embedder.base import FakeDenseEmbedder, FakeSparseEmbedder
from app.ingestion.indexer import index_chunks
from app.pipeline.query_graph import QueryGraphDeps, build_query_graph
from app.query.reranker.base import FakeCrossEncoderReranker
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import SourceType
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_client import QdrantPoolStore

warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")


# --- 요청 본문 / SSE 파싱 헬퍼 ---


def _body(
    *,
    question: str = "alpha",
    user_id: str = "taesung",
    groups: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """``POST /ml/query`` 요청 본문(BE 통합 스펙)을 만든다.

    JWT 미수신 — BFF가 추출한 ``userId``/``groups``를 직접 전달한다. 기본 groups 는
    인덱싱된 청크의 ``allowed_groups``(``space:CLOUD``)와 일치시켜 ACL 통과시킨다.
    """
    payload: dict[str, Any] = {
        "question": question,
        "userId": user_id,
        "groups": groups if groups is not None else ["space:CLOUD"],
    }
    payload.update(extra)
    return payload


def _content(token_data: str) -> str:
    """token 이벤트 data(``{"content": ...}`` JSON) → content 문자열."""
    return json.loads(token_data)["content"]


# --- 테스트용 그래프 픽스처 (lifespan 우회 + 작은 인메모리 데이터) ---


def _chunk(*, chunk_id: str, text: str = "alpha bravo charlie") -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id="P1",
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=0,
        labels=["eks"],
        doc_type="operation",
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _build_test_graph(*, indexed: bool = True) -> Any:
    """`:memory:` Qdrant + Fake everything 으로 컴파일된 테스트용 그래프를 만든다."""
    settings = Settings(_env_file=None)
    dense = FakeDenseEmbedder(dimension=8)
    sparse = FakeSparseEmbedder()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    if indexed:
        index_chunks(
            [
                _chunk(chunk_id="a" * 40, text="alpha bravo charlie"),
                _chunk(chunk_id="b" * 40, text="bravo delta echo"),
            ],
            version_by_page_id={"P1": 1},
            dense_embedder=dense,
            sparse_embedder=sparse,
            store=store,
            cache=FakeEmbeddingCache(),
        )
    deps = QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=FakeCrossEncoderReranker(),
    )
    return build_query_graph(deps)


@pytest.fixture()
def populated_graph() -> Any:
    return _build_test_graph(indexed=True)


@pytest.fixture()
def empty_graph() -> Any:
    return _build_test_graph(indexed=False)


def _client(graph: Any) -> httpx.AsyncClient:
    """lifespan을 우회한 ASGITransport 클라이언트.

    lifespan을 끄려면 ``transport`` 의 ``lifespan="off"`` 옵션을 활용한다.
    그래프는 dependency override로 직접 주입.
    """
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# --- 헬스 ---


@pytest.mark.asyncio
async def test_healthz_returns_ok(populated_graph: Any) -> None:
    async with _client(populated_graph) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ml_rag_health_returns_up(populated_graph: Any) -> None:
    """api-spec v2.2.0 §2-4-1 — GET /ml/rag/health → {"status": "UP"}."""
    async with _client(populated_graph) as client:
        resp = await client.get("/ml/rag/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "UP"}


# --- 정상 흐름 ---


def _parse_sse(body: str) -> list[tuple[str, str]]:
    """SSE 본문에서 (event, data) 튜플 시퀀스를 추출한다."""
    events: list[tuple[str, str]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data.append(line[len("data:") :].strip())
        elif line.strip() == "" and current_event is not None:
            events.append((current_event, "\n".join(current_data)))
            current_event = None
            current_data = []
    if current_event is not None:
        events.append((current_event, "\n".join(current_data)))
    return events


@pytest.mark.asyncio
async def test_query_route_emits_full_sse_sequence(populated_graph: Any) -> None:
    """정상 흐름: status 4종 + token → sources → verification → meta → done 시퀀스.

    코드 리뷰 A14 — 비-streaming 경로도 ``status`` 4종을 송신한다(스펙 §1-1 불변식 #1).
    connecting/acl_filtering/searching 은 run_query 직전, formatting 은 직후에 와서
    모든 status 가 token 보다 앞선다. 핵심 5종 시퀀스는 status 제외 시 종전과 동일.
    """
    async with _client(populated_graph) as client:
        resp = await client.post("/ml/query", json=_body())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    event_names = [name for name, _ in events]
    assert event_names == [
        "status",
        "status",
        "status",
        "status",
        "token",
        "sources",
        "verification",
        "meta",
        "done",
    ]
    assert _status_phases(events) == ["connecting", "acl_filtering", "searching", "formatting"]

    # token 페이로드는 {"content": ...} JSON.
    assert isinstance(_content(dict(events)["token"]), str)

    # sources 페이로드는 {"sources": [...]} 래핑 — relevanceScore 0~1.
    sources = json.loads(dict(events)["sources"])["sources"]
    assert isinstance(sources, list)
    assert all(0 <= source["relevanceScore"] <= 1 for source in sources)
    # BE sources 항목 필드(api-spec v2.2.0 정합 — sourceUpdatedAt).
    for source in sources:
        assert {
            "title",
            "pageId",
            "spaceId",
            "spaceName",
            "url",
            "sourceUpdatedAt",
        } <= source.keys()

    # verification 페이로드는 집계 {"confidenceScore", "verificationResult"}.
    verification = json.loads(dict(events)["verification"])
    assert 0 <= verification["confidenceScore"] <= 1
    assert verification["verificationResult"] in {
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "NOT_SUPPORTED",
    }

    # meta 페이로드 — 현재 구현 호환용(intent/used_llm/feedback_enabled/latency_ms + title).
    meta = json.loads(dict(events)["meta"])
    assert meta["intent"] == "운영가이드"
    assert meta["used_llm"] == "gpt-4o"
    assert isinstance(meta["feedback_enabled"], bool)
    assert meta["latency_ms"] >= 0
    # api-spec §1-1 meta.title — 답변 산출 후 채워진다(PoC 는 질문 기반 fallback).
    assert isinstance(meta["title"], str)
    assert meta["title"]

    # done 은 빈 객체 {} (messageId 는 BFF 주입).
    assert json.loads(dict(events)["done"]) == {}


# --- RETRIEVAL_EMPTY 표준 분기 ---


@pytest.mark.asyncio
async def test_query_route_retrieval_empty_returns_standard_message(
    empty_graph: Any,
) -> None:
    """청크 0건이면 200 SSE 정상 응답으로 표준 메시지를 송신한다 (api-spec.md 분기)."""
    async with _client(empty_graph) as client:
        resp = await client.post("/ml/query", json=_body(question="anything"))
    assert resp.status_code == 200
    events = dict(_parse_sse(resp.text))
    assert "권한 범위" in _content(events["token"])
    assert json.loads(events["sources"])["sources"] == []
    # api-spec §1-1 "0건 처리" — 검색 0건이면 verification 이벤트는 생략된다(검증 근거 없음).
    assert "verification" not in events
    # meta.title 은 0건에도 채워진다(질문 기반 fallback).
    assert json.loads(events["meta"]).get("title")


# --- 요청 본문 검증 ---


@pytest.mark.asyncio
async def test_query_route_missing_required_fields_returns_422(
    populated_graph: Any,
) -> None:
    """question 필드 누락 → FastAPI 기본 422 (Pydantic 검증)."""
    async with _client(populated_graph) as client:
        resp = await client.post("/ml/query", json={"userId": "taesung"})
    assert resp.status_code == 422


# --- ACL 매칭 0건 → RETRIEVAL_EMPTY ---


@pytest.mark.asyncio
async def test_query_route_acl_mismatch_yields_empty_retrieval(
    populated_graph: Any,
) -> None:
    """groups가 인덱싱된 청크의 allowed_groups와 일치하지 않으면 표준 메시지."""
    async with _client(populated_graph) as client:
        resp = await client.post("/ml/query", json=_body(groups=["space:OTHER"]))
    assert resp.status_code == 200
    events = dict(_parse_sse(resp.text))
    assert "권한 범위" in _content(events["token"])
    assert json.loads(events["sources"])["sources"] == []


# --- feature14: SSE token streaming (stream=True) 분기 ---


@pytest.mark.asyncio
async def test_query_route_stream_true_falls_back_when_no_generator_provider(
    populated_graph: Any,
) -> None:
    """PoC 안전 fallback — stream=True 라도 deps.generator_provider 없으면 비-streaming.

    `_should_fallback_to_non_streaming` 회귀 — app.state.deps 가 미설정 / generator
    _provider None / generator_config None / settings.openai_api_key 빈 SecretStr 중
    하나라도 해당하면 stream=True 가 무시되고 기존 run_query 흐름으로 처리된다.
    """
    async with _client(populated_graph) as client:
        resp = await client.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    # fallback 흐름은 비-streaming 시퀀스 그대로 — status 축약 4종 + token 1회 + 후행 4종.
    event_names = [name for name, _ in events]
    assert [n for n in event_names if n != "status"] == [
        "token",
        "sources",
        "verification",
        "meta",
        "done",
    ]
    assert _status_phases(events) == ["connecting", "acl_filtering", "searching", "formatting"]


def _streaming_client(
    populated_graph: Any,
    *,
    monkeypatch: pytest.MonkeyPatch,
    streaming_tokens: list[str],
    indexed: bool = True,
) -> httpx.AsyncClient:
    """운영 streaming 분기 회귀용 클라이언트.

    app.state 에 streaming_graph / deps / settings 를 수동 채워 lifespan 우회하면서
    stream=True 분기로 진입할 수 있게 한다. ``stream_openai_answer`` 는 monkeypatch
    로 fake token generator 로 대체.

    ``indexed=False`` 면 청크를 인덱싱하지 않아 검색 0건(RETRIEVAL_EMPTY) 분기로
    진입한다 — feature19 status phase 단축 회귀에 사용.
    """
    from types import SimpleNamespace

    from app.api import routes as routes_module
    from app.pipeline.query_graph import build_query_graph_for_streaming

    # populated_graph 자체에는 streaming_graph 가 없으므로 동일 인메모리 deps 로
    # streaming graph 도 컴파일한다. 본 테스트는 token chunk 송신·검증 호출 흐름을
    # 검증하므로 deps 의 generator_provider/config 는 sentinel 로 채워 분기만 활성화.
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    dense = FakeDenseEmbedder(dimension=8)
    sparse = FakeSparseEmbedder()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    if indexed:
        index_chunks(
            [
                _chunk(chunk_id="a" * 40, text="alpha bravo charlie"),
                _chunk(chunk_id="b" * 40, text="bravo delta echo"),
            ],
            version_by_page_id={"P1": 1},
            dense_embedder=dense,
            sparse_embedder=sparse,
            store=store,
            cache=FakeEmbeddingCache(),
        )
    deps = QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=FakeCrossEncoderReranker(),
        # generator_provider 가 None 이 아니어야 streaming 분기 활성. 본 fake 는
        # 실제로는 호출되지 않으며 stream_openai_answer monkeypatch 만 사용된다.
        generator_provider=object(),
        generator_config=SimpleNamespace(
            model="gpt-4o",
            fallback_model="gpt-4o-mini",  # feature15 — _streaming_event_stream 이 참조
            temperature=0.2,
            timeout_seconds=45,
        ),
    )
    streaming_graph = build_query_graph_for_streaming(deps)

    # streaming OpenAI 호출은 monkeypatch — fake token chunk 를 순차 yield.
    from app.query.openai_streaming import StreamingTokenChunk

    def _fake_stream_openai_answer(**_kwargs: Any) -> Any:
        for token in streaming_tokens:
            yield StreamingTokenChunk(text=token)

    monkeypatch.setattr(routes_module, "stream_openai_answer", _fake_stream_openai_answer)
    # meta.title 생성도 네트워크 없이 — titler 를 fake 로 monkeypatch(테스트 결정론).
    monkeypatch.setattr(routes_module, "generate_conversation_title", lambda **_kw: "테스트 제목")

    # settings.openai_api_key 가 빈 SecretStr 이면 fallback. 채워 둔다.
    from pydantic import SecretStr

    settings_with_key = settings.model_copy(update={"openai_api_key": SecretStr("sk-test")})

    app = create_app()
    # lifespan 우회한 채 app.state 를 수동 채움 — ASGITransport 가 lifespan 을 자동
    # 켜기 때문에 state 가 초기화될 가능성이 있어 dependency_overrides 와 함께 둔다.
    app.state.deps = deps
    app.state.settings = settings_with_key
    app.state.streaming_graph = streaming_graph
    app.state.graph = populated_graph  # stream=False 케이스 호환을 위해.
    app.dependency_overrides[get_graph] = lambda: populated_graph
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _streaming_client_with_stream_callable(
    populated_graph: Any,
    *,
    monkeypatch: pytest.MonkeyPatch,
    stream_callable: Any,
) -> httpx.AsyncClient:
    """feature15 streaming fallback 회귀용 — stream_openai_answer 를 임의 callable 로
    monkeypatch 한 라우트 클라이언트. _streaming_client 와 동일 패턴이지만 호출 측에서
    더 복잡한 분기를 검증할 수 있도록 monkeypatch 값을 외부에서 주입한다.
    """
    from types import SimpleNamespace

    from app.api import routes as routes_module
    from app.pipeline.query_graph import build_query_graph_for_streaming

    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    dense = FakeDenseEmbedder(dimension=8)
    sparse = FakeSparseEmbedder()
    store = QdrantPoolStore.in_memory(settings, dense_dimension=8)
    store.bootstrap_collections()
    index_chunks(
        [
            _chunk(chunk_id="a" * 40, text="alpha bravo charlie"),
            _chunk(chunk_id="b" * 40, text="bravo delta echo"),
        ],
        version_by_page_id={"P1": 1},
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        cache=FakeEmbeddingCache(),
    )
    deps = QueryGraphDeps(
        dense_embedder=dense,
        sparse_embedder=sparse,
        store=store,
        reranker=FakeCrossEncoderReranker(),
        generator_provider=object(),
        generator_config=SimpleNamespace(
            model="gpt-4o",
            fallback_model="gpt-4o-mini",
            temperature=0.2,
            timeout_seconds=45,
        ),
    )
    streaming_graph = build_query_graph_for_streaming(deps)

    monkeypatch.setattr(routes_module, "stream_openai_answer", stream_callable)
    # meta.title 생성도 네트워크 없이 — titler 를 fake 로 monkeypatch(테스트 결정론).
    monkeypatch.setattr(routes_module, "generate_conversation_title", lambda **_kw: "테스트 제목")

    from pydantic import SecretStr

    settings_with_key = settings.model_copy(update={"openai_api_key": SecretStr("sk-test")})

    app = create_app()
    app.state.deps = deps
    app.state.settings = settings_with_key
    app.state.streaming_graph = streaming_graph
    app.state.graph = populated_graph
    app.dependency_overrides[get_graph] = lambda: populated_graph
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_query_route_stream_true_rate_limit_falls_back_to_fallback_model(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """feature15 streaming Rate Limit fallback — 1차 RateLimitError → fallback_model 재시도.

    stream_openai_answer 첫 호출 (primary_model=gpt-4o) 에서 RateLimitError raise,
    두 번째 호출 (fallback_model=gpt-4o-mini) 에서 정상 token chunk yield → 라우트가
    정상 SSE 응답 송신. used_llm 다운그레이드는 내부 메트릭으로만 관측되므로(meta 제거)
    fallback 호출 자체(call_count==2 + model 인자)로 검증한다.
    """
    # openai.RateLimitError 생성 — sentinel response/body 만 채우고 status_code=429.
    from openai import RateLimitError

    from app.query.openai_streaming import StreamingTokenChunk

    # RateLimitError 시그니처 — message + response (httpx.Response) + body. 본 테스트는
    # 메시지·status_code 만 검증하므로 minimal Response 객체로 인스턴스화.
    fake_response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com"))
    rate_limit_error = RateLimitError(
        message="rate limit exceeded", response=fake_response, body=None
    )

    call_count = {"n": 0}

    def _stream_with_rate_limit_then_success(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert kwargs["model"] == "gpt-4o"

            # primary_model 호출 — 토큰 1개 yield 후 raise (UI 가 부분 답변 송신을
            # 받았다가 빈 token 으로 clear 되는 흐름까지 검증).
            def _gen() -> Any:
                yield StreamingTokenChunk(text="(부분)")
                raise rate_limit_error

            return _gen()
        # fallback_model 호출 — 정상 token yield.
        assert kwargs["model"] == "gpt-4o-mini"

        def _gen_fb() -> Any:
            yield StreamingTokenChunk(text="정상")
            yield StreamingTokenChunk(text="[#1]")

        return _gen_fb()

    client = _streaming_client_with_stream_callable(
        populated_graph,
        monkeypatch=monkeypatch,
        stream_callable=_stream_with_rate_limit_then_success,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    # stream_openai_answer 가 정확히 2회 호출됐다 — primary 1회 + fallback 1회.
    assert call_count["n"] == 2
    token_payloads = [data for name, data in events if name == "token"]
    # (부분) + (빈 clear) + 정상 + [#1] = 4 회 token (또는 차단 분기 추가 1회). 최소 3회.
    assert len(token_payloads) >= 3
    # 빈 clear token 이 송신됐다 (content="") — UI 가 부분 답변을 덮어쓸 수 있도록.
    assert "" in [_content(data) for data in token_payloads]


@pytest.mark.asyncio
async def test_query_route_stream_true_emits_multiple_token_chunks(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """운영 streaming — token chunk 가 다중 송신되고 후행 4 이벤트 시퀀스 정합.

    NOTE: ``_parse_sse`` 가 ``data:`` 라인을 ``.strip()`` 으로 정규화하므로 본 회귀
    에서는 trailing/leading 공백 없는 토큰을 사용해 단언 정합화한다 (SSE 공백 보존
    여부는 본 회귀 범위 외).
    """
    streaming_tokens = ["답변", "시작", "[#1]"]
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=streaming_tokens,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    # token 이벤트는 streaming_tokens 갯수 이상 (검증 차단 분기에서 1회 더 송신 가능).
    token_count = sum(1 for name, _ in events if name == "token")
    assert token_count >= len(streaming_tokens)
    # token content 누적은 streaming_tokens 의 순서를 보존한다.
    token_contents = [_content(data) for name, data in events if name == "token"]
    assert token_contents[: len(streaming_tokens)] == streaming_tokens
    # 후행 이벤트 시퀀스 정합 — sources / verification / meta / done.
    # feature19 — status 이벤트가 추가됐으므로 token/status 를 제외하고 단언한다.
    trailing_names = [name for name, _ in events if name not in ("token", "status")]
    assert trailing_names == ["sources", "verification", "meta", "done"]


@pytest.mark.asyncio
async def test_query_route_stream_false_forces_non_streaming(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """명세 정합 — ``stream=false`` 면 OpenAI 가용(streaming 가능) 환경이어도 비-streaming.

    ``_streaming_client`` 는 deps.generator_provider/openai_api_key 를 채워 streaming 분기가
    가능한 상태를 만든다. 그럼에도 ``stream=False`` 요청은 단일 token(전체 답변) + 후행 4종의
    비-streaming 시퀀스로 응답해야 한다(stream 플래그가 서버 가용성보다 우선). 코드 리뷰
    A14 이후 비-streaming 도 status 를 송신하므로, streaming 전용 phase(answering/streaming/
    verifying)의 부재 + token 1회를 비-streaming 진입의 신호로 단언한다.
    """
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=["답변", "시작", "[#1]"],
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=False))
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    event_names = [name for name, _ in events]
    # 비-streaming — status 는 축약 4종뿐(streaming 전용 phase 없음), token 정확히 1회.
    assert _status_phases(events) == ["connecting", "acl_filtering", "searching", "formatting"]
    assert [n for n in event_names if n != "status"] == [
        "token",
        "sources",
        "verification",
        "meta",
        "done",
    ]


# --- feature19: SSE 진행 status 이벤트 ---


def _status_phases(events: list[tuple[str, str]]) -> list[str]:
    """SSE 이벤트 시퀀스에서 status 이벤트의 phase 만 순서대로 추출한다."""
    phases: list[str] = []
    for name, data in events:
        if name == "status":
            payload = json.loads(data)
            phases.append(payload["phase"])
    return phases


@pytest.mark.asyncio
async def test_query_route_stream_status_phases_in_order(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rerank 분기 — status phase 가 정의된 순서대로 송신된다(feature19).

    connecting → acl_filtering → searching → answering → streaming →
    verifying → formatting. 각 phase 는 정확히 1회 송신되며, message 는 한국어 문자열.
    """
    streaming_tokens = ["답변", "시작", "[#1]"]
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=streaming_tokens,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    phases = _status_phases(events)
    assert phases == [
        "connecting",
        "acl_filtering",
        "searching",
        "answering",
        "streaming",
        "verifying",
        "formatting",
    ]

    # 각 status 이벤트는 한국어 message 를 동봉한다 (ensure_ascii=False JSON).
    for name, data in events:
        if name == "status":
            payload = json.loads(data)
            assert isinstance(payload["message"], str)
            assert payload["message"] != ""


@pytest.mark.asyncio
async def test_query_route_stream_status_ordering_relative_to_token_and_trailing(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status 와 기존 이벤트의 상대 순서 — searching/answering/streaming 은 첫 token 이전,
    verifying/formatting 은 토큰 송신 이후 sources 이전에 위치한다(feature19)."""
    streaming_tokens = ["답변", "시작", "[#1]"]
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=streaming_tokens,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    names = [name for name, _ in events]
    phase_at = {
        json.loads(data)["phase"]: i for i, (name, data) in enumerate(events) if name == "status"
    }
    first_token = names.index("token")
    sources_idx = names.index("sources")

    # streaming phase 는 첫 token 직전에 송신된다.
    assert phase_at["streaming"] < first_token
    assert phase_at["answering"] < phase_at["streaming"]
    # verifying/formatting 은 token 송신 이후, sources 이전.
    assert phase_at["verifying"] > first_token
    assert phase_at["formatting"] > phase_at["verifying"]
    assert phase_at["formatting"] < sources_idx


@pytest.mark.asyncio
async def test_query_route_stream_status_phases_shortened_on_empty_retrieval(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검색 0건(RETRIEVAL_EMPTY) 분기 — answering/streaming/verifying 를 건너뛰고
    formatting 으로 직행한다(feature19). status phase 는 단축된다."""
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=["답변"],
        indexed=False,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(question="anything", stream=True))
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    phases = _status_phases(events)
    # 단축 시퀀스 — connecting → acl_filtering → searching → formatting.
    assert phases == ["connecting", "acl_filtering", "searching", "formatting"]
    # answering/streaming/verifying 는 생략된다.
    assert "answering" not in phases
    assert "streaming" not in phases
    assert "verifying" not in phases
    # 표준 RETRIEVAL_EMPTY 메시지 + 후행 이벤트는 그대로 송신된다.
    by_name = dict(events)
    assert "권한 범위" in _content(by_name["token"])
    assert json.loads(by_name["sources"])["sources"] == []


@pytest.mark.asyncio
async def test_query_route_stream_core_events_unchanged_with_status(
    populated_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """핵심 이벤트(token/sources/verification/meta/done) 무회귀 — status 를 무시하면
    token 누적·후행 이벤트가 feature19 status 추가 전과 동일하다(feature19)."""
    streaming_tokens = ["답변", "시작", "[#1]"]
    client = _streaming_client(
        populated_graph,
        monkeypatch=monkeypatch,
        streaming_tokens=streaming_tokens,
    )
    async with client as c:
        resp = await c.post("/ml/query", json=_body(stream=True))
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    # status 를 무시한 (기존 클라이언트 관점) 이벤트 시퀀스.
    non_status = [(name, data) for name, data in events if name != "status"]
    non_status_names = [name for name, _ in non_status]
    # token 다중 + 후행 4종 — status 추가 전과 동일한 5개 이벤트 종류·순서.
    assert non_status_names[: len(streaming_tokens)] == ["token"] * len(streaming_tokens)
    assert non_status_names[-4:] == ["sources", "verification", "meta", "done"]
    # token content 누적은 streaming_tokens 순서를 보존한다.
    token_contents = [_content(data) for name, data in non_status if name == "token"]
    assert token_contents[: len(streaming_tokens)] == streaming_tokens
    # done 은 빈 객체 {}.
    assert non_status[-1] == ("done", "{}")


# --- 2026-06-10 코드 리뷰(A15): SSE error — errorCode 분류 + 고정 안내 문구 ---


class _RaisingGraph:
    """invoke 진입 즉시 지정 예외를 던지는 그래프 stub — 비-streaming SSE error 회귀용.

    ``run_query`` 가 ``graph.invoke(state)`` 를 호출하므로 invoke 만 구현하면 충분하다.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def invoke(self, state: Any) -> Any:
        raise self._exc


async def _post_error_query(exc: Exception) -> list[tuple[str, str]]:
    """예외를 던지는 그래프로 비-streaming 질의를 보내고 SSE 이벤트 목록을 돌려준다."""
    async with _client(_RaisingGraph(exc)) as client:
        resp = await client.post("/ml/query", json=_body())
    assert resp.status_code == 200  # 오류도 SSE error 이벤트로 전달 (HTTP 에러 아님).
    return _parse_sse(resp.text)


@pytest.mark.asyncio
async def test_non_streaming_error_event_timeout_uses_fixed_message() -> None:
    """TimeoutError → errorCode=ML_TIMEOUT + 고정 안내 문구. 내부 원문은 미노출(A15)."""
    events = await _post_error_query(TimeoutError("internal-secret: read timed out at 10.0.0.7"))
    # 오류 시퀀스 — invoke 직전 status 3종(connecting/acl_filtering/searching) 후
    # error 로 종료한다(formatting/token 없음).
    assert [name for name, _ in events] == ["status", "status", "status", "error"]
    assert _status_phases(events) == ["connecting", "acl_filtering", "searching"]
    payload = json.loads(dict(events)["error"])
    assert payload["errorCode"] == "ML_TIMEOUT"
    assert payload["message"] == "답변 생성이 제한 시간 내에 완료되지 않았습니다"
    # 내부 예외 원문(상류 상세·내부 주소)은 클라이언트에 노출되지 않는다.
    assert "internal-secret" not in json.dumps(dict(events), ensure_ascii=False)


@pytest.mark.asyncio
async def test_non_streaming_error_event_connection_uses_fixed_message() -> None:
    """ConnectionError → errorCode=ML_CONNECTION_ERROR + 고정 안내 문구(A15)."""
    events = await _post_error_query(ConnectionError("refused: 10.0.0.7:6333"))
    payload = json.loads(dict(events)["error"])
    assert payload["errorCode"] == "ML_CONNECTION_ERROR"
    assert payload["message"] == "답변 생성 서비스에 연결하지 못했습니다"
    assert "10.0.0.7" not in payload["message"]


@pytest.mark.asyncio
async def test_non_streaming_error_event_generic_uses_fixed_message() -> None:
    """그 외 예외 → errorCode=ML_SERVER_ERROR + 고정 안내 문구. 예외 원문 미노출(A15)."""
    events = await _post_error_query(RuntimeError("traceback 상세 — 내부 request-id=abc123"))
    payload = json.loads(dict(events)["error"])
    assert payload["errorCode"] == "ML_SERVER_ERROR"
    assert payload["message"] == "답변 생성 중 오류가 발생했습니다"
    assert "abc123" not in json.dumps(dict(events), ensure_ascii=False)


@pytest.mark.asyncio
async def test_non_streaming_acl_violation_uses_fixed_message() -> None:
    """ACLViolationError 표면화 → ML_SERVER_ERROR + ACL 전용 고정 문구(시스템 단 안전망)."""
    from app.query.acl import ACLViolationError

    events = await _post_error_query(ACLViolationError("acl filter missing in node X"))
    payload = json.loads(dict(events)["error"])
    assert payload["errorCode"] == "ML_SERVER_ERROR"
    assert payload["message"] == "접근 권한 처리 중 오류가 발생했습니다"
    assert "node X" not in json.dumps(dict(events), ensure_ascii=False)


# --- _classify_ml_error 단위 — api-spec §1-1 ML_* 3종 분류 ---


def test_classify_ml_error_timeout_variants() -> None:
    """표준 TimeoutError + 클래스명에 'Timeout' 을 포함한 예외(openai.APITimeoutError 상당)."""

    class APITimeoutError(Exception):  # openai 미설치 환경 정합 — 이름 기반 판별 회귀.
        pass

    assert _classify_ml_error(TimeoutError("t")) is ErrorCode.ML_TIMEOUT
    assert _classify_ml_error(APITimeoutError("t")) is ErrorCode.ML_TIMEOUT


def test_classify_ml_error_connection_variants() -> None:
    """표준 ConnectionError + 클래스명에 'Connection' 을 포함한 예외."""

    class APIConnectionError(Exception):  # openai.APIConnectionError 상당.
        pass

    assert _classify_ml_error(ConnectionError("c")) is ErrorCode.ML_CONNECTION_ERROR
    assert _classify_ml_error(APIConnectionError("c")) is ErrorCode.ML_CONNECTION_ERROR


def test_classify_ml_error_other_falls_back_to_server_error() -> None:
    """타임아웃/연결 어느 쪽도 아니면 ML_SERVER_ERROR (내부 처리 오류 기본값)."""
    assert _classify_ml_error(RuntimeError("boom")) is ErrorCode.ML_SERVER_ERROR
    assert _classify_ml_error(ValueError("bad")) is ErrorCode.ML_SERVER_ERROR
