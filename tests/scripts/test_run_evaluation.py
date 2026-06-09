"""scripts/run_evaluation.py — ROUGE-L / BERTScore helper 회귀 (feature17b).

라이브러리 의존성 (rouge-score / bert-score) 은 monkeypatch 로 sentinel 응답으로
대체해 evaluation extras 미설치 환경에서도 회귀 가능. helper 함수의 평균 산출
로직과 옵션 미설치 시 ImportError 분기를 검증한다.
"""

from __future__ import annotations

import sys
import types

import pytest


def test_compute_rouge_l_f1_avg_with_fake_scorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """rouge_scorer.RougeScorer mock 으로 평균 산출 회귀."""

    class _FakeScore:
        def __init__(self, fmeasure: float) -> None:
            self.fmeasure = fmeasure

    class _FakeScorer:
        def __init__(self, types: list[str], use_stemmer: bool) -> None:
            self.types = types
            self.use_stemmer = use_stemmer

        def score(self, ref: str, pred: str) -> dict:
            # ref / pred 의 길이 차이로 F1 을 단순 매핑 (회귀 검증용 deterministic).
            common = min(len(ref), len(pred))
            return {"rougeL": _FakeScore(common / max(len(ref), len(pred), 1))}

    rouge_module = types.ModuleType("rouge_score")
    rouge_scorer_module = types.ModuleType("rouge_score.rouge_scorer")
    rouge_scorer_module.RougeScorer = _FakeScorer  # type: ignore[attr-defined]
    rouge_module.rouge_scorer = rouge_scorer_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rouge_score", rouge_module)
    monkeypatch.setitem(sys.modules, "rouge_score.rouge_scorer", rouge_scorer_module)

    from scripts.run_evaluation import _compute_rouge_l_f1_avg

    avg = _compute_rouge_l_f1_avg(
        predictions=["abc", "abcdef"],
        references=["abcd", "abcdef"],
    )
    # (3/4 + 6/6) / 2 = (0.75 + 1.0) / 2 = 0.875
    assert avg == pytest.approx(0.875)


def test_compute_rouge_l_f1_avg_raises_without_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rouge-score 미설치 시 ImportError + 안내 메시지."""
    # rouge_score 모듈을 sys.modules 에서 제거 → import 실패 유도.
    monkeypatch.setitem(sys.modules, "rouge_score", None)  # type: ignore[arg-type]
    from scripts.run_evaluation import _compute_rouge_l_f1_avg

    with pytest.raises(ImportError, match="evaluation"):
        _compute_rouge_l_f1_avg(["pred"], ["ref"])


def test_compute_bert_score_f1_avg_with_fake_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bert_score.score mock 으로 평균 산출 회귀."""

    def _fake_score(preds: list[str], refs: list[str], lang: str, verbose: bool) -> tuple:
        # 단순 deterministic F1 — 길이 매칭 비율.
        assert lang == "ko"
        scores = [
            min(len(p), len(r)) / max(len(p), len(r), 1) for p, r in zip(preds, refs, strict=True)
        ]
        # bert_score 는 (P, R, F1) tuple 반환. F1 만 사용.
        return scores, scores, _FakeTensor(scores)

    class _FakeTensor:
        def __init__(self, values: list[float]) -> None:
            self._values = values

        def tolist(self) -> list[float]:
            return self._values

    bert_module = types.ModuleType("bert_score")
    bert_module.score = _fake_score  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bert_score", bert_module)

    from scripts.run_evaluation import _compute_bert_score_f1_avg

    avg = _compute_bert_score_f1_avg(
        predictions=["abc", "abcdef"],
        references=["abcd", "abcdef"],
    )
    assert avg == pytest.approx(0.875)


def test_compute_bert_score_f1_avg_raises_without_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bert-score 미설치 시 ImportError + 안내 메시지."""
    monkeypatch.setitem(sys.modules, "bert_score", None)  # type: ignore[arg-type]
    from scripts.run_evaluation import _compute_bert_score_f1_avg

    with pytest.raises(ImportError, match="evaluation"):
        _compute_bert_score_f1_avg(["pred"], ["ref"])


# ---------------------------------------------------------------------------
# feature17b 정밀 매칭 회귀 (2026-05-20) — _load_page_id_to_webui_link / _precision_match
# ---------------------------------------------------------------------------


def test_load_page_id_to_webui_link_reads_confluence_and_datadog(tmp_path) -> None:
    """samples 의 두 JSON 파일에서 page_id → webui_link 매핑이 합쳐져 로드된다."""
    import json

    from scripts.run_evaluation import _load_page_id_to_webui_link

    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "confluence_sample_data.json").write_text(
        json.dumps(
            {
                "single_page_responses": [
                    {"id": "100001", "_links": {"webui": "/display/CLOUD/A"}},
                    {"id": "100002", "_links": {"webui": "/display/CLOUD/B"}},
                ]
            }
        )
    )
    (samples_dir / "datadog_docs.json").write_text(
        json.dumps(
            {
                "single_page_responses": [
                    {"id": "dd001", "_links": {"webui": "https://docs/x"}},
                ]
            }
        )
    )

    mapping = _load_page_id_to_webui_link(samples_dir=samples_dir)
    assert mapping == {
        "100001": "/display/CLOUD/A",
        "100002": "/display/CLOUD/B",
        "dd001": "https://docs/x",
    }


def test_load_page_id_to_webui_link_returns_empty_when_samples_missing(tmp_path) -> None:
    """samples 디렉토리가 없거나 파일이 없으면 빈 dict 반환 (호출 측이 fallback)."""
    from scripts.run_evaluation import _load_page_id_to_webui_link

    mapping = _load_page_id_to_webui_link(samples_dir=tmp_path / "no_such")
    assert mapping == {}


def _fake_source(confluence_url: str):
    """Source 의 confluence_url 만 채운 가벼운 sentinel."""
    return types.SimpleNamespace(confluence_url=confluence_url)


def test_precision_match_strict_hit_via_webui_link() -> None:
    """expected_page_ids 의 webui_link 중 하나가 sources confluence_url 에 있으면 hit."""
    from scripts.run_evaluation import _precision_match

    mapping = {"100001": "/display/CLOUD/A", "100002": "/display/CLOUD/B"}
    sources = [_fake_source("/display/CLOUD/B"), _fake_source("/display/CLOUD/Z")]

    assert _precision_match(sources, {"100001", "100002"}, mapping) is True


def test_precision_match_strict_miss_when_webui_link_not_in_sources() -> None:
    """expected_page_ids 의 webui_link 가 sources 에 없으면 miss."""
    from scripts.run_evaluation import _precision_match

    mapping = {"100001": "/display/CLOUD/A"}
    sources = [_fake_source("/display/CLOUD/Z")]

    assert _precision_match(sources, {"100001"}, mapping) is False


def test_precision_match_falls_back_to_loose_when_mapping_empty() -> None:
    """samples lookup 부재 시 sources 비어 있지 않으면 hit (약식)."""
    from scripts.run_evaluation import _precision_match

    sources = [_fake_source("/whatever")]

    # mapping 자체가 비어 있음 → loose fallback.
    assert _precision_match(sources, {"100001"}, {}) is True


def test_precision_match_returns_false_when_no_expected_page_ids() -> None:
    """expected_page_ids 가 비어 있으면 항상 miss (집계 대상 제외)."""
    from scripts.run_evaluation import _precision_match

    sources = [_fake_source("/display/CLOUD/A")]
    assert _precision_match(sources, set(), {"100001": "/display/CLOUD/A"}) is False


# ---------------------------------------------------------------------------
# feature17c-9 — Pool 가중치 그리드 서치 오버라이드 파서
# ---------------------------------------------------------------------------


def test_parse_pool_weights_maps_short_keys_to_pool_names() -> None:
    """title/content/label 단축키 → title_pool/content_pool/label_pool 매핑 + float 변환."""
    from scripts.run_evaluation import _parse_pool_weights

    assert _parse_pool_weights("title:0.25,content:0.6,label:0.15") == {
        "title_pool": 0.25,
        "content_pool": 0.6,
        "label_pool": 0.15,
    }


def test_parse_pool_weights_rejects_unknown_key() -> None:
    """미지의 Pool 키는 ValueError."""
    from scripts.run_evaluation import _parse_pool_weights

    with pytest.raises(ValueError, match="미지의 Pool"):
        _parse_pool_weights("title:0.5,body:0.5,label:0.0")


def test_parse_pool_weights_requires_all_three_pools() -> None:
    """3 Pool 을 모두 명시하지 않으면 ValueError."""
    from scripts.run_evaluation import _parse_pool_weights

    with pytest.raises(ValueError, match="3 Pool"):
        _parse_pool_weights("title:0.5,content:0.5")


def test_parse_pool_weights_rejects_malformed_item() -> None:
    """':' 없는 항목은 ValueError."""
    from scripts.run_evaluation import _parse_pool_weights

    with pytest.raises(ValueError, match="잘못된 pool-weights"):
        _parse_pool_weights("title=0.5,content:0.3,label:0.2")


# ---------------------------------------------------------------------------
# feature17c-13 — 환각 측정 공정화 (_summarize_hallucination)
# ---------------------------------------------------------------------------


def _ns(*statuses: str) -> list[dict[str, str]]:
    """verification status 목록을 result 형태로 변환."""
    return [{"sentence_id": i, "status": s} for i, s in enumerate(statuses)]


def test_summarize_hallucination_separates_answerable() -> None:
    """is_answerable=false 항목의 NOT_SUPPORTED 는 answerable 지표에서 제외된다."""
    from scripts.run_evaluation import _summarize_hallucination

    results = [
        # answerable: 3문장 중 1 NOT_SUPPORTED
        {
            "is_answerable": True,
            "n_sources": 3,
            "verification": _ns("SUPPORTED", "NOT_SUPPORTED", "PASS"),
        },
        # answerable: 1문장 SUPPORTED
        {"is_answerable": True, "n_sources": 2, "verification": _ns("SUPPORTED")},
        # non-answerable: 1 NOT_SUPPORTED (올바른 거부) → answerable 집계 제외
        {"is_answerable": False, "n_sources": 0, "verification": _ns("NOT_SUPPORTED")},
    ]
    out = _summarize_hallucination(results)

    # 전체: 5문장 중 2 NOT_SUPPORTED
    assert out["verification_total"] == 5
    assert out["not_supported_count"] == 2
    assert out["not_supported_ratio"] == pytest.approx(2 / 5)
    # answerable 만: 4문장 중 1 NOT_SUPPORTED (non-answerable 1건 분리)
    assert out["verification_total_answerable"] == 4
    assert out["not_supported_count_answerable"] == 1
    assert out["not_supported_ratio_answerable"] == pytest.approx(1 / 4)
    assert out["answerable_n_items"] == 2
    assert out["non_answerable_n_items"] == 1


def test_summarize_hallucination_defaults_missing_flag_to_answerable() -> None:
    """is_answerable 미지정 항목은 answerable(True)로 집계된다."""
    from scripts.run_evaluation import _summarize_hallucination

    results = [
        {"n_sources": 1, "verification": _ns("NOT_SUPPORTED", "SUPPORTED")},
    ]
    out = _summarize_hallucination(results)

    assert out["answerable_n_items"] == 1
    assert out["non_answerable_n_items"] == 0
    assert out["verification_total_answerable"] == 2
    assert out["not_supported_count_answerable"] == 1
    # 전체와 answerable 이 동일 (모두 answerable)
    assert out["not_supported_ratio"] == out["not_supported_ratio_answerable"]


def test_summarize_hallucination_separates_blocked_from_delivered() -> None:
    """is_blocked 항목은 사용자 노출(delivered) 환각 지표에서 분리되고 blocked_n_items 로 집계."""
    from scripts.run_evaluation import _summarize_hallucination

    results = [
        # delivered(answerable & not blocked): 2문장 중 1 NOT_SUPPORTED
        {
            "is_answerable": True,
            "is_blocked": False,
            "n_sources": 3,
            "verification": _ns("NOT_SUPPORTED", "SUPPORTED"),
        },
        # blocked(answerable but blocked): NOT_SUPPORTED 2문장 — delivered 에서 제외.
        {
            "is_answerable": True,
            "is_blocked": True,
            "n_sources": 2,
            "verification": _ns("NOT_SUPPORTED", "NOT_SUPPORTED"),
        },
    ]
    out = _summarize_hallucination(results)

    # answerable: 4문장 중 3 NOT_SUPPORTED (차단 포함)
    assert out["verification_total_answerable"] == 4
    assert out["not_supported_count_answerable"] == 3
    # delivered: 차단 제외 → 2문장 중 1 NOT_SUPPORTED
    assert out["verification_total_delivered"] == 2
    assert out["not_supported_count_delivered"] == 1
    assert out["not_supported_ratio_delivered"] == pytest.approx(1 / 2)
    assert out["blocked_n_items"] == 1
    assert out["answerable_n_items"] == 2


# ---------------------------------------------------------------------------
# feature17c-26 — 측정 이원화 (faithfulness + citation precision)
# ---------------------------------------------------------------------------


def test_summarize_hallucination_dual_metric_and_flip() -> None:
    """verification_faithfulness 가 있으면 표준 faithfulness(unfaithful_*)와 per-cited NS의
    flip 분해(citation_imprecision / true_hallucination)를 함께 산출한다."""
    from scripts.run_evaluation import _summarize_hallucination

    results = [
        {
            "is_answerable": True,
            "is_blocked": False,
            "n_sources": 3,
            # per-cited: 2 NS (s1, s2)
            "verification": [
                {"sentence_id": 1, "status": "NOT_SUPPORTED", "cited_chunks": [1]},
                {"sentence_id": 2, "status": "NOT_SUPPORTED", "cited_chunks": [1]},
                {"sentence_id": 3, "status": "SUPPORTED", "cited_chunks": [2]},
            ],
            # 전체 top-k 재검증: s1 flip(SUPPORTED=오인용), s2 still NS(진짜 환각)
            "verification_faithfulness": [
                {"sentence_id": 1, "status": "SUPPORTED"},
                {"sentence_id": 2, "status": "NOT_SUPPORTED"},
                {"sentence_id": 3, "status": "SUPPORTED"},
            ],
        },
    ]
    out = _summarize_hallucination(results)

    # per-cited(citation precision)는 기존대로 유지
    assert out["not_supported_count_delivered"] == 2
    assert out["verification_total_delivered"] == 3
    # faithfulness(전체 top-k): 3문장 중 1 NS
    assert out["unfaithful_count_delivered"] == 1
    assert out["unfaithful_ratio_delivered"] == pytest.approx(1 / 3)
    # flip 분해: per-cited NS 2건 = 오인용 1 + 진짜 환각 1
    assert out["citation_imprecision_count_delivered"] == 1
    assert out["true_hallucination_count_delivered"] == 1


def test_summarize_hallucination_faithfulness_absent_is_none() -> None:
    """verification_faithfulness 가 없으면 faithfulness 키는 None(하위호환)."""
    from scripts.run_evaluation import _summarize_hallucination

    out = _summarize_hallucination(
        [{"is_answerable": True, "verification": [{"sentence_id": 0, "status": "NOT_SUPPORTED"}]}]
    )
    assert out["not_supported_ratio"] == pytest.approx(1.0)
    assert out["unfaithful_ratio"] is None
    assert out["unfaithful_count_delivered"] is None
    assert out["citation_imprecision_count_delivered"] is None


def test_compute_faithfulness_verification_uses_full_context() -> None:
    """rule 1단계는 그대로, 의심 문장만 full_context=True 로 2단계 재판정한다."""
    from scripts.run_evaluation import _compute_faithfulness_verification

    class _V:
        def __init__(self, sid: int, status: str) -> None:
            self.sentence_id = sid
            self.status = status
            self.cited_chunks: list[int] = []

    class _Rule:
        def __init__(self, passed: list, suspicious: list) -> None:
            self._passed = passed
            self.suspicious_sentences = suspicious

        def passed_verifications(self) -> list:
            return self._passed

    captured: dict = {}

    def fake_rules(answer: str, top_chunks: list) -> _Rule:
        return _Rule([_V(1, "SUPPORTED")], [object()])  # s1 PASS, s2 의심

    def fake_eval(*, answer, top_chunks, suspicious_sentences, provider, config, full_context):
        captured["full_context"] = full_context
        captured["n"] = len(suspicious_sentences)
        return [_V(2, "SUPPORTED")]

    res = _compute_faithfulness_verification(
        answer="a. b.",
        top_chunks=["x"],
        verify_rules=fake_rules,
        evaluate=fake_eval,
        provider="P",
        config="C",
    )
    assert [v.sentence_id for v in res] == [1, 2]
    assert captured["full_context"] is True
    assert captured["n"] == 1
    # 빈 답변 → 재검증 없이 빈 목록
    assert (
        _compute_faithfulness_verification(
            answer="   ",
            top_chunks=[],
            verify_rules=fake_rules,
            evaluate=fake_eval,
            provider=None,
            config=None,
        )
        == []
    )


def test_summarize_hallucination_counts_non_answerable_correct_refusal() -> None:
    """non-answerable 항목 중 검색 후보 0건은 '올바른 거부'로 카운트된다."""
    from scripts.run_evaluation import _summarize_hallucination

    results = [
        # non-answerable + n_sources=0 → 올바른 거부
        {"is_answerable": False, "n_sources": 0, "verification": _ns("NOT_SUPPORTED")},
        # non-answerable + n_sources=1 → 거부 실패(답변 시도) → 카운트 안 함
        {"is_answerable": False, "n_sources": 1, "verification": _ns("NOT_SUPPORTED", "PASS")},
        # answerable → 거부 카운트 무관
        {"is_answerable": True, "n_sources": 2, "verification": _ns("SUPPORTED")},
    ]
    out = _summarize_hallucination(results)

    assert out["non_answerable_n_items"] == 2
    assert out["non_answerable_correct_refusal_n_items"] == 1


def test_summarize_hallucination_handles_empty_results() -> None:
    """결과가 없으면 비율은 None, 카운트는 0."""
    from scripts.run_evaluation import _summarize_hallucination

    out = _summarize_hallucination([])

    assert out["not_supported_ratio"] is None
    assert out["not_supported_ratio_answerable"] is None
    assert out["verification_total"] == 0
    assert out["answerable_n_items"] == 0
    assert out["non_answerable_n_items"] == 0


# ---------------------------------------------------------------------------
# feature17c-15 — 검증 진단 (--debug-verify) 순수 헬퍼
# ---------------------------------------------------------------------------


def test_classify_token_location_in_cited() -> None:
    """인용 청크에 존재하면 in_cited (1단계 false positive 후보)."""
    from scripts.run_evaluation import _classify_token_location

    assert _classify_token_location(grounded_in_cited=True, grounded_in_any=True) == "in_cited"


def test_classify_token_location_in_other_topk() -> None:
    """인용엔 없으나 다른 Top-K 에 있으면 in_other_topk (citation 정밀도)."""
    from scripts.run_evaluation import _classify_token_location

    assert (
        _classify_token_location(grounded_in_cited=False, grounded_in_any=True) == "in_other_topk"
    )


def test_classify_token_location_absent() -> None:
    """어느 Top-K 에도 없으면 absent (recall·생성 갭)."""
    from scripts.run_evaluation import _classify_token_location

    assert _classify_token_location(grounded_in_cited=False, grounded_in_any=False) == "absent"


def _verify_rec(
    *,
    sid: int,
    final: str,
    suspicious: bool = True,
    raw_label: str | None = None,
    locations: list[str] | None = None,
) -> dict:
    return {
        "sentence_id": sid,
        "sentence": f"sentence {sid}",
        "cited_chunks": [1],
        "checkable_tokens": [],
        "unverified_tokens": [
            {"token": f"t{i}", "location": loc} for i, loc in enumerate(locations or [])
        ],
        "suspicious": suspicious,
        "stage2_raw_label": raw_label,
        "stage2_score": None,
        "stage2_reason": None,
        "final_status": final,
    }


def test_summarize_debug_verify_distributions() -> None:
    """final 상태/raw label/토큰 위치 분포를 집계한다."""
    from scripts.run_evaluation import _summarize_debug_verify

    records = [
        _verify_rec(sid=1, final="PASS", suspicious=False),
        _verify_rec(
            sid=2,
            final="NOT_SUPPORTED",
            raw_label="low_confidence",
            locations=["absent", "in_other_topk"],
        ),
        _verify_rec(sid=3, final="NOT_SUPPORTED", raw_label="unsupported", locations=["absent"]),
        _verify_rec(sid=4, final="SUPPORTED", raw_label="supported", locations=[]),
    ]
    out = _summarize_debug_verify(records)

    assert out["n_sentences"] == 4
    assert out["final_status_dist"] == {"PASS": 1, "NOT_SUPPORTED": 2, "SUPPORTED": 1}
    # NOT_SUPPORTED 만 raw label 집계
    assert out["not_supported_raw_label_dist"] == {"low_confidence": 1, "unsupported": 1}
    assert out["unverified_token_location_dist"] == {"absent": 2, "in_other_topk": 1}


def test_summarize_debug_verify_fullctx_flip() -> None:
    """NOT_SUPPORTED 문장이 전체 top-k 재평가에서 supported 로 뒤집히는 수를 집계한다."""
    from scripts.run_evaluation import _summarize_debug_verify

    # agent SentenceLabel.value 는 대문자("SUPPORTED")이므로 대소문자 무관 비교 회귀.
    records = [
        # 인용청크 NOT_SUPPORTED → 전체 top-k 에서 SUPPORTED (오인용=citation 정밀도)
        {
            **_verify_rec(sid=1, final="NOT_SUPPORTED", raw_label="UNSUPPORTED"),
            "stage2_fullctx_label": "SUPPORTED",
        },
        # 인용청크 NOT_SUPPORTED → 전체 top-k 도 UNSUPPORTED (진짜 미근거)
        {
            **_verify_rec(sid=2, final="NOT_SUPPORTED", raw_label="UNSUPPORTED"),
            "stage2_fullctx_label": "UNSUPPORTED",
        },
        # PASS 문장은 fullctx 집계 대상 아님
        {**_verify_rec(sid=3, final="PASS", suspicious=False), "stage2_fullctx_label": None},
    ]
    out = _summarize_debug_verify(records)

    assert out["not_supported_fullctx_flip_to_supported"] == 1
    assert out["not_supported_fullctx_still_unsupported"] == 1


# ---------------------------------------------------------------------------
# feature17c-19 — full_context grounding leniency 검증 판정
# ---------------------------------------------------------------------------


def test_leniency_verdict_pass_when_all_controls_unsupported() -> None:
    """통제 문장이 모두 unsupported 면 평가자 판별력 있음 = PASS."""
    from scripts.run_evaluation import _leniency_verdict

    results = [{"label": "UNSUPPORTED"}, {"label": "unsupported"}]
    assert _leniency_verdict(results) == "PASS"


def test_leniency_verdict_fail_when_any_control_supported() -> None:
    """통제(거짓) 문장이 하나라도 supported 면 무분별 통과 = FAIL."""
    from scripts.run_evaluation import _leniency_verdict

    results = [{"label": "UNSUPPORTED"}, {"label": "SUPPORTED"}]
    assert _leniency_verdict(results) == "FAIL"


def test_leniency_verdict_inconclusive_when_no_labels() -> None:
    """라벨이 없으면(평가 미수행) INCONCLUSIVE."""
    from scripts.run_evaluation import _leniency_verdict

    assert _leniency_verdict([{"label": None}, {}]) == "INCONCLUSIVE"
