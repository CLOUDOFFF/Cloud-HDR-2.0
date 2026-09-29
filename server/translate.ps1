<#
    Cloud HDR — мгновенный перевод, Ctrl+Alt+T.

    Нажали клавиши — экран «под стеклом», обводим английский текст мышью, и
    прямо поверх него появляется перевод на русский. Без копирования, без
    вкладки с переводчиком и без интернета:

      • обводка и снимок   — те же, что у умного поиска (lens.ps1);
      • чтение букв        — встроенный в Windows движок OCR, английский;
      • перевод            — своя модель opus-mt-en-ru в python\cloudhdr_hub.py
                             (127.0.0.1:4480), на видеокарте доли секунды.

    Подключается к спутнику (companion.ps1) так же, как lens.ps1: точкой.
    Работа с OCR и сетью идёт в пространстве выполнения, чтобы карточка
    «Перевожу…» не замирала на время перевода.
#>

<#
    Английский текст с картинки.

    Движок берём английский ЯВНО, по той же причине, по которой lens.ps1 явно
    берёт русский: по языкам профиля Windows нередко отдаёт не тот, и тогда
    латиница читается как похожая на неё кириллица — правдоподобная каша.
#>
function Read-TranslateText([string]$path) {
    if (-not (Initialize-LensOcr)) { return '' }
    try {
        $file = Wait-Lens ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Wait-Lens ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Wait-Lens ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-Lens ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

        $engine = $null
        foreach ($tag in @('en-US', 'en-GB', 'en')) {
            try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $tag)) } catch { $engine = $null }
            if ($engine) { break }
        }
        if (-not $engine) { return '' }

        $result = Wait-Lens ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($line in $result.Lines) { $lines += [string]$line.Text }
        return ($lines -join "`n")
    } catch {
        Write-Lens ("Перевод: чтение не удалось: {0}" -f $_.Exception.Message)
        return ''
    }
}

#: Выполняется в пространстве выполнения: функций спутника там нет, поэтому
#: lens.ps1 и этот файл подключаются заново.
$TranslateScript = {
    param($root, $shotPath)

    . (Join-Path $root 'lens.ps1')
    . (Join-Path $root 'translate.ps1')

    $source = Read-TranslateText $shotPath
    if (-not $source.Trim()) {
        return @{ ok = $false; error = 'Английского текста в обведённой области не нашлось. Обведите строки целиком.' }
    }
    try {
        $body = [Text.Encoding]::UTF8.GetBytes((@{ text = $source } | ConvertTo-Json -Compress))
        $answer = Invoke-RestMethod 'http://127.0.0.1:4480/hub/translate' -Method Post -Body $body `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 60
        if ($answer.ok) { return @{ ok = $true; source = $source; text = [string]$answer.text; ms = [int]$answer.ms } }
        return @{ ok = $false; source = $source; error = [string]$answer.error }
    } catch {
        return @{ ok = $false; source = $source
                  error = 'Служба перевода не отвечает. Перезапустите Cloud HDR — она поднимается вместе с приложением.' }
    }
}

# ------------------------------------------------------------ карточка ------

function New-TranslateBrush([string]$hex) {
    return New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($hex))
}

<#
    Карточка с переводом поверх экрана — в цветах презентации: бумага, рамка
    цвета солнца и жёсткая тень. Ставится на место обведённого текста, чтобы
    взгляд не искал её по экрану; если там не помещается — ниже или выше.

    Закрывается Escape, щелчком по крестику и сама — когда человек ушёл в
    другое окно. Держать её дольше незачем: перевод уже прочитан.
#>
function Show-TranslateCard($rect) {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

    $window = New-Object Windows.Window
    $window.WindowStyle = 'None'
    $window.AllowsTransparency = $true
    $window.Background = [Windows.Media.Brushes]::Transparent
    $window.Topmost = $true
    $window.ShowInTaskbar = $false
    $window.ResizeMode = 'NoResize'
    $window.SizeToContent = 'Height'
    $window.ShowActivated = $true

    $shadow = New-Object Windows.Controls.Border
    $shadow.Background = New-TranslateBrush '#80D9AC70'
    $shadow.CornerRadius = New-Object Windows.CornerRadius 20
    $shadow.Margin = New-Object Windows.Thickness 10, 10, 0, 0

    $card = New-Object Windows.Controls.Border
    $card.Background = New-TranslateBrush '#FFF7EA'
    $card.BorderBrush = New-TranslateBrush '#EE7B24'
    $card.BorderThickness = New-Object Windows.Thickness 2
    $card.CornerRadius = New-Object Windows.CornerRadius 20
    $card.Padding = New-Object Windows.Thickness 18, 14, 18, 16
    $card.Margin = New-Object Windows.Thickness 0, 0, 10, 10

    $stack = New-Object Windows.Controls.StackPanel

    $head = New-Object Windows.Controls.DockPanel
    $head.LastChildFill = $false
    $tag = New-Object Windows.Controls.Border
    $tag.Background = New-TranslateBrush '#EE7B24'
    $tag.CornerRadius = New-Object Windows.CornerRadius 999
    $tag.Padding = New-Object Windows.Thickness 10, 3, 10, 3
    $tagText = New-Object Windows.Controls.TextBlock
    $tagText.Text = 'EN → RU'
    $tagText.FontWeight = 'Bold'
    $tagText.FontSize = 11
    $tagText.Foreground = New-TranslateBrush '#2A1608'
    $tag.Child = $tagText
    [Windows.Controls.DockPanel]::SetDock($tag, 'Left')
    [void]$head.Children.Add($tag)

    $status = New-Object Windows.Controls.TextBlock
    $status.Text = 'Cloud HDR'
    $status.Margin = New-Object Windows.Thickness 10, 2, 0, 0
    $status.FontSize = 12
    $status.Foreground = New-TranslateBrush '#6E4B2C'
    [Windows.Controls.DockPanel]::SetDock($status, 'Left')
    [void]$head.Children.Add($status)

    $close = New-Object Windows.Controls.Button
    $close.Content = '✕'
    $close.Width = 28; $close.Height = 28
    $close.Background = [Windows.Media.Brushes]::Transparent
    $close.BorderThickness = New-Object Windows.Thickness 0
    $close.Foreground = New-TranslateBrush '#6E4B2C'
    $close.Cursor = [Windows.Input.Cursors]::Hand
    [Windows.Controls.DockPanel]::SetDock($close, 'Right')
    [void]$head.Children.Add($close)
    [void]$stack.Children.Add($head)

    $body = New-Object Windows.Controls.TextBox
    $body.Text = 'Перевожу…'
    $body.IsReadOnly = $true
    $body.TextWrapping = 'Wrap'
    $body.BorderThickness = New-Object Windows.Thickness 0
    $body.Background = [Windows.Media.Brushes]::Transparent
    $body.Foreground = New-TranslateBrush '#3A2412'
    $body.FontSize = 15
    $body.FontFamily = New-Object Windows.Media.FontFamily 'Segoe UI'
    $body.Margin = New-Object Windows.Thickness -2, 12, 0, 0
    $body.MaxHeight = 420
    $body.VerticalScrollBarVisibility = 'Auto'
    [void]$stack.Children.Add($body)

    $copy = New-Object Windows.Controls.Button
    $copy.Content = 'Копировать перевод'
    $copy.HorizontalAlignment = 'Left'
    $copy.Margin = New-Object Windows.Thickness 0, 12, 0, 0
    $copy.Padding = New-Object Windows.Thickness 14, 6, 14, 6
    $copy.Background = New-TranslateBrush '#3A2412'
    $copy.Foreground = New-TranslateBrush '#FFF7EA'
    $copy.BorderThickness = New-Object Windows.Thickness 0
    $copy.Cursor = [Windows.Input.Cursors]::Hand
    $copy.Visibility = 'Collapsed'
    [void]$stack.Children.Add($copy)

    $card.Child = $stack
    $grid = New-Object Windows.Controls.Grid
    [void]$grid.Children.Add($shadow)
    [void]$grid.Children.Add($card)
    $window.Content = $grid

    # Место: поверх обведённого, шириной не меньше 360 и не больше 640.
    $scale = 1.0
    $window.Add_SourceInitialized({
        $source = [Windows.PresentationSource]::FromVisual($window)
        if ($source -and $source.CompositionTarget) { $scale = $source.CompositionTarget.TransformToDevice.M11 }
        $screen = [CloudHdrLens.Native]::Desktop()
        $width = [Math]::Min(640, [Math]::Max(360, $rect.width / $scale + 40))
        $left = [Math]::Max($screen[0] / $scale + 8, [Math]::Min($rect.x / $scale - 12, ($screen[0] + $screen[2]) / $scale - $width - 8))
        $top = [Math]::Max($screen[1] / $scale + 8, $rect.y / $scale - 14)
        $window.Width = $width
        $window.Left = $left
        $window.Top = [Math]::Min($top, ($screen[1] + $screen[3]) / $scale - 220)
    }.GetNewClosure())

    $close.Add_Click({ $window.Close() }.GetNewClosure())
    $copy.Add_Click({
        try { [Windows.Clipboard]::SetText($body.Text); $copy.Content = 'Скопировано ✓' } catch { }
    }.GetNewClosure())
    $window.Add_KeyDown({ param($s, $e) if ($e.Key -eq 'Escape') { $window.Close() } }.GetNewClosure())
    $window.Add_Deactivated({ $window.Close() }.GetNewClosure())

    $window.Show()
    [void]$window.Activate()
    return @{ window = $window; body = $body; status = $status; copy = $copy }
}
