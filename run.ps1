$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Сначала выполните команды установки из README.md"
}
Push-Location $PSScriptRoot
try {
    & $python (Join-Path $PSScriptRoot "run_pipeline.py")
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
