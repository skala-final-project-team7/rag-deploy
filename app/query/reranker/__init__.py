"""app.query.reranker — Cross-Encoder 재순위화 어댑터 패키지 [Pipeline].

분리 의도 (app/CLAUDE.md §8 — 외부 호출은 어댑터/클라이언트 계층으로 분리):

- ``base.py`` — ``CrossEncoderReranker`` ABC + ``FakeCrossEncoderReranker``.
  외부 의존성 0. 단위 테스트는 이 모듈만으로 동작한다.
- ``cross_encoder.py`` — ``CrossEncoderRerankerImpl`` (sentence-transformers
  ``CrossEncoder`` 래퍼). embedding extra 필요.

실 어댑터(``CrossEncoderRerankerImpl``)는 무거운 의존성 때문에 본 ``__init__`` 에서
re-export하지 않는다. 호출자는 명시적으로
``from app.query.reranker.cross_encoder import CrossEncoderRerankerImpl`` 같이 import한다.

본 패키지는 (query, passage) 쌍의 관련도 점수 산출만 담당한다. Top-K 선정·저신뢰 분기는
feature9-A의 ``app/query/rerank.py::select_reranked`` 가 담당하며, 본 어댑터의 출력
dict[chunk_id → score] 를 그대로 입력으로 받는다.
"""

from app.query.reranker.base import CrossEncoderReranker, FakeCrossEncoderReranker

__all__ = [
    "CrossEncoderReranker",
    "FakeCrossEncoderReranker",
]
