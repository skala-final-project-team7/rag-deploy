"""Query LangGraph 그래프 조립 + 그래프 호출 래퍼 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합 — Query 파이프라인의 Pipeline 노드(완료)와 Agent stub(`app/
          pipeline/stubs.py`)을 LangGraph StateGraph로 잇는다. ACL Pre-filtering →
          멀티턴 히스토리 → 라우터 → Multi-Pool Hybrid Search → (검색 0건 분기) →
          Cross-Encoder 재순위화 → 답변 생성 → 답변 검증(1+2단계) → 응답 포맷터 흐름을
          한 위치에서 wiring한다 (`docs/architecture.md` §5.1, `docs/rag-pipeline-design.md`
          §6, `docs/api-spec.md`).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 — QueryGraphDeps + build_query_graph +
    run_query (Phase 1: 그래프 조립). FastAPI SSE 라우트는 별도 세션(Phase 2).
  - 2026-05-18, 풀 텍스트 lookup 후속 — QueryGraphDeps.chunk_lookup 필드 추가
    (기본 FakeChunkTextLookup). rerank 노드 wiring에 lookup 주입해 첨부 청크의
    Source.download_url을 채우도록 확장.
  - 2026-05-18, Agent 통합 1/4 — query-routing-agent vendoring + manage_router 어댑터
    교체. QueryGraphDeps 의 router_node 기본값을 router_stub → manage_router 로 변경.
    LLM provider/Config 주입을 위해 routing_provider/routing_config 필드 추가
    (functools.partial 패턴으로 노드에 wiring). 라우터 stub 은 회귀 보호·PoC fallback
    용도로 보존.
  - 2026-05-19, Agent 통합 2/4 — answer-generation-agent vendoring + manage_generator
    어댑터 교체. QueryGraphDeps 의 generator_node 기본값을 generator_stub →
    manage_generator 로 변경. LLM provider/Config 주입을 위해 generator_provider/
    generator_config 필드 추가 (router 와 동일 partial 패턴). generator_stub 은
    회귀 보호용으로 보존. SSE 토큰 스트리밍 (설계서 §4.6.4)·운영 OpenAI transport
    (§4.6.3)·Rate Limit fallback (§4.6.5)은 본 세션(2/4)에서는 미구현이었고, 이후
    feature14·build_real_deps 에서 구현 완료 (generator.py [구현 현황] 섹션 참조).
  - 2026-05-19, Agent 통합 3/4 — answer-verification-agent vendoring +
    manage_verifier_evaluator 어댑터 교체. QueryGraphDeps 의 verify_llm_evaluator
    기본값을 verify_llm_evaluator_stub → manage_verifier_evaluator 로 변경.
    LLM provider/Config 주입을 위해 verifier_provider/verifier_config 필드 추가
    (router/generator 와 동일 partial 패턴). verify_llm_evaluator_stub 은 회귀
    보호용으로 보존.
  - 2026-05-19, feature12 라우터 LangGraph config 충돌 fix — manage_router 의
    시그니처 변경 (``config`` placeholder + ``routing_config`` keyword-only)
    에 맞춰 router partial wiring 을 ``config=`` → ``routing_config=`` 로 갱신.
    generator/verifier partial 은 변경 없음.
  - 2026-05-19, feature14 SSE token streaming 라우트 통합 —
    ``build_query_graph_for_streaming(deps)`` helper 신설. 기존 build_query
    _graph 가 history→router→search→(empty|rerank)→generate→verify 전체를
    조립하는 반면, 본 helper 는 generate/verify 를 제외하고 rerank 까지만
    조립한다 (rerank 노드 종료 후 END). SSE 라우트가 rerank 결과 top_chunks
    를 받아 OpenAI streaming + 검증을 직접 수행하는 흐름 (설계서 §4.6.4).
    기존 build_query_graph 는 무수정 보존 (PoC 600 test 회귀 + non-streaming
    경로 유지).
--------------------------------------------------
[호환성]
  - Python 3.11.x, LangGraph 0.2.x
  - 외부 의존성: dense/sparse 임베더·QdrantPoolStore·Cross-Encoder Reranker는 모두
    호출자가 주입한다 (`functools.partial` 패턴 — `app/CLAUDE.md` §8).
  - NOTE: 본 모듈은 Agent 노드(라우터·답변 생성기·검증 2단계)를 실 어댑터로 기본 wiring
          한다 (manage_router / manage_generator / manage_verifier_evaluator). 회귀
          보호용 stub 은 stubs.py 에 보존된다.
--------------------------------------------------
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from app.ingestion.embedder.base import DenseEmbedder, SparseEmbedder
from app.pipeline.nodes import (
    after_search_branch,
    empty_retrieval_node,
    verify_pipeline_node,
)
from app.query.formatter import format_response
from app.query.generator import manage_generator
from app.query.history import manage_history
from app.query.rerank_node import cross_encoder_rerank
from app.query.reranker.base import CrossEncoderReranker
from app.query.router import manage_router
from app.query.search_node import hybrid_search
from app.query.verifier_evaluator import manage_verifier_evaluator
from app.schemas.enums import Intent, LlmModel
from app.schemas.rag_state import RagState
from app.schemas.response import QueryResponse
from app.storage.chunk_lookup import ChunkTextLookup, FakeChunkTextLookup
from app.storage.qdrant_client import QdrantPoolStore

# history-manager-agent의 LLM provider — runtime 인터페이스 의존성 회피를 위해 Any.
HistoryProvider = Any

# query-routing-agent의 LLM provider / config — runtime 인터페이스 의존성 회피를 위해 Any.
# 실제 타입은 RoutingLLMProvider / QueryRoutingConfig. manage_router 가 None 일 때 fake
# provider + 기본 config 를 사용하므로, 본 dataclass 가 vendoring 패키지의 import 를
# 강제하지 않아도 된다.
RoutingProvider = Any
RoutingConfig = Any

# answer-generation-agent 의 LLM provider / config — runtime 인터페이스 의존성 회피를
# 위해 Any. 실제 타입은 AnswerLLMProvider / AnswerGenerationConfig. manage_generator
# 가 None 일 때 FakeAnswerLLMProvider + 기본 AnswerGenerationConfig 를 사용한다.
GeneratorProvider = Any
GeneratorConfig = Any

# answer-verification-agent 의 LLM provider / config — runtime 인터페이스 의존성 회피
# 를 위해 Any. 실제 타입은 AnswerEvaluatorProvider / AnswerVerificationConfig.
# manage_verifier_evaluator 가 None 일 때 FakeEvaluatorProvider + 기본
# AnswerVerificationConfig 를 사용한다.
VerifierProvider = Any
VerifierConfig = Any

# 노드 시그니처 (모두 (RagState) -> RagState)
QueryNode = Callable[[RagState], RagState]

# 검증 2단계 LLM 평가자 시그니처 — keyword args (`answer`, `top_chunks`,
# `suspicious_sentences`). `app/pipeline/nodes.VerifyLLMEvaluator` 와 정합.
VerifyEvaluator = Callable[..., list]


@dataclass(slots=True)
class QueryGraphDeps:
    """Query 그래프 의존성 묶음 — 그래프 빌더가 노드에 wiring한다.

    Pipeline 컴포넌트(검색·재순위화·검증 1단계·포맷터)는 본 담당자 영역에서 이미 완료
    되었으며, Agent 컴포넌트 3종(라우터·답변 생성기·검증 2단계)은 실 어댑터(manage_*)가
    기본값이다(회귀 보호용 stub 은 stubs.py 에 보존). 본 dataclass 인자만 교체하면 그래프
    변경 없이 다른 구현으로 바꿀 수 있다.
    """

    # --- Pipeline / Storage 의존성 ---
    dense_embedder: DenseEmbedder
    sparse_embedder: SparseEmbedder
    store: QdrantPoolStore
    reranker: CrossEncoderReranker
    # Chunk 풀 텍스트·첨부 download_url lookup (풀 텍스트 lookup 후속, 2026-05-18).
    # 기본값은 빈 FakeChunkTextLookup — 미주입 환경에서도 안전 동작 (download_url=None).
    chunk_lookup: ChunkTextLookup = field(default_factory=FakeChunkTextLookup)
    # 멀티턴 히스토리 관리자 LLM provider — None이면 manage_history가
    # FakeHistoryLLMProvider 기본을 사용한다.
    history_provider: HistoryProvider | None = None

    # 질의 라우터 LLM provider / config — None 이면 manage_router 가
    # FakeRoutingLLMProvider + 기본 QueryRoutingConfig 를 사용한다.
    routing_provider: RoutingProvider | None = None
    routing_config: RoutingConfig | None = None

    # 답변 생성기 LLM provider / config — None 이면 manage_generator 가
    # FakeAnswerLLMProvider + 기본 AnswerGenerationConfig 를 사용한다(PoC·테스트).
    # 운영(build_real_deps)은 OpenAIAnswerLLMProvider + build_openai_chat_transport
    # 를 본 필드에 주입한다 (app/query/openai_transport.py).
    generator_provider: GeneratorProvider | None = None
    generator_config: GeneratorConfig | None = None

    # 답변 검증 2단계 LLM 평가자 provider / config — None 이면
    # manage_verifier_evaluator 가 FakeEvaluatorProvider + 기본
    # AnswerVerificationConfig 를 사용한다. agent OpenAIEvaluatorProvider 는
    # 자체 urllib HTTP transport (default) 가 있어 운영 모드는 즉시 wiring 가능.
    verifier_provider: VerifierProvider | None = None
    verifier_config: VerifierConfig | None = None
    # feature17c-19 (opt-in, 기본 False) — True 면 검증 2단계가 의심 문장을 인용 청크가
    # 아니라 검색된 전체 top-k 근거로 평가한다(환각/차단을 "어느 retrieved 근거로도 미지원"
    # 으로만 판정). manage_verifier_evaluator 기본값일 때만 partial 로 전달된다.
    verifier_full_context: bool = False

    # --- Agent 노드 ---
    # router_node 는 manage_router (query-routing-agent 어댑터) 가 기본값.
    # generator_node 는 manage_generator (answer-generation-agent 어댑터) 가 기본값.
    # verify_llm_evaluator 는 manage_verifier_evaluator (answer-verification-agent
    # 어댑터) 가 기본값. provider 가 None 이면 fake provider 가 자동 주입되어 외부
    # API 키 없이 PoC 경로 동작.
    router_node: QueryNode = field(default=manage_router)
    generator_node: QueryNode = field(default=manage_generator)
    verify_llm_evaluator: VerifyEvaluator = field(default=manage_verifier_evaluator)


def build_query_graph(deps: QueryGraphDeps) -> Any:
    """Query LangGraph StateGraph를 조립해 컴파일된 그래프를 반환한다.

    그래프 구조 (rag-pipeline-design.md §6, api-spec.md 표준 분기):
        manage_history → router → hybrid_search
                                     ├─(candidates 0건)─► empty_retrieval ─► END
                                     └─(후보 있음)─► rerank → generate → verify ─► END

    Args:
        deps: 그래프 노드 wiring에 필요한 의존성 묶음.

    Returns:
        LangGraph CompiledGraph (`graph.invoke(state)` 로 실행).
    """
    builder = StateGraph(RagState)

    # 노드 등록 — 외부 의존성은 functools.partial 로 wiring.
    # NOTE: 노드명은 RagState 필드명과 네임스페이스를 공유한다 (LangGraph 1.x 제약).
    # 히스토리 관리자 노드는 RagState.history 필드와 충돌하므로 'manage_history'로 둔다.
    builder.add_node("manage_history", partial(manage_history, provider=deps.history_provider))
    # 라우터 노드는 manage_router 기본값일 때만 routing_provider / routing_config 를
    # functools.partial 로 주입한다. 외부에서 주입된 사용자 정의 router_node 는 이미
    # provider 가 captured 되어 있다고 가정하고 그대로 등록 (history 패턴 정합).
    if deps.router_node is manage_router:
        builder.add_node(
            "router",
            partial(
                manage_router,
                provider=deps.routing_provider,
                routing_config=deps.routing_config,
            ),
        )
    else:
        builder.add_node("router", deps.router_node)
    builder.add_node(
        "hybrid_search",
        partial(
            hybrid_search,
            dense_embedder=deps.dense_embedder,
            sparse_embedder=deps.sparse_embedder,
            store=deps.store,
        ),
    )
    builder.add_node("empty_retrieval", empty_retrieval_node)
    builder.add_node(
        "rerank",
        partial(cross_encoder_rerank, reranker=deps.reranker, chunk_lookup=deps.chunk_lookup),
    )
    # 생성기 노드는 manage_generator 기본값일 때만 generator_provider / generator_config
    # 를 functools.partial 로 주입한다. 외부에서 주입된 사용자 정의 generator_node 는
    # 이미 provider 가 captured 되어 있다고 가정하고 그대로 등록 (router 패턴 정합).
    if deps.generator_node is manage_generator:
        builder.add_node(
            "generate",
            partial(
                manage_generator,
                provider=deps.generator_provider,
                generation_config=deps.generator_config,
            ),
        )
    else:
        builder.add_node("generate", deps.generator_node)
    # 검증 2단계 평가자도 manage_verifier_evaluator 기본값일 때만 verifier_provider /
    # verifier_config 를 functools.partial 로 주입한다 (router/generator 패턴 정합).
    # 외부 사용자 정의 verify_llm_evaluator 는 captured 가 이미 있다고 가정하고 그대로
    # 등록.
    verifier_callable: VerifyEvaluator
    if deps.verify_llm_evaluator is manage_verifier_evaluator:
        verifier_callable = partial(
            manage_verifier_evaluator,
            provider=deps.verifier_provider,
            config=deps.verifier_config,
            full_context=deps.verifier_full_context,
        )
    else:
        verifier_callable = deps.verify_llm_evaluator
    builder.add_node(
        "verify",
        partial(verify_pipeline_node, llm_evaluator=verifier_callable),
    )

    # 엣지 — 단일 경로 + 검색 0건 분기.
    builder.set_entry_point("manage_history")
    builder.add_edge("manage_history", "router")
    builder.add_edge("router", "hybrid_search")
    builder.add_conditional_edges(
        "hybrid_search",
        after_search_branch,
        {"empty": "empty_retrieval", "rerank": "rerank"},
    )
    builder.add_edge("empty_retrieval", END)
    builder.add_edge("rerank", "generate")
    builder.add_edge("generate", "verify")
    builder.add_edge("verify", END)

    return builder.compile()


def build_query_graph_for_streaming(deps: QueryGraphDeps) -> Any:
    """SSE token streaming 용 partial Query LangGraph (rerank 까지만 조립).

    설계서 §4.6.4 — SSE 라우트가 첫 토큰부터 사용자에게 즉시 송신하려면 답변 생성기를
    LangGraph 노드로 두지 말고 라우트에서 직접 OpenAI streaming 을 호출해야 한다.
    본 helper 는 build_query_graph 와 동일한 wiring 을 사용하되 generate / verify
    노드를 제외하고 rerank 종료 후 END 로 끝난다. 라우트가 종료된 state 의
    ``top_chunks`` 를 받아 ``stream_openai_answer`` 로 토큰 송신 + 검증 1+2단계
    를 사후에 직접 수행한다.

    그래프 구조:
        manage_history → router → hybrid_search
                                     ├─(candidates 0건)─► empty_retrieval ─► END
                                     └─(후보 있음)─► rerank ─► END

    Args:
        deps: 그래프 노드 wiring 에 필요한 의존성 묶음. ``generator_*`` /
            ``verifier_*`` / ``verify_llm_evaluator`` 필드는 본 그래프에서
            사용하지 않는다.

    Returns:
        LangGraph CompiledGraph — rerank 까지 실행한 RagState 를 반환한다.
        검색 0건이면 empty_retrieval 분기로 빠지며 ``answer`` 가 RETRIEVAL_EMPTY
        표준 메시지로 채워진다 (기존 build_query_graph 정합).
    """
    builder = StateGraph(RagState)

    # 노드 등록 — build_query_graph 와 동일 wiring. partial 패턴 정합.
    builder.add_node("manage_history", partial(manage_history, provider=deps.history_provider))
    if deps.router_node is manage_router:
        builder.add_node(
            "router",
            partial(
                manage_router,
                provider=deps.routing_provider,
                routing_config=deps.routing_config,
            ),
        )
    else:
        builder.add_node("router", deps.router_node)
    builder.add_node(
        "hybrid_search",
        partial(
            hybrid_search,
            dense_embedder=deps.dense_embedder,
            sparse_embedder=deps.sparse_embedder,
            store=deps.store,
        ),
    )
    builder.add_node("empty_retrieval", empty_retrieval_node)
    builder.add_node(
        "rerank",
        partial(cross_encoder_rerank, reranker=deps.reranker, chunk_lookup=deps.chunk_lookup),
    )

    # 엣지 — 단일 경로 + 검색 0건 분기 (generate/verify 미포함).
    builder.set_entry_point("manage_history")
    builder.add_edge("manage_history", "router")
    builder.add_edge("router", "hybrid_search")
    builder.add_conditional_edges(
        "hybrid_search",
        after_search_branch,
        {"empty": "empty_retrieval", "rerank": "rerank"},
    )
    builder.add_edge("empty_retrieval", END)
    builder.add_edge("rerank", END)

    return builder.compile()


def run_query(
    state: RagState,
    *,
    graph: Any,
    formatter: Callable[..., QueryResponse] = format_response,
) -> QueryResponse:
    """그래프를 invoke해 RagState를 채운 뒤 포맷터로 QueryResponse를 산출한다.

    latency 측정은 본 wrapper가 책임진다 — 그래프 진입 직전부터 invoke 종료 시점까지의
    monotonic 시간을 ms로 환산한다. 그래프 자체는 비결정적 외부 호출(임베딩 / Qdrant /
    Cross-Encoder)을 포함하므로 wall-clock 대신 ``time.perf_counter_ns`` 를 사용한다.

    LangGraph 0.2.x는 Pydantic state를 dict로 직렬화해 반환하므로
    ``RagState.model_validate`` 로 재구성한 뒤 포맷터에 전달한다.

    Args:
        state: 초기 RagState. ``query`` / ``user_id`` / ``groups`` / ``acl_filter``
            (호출자가 build_acl_filter로 산출) 채워 진입.
        graph: ``build_query_graph`` 로 컴파일된 그래프.
        formatter: 포맷터 함수 — 기본값 `app.query.formatter.format_response`. 테스트
            에서 분기 검증을 위해 주입 가능.

    Returns:
        UI 렌더링용 QueryResponse (api-spec.md 정합).
    """
    response, _ = run_query_with_state(state, graph=graph, formatter=formatter)
    return response


def run_query_with_state(
    state: RagState,
    *,
    graph: Any,
    formatter: Callable[..., QueryResponse] = format_response,
) -> tuple[QueryResponse, RagState]:
    """``run_query`` 와 동일하되 최종 RagState 도 함께 반환한다.

    ``run_query`` 는 UI 응답(QueryResponse)만 돌려주므로 ``top_chunks`` / 원본 ``answer`` /
    파이프라인 ``verification`` 같은 내부 상태에 접근할 수 없다. 평가·진단 도구가 동일
    답변을 다른 grounding(전체 top-k 등)으로 재검증하려면 ``top_chunks`` 가 필요하므로,
    최종 RagState 를 노출하는 변형을 제공한다(feature17c-26 측정 이원화). 프로덕션 경로는
    ``run_query`` 를 그대로 쓰며 동작 변화가 없다.

    Returns:
        (QueryResponse, 최종 RagState) 튜플. RagState 는 ``top_chunks`` / ``answer`` /
        ``verification`` 등 그래프 실행 결과를 모두 담는다.
    """
    started = time.perf_counter_ns()
    result_dict = graph.invoke(state)
    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000

    final = RagState.model_validate(result_dict)
    # latency_ms 는 그래프 외부에서 측정한 값만 신뢰한다.
    intent = final.intent or Intent.OPERATION_GUIDE
    used_llm = final.used_llm or LlmModel.GPT_4O_MINI
    answer = final.answer or ""

    response = formatter(
        answer=answer,
        sources=final.sources,
        verification=final.verification,
        intent=intent,
        used_llm=used_llm,
        latency_ms=int(elapsed_ms),
    )
    return response, final
