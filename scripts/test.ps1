# LINA RAG Pipeline — Windows PowerShell 테스트 스크립트
# scripts/test.sh의 Python 분기와 동등 (pytest).
# 사용법:  .\scripts\test.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

Write-Host "==> Running tests from $RootDir"

if (-not ((Test-Path "./pyproject.toml") -or (Test-Path "./requirements.txt"))) {
    Write-Host "pyproject.toml/requirements.txt 둘 다 없음. 종료."
    exit 0
}

Write-Host ""
Write-Host "==> root python tests"

if (Get-Command pytest -ErrorAction SilentlyContinue) {
    pytest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "pytest not found. Install pytest or adjust scripts/test.ps1."
    exit 1
}

Write-Host ""
Write-Host "==> Tests completed"
