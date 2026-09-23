param(
    [switch]$SkipUi
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Не найден .venv\Scripts\python.exe. Сначала выполните установку из README.md."
}

function Invoke-Step([string]$Label, [scriptblock]$Action) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "Шаг '$Label' завершился с кодом $LASTEXITCODE." }
}

Push-Location $root
try {
    Invoke-Step -Label "Проверка JavaScript и темы" -Action {
        node scripts/check_graph_view.js
        if ($LASTEXITCODE -ne 0) { return }
        node scripts/check_theme.js
    }
    Invoke-Step -Label "Python-регрессии" -Action {
        & $python -X utf8 -m unittest discover -s tests -q
    }
    if (-not $SkipUi) {
        Invoke-Step -Label "Графовый UI" -Action { npm.cmd run test:ui }
        Invoke-Step -Label "Workbench UI" -Action { npm.cmd run test:workbench }
    } else {
        Write-Host "`n=== UI пропущен (-SkipUi) ===" -ForegroundColor Yellow
    }
    Write-Host "`nПроверка проекта завершена успешно." -ForegroundColor Green
} finally {
    Pop-Location
}
