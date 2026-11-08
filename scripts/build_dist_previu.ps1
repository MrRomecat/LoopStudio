# Полная сборка дистрибутива PreviuStudio:
#   1) PyInstaller onedir  2) контроль поставки  3) установщик Inno Setup (если установлен)
# Запуск из корня репозитория:  .\scripts\build_dist_previu.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "[1/3] PyInstaller (onedir)" -ForegroundColor Cyan
& $py -m PyInstaller (Join-Path $root "packaging\previustudio.spec") --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build")
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- контроль поставки: секретам в dist не место, обязательное — на месте ---
$dist = Join-Path $root "dist\PreviuStudio"
# cacert.pem (certifi) — публичные корневые сертификаты TLS для httpx, не секрет
$leaks = Get-ChildItem $dist -Recurse -File | Where-Object {
    $_.Name -match "private_key" -or ($_.Name -match "\.pem$" -and $_.Name -ne "cacert.pem")
}
if ($leaks) {
    Write-Host "СБОРКА ОСТАНОВЛЕНА: в dist попали приватные ключи:" -ForegroundColor Red
    $leaks | ForEach-Object { Write-Host ("  " + $_.FullName) -ForegroundColor Red }
    exit 1
}
foreach ($required in @("assets\themes\relax_music\theme.json", "assets\themes\history\theme.json", "assets\fonts", "licenses\NOTICE.txt")) {
    if (-not (Test-Path (Join-Path $dist $required))) {
        Write-Host "СБОРКА ОСТАНОВЛЕНА: в dist нет обязательного '$required'." -ForegroundColor Red
        exit 1
    }
}
# ffmpeg PreviuStudio не нужен — случайное попадание раздуло бы дистрибутив
if (Test-Path (Join-Path $dist "bin\ffmpeg.exe")) {
    Write-Host "СБОРКА ОСТАНОВЛЕНА: в dist попал ffmpeg — он не нужен PreviuStudio." -ForegroundColor Red
    exit 1
}

Write-Host "[2/3] Смоук: запуск собранного exe" -ForegroundColor Cyan
$exe = Join-Path $dist "PreviuStudio.exe"
$process = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 6
if ($process.HasExited) {
    Write-Host "СБОРКА ОСТАНОВЛЕНА: PreviuStudio.exe завершился сразу (код $($process.ExitCode))." -ForegroundColor Red
    exit 1
}
Stop-Process -Id $process.Id -Force
Write-Host "exe запускается (окно активации поднялось)." -ForegroundColor Green

Write-Host "[3/3] Inno Setup" -ForegroundColor Cyan
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($null -eq $iscc) {
    # ProgramFiles(x86) берём через GetEnvironmentVariable: конструкция
    # "${env:ProgramFiles(x86)}" ломает парсер Windows PowerShell 5.1
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $candidates = @()
    foreach ($ver in @(7, 6, 5)) {
        if ($pf86) { $candidates += (Join-Path $pf86 "Inno Setup $ver\ISCC.exe") }
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup $ver\ISCC.exe")
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup $ver\ISCC.exe")
    }
    foreach ($c in $candidates) { if (Test-Path $c) { $iscc = $c; break } }
}
if ($null -eq $iscc) {
    Write-Host "Inno Setup не найден — пропускаю сборку установщика." -ForegroundColor Yellow
    Write-Host "Установите с https://jrsoftware.org/isinfo.php и повторите," -ForegroundColor Yellow
    Write-Host "либо распространяйте папку dist\PreviuStudio как есть." -ForegroundColor Yellow
    exit 0
}
$isccPath = if ($iscc -is [string]) { $iscc } else { $iscc.Source }
& $isccPath (Join-Path $root "packaging\previu_installer.iss")
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "Готово: dist\installer\PreviuStudio-*-setup.exe" -ForegroundColor Green
