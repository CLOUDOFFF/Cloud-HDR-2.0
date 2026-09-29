<#
    Cloud HDR Lens — умный поиск по обведённой области экрана.

    Ctrl+Shift+F — выходит маскот и просит обвести мышью то место, о котором
    хочется узнать. Область снимается, текст с неё читается офлайновым
    распознавателем Windows, а дальше помощник рассказывает, что это, и
    добавляет подсказки.

    ПОЧЕМУ ОБВОДКА, А НЕ «РАСПОЗНАЙ ВЕСЬ ЭКРАН». На экране всегда есть текст, и
    его много: панель задач, вкладки, чужие уведомления, наша же панель. Прочитав
    всё, помощник получил бы кашу и отвечал бы про случайное слово из неё.
    Обводка — это и есть вопрос: человек показывает пальцем, о чём спрашивает, и
    ничего другого сообщать не нужно.

    ЧТО ЗДЕСЬ СВОЁ, А ЧТО ЧУЖОЕ. Распознавание текста — встроенный в Windows
    движок (Windows.Media.Ocr), он работает офлайн, на этой машине, и русский
    язык у него есть. Это не сервис и не ключ: та же самая библиотека, что и
    System.Drawing, только для букв. Подсказки берёт cloudhdr_wiki.py — свой
    модуль проекта со своим HTTP-клиентом и своим разбором ответа; он ходит на
    открытые адреса Википедии без ключей, учётных записей и посредников. Ничего
    из того, что человек обвёл, никуда не отправляется: наружу уходит только
    короткий запрос по теме, и только когда без интернета не обойтись.
#>

# ------------------------------------------------------------------ Win32 ----
#
# Положение курсора берётся у Windows, а не у WPF, и это принципиально.
# WPF отдаёт координаты в своих единицах, зависящих от масштаба экрана, а снимок
# делается в настоящих пикселях. При масштабе 125% — обычном для ноутбуков —
# рамка и снимок разъезжаются на четверть, и человек получает картинку не того
# места, которое обвёл. GetCursorPos и CopyFromScreen живут в одной системе
# координат, поэтому берём обе оттуда.

if (-not ('CloudHdrLens.Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace CloudHdrLens {
    public static class Native {
        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
        [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);

        public static int[] Cursor() {
            POINT p; GetCursorPos(out p);
            return new int[] { p.X, p.Y };
        }

        /// <summary>Весь рабочий стол целиком: левый край, верх, ширина, высота.</summary>
        public static int[] Desktop() {
            return new int[] {
                GetSystemMetrics(76),   // SM_XVIRTUALSCREEN
                GetSystemMetrics(77),   // SM_YVIRTUALSCREEN
                GetSystemMetrics(78),   // SM_CXVIRTUALSCREEN
                GetSystemMetrics(79)    // SM_CYVIRTUALSCREEN
            };
        }
    }
}
'@ -ErrorAction Stop
}

$Script:LensRoot = Split-Path -Parent $PSCommandPath
$Script:LensLog  = Join-Path $Script:LensRoot 'logs\lens.log'
$Script:LensShot = Join-Path $Script:LensRoot 'logs\lens-shot.png'

function Write-Lens([string]$message) {
    try {
        $directory = Split-Path -Parent $Script:LensLog
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        Add-Content -LiteralPath $Script:LensLog -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message) -Encoding UTF8
    } catch { }
}

# --------------------------------------------------------------- обводка -----

<#
    Полупрозрачное окно во весь рабочий стол и рамка выделения.

    Затемнение нужно не для красоты: без него непонятно, что приложение чего-то
    ждёт, и обводка выглядит как зависший экран. С затемнением ясно с первого
    взгляда — экран «под стеклом», и по нему что-то выбирают.

    Escape и правая кнопка отменяют. Отмена обязана быть очевидной и мгновенной:
    режим, из которого не выйти, пугает сильнее, чем любая ошибка.

    Возвращает прямоугольник в настоящих пикселях экрана либо $null.
#>
function Show-LensSelection([string]$HintText = 'Обведите мышью область экрана, о которой хотите узнать.   Escape — отмена') {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

    $desktop = [CloudHdrLens.Native]::Desktop()
    $window = New-Object Windows.Window
    $window.WindowStyle = 'None'
    $window.AllowsTransparency = $true
    $window.Background = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromArgb(90, 31, 20, 11))
    $window.Topmost = $true
    $window.ShowInTaskbar = $false
    $window.Cursor = [Windows.Input.Cursors]::Cross
    $window.ResizeMode = 'NoResize'

    $canvas = New-Object Windows.Controls.Canvas
    $window.Content = $canvas

    $band = New-Object Windows.Shapes.Rectangle
    $band.Stroke = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromRgb(238, 123, 36))
    $band.StrokeThickness = 2
    $band.StrokeDashArray = New-Object Windows.Media.DoubleCollection (,[double[]](4, 3))
    $band.RadiusX = 6; $band.RadiusY = 6
    $band.Fill = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromArgb(40, 238, 123, 36))
    $band.Visibility = 'Collapsed'
    [void]$canvas.Children.Add($band)

    $hint = New-Object Windows.Controls.TextBlock
    $hint.Text = $HintText
    $hint.Foreground = New-Object Windows.Media.SolidColorBrush ([Windows.Media.Color]::FromRgb(255, 247, 234))
    $hint.FontSize = 15
    $hint.FontFamily = New-Object Windows.Media.FontFamily 'Segoe UI'
    $hint.Opacity = 0.92
    [void]$canvas.Children.Add($hint)

    $state = @{ dragging = $false; from = $null; to = $null; result = $null; scale = 1.0 }

    $window.Add_SourceInitialized({
        $source = [Windows.PresentationSource]::FromVisual($window)
        if ($source -and $source.CompositionTarget) {
            $matrix = $source.CompositionTarget.TransformToDevice
            if ($matrix.M11 -gt 0) { $state.scale = $matrix.M11 }
        }
        # Окно ставится в единицах WPF, а рабочий стол измерен в пикселях —
        # поэтому делим на масштаб. Без этого при 125% окно накрывало бы только
        # четыре пятых экрана, и правый край было бы не обвести вовсе.
        $window.Left = $desktop[0] / $state.scale
        $window.Top = $desktop[1] / $state.scale
        $window.Width = $desktop[2] / $state.scale
        $window.Height = $desktop[3] / $state.scale
        [Windows.Controls.Canvas]::SetLeft($hint, 24)
        [Windows.Controls.Canvas]::SetTop($hint, 24)
    })

    $redraw = {
        if (-not $state.from -or -not $state.to) { return }
        $left = [Math]::Min($state.from[0], $state.to[0])
        $top = [Math]::Min($state.from[1], $state.to[1])
        $width = [Math]::Abs($state.to[0] - $state.from[0])
        $height = [Math]::Abs($state.to[1] - $state.from[1])
        [Windows.Controls.Canvas]::SetLeft($band, ($left - $desktop[0]) / $state.scale)
        [Windows.Controls.Canvas]::SetTop($band, ($top - $desktop[1]) / $state.scale)
        $band.Width = $width / $state.scale
        $band.Height = $height / $state.scale
        $band.Visibility = 'Visible'
    }

    $window.Add_MouseLeftButtonDown({
        $state.dragging = $true
        $state.from = [CloudHdrLens.Native]::Cursor()
        $state.to = $state.from
        & $redraw
    })
    $window.Add_MouseMove({
        if (-not $state.dragging) { return }
        $state.to = [CloudHdrLens.Native]::Cursor()
        & $redraw
    })
    $window.Add_MouseLeftButtonUp({
        if (-not $state.dragging) { return }
        $state.dragging = $false
        $state.to = [CloudHdrLens.Native]::Cursor()
        $left = [Math]::Min($state.from[0], $state.to[0])
        $top = [Math]::Min($state.from[1], $state.to[1])
        $width = [Math]::Abs($state.to[0] - $state.from[0])
        $height = [Math]::Abs($state.to[1] - $state.from[1])

        # Слишком маленькая рамка — это не обводка, а случайный щелчок. Читать
        # с неё нечего, и честнее ничего не делать, чем показать пустой ответ.
        if ($width -lt 24 -or $height -lt 12) { $state.result = $null }
        else { $state.result = @{ x = $left; y = $top; width = $width; height = $height } }
        $window.Close()
    })
    $window.Add_MouseRightButtonDown({ $state.result = $null; $window.Close() })
    $window.Add_KeyDown({
        param($sender, $event)
        if ($event.Key -eq 'Escape') { $state.result = $null; $window.Close() }
    })

    [void]$window.ShowDialog()
    return $state.result
}

# ---------------------------------------------------------------- снимок -----

function Get-LensShot($rect) {
    Add-Type -AssemblyName System.Drawing
    $bitmap = New-Object Drawing.Bitmap $rect.width, $rect.height
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($rect.x, $rect.y, 0, 0, (New-Object Drawing.Size($rect.width, $rect.height)))
    } finally { $graphics.Dispose() }

    # Мелкий текст распознаётся заметно хуже крупного, а обводят чаще всего
    # именно мелкое — подпись, строку таблицы, надпись на кнопке. Увеличение
    # вдвое ничего не портит и заметно поднимает разборчивость: движку проще
    # различить «с» и «о», когда буква занимает двадцать точек, а не десять.
    if ($rect.width -lt 700 -and $rect.height -lt 400) {
        $big = New-Object Drawing.Bitmap ($rect.width * 2), ($rect.height * 2)
        $canvas = [Drawing.Graphics]::FromImage($big)
        $canvas.InterpolationMode = 'HighQualityBicubic'
        $canvas.DrawImage($bitmap, 0, 0, $big.Width, $big.Height)
        $canvas.Dispose()
        $bitmap.Dispose()
        $bitmap = $big
    }

    $directory = Split-Path -Parent $Script:LensShot
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $bitmap.Save($Script:LensShot, [Drawing.Imaging.ImageFormat]::Png)
    $bitmap.Dispose()
    return $Script:LensShot
}

# ------------------------------------------------------------ чтение букв ----

<#
    Ожидание WinRT из PowerShell 5.1.

    WinRT отдаёт не задачи .NET, а свои IAsyncOperation, и await для них в
    PowerShell нет. Переходник существует — AsTask из
    WindowsRuntimeSystemExtensions, — но он обобщённый, и нужную перегрузку
    приходится доставать отражением: их несколько, и различаются они только
    типами параметров.
#>
$Script:LensAwait = $null

function Initialize-LensOcr {
    if ($Script:LensAwait) { return $true }
    try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop
        $Script:LensAwait = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
            Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                           $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
        [void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
        [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
        [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
        [void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
        return $true
    } catch {
        Write-Lens ("Распознавание текста недоступно: {0}" -f $_.Exception.Message)
        $Script:LensAwait = $null
        return $false
    }
}

function Wait-Lens($operation, $type) {
    $task = $Script:LensAwait.MakeGenericMethod($type).Invoke($null, @($operation))
    [void]$task.Wait(20000)
    return $task.Result
}

function Read-LensText([string]$path) {
    if (-not (Initialize-LensOcr)) { return '' }
    try {
        $file = Wait-Lens ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Wait-Lens ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Wait-Lens ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-Lens ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

        # Русский движок берём явно. По языкам профиля Windows нередко отдаёт
        # английский, и тогда русская надпись читается как набор похожих на неё
        # латинских букв — хуже, чем не читается вовсе, потому что выглядит
        # правдоподобно.
        $engine = $null
        try {
            $language = New-Object Windows.Globalization.Language 'ru'
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        } catch { $engine = $null }
        if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
        if (-not $engine) { return '' }

        $result = Wait-Lens ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($line in $result.Lines) { $lines += [string]$line.Text }
        return ($lines -join "`n")
    } catch {
        Write-Lens ("Чтение не удалось: {0}" -f $_.Exception.Message)
        return ''
    }
}

# ---------------------------------------------------------------- вопрос -----

<#
    Из прочитанного — вопрос.

    Распознанное — это не вопрос, а обрывок интерфейса: заголовок, подпись,
    кусок абзаца. Спрашивать по нему целиком бессмысленно, поэтому выбирается
    ТЕМА: самая содержательная строка.

    Содержательность считается просто и работает. Служебные слова («ок»,
    «отмена», «файл», «настройки») тему не образуют — это части интерфейса, а не
    предмет разговора. Строка из одних цифр — тоже не тема. Из остального
    выигрывает та, где больше длинных слов: длинные слова несут смысл, короткие
    держат грамматику.
#>
$Script:LensNoise = @(
    'ок','отмена','да','нет','файл','правка','вид','справка','настройки','параметры',
    'закрыть','свернуть','развернуть','назад','вперед','далее','готово','применить',
    'сохранить','открыть','пуск','поиск','меню','вход','выход','ok','cancel','file','edit'
)

function Get-LensTopics([string]$text, [int]$limit = 3) {
    if (-not $text) { return @() }
    $ranked = @()

    foreach ($line in ($text -split "`n")) {
        $clean = ($line -replace '[^\p{L}\p{Nd}\s\-]', ' ') -replace '\s+', ' '
        $clean = $clean.Trim()
        if ($clean.Length -lt 3) { continue }
        $words = @($clean -split '\s+' | Where-Object { $_.Length -gt 1 })
        if ($words.Count -eq 0) { continue }
        if ($words.Count -eq 1 -and ($Script:LensNoise -contains $words[0].ToLower())) { continue }
        if ($clean -notmatch '\p{L}') { continue }

        $weight = 0
        $content = 0
        foreach ($word in $words) {
            if ($Script:LensNoise -contains $word.ToLower()) { continue }
            if ($word -match '^\d+$') { continue }
            $weight += [Math]::Min(8, $word.Length)
            $content++
        }
        if ($content -eq 0) { continue }

        # Делим на корень из числа слов, а не берём сумму как есть.
        #
        # Сумма выбирает самую ДЛИННУЮ строку, а не самую содержательную:
        # «площадь круга через радиус» набирает больше, чем «Гравитационная
        # постоянная», просто потому что слов вчетверо больше. Корень оставляет
        # длине влияние, но перестаёт делать её решающей — выигрывает строка с
        # длинными словами, то есть заголовок.
        $score = $weight / [Math]::Sqrt($content)

        # Заглавная буква в начале — признак названия, а не куска фразы.
        if ($clean.Substring(0, 1) -cmatch '\p{Lu}') { $score += 4 }
        # Длинная строка — это абзац. Тема из абзаца выходит расплывчатой.
        if ($content -gt 10) { $score = $score * 0.5 }

        $short = $clean
        $parts = @($short -split '\s+')
        if ($parts.Count -gt 8) { $short = ($parts[0..7] -join ' ') }
        $ranked += @{ topic = $short.Trim(); score = $score }
    }

    return @($ranked | Sort-Object { -$_.score } | Select-Object -First $limit | ForEach-Object { $_.topic })
}

<# Лучшая тема — для тех мест, где нужна ровно одна. #>
function Get-LensTopic([string]$text) {
    $topics = @(Get-LensTopics $text 1)
    if ($topics.Count -eq 0) { return '' }
    return $topics[0]
}

# ------------------------------------------------------------- подсказки -----

<#
    Работа, которую нельзя делать в потоке окна.

    Здесь и распознавание (до секунды на крупной области), и обращение в сеть
    (до нескольких секунд). Панель на это время замерла бы вместе с маскотом —
    то есть ровно тогда, когда человек ждёт ответа, помощник выглядел бы
    зависшим. Поэтому всё уезжает в отдельное пространство выполнения, а окно
    продолжает жить и показывать, что идёт работа.

    Скрипт получает только путь к снимку и корень проекта: пространство
    выполнения не наследует ни функций, ни переменных, и полагаться на них
    отсюда нельзя.
#>
$LensScript = {
    param($root, $shotPath)

    . (Join-Path $root 'lens.ps1')

    $text = Read-LensText $shotPath
    $topics = @(Get-LensTopics $text 3)
    $topic = if ($topics.Count -gt 0) { $topics[0] } else { '' }

    $python = $null
    foreach ($candidate in @(
        (Join-Path $root '..\runtime\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        'python.exe'
    )) {
        try {
            $resolved = if ($candidate -eq 'python.exe') { (Get-Command python -ErrorAction Stop).Source }
                        else { (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path }
            if ($resolved) { $python = $resolved; break }
        } catch { }
    }

    # Папка python ищется по списку, а не берётся соседней, и это не
    # перестраховка. Установщик папку python в себя НЕ кладёт — там веса на
    # десятки гигабайт (см. desktop\build.ps1). У установленной копии в
    # %LOCALAPPDATA%\Programs соседней папки python нет вовсе, и подсказки
    # молча не работали бы именно там, где приложением и пользуются, — а в
    # рабочей копии проекта всё выглядело бы прекрасно. Тот же список уже
    # применён в launch.ps1 для запуска моделей.
    $wiki = $null
    $roots = @()
    if ($env:CLOUDHDR_PYTHON) { $roots += $env:CLOUDHDR_PYTHON }
    try {
        $settings = Get-Content (Join-Path $root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($settings.PSObject.Properties['pythonDir'] -and $settings.pythonDir) {
            $roots += [Environment]::ExpandEnvironmentVariables([string]$settings.pythonDir)
        }
    } catch { }
    $roots += (Join-Path (Split-Path -Parent $root) 'python')
    $roots += (Join-Path $env:USERPROFILE 'cloud-hdr\python')
    $roots += (Join-Path $env:USERPROFILE 'Desktop\cloud-hdr\python')

    foreach ($candidate in $roots) {
        if (-not $candidate) { continue }
        $file = Join-Path $candidate 'cloudhdr_wiki.py'
        if (Test-Path -LiteralPath $file) { $wiki = $file; break }
    }

    # Темы пробуются по очереди, а не берётся одна лучшая.
    #
    # Выбор темы — догадка по нескольким строкам текста, и ошибиться в ней
    # нормально: обвели таблицу, лучшей строкой оказалась подпись к столбцу, а
    # спрашивали про заголовок. Одна попытка превращала бы такую догадку в
    # окончательный ответ «ничего не нашлось». Три попытки стоят секунды и
    # закрывают почти все такие промахи.
    $hint = $null
    if ($python -and $wiki) {
        foreach ($candidate in $topics) {
            if (-not $candidate) { continue }
            try {
                # Наружу уходит ТОЛЬКО тема — несколько слов. Ни снимок, ни
                # весь прочитанный текст компьютера не покидают.
                $raw = & $python $wiki $candidate 2>$null
                $joined = ($raw -join "`n").Trim()
                if ($joined -and $joined -ne 'ответа нет') {
                    $hint = $joined | ConvertFrom-Json
                    $topic = $candidate
                    break
                }
            } catch { }
        }
    }

    return @{ text = $text; topic = $topic; topics = $topics; hint = $hint }
}

<#
    Собрать ответ для панели.

    Сначала — что прочитано, потом — что об этом известно. Порядок важен: если
    распознано неверно, человек увидит это сразу и поймёт причину странного
    ответа, а не будет гадать.
#>
function Format-LensAnswer($result) {
    if (-not $result) { return 'Ничего не вышло прочитать.' }
    $text = [string]$result.text
    $topic = [string]$result.topic

    if (-not $text.Trim()) {
        return 'В обведённой области текста не нашлось. Обведите надпись — я читаю буквы, а не картинки.'
    }
    if (-not $topic) {
        $short = ($text -replace "`n", ' ')
        if ($short.Length -gt 160) { $short = $short.Substring(0, 160) + '…' }
        return ("Прочитал: «{0}». Тему из этого выделить не смог — обведите заголовок или название." -f $short)
    }

    $lines = @(("Прочитал: «{0}»." -f $topic))
    if ($result.hint -and $result.hint.text) {
        $lines += [string]$result.hint.text
        if ($result.hint.url) { $lines += [string]$result.hint.url }
    } else {
        $lines += 'В интернете об этом ничего не нашлось — либо нет связи, либо тема слишком узкая.'
    }
    return ($lines -join "`n")
}
