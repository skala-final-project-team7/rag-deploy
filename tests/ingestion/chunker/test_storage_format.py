"""clean_storage_format — Confluence Storage Format(HTML) 전처리 검증.

입력 HTML 구조는 samples/*.json 실제 본문 패턴 기준
(h2/h3/h4, p, ul/ol/li, table, inline <code>, code 매크로, ac:task-list).
"""

from app.ingestion.chunker.storage_format import clean_storage_format


def test_empty_body() -> None:
    assert clean_storage_format("") == ""
    assert clean_storage_format("   ") == ""


def test_headings_become_markdown() -> None:
    out = clean_storage_format("<h2>개요</h2><h3>세부</h3><h4>비고</h4>")
    assert "## 개요" in out
    assert "### 세부" in out
    assert "#### 비고" in out


def test_paragraph_and_inline_formatting() -> None:
    out = clean_storage_format("<p><strong>증상:</strong> 노드가 조인되지 않음</p>")
    assert "증상: 노드가 조인되지 않음" in out


def test_inline_code_wrapped_in_backticks() -> None:
    out = clean_storage_format("<p>명령: <code>journalctl -u kubelet -f</code></p>")
    assert "`journalctl -u kubelet -f`" in out


def test_code_macro_becomes_fenced_block() -> None:
    html = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[helm repo add karpenter https://charts.karpenter.sh]]>"
        "</ac:plain-text-body></ac:structured-macro>"
    )
    out = clean_storage_format(html)
    assert "```" in out
    assert "helm repo add karpenter" in out


def test_code_macro_preserves_angle_brackets() -> None:
    # CDATA 코드 내부의 <env> 같은 텍스트가 태그로 파싱되어 사라지면 안 된다
    html = (
        '<ac:structured-macro ac:name="code">'
        "<ac:plain-text-body><![CDATA[<env>production</env>]]>"
        "</ac:plain-text-body></ac:structured-macro>"
    )
    out = clean_storage_format(html)
    assert "<env>production</env>" in out


def test_unordered_list() -> None:
    out = clean_storage_format("<ul><li>첫째</li><li>둘째</li></ul>")
    assert "- 첫째" in out
    assert "- 둘째" in out


def test_ordered_list_is_numbered() -> None:
    out = clean_storage_format("<ol><li>준비</li><li>실행</li></ol>")
    assert "1. 준비" in out
    assert "2. 실행" in out


def test_table_becomes_markdown() -> None:
    html = (
        "<table><tr><th>역할</th><th>담당자</th></tr><tr><td>리드</td><td>이영훈</td></tr></table>"
    )
    out = clean_storage_format(html)
    assert "| 역할 | 담당자 |" in out
    assert "| --- | --- |" in out
    assert "| 리드 | 이영훈 |" in out


def test_confluence_task_list() -> None:
    html = (
        "<ac:task-list><ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>VPN 접속 설정</ac:task-body></ac:task>"
        "<ac:task><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>계정 발급</ac:task-body></ac:task></ac:task-list>"
    )
    out = clean_storage_format(html)
    assert "VPN 접속 설정" in out
    assert "계정 발급" in out
    assert "[ ]" in out and "[x]" in out


def test_smart_quotes_normalized() -> None:
    out = clean_storage_format("<p>“스마트” ‘따옴표’</p>")
    assert "“" not in out and "’" not in out
    assert '"스마트"' in out


def test_malformed_html_does_not_crash() -> None:
    # 깨진 HTML이어도 예외 없이 텍스트를 반환해야 한다 (plain text fallback)
    out = clean_storage_format("<p>닫히지 않은 태그 <strong>굵게")
    assert "닫히지 않은 태그" in out
    assert "굵게" in out


def test_hugo_shortcode_is_stripped() -> None:
    # P2: datadog 본문의 Hugo 숏코드(`{{< ref "..." >}}`)는 임베딩 잡음을 줄이려 제거된다.
    out = clean_storage_format(
        '<p>자세한 내용은 {{< ref "/agent/install" >}} 페이지를 참고하세요.</p>'
    )
    assert "Hugo" not in out  # placeholder
    assert "{{<" not in out
    assert "ref" not in out  # 숏코드 안의 'ref' 키워드도 함께 제거된다
    assert "참고하세요" in out
