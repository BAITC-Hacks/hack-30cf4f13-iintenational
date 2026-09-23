param(
    [switch]$SkipUi
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv\Scripts\python.exe. Follow the installation steps in README.md."
}

function Invoke-Step([string]$Label, [string]$Executable, [string[]]$Arguments) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    & $Executable @Arguments
    $stepExitCode = $LASTEXITCODE
    if ($stepExitCode -ne 0) {
        Write-Host "Step '$Label' failed with exit code $stepExitCode." -ForegroundColor Red
        exit $stepExitCode
    }
}

# -SkipUi is a Python-only check: no Node.js, npm, or browser is required.
# Preflight the full mode before running the pipeline or tests.
if (-not $SkipUi) {
    $node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $node -or -not $npm) {
        throw "Full verification requires Node.js and npm. Install them or use -SkipUi."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'node_modules\playwright\package.json') -PathType Leaf)) {
        throw "Missing UI test dependencies. Run npm.cmd ci, or use -SkipUi."
    }
}

Push-Location $root
try {
    Invoke-Step "Python dependency consistency" $python @("-X", "utf8", "-m", "pip", "check")
    Invoke-Step "Rebuild pipeline outputs" $python @("-X", "utf8", "run_pipeline.py")
    Invoke-Step "Python regressions" $python @("-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-q")
    if (-not $SkipUi) {
        Invoke-Step "Graph JavaScript and controls" $node.Source @("scripts/check_graph_view.js")
        Invoke-Step "Source palette and contrast" $node.Source @("scripts/check_theme.js")
        Invoke-Step "Graph UI" $npm.Source @("run", "test:ui")
        Invoke-Step "Workbench UI" $npm.Source @("run", "test:workbench")
    } else {
        Write-Host "`n=== JavaScript and browser checks skipped (-SkipUi) ===" -ForegroundColor Yellow
    }
    Write-Host "`nProject verification passed." -ForegroundColor Green
} finally {
    Pop-Location
}
