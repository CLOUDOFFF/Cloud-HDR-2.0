<#
    Cloud HDR — удаление. Гасит программу, убирает ярлыки, строку в списке
    установленных программ и саму папку.
#>

$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Target = Join-Path $env:LOCALAPPDATA 'Programs\Cloud HDR'

Write-Host ''
Write-Host '  Удаляю Cloud HDR...' -ForegroundColor Cyan

Get-Process CloudHDR | ForEach-Object { try { $_.Kill() } catch { } }
# Все процессы Cloud HDR: агент, спутник, уши и службы на Python (Hub, телефон,
# голос, модель). Раньше гасились не все, и занятый python312.dll не давал
# удалить папку — после «удаления» оставались гигабайты.
Get-CimInstance Win32_Process |
    Where-Object { $_.ProcessId -ne $PID -and (
        ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'agent\.ps1|companion\.ps1|ears\.ps1|launch\.ps1') -or
        ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'cloudhdr_')) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 1

# Регистрация уведомлений (от неё приходят напоминания)
Remove-Item 'HKCU:\Software\Classes\AppUserModelId\CloudHDR.App' -Recurse -Force

foreach ($folder in @('Desktop', 'Programs', 'Startup')) {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) 'Cloud HDR.lnk'
    if (Test-Path -LiteralPath $link) { Remove-Item $link -Force; Write-Host "    - $link" -ForegroundColor DarkGray }
}
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CloudHDR' -Recurse -Force

if (Test-Path -LiteralPath $Target) {
    Remove-Item $Target -Recurse -Force
    Write-Host "    - $Target" -ForegroundColor DarkGray
}

# Правило брандмауэра для телефона снимается только с правами администратора;
# без них оно безвредно — порт после удаления никто не слушает.
Remove-NetFirewallRule -DisplayName 'Cloud HDR - телефон' -ErrorAction SilentlyContinue

Write-Host '  Ваши данные (напоминания, карантин, журналы) остались в' -ForegroundColor DarkGray
Write-Host "  $env:LOCALAPPDATA\Cloud HDR — удалите папку вручную, если они не нужны." -ForegroundColor DarkGray
Write-Host '  Готово.' -ForegroundColor Cyan
Write-Host ''
Start-Sleep -Seconds 2