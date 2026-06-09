# LINA RAG Pipeline — Windows PowerShell 린트 스크립트
# scripts/lint.sh의 Python 분기와 동등 (ruff check + mypy app).
# 사용법:  .\scripts\lint.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

Write-Host "==> Running lint checks from $RootDir"

if (-not ((Test-Path "./pyproject.toml") -or (Test-Path "./requirements.txt"))) {
    Write-Host "pyproject.toml/requirements.txt 둘 다 없음. 종료."
    exit 0
}

Write-Host ""
Write-Host "==> root python lint"

if (Get-Command ruff -ErrorAction SilentlyContinue) {
    ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "ruff not found. Skipping ruff for root Python project."
}

if (Get-Command mypy -ErrorAction SilentlyContinue) {
    mypy app
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "mypy not found. Skipping mypy for root Python project."
}

Write-Host ""
Write-Host "==> Lint completed"
