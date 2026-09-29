<#
    Cloud HDR 2.0 — установка из релиза на GitHub.

    Положите в одну папку все части CloudHDR-2.0.zip.001, .002, …, SHA256SUMS.txt
    и Install-CloudHDR-2.0.cmd — и запустите его. Скрипт сам:
      1. проверит видеокарту, драйвер и место на диске;
      2. сверит каждую часть по SHA-256 и назовёт битые (их нужно перекачать);
      3. склеит части и распакует во временную папку на диске C;
      4. поставит библиотеки Visual C++ для модели и поправит путь голоса;
      5. запустит обычную установку (setup\install.ps1) — ярлыки, «Пуск», автозапуск;
      6. уберёт временные файлы.
    Оборвалось — просто запустите ещё раз: сделанное не повторяется.
#>
param([string]$Temp = (Join-Path $env:SystemDrive 'CloudHDR-2.0-setup'),
      # только проверить пакет: распаковать и проверить запуск частей, в систему не ставить
      [switch]$CheckOnly)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$here = $PSScriptRoot
$app = Join-Path $env:LOCALAPPDATA 'Programs\Cloud HDR'
$zip = Join-Path $here 'CloudHDR-2.0.zip'
function Say($text, $color = 'Gray') { Write-Host "  $text" -ForegroundColor $color }

function Write-VenvCfg($base) {
    $rt = Join-Path $base 'runtime'
    $venv = Join-Path $base 'python\voice-env'
    if (-not (Test-Path $venv)) { return }
    $lines = @("home = $rt", 'include-system-site-packages = true', 'version = 3.12.10',
               "executable = $rt\python.exe", "command = $rt\python.exe -m venv --system-site-packages $venv")
    [IO.File]::WriteAllLines((Join-Path $venv 'pyvenv.cfg'), $lines, (New-Object Text.UTF8Encoding $false))
}

try {
    Write-Host ''
    Write-Host '  Cloud HDR 2.0 — установка' -ForegroundColor Cyan
    Write-Host '  ──────────────────────────'

    # 0. Проверка компьютера — до долгой работы.
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        Say 'ВНИМАНИЕ: видеокарта NVIDIA не найдена — модель Cloud HDR работает только на NVIDIA (от 6 ГБ).' 'Yellow'
    } else {
        $gpu = (& $smi.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null | Select-Object -First 1) -split ',\s*'
        Say "Видеокарта: $($gpu[0]), $([math]::Round([int]$gpu[1] / 1024, 1)) ГБ, драйвер $($gpu[2])"
        if ($gpu[2] -and [double]$gpu[2] -lt 580) {
            Say 'ВНИМАНИЕ: драйвер NVIDIA старый — нужен 580 или новее (NVIDIA App или nvidia.com/drivers).' 'Yellow'
        }
        if ([int]$gpu[1] -lt 5500) { Say 'ВНИМАНИЕ: на видеокарте меньше 6 ГБ — модель может не поместиться.' 'Yellow' }
    }
    $free = (New-Object IO.DriveInfo $env:SystemDrive).AvailableFreeSpace
    $unpacked = Test-Path (Join-Path $Temp 'CloudHDR\.unpacked')
    $need = if ($unpacked) { 14GB } else { 28GB }
    if ($free -lt $need) { throw "На диске $env:SystemDrive мало места: нужно $([math]::Round($need / 1GB)) ГБ, свободно $([math]::Round($free / 1GB)) ГБ." }

    if (-not $unpacked) {
        # 1. Части: все на месте и целые?
        $sumsFile = Join-Path $here 'SHA256SUMS.txt'
        if (-not (Test-Path $sumsFile)) { throw 'Рядом нет SHA256SUMS.txt — скачайте его из релиза в ту же папку.' }
        $sums = [ordered]@{}
        foreach ($line in Get-Content $sumsFile) {
            $hash, $name = $line -split '\s+', 2
            if ($name) { $sums[$name.Trim()] = $hash.ToUpper() }
        }
        $parts = @($sums.Keys | Where-Object { $_ -match '^CloudHDR-2\.0\.zip\.\d{3}$' } | Sort-Object)
        if (-not $parts) { throw 'В SHA256SUMS.txt нет частей архива.' }
        Say "1/5  Проверяю части архива ($($parts.Count) шт.)..." 'Cyan'
        $bad = @()
        foreach ($name in $parts) {
            $file = Join-Path $here $name
            if (-not (Test-Path $file)) { $bad += "$name — нет файла"; continue }
            if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $sums[$name]) { $bad += "$name — скачался с ошибкой" }
            else { Say "     $name — целая" 'Green' }
        }
        if ($bad) { throw ("Перекачайте из релиза:`n    " + ($bad -join "`n    ")) }

        # 2. Склейка.
        Say '2/5  Склеиваю архив...' 'Cyan'
        $out = [IO.File]::Create($zip)
        try {
            foreach ($name in $parts) {
                $in = [IO.File]::OpenRead((Join-Path $here $name))
                try { $in.CopyTo($out, 8MB) } finally { $in.Dispose() }
            }
        } finally { $out.Dispose() }
        if ($sums.Contains('CloudHDR-2.0.zip') -and (Get-FileHash $zip -Algorithm SHA256).Hash -ne $sums['CloudHDR-2.0.zip']) {
            throw 'Склеенный архив не сошёлся с контрольной суммой — запустите ещё раз.'
        }

        # 3. Распаковка в короткий путь: в архиве длинные имена файлов.
        Say "3/5  Распаковываю в $Temp (5-15 минут)..." 'Cyan'
        if (Test-Path $Temp) { Remove-Item -LiteralPath $Temp -Recurse -Force }
        New-Item -ItemType Directory -Force $Temp | Out-Null
        $tar = Join-Path $env:WINDIR 'System32\tar.exe'
        if (Test-Path $tar) {
            & $tar -xf $zip -C $Temp
            if ($LASTEXITCODE -ne 0) { throw "Распаковка оборвалась (код $LASTEXITCODE). Хватает ли места?" }
        } else {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $Temp)
        }
        Set-Content -Path (Join-Path $Temp 'CloudHDR\.unpacked') -Value (Get-Date) -Encoding ascii
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    } else {
        Say '1-3/5  Архив уже распакован прошлым запуском.' 'Green'
    }
    $pkg = Join-Path $Temp 'CloudHDR'

    # 4. Библиотеки Visual C++ для llama.cpp и путь голоса.
    Say '4/5  Готовлю модель и голос...' 'Cyan'
    Copy-Item (Join-Path $pkg 'vcruntime\*.dll') (Join-Path $pkg 'runtime\llama') -Force
    Write-VenvCfg $pkg
    & (Join-Path $pkg 'runtime\python.exe') -c "import torch" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Python из пакета не запускается — архив распаковался не полностью. Запустите ещё раз.' }

    if ($CheckOnly) {
        Say 'Проверка частей пакета:' 'Cyan'
        $py = Join-Path $pkg 'runtime\python.exe'
        $checks = [ordered]@{
            'модель (движок llama.cpp, токенизатор)' = @($py, '-c', "import sys; sys.path.insert(0, r'$pkg\python'); from cloudhdr_qwen.llama_engine import LlamaEngine; e = LlamaEngine(); print(e.label)")
            'хаб: перевод, речь, управление'          = @($py, '-c', "import sys; sys.path.insert(0, r'$pkg\python'); import cloudhdr_hub, cloudhdr_control, cloudhdr_saytext; print('ok')")
            'разбор команд (NLU)'                    = @($py, '-c', "import sys; sys.path.insert(0, r'$pkg\python'); import cloudhdr_nlu, spacy; print('ok')")
            'голос (XTTS в voice-env)'               = @((Join-Path $pkg 'python\voice-env\Scripts\python.exe'), '-c', "import sys; sys.path.insert(0, r'$pkg\python'); import TTS, cloudhdr_voice; print('ok')")
            'llama.cpp'                              = @((Join-Path $pkg 'runtime\llama\llama-server.exe'), '--version')
        }
        $failed = 0
        # llama.cpp и torch пишут служебное в поток ошибок — для проверки это не сбой, решает код выхода
        $ErrorActionPreference = 'Continue'
        foreach ($name in $checks.Keys) {
            $cmd = $checks[$name]
            $out = & $cmd[0] $cmd[1..($cmd.Count - 1)] 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) { Say "     ✓ $name" 'Green' } else { $failed++; Say "     ✗ $name`n$($out.Trim() -split "`n" | Select-Object -Last 4 | Out-String)" 'Red' }
        }
        if ($failed) { throw "Не прошло проверок: $failed" }
        Say "Пакет в порядке. Распакован в $pkg (в систему не ставил)." 'Green'
        return
    }

    # 5. Обычная установка.
    Say '5/5  Устанавливаю Cloud HDR...' 'Cyan'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $pkg 'setup\install.ps1')
    if (-not (Test-Path (Join-Path $app 'desktop\CloudHDR.exe'))) { throw 'Установка не удалась — причина написана выше.' }
    Write-VenvCfg $app
    Copy-Item (Join-Path $pkg 'vcruntime\*.dll') (Join-Path $app 'runtime\llama') -Force
    # Запись своего голоса: без неё Cloud HDR говорит стандартным голосом Windows.
    $record = Join-Path $app 'python\record-voice.cmd'
    if (Test-Path $record) {
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut(
            (Join-Path ([Environment]::GetFolderPath('Programs')) 'Записать голос Cloud HDR.lnk'))
        $link.TargetPath = $record
        $link.WorkingDirectory = Split-Path $record
        $link.IconLocation = (Join-Path $app 'desktop\app.ico') + ',0'
        $link.Save()
    }

    Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ''
    Say 'ГОТОВО. Cloud HDR 2.0 установлен и запущен — Ctrl+Alt+C.' 'Green'
    Say 'Первый ответ 10-30 секунд: модель ложится в видеопамять.' 'Gray'
    Say 'Части архива в этой папке больше не нужны — их можно удалить.' 'Gray'
} catch {
    Write-Host ''
    Say "ОШИБКА: $($_.Exception.Message)" 'Red'
    Say 'Если непонятно, что делать, — создайте Issue на GitHub со снимком этого окна.' 'Yellow'
}
