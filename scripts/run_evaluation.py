"""Evaluation Set 실행 + 결과 측정 CLI [Pipeline 평가 도구].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature17a — 설계서 §6.2 Evaluation Set 50건을 그래프에 통과시키고
          (1) Precision@k (정답 page_id 기준), (2) 의도 분류 정확도, (3) 평균
          latency / NOT_SUPPORTED 비율 / Top-1 Cross-Encoder 점수 분포를 산출
          하는 자동 평가 CLI. 본 세션 (feature17a) 은 골격 + 시드 10건 실행
          까지, 50건 라벨링 + ROUGE-L / BERTScore 평가는 feature17b 이관.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, feature17a — Evaluation Set 실행 + 4종 지표 산출.
  - 2026-05-19, feature17b 인프라 — ``--rouge-l`` / ``--bert-score`` 옵션 추가
    (설계서 §7.2.3 Golden Set 기반 자동 평가). Precision@k 매칭은 expected
    _chunk_ids 가 채워져 있으면 chunk_id 직접 비교 (정밀), 빈 배열이면 기존
    sources 비어 있지 않음 약식 매칭 (feature17a 동작 유지). chunk_id 추출은
    Source 의 confluence_url / text_preview 와 chunk_lookup 으로는 어려우므로,
    backfill 시점에 ``expected_chunk_ids`` 를 그대로 Qdrant scroll 로 채워두면
    eval 단계에서는 chunk_id 가 Source schema 에 없어도 page_id 우회 매칭이
    가능하다.
  - 2026-05-20, feature17b 정밀 매칭 — Source 스키마에 chunk_id/page_id 직접
    필드가 없고 confluence_url 패턴 (``/display/<SPACE>/<title>``) 에도 page_id
    가 없어, samples 의 page_id → webui_link 매핑을 1회 로드해 expected_page
    _ids 가 가리키는 webui_link set 과 Source.confluence_url 의 동일성으로
    page-level 정밀 매칭한다 (chunk-level 은 여전히 불가). samples 데이터가
    없으면 약식 매칭으로 자동 fallback. summary.precision_at_k.match_method 로
    매칭 방식을 명시한다.
  - 2026-05-20, feature17c-13 환각 측정 공정화 — Evaluation Set 의 ``is_answerable``
    (코퍼스에 정답 근거 없는 항목 = false) 플래그를 반영해 환각(NOT_SUPPORTED) 집계를
    ``_summarize_hallucination`` 순수 헬퍼로 분리. 전체 지표(``not_supported_ratio``)는
    유지하고, false 항목을 제외한 ``not_supported_ratio_answerable`` 를 신설(공정 지표).
    각 result 에 ``is_answerable`` 기록 + non-answerable 올바른 거부 진단 카운트 추가.
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 사용법:
        # PoC 그래프로 시드 10건 실행 (외부 키/모델 없이)
        python scripts/run_evaluation.py --eval-set samples/evaluation_set.json

        # 운영 그래프 (실 GPT-4o + 운영 Qdrant) 로 실행
        python scripts/run_evaluation.py --use-real-adapters

        # 단일 질문 라우터 디버깅 (feature16 발견 #2 분석)
        python scripts/run_evaluation.py --debug-route "EKS 노드 장애 대응 절차는?"
  - NOTE: 본 스크립트는 routing/generation/verification 의 운영 적합성을 회귀
          가능한 형태로 측정한다. 라이브 평가가 아니므로 결과 JSON 은 시점에
          따라 다를 수 있다 (LLM 비결정론).
--------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluation Set 을 그래프에 통과시키고 4종 지표를 측정한다.",
    )
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=Path("samples/evaluation_set.json"),
        help="Evaluation Set JSON 경로 (기본: samples/evaluation_set.json).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="결과 JSON 저장 경로 (기본: reports/evaluation_<timestamp>.json).",
    )
    parser.add_argument(
        "--use-real-adapters",
        action="store_true",
        help="운영 그래프 (E5/BM25/Qdrant.from_settings + 실 OpenAI) 사용. 외부 의존성 필요.",
    )
    parser.add_argument(
        "--debug-route",
        type=str,
        default=None,
        help="단일 질문 라우터 디버깅 모드 — 의도/pool_weights/rewritten_queries 만 출력.",
    )
    parser.add_argument(
        "--debug-rerank",
        type=str,
        default=None,
        help=(
            "단일 질문의 검색 후보 raw Cross-Encoder logit 분포 출력 — temperature "
            "결정용 (feature17c-1). 운영 reranker(predict_logits) 필요 → --use-real-adapters 권장."
        ),
    )
    parser.add_argument(
        "--debug-verify",
        type=str,
        default=None,
        help=(
            "단일 질문의 문장별 검증 진단 출력 — 환각(NOT_SUPPORTED) 근본 원인 분류 "
            "(feature17c-15). 1단계 토큰/미확인토큰/인용청크 + 2단계 raw label/score/reason "
            "+ 미확인 토큰 위치(인용청크/타 top-k/부재). 운영 LLM 필요 → --use-real-adapters 권장. "
            "reports/debug_verify_<ts>.json 에도 구조화 저장."
        ),
    )
    parser.add_argument(
        "--debug-leniency",
        type=str,
        default=None,
        help=(
            "feature17c-19 — full_context grounding 채택 전 leniency 검증. 질의의 검색 "
            "top-k 에 대해 의도적으로 근거 없는(fabricated) 통제 문장을 전체 top-k 근거로 "
            "2단계 평가 → UNSUPPORTED 유지하면 평가자가 무분별 통과(거짓음성) 아님(PASS). "
            "운영 LLM 필요 → --use-real-adapters."
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Precision@k 계산용 k (기본 3, 설계서 §6.4 KPI Precision@3).",
    )
    parser.add_argument(
        "--pool-weights",
        type=str,
        default=None,
        help=(
            "Pool 가중치 그리드 서치 — 모든 질의의 라우터 pool_weights 를 강제 오버라이드한다 "
            "(feature17c-9). 형식: 'title:0.25,content:0.6,label:0.15'. 라우터 출력을 덮어쓰므로 "
            "의도별 가중치 비교 실험에 사용. 미지정 시 라우터 값 그대로."
        ),
    )
    parser.add_argument(
        "--rouge-l",
        action="store_true",
        help="ROUGE-L F1 (rouge-score 라이브러리) 으로 answer vs expected_answer_excerpt 평가.",
    )
    parser.add_argument(
        "--bert-score",
        action="store_true",
        help=(
            "BERTScore F1 (bert-score 라이브러리) 으로 answer vs expected_answer_excerpt 평가."
            " transformers/torch 모델 다운로드 (~500MB) 필요."
        ),
    )
    args = parser.parse_args()

    if args.debug_route:
        return _run_debug_route(args.debug_route, use_real=args.use_real_adapters)

    if args.debug_rerank:
        return _run_debug_rerank(args.debug_rerank, use_real=args.use_real_adapters)

    if args.debug_verify:
        return _run_debug_verify(args.debug_verify, use_real=args.use_real_adapters)

    if args.debug_leniency:
        return _run_debug_leniency(args.debug_leniency, use_real=args.use_real_adapters)

    if not args.eval_set.exists():
        print(f"[err] eval-set not found: {args.eval_set}")
        return 1

    pool_weights_override = _parse_pool_weights(args.pool_weights) if args.pool_weights else None

    return _run_evaluation(
        eval_set_path=args.eval_set,
        output_path=args.output,
        use_real=args.use_real_adapters,
        top_k=args.top_k,
        compute_rouge_l=args.rouge_l,
        compute_bert_score=args.bert_score,
        pool_weights_override=pool_weights_override,
    )


def _parse_pool_weights(spec: str) -> dict[str, float]:
    """'title:0.25,content:0.6,label:0.15' → {title_pool/content_pool/label_pool: float}.

    Pool 가중치 그리드 서치(feature17c-9)용. 짧은 키(title/content/label)를 Qdrant Pool
    이름(`title_pool`/`content_pool`/`label_pool`)으로 매핑한다. 3 Pool 모두 명시해야 한다.

    Raises:
        ValueError: 형식 오류 / 미지의 Pool 키 / 3 Pool 누락.
    """
    alias = {"title": "title_pool", "content": "content_pool", "label": "label_pool"}
    weights: dict[str, float] = {}
    for part in spec.split(","):
        if ":" not in part:
            raise ValueError(f"잘못된 pool-weights 항목: {part!r} (형식 'title:0.25')")
        key, _, value = part.partition(":")
        key = key.strip().lower()
        if key not in alias:
            raise ValueError(f"미지의 Pool 키: {key!r} (title/content/label 중 하나)")
        weights[alias[key]] = float(value.strip())
    if set(weights) != set(alias.values()):
        raise ValueError("pool-weights 는 title/content/label 3 Pool 을 모두 명시해야 한다")
    return weights


def _run_debug_route(query: str, *, use_real: bool) -> int:
    """단일 질문 라우터 디버깅 — feature16 smoke 발견 #2 (모두 운영가이드 분류) 분석."""
    from app.api.deps import build_poc_deps, build_real_deps
    from app.config import get_settings
    from app.query.acl import build_acl_filter
    from app.query.history import manage_history
    from app.query.router import manage_router
    from app.schemas.rag_state import RagState

    settings = get_settings()
    deps = build_real_deps(settings) if use_real else build_poc_deps(settings)

    # debug-route 는 라우터만 호출하므로 ACL 의 영향은 없으나 _run_evaluation 과
    # 일관되게 모든 space 포함.
    eval_groups = [
        "space:CLOUD",
        "space:CCC",
        "space:DEVOPS",
        "space:SEC",
        "space:ONBOARD",
        "space:PROJ",
        "space:DATADOG_KR",
    ]
    state = RagState(
        query=query,
        user_id="eval-user",
        groups=eval_groups,
        conversation_id="eval-conv-debug",
        acl_filter=build_acl_filter("eval-user", eval_groups),
    )
    # 라우터는 history_decision 을 읽으므로 manage_history 먼저 통과.
    manage_history(state, provider=deps.history_provider)
    manage_router(
        state,
        provider=deps.routing_provider,
        routing_config=deps.routing_config,
    )
    print(
        json.dumps(
            {
                "query": query,
                "intent": state.intent.value if state.intent else None,
                "rewritten_queries": state.rewritten_queries,
                "pool_weights": state.pool_weights,
                "metadata_filters": state.metadata_filters,
                "target_llm": state.target_llm.value if state.target_llm else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run_debug_rerank(query: str, *, use_real: bool) -> int:
    """단일 질문의 검색 후보 raw Cross-Encoder logit 분포 출력 — temperature 결정용.

    feature17c-1 — ms-marco logit 이 sigmoid 를 saturate 시켜 Source.score 가 모두
    100 으로 변별력을 잃던 문제의 적정 temperature(T) 를 데이터 기반으로 정하기 위해,
    실제 검색 후보(Top-20)의 raw logit 분포와 T별 sigmoid 점수 미리보기를 출력한다.
    """
    import math

    from app.api.deps import build_poc_deps, build_real_deps
    from app.config import get_settings
    from app.query.acl import build_acl_filter
    from app.query.history import manage_history
    from app.query.router import manage_router
    from app.query.search_node import hybrid_search
    from app.schemas.rag_state import RagState

    settings = get_settings()
    deps = build_real_deps(settings) if use_real else build_poc_deps(settings)

    reranker = deps.reranker
    if not hasattr(reranker, "predict_logits"):
        print(
            "[err] 주입된 reranker 에 predict_logits 가 없다 (Fake reranker). "
            "--use-real-adapters 로 실 CrossEncoderRerankerImpl 을 사용하라."
        )
        return 1

    eval_groups = [
        "space:CLOUD",
        "space:CCC",
        "space:DEVOPS",
        "space:SEC",
        "space:ONBOARD",
        "space:PROJ",
        "space:DATADOG_KR",
    ]
    state = RagState(
        query=query,
        user_id="eval-user",
        groups=eval_groups,
        conversation_id="eval-conv-debug-rerank",
        acl_filter=build_acl_filter("eval-user", eval_groups),
    )
    manage_history(state, provider=deps.history_provider)
    manage_router(state, provider=deps.routing_provider, routing_config=deps.routing_config)
    hybrid_search(
        state,
        dense_embedder=deps.dense_embedder,
        sparse_embedder=deps.sparse_embedder,
        store=deps.store,
    )

    candidates = state.candidates
    if not candidates:
        print(f"[debug-rerank] 검색 후보 0건 — query={query!r}")
        return 0

    query_text = (
        state.history_decision.contextualized_question
        if state.history_decision and state.history_decision.contextualized_question
        else state.query
    )
    passages = [c.text for c in candidates]
    logits = reranker.predict_logits(query_text, passages)
    ordered = sorted(logits, reverse=True)

    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))

    n = len(ordered)
    print(f"[debug-rerank] query={query!r}")
    print(
        f"[debug-rerank] intent={state.intent.value if state.intent else None} "
        f"pool_weights={state.pool_weights} metadata_filters={state.metadata_filters}"
    )

    # 후보별 page 분포 — 정답 페이지가 후보에 있는지/몇 위에 reranking 되는지 진단용
    # (잔여 recall 실패 분석). logit 내림차순으로 page_id/title/section 출력.
    ranked = sorted(zip(candidates, logits, strict=True), key=lambda pair: pair[1], reverse=True)
    print(f"[debug-rerank] 후보 {n}건 (rerank logit 내림차순 — Top-5 가 답변 컨텍스트로 전달):")
    print("  rank | T4score | logit  | src  | page_id | section / title")
    for rank, (chunk, lg) in enumerate(ranked, start=1):
        meta = chunk.metadata
        src = "ATT" if meta.source_type.value == "attachment" else "page"
        label = (meta.attachment_filename or meta.page_title)[:32]
        section = (meta.section_header or "")[:24]
        marker = " <Top5" if rank <= 5 else ""
        print(
            f"  #{rank:>2} | {round(_sigmoid(lg / 4) * 100):>3} | {lg:>7.3f} | {src:>4} | "
            f"{meta.page_id:>7} | {label} / {section}{marker}"
        )
    print()
    print("[debug-rerank] raw logit 분포:")
    print(f"  max={ordered[0]:.3f} / min={ordered[-1]:.3f} / mean={sum(ordered) / n:.3f}")
    print(f"  Top-1 logit = {ordered[0]:.3f}")
    print()
    print("  T별 Top-1 sigmoid 점수 (round*100):")
    for t in (1.0, 2.0, 3.0, 4.0, 5.0, 8.0):
        s = _sigmoid(ordered[0] / t)
        print(f"    T={t:>4}: {s:.4f} → score {round(s * 100)}")
    print()
    print("  상위 5개 logit → T=1 vs T=4 vs T=8 score:")
    for i, lg in enumerate(ordered[:5]):
        s1, s4, s8 = _sigmoid(lg), _sigmoid(lg / 4), _sigmoid(lg / 8)
        print(
            f"    #{i + 1} logit={lg:>7.3f} → T1 {round(s1 * 100):>3} / "
            f"T4 {round(s4 * 100):>3} / T8 {round(s8 * 100):>3}"
        )
    return 0


def _classify_token_location(*, grounded_in_cited: bool, grounded_in_any: bool) -> str:
    """미확인 검증 토큰의 위치를 분류한다 (feature17c-15, 순수 함수).

    NOT_SUPPORTED 의 근본 원인을 분리하기 위한 진단 분류:
    - ``in_cited``: 인용 청크 텍스트에 존재(1단계가 미확인으로 봤으나 실재 — 워드 경계/
      형식 차이로 인한 1단계 false positive 후보).
    - ``in_other_topk``: 인용 청크엔 없으나 다른 Top-K 청크엔 존재 → **citation 정밀도**
      문제(생성기가 근거 청크를 잘못 인용).
    - ``absent``: 어느 Top-K 청크에도 없음 → **recall/생성 갭**(코퍼스 미검색 또는
      생성기가 컨텍스트 밖 토큰을 만들어냄).

    grounding 판정은 호출자가 ``app.query.verifier._token_grounded`` 로 계산해 넘긴다
    (단일 소스 유지). 본 함수는 두 boolean 으로 분류만 수행해 단위 테스트가 쉽다.
    """
    if grounded_in_cited:
        return "in_cited"
    if grounded_in_any:
        return "in_other_topk"
    return "absent"


def _run_debug_verify(query: str, *, use_real: bool) -> int:
    """단일 질문의 문장별 검증 진단 — 환각(NOT_SUPPORTED) 근본 원인 분류 (feature17c-15).

    환각 KPI 미달의 병목이 생성기 prompt 가 아니라 문장별 검증기로 진단됨(feature17c-14
    §8). 본 모드는 단일 질의를 풀 파이프라인에 통과시킨 뒤, 각 문장에 대해 1단계 규칙
    검증(검증 토큰·미확인 토큰·인용 청크)과 2단계 LLM 평가자의 **raw label/score/reason**
    (운영 경로에서는 status 로 매핑되며 버려지는 정보)을 함께 노출한다. 미확인 토큰은
    인용 청크/타 Top-K/부재로 위치 분류해, NOT_SUPPORTED 가 (a) citation 정밀도, (b)
    recall/생성 갭, (c) 1단계 false positive, (d) 2단계 보수 매핑(LOW_CONFIDENCE/
    NOT_CHECKED→NOT_SUPPORTED) 중 무엇에서 오는지 데이터로 분리한다.

    비용: 단일 질의 풀 파이프라인 1회 + 의심 문장 2단계 재호출(소수). 거의 무료.
    운영 LLM 평가자가 있어야 raw label 이 의미 있으므로 ``--use-real-adapters`` 권장.
    """
    from datetime import datetime

    # feature17c-18 — 전체 top-k 근거 재평가용 target 빌더. 진단에서 인용 청크만이 아니라
    # 검색된 전체 top-k 를 citations 로 줬을 때 2단계 라벨이 SUPPORTED 로 뒤집히는지 본다
    # (뒤집히면 "근거는 검색됐으나 오인용"=citation 정밀도, 안 뒤집히면 진짜 미근거).
    from answer_verification_agent.schemas import SentenceLabel
    from answer_verification_agent.verification.suspicious_selector import (
        SuspiciousSentenceTarget,
    )
    from app.api.deps import build_poc_deps, build_real_deps
    from app.config import get_settings
    from app.pipeline.query_graph import build_query_graph
    from app.query.acl import build_acl_filter
    from app.query.verifier import (
        _extract_checkable_tokens,
        _gather_cited_text,
        _token_grounded,
        verify_answer_rules,
    )
    from app.query.verifier_evaluator import (
        _chunk_to_context_id,
        _chunks_to_normalized_contexts,
        _sentence_check_to_target,
    )
    from app.schemas.rag_state import RagState

    settings = get_settings()
    deps = build_real_deps(settings) if use_real else build_poc_deps(settings)
    graph = build_query_graph(deps)

    eval_groups = [
        "space:CLOUD",
        "space:CCC",
        "space:DEVOPS",
        "space:SEC",
        "space:ONBOARD",
        "space:PROJ",
        "space:DATADOG_KR",
    ]
    state = RagState(
        query=query,
        user_id="eval-user",
        groups=eval_groups,
        conversation_id="eval-conv-debug-verify",
        acl_filter=build_acl_filter("eval-user", eval_groups),
    )
    result_dict = graph.invoke(state)
    final = RagState.model_validate(result_dict)
    answer = final.answer or ""
    top_chunks = final.top_chunks
    verification_by_id = {v.sentence_id: v for v in final.verification}

    print(f"[debug-verify] query={query!r}")
    print(
        f"[debug-verify] intent={final.intent.value if final.intent else None} "
        f"n_top_chunks={len(top_chunks)} n_sentences_verified={len(final.verification)}"
    )
    if not answer:
        print("[debug-verify] 답변 없음(검색 0건 또는 거부) — 진단할 문장 없음.")
        return 0

    rule_result = verify_answer_rules(answer, top_chunks)
    all_text = "\n".join(c.text for c in top_chunks)
    normalized_contexts = _chunks_to_normalized_contexts(top_chunks)
    all_context_ids = [
        _chunk_to_context_id(chunk, index=i) for i, chunk in enumerate(top_chunks, start=1)
    ]

    def _eval_label(target: Any) -> tuple[str | None, float | None, str | None]:
        if not (use_real and deps.verifier_provider is not None):
            return None, None, None
        try:
            ev = deps.verifier_provider.evaluate_sentence(target, normalized_contexts)
            return getattr(ev.label, "value", str(ev.label)), ev.score, ev.reason
        except Exception as exc:  # noqa: BLE001 — 진단 도구: 평가자 실패도 기록만.
            return None, None, f"<evaluate_sentence error: {exc}>"

    records: list[dict[str, Any]] = []
    for check in rule_result.sentences:
        cited_text = _gather_cited_text(check.cited_chunks, top_chunks)
        token_recs: list[dict[str, Any]] = []
        for tok in check.unverified_tokens:
            token_recs.append(
                {
                    "token": tok,
                    "location": _classify_token_location(
                        grounded_in_cited=_token_grounded(tok, cited_text),
                        grounded_in_any=_token_grounded(tok, all_text),
                    ),
                }
            )
        raw_label = raw_score = raw_reason = None
        fullctx_label = fullctx_score = fullctx_reason = None
        if check.is_suspicious:
            # (1) 인용 청크만 근거로 — 운영 동작과 동일.
            raw_label, raw_score, raw_reason = _eval_label(
                _sentence_check_to_target(check, top_chunks=top_chunks)
            )
            # (2) 전체 top-k 근거로 재평가 — 라벨이 SUPPORTED 로 뒤집히면 "근거는 검색됐으나
            #     오인용"(citation 정밀도), 안 뒤집히면 진짜 미근거(생성/recall).
            full_target = SuspiciousSentenceTarget(
                sentence_id=f"s{check.sentence_id}",
                text=check.sentence,
                score=0.0,
                preliminary_label=SentenceLabel.LOW_CONFIDENCE.value,
                reasons=["low_token_overlap"],
                citations=list(all_context_ids),
                matched_context_ids=list(all_context_ids),
                invalid_citations=[],
                failed_rules=["token_overlap"],
            )
            fullctx_label, fullctx_score, fullctx_reason = _eval_label(full_target)
        final_v = verification_by_id.get(check.sentence_id)
        records.append(
            {
                "sentence_id": check.sentence_id,
                "sentence": check.sentence,
                "cited_chunks": check.cited_chunks,
                "checkable_tokens": _extract_checkable_tokens(check.sentence),
                "unverified_tokens": token_recs,
                "suspicious": check.is_suspicious,
                "stage2_raw_label": raw_label,
                "stage2_score": raw_score,
                "stage2_reason": raw_reason,
                "stage2_fullctx_label": fullctx_label,
                "stage2_fullctx_score": fullctx_score,
                "stage2_fullctx_reason": fullctx_reason,
                "final_status": final_v.status.value if final_v else "PASS",
            }
        )

    _print_debug_verify(records)

    output_path = Path("reports") / f"debug_verify_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "query": query,
        "intent": final.intent.value if final.intent else None,
        "use_real_adapters": use_real,
        "answer": answer,
        "n_top_chunks": len(top_chunks),
        "sentences": records,
        "summary": _summarize_debug_verify(records),
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[debug-verify] report = {output_path}")
    return 0


def _summarize_debug_verify(records: list[dict[str, Any]]) -> dict[str, Any]:
    """debug-verify 레코드에서 진단 집계를 산출한다 (순수, 테스트 가능)."""
    final_dist: dict[str, int] = {}
    raw_label_dist: dict[str, int] = {}
    token_loc_dist: dict[str, int] = {}
    # feature17c-18 — NOT_SUPPORTED(인용 청크 기준) 문장이 전체 top-k 근거 재평가에서
    # SUPPORTED 로 뒤집히는 수 = "근거는 검색됐으나 오인용"(citation 정밀도) 추정량.
    flip_to_supported = 0
    not_supported_still = 0
    for r in records:
        final_dist[r["final_status"]] = final_dist.get(r["final_status"], 0) + 1
        if r["final_status"] == "NOT_SUPPORTED" and r["stage2_raw_label"]:
            raw_label_dist[r["stage2_raw_label"]] = raw_label_dist.get(r["stage2_raw_label"], 0) + 1
        if r["final_status"] == "NOT_SUPPORTED" and r.get("stage2_fullctx_label"):
            # 라벨 값 대소문자(agent enum 은 "SUPPORTED")에 무관하게 비교.
            if str(r["stage2_fullctx_label"]).lower() == "supported":
                flip_to_supported += 1
            else:
                not_supported_still += 1
        for t in r["unverified_tokens"]:
            token_loc_dist[t["location"]] = token_loc_dist.get(t["location"], 0) + 1
    return {
        "n_sentences": len(records),
        "final_status_dist": final_dist,
        "not_supported_raw_label_dist": raw_label_dist,
        "unverified_token_location_dist": token_loc_dist,
        "not_supported_fullctx_flip_to_supported": flip_to_supported,
        "not_supported_fullctx_still_unsupported": not_supported_still,
    }


def _print_debug_verify(records: list[dict[str, Any]]) -> None:
    """debug-verify 레코드를 사람이 읽기 좋은 형식으로 출력한다."""
    for r in records:
        flag = "SUSPECT" if r["suspicious"] else "PASS   "
        print()
        print(f"  [s{r['sentence_id']}] {flag} final={r['final_status']}")
        print(f"    문장: {r['sentence'][:120]}")
        print(f"    인용청크: {r['cited_chunks']} | 검증토큰: {r['checkable_tokens']}")
        if r["unverified_tokens"]:
            locs = ", ".join(f"{t['token']}({t['location']})" for t in r["unverified_tokens"])
            print(f"    미확인토큰: {locs}")
        if r["stage2_raw_label"] is not None:
            print(
                f"    2단계 raw(인용청크): label={r['stage2_raw_label']} "
                f"score={r['stage2_score']} reason={(r['stage2_reason'] or '')[:90]}"
            )
        if r.get("stage2_fullctx_label") is not None:
            flip = (
                " ★FLIP→SUPPORTED"
                if r["final_status"] == "NOT_SUPPORTED"
                and str(r["stage2_fullctx_label"]).lower() == "supported"
                else ""
            )
            print(
                f"    2단계 전체top-k: label={r['stage2_fullctx_label']} "
                f"score={r['stage2_fullctx_score']}{flip}"
            )
    summary = _summarize_debug_verify(records)
    print()
    print("[debug-verify] 집계:")
    print(f"  문장 {summary['n_sentences']}개 final 분포: {summary['final_status_dist']}")
    print(f"  NOT_SUPPORTED 의 2단계 raw label 분포: {summary['not_supported_raw_label_dist']}")
    print(f"  미확인 토큰 위치 분포: {summary['unverified_token_location_dist']}")
    print(
        f"  NOT_SUPPORTED → 전체 top-k 재평가: "
        f"SUPPORTED 로 뒤집힘 {summary['not_supported_fullctx_flip_to_supported']} / "
        f"여전히 미근거 {summary['not_supported_fullctx_still_unsupported']}"
    )
    print(
        "  해석: FLIP 다수=오인용(citation 정밀도, 우리 영역 fix 가능) / "
        "여전히 미근거 다수=진짜 환각·recall. in_other_topk=인용밖 top-k존재 / absent=top-k부재."
    )


# feature17c-19 — full_context grounding leniency 검증용 통제(fabricated) 문장.
# 인프라 코퍼스와 토큰이 거의 겹치지 않는 명백한 허위 진술 → 전체 top-k 근거로도
# SUPPORTED 가 나오면 평가자가 무분별 통과(거짓음성)임을 뜻한다.
_LENIENCY_CONTROL_SENTENCES = [
    "이 인프라의 공식 마스코트는 분홍색 코끼리이며 모든 서버는 토요일 자정에 춤을 춥니다. [#1]",
    "본 시스템은 2099년 목성 궤도 데이터센터에서 시인 세 명이 운영합니다. [#1]",
]


def _leniency_verdict(control_results: list[dict[str, Any]]) -> str:
    """통제(fabricated) 문장 라벨로 평가자 판별력을 판정한다 (순수, 테스트 가능).

    - 라벨이 하나도 없으면(평가 미수행) ``INCONCLUSIVE``.
    - 하나라도 ``supported`` 면 ``FAIL`` (무분별 통과 = full_context 채택 위험).
    - 모두 supported 가 아니면 ``PASS`` (판별력 있음).
    """
    labels = [str(r.get("label") or "").lower() for r in control_results if r.get("label")]
    if not labels:
        return "INCONCLUSIVE"
    if any(label == "supported" for label in labels):
        return "FAIL"
    return "PASS"


def _run_debug_leniency(query: str, *, use_real: bool) -> int:
    """full_context grounding 채택 전 leniency 검증 (feature17c-19).

    질의의 검색 top-k 를 확보한 뒤, 의도적으로 근거 없는 통제 문장을 **전체 top-k**
    근거로 2단계 평가한다. UNSUPPORTED 가 유지되면 평가자가 전체 근거를 줘도 거짓을
    통과시키지 않음(PASS) → full_context 채택이 환각을 은폐하지 않음을 입증. 하나라도
    SUPPORTED 면 FAIL(평가자 과민/무분별 → 채택 보류).
    """
    from datetime import datetime

    from answer_verification_agent.schemas import SentenceLabel
    from answer_verification_agent.verification.suspicious_selector import (
        SuspiciousSentenceTarget,
    )
    from app.api.deps import build_poc_deps, build_real_deps
    from app.config import get_settings
    from app.pipeline.query_graph import build_query_graph
    from app.query.acl import build_acl_filter
    from app.query.verifier_evaluator import _chunk_to_context_id, _chunks_to_normalized_contexts
    from app.schemas.rag_state import RagState

    settings = get_settings()
    deps = build_real_deps(settings) if use_real else build_poc_deps(settings)
    graph = build_query_graph(deps)
    eval_groups = [
        "space:CLOUD",
        "space:CCC",
        "space:DEVOPS",
        "space:SEC",
        "space:ONBOARD",
        "space:PROJ",
        "space:DATADOG_KR",
    ]
    state = RagState(
        query=query,
        user_id="eval-user",
        groups=eval_groups,
        conversation_id="eval-conv-debug-leniency",
        acl_filter=build_acl_filter("eval-user", eval_groups),
    )
    final = RagState.model_validate(graph.invoke(state))
    top_chunks = final.top_chunks
    if not top_chunks:
        print(f"[debug-leniency] 검색 top-k 0건 — query={query!r}. 다른 질의로 시도.")
        return 0
    if not (use_real and deps.verifier_provider is not None):
        print("[debug-leniency] 운영 평가자 없음 — --use-real-adapters 필요.")
        return 1

    normalized_contexts = _chunks_to_normalized_contexts(top_chunks)
    all_context_ids = [
        _chunk_to_context_id(chunk, index=i) for i, chunk in enumerate(top_chunks, start=1)
    ]
    print(f"[debug-leniency] query={query!r} | n_top_chunks={len(top_chunks)}")
    print("[debug-leniency] 의도적 미근거(fabricated) 통제 문장을 전체 top-k 근거로 평가:")

    control_results: list[dict[str, Any]] = []
    for idx, sentence in enumerate(_LENIENCY_CONTROL_SENTENCES, start=1):
        target = SuspiciousSentenceTarget(
            sentence_id=f"ctrl{idx}",
            text=sentence,
            score=0.0,
            preliminary_label=SentenceLabel.LOW_CONFIDENCE.value,
            reasons=["low_token_overlap"],
            citations=list(all_context_ids),
            matched_context_ids=list(all_context_ids),
            invalid_citations=[],
            failed_rules=["token_overlap"],
        )
        try:
            evaluation = deps.verifier_provider.evaluate_sentence(target, normalized_contexts)
            label = getattr(evaluation.label, "value", str(evaluation.label))
            rec = {
                "sentence": sentence,
                "label": label,
                "score": evaluation.score,
                "reason": evaluation.reason,
            }
        except Exception as exc:  # noqa: BLE001 — 진단: 평가자 실패도 기록.
            rec = {"sentence": sentence, "label": None, "score": None, "reason": f"<error: {exc}>"}
        control_results.append(rec)
        print(
            f"  [ctrl{idx}] label={rec['label']} score={rec['score']} "
            f"reason={(rec['reason'] or '')[:90]}"
        )

    verdict = _leniency_verdict(control_results)
    print()
    print(f"[debug-leniency] 판정: {verdict}")
    print(
        "  PASS=평가자가 전체 근거를 줘도 거짓을 통과시키지 않음(full_context 채택 안전) / "
        "FAIL=무분별 통과(채택 보류) / INCONCLUSIVE=평가 미수행."
    )
    output_path = Path("reports") / f"debug_leniency_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "query": query,
                "n_top_chunks": len(top_chunks),
                "verdict": verdict,
                "controls": control_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"[debug-leniency] report = {output_path}")
    return 0


def _run_evaluation(
    *,
    eval_set_path: Path,
    output_path: Path | None,
    use_real: bool,
    top_k: int,
    compute_rouge_l: bool = False,
    compute_bert_score: bool = False,
    pool_weights_override: dict[str, float] | None = None,
) -> int:
    """Evaluation Set 전체 실행 + 지표 산출."""
    from app.api.deps import build_poc_deps, build_real_deps
    from app.config import get_settings
    from app.pipeline.query_graph import build_query_graph, run_query_with_state
    from app.query.acl import build_acl_filter
    from app.query.formatter import BLOCKED_ANSWER_MESSAGE
    from app.query.router import manage_router
    from app.query.verifier import verify_answer_rules
    from app.query.verifier_evaluator import manage_verifier_evaluator
    from app.schemas.rag_state import RagState

    with eval_set_path.open() as fp:
        eval_data = json.load(fp)
    items: list[dict[str, Any]] = eval_data["items"]

    # feature17b 정밀 매칭 — samples 의 page_id → webui_link 매핑 1회 로드.
    # 운영 그래프는 chunk metadata 의 webui_link 를 Source.confluence_url 에
    # 그대로 채우므로, expected_page_ids 가 가리키는 webui_link set 과 Source
    # .confluence_url 동일성으로 page-level 정밀 매칭이 가능하다. samples 미
    # 존재 시 약식 매칭으로 자동 fallback.
    page_id_to_webui = _load_page_id_to_webui_link()
    match_method = "webui_link_strict" if page_id_to_webui else "loose_has_sources"

    settings = get_settings()
    deps = build_real_deps(settings) if use_real else build_poc_deps(settings)

    # feature17c-9 — Pool 가중치 그리드 서치: 라우터 노드를 래핑해 실 라우터 실행 후
    # state.pool_weights 를 강제 오버라이드한다. build_query_graph 는 router_node 가
    # manage_router 일 때만 provider/config 를 주입하므로, 래퍼가 직접 provider/config 를
    # captured 해 manage_router 를 호출한다(라우팅 정확도는 그대로, 가중치만 교체).
    if pool_weights_override is not None:
        routing_provider = deps.routing_provider
        routing_config = deps.routing_config

        # NOTE: 노드 annotation 은 ``Any`` 로 둔다 — LangGraph add_node 가 노드 콜러블에
        # get_type_hints 를 호출하는데, 그 평가는 run_evaluation 모듈 globals 에서 일어난다.
        # RagState 는 본 함수 내부 lazy import 라 모듈 globals 에 없어 NameError 가 난다.
        # Any 는 모듈 상단에 import 되어 있어 안전하다(그래프 state schema 는 StateGraph(RagState)).
        def _router_with_pool_override(state: Any) -> Any:
            manage_router(state, provider=routing_provider, routing_config=routing_config)
            state.pool_weights = dict(pool_weights_override)
            return state

        deps.router_node = _router_with_pool_override

    graph = build_query_graph(deps)

    results: list[dict[str, Any]] = []
    intent_correct = 0
    intent_total = 0
    precision_at_k_hits = 0
    precision_at_k_total = 0
    latency_ms_list: list[int] = []
    top1_score_list: list[int] = []
    # feature17b — ROUGE-L / BERTScore 누적 (라이브러리 lazy import, summary 에 평균 출력).
    predictions_for_metric: list[str] = []
    references_for_metric: list[str] = []

    for item in items:
        eval_id = item["id"]
        query = item["query"]
        expected_intent = item.get("intent")
        expected_page_ids: set[str] = set(item.get("expected_page_ids", []))

        # 평가용 사용자는 samples 의 모든 space 에 접근 가능해야 한다. ACL filter
        # 의 groups 인자가 state.groups 와 일치하지 않으면 검색이 차단되어
        # precision_at_k / verification 이 일관되게 0 으로 떨어진다 (2026-05-20 발견).
        # samples space: CLOUD / CCC / DEVOPS / SEC / ONBOARD / PROJ / DATADOG_KR.
        eval_groups = [
            "space:CLOUD",
            "space:CCC",
            "space:DEVOPS",
            "space:SEC",
            "space:ONBOARD",
            "space:PROJ",
            "space:DATADOG_KR",
        ]
        state = RagState(
            query=query,
            user_id="eval-user",
            groups=eval_groups,
            conversation_id=f"eval-conv-{eval_id}",
            acl_filter=build_acl_filter("eval-user", eval_groups),
        )
        started = time.perf_counter()
        response, final_state = run_query_with_state(state, graph=graph)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # feature17c-26 — 측정 이원화. 그래프가 산출한 verification 은 per-cited grounding
        # (= citation precision). 같은 답변을 전체 top-k 근거로 한 번 더 검증해 표준
        # faithfulness(검색 근거 어디에라도 있나)를 산출한다. rule 1단계는 결정론이라
        # suspicious set 이 그래프와 동일하고, 2단계만 full_context=True 로 재판정한다
        # (suspicious 문장만 → 추가 LLM 호출 소수). 프로덕션 경로(run_query)는 무변경.
        faithfulness_verification = _compute_faithfulness_verification(
            answer=final_state.answer or "",
            top_chunks=list(final_state.top_chunks),
            verify_rules=verify_answer_rules,
            evaluate=manage_verifier_evaluator,
            provider=deps.verifier_provider,
            config=deps.verifier_config,
        )

        # Precision@k — 응답 sources Top-k 중 expected_page_ids 와 매칭되는지.
        top_k_sources = response.sources[:top_k]
        match = _precision_match(
            top_k_sources,
            expected_page_ids,
            page_id_to_webui,
        )
        if expected_page_ids:
            precision_at_k_total += 1
            if match:
                precision_at_k_hits += 1

        # 의도 분류 정확도.
        if expected_intent and response.intent:
            intent_total += 1
            if response.intent.value == expected_intent:
                intent_correct += 1

        # 환각 비율 집계는 루프 후 _summarize_hallucination 으로 산출(answerable 분리).
        latency_ms_list.append(elapsed_ms)
        if response.sources:
            top1_score_list.append(response.sources[0].score)

        # feature17b — ROUGE-L/BERTScore 용 예측·정답 쌍 수집. expected_answer_excerpt
        # 가 있는 항목만 평가 대상에 포함.
        expected_excerpt = item.get("expected_answer_excerpt")
        if expected_excerpt and response.answer:
            predictions_for_metric.append(response.answer)
            references_for_metric.append(expected_excerpt)

        results.append(
            {
                "id": eval_id,
                "query": query,
                "expected_intent": expected_intent,
                "actual_intent": response.intent.value if response.intent else None,
                "intent_match": (
                    response.intent.value == expected_intent
                    if response.intent and expected_intent
                    else None
                ),
                "expected_page_ids": list(expected_page_ids),
                "is_answerable": item.get("is_answerable", True),
                # feature17c-17 — formatter 가 NOT_SUPPORTED>0.5 로 답변을 차단(BLOCKED_
                # ANSWER_MESSAGE 대체)했는지. 차단 시 환각이 사용자에게 전달되지 않음.
                "is_blocked": (response.answer or "") == BLOCKED_ANSWER_MESSAGE,
                "actual_top_k_source_titles": [s.title for s in top_k_sources],
                "n_sources": len(response.sources),
                "top1_score": response.sources[0].score if response.sources else None,
                # feature17c-26 — cited_chunks 도 기록(사후 분석·감사용). per-cited 검증
                # (= citation precision)이다.
                "verification": [
                    {
                        "sentence_id": v.sentence_id,
                        "status": v.status.value,
                        "cited_chunks": list(v.cited_chunks),
                    }
                    for v in response.verification
                ],
                # feature17c-26 — 전체 top-k 근거 재검증(= 표준 faithfulness).
                "verification_faithfulness": [
                    {
                        "sentence_id": v.sentence_id,
                        "status": v.status.value,
                        "cited_chunks": list(v.cited_chunks),
                    }
                    for v in faithfulness_verification
                ],
                "answer_excerpt": (response.answer or "")[:200],
                "feedback_enabled": response.feedback_enabled,
                "latency_ms": elapsed_ms,
            }
        )

    # --- ROUGE-L / BERTScore 산출 (feature17b 인프라) ---
    rouge_l_f1_avg: float | None = None
    bert_score_f1_avg: float | None = None
    if compute_rouge_l and predictions_for_metric:
        rouge_l_f1_avg = _compute_rouge_l_f1_avg(predictions_for_metric, references_for_metric)
    if compute_bert_score and predictions_for_metric:
        bert_score_f1_avg = _compute_bert_score_f1_avg(
            predictions_for_metric, references_for_metric
        )

    # --- 집계 ---
    summary = {
        "n_items": len(items),
        "intent_accuracy": (intent_correct / intent_total) if intent_total else None,
        "precision_at_k": {
            "k": top_k,
            "hit_ratio": (
                precision_at_k_hits / precision_at_k_total if precision_at_k_total else None
            ),
            "hits": precision_at_k_hits,
            "denom": precision_at_k_total,
            "match_method": match_method,
        },
        # feature17c-13 — 환각 집계(전체 + is_answerable 분리). 키 추가라 하위호환.
        **_summarize_hallucination(results),
        "latency_ms_avg": (
            sum(latency_ms_list) / len(latency_ms_list) if latency_ms_list else None
        ),
        "latency_ms_max": max(latency_ms_list) if latency_ms_list else None,
        "latency_ms_p95": (
            sorted(latency_ms_list)[int(0.95 * (len(latency_ms_list) - 1))]
            if latency_ms_list
            else None
        ),
        "intent_distribution": dict(
            Counter(r["actual_intent"] for r in results if r["actual_intent"])
        ),
        "top1_score_avg": (
            sum(top1_score_list) / len(top1_score_list) if top1_score_list else None
        ),
        "rouge_l_f1_avg": rouge_l_f1_avg,
        "bert_score_f1_avg": bert_score_f1_avg,
        "answer_quality_n_items": len(predictions_for_metric),
        # feature17c-9 — Pool 가중치 그리드 서치 시 어떤 가중치로 측정했는지 기록(없으면 라우터 값).
        "pool_weights_override": pool_weights_override,
        # feature17c-14 — 생성기 보수성 guard 토글 상태(A/B 추적성). 운영 어댑터 실행 시만
        # 의미(PoC 는 fake provider 라 transport guard 미적용). None 이면 PoC.
        "generator_conservative_guard": (
            settings.generator_conservative_guard if use_real else None
        ),
    }

    # --- 출력 ---
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "eval_set": str(eval_set_path),
        "use_real_adapters": use_real,
        "summary": summary,
        "results": results,
    }
    if output_path is None:
        output_path = Path("reports") / f"evaluation_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"[eval] {len(items)} items 실행 완료")
    print(f"[eval] report = {output_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _load_page_id_to_webui_link(
    samples_dir: Path | None = None,
) -> dict[str, str]:
    """samples 의 페이지 데이터에서 ``{page_id: webui_link}`` 매핑을 로드한다.

    Source 스키마에 chunk_id/page_id 직접 필드가 없고 confluence_url 패턴
    (``/display/<SPACE>/<title>``) 에도 page_id 가 없어, samples 의 webui_link
    를 통해 page-level 정밀 매칭을 수행한다. 운영 그래프는 chunk metadata 의
    webui_link 를 Source.confluence_url 에 그대로 채우므로 일치 비교가 가능.

    samples_dir 미지정 시 ``samples/`` 를 기본 경로로 한다. 파일 미존재 또는
    스키마 불일치 시 빈 dict 반환 (호출 측이 약식 매칭으로 fallback).
    """
    samples_dir = samples_dir or Path("samples")
    candidates = [
        samples_dir / "confluence_sample_data.json",
        samples_dir / "datadog_docs.json",
    ]
    mapping: dict[str, str] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open() as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data.get("single_page_responses", []) or []:
            if not isinstance(entry, dict):
                continue
            page_id = entry.get("id")
            webui = entry.get("_links", {}).get("webui")
            if page_id is not None and webui is not None:
                mapping[str(page_id)] = str(webui)
    return mapping


def _precision_match(
    top_k_sources: list[Any],
    expected_page_ids: set[str],
    page_id_to_webui: dict[str, str],
) -> bool:
    """Precision@k 단건 매칭 — webui_link 정밀 / sources 약식 fallback.

    page_id_to_webui 매핑이 있고 expected_page_ids 중 매핑 가능한 항목이 1건
    이상이면 webui_link 동일성 정밀 매칭. 매핑이 없거나 비어 있으면 sources
    가 비어 있지 않은지 만 검사하는 약식 매칭으로 fallback (feature17a 동작
    유지).
    """
    if not expected_page_ids:
        return False
    if page_id_to_webui:
        expected_webui_links: set[str] = {
            page_id_to_webui[pid] for pid in expected_page_ids if pid in page_id_to_webui
        }
        if expected_webui_links:
            return any(
                getattr(src, "confluence_url", None) in expected_webui_links
                for src in top_k_sources
            )
    # samples lookup 부재 또는 expected page_id 가 lookup 에 없음 → 약식.
    return len(top_k_sources) > 0


def _compute_faithfulness_verification(
    *,
    answer: str,
    top_chunks: list[Any],
    verify_rules: Any,
    evaluate: Any,
    provider: Any,
    config: Any,
) -> list[Any]:
    """답변을 검색된 전체 top-k 근거로 재검증해 faithfulness용 Verification 목록을 만든다
    (feature17c-26 측정 이원화).

    그래프가 산출하는 verification 은 per-cited grounding(= citation precision)이다. 본
    헬퍼는 같은 답변·top_chunks 를 1단계 규칙(``verify_rules``, 결정론)으로 다시 나눠
    그래프와 동일한 suspicious set 을 얻고, 의심 문장만 2단계(``evaluate``)에
    ``full_context=True`` 로 넘겨 "검색된 전체 top-k 어디에라도 근거가 있나"로 판정한다.
    PASS(비의심) 문장은 인용 청크에 이미 근거가 있어 전체 근거에도 당연히 포함되므로 그대로
    SUPPORTED 로 둔다. provider/config 는 호출자(deps)가 주입 — 앱 의존 없이 테스트 가능.
    """
    if not answer.strip():
        return []
    rule_result = verify_rules(answer, top_chunks)
    passed = rule_result.passed_verifications()
    suspicious = rule_result.suspicious_sentences
    evaluated = (
        evaluate(
            answer=answer,
            top_chunks=top_chunks,
            suspicious_sentences=suspicious,
            provider=provider,
            config=config,
            full_context=True,
        )
        if suspicious
        else []
    )
    return sorted(passed + evaluated, key=lambda v: v.sentence_id)


def _summarize_hallucination(results: list[dict[str, Any]]) -> dict[str, Any]:
    """검증 결과(per-item)에서 환각(NOT_SUPPORTED) 집계를 산출한다 (feature17c-13).

    ``is_answerable=false`` 항목은 코퍼스에 정답 근거가 없어 시스템이 거부(NOT_SUPPORTED)
    하는 것이 올바른 동작이므로, 이를 환각 지표에서 분리한 ``*_answerable`` 값을 별도로
    산출한다. 전체 지표(``not_supported_ratio``)는 투명성을 위해 함께 유지한다.

    feature17c-17 — ``is_blocked=true`` 항목(formatter 가 NOT_SUPPORTED 비율 > 0.5 로
    답변을 차단하고 BLOCKED_ANSWER_MESSAGE 로 대체 = 환각이 사용자에게 전달되지 않음)을
    추가 분리한 ``*_delivered``(= answerable AND not blocked) 값을 산출한다. 이것이
    사용자 노출 환각(user-facing)에 가장 가깝다. 단 차단도 비용(거부 UX)이므로
    ``blocked_n_items`` 로 차단율을 함께 노출해 "거부 남발로 환각 은폐"를 감시한다.

    feature17c-26 — 측정 이원화. 위 ``not_supported_*`` 지표는 per-cited grounding,
    즉 "그 문장이 **인용한** 청크에 근거가 있나"로 사실상 **citation precision** 을 잰다.
    표준 RAG faithfulness(RAGAS/TruLens=검색된 **전체** 컨텍스트 기준)와 다르다. 진단상
    per-cited NOT_SUPPORTED 의 대부분은 "검색은 됐으나 단일 청크만 인용"한 오인용
    아티팩트라, 전체 top-k 근거로 재판정하면 SUPPORTED 로 flip 한다(=진짜 환각 아님).
    각 result 의 ``verification_faithfulness``(전체 top-k 재검증)가 있으면 ``unfaithful_*``
    (= 표준 환각)와, per-cited NS 중 flip 여부로 ``citation_imprecision_*`` /
    ``true_hallucination_*`` 를 함께 산출한다. 없으면 해당 키는 None/0(하위호환).

    각 result 는 ``is_answerable``(미지정 시 True), ``is_blocked``(미지정 시 False),
    ``n_sources``, ``verification=[{"status": ..., "sentence_id": ...}, ...]`` 를 포함한다고
    가정한다(``verification_faithfulness`` 는 선택). 순수 함수라 앱 의존 없이 단위 테스트 가능.
    """
    verification_total = 0
    not_supported_count = 0
    verification_total_answerable = 0
    not_supported_count_answerable = 0
    verification_total_delivered = 0
    not_supported_count_delivered = 0
    answerable_n = 0
    non_answerable_n = 0
    non_answerable_correct_refusal_n = 0
    blocked_n = 0

    for r in results:
        is_answerable = r.get("is_answerable", True)
        is_blocked = bool(r.get("is_blocked", False))
        if is_answerable:
            answerable_n += 1
            if is_blocked:
                blocked_n += 1
        else:
            non_answerable_n += 1
            # 검색 후보 0건 = 검색 단계에서 올바르게 거부(근거 없음 → 답변 시도 안 함).
            if not r.get("n_sources"):
                non_answerable_correct_refusal_n += 1
        delivered = is_answerable and not is_blocked
        for v in r.get("verification", []):
            verification_total += 1
            is_ns = v.get("status") == "NOT_SUPPORTED"
            if is_ns:
                not_supported_count += 1
            if is_answerable:
                verification_total_answerable += 1
                if is_ns:
                    not_supported_count_answerable += 1
            if delivered:
                verification_total_delivered += 1
                if is_ns:
                    not_supported_count_delivered += 1

    # feature17c-26 — faithfulness(전체 top-k 근거) 집계 + per-cited NS 의 flip 분석.
    faith_total = faith_ns = 0
    faith_total_ans = faith_ns_ans = 0
    faith_total_del = faith_ns_del = 0
    citation_imprecision_del = true_halluc_del = 0
    citation_imprecision_ans = true_halluc_ans = 0
    has_faithfulness = False

    for r in results:
        faith = r.get("verification_faithfulness")
        if not faith:
            continue
        has_faithfulness = True
        is_answerable = r.get("is_answerable", True)
        delivered = is_answerable and not bool(r.get("is_blocked", False))
        faith_status = {v.get("sentence_id"): v.get("status") for v in faith}
        for v in faith:
            is_ns = v.get("status") == "NOT_SUPPORTED"
            faith_total += 1
            faith_ns += is_ns
            if is_answerable:
                faith_total_ans += 1
                faith_ns_ans += is_ns
            if delivered:
                faith_total_del += 1
                faith_ns_del += is_ns
        # per-cited NS 문장이 전체 top-k 재판정에서 flip(SUPPORTED)되면 citation 정밀도
        # 아티팩트, 그대로 NS 면 진짜 환각(true hallucination).
        for v in r.get("verification", []):
            if v.get("status") != "NOT_SUPPORTED":
                continue
            still_ns = faith_status.get(v.get("sentence_id")) == "NOT_SUPPORTED"
            if is_answerable:
                if still_ns:
                    true_halluc_ans += 1
                else:
                    citation_imprecision_ans += 1
            if delivered:
                if still_ns:
                    true_halluc_del += 1
                else:
                    citation_imprecision_del += 1

    def _ratio(num: int, den: int) -> float | None:
        return num / den if den else None

    faithfulness_keys: dict[str, Any] = {
        # 표준 faithfulness (검색된 전체 top-k 근거 미지원 = 진짜 환각). None=재검증 데이터 없음.
        "unfaithful_ratio": _ratio(faith_ns, faith_total) if has_faithfulness else None,
        "unfaithful_count": faith_ns if has_faithfulness else None,
        "faithfulness_verification_total": faith_total if has_faithfulness else None,
        "unfaithful_ratio_answerable": (
            _ratio(faith_ns_ans, faith_total_ans) if has_faithfulness else None
        ),
        "unfaithful_count_answerable": faith_ns_ans if has_faithfulness else None,
        "unfaithful_ratio_delivered": (
            _ratio(faith_ns_del, faith_total_del) if has_faithfulness else None
        ),
        "unfaithful_count_delivered": faith_ns_del if has_faithfulness else None,
        # per-cited NS 분해: 오인용(citation precision) vs 진짜 환각.
        "citation_imprecision_count_answerable": (
            citation_imprecision_ans if has_faithfulness else None
        ),
        "true_hallucination_count_answerable": true_halluc_ans if has_faithfulness else None,
        "citation_imprecision_count_delivered": (
            citation_imprecision_del if has_faithfulness else None
        ),
        "true_hallucination_count_delivered": true_halluc_del if has_faithfulness else None,
    }

    return {
        # --- per-cited grounding (= citation precision) ---
        "not_supported_ratio": (
            not_supported_count / verification_total if verification_total else None
        ),
        "not_supported_count": not_supported_count,
        "verification_total": verification_total,
        "not_supported_ratio_answerable": (
            not_supported_count_answerable / verification_total_answerable
            if verification_total_answerable
            else None
        ),
        "not_supported_count_answerable": not_supported_count_answerable,
        "verification_total_answerable": verification_total_answerable,
        # feature17c-17 — 사용자 노출 환각(answerable AND not blocked).
        "not_supported_ratio_delivered": (
            not_supported_count_delivered / verification_total_delivered
            if verification_total_delivered
            else None
        ),
        "not_supported_count_delivered": not_supported_count_delivered,
        "verification_total_delivered": verification_total_delivered,
        "answerable_n_items": answerable_n,
        "non_answerable_n_items": non_answerable_n,
        "non_answerable_correct_refusal_n_items": non_answerable_correct_refusal_n,
        "blocked_n_items": blocked_n,
        # --- full-context grounding (= 표준 faithfulness) + flip 분해 (feature17c-26) ---
        **faithfulness_keys,
    }


def _compute_rouge_l_f1_avg(predictions: list[str], references: list[str]) -> float:
    """ROUGE-L F1 평균 — 설계서 §7.2.3 자동 평가 정합 (rouge-score 라이브러리).

    rouge-score 는 경량 (pure Python) 이라 평가 시점 lazy import. evaluation extras
    미설치 환경에서는 ImportError 즉시 발생 — ``pip install -e ".[evaluation]"`` 안내.
    """
    try:
        from rouge_score import rouge_scorer
    except ImportError as exc:
        raise ImportError(
            'rouge-score 미설치 — `pip install -e ".[evaluation]"` 후 재실행.'
        ) from exc
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=False)
    f1_scores = [
        scorer.score(ref, pred)["rougeL"].fmeasure
        for pred, ref in zip(predictions, references, strict=True)
    ]
    return sum(f1_scores) / len(f1_scores) if f1_scores else 0.0


def _compute_bert_score_f1_avg(predictions: list[str], references: list[str]) -> float:
    """BERTScore F1 평균 — 설계서 §7.2.3 자동 평가 정합 (bert-score 라이브러리).

    bert-score 는 transformers/torch 모델 다운로드 (~500MB, multilingual). 한국어
    질의 정합으로 ``lang="ko"`` 사용. evaluation extras 미설치 시 ImportError.
    """
    try:
        from bert_score import score
    except ImportError as exc:
        raise ImportError(
            'bert-score 미설치 — `pip install -e ".[evaluation]"` 후 재실행.'
        ) from exc
    _, _, f1_tensor = score(predictions, references, lang="ko", verbose=False)
    f1_list = f1_tensor.tolist() if hasattr(f1_tensor, "tolist") else list(f1_tensor)
    return sum(f1_list) / len(f1_list) if f1_list else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
