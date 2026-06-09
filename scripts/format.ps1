# LINA RAG Pipeline — Windows PowerShell 포맷 스크립트
# scripts/format.sh의 Python 분기와 동등.
# 사용법:  .\scripts\format.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

Write-Host "==> Running format from $RootDir"

if (-not ((Test-Path "./pyproject.toml") -or (Test-Path "./requirements.txt"))) {
    Write-Host "pyproject.toml/requirements.txt 둘 다 없음. 종료."
    exit 0
}

Write-Host ""
Write-Host "==> root python format"

if (Get-Command ruff -ErrorAction SilentlyContinue) {
    ruff format .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    ruff check . --fix
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif (Get-Command black -ErrorAction SilentlyContinue) {
    black .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "ruff/black not found. Skipping root Python format."
}

Write-Host ""
Write-Host "==> Format completed"
