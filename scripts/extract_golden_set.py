"""Golden Set 자동 추출 CLI [Pipeline 평가 도구].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature17b — 설계서 §6.3 의 Golden Set 채택 3 조건 AND 필터를
          ``scripts/run_evaluation.py`` 의 결과 JSON 에 적용해 Golden Set 을
          자동 추출한다. Golden Set 은 후속 회귀 평가 (feature17c 튜닝 vs
          baseline 비교) 의 안정 기준선이 된다.

          3 조건:
            (1) 답변 검증 PASS / SUPPORTED 만 (NOT_SUPPORTED 0건)
            (2) Cross-Encoder Top-1 점수 ≥ 임계값 (기본 0.85)
            (3) 사용자 피드백 Positive (또는 미제출)

          본 스크립트는 stdin / config 없이 결정론적으로 동작 — 동일 입력
          (evaluation_*.json + feedback.json + 임계값) → 동일 출력. 따라서
          CI 회귀 가능한 도구로 활용 가능.
작성일 : 2026-05-20
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-20, 최초 작성, feature17b — 평가 결과 JSON 기반 Golden Set 추출
    + 3 조건 AND 필터 + 임계값 스케일 자동 인식 (0~1 / 0~100) + 피드백 미보유
    환경 fallback (조건 #3 통과 처리) + 추출 요약 출력.
--------------------------------------------------
[호환성]
  - Python 3.11.x, 외부 의존성 없음 (stdlib 만 사용).
  - 사용법:
        # 기본 (피드백 미보유 환경 — 조건 #3 자동 통과)
        python scripts/extract_golden_set.py \\
            --evaluation-results reports/evaluation_20260520_120000.json

        # 피드백 파일 + 임계값 조정
        python scripts/extract_golden_set.py \\
            --evaluation-results reports/evaluation_20260520_120000.json \\
            --feedback-file reports/feedback.json \\
            --top1-threshold 0.90

  - 피드백 파일 형식: ``{"<eval_id>": "positive" | "negative"}``. 누락된
    eval_id 는 '미제출' 로 간주되어 조건 #3 통과 처리.
  - 임계값 스케일: ``--top1-threshold 0.85`` (0~1) 또는 ``85`` (0~100) 모두
    허용. ≤ 1.0 이면 0~1 스케일, 그 외 0~100 스케일로 자동 인식.
    ``Source.score`` 는 int 0~100 이므로 내부적으로 0~100 스케일로 변환 비교.
--------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# 설계서 §6.3 의 검증 통과 status — PASS / SUPPORTED 만 인정.
# NOT_SUPPORTED 가 1건이라도 있으면 Golden Set 제외.
VERIFICATION_PASS_STATUSES = frozenset({"PASS", "SUPPORTED"})


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "평가 결과 JSON 에 설계서 §6.3 3 조건 AND 필터를 적용해 Golden Set 을 자동 추출한다."
        ),
    )
    parser.add_argument(
        "--evaluation-results",
        type=Path,
        required=True,
        help="scripts/run_evaluation.py 가 생성한 결과 JSON 경로.",
    )
    parser.add_argument(
        "--feedback-file",
        type=Path,
        default=None,
        help=(
            "사용자 피드백 JSON (선택). 형식: {<eval_id>: 'positive'|'negative'}. "
            "미지정 시 모든 항목이 '미제출' 로 간주 → 조건 #3 자동 통과."
        ),
    )
    parser.add_argument(
        "--top1-threshold",
        type=float,
        default=0.80,
        help=(
            "Cross-Encoder Top-1 점수 임계값 (기본 0.80). 0~1 또는 0~100 스케일 "
            "모두 허용 — ≤ 1.0 이면 0~1, 그 외 0~100 으로 자동 인식. "
            "feature17c-2: temperature scaling(T=4) 도입으로 score 분포가 펴져 "
            "설계서 §6.3 의 0.85 를 T=4 기준 0.80(강관련 logit 5.5+ 통과)으로 재조정."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Golden Set JSON 저장 경로 (기본: reports/golden_set_<timestamp>.json).",
    )
    args = parser.parse_args()

    if not args.evaluation_results.exists():
        print(f"[err] evaluation-results not found: {args.evaluation_results}")
        return 1

    report = json.loads(args.evaluation_results.read_text())
    results: list[dict[str, Any]] = report.get("results", [])
    if not results:
        print("[err] 평가 결과 JSON 에 results 가 비어 있다.")
        return 1

    feedback_map = _load_feedback_map(args.feedback_file)
    threshold_score = _normalize_threshold(args.top1_threshold)

    print(f"[golden] evaluation-results = {args.evaluation_results}")
    print(f"[golden] n_results = {len(results)}")
    print(f"[golden] top1-threshold = {args.top1_threshold} → score >= {threshold_score} (0~100)")
    if args.feedback_file:
        print(f"[golden] feedback-file = {args.feedback_file} ({len(feedback_map)} 건)")
    else:
        print("[golden] feedback-file 미지정 — 모든 항목 '미제출' → 조건 #3 통과 처리")

    extracted, breakdown = _extract_golden(results, feedback_map, threshold_score)

    print()
    print(f"[golden] 추출 건수: {len(extracted)} / {len(results)}")
    print(f"[golden] 조건별 단독 통과: {breakdown}")
    if results:
        ratio = len(extracted) / len(results) * 100
        print(f"[golden] 추출률: {ratio:.1f}%")

    output_path = args.output or (
        Path("reports") / f"golden_set_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source_report": str(args.evaluation_results),
        "criteria": {
            "verification_pass_statuses": sorted(VERIFICATION_PASS_STATUSES),
            "top1_threshold_input": args.top1_threshold,
            "top1_threshold_score_0_100": threshold_score,
            "feedback_file": str(args.feedback_file) if args.feedback_file else None,
            "feedback_unsubmitted_passes": True,
        },
        "summary": {
            "n_results": len(results),
            "n_extracted": len(extracted),
            "extraction_ratio": (len(extracted) / len(results) if results else None),
            "single_condition_pass_counts": breakdown,
        },
        "golden_set": extracted,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"[golden] 저장 = {output_path}")
    print()
    print("[가이드] Golden Set 은 후속 회귀 평가 (feature17c 튜닝 결과 vs baseline) ")
    print("        비교 기준선으로 활용한다. 사용자 피드백 인프라 구축 후 feedback-file")
    print("        을 명시하면 조건 #3 이 엄격해진다 (Negative 항목 제외).")
    return 0


def _load_feedback_map(feedback_path: Path | None) -> dict[str, str]:
    """피드백 JSON 로드 — 미지정/미존재 시 빈 dict 반환.

    형식: ``{"EVAL-001": "positive", "EVAL-002": "negative", ...}``.
    값은 소문자로 정규화한다. 누락된 eval_id 는 '미제출' 로 간주.
    """
    if feedback_path is None:
        return {}
    if not feedback_path.exists():
        print(f"[warn] feedback-file 미존재: {feedback_path} — 빈 매핑으로 진행")
        return {}
    raw = json.loads(feedback_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(
            f"feedback-file 형식 오류 — dict 가 필요한데 {type(raw).__name__} 가 왔다."
        )
    return {str(k): str(v).lower() for k, v in raw.items()}


def _normalize_threshold(threshold: float) -> int:
    """임계값을 Source.score 와 같은 0~100 정수 스케일로 정규화.

    ≤ 1.0 → 0~1 스케일 → ``* 100`` 후 반올림. 그 외 → 0~100 으로 간주.
    """
    if threshold <= 1.0:
        return int(round(threshold * 100))
    return int(round(threshold))


def _check_verification_pass(result: dict[str, Any]) -> bool:
    """조건 #1 — verification 의 모든 status 가 PASS / SUPPORTED 인가."""
    verifications = result.get("verification") or []
    # verification 자체가 없는 항목 (생성 실패 / 차단 응답 등) 은 통과 처리하지
    # 않는다 — Golden Set 은 '검증된 우수 응답' 만 모은다.
    if not verifications:
        return False
    for v in verifications:
        status = str(v.get("status", "")).upper()
        if status not in VERIFICATION_PASS_STATUSES:
            return False
    return True


def _check_top1_score(result: dict[str, Any], threshold_score: int) -> bool:
    """조건 #2 — Cross-Encoder Top-1 score (0~100 int) ≥ threshold."""
    top1 = result.get("top1_score")
    if top1 is None:
        return False
    return int(top1) >= threshold_score


def _check_feedback(eval_id: str, feedback_map: dict[str, str]) -> bool:
    """조건 #3 — Positive 또는 미제출이면 통과. Negative 만 미통과."""
    fb = feedback_map.get(eval_id)
    if fb is None:
        return True  # 미제출 → 통과
    return fb == "positive"


def _extract_golden(
    results: list[dict[str, Any]],
    feedback_map: dict[str, str],
    threshold_score: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """3 조건 AND 필터 적용 + 조건별 단독 통과 건수 집계 (디버깅용)."""
    extracted: list[dict[str, Any]] = []
    counter: Counter[str] = Counter()
    for r in results:
        eval_id = str(r.get("id", ""))
        c1 = _check_verification_pass(r)
        c2 = _check_top1_score(r, threshold_score)
        c3 = _check_feedback(eval_id, feedback_map)
        if c1:
            counter["verification_pass"] += 1
        if c2:
            counter["top1_score"] += 1
        if c3:
            counter["feedback"] += 1
        if c1 and c2 and c3:
            extracted.append(
                {
                    "id": eval_id,
                    "query": r.get("query"),
                    "actual_intent": r.get("actual_intent"),
                    "top1_score": r.get("top1_score"),
                    "n_sources": r.get("n_sources"),
                    "answer_excerpt": r.get("answer_excerpt"),
                    "latency_ms": r.get("latency_ms"),
                    "feedback": feedback_map.get(eval_id, "not_submitted"),
                }
            )
    # 3 키 모두 명시 — 0 인 키도 누락 없이 reporting / 회귀 가능.
    return extracted, {
        "verification_pass": int(counter["verification_pass"]),
        "top1_score": int(counter["top1_score"]),
        "feedback": int(counter["feedback"]),
    }


if __name__ == "__main__":
    raise SystemExit(main())
