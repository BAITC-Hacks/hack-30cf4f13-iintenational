param(
    [int]$Port = 8765,
    [string]$DataDir = ""
)
$ErrorActionPreference = "Stop"
$appPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $appPython)) {
    throw "Сначала выполните команды установки из README.md"
}
$appArguments = @("-X", "utf8", (Join-Path $PSScriptRoot "run_app.py"), "--port", "$Port")
if ($DataDir) {
    $appArguments += @("--data-dir", $DataDir)
}
Push-Location $PSScriptRoot
try {
    & $appPython @appArguments
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
