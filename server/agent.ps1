<#
    Cloud HDR Agent — версия на PowerShell (без Node.js и Python).

    Работает на встроенном в Windows PowerShell 5.1: HTTP-сервер поднимается
    на TcpListener (127.0.0.1), поэтому права администратора не нужны.
    Контракт API полностью совпадает с server.js и python/server.py.

        powershell -NoProfile -ExecutionPolicy Bypass -File agent.ps1
#>

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root      = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PublicDir = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $Root) 'public'))
$Config    = Get-Content (Join-Path $Root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Version   = '1.0.0'
$StartTime = Get-Date

$Pin = $null
if ($Config.requirePin) { $Pin = '{0:D6}' -f (Get-Random -Minimum 0 -Maximum 999999) }

$Sessions = @{}

# ---------------------------------------------------------------- утилиты ----

function Expand-Env([string]$value) { [Environment]::ExpandEnvironmentVariables($value) }

function Write-Log([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 's'), $message
    Write-Host $line
    if ($Config.logging -and $Config.logging.enabled) {
        $file = Join-Path $Root $Config.logging.file
        $dir = Split-Path -Parent $file
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $file -Value $line -Encoding UTF8
    }
}

# Ошибки передаются строкой "код|текст" — так их удобно превращать в HTTP-ответ.
function Deny([int]$code, [string]$message) { throw ("{0}|{1}" -f $code, $message) }

# ------------------------------------------------- реестр приложений и папок --

$APPS = [ordered]@{
    roblox = @{ title = 'Roblox'; icon = 'game'; aliases = @('роблокс', 'роблокc', 'рублокс', 'roblox')
                dynamic = 'roblox'; uri = 'roblox://'; image = 'RobloxPlayerBeta.exe' }

    discord = @{ title = 'Discord'; icon = 'chat'; aliases = @('дискорд', 'дискорт', 'дс', 'discord')
                 paths = @('%LOCALAPPDATA%\Discord\Update.exe'); args = @('--processStart', 'Discord.exe')
                 uri = 'discord://'; image = 'Discord.exe' }

    chrome = @{ title = 'Google Chrome'; icon = 'globe'; aliases = @('хром', 'chrome', 'гугл хром', 'браузер')
                paths = @('%ProgramFiles%\Google\Chrome\Application\chrome.exe',
                          '%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
                          '%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe')
                shell = 'chrome'; image = 'chrome.exe' }

    edge = @{ title = 'Microsoft Edge'; icon = 'globe'; aliases = @('эдж', 'edge')
              paths = @('%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe',
                        '%ProgramFiles%\Microsoft\Edge\Application\msedge.exe')
              shell = 'msedge'; image = 'msedge.exe' }

    firefox = @{ title = 'Mozilla Firefox'; icon = 'globe'; aliases = @('фаерфокс', 'firefox', 'мозилла')
                 paths = @('%ProgramFiles%\Mozilla Firefox\firefox.exe', '%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe')
                 shell = 'firefox'; image = 'firefox.exe' }

    steam = @{ title = 'Steam'; icon = 'game'; aliases = @('стим', 'steam')
               paths = @('%ProgramFiles(x86)%\Steam\steam.exe', '%ProgramFiles%\Steam\steam.exe')
               uri = 'steam://open/main'; image = 'steam.exe' }

    telegram = @{ title = 'Telegram'; icon = 'chat'; aliases = @('телеграм', 'телега', 'тг', 'telegram')
                  paths = @('%APPDATA%\Telegram Desktop\Telegram.exe', '%LOCALAPPDATA%\Programs\Telegram Desktop\Telegram.exe')
                  uri = 'tg://'; image = 'Telegram.exe' }

    spotify = @{ title = 'Spotify'; icon = 'music'; aliases = @('спотифай', 'spotify')
                 paths = @('%APPDATA%\Spotify\Spotify.exe'); uri = 'spotify:'; image = 'Spotify.exe' }

    vscode = @{ title = 'Visual Studio Code'; icon = 'code'; aliases = @('вскод', 'vscode', 'vs code', 'код')
                paths = @('%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe', '%ProgramFiles%\Microsoft VS Code\Code.exe')
                image = 'Code.exe' }

    explorer = @{ title = 'Проводник'; icon = 'folder'; aliases = @('проводник', 'explorer', 'файлы')
                  paths = @('%WINDIR%\explorer.exe'); image = $null }

    notepad = @{ title = 'Блокнот'; icon = 'note'; aliases = @('блокнот', 'notepad')
                 paths = @('%WINDIR%\system32\notepad.exe'); image = 'notepad.exe' }

    calc = @{ title = 'Калькулятор'; icon = 'calc'; aliases = @('калькулятор', 'calc')
              shell = 'calc'; uri = 'calculator://'; image = 'CalculatorApp.exe' }

    terminal = @{ title = 'Терминал'; icon = 'code'; aliases = @('терминал', 'консоль', 'cmd', 'powershell')
                  paths = @('%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe', '%WINDIR%\system32\cmd.exe'); image = $null }

    settings = @{ title = 'Параметры Windows'; icon = 'gear'; aliases = @('настройки', 'параметры', 'settings')
                  uri = 'ms-settings:'; image = $null }

    taskmgr = @{ title = 'Диспетчер задач'; icon = 'gear'; aliases = @('диспетчер задач', 'taskmgr')
                 paths = @('%WINDIR%\system32\Taskmgr.exe'); image = $null }

    paint = @{ title = 'Paint'; icon = 'brush'; aliases = @('пейнт', 'paint')
               paths = @('%WINDIR%\system32\mspaint.exe'); image = 'mspaint.exe' }

    obs = @{ title = 'OBS Studio'; icon = 'video'; aliases = @('обс', 'obs')
             paths = @('%ProgramFiles%\obs-studio\bin\64bit\obs64.exe'); image = 'obs64.exe' }

    epicgames = @{ title = 'Epic Games Launcher'; icon = 'game'; aliases = @('эпик', 'epic', 'epic games')
                   paths = @('%ProgramFiles(x86)%\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe')
                   uri = 'com.epicgames.launcher://'; image = 'EpicGamesLauncher.exe' }
}

$FOLDERS = [ordered]@{
    desktop   = @{ title = 'Рабочий стол'; aliases = @('рабочий стол', 'десктоп', 'desktop'); path = '%USERPROFILE%\Desktop' }
    downloads = @{ title = 'Загрузки'; aliases = @('загрузки', 'загрузок', 'downloads'); path = '%USERPROFILE%\Downloads' }
    documents = @{ title = 'Документы'; aliases = @('документы', 'documents'); path = '%USERPROFILE%\Documents' }
    pictures  = @{ title = 'Изображения'; aliases = @('изображения', 'картинки', 'pictures'); path = '%USERPROFILE%\Pictures' }
    music     = @{ title = 'Музыка'; aliases = @('музыка', 'music'); path = '%USERPROFILE%\Music' }
    videos    = @{ title = 'Видео'; aliases = @('видео', 'videos'); path = '%USERPROFILE%\Videos' }
    home      = @{ title = 'Папка пользователя'; aliases = @('профиль', 'home'); path = '%USERPROFILE%' }
}

function Resolve-Roblox {
    $base = Expand-Env '%LOCALAPPDATA%\Roblox\Versions'
    if (-not (Test-Path -LiteralPath $base)) { return $null }
    $exe = Get-ChildItem -Path $base -Filter 'RobloxPlayerBeta.exe' -Recurse -Depth 1 -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($exe) { return $exe.FullName }
    return $null
}

function Resolve-AppExe($app) {
    if ($app.dynamic -eq 'roblox') { return Resolve-Roblox }
    foreach ($candidate in @($app.paths)) {
        if (-not $candidate) { continue }
        $full = Expand-Env $candidate
        if (Test-Path -LiteralPath $full -PathType Leaf) { return $full }
    }
    return $null
}

function Find-AppKey([string]$raw) {
    if (-not $raw) { return $null }
    $needle = $raw.Trim().ToLower()
    foreach ($key in $APPS.Keys) {
        if ($key -eq $needle) { return $key }
        $app = $APPS[$key]
        if ($app.title.ToLower() -eq $needle) { return $key }
        if (@($app.aliases) -contains $needle) { return $key }
    }
    return $null
}

function Find-FolderKey([string]$raw) {
    if (-not $raw) { return $null }
    $needle = ($raw.Trim().ToLower() -replace '^папк[аиуе]\s+', '')
    foreach ($key in $FOLDERS.Keys) {
        if ($key -eq $needle) { return $key }
        if (@($FOLDERS[$key].aliases) -contains $needle) { return $key }
    }
    return $null
}

# --------------------------------------------------------- запуск процессов --

function Start-Detached([string]$file, [string[]]$arguments, [string]$workDir, [switch]$Hidden) {
    $params = @{ FilePath = $file; PassThru = $true }
    $clean = @($arguments | Where-Object { $_ })
    if ($clean.Count -gt 0) { $params.ArgumentList = $clean }
    if ($workDir -and (Test-Path -LiteralPath $workDir)) { $params.WorkingDirectory = $workDir }
    if ($Hidden) { $params.WindowStyle = 'Hidden' }
    $process = Start-Process @params
    if ($process) { return $process.Id }
    return $null
}

function Open-Uri([string]$uri) {
    $scheme = ($uri -split ':', 2)[0].ToLower()
    if ($scheme -notmatch '^[a-z][a-z0-9+.-]{0,20}$' -or @($Config.allowedUriSchemes) -notcontains $scheme) {
        Deny 403 ("Протокол `"{0}:`" не разрешён" -f $scheme)
    }
    if ($uri -match "[`r`n]") { Deny 400 'Некорректный URI' }
    Start-Process $uri | Out-Null
}

function Test-Permission([string]$name) {
    if (-not $Config.permissions.$name) { Deny 403 ("Право «{0}» отключено в config.json" -f $name) }
}

function Get-SafeFolder([string]$raw) {
    if (-not $raw) { Deny 400 'Не указан путь к папке' }
    if ($raw -match '[\r\n\*\?"<>\|]') { Deny 400 'Недопустимые символы в пути' }

    $candidate = (Expand-Env $raw.Trim().Trim('"').Trim("'")) -replace '/', '\'
    if ($candidate.StartsWith('\\')) { Deny 403 'Сетевые пути (UNC) запрещены' }
    if ($candidate -notmatch '^[a-zA-Z]:\\') { $candidate = Join-Path $env:USERPROFILE $candidate }

    $resolved = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
    $inside = $false
    foreach ($root in @($Config.allowedFolderRoots)) {
        $full = [IO.Path]::GetFullPath((Expand-Env $root)).TrimEnd('\')
        if ($resolved -eq $full -or $resolved.ToLower().StartsWith(($full + '\').ToLower())) { $inside = $true; break }
    }
    if (-not $inside) { Deny 403 'Папка вне разрешённых каталогов. Добавьте её в allowedFolderRoots.' }

    foreach ($bad in @($env:WINDIR, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($bad -and $resolved.ToLower().StartsWith(([IO.Path]::GetFullPath($bad).TrimEnd('\') + '\').ToLower())) {
            Deny 403 'Системные каталоги открывать запрещено'
        }
    }
    if (-not (Test-Path -LiteralPath $resolved)) { Deny 404 ("Папка не найдена: {0}" -f $resolved) }
    if (-not (Get-Item -LiteralPath $resolved).PSIsContainer) { Deny 400 'Указанный путь — не папка' }
    return $resolved
}

# ------------------------------------------------------------- действия ------

# ------------------------------- индекс всех установленных программ ---------
#
# Курируемый список $APPS остаётся приоритетным (там алиасы вроде «роблокс»),
# а всё остальное ищется по тому, что реально установлено: меню «Пуск»,
# классические программы и приложения Microsoft Store.
# Запускаются только найденные ярлыки/AppID — произвольную строку выполнить нельзя.

$Script:AppIndex = $null
$Script:AppIndexAt = [DateTime]::MinValue

$TRANSLIT = @{
    'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ж'='zh';'з'='z';'и'='i';'й'='y'
    'к'='k';'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u'
    'ф'='f';'х'='h';'ц'='c';'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'='';'э'='e';'ю'='yu';'я'='ya'
}

function ConvertTo-Comparable([string]$value) {
    $text = $value.ToLower().Replace('ё', 'е')
    return ($text -replace '[^\p{L}\p{Nd}]', '')
}

function ConvertTo-Translit([string]$value) {
    $builder = New-Object Text.StringBuilder
    foreach ($char in $value.ToCharArray()) {
        $key = [string]$char
        if ($TRANSLIT.ContainsKey($key)) { [void]$builder.Append($TRANSLIT[$key]) }
        else { [void]$builder.Append($key) }
    }
    return $builder.ToString()
}

function ConvertTo-Loose([string]$value) {
    <#
      «Мягкая» форма названия — только для нечёткого сравнения.

      Транслитерация неоднозначна в обе стороны, и на этом ломался поиск игр:
      «гарис мод» превращается в garismod, а Garry's Mod — в garrysmod, и
      расстояние 2 не дотягивало до порога. Причина не в опечатке пользователя,
      а в том, что одну и ту же букву принято писать по-разному: и/й/ы → i или y,
      w или v, ck или k, з → z или s, сдвоенные согласные то есть, то нет.

      Здесь эти различия стираются: обе стороны сводятся к одному написанию, и
      «гарис мод» с «Garry's Mod» совпадают точно. Форма для сравнения, показывать
      её пользователю нельзя.
    #>
    $text = ConvertTo-Comparable $value
    if (-not $text) { return '' }
    $text = $text -replace 'ck', 'k'
    $text = $text -replace 'w', 'v'
    $text = $text -replace 'y', 'i'
    $text = $text -replace 'j', 'i'
    $text = $text -replace 'z', 's'
    return [regex]::Replace($text, '(.)\1+', '$1')
}

function Get-Levenshtein([string]$a, [string]$b) {
    if ($a -eq $b) { return 0 }
    if (-not $a) { return $b.Length }
    if (-not $b) { return $a.Length }
    $prev = 0..$b.Length
    for ($i = 1; $i -le $a.Length; $i++) {
        $row = @($i)
        for ($j = 1; $j -le $b.Length; $j++) {
            $cost = if ($a[$i - 1] -eq $b[$j - 1]) { 0 } else { 1 }
            $row += [Math]::Min([Math]::Min($prev[$j] + 1, $row[$j - 1] + 1), $prev[$j - 1] + $cost)
        }
        $prev = $row
    }
    return $prev[$b.Length]
}

function Get-AppIndex([switch]$Force) {
    if ($Script:AppIndex -and -not $Force -and ((Get-Date) - $Script:AppIndexAt).TotalMinutes -lt 10) {
        return $Script:AppIndex
    }
    $list = New-Object Collections.ArrayList
    $seen = @{}
    $junk = '(?i)(удалить|удаление|uninstall|readme|прочти|help|справк|документац|manual|license|лиценз|website|веб-сайт|на сайт|support|поддержк)'

    # 1. то, что Windows показывает в меню «Пуск» (и Win32, и Store)
    try {
        foreach ($entry in (Get-StartApps -ErrorAction Stop)) {
            if (-not $entry.Name -or $entry.Name -match $junk) { continue }
            $key = ConvertTo-Comparable $entry.Name
            if (-not $key -or $seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            [void]$list.Add(@{ name = $entry.Name; kind = 'startapp'; target = $entry.AppID })
        }
    } catch { }

    # 2. запасной путь — ярлыки меню «Пуск» на диске
    if ($list.Count -eq 0) {
        $roots = @((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
                   (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'))
        foreach ($root in $roots) {
            if (-not (Test-Path -LiteralPath $root)) { continue }
            foreach ($file in (Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue)) {
                $name = [IO.Path]::GetFileNameWithoutExtension($file.Name)
                if ($name -match $junk) { continue }
                $key = ConvertTo-Comparable $name
                if (-not $key -or $seen.ContainsKey($key)) { continue }
                $seen[$key] = $true
                [void]$list.Add(@{ name = $name; kind = 'lnk'; target = $file.FullName })
            }
        }
    }

    # 3. App Paths реестра — программы без ярлыка в «Пуске»
    foreach ($hive in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths',
                        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths')) {
        foreach ($item in (Get-ChildItem -Path $hive -ErrorAction SilentlyContinue)) {
            $exe = Split-Path -Leaf $item.PSChildName
            $name = [IO.Path]::GetFileNameWithoutExtension($exe)
            $key = ConvertTo-Comparable $name
            if (-not $key -or $seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            [void]$list.Add(@{ name = $name; kind = 'apppath'; target = $exe })
        }
    }

    $Script:AppIndex = $list
    $Script:AppIndexAt = Get-Date
    return $list
}

function Find-InstalledApp([string]$query) {
    $variants = @((ConvertTo-Comparable $query))
    $translit = ConvertTo-Comparable (ConvertTo-Translit $query)
    if ($translit -and $translit -ne $variants[0]) { $variants += $translit }

    $scored = @()
    foreach ($app in (Get-AppIndex)) {
        $name = ConvertTo-Comparable $app.name
        if (-not $name) { continue }
        $appScore = 0
        foreach ($needle in $variants) {
            if (-not $needle) { continue }
            $score = 0
            if ($name -eq $needle) { $score = 100 }
            elseif ($name.StartsWith($needle)) { $score = 88 - [Math]::Min(($name.Length - $needle.Length), 25) }
            elseif ($name.Contains($needle) -and $needle.Length -ge 3) { $score = 72 }
            elseif ($needle.Length -ge 3) {
                $distance = Get-Levenshtein $needle $name
                $allowed = [Math]::Max(1, [int]($needle.Length / 4))
                if ($distance -le $allowed) { $score = 66 - $distance * 6 }
                # мягкий порог только ради подсказок — и лишь если совпала первая буква,
                # иначе в советы лезет случайный мусор
                elseif ($distance -le $allowed + 2 -and $name[0] -eq $needle[0]) { $score = 30 - $distance }
            }
            if ($score -gt $appScore) { $appScore = $score }
        }
        if ($appScore -gt 0) { $scored += [PSCustomObject]@{ app = $app; score = $appScore } }
    }

    $ranked = $scored | Sort-Object -Property score -Descending
    $best = $null
    if ($ranked -and $ranked[0].score -ge 55) { $best = $ranked[0].app }
    $suggestions = @($ranked | Select-Object -First 3 | ForEach-Object { $_.app.name })

    return @{ best = $best; suggestions = $suggestions }
}

function Start-IndexedApp($app) {
    switch ($app.kind) {
        'startapp' { Start-Process 'explorer.exe' -ArgumentList ("shell:AppsFolder\" + $app.target) | Out-Null }
        'lnk'      { Start-Process -FilePath $app.target | Out-Null }
        default    { Start-Process -FilePath $app.target | Out-Null }
    }
}

# ----------------------------------------------------------- игры Steam ------
#
# Игру мало найти на диске — её надо запустить ПРАВИЛЬНО. Запуск exe напрямую у
# многих игр либо не работает вовсе, либо ломает то, что рассчитывает на Steam:
# античит, облачные сохранения, оверлей, права DRM. Штатный способ один —
# steam://rungameid/<appid>, и для него нужен appid.
#
# Обход папок в steamapps\common (им занимается поиск файлов) appid не даёт: там
# только названия каталогов. Зато рядом лежат appmanifest_<appid>.acf — по файлу
# на установленную игру, и внутри есть и appid, и настоящее название, и состояние
# установки. Читаем их.

function Get-SteamLibraryPath {
    $roots = New-Object Collections.ArrayList

    foreach ($hive in @('HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKLM:\SOFTWARE\Valve\Steam', 'HKCU:\SOFTWARE\Valve\Steam')) {
        try {
            $install = (Get-ItemProperty -Path $hive -ErrorAction Stop).InstallPath
            if ($install -and (Test-Path -LiteralPath $install)) { [void]$roots.Add($install) }
        } catch { }
    }
    foreach ($guess in @('C:\Program Files (x86)\Steam', 'D:\Steam', 'D:\SteamLibrary', 'E:\SteamLibrary')) {
        if (Test-Path -LiteralPath $guess) { [void]$roots.Add($guess) }
    }

    # libraryfolders.vdf перечисляет библиотеки на других дисках
    $libraries = New-Object Collections.ArrayList
    foreach ($root in $roots) {
        [void]$libraries.Add($root)
        $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
        if (-not (Test-Path -LiteralPath $vdf)) { continue }
        try {
            $text = Get-Content -LiteralPath $vdf -Raw -ErrorAction Stop
            foreach ($match in [regex]::Matches($text, '"path"\s*"([^"]+)"')) {
                $library = $match.Groups[1].Value -replace '\\\\', '\'
                if (Test-Path -LiteralPath $library) { [void]$libraries.Add($library) }
            }
        } catch { }
    }
    return @($libraries | Select-Object -Unique)
}

#: Служебные пакеты Steam: формально «приложения», играть в них нельзя.
$STEAM_JUNK = '(?i)(redistributable|proton|steam linux runtime|steamworks|dedicated server|sdk)'

function Get-SteamAppIndex([switch]$Force) {
    if ($Script:SteamIndex -and -not $Force -and ((Get-Date) - $Script:SteamIndexAt).TotalMinutes -lt 10) {
        return $Script:SteamIndex
    }

    $list = New-Object Collections.ArrayList
    $seen = @{}
    foreach ($library in (Get-SteamLibraryPath)) {
        $steamapps = Join-Path $library 'steamapps'
        if (-not (Test-Path -LiteralPath $steamapps)) { continue }
        foreach ($file in (Get-ChildItem -LiteralPath $steamapps -Filter 'appmanifest_*.acf' -File -ErrorAction SilentlyContinue)) {
            try { $text = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop } catch { continue }

            $appid = [regex]::Match($text, '"appid"\s*"(\d+)"').Groups[1].Value
            $name  = [regex]::Match($text, '"name"\s*"([^"]+)"').Groups[1].Value
            $state = [regex]::Match($text, '"StateFlags"\s*"(\d+)"').Groups[1].Value
            if (-not $appid -or -not $name) { continue }
            if ($name -match $STEAM_JUNK) { continue }
            if ($seen.ContainsKey($appid)) { continue }
            $seen[$appid] = $true

            # StateFlags — битовая маска Steam; бит 4 означает «установлено
            # полностью». Игру со снятым битом запускать бессмысленно: Steam
            # начнёт докачивать её и до запуска дело не дойдёт.
            $installed = $false
            if ($state) { $installed = (([int]$state) -band 4) -ne 0 }

            [void]$list.Add(@{
                appid = $appid
                name = $name
                installed = $installed
                library = $library
            })
        }
    }

    $Script:SteamIndex = $list
    $Script:SteamIndexAt = Get-Date
    return $list
}

function Get-GameNameScore([string]$needle, [string]$name) {
    if (-not $needle -or -not $name -or $needle.Length -lt 2) { return 0 }
    if ($name -eq $needle) { return 100 }
    if ($name.StartsWith($needle)) { return 90 - [Math]::Min(($name.Length - $needle.Length), 20) }
    if ($name.Contains($needle) -and $needle.Length -ge 3) { return 76 }
    if ($needle.Contains($name) -and $name.Length -ge 4) { return 70 }
    if ($needle.Length -ge 4) {
        $distance = Get-Levenshtein $needle $name
        $allowed = [Math]::Max(1, [int]($needle.Length / 4))
        if ($distance -le $allowed) { return 66 - $distance * 6 }
    }
    return 0
}

function Find-SteamGame([string]$query) {
    $index = Get-SteamAppIndex
    if (-not $index -or $index.Count -eq 0) { return @{ best = $null; suggestions = @() } }

    # Названия в библиотеке английские, запрос — русский: «кс 2» и
    # «Counter-Strike 2» не сближаются ни расстоянием, ни транслитом. Поэтому
    # сначала переводим запрос по таблице псевдонимов, которая для поиска игр в
    # проекте уже есть, и только потом сравниваем.
    $variants = New-Object Collections.ArrayList
    [void]$variants.Add((ConvertTo-Comparable $query))

    $translit = ConvertTo-Comparable (ConvertTo-Translit $query)
    if ($translit) { [void]$variants.Add($translit) }

    $alias = Resolve-GameAlias $query
    if ($alias) { [void]$variants.Add((ConvertTo-Comparable $alias)) }

    $scored = @()
    foreach ($game in $index) {
        $name = ConvertTo-Comparable $game.name
        if (-not $name) { continue }
        $nameLoose = ConvertTo-Loose $game.name

        $best = 0
        foreach ($needle in $variants) {
            $score = Get-GameNameScore $needle $name
            if ($score -gt $best) { $best = $score }

            # Второй проход по «мягкой» форме — с небольшим штрафом, чтобы точное
            # совпадение написания всегда выигрывало у приблизительного.
            $looseScore = Get-GameNameScore (ConvertTo-Loose $needle) $nameLoose
            if ($looseScore -gt 0) { $looseScore -= 6 }
            if ($looseScore -gt $best) { $best = $looseScore }
        }

        # Не докачанная игра проигрывает установленной при равном совпадении
        if ($best -gt 0 -and -not $game.installed) { $best -= 20 }
        if ($best -gt 0) { $scored += [PSCustomObject]@{ game = $game; score = $best } }
    }

    $ranked = @($scored | Sort-Object -Property score -Descending)
    $found = $null
    if ($ranked.Count -gt 0 -and $ranked[0].score -ge 60) { $found = $ranked[0].game }
    $suggestions = @($ranked | Select-Object -First 3 | ForEach-Object { $_.game.name })
    return @{ best = $found; suggestions = $suggestions }
}

function Start-SteamGame($game) {
    $uri = "steam://rungameid/{0}" -f $game.appid
    Open-Uri $uri
    return @{
        app = ('steam:' + $game.appid)
        title = $game.name
        method = 'steam'
        detail = $uri
    }
}

function Invoke-OpenApp([string]$target, [string]$via) {
    Test-Permission 'allowApps'

    # «Запусти В СТИМЕ кс 2» — Steam назван прямо, и искать надо только среди
    # игр. Без этой ветки запрос ушёл бы в общий поиск установленного, где по
    # слову «кс» вполне может найтись что-то посторонее, и пользователь получил
    # бы не то, что просил, вместо внятного «такой игры в библиотеке нет».
    if ($via -eq 'steam') {
        $onlyGame = Find-SteamGame $target
        if (-not $onlyGame.best) {
            $hint = ''
            if ($onlyGame.suggestions -and $onlyGame.suggestions.Count -gt 0) {
                $hint = ' В библиотеке есть: ' + ($onlyGame.suggestions -join ', ') + '.'
            }
            Deny 404 ("Не нашёл «{0}» в библиотеке Steam.{1}" -f $target, $hint)
        }
        return Start-SteamGame $onlyGame.best
    }

    $key = Find-AppKey $target

    if (-not $key) {
        # вне курируемого списка — ищем среди всего установленного
        if (-not $Config.permissions.allowAnyApp) {
            Deny 404 ("Приложение `"{0}`" не входит в белый список" -f $target)
        }

        # Игры Steam проверяются РАНЬШЕ общего индекса приложений. У игры обычно
        # есть и ярлык в «Пуске», и exe на диске, но запуск через steam:// —
        # единственный, при котором работают античит, облачные сохранения и
        # оверлей. Ярлык бы «сработал» и тихо запустил игру неправильно.
        $game = Find-SteamGame $target
        if ($game.best) { return Start-SteamGame $game.best }

        $search = Find-InstalledApp $target
        if (-not $search.best) {
            $hint = ''
            $suggestions = @()
            if ($search.suggestions) { $suggestions += $search.suggestions }
            if ($game.suggestions) { $suggestions += $game.suggestions }
            $suggestions = @($suggestions | Select-Object -Unique -First 3)
            if ($suggestions.Count -gt 0) {
                $hint = ' Возможно, вы имели в виду: ' + ($suggestions -join ', ') + '.'
            }
            Deny 404 ("Не нашёл «{0}» среди игр и установленных программ.{1}" -f $target, $hint)
        }
        $found = $search.best
        Start-IndexedApp $found
        return @{ app = $found.name; title = $found.name; method = $found.kind; detail = $found.target }
    }

    $app = $APPS[$key]
    $exe = Resolve-AppExe $app
    if ($exe) {
        $pid_ = Start-Detached $exe @($app.args) (Split-Path -Parent $exe)
        return @{ app = $key; title = $app.title; method = 'exe'; detail = $exe; pid = $pid_ }
    }
    if ($app.uri) {
        Open-Uri $app.uri
        return @{ app = $key; title = $app.title; method = 'uri'; detail = $app.uri }
    }
    if ($app.shell) {
        # ShellExecute сам найдёт программу через App Paths реестра
        Start-Process -FilePath $app.shell | Out-Null
        return @{ app = $key; title = $app.title; method = 'shell'; detail = $app.shell }
    }
    Deny 404 ("{0} не найден на этом компьютере" -f $app.title)
}

function Invoke-OpenFolder([string]$target) {
    Test-Permission 'allowFolders'
    $key = Find-FolderKey $target
    $raw = if ($key) { $FOLDERS[$key].path } else { $target }
    $resolved = Get-SafeFolder $raw
    Start-Process -FilePath 'explorer.exe' -ArgumentList $resolved | Out-Null
    $title = if ($key) { $FOLDERS[$key].title } else { Split-Path -Leaf $resolved }
    return @{ method = 'explorer'; title = $title; detail = $resolved }
}

function Invoke-OpenUrl([string]$target) {
    Test-Permission 'allowWeb'
    $url = $target.Trim()
    if ($url -notmatch '^https?://') { $url = 'https://' + $url.TrimStart('/') }
    Open-Uri $url
    return @{ method = 'browser'; title = ($url -replace '^https?://', ''); detail = $url }
}

function Invoke-WebSearch([string]$query) {
    Test-Permission 'allowWeb'
    $text = $query.Trim()
    if (-not $text) { Deny 400 'Пустой поисковый запрос' }
    if ($text.Length -gt 300) { Deny 400 'Слишком длинный запрос' }
    $url = $Config.searchEngine + [Uri]::EscapeDataString($text)
    Open-Uri $url
    return @{ method = 'search'; title = $text; detail = $url }
}

<#
    Процессы, которые не закрываются никогда.

    Список короткий и весь про одно: это не «программы пользователя», а сама
    работающая Windows. Завершить explorer — остаться без панели задач и
    рабочего стола, csrss или winlogon — получить синий экран. Ни одна фраза
    человека не может иметь в виду это, а вот совпасть по имени случайно —
    вполне: «закрой поиск» рядом с процессом SearchHost.
#>
$Script:ProtectedImages = @(
    'explorer', 'csrss', 'winlogon', 'wininit', 'services', 'lsass', 'smss',
    'svchost', 'dwm', 'system', 'idle', 'fontdrvhost', 'sihost', 'ctfmon',
    'runtimebroker', 'shellexperiencehost', 'startmenuexperiencehost',
    'searchhost', 'searchapp', 'textinputhost', 'audiodg', 'registry', 'memory compression'
)

<#
    Ищет запущенную программу по названию — среди того, что реально открыто.

    Нужна потому, что белый список из двух десятков программ описывает далеко
    не всё, что стоит на компьютере. «Закрой капкат» не должно упираться в
    «нет в белом списке»: программа открыта, человек на неё смотрит и просит
    её закрыть — отказ здесь выглядит не осторожностью, а неисправностью.

    Сравнение идёт и по имени процесса, и по заголовку окна: у половины
    программ имя файла не совпадает с тем, как их называют люди (Telegram.exe
    против «телега», Code.exe против «вс код»), зато в заголовке окна название
    почти всегда написано целиком.
#>
function Find-RunningApp([string]$query) {
    $needle = ConvertTo-Loose $query
    if ($needle.Length -lt 2) { return $null }

    $best = $null
    $bestScore = 0
    foreach ($process in (Get-Process -ErrorAction SilentlyContinue)) {
        if (-not $process.Id -or $process.Id -eq $PID) { continue }
        $image = $process.ProcessName
        if (-not $image) { continue }
        if ($Script:ProtectedImages -contains $image.ToLower()) { continue }

        # Окно Cloud HDR исключается отдельно от прочих: помощник живёт в окне
        # браузера, и «закрой хром» иначе закрывало бы того, кто эту команду
        # выполняет. Человек просит закрыть браузер, а не разговор с ним.
        $windowTitle = "$($process.MainWindowTitle)"
        if ($windowTitle -like 'Cloud HDR*') { continue }

        $candidates = @($image)
        if ($windowTitle) {
            $candidates += $windowTitle
            # Заголовки почти всегда составные: «документ.txt — Блокнот»,
            # «YouTube — Google Chrome». Имя программы стоит последним куском.
            $tail = ($windowTitle -split '\s[-—|]\s')[-1]
            if ($tail) { $candidates += $tail.Trim() }
        }

        foreach ($candidate in $candidates) {
            $name = ConvertTo-Loose $candidate
            if ($name.Length -lt 2) { continue }
            $score = 0
            if ($name -eq $needle) { $score = 100 }
            elseif ($name.StartsWith($needle) -and $needle.Length -ge 3) { $score = 84 }
            elseif ($name.Contains($needle) -and $needle.Length -ge 4) { $score = 70 }
            elseif ($needle.Contains($name) -and $name.Length -ge 4) { $score = 66 }
            elseif ($needle.Length -ge 4) {
                $distance = Get-Levenshtein $needle $name
                if ($distance -le [Math]::Max(1, [int]($needle.Length / 4))) { $score = 62 - $distance * 6 }
            }
            # Программа с окном на экране весомее фоновой службы с похожим
            # именем: закрывают обычно то, что видят.
            if ($score -gt 0 -and $windowTitle) { $score += 8 }
            if ($score -gt $bestScore) { $bestScore = $score; $best = $process }
        }
    }

    if ($bestScore -lt 60) { return $null }
    return @{ process = $best; score = $bestScore }
}

function Invoke-CloseApp([string]$target) {
    Test-Permission 'allowClose'

    # Подтверждения здесь нет и не должно быть. Раньше агент отвечал отказом
    # «требуется подтверждение пользователя», а интерфейс поверх этого показывал
    # ещё и окно «вы уверены?». Получалось, что на просьбу закрыть программу
    # помощник дважды переспрашивал — при том, что просьба и была
    # подтверждением. Закрытие окна обратимо: программа открывается заново той
    # же фразой.
    $key = Find-AppKey $target
    if ($key) {
        $app = $APPS[$key]
        if ($app.image) {
            # Убиваем по идентификатору процесса, а не по имени образа: у
            # браузера имя общее с окном самого Cloud HDR, и «закрой хром» по
            # имени образа уносило бы помощника вместе с браузером.
            $victims = @(Get-Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ProcessName -eq [IO.Path]::GetFileNameWithoutExtension($app.image) -and
                               "$($_.MainWindowTitle)" -notlike 'Cloud HDR*' })
            if ($victims.Count) {
                foreach ($victim in $victims) {
                    Start-Process -FilePath 'taskkill.exe' -ArgumentList '/PID', $victim.Id, '/T', '/F' -WindowStyle Hidden | Out-Null
                }
                return @{ app = $key; method = 'taskkill'; title = $app.title
                          detail = ("{0} · закрыто окон: {1}" -f $app.image, $victims.Count) }
            }
            # Программа из списка есть, но сейчас не запущена — это не ошибка,
            # это уже нужное состояние.
            return @{ app = $key; method = 'idle'; title = $app.title; detail = 'программа и так не запущена' }
        }
        if (-not $app.image) { Deny 403 ("{0} закрывать нельзя — это часть Windows" -f $app.title) }
    }

    # Не из белого списка — ищем среди открытого прямо сейчас.
    $running = Find-RunningApp $target
    if (-not $running) {
        Deny 404 ("Не нашёл открытую программу «{0}» — возможно, она уже закрыта" -f $target)
    }
    $name = "$($running.process.MainWindowTitle)"
    if (-not $name) { $name = $running.process.ProcessName }
    Start-Process -FilePath 'taskkill.exe' -ArgumentList '/PID', $running.process.Id, '/T', '/F' -WindowStyle Hidden | Out-Null
    return @{ app = $running.process.ProcessName; method = 'taskkill'; title = $name
              detail = ("{0}.exe · найдено среди запущенного" -f $running.process.ProcessName) }
}

# ------------------------------------------------------------- громкость ----
#
# Клавиши мультимедиа (SendKeys 174/175) умеют ровно одно: подвинуть громкость
# на один системный шаг. Ими нельзя ни узнать текущий уровень, ни поставить
# нужный — «поставь громкость на 30» превращалось в несколько нажатий вслепую,
# и результат зависел от того, что было до команды.
#
# Точный уровень даёт Core Audio: интерфейс IAudioEndpointVolume умеет и читать,
# и задавать громкость долей от нуля до единицы. Объявляем ровно те методы, что
# нужны, а пропущенные заменяем заглушками — порядок в таблице виртуальных
# функций обязан совпасть с оригиналом, иначе вызовется не то.

function Initialize-AudioApi {
    if ('CloudHDR.Audio' -as [type]) { return $true }
    $code = @'
using System;
using System.Runtime.InteropServices;
namespace CloudHDR {
  [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioEndpointVolume {
    int RegisterControlChangeNotify(IntPtr p); int UnregisterControlChangeNotify(IntPtr p);
    int GetChannelCount(out uint c);
    int SetMasterVolumeLevel(float level, ref Guid ctx);
    int SetMasterVolumeLevelScalar(float level, ref Guid ctx);
    int GetMasterVolumeLevel(out float level);
    int GetMasterVolumeLevelScalar(out float level);
    int SetChannelVolumeLevel(uint n, float level, ref Guid ctx);
    int SetChannelVolumeLevelScalar(uint n, float level, ref Guid ctx);
    int GetChannelVolumeLevel(uint n, out float level);
    int GetChannelVolumeLevelScalar(uint n, out float level);
    int SetMute(bool mute, ref Guid ctx);
    int GetMute(out bool mute);
  }
  [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice {
    int Activate(ref Guid iid, int ctx, IntPtr param, [MarshalAs(UnmanagedType.IUnknown)] out object o);
  }
  [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int flow, int mask, IntPtr devices);
    int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device);
  }
  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class DeviceEnumerator { }
  public static class Audio {
    static IAudioEndpointVolume Endpoint() {
      IMMDeviceEnumerator e = (IMMDeviceEnumerator)(new DeviceEnumerator());
      IMMDevice device; e.GetDefaultAudioEndpoint(0, 1, out device);
      Guid iid = typeof(IAudioEndpointVolume).GUID;
      object o; device.Activate(ref iid, 23, IntPtr.Zero, out o);
      return (IAudioEndpointVolume)o;
    }
    public static float Get() { float v; Endpoint().GetMasterVolumeLevelScalar(out v); return v; }
    public static void Set(float v) { Guid g = Guid.Empty; Endpoint().SetMasterVolumeLevelScalar(v, ref g); }
    public static bool GetMute() { bool m; Endpoint().GetMute(out m); return m; }
    public static void SetMute(bool m) { Guid g = Guid.Empty; Endpoint().SetMute(m, ref g); }
  }
}
'@
    try { Add-Type -TypeDefinition $code -ErrorAction Stop; return $true } catch { return $false }
}

function Send-VolumeKey([int]$code, [int]$times) {
    $shell = New-Object -ComObject WScript.Shell
    for ($i = 0; $i -lt $times; $i++) { $shell.SendKeys([char]$code); Start-Sleep -Milliseconds 40 }
}

<#
    Звуковое устройство может смениться прямо под рукой: воткнули USB-микрофон
    с динамиками, Windows переназначила устройство по умолчанию — и ближайший
    вызов падает с «The I/O operation has been aborted» (0x800703E3). Точка
    подключения берётся заново на каждый вызов, так что повтор через мгновение
    обычно проходит. Если нет — громкость меняется клавишами, а не ошибкой с
    кодом HRESULT на экране.
#>
function Invoke-Audio([scriptblock]$op) {
    try { return (& $op) } catch {
        Start-Sleep -Milliseconds 300
        return (& $op)
    }
}

function Invoke-Volume([string]$intent, $amount, $level) {
    Test-Permission 'allowSystem'
    $hasApi = Initialize-AudioApi
    if ($hasApi) {
        try {
            return (Invoke-Audio { Invoke-VolumeApi $intent $amount $level })
        } catch {
            Write-Log ("VOLUME звуковое устройство не ответило ({0}) — работаю клавишами" -f $_.Exception.Message)
            if ($intent -eq 'volume_set') { Deny 503 'Звуковое устройство сейчас не отвечает — Windows только что его сменила. Повторите через пару секунд.' }
            $hasApi = $false
        }
    }
    return (Invoke-VolumeKeys $intent $amount)
}

function Invoke-VolumeApi([string]$intent, $amount, $level) {
    $hasApi = $true

    # --- точный уровень: «поставь громкость на 30» --------------------------
    if ($intent -eq 'volume_set') {
        if ($null -eq $level) { Deny 400 'Не понял, какую громкость поставить. Например: «поставь громкость на 30».' }
        $target = [Math]::Min([Math]::Max([int]$level, 0), 100)
        if (-not $hasApi) { Deny 501 'Точная громкость недоступна: не удалось подключиться к аудиоустройству.' }
        $was = [int][Math]::Round([CloudHDR.Audio]::Get() * 100)
        [CloudHDR.Audio]::Set($target / 100.0)
        if ($target -gt 0 -and [CloudHDR.Audio]::GetMute()) { [CloudHDR.Audio]::SetMute($false) }
        return @{ method = 'audio'; title = ("Громкость {0}%" -f $target)
                  detail = ("было {0}% → стало {1}%" -f $was, $target); level = $target }
    }

    # --- переключение звука --------------------------------------------------
    if ($intent -eq 'volume_unmute') {
        [CloudHDR.Audio]::SetMute($false)
        return @{ method = 'audio'; title = 'Звук включён'
                  detail = ("громкость {0}%" -f [int][Math]::Round([CloudHDR.Audio]::Get() * 100)) }
    }
    if ($intent -eq 'volume_mute') {
        if ($hasApi) {
            # «Выключи звук» выключает, а не переключает: раньше повторная фраза
            # при уже выключенном звуке его включала — ровно наоборот просьбе.
            if ([CloudHDR.Audio]::GetMute()) {
                return @{ method = 'audio'; title = 'Звук уже выключен'; detail = 'включить — «включи звук»' }
            }
            [CloudHDR.Audio]::SetMute($true)
            return @{ method = 'audio'; title = 'Звук выключен'; detail = 'без звука' }
        }
        Send-VolumeKey 173 1
        return @{ method = 'volume'; title = 'Звук переключён'; detail = 'SendKeys 173' }
    }

    # --- шаг вверх или вниз --------------------------------------------------
    # С доступным API шаг задаётся В ПРОЦЕНТАХ и означает ровно то, что сказано:
    # «сделай громче на 20» поднимет на 20 пунктов, а не на 20 нажатий клавиши.
    $step = 10
    if ($amount) { $step = [Math]::Min([Math]::Max([int]$amount, 1), 100) }

    if ($hasApi) {
        $was = [int][Math]::Round([CloudHDR.Audio]::Get() * 100)
        $target = if ($intent -eq 'volume_up') { $was + $step } else { $was - $step }
        $target = [Math]::Min([Math]::Max($target, 0), 100)
        [CloudHDR.Audio]::Set($target / 100.0)
        if ($intent -eq 'volume_up' -and [CloudHDR.Audio]::GetMute()) { [CloudHDR.Audio]::SetMute($false) }
        $label = if ($intent -eq 'volume_up') { 'Громкость увеличена' } else { 'Громкость уменьшена' }
        return @{ method = 'audio'; title = $label
                  detail = ("{0}% → {1}%" -f $was, $target); level = $target }
    }
}

# Запасной путь: API недоступен или устройство не отвечает — клавишами.
function Invoke-VolumeKeys([string]$intent, $amount) {
    if ($intent -eq 'volume_set') { Deny 501 'Точная громкость недоступна: не удалось подключиться к аудиоустройству.' }
    if ($intent -eq 'volume_mute') {
        Send-VolumeKey 173 1
        return @{ method = 'volume'; title = 'Звук переключён'; detail = 'клавиша «без звука»' }
    }
    $step = 10
    if ($amount) { $step = [Math]::Min([Math]::Max([int]$amount, 1), 100) }
    $times = [Math]::Min([Math]::Max([int]($step / 2), 1), 10)
    Send-VolumeKey $(if ($intent -eq 'volume_up') { 175 } else { 174 }) $times
    $label = if ($intent -eq 'volume_up') { 'Громкость увеличена' } else { 'Громкость уменьшена' }
    return @{ method = 'volume'; title = $label; detail = ("нажатий клавиши: {0}" -f $times) }
}

function Invoke-Screenshot {
    Test-Permission 'allowSystem'
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $dir = Expand-Env $Config.screenshotDir
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $file = Join-Path $dir ("cloudhdr-{0:yyyy-MM-dd_HH-mm-ss}.png" -f (Get-Date))

    $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($file, [Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()

    return @{ method = 'screenshot'; title = 'Скриншот сохранён'; detail = $file }
}

# ---------------------------------------------------------------- зрение -----
#
# Разбор картинок идёт целиком в браузере (public/js/vision.js): свет, цвет,
# резкость, палитра и паспорт EXIF считаются на той же странице, где показан
# ответ. Агенту достаётся ровно одно, чего страница не может, — достать файл с
# диска.
#
# Нужно это ради «разбери мой последний скриншот». Cloud HDR снимает экран сам и
# складывает снимки в свою папку; заставлять человека после этого искать файл
# через окно выбора было бы насмешкой над собственной же функцией.
#
# Границы жёсткие и намеренно те же, что у «открой папку»: путь обязан лежать
# внутри allowedFolderRoots, расширение — быть картинкой, размер — влезать в
# рамки. Произвольный файл этим путём не прочитать: ни .ps1, ни .kdbx, ни файл
# из системного каталога.

$VISION_EXT = @('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.avif')

$VISION_MIME = @{
    '.png' = 'image/png'; '.jpg' = 'image/jpeg'; '.jpeg' = 'image/jpeg'; '.webp' = 'image/webp'
    '.bmp' = 'image/bmp'; '.gif' = 'image/gif'; '.avif' = 'image/avif'
}

$VISION_MAX = 48MB

function Get-RecentImages([int]$limit = 12) {
    Test-Permission 'allowFolders'

    # Порядок папок — это порядок ожиданий. Первой стоит своя папка снимков:
    # если человек говорит «мой последний скриншот» сразу после того, как
    # приложение его сняло, речь почти наверняка о нём. Дальше — куда экран
    # снимает сама Windows, и только потом общие свалки.
    $roots = @()
    if ($Config.screenshotDir) { $roots += (Expand-Env $Config.screenshotDir) }
    $roots += @(
        (Join-Path $env:USERPROFILE 'Pictures\Screenshots'),
        (Join-Path $env:USERPROFILE 'OneDrive\Pictures\Screenshots'),
        (Join-Path $env:USERPROFILE 'OneDrive\Изображения\Снимки экрана'),
        (Join-Path $env:USERPROFILE 'Pictures'),
        (Join-Path $env:USERPROFILE 'Desktop'),
        (Join-Path $env:USERPROFILE 'Downloads')
    )

    # Вглубь не идём: Pictures у иного человека — это десятки тысяч файлов в
    # сотне папок, и обход занял бы минуты, а нужен верхний десяток по свежести.
    $seen = @{}
    $found = @()
    foreach ($root in $roots) {
        if (-not $root) { continue }
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $full = [IO.Path]::GetFullPath($root).TrimEnd('\').ToLower()
        if ($seen.ContainsKey($full)) { continue }
        $seen[$full] = $true

        try {
            $found += Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
                Where-Object { $VISION_EXT -contains $_.Extension.ToLower() -and
                               $_.Length -gt 0 -and $_.Length -le $VISION_MAX }
        } catch { }
    }

    return @($found |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First $limit |
        ForEach-Object {
            @{ name = $_.Name; path = $_.FullName; size = $_.Length
               folder = $_.DirectoryName
               modified = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') }
        })
}

function Get-SafeImage([string]$raw) {
    if (-not $raw) { Deny 400 'Не указан путь к картинке' }
    if ($raw -match '[\r\n\*\?"<>\|]') { Deny 400 'Недопустимые символы в пути' }

    $candidate = (Expand-Env $raw.Trim().Trim('"').Trim("'")) -replace '/', '\'
    if ($candidate.StartsWith('\\')) { Deny 403 'Сетевые пути (UNC) запрещены' }
    if ($candidate -notmatch '^[a-zA-Z]:\\') { Deny 400 'Нужен полный путь к файлу' }

    $resolved = [IO.Path]::GetFullPath($candidate)
    $extension = [IO.Path]::GetExtension($resolved).ToLower()
    if ($VISION_EXT -notcontains $extension) { Deny 400 'Отдаю только изображения' }

    # Проверку каталога делает ТА ЖЕ функция, что и «открой папку». Списывать её
    # правила во второе место значило бы завести второй список разрешений,
    # который однажды разойдётся с первым.
    $null = Get-SafeFolder (Split-Path -Parent $resolved)

    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { Deny 404 'Файл не найден' }
    $file = Get-Item -LiteralPath $resolved
    if ($file.Length -gt $VISION_MAX) { Deny 413 'Картинка тяжелее 48 МБ' }
    return $file
}

# --- закрепление окна приложения поверх остальных (user32) ---

Add-Type -Namespace CloudHdr -Name Win -MemberDefinition @'
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@

# --------------------------------------------------------- запись экрана -----
#
# Пишет экран встроенный в Windows Game Bar — тот же, что по Win+Alt+R. Выбран он
# не от бедности: ffmpeg пришлось бы устанавливать, а Game Bar есть в системе,
# умеет звук и не требует прав администратора. Готовый файл он кладёт в
# «Видео\Captures».
#
# Тонкость, из-за которой здесь код, а не одна строка: у Game Bar нет ни
# командной строки, ни COM-интерфейса — только горячая клавиша. А клавишу Win
# нельзя послать через SendKeys, которым в этом файле переключается громкость:
# WScript.Shell её просто не знает. Поэтому нажатие идёт через keybd_event.
#
# И главное: горячая клавиша ничего не возвращает. Если запись выключена в
# параметрах или окно не поддаётся захвату, Game Bar молча ничего не сделает, а
# агент отчитался бы об успехе. Поэтому результат ПРОВЕРЯЕТСЯ по появлению файла
# в папке записей — отчёт об успехе означает, что запись действительно идёт.

$Script:Recording = @{ active = $false; file = $null; startedAt = $null }

function Initialize-KeyboardApi {
    if ('CloudHDR.Keys' -as [type]) { return }
    # Одной строкой, без here-string: C# переводов строки не требует, а
    # here-string внутри функции пришлось бы закрывать в нулевой колонке.
    #
    # -UsingNamespace здесь нет намеренно: Add-Type подставляет
    # System.Runtime.InteropServices сам, и повторное указание даёт вторую
    # директиву using — а компилятор считает это предупреждение ошибкой.
    # Поэтому атрибут записан полным именем.
    $member = '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, System.UIntPtr dwExtraInfo);'
    Add-Type -Namespace 'CloudHDR' -Name 'Keys' -MemberDefinition $member
}

function Send-GameBarChord {
    Initialize-KeyboardApi
    $win = [byte]0x5B      # VK_LWIN
    $alt = [byte]0x12      # VK_MENU
    $key = [byte]0x52      # 'R'
    $up  = [uint32]2       # KEYEVENTF_KEYUP
    $none = [UIntPtr]::Zero

    [CloudHDR.Keys]::keybd_event($win, 0, 0, $none)
    [CloudHDR.Keys]::keybd_event($alt, 0, 0, $none)
    [CloudHDR.Keys]::keybd_event($key, 0, 0, $none)
    Start-Sleep -Milliseconds 70
    [CloudHDR.Keys]::keybd_event($key, 0, $up, $none)
    [CloudHDR.Keys]::keybd_event($alt, 0, $up, $none)
    [CloudHDR.Keys]::keybd_event($win, 0, $up, $none)
}

function Get-CaptureDir {
    try {
        $custom = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' -ErrorAction Stop).CustomCapturePath
        if ($custom -and (Test-Path -LiteralPath $custom)) { return $custom }
    } catch { }
    return (Join-Path $env:USERPROFILE 'Videos\Captures')
}

function Test-GameBarEnabled {
    # Отказываем только при ЯВНОМ выключении: когда параметра нет, запись в
    # Windows включена по умолчанию, и придумывать отказ на этом основании
    # значило бы не давать работать там, где всё в порядке.
    try {
        $dvr = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' -ErrorAction Stop
        if ($null -ne $dvr.AppCaptureEnabled -and ([int]$dvr.AppCaptureEnabled) -eq 0) { return $false }
    } catch { }
    return $true
}

function Get-CaptureFile([string]$dir) {
    return @(Get-ChildItem -LiteralPath $dir -Filter '*.mp4' -File -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending)
}

function Invoke-RecordStart {
    Test-Permission 'allowSystem'
    if (-not (Test-GameBarEnabled)) {
        Deny 409 ('Запись экрана выключена в параметрах Windows. Откройте «Параметры → Игры → Записи» ' +
                  'и включите запись происходящего, затем повторите команду.')
    }

    $dir = Get-CaptureDir
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $before = @{}
    foreach ($file in (Get-CaptureFile $dir)) { $before[$file.Name] = $true }

    Send-GameBarChord

    # Game Bar создаёт файл записи сразу, но не мгновенно: ему надо поднять
    # оверлей. Пятнадцать секунд здесь не запас «на всякий случай» — столько
    # занимает ПЕРВЫЙ вызов, когда GameBar.exe ещё не запущен. На проверке
    # пятисекундного ожидания хватало на второй запуск и не хватало на первый,
    # и агент честно сообщал об отказе там, где запись просто не успевала
    # начаться. Когда Game Bar уже поднят, цикл выходит через полсекунды.
    $appeared = $null
    for ($i = 0; $i -lt 75; $i++) {
        Start-Sleep -Milliseconds 200
        foreach ($file in (Get-CaptureFile $dir)) {
            if (-not $before.ContainsKey($file.Name)) { $appeared = $file; break }
        }
        if ($appeared) { break }
    }

    if (-not $appeared) {
        Deny 409 ('Game Bar не начал запись. Обычно причина одна из двух: запись выключена в ' +
                  '«Параметры → Игры → Записи», либо активное окно защищено от захвата. ' +
                  'Проверьте, что Win+Alt+R работает вручную.')
    }

    $Script:Recording = @{ active = $true; file = $appeared.FullName; startedAt = Get-Date }
    Write-Log ("record start -> {0}" -f $appeared.FullName)
    return @{
        method = 'gamebar'
        title = 'Запись экрана идёт'
        detail = $appeared.FullName
        file = $appeared.Name
        folder = $dir
    }
}

function Invoke-RecordStop {
    Test-Permission 'allowSystem'
    $dir = Get-CaptureDir
    $tracked = $Script:Recording.file

    Send-GameBarChord

    # На остановке Game Bar досводит файл: размер растёт ещё секунду-две. Ждём,
    # пока он перестанет меняться, — только тогда файл действительно готов.
    $target = $null
    $previous = -1
    $stable = 0
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 200
        $files = Get-CaptureFile $dir
        if ($files.Count -eq 0) { continue }

        $target = $files[0]
        if ($tracked) {
            $match = @($files | Where-Object { $_.FullName -eq $tracked })
            if ($match.Count -gt 0) { $target = $match[0] }
        }

        $size = $target.Length
        if ($size -gt 0 -and $size -eq $previous) {
            $stable++
            if ($stable -ge 3) { break }     # ~0,6 с без изменений
        } else {
            $stable = 0
        }
        $previous = $size
    }

    $wasActive = $Script:Recording.active
    $seconds = $null
    if ($wasActive -and $Script:Recording.startedAt) {
        $seconds = [int]((Get-Date) - $Script:Recording.startedAt).TotalSeconds
    }
    $Script:Recording = @{ active = $false; file = $null; startedAt = $null }

    if (-not $target) {
        # Записи не было — но и ошибки тут нет: пользователь мог сказать
        # «останови запись», когда ничего не писалось. Сообщаем как есть.
        return @{ method = 'gamebar'; title = 'Записи не было'; detail = 'файл записи не найден'; folder = $dir }
    }

    Write-Log ("record stop -> {0} ({1} байт)" -f $target.FullName, $target.Length)
    $size = [Math]::Round($target.Length / 1MB, 1)
    $detail = "{0} · {1} МБ" -f $target.Name, $size
    if ($null -ne $seconds) { $detail = "{0} · {1} МБ · {2} с" -f $target.Name, $size, $seconds }
    return @{
        method = 'gamebar'
        title = 'Запись сохранена'
        detail = $detail
        file = $target.Name
        path = $target.FullName
        folder = $dir
        bytes = $target.Length
    }
}

function Get-AppWindow {
    # Окно приложения — это Chrome/Edge с заголовком страницы «Cloud HDR …»
    $process = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -in @('chrome', 'msedge', 'brave', 'vivaldi') -and
                       $_.MainWindowTitle -like 'Cloud HDR*' -and $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
    if (-not $process) { Deny 404 'Окно Cloud HDR не найдено. Откройте приложение и повторите.' }
    return $process
}

function Set-WindowTopmost([bool]$on) {
    Test-Permission 'allowSystem'
    $process = Get-AppWindow
    $insertAfter = if ($on) { [IntPtr](-1) } else { [IntPtr](-2) }   # HWND_TOPMOST / HWND_NOTOPMOST
    $SWP_NOMOVE = 0x0002; $SWP_NOSIZE = 0x0001; $SWP_SHOWWINDOW = 0x0040
    [CloudHdr.Win]::SetWindowPos($process.MainWindowHandle, $insertAfter, 0, 0, 0, 0, $SWP_NOMOVE -bor $SWP_NOSIZE -bor $SWP_SHOWWINDOW) | Out-Null
    if ($on) { [CloudHdr.Win]::SetForegroundWindow($process.MainWindowHandle) | Out-Null }
    $title = if ($on) { 'Окно поверх остальных' } else { 'Окно откреплено' }
    return @{ method = 'window'; title = $title; detail = $process.MainWindowTitle }
}

function Invoke-Lock {
    Test-Permission 'allowSystem'
    Start-Process -FilePath 'rundll32.exe' -ArgumentList 'user32.dll,LockWorkStation' -WindowStyle Hidden | Out-Null
    return @{ method = 'lock'; title = 'Рабочая станция заблокирована'; detail = 'user32.dll,LockWorkStation' }
}

# ------------------------------------------------- сканирование файлов -------
#
# «найди игру амонг ас», «просканируй все файлы и найди отчёт».
#
# Полный обход C:\ занял бы минуты, поэтому источники идут по возрастанию цены:
# сначала бесплатные реестры (Steam, Epic, «Установка и удаление программ»),
# затем обход диска с ограничением по глубине и по времени.
#
# Запустить можно только то, что агент нашёл сам в этой сессии: путь из браузера
# сверяется с $Script:ScanFound и без совпадения отклоняется.

$Script:ScanFound = @{}

# Русские названия игр не совпадают с именами папок на диске — таблица связывает
# «амонг ас» с «Among Us», иначе поиск по подстроке ничего не найдёт.
$GAME_ALIASES = @(
    @{ name = 'Among Us';         words = @('амонг ас', 'амонгас', 'амонг ус', 'амонг', 'among us', 'amongus') }
    @{ name = 'Minecraft';        words = @('майнкрафт', 'майнкравт', 'майн', 'minecraft') }
    @{ name = 'Roblox';           words = @('роблокс', 'рублокс', 'roblox') }
    @{ name = 'Counter-Strike';   words = @('контра', 'кс го', 'ксго', 'кс 2', 'кс2', 'counter strike', 'csgo', 'cs2') }
    @{ name = 'Grand Theft Auto'; words = @('гта', 'гта 5', 'гта5', 'гта 6', 'gta', 'grand theft auto') }
    @{ name = 'Fortnite';         words = @('фортнайт', 'фортнит', 'fortnite') }
    @{ name = 'Valorant';         words = @('валорант', 'valorant') }
    @{ name = 'Dota';             words = @('дота', 'дота 2', 'дота2', 'dota') }
    @{ name = 'Terraria';         words = @('террария', 'terraria') }
    @{ name = 'Stardew Valley';   words = @('стардью', 'стардев', 'stardew') }
    @{ name = 'Genshin Impact';   words = @('геншин', 'genshin') }
    @{ name = 'Brawl Stars';      words = @('бравл старс', 'бравл', 'brawl stars') }
    @{ name = 'Cyberpunk';        words = @('киберпанк', 'cyberpunk') }
    @{ name = 'The Witcher';      words = @('ведьмак', 'witcher') }
    @{ name = 'PUBG';             words = @('пабг', 'пубг', 'pubg') }
    @{ name = 'Apex Legends';     words = @('апекс', 'apex legends') }
    @{ name = 'Rust';             words = @('раст', 'rust') }
    @{ name = 'Subnautica';       words = @('субнавтика', 'subnautica') }
    @{ name = 'Fall Guys';        words = @('фолл гайс', 'фал гайз', 'fall guys') }
    @{ name = 'Phasmophobia';     words = @('фазмофобия', 'phasmophobia') }
    # Ниже — то, что не берётся ни транслитом, ни расстоянием: русские переводы
    # названий и сленг. «Среди нас» и «Among Us» не сближаются никаким алгоритмом,
    # это разные слова; «бимка» и «резик» — тем более.
    @{ name = 'Among Us';         words = @('среди нас') }
    @{ name = "Garry's Mod";      words = @('гарис мод', 'гаррис мод', 'гмод', 'гэрис мод', 'garrys mod', 'gmod') }
    @{ name = 'BeamNG.drive';     words = @('бимка', 'бимэнджи', 'бим эн джи', 'beamng', 'beamng drive') }
    @{ name = 'Black Myth: Wukong'; words = @('вуконг', 'укун', 'чёрный миф', 'черный миф', 'wukong', 'black myth') }
    @{ name = 'Resident Evil';    words = @('резидент ивел', 'резидент ивил', 'резик', 'обитель зла', 'resident evil') }
    @{ name = 'Slay the Spire';   words = @('слей зе спайр', 'спайр', 'слэй зэ спайр', 'slay the spire') }
    @{ name = 'Tabletop Simulator'; words = @('тейблтоп', 'настольный симулятор', 'tabletop simulator') }
    @{ name = 'Buckshot Roulette'; words = @('бакшот', 'бакшот рулетка', 'buckshot roulette') }
    @{ name = 'DCS World';        words = @('дцс', 'дцс ворлд', 'dcs world') }
    @{ name = 'Geometry Dash';    words = @('геометри даш', 'гд', 'geometry dash') }
    @{ name = 'Subnautica';       words = @('субнатика', 'сабнатика') }
    @{ name = 'Cyberpunk 2077';   words = @('киберпанк 2077', 'сайберпанк') }
)

function Resolve-GameAlias([string]$query) {
    $needle = ConvertTo-Comparable $query
    if (-not $needle) { return $null }
    foreach ($game in $GAME_ALIASES) {
        foreach ($word in $game.words) {
            $key = ConvertTo-Comparable $word
            if ($key -and ($needle -eq $key -or $needle.Contains($key) -or $key.Contains($needle))) { return $game.name }
        }
    }
    return $null
}

function Get-ScanSetting([string]$name, $fallback) {
    if ($Config.scan -and $null -ne $Config.scan.$name) { return $Config.scan.$name }
    return $fallback
}

# Служебные .exe, которые лежат рядом с игрой и не являются ею.
# Без этого фильтра «самый большой файл» в папке Unity-игры — UnityCrashHandler32.exe,
# и кнопка «Запустить» открывала бы обработчик сбоев вместо самой игры.
$EXE_JUNK = '(?i)^(unitycrashhandler|unins|uninstall|setup|installer|vcredist|vc_redist|dxsetup|dxwebsetup|crashpad|crashreport|notification_helper|dotnetfx|oalinst|directx|redist|helper|updater|launcherpatcher)'

<#
    Выбирает главный исполняемый файл каталога.
    Приоритет: имя совпадает с именем папки → просто не служебный → самый крупный.
#>
function Select-MainExe([string]$folder, [int]$depth = 0) {
    if (-not $folder -or -not (Test-Path -LiteralPath $folder)) { return $null }

    $params = @{ LiteralPath = $folder; Filter = '*.exe'; File = $true; ErrorAction = 'SilentlyContinue' }
    if ($depth -gt 0) { $params.Recurse = $true; $params.Depth = $depth }
    $all = @(Get-ChildItem @params)
    if ($all.Count -eq 0) { return $null }

    $clean = @($all | Where-Object { $_.BaseName -notmatch $EXE_JUNK })
    if ($clean.Count -eq 0) { $clean = $all }

    $folderKey = ConvertTo-Comparable (Split-Path -Leaf $folder)
    if ($folderKey) {
        $named = @($clean | Where-Object {
            $key = ConvertTo-Comparable $_.BaseName
            $key -and ($key -eq $folderKey -or $key.StartsWith($folderKey) -or $folderKey.StartsWith($key))
        })
        if ($named.Count -gt 0) { return ($named | Sort-Object Length -Descending | Select-Object -First 1).FullName }
    }
    return ($clean | Sort-Object Length -Descending | Select-Object -First 1).FullName
}

# --- источник 1: библиотеки Steam ---
function Get-SteamGame {
    $result = New-Object Collections.ArrayList
    $roots = New-Object Collections.ArrayList

    foreach ($hive in @('HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKLM:\SOFTWARE\Valve\Steam', 'HKCU:\SOFTWARE\Valve\Steam')) {
        try {
            $install = (Get-ItemProperty -Path $hive -ErrorAction Stop).InstallPath
            if ($install -and (Test-Path -LiteralPath $install)) { [void]$roots.Add($install) }
        } catch { }
    }
    foreach ($guess in @('C:\Program Files (x86)\Steam', 'D:\Steam', 'D:\SteamLibrary', 'E:\SteamLibrary')) {
        if (Test-Path -LiteralPath $guess) { [void]$roots.Add($guess) }
    }

    # libraryfolders.vdf перечисляет дополнительные библиотеки на других дисках
    $libraries = New-Object Collections.ArrayList
    foreach ($root in $roots) {
        [void]$libraries.Add($root)
        $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
        if (-not (Test-Path -LiteralPath $vdf)) { continue }
        try {
            $text = Get-Content -LiteralPath $vdf -Raw -ErrorAction Stop
            foreach ($match in [regex]::Matches($text, '"path"\s*"([^"]+)"')) {
                $library = $match.Groups[1].Value -replace '\\\\', '\'
                if (Test-Path -LiteralPath $library) { [void]$libraries.Add($library) }
            }
        } catch { }
    }

    foreach ($library in ($libraries | Select-Object -Unique)) {
        $common = Join-Path $library 'steamapps\common'
        if (-not (Test-Path -LiteralPath $common)) { continue }
        foreach ($folder in (Get-ChildItem -LiteralPath $common -Directory -ErrorAction SilentlyContinue)) {
            $exe = Select-MainExe $folder.FullName 1
            [void]$result.Add(@{
                name = $folder.Name
                path = if ($exe) { $exe } else { $folder.FullName }
                folder = $folder.FullName
                kind = if ($exe) { 'exe' } else { 'folder' }
                source = 'Steam'
            })
        }
    }
    return $result
}

# --- источник 2: Epic Games ---
function Get-EpicGame {
    $result = New-Object Collections.ArrayList
    $manifest = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
    if (-not (Test-Path -LiteralPath $manifest)) { return $result }
    try {
        $data = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($entry in @($data.InstallationList)) {
            if (-not $entry.InstallLocation -or -not (Test-Path -LiteralPath $entry.InstallLocation)) { continue }
            $name = if ($entry.AppName) { [string]$entry.AppName } else { Split-Path -Leaf $entry.InstallLocation }
            $exe = Select-MainExe ([string]$entry.InstallLocation) 2
            [void]$result.Add(@{
                name = $name
                path = if ($exe) { $exe } else { [string]$entry.InstallLocation }
                folder = [string]$entry.InstallLocation
                kind = if ($exe) { 'exe' } else { 'folder' }
                source = 'Epic Games'
            })
        }
    } catch { }
    return $result
}

# --- источник 3: «Установка и удаление программ» ---
function Get-RegistryProgram {
    $result = New-Object Collections.ArrayList
    $hives = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
               'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
               'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')
    foreach ($hive in $hives) {
        foreach ($item in (Get-ChildItem -Path $hive -ErrorAction SilentlyContinue)) {
            try { $info = Get-ItemProperty -Path $item.PSPath -ErrorAction Stop } catch { continue }
            if (-not $info.DisplayName -or $info.SystemComponent -eq 1) { continue }

            # в реестре путь нередко записан в кавычках и с хвостовым слэшем
            $location = ([string]$info.InstallLocation).Trim().Trim('"').TrimEnd('\')
            $exe = Select-MainExe $location 0
            [void]$result.Add(@{
                name = ([string]$info.DisplayName).Trim()
                path = if ($exe) { $exe } elseif ($location) { $location } else { '' }
                folder = $location
                kind = if ($exe) { 'exe' } elseif ($location) { 'folder' } else { 'entry' }
                source = 'Установленные программы'
            })
        }
    }
    return $result
}

# --- источник 4: обход диска с бюджетом по времени ---

$SCAN_SKIP = '(?i)\\(Windows|\$Recycle\.Bin|System Volume Information|WinSxS|DriverStore|node_modules|\.git|\.cache|OneDriveTemp|MSOCache|Recovery|Config\.Msi|PerfLogs|Intel|AMD|NVIDIA\\NvBackend)(\\|$)'

function Search-Disk([string[]]$roots, [string[]]$needles, [int]$budgetMs, [int]$maxDepth, [int]$maxResults) {
    $result = New-Object Collections.ArrayList
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $visited = 0
    $truncated = $false

    $stack = New-Object Collections.Stack
    foreach ($root in $roots) {
        if ($root -and (Test-Path -LiteralPath $root)) { $stack.Push(@{ path = $root; depth = 0 }) }
    }

    while ($stack.Count -gt 0) {
        if ($watch.ElapsedMilliseconds -gt $budgetMs) { $truncated = $true; break }
        if ($result.Count -ge $maxResults) { $truncated = $true; break }

        $node = $stack.Pop()
        $visited++

        # файлы текущего каталога
        try {
            foreach ($file in (Get-ChildItem -LiteralPath $node.path -File -Force -ErrorAction SilentlyContinue)) {
                $extension = $file.Extension.ToLower()
                if ($extension -notin @('.exe', '.lnk', '.url', '.jar')) { continue }
                $plain = ConvertTo-Comparable ([IO.Path]::GetFileNameWithoutExtension($file.Name))
                if (-not $plain) { continue }
                foreach ($needle in $needles) {
                    if ($needle -and $plain.Contains($needle)) {
                        [void]$result.Add(@{
                            name = [IO.Path]::GetFileNameWithoutExtension($file.Name)
                            path = $file.FullName
                            folder = $file.DirectoryName
                            kind = if ($extension -eq '.exe') { 'exe' } else { 'file' }
                            source = 'Диск'
                            size = $file.Length
                            modified = $file.LastWriteTime.ToString('s')
                        })
                        break
                    }
                }
            }
        } catch { }

        if ($node.depth -ge $maxDepth) { continue }

        # подкаталоги: имя папки тоже может быть попаданием («…\common\Among Us»)
        try {
            foreach ($dir in (Get-ChildItem -LiteralPath $node.path -Directory -Force -ErrorAction SilentlyContinue)) {
                if ($dir.FullName -match $SCAN_SKIP) { continue }
                if ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }

                $plain = ConvertTo-Comparable $dir.Name
                foreach ($needle in $needles) {
                    if ($needle -and $plain -and $plain.Contains($needle)) {
                        $exe = Select-MainExe $dir.FullName 0
                        [void]$result.Add(@{
                            name = $dir.Name
                            path = if ($exe) { $exe } else { $dir.FullName }
                            folder = $dir.FullName
                            kind = if ($exe) { 'exe' } else { 'folder' }
                            source = 'Диск'
                            modified = $dir.LastWriteTime.ToString('s')
                        })
                        break
                    }
                }
                $stack.Push(@{ path = $dir.FullName; depth = $node.depth + 1 })
            }
        } catch { }
    }

    $watch.Stop()
    return @{ items = $result; visited = $visited; truncated = $truncated; elapsed = [int]$watch.ElapsedMilliseconds }
}

function Get-ScanRoot {
    $configured = @(Get-ScanSetting 'roots' @())
    if ($configured.Count -gt 0) {
        return @($configured | ForEach-Object { Expand-Env $_ } | Where-Object { Test-Path -LiteralPath $_ })
    }
    # по умолчанию: профиль пользователя и корни несистемных дисков —
    # там лежит практически всё пользовательское, а C:\Windows не тратит время
    $roots = New-Object Collections.ArrayList
    [void]$roots.Add($env:USERPROFILE)
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        if (-not $drive.Root -or $drive.Root.Length -gt 3) { continue }
        if ($drive.Root -like 'C:*') {
            foreach ($sub in @('C:\Games', 'C:\Program Files', 'C:\Program Files (x86)')) {
                if (Test-Path -LiteralPath $sub) { [void]$roots.Add($sub) }
            }
            continue
        }
        [void]$roots.Add($drive.Root)
    }
    return @($roots | Select-Object -Unique)
}

function Get-MatchScore([string]$name, [string[]]$variants) {
    $plain = ConvertTo-Comparable $name
    if (-not $plain) { return 0 }
    $best = 0
    foreach ($needle in $variants) {
        if (-not $needle) { continue }
        $score = 0
        if ($plain -eq $needle) { $score = 100 }
        elseif ($plain.StartsWith($needle)) { $score = 88 - [Math]::Min(($plain.Length - $needle.Length), 22) }
        elseif ($plain.Contains($needle) -and $needle.Length -ge 3) { $score = 74 }
        elseif ($needle.Length -ge 4 -and $plain.Length -le 40) {
            $distance = Get-Levenshtein $needle $plain
            $allowed = [Math]::Max(1, [int]($needle.Length / 4))
            if ($distance -le $allowed) { $score = 66 - $distance * 6 }
        }
        if ($score -gt $best) { $best = $score }
    }
    return $best
}

function Invoke-ScanFiles($payload) {
    Test-Permission 'allowScan'

    $query = ''
    if ($payload.query) { $query = ([string]$payload.query).Trim() }
    elseif ($payload.target) { $query = ([string]$payload.target).Trim() }
    if ($query.Length -gt 120) { $query = $query.Substring(0, 120) }

    $mode = 'files'
    if ($payload.mode) { $mode = [string]$payload.mode }
    $deep = [bool]$payload.deep

    $maxResults = [int](Get-ScanSetting 'maxResults' 40)
    $budget = [int](Get-ScanSetting 'budgetMs' 9000)
    if ($deep) { $budget = [int](Get-ScanSetting 'deepBudgetMs' 25000) }
    $maxDepth = [int](Get-ScanSetting 'maxDepth' 4)
    if ($deep) { $maxDepth = [int](Get-ScanSetting 'deepMaxDepth' 6) }

    # варианты написания: как есть, в транслите и каноническое имя игры
    $variants = New-Object Collections.ArrayList
    if ($query) {
        [void]$variants.Add((ConvertTo-Comparable $query))
        $translit = ConvertTo-Comparable (ConvertTo-Translit $query)
        if ($translit) { [void]$variants.Add($translit) }
        $canonical = Resolve-GameAlias $query
        if ($canonical) { [void]$variants.Add((ConvertTo-Comparable $canonical)) }
    }
    $needles = @($variants | Where-Object { $_ -and $_.Length -ge 2 } | Select-Object -Unique)

    $watch = [Diagnostics.Stopwatch]::StartNew()

    # 1-3. дешёвые источники — всегда
    $pool = New-Object Collections.ArrayList
    foreach ($item in (Get-SteamGame))      { [void]$pool.Add($item) }
    foreach ($item in (Get-EpicGame))       { [void]$pool.Add($item) }
    foreach ($item in (Get-RegistryProgram)){ [void]$pool.Add($item) }

    $scanned = $pool.Count
    $truncated = $false

    # 4. диск — если запрошено имя и в реестрах ничего убедительного не нашлось
    $strong = @($pool | Where-Object { (Get-MatchScore $_.name $needles) -ge 70 })
    if ($needles.Count -gt 0 -and ($deep -or $strong.Count -eq 0)) {
        $disk = Search-Disk (Get-ScanRoot) $needles $budget $maxDepth $maxResults
        foreach ($item in $disk.items) { [void]$pool.Add($item) }
        $scanned += $disk.visited
        $truncated = $disk.truncated
    }

    # ранжирование и отсев дублей по пути
    $ranked = New-Object Collections.ArrayList
    $seen = @{}
    foreach ($item in $pool) {
        if (-not $item.path) { continue }
        $key = ([string]$item.path).ToLower()
        if ($seen.ContainsKey($key)) { continue }

        $score = if ($needles.Count -gt 0) { Get-MatchScore $item.name $needles } else { 50 }
        if ($mode -eq 'games') {
            # в режиме «покажи игры» источники Steam/Epic сами по себе — признак игры
            if ($item.source -eq 'Steam' -or $item.source -eq 'Epic Games') { $score += 40 }
            elseif ($needles.Count -eq 0) { continue }
        }
        if ($score -le 0) { continue }

        $seen[$key] = $true
        $item.score = $score
        $item.launchable = ($item.kind -eq 'exe')
        [void]$ranked.Add([PSCustomObject]$item)
    }

    $top = @($ranked | Sort-Object -Property score -Descending | Select-Object -First $maxResults)

    # запомним пути — только их разрешено запускать через open_path
    foreach ($item in $top) { $Script:ScanFound[([string]$item.path).ToLower()] = $true }

    $watch.Stop()
    $title = if ($query) { "Поиск: $query" } elseif ($mode -eq 'games') { 'Игры на компьютере' } else { 'Сканирование файлов' }
    $detail = "найдено {0}, просмотрено {1}, {2} мс" -f $top.Count, $scanned, [int]$watch.ElapsedMilliseconds

    return @{
        method = 'scan'
        title = $title
        detail = $detail
        query = $query
        mode = $mode
        results = @($top)
        found = $top.Count
        scanned = $scanned
        elapsed = [int]$watch.ElapsedMilliseconds
        truncated = $truncated
    }
}

function Invoke-OpenPath($payload) {
    Test-Permission 'allowScan'
    if (-not $payload.confirm) { Deny 428 'Запуск найденного файла требует подтверждения' }

    $path = ([string]$payload.target).Trim()
    if (-not $path) { Deny 400 'Не указан путь' }
    if (-not $Script:ScanFound.ContainsKey($path.ToLower())) {
        Deny 403 'Этот путь не найден агентом в текущей сессии — запуск отклонён. Сначала выполните поиск.'
    }
    if (-not (Test-Path -LiteralPath $path)) { Deny 404 'Файл больше не существует' }

    $extension = [IO.Path]::GetExtension($path).ToLower()
    if ($extension -notin @('.exe', '.lnk', '.url')) { Deny 403 "Файлы «$extension» агент не запускает" }

    Start-Process -FilePath $path -WorkingDirectory (Split-Path -Parent $path) | Out-Null
    return @{ method = 'launch'; title = ([IO.Path]::GetFileNameWithoutExtension($path)); detail = $path }
}

function Invoke-RevealPath($payload) {
    Test-Permission 'allowScan'
    $path = ([string]$payload.target).Trim()
    if (-not $path) { Deny 400 'Не указан путь' }
    if (-not $Script:ScanFound.ContainsKey($path.ToLower())) {
        Deny 403 'Этот путь не найден агентом в текущей сессии.'
    }
    if (-not (Test-Path -LiteralPath $path)) { Deny 404 'Путь больше не существует' }

    if ((Get-Item -LiteralPath $path).PSIsContainer) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList $path | Out-Null
    } else {
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('/select,"{0}"' -f $path) | Out-Null
    }
    return @{ method = 'explorer'; title = 'Показано в Проводнике'; detail = $path }
}

function Invoke-Action($payload) {
    $intent = [string]$payload.intent
    $target = if ($payload.target) { ([string]$payload.target) } else { '' }
    if ($target.Length -gt 512) { $target = $target.Substring(0, 512) }

    switch ($intent) {
        'open_app'    {
            $via = ''
            if ($payload.via) { $via = [string]$payload.via }
            return Invoke-OpenApp $target $via
        }
        'open_folder' { return Invoke-OpenFolder $target }
        'open_url'    { return Invoke-OpenUrl $target }
        'web_search'  {
            $query = $target
            if ($payload.query) { $query = [string]$payload.query }
            return Invoke-WebSearch $query
        }
        # Отдельной проверки confirm здесь нет намеренно: сама произнесённая
        # команда и есть подтверждение, см. комментарий у Invoke-CloseApp.
        'close_app'   { return Invoke-CloseApp $target }
        'volume_up'   { return Invoke-Volume $intent $payload.amount $payload.level }
        'volume_down' { return Invoke-Volume $intent $payload.amount $payload.level }
        'volume_mute' { return Invoke-Volume $intent $payload.amount $payload.level }
        'volume_unmute' { return Invoke-Volume $intent $payload.amount $payload.level }
        'volume_set'  { return Invoke-Volume $intent $payload.amount $payload.level }
        'screenshot'    { return Invoke-Screenshot }
        'record_screen' { return Invoke-RecordStart }
        'record_stop'   { return Invoke-RecordStop }
        'lock'          { return Invoke-Lock }
        'media_play'    { return Invoke-Media $intent }
        'media_next'    { return Invoke-Media $intent }
        'media_prev'    { return Invoke-Media $intent }
        'system_status' { return Get-SystemStatus }
        'focus_app'     { return Invoke-FocusApp $target }
        'window_pin'    { return Set-WindowTopmost $true }
        'window_unpin'  { return Set-WindowTopmost $false }
        'scan_files'    { return Invoke-ScanFiles $payload }
        'open_path'     { return Invoke-OpenPath $payload }
        'reveal_path'   { return Invoke-RevealPath $payload }
        default       { Deny 400 ("Неизвестное действие: {0}" -f $intent) }
    }
}

# ----------------------------------------------------- привычки хозяина ------
#
# Помощник, который знает только команды, отвечает на «мне скучно» вопросом
# «что открыть?». Помощник, который помнит привычки, открывает то, что человек и
# так собирался открыть. Разница между ними — этот раздел.
#
# Данных два источника, и они дополняют друг друга.
#
# СПУТНИК (logs/usage.json) считает, сколько времени в какой программе проведено
# и в какие часы. Это самое точное, что есть, но копится оно со временем: в
# первый день после установки файла нет вовсе.
#
# РЕЕСТР (UserAssist) знает, сколько раз что запускалось за всю жизнь системы —
# это ведёт сама Windows для меню «Пуск». Точность грубее (только счётчик
# запусков), зато данные есть сразу. Поэтому первый же вопрос «чем заняться»
# получает осмысленный ответ, а дальше ответ уточняется своей статистикой.

$Script:UsageCache = $null
$Script:UsageCacheAt = [DateTime]::MinValue
$Script:UserAssist = $null
$Script:UserAssistAt = [DateTime]::MinValue

function Get-UsageData {
    # Файл переписывает другой процесс раз в минуту — держим копию 20 секунд,
    # чтобы не читать диск на каждый чих, но и не отставать от жизни.
    if ($Script:UsageCache -and ((Get-Date) - $Script:UsageCacheAt).TotalSeconds -lt 20) {
        return $Script:UsageCache
    }
    $file = Join-Path $Root 'logs\usage.json'
    $data = @{}
    if (Test-Path -LiteralPath $file) {
        try {
            $saved = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($property in $saved.apps.PSObject.Properties) {
                $value = $property.Value
                $hours = @(0) * 24
                if ($value.hours) {
                    $list = @($value.hours)
                    for ($i = 0; $i -lt 24 -and $i -lt $list.Count; $i++) { $hours[$i] = [int]$list[$i] }
                }
                $data[$property.Name] = @{
                    name = [string]$value.name; path = [string]$value.path
                    seconds = [int]$value.seconds; sessions = [int]$value.sessions
                    last = [string]$value.last; hours = $hours
                }
            }
        } catch { $data = @{} }
    }
    $Script:UsageCache = $data
    $Script:UsageCacheAt = Get-Date
    return $data
}

function ConvertFrom-Rot13([string]$text) {
    $builder = New-Object Text.StringBuilder
    foreach ($char in $text.ToCharArray()) {
        $code = [int]$char
        if ($code -ge 65 -and $code -le 90)      { $code = 65 + (($code - 65 + 13) % 26) }
        elseif ($code -ge 97 -and $code -le 122) { $code = 97 + (($code - 97 + 13) % 26) }
        [void]$builder.Append([char]$code)
    }
    return $builder.ToString()
}

#: Папки, которые Windows записывает в UserAssist кодом, а не именем.
#:
#: Развернуть их важно не ради красоты: из полного пути достаётся и настоящее
#: название программы («Steam» вместо «steam»), и сам файл, который можно
#: запустить. Без этого «хочу отдохнуть» знало бы, что человек 300 раз запускал
#: что-то по имени steam, но не знало бы, где оно лежит.
$KNOWN_FOLDERS = @{
    '{6D809377-6AF0-444B-8957-A3773F02200E}' = ${env:ProgramW6432}
    '{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}' = ${env:ProgramFiles(x86)}
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}' = (Join-Path $env:WinDir 'System32')
    '{F38BF404-1D43-42F2-9305-67DE0B28FC23}' = $env:WinDir
    '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}' = (Join-Path $env:USERPROFILE 'Desktop')
    '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}' = (Join-Path $env:USERPROFILE 'Documents')
    '{374DE290-123F-4565-9164-39C4925E467B}' = (Join-Path $env:USERPROFILE 'Downloads')
    '{D65231B0-B2F1-4857-A4CE-A8E7C6EA7D27}' = (Join-Path $env:WinDir 'System32')
    '{9E3995AB-1F9C-4F13-B827-48B24B6C7174}' = (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned')
}

#: Служебные запускалки, которые Windows считает программами, а человек — нет.
#:
#: Первый же прогон показал их в ответе на «чем я занимаюсь»: gamelaunchhelper
#: с тремя сотнями запусков (его дёргает каждая игра из Store) и shellhost.
#: Формально всё честно, а по сути это шум, из-за которого настоящие любимые
#: программы уезжают вниз списка.
$SYSTEM_EXES = @('gamelaunchhelper', 'shellhost', 'applicationframehost', 'runtimebroker',
                 'backgroundtaskhost', 'backgroundtransferhost', 'dllhost', 'rundll32',
                 'conhost', 'openwith', 'systemsettings', 'searchapp', 'searchhost',
                 'shellexperiencehost', 'startmenuexperiencehost', 'textinputhost',
                 'lockapp', 'sihost', 'dwm', 'wscript', 'cscript', 'regedit', 'mmc',
                 'installer', 'setup', 'update', 'updater', 'unins000', 'msiexec',
                 'crashpad_handler', 'crashhandler', 'werfault', 'consent', 'explorer',
                 'powershell', 'pwsh', 'cmd', 'wt', 'windowsterminal', 'taskmgr')

function Expand-UserAssistPath([string]$decoded) {
    if ($decoded -match '^\{[0-9A-Fa-f-]+\}\\(.+)$') {
        $folder = $KNOWN_FOLDERS[$decoded.Substring(0, $decoded.IndexOf('}') + 1).ToUpper()]
        if ($folder) { return (Join-Path $folder $Matches[1]) }
        return ''
    }
    if ($decoded -match '^[A-Za-z]:\\') { return $decoded }
    return ''
}

function Get-UserAssistRuns {
    <#
        Что Windows помнит о запусках: @{ имя = @{ runs; seconds; path; name } }.

        Кроме счётчика запусков здесь есть и время, проведённое в программе, —
        Windows пишет его в ту же запись. Для только что установленного помощника
        это единственный способ ответить на «чем я обычно занят» до того, как
        накопится своя статистика.
    #>
    if ($Script:UserAssist -and ((Get-Date) - $Script:UserAssistAt).TotalMinutes -lt 30) {
        return $Script:UserAssist
    }
    $known = @{}
    try {
        $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist'
        foreach ($guid in (Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
            $counters = Get-Item -LiteralPath (Join-Path $guid.PSPath 'Count') -ErrorAction SilentlyContinue
            if (-not $counters) { continue }
            foreach ($name in $counters.GetValueNames()) {
                # Имена значений записаны ROT13 — так Windows прячет их от
                # случайного взгляда. Никакой защиты в этом нет, просто формат.
                $decoded = ConvertFrom-Rot13 $name
                if ($decoded -notmatch '\.exe$') { continue }

                $key = ([IO.Path]::GetFileNameWithoutExtension($decoded)).ToLower()
                if (-not $key -or $SYSTEM_EXES -contains $key) { continue }

                $data = $counters.GetValue($name)
                if (-not $data -or $data.Length -lt 16) { continue }

                # Разметка записи (Windows 7 и новее): счётчик запусков идёт
                # четвёртым байтом, время в фокусе — двенадцатым, в миллисекундах.
                $count = [BitConverter]::ToInt32($data, 4)
                $focusMs = [BitConverter]::ToInt32($data, 12)
                if ($count -le 0 -or $count -gt 100000) { continue }
                $seconds = 0
                if ($focusMs -gt 0 -and $focusMs -lt 2147483647) { $seconds = [int]($focusMs / 1000) }

                if (-not $known.ContainsKey($key)) {
                    $known[$key] = @{ runs = 0; seconds = 0; path = ''; name = $key }
                }
                $entry = $known[$key]
                $entry.runs += $count
                $entry.seconds += $seconds

                if (-not $entry.path) {
                    $full = Expand-UserAssistPath $decoded
                    if ($full -and (Test-Path -LiteralPath $full)) {
                        $entry.path = $full
                        try {
                            # «Notepad.exe» в качестве подписи не нужно — такое
                            # описание не лучше имени файла.
                            $description = [Diagnostics.FileVersionInfo]::GetVersionInfo($full).FileDescription
                            if ($description -and $description.Trim() -and $description -notmatch '\.exe\s*$') {
                                $entry.name = $description.Trim()
                            }
                        } catch { }
                    }
                }
            }
        }
    } catch { }
    $Script:UserAssist = $known
    $Script:UserAssistAt = Get-Date
    return $known
}

#: Чем занимаются для удовольствия…
$FUN_APPS = @('steam', 'robloxplayerbeta', 'roblox', 'epicgameslauncher', 'discord',
              'spotify', 'vlc', 'telegram', 'battle.net', 'origin', 'minecraft',
              'obs64', 'obs', 'wmplayer', 'itunes', 'osu!', 'javaw', 'wgc',
              'hd-player', 'ldplayer', 'nox', 'gta5', 'cs2', 'dota2')

#: …и чем — по делу. Списки нужны ровно для одного вопроса: «хочу отдохнуть».
#: Предложить в ответ на него Visual Studio было бы издевательством.
$WORK_APPS = @('code', 'devenv', 'word', 'excel', 'powerpnt', 'onenote', 'outlook',
               'photoshop', 'illustrator', 'blender', 'unity', 'unrealeditor',
               'rider', 'pycharm', 'idea', 'notepad++', 'sublime_text', 'teams',
               'zoom', 'winword', 'acad', 'figma', 'obsidian', 'git-bash')

$BROWSER_APPS = @('chrome', 'msedge', 'firefox', 'browser', 'opera', 'brave',
                  'vivaldi', 'yandex', 'iexplore')

#: Сайты, на которые ходят отдыхать. Нужны, когда любимая программа — браузер:
#: открыть просто браузер в ответ на «мне скучно» — это ничего не ответить.
$FUN_SITES = @('youtube.com', 'twitch.tv', 'vk.com', 'netflix.com', 'kinopoisk.ru',
               'rutube.ru', 'dzen.ru', 'tiktok.com', 'ok.ru', 'pikabu.ru',
               'reddit.com', 'music.yandex.ru', 'twitter.com', 'x.com')

function Get-AppCategory([string]$key, [string]$path) {
    if ($FUN_APPS -contains $key) { return 'fun' }
    if ($path -and $path -match '(?i)steamapps|\\Games\\|Epic Games|GOG Galaxy|Riot Games') { return 'fun' }
    if ($WORK_APPS -contains $key) { return 'work' }
    if ($BROWSER_APPS -contains $key) { return 'browser' }
    return 'other'
}

function Get-RankedApps {
    <#
        Список программ по «любимости», от самой любимой.

        Оценка складывается из четырёх слагаемых, и каждое отвечает за свой
        оттенок привычки:

            минуты        — сколько времени человек там проводит;
            заходы        — как часто возвращается (важнее длительности:
                            мессенджер открывают двадцать раз в день на минуту);
            запуски       — что говорит реестр за всё время жизни системы;
            час           — чем он занят ИМЕННО СЕЙЧАС в обычные дни.

        Последнее слагаемое и делает ответ на «хочу отдохнуть» точным: вечером
        человек играет, днём работает, и один и тот же вопрос в разное время
        честно заслуживает разных ответов.
    #>
    $usage = Get-UsageData
    $runs = Get-UserAssistRuns
    $hour = (Get-Date).Hour

    $keys = @($usage.Keys)
    foreach ($key in $runs.Keys) { if ($keys -notcontains $key) { $keys += $key } }

    $list = @()
    foreach ($key in $keys) {
        if ($SYSTEM_EXES -contains $key) { continue }

        $entry = $usage[$key]
        $seconds = 0; $sessions = 0; $path = ''; $name = $key; $hours = @(0) * 24
        if ($entry) {
            $seconds = [int]$entry.seconds; $sessions = [int]$entry.sessions
            $path = [string]$entry.path; $hours = $entry.hours
            if ($entry.name) { $name = [string]$entry.name }
        }

        $launches = 0
        if ($runs.ContainsKey($key)) {
            $historic = $runs[$key]
            $launches = [int]$historic.runs
            if (-not $path) { $path = [string]$historic.path }
            if ($name -eq $key -and $historic.name) { $name = [string]$historic.name }
            # Своё измеренное время точнее реестрового, поэтому оно в приоритете;
            # реестровое идёт в дело, только пока своего нет.
            if ($seconds -le 0) { $seconds = [int]$historic.seconds }
        }

        $nearby = $hours[$hour] + $hours[($hour + 23) % 24] + $hours[($hour + 1) % 24]
        $score = ($seconds / 60.0) + ($sessions * 3) + ($launches * 2) + ($nearby / 20.0)
        if ($score -le 0) { continue }

        $list += [pscustomobject]@{
            key = $key; name = $name; path = $path; seconds = $seconds
            sessions = $sessions; launches = $launches
            category = (Get-AppCategory $key $path)
            score = [Math]::Round($score, 1)
        }
    }
    return @($list | Sort-Object -Property score -Descending)
}

function Start-UsageApp($entry) {
    <#
        Запуск программы из статистики.

        Путь к файлу берётся из статистики, а не ищется заново по названию, и
        это важно: у процесса «robloxplayerbeta» нет ни ярлыка с таким именем,
        ни строки в «Установке программ» — поиск по названию его не нашёл бы.
        Если пути нет (данные пришли из реестра), в дело идёт обычный поиск.
    #>
    Test-Permission 'allowApps'
    if ($entry.path -and (Test-Path -LiteralPath $entry.path)) {
        $identifier = Start-Detached $entry.path @() (Split-Path -Parent $entry.path)
        return @{ app = $entry.key; title = $entry.name; method = 'usage'; detail = $entry.path; pid = $identifier }
    }
    return Invoke-OpenApp $entry.name ''
}

# ------------------------------------------------------ история браузеров -----

$Script:BrowserTop = $null
$Script:BrowserTopAt = [DateTime]::MinValue

<#
    Ищет cloudhdr_browser.py — тем же списком мест, что и launch.ps1 ищет папку
    с весами модели.

    Раньше путь был один: папка python рядом с агентом. В рабочей копии это
    верно, но обычный установщик (desktop\build.ps1) папку python в себя не
    кладёт — она весит десятки гигабайт. У установленной копии в
    %LOCALAPPDATA%\Programs соседней папки python нет, скрипт не находился, и
    любимые сайты не показывались никогда, хотя рабочая копия лежит в паре
    папок отсюда и историю читает прекрасно.
#>
function Find-BrowserScript {
    $dirs = @()
    if ($env:CLOUDHDR_PYTHON) { $dirs += $env:CLOUDHDR_PYTHON }
    if ($Config.pythonDir) { $dirs += (Expand-Env ([string]$Config.pythonDir)) }
    $dirs += Join-Path (Split-Path -Parent $Root) 'python'
    $dirs += Join-Path $env:USERPROFILE 'cloud-hdr\python'
    $dirs += Join-Path $env:USERPROFILE 'Desktop\cloud-hdr\python'
    $dirs += Join-Path $env:LOCALAPPDATA 'Programs\Cloud HDR\python'

    foreach ($dir in $dirs) {
        if (-not $dir) { continue }
        $file = Join-Path $dir 'cloudhdr_browser.py'
        if (Test-Path -LiteralPath $file) { return $file }
    }
    return $null
}

<# Интерпретатор: сначала свой, вложенный в полный пакет, затем системный. #>
function Find-PythonExe {
    $candidates = @(
        (Join-Path (Split-Path -Parent $Root) 'runtime\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    )
    foreach ($exe in $candidates) {
        if (Test-Path -LiteralPath $exe) { return $exe }
    }
    return 'python'
}

function Get-BrowserTop([int]$limit = 10) {
    <#
        Самые посещаемые сайты. Читает их python/cloudhdr_browser.py.

        Почему через Python. История браузера — база SQLite, а в PowerShell 5.1
        читать её нечем: System.Data.SQLite в Windows не входит. В Python модуль
        sqlite3 встроен, и Python в проекте уже есть — на нём работает модель.
        Ответ кешируется на двадцать минут: история за это время не меняется
        настолько, чтобы ради этого запускать процесс на каждый вопрос.
    #>
    if ($Script:BrowserTop -and ((Get-Date) - $Script:BrowserTopAt).TotalMinutes -lt 20) {
        return $Script:BrowserTop
    }
    $result = $null
    try {
        $script = Find-BrowserScript
        if ($script) {
            $python = Find-PythonExe
            $output = & $python $script top --limit $limit --days 120 2>$null
            if ($output) { $result = ($output -join '') | ConvertFrom-Json }
        }
    } catch { $result = $null }

    $Script:BrowserTop = $result
    $Script:BrowserTopAt = Get-Date
    return $result
}

# --------------------------------------------------- дополнительные действия --

function Invoke-Media([string]$intent) {
    <#
        Управление проигрыванием: те же кнопки, что на мультимедийной клавиатуре.

        Работает с чем угодно, что слушает системные медиаклавиши, — Spotify,
        YouTube во вкладке, «Кино и ТВ». Ничего настраивать не нужно: нажатие
        уходит в систему, а она сама доставляет его тому, кто сейчас играет.
    #>
    Test-Permission 'allowSystem'
    Initialize-KeyboardApi
    $codes = @{ 'media_play' = 0xB3; 'media_next' = 0xB0; 'media_prev' = 0xB1 }
    $titles = @{ 'media_play' = 'Пауза или продолжение'; 'media_next' = 'Следующий трек'
                 'media_prev' = 'Предыдущий трек' }
    if (-not $codes.ContainsKey($intent)) { Deny 400 'Неизвестная команда проигрывателя' }

    $key = [byte]$codes[$intent]
    [CloudHDR.Keys]::keybd_event($key, 0, 0, [UIntPtr]::Zero)
    [CloudHDR.Keys]::keybd_event($key, 0, [uint32]2, [UIntPtr]::Zero)
    return @{ method = 'media'; title = $titles[$intent]; detail = ('vk 0x{0:X2}' -f $codes[$intent]) }
}

function Get-SystemStatus {
    <# Короткая сводка о компьютере — то, что человек спрашивает словами «как там комп». #>
    $os = Get-CimInstance Win32_OperatingSystem
    $totalMb = [int]($os.TotalVisibleMemorySize / 1024)
    $freeMb = [int]($os.FreePhysicalMemory / 1024)
    $usedPercent = if ($totalMb -gt 0) { [int](100 * ($totalMb - $freeMb) / $totalMb) } else { 0 }

    $cpu = 0
    try { $cpu = [int](Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average } catch { }

    $uptime = (Get-Date) - $os.LastBootUpTime
    $disks = @()
    foreach ($drive in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
        $freeGb = [Math]::Round($drive.FreeSpace / 1GB, 1)
        $disks += ("{0} — свободно {1} ГБ" -f $drive.DeviceID, $freeGb)
    }

    $battery = ''
    try {
        $power = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
        if ($power) { $battery = ("Батарея — {0}%. " -f [int]$power.EstimatedChargeRemaining) }
    } catch { }

    $text = ("Процессор загружен на {0}%, память — на {1}% ({2} ГБ из {3} ГБ занято). {4}{5}. " +
             "Работает без перезагрузки {6} ч {7} мин.") -f
        $cpu, $usedPercent, [Math]::Round(($totalMb - $freeMb) / 1024, 1), [Math]::Round($totalMb / 1024, 1),
        $battery, ($disks -join '; '), [int]$uptime.TotalHours, $uptime.Minutes

    return @{ method = 'status'; title = 'Состояние компьютера'; detail = $text
              cpu = $cpu; memory = $usedPercent; uptimeHours = [int]$uptime.TotalHours }
}

function Invoke-FocusApp([string]$target) {
    <#
        Показать уже открытую программу вместо запуска второй копии.

        «Открой телеграм», когда телеграм уже запущен, раньше приводило к
        мельканию: система поднимала существующее окно сама, но не всегда, и
        иногда появлялся второй процесс. Теперь запущенное просто выносится
        вперёд — это и быстрее, и ближе к тому, чего человек хотел.
    #>
    Test-Permission 'allowApps'
    $found = Find-RunningApp $target
    if (-not $found -or -not $found.process) { Deny 404 ("«{0}» сейчас не запущено" -f $target) }
    $process = $found.process
    if ($process.MainWindowHandle -eq 0) { Deny 404 ("У «{0}» нет окна" -f $process.ProcessName) }

    # Имя для ответа берём из заголовка окна: «YouTube — Google Chrome» человеку
    # понятнее, чем имя файла chrome.exe.
    $title = $process.ProcessName
    $windowTitle = "$($process.MainWindowTitle)"
    if ($windowTitle) { $title = ($windowTitle -split '\s[-—|]\s')[-1].Trim() }

    [CloudHdr.Win]::ShowWindow($process.MainWindowHandle, 9) | Out-Null      # SW_RESTORE
    [CloudHdr.Win]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
    return @{ method = 'focus'; title = $title; detail = $windowTitle }
}

# --------------------------------------------------------- быстрая строка -----
#
# Разбор команд для панели по Ctrl+Alt+P. Это НЕ второй мозг приложения: главный
# разбор живёт в public/js/brain.js и остаётся там. Здесь ровно то, что нужно
# строке быстрого вызова, — несколько десятков оборотов, которые покрывают почти
# всё, зачем её открывают: «открой», «закрой», «найди», «громче», «мне скучно».
#
# Всё, что не опознано, уходит модели: панель спрашивает её сама, напрямую на
# порт 8080. Агент при этом не ждёт ответа — иначе на время генерации он перестал
# бы отвечать всем остальным, включая окно приложения.

function Resolve-Quick([string]$text) {
    $normalized = ($text -replace 'ё', 'е').Trim().ToLower()
    $normalized = $normalized -replace '^\s*(привет(ик)?|здравствуй(те)?|хай|ку|доброе утро|добрый (день|вечер))[\s,!.]*', ''
    $normalized = $normalized.Trim()

    if (-not $normalized) {
        return @{ kind = 'info'; say = 'Привет! Скажите, что открыть, или спросите о чём угодно.' }
    }

    # --- отдых: главный смысл всей статистики -------------------------------
    if ($normalized -match '(отдохн|расслаб|скучно|развлеч|отдых|устал|нечего делать|чем занять)') {
        $ranked = Get-RankedApps
        $fun = @($ranked | Where-Object { $_.category -eq 'fun' })
        if ($fun.Count -gt 0) {
            $pick = $fun[0]
            $result = Start-UsageApp $pick
            return @{ kind = 'action'; intent = 'open_app'; result = $result
                      say = ("Открываю {0} — в это время вы чаще всего там." -f $result.title) }
        }

        $sites = Get-BrowserTop
        if ($sites -and $sites.sites) {
            $site = @($sites.sites | Where-Object { $FUN_SITES -contains $_.domain })
            if ($site.Count -gt 0) {
                $result = Invoke-OpenUrl $site[0].url
                return @{ kind = 'action'; intent = 'open_url'; result = $result
                          say = ("Открываю {0} — вы отдыхаете там чаще всего." -f $site[0].title) }
            }
        }

        $any = @($ranked | Where-Object { $_.category -ne 'work' })
        if ($any.Count -gt 0) {
            $result = Start-UsageApp $any[0]
            return @{ kind = 'action'; intent = 'open_app'
                      say = ("Открываю {0} — чаще всего вы отдыхаете там." -f $result.title) }
        }
        return @{ kind = 'info'; say = 'Пока не знаю ваших привычек — дайте мне поработать пару дней. А что бы вы хотели открыть?' }
    }

    # --- любимые сайты ------------------------------------------------------
    if ($normalized -match '(обычно смотрю|мои сайты|часто посещ|обычно захожу|любимый сайт)') {
        $sites = Get-BrowserTop
        if ($sites -and $sites.sites -and @($sites.sites).Count -gt 0) {
            $site = @($sites.sites)[0]
            $result = Invoke-OpenUrl $site.url
            return @{ kind = 'action'; intent = 'open_url'; result = $result
                      say = ("Открываю {0} — {1} заходов по истории браузера." -f $site.title, $site.visits) }
        }
        return @{ kind = 'info'; say = 'Историю браузера прочитать не удалось.' }
    }

    # --- рассказ о привычках ------------------------------------------------
    if ($normalized -match '(статистик|чем я занима|на что уход|мои привычк|сколько я сижу)') {
        $ranked = @(Get-RankedApps | Select-Object -First 5)
        if ($ranked.Count -eq 0) { return @{ kind = 'info'; say = 'Статистика ещё пустая — я только начал наблюдать.' } }
        $lines = foreach ($item in $ranked) {
            $minutes = [int]($item.seconds / 60)
            if ($minutes -ge 60) { "• {0} — {1} ч {2} мин" -f $item.name, [int]($minutes / 60), ($minutes % 60) }
            elseif ($minutes -gt 0) { "• {0} — {1} мин" -f $item.name, $minutes }
            else { "• {0} — запусков: {1}" -f $item.name, $item.launches }
        }
        return @{ kind = 'info'; say = ("Чаще всего вы здесь:`n" + ($lines -join "`n")) }
    }

    # --- звук ---------------------------------------------------------------
    # «Включи звук» раньше доходило до правила «включи X» ниже и открывало
    # программу «Звукозапись»: слово «звук» похоже на её название.
    if ($normalized -match '^(включи|верни|вруби)\s+звук|^убери\s+без\s+звука|^(включи|сними)\s+(со\s+)?(беззвучн|немого)') {
        return @{ kind = 'action'; intent = 'volume_unmute'; payload = @{ intent = 'volume_unmute' } }
    }
    if ($normalized -match '(выключи звук|без звука|заглуши|немой режим)') {
        return @{ kind = 'action'; intent = 'volume_mute'; payload = @{ intent = 'volume_mute' } }
    }
    if ($normalized -match '^(громче|сделай громче|прибав)') {
        return @{ kind = 'action'; intent = 'volume_up'; payload = @{ intent = 'volume_up' } }
    }
    if ($normalized -match '^(тише|сделай тише|убав)') {
        return @{ kind = 'action'; intent = 'volume_down'; payload = @{ intent = 'volume_down' } }
    }
    if ($normalized -match 'звук (на|до) (\d{1,3})') {
        return @{ kind = 'action'; intent = 'volume_set'; payload = @{ intent = 'volume_set'; level = [int]$Matches[2] } }
    }

    # --- проигрыватель ------------------------------------------------------
    if ($normalized -match '(следующ(ий|ая) (трек|песн)|переключи (песню|трек)|дальше песн)') {
        return @{ kind = 'action'; intent = 'media_next'; payload = @{ intent = 'media_next' } }
    }
    if ($normalized -match '(предыдущ(ий|ая) (трек|песн)|верни песн)') {
        return @{ kind = 'action'; intent = 'media_prev'; payload = @{ intent = 'media_prev' } }
    }
    # «Включи музыку» здесь больше нет: без открытого плеера «продолжить» было
    # нечего, и ничего не происходило. Теперь фраза доходит до модели, и та
    # спрашивает, что включить.
    if ($normalized -match '^(пауза|поставь на паузу|продолжи|играй)$') {
        return @{ kind = 'action'; intent = 'media_play'; payload = @{ intent = 'media_play' } }
    }

    # --- система ------------------------------------------------------------
    if ($normalized -match '(скрин|снимок экрана)') {
        return @{ kind = 'action'; intent = 'screenshot'; payload = @{ intent = 'screenshot' } }
    }
    if ($normalized -match '(заблокируй|запри компьютер)') {
        return @{ kind = 'action'; intent = 'lock'; payload = @{ intent = 'lock' } }
    }
    if ($normalized -match '(как дела с компом|состояние компьютера|загрузка системы|сколько памяти|как там комп)') {
        $status = Get-SystemStatus
        return @{ kind = 'info'; say = $status.detail }
    }

    # --- поиск и сайты ------------------------------------------------------
    if ($normalized -match '^(найди|загугли|поищи|поиск)\s+(?:в\s+(?:интернете|гугле|яндексе|сети|браузере)\s+)?(.{2,})$') {
        return @{ kind = 'action'; intent = 'web_search'; payload = @{ intent = 'web_search'; query = $Matches[2] } }
    }
    if ($normalized -match '^(открой|зайди на|перейди на)\s+(сайт\s+)?((?:https?://)?[a-z0-9.-]+\.[a-z]{2,10}(?:[/?#]\S*)?)$') {
        return @{ kind = 'action'; intent = 'open_url'; payload = @{ intent = 'open_url'; target = $Matches[3] } }
    }

    # --- программы ----------------------------------------------------------
    if ($normalized -match '^(закрой|заверши|выключи)\s+(.{2,})$') {
        return @{ kind = 'action'; intent = 'close_app'; payload = @{ intent = 'close_app'; target = $Matches[2] } }
    }
    if ($normalized -match '^(открой|запусти|включи|давай|покажи)\s+(.{2,})$') {
        $target = $Matches[2] -replace '^(мне|нам|пожалуйста)\s+', ''
        # Уже запущенное не запускаем второй раз, а выносим вперёд.
        try { $focused = Invoke-FocusApp $target } catch { $focused = $null }
        if ($focused) {
            return @{ kind = 'action'; intent = 'focus_app'; result = $focused
                      say = ("Показываю {0} — оно уже было открыто." -f $focused.title) }
        }
        return @{ kind = 'action'; intent = 'open_app'; payload = @{ intent = 'open_app'; target = $target } }
    }

    # --- всё остальное — вопрос к модели ------------------------------------
    return @{ kind = 'model'; say = '' }
}

# --------------------------------------------------------------- руки --------
#
# Обмен со спутником — два файла рядом с журналами.
#
#   cursor-state.json — что происходит: выдано ли разрешение, идёт ли работа,
#                       какие шаги пройдены. Пишет спутник, читают все.
#   cursor-inbox.json — поручение от окна приложения. Пишет агент, читает
#                       спутник и после выполнения не удаляет: у каждого
#                       поручения свой номер, и повторно оно не берётся.
#
# Спутник обновляет состояние и когда ничего не делает — раз в несколько
# секунд. По свежести записи и видно, жив ли он: отдельной проверки процесса
# не нужно, а сама проверка через WMI стоила бы четверть секунды на запрос.

$CursorStateFile = Join-Path $Root 'logs\cursor-state.json'
$CursorInboxFile = Join-Path $Root 'logs\cursor-inbox.json'

# ------------------------------------------------------------- уши -----------
#
# С ушами обмен устроен так же, как со спутником, и по той же причине: файл
# состояния дешевле любого сетевого протокола и переживает перезапуск обеих
# сторон. Свежесть записи заменяет проверку процесса — уши пишут о себе раз в
# две секунды даже когда молчат.

$EarsStateFile = Join-Path $Root 'logs\ears-state.json'
$EarsMuteFile  = Join-Path $Root 'logs\ears-mute'
$EarsScript    = Join-Path $Root 'ears.ps1'
$EarsSenses    = @('строгий', 'обычный', 'чуткий')

function Get-EarsState {
    $state = $null
    try {
        if (Test-Path -LiteralPath $EarsStateFile) {
            $state = Get-Content -LiteralPath $EarsStateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    } catch { $state = $null }

    # Десять секунд, а не тридцать как у спутника: уши отчитываются раз в две
    # секунды, и пропуск пяти отчётов подряд означает, что процесс встал.
    # Свежесть — по времени записи файла. Раньше требовалось ещё поле «at», а
    # пока уши ждут микрофон, они пишут состояние без него: агент считал их
    # мёртвыми и отвечал «прослушивание не запущено», хотя настоящая причина —
    # выдернутый микрофон. Человек видел не ту беду и чинил не то.
    $fresh = $false
    if ($state) {
        try {
            $written = (Get-Item -LiteralPath $EarsStateFile).LastWriteTime
            $fresh = ((Get-Date) - $written).TotalSeconds -lt 10
        } catch { $fresh = $false }
    }

    if (-not $fresh -or -not $state) {
        return @{ ok = $true; available = $false; on = $false
                  reason = 'Постоянное прослушивание не запущено.' }
    }
    if ($state.PSObject.Properties['error'] -and $state.error) {
        $reason = [string]$state.error
        if ($reason -match 'не найден') {
            $reason = 'Микрофон не подключён. Вставьте его — Cloud HDR подхватит сам, перезапуск не нужен.'
        }
        return @{ ok = $true; available = $false; on = $false; waiting = [bool]$state.waiting; noMic = ($state.error -match 'не найден'); reason = $reason }
    }

    return @{
        ok = $true; available = $true; on = [bool]$state.on
        sense = [string]$state.sense
        templates = [int]$state.templates
        level = [double]$state.level; floor = [double]$state.floor
        speaking = [bool]$state.speaking
        heard = [string]$state.heard
    }
}

<#
    Включить или выключить уши.

    Выключение идёт по командной строке процесса, и здесь важна одна оговорка,
    купленная опытом: свой собственный процесс из выборки надо исключать. Агент
    сам запущен как powershell.exe, и в его командной строке слово ears.ps1
    вполне может оказаться — например, если агента запустили из той же строки.
    Без исключения по PID «выключить уши» гасило бы самого агента.
#>
function Set-Ears([string]$action, [string]$value = '') {
    # Заглушка на время, пока говорит само приложение.
    #
    # Уши глушат себя сами, когда отвечают своим голосом, но озвучивать ответы
    # умеет и страница — через /api/tts и колонки. Этого уши не видят никак:
    # для них звук из колонок неотличим от человека в комнате, и помощник
    # честно принимал бы собственный ответ за новую команду. Отметка временем в
    # файле — самый дешёвый способ сказать «сейчас это я»; уши смотрят на него
    # раз в цикл, стоит это одного обращения к файловой системе.
    if ($action -eq 'mute') {
        $until = (Get-Date).AddMilliseconds([Math]::Min(30000, [Math]::Max(200, [int]$value)))
        try { [IO.File]::WriteAllText($EarsMuteFile, $until.ToString('o')) } catch { }
        return @{ ok = $true; until = $until.ToString('s') }
    }
    if ($action -eq 'unmute') {
        try { if (Test-Path -LiteralPath $EarsMuteFile) { Remove-Item -LiteralPath $EarsMuteFile -Force } } catch { }
        return @{ ok = $true }
    }

    $mine = $PID
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -like '*ears.ps1*' -and $_.ProcessId -ne $mine })

    # Чувствительность задаётся при запуске, поэтому смена — это перезапуск.
    # Он стоит секунды две: словарь уже собран и читается из файла.
    if ($action -eq 'sense') {
        if ($EarsSenses -notcontains $value) {
            return @{ ok = $false; say = ("Чувствительность бывает: {0}" -f ($EarsSenses -join ', ')) }
        }
        foreach ($process in $running) {
            try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop } catch { }
        }
        # Путь в кавычках обязателен: установленная копия живёт в папке
        # «Cloud HDR», а Start-Process склеивает аргументы через пробел без
        # всякого экранирования — без кавычек PowerShell получил бы обрезанный
        # путь и молча ничего не запустил.
        Start-Process -FilePath 'powershell.exe' -WorkingDirectory $Root -WindowStyle Hidden `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                            '-File', ('"{0}"' -f $EarsScript), '-Sense', $value)
        Write-Log ("EARS   чувствительность: {0}" -f $value)
        return @{ ok = $true; on = $true; sense = $value; say = ("Слушаю: {0}" -f $value) }
    }

    if ($action -eq 'off') {
        foreach ($process in $running) {
            try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop } catch { }
        }
        Write-Log 'EARS   прослушивание выключено'
        return @{ ok = $true; on = $false; say = 'Слушать перестал.' }
    }

    if ($action -eq 'on' -or $action -eq 'rebuild') {
        if (-not (Test-Path -LiteralPath $EarsScript)) {
            return @{ ok = $false; say = 'Файл ears.ps1 не найден.' }
        }
        if ($running.Count -gt 0 -and $action -eq 'on') {
            return @{ ok = $true; on = $true; say = 'Уже слушаю.' }
        }
        foreach ($process in $running) {
            try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop } catch { }
        }
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                       '-File', ('"{0}"' -f $EarsScript))
        if ($action -eq 'rebuild') { $arguments += '-Rebuild' }
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
            -WorkingDirectory $Root -WindowStyle Hidden
        Write-Log ("EARS   прослушивание включено ({0})" -f $action)
        return @{ ok = $true; on = $true
                  say = if ($action -eq 'rebuild') { 'Пересобираю словарь и слушаю.' } else { 'Слушаю.' } }
    }

    return (Get-EarsState)
}

function Get-CursorState {
    $state = $null
    try {
        if (Test-Path -LiteralPath $CursorStateFile) {
            $state = Get-Content -LiteralPath $CursorStateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    } catch { $state = $null }

    $fresh = $false
    if ($state -and $state.updated) {
        try { $fresh = ((Get-Date) - [DateTime]$state.updated).TotalSeconds -lt 30 } catch { $fresh = $false }
    }

    if (-not $fresh) {
        return @{ ok = $true; available = $false; granted = $false; running = $false
                  steps = @(); say = ''
                  reason = 'Панель Cloud HDR не запущена — откройте приложение ярлыком.' }
    }

    return @{
        ok = $true; available = $true
        granted = [bool]$state.granted; running = [bool]$state.running
        seconds = [int]$state.seconds; done = [int]$state.done; total = [int]$state.total
        say = [string]$state.say; success = [bool]$state.ok
        steps = @($state.steps)
    }
}

$HandsVerbs = '^\s*(?:открой|запусти|зайди|перейди|найди|поищи|выбери|включи|поставь|сделай|скачай|отметь|заполни|выдели|скопируй|вставь|нажми|кликни|щелкни|щёлкни|напиши|введи|закрой|прокрути|полистай|добавь|создай|сохрани|продолжи|останови)(?![а-яё])'

function Send-HandsTask([string]$text) {
    $sent = Send-CursorOrder 'task' $text
    if (-not $sent.ok) { return @{ ok = $false; kind = 'error'; say = [string]$sent.error } }
    $state = Get-CursorState
    $say = if ($state.granted) { 'Беру мышь — смотрите на панель, Escape остановит.' }
           else { 'Разрешите управлять мышью на панели Cloud HDR — и начну.' }
    return @{ ok = $true; kind = 'action'; say = $say; intent = 'hands'; title = 'Cloud HDR' }
}

function Send-CursorOrder([string]$kind, [string]$text) {
    $allowed = @('task', 'grant', 'revoke', 'stop')
    if ($allowed -notcontains $kind) { Deny 400 ("Неизвестная просьба к рукам: {0}" -f $kind) }
    if ($kind -eq 'task' -and -not $text.Trim()) { Deny 400 'Пустое поручение' }
    if ($text.Length -gt 400) { $text = $text.Substring(0, 400) }

    $state = Get-CursorState
    if (-not $state.available) {
        return @{ ok = $false; available = $false; error = $state.reason }
    }

    $order = [ordered]@{
        id   = ((Get-Date).ToString('yyyyMMddHHmmssfff') + '-' + (Get-Random -Maximum 9999))
        kind = $kind
        text = $text
        at   = (Get-Date).ToString('s')
    } | ConvertTo-Json -Compress

    $directory = Split-Path -Parent $CursorInboxFile
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

    # Пишем через временный файл: спутник читает этот же файл по таймеру, и
    # половина записи ему досталась бы ровно в тот раз, когда поручение важное.
    $temporary = $CursorInboxFile + '.tmp'
    [IO.File]::WriteAllText($temporary, $order, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $CursorInboxFile -Force

    Write-Log ("HANDS  {0} -> {1}" -f $kind, $text)
    return @{ ok = $true; available = $true; sent = $kind }
}

# ------------------------------------------------------------- сессии --------

<#
    Проверка разрешения. Лимит считает только настоящие действия (-Count):
    выполнение команд, быструю строку, мышь. Раньше считалось всё подряд —
    озвучка ответа, «приглушить уши на время речи», список программ, — и один
    ответ голосом тратил три-четыре «действия» из тридцати в минуту. При
    включённом прослушивании команды из окна упирались в «Слишком много команд
    подряд», хотя человек отдал две.
#>
function Test-Session([string]$token, [switch]$Count) {
    if (-not $token -or -not $Sessions.ContainsKey($token)) {
        Deny 401 'Нет разрешения на управление системой. Обновите страницу.'
    }
    if (-not $Count) { return }
    $session = $Sessions[$token]
    $now = Get-Date
    $limit = 30
    if ($Config.rateLimit -and $Config.rateLimit.actionsPerMinute) { $limit = [int]$Config.rateLimit.actionsPerMinute }
    $session.hits = @($session.hits | Where-Object { ($now - $_).TotalSeconds -lt 60 })
    if ($session.hits.Count -ge $limit) { Deny 429 'Слишком много команд подряд. Подождите минуту.' }
    $session.hits += $now
}

# ------------------------------------------------------------- голос ---------
#
# Помощник отвечает вслух, и голос для него синтезируется здесь же, на этом
# компьютере. Никаких облачных сервисов: текст ответа никуда не отправляется,
# как и всё остальное в Cloud HDR.
#
# Взят OneCore (Windows.Media.SpeechSynthesis), а не System.Speech, и это
# главное решение во всём разделе. System.Speech работает через SAPI5, а там
# из русских голосов видна одна «Microsoft Irina Desktop» — механическая
# озвучка родом из Windows 7, которую ни при каких настройках не сделать
# приятной. OneCore отдаёт голоса нового поколения, и среди них есть мужской
# «Microsoft Pavel»: ровный, спокойный, без металлического призвука. Обе
# библиотеки одинаково локальные — разница только в том, как это звучит.
#
# Типы поднимаются ЛЕНИВО, при первом обращении к голосу. Загрузка WinRT стоит
# около секунды, и платить её при старте агента незачем: человек, который
# озвучку не включает, не должен ждать ради неё.

$Script:SpeechReady = $false
$Script:SpeechSynth = $null
$Script:SpeechVoices = @()
$Script:SpeechAsTask = $null
$Script:SpeechError = $null

function Initialize-SpeechApi {
    if ($Script:SpeechReady) { return $true }
    if ($Script:SpeechError) { return $false }      # уже пробовали и не вышло — не долбимся каждый запрос
    try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop | Out-Null
        $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows, ContentType = WindowsRuntime]
        $null = [Windows.Storage.Streams.DataReader, Windows, ContentType = WindowsRuntime]

        # Асинхронные методы WinRT возвращают IAsyncOperation, ждать который
        # Windows PowerShell сам не умеет. AsTask превращает его в обычный Task.
        $Script:SpeechAsTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]

        $Script:SpeechSynth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
        $Script:SpeechVoices = @([Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices)
        if (-not $Script:SpeechVoices.Length) { throw 'в системе нет ни одного голоса' }
        $Script:SpeechReady = $true
        return $true
    } catch {
        $Script:SpeechError = $_.Exception.Message
        return $false
    }
}

function Wait-SpeechTask($operation, $resultType) {
    $task = $Script:SpeechAsTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

<#
    Выбор голоса.

    Порядок предпочтения описывает задачу, а не вкус: помощнику нужен ровный
    мужской русский голос. Явно названный уважается всегда; если такого нет —
    мужской русский, затем любой русский, затем любой вообще. Промолчать из-за
    того, что конкретное имя не найдено, было бы худшим из исходов.
#>
function Select-SpeechVoice([string]$hint) {
    if ($hint) {
        $exact = $Script:SpeechVoices | Where-Object { $_.DisplayName -eq $hint } | Select-Object -First 1
        if ($exact) { return $exact }
        $loose = $Script:SpeechVoices | Where-Object { $_.DisplayName -like "*$hint*" } | Select-Object -First 1
        if ($loose) { return $loose }
    }
    $male = $Script:SpeechVoices | Where-Object { $_.Language -like 'ru*' -and "$($_.Gender)" -eq 'Male' } | Select-Object -First 1
    if ($male) { return $male }
    $ru = $Script:SpeechVoices | Where-Object { $_.Language -like 'ru*' } | Select-Object -First 1
    if ($ru) { return $ru }
    return ($Script:SpeechVoices | Select-Object -First 1)
}

function Protect-SpeechXml([string]$text) {
    $text.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;').Replace("'", '&apos;')
}

<#
    Синтез фразы в WAV.

    Темп и высота задаются разметкой SSML, а не свойствами объекта, потому что
    свойств для этого нет: WinRT-синтезатор умеет менять голос и громкость, но
    ни темпа, ни тона в его API не выведено. Высота считается в полутонах — это
    величина музыкальная и на слух ровная, тогда как «минус десять процентов»
    на разных голосах означает разное.
#>
function Invoke-Speech([string]$text, [string]$voiceName, [double]$rate, [double]$pitch) {
    if (-not (Initialize-SpeechApi)) {
        Deny 503 ("Синтез речи недоступен: " + $Script:SpeechError)
    }
    $clean = $text.Trim()
    if (-not $clean) { Deny 400 'Нечего произносить' }
    if ($clean.Length -gt 1200) { Deny 400 'Слишком длинная фраза для одной озвучки' }

    # Границы не косметические: за ними синтезатор либо тараторит, либо гудит,
    # и «приятный голос» превращается в свою противоположность.
    $rate = [Math]::Max(-50, [Math]::Min(50, $rate))
    $pitch = [Math]::Max(-12, [Math]::Min(12, $pitch))

    $voice = Select-SpeechVoice $voiceName
    if ($Script:SpeechSynth.Voice.DisplayName -ne $voice.DisplayName) { $Script:SpeechSynth.Voice = $voice }

    <#
        Знак обязателен, даже у нуля, и разделитель обязан быть точкой.

        И то и другое — разбор живых отказов, а не педантизм.

        Ноль: разметка ждёт ОТНОСИТЕЛЬНУЮ величину, и «0st» для тона она
        считает недопустимой — синтез падает целиком. Ровно на этом ломался
        характер «без обработки», где тон и стоит в нуле, и любое положение
        ползунка тона посередине. Отдельно забавно, что «0%» для темпа
        проходит: полагаться на такую разницу нельзя, поэтому знак ставится
        всегда и там и там.

        Точка: система здесь русская, и обычное форматирование даёт «-1,5» с
        запятой. Сейчас синтезатор это глотает, но формат машинный, и
        зависеть от языка системы он не должен — на машине с другой локалью
        это сломалось бы молча и необъяснимо.
    #>
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    $rateText = [string]::Format($invariant, '{0:+0.##;-0.##;+0}%', $rate)
    $pitchText = [string]::Format($invariant, '{0:+0.##;-0.##;+0}st', $pitch)
    $ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="' + $voice.Language + '">' +
            '<voice name="' + $voice.DisplayName + '">' +
            '<prosody rate="' + $rateText + '" pitch="' + $pitchText + '">' +
            (Protect-SpeechXml $clean) + '</prosody></voice></speak>'

    $stream = Wait-SpeechTask ($Script:SpeechSynth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    try {
        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        try {
            $null = Wait-SpeechTask ($reader.LoadAsync([uint32]$stream.Size)) ([uint32])
            $bytes = New-Object byte[] ([int]$stream.Size)
            $reader.ReadBytes($bytes)
        } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }

    return @{ bytes = $bytes; voice = $voice.DisplayName }
}

# ---------------------------------------------------------------- HTTP -------

$MIME = @{
    '.html' = 'text/html; charset=utf-8'; '.css' = 'text/css; charset=utf-8'
    '.js' = 'text/javascript; charset=utf-8'; '.json' = 'application/json; charset=utf-8'
    '.svg' = 'image/svg+xml'; '.png' = 'image/png'; '.jpg' = 'image/jpeg'
    '.ico' = 'image/x-icon'; '.woff2' = 'font/woff2'; '.map' = 'application/json'
    '.webmanifest' = 'application/manifest+json; charset=utf-8'
}

$REASON = @{ 200 = 'OK'; 204 = 'No Content'; 304 = 'Not Modified'
             400 = 'Bad Request'; 401 = 'Unauthorized'; 403 = 'Forbidden'
             404 = 'Not Found'; 413 = 'Payload Too Large'; 428 = 'Precondition Required'
             429 = 'Too Many Requests'; 500 = 'Internal Server Error' }

function New-JsonResponse($object, [int]$code = 200) {
    $text = $object | ConvertTo-Json -Depth 8 -Compress
    return @{ code = $code; type = 'application/json; charset=utf-8'; bytes = [Text.Encoding]::UTF8.GetBytes($text) }
}

function New-TextResponse([string]$text, [int]$code = 200) {
    return @{ code = $code; type = 'text/plain; charset=utf-8'; bytes = [Text.Encoding]::UTF8.GetBytes($text) }
}

function Get-StaticResponse([string]$path, $headers) {
    <#
        Отдача файлов интерфейса — с проверкой «не изменилось ли».

        Раньше каждый ответ уходил с Cache-Control: no-store, то есть браузеру
        запрещалось хранить что-либо вообще. Для API это правильно, а для
        интерфейса оказалось дорого: app.js весит 150 КБ, style.css — 108 КБ, и
        всё это читалось с диска и передавалось заново при каждом открытии окна
        и каждом обновлении страницы. На глаз это те самые полсекунды белого
        экрана перед появлением приложения.

        Теперь у файла считается метка ETag из времени правки и размера. Браузер
        присылает её обратно в If-None-Match, и если файл не менялся, ответом
        идёт пустой 304 — ни чтения с диска, ни передачи. При этом правка файла
        меняет метку, и новая версия подхватывается сразу: no-cache в заголовке
        требует спрашивать разрешение каждый раз, а не «поверить на слово» на
        сутки. Обновлять приложение по-прежнему можно просто пересохранив файл.
    #>
    $rel = [Uri]::UnescapeDataString($path).TrimStart('/')
    if (-not $rel) { $rel = 'index.html' }
    $full = [IO.Path]::GetFullPath((Join-Path $PublicDir ($rel -replace '/', '\')))
    if (-not $full.StartsWith($PublicDir)) { return (New-TextResponse '403' 403) }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { return (New-TextResponse ("404 — не найдено: " + $rel) 404) }

    $file = Get-Item -LiteralPath $full
    $tag = '"{0:x}-{1:x}"' -f $file.LastWriteTimeUtc.Ticks, $file.Length
    $cache = @{ 'ETag' = $tag; 'Cache-Control' = 'no-cache' }

    if ($headers -and $headers['if-none-match'] -eq $tag) {
        return @{ code = 304; type = 'text/plain'; bytes = @(); headers = $cache }
    }

    $extension = [IO.Path]::GetExtension($full).ToLower()
    $type = if ($MIME.ContainsKey($extension)) { $MIME[$extension] } else { 'application/octet-stream' }
    return @{ code = 200; type = $type; bytes = [IO.File]::ReadAllBytes($full); headers = $cache }
}

<#
    Одна фраза быстрой строки или голоса — без HTTP-обёртки. Вынесено из
    обработчика /api/quick, чтобы выполнять подряд несколько команд, которые
    вернула модель: агент однопоточный и сам себе запрос отправить не может.
#>
function Invoke-QuickText([string]$text) {

        # «Включи фильм про Бэтмена» — голосом и из быстрой строки так же, как
        # из чата: ищет и открывает служба умений (python\cloudhdr_hub.py).
        if ($text.Trim() -match '^(?:пожалуйста\s+)?(?:открой\s+и\s+)?(?:включи|поставь|запусти|найди|покажи|открой)\s+(?:мне\s+)?(?:фильм|кино|сериал|мультфильм|мультик|видео|ролик|клип)') {
            try {
                $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text.Trim() } | ConvertTo-Json -Compress))
                $found = Invoke-RestMethod 'http://127.0.0.1:4480/hub/video/play' -Method Post -Body $request `
                    -ContentType 'application/json; charset=utf-8' -TimeoutSec 45
                if ($found.ok -and $found.source -eq 'rutube') {
                    $say = 'Включаю «{0}» — {1}. Приятного просмотра!' -f ($found.title -replace '\s*\|.*$', ''), $found.quality
                } elseif ($found.ok) {
                    $say = 'На Rutube не нашлось — открыл поиск VK Видео.'
                } else { $say = [string]$found.error }
                Write-Log ("QUICK  video -> {0}" -f $say)
                return @{ ok = [bool]$found.ok; kind = 'action'; say = $say; intent = 'video'; title = [string]$found.title }
            } catch {
                return @{ ok = $false; kind = 'error'; say = 'Служба умений не отвечает — перезапустите Cloud HDR.' }
            }
        }

        # Медиа, буфер обмена, напоминания (группа 2). Раньше их знало только
        # окно приложения: из быстрой строки и голосом «пауза» или «напомни
        # через 10 минут» уходили модели и получали в ответ рассуждение.
        # Порядок и грубые фильтры — те же, что в public/js/features.js.
        foreach ($skill in @(
                @{ path = '/hub/remind'; hint = '(напомн|напоминани|таймер|засеки|отсчитай|разбуди|будильник|пикни)' },
                @{ path = '/hub/clip'; hint = '(буфер|скопировал)' },
                @{ path = '/hub/media'; hint = '(пауз|трек|песн|музык|композици|играет|звучит|^некст$|^next$|^skip$|^плей$|^play$|^следующ\S*$|^продолжи$|^возобнови$)' })) {
            if ($text.Trim() -notmatch $skill.hint) { continue }
            try {
                $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text.Trim() } | ConvertTo-Json -Compress))
                $answer = Invoke-RestMethod ('http://127.0.0.1:4480' + $skill.path) -Method Post -Body $request `
                    -ContentType 'application/json; charset=utf-8' -TimeoutSec 30
                if ($answer.known) {
                    $say = [string]$answer.say
                    if ($answer.text -and -not $answer.speak) { $say = "$say`n$($answer.text)" }
                    Write-Log ("QUICK  {0} -> {1}" -f $skill.path, ($say -replace "`n", ' | '))
                    return @{ ok = [bool]$answer.ok; kind = 'action'; say = $say; intent = $skill.path.Substring(5); title = 'Cloud HDR' }
                }
            } catch { }
        }

        # Система и железо: Wi-Fi, тема, питание, «сколько места», «что грузит»…
        # Служба умений сама решает, её ли это фраза (known), и если нет — идём
        # обычным путём. Сон, перезагрузку и выключение отсюда не выполняем:
        # у голоса и быстрой строки нет кнопки «Да», подтверждение — в окне.
        if ($text -match '(выключи|отключи|перезагрузи|усни|спящ|сон|вай|wi-?fi|блютуз|bluetooth|тем[аун]|ночн|яркост|питани|производительн|энергосбереж|диск|мест\S*\s+на\s|свободн|грузит|загруж|нагруз|процессор|цп|тормоз|тупит|лагает|виснет|греется|перегрев|памят|оперативк|озу|температур|заряд|батаре|аккумулятор|отмен|не\s+(выключай|перезагружай))') {
            try {
                $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text.Trim() } | ConvertTo-Json -Compress))
                $sys = Invoke-RestMethod 'http://127.0.0.1:4480/hub/sys' -Method Post -Body $request `
                    -ContentType 'application/json; charset=utf-8' -TimeoutSec 40
                if ($sys.known) {
                    $say = if ($sys.confirm) { '{0} Подтвердите в окне Cloud HDR — голосом такое не выполняю.' -f $sys.title } else { [string]$sys.say }
                    Write-Log ("QUICK  system -> {0}" -f ($say -replace "`n", ' | '))
                    return @{ ok = [bool]$sys.ok; kind = 'action'; say = $say; intent = 'system'; title = 'Система' }
                }
            } catch { }
        }

        # Программы, окна, автозагрузка (группа 3, python\cloudhdr_apps.py).
        # «Сверни все окна», «хром слева, телеграм справа», «перезапусти
        # браузер» выполняются сразу; установка, удаление и обновление — только
        # кнопкой в окне приложения, голосом их не запускаем.
        if ($text -match '(установи|инсталлируй|поставь\s+(программ|приложени)|обнови|удали\s+(программ|приложени|игру)|снеси|окн[аоы]|открыто|запущено|сверни|разверни|раскрой|весь\s+экран|переключись|переключи\s+на|перейди\s+(на|в)|выведи|подними|слева|справа|налево|направо|влево|вправо|раздели\s+экран|монитор|перезапусти|перезагрузи|завис|не\s+отвечает|автозагрузк|автозапуск|рабочий\s+стол|вкладк|^назад$|^вперед$|^вперёд$|прокрути|листай|пролистай|обнови\s+(эту\s+)?страниц|закрой\s+(это|окно)|^сверни$|весь\s+экран)') {
            try {
                $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text.Trim() } | ConvertTo-Json -Compress))
                $apps = Invoke-RestMethod 'http://127.0.0.1:4480/hub/apps' -Method Post -Body $request `
                    -ContentType 'application/json; charset=utf-8' -TimeoutSec 90
                if ($apps.known) {
                    $say = [string]$apps.say
                    if ($apps.text) { $say = "$say`n$($apps.text)" }
                    if ($apps.confirm) { $say = "$say`nПодтвердите в окне Cloud HDR — там кнопка." }
                    Write-Log ("QUICK  apps -> {0}" -f ($say -replace "`n", ' | '))
                    return @{ ok = [bool]$apps.ok; kind = 'action'; say = $say; intent = 'apps'; title = 'Программы' }
                }
            } catch { }
        }

        # Файлы (группа 4, python\cloudhdr_fileops.py): «открой папку проекты»,
        # «перемести отчёт в документы», «что я недавно скачал». Удаление голосом
        # не выполняется — только показывается, что будет удалено, а кнопка
        # «В корзину» живёт в окне.
        if ($text -match '(удали|сотри|выкинь|корзин|переименуй|перемести|перенеси|переложи|скопируй|создай\s+(новую\s+)?папку|сделай\s+папку|скача|занима\S*\s+(\S+\s+){0,3}мест|самые\s+больш|весит|дубл|сожми|заархивируй|упакуй|распакуй|открой\s+(мне\s+)?(файл|документ|фото|картинк|таблиц|презентаци|папку)|где\s+(лежит|находится|хранится))') {
            try {
                $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text.Trim() } | ConvertTo-Json -Compress))
                $fs = Invoke-RestMethod 'http://127.0.0.1:4480/hub/fs' -Method Post -Body $request `
                    -ContentType 'application/json; charset=utf-8' -TimeoutSec 60
                if ($fs.known) {
                    $say = [string]$fs.say
                    if ($fs.text) { $say = "$say`n$($fs.text)" }
                    if ($fs.files) { $say = "$say`n" + ((@($fs.files) | Select-Object -First 5 | ForEach-Object { "$($_.name) — $($_.where)" }) -join "`n") }
                    if ($fs.confirm -or $fs.offer -eq 'bin') { $say = "$say`nПодтвердите в окне Cloud HDR — там кнопка." }
                    Write-Log ("QUICK  files -> {0}" -f ($say -replace "`n", ' | '))
                    return @{ ok = [bool]$fs.ok; kind = 'action'; say = $say; intent = 'files'; title = 'Файлы' }
                }
            } catch { }
        }

        $decision = Resolve-Quick $text.Trim()

        if ($decision.kind -eq 'action' -and $decision.payload) {
            try {
                $result = Invoke-Action $decision.payload
            } catch {
                $message = $_.Exception.Message
                if ($message -match '^\d{3}\|(.+)$') { $message = $Matches[1] }
                Write-Log ("QUICK  {0} -> {1}" -f $decision.intent, $message)
                return @{ ok = $false; kind = 'error'; say = $message }
            }
            $decision.result = $result
            if (-not $decision.say) { $decision.say = [string]$result.title }
        }

        if ($decision.result) {
            Write-Log ("QUICK  {0} -> {1}" -f $decision.intent, $decision.result.title)
        }
        return @{ ok = $true; kind = $decision.kind; say = [string]$decision.say
                                   intent = [string]$decision.intent
                                   title = [string]$decision.result.title }
}

<#
    Просьба → команды приложения: сервер модели (cloudhdr_qwen/router.py).
    history — предыдущие реплики уточнения, чтобы «рок» после вопроса «какую
    музыку?» понялся как ответ, а не как новая просьба.
#>
function Invoke-Router([string]$text, $history) {
    try {
        $request = [Text.Encoding]::UTF8.GetBytes((@{ text = $text; history = @($history) } | ConvertTo-Json -Depth 5 -Compress))
        $response = Invoke-WebRequest 'http://127.0.0.1:8080/v1/route' -Method Post -Body $request `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 60 -UseBasicParsing
        return ([Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json)
    } catch { return $null }
}

function Invoke-Route([string]$method, [string]$path, $headers, [string]$body) {
    $token = $headers['x-cloud-token']

    if ($method -eq 'OPTIONS') { return @{ code = 204; type = 'text/plain'; bytes = @() } }

    if ($path -eq '/api/health' -and $method -eq 'GET') {
        return New-JsonResponse @{
            ok = $true; name = 'Cloud HDR Agent (PowerShell)'; version = $Version
            platform = 'win32'; host = $env:COMPUTERNAME; user = $env:USERNAME
            requirePin = [bool]$Pin; permissions = $Config.permissions
            uptime = [int]((Get-Date) - $StartTime).TotalSeconds
        }
    }

    # Поднять упавшие службы (модель, разбор команд, Hub, телефон, голос).
    # Окно зовёт сюда, когда модель перестала отвечать: раньше она лежала до
    # перезапуска всего приложения. launch.ps1 -AgentOnly сам проверяет каждую
    # службу и запускает только те, что молчат, — повторный вызов безвреден.
    # Не чаще раза в 90 секунд: пока веса грузятся, модель молчит, и без
    # паузы сторож окна успел бы запустить её вторую копию.
    if ($path -eq '/api/services/start' -and $method -eq 'POST') {
        $now = Get-Date
        if ($Script:ServicesKick -and ($now - $Script:ServicesKick).TotalSeconds -lt 90) {
            return New-JsonResponse @{ ok = $true; started = $false; wait = $true }
        }
        $Script:ServicesKick = $now
        $launcher = Join-Path $Root 'launch.ps1'
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -WorkingDirectory $Root -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $launcher), '-AgentOnly')
        Write-Log 'SERVICES перезапуск служб по просьбе окна'
        return New-JsonResponse @{ ok = $true; started = $true }
    }

    if ($path -eq '/api/session/grant' -and $method -eq 'POST') {
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        if ($Pin -and ([string]$payload.pin).Trim() -ne $Pin) { Deny 401 'Неверный код подтверждения' }
        $token = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
        $Sessions[$token] = @{ created = Get-Date; hits = @() }
        Write-Log ("GRANT  сессия выдана ({0}...)" -f $token.Substring(0, 8))
        return New-JsonResponse @{ ok = $true; token = $token; permissions = $Config.permissions }
    }

    if ($path -eq '/api/session/revoke' -and $method -eq 'POST') {
        if ($token) { $Sessions.Remove($token) }
        Write-Log 'REVOKE разрешение отозвано'
        return New-JsonResponse @{ ok = $true }
    }

    if ($path -eq '/api/apps' -and $method -eq 'GET') {
        Test-Session $token
        # ВАЖНО: имена переменных в PowerShell регистронезависимы — локальная $apps
        # затенила бы реестр $APPS, поэтому списки называются иначе.
        $appList = @()
        foreach ($key in $APPS.Keys) {
            $app = $APPS[$key]
            $exe = Resolve-AppExe $app
            $appList += @{ key = $key; title = $app.title; icon = $app.icon; aliases = @($app.aliases)
                           installed = [bool]$exe; found = [bool]($exe -or $app.uri -or $app.shell) }
        }
        $folderList = @()
        foreach ($key in $FOLDERS.Keys) {
            $folder = $FOLDERS[$key]
            $full = Expand-Env $folder.path
            $folderList += @{ key = $key; title = $folder.title; aliases = @($folder.aliases)
                              path = $full; exists = (Test-Path -LiteralPath $full) }
        }
        # Имена всего установленного и игр библиотеки отдаются браузеру целиком.
        #
        # Это словарь ИМЕННО ЭТОГО компьютера, и нужен он постоянному
        # прослушиванию. Без него «открой брав старс» — просто два слова после
        # глагола, и отличить их от обрывка чужого разговора нечем: приходится
        # либо выполнять всё подряд, либо не выполнять ничего. Со списком
        # вопрос решается фактом: такая программа на машине есть.
        #
        # Цена нулевая: оба индекса всё равно строятся для запуска программ и
        # живут в кэше десять минут, здесь берутся только имена.
        $indexed = 0
        $names = @()
        $games = @()
        if ($Config.permissions.allowAnyApp) {
            $index = Get-AppIndex
            $indexed = $index.Count
            $names = @($index | ForEach-Object { $_.name })
            $games = @(Get-SteamAppIndex | Where-Object { $_.installed } | ForEach-Object { $_.name })
        }
        return New-JsonResponse @{ ok = $true; apps = $appList; folders = $folderList
                                   indexed = $indexed; names = $names; games = $games }
    }

    # Голос. Сессионный токен здесь НЕ спрашивается, и это отличается от всех
    # остальных маршрутов сознательно: синтез ничего в системе не делает — он
    # превращает текст в звук и возвращает его обратно в браузер. Требовать
    # разрешение на управление Windows ради этого значило бы, что в демо-режиме
    # помощник немеет, хотя причин молчать у него нет.
    if ($path -eq '/api/tts/voices' -and $method -eq 'GET') {
        $ok = Initialize-SpeechApi
        $list = @()
        if ($ok) {
            $list = @($Script:SpeechVoices | ForEach-Object {
                @{ name = $_.DisplayName; language = $_.Language; gender = "$($_.Gender)" }
            })
        }
        return New-JsonResponse @{ ok = $true; available = $ok; voices = $list; error = $Script:SpeechError }
    }

    if ($path -eq '/api/tts' -and $method -eq 'POST') {
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $text = if ($payload.text) { [string]$payload.text } else { '' }
        $voiceName = if ($payload.voice) { [string]$payload.voice } else { '' }
        $rate = if ($null -ne $payload.rate) { [double]$payload.rate } else { 0 }
        $pitch = if ($null -ne $payload.pitch) { [double]$payload.pitch } else { 0 }
        $spoken = Invoke-Speech $text $voiceName $rate $pitch
        return @{ code = 200; type = 'audio/wav'; bytes = $spoken.bytes }
    }

    if ($path -eq '/api/exec' -and $method -eq 'POST') {
        Test-Session $token -Count
        $payload = $body | ConvertFrom-Json
        try {
            $result = Invoke-Action $payload
        } catch {
            Write-Log ("FAIL   {0} -> {1}" -f $payload.intent, $_.Exception.Message)
            throw
        }
        Write-Log ("EXEC   {0} -> {1} ({2}: {3})" -f $payload.intent, $result.title, $result.method, $result.detail)
        $response = @{ ok = $true; intent = [string]$payload.intent }
        foreach ($pair in $result.GetEnumerator()) { $response[$pair.Key] = $pair.Value }
        return New-JsonResponse $response
    }

    # ---------------------------------------------------------- быстрая строка --
    #
    # Панель по Ctrl+Alt+P присылает сюда одну строку и получает либо выполненное
    # действие, либо kind='model' — знак «команды не вижу, спроси модель сам».
    # Ходить к модели отсюда нельзя: агент однопоточный, и на те секунды, что
    # идёт генерация, он перестал бы отвечать окну приложения.
    if ($path -eq '/api/quick' -and $method -eq 'POST') {
        Test-Session $token -Count
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $text = if ($payload.text) { [string]$payload.text } else { '' }
        if (-not $text.Trim()) { return New-JsonResponse @{ ok = $true; kind = 'info'; say = 'Скажите, что открыть.' } }

        # «Курсором открой ютуб и включи первое видео» — прямо рукам: модель
        # ведёт мышь по экрану (server/cursor-mind.ps1, Invoke-MindAgent).
        if ($text -match '^\s*(?:курсор(?:ом)?|мышк(?:ой|а)|руками)[\s,]+(.{2,})$') {
            return New-JsonResponse (Send-HandsTask $text)
        }

        $answer = Invoke-QuickText $text

        # «Ты ждёшь, сверни хром», «слушай, открой телеграм»: шаблоны якорятся
        # на начало фразы, и лишние слова впереди отправляли её модели, а та
        # делала «сверни это» — не то окно. Сначала пробуем хвост от глагола.
        $failed = $answer.kind -eq 'model' -or ($answer.kind -eq 'error' -and [string]$answer.say -match '^Не нашёл')
        if ($failed) {
            $tail = [regex]::Match($text.Trim(), '(?i)(?:^|[\s,.!?:—-])((?:открой|закрой|сверни|разверни|включи|выключи|найди|поставь|сделай|переключись|перейди|запусти|напомни|громче|тише|пауза|перезапусти)(?![а-яё]).*)$')
            if ($tail.Success -and $tail.Index -gt 0) {
                $retry = Invoke-QuickText $tail.Groups[1].Value.Trim(' ', '.', '!', '?')
                $retryFailed = $retry.kind -eq 'model' -or ($retry.kind -eq 'error' -and [string]$retry.say -match '^Не нашёл')
                if (-not $retryFailed) { return New-JsonResponse $retry }
            }
        }

        # Не узнали — спрашиваем модель: она переписывает живую просьбу в
        # команды из каталога («открой браузер и глянь погоду» → две команды)
        # или задаёт уточняющий вопрос («включи музыку» — какую?). Выполняет
        # команды тот же Invoke-QuickText, что и набранные руками.
        # «Включи что-нибудь послушать» разбор принимает за «открой программу
        # что-нибудь послушать» и не находит её — это тоже повод спросить модель.
        $unknownApp = $answer.kind -eq 'error' -and [string]$answer.say -match '^Не нашёл'
        if ($answer.kind -eq 'model' -or $unknownApp) {
            $routed = Invoke-Router $text $payload.history
            if ($routed -and $routed.ask) {
                Write-Log ("QUICK  уточняю -> {0}" -f $routed.ask)
                return New-JsonResponse @{ ok = $true; kind = 'ask'; say = [string]$routed.ask; intent = 'ask'; title = 'Cloud HDR' }
            }
            if ($routed -and @($routed.commands).Count -gt 0) {
                $says = @(); $allOk = $true
                foreach ($command in @($routed.commands)) {
                    $step = Invoke-QuickText ([string]$command)
                    if ($step.kind -eq 'model') { continue }
                    if ($step.say) { $says += [string]$step.say }
                    if (-not $step.ok) { $allOk = $false }
                }
                # Часть команд не вышла («открой сообщения» — это кнопка на сайте,
                # а не программа), а мышь рукам доверена — доделывают руки.
                if (-not $allOk -and $text -match $HandsVerbs) {
                    $hands = Get-CursorState
                    if ($hands.available -and $hands.granted) { return New-JsonResponse (Send-HandsTask $text) }
                }
                $say = if ($allOk -and $routed.say) { [string]$routed.say } else { ($says -join ' ') }
                Write-Log ("QUICK  по модели: {0} -> {1}" -f (@($routed.commands) -join ' | '), $say)
                return New-JsonResponse @{ ok = $allOk; kind = 'action'; say = $say; intent = 'routed'; title = 'Cloud HDR'
                                           commands = @($routed.commands) }
            }
        }
        # Никто не понял, а мышь рукам уже доверена — значит, это поручение
        # для них: «зайди в вк и открой сообщения», «нажми подписаться».
        if (($answer.kind -eq 'model' -or $unknownApp) -and $text -match $HandsVerbs) {
            $hands = Get-CursorState
            if ($hands.available -and $hands.granted) { return New-JsonResponse (Send-HandsTask $text) }
        }
        if ($unknownApp -and $answer.kind -eq 'error') { return New-JsonResponse $answer }
        return New-JsonResponse $answer
    }

    # ------------------------------------------------------------ руки --------
    #
    # Мышью управляет не агент, а спутник — тот самый процесс, что рисует панель
    # по Ctrl+Alt+P. Агент здесь только почтальон: складывает поручение в файл и
    # отдаёт странице то, что спутник о себе написал.
    #
    # Почему так, а не своей службой на своём порту. Работа руками идёт минуту и
    # дольше, а агент однопоточный: он бы замолчал на всё это время. Заводить
    # ради этого третий процесс с третьим портом тоже незачем — спутник уже
    # запущен, у него есть окно, кнопка «стоп» и живой показ шагов. Два файла в
    # logs дешевле любого сетевого протокола и переживают перезапуск обеих
    # сторон.
    if ($path -eq '/api/cursor' -and $method -eq 'GET') {
        return New-JsonResponse (Get-CursorState)
    }

    if ($path -eq '/api/cursor' -and $method -eq 'POST') {
        Test-Session $token -Count
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $kind = if ($payload -and $payload.action) { [string]$payload.action } else { 'task' }
        $text = if ($payload -and $payload.text) { [string]$payload.text } else { '' }
        return New-JsonResponse (Send-CursorOrder $kind $text)
    }

    # ------------------------------------------------------------- уши -------
    #
    # Слушает не агент, а отдельный процесс ears.ps1 — агент однопоточный и
    # глох бы на всё время выполнения предыдущей команды. Здесь только окошко
    # наружу: показать, что уши о себе написали, и включить их или выключить.
    #
    # Узнанные команды сюда НЕ приходят маршрутом «услышал — доложил». Уши бьют
    # прямо в /api/quick, тем же путём, что и быстрая строка. Лишний
    # посредник добавил бы задержку ровно там, где вся затея была ради её
    # отсутствия.
    if ($path -eq '/api/ears' -and $method -eq 'GET') {
        return New-JsonResponse (Get-EarsState)
    }

    if ($path -eq '/api/ears' -and $method -eq 'POST') {
        Test-Session $token
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $action = if ($payload -and $payload.action) { [string]$payload.action } else { 'state' }
        $value = if ($payload -and $payload.value) { [string]$payload.value } else { '' }
        return New-JsonResponse (Set-Ears $action $value)
    }

    # ------------------------------------------------------------ зрение ------
    #
    # Два маршрута и оба POST, хотя по смыслу это чтение. Причина техническая:
    # разбор запроса отрезает строку запроса вместе с параметрами (см.
    # Read-HttpRequest), и путь к файлу передать в адресе просто нечем.
    if ($path -eq '/api/vision/recent' -and $method -eq 'POST') {
        Test-Session $token
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $limit = if ($payload -and $payload.limit) { [int]$payload.limit } else { 12 }
        $files = Get-RecentImages ([Math]::Max(1, [Math]::Min(40, $limit)))
        return New-JsonResponse @{ ok = $true; files = @($files) }
    }

    if ($path -eq '/api/vision/file' -and $method -eq 'POST') {
        Test-Session $token
        $payload = if ($body) { $body | ConvertFrom-Json } else { $null }
        $file = Get-SafeImage ([string]$payload.path)
        $extension = $file.Extension.ToLower()
        $type = if ($VISION_MIME.ContainsKey($extension)) { $VISION_MIME[$extension] } else { 'application/octet-stream' }
        Write-Log ("VISION отдал {0} ({1} КБ)" -f $file.Name, [int]($file.Length / 1KB))
        return @{ code = 200; type = $type; bytes = [IO.File]::ReadAllBytes($file.FullName) }
    }

    if ($path -eq '/api/usage' -and $method -eq 'GET') {
        $ranked = @(Get-RankedApps | Select-Object -First 12)
        $top = Get-BrowserTop 8

        # Списки собираются в переменные ДО хеш-таблицы, и это не украшательство.
        #
        # В PowerShell 5.1 оператор if в значении ключа отдаёт не сам массив, а
        # обёртку конвейера, и ConvertTo-Json печатает пустую ветку как {}, а не
        # как []. Страница получала "sites":{} — объект вместо списка — и падала
        # на (data.sites || []).slice: пустой объект истинный, метода slice у
        # него нет. Присваивание через переменную такого превращения не делает.
        #
        # Пустой список здесь — норма, а не сбой: истории браузера нет, если
        # рядом с установленной копией не нашлось python-скрипта.
        $topSites = @()
        $topBrowsers = @()
        if ($top) {
            $topSites = @($top.sites | Where-Object { $_ })
            $topBrowsers = @($top.browsers | Where-Object { $_ })
        }

        return New-JsonResponse @{
            ok = $true
            apps = @($ranked | ForEach-Object {
                @{ key = $_.key; name = $_.name; minutes = [int]($_.seconds / 60)
                   sessions = $_.sessions; launches = $_.launches
                   category = $_.category; score = $_.score }
            })
            sites = $topSites
            browsers = $topBrowsers
        }
    }

    if ($path -eq '/api/browser/top' -and $method -eq 'GET') {
        $top = Get-BrowserTop 12
        if (-not $top) { return New-JsonResponse @{ ok = $false; error = 'История браузеров недоступна' } }
        # @($null) — это список из одного $null, а не пустой список: без отсева
        # страница получила бы строку без домена и заголовка.
        $topSites = @($top.sites | Where-Object { $_ })
        $topBrowsers = @($top.browsers | Where-Object { $_ })
        return New-JsonResponse @{ ok = $true; sites = $topSites; browsers = $topBrowsers }
    }

    if ($path.StartsWith('/api/')) { return (New-JsonResponse @{ ok = $false; error = 'Метод не найден' } 404) }

    return Get-StaticResponse $path $headers
}

# ------------------------------------------------------------ сервер ---------

function Read-HttpRequest($stream) {
    $buffer = New-Object byte[] 8192
    $memory = New-Object IO.MemoryStream
    $headerEnd = -1

    while ($headerEnd -lt 0) {
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) { return $null }
        $memory.Write($buffer, 0, $read)
        $bytes = $memory.ToArray()
        for ($i = 3; $i -lt $bytes.Length; $i++) {
            if ($bytes[$i - 3] -eq 13 -and $bytes[$i - 2] -eq 10 -and $bytes[$i - 1] -eq 13 -and $bytes[$i] -eq 10) {
                $headerEnd = $i - 3; break
            }
        }
        if ($memory.Length -gt 262144) { return $null }
    }

    $bytes = $memory.ToArray()
    $headText = [Text.Encoding]::ASCII.GetString($bytes, 0, $headerEnd)
    $lines = $headText -split "`r`n"
    $request = $lines[0] -split ' '
    if ($request.Count -lt 2) { return $null }

    $headers = @{}
    if ($lines.Count -gt 1) {
        foreach ($line in $lines[1..($lines.Count - 1)]) {
            if (-not $line) { continue }
            $index = $line.IndexOf(':')
            if ($index -gt 0) { $headers[$line.Substring(0, $index).Trim().ToLower()] = $line.Substring($index + 1).Trim() }
        }
    }

    $bodyStart = $headerEnd + 4
    $length = 0
    if ($headers.ContainsKey('content-length')) { $length = [int]$headers['content-length'] }
    $bodyBytes = New-Object byte[] $length
    $have = [Math]::Min($length, $bytes.Length - $bodyStart)
    if ($have -gt 0) { [Array]::Copy($bytes, $bodyStart, $bodyBytes, 0, $have) }
    while ($have -lt $length) {
        $read = $stream.Read($bodyBytes, $have, $length - $have)
        if ($read -le 0) { break }
        $have += $read
    }

    $bodyText = ''
    if ($have -gt 0) { $bodyText = [Text.Encoding]::UTF8.GetString($bodyBytes, 0, $have) }

    return @{
        method = $request[0]
        path = ($request[1] -split '\?')[0]
        headers = $headers
        body = $bodyText
    }
}

function Send-HttpResponse($stream, $response) {
    $code = [int]$response.code
    $reasonText = 'OK'
    if ($REASON.ContainsKey($code)) { $reasonText = $REASON[$code] }
    $bytes = [byte[]]$response.bytes
    if ($null -eq $bytes) { $bytes = @() }

    # Свои заголовки есть только у статики (метка версии файла). У всего
    # остального остаётся no-store: ответы API устаревают мгновенно.
    $extra = ''
    $cacheControl = "Cache-Control: no-store`r`n"
    if ($response.headers) {
        foreach ($pair in $response.headers.GetEnumerator()) {
            if ($pair.Key -eq 'Cache-Control') { $cacheControl = "Cache-Control: $($pair.Value)`r`n" }
            else { $extra += "$($pair.Key): $($pair.Value)`r`n" }
        }
    }

    # Разрешение на чтение ответа — только странице самого приложения (см.
    # Test-Caller). Раньше здесь стояла звёздочка, и любой сайт, открытый в
    # браузере, получал ответы агента — вместе с токеном управления.
    $cors = ''
    if ($Script:CallerOrigin) {
        $cors = "Access-Control-Allow-Origin: $($Script:CallerOrigin)`r`nVary: Origin`r`n" +
                "Access-Control-Allow-Headers: Content-Type, X-Cloud-Token`r`n" +
                "Access-Control-Allow-Methods: GET, POST, OPTIONS`r`n"
    }
    $head = "HTTP/1.1 $code $reasonText`r`n" +
            "Content-Type: $($response.type)`r`n" +
            "Content-Length: $($bytes.Length)`r`n" +
            $cacheControl + $extra + $cors +
            "Connection: close`r`n`r`n"

    $headBytes = [Text.Encoding]::ASCII.GetBytes($head)
    $stream.Write($headBytes, 0, $headBytes.Length)
    if ($bytes.Length -gt 0) { $stream.Write($bytes, 0, $bytes.Length) }
    $stream.Flush()
}

$port = if ($Config.port) { [int]$Config.port } else { 4477 }
$address = "http://127.0.0.1:$port"

<#
    Кто зовёт агента. Пускаем два вида вызовов:
      • страницу самого приложения — Origin http://127.0.0.1:4477 (или localhost);
      • программы без браузера — PowerShell, быстрая строка, спутник: у них
        заголовка Origin нет вовсе.
    Всё остальное — чужой сайт, открытый в браузере. Раньше агент отвечал и ему:
    страница с example.com одним запросом получала токен и дальше могла
    запускать на компьютере что угодно. Проверка Host закрывает подмену DNS
    (evil.com, который вдруг смотрит на 127.0.0.1): у такой страницы Origin
    на GET-запросах нет, а вот Host выдаёт чужое имя.
#>
$AllowedHosts = @("127.0.0.1:$port", "localhost:$port")
$AllowedOrigins = @("http://127.0.0.1:$port", "http://localhost:$port")

function Test-Caller($headers) {
    $Script:CallerOrigin = $null
    $hostHeader = [string]$headers['host']
    if ($hostHeader -and $AllowedHosts -notcontains $hostHeader.ToLower()) { return $false }
    $origin = [string]$headers['origin']
    if (-not $origin) { return $true }
    if ($AllowedOrigins -contains $origin.ToLower()) { $Script:CallerOrigin = $origin; return $true }
    return $false
}

try {
    $bindAddress = [Net.IPAddress]::Parse('127.0.0.1')
    $listener = New-Object Net.Sockets.TcpListener -ArgumentList $bindAddress, $port
    $listener.Start()
} catch {
    Write-Host ""
    Write-Host ("   [x] Порт {0} занят. Закройте другой агент или смените port в config.json" -f $port) -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "   [CLOUD HDR AI v1.0] Запуск модуля..." -ForegroundColor Cyan
Write-Host "   Cloud HDR Agent (PowerShell) v$Version" -ForegroundColor Cyan
Write-Host "   ---------------------------------------------"
Write-Host "   Интерфейс :  $address"
Write-Host ("   Права     :  " + (($Config.permissions.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { $_.Name }) -join ', '))
Write-Host ("   Папки     :  " + ((@($Config.allowedFolderRoots) | ForEach-Object { Expand-Env $_ }) -join '  |  '))
if ($Pin) { Write-Host "   КОД ДОСТУПА: $Pin   <- введите его в окне разрешений" -ForegroundColor Yellow }
Write-Host "   Ctrl+C — остановить агента" -ForegroundColor DarkGray
Write-Host ""

if ($Config.openBrowserOnStart) { Start-Process $address | Out-Null }

while ($true) {
    $client = $null
    try {
        $client = $listener.AcceptTcpClient()
        # сервер однопоточный: короткий таймаут не даёт «спекулятивным» сокетам
        # браузера (preconnect) занимать очередь
        $client.ReceiveTimeout = 1500
        $client.SendTimeout = 8000
        $stream = $client.GetStream()

        $request = Read-HttpRequest $stream
        if ($request -and -not (Test-Caller $request.headers)) {
            Write-Log ("DENY   чужой источник: {0} {1} ({2})" -f $request.method, $request.path, $request.headers['origin'])
            Send-HttpResponse $stream (New-JsonResponse @{ ok = $false; error = 'Запрос не со страницы Cloud HDR' } 403)
        } elseif ($request) {
            try {
                $response = Invoke-Route $request.method $request.path $request.headers $request.body
            } catch {
                $message = $_.Exception.Message
                $code = 500
                if ($message -match '^(\d{3})\|(.+)$') { $code = [int]$Matches[1]; $message = $Matches[2] }
                $response = New-JsonResponse @{ ok = $false; error = $message } $code
            }
            Send-HttpResponse $stream $response
        }
    } catch {
        # обрыв соединения браузером — не повод останавливать агента
    } finally {
        if ($client) { $client.Close() }
    }
}
