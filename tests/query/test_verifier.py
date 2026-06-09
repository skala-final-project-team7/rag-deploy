"""답변 검증 1단계 규칙 매칭 검증 (feature10-Pipeline) — rag-pipeline-design.md §6 4.7.

verify_answer_rules: 답변을 문장 단위로 분해해 검증 토큰(수치·구조적 식별자)이 인용
청크 텍스트에 나타나는지 대조하고, 미검증 토큰이 있는 문장을 suspicious로 FLAG한다.
"""

from app.query.verifier import RuleVerificationResult, verify_answer_rules
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import SourceType, VerificationStatus


def _chunk(text: str) -> Chunk:
    """검증 토큰 대조용 최소 Chunk 픽스처 — verify_answer_rules는 chunk.text만 사용한다."""
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            chunk_id="chunk-0",
            page_id="CONF-PAGE-1",
            page_title="EKS 운영",
            section_header="개요",
            section_path="개요",
            chunk_index=0,
            doc_type="operation",
            space_key="CLOUD",
            allowed_groups=["space:CLOUD"],
            allowed_users=[],
            webui_link="/display/CLOUD/eks",
            last_modified="2026-04-22T08:15:00+09:00",
            source_type=SourceType.PAGE,
            token_count=10,
        ),
    )


def test_grounded_sentence_passes() -> None:
    answer = "prod-main-eks 클러스터는 노드 32대를 운영합니다 [#1]."
    result = verify_answer_rules(
        answer, [_chunk("prod-main-eks 클러스터는 평균 32대 노드를 운영한다")]
    )
    assert isinstance(result, RuleVerificationResult)
    assert len(result.sentences) == 1
    check = result.sentences[0]
    # 검증 토큰(prod-main-eks, 32)이 모두 인용 청크에 있어 의심스럽지 않다
    assert check.is_suspicious is False
    assert check.cited_chunks == [1]
    assert check.unverified_tokens == []


def test_hallucinated_number_is_suspicious() -> None:
    # 인용 청크에 없는 수치를 주장하면 의심 문장으로 FLAG
    result = verify_answer_rules(
        "노드는 99대 운영됩니다 [#1].", [_chunk("평균 32대 노드를 운영한다")]
    )
    check = result.sentences[0]
    assert check.is_suspicious is True
    assert "99" in check.unverified_tokens


def test_unsourced_claim_is_suspicious() -> None:
    # 검증 토큰이 있는데 [#n] 인용이 없으면 의심 문장 (출처 없는 주장)
    result = verify_answer_rules("노드는 32대 운영됩니다.", [_chunk("평균 32대 노드")])
    check = result.sentences[0]
    assert check.cited_chunks == []
    assert check.is_suspicious is True
    assert "32" in check.unverified_tokens


def test_filler_sentence_passes() -> None:
    # 검증 토큰이 없는 연결/안내 문장은 검증 대상이 없어 PASS
    result = verify_answer_rules("다음 절차를 따르세요.", [_chunk("아무 내용")])
    assert result.sentences[0].is_suspicious is False
    assert result.sentences[0].unverified_tokens == []


def test_out_of_range_citation_is_suspicious() -> None:
    # 범위를 벗어난 인용 번호는 텍스트를 제공하지 못해 토큰이 미검증으로 남는다
    result = verify_answer_rules("노드는 32대입니다 [#9].", [_chunk("32대")])
    assert result.sentences[0].is_suspicious is True


def test_multiple_sentences_split_and_indexed() -> None:
    answer = "prod-main-eks는 32대입니다 [#1].\n메모리는 99% 입니다 [#2]."
    chunks = [_chunk("prod-main-eks 32대"), _chunk("메모리 70%")]
    result = verify_answer_rules(answer, chunks)
    assert [s.sentence_id for s in result.sentences] == [1, 2]
    assert result.sentences[0].is_suspicious is False  # 32 → chunk[0]에 있음
    assert result.sentences[1].is_suspicious is True  # 99 → chunk[1]에 없음(70%)


def test_sentence_can_cite_multiple_chunks() -> None:
    answer = "prod-main-eks는 32대이고 메모리는 80% 입니다 [#1][#2]."
    chunks = [_chunk("prod-main-eks 32대"), _chunk("메모리 80% 사용")]
    check = verify_answer_rules(answer, chunks).sentences[0]
    # 토큰이 인용한 두 청크에 나뉘어 있어도 합쳐서 대조한다
    assert check.cited_chunks == [1, 2]
    assert check.is_suspicious is False


def test_version_number_does_not_split_sentence() -> None:
    # v1.29.1 내부의 마침표는 문장 경계가 아니다 (PoC 휴리스틱: 종결부호 + 공백만 분리)
    result = verify_answer_rules(
        "클러스터는 v1.29.1 버전입니다 [#1].", [_chunk("Kubernetes 버전: v1.29.1")]
    )
    assert len(result.sentences) == 1
    assert result.sentences[0].is_suspicious is False


def test_period_space_splits_sentences() -> None:
    answer = "prod-main-eks는 32대입니다 [#1]. 메모리는 99%입니다 [#2]."
    chunks = [_chunk("prod-main-eks 32대"), _chunk("메모리 70%")]
    result = verify_answer_rules(answer, chunks)
    assert len(result.sentences) == 2
    assert result.sentences[0].is_suspicious is False
    assert result.sentences[1].is_suspicious is True


# ---------------------------------------------------------------------------
# feature17c-16 — 운영 포맷(종결 부호 뒤 인용 마커) citation 재부착
# 생성기는 "문장1. [#1] 문장2. [#2]" 처럼 마침표 뒤에 마커를 붙인다. 종결 부호+공백
# 경계로 단순 분리하면 마커가 다음 문장 앞으로 떨어져 첫 문장이 인용을 잃는 off-by-one
# 이 발생(환각 NOT_SUPPORTED 과대 측정 주원인). 재부착으로 각 문장이 자기 마커를 갖는다.
# ---------------------------------------------------------------------------


def test_trailing_marker_after_period_reattached_to_owning_sentence() -> None:
    """'문장1. [#1] 문장2. [#2]' — 각 문장이 자기 인용을 갖고, 첫 문장이 유실되지 않는다."""
    answer = "prod-main-eks는 32대입니다. [#1] 메모리는 80%입니다. [#2]"
    chunks = [_chunk("prod-main-eks 32대"), _chunk("메모리 80%")]
    result = verify_answer_rules(answer, chunks)
    assert len(result.sentences) == 2
    # 첫 문장이 인용을 회복(off-by-one 이전엔 cited=[] 로 의심).
    assert result.sentences[0].cited_chunks == [1]
    assert result.sentences[0].is_suspicious is False
    assert result.sentences[1].cited_chunks == [2]
    assert result.sentences[1].is_suspicious is False


def test_single_sentence_trailing_marker_after_period() -> None:
    """'문장. [#1]' — 마침표 뒤 마커도 그 문장에 귀속(트레일링 마커 흡수)."""
    answer = "노드는 32대 운영됩니다. [#1]"
    result = verify_answer_rules(answer, [_chunk("평균 32대 노드를 운영한다")])
    assert len(result.sentences) == 1
    assert result.sentences[0].cited_chunks == [1]
    assert result.sentences[0].is_suspicious is False


def test_numbered_list_item_keeps_citation() -> None:
    """번호 목록 '1. ... . [#1] 2. ...' — 내용 문장이 인용을 유지한다."""
    answer = "1. 노드는 32대입니다. [#1] 2. 메모리는 80%입니다. [#1]"
    result = verify_answer_rules(answer, [_chunk("노드 32대 메모리 80%")])
    # "1." / "2." 는 토큰 없는 PASS, 내용 문장 2개는 [#1] 인용.
    content = [s for s in result.sentences if s.unverified_tokens or s.cited_chunks]
    assert all(s.cited_chunks == [1] for s in content)
    assert all(not s.is_suspicious for s in content)


def test_empty_answer_returns_empty_result() -> None:
    result = verify_answer_rules("", [_chunk("아무 내용")])
    assert result.sentences == []
    assert result.has_suspicious_sentences() is False
    assert result.passed_verifications() == []


def test_number_not_matched_inside_larger_number() -> None:
    # P2: 답변의 '32'가 청크의 '320'에 false positive 매칭되지 않는다 (워드 경계)
    from app.query.verifier import verify_answer_rules

    result = verify_answer_rules(
        "노드는 32대 운영됩니다 [#1].", [_chunk("평균 320대 노드를 운영한다")]
    )
    check = result.sentences[0]
    assert check.is_suspicious is True  # '32'는 '320' 안에 있어도 매칭 안 됨
    assert "32" in check.unverified_tokens


def test_accessors_split_passed_and_suspicious() -> None:
    answer = "prod-main-eks는 32대입니다 [#1].\n메모리는 99% 입니다 [#2]."
    chunks = [_chunk("prod-main-eks 32대"), _chunk("메모리 70%")]
    result = verify_answer_rules(answer, chunks)

    assert result.has_suspicious_sentences() is True
    suspicious = result.suspicious_sentences
    assert [s.sentence_id for s in suspicious] == [2]

    # 통과한 문장은 status=PASS의 최종 Verification으로 변환된다
    passed = result.passed_verifications()
    assert len(passed) == 1
    assert passed[0].sentence_id == 1
    assert passed[0].status is VerificationStatus.PASS
    assert passed[0].cited_chunks == [1]
