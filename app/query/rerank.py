"""Cross-Encoder 재순위화 선정 로직 — Top-K 선정 / 저신뢰 분기.

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인 Query 단계의 Cross-Encoder 재순위화 결정론적 선정
          로직을 제공한다. Cross-Encoder 점수로 Top-5를 뽑되, 5위 점수가 낮으면
          Top-3로 축소하고, 최고 점수가 낮으면 저신뢰 분기를 표시한다
          (rag-pipeline-design.md §6 4.5, §8).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature9-A — select_reranked / RerankResult (순수 로직)
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - NOTE: Cross-Encoder 모델 추론·RagState 배선은 feature9-B(노드 오케스트레이션)
          책임이다. 본 모듈은 (item, 점수)에 대한 순수 선정 함수만 제공한다.
--------------------------------------------------
"""

from dataclasses import dataclass

# rag-pipeline-design.md §6 4.5, §8
RERANK_TOP_K = 5  # 기본 재순위화 Top-K
RERANK_NARROW_TOP_K = 3  # 5위 점수가 낮을 때 축소하는 Top-K
# feature17c-2 (2026-05-20): Cross-Encoder temperature scaling(T=4) 도입으로 점수
# 분포가 sigmoid(원본) 대비 펴졌다(강관련 ~0.88 / 중관련 ~0.77 / 무관 ~0.51). 임계값을
# T=4 분포 기준으로 재조정 — NARROW 0.30→0.65, LOW 0.20→0.55. T 변경 시 함께 재조정.
NARROW_SCORE_THRESHOLD = 0.65  # 5위 점수가 이 미만이면 Top-3로 축소
LOW_CONFIDENCE_THRESHOLD = 0.55  # 최고 점수가 이 미만이면 저신뢰 분기


@dataclass
class RerankResult:
    """Cross-Encoder 재순위화 선정 결과.

    Attributes:
        top: (item id, Cross-Encoder 점수) 내림차순 — Top-5 또는 축소된 Top-3.
        is_low_confidence: 최고 점수가 LOW_CONFIDENCE_THRESHOLD 미만이거나 결과가
            비어 있으면 True. 응답을 '참고용'으로 제시하는 저신뢰 분기 신호다.
    """

    top: list[tuple[str, float]]
    is_low_confidence: bool


def select_reranked(scored: dict[str, float]) -> RerankResult:
    """Cross-Encoder 점수로 재순위화 Top-K를 선정한다 (rag-pipeline-design.md §6 4.5, §8).

    - 점수 내림차순 Top-5를 뽑는다. 동점은 item id 오름차순으로 정렬해 결정론을 보장한다.
    - Top-5가 모두 채워졌고 그 5위 점수가 NARROW_SCORE_THRESHOLD 미만이면 Top-3로 축소한다.
    - 선정 결과의 최고 점수가 LOW_CONFIDENCE_THRESHOLD 미만이거나 결과가 비어 있으면
      저신뢰 분기(is_low_confidence=True)로 표시한다.

    Args:
        scored: item id → Cross-Encoder 관련도 점수.

    Returns:
        RerankResult — 선정된 (item id, 점수) 목록과 저신뢰 분기 여부.
    """
    ordered = sorted(scored.items(), key=lambda item_score: (-item_score[1], item_score[0]))
    top = ordered[:RERANK_TOP_K]
    # 5위 점수가 임계 미만이면 출처 신뢰도가 낮으므로 Top-3로 축소한다.
    if len(top) == RERANK_TOP_K and top[-1][1] < NARROW_SCORE_THRESHOLD:
        top = top[:RERANK_NARROW_TOP_K]
    is_low_confidence = not top or top[0][1] < LOW_CONFIDENCE_THRESHOLD
    return RerankResult(top=top, is_low_confidence=is_low_confidence)
