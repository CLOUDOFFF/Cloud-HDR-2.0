<#
    Cloud HDR — единая точка входа десктопного приложения.

    Что делает:
      1. поднимает модель разговора и разбор команд, если они обучены;
      2. поднимает спутника (быстрая строка Ctrl+Alt+P);
      3. открывает окно приложения — desktop\CloudHDR.exe;
      4. если приложение не собрано, откатывается на окно браузера (режим --app)
         и сам поднимает агента.

    Окно — отдельная программа, а не вкладка: у неё своя иконка, своя строка в
    панели задач и свой процесс. Она сама поднимает агента, если тот молчит, и
    сама выводит вперёд уже открытое окно, поэтому здесь этим заниматься не
    нужно. Ветка с браузером осталась запасной: на движке Chrome работает
    распознавание речи, которого во встроенном движке может не быть.

    Используется ярлыками на рабочем столе, в меню «Пуск» и в автозагрузке.
#>

param(
    [switch]$Voice,      # открыть сразу с включённым микрофоном
    [switch]$AgentOnly,  # только поднять агента и службы, окно не открывать
    [switch]$Browser     # открыть на движке Chrome, минуя CloudHDR.exe
)

$ErrorActionPreference = 'SilentlyContinue'

$Root   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Agent  = Join-Path $Root 'agent.ps1'
$Companion = Join-Path $Root 'companion.ps1'
$Ears   = Join-Path $Root 'ears.ps1'
$App    = Join-Path (Split-Path -Parent $Root) 'desktop\CloudHDR.exe'
$Config = Get-Content (Join-Path $Root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Port   = if ($Config.port) { [int]$Config.port } else { 4477 }
$Address = "http://127.0.0.1:$Port"
if ($Voice) { $Address += '/?voice=1' }

Add-Type -Namespace CloudHdrLaunch -Name Win -MemberDefinition @'
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@

<#
    Запуск скрипта в отдельном процессе.

    Обёртка нужна ровно из-за одной вещи — КАВЫЧЕК, и стоила она установленному
    приложению всех фоновых служб сразу.

    Start-Process собирает -ArgumentList в одну строку через пробел и ничего не
    экранирует. Пока проект лежит в C:\Users\11\cloud-hdr, это незаметно: пробелов
    в пути нет. Но установленная копия живёт в «...\Programs\Cloud HDR\server», и
    аргумент разваливался по пробелу — PowerShell получал -File "…\Programs\Cloud"
    и отвечал, что у файла расширение не PS1. Сообщение уходило в никуда: процесс
    запускался скрытым, launch.ps1 работает с погашенными ошибками и выходил с
    нулём. Снаружи это выглядело так, что ярлык открывает окно приложения — и
    больше не делает ничего: ни агента, ни быстрой строки по Ctrl+Alt+P.

    Кавычки вокруг пути закрывают это целиком и не мешают путям без пробелов.
#>
function Start-Script([string]$path, [string[]]$extra = @()) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                   '-File', ('"{0}"' -f $path))
    foreach ($item in $extra) { $arguments += $item }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
        -WorkingDirectory (Split-Path -Parent $path) -WindowStyle Hidden
}

function Get-AppWindow {
    Get-Process |
        Where-Object { $_.ProcessName -in @('chrome', 'msedge', 'brave', 'vivaldi') -and
                       $_.MainWindowTitle -like 'Cloud HDR*' -and $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
}

function Test-Agent {
    try { $null = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3; return $true }
    catch { return $false }
}

function Test-Model {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:8080/health' -TimeoutSec 2; return $true }
    catch { return $false }
}

function Test-Hub {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:4480/hub/health' -TimeoutSec 2; return $true }
    catch { return $false }
}

function Test-Nlu {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:4478/nlu/health' -TimeoutSec 2; return $true }
    catch { return $false }
}

<#
    Ищет папку python — ту, где лежат веса и serve.bat.

    Раньше здесь стояло просто «папка python рядом с приложением», и в рабочей
    копии проекта это верно. Но установщик папку python в себя не кладёт —
    там веса на десятки гигабайт (см. desktop\build.ps1). Значит у
    установленной копии в %LOCALAPPDATA%\Programs соседней папки python нет,
    проверка ниже молча не находила весов, и модель не поднималась вообще
    никогда: приложение отвечало заготовкой, хотя обученная модель лежала на
    диске в паре папок отсюда.

    Поэтому ищем по списку: своя переменная среды, затем pythonDir из
    config.json (на случай, если проект лежит в необычном месте), затем папка
    рядом с приложением, затем обычные места рабочей копии. Признак нужной
    папки — serve.bat внутри: именно его мы и запускаем.
#>
function Find-PythonDir {
    $candidates = @()
    if ($env:CLOUDHDR_PYTHON) { $candidates += $env:CLOUDHDR_PYTHON }
    if ($Config.pythonDir) {
        $candidates += [Environment]::ExpandEnvironmentVariables([string]$Config.pythonDir)
    }
    $candidates += Join-Path (Split-Path -Parent $Root) 'python'
    $candidates += Join-Path $env:USERPROFILE 'cloud-hdr\python'
    $candidates += Join-Path $env:USERPROFILE 'Desktop\cloud-hdr\python'
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Cloud HDR\python'

    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'serve.bat'))) { return $dir }
    }
    return $null
}

<#
    Поднимает модель разговора и разбор команд, если они обучены и ещё не
    запущены.

    Раньше этого здесь не было, и ярлык на рабочем столе поднимал только
    агента — то есть выполнение команд Windows работало, а на любой вопрос
    приложение отвечало заготовкой «модель не запущена». Получить модель можно
    было лишь через start-ai.bat, о чём догадаться неоткуда: ярлык и .bat
    выглядят как одно и то же приложение.

    Ждать готовности здесь НЕ нужно, и это осознанно: весам нужно 10-30 секунд
    на загрузку в видеопамять, а окно должно открыться сразу. Страница опросит
    порт при загрузке, а если модель не успела — поможет обновление страницы.
    Блокировать запуск приложения ради этого было бы хуже.
#>
function Start-Models {
    $python = Find-PythonDir
    if (-not $python) { return }

    # Пока идёт обучение модели, видеокарта занята им почти целиком: модель
    # разговора (~3 ГБ) рядом не поместится, и упало бы одно из двух. Окно
    # приложения при этом работает — команды, файлы, защита, перевод.
    $training = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*cloudhdr_ai*train*' -or $_.CommandLine -like '*cloudhdr_qwen*train*' -or $_.CommandLine -like '*compare_*' }
    if ($training) { return }

    if (-not (Test-Nlu) -and (Test-Path (Join-Path $python 'models\nlu\model-best'))) {
        Start-PyModule $python 'cloudhdr_nlu' 4478 'nlu'
    }

    if (Test-Model) { return }
    foreach ($weights in @('checkpoints\v7\best.pt', 'checkpoints\v6\best.pt', 'checkpoints\v5\best.pt',
                           'checkpoints\v4\best.pt', 'checkpoints\v3\best.pt',
                           'checkpoints\v2\best.pt', 'checkpoints\best.pt')) {
        if (Test-Path (Join-Path $python $weights)) {
            Start-PyModule $python 'cloudhdr_ai' 8080 'model'
            return
        }
    }
}

<#
    Запускает модуль python -m <module> serve скрыто, с журналом в
    %LOCALAPPDATA%\Cloud HDR\logs\<name>.log.

    Раньше модель и разбор команд запускались через serve.bat в СВЁРНУТЫХ
    окнах консоли. На панели задач это две лишние чёрные кнопки, их закрывали
    как мусор — и модель умирала до следующего запуска приложения, а окно
    отвечало заготовкой «модель не запущена». Hub, телефон и голос давно
    работали скрыто; теперь и эти две. serve.bat остался для ручного запуска.

    python.exe, а не pythonw.exe: у pythonw нет потоков вывода, и индикаторы
    загрузки весов падали бы на записи в них. Окно при этом скрыто, а вывод
    уходит в журнал — прошлый запуск сохраняется рядом как .prev.log.
#>
function Start-PyModule([string]$python, [string]$module, [int]$port, [string]$name) {
    $runtime = Join-Path (Split-Path -Parent $python) 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $runtime)) { $runtime = 'python.exe' }
    $logs = Join-Path $env:LOCALAPPDATA 'Cloud HDR\logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $log = Join-Path $logs "$name.log"
    $err = Join-Path $logs "$name.err.log"
    foreach ($file in @($log, $err)) {
        if (Test-Path -LiteralPath $file) { Move-Item -LiteralPath $file ($file -replace '\.log$', '.prev.log') -Force }
    }
    $env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'; $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Process -FilePath $runtime -ArgumentList '-m', $module, 'serve', '--host', '127.0.0.1', '--port', $port `
        -WorkingDirectory $python -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err
}

<#
    Поднимает Hub — умения второй версии: перевод по Ctrl+Alt+T, проверку на
    угрозы и разбор папок (python\cloudhdr_hub.py, только 127.0.0.1:4480).

    pythonw.exe, а не python.exe: у службы нет и не должно быть окна. Вторая
    копия не запускается — порт занят первой, и проверка здоровья это видит.
#>
function Start-Hub {
    if (Test-Hub) { return }
    $python = Find-PythonDir
    if (-not $python) { return }
    $hub = Join-Path $python 'cloudhdr_hub.py'
    if (-not (Test-Path -LiteralPath $hub)) { return }
    $runtime = Join-Path (Split-Path -Parent $python) 'runtime\pythonw.exe'
    if (-not (Test-Path -LiteralPath $runtime)) { $runtime = 'pythonw.exe' }
    $env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'
    Start-Process -FilePath $runtime -ArgumentList ('"{0}"' -f $hub) -WorkingDirectory $python -WindowStyle Hidden
}

<#
    Поднимает службу своего голоса (python\cloudhdr_voice.py, 127.0.0.1:4482):
    клон голоса хозяина для озвучки ответов. Живёт в отдельном окружении
    python\voice-env — модели XTTS нужна другая версия transformers.
    Пока голос не записан, служба просто отвечает «не готов», и озвучка идёт
    голосом Windows.
#>
function Start-Voice {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:4482/voice/health' -TimeoutSec 2; return } catch { }
    $python = Find-PythonDir
    if (-not $python) { return }
    $voice = Join-Path $python 'cloudhdr_voice.py'
    $runtime = Join-Path $python 'voice-env\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $voice) -or -not (Test-Path -LiteralPath $runtime)) { return }
    $env:COQUI_TOS_AGREED = '1'
    Start-Process -FilePath $runtime -ArgumentList ('"{0}"' -f $voice) -WorkingDirectory $python -WindowStyle Hidden
}

<#
    Поднимает службу телефона (python\cloudhdr_phone.py, порт 4481).

    Единственная служба Cloud HDR, видимая в домашней сети: телефону нужно до
    неё достучаться. Всё, что она делает для телефона, требует ключ из QR-кода,
    а брандмауэр пускает на порт только устройства той же локальной сети.
#>
function Start-Phone {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:4481/phone/health' -TimeoutSec 2; return } catch { }
    $python = Find-PythonDir
    if (-not $python) { return }
    $phone = Join-Path $python 'cloudhdr_phone.py'
    if (-not (Test-Path -LiteralPath $phone)) { return }
    $runtime = Join-Path (Split-Path -Parent $python) 'runtime\pythonw.exe'
    if (-not (Test-Path -LiteralPath $runtime)) { $runtime = 'pythonw.exe' }
    Start-Process -FilePath $runtime -ArgumentList ('"{0}"' -f $phone) -WorkingDirectory $python -WindowStyle Hidden
}

<#
    Поднимает спутника — быструю строку по Ctrl+Alt+P и учёт активности.

    Проверка идёт по имени файла в командной строке процесса, а не по окну:
    окна у спутника нет, пока не нажата горячая клавиша, и по окну он выглядел
    бы незапущенным. Вторая копия здесь недопустима: она попыталась бы занять ту
    же клавишу, не смогла и молча осталась бы висеть в памяти.
#>
$Script:CompanionStarted = $false

function Start-Companion {
    if ($Script:CompanionStarted) { return }
    $Script:CompanionStarted = $true
    if (-not (Test-Path -LiteralPath $Companion)) { return }
    $running = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*companion.ps1*' }
    if ($running) { return }

    Start-Script $Companion
}

<#
    Поднимает уши — постоянное прослушивание.

    Отдельный процесс, а не поток внутри агента или спутника, и это не
    архитектурная роскошь. Агент однопоточный: слушать в нём значило бы глохнуть
    ровно на то время, пока выполняется предыдущая команда, — то есть терять
    вторую команду подряд, которую как раз и говорят чаще всего. У спутника
    очередь сообщений WPF, и анимация панели съедала бы те же полсекунды.

    Проверка на вторую копию обязательна: микрофон открывается монопольно, и
    вторая копия молча не открыла бы его и осталась висеть в памяти, а первая
    при этом продолжала бы работать. Снаружи это выглядело бы как «иногда
    слышит, иногда нет».
#>
$Script:EarsStarted = $false

function Start-Ears {
    if ($Script:EarsStarted) { return }
    $Script:EarsStarted = $true
    if (-not (Test-Path -LiteralPath $Ears)) { return }
    if ($Config.listen -and $Config.listen.enabled -eq $false) { return }

    $running = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*ears.ps1*' }
    if ($running) { return }

    Start-Script $Ears
}

function Find-Browser {
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($path in $candidates) { if (Test-Path -LiteralPath $path) { return $path } }
    return $null
}

# --- 1. службы и окно приложения --------------------------------------------

# Модели поднимаем ДО всего остального и в обоих режимах.
#
# Иначе получалось так: окно приложения уже открыто, человек жмёт ярлык, ветка
# ниже выводит окно на передний план и выходит — а до запуска моделей выполнение
# просто не доходит. То есть ярлык чинил ровно тот случай, когда чинить нечего,
# и не помогал в том, ради которого его нажимают. С -AgentOnly та же история:
# теперь этим ключом окно приложения поднимает всё остальное хозяйство, и модели
# должны попасть в этот же список.
#
# Задержки это почти не добавляет: когда службы подняты, обе проверки отвечают
# за миллисекунды, а ждать их готовности здесь никто не станет.
Start-Models
Start-Hub
Start-Phone
Start-Voice

if (-not $AgentOnly) {
    # Спутника поднимаем здесь же, по той же причине, что и модели: ниже ветка с
    # уже открытым окном выходит из скрипта, и до запуска дело бы не дошло —
    # горячая клавиша не работала бы ровно у того, кто приложением пользуется.
    Start-Companion

    # Обычный путь: своё окно. Второй запуск CloudHDR.exe не плодит окон — он
    # будит уже открытое, поэтому проверять что-либо здесь не нужно.
    if (-not $Browser -and (Test-Path -LiteralPath $App)) {
        $where = Split-Path -Parent $App
        if ($Voice) { Start-Process -FilePath $App -ArgumentList '-voice' -WorkingDirectory $where }
        else        { Start-Process -FilePath $App -WorkingDirectory $where }
        exit 0
    }

    $window = Get-AppWindow
    if ($window) {
        [CloudHdrLaunch.Win]::ShowWindow($window.MainWindowHandle, 9) | Out-Null   # SW_RESTORE
        [CloudHdrLaunch.Win]::SetForegroundWindow($window.MainWindowHandle) | Out-Null
        exit 0
    }
}

# --- 2. агент ---------------------------------------------------------------

if (-not (Test-Agent)) {
    Start-Script $Agent

    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 400
        if (Test-Agent) { break }
    }
}

# Спутник поднимается ПОСЛЕ агента: при старте он просит у него список программ,
# чтобы первое «открой …» из быстрой строки не ждало сбора списка.
Start-Companion

# Уши — по той же причине и строго здесь, а не выше. Словарь прослушивания
# состоит из имён программ ЭТОГО компьютера, и берутся они у агента. Запустить
# уши раньше агента значило бы собрать словарь из одних постоянных фраз, положить
# его в кэш — и потом долго удивляться, почему «открой стим» не работает, хотя
# Steam стоит.
Start-Ears

if ($AgentOnly) { exit 0 }

# --- 3. запасное окно на движке браузера ------------------------------------
# Сюда попадаем, если CloudHDR.exe не собран или запуск сделан с ключом -Browser
# (например, ради распознавания речи, которое живёт только в Chrome).
# Модели уже запущены выше; окно их готовности не ждёт — весам нужно 10-30
# секунд, а приложение должно открыться сразу.

$browser = Find-Browser
if (-not $browser) {
    Start-Process $Address        # запасной вариант — браузер по умолчанию
    exit 0
}

Start-Process -FilePath $browser -ArgumentList @(
    "--app=$Address",
    '--window-size=1280,860',
    '--disable-features=Translate'
)
