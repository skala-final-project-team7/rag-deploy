"""Cross-Encoder 재순위화 — 어댑터 추상 인터페이스 + Fake 구현 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인 Query 단계의 Cross-Encoder 재순위화 외부 모델 어댑터
          경계를 정의한다. ms-marco-MiniLM-L-12 등 sentence-transformers CrossEncoder
          모델이 무엇이든 동일한 인터페이스로 호출되도록 강제하고, 외부 모델 없이도
          단위 테스트가 통과하도록 결정론적인 Fake 구현을 함께 제공한다
          (`docs/rag-pipeline-design.md` §6 4.5·§8, `app/CLAUDE.md` §8 — 외부 호출은
          어댑터/클라이언트 계층으로 분리).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature9-B-1 — CrossEncoderReranker ABC +
    FakeCrossEncoderReranker
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 (sentence-transformers·torch는 실 어댑터 모듈에서만 요구)
--------------------------------------------------
"""

from abc import ABC, abstractmethod
from hashlib import sha256


class CrossEncoderReranker(ABC):
    """Cross-Encoder 재순위화 어댑터 추상 인터페이스.

    sentence-transformers ``CrossEncoder`` 등 (query, passage) 쌍의 관련도를 직접
    예측하는 모델 래퍼는 이 클래스를 상속한다. 본 어댑터의 책임은 점수 산출만 — Top-K
    선정·저신뢰 분기는 feature9-A의 ``app/query/rerank.py::select_reranked`` 가
    담당한다.

    반환 점수는 ``[0.0, 1.0]`` 범위여야 한다 — ``select_reranked`` 의 임계값
    (``NARROW_SCORE_THRESHOLD=0.30``, ``LOW_CONFIDENCE_THRESHOLD=0.20``) 정합. 실
    모델 어댑터(``CrossEncoderRerankerImpl``)는 Sigmoid 활성화 함수를 통해
    이 계약을 강제한다.
    """

    @abstractmethod
    def score(self, query: str, passages: list[str]) -> list[float]:
        """``(query, passage)`` 쌍들에 대한 관련도 점수를 반환한다.

        Args:
            query: 사용자 질의(또는 Query Rewriter가 확장한 쿼리).
            passages: 점수 대상 passage 텍스트 목록 — 1차 검색(feature9-A
                ``select_top_candidates``)의 Top-N 후보 텍스트.

        Returns:
            ``passages`` 와 같은 길이의 ``[0.0, 1.0]`` 점수 목록. 빈 입력은 빈 목록.
        """


class FakeCrossEncoderReranker(CrossEncoderReranker):
    """결정론적 해시 기반 Cross-Encoder Reranker — 테스트·PoC용 (외부 의존성 0).

    같은 ``(query, passage)`` 쌍은 항상 같은 점수를 반환한다(재현성). 실제 cross-encoder
    모델 다운로드(약 130 MB) 없이 단위 테스트가 통과하도록 한다. 점수는 sha256 해시의
    첫 8바이트를 [0.0, 1.0]으로 정규화한 값이다.
    """

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [self._score_one(query, passage) for passage in passages]

    @staticmethod
    def _score_one(query: str, passage: str) -> float:
        # sha256 결정론 해시 → 첫 8바이트 → [0.0, 1.0) 정규화
        digest = sha256(f"{query}\x00{passage}".encode()).digest()[:8]
        raw = int.from_bytes(digest, "big")
        return raw / 2**64
