<#
    Cloud HDR Companion — спутник агента: быстрая строка, руки и память о
    привычках.

    Делает четыре вещи, и все четыре невозможны внутри agent.ps1.

    ГОРЯЧАЯ КЛАВИША. Агент занят навсегда: его цикл висит на AcceptTcpClient и
    ждёт соединения. Глобальная клавиша требует обратного — очереди сообщений
    Windows, которую надо непрерывно разбирать. Два бесконечных ожидания в одном
    потоке не живут, поэтому спутник — отдельный процесс.

    БЫСТРАЯ СТРОКА. Панель написана на WPF, а не на HTML, и это осознанный
    размен. Окно Chrome открывается почти секунду, приносит с собой заголовок с
    крестиком и не умеет быть полупрозрачным — для строки, которую вызывают
    десятки раз в день, это никуда не годится. Окно WPF создаётся один раз при
    запуске и дальше только показывается: от нажатия до курсора в поле проходит
    один кадр. Скруглённые углы, тень, прозрачность и анимации достаются даром.

    РУКИ. Слово «curs» в строке открывает разрешение на управление мышью, и с
    этой минуты помощник умеет то же, что и человек: водить курсор, нажимать,
    крутить колесо, печатать. Движок лежит в cursor-core.ps1 и подключается сюда
    точкой — без служб, портов и сетевых запросов: панель и руки живут в одном
    процессе, и между ними нет ничего, что могло бы отвалиться.

    Задача выполняется в отдельном пространстве того же процесса. Это
    обязательно: «открой Paint и нарисуй логотип» идёт минуту, и всю эту минуту
    поток панели должен рисовать шаги, а не ждать.

    УЧЁТ АКТИВНОСТИ. Раз в несколько секунд спутник смотрит, какое окно сейчас
    впереди, и складывает время по программам в logs/usage.json. Из этого потом
    получается ответ на «мне скучно»: помощник знает, чем хозяин обычно занят и
    в какое время суток.

    Ничего из этого наружу не уходит. Файл со статистикой лежит рядом с логами
    агента, читает его только агент.

        powershell -NoProfile -ExecutionPolicy Bypass -File companion.ps1
#>

param(
    [switch]$NoTracker    # только горячая клавиша, без учёта активности
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$Root      = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Config    = Get-Content (Join-Path $Root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Port      = if ($Config.port) { [int]$Config.port } else { 4477 }
$Base      = "http://127.0.0.1:$Port"
$UsageFile = Join-Path $Root 'logs\usage.json'

$Quick = $Config.quickBar
$HotkeyText = if ($Quick -and $Quick.hotkey) { [string]$Quick.hotkey } else { 'Ctrl+Alt+P' }
$Side       = if ($Quick -and $Quick.side)   { [string]$Quick.side }   else { 'right' }

$CursorFile     = Join-Path $Root 'cursor-core.ps1'
$CursorSettings = $Config.cursor
$GrantMinutes   = if ($CursorSettings -and $CursorSettings.grantMinutes) { [int]$CursorSettings.grantMinutes } else { 30 }
$MaxTaskMinutes = if ($CursorSettings -and $CursorSettings.maxTaskMinutes) { [int]$CursorSettings.maxTaskMinutes } else { 6 }
$StateFile      = Join-Path $Root 'logs\cursor-state.json'
$InboxFile      = Join-Path $Root 'logs\cursor-inbox.json'

<#
    Движок рук подключается ДО создания окна, и порядок здесь принципиален.

    Внутри он объявляет процесс осведомлённым о масштабе экрана
    (SetProcessDPIAware). Сделать это после запуска WPF нельзя: окно уже
    посчитало свои размеры по старым правилам, и панель поехала бы — на экране
    со 125% масштабом она стала бы меньше и размылась. До создания окна тот же
    вызов совершенно безобиден.

    Отсутствие файла не считается бедой: без него панель работает как раньше,
    просто «curs» отвечает, что рук нет.
#>
$Script:CursorReady = $false
if (Test-Path -LiteralPath $CursorFile) {
    try {
        . $CursorFile
        $Script:CursorReady = $true
    } catch {
        $Script:CursorReady = $false
    }
}

<#
    Умный поиск по обведённой области — Ctrl+Shift+F.

    Подключается здесь же и по тем же правилам: нет файла — нет возможности, всё
    остальное работает как прежде. Внутри только чтение экрана и разбор, ничего
    из того, что меняло бы поведение панели.
#>
$LensFile = Join-Path $Root 'lens.ps1'
$LensHotkeyText = if ($Quick -and $Quick.lensHotkey) { [string]$Quick.lensHotkey } else { 'Ctrl+Shift+F' }

$Script:LensReady = $false
if (Test-Path -LiteralPath $LensFile) {
    try {
        . $LensFile
        $Script:LensReady = $true
    } catch {
        $Script:LensReady = $false
    }
}

<#
    Мгновенный перевод по обведённой области — Ctrl+Alt+T.

    Опирается на lens.ps1 (обводка, снимок, OCR), поэтому без него недоступен.
#>
$TranslateFile = Join-Path $Root 'translate.ps1'
$TranslateHotkeyText = if ($Quick -and $Quick.PSObject.Properties['translateHotkey'] -and $Quick.translateHotkey) {
    [string]$Quick.translateHotkey } else { 'Ctrl+Alt+T' }

$Script:TranslateReady = $false
if ($Script:LensReady -and (Test-Path -LiteralPath $TranslateFile)) {
    try {
        . $TranslateFile
        $Script:TranslateReady = $true
    } catch {
        $Script:TranslateReady = $false
    }
}

# Журнал пишется в файл, а не только в консоль: спутника запускает launch.ps1 со
# скрытым окном, и без файла любая его жалоба уходила бы в никуда.
$LogFile = Join-Path $Root 'logs\companion.log'

function Write-Line([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    Write-Host $line
    try {
        $directory = Split-Path -Parent $LogFile
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch { }
}

# ------------------------------------------------------------- Win32 ---------

Add-Type -Namespace CloudHdrCompanion -Name Native -MemberDefinition @'
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int lpdwProcessId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, System.Text.StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUT plii);
    [DllImport("kernel32.dll")] public static extern uint GetTickCount();

    public struct LASTINPUT { public uint cbSize; public uint dwTime; }

    public static string TitleOf(IntPtr hWnd) {
        var buffer = new System.Text.StringBuilder(512);
        GetWindowTextW(hWnd, buffer, buffer.Capacity);
        return buffer.ToString();
    }

    public static uint IdleSeconds() {
        LASTINPUT info = new LASTINPUT();
        info.cbSize = (uint)System.Runtime.InteropServices.Marshal.SizeOf(info);
        if (!GetLastInputInfo(ref info)) return 0;
        return (GetTickCount() - info.dwTime) / 1000;
    }
'@

# --------------------------------------------------- разбор горячей клавиши --
#
# Модификаторы Windows: Alt=1, Ctrl=2, Shift=4, Win=8. К ним добавляется
# NOREPEAT (0x4000) — без него зажатая клавиша сыпет событиями подряд, и панель
# начинает моргать вместо того, чтобы открыться один раз.

function ConvertTo-Hotkey([string]$text) {
    $modifiers = 0x4000
    $key = 0
    foreach ($part in ($text -split '\+')) {
        switch -Regex ($part.Trim()) {
            '^(ctrl|control)$' { $modifiers = $modifiers -bor 2; continue }
            '^alt$'            { $modifiers = $modifiers -bor 1; continue }
            '^shift$'          { $modifiers = $modifiers -bor 4; continue }
            '^(win|windows)$'  { $modifiers = $modifiers -bor 8; continue }
            '^space$'          { $key = 0x20; continue }
            '^f([1-9]|1[0-2])$' { $key = 0x6F + [int]$Matches[1]; continue }
            # Только латиница и цифры: код клавиши у RegisterHotKey — это код
            # физической кнопки, и у русской буквы его попросту нет. «Ctrl+Alt+З»
            # молча не зарегистрировалось бы, а человек ждал бы панель.
            '^[a-z0-9]$'       { $key = [int][char]$part.Trim().ToUpper()[0]; continue }
        }
    }
    return @{ modifiers = $modifiers; key = $key }
}

# ------------------------------------------------------ учёт активности ------
#
# Что считается «занятием». Секунды идут только тому окну, которое сейчас
# впереди, и только пока человек за компьютером: если пять минут не было ни
# одного нажатия и движения мыши, время не начисляется никому. Иначе всякая
# программа, оставленная открытой на ночь, оказывалась бы любимой.

$Script:Usage = @{}
$Script:UsageDirty = $false
$Script:LastKey = $null
$Script:LastSwitch = [DateTime]::MinValue

#: Окна, которые в статистике только мешают: оболочка Windows, системные
#: всплывашки и сам Cloud HDR (иначе помощник посоветует открыть самого себя).
$SkipProcesses = @('shellexperiencehost', 'startmenuexperiencehost', 'searchhost',
                   'searchapp', 'textinputhost', 'applicationframehost', 'lockapp',
                   'systemsettings', 'sihost', 'dwm', 'taskmgr', 'openwith',
                   'powershell', 'pwsh', 'conhost', 'windowsterminal', 'cmd')

function Import-Usage {
    if (-not (Test-Path -LiteralPath $UsageFile)) { return }
    try {
        $saved = Get-Content -LiteralPath $UsageFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $saved.apps.PSObject.Properties) {
            $value = $property.Value
            $hours = @(0) * 24
            if ($value.hours) {
                for ($i = 0; $i -lt 24 -and $i -lt @($value.hours).Count; $i++) { $hours[$i] = [int]$value.hours[$i] }
            }
            $Script:Usage[$property.Name] = @{
                name = [string]$value.name; path = [string]$value.path
                seconds = [int]$value.seconds; sessions = [int]$value.sessions
                last = [string]$value.last; hours = $hours
            }
        }
        Write-Line ("Статистика загружена: программ — {0}" -f $Script:Usage.Count)
    } catch {
        Write-Line "Файл статистики повреждён — начинаю заново"
        $Script:Usage = @{}
    }
}

function Save-Usage {
    if (-not $Script:UsageDirty) { return }
    try {
        $directory = Split-Path -Parent $UsageFile
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

        $apps = [ordered]@{}
        foreach ($key in ($Script:Usage.Keys | Sort-Object { -$Script:Usage[$_].seconds })) {
            $apps[$key] = $Script:Usage[$key]
        }
        $payload = [ordered]@{
            version = 1
            updated = (Get-Date).ToString('s')
            apps = $apps
        } | ConvertTo-Json -Depth 6 -Compress

        # Пишем через временный файл: агент читает этот же файл в любой момент, и
        # ему не должна достаться половина записи.
        $temporary = $UsageFile + '.tmp'
        [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $UsageFile -Force
        $Script:UsageDirty = $false
    } catch {
        Write-Line ("Не удалось сохранить статистику: {0}" -f $_.Exception.Message)
    }
}

function Update-Usage([int]$seconds) {
    if ([CloudHdrCompanion.Native]::IdleSeconds() -gt 300) { $Script:LastKey = $null; return }

    $handle = [CloudHdrCompanion.Native]::GetForegroundWindow()
    if ($handle -eq [IntPtr]::Zero) { return }

    $title = [CloudHdrCompanion.Native]::TitleOf($handle)
    if (-not $title) { return }                       # рабочий стол, всплывашки

    $processId = 0
    [void][CloudHdrCompanion.Native]::GetWindowThreadProcessId($handle, [ref]$processId)
    if ($processId -le 0 -or $processId -eq $PID) { return }

    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) { return }

    $key = $process.ProcessName.ToLower()
    if ($SkipProcesses -contains $key) { return }
    if ($title -like 'Cloud HDR*') { return }         # своё же окно

    $path = ''
    try { $path = $process.Path } catch { }

    if (-not $Script:Usage.ContainsKey($key)) {
        # Имя для человека берём из свойств файла, но не всякое: у стандартного
        # «Блокнота» там записано «Notepad.exe», и такая подпись в списке
        # любимых программ выглядит хуже, чем просто «Notepad».
        $friendly = $process.ProcessName
        if ($friendly.Length -gt 1) { $friendly = $friendly.Substring(0, 1).ToUpper() + $friendly.Substring(1) }
        if ($path) {
            try {
                $description = [Diagnostics.FileVersionInfo]::GetVersionInfo($path).FileDescription
                if ($description -and $description.Trim() -and $description -notmatch '\.exe\s*$') {
                    $friendly = $description.Trim()
                }
            } catch { }
        }
        $Script:Usage[$key] = @{ name = $friendly; path = $path; seconds = 0
                                 sessions = 0; last = ''; hours = @(0) * 24 }
    }

    $entry = $Script:Usage[$key]
    if ($path -and -not $entry.path) { $entry.path = $path }
    $entry.seconds += $seconds
    $entry.hours[[int](Get-Date).Hour] += $seconds
    $entry.last = (Get-Date).ToString('s')

    # Новый заход считается, только если человек уходил из программы больше чем
    # на минуту: иначе переключение туда-обратно между двумя окнами накрутило бы
    # сотни «запусков» за вечер.
    if ($Script:LastKey -ne $key -and ((Get-Date) - $Script:LastSwitch).TotalSeconds -gt 60) {
        $entry.sessions += 1
        $Script:LastSwitch = Get-Date
    }
    $Script:LastKey = $key
    $Script:UsageDirty = $true
}

# ------------------------------------------------------- запрос к агенту -----
#
# Запрос уходит в отдельное пространство выполнения, а не прямо отсюда. Причина
# простая: ответ модели идёт секунды, а Invoke-RestMethod блокирует поток. Тот
# же поток рисует окно — значит, на всё время ожидания панель замерла бы:
# ни анимации, ни возможности напечатать что-то ещё, ни закрыть по Escape.

$AskScript = {
    param($base, $token, $text)

    function Invoke-Quick($base, $token, $text) {
        $json = @{ text = $text } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($json)
        $response = Invoke-WebRequest -Uri "$base/api/quick" -Method Post -Body $bytes `
            -ContentType 'application/json; charset=utf-8' `
            -Headers @{ 'X-Cloud-Token' = $token } -TimeoutSec 90 -UseBasicParsing
        return ([Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json)
    }

    function Read-Json($uri, [string]$method = 'Get', $bytes = $null, [int]$timeout = 20) {
        <#
            Запрос с честным разбором UTF-8.

            Invoke-RestMethod здесь не годится, и это стоило одного испорченного
            ответа: PowerShell 5.1 смотрит на charset в Content-Type, а FastAPI
            отдаёт просто application/json — без кодировки. Не найдя её,
            PowerShell берёт ISO-8859-1, и русский ответ модели приходит в виде
            «ÐÐ¾Ð¼Ð¿ÑÑÑÐµÑÐ½Ð°Ñ Ð¸Ð³ÑÐ°». Байты при этом целы — беда
            только в том, кто как их прочёл. Поэтому читаем их сами.
        #>
        $arguments = @{ Uri = $uri; Method = $method; TimeoutSec = $timeout; UseBasicParsing = $true }
        if ($bytes) {
            $arguments.Body = $bytes
            $arguments.ContentType = 'application/json; charset=utf-8'
        }
        $response = Invoke-WebRequest @arguments
        $raw = $response.RawContentStream.ToArray()
        if (-not $raw -or $raw.Length -eq 0) { return $null }
        return ([Text.Encoding]::UTF8.GetString($raw) | ConvertFrom-Json)
    }

    function Invoke-Model($text) {
        <#
            Вопрос к своей модели — напрямую, минуя агента.

            Через агента было бы короче, но нельзя: он однопоточный, и пока идёт
            генерация (а это секунды), он не ответил бы больше никому — ни окну
            приложения, ни следующему нажатию горячей клавиши.

            Порты перебираются те же, что у public/js/llm.js: 8080 — своя модель
            Cloud HDR, остальные два оставлены на случай, если человек поднял
            Ollama или LM Studio и хочет разговаривать с ними.
        #>
        foreach ($port in @(8080, 11434, 1234)) {
            $host_ = "http://127.0.0.1:$port"
            try {
                $models = Read-Json "$host_/v1/models" 'Get' $null 2
            } catch { continue }

            $name = 'cloud-hdr-3.2'
            if ($models.data -and @($models.data).Count -gt 0) { $name = @($models.data)[0].id }

            $payload = @{
                model = $name
                messages = @(@{ role = 'user'; content = $text })
                stream = $false; max_tokens = 220; temperature = 0.7
            } | ConvertTo-Json -Depth 5 -Compress

            $reply = Read-Json "$host_/v1/chat/completions" 'Post' ([Text.Encoding]::UTF8.GetBytes($payload)) 120

            $said = ''
            if ($reply.choices) { $said = [string]@($reply.choices)[0].message.content }
            if ($said.Trim()) { return $said.Trim() }
        }
        return $null
    }

    try {
        if (-not $token) {
            $grant = Invoke-RestMethod -Uri "$base/api/session/grant" -Method Post -Body '{}' `
                -ContentType 'application/json' -TimeoutSec 10
            $token = $grant.token
        }
        try {
            $answer = Invoke-Quick $base $token $text
        } catch {
            # Агента перезапустили — старый пропуск больше не действует.
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) {
                $grant = Invoke-RestMethod -Uri "$base/api/session/grant" -Method Post -Body '{}' `
                    -ContentType 'application/json' -TimeoutSec 10
                $token = $grant.token
                $answer = Invoke-Quick $base $token $text
            } else { throw }
        }
        # kind='model' — агент команды не увидел и передаёт вопрос дальше.
        if ([string]$answer.kind -eq 'model') {
            $said = Invoke-Model $text
            if (-not $said) {
                return @{ ok = $false; token = $token; kind = 'error'
                          say = 'Модель не запущена — откройте приложение ярлыком, она поднимется вместе с ним.' }
            }
            return @{ ok = $true; token = $token; kind = 'answer'; say = $said }
        }

        return @{ ok = $true; token = $token; say = [string]$answer.say
                  kind = [string]$answer.kind; title = [string]$answer.title }
    } catch {
        $message = $_.Exception.Message
        if ($message -match 'Unable to connect|не удалось') {
            $message = 'Агент Cloud HDR не отвечает. Запустите приложение ярлыком.'
        }
        return @{ ok = $false; say = $message }
    }
}

$Script:Pool = [RunspaceFactory]::CreateRunspacePool(1, 2)
$Script:Pool.Open()
$Script:Pending = $null
$Script:Token = ''

# ------------------------------------------------------------- окно ----------

$Xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Cloud HDR — быстрый ввод"
        Width="470" SizeToContent="Height"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        ShowInTaskbar="False" Topmost="True" ResizeMode="NoResize"
        FontFamily="Segoe UI">

  <Window.Resources>
    <!-- Поле ввода без рамки: рамку рисует Border снаружи, он же светится при фокусе. -->
    <Style x:Key="Bare" TargetType="TextBox">
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Foreground" Value="#FCF1E8"/>
      <Setter Property="CaretBrush" Value="#FBA35F"/>
      <Setter Property="SelectionBrush" Value="#FBA35F"/>
      <Setter Property="FontSize" Value="15"/>
      <Setter Property="VerticalContentAlignment" Value="Center"/>
    </Style>

    <!-- Кнопки.

         Своя разметка, а не системная: у стандартной кнопки WPF серая рамка и
         подложка из Windows 7, и на тёмном стекле она выглядит вставленной из
         другого приложения. Здесь остаётся только то, что нужно: скруглённый
         прямоугольник, который отзывается на наведение и нажатие.

         Отзыв на наведение обязателен. Кнопка «Разрешить управление мышью» —
         самая ответственная в приложении, и она должна вести себя как кнопка:
         подсветиться под курсором и просесть под нажатием. Неподвижный
         прямоугольник читается как надпись, а надписи не нажимают. -->
    <Style x:Key="Solid" TargetType="Button">
      <Setter Property="Foreground" Value="#1E1209"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Padding" Value="14,9"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Skin" CornerRadius="11" Padding="{TemplateBinding Padding}">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
                  <GradientStop Color="#FFD79A" Offset="0"/>
                  <GradientStop Color="#F0A93C" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <Border.Effect>
                <DropShadowEffect BlurRadius="18" ShadowDepth="0" Opacity="0.45" Color="#F0A93C"/>
              </Border.Effect>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Skin" Property="Opacity" Value="0.88"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="Skin" Property="RenderTransform">
                  <Setter.Value><ScaleTransform ScaleX="0.97" ScaleY="0.94"/></Setter.Value>
                </Setter>
                <Setter TargetName="Skin" Property="RenderTransformOrigin" Value="0.5,0.5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="#BFA490"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Padding" Value="13,9"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Skin" CornerRadius="11" Background="#18110C"
                    BorderBrush="#392A1E" BorderThickness="1" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Skin" Property="Background" Value="#281D14"/>
                <Setter TargetName="Skin" Property="BorderBrush" Value="#4F3C2D"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Grid x:Name="Shell" Margin="26,20,26,26" RenderTransformOrigin="1,0.5">
    <Grid.RenderTransform>
      <TranslateTransform x:Name="Slide" X="70"/>
    </Grid.RenderTransform>

    <!-- мягкое свечение под панелью -->
    <Border CornerRadius="24" Margin="6,8,6,4" Background="#FBA35F" Opacity="0.11">
      <Border.Effect><BlurEffect Radius="48"/></Border.Effect>
    </Border>

    <Border x:Name="Card" CornerRadius="22" BorderThickness="1">
      <!-- Рамка не одного цвета, а градиентом: сверху светлее, снизу темнее.
           Так карточка читается как освещённая сверху — тем же светом, что и
           всё вокруг, — и перестаёт выглядеть наклейкой поверх экрана.

           Стекло держится почти чёрным. Панель приходит поверх чужого окна, и
           чем темнее подложка, тем меньше она спорит с тем, что под ней: свет
           остаётся только там, где что-то происходит — на сфере и на линии
           голоса. Раньше карточка была синевато-серой и сама светилась ровно
           настолько, чтобы перетягивать взгляд на пустое место. -->
      <Border.BorderBrush>
        <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
          <GradientStop Color="#573F2D" Offset="0"/>
          <GradientStop Color="#2F2117" Offset="0.45"/>
          <GradientStop Color="#18110C" Offset="1"/>
        </LinearGradientBrush>
      </Border.BorderBrush>
      <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
          <GradientStop Color="#FA0C0805" Offset="0"/>
          <GradientStop Color="#FA140D08" Offset="1"/>
        </LinearGradientBrush>
      </Border.Background>
      <Border.Effect>
        <DropShadowEffect BlurRadius="44" ShadowDepth="12" Direction="270" Opacity="0.72" Color="#000000"/>
      </Border.Effect>

      <Grid>

        <!-- Северное сияние под содержимым.

             Два цветных пятна, которые очень медленно переползают внутри
             карточки. Смысл не в украшении: чёрное стекло без единого движения
             выглядит выключенным, а panель вызывают на секунду и она обязана
             сразу читаться как живая. Движение здесь на грани заметности — в
             минуту меньше одного прохода, — и именно поэтому оно не отвлекает,
             а на глаз ощущается как подсветка, а не как анимация.

             Пятна лежат в Border со скруглением, а не в отдельном слое: фон
             Border обрезается его же углами, и свет не вылезает за карточку.
             Слой с размытием этого не умеет и оставлял бы ореол снаружи. -->
        <Border CornerRadius="22">
          <Border.Background>
            <RadialGradientBrush x:Name="AuroraA" Center="0.22,0.12" GradientOrigin="0.22,0.12"
                                 RadiusX="0.7" RadiusY="0.95">
              <GradientStop Color="#2EFBA35F" Offset="0"/>
              <GradientStop Color="#00FBA35F" Offset="1"/>
            </RadialGradientBrush>
          </Border.Background>
          <Border.Triggers>
            <EventTrigger RoutedEvent="FrameworkElement.Loaded">
              <BeginStoryboard>
                <Storyboard>
                  <PointAnimation Storyboard.TargetName="AuroraA" Storyboard.TargetProperty="Center"
                                  From="0.18,0.08" To="0.66,0.34" Duration="0:0:19"
                                  AutoReverse="True" RepeatBehavior="Forever"/>
                  <PointAnimation Storyboard.TargetName="AuroraA" Storyboard.TargetProperty="GradientOrigin"
                                  From="0.18,0.08" To="0.66,0.34" Duration="0:0:19"
                                  AutoReverse="True" RepeatBehavior="Forever"/>
                </Storyboard>
              </BeginStoryboard>
            </EventTrigger>
          </Border.Triggers>
        </Border>

        <Border CornerRadius="22">
          <Border.Background>
            <RadialGradientBrush x:Name="AuroraB" Center="0.86,0.9" GradientOrigin="0.86,0.9"
                                 RadiusX="0.75" RadiusY="0.9">
              <GradientStop Color="#26E97B27" Offset="0"/>
              <GradientStop Color="#00E97B27" Offset="1"/>
            </RadialGradientBrush>
          </Border.Background>
          <Border.Triggers>
            <EventTrigger RoutedEvent="FrameworkElement.Loaded">
              <BeginStoryboard>
                <Storyboard>
                  <PointAnimation Storyboard.TargetName="AuroraB" Storyboard.TargetProperty="Center"
                                  From="0.9,0.95" To="0.42,0.62" Duration="0:0:27"
                                  AutoReverse="True" RepeatBehavior="Forever"/>
                  <PointAnimation Storyboard.TargetName="AuroraB" Storyboard.TargetProperty="GradientOrigin"
                                  From="0.9,0.95" To="0.42,0.62" Duration="0:0:27"
                                  AutoReverse="True" RepeatBehavior="Forever"/>
                </Storyboard>
              </BeginStoryboard>
            </EventTrigger>
          </Border.Triggers>
        </Border>

        <!-- Блик по верхней кромке: тонкая светлая линия, гаснущая к краям.
             Та же уловка, что и на сфере, — она одна превращает плоский
             прямоугольник в предмет, на который падает свет сверху. -->
        <Border Height="1" VerticalAlignment="Top" Margin="16,0,16,0">
          <Border.Background>
            <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
              <GradientStop Color="#00FFFFFF" Offset="0"/>
              <GradientStop Color="#33FEE1CA" Offset="0.35"/>
              <GradientStop Color="#3DFEEBDD" Offset="0.55"/>
              <GradientStop Color="#00FFFFFF" Offset="1"/>
            </LinearGradientBrush>
          </Border.Background>
        </Border>

        <StackPanel Margin="18,16,18,18">

        <!-- маскот и шапка -->
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>

          <!-- Маскот.

               Был шар с глазами. Глаза обещали больше, чем панель может дать:
               мимика намекает на характер, а характера у быстрой строки нет —
               она инструмент, а не собеседник, и любое выражение «лица»
               оказывалось невпопад. На их месте теперь чистый свет, а живость
               держится на движении.

               Из того, что осталось после глаз, убрано ещё одно: пунктирное
               кольцо, которое крутилось всегда. Вечное вращение — обещание
               работы, которой нет, и глаз перестаёт его замечать за минуту.
               Теперь в покое сфера почти неподвижна: дышит ореол да изредка
               уходит волна. Кольцо появляется только пока идёт ответ — и
               именно поэтому его видно.

               Сама сфера собрана как стекло, а не как плоский кружок: свет
               падает сверху слева, у края идёт тонкая светлая кромка, снизу
               тело уходит почти в чёрное. Пульс ядра задаёт код — по словам
               ответа, — поэтому анимации на нём в разметке нет. -->
          <Grid x:Name="MascotBox" Width="54" Height="54" VerticalAlignment="Center">

            <!-- ореол: единственное, что дышит в покое -->
            <Ellipse Width="54" Height="54" Opacity="0.26">
              <Ellipse.Fill>
                <RadialGradientBrush>
                  <GradientStop Color="#FBAD72" Offset="0"/>
                  <GradientStop Color="#00FBA35F" Offset="1"/>
                </RadialGradientBrush>
              </Ellipse.Fill>
              <Ellipse.Effect><BlurEffect Radius="14"/></Ellipse.Effect>
              <Ellipse.Triggers>
                <EventTrigger RoutedEvent="FrameworkElement.Loaded">
                  <BeginStoryboard>
                    <Storyboard>
                      <DoubleAnimation Storyboard.TargetProperty="Opacity" From="0.17" To="0.46"
                                       Duration="0:0:3.4" AutoReverse="True" RepeatBehavior="Forever">
                        <DoubleAnimation.EasingFunction><SineEase EasingMode="EaseInOut"/></DoubleAnimation.EasingFunction>
                      </DoubleAnimation>
                    </Storyboard>
                  </BeginStoryboard>
                </EventTrigger>
              </Ellipse.Triggers>
            </Ellipse>

            <!-- Волна, уходящая от ядра. Раньше уходила каждые три секунды и
                 читалась как пульс прибора; теперь реже и тише — как круг по
                 воде, а не как индикатор. -->
            <Ellipse x:Name="Ripple" Width="32" Height="32" StrokeThickness="1.1" Opacity="0"
                     Stroke="#FBAE73"
                     HorizontalAlignment="Center" VerticalAlignment="Center" RenderTransformOrigin="0.5,0.5">
              <!-- Размытие обязательно. Резкая окружность в чёрном поле читается
                   как нарисованное кольцо, а не как разошедшийся круг по воде. -->
              <Ellipse.Effect><BlurEffect Radius="2.5"/></Ellipse.Effect>
              <Ellipse.RenderTransform><ScaleTransform x:Name="RippleScale" ScaleX="0.9" ScaleY="0.9"/></Ellipse.RenderTransform>
              <Ellipse.Triggers>
                <EventTrigger RoutedEvent="FrameworkElement.Loaded">
                  <BeginStoryboard>
                    <Storyboard Duration="0:0:5.4" RepeatBehavior="Forever">
                      <DoubleAnimation Storyboard.TargetName="RippleScale" Storyboard.TargetProperty="ScaleX"
                                       From="0.9" To="1.68" Duration="0:0:3.0">
                        <DoubleAnimation.EasingFunction><CubicEase EasingMode="EaseOut"/></DoubleAnimation.EasingFunction>
                      </DoubleAnimation>
                      <DoubleAnimation Storyboard.TargetName="RippleScale" Storyboard.TargetProperty="ScaleY"
                                       From="0.9" To="1.68" Duration="0:0:3.0">
                        <DoubleAnimation.EasingFunction><CubicEase EasingMode="EaseOut"/></DoubleAnimation.EasingFunction>
                      </DoubleAnimation>
                      <DoubleAnimationUsingKeyFrames Storyboard.TargetProperty="Opacity" Duration="0:0:5.4">
                        <LinearDoubleKeyFrame Value="0" KeyTime="0:0:0"/>
                        <LinearDoubleKeyFrame Value="0.22" KeyTime="0:0:0.7"/>
                        <LinearDoubleKeyFrame Value="0" KeyTime="0:0:3.0"/>
                        <LinearDoubleKeyFrame Value="0" KeyTime="0:0:5.4"/>
                      </DoubleAnimationUsingKeyFrames>
                    </Storyboard>
                  </BeginStoryboard>
                </EventTrigger>
              </Ellipse.Triggers>
            </Ellipse>

            <!-- Свечение вокруг ядра. Яркость ему задаёт код — по той же
                 громкости, что качает линию под строкой, — поэтому анимации
                 на нём нет: заданная в разметке, она бы всё перебивала. -->
            <Ellipse x:Name="CoreGlow" Width="36" Height="36" Opacity="0.16"
                     HorizontalAlignment="Center" VerticalAlignment="Center">
              <Ellipse.Fill>
                <RadialGradientBrush>
                  <GradientStop Color="#FEE4D0" Offset="0"/>
                  <GradientStop Color="#00FBA35F" Offset="1"/>
                </RadialGradientBrush>
              </Ellipse.Fill>
              <Ellipse.Effect><BlurEffect Radius="11"/></Ellipse.Effect>
            </Ellipse>

            <!-- Тело сферы. Всё, что двигается вместе с ядром, лежит в одной
                 группе: масштаб пульса применяется к ней целиком, иначе блик
                 отставал бы от шара и стекло рассыпалось. -->
            <Grid Width="34" Height="34" HorizontalAlignment="Center" VerticalAlignment="Center"
                  RenderTransformOrigin="0.5,0.5">
              <Grid.RenderTransform><ScaleTransform x:Name="CorePulse" ScaleX="1" ScaleY="1"/></Grid.RenderTransform>

              <!-- Тело.
                   Тёмное стекло, а не глянцевый шарик. Раньше в градиенте
                   стоял белый прямо в точке света, и он расплывался мягким
                   пятном на треть сферы — получалась кнопка из нулевых. Теперь
                   в самом градиенте белого нет вовсе: он только притемняется к
                   краю, а свет дают отдельные слои поверх — точечный блик и
                   две кромки. Тёмное тело и мелкий резкий блик — это и есть
                   разница между стеклом и пластиком. -->
              <Ellipse x:Name="Core" Width="30" Height="30"
                       HorizontalAlignment="Center" VerticalAlignment="Center">
                <Ellipse.Fill>
                  <RadialGradientBrush GradientOrigin="0.42,0.32" Center="0.5,0.5" RadiusX="0.58" RadiusY="0.58">
                    <GradientStop Color="#C17840" Offset="0"/>
                    <GradientStop Color="#854C21" Offset="0.34"/>
                    <GradientStop Color="#472B15" Offset="0.62"/>
                    <GradientStop Color="#24160B" Offset="0.85"/>
                    <GradientStop Color="#0C0704" Offset="1"/>
                  </RadialGradientBrush>
                </Ellipse.Fill>
              </Ellipse>

              <!-- Свет внутри стекла. Горит всегда — это и есть «кто-то дома»,
                   — и разгорается на речи вместе с ореолом снаружи, поэтому
                   сфера светится изнутри, а не подсвечивается со стороны.
                   Яркость задаёт код. -->
              <Ellipse x:Name="CoreSpark" Width="17" Height="17" Opacity="0"
                       HorizontalAlignment="Center" VerticalAlignment="Center">
                <Ellipse.Fill>
                  <RadialGradientBrush>
                    <GradientStop Color="#FFFFFF" Offset="0"/>
                    <GradientStop Color="#FCC092" Offset="0.4"/>
                    <GradientStop Color="#00FB9A50" Offset="1"/>
                  </RadialGradientBrush>
                </Ellipse.Fill>
                <Ellipse.Effect><BlurEffect Radius="6"/></Ellipse.Effect>
                <Ellipse.RenderTransform><TranslateTransform X="-1.8" Y="-3.2"/></Ellipse.RenderTransform>
              </Ellipse>

              <!-- Кромка со стороны света: сверху слева светлая, к низу гаснет.
                   Тонкая и приглушённая: яркая читалась обводкой наклейки. -->
              <Ellipse Width="30" Height="30" StrokeThickness="0.8" Opacity="0.32"
                       HorizontalAlignment="Center" VerticalAlignment="Center">
                <Ellipse.Stroke>
                  <LinearGradientBrush StartPoint="0.18,0" EndPoint="0.82,1">
                    <GradientStop Color="#FDD6B9" Offset="0"/>
                    <GradientStop Color="#946541" Offset="0.3"/>
                    <GradientStop Color="#00301D0E" Offset="0.55"/>
                    <GradientStop Color="#00301D0E" Offset="1"/>
                  </LinearGradientBrush>
                </Ellipse.Stroke>
                <Ellipse.Effect><BlurEffect Radius="0.6"/></Ellipse.Effect>
              </Ellipse>

              <!-- Отражённый свет по нижней правой кромке.
                   Он и делает шар шаром. В темноте край, отвёрнутый от
                   источника, никогда не бывает совсем чёрным: в него приходит
                   свет, отражённый от всего вокруг. Без этой дуги сфера
                   выглядела откушенной снизу и читалась как полумесяц. -->
              <Ellipse Width="30" Height="30" StrokeThickness="1.4" Opacity="0.75"
                       HorizontalAlignment="Center" VerticalAlignment="Center">
                <Ellipse.Stroke>
                  <LinearGradientBrush StartPoint="0.2,0" EndPoint="0.8,1">
                    <GradientStop Color="#00C5824F" Offset="0"/>
                    <GradientStop Color="#00C5824F" Offset="0.46"/>
                    <GradientStop Color="#8CD59C71" Offset="0.78"/>
                    <GradientStop Color="#E6FDDABF" Offset="1"/>
                  </LinearGradientBrush>
                </Ellipse.Stroke>
                <Ellipse.Effect><BlurEffect Radius="1.3"/></Ellipse.Effect>
              </Ellipse>

              <!-- Блик. Мелкий и резкий: расплывчатый читается как глянец на
                   пластике, точечный — как отражение в стекле. -->
              <Ellipse Width="6.5" Height="4.6" Opacity="0.9"
                       HorizontalAlignment="Center" VerticalAlignment="Center">
                <Ellipse.Fill>
                  <RadialGradientBrush>
                    <GradientStop Color="#FFFFFF" Offset="0"/>
                    <GradientStop Color="#B3FEF2E9" Offset="0.55"/>
                    <GradientStop Color="#00FFFFFF" Offset="1"/>
                  </RadialGradientBrush>
                </Ellipse.Fill>
                <Ellipse.Effect><BlurEffect Radius="1.4"/></Ellipse.Effect>
                <Ellipse.RenderTransform><TranslateTransform X="-5.6" Y="-7.6"/></Ellipse.RenderTransform>
              </Ellipse>
            </Grid>

            <!-- Кольцо ожидания.
                 Дуга, а не пунктир: у пунктира нет начала и конца, поэтому он
                 крутится «на месте», а дуга явно ведёт линию. Лежит в Canvas,
                 потому что вращать надо вокруг центра окружности, а границы у
                 дуги свои — выравнивание в Grid увело бы ось. -->
            <Canvas Width="54" Height="54" HorizontalAlignment="Center" VerticalAlignment="Center"
                    IsHitTestVisible="False">
              <Path x:Name="ThinkRing" Opacity="0" StrokeThickness="1.5"
                    StrokeStartLineCap="Round" StrokeEndLineCap="Round">
                <Path.Stroke>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                    <GradientStop Color="#00FCB783" Offset="0"/>
                    <GradientStop Color="#FCB680" Offset="0.55"/>
                    <GradientStop Color="#FEF3EB" Offset="1"/>
                  </LinearGradientBrush>
                </Path.Stroke>
                <Path.Data>
                  <PathGeometry>
                    <PathFigure StartPoint="27,5">
                      <ArcSegment Point="43.85,41.14" Size="22,22" SweepDirection="Clockwise" IsLargeArc="False"/>
                    </PathFigure>
                  </PathGeometry>
                </Path.Data>
                <Path.RenderTransform>
                  <RotateTransform x:Name="ThinkSpin" Angle="0" CenterX="27" CenterY="27"/>
                </Path.RenderTransform>
                <Path.Triggers>
                  <EventTrigger RoutedEvent="FrameworkElement.Loaded">
                    <BeginStoryboard>
                      <Storyboard>
                        <DoubleAnimation Storyboard.TargetName="ThinkSpin" Storyboard.TargetProperty="Angle"
                                         From="0" To="360" Duration="0:0:1.4" RepeatBehavior="Forever"/>
                      </Storyboard>
                    </BeginStoryboard>
                  </EventTrigger>
                </Path.Triggers>
              </Path>
            </Canvas>
          </Grid>

          <StackPanel Grid.Column="1" Margin="14,0,0,0" VerticalAlignment="Center">
            <TextBlock Text="Cloud HDR" Foreground="#FCF1E8" FontSize="15" FontWeight="SemiBold"/>
            <TextBlock x:Name="Status" Text="Скажите, что открыть или о чём рассказать"
                       Foreground="#C7AA94" FontSize="11.5" Margin="0,2,0,0" TextTrimming="CharacterEllipsis"/>
          </StackPanel>

          <!-- Метка режима.

               В покое здесь просто напоминание о горячей клавише. Когда выдано
               разрешение управлять мышью, метка становится янтарной и
               показывает, сколько минут осталось.

               Смена цвета — не украшение, а единственное, что отличает
               «помощник слушает» от «помощник может двигать мышь». Человек
               должен видеть это состояние всегда, не вспоминая, давал он
               разрешение или нет. Янтарный выбран потому, что он единственный
               тёплый в холодной панели: его замечаешь боковым зрением. -->
          <Border x:Name="ModeChip" Grid.Column="2" CornerRadius="9" Background="#120C08" BorderBrush="#34251A"
                  BorderThickness="1" Padding="9,5" VerticalAlignment="Center">
            <StackPanel Orientation="Horizontal">
              <Ellipse x:Name="ModeDot" Width="6" Height="6" Fill="#634D3D" Margin="0,0,7,0"
                       VerticalAlignment="Center"/>
              <TextBlock x:Name="HotkeyHint" Text="Ctrl+Alt+P" Foreground="#9A806C" FontSize="10.5"/>
            </StackPanel>
          </Border>
        </Grid>

        <!-- ввод -->
        <Border x:Name="InputWrap" CornerRadius="15" Background="#0E0906" BorderBrush="#34261B"
                BorderThickness="1" Margin="0,15,0,0" Padding="15,12">
          <Grid>
            <TextBlock x:Name="Placeholder" Text="открой стим · что такое гаррис мод · я хочу отдохнуть"
                       Foreground="#775E4B" FontSize="14" VerticalAlignment="Center" IsHitTestVisible="False"/>
            <TextBox x:Name="Input" Style="{StaticResource Bare}"/>
          </Grid>
        </Border>

        <!-- Линия света под строкой — голос помощника.

             В покое это ровная светящаяся нить с бегущим по ней огоньком.
             Когда помощник думает, по нити идёт мелкая рябь; когда говорит —
             она качается вспышками разной высоты, по одной на слово ответа.
             Точки ломаной считает код (см. «линия света» ниже), поэтому здесь
             только оболочка.

             Над нитью встаёт свет. Он не нарисован отдельно: это та же
             ломаная, дотянутая вверх, — поэтому свет не «лежит поверх» линии,
             а растёт из неё и повторяет каждый изгиб.

             Слоёв у света три, и это не украшательство. Свечение узнаётся не
             по яркости, а по тому, как оно спадает: у настоящего источника
             есть раскалённая сердцевина, вокруг неё плотный ореол, а дальше
             широкое мягкое зарево. Один размытый слой, как ни крась, даёт
             ровное пятно — глаз читает его как грязь на стекле, а не как свет.
             Отсюда BeamWide (толстый и еле видный), BeamTight (у самой нити,
             ярче) и добела раскалённое ядро самой линии.

             Оба ореола — не заливки, а толстые размытые ЛОМАНЫЕ, сдвинутые
             вверх от нити. Заливкой это уже было и не получилось: градиент
             заливки привязан к высоте в окне, а нить ходит вверх-вниз, и на
             гребнях свет гас ровно там, где обязан быть самым ярким. У
             ломаной такой беды нет — она идёт по нити точка в точку, а спад
             яркости даёт размытие.

             Сдвиг вверх у каждой точки свой, по силе голоса в ней: где нить
             взлетает, свет вытягивается языком, где спокойно — стелется низко.
             Поэтому зарево не прямоугольник дымки, а что-то вроде северного
             сияния, привязанного к голосу.

             Яркость вдоль нити задаёт маска, и её код пересчитывает каждый
             кадр по силе голоса в каждом участке (см. «маска зарева»). Это
             последнее, что отличает свет от полосы дымки: обводка у ломаной
             одной толщины по всей длине, поэтому без маски зарево выходит
             ровным по всей строке — а глаз читает ровное как фон, каким бы
             мягким ни был его край. С маской свет вспыхивает там, где нить
             взлетает, и пропадает там, где она успокоилась.

             Порядок важен: свет уходит ПОД линию, иначе размытие съело бы саму
             нить. Высота полосы — 78, нить стоит на 16 от низа: свету нужно
             место вверх и почти не нужно вниз. Запас вверх щедрый намеренно —
             размытие в шестнадцать пикселей срезалось о край полосы и оставляло
             ровно ту прямую черту, от которой всё это затевалось уйти.

             Отрицательный отступ сверху убирает то, что этот запас натворил бы
             в покое: место под свет нужно всегда, а видно его только на речи, и
             без сдвига между полем ввода и спящей нитью зияла бы полоса пустоты
             под шестьдесят пикселей.

             Тёмного рельса под нитью больше нет: на большой амплитуде нить
             уходила от него, и он оставался висеть отдельной серой чертой. -->
        <Grid x:Name="WaveWrap" Height="78" Margin="2,-24,2,0" ClipToBounds="True">

          <Polyline x:Name="BeamWide" StrokeThickness="16" Opacity="0"
                    StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
            <Polyline.Stroke>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#D47832" Offset="0"/>
                <GradientStop Color="#DC8542" Offset="0.25"/>
                <GradientStop Color="#FBA35F" Offset="0.55"/>
                <GradientStop Color="#E79C62" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.Stroke>
            <!-- Прозрачность вдоль нити. Узлы стоят через равные доли строки,
                 а их альфу код меняет каждый кадр по силе голоса. Цвет здесь
                 везде непрозрачный: за то, где света нет, отвечает только эта
                 маска — иначе два затухания накладывались бы друг на друга. -->
            <Polyline.OpacityMask>
              <LinearGradientBrush x:Name="WideMask" StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#00000000" Offset="0"/>
                <GradientStop Color="#00000000" Offset="0.125"/>
                <GradientStop Color="#00000000" Offset="0.25"/>
                <GradientStop Color="#00000000" Offset="0.375"/>
                <GradientStop Color="#00000000" Offset="0.5"/>
                <GradientStop Color="#00000000" Offset="0.625"/>
                <GradientStop Color="#00000000" Offset="0.75"/>
                <GradientStop Color="#00000000" Offset="0.875"/>
                <GradientStop Color="#00000000" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.OpacityMask>
            <Polyline.Effect><BlurEffect Radius="16"/></Polyline.Effect>
          </Polyline>

          <Polyline x:Name="BeamTight" StrokeThickness="9" Opacity="0"
                    StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
            <Polyline.Stroke>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#EC914C" Offset="0"/>
                <GradientStop Color="#F19F60" Offset="0.22"/>
                <GradientStop Color="#FBAE73" Offset="0.55"/>
                <GradientStop Color="#FCB57F" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.Stroke>
            <Polyline.OpacityMask>
              <LinearGradientBrush x:Name="TightMask" StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#00000000" Offset="0"/>
                <GradientStop Color="#00000000" Offset="0.125"/>
                <GradientStop Color="#00000000" Offset="0.25"/>
                <GradientStop Color="#00000000" Offset="0.375"/>
                <GradientStop Color="#00000000" Offset="0.5"/>
                <GradientStop Color="#00000000" Offset="0.625"/>
                <GradientStop Color="#00000000" Offset="0.75"/>
                <GradientStop Color="#00000000" Offset="0.875"/>
                <GradientStop Color="#00000000" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.OpacityMask>
            <Polyline.Effect><BlurEffect Radius="6"/></Polyline.Effect>
          </Polyline>

          <Polyline x:Name="WaveGlow" StrokeThickness="7" Opacity="0.22"
                    StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
            <Polyline.Stroke>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#00E97B27" Offset="0"/>
                <GradientStop Color="#E97B27" Offset="0.2"/>
                <GradientStop Color="#FBA35F" Offset="0.52"/>
                <GradientStop Color="#F2A971" Offset="0.82"/>
                <GradientStop Color="#00F2A971" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.Stroke>
            <Polyline.Effect><BlurEffect Radius="9"/></Polyline.Effect>
          </Polyline>

          <Polyline x:Name="Wave" StrokeThickness="2.4" Opacity="0.9"
                    StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
            <Polyline.Stroke>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#00EC914C" Offset="0"/>
                <GradientStop Color="#8CEC914C" Offset="0.14"/>
                <GradientStop Color="#FFF19F60" Offset="0.32"/>
                <GradientStop Color="#FFFCB782" Offset="0.58"/>
                <GradientStop Color="#FFFCBE8F" Offset="0.8"/>
                <GradientStop Color="#8CFCBE8F" Offset="0.9"/>
                <GradientStop Color="#00FCBE8F" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.Stroke>
            <Polyline.Effect><BlurEffect Radius="2.2"/></Polyline.Effect>
          </Polyline>

          <!-- Раскалённое ядро нити. Тот же путь, но тоньше и почти белый:
               настоящий источник света в середине всегда выбелен, цвет живёт
               только по краям. Без этой линии полоса читалась как крашеная
               верёвка, а не как свет. -->
          <Polyline x:Name="WaveCore" StrokeThickness="1.0" Opacity="0.95"
                    StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
            <Polyline.Stroke>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#00FEE9D9" Offset="0"/>
                <GradientStop Color="#73FEE9D9" Offset="0.14"/>
                <GradientStop Color="#FFFFFFFF" Offset="0.32"/>
                <GradientStop Color="#FFFFFFFF" Offset="0.7"/>
                <GradientStop Color="#73FEEBDD" Offset="0.88"/>
                <GradientStop Color="#00FEEBDD" Offset="1"/>
              </LinearGradientBrush>
            </Polyline.Stroke>
          </Polyline>

          <Ellipse x:Name="Spark" Width="9" Height="9" Opacity="0"
                   HorizontalAlignment="Left" VerticalAlignment="Top">
            <Ellipse.Fill>
              <RadialGradientBrush>
                <GradientStop Color="#FFFFFF" Offset="0"/>
                <GradientStop Color="#FCC092" Offset="0.45"/>
                <GradientStop Color="#00FBA35F" Offset="1"/>
              </RadialGradientBrush>
            </Ellipse.Fill>
            <Ellipse.Effect><BlurEffect Radius="5"/></Ellipse.Effect>
            <Ellipse.RenderTransform><TranslateTransform x:Name="SparkMove" X="0" Y="0"/></Ellipse.RenderTransform>
          </Ellipse>
        </Grid>

        <!-- ответ -->
        <Border x:Name="AnswerWrap" CornerRadius="14" Background="#0C0805" BorderBrush="#2C2016"
                BorderThickness="1" Margin="0,10,0,0" Padding="14,12" Visibility="Collapsed"
                RenderTransformOrigin="0.5,0">
          <Border.RenderTransform><TranslateTransform x:Name="AnswerSlide" Y="8"/></Border.RenderTransform>
          <ScrollViewer MaxHeight="260" VerticalScrollBarVisibility="Auto">
            <StackPanel>
              <TextBlock x:Name="AnswerKind" Text="" Foreground="#E97B27" FontSize="10.5"
                         FontWeight="SemiBold" Margin="0,0,0,5" Visibility="Collapsed"/>
              <TextBlock x:Name="Answer" Text="" Foreground="#F9E9DD" FontSize="13.5"
                         TextWrapping="Wrap" LineHeight="20"/>
            </StackPanel>
          </ScrollViewer>
        </Border>

        <!-- Разрешение управлять мышью.

             Отдельная карточка, а не строка ответа и не системное окно.
             Причина простая: это единственное место, где помощник просит
             доверия, и оно обязано выглядеть иначе, чем всё остальное —
             тёплой рамкой, кнопкой в цвет и перечнем того, что произойдёт.

             Здесь же написано, как всё это прекратить. Человек соглашается
             отдать мышь только тогда, когда заранее знает, как забрать её
             обратно. -->
        <Border x:Name="GrantWrap" CornerRadius="16" Background="#0E0C0A" BorderBrush="#3B2D12"
                BorderThickness="1" Margin="0,11,0,0" Padding="16,14" Visibility="Collapsed"
                RenderTransformOrigin="0.5,0">
          <Border.RenderTransform><TranslateTransform x:Name="GrantSlide" Y="10"/></Border.RenderTransform>
          <Border.Effect>
            <DropShadowEffect BlurRadius="26" ShadowDepth="0" Opacity="0.3" Color="#F0A93C"/>
          </Border.Effect>
          <StackPanel>
            <StackPanel Orientation="Horizontal" Margin="0,0,0,9">
              <!-- Стрелка курсора: рисунок, а не буква, — символ должен быть
                   тем самым предметом, о котором идёт речь. -->
              <Path Data="M 0,0 L 0,13.5 L 3.4,10.6 L 5.6,15.4 L 8,14.2 L 5.8,9.6 L 10,9.2 Z"
                    Fill="#FFC469" VerticalAlignment="Center" Margin="0,0,9,0"/>
              <TextBlock Text="Разрешить управлять мышью?" Foreground="#FFE0B0" FontSize="14"
                         FontWeight="SemiBold" VerticalAlignment="Center"/>
            </StackPanel>

            <TextBlock Foreground="#C7B7AA" FontSize="12" TextWrapping="Wrap" LineHeight="18"
                       Text="Cloud HDR будет водить курсор, нажимать, крутить колесо и печатать — так же, как это делаете вы."/>

            <StackPanel Margin="0,9,0,0">
              <TextBlock x:Name="GrantTime" Foreground="#A58F7F" FontSize="11.5" Margin="0,0,0,3"
                         Text="• Разрешение действует 30 минут, потом гаснет само"/>
              <TextBlock Foreground="#A58F7F" FontSize="11.5" Margin="0,0,0,3"
                         Text="• Escape в любую секунду прекращает работу"/>
              <TextBlock Foreground="#A58F7F" FontSize="11.5"
                         Text="• Возьмитесь за мышь — управление сразу вернётся к вам"/>
            </StackPanel>

            <StackPanel Orientation="Horizontal" Margin="0,13,0,0">
              <Button x:Name="GrantYes" Style="{StaticResource Solid}" Content="Разрешить" Margin="0,0,8,0"/>
              <Button x:Name="GrantNo" Style="{StaticResource Ghost}" Content="Не сейчас"/>
            </StackPanel>
          </StackPanel>
        </Border>

        <!-- Что помощник делает прямо сейчас.

             Шаги показываются по мере выполнения, а не списком в конце. Пока
             курсор ездит по чужим окнам, эта панель — единственное место, где
             видно замысел: «открываю Проводник», «листаю», «нашёл Загрузки».
             Без неё происходящее выглядит как сбойнувшая мышь. -->
        <Border x:Name="CursorWrap" CornerRadius="16" Background="#0B0806" BorderBrush="#34261C"
                BorderThickness="1" Margin="0,11,0,0" Padding="14,12" Visibility="Collapsed">
          <StackPanel>
            <Grid Margin="0,0,0,9">
              <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                <Ellipse x:Name="WorkDot" Width="7" Height="7" Fill="#F0A93C" Margin="0,0,8,0"
                         VerticalAlignment="Center">
                  <Ellipse.Triggers>
                    <EventTrigger RoutedEvent="FrameworkElement.Loaded">
                      <BeginStoryboard>
                        <Storyboard>
                          <DoubleAnimation Storyboard.TargetProperty="Opacity" From="1" To="0.25"
                                           Duration="0:0:0.8" AutoReverse="True" RepeatBehavior="Forever"/>
                        </Storyboard>
                      </BeginStoryboard>
                    </EventTrigger>
                  </Ellipse.Triggers>
                </Ellipse>
                <TextBlock x:Name="CursorTitle" Text="Работаю руками" Foreground="#F9E9DD"
                           FontSize="12.5" FontWeight="SemiBold" VerticalAlignment="Center"/>
              </StackPanel>
              <Button x:Name="StopButton" Style="{StaticResource Ghost}" Content="Стоп"
                      HorizontalAlignment="Right" Padding="11,5" FontSize="11.5"/>
            </Grid>

            <!-- Полоса выполнения: сколько шагов из задуманных пройдено. -->
            <Border x:Name="CursorTrack" CornerRadius="2" Height="3" Background="#20160E" Margin="0,0,0,10">
              <Border x:Name="CursorFill" CornerRadius="2" HorizontalAlignment="Left" Width="0">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                    <GradientStop Color="#F0A93C" Offset="0"/>
                    <GradientStop Color="#FFD79A" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
            </Border>

            <StackPanel x:Name="StepList"/>
          </StackPanel>
        </Border>

        <!-- Подсказка внизу. Здесь же человек впервые узнаёт про «curs» —
             команду, о существовании которой иначе догадаться неоткуда. -->
        <TextBlock x:Name="Hints" Foreground="#614C3C" FontSize="10.5" Margin="3,11,0,0"
                   Text="Enter — выполнить · Esc — скрыть · curs — управление мышью"/>

        </StackPanel>
      </Grid>
    </Border>
  </Grid>
</Window>
'@

$reader = New-Object IO.StringReader $Xaml
$xml = [Xml.XmlReader]::Create($reader)
$Window = [Windows.Markup.XamlReader]::Load($xml)

$InputBox       = $Window.FindName('Input')
$Placeholder = $Window.FindName('Placeholder')
$Answer      = $Window.FindName('Answer')
$AnswerKind  = $Window.FindName('AnswerKind')
$AnswerWrap  = $Window.FindName('AnswerWrap')
$AnswerSlide = $Window.FindName('AnswerSlide')
$Status      = $Window.FindName('Status')
$ThinkRing   = $Window.FindName('ThinkRing')
$InputWrap   = $Window.FindName('InputWrap')
$Slide       = $Window.FindName('Slide')
$Shell       = $Window.FindName('Shell')
$WaveWrap    = $Window.FindName('WaveWrap')
$Wave        = $Window.FindName('Wave')
$WaveGlow    = $Window.FindName('WaveGlow')
$Spark       = $Window.FindName('Spark')
$SparkMove   = $Window.FindName('SparkMove')
$CoreGlow    = $Window.FindName('CoreGlow')
$WaveCore    = $Window.FindName('WaveCore')
$CoreSpark   = $Window.FindName('CoreSpark')
$BeamTight   = $Window.FindName('BeamTight')
$BeamWide    = $Window.FindName('BeamWide')

$ModeChip    = $Window.FindName('ModeChip')
$ModeDot     = $Window.FindName('ModeDot')
$Hints       = $Window.FindName('Hints')
$GrantWrap   = $Window.FindName('GrantWrap')
$GrantSlide  = $Window.FindName('GrantSlide')
$GrantTime   = $Window.FindName('GrantTime')
$GrantYes    = $Window.FindName('GrantYes')
$GrantNo     = $Window.FindName('GrantNo')
$CursorWrap  = $Window.FindName('CursorWrap')
$CursorTitle = $Window.FindName('CursorTitle')
$CursorTrack = $Window.FindName('CursorTrack')
$CursorFill  = $Window.FindName('CursorFill')
$StepList    = $Window.FindName('StepList')
$StopButton  = $Window.FindName('StopButton')
$WorkDot     = $Window.FindName('WorkDot')

# Узлы маски зарева. Держим их отдельно, чтобы в кадре менять цвет уже готовым
# узлам, а не собирать кисть заново: тридцать новых кистей в секунду — это мусор
# для сборщика на ровном месте.
$TightStops  = $Window.FindName('TightMask').GradientStops
$WideStops   = $Window.FindName('WideMask').GradientStops
$Script:MaskSegs = $TightStops.Count - 1
$CorePulse   = $Window.FindName('CorePulse')
$HotkeyHint  = $Window.FindName('HotkeyHint')
$HotkeyHint.Text = $HotkeyText

# ---------------------------------------------------------- линия света ------
#
# Полоса под строкой ввода — единственное, что в панели изображает голос.
# Сделана она честно: точки ломаной пересчитываются тридцать раз в секунду,
# бегущая волна складывается из двух синусов разной длины, а огибающая гасит её
# у краёв. Поэтому в покое линия совершенно прямая — светящаяся нить с бегущим
# по ней огоньком, — и оживает ровно тогда, когда помощнику есть что сказать.
#
# Амплитуда не взята из звука: агент отвечает текстом, звука тут нет. Она
# набирается вспышками разной высоты и длины — так же неровно, как ложатся
# слоги в речи. Ровное синусоидальное качание читалось бы как «идёт процесс»,
# а не «кто-то говорит», и разница видна с первого взгляда.
#
# Таймер работает только пока панель на экране: тридцать кадров в секунду в
# спрятанном окне грели бы процессор впустую.

$Script:WaveLevel  = 0.0                      # текущая амплитуда, пикселей
$Script:WaveTarget = 0.0                      # к чему тянется амплитуда
$Script:WavePhase  = 0.0                      # фаза бегущей волны
$Script:WaveMode   = 'idle'
$Script:WaveUntil  = [DateTime]::MinValue     # когда состояние кончится
$Script:SparkAt    = -0.2                     # огонёк на линии, 0…1
$Script:Breath     = 0.0                      # своя фаза у дыхания сферы
$Script:CorePop    = 0.0                      # толчок ядра на слове, затухает

$Script:BaseLine   = 62                       # где стоит нить внутри полосы
$Script:MaskLevel  = New-Object 'double[]' ($Script:MaskSegs + 1)   # сглаженная маска зарева

# Речь по словам. Ответ не появляется целиком: слова выкладываются по одному, и
# каждое одинаково толкает и линию, и ядро. Из-за этого движение перестаёт быть
# «анимацией на время ответа» и становится чтением вслух — видно, где помощник
# ускоряется, а где останавливается на точке.
$Script:Words     = @()
$Script:WordIndex = 0
$Script:WordAt    = [DateTime]::MinValue
$Script:WordRate  = 1.0                       # длинный ответ проговаривается быстрее

function Set-Wave([string]$mode, [double]$seconds = 0) {
    $Script:WaveMode = $mode
    switch ($mode) {
        'idle'  { $Script:WaveTarget = 0.0;  $Script:WaveUntil = [DateTime]::MinValue }
        'type'  { $Script:WaveTarget = 2.4;  $Script:WaveUntil = (Get-Date).AddMilliseconds(520) }
        'wake'  { $Script:WaveTarget = 6.5;  $Script:WaveUntil = (Get-Date).AddMilliseconds(760) }
        'think' { $Script:WaveTarget = 3.0;  $Script:WaveUntil = [DateTime]::MinValue }
        'speak' { $Script:WaveUntil = (Get-Date).AddSeconds($seconds) }
        default { $Script:WaveTarget = 0.0;  $Script:WaveUntil = [DateTime]::MinValue }
    }
}

# Разбивает ответ на слова вместе с пробелами после них: склеенные обратно, они
# дают ровно исходный текст, поэтому выкладывать можно простым дописыванием.
function Start-Speech([string]$text) {
    $Script:Words = @([regex]::Matches($text, '\S+\s*') | ForEach-Object { $_.Value })
    $Script:WordIndex = 0
    $Script:WordAt = Get-Date

    # Говорить нечего — и вида, что говорим, тоже не делаем: иначе линия висела
    # бы в режиме речи до истечения срока и не отзывалась бы на набор.
    if ($Script:Words.Count -eq 0) { Set-Wave 'idle'; return }

    # Короткий ответ читается в своём темпе, длинный — быстрее. Человеку важно
    # увидеть конец ответа, а не досмотреть красивую анимацию: без этого абзац
    # на две сотни слов выкладывался бы полминуты.
    $Script:WordRate = 1.0
    if ($Script:Words.Count -gt 0) {
        $Script:WordRate = [Math]::Max(0.32, [Math]::Min(1.0, 9000.0 / ($Script:Words.Count * 165.0)))
    }
    Set-Wave 'speak' 60
}

# Досказать немедленно: текст на месте, очередь пуста. Нужно там, где панель
# уходит с экрана, — иначе слова замрут на полуслове до следующего показа.
function Stop-Speech {
    if ($Script:WordIndex -lt $Script:Words.Count) {
        $Answer.Text += (-join $Script:Words[$Script:WordIndex..($Script:Words.Count - 1)])
    }
    $Script:Words = @()
    $Script:WordIndex = 0
}

function Update-Wave {
    $now = Get-Date

    switch ($Script:WaveMode) {
        'speak' {
            if ($Script:WordIndex -ge $Script:Words.Count) {
                # Слова кончились — линия договаривает и опадает.
                $Script:WaveTarget = 0.0
                if ($now -ge $Script:WaveUntil) { Set-Wave 'idle' }
            } elseif ($now -ge $Script:WordAt) {
                $word = $Script:Words[$Script:WordIndex]
                $Script:WordIndex++
                $Answer.Text += $word

                # Длинное слово звучит дольше и громче короткого — этого хватает,
                # чтобы речь перестала быть равномерной.
                $bare = $word.Trim()
                $len = [Math]::Min(14, $bare.Length)
                $Script:WaveTarget = 3.6 + $len * 0.72
                $Script:CorePop = 1.0

                $ms = 62 + $len * 19
                if ($bare -match '[,;:—–]$')  { $ms += 130 }
                if ($bare -match '[.!?…]$')   { $ms += 240 }
                $Script:WordAt = $now.AddMilliseconds($ms * $Script:WordRate)

                if ($Script:WordIndex -ge $Script:Words.Count) {
                    $Script:WaveUntil = $now.AddMilliseconds(900)
                }
            } else {
                # Между словами амплитуда проседает, но не в ноль: так слышны
                # отдельные слова, а не одна сплошная дрожь.
                $Script:WaveTarget *= 0.86
            }
        }
        'think' { $Script:WaveTarget = 2.6 + [Math]::Sin($Script:WavePhase * 0.4) * 1.1 }
        default {
            if ($Script:WaveUntil -ne [DateTime]::MinValue -and $now -ge $Script:WaveUntil) { Set-Wave 'idle' }
        }
    }

    # Сглаживание: амплитуда не прыгает к цели, а догоняет её. Без этого
    # вспышки выглядели бы дёрганьем, а не дыханием.
    $Script:WaveLevel += ($Script:WaveTarget - $Script:WaveLevel) * 0.2
    $level = $Script:WaveLevel
    $Script:WavePhase += 0.17 + $level * 0.014
    $phase = $Script:WavePhase

    $width = $WaveWrap.ActualWidth
    if ($width -lt 40) { $width = 380 }
    $base = $WaveWrap.ActualHeight - 16
    if ($base -lt 10) { $base = $Script:BaseLine }

    # Огибающая: у краёв волна сходит на нет, поэтому линия всегда закреплена
    # концами и никогда не обрывается ступенькой. Между «синусом» и «синусом в
    # квадрате» выбрана середина: квадрат сгонял всё движение в центр, чистый
    # синус качал линию до самых краёв и делал её похожей на верёвку.
    $count = 48
    $points = New-Object Windows.Media.PointCollection
    $tight  = New-Object Windows.Media.PointCollection
    $wide   = New-Object Windows.Media.PointCollection

    # Насколько громко говорим сейчас, 0…1. От этого зависит, как высоко
    # поднимется свет: в покое он лежит у самой нити, на слове — выстреливает.
    $loud = [Math]::Min(1.0, $level / 11.0)

    # Толщина ореолов и их подъём над нитью.
    #
    # Подъём — ровно половина толщины (минус пара пикселей на подмес вниз): у
    # ломаной обводка ложится по обе стороны от пути, поэтому только при таком
    # сдвиге НИЖНИЙ край каждого слоя садится на нить. Это и даёт настоящий
    # спад яркости: у самой нити светят все три слоя, в двадцати пикселях над
    # ней — только самый широкий. Когда широкий слой висел выше и до нити не
    # доставал, он читался отдельным облаком дыма, а не заревом.
    #
    # Толщину приходится держать в узде. Свету отведено 42 пикселя вверх, и
    # слой толще этого упирается в край полосы и обрезается прямой линией —
    # вместо зарева выходит аккуратный прямоугольник. Тесное зарево к тому же
    # честнее читается как свет: широкое и бледное глаз принимает за туман.
    $tightW = 9.0 + $level * 0.5
    $wideW  = 15.0 + $level * 0.9
    $tightLift = $tightW / 2 - 2
    $wideLift  = $wideW / 2 - 3

    # маска зарева: сила голоса по участкам строки, набирается в основном цикле
    $segs = $Script:MaskSegs
    $segEnergy = New-Object 'double[]' ($segs + 1)

    $lineY = New-Object 'double[]' $count
    $tightY = New-Object 'double[]' $count
    $wideY  = New-Object 'double[]' $count

    for ($i = 0; $i -lt $count; $i++) {
        $t = $i / ($count - 1)
        $fade = [Math]::Sin([Math]::PI * $t)
        $fade = $fade * (0.45 + 0.55 * $fade)
        $ripple = [Math]::Sin($t * 11.0 - $phase) * 0.68 + [Math]::Sin($t * 23.0 - $phase * 1.6) * 0.32
        $y = $base + $level * $fade * $ripple
        $lineY[$i] = $y

        # Сила голоса в этой точке: и огибающая, и текущий взмах ряби. Свет
        # тянется вверх именно там, где нить взлетает, — потому и получается
        # язык сияния, а не ровная крышка.
        #
        # Сдвиг — до середины толщины ореола: у ломаной обводка ложится по обе
        # стороны от пути, и без сдвига половина света ушла бы под нить.
        $energy = $fade * (0.42 + 0.58 * [Math]::Abs($ripple))
        $tightY[$i] = $y - ($tightLift + 2.5 * $energy * $loud)
        $wideY[$i]  = $y - ($wideLift + 5.0 * $energy * $loud)

        # По участку берём самый сильный размах, а не средний: усреднение
        # съедает как раз те всплески, ради которых свет и затевался.
        $seg = [int][Math]::Round($t * $segs)
        if ($energy -gt $segEnergy[$seg]) { $segEnergy[$seg] = $energy }
    }

    # Альфа узлов маски. Ползёт к цели, а не прыгает: пересчёт идёт каждый кадр,
    # и без сглаживания яркие пятна мигали бы вместе с рябью.
    for ($k = 0; $k -le $segs; $k++) {
        $want = [Math]::Min(1.0, $segEnergy[$k] * 1.45)
        $prev = $Script:MaskLevel[$k]
        $now2 = $prev + ($want - $prev) * 0.3
        $Script:MaskLevel[$k] = $now2
        $a = [byte][Math]::Round(255 * $now2)
        $color = [Windows.Media.Color]::FromArgb($a, 0, 0, 0)
        $TightStops[$k].Color = $color
        $WideStops[$k].Color = $color
    }

    for ($i = 0; $i -lt $count; $i++) {
        $x = ($i / ($count - 1)) * $width
        $points.Add([Windows.Point]::new($x, $lineY[$i]))
        $tight.Add([Windows.Point]::new($x, $tightY[$i]))
        $wide.Add([Windows.Point]::new($x, $wideY[$i]))
    }

    # Одна и та же ломаная достаётся линии, её белому ядру и размытой копии:
    # свечение обязано повторять линию точка в точку, а считать её трижды незачем.
    $points.Freeze()
    $tight.Freeze()
    $wide.Freeze()
    $Wave.Points = $points
    $WaveCore.Points = $points
    $WaveGlow.Points = $points
    $BeamTight.Points = $tight
    $BeamWide.Points = $wide

    $Wave.StrokeThickness = 2.4 + $level * 0.12
    $WaveCore.StrokeThickness = 1.0 + $level * 0.05
    $WaveGlow.Opacity = 0.22 + [Math]::Min(0.42, $level * 0.045)
    $CoreGlow.Opacity = 0.16 + [Math]::Min(0.62, $level * 0.06)
    $CoreSpark.Opacity = [Math]::Min(0.82, 0.3 + $loud * 0.45 + $Script:CorePop * 0.25)

    # Свет над нитью почти не виден в покое и разгорается на речи. Порог здесь
    # нужен: без него полоса тлела бы всегда и читалась как засветка экрана, а
    # не как след голоса. Плотный слой вспыхивает раньше и резче широкого —
    # так у зарева появляется настоящий спад яркости от нити наружу.
    $BeamTight.Opacity = [Math]::Min(0.52, [Math]::Max(0, $level - 0.8) * 0.07)
    $BeamWide.Opacity  = [Math]::Min(0.34, [Math]::Max(0, $level - 1.4) * 0.036)
    $BeamTight.StrokeThickness = $tightW
    $BeamWide.StrokeThickness  = $wideW

    # Сфера: медленное дыхание плюс толчок на каждом слове. И то и другое
    # считается здесь, а не разметкой, — иначе две анимации дрались бы за масштаб.
    $Script:Breath += 0.06
    $Script:CorePop *= 0.87
    $scale = 1 + [Math]::Sin($Script:Breath) * 0.022 + $Script:CorePop * 0.1 + $level * 0.003
    $CorePulse.ScaleX = $scale
    $CorePulse.ScaleY = $scale

    # Огонёк, бегущий по линии: в покое всё движение — это он.
    $Script:SparkAt += 0.0075 + $level * 0.0009
    if ($Script:SparkAt -gt 1.2) { $Script:SparkAt = -0.2 }

    $at = $Script:SparkAt
    if ($at -lt 0 -or $at -gt 1) {
        $Spark.Opacity = 0
    } else {
        $fade = [Math]::Sin([Math]::PI * $at)
        $ripple = [Math]::Sin($at * 11.0 - $phase) * 0.68 + [Math]::Sin($at * 23.0 - $phase * 1.6) * 0.32
        $SparkMove.X = $at * $width - 4.5
        $SparkMove.Y = $base + $level * $fade * (0.45 + 0.55 * $fade) * $ripple - 4.5
        $Spark.Opacity = 0.3 + $fade * 0.7
    }
}

$Script:Waver = New-Object Windows.Threading.DispatcherTimer
$Script:Waver.Interval = [TimeSpan]::FromMilliseconds(33)
$Script:Waver.Add_Tick({ Update-Wave })

# ------------------------------------------------------------ анимации -------

function Start-Fade($target, [double]$to, [int]$ms, [scriptblock]$done) {
    $animation = New-Object Windows.Media.Animation.DoubleAnimation
    $animation.To = $to
    $animation.Duration = [Windows.Duration][TimeSpan]::FromMilliseconds($ms)
    $animation.EasingFunction = New-Object Windows.Media.Animation.CubicEase -Property @{ EasingMode = 'EaseOut' }
    if ($done) { $animation.Add_Completed({ & $done }.GetNewClosure()) }
    $target.BeginAnimation([Windows.UIElement]::OpacityProperty, $animation)
}

function Start-Slide([double]$to, [int]$ms) {
    $animation = New-Object Windows.Media.Animation.DoubleAnimation
    $animation.To = $to
    $animation.Duration = [Windows.Duration][TimeSpan]::FromMilliseconds($ms)
    $animation.EasingFunction = New-Object Windows.Media.Animation.CubicEase -Property @{ EasingMode = 'EaseOut' }
    $Slide.BeginAnimation([Windows.Media.TranslateTransform]::XProperty, $animation)
}

function Set-Position {
    $area = [Windows.SystemParameters]::WorkArea
    $margin = 8
    if ($Side -eq 'left') { $Window.Left = $area.Left + $margin }
    else { $Window.Left = $area.Right - $Window.Width - $margin }
    $height = if ($Window.ActualHeight -gt 0) { $Window.ActualHeight } else { 210 }
    $Window.Top = $area.Top + [Math]::Max(0, ($area.Height - $height) / 2)
}

function Hide-Answer {
    $Script:Words = @()
    $Script:WordIndex = 0
    $AnswerWrap.Visibility = 'Collapsed'
    $Answer.Text = ''
    $Answer.MinHeight = 0
    $AnswerKind.Visibility = 'Collapsed'
}

function Show-Answer([string]$text, [string]$kind) {
    $Answer.Text = $text
    if ($kind) {
        $AnswerKind.Text = $kind.ToUpper()
        $AnswerKind.Visibility = 'Visible'
    } else {
        $AnswerKind.Visibility = 'Collapsed'
    }
    $AnswerWrap.Opacity = 0
    $AnswerWrap.Visibility = 'Visible'

    $move = New-Object Windows.Media.Animation.DoubleAnimation
    $move.From = 10; $move.To = 0
    $move.Duration = [Windows.Duration][TimeSpan]::FromMilliseconds(240)
    $move.EasingFunction = New-Object Windows.Media.Animation.CubicEase -Property @{ EasingMode = 'EaseOut' }
    $AnswerSlide.BeginAnimation([Windows.Media.TranslateTransform]::YProperty, $move)
    Start-Fade $AnswerWrap 1 240 $null

    # Место под ответ занимается сразу, на весь текст, и только потом текст
    # убирается и выкладывается по словам. Иначе панель росла бы на каждом
    # слове: окно у нас по содержимому, и подпрыгивало бы двадцать раз подряд,
    # а читать на ходу было бы невозможно.
    $Answer.MinHeight = 0
    $AnswerWrap.UpdateLayout()
    $Answer.MinHeight = $Answer.ActualHeight
    $Answer.Text = ''

    # Спрятанной панели говорить не с кем: таймер там остановлен, и слова
    # замерли бы на первом. Кладём ответ целиком.
    if ($Window.IsVisible) { Start-Speech $text } else { $Answer.Text = $text }

    $Window.Dispatcher.BeginInvoke([Windows.Threading.DispatcherPriority]::Loaded,
        [action]{ Set-Position }) | Out-Null
}

<#
    Показать панель.

    Тихий показ нужен для поручений из окна приложения. Обычный отбирает фокус —
    это правильно для горячей клавиши (человек её нажал, чтобы печатать) и
    недопустимо здесь: человек в этот момент говорит или печатает в окне
    приложения, и уведённый фокус означает, что следующая его фраза уйдёт не
    туда. Панель при этом всё равно видно: она поверх всех окон и служит
    подсказкой о том, что происходит.
#>
function Show-Panel([switch]$Quiet) {
    # Отменяет уже начатое исчезновение.
    #
    # Прятание идёт анимацией, а окно закрывается в её конце. Без этого флага
    # быстрое второе нажатие попадало в середину исчезновения: панель послушно
    # появлялась, а через миг доигрывала чужая анимация и прятала её обратно.
    $Script:Hiding = $false

    if (-not $Window.IsVisible) {
        Hide-Answer

        # Следы прошлого поручения убираются, пока работа не идёт. Если руки
        # заняты прямо сейчас, панель обязана открыться на том же месте, где её
        # закрыли: человек вызывает её как раз затем, чтобы посмотреть, докуда
        # дошло дело, и нажать «стоп».
        if (-not (Test-CursorBusy)) {
            $CursorWrap.Visibility = 'Collapsed'
            $GrantWrap.Visibility = 'Collapsed'
            $Status.Text = 'Скажите, что открыть или о чём рассказать'
        }

        $InputBox.Text = ''
        $Shell.Opacity = 0
        $Slide.X = if ($Side -eq 'left') { -70 } else { 70 }
        $Script:WaveLevel = 0
        $Script:SparkAt = -0.2
        $Script:CorePop = 0.55                # сфера отзывается на вызов
        Set-Wave 'wake'                       # линия здоровается и успокаивается
        $Script:Waver.Start()
        $Window.Show()
        Set-Position
    }
    $Script:ShownAt = Get-Date
    Update-Mode

    # Activate() у WPF просит фокус вежливо, и Windows вправе отказать: право
    # выносить окно вперёд есть только у того, кто получил последний ввод. Для
    # нажатия горячей клавиши система это право нам как раз выдаёт, но
    # пользуется им SetForegroundWindow, а не Activate.
    #
    # Разница видна сразу: без этого панель появляется, а буквы продолжают
    # уходить в ту программу, что была впереди, — человек печатает вопрос
    # помощнику прямо в чужое окно.
    if (-not $Quiet) {
        $Window.Activate() | Out-Null
        if ($Script:Handle -and $Script:Handle -ne [IntPtr]::Zero) {
            [void][CloudHdrCompanion.Native]::SetForegroundWindow($Script:Handle)
        }
        $InputBox.Focus() | Out-Null
    }

    Start-Slide 0 300
    Start-Fade $Shell 1 220 $null
}

function Hide-Panel([string]$why = '') {
    if (-not $Window.IsVisible) { return }
    # Причина скрытия попадает в журнал. Панель обязана оставаться на экране,
    # пока помощник работает руками, и если она всё-таки пропала — важно знать,
    # кто её убрал: горячая клавиша, Escape или потеря фокуса.
    if ($why) { Write-Line ("Панель скрыта: {0}" -f $why) }
    Stop-Speech
    $Script:Hiding = $true
    $away = if ($Side -eq 'left') { -60 } else { 60 }
    Start-Slide $away 180
    Start-Fade $Shell 0 170 { if ($Script:Hiding) { $Window.Hide(); $Script:Waver.Stop() } }
}

function Switch-Panel {
    try {
        if ($Window.IsVisible -and $Window.IsActive) { Hide-Panel 'горячая клавиша' } else { Show-Panel }
    } catch {
        Write-Line ("Панель не открылась: {0}" -f $_.Exception.Message)
    }
}

# ------------------------------------------------------- управление мышью ----
#
# Режим «руки» целиком здесь: разрешение, запуск поручения, показ шагов,
# остановка. Сам движок лежит в cursor-core.ps1 и подключён в начале файла,
# поэтому обращение к нему — обычный вызов функции. Ни служб, ни портов, ни
# ожидания ответа по сети: панель и руки — один процесс.

$Script:CursorShared = $null
$Script:CursorSpace  = $null
$Script:CursorWorker = $null
$Script:CursorHandle = $null
$Script:CursorUntil  = [DateTime]::MinValue
$Script:CursorStarted = [DateTime]::MinValue
$Script:StopAsked    = [DateTime]::MinValue
$Script:CursorQueued = ''                  # поручение, которое ждёт разрешения
$Script:StepsShown   = -1
$Script:StateAt      = [DateTime]::MinValue
$Script:InboxSeen    = ''

$CursorWord = if ($CursorSettings -and $CursorSettings.word) { [string]$CursorSettings.word } else { 'curs' }

# Слова включения. Латинское «curs» — основное, остальные добавлены потому, что
# по-русски его наберут как «курс», а голосом продиктуют как «мышь».
$CursorCall = '^\s*(curs|cursor|курс|курсор|мышь|мышка|руки)\s*$'
$CursorQuit = '^\s*(выход|хватит управлять|отключи курсор|верни мышь|обычный режим)\s*$'
$CursorStop = '^\s*(стоп|стой|хватит|остановись|отмена|прекрати)\s*$'

function New-Brush([string]$hex) {
    return New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($hex))
}

$Amber     = New-Brush '#F0A93C'
$AmberSoft = New-Brush '#FFD79A'
$AmberBack = New-Brush '#17110A'
$AmberEdge = New-Brush '#4A3617'
$Calm      = New-Brush '#120C08'
$CalmEdge  = New-Brush '#34251A'
$CalmText  = New-Brush '#9A806C'
$CalmDot   = New-Brush '#634D3D'
$Good      = New-Brush '#4ADE80'
$Warn      = New-Brush '#F87171'
$Dim       = New-Brush '#6D5747'
$Plain     = New-Brush '#DDC9BA'

function Test-CursorOn { return ($Script:CursorReady -and $Script:CursorUntil -gt (Get-Date)) }

function Test-CursorBusy {
    return ($null -ne $Script:CursorShared -and [bool]$Script:CursorShared['running'])
}

<#
    Поручение это рукам или всё-таки разговор?

    Спрашиваем у самого разбора: он раскладывает фразу на шаги и всё непонятое
    складывает в шаг «unknown». Остался хоть один настоящий шаг — значит, есть
    что делать. Отдельный список глаголов здесь был бы вторым разбором, который
    неизбежно разошёлся бы с первым.
#>
function Test-CursorAction([string]$text) {
    if (-not $Script:CursorReady) { return $false }
    try {
        $plan = @(ConvertTo-Plan $text)
        foreach ($step in $plan) { if ([string]$step.act -ne 'unknown') { return $true } }
    } catch { return $false }
    return $false
}

<#
    Привести панель к текущему режиму.

    Вызывается после каждой смены состояния, а не в одном месте при включении:
    разрешение может кончиться само по себе, и панель обязана это показать без
    единого нажатия.
#>
function Update-Mode {
    if (Test-CursorOn) {
        $minutes = [int][Math]::Ceiling(($Script:CursorUntil - (Get-Date)).TotalMinutes)
        $ModeChip.Background = $AmberBack
        $ModeChip.BorderBrush = $AmberEdge
        $ModeDot.Fill = $Amber
        $HotkeyHint.Foreground = $AmberSoft
        $HotkeyHint.Text = ("КУРСОР · {0} мин" -f $minutes)
        $Placeholder.Text = 'открой проводник и найди папку · нарисуй логотип'
        $Hints.Text = 'Enter — выполнить · Esc — стоп · «выход» — забрать мышь'
        if (-not (Test-CursorBusy)) { $Status.Text = 'Мышь в моих руках — скажите, что сделать' }
    } else {
        $ModeChip.Background = $Calm
        $ModeChip.BorderBrush = $CalmEdge
        $ModeDot.Fill = $CalmDot
        $HotkeyHint.Foreground = $CalmText
        $HotkeyHint.Text = $HotkeyText
        $Placeholder.Text = 'открой стим · что такое гаррис мод · я хочу отдохнуть'
        $Hints.Text = ("Enter — выполнить · Esc — скрыть · {0} — управление мышью" -f $CursorWord)
    }
}

function Hide-Grant {
    $GrantWrap.Visibility = 'Collapsed'
    $Script:CursorQueued = ''
}

<#
    Показать карточку разрешения.

    Поручение, ради которого её открыли, запоминается и выполняется сразу
    после согласия. Иначе человеку пришлось бы повторять просьбу дважды: один
    раз — чтобы услышать «нет разрешения», второй — уже после согласия.
#>
function Show-Grant([string]$queued = '') {
    if (-not $Script:CursorReady) {
        Show-Answer 'Управление мышью недоступно: рядом нет файла cursor-core.ps1.' 'не вышло'
        return
    }

    $Script:CursorQueued = $queued
    $GrantTime.Text = ("• Разрешение действует {0} минут, потом гаснет само" -f $GrantMinutes)
    $GrantYes.Content = ("Разрешить на {0} минут" -f $GrantMinutes)

    Hide-Answer
    $CursorWrap.Visibility = 'Collapsed'
    $GrantWrap.Opacity = 0
    $GrantWrap.Visibility = 'Visible'

    $move = New-Object Windows.Media.Animation.DoubleAnimation
    $move.From = 12; $move.To = 0
    $move.Duration = [Windows.Duration][TimeSpan]::FromMilliseconds(260)
    $move.EasingFunction = New-Object Windows.Media.Animation.CubicEase -Property @{ EasingMode = 'EaseOut' }
    $GrantSlide.BeginAnimation([Windows.Media.TranslateTransform]::YProperty, $move)
    Start-Fade $GrantWrap 1 260 $null

    $Status.Text = 'Жду вашего решения'
    Set-Wave 'wake'
    $Window.Dispatcher.BeginInvoke([Windows.Threading.DispatcherPriority]::Loaded, [action]{ Set-Position }) | Out-Null
}

function Grant-Cursor([string]$source = 'панель') {
    $Script:CursorUntil = (Get-Date).AddMinutes($GrantMinutes)

    # Общая таблица создаётся один раз на всё разрешение: рабочее пространство
    # каждой задачи получает её же и пишет туда шаги.
    if (-not $Script:CursorShared) {
        $Script:CursorShared = [hashtable]::Synchronized(@{
            stop = $false; running = $false; done = 0; total = 0
            say = ''; ok = $true; until = $Script:CursorUntil
            steps = [Collections.ArrayList]::Synchronized((New-Object Collections.ArrayList))
        })
    }
    $Script:CursorShared['until'] = $Script:CursorUntil
    $Script:CursorShared['stop'] = $false
    Initialize-Cursor $Script:CursorShared

    Write-Line ("Выдано разрешение управлять мышью на {0} мин ({1})" -f $GrantMinutes, $source)
    Hide-Grant
    Update-Mode
    Write-CursorState

    $queued = $Script:CursorQueued
    if ($queued) { Start-CursorTask $queued }
    else { Show-Answer ("Мышь в моих руках на {0} минут. Скажите, что сделать: «открой проводник и найди папку», «нарисуй логотип». Escape — стоп." -f $GrantMinutes) 'готов' }
}

function Exit-Cursor([string]$say = '') {
    Stop-CursorTask
    $Script:CursorUntil = [DateTime]::MinValue
    if ($Script:CursorShared) {
        $Script:CursorShared['until'] = [DateTime]::MinValue
        $Script:CursorShared['stop'] = $true
    }
    Hide-Grant
    $CursorWrap.Visibility = 'Collapsed'
    Update-Mode
    Write-CursorState
    Write-Line 'Управление мышью выключено'
    if ($say) { Show-Answer $say '' }
}

# То, что выполняется в отдельном пространстве. Здесь намеренно нет ни строчки
# про сами движения: план уже собран, дело этого куска — выполнить и не
# промолчать, что бы ни случилось.
$CursorWork = {
    # Имена параметров подобраны так, чтобы не совпасть ни с одной переменной
    # движка: подключение файла точкой выполняется в ЭТОЙ же области видимости
    # и затирает всё одноимённое. Совпадение стоило полутора часов поисков —
    # таблица состояния обнулялась первой же строкой подключения.
    param($corePath, $board, $steps)

    try {
        . $corePath
        Initialize-Cursor $board
        Write-Cursor ("Берусь за дело: шагов {0}" -f @($steps).Count)
        $result = Invoke-Plan $steps
        $board['say'] = [string]$result.say
        $board['ok'] = [bool]$result.ok
    } catch {
        if (Test-AbortError $_) {
            $board['ok'] = $false
            $board['say'] = ('Остановился: {0}.' -f (Get-AbortText $_))
        } else {
            $board['ok'] = $false
            $board['say'] = ('Не получилось: {0}' -f $_.Exception.Message)
        }
    } finally {
        $board['running'] = $false
    }
}

function Start-CursorTask([string]$text) {
    if (-not (Test-CursorOn)) { Show-Grant $text; return }
    if (Test-CursorBusy) {
        Show-Answer 'Я ещё занят прошлым поручением. Скажите «стоп», если оно больше не нужно.' ''
        return
    }

    $plan = @(ConvertTo-Plan $text)
    if ($plan.Count -eq 0) {
        Show-Answer 'Не понял поручение. Скажите проще: «открой проводник», «нарисуй логотип», «полистай вниз».' 'не вышло'
        return
    }

    Close-CursorSpace

    $Script:CursorShared['steps'].Clear()
    $Script:CursorShared['stop'] = $false
    $Script:CursorShared['running'] = $true
    $Script:CursorShared['say'] = ''
    $Script:CursorShared['ok'] = $true
    $Script:CursorShared['done'] = 0
    $Script:CursorShared['total'] = $plan.Count
    $Script:CursorShared['until'] = $Script:CursorUntil
    $Script:StepsShown = -1
    $Script:StopAsked = [DateTime]::MinValue

    # Пространство однопоточно-квартирное. Иначе разбор чужих окон (а он через
    # COM) через раз возвращает пустоту вместо элементов, и «нажми на кнопку
    # Сохранить» перестаёт работать без единой ошибки в журнале.
    $Script:CursorSpace = [RunspaceFactory]::CreateRunspace()
    $Script:CursorSpace.ApartmentState = 'STA'
    $Script:CursorSpace.ThreadOptions = 'ReuseThread'
    $Script:CursorSpace.Open()

    $Script:CursorWorker = [PowerShell]::Create()
    $Script:CursorWorker.Runspace = $Script:CursorSpace
    [void]$Script:CursorWorker.AddScript($CursorWork).AddArgument($CursorFile).AddArgument($Script:CursorShared).AddArgument($plan)
    $Script:CursorHandle = $Script:CursorWorker.BeginInvoke()
    $Script:CursorStarted = Get-Date

    Hide-Answer
    Hide-Grant
    $StepList.Children.Clear()
    $CursorFill.Width = 0
    $CursorTitle.Text = 'Работаю руками'
    $CursorTitle.Foreground = $Plain
    $WorkDot.Fill = $Amber
    $CursorWrap.Visibility = 'Visible'
    $StopButton.Visibility = 'Visible'

    $Status.Text = 'Работаю мышью…'
    Set-Wave 'think'
    Start-Fade $ThinkRing 1 200 $null
    Write-Line ("Поручение курсору: {0} (шагов {1})" -f $text, $plan.Count)
    Write-CursorState

    $Window.Dispatcher.BeginInvoke([Windows.Threading.DispatcherPriority]::Loaded, [action]{ Set-Position }) | Out-Null
}

function Stop-CursorTask {
    if ($Script:CursorShared) { $Script:CursorShared['stop'] = $true }
    if ($Script:StopAsked -eq [DateTime]::MinValue) { $Script:StopAsked = Get-Date }
}

function Close-CursorSpace {
    if (-not $Script:CursorWorker) { return }
    try {
        foreach ($problem in @($Script:CursorWorker.Streams.Error)) {
            Write-Line ("Ошибка в поручении: {0}" -f $problem.Exception.Message)
        }
    } catch { }
    try { [void]$Script:CursorWorker.EndInvoke($Script:CursorHandle) } catch { }
    try { $Script:CursorWorker.Dispose() } catch { }
    try { $Script:CursorSpace.Close(); $Script:CursorSpace.Dispose() } catch { }
    $Script:CursorWorker = $null
    $Script:CursorHandle = $null
    $Script:CursorSpace = $null
}

<#
    Перерисовать шаги.

    Строки собираются заново только когда их стало больше. Панель обновляется
    четыре раза в секунду, и пересборка полудюжины надписей на каждом тике —
    это мусор для сборщика и заметное подёргивание текста при чтении.
#>
function Update-Steps {
    $steps = @($Script:CursorShared['steps'].ToArray())
    if ($steps.Count -eq $Script:StepsShown) { return }
    $Script:StepsShown = $steps.Count

    $StepList.Children.Clear()
    $tail = $steps
    if ($steps.Count -gt 6) { $tail = $steps[($steps.Count - 6)..($steps.Count - 1)] }

    foreach ($step in $tail) {
        $row = New-Object Windows.Controls.TextBlock
        $row.TextWrapping = 'Wrap'
        $row.FontSize = 11.5
        $row.LineHeight = 16
        $row.Margin = New-Object Windows.Thickness 0, 0, 0, 4

        $stamp = New-Object Windows.Documents.Run ("{0}   " -f $step.at)
        $stamp.Foreground = $Dim

        $body = New-Object Windows.Documents.Run ([string]$step.text)
        $body.Foreground = switch ([string]$step.kind) {
            'good' { $Good }
            'warn' { $Warn }
            default { $Plain }
        }

        $row.Inlines.Add($stamp)
        $row.Inlines.Add($body)
        [void]$StepList.Children.Add($row)
    }
}

<#
    Что происходит с поручением прямо сейчас.

    Вызывается по таймеру. Кроме перерисовки шагов делает две обязательные
    вещи: ловит конец работы (в том числе оборванной) и гасит режим, когда
    разрешение истекло.
#>
function Update-CursorUi {
    if (-not $Script:CursorReady) { return }

    if ($Script:CursorUntil -ne [DateTime]::MinValue -and $Script:CursorUntil -lt (Get-Date)) {
        Exit-Cursor 'Разрешение управлять мышью истекло. Скажите «curs», чтобы выдать снова.'
        return
    }

    # Пульс для окна приложения.
    #
    # Страница в браузере не может заглянуть в этот процесс и узнать, жив ли он.
    # Поэтому состояние переписывается раз в несколько секунд, даже когда ничего
    # не происходит: по свежести записи агент и понимает, что руки на месте. Без
    # пульса приложение считало бы панель мёртвой всё время, пока та просто
    # ничего не делала.
    if (((Get-Date) - $Script:StateAt).TotalSeconds -gt 8) { Write-CursorState }

    if (-not $Script:CursorShared) { return }

    # Пространство закончило работу, а флажок остался поднятым — значит, оно
    # оборвалось где-то до собственного «готово». Без этой проверки панель
    # показывала бы «работаю» до перезапуска и отказывалась брать новые
    # поручения: признак занятости есть, а занят никто.
    if ($Script:CursorHandle -and $Script:CursorHandle.IsCompleted -and [bool]$Script:CursorShared['running']) {
        $Script:CursorShared['running'] = $false
        if (-not $Script:CursorShared['say']) {
            $Script:CursorShared['say'] = 'Поручение оборвалось — подробности в журнале.'
            $Script:CursorShared['ok'] = $false
        }
    }

    if (Test-CursorBusy) {
        Update-Steps

        # Поручение не имеет права идти вечно. Зависшее окно, пропавшая кнопка,
        # бесконечное ожидание — и мышь оказалась бы занята до перезапуска
        # приложения.
        if ($Script:CursorStarted -ne [DateTime]::MinValue -and
            ((Get-Date) - $Script:CursorStarted).TotalMinutes -gt $MaxTaskMinutes) {
            Write-Line 'Поручение идёт слишком долго — останавливаю'
            # Отметка сбрасывается сразу: проверка идёт четыре раза в секунду, и
            # без этого одна затянувшаяся задача написала бы в журнал тысячу
            # одинаковых строк, похоронив под ними всё остальное.
            $Script:CursorStarted = [DateTime]::MinValue
            Stop-CursorTask
        }

        # Просьба остановиться, на которую не ответили.
        #
        # Обычно хватает флажка: движок проверяет его перед каждым шагом и сам
        # выходит. Но если пространство застряло там, где проверок нет, — а
        # такое место всегда найдётся, — «стоп» остался бы кнопкой, которая
        # ничего не делает. Через пять секунд молчания поток снимается силой.
        if ($Script:StopAsked -ne [DateTime]::MinValue -and
            ((Get-Date) - $Script:StopAsked).TotalSeconds -gt 5 -and $Script:CursorWorker) {
            Write-Line 'Пространство не ответило на «стоп» — снимаю принудительно'
            try { $Script:CursorWorker.Stop() } catch { }
            $Script:CursorShared['running'] = $false
            $Script:CursorShared['say'] = 'Остановился: пришлось прервать работу.'
            $Script:CursorShared['ok'] = $false
            $Script:StopAsked = [DateTime]::MinValue
        }

        $total = [int]$Script:CursorShared['total']
        if ($total -gt 0) {
            $ratio = [Math]::Min(1.0, [double]$Script:CursorShared['done'] / $total)
            $width = $CursorTrack.ActualWidth * $ratio
            if ($width -ge 0) { $CursorFill.Width = $width }
            $CursorTitle.Text = ("Работаю руками · шаг {0} из {1}" -f ([Math]::Min($total, [int]$Script:CursorShared['done'] + 1)), $total)
        }

        # Метка режима отсчитывает минуты, пока идёт работа: срок разрешения
        # тикает и в это время тоже.
        Update-Mode
        Write-CursorState
        return
    }

    # Работа кончилась — но это надо заметить ровно один раз.
    if ($Script:CursorWorker) {
        Update-Steps
        Close-CursorSpace

        $CursorFill.Width = $CursorTrack.ActualWidth
        $stopped = [string]$Script:CursorShared['say'] -match '^Остановился'
        $CursorTitle.Text = if ($stopped) { 'Остановлено' } else { 'Готово' }
        $CursorTitle.Foreground = if ($stopped) { $Warn } else { $Good }
        $WorkDot.Fill = if ($stopped) { $Warn } else { $Good }
        $StopButton.Visibility = 'Collapsed'

        Start-Fade $ThinkRing 0 200 $null
        $Status.Text = 'Мышь в моих руках — скажите, что сделать'

        $say = [string]$Script:CursorShared['say']
        if (-not $say) { $say = 'Готово.' }
        $kind = if ([bool]$Script:CursorShared['ok']) { 'сделано' } else { 'не вышло' }
        Show-Answer $say $kind

        Update-Mode
        Write-CursorState
    }
}

<#
    Состояние для окна приложения.

    Приложение в браузере не может заглянуть в этот процесс, а знать, что
    происходит, ему надо. Поэтому состояние выкладывается в файл рядом с
    журналами, а агент отдаёт его страницей по своему обычному адресу. Новых
    портов и служб ради этого не заводится: файл проще, и он переживает
    перезапуск любой из сторон.
#>
function Write-CursorState {
    if (((Get-Date) - $Script:StateAt).TotalMilliseconds -lt 250 -and (Test-CursorBusy)) { return }
    $Script:StateAt = Get-Date
    try {
        $steps = @()
        if ($Script:CursorShared) {
            foreach ($step in @($Script:CursorShared['steps'].ToArray())) {
                $steps += @{ at = [string]$step.at; text = [string]$step.text; kind = [string]$step.kind }
            }
        }
        $payload = [ordered]@{
            granted = (Test-CursorOn)
            running = (Test-CursorBusy)
            seconds = if (Test-CursorOn) { [int]($Script:CursorUntil - (Get-Date)).TotalSeconds } else { 0 }
            done    = if ($Script:CursorShared) { [int]$Script:CursorShared['done'] } else { 0 }
            total   = if ($Script:CursorShared) { [int]$Script:CursorShared['total'] } else { 0 }
            say     = if ($Script:CursorShared) { [string]$Script:CursorShared['say'] } else { '' }
            ok      = if ($Script:CursorShared) { [bool]$Script:CursorShared['ok'] } else { $true }
            steps   = $steps
            updated = (Get-Date).ToString('s')
        } | ConvertTo-Json -Depth 5 -Compress

        $directory = Split-Path -Parent $StateFile
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        $temporary = $StateFile + '.tmp'
        [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $StateFile -Force
    } catch { }
}

<#
    Поручения из окна приложения.

    Страница в браузере кладёт просьбу агенту, агент пишет её сюда файлом, а
    панель забирает. Выглядит кружным путём, но зато между окном и руками нет
    ни одной новой службы: всё, что уже работает, работает и для этого.

    Каждая просьба помечена своим номером, и повторно она не выполняется —
    файл лежит на диске и после выполнения, а перечитывать его панель будет
    ещё много раз.
#>
function Read-CursorInbox {
    if (-not $Script:CursorReady) { return }
    if (-not (Test-Path -LiteralPath $InboxFile)) { return }

    try {
        $raw = Get-Content -LiteralPath $InboxFile -Raw -Encoding UTF8 -ErrorAction Stop
        if (-not $raw) { return }
        $order = $raw | ConvertFrom-Json
    } catch { return }

    $id = [string]$order.id
    if (-not $id -or $id -eq $Script:InboxSeen) { return }
    $Script:InboxSeen = $id

    # Просроченное поручение не выполняется.
    #
    # Файл остаётся на диске после выполнения, и при следующем запуске панель
    # честно считала бы его новым — она-то номера не помнит. Получалось так:
    # человек перезапускает приложение, а оно ни с того ни с сего просит
    # разрешения на мышь ради поручения, отданного вчера. Свежесть решает это
    # без всякого учёта выполненного.
    try {
        if ($order.at -and ((Get-Date) - [DateTime]$order.at).TotalSeconds -gt 60) { return }
    } catch { return }

    switch ([string]$order.kind) {
        'grant'  { Show-Panel -Quiet; Grant-Cursor 'окно приложения' }
        'revoke' { Exit-Cursor 'Управление мышью выключено из приложения.' }
        'stop'   { Stop-CursorTask }
        default  {
            $text = [string]$order.text
            if (-not $text) { return }
            Show-Panel -Quiet
            # Разрешение из окна приложения уже спрошено там же. Если его всё-таки
            # нет — значит, оно истекло, и карточку показываем здесь.
            if (Test-CursorOn) { Start-CursorTask $text } else { Show-Grant $text }
        }
    }
}

$GrantYes.Add_Click({ Grant-Cursor 'панель' })
$GrantNo.Add_Click({
    Hide-Grant
    Update-Mode
    Show-Answer 'Хорошо, мышь остаётся у вас.' ''
})
$StopButton.Add_Click({ Stop-CursorTask })

# -------------------------------------------------------------- ввод ---------

<#
    Строка одна, а смыслов у неё теперь два.

    Пока разрешения нет, всё уходит агенту — как и раньше. Как только оно
    выдано, та же строка становится поручением рукам: «открой проводник и найди
    папку» больше не вопрос, а работа. Отдельного поля для этого не заведено
    намеренно — человек уже знает, куда печатать, и учить его второму месту
    ради того же действия незачем.
#>
function Send-Question {
    $text = $InputBox.Text.Trim()
    if (-not $text -or $Script:Pending) { return }

    # «curs» — просьба дать руки. Повторное слово при выданном разрешении
    # возвращает мышь человеку: включатель и выключатель — одна и та же кнопка.
    if ($text -match $CursorCall) {
        $InputBox.Text = ''
        if (Test-CursorOn) { Exit-Cursor 'Мышь снова ваша.' } else { Show-Grant '' }
        return
    }

    # «curs открой проводник» — разрешение и поручение одной фразой.
    if ($text -match '^\s*(?:curs|cursor|курс|курсор)\s+(.{2,})$') {
        $InputBox.Text = ''
        Start-CursorTask $Matches[1].Trim()
        return
    }

    if (Test-CursorOn) {
        if ($text -match $CursorQuit) { $InputBox.Text = ''; Exit-Cursor 'Мышь снова ваша.'; return }
        if ($text -match $CursorStop) {
            $InputBox.Text = ''
            if (Test-CursorBusy) { Stop-CursorTask } else { Show-Answer 'Я и так стою.' '' }
            return
        }

        # Выданное разрешение НЕ превращает помощника в одни только руки.
        #
        # Сначала так и было: пока режим включён, любая фраза уходила курсору. И
        # на «как дела» человек получал «не понял, скажите проще: откройте
        # проводник». Разрешение управлять мышью — это новая возможность, а не
        # запрет на разговор, и молчать в ответ на вопрос из-за него нелепо.
        #
        # Разбирает фразу тот же код, что и выполняет: если из неё получился
        # хоть один настоящий шаг — это поручение рукам, если ничего, кроме
        # «не понял», — это разговор, и он идёт обычным путём к агенту и модели.
        if (Test-CursorAction $text) {
            $InputBox.Text = ''
            Start-CursorTask $text
            return
        }
    }

    $Status.Text = 'Думаю…'
    Start-Fade $ThinkRing 1 200 $null
    Set-Wave 'think'
    Hide-Answer

    $shell = [PowerShell]::Create()
    $shell.RunspacePool = $Script:Pool
    [void]$shell.AddScript($AskScript).AddArgument($Base).AddArgument($Script:Token).AddArgument($text)
    $Script:Pending = @{ shell = $shell; handle = $shell.BeginInvoke(); text = $text }
    $InputBox.Text = ''
}

function Complete-Question {
    if (-not $Script:Pending -or -not $Script:Pending.handle.IsCompleted) { return }

    $result = $null
    try { $result = $Script:Pending.shell.EndInvoke($Script:Pending.handle) | Select-Object -Last 1 }
    catch { $result = @{ ok = $false; say = $_.Exception.Message } }
    finally { $Script:Pending.shell.Dispose(); $Script:Pending = $null }

    Start-Fade $ThinkRing 0 200 $null
    $Status.Text = 'Скажите, что открыть или о чём рассказать'

    if (-not $result) { Show-Answer 'Агент промолчал.' ''; return }
    if ($result.token) { $Script:Token = [string]$result.token }

    $kind = ''
    if ($result.ok -and $result.kind -eq 'action') { $kind = 'сделано' }
    elseif (-not $result.ok) { $kind = 'не вышло' }
    Show-Answer ([string]$result.say) $kind
}

# ------------------------------------------------------- умный поиск --------

<#
    Ctrl+Shift+F: маскот выходит, просит обвести область и рассказывает о ней.

    Порядок здесь сделан ради одной вещи — человек должен успеть прочитать
    просьбу. Показать панель и тут же накрыть экран рамкой выделения нельзя:
    просьба мелькнёт и исчезнет, а на экране появится непонятное затемнение.
    Поэтому между просьбой и обводкой стоит пауза, и панель к этому моменту
    уходит сама: она перекрывает часть экрана, а обводить могут как раз её
    угол.

    Пауза сделана таймером, а не ожиданием. Ожидание в потоке окна остановило
    бы и анимацию маскота, и его линию света — то есть помощник замер бы ровно
    в тот момент, когда просит о чём-то человека.
#>
$Script:LensPending = $null
$Script:LensTimer = $null
$Script:LensBusy = $false     # от просьбы обвести до показа ответа

function Start-LensSearch {
    if (-not $Script:LensReady) {
        Show-Panel
        Show-Answer 'Умный поиск недоступен: рядом нет файла lens.ps1.' 'не вышло'
        return
    }
    if ($Script:LensPending) { return }

    # Пока помощник работает руками, отбирать у него экран нельзя: рамка
    # выделения перехватит мышь посреди задачи, и работа сорвётся.
    if (Test-CursorBusy) {
        Show-Panel
        Show-Answer 'Сейчас идёт работа мышью — скажите «стоп», и повторите.' ''
        return
    }

    $Script:LensBusy = $true
    Show-Panel -Quiet
    $Status.Text = 'Умный поиск'
    Set-Wave 'wake'
    Show-Answer 'Обведите мышью ту область экрана, о которой хотите узнать.' ''
    Write-Line 'Умный поиск: жду обводку'

    # Три такта, а не один со сном посередине.
    #
    # Сначала было проще: показать просьбу, поспать, спрятать панель и открыть
    # рамку. Так делать нельзя, и это выяснилось на первой же проверке — рамка
    # открывалась и тут же сама закрывалась. Причина в том, что панель прячется
    # АНИМАЦИЕЙ, а её завершение — это работа в очереди окна. Сон эту очередь
    # блокирует, анимация не доигрывает, и завершение выполняется уже внутри
    # рамки, посреди её собственного цикла сообщений: панель скрывается из-под
    # неё, и рамка уходит вместе с ней.
    #
    # Разведённые по тактам шаги решают это без единой оговорки: к моменту
    # открытия рамки очередь окна пуста, а панель убрана по-настоящему.
    if ($Script:LensTimer) { $Script:LensTimer.Stop() }
    $Script:LensTimer = New-Object Windows.Threading.DispatcherTimer
    $Script:LensTimer.Interval = [TimeSpan]::FromMilliseconds(1100)
    $Script:LensTimer.Add_Tick({
        $Script:LensTimer.Stop()
        Hide-Panel 'умный поиск'

        $Script:LensTimer = New-Object Windows.Threading.DispatcherTimer
        $Script:LensTimer.Interval = [TimeSpan]::FromMilliseconds(320)
        $Script:LensTimer.Add_Tick({
            $Script:LensTimer.Stop()
            try { Invoke-LensCapture } catch {
                Write-Line ("Умный поиск сорвался: {0}" -f $_.Exception.Message)
                Show-Panel
                Show-Answer ("Не получилось: {0}" -f $_.Exception.Message) 'не вышло'
            }
        })
        $Script:LensTimer.Start()
    })
    $Script:LensTimer.Start()
}

function Invoke-LensCapture {
    $rect = Show-LensSelection
    if (-not $rect) {
        Write-Line 'Умный поиск: отменено'
        $Script:LensBusy = $false
        return
    }
    Write-Line ("Умный поиск: область {0}x{1}" -f $rect.width, $rect.height)

    $shot = Get-LensShot $rect

    Show-Panel -Quiet
    $Status.Text = 'Читаю…'
    Start-Fade $ThinkRing 1 200 $null
    Set-Wave 'think'
    Show-Answer 'Читаю обведённое…' ''

    $shell = [PowerShell]::Create()
    $shell.RunspacePool = $Script:Pool
    [void]$shell.AddScript($LensScript).AddArgument($Root).AddArgument($shot)
    $Script:LensPending = @{ shell = $shell; handle = $shell.BeginInvoke() }
}

function Complete-LensSearch {
    if (-not $Script:LensPending -or -not $Script:LensPending.handle.IsCompleted) { return }

    $result = $null
    try { $result = $Script:LensPending.shell.EndInvoke($Script:LensPending.handle) | Select-Object -Last 1 }
    catch { $result = $null; Write-Line ("Умный поиск не ответил: {0}" -f $_.Exception.Message) }
    finally { $Script:LensPending.shell.Dispose(); $Script:LensPending = $null }

    Start-Fade $ThinkRing 0 200 $null
    $Status.Text = 'Скажите, что открыть или о чём рассказать'

    $say = Format-LensAnswer $result
    Write-Line ("Умный поиск: {0}" -f ($say -replace "`n", ' | '))
    Show-Panel -Quiet
    Show-Answer $say ''
    Start-Speech $say
    $Script:LensBusy = $false
}

# ------------------------------------------------------- мгновенный перевод --

<#
    Ctrl+Alt+T: обвести английский текст — и перевод появляется поверх него.

    Панель спутника здесь не открывается вовсе: перевод — это не разговор, и
    лишнее окно между клавишами и результатом только мешало бы. Карточка
    «Перевожу…» встаёт на место текста сразу, а перевод приходит в неё, когда
    будет готов, — окно спутника при этом не замирает.
#>
$Script:TranslatePending = $null

function Start-Translate {
    if (-not $Script:TranslateReady) { return }
    if ($Script:TranslatePending -or $Script:LensBusy) { return }
    if (Test-CursorBusy) { return }

    $Script:LensBusy = $true
    try {
        $rect = Show-LensSelection 'Обведите английский текст — переведу на русский.   Escape — отмена'
        if (-not $rect) { Write-Line 'Перевод: отменено'; return }
        Write-Line ("Перевод: область {0}x{1}" -f $rect.width, $rect.height)

        $shot = Get-LensShot $rect
        $card = Show-TranslateCard $rect

        $shell = [PowerShell]::Create()
        $shell.RunspacePool = $Script:Pool
        [void]$shell.AddScript($TranslateScript).AddArgument($Root).AddArgument($shot)
        $Script:TranslatePending = @{ shell = $shell; handle = $shell.BeginInvoke(); card = $card }
    } catch {
        Write-Line ("Перевод сорвался: {0}" -f $_.Exception.Message)
    } finally {
        $Script:LensBusy = $false
    }
}

function Complete-Translate {
    $pending = $Script:TranslatePending
    if (-not $pending -or -not $pending.handle.IsCompleted) { return }
    $Script:TranslatePending = $null

    $result = $null
    try { $result = $pending.shell.EndInvoke($pending.handle) | Select-Object -Last 1 }
    catch { Write-Line ("Перевод не ответил: {0}" -f $_.Exception.Message) }
    finally { $pending.shell.Dispose() }

    $card = $pending.card
    if (-not $card.window.IsVisible) { return }
    if ($result -and $result.ok) {
        $card.body.Text = [string]$result.text
        $card.status.Text = ('переведено за {0:0.0} с' -f ($result.ms / 1000.0))
        $card.copy.Visibility = 'Visible'
        Write-Line ("Перевод: {0} знаков за {1} мс" -f ([string]$result.source).Length, $result.ms)
    } else {
        $card.body.Text = if ($result) { [string]$result.error } else { 'Перевод не удался.' }
        $card.status.Text = 'не вышло'
    }
}

$InputBox.Add_TextChanged({
    $Placeholder.Visibility = if ($InputBox.Text.Length -gt 0) { 'Collapsed' } else { 'Visible' }
    # Линия отзывается на набор: слушать — тоже занятие. Думающий и говорящий
    # режимы при этом не перебиваются, у них разговор поважнее.
    if ($Script:WaveMode -eq 'idle' -or $Script:WaveMode -eq 'type') { Set-Wave 'type' }
})

<#
    Escape значит «прекрати то, что сейчас важнее всего».

    Пока идёт работа руками — это остановка работы, а не скрытие окна: панель в
    этот момент единственное место, где написано, что происходит, и прятать её
    по той же клавише было бы жестоко. Открытая карточка разрешения по Escape
    закрывается, а поручение отменяется. И только в спокойном состоянии Escape
    убирает панель, как и раньше.
#>
$InputBox.Add_KeyDown({
    param($sender, $event)
    if ($event.Key -eq 'Return') {
        $event.Handled = $true
        if ($GrantWrap.Visibility -eq 'Visible') { Grant-Cursor 'панель' } else { Send-Question }
    }
    elseif ($event.Key -eq 'Escape') {
        $event.Handled = $true
        if (Test-CursorBusy) { Stop-CursorTask }
        elseif ($GrantWrap.Visibility -eq 'Visible') {
            Hide-Grant
            Update-Mode
            $Status.Text = 'Скажите, что открыть или о чём рассказать'
        }
        else { Hide-Panel 'Escape' }
    }
})

# Подсветка поля по фокусу — мелочь, а строка сразу выглядит живой.
$InputBox.Add_GotFocus({
    $InputWrap.BorderBrush = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromRgb(91, 140, 255))
    if ($Script:WaveMode -eq 'idle') { Set-Wave 'type' }
})
$InputBox.Add_LostFocus({ $InputWrap.BorderBrush = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromRgb(36, 50, 71)) })

# Щелчок мимо панели убирает её — но с двумя оговорками.
#
# Первая: пока идёт ответ, панель не прячется. Иначе ответ пришёл бы в закрытое
# окно, и человек решил бы, что помощник промолчал.
#
# Вторая: первые полсекунды после появления не считаются. В этот момент окно ещё
# перехватывает фокус у прежней программы, и Windows успевает прислать
# Deactivated до того, как фокус устоялся, — панель мигала и пропадала ровно в
# тот миг, когда её вызвали.
$Script:ShownAt = [DateTime]::MinValue

# Третья оговорка появилась вместе с руками, и без неё режим курсора не работал
# бы вовсе: помощник сам выносит вперёд чужие окна — Проводник, Paint, — и
# каждое такое переключение прилетало бы сюда как «щёлкнули мимо». Панель
# пряталась бы в первую же секунду работы, унося с собой и шаги, и кнопку
# «стоп».
# Четвёртая оговорка — умный поиск, и она стоила отдельной поломки.
#
# Рамка выделения забирает фокус, и панель честно считала это «щёлкнули мимо»:
# начинала прятаться второй раз, уже после того как её спрятали намеренно. Само
# по себе безобидно, но завершение этой анимации выполнялось внутри цикла
# сообщений рамки и убирало окно панели из-под неё — рамка закрывалась через
# секунду после появления, ничего не дождавшись. Снаружи это выглядело так, что
# помощник просит обвести область и тут же передумывает.
$Window.Add_Deactivated({
    if ($Script:Pending) { return }
    if ($Script:LensBusy) { return }
    if (Test-CursorBusy) { return }
    if ($GrantWrap.Visibility -eq 'Visible') { return }
    if (((Get-Date) - $Script:ShownAt).TotalMilliseconds -lt 500) { return }
    Hide-Panel 'фокус ушёл'
})

# ---------------------------------------------------------- горячая клавиша --

$helper = New-Object Windows.Interop.WindowInteropHelper $Window
[void]$helper.EnsureHandle()
$Script:Handle = $helper.Handle          # им же панель выносится вперёд при показе
$source = [Windows.Interop.HwndSource]::FromHwnd($helper.Handle)

# Клавиш теперь две, и различает их номер в wParam. Раньше номер не читался
# вовсе — горячая клавиша была одна, и любое WM_HOTKEY означало её. Со второй
# клавишей такая трактовка открывала бы панель на обе.
$hook = [Windows.Interop.HwndSourceHook] {
    param($handle, $message, $wparam, $lparam, $handled)
    if ($message -eq 0x0312) {
        $id = [int]$wparam
        if ($id -eq 2) {
            $Window.Dispatcher.BeginInvoke([action]{ Start-LensSearch }) | Out-Null
        } elseif ($id -eq 3) {
            $Window.Dispatcher.BeginInvoke([action]{ Start-Translate }) | Out-Null
        } else {
            $Window.Dispatcher.BeginInvoke([action]{ Switch-Panel }) | Out-Null
        }
    }
    return [IntPtr]::Zero
}
$source.AddHook($hook)

$combination = ConvertTo-Hotkey $HotkeyText
$registered = $false
if ($combination.key -gt 0) {
    $registered = [CloudHdrCompanion.Native]::RegisterHotKey($helper.Handle, 1, [uint32]$combination.modifiers, [uint32]$combination.key)
}
if (-not $registered) {
    Write-Line ("Не удалось занять «{0}» — её держит другая программа. Панель откроется только из приложения." -f $HotkeyText)
} else {
    Write-Line ("Быстрая строка: {0}" -f $HotkeyText)
}

if ($Script:LensReady) {
    $lensCombination = ConvertTo-Hotkey $LensHotkeyText
    $lensRegistered = $false
    if ($lensCombination.key -gt 0) {
        $lensRegistered = [CloudHdrCompanion.Native]::RegisterHotKey(
            $helper.Handle, 2, [uint32]$lensCombination.modifiers, [uint32]$lensCombination.key)
    }
    if (-not $lensRegistered) {
        Write-Line ("Не удалось занять «{0}» для умного поиска — её держит другая программа." -f $LensHotkeyText)
    } else {
        Write-Line ("Умный поиск по области: {0}" -f $LensHotkeyText)
    }
} else {
    Write-Line 'Умный поиск недоступен: рядом нет файла lens.ps1'
}

if ($Script:TranslateReady) {
    $translateCombination = ConvertTo-Hotkey $TranslateHotkeyText
    $translateRegistered = $false
    if ($translateCombination.key -gt 0) {
        $translateRegistered = [CloudHdrCompanion.Native]::RegisterHotKey(
            $helper.Handle, 3, [uint32]$translateCombination.modifiers, [uint32]$translateCombination.key)
    }
    if ($translateRegistered) { Write-Line ("Мгновенный перевод: {0}" -f $TranslateHotkeyText) }
    else { Write-Line ("Не удалось занять «{0}» для перевода — её держит другая программа." -f $TranslateHotkeyText) }
}

# ------------------------------------------------------------- таймеры -------

$TrackSeconds = 5

$ticker = New-Object Windows.Threading.DispatcherTimer
$ticker.Interval = [TimeSpan]::FromSeconds($TrackSeconds)
$ticker.Add_Tick({ if (-not $NoTracker) { Update-Usage $TrackSeconds } })
$ticker.Start()

$saver = New-Object Windows.Threading.DispatcherTimer
$saver.Interval = [TimeSpan]::FromSeconds(60)
$saver.Add_Tick({ Save-Usage })
$saver.Start()

# Опрос ответа: 60 мс незаметны глазу и не греют процессор.
$poller = New-Object Windows.Threading.DispatcherTimer
$poller.Interval = [TimeSpan]::FromMilliseconds(60)
$poller.Add_Tick({
    Complete-Question
    if ($Script:LensReady) { Complete-LensSearch }
    if ($Script:TranslateReady) { Complete-Translate }
})
$poller.Start()

# Ход работы руками. Четыре раза в секунду — этого хватает, чтобы шаги
# появлялись «сразу», и вчетверо меньше, чем у опроса ответа: перерисовка
# списка дороже, чем проверка одного флажка.
$handsTimer = New-Object Windows.Threading.DispatcherTimer
$handsTimer.Interval = [TimeSpan]::FromMilliseconds(250)
$handsTimer.Add_Tick({
    try { Update-CursorUi } catch { Write-Line ("Сбой показа работы: {0}" -f $_.Exception.Message) }
})
$handsTimer.Start()

# Поручения из окна приложения. Полсекунды задержки на файл, который в обычный
# день не меняется ни разу, — незаметно и человеку, и диску.
$inboxTimer = New-Object Windows.Threading.DispatcherTimer
$inboxTimer.Interval = [TimeSpan]::FromMilliseconds(500)
$inboxTimer.Add_Tick({
    try { Read-CursorInbox } catch { Write-Line ("Сбой чтения поручения: {0}" -f $_.Exception.Message) }
})
$inboxTimer.Start()

# ------------------------------------------------------------- запуск --------

Import-Usage

Write-Line "Cloud HDR Companion запущен"
if (-not $NoTracker) { Write-Line ("Учёт активности: раз в {0} с, файл {1}" -f $TrackSeconds, $UsageFile) }
if ($Script:CursorReady) { Write-Line ("Руки готовы: слово «{0}» в строке выдаёт разрешение на мышь" -f $CursorWord) }
else { Write-Line 'Руки недоступны: не нашёлся cursor-core.ps1' }

# Прогрев: первый «открой …» иначе ждёт, пока агент соберёт список программ.
try { [void](Invoke-RestMethod "$Base/api/apps" -TimeoutSec 20) } catch { }

$application = New-Object Windows.Application
$application.ShutdownMode = 'OnExplicitShutdown'

try {
    $application.Run()
} finally {
    Save-Usage
    if ($registered) { [void][CloudHdrCompanion.Native]::UnregisterHotKey($helper.Handle, 1) }
    $Script:Pool.Close()

    # Руки отпускаются вместе с приложением: незакрытое пространство держало бы
    # процесс живым после закрытия окна.
    if ($Script:CursorShared) { $Script:CursorShared['stop'] = $true }
    Close-CursorSpace
}
