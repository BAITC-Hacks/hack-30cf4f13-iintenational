param(
    [int]$Port = 8765,
    [string]$DataDir = ""
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$appPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $appPython -PathType Leaf)) {
    throw "Missing .venv\Scripts\python.exe. Follow the installation steps in README.md."
}
$appArguments = @("-X", "utf8", (Join-Path $PSScriptRoot "run_app.py"), "--port", "$Port")
if ($DataDir) {
    $appArguments += @("--data-dir", $DataDir)
}
Push-Location $PSScriptRoot
try {
    & $appPython @appArguments
    $appExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($appExitCode -ne 0) { exit $appExitCode }
