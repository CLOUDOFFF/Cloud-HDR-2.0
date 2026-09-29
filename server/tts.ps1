<#
    Cloud HDR — голос помощника.

    Постоянно живущий синтезатор речи. Node запускает этот скрипт ОДИН раз и
    дальше только пишет ему запросы в stdin построчно; ответ приходит строкой в
    stdout. Так сделано ради задержки: сам синтез занимает 150-350 мс, а вот
    подъём powershell.exe с загрузкой WinRT — ещё 600-900 мс, и платить их на
    каждой фразе значило бы, что помощник начинает говорить через секунду после
    того, как ответ уже написан.

    Голос берётся из OneCore (Windows.Media.SpeechSynthesis), а не из SAPI5
    (System.Speech), и это главное решение здесь. В SAPI5 на этой системе видна
    одна русская «Microsoft Irina Desktop» — та самая механическая женская
    озвучка из Windows 7. OneCore отдаёт голоса нового поколения, и среди них
    есть МУЖСКОЙ «Microsoft Pavel»: спокойный, ровный, без металла — то, что и
    нужно помощнику. Обе библиотеки полностью локальные, ничего никуда не
    уходит; разница только в качестве.

    Протокол — по строке JSON в каждую сторону:
        <<  {"id":"7","text":"…","voice":"Pavel","rate":-6,"pitch":-2,"file":"…"}
        >>  {"id":"7","ok":true,"file":"…","bytes":153902,"ms":312}
        >>  {"id":"7","ok":false,"error":"…"}
    Готовый WAV кладётся в файл: гнать бинарные данные через stdout Windows
    PowerShell нельзя — поток текстовый и портит байты перекодировкой.
#>

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8

# --- подъём WinRT ------------------------------------------------------------
# Асинхронные методы WinRT возвращают IAsyncOperation, который Windows
# PowerShell 5.1 сам ждать не умеет. Переходник AsTask из System.Runtime.
# WindowsRuntime превращает его в обычный Task — дальше работает Wait().

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Wait-Winrt($operation, $resultType) {
    $task = $asTaskGeneric.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$synth  = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$voices = @([Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices)

<#
    Выбор голоса.

    Порядок предпочтения — не вкусовщина, а описание задачи: помощнику нужен
    ровный мужской русский голос. Явно названный голос уважается всегда; если
    его нет, ищем мужской русский, затем любой русский, затем что угодно —
    молчать из-за отсутствия конкретного имени было бы худшим исходом.
#>
function Select-Voice([string]$hint) {
    if ($hint) {
        $exact = $voices | Where-Object { $_.DisplayName -eq $hint -or $_.Id -eq $hint } | Select-Object -First 1
        if ($exact) { return $exact }
        $loose = $voices | Where-Object { $_.DisplayName -like "*$hint*" } | Select-Object -First 1
        if ($loose) { return $loose }
    }
    $male = $voices | Where-Object { $_.Language -like 'ru*' -and $_.Gender -eq 'Male' } | Select-Object -First 1
    if ($male) { return $male }
    $ru = $voices | Where-Object { $_.Language -like 'ru*' } | Select-Object -First 1
    if ($ru) { return $ru }
    return ($voices | Select-Object -First 1)
}

function Protect-Xml([string]$text) {
    $text.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;').Replace("'", '&apos;')
}

<#
    Просодия задаётся через SSML, а не свойствами объекта, потому что свойств
    для этого просто нет: WinRT-синтезатор умеет менять голос и громкость, но
    ни темпа, ни высоты тона у него в API не выведено. Через разметку доступно
    и то и другое.

    Высота считается в полутонах (st), а не процентах: полутон — величина
    музыкальная и на слух ровная, тогда как «-10%» на разных голосах звучит
    по-разному.
#>
function Build-Ssml([string]$text, $voice, [double]$rate, [double]$pitch) {
    $safe = Protect-Xml $text

    # Знак обязателен даже у нуля, разделитель — точка. Разметка ждёт
    # ОТНОСИТЕЛЬНУЮ величину, и «0st» для тона считает недопустимой: синтез
    # падает целиком. Точка вместо запятой — чтобы формат не зависел от языка
    # системы: на русской локали обычное форматирование даёт «-1,5».
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    $rateText  = [string]::Format($invariant, '{0:+0.##;-0.##;+0}%', $rate)
    $pitchText = [string]::Format($invariant, '{0:+0.##;-0.##;+0}st', $pitch)
    @"
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="$($voice.Language)">
<voice name="$($voice.DisplayName)"><prosody rate="$rateText" pitch="$pitchText">$safe</prosody></voice>
</speak>
"@
}

function Write-Line($object) {
    [Console]::Out.WriteLine(($object | ConvertTo-Json -Compress -Depth 4))
    [Console]::Out.Flush()
}

# --- рукопожатие: Node узнаёт список голосов, не задавая отдельного вопроса ---
Write-Line ([pscustomobject]@{
    id     = 'ready'
    ok     = $true
    voices = @($voices | ForEach-Object {
        [pscustomobject]@{ name = $_.DisplayName; language = $_.Language; gender = "$($_.Gender)" }
    })
})

# --- рабочий цикл ------------------------------------------------------------

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }          # Node закрыл stdin — выходим
    $line = $line.Trim()
    if (-not $line) { continue }

    $id = ''
    try {
        $request = $line | ConvertFrom-Json
        $id = "$($request.id)"

        $text = "$($request.text)".Trim()
        if (-not $text) { throw 'Пустой текст' }

        $watch = [Diagnostics.Stopwatch]::StartNew()
        $voice = Select-Voice "$($request.voice)"
        if ($synth.Voice.DisplayName -ne $voice.DisplayName) { $synth.Voice = $voice }

        $rate  = if ($null -ne $request.rate)  { [double]$request.rate }  else { 0 }
        $pitch = if ($null -ne $request.pitch) { [double]$request.pitch } else { 0 }

        $ssml   = Build-Ssml $text $voice $rate $pitch
        $stream = Wait-Winrt ($synth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])

        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        $null   = Wait-Winrt ($reader.LoadAsync([uint32]$stream.Size)) ([uint32])
        $bytes  = New-Object byte[] ([int]$stream.Size)
        $reader.ReadBytes($bytes)
        $reader.Dispose()
        $stream.Dispose()

        [IO.File]::WriteAllBytes($request.file, $bytes)
        $watch.Stop()

        Write-Line ([pscustomobject]@{
            id = $id; ok = $true; file = "$($request.file)"
            bytes = $bytes.Length; ms = $watch.ElapsedMilliseconds; voice = $voice.DisplayName
        })
    } catch {
        Write-Line ([pscustomobject]@{ id = $id; ok = $false; error = "$($_.Exception.Message)" })
    }
}
