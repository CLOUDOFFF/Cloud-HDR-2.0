<#
    Cloud HDR — скачать релиз с GitHub и установить одной командой.

    GitHub принимает файлы до 2 ГБ, поэтому полный пакет (код, Python, модель,
    распознавание речи, голос) лежит в релизе частями CloudHDR-2.0.zip.001, .002, …
    Скрипт скачивает части в «Загрузки\CloudHDR-2.0» (уже скачанные и целые — пропускает),
    затем запускает install-release.ps1: сверка, склейка, распаковка, установка.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Repo = 'CLOUDOFFF/Cloud-HDR-2.0'
$Out  = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\CloudHDR-2.0'
New-Item -ItemType Directory -Force $Out | Out-Null

Write-Host '  Узнаю последний релиз...' -ForegroundColor Cyan
$release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'CloudHDR' }
$assets = @($release.assets)
$sumsAsset = $assets | Where-Object name -eq 'SHA256SUMS.txt'
if (-not $sumsAsset) { throw 'В релизе нет SHA256SUMS.txt.' }
Invoke-WebRequest $sumsAsset.browser_download_url -OutFile (Join-Path $Out 'SHA256SUMS.txt') -UseBasicParsing
$sums = @{}
foreach ($line in Get-Content (Join-Path $Out 'SHA256SUMS.txt')) {
    $hash, $name = $line -split '\s+', 2
    if ($name) { $sums[$name.Trim()] = $hash.ToUpper() }
}

foreach ($asset in $assets | Where-Object { $_.name -ne 'SHA256SUMS.txt' } | Sort-Object name) {
    $file = Join-Path $Out $asset.name
    $whole = (Test-Path $file) -and ((Get-Item $file).Length -eq $asset.size) -and
             (-not $sums.ContainsKey($asset.name) -or (Get-FileHash $file -Algorithm SHA256).Hash -eq $sums[$asset.name])
    if ($whole) { Write-Host "  = $($asset.name) (уже скачан)" -ForegroundColor DarkGray; continue }
    Write-Host ("  + {0} ({1:N1} ГБ)" -f $asset.name, ($asset.size / 1GB)) -ForegroundColor Green
    Invoke-WebRequest $asset.browser_download_url -OutFile $file -UseBasicParsing
}

Write-Host "  Всё скачано в $Out — устанавливаю." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Out 'install-release.ps1')
