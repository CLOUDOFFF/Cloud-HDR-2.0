<#
    Cloud HDR — установка из пакета «всё в одном».

    Копирует программу, модель и свою копию Python в
    %LOCALAPPDATA%\Programs\Cloud HDR, создаёт ярлыки и открывает приложение.
    Права администратора не нужны: всё ставится только для этого пользователя.
#>

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Pkg    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Target = Join-Path $env:LOCALAPPDATA 'Programs\Cloud HDR'
$AppExe = Join-Path $Target 'desktop\CloudHDR.exe'
$Ico    = Join-Path $Target 'desktop\app.ico'

Write-Host ''
Write-Host '  Cloud HDR' -ForegroundColor Cyan
Write-Host '  ─────────'
Write-Host '  Программа, своя языковая модель и Python с torch — всё локально.'
Write-Host ''

# 1. Гасим прошлый запуск, иначе файлы окажутся занятыми.
#    Агента и спутника отбираем по строке запуска и обязательно пропускаем
#    собственный процесс: иначе установщик погасит сам себя.
Get-Process CloudHDR -ErrorAction SilentlyContinue | ForEach-Object {
    $null = $_.CloseMainWindow()
    Start-Sleep -Milliseconds 500
    if (-not $_.HasExited) { try { $_.Kill() } catch { } }
}
#    Службы на Python гасим тоже: работающий pythonw держит runtime\python312.dll,
#    и robocopy падал на нём с кодом 8 — переустановка поверх запущенного
#    приложения обрывалась на полпути.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and (
        ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'agent\.ps1|companion\.ps1|ears\.ps1|launch\.ps1') -or
        ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'cloudhdr_')) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

# 2. Копируем.
Write-Host '  Копирую файлы — около 8 ГБ, это несколько минут...' -ForegroundColor Cyan
foreach ($part in @('desktop', 'public', 'server', 'python', 'runtime')) {
    $src = Join-Path $Pkg $part
    if (-not (Test-Path -LiteralPath $src)) { continue }
    & robocopy.exe $src (Join-Path $Target $part) /E /MT:16 /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Не удалось скопировать $part (robocopy $LASTEXITCODE)" }
    Write-Host "    + $part" -ForegroundColor Green
}
$global:LASTEXITCODE = 0
if (-not (Test-Path -LiteralPath $AppExe)) { throw 'После копирования нет desktop\CloudHDR.exe — пакет повреждён' }

# 3. Ярлыки: рабочий стол, «Пуск» и автозагрузка.
Write-Host '  Создаю ярлыки...' -ForegroundColor Cyan
$shell = New-Object -ComObject WScript.Shell
function New-AppShortcut([string]$Path, [string]$Arguments, [string]$Hotkey, [string]$Description) {
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath       = $AppExe
    $link.Arguments        = $Arguments
    $link.WorkingDirectory = Split-Path -Parent $AppExe
    $link.IconLocation     = "$Ico,0"
    $link.Description      = $Description
    if ($Hotkey) { $link.Hotkey = $Hotkey }
    $link.Save()
    Write-Host "    + $Path" -ForegroundColor Green
}
$desktop  = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$startup  = [Environment]::GetFolderPath('Startup')
New-AppShortcut (Join-Path $desktop  'Cloud HDR.lnk') '' 'CTRL+ALT+C' 'Cloud HDR — помощник Windows со своей моделью (Ctrl+Alt+C)'
New-AppShortcut (Join-Path $programs 'Cloud HDR.lnk') '' '' 'Cloud HDR — помощник Windows со своей моделью'
New-AppShortcut (Join-Path $startup  'Cloud HDR.lnk') '' '' 'Автозапуск Cloud HDR при входе в Windows'

# 4. Строка в «Установка и удаление программ».
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CloudHDR'
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name 'DisplayName'     -Value 'Cloud HDR'
Set-ItemProperty -Path $key -Name 'DisplayIcon'     -Value $Ico
Set-ItemProperty -Path $key -Name 'InstallLocation' -Value $Target
Set-ItemProperty -Path $key -Name 'Publisher'       -Value 'Cloud HDR'
Set-ItemProperty -Path $key -Name 'UninstallString' -Value ("powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"" + (Join-Path $Target 'setup\uninstall.ps1') + "`"")
Copy-Item (Join-Path $Pkg 'setup') (Join-Path $Target 'setup') -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '  Готово.' -ForegroundColor Cyan
Write-Host '  ─────────────────────────────────────────────'
Write-Host '  Ctrl + Alt + C   вызвать окно'
Write-Host '  Первый ответ     10-30 секунд: веса ложатся в память'
Write-Host '  Интернет         не нужен — модель считает на этом компьютере'
Write-Host ''
Start-Process -FilePath $AppExe -WorkingDirectory (Split-Path -Parent $AppExe)
Start-Sleep -Seconds 2