"""응답 포맷터 검증 (feature11-Pipeline) — rag-pipeline-design.md §6 4.8, api-spec.md.

format_response: 생성·검증을 거친 답변을 QueryResponse로 변환하고 "표준 분기 응답"
규칙(저신뢰 분기 → feedback_enabled=false, NOT_SUPPORTED 비율 > 50% → 답변 차단)을 적용한다.
"""

from app.query.formatter import BLOCKED_ANSWER_MESSAGE, format_response
from app.schemas.enums import Intent, LlmModel, SourceType, VerificationStatus
from app.schemas.response import QueryResponse, Source, Verification


def _source(score: int) -> Source:
    """Cross-Encoder 점수만 의미 있는 최소 Source 픽스처."""
    return Source(
        title="EKS 운영 > 개요",
        score=score,
        path="Cloud 운영 문서 > 개요",
        space_key="CLOUD",
        source_type=SourceType.PAGE,
        confluence_url="https://confluence.example/pages/100001",
        last_modified="2026-04-22T08:15:00+09:00",
        text_preview="EKS 클러스터 개요...",
    )


def _verification(status: VerificationStatus, sentence_id: int = 1) -> Verification:
    return Verification(sentence_id=sentence_id, status=status, cited_chunks=[1])


def test_normal_response_enables_feedback() -> None:
    sources = [_source(87), _source(60)]
    verification = [
        _verification(VerificationStatus.PASS, 1),
        _verification(VerificationStatus.SUPPORTED, 2),
    ]
    response = format_response(
        "EKS 노드는 32대입니다 [#1].",
        sources,
        verification,
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        4120,
    )
    assert isinstance(response, QueryResponse)
    assert response.answer == "EKS 노드는 32대입니다 [#1]."
    assert response.feedback_enabled is True
    assert response.sources == sources
    assert response.verification == verification
    assert response.intent is Intent.OPERATION_GUIDE
    assert response.used_llm is LlmModel.GPT_4O
    assert response.latency_ms == 4120


def test_low_confidence_disables_feedback() -> None:
    # 최고 Source 점수가 LOW_CONFIDENCE_SCORE(55) 미만이면 저신뢰 분기 — 답변은 유지하되
    # feedback_enabled=False (feature17c-2: temperature scaling 으로 임계 20→55 재조정)
    sources = [_source(50), _source(40)]
    response = format_response(
        "참고할 만한 답변",
        sources,
        [_verification(VerificationStatus.PASS)],
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        3000,
    )
    assert response.feedback_enabled is False
    assert response.answer == "참고할 만한 답변"


def test_low_confidence_boundary_score_55() -> None:
    # 최고 점수가 정확히 55이면 저신뢰가 아니다 (55 미만만 저신뢰)
    response = format_response(
        "답변",
        [_source(55)],
        [_verification(VerificationStatus.PASS)],
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        3000,
    )
    assert response.feedback_enabled is True


def test_empty_sources_is_low_confidence() -> None:
    response = format_response(
        "답변",
        [],
        [_verification(VerificationStatus.PASS)],
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        3000,
    )
    assert response.feedback_enabled is False


def test_verification_block_replaces_answer() -> None:
    # NOT_SUPPORTED 비율이 50%를 넘으면 답변을 차단하고 저신뢰 응답으로 대체한다
    verification = [
        _verification(VerificationStatus.NOT_SUPPORTED, 1),
        _verification(VerificationStatus.NOT_SUPPORTED, 2),
        _verification(VerificationStatus.PASS, 3),
    ]
    response = format_response(
        "근거 없는 원래 답변",
        [_source(80)],
        verification,
        Intent.INCIDENT_RESPONSE,
        LlmModel.GPT_4O,
        5000,
    )
    assert response.answer == BLOCKED_ANSWER_MESSAGE
    assert response.feedback_enabled is False


def test_verification_block_boundary_exactly_half() -> None:
    # NOT_SUPPORTED 비율이 정확히 50%이면 차단하지 않는다 (50% 초과만 차단)
    verification = [
        _verification(VerificationStatus.NOT_SUPPORTED, 1),
        _verification(VerificationStatus.NOT_SUPPORTED, 2),
        _verification(VerificationStatus.PASS, 3),
        _verification(VerificationStatus.SUPPORTED, 4),
    ]
    response = format_response(
        "원래 답변",
        [_source(80)],
        verification,
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        5000,
    )
    assert response.answer == "원래 답변"
    assert response.feedback_enabled is True


def test_empty_verification_is_not_blocked() -> None:
    response = format_response(
        "답변",
        [_source(80)],
        [],
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        3000,
    )
    assert response.answer == "답변"
    assert response.feedback_enabled is True


def test_block_takes_precedence_over_low_confidence() -> None:
    # 검증 차단과 저신뢰가 동시에 발생하면 차단(답변 대체)이 우선한다
    verification = [
        _verification(VerificationStatus.NOT_SUPPORTED, 1),
        _verification(VerificationStatus.NOT_SUPPORTED, 2),
        _verification(VerificationStatus.NOT_SUPPORTED, 3),
        _verification(VerificationStatus.PASS, 4),
    ]
    response = format_response(
        "원래 답변",
        [_source(5)],
        verification,
        Intent.INCIDENT_RESPONSE,
        LlmModel.GPT_4O,
        5000,
    )
    assert response.answer == BLOCKED_ANSWER_MESSAGE
    assert response.feedback_enabled is False


def test_passes_through_sources_and_verification_when_blocked() -> None:
    # 차단되어도 출처·검증 결과는 투명성을 위해 그대로 전달한다
    sources = [_source(80)]
    verification = [
        _verification(VerificationStatus.NOT_SUPPORTED, 1),
        _verification(VerificationStatus.NOT_SUPPORTED, 2),
        _verification(VerificationStatus.NOT_SUPPORTED, 3),
    ]
    response = format_response(
        "원래 답변",
        sources,
        verification,
        Intent.OPERATION_GUIDE,
        LlmModel.GPT_4O,
        5000,
    )
    assert response.sources == sources
    assert response.verification == verification
