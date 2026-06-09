# LINA RAG Pipeline — Windows 환경 사전 체크
# --------------------------------------------------
# 작성목적 : 집/회사 Windows에서 작업 시작 전 환경이 정상인지 한방에 검증.
#           - Python 3.11.x 인지
#           - 가상환경 활성화 됐는지
#           - ruff / pytest / mypy 설치됐는지
#           - 핵심 패키지 import 가능한지 (pydantic, langgraph, qdrant_client 등)
#           - .env 파일 존재 여부 (없어도 경고만)
# 사용법   : .\scripts\preflight.ps1
# --------------------------------------------------

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$Errors = @()
$Warnings = @()

function Test-Step {
    param(
        [string]$Name,
        [scriptblock]$Check,
        [string]$Hint = ""
    )
    $ok = $false
    $detail = ""
    try {
        $detail = & $Check
        $ok = $true
    } catch {
        $detail = $_.Exception.Message
    }
    if ($ok) {
        Write-Host ("  [OK]  {0,-40} {1}" -f $Name, $detail)
    } else {
        Write-Host ("  [FAIL] {0,-40} {1}" -f $Name, $detail) -ForegroundColor Red
        $script:Errors += "$Name — $detail" + $(if ($Hint) { " (힌트: $Hint)" } else { "" })
    }
}

function Test-Warn {
    param(
        [string]$Name,
        [scriptblock]$Check,
        [string]$Hint = ""
    )
    try {
        $detail = & $Check
        Write-Host ("  [OK]  {0,-40} {1}" -f $Name, $detail)
    } catch {
        Write-Host ("  [WARN] {0,-40} {1}" -f $Name, $_.Exception.Message) -ForegroundColor Yellow
        $script:Warnings += "$Name — $($_.Exception.Message)" + $(if ($Hint) { " (힌트: $Hint)" } else { "" })
    }
}

Write-Host ""
Write-Host "==> LINA RAG preflight check ($RootDir)"
Write-Host ""

# --- Python ---
Write-Host "[Python]"
Test-Step "python 실행 가능" {
    $v = (python --version) 2>&1
    if ($LASTEXITCODE -ne 0) { throw "python 명령 실패" }
    return $v
} "py -3.11 -m venv .venv 로 가상환경 만들고 .\.venv\Scripts\Activate.ps1"

Test-Step "Python 3.11.x" {
    $v = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($v -notmatch "^3\.11\.") { throw "현재 $v — pyproject.toml에 ``requires-python = "">=3.11,<3.12""``" }
    return $v
} "winget install Python.Python.3.11"

Test-Step "가상환경 활성화 상태" {
    $venv = $env:VIRTUAL_ENV
    if (-not $venv) { throw "VIRTUAL_ENV 환경변수 비어있음" }
    return $venv
} ".\.venv\Scripts\Activate.ps1"

Write-Host ""

# --- 개발 도구 ---
Write-Host "[개발 도구]"
foreach ($tool in @("pip", "pytest", "ruff", "mypy")) {
    Test-Step "$tool 사용 가능" {
        # pyenv-win이 'pip'을 가로채는 사례가 있어 'python -m' 형태 우선
        $v = python -m $tool --version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "$tool 미설치" }
        return ($v -split "`n")[0]
    } "python -m pip install -e ``""``.[dev]``""``"
}

Write-Host ""

# --- 의존성 ---
Write-Host "[핵심 의존성 import]"
$packages = @(
    @{Name="pydantic"; Import="pydantic"},
    @{Name="pydantic_settings"; Import="pydantic_settings"},
    @{Name="fastapi"; Import="fastapi"},
    @{Name="langgraph"; Import="langgraph"},
    @{Name="langchain"; Import="langchain"},
    @{Name="openai"; Import="openai"},
    @{Name="qdrant_client"; Import="qdrant_client"},
    @{Name="pymongo"; Import="pymongo"},
    @{Name="sqlalchemy"; Import="sqlalchemy"},
    @{Name="tiktoken"; Import="tiktoken"},
    @{Name="beautifulsoup4"; Import="bs4"},
    @{Name="python-docx"; Import="docx"},
    @{Name="openpyxl"; Import="openpyxl"}
)
foreach ($pkg in $packages) {
    Test-Step ($pkg.Name) {
        $v = python -c "import $($pkg.Import); print(getattr($($pkg.Import), '__version__', 'imported'))" 2>&1
        if ($LASTEXITCODE -ne 0) { throw "import 실패" }
        return $v
    } "python -m pip install -e ``""``.[ingestion,dev]``""``"
}

Write-Host ""

# --- 임베딩 extras (선택) ---
Write-Host "[임베딩 extras (선택)]"
Test-Warn "sentence_transformers" {
    $v = python -c "import sentence_transformers; print(sentence_transformers.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "미설치 (feature5-B 작업 시 필요)" }
    return $v
} "python -m pip install -e ``""``.[embedding]``""``"

Test-Warn "fastembed" {
    $v = python -c "import fastembed; print(fastembed.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "미설치 (BM25 sparse 작업 시 필요)" }
    return $v
} "python -m pip install -e ``""``.[embedding]``""``"

Write-Host ""

# --- 프로젝트 자체 ---
Write-Host "[프로젝트]"
Test-Step "app 패키지 import" {
    $v = python -c "import app; from app.config import get_settings; s = get_settings(); print(s.qdrant_host, s.mongo_db)" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$v" }
    return $v
} "python -m pip install -e ``""``.``""``"

Test-Warn ".env 파일 존재" {
    if (-not (Test-Path ".env")) { throw "없음 — .env.example을 참고해 만들어주세요" }
    return ".env 발견"
} "Copy-Item .env.example .env  후 RAG_OPENAI_API_KEY 등 채우기"

Test-Step "samples/ 데이터" {
    $count = (Get-ChildItem -File samples\*.json).Count
    if ($count -eq 0) { throw "samples/*.json 없음" }
    return "$count 개 JSON 픽스처"
}

Write-Host ""

# --- 외부 서비스 (선택) ---
Write-Host "[외부 서비스 (선택 — feature5-B 작업 시 필요)]"
Test-Warn "Docker Desktop" {
    $v = docker --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "미설치 — 현재 코드는 외부 서비스 없이 동작" }
    return $v
} "winget install Docker.DockerDesktop"

Write-Host ""
Write-Host "==> 결과 요약"
Write-Host ""

if ($Errors.Count -eq 0) {
    Write-Host "  [v] 필수 항목 전부 OK" -ForegroundColor Green
} else {
    Write-Host ("  [x] 필수 실패 {0}건:" -f $Errors.Count) -ForegroundColor Red
    foreach ($e in $Errors) { Write-Host "    - $e" }
}

if ($Warnings.Count -gt 0) {
    Write-Host ""
    Write-Host ("  [!] 경고 {0}건 (지금 당장 막힘은 아님):" -f $Warnings.Count) -ForegroundColor Yellow
    foreach ($w in $Warnings) { Write-Host "    - $w" }
}

Write-Host ""
if ($Errors.Count -gt 0) { exit 1 } else { exit 0 }
