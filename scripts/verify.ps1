# LINA RAG Pipeline — Windows PowerShell 종합 검증 스크립트
# scripts/verify.sh와 동등. format → lint → test 순으로 실행.
# 사용법:  .\scripts\verify.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

Write-Host "==> Running full verification from $RootDir"

Write-Host ""
Write-Host "==> Step 1/3: format"
& (Join-Path $PSScriptRoot "format.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "==> Step 2/3: lint"
& (Join-Path $PSScriptRoot "lint.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "==> Step 3/3: test"
& (Join-Path $PSScriptRoot "test.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "==> Verification completed successfully"
