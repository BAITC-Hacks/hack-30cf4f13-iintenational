$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv\Scripts\python.exe. Follow the installation steps in README.md."
}
Push-Location $PSScriptRoot
try {
    & $python -X utf8 (Join-Path $PSScriptRoot "run_pipeline.py")
    $pipelineExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($pipelineExitCode -ne 0) { exit $pipelineExitCode }
