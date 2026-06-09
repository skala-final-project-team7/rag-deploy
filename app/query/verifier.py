"""답변 검증 — 1단계 규칙 매칭 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인 Query 단계의 답변 검증 1단계(규칙 기반)를 구현한다.
          생성된 답변을 문장 단위로 분해해, 각 문장의 검증 토큰(수치·구조적 식별자)이
          인용한 청크 텍스트에 나타나는지 결정론적으로 대조한다. 확인되지 않은 토큰이
          있는 문장은 의심(suspicious)으로 FLAG되어 2단계 LLM 평가자로 넘어간다
          (rag-pipeline-design.md §6 4.7, conventions.md §5.5).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature10-Pipeline — verify_answer_rules / RuleVerificationResult
    (1단계 규칙 매칭. 2단계 LLM 평가자 [Agent]는 별도 담당자가 추가한다)
  - 2026-05-17, 코드 리뷰 후속(P2) — _token_grounded가 ASCII 토큰에 대해 워드 경계를
    적용해 false positive(예: '32'가 '320'에 매칭) 차단. 한글은 워드 경계 개념이 없어
    부분 문자열 매칭 유지(품질 튜닝 단계에서 Mecab 도입 후 교체)
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: 검증 토큰 추출은 PoC 휴리스틱(수치·구조적 식별자)이며 Mecab 형태소 분석은
          쓰지 않는다. 정밀 엔티티 추출은 품질 튜닝 단계에서 교체한다
          (rag-pipeline-design.md §6 4.7의 "엔티티/수치/코드 토큰, Mecab"과 정합 예정).
--------------------------------------------------
"""

import re
from dataclasses import dataclass, field

from app.schemas.chunk import Chunk
from app.schemas.enums import VerificationStatus
from app.schemas.response import Verification

# 문장 경계 — PoC 휴리스틱: 줄바꿈, 또는 종결 부호(.!?) 뒤 공백.
# 종결 부호 뒤에 공백이 없으면(예: 버전 'v1.29.1') 분리하지 않는다.
_SENTENCE_BOUNDARY = re.compile(r"\n+|(?<=[.!?])\s+")
# 인용 마커 [#n] — n은 1-based 인용 청크 번호.
_CITATION = re.compile(r"\[#([0-9]+)\]")
# 문장 맨 앞에 위치한 인용 마커 묶음 — 생성기가 직전 문장 끝(종결 부호 뒤)에 붙인
# 마커가 문장 분리 경계로 떨어져 나온 것이므로 직전 문장에 재부착한다 (feature17c-16).
_LEADING_CITATIONS = re.compile(r"^(?:\[#[0-9]+\]\s*)+")
# 검증 토큰 — 수치(2자 이상): 정수·소수·시각·버전 등.
_NUMBER = re.compile(r"[0-9][0-9,.:]+[0-9]|[0-9]{2,}")
# 검증 토큰 — 구조적 식별자: 구분자(-_./)를 포함하거나 대문자 약어(2자 이상).
# ASCII 문자 클래스만 사용한다 — `\w`는 한글을 포함해 조사가 식별자에 붙는다.
_STRUCTURED_TOKEN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_]*(?:[-_./][A-Za-z0-9_]+)+|[A-Z]{2,}[A-Z0-9-]*"
)


@dataclass
class SentenceCheck:
    """답변 문장 1개의 1단계 규칙 검증 결과.

    Attributes:
        sentence_id: 답변 내 1-based 문장 순번.
        sentence: 문장 원문.
        cited_chunks: 문장이 인용한 청크 번호(`[#n]`)들. 1-based, 등장 순서·중복 제거.
        unverified_tokens: 인용 청크 텍스트에서 확인되지 않은 검증 토큰. 비어 있지 않으면
            의심 문장이며 2단계 LLM 평가자가 SUPPORTED/NOT_SUPPORTED를 판정한다.
    """

    sentence_id: int
    sentence: str
    cited_chunks: list[int]
    unverified_tokens: list[str] = field(default_factory=list)

    @property
    def is_suspicious(self) -> bool:
        """확인되지 않은 검증 토큰이 하나라도 있으면 의심 문장."""
        return bool(self.unverified_tokens)


@dataclass
class RuleVerificationResult:
    """답변 전체의 1단계 규칙 검증 결과.

    PASS 문장은 ``passed_verifications()``로 최종 Verification이 확정되고, 의심 문장은
    ``suspicious_sentences``를 2단계 LLM 평가자(Agent)가 받아 판정한다. 두 결과의 병합과
    NOT_SUPPORTED 비율 기반 차단은 Query 그래프 통합 지점(feature11)에서 처리한다.
    """

    sentences: list[SentenceCheck]

    @property
    def suspicious_sentences(self) -> list[SentenceCheck]:
        """2단계 LLM 평가자로 넘길 의심 문장들."""
        return [check for check in self.sentences if check.is_suspicious]

    def has_suspicious_sentences(self) -> bool:
        """의심 문장이 하나라도 있으면 True (2단계 호출 게이팅에 사용)."""
        return any(check.is_suspicious for check in self.sentences)

    def passed_verifications(self) -> list[Verification]:
        """규칙 검증을 통과한 문장의 최종 Verification 목록(status=PASS)."""
        return [
            Verification(
                sentence_id=check.sentence_id,
                status=VerificationStatus.PASS,
                cited_chunks=check.cited_chunks,
            )
            for check in self.sentences
            if not check.is_suspicious
        ]


def _split_sentences(answer: str) -> list[str]:
    """답변을 문장 단위로 분리한다 (PoC 휴리스틱). 빈 문장은 제외한다.

    생성기는 인용 마커를 문장 끝(종결 부호 뒤)에 붙인다 — ``"문장1. [#1] 문장2. [#2]"``
    (``app/query/generator.py`` ``_compose_answer_with_citations`` 가 ``"{문장} {마커}"``
    로 조립). 그런데 종결 부호+공백 경계로 단순 분리하면 마커가 다음 문장 앞으로 떨어져,
    **첫 문장이 인용을 잃고 이후 문장이 직전 문장의 마커를 갖는 off-by-one** 이 생긴다
    (feature17c-16: 환각 NOT_SUPPORTED 과대 측정의 주원인 — 진단으로 확인). 따라서 분리
    후 조각 맨 앞의 인용 마커는 직전 문장 끝의 마커가 경계로 떨어진 것이므로 직전 문장에
    재부착한다. 마커가 종결 부호 앞("문장 [#1].")에 오는 경우엔 애초에 떨어지지 않아
    영향이 없다(기존 동작 보존).
    """
    raw = [part.strip() for part in _SENTENCE_BOUNDARY.split(answer) if part.strip()]
    if not raw:
        return []
    sentences: list[str] = [raw[0]]
    for piece in raw[1:]:
        match = _LEADING_CITATIONS.match(piece)
        if match:
            # 떨어져 나온 직전 문장의 인용 마커를 직전 문장 끝에 재부착.
            markers = match.group(0).strip()
            sentences[-1] = f"{sentences[-1]} {markers}"
            piece = piece[match.end() :].strip()
        if piece:
            sentences.append(piece)
    return sentences


def _extract_citations(sentence: str) -> list[int]:
    """문장의 `[#n]` 인용 마커에서 청크 번호를 등장 순서대로 추출한다 (중복 제거)."""
    numbers = (int(match) for match in _CITATION.findall(sentence))
    return list(dict.fromkeys(numbers))


def _gather_cited_text(cited_chunks: list[int], top_chunks: list[Chunk]) -> str:
    """인용한 청크들의 텍스트를 모은다. 범위를 벗어난 인용 번호는 건너뛴다."""
    texts = [
        top_chunks[number - 1].text for number in cited_chunks if 1 <= number <= len(top_chunks)
    ]
    return "\n".join(texts)


def _extract_checkable_tokens(sentence: str) -> list[str]:
    """문장에서 규칙 대조 대상 검증 토큰(수치·구조적 식별자)을 추출한다.

    인용 마커 `[#n]`은 제거한 뒤 추출해 마커 숫자가 토큰으로 잡히지 않게 한다.
    일반 단어는 패러프레이즈 여지가 커 노이즈가 되므로 검증 대상에서 제외한다. 구조적
    식별자(예: ``v1.29.1``)가 잡힌 영역에서는 같은 부위의 숫자(예: ``1.29.1``)를
    중복 추출하지 않도록 _STRUCTURED_TOKEN을 먼저 적용하고 그 자리를 비운 뒤 _NUMBER를
    돌린다 (P2 보완, 2026-05-17).
    """
    text = _CITATION.sub(" ", sentence)
    structured = _STRUCTURED_TOKEN.findall(text)
    remaining = _STRUCTURED_TOKEN.sub(" ", text)
    numbers = _NUMBER.findall(remaining)
    return list(dict.fromkeys(structured + numbers))


def _token_grounded(token: str, cited_text: str) -> bool:
    """검증 토큰이 인용 청크 텍스트에 나타나면 True (대소문자 무시).

    ASCII 전용 토큰(수치·영문 식별자)은 워드 경계 안에서만 매칭한다 — 예: 답변의 '32'가
    청크의 '320' 안에서 false positive 매칭되는 것을 차단한다(P2 보완 2026-05-17).
    한글 토큰은 워드 경계 개념이 없어 부분 문자열 매칭을 유지한다(Mecab 도입 단계에서
    교체).
    """
    token_lower = token.lower()
    text_lower = cited_text.lower()
    if all(ord(char) < 128 for char in token_lower):
        # ASCII 단어 문자/구분자(./-_) 경계만 검사한다. `\\w`는 Unicode 모드에서 한글까지
        # 포함하므로 명시적 ASCII 클래스를 쓴다. 한글 '32대' 같은 케이스에서 '32'가 정상
        # 매칭되도록 우측은 [A-Za-z0-9_]만 차단한다.
        pattern = r"(?<![A-Za-z0-9_./-])" + re.escape(token_lower) + r"(?![A-Za-z0-9_])"
        return re.search(pattern, text_lower) is not None
    return token_lower in text_lower


def verify_answer_rules(answer: str, top_chunks: list[Chunk]) -> RuleVerificationResult:
    """답변을 문장 단위로 1단계 규칙 검증한다 (rag-pipeline-design.md §6 4.7).

    각 문장의 검증 토큰(수치·구조적 식별자)이 그 문장이 인용한 청크 텍스트에 나타나는지
    대조한다. 확인되지 않은 토큰이 있는 문장은 의심(suspicious)으로 FLAG되어 2단계 LLM
    평가자로 넘어가고, 그 외 문장은 PASS로 확정된다. 인용이 없는데 검증 토큰이 있는
    문장은 대조할 근거가 없으므로 의심 문장이 된다.

    Args:
        answer: 생성기가 만든 답변 텍스트. 각 문장에 `[#n]` 인용 마커를 포함할 수 있다.
        top_chunks: 재순위화 Top-K 청크. `[#n]`은 이 목록의 n번째(1-based)를 가리킨다.

    Returns:
        문장별 SentenceCheck를 담은 RuleVerificationResult.
    """
    checks: list[SentenceCheck] = []
    for index, sentence in enumerate(_split_sentences(answer), start=1):
        cited_chunks = _extract_citations(sentence)
        cited_text = _gather_cited_text(cited_chunks, top_chunks)
        tokens = _extract_checkable_tokens(sentence)
        unverified = [token for token in tokens if not _token_grounded(token, cited_text)]
        checks.append(
            SentenceCheck(
                sentence_id=index,
                sentence=sentence,
                cited_chunks=cited_chunks,
                unverified_tokens=unverified,
            )
        )
    return RuleVerificationResult(sentences=checks)
