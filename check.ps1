# Локальная проверка качества: линт + формат + типы + тесты.
# Запуск из корня репозитория:  .\check.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "[1/4] ruff check" -ForegroundColor Cyan
& $py -m ruff check $root
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "[2/4] ruff format --check" -ForegroundColor Cyan
& $py -m ruff format --check $root
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "[3/4] mypy (strict)" -ForegroundColor Cyan
Push-Location $root
& $py -m mypy
$mypyExit = $LASTEXITCODE
Pop-Location
if ($mypyExit -ne 0) { exit 1 }

Write-Host "[4/4] pytest" -ForegroundColor Cyan
Push-Location $root
& $py -m pytest
$pytestExit = $LASTEXITCODE
Pop-Location
if ($pytestExit -ne 0) { exit 1 }

Write-Host "OK: все проверки зелёные." -ForegroundColor Green
