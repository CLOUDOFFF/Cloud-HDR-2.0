<#
    Cloud HDR Ears — постоянное прослушивание.

    Слушает всегда, ключевое слово говорить не нужно, наружу не уходит ничего.
    Движок — свой, из ears-core.ps1; здесь словарь, пороги и то, что происходит
    после того, как фраза узнана.

    ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ПРОЦЕСС. Агент однопоточный: пока он выполняет команду,
    он не отвечает никому. Слушать в нём означало бы глохнуть ровно на то время,
    пока помощник занят, — то есть терять вторую команду подряд, которую как раз
    и говорят чаще всего («открой блокнот… нет, закрой»). Спутник с панелью не
    подходит по другой причине: у него окно и очередь сообщений WPF, и полсекунды
    на анимации панели превратились бы в полсекунды глухоты.

    КАК ЭТО ОКАЗЫВАЕТСЯ БЫСТРЫМ. После конца фразы делать почти нечего: признаки
    считаются за десяток миллисекунд, перебор словаря — за единицы. Всё время
    уходит на 280 мс тишины, по которым определяется конец фразы, и меньше их
    сделать нельзя — люди делают паузы внутри команд. Итого от последнего звука
    до запуска программы — примерно треть секунды. Прежняя схема ждала ответа
    чужого сервиса и укладывалась в полторы-две секунды в лучшем случае.

    ДВА ПОРОГА, А НЕ ОДИН. Первый запуск помощник встречает с эталонами, снятыми
    с синтезатора Windows, — это не человеческий голос, и совпадения с ним хуже,
    чем будут потом. Один порог здесь работать не может: строгий не пропустит
    ничего и учиться будет не на чем, свободный начнёт выполнять услышанное в
    телевизоре. Поэтому порогов два. Уверенное совпадение выполняется сразу.
    Похожее — переспрашивается («Открыть Блокнот?»), и вот ЭТОТ случай и есть
    обучение: подтверждённая фраза сохраняется как эталон уже в голосе хозяина.
    Живой эталон совпадает с живым голосом куда лучше синтетического, поэтому
    фраза, подтверждённая один раз, дальше срабатывает сразу.
#>

param(
    [int]$Port = 0,
    [switch]$Rebuild,      # пересобрать словарь с нуля
    [switch]$Listen,       # слушать и показывать, но ничего не выполнять
    [switch]$Enroll,       # наговорить эталоны своим голосом
    [switch]$Check,        # проверить микрофон: показать уровень вживую
    [int]$Device = -1,     # номер устройства записи; -1 — то, что выбрано в Windows
    [string]$Sense = ''    # строгий | обычный | чуткий
)

# StrictMode здесь намеренно не включён. Он ловит обращения к несуществующим
# свойствам — а половина работы этой службы состоит как раз в разборе ответов
# агента, где поле может и не прийти. Служба, которая слушает целый день, не
# должна умирать от отсутствующего поля в JSON: цена ошибки — молчащий
# микрофон, и человек узнает об этом, только когда команда не сработает.
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
. (Join-Path $Root 'ears-core.ps1')

$LogDir = Join-Path $Root 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile   = Join-Path $LogDir 'ears.log'
$StateFile = Join-Path $LogDir 'ears-state.json'
$VocabFile = Join-Path $LogDir 'ears-vocab.json'
$MuteFile  = Join-Path $LogDir 'ears-mute'

$ConfigFile = Join-Path $Root 'config.json'
$Config = if (Test-Path $ConfigFile) { Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
if ($Port -le 0) { $Port = if ($Config -and $Config.PSObject.Properties['port']) { [int]$Config.port } else { 4477 } }
$Base = "http://127.0.0.1:$Port"

function Write-Line([string]$message) {
    $stamp = (Get-Date).ToString('HH:mm:ss')
    $line = "$stamp  $message"
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

# ------------------------------------------------------------- пороги --------

<#
    Пороги названы по тому, чем платит человек за ошибку.

    Ложное срабатывание — компьютер сделал что-то сам, и это заметно и
    неприятно. Пропуск — команду пришлось повторить, это две секунды. Цена
    разная, поэтому пороги смещены в сторону молчания.

    Числа взяты не с потолка. На проверке словаря из шести команд своя фраза,
    сказанная заметно быстрее эталона, стоила 0,39-0,43, а посторонняя речь —
    1,74-1,80. Между ними пустое поле шириной больше единицы, и пороги стоят
    в нём: 0,95 «выполняю», 1,30 «переспрошу». Живой голос против
    синтетического эталона попадает выше синтетической пары, поэтому нижний
    порог намеренно не прижат к 0,43 — он оставляет место живому голосу.

    near — во сколько раз голос должен быть громче тишины в комнате. Это
    единственное, чем «сказали мне» отличается от «говорят рядом»: слова у них
    одинаковые, и никакой словарь такого различия не даёт.
#>
$SENSES = @{
    'строгий' = @{ run = 0.80; ask = 1.05; gap = 0.28; near = 3.2; floor = 0.030 }
    'обычный' = @{ run = 0.95; ask = 1.30; gap = 0.18; near = 2.4; floor = 0.020 }
    'чуткий'  = @{ run = 1.10; ask = 1.45; gap = 0.10; near = 1.7; floor = 0.013 }
}
if (-not $Sense) {
    $Sense = if ($Config -and $Config.PSObject.Properties['listen'] -and $Config.listen.PSObject.Properties['sense']) {
        [string]$Config.listen.sense
    } else { 'обычный' }
}
if (-not $SENSES.ContainsKey($Sense)) { $Sense = 'обычный' }
$Level = $SENSES[$Sense]

# ------------------------------------------------------------- словарь -------

<#
    Постоянная часть словаря.

    Это фразы, которые не зависят от того, что установлено на компьютере.
    Каждая уходит в /api/quick как есть — разбирать её здесь второй раз не нужно,
    у агента для этого уже есть Resolve-Quick, и дублировать его логику значило
    бы завести два разных мнения об одной фразе.
#>
$FIXED = @(
    'стоп', 'отмена', 'хватит',
    'сделай скриншот', 'сними экран',
    'сверни все окна', 'покажи рабочий стол',
    'громче', 'тише', 'выключи звук', 'включи звук',
    'какое время', 'который час', 'какой сегодня день',
    'заблокируй компьютер',
    'открой проводник', 'открой папку загрузки', 'открой документы',
    'открой рабочий стол', 'открой музыку', 'открой видео',
    'открой корзину', 'открой параметры', 'открой диспетчер задач',
    'открой калькулятор', 'открой блокнот', 'открой пейнт',
    'закрой окно', 'сверни окно', 'разверни окно',
    # «это окно» и вкладки — работают с тем, что перед глазами (cloudhdr_apps.this_answer)
    'сверни это', 'разверни это', 'закрой вкладку', 'новая вкладка', 'следующая вкладка',
    'обнови страницу', 'прокрути вниз', 'включи звук', 'открой яндекс музыку',
    'следующий трек', 'предыдущий трек', 'поставь на паузу',
    'включи курсор', 'выключи курсор',
    'что на экране', 'найди в интернете',

    # Ответы на переспрос. Они обязаны быть в словаре наравне с командами:
    # переспрашивать голосом и не уметь расслышать ответ — худший из возможных
    # вариантов, человек оказывается в разговоре с глухим.
    'да', 'нет', 'ага', 'давай', 'конечно', 'не надо'
)

<#
    Переменная часть — имена программ этого компьютера.

    Берётся у агента: он всё равно строит эти списки, чтобы уметь запускать
    программы, и держит их в кэше. Спрашивать реестр второй раз отсюда значило
    бы завести вторую точку правды.

    Список обрезается, и это не экономия ради экономии. Каждая фраза словаря —
    это одно сравнение DTW на каждую услышанную фразу и примерно треть секунды
    синтеза при сборке. Сотня фраз — это 5 мс на решение и полминуты на первую
    сборку; тысяча превратила бы и то, и другое в заметное ожидание. Поэтому
    берутся те программы, которыми пользуются: у агента есть учёт запусков, и
    порядок в списке уже осмысленный.
#>
function Get-Token {
    $grant = Invoke-RestMethod -Uri "$Base/api/session/grant" -Method Post -Body '{}' `
        -ContentType 'application/json' -TimeoutSec 10
    return [string]$grant.token
}

function Get-AppPhrases([string]$token) {
    $phrases = @()
    try {
        $apps = Invoke-RestMethod -Uri "$Base/api/apps" -Method Get -TimeoutSec 30 `
            -Headers @{ 'X-Cloud-Token' = $token }
    } catch {
        Write-Line "Список программ не получен ($($_.Exception.Message)) — словарь только из постоянных фраз."
        return $phrases
    }

    $names = @()
    foreach ($app in @($apps.apps)) {
        if ($app.found) { $names += [string]$app.title }
    }
    foreach ($game in @($apps.games)) { $names += [string]$game }
    foreach ($name in @($apps.names)) { $names += [string]$name }

    # Длинные имена вида «Microsoft Visual C++ 2015 Redistributable» голосом не
    # называют никогда, а место в словаре занимают наравне с «Хром». Отсекаем по
    # числу слов: команда, которую реально произносят, — это одно-два слова.
    $clean = @()
    foreach ($name in $names) {
        $short = ($name -replace '\s*\(.*?\)\s*', ' ').Trim()
        if (-not $short) { continue }
        $words = @($short -split '\s+')
        if ($words.Count -gt 3) { continue }
        if ($short.Length -gt 26) { continue }
        if ($short -match '^\d') { continue }
        if ($clean -notcontains $short) { $clean += $short }
    }

    $limit = 34
    foreach ($name in ($clean | Select-Object -First $limit)) {
        $phrases += ("открой {0}" -f $name.ToLower())
        $phrases += ("закрой {0}" -f $name.ToLower())
    }
    Write-Line ("Программ в словаре: {0}" -f [Math]::Min($limit, $clean.Count))
    return $phrases
}

<#
    Сборка словаря.

    Каждая фраза проговаривается синтезатором Windows и превращается в эталон.
    Это единственное место, где помощнику нужно время на подготовку, — примерно
    полминуты на сотню фраз, один раз. Результат кладётся в файл и переживает
    перезапуск; пересборка нужна, только когда изменился список программ.

    Живые эталоны, накопленные голосом хозяина, при пересборке СОХРАНЯЮТСЯ.
    Иначе установка любой новой программы стирала бы всё обучение — и помощник
    раз за разом возвращался бы к тому, что хуже слышит знакомые команды.
#>
function Build-Vocabulary([string[]]$phrases, $keepLive) {
    $templates = @()
    $done = 0
    foreach ($phrase in $phrases) {
        $features = $null
        try { $features = Get-SynthFeatures $phrase } catch { $features = $null }
        if ($features -and $features.Count -gt 0) {
            $templates += (New-Template $phrase 'quick' $features 'synth')
        }
        $done++
        if ($done % 20 -eq 0) { Write-Line ("  собрано {0} из {1}" -f $done, $phrases.Count) }
    }
    foreach ($live in @($keepLive)) { $templates += $live }
    return $templates
}

function Save-Vocabulary($templates, [string[]]$phrases) {
    $data = [ordered]@{
        built = (Get-Date).ToString('s')
        signature = (Get-Signature $phrases)
        templates = @($templates)
    }
    $json = $data | ConvertTo-Json -Depth 6 -Compress
    [IO.File]::WriteAllText($VocabFile, $json, (New-Object Text.UTF8Encoding($false)))
}

function Get-Signature([string[]]$phrases) {
    $joined = ($phrases | Sort-Object) -join '|'
    $bytes = [Text.Encoding]::UTF8.GetBytes($joined)
    $hash = [Security.Cryptography.MD5]::Create().ComputeHash($bytes)
    return ([BitConverter]::ToString($hash) -replace '-', '').Substring(0, 12)
}

function Import-Vocabulary {
    if (-not (Test-Path $VocabFile)) { return $null }
    try { return Get-Content $VocabFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

# --------------------------------------------------------------- голос -------

# Говорим тем же голосом, что и окно приложения, — клоном хозяина из службы
# голоса (python/cloudhdr_voice.py, 4482). Раньше здесь был синтезатор Windows
# (Irina): окно отвечало живым голосом, а уши — роботом.
#
# Синтез идёт в отдельном потоке: цикл прослушивания не ждёт ни службу, ни
# звук. Частые фразы служба отдаёт с диска мгновенно; если её нет — старый
# синтезатор Windows, лишь бы подтверждение прозвучало.
$Script:VoiceUrl = 'http://127.0.0.1:4482'
$Script:Voice = [hashtable]::Synchronized(@{ Seq = 0; MuteUntil = [datetime]::MinValue; Player = $null; Synth = $null })
$Script:VoicePool = $null

$Script:SayWorker = {
    param($V, $url, $text, $seq)
    try {
        $client = New-Object System.Net.WebClient
        $client.Encoding = [Text.Encoding]::UTF8
        $client.Headers['Content-Type'] = 'application/json; charset=utf-8'
        $body = @{ text = $text } | ConvertTo-Json -Compress
        $wav = $client.UploadData("$url/voice/tts", 'POST', [Text.Encoding]::UTF8.GetBytes($body))
        if ($V.Seq -ne $seq) { return }                 # пока синтезировали — сказали новое
        # PCM 16 бит моно: длительность — из заголовка WAV
        $perSec = [BitConverter]::ToInt32($wav, 28)
        $seconds = if ($perSec -gt 0) { ($wav.Length - 44) / $perSec } else { 3 }
        $player = New-Object System.Media.SoundPlayer (New-Object IO.MemoryStream (, $wav))
        try { if ($V.Player) { $V.Player.Stop() } } catch { }
        $V.Player = $player
        $V.MuteUntil = (Get-Date).AddSeconds($seconds + 0.4)
        $player.Play()
    } catch {
        if ($V.Seq -ne $seq) { return }
        try {
            if (-not $V.Synth) {
                Add-Type -AssemblyName System.Speech
                $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
                $russian = $synth.GetInstalledVoices() |
                           Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'ru*' } |
                           Select-Object -First 1
                if ($russian) { $synth.SelectVoice($russian.VoiceInfo.Name) }
                $synth.Rate = 1
                $V.Synth = $synth
            }
            $V.MuteUntil = (Get-Date).AddSeconds([Math]::Min(8, 1 + $text.Length / 14))
            $V.Synth.SpeakAsyncCancelAll()
            $V.Synth.SpeakAsync($text) | Out-Null
        } catch { }
    }
}

function Say([string]$text) {
    if (-not $text) { return }
    try {
        if (-not $Script:VoicePool) {
            $Script:VoicePool = [RunspaceFactory]::CreateRunspacePool(1, 2)
            $Script:VoicePool.Open()
        }
        $V = $Script:Voice
        $V.Seq++
        try { if ($V.Player) { $V.Player.Stop() } } catch { }
        try { if ($V.Synth) { $V.Synth.SpeakAsyncCancelAll() } } catch { }
        # Пока фраза синтезируется, мы глухи: ответ вот-вот зазвучит, и
        # услышать собственное начало как команду нельзя.
        $V.MuteUntil = (Get-Date).AddSeconds(6)
        Add-SaidPhrase $text
        $ps = [PowerShell]::Create()
        $ps.RunspacePool = $Script:VoicePool
        [void]$ps.AddScript($Script:SayWorker).AddArgument($V).AddArgument($Script:VoiceUrl).AddArgument($text).AddArgument($V.Seq)
        [void]$ps.BeginInvoke()
    } catch { }
}

# Сказанное запоминаем (последние 60 коротких фраз): при следующем запуске
# они озвучатся заранее, и привычные ответы будут звучать без задержки.
$Script:SaidFile = Join-Path $LogDir 'ears-said.txt'
$Script:Said = New-Object System.Collections.Generic.List[string]
try { if (Test-Path $Script:SaidFile) { $Script:Said.AddRange([string[]](Get-Content $Script:SaidFile -Encoding UTF8 | Where-Object { $_ })) } } catch { }

function Add-SaidPhrase([string]$text) {
    if ($text.Length -gt 160 -or $text -match '[\r\n]') { return }
    [void]$Script:Said.Remove($text)
    $Script:Said.Add($text)
    while ($Script:Said.Count -gt 60) { $Script:Said.RemoveAt(0) }
    try { Set-Content -Path $Script:SaidFile -Value $Script:Said -Encoding UTF8 } catch { }
}

# Частые подтверждения озвучиваем заранее, один раз: дальше они лежат на диске
# у службы голоса и звучат сразу, не трогая видеокарту.
function Initialize-VoicePhrases {
    $phrases = @($Script:Said) + @('Слушаю.', 'Готово.', 'Сделал.', 'Не расслышал, повтори.', 'Сворачиваю.', 'Разворачиваю.',
                 'Закрыл вкладку.', 'Открыл новую вкладку.', 'Громче.', 'Тише.', 'Звук выключен.', 'Звук включён.',
                 'Пауза.', 'Продолжаю.', 'Следующий трек.', 'Открываю Яндекс Музыку.', 'Что сегодня по душе — какую музыку включить?')
    try {
        $body = @{ texts = $phrases } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "$Script:VoiceUrl/voice/prepare" -Method Post -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 3 | Out-Null
    } catch { }
}

# ------------------------------------------------------------ состояние ------

function Write-State($data) {
    try {
        $json = $data | ConvertTo-Json -Depth 4 -Compress
        [IO.File]::WriteAllText($StateFile, $json, (New-Object Text.UTF8Encoding($false)))
    } catch { }
}

# ------------------------------------------------------------ исполнение -----

<#
    Выполнить узнанную фразу.

    Токен добывается лениво и переспрашивается один раз при отказе: агент мог
    перезапуститься, и старый токен стал бы недействительным. Второй отказ
    подряд — это уже не просроченный токен, а что-то другое, и повторять
    бесконечно незачем.
#>
$Script:Token = ''

<#
    Разговор уточнения. Модель переспросила («Что сегодня по душе?») — ответ
    («рок») уходит вместе с вопросом, иначе «рок» понялся бы как новая просьба.
#>
$Script:Dialog = @()
$Script:DialogUntil = [datetime]::MinValue

function Invoke-Heard([string]$text) {
    $history = @()
    if ((Get-Date) -lt $Script:DialogUntil) { $history = $Script:Dialog }
    $Script:Dialog = @(); $Script:DialogUntil = [datetime]::MinValue
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        if (-not $Script:Token) {
            try { $Script:Token = Get-Token } catch { return @{ ok = $false; say = 'Агент не отвечает.' } }
        }
        # Повтор — ТОЛЬКО если агент не пустил (401: токен устарел после его
        # перезапуска). Раньше повторялось при любой ошибке, а ответ читался
        # через $response.Content: у текстового ответа это строка, а не байты,
        # GetString падал — и каждая команда уходила агенту дважды («громче»
        # прибавляло двадцать, «сверни» сворачивало два окна).
        $response = $null
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes((@{ text = $text; history = @($history) } | ConvertTo-Json -Depth 5 -Compress))
            $response = Invoke-WebRequest -Uri "$Base/api/quick" -Method Post -Body $bytes `
                -ContentType 'application/json; charset=utf-8' `
                -Headers @{ 'X-Cloud-Token' = $Script:Token } -TimeoutSec 60 -UseBasicParsing
        } catch {
            $status = 0
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
            if ($status -eq 401 -and $attempt -eq 0) { $Script:Token = ''; continue }
            return @{ ok = $false; say = "Не вышло: $($_.Exception.Message)" }
        }
        try {
            $answer = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
            return @{ ok = $true; say = [string]$answer.say; kind = [string]$answer.kind }
        } catch {
            return @{ ok = $true; say = '' }        # выполнено, ответ не разобрался — не повторяем
        }
    }
    return @{ ok = $false; say = 'Не вышло.' }
}

# ------------------------------------------------------ свободная речь ------

<#
    Всё, чего нет среди эталонов, — «Клауд, перемести отчёт в документы»,
    «Клауд, что занимает место на диске» — распознаёт Whisper в службе умений
    (python\cloudhdr_hub.py, /hub/asr). Здесь только упаковка звука и решение,
    к нам ли обращаются.

    Без обращения по имени свободную речь не выполняем: слушаем мы всегда, и
    разговор в комнате или фильм на фоне иначе превращались бы в команды.
    Заготовленные короткие фразы («громче», «открой блокнот») по-прежнему
    работают без имени — там словарь сам по себе защищает от случайностей.
#>
Add-Type -TypeDefinition @'
public static class EarsPcm {
    public static string ToBase64(float[] samples) {
        byte[] bytes = new byte[samples.Length * 2];
        for (int i = 0; i < samples.Length; i++) {
            float v = samples[i];
            if (v > 1f) v = 1f; else if (v < -1f) v = -1f;
            short s = (short)(v * 32767f);
            bytes[2 * i] = (byte)(s & 0xFF);
            bytes[2 * i + 1] = (byte)((s >> 8) & 0xFF);
        }
        return System.Convert.ToBase64String(bytes);
    }
}
'@

$HubBase = 'http://127.0.0.1:4480'

function Invoke-Asr([float[]]$samples) {
    try {
        $body = [Text.Encoding]::UTF8.GetBytes((@{ pcm = [EarsPcm]::ToBase64($samples); rate = 16000 } | ConvertTo-Json -Compress))
        $response = Invoke-WebRequest -Uri "$HubBase/hub/asr" -Method Post -Body $body `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 40 -UseBasicParsing
        $answer = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
        if (-not $answer.ok) { return $null }
        return $answer
    } catch { return $null }
}

<#
    Фраза не команда («Клауд, как дела?») — агент отвечает kind='model', и
    спрашиваем модель сами, коротко: ответ читается вслух, лекция не нужна.
#>
function Ask-Model([string]$text) {
    try {
        $body = @{ model = 'cloud-hdr-3.2'; stream = $false; max_tokens = 120
                   messages = @(@{ role = 'system'; content = 'Отвечай коротко, в одно-два предложения: ответ будет прочитан вслух.' },
                                @{ role = 'user'; content = $text }) } | ConvertTo-Json -Depth 5 -Compress
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/v1/chat/completions' -Method Post `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 60 -UseBasicParsing
        $answer = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
        return [string]$answer.choices[0].message.content
    } catch { return 'Модель сейчас не отвечает.' }
}

function Invoke-Free([string]$command) {
    Write-Line ("КОМАНДА | «{0}»" -f $command)
    $result = Invoke-Heard $command
    $say = [string]$result.say
    if ($result.kind -eq 'ask') {
        # Модель уточняет — следующую фразу слушаем без имени и с историей
        $Script:Dialog = @(@{ role = 'user'; content = $command }, @{ role = 'assistant'; content = $say })
        $Script:DialogUntil = (Get-Date).AddSeconds(15)
        $Script:awaitUntil = (Get-Date).AddSeconds(15)
        Write-Line ("УТОЧНЯЮ | «{0}»" -f $say)
        return $say
    }
    if ($result.kind -eq 'model' -or -not $say) { $say = Ask-Model $command }
    # Длинные списки вслух не читаем — первые строки и хватит.
    $lines = @($say -split "`n" | Where-Object { $_.Trim() })
    if ($lines.Count -gt 4) { $say = (($lines | Select-Object -First 4) -join '. ') + '. Остальное — в окне Cloud HDR.' }
    return $say
}

# ---------------------------------------------------------- проверка --------

<#
    Проверка микрофона.

    Нужна отдельным режимом, потому что «помощник меня не слышит» — это три
    разные поломки с одинаковым видом снаружи: микрофон не тот, микрофон
    выключен физически, голос слишком тихий для порога. Показанный вживую
    уровень отвечает на все три сразу: цифры стоят на месте — не тот микрофон
    или он выключен; цифры шевелятся, но не дотягивают до нужного — гейн или
    расстояние.
#>
if ($Check) {
    Write-Host ""
    Write-Host "Устройства записи, как их видит Windows:" -ForegroundColor Cyan
    $names = [CloudHdrEars.Native]::InputNames()
    if ($names.Count -eq 0) { Write-Host "  ни одного — микрофон не подключён"; exit 1 }
    for ($i = 0; $i -lt $names.Count; $i++) {
        $mark = if ($i -eq $Device -or ($Device -lt 0 -and $i -eq 0)) { '*' } else { ' ' }
        Write-Host ("  {0} [{1}] {2}" -f $mark, $i, $names[$i])
    }

    $mic = New-Object CloudHdrEars.Mic
    if (-not $mic.Start($Device)) { Write-Host "Не открылся: $($mic.Error)" -ForegroundColor Red; exit 1 }

    Write-Host ""
    Write-Host "Говорите обычным голосом. Ctrl+C — закончить." -ForegroundColor Cyan
    Write-Host "Нужно, чтобы «сейчас» во время речи уверенно перекрывало «порог»." -ForegroundColor DarkGray
    Write-Host ""
    try {
        while ($true) {
            Start-Sleep -Milliseconds 250
            $need = [Math]::Max($Level.floor, $mic.Floor * $Level.near)
            $bars = [int][Math]::Min(40, $mic.RawPeak * 400)
            $mic.RawPeak = 0
            $line = "  сейчас {0,-42} порог {1:N4}  фраз услышано: {2}" -f
                    ('#' * $bars + '.' * (40 - $bars)), $need, ($(if ($mic.Speaking) { 'говорят' } else { 'тихо' }))
            Write-Host ("`r" + $line) -NoNewline
        }
    } finally { $mic.Stop(); Write-Host "" }
    exit 0
}

# =============================================================== запуск =======

Write-Line '--------------------------------------------------'
Write-Line ("Уши Cloud HDR. Чувствительность: {0}. Порт агента: {1}." -f $Sense, $Port)

# Агента ждём, а не машем на него рукой.
#
# Уши поднимаются вместе с приложением, и агент в этот момент может ещё
# подниматься сам. Собрать словарь без него можно — но это будет словарь из
# одних постоянных фраз, без единого имени программы, и он ляжет в кэш. Дальше
# всё выглядело бы работающим: помощник слушает, «сделай скриншот» выполняет, а
# «открой стим» не понимает никогда. Полминуты ожидания здесь дешевле такой
# поломки, потому что найти её потом почти невозможно.
$token = ''
for ($wait = 0; $wait -lt 40; $wait++) {
    try { $token = Get-Token; break } catch { Start-Sleep -Milliseconds 750 }
}
if (-not $token) { Write-Line 'Агент так и не ответил — словарь будет только из постоянных фраз.' }
$Script:Token = $token

$phrases = @($FIXED)
if ($token) { $phrases += (Get-AppPhrases $token) }
$phrases = @($phrases | Select-Object -Unique)

$stored = Import-Vocabulary
$signature = Get-Signature $phrases
$live = @()
if ($stored -and $stored.PSObject.Properties['templates']) {
    $live = @($stored.templates | Where-Object { $_.source -eq 'live' })
}

if ($Rebuild -or -not $stored -or $stored.signature -ne $signature) {
    Write-Line ("Собираю словарь: {0} фраз. Это разовая работа примерно на полминуты." -f $phrases.Count)
    if ($live.Count -gt 0) { Write-Line ("Живых эталонов сохраняю: {0}" -f $live.Count) }
    $templates = Build-Vocabulary $phrases $live
    Save-Vocabulary $templates $phrases
    Write-Line ("Словарь готов: {0} эталонов." -f @($templates).Count)
} else {
    $templates = @($stored.templates)
    Write-Line ("Словарь из файла: {0} эталонов, из них живых {1}." -f @($templates).Count, $live.Count)
}

$matcher = New-Object CloudHdrEars.Matcher
foreach ($template in $templates) {
    $matcher.Add([string]$template.phrase, [string]$template.intent, [string]$template.source,
                 (ConvertTo-Features $template)) | Out-Null
}
Write-Line ("В памяти эталонов: {0}" -f $matcher.Count)

# --------------------------------------------------------------- эталоны -----

<#
    Наговорить эталоны своим голосом.

    Режим необязательный: помощник слышит и без него. Но десять минут здесь
    заменяют неделю привыкания — живой эталон совпадает с живым голосом заметно
    лучше синтетического, и после записи фразы переспрашивание по ней исчезает.
#>
if ($Enroll) {
    $mic = New-Object CloudHdrEars.Mic
    if (-not $mic.Start($Device)) { Write-Line ("Микрофон недоступен: {0}" -f $mic.Error); exit 1 }
    Write-Line 'Говорите фразу после подсказки. Пустой ввод — пропустить, «хватит» — закончить.'
    $added = @()
    foreach ($phrase in $phrases) {
        Write-Host ""
        Write-Host ("  Скажите: «{0}»   (Enter — записываю, «хватит» — конец)" -f $phrase) -ForegroundColor Cyan
        $answer = Read-Host '  '
        if ($answer -eq 'хватит') { break }
        $mic.Flush()
        Write-Host '  слушаю...' -NoNewline
        $said = $null
        $until = (Get-Date).AddSeconds(6)
        while ((Get-Date) -lt $until -and -not $said) {
            $said = $mic.Take()
            if (-not $said) { Start-Sleep -Milliseconds 30 }
        }
        if (-not $said) { Write-Host ' не расслышал, пропускаю'; continue }
        $features = [CloudHdrEars.Dsp]::Mfcc($said.Samples)
        if ($features.Count -lt 10) { Write-Host ' слишком коротко'; continue }
        $added += (New-Template $phrase 'quick' $features 'live')
        Write-Host (' записано ({0:N1} с)' -f $said.Seconds) -ForegroundColor Green
    }
    $mic.Stop()
    if ($added.Count -gt 0) {
        $all = @($templates) + $added
        Save-Vocabulary $all $phrases
        Write-Line ("Добавлено эталонов своим голосом: {0}" -f $added.Count)
    }
    exit 0
}

# ------------------------------------------------------------- слушаем -------

$mic = New-Object CloudHdrEars.Mic
$mic.NearRatio = $Level.near
$mic.AbsFloor  = $Level.floor

<#
    Микрофона может не быть в ту секунду, когда уши поднимаются, и это не
    повод умирать.

    Уши стартуют из автозагрузки вместе с Windows, а звуковая подсистема в
    этот момент ещё перечисляет устройства — waveIn честно отвечает, что их
    ноль. Прежний код на это выходил с ошибкой, и прослушивания не было до
    перезапуска приложения. Снаружи это выглядело как «работает через раз»:
    с гарнитурой, воткнутой заранее, — да; с подключённой минутой позже —
    нет, и понять причину человеку неоткуда, потому что окно показывает
    ровно то же самое.

    Поэтому здесь ждём, а не выходим. В лог идёт одна строка на пропажу и
    одна на появление — иначе за ночь ожидания он вырастет на тысячи
    одинаковых записей.
#>
$waiting = $false
while (-not $mic.Start($Device)) {
    if (-not $waiting) {
        Write-Line ("Микрофон недоступен: {0}. Жду, пока появится." -f $mic.Error)
        $waiting = $true
    }
    Write-State @{ on = $false; waiting = $true; error = $mic.Error }
    Start-Sleep -Seconds 3
}
if ($waiting) { Write-Line 'Микрофон появился.' }

Write-Line 'Слушаю. Ключевое слово не нужно. Наружу не уходит ничего.'
if (-not $Listen) { Initialize-VoicePhrases }
if ($Listen) { Write-Line 'Режим проверки: показываю, что слышу, но ничего не выполняю.' }

$lastText = ''; $lastAt = [datetime]::MinValue
$pending = $null            # фраза, о которой переспросили
$pendingUntil = [datetime]::MinValue
$awaitUntil = [datetime]::MinValue     # после «Клауд» — ждём команду без имени
$muteUntil = [datetime]::MinValue
$heartbeat = [datetime]::MinValue
$dirty = $false             # появились живые эталоны — надо сохранить
$lastFrames = -1L           # сколько кадров звука пришло к прошлой проверке
$stalled = 0                # проверок подряд без единого нового кадра

try {
    while ($true) {

        # Раз в две секунды пишем состояние: его читают окно приложения и панель,
        # чтобы показывать уровень и то, что было услышано последним.
        if ((Get-Date) -gt $heartbeat) {
            $heartbeat = (Get-Date).AddSeconds(2)
            Write-State @{ on = $true; sense = $Sense; templates = $matcher.Count
                           floor = [math]::Round($mic.Floor, 5); level = [math]::Round($mic.Level, 5)
                           speaking = $mic.Speaking; heard = $lastText
                           at = $lastAt.ToString('s') }

            # Живые эталоны сохраняются здесь, а не сразу после команды. Запись
            # файла на каждое срабатывание — это работа диска ровно в тот
            # момент, когда помощник должен отвечать быстро; две секунды спустя
            # она никому не мешает, а потерять при внезапном закрытии можно
            # самое большее один эталон, и тот запишется при следующем разе.
            if ($dirty) {
                try { Save-Vocabulary $templates $phrases; $dirty = $false } catch { }
            }

            <#
                Микрофон может пропасть на ходу: гарнитуру выдернули, звуковую
                подсистему перезапустили, устройство отобрала другая программа.
                waveIn об этом не сообщает никак — буферы просто перестают
                возвращаться, и уши остаются «работающими», но глухими.

                Единственный честный признак — кадры перестали приходить.
                Шесть секунд молчания счётчика: живой микрофон в тихой комнате
                всё равно шлёт кадры, там не бывает пауз даже на секунду.
            #>
            if ($mic.Frames -eq $lastFrames) {
                $stalled++
                if ($stalled -ge 3) {
                    $stalled = 0
                    Write-Line 'Микрофон замолчал — переоткрываю.'
                    if ($mic.Restart($Device)) { Write-Line 'Микрофон на месте, слушаю дальше.' }
                    else { Write-Line ("Пока не открывается: {0}" -f $mic.Error) }
                }
            } else {
                $stalled = 0
                $lastFrames = $mic.Frames
            }
        }

        $said = $mic.Take()
        if (-not $said) { Start-Sleep -Milliseconds 20; continue }

        # Пока помощник говорит сам, услышанное выбрасываем: иначе он слышит
        # собственный голос из колонок и принимает его за команду. Своё
        # молчание мы знаем сами, а о том, что говорит окно приложения, узнаём
        # из файла — его пишет агент, когда страница просит озвучить ответ.
        if ((Get-Date) -lt $muteUntil) { continue }
        if ((Get-Date) -lt $Script:Voice.MuteUntil) { continue }
        if (Test-Path -LiteralPath $MuteFile) {
            try {
                $until = [DateTime]::Parse((Get-Content -LiteralPath $MuteFile -Raw))
                if ((Get-Date) -lt $until) { continue }
            } catch { }
        }

        $features = [CloudHdrEars.Dsp]::Mfcc($said.Samples)
        if ($features.Count -lt 12) { continue }
        $match = $matcher.Best($features)
        if ($match.Cost -eq [double]::MaxValue) { continue }

        $near = [Math]::Max($Level.floor, $mic.Floor * $Level.near)
        $loud = $said.Peak -ge $near

        $note = ("{0:N2} с | «{1}» цена {2:N2} отрыв {3:N2} | {4:N0} мс | громкость {5:N3} при нужных {6:N3}" -f
                 $said.Seconds, $match.Phrase, $match.Cost, $match.Gap, $match.Millis, $said.Peak, $near)

        # ------------------------------------------------ ответ на услышанное --

        if ($pending -and (Get-Date) -lt $pendingUntil) {
            # Ждём «да» или «нет» на переспрос. Любая другая фраза отменяет
            # вопрос и разбирается сама по себе — переспрашивать дважды подряд
            # хуже, чем не переспрашивать вовсе.
            if ($match.Phrase -match '^(да|ага|давай|конечно)$' -and $match.Cost -le $Level.ask) {
                Write-Line ("Подтверждено: «{0}»" -f $pending.phrase)
                $matcher.Add($pending.phrase, 'quick', 'live', $pending.features) | Out-Null
                $templates = @($templates) + (New-Template $pending.phrase 'quick' $pending.features 'live')
                $dirty = $true
                if (-not $Listen) {
                    $result = Invoke-Heard $pending.phrase
                    Say $result.say
                    $muteUntil = (Get-Date).AddSeconds(2)
                }
                $pending = $null
                continue
            }
            $pending = $null
        }

        # ------------------------------------------------ свободная речь --
        # Короткая фраза, уверенно совпавшая с эталоном, идёт быстрым путём
        # ниже — Whisper ей не нужен. Всё остальное, сказанное близко к
        # микрофону, распознаётся целиком: не «Клауд, …» — значит, не нам.
        if (-not $Script:AsrWarmed -and $loud) {
            # Первая громкая фраза за запуск — будим распознавание заранее
            $Script:AsrWarmed = $true
            try { Invoke-RestMethod -Uri "$HubBase/hub/asr/warm" -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 3 | Out-Null } catch { }
        }
        $sure = $match.Cost -le $Level.run -and $match.Gap -ge $Level.gap -and $said.Seconds -lt 1.6
        $awaiting = (Get-Date) -lt $awaitUntil
        if (($loud -or $awaiting) -and -not $sure -and -not $Listen -and $said.Seconds -ge 0.5) {
            $heard = Invoke-Asr $said.Samples
            if ($heard -and $heard.text) {
                if ($heard.wake -and -not $heard.command) {
                    # «Клауд» и пауза — ждём саму команду следующей фразой
                    Write-Line ("ОБРАЩЕНИЕ | «{0}» — жду команду" -f $heard.text)
                    $awaitUntil = (Get-Date).AddSeconds(8)
                    Say 'Слушаю.'
                    $muteUntil = (Get-Date).AddSeconds(1)
                    continue
                }
                # Без имени выполняем то, что звучит как команда компьютеру
                # («открой папку Секрет») и сказано близко к микрофону.
                $imperative = [bool]$heard.imperative -and $loud
                if ($heard.wake -or $awaiting -or $imperative) {
                    $command = if ($heard.wake) { [string]$heard.command } else { [string]$heard.plain }
                    if (-not $command) { continue }
                    $awaitUntil = [datetime]::MinValue
                    $lastText = $command; $lastAt = Get-Date
                    $mic.Flush()
                    $say = Invoke-Free $command
                    if ($say) { Say $say; $muteUntil = (Get-Date).AddSeconds([Math]::Min(8, 1 + $say.Length / 14)) }
                    $mic.Flush()
                    continue
                }
                # Эталон нашёлся, но Whisper расслышал другое — значит, эталон
                # совпал случайно (фильм, разговор рядом). Не выполняем.
                $plainHeard = ($heard.text.ToLower() -replace 'ё', 'е' -replace '[^a-zа-я0-9 ]', '').Trim()
                if ($plainHeard -notlike ('*' + $match.Phrase + '*')) {
                    Write-Line ("не нам (нет обращения) | «{0}» | ближайший эталон «{1}»" -f $heard.text, $match.Phrase)
                    continue
                }
            }
        }

        if ($match.Cost -le $Level.run -and $match.Gap -ge $Level.gap -and $loud) {
            if ($match.Phrase -eq $lastText -and ((Get-Date) - $lastAt).TotalSeconds -lt 4) {
                Write-Line ("повтор, пропускаю | {0}" -f $note)
                continue
            }
            $lastText = $match.Phrase; $lastAt = Get-Date
            Write-Line ("ВЫПОЛНЯЮ | {0}" -f $note)

            # Команда уходит агенту раньше, чем помощник на ней учится.
            # Построение эталона — это округление полутора сотен кадров по
            # тринадцать чисел, около тридцати миллисекунд PowerShell, и
            # раньше они ложились ровно между «услышал» и «сделал». Обучение
            # подождёт; человек, сказавший «стоп», ждать не должен.
            if (-not $Listen) {
                $mic.Flush()
                $result = Invoke-Heard $match.Phrase
                if ($result.say) { Say $result.say; $muteUntil = (Get-Date).AddSeconds(2) }
                $mic.Flush()
            }

            # Живой эталон копится и на уверенных срабатываниях, не только на
            # подтверждённых: это тот же голос в той же комнате, и лучшего
            # материала для обучения взять неоткуда. Но только если совпадение
            # было с синтетическим эталоном — иначе набор рос бы бесконечно.
            if ($match.Source -eq 'synth') {
                $matcher.Add($match.Phrase, 'quick', 'live', $features) | Out-Null
                $templates = @($templates) + (New-Template $match.Phrase 'quick' $features 'live')
                $dirty = $true
            }
            continue
        }

        if ($match.Cost -le $Level.ask -and $loud) {
            Write-Line ("ПЕРЕСПРАШИВАЮ | {0}" -f $note)
            $pending = @{ phrase = $match.Phrase; features = $features }
            $pendingUntil = (Get-Date).AddSeconds(6)
            if (-not $Listen) {
                Say ("{0}?" -f $match.Phrase)
                $muteUntil = (Get-Date).AddSeconds(2)
            }
            continue
        }

        $why = if (-not $loud) { 'слишком тихо или далеко' } else { 'не похоже ни на одну команду' }
        Write-Line ("мимо ({0}) | {1}" -f $why, $note)

    }
} finally {
    if ($dirty) { try { Save-Vocabulary $templates $phrases } catch { } }
    $mic.Stop()
    Write-State @{ on = $false }
    Write-Line 'Уши закрыты.'
}
