"""Pipeline 노드 래퍼 — RETRIEVAL_EMPTY 표준 분기 + 답변 검증 1+2단계 병합 + 분기 함수.

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합 — Query LangGraph 그래프 조립에 필요한 Pipeline 노드
          래퍼를 한 곳에 모은다. 기존 [Pipeline] 단계(`app/query/*.py`)는 본 담당자
          영역으로 변경하지 않고, 그래프 조립 단계에서 필요한 어댑터 노드만 여기에
          추가한다 (`docs/api-spec.md` "표준 분기 응답", `docs/rag-pipeline-design.md`
          §6 4.7, `app/CLAUDE.md` §3·§5).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 — empty_retrieval_node /
    verify_pipeline_node / after_search_branch
  - 2026-05-19, feature17a — verify_pipeline_node 가 verification 결과를
    state 에 반영한 직후 ``verification_status_total{status}`` Prometheus
    카운터를 inc 한다. 설계서 §6.4 KPI "환각 비율 15% 이하" (NOT_SUPPORTED
    비율) 관측 지점 정합. 카운터는 default registry 에 등록되어 ``/metrics``
    로 자동 노출 (``app/api/main.py`` Instrumentator wiring 정합).
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 — 본 모듈은 노드 시그니처와 결정론적 분기만 다룬다. 검증 2단계
    LLM 평가자는 호출자가 주입한다 (`functools.partial` 또는 `QueryGraphDeps`).
--------------------------------------------------
"""

from collections.abc import Callable

from app.metrics import verification_status_total
from app.query.verifier import RuleVerificationResult, SentenceCheck, verify_answer_rules
from app.schemas.enums import Intent, LlmModel
from app.schemas.rag_state import RagState
from app.schemas.response import Verification

# api-spec.md "표준 분기 응답" — 검색 결과 0건 시 LLM 미호출 표준 메시지.
RETRIEVAL_EMPTY_ANSWER = "권한 범위 내에서 참고할 수 있는 문서를 찾지 못했습니다."

# 검증 2단계 LLM 평가자 시그니처 — Agent 코드 전달 시 동일 시그니처로 교체된다.
VerifyLLMEvaluator = Callable[..., list[Verification]]


def empty_retrieval_node(state: RagState) -> RagState:
    """RETRIEVAL_EMPTY 분기 — ACL 통과 후보 0건일 때 LLM 호출 없이 표준 응답으로 분기.

    답변 생성기·검증을 건너뛰고 곧장 포맷터로 향하는 분기를 위한 자리. 응답 포맷터
    (feature11-Pipeline)는 `Source.score < LOW_CONFIDENCE_SCORE` 또는 sources가 비면
    `feedback_enabled=False` 로 처리하므로 본 노드는 의도/모델만 정합하게 채우고
    answer를 표준 메시지로 두면 된다 (api-spec.md, rag-pipeline-design.md §8).
    """
    state.top_chunks = []
    state.sources = []
    state.verification = []
    state.answer = RETRIEVAL_EMPTY_ANSWER
    # 라우터가 채운 의도가 있으면 그대로, 없으면 운영가이드 fallback (rag-pipeline-design.md §8).
    if state.intent is None:
        state.intent = Intent.OPERATION_GUIDE
    # LLM 미호출이지만 응답 객체에는 used_llm 필드가 필요하다. 라우터가 결정한 target_llm을
    # 그대로 기록하고, 없으면 라우터·검증·히스토리·문서분석기 보조 모델 기본값(GPT_4O_MINI).
    state.used_llm = state.target_llm or LlmModel.GPT_4O_MINI
    return state


def verify_pipeline_node(
    state: RagState,
    *,
    llm_evaluator: VerifyLLMEvaluator,
) -> RagState:
    """답변 검증 1+2단계 병합 노드 (rag-pipeline-design.md §6 4.7).

    1단계 ``verify_answer_rules`` (feature10-Pipeline 완료) → suspicious 문장에 대해
    2단계 LLM 평가자(Agent, ``llm_evaluator``로 주입) → 결과를 sentence_id 정렬해
    ``state.verification`` 으로 병합한다.

    답변이 비어있으면 검증할 문장이 없으므로 verification을 빈 목록으로 둔다.
    2단계 호출은 1단계에서 의심 문장이 발견된 경우에만 일어난다 (LLM 비용 게이팅).

    Args:
        state: ``answer`` / ``top_chunks`` 를 읽고 ``verification`` 을 채운다.
        llm_evaluator: 검증 2단계 LLM 평가자 — Agent 담당자 코드 또는 stub.

    Returns:
        ``verification`` 이 채워진 RagState (입력 state를 갱신해 반환).
    """
    answer = state.answer or ""
    if not answer:
        state.verification = []
        return state

    rule_result: RuleVerificationResult = verify_answer_rules(answer, state.top_chunks)
    passed: list[Verification] = rule_result.passed_verifications()

    suspicious: list[SentenceCheck] = rule_result.suspicious_sentences
    if suspicious:
        evaluated = llm_evaluator(
            answer=answer,
            top_chunks=list(state.top_chunks),
            suspicious_sentences=suspicious,
        )
    else:
        evaluated = []

    # sentence_id 오름차순 정렬 — UI 표시 정합 (api-spec.md verification 배열).
    merged = sorted(passed + evaluated, key=lambda item: item.sentence_id)
    state.verification = merged
    # feature17a — verification status 분포 메트릭 (설계서 §6.4 환각 비율 KPI 관측).
    for item in merged:
        verification_status_total.labels(status=item.status.value).inc()
    return state


def after_search_branch(state: RagState) -> str:
    """hybrid_search 노드 직후의 conditional edges 분기 키.

    candidates가 비어 있으면 RETRIEVAL_EMPTY 분기(``empty``), 그 외에는 재순위화
    단계(``rerank``)로 진행한다. LangGraph 그래프 빌더가 본 함수를
    ``add_conditional_edges`` 분기 함수로 등록한다.
    """
    return "rerank" if state.candidates else "empty"
