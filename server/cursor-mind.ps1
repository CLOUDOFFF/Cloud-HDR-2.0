<#
    Cloud HDR Cursor Mind — понимание перед действием.

    ЧТО ЗДЕСЬ ЛЕЧИТСЯ. Раньше фраза превращалась в план одним switch по
    регулярным выражениям: «нарисуй (.+)» — рисуй, «открой (.+)» — запускай.
    Это работает ровно до первого шага в сторону и ломается одинаково во всех
    случаях — потому что план строился ИЗ СЛОВ, а не из того, что на самом деле
    есть на экране и на компьютере.

    Отсюда все жалобы разом. «Нажми сохранить» превращалось в поиск подстроки
    «сохранить» и промахивалось мимо кнопки «Сохранить как…». «Открой отчёт»
    искало программу с именем «отчёт», не находило и сдавалось — хотя отчёт
    лежит файлом в Документах. «Открой фотошоп» отвечало отказом, потому что в
    списке знакомых программ было семь штук: проводник, paint, блокнот,
    калькулятор, хром, edge, ворд. Всё остальное, что стоит на машине, для
    помощника не существовало.

    КАК УСТРОЕНО ТЕПЕРЬ. Четыре шага, и первый из них новый.

      1. ВОСПРИЯТИЕ. Прежде чем что-то решать, помощник смотрит: какое окно
         впереди, какие в нём есть кнопки, пункты меню, поля и строки списка,
         какие окна открыты вообще. Это дерево доступности — то же, чем
         пользуются экранные дикторы, — и оно даёт имена и координаты всего,
         что видно человеку.

      2. ПОНИМАНИЕ. Фразу разбирает обученная модель понимания команд
         (models\nlu, служба на 4478). Если она не поднята, работают свои
         правила — но правила теперь только про ДЕЙСТВИЕ («открой», «нажми»,
         «найди»), а не про цель.

      3. ЗАЗЕМЛЕНИЕ. Самое главное. Цель ищется среди того, что существует:
         среди элементов переднего окна, среди открытых окон, среди
         установленных программ, среди файлов и папок. Побеждает не тот, кто
         совпал по подстроке, а тот, кто ближе по смыслу и доступнее для
         действия. Не нашлось ничего — помощник так и говорит, а не делает
         наугад.

      4. ПЛАН. Шаги собираются под найденную цель. «Нажми сохранить» с
         найденной кнопкой — это клик по её координатам, а не поиск вслепую.

    ПОЧЕМУ ЗАЗЕМЛЕНИЕ ВАЖНЕЕ РАЗБОРА. Разобрать фразу можно как угодно
    хорошо — если цели не существует, план всё равно будет мусором. И наоборот:
    «эта штука» и «вон та кнопка» разбираются плохо, но если на экране ровно
    одна кнопка, понятно и без разбора. Поэтому вес решения перенесён со слов
    на экран.
#>

# ---------------------------------------------------------------- текст ------
#
# Сравнение имён вынесено в C# по той же причине, что и всё остальное тяжёлое:
# кандидатов бывает несколько сотен (строки списка в папке, пункты меню, имена
# программ), и на каждого считается расстояние Левенштейна. В PowerShell это
# заметная пауза перед каждым действием, в C# — доли миллисекунды.

if (-not ('CloudHdrMind.Text' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;

namespace CloudHdrMind {
    public static class Text {

        /// <summary>Расстояние Левенштейна с ранним выходом по потолку.</summary>
        public static int Distance(string a, string b, int ceiling) {
            if (string.IsNullOrEmpty(a)) return string.IsNullOrEmpty(b) ? 0 : b.Length;
            if (string.IsNullOrEmpty(b)) return a.Length;
            if (Math.Abs(a.Length - b.Length) > ceiling) return ceiling + 1;

            int[] previous = new int[b.Length + 1];
            int[] current = new int[b.Length + 1];
            for (int j = 0; j <= b.Length; j++) previous[j] = j;

            for (int i = 1; i <= a.Length; i++) {
                current[0] = i;
                int rowBest = current[0];
                for (int j = 1; j <= b.Length; j++) {
                    int cost = (a[i - 1] == b[j - 1]) ? 0 : 1;
                    int value = Math.Min(Math.Min(current[j - 1] + 1, previous[j] + 1), previous[j - 1] + cost);
                    current[j] = value;
                    if (value < rowBest) rowBest = value;
                }
                // Вся строка хуже потолка — дальше будет только хуже.
                if (rowBest > ceiling) return ceiling + 1;
                int[] swap = previous; previous = current; current = swap;
            }
            return previous[b.Length];
        }

        /// <summary>Близость двух имён, 0..1.</summary>
        public static double Similarity(string a, string b) {
            if (string.IsNullOrEmpty(a) || string.IsNullOrEmpty(b)) return 0;
            int longest = Math.Max(a.Length, b.Length);
            int ceiling = Math.Max(1, longest / 2);
            int distance = Distance(a, b, ceiling);
            if (distance > ceiling) return 0;
            return 1.0 - (double)distance / longest;
        }
    }
}
'@ -ErrorAction Stop
}

<#
    Приведение имени к сравнимому виду.

    Три вещи делаются здесь, и каждая закрывает свой класс промахов.

    ПЕРЕВОД НА ЛАТИНИЦУ. Программы называются по-английски, а говорят о них
    по-русски: «хром», «ворд», «стим», «фотошоп». Без перевода «хром» и
    «Chrome» — просто разные строки, и никакая нечёткость их не сблизит.

    ОТБРАСЫВАНИЕ ОКОНЧАНИЙ. Русский язык склоняет: «открой блокнот», «в
    блокноте», «блокнотом». Полноценная морфология здесь избыточна — достаточно
    срезать хвост, потому что сравнение всё равно нечёткое и опирается на
    начало слова.

    ЧИСТКА. Скобки, версии, суффиксы вида «(x64)» в именах установленных
    программ — шум, который сдвигает расстояние и никогда не произносится.
#>
$Script:MindTranslit = @{
    'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ж'='zh';'з'='z';'и'='i';'й'='y'
    'к'='k';'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u'
    'ф'='f';'х'='h';'ц'='c';'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'=''
    'э'='e';'ю'='yu';'я'='ya'
}

$Script:MindEndings = @(
    'ами','ями','ого','ему','ому','ыми','ими','ах','ях','ов','ев','ам','ям','ом','ем',
    'ой','ей','ый','ий','ая','яя','ое','ее','ые','ие','ку','ке','ки','ка','у','е','ы','и','а','я','о'
)

function ConvertTo-Loose([string]$text) {
    if (-not $text) { return '' }
    $value = $text.ToLower() -replace 'ё', 'е'
    $value = $value -replace '\s*\([^)]*\)', ' '          # «(x64)», «(64-bit)»
    $value = $value -replace '[^\p{L}\p{Nd}]+', ' '
    $value = $value.Trim()
    if (-not $value) { return '' }

    $builder = New-Object Text.StringBuilder
    foreach ($word in ($value -split '\s+')) {
        if (-not $word) { continue }
        $stem = $word
        # Окончание срезаем только у достаточно длинных слов: у «имя», «код»,
        # «вид» отрезать нечего, а срезав, мы превратим их в «им», «ко», «ви» и
        # склеим с чем попало.
        if ($stem.Length -ge 5) {
            foreach ($ending in $Script:MindEndings) {
                if ($stem.Length - $ending.Length -ge 4 -and $stem.EndsWith($ending)) {
                    $stem = $stem.Substring(0, $stem.Length - $ending.Length)
                    break
                }
            }
        }
        foreach ($char in $stem.ToCharArray()) {
            $key = [string]$char
            if ($Script:MindTranslit.ContainsKey($key)) { [void]$builder.Append($Script:MindTranslit[$key]) }
            else { [void]$builder.Append($key) }
        }
    }
    return $builder.ToString()
}

<#
    Согласный скелет имени.

    Решает задачу, на которой ломается любая транслитерация: одно и то же имя
    записывают по-русски и по-английски по-разному, и различаются они почти
    всегда ГЛАСНЫМИ и парой привычных диграфов. «Стим» и «Steam», «ворд» и
    «Word», «фотошоп» и «Photoshop» — побуквенно это разные строки, и никакая
    нечёткость их не сблизит: расстояние там слишком велико.

    А согласные совпадают. Выбросив гласные и приведя диграфы к одной букве,
    получаем stm ↔ stm, vrd ↔ vrd, ftsp ↔ ftsp. Это грубо, но грубость здесь
    к месту: имена программ коротки и различаются согласными, а не гласными.

    Скелет короче трёх букв не используется. «Ко», «ор», «ит» совпадут с чем
    угодно, и такое совпадение хуже, чем никакого.
#>
function Get-Skeleton([string]$loose) {
    if (-not $loose) { return '' }
    $value = $loose
    foreach ($pair in @(@('sch','s'), @('sh','s'), @('ch','h'), @('ph','f'), @('th','t'),
                        @('ck','k'), @('qu','kv'), @('zh','z'), @('x','ks'), @('w','v'),
                        @('c','k'), @('q','k'))) {
        $value = $value.Replace($pair[0], $pair[1])
    }
    return ($value -replace '[aeiouy]', '')
}

<#
    Имена, которые называют по-русски совсем другим словом.

    Транслитерация и скелет бессильны там, где слова просто разные: «блокнот»
    и Notepad не похожи ничем. Список короткий и намеренно таким остаётся —
    сюда входит только то, что человек действительно произносит вслух, а не
    полный словарь синонимов.
#>
$Script:MindSynonyms = @{
    'блокнот' = @('notepad'); 'калькулятор' = @('calculator', 'calc')
    'проводник' = @('explorer', 'file explorer'); 'папки' = @('explorer')
    'настройки' = @('параметры', 'settings'); 'параметры' = @('settings')
    'корзина' = @('recycle bin'); 'диспетчер задач' = @('task manager')
    'ножницы' = @('snipping tool'); 'краски' = @('paint'); 'рисовалка' = @('paint')
    'часы' = @('clock'); 'почта' = @('mail', 'outlook'); 'магазин' = @('store', 'microsoft store')
    'браузер' = @('chrome', 'edge', 'firefox'); 'терминал' = @('terminal', 'windows terminal')
    'фотографии' = @('photos'); 'проигрыватель' = @('media player')
    'командная строка' = @('cmd', 'command prompt'); 'ворд' = @('word'); 'эксель' = @('excel')
    # кнопки на сайтах и в плеерах — называют одним словом, подписаны другим
    'пауза' = @('приостановить', 'pause'); 'играть' = @('смотреть', 'воспроизвести', 'play')
    'воспроизвести' = @('смотреть', 'play'); 'сообщения' = @('мессенджер', 'messenger', 'чаты')
    'мессенджер' = @('сообщения', 'messenger'); 'поиск' = @('search', 'найти')
    'подписаться' = @('оформить подписку', 'subscribe'); 'лайк' = @('нравится', 'like')
    'войти' = @('вход', 'sign in', 'log in'); 'далее' = @('next', 'продолжить')
    'следующее' = @('следующее видео', 'next'); 'полный экран' = @('полноэкранн', 'fullscreen')
    'звук' = @('отключить звук', 'включить звук', 'mute')
    'ютуб' = @('youtube'); 'ютьюб' = @('youtube'); 'вк' = @('vk', 'вконтакте'); 'вконтакте' = @('vk')
    'яндекс музыка' = @('yandex music', 'яндекс музыка'); 'телеграм' = @('telegram'); 'дискорд' = @('discord')
    'стим' = @('steam'); 'хром' = @('chrome'); 'гугл' = @('google')
}

<#
    Насколько имя кандидата отвечает тому, что попросили.

    Порядок проверок — от бесспорного к натянутому, и каждая ступень заметно
    ниже предыдущей. Это не шкала «похожести»: это шкала УВЕРЕННОСТИ, и провал
    между ступенями нужен, чтобы точное совпадение никогда не проиграло
    длинному нечёткому.
#>
function Get-NameScore([string]$needle, [string]$candidate) {
    if (-not $needle -or -not $candidate) { return 0 }

    # Синоним пробуется наравне с самим словом, и берётся лучший результат.
    # Заменять слово синонимом нельзя: «настройки» бывают и настройками —
    # в окне вполне может быть кнопка ровно с таким именем.
    $words = @($needle)
    $key = ($needle -replace 'ё','е').Trim().ToLower()
    if ($Script:MindSynonyms.ContainsKey($key)) { $words += $Script:MindSynonyms[$key] }

    # Кандидат сравнивается и целиком, и по отдельным словам.
    #
    # Без разбора по словам ломается самый частый случай из всех: программы
    # называются с именем производителя впереди — «Google Chrome», «Adobe
    # Photoshop», «Microsoft Word», — а человек произносит только второе слово.
    # Целиком «хром» с «Google Chrome» не сходится ничем: ни началом, ни
    # скелетом (тот получается «gglhrm» против «hrm»). По словам — сходится
    # сразу и точно.
    #
    # Совпадение по слову стоит на несколько очков дешевле совпадения целиком.
    # Это решает спор между «Word» и «Microsoft Word» в пользу более точного,
    # когда в списке есть оба.
    $forms = @(@{ text = $candidate; penalty = 0 })
    foreach ($part in ($candidate -split '[^\p{L}\p{Nd}]+')) {
        if ($part.Length -ge 3) { $forms += @{ text = $part; penalty = 6 } }
    }

    $best = 0
    foreach ($form in $forms) {
        $b = ConvertTo-Loose $form.text
        if (-not $b) { continue }
        $skeletonB = Get-Skeleton $b

        foreach ($word in $words) {
            $a = ConvertTo-Loose $word
            if (-not $a) { continue }

            $score = 0
            if ($a -eq $b) { $score = 100 }
            elseif ($b.StartsWith($a)) { $score = 88 - [Math]::Min(8, ($b.Length - $a.Length) / 4) }
            elseif ($b.Contains($a)) { $score = 74 - [Math]::Min(10, ($b.Length - $a.Length) / 4) }
            elseif ($a.Contains($b) -and $b.Length -ge 3) { $score = 66 }
            else {
                # Нечёткое сравнение — предпоследняя ступень, и порог у неё
                # высокий. Опечатки распознавания бывают, но «сохранить» не
                # должно находить «сохранение состояния»: цена ошибочного
                # клика выше цены переспроса.
                $similarity = [CloudHdrMind.Text]::Similarity($a, $b)
                if ($similarity -ge 0.78) { $score = [int](40 + ($similarity - 0.78) * 100) }
            }

            # Скелет — последняя попытка, и очков он даёт меньше прямого
            # совпадения начала. Это сознательно: скелет грубее, и там, где
            # есть настоящее совпадение, оно должно побеждать.
            if ($score -lt 80) {
                $skeletonA = Get-Skeleton $a
                if ($skeletonA.Length -ge 3 -and $skeletonB.Length -ge 3) {
                    if ($skeletonA -eq $skeletonB) { $score = [Math]::Max($score, 80) }
                    elseif ($skeletonB.StartsWith($skeletonA)) { $score = [Math]::Max($score, 70) }
                }
            }

            if ($score -gt 0) { $score -= $form.penalty }
            if ($score -gt $best) { $best = $score }
        }
    }
    return $best
}

# ------------------------------------------------------------ восприятие -----

<#
    Что сейчас на экране.

    Собирается ОДИН раз на план, а не на каждый шаг: обход дерева окна стоит
    от десятков миллисекунд до секунды, и опрашивать его на каждое слово фразы
    значило бы думать дольше, чем делать. Между шагами сцена обновляется
    точечно — там, где шаг мог её изменить.

    Строки списка берутся отдельным запросом и с меньшим лимитом. В папке на
    тысячу файлов полный обход — это секунды, а нужно из него почти всегда
    первое совпадение.
#>
function Get-Scene([switch]$Deep) {
    $scene = @{ front = $null; items = @(); windows = @(); at = (Get-Date) }

    try { $scene.windows = @(Get-Windows) } catch { $scene.windows = @() }

    $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
    if ($handle -ne [IntPtr]::Zero) {
        try { $scene.front = Get-WindowInfo $handle } catch { $scene.front = $null }
    }

    if ($scene.front) {
        $items = @()
        # Управляющие элементы — сначала и целиком: их немного, а именно по ним
        # выполняются почти все просьбы вида «нажми …».
        foreach ($type in @('Button', 'MenuItem', 'TabItem', 'CheckBox', 'RadioButton', 'Hyperlink')) {
            try { $items += @(Get-UiItems $scene.front $type 120) } catch { }
        }
        try { $items += @(Get-UiItems $scene.front 'Edit' 40) } catch { }
        if ($Deep) {
            try { $items += @(Get-UiItems $scene.front 'ListItem' 300) } catch { }
            try { $items += @(Get-UiItems $scene.front 'TreeItem' 120) } catch { }
        }
        $scene.items = @($items | Where-Object { $_.name -and $_.name.Trim() })
    }
    return $scene
}

# -------------------------------------------------------------- словари ------

<#
    Живой список установленных программ.

    Пришёл на смену карте из семи имён, которая и была причиной жалобы «курсор
    умеет только блокнот». Источников три, и они дополняют друг друга:

      меню «Пуск» — то, что человек видит и как оно там названо;
      реестр удаления — то, что установлено, включая не попавшее в меню;
      папки Steam — игры, которых нет ни там, ни там под своими именами.

    Кэш на десять минут: обход занимает доли секунды, но он не бесплатный, а
    список программ между двумя фразами не меняется.
#>
$Script:MindApps = $null
$Script:MindAppsAt = [datetime]::MinValue

function Get-MindApps {
    if ($Script:MindApps -and ((Get-Date) - $Script:MindAppsAt).TotalMinutes -lt 10) {
        return $Script:MindApps
    }

    $apps = @()
    $seen = @{}

    function Add-App([string]$name, [string]$path, [string]$source) {
        if (-not $name -or -not $path) { return }
        $key = $name.ToLower()
        if ($seen.ContainsKey($key)) { return }
        $seen[$key] = $true
        $script:collected += @{ name = $name; path = $path; source = $source }
    }

    $script:collected = @()

    # Меню «Пуск» глазами самой Windows — главный источник, и он идёт первым.
    #
    # Обход папок с ярлыками, который стоит ниже, находит только классические
    # программы. Половины встроенных приложений там нет вовсе: Блокнот,
    # Калькулятор, Paint в Windows 11 — пакеты из магазина, у них нет ни .lnk,
    # ни записи в App Paths, и найти их обходом файлов невозможно в принципе.
    # Именно поэтому «открой блокнот» раньше отвечало «не нашёл» на машине, где
    # блокнот, разумеется, есть.
    #
    # Отдельная ценность источника — имена. Windows отдаёт их так, как человек
    # видит их в меню: «Блокнот», а не Notepad. Это ровно те слова, которыми
    # команду и произносят.
    try {
        foreach ($item in (Get-StartApps -ErrorAction Stop)) {
            if (-not $item.Name -or -not $item.AppID) { continue }
            # Через оболочку запускается ВСЁ отсюда, без разбора видов.
            #
            # Соблазн отличить «обычную программу» от пакета и запускать первую
            # напрямую здесь есть, и он ведёт в тупик: AppID у обычных программ
            # — это не путь к файлу, а имя внутри папки приложений, иногда в
            # виде {GUID}\Steam\Steam.exe. Как путь оно не существует, и запуск
            # падал бы именно на самых обычных программах. shell:AppsFolder
            # понимает все формы разом — это и есть то, чем открывает их само
            # меню «Пуск».
            Add-App ([string]$item.Name) ("shell:AppsFolder\{0}" -f $item.AppID) 'меню'
        }
    } catch { }

    foreach ($root in @(
        [Environment]::GetFolderPath('CommonStartMenu'),
        [Environment]::GetFolderPath('StartMenu')
    )) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        try {
            Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 400 | ForEach-Object {
                    Add-App $_.BaseName $_.FullName 'Пуск'
                }
        } catch { }
    }

    foreach ($key in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths'
    )) {
        try {
            Get-ChildItem $key -ErrorAction SilentlyContinue | ForEach-Object {
                $exe = $_.PSChildName
                $full = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).'(default)'
                if ($full) { Add-App ([IO.Path]::GetFileNameWithoutExtension($exe)) $full 'система' }
            }
        } catch { }
    }

    # Steam: имя игры лежит в манифесте, а не в имени папки, и запускается она
    # по номеру через steam://. Пытаться найти её .exe бессмысленно — у половины
    # игр он называется никак не как игра.
    try {
        $steam = (Get-ItemProperty 'HKCU:\SOFTWARE\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
        if ($steam) {
            $libraries = @($steam)
            $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
            if (Test-Path -LiteralPath $vdf) {
                foreach ($line in (Get-Content -LiteralPath $vdf -ErrorAction SilentlyContinue)) {
                    if ($line -match '"path"\s+"(.+?)"') { $libraries += ($Matches[1] -replace '\\\\', '\') }
                }
            }
            foreach ($library in ($libraries | Select-Object -Unique)) {
                $steamapps = Join-Path $library 'steamapps'
                if (-not (Test-Path -LiteralPath $steamapps)) { continue }
                Get-ChildItem -LiteralPath $steamapps -Filter 'appmanifest_*.acf' -ErrorAction SilentlyContinue |
                    ForEach-Object {
                        $text = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
                        if ($text -match '"appid"\s+"(\d+)"' ) { $id = $Matches[1] } else { return }
                        if ($text -match '"name"\s+"(.+?)"') { $title = $Matches[1] } else { return }
                        Add-App $title ("steam://rungameid/{0}" -f $id) 'Steam'
                    }
            }
        }
    } catch { }

    # Классика из system32 — последней и только если её ещё не нашли выше.
    # На Windows 10 это основной способ добраться до Блокнота и Paint, на
    # Windows 11 — запасной для случая, когда пакет удалён, а файл остался.
    foreach ($pair in @(
        @('Блокнот', 'notepad.exe'), @('Калькулятор', 'calc.exe'), @('Paint', 'mspaint.exe'),
        @('Проводник', 'explorer.exe'), @('Командная строка', 'cmd.exe'),
        @('Панель управления', 'control.exe'), @('Диспетчер задач', 'taskmgr.exe'),
        @('Ножницы', 'snippingtool.exe'), @('Параметры', 'ms-settings:'),
        @('Регистратор действий', 'psr.exe'), @('Таблица символов', 'charmap.exe')
    )) {
        $exe = $pair[1]
        if ($exe -notmatch ':$') {
            $full = Join-Path $env:WINDIR ("system32\{0}" -f $exe)
            if (-not (Test-Path -LiteralPath $full)) { continue }
            $exe = $full
        }
        Add-App $pair[0] $exe 'система'
    }

    $apps = $script:collected
    $Script:MindApps = $apps
    $Script:MindAppsAt = Get-Date
    return $apps
}

<#
    Файлы и папки пользователя.

    Сначала спрашиваем индекс, который строит служба понимания: он уже есть,
    он умеет находить «клоуд мап» по имени «Cloud map» и отвечает за
    миллисекунды. Службы нет — обходим сами, но только обычные папки и без
    рекурсии вглубь: полный обход диска посреди выполнения команды человек
    воспринимает как зависание.
#>
function Get-MindFiles([string]$query, [int]$limit = 12) {
    if (-not $query) { return @() }

    # К индексу идём только тогда, когда служба точно жива, и проверка эта
    # кэшируется на минуту. Иначе каждый запрос к незапущенной службе стоил бы
    # пары секунд: обращение к закрытому порту не отказывает мгновенно, Windows
    # честно повторяет попытку соединения. Именно это и превращало «открой
    # фотошоп» на машине без фотошопа в трёхсекундное раздумье.
    if (Test-MindNlu) {
      try {
        $found = Invoke-RestMethod -Uri ("http://127.0.0.1:4478/files/search?q={0}&limit={1}" -f
                                         [Uri]::EscapeDataString($query), $limit) -TimeoutSec 4
        if ($found -and $found.items) {
            return @($found.items | ForEach-Object {
                @{ name = [string]$_.name; path = [string]$_.path
                   kind = if ($_.PSObject.Properties['kind']) { [string]$_.kind } else { 'file' }
                   source = 'индекс' }
            })
        }
      } catch { }
    }

    # Запасной обход идёт со счётчиком времени, и это не перестраховка.
    #
    # Без него «открой фотошоп» на машине без фотошопа занимало почти три
    # секунды: цель не находилась нигде, дело доходило сюда, и обход четырёх
    # папок на два уровня вглубь честно перебирал тысячи имён — чтобы в итоге
    # тоже ничего не найти. Человек всё это время видел замерший курсор.
    #
    # Полсекунды — это заметно больше, чем нужно на обычные папки, и заведомо
    # меньше, чем требуется, чтобы ожидание стало похоже на зависание. Кому
    # нужен полный поиск по диску — тому служба понимания строит настоящий
    # индекс, и тогда сюда не заходят вовсе.
    $results = @()
    $budget = [Diagnostics.Stopwatch]::StartNew()
    foreach ($root in @(
        [Environment]::GetFolderPath('Desktop'),
        (Join-Path $env:USERPROFILE 'Downloads'),
        [Environment]::GetFolderPath('MyDocuments'),
        [Environment]::GetFolderPath('MyPictures')
    )) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        if ($budget.ElapsedMilliseconds -gt 500) { break }
        try {
            foreach ($entry in (Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue -Depth 1)) {
                if ($budget.ElapsedMilliseconds -gt 500) { break }
                if ((Get-NameScore $query $entry.BaseName) -lt 66) { continue }
                $results += @{ name = $entry.Name; path = $entry.FullName
                               kind = if ($entry.PSIsContainer) { 'folder' } else { 'file' }
                               source = 'обход' }
                if ($results.Count -ge $limit) { break }
            }
        } catch { }
        if ($results.Count -ge $limit) { break }
    }
    return $results
}

$Script:MindFolders = @{
    'рабочий стол' = 'Desktop'; 'десктоп' = 'Desktop'
    'документы' = 'MyDocuments'; 'мои документы' = 'MyDocuments'
    'загрузки' = 'Downloads'; 'скачанное' = 'Downloads'
    'картинки' = 'MyPictures'; 'изображения' = 'MyPictures'; 'фото' = 'MyPictures'
    'музыка' = 'MyMusic'; 'видео' = 'MyVideos'
}

function Resolve-MindFolder([string]$name) {
    $key = ($name -replace 'ё', 'е').Trim().ToLower()
    foreach ($known in $Script:MindFolders.Keys) {
        if ((Get-NameScore $key $known) -ge 74) {
            $folder = $Script:MindFolders[$known]
            $path = if ($folder -eq 'Downloads') { Join-Path $env:USERPROFILE 'Downloads' }
                    else { [Environment]::GetFolderPath($folder) }
            if ($path -and (Test-Path -LiteralPath $path)) {
                return @{ kind = 'folder'; name = $known; path = $path; score = 95 }
            }
        }
    }
    # Полный путь, названный целиком, — тоже допустимый ответ.
    if ($name -match '^[a-zA-Z]:\\' -and (Test-Path -LiteralPath $name)) {
        $isFolder = (Get-Item -LiteralPath $name).PSIsContainer
        return @{ kind = if ($isFolder) { 'folder' } else { 'file' }; name = $name; path = $name; score = 100 }
    }
    return $null
}

# ----------------------------------------------------------- заземление ------

<#
    Найти то, о чём говорят.

    Кандидаты собираются из всех источников сразу и соревнуются по одной шкале.
    Приоритет источника добавляется к очкам имени, и порядок приоритетов —
    это ответ на вопрос «что человек имел в виду с большей вероятностью».

    Первым идёт ПЕРЕДНЕЕ ОКНО. Если человек смотрит на открытый Word и говорит
    «нажми сохранить», он говорит о кнопке перед собой, а не о программе с
    похожим именем. Дальше — открытые окна: «перейди в проводник» про уже
    открытый Проводник, а не про запуск второго. Потом программы, потом файлы.

    Файлы идут последними не потому, что они менее важны, а потому, что их
    больше всего: имя «отчёт» найдётся среди файлов почти наверняка, и пусти
    их вперёд — они выигрывали бы у кнопок и программ просто числом.
#>
$Script:MindHints = @(
    @{ prefer = 'folder'; words = @('папку', 'папка', 'папке', 'каталог', 'директорию') }
    @{ prefer = 'file';   words = @('файл', 'файлик', 'документ', 'картинку', 'фотографию', 'песню', 'видео') }
    @{ prefer = 'app';    words = @('программу', 'программа', 'приложение', 'приложуху', 'игру', 'игрушку') }
    @{ prefer = 'ui';     words = @('кнопку', 'пункт', 'значок', 'вкладку', 'галочку', 'поле') }
)

<#
    Убрать уточняющее слово и превратить его в подсказку.

    «Открой ПАПКУ загрузки» — здесь «папку» не часть имени, а указание, где
    искать. Оставленное в имени, оно ломает поиск дважды: точного совпадения с
    «Загрузки» больше нет, зато появляется шанс совпасть с чем-нибудь
    посторонним по куску строки. Ровно так «открой папку загрузки» однажды
    открыло «Website 2 APK Builder Pro» — совпадение нашлось там, где его никто
    не искал.

    Слово не выбрасывается впустую: оно становится предпочтением, и папка
    выигрывает у программы с похожим именем именно благодаря ему.
#>
function Split-MindHint([string]$name) {
    $value = $name.Trim()
    $prefer = ''
    foreach ($hint in $Script:MindHints) {
        foreach ($word in $hint.words) {
            if ($value -match ("^{0}\s+(.+)$" -f [regex]::Escape($word))) {
                return @{ name = $Matches[1].Trim(); prefer = $hint.prefer }
            }
        }
    }
    return @{ name = $value; prefer = $prefer }
}

function Resolve-Target($scene, [string]$name, [string]$prefer = '') {
    if (-not $name) { return $null }
    $split = Split-MindHint $name
    if ($split.prefer -and -not $prefer) { $prefer = $split.prefer }
    $needle = $split.name.Trim(' ', '"', '«', '»', '.', ',')
    if (-not $needle) { return $null }

    $candidates = @()

    if ($scene -and $scene.items) {
        foreach ($item in $scene.items) {
            $score = Get-NameScore $needle $item.name
            if ($score -le 0) { continue }
            # Невидимую точку не предлагаем вовсе: клик по ней попадёт не туда,
            # и это худший исход — помощник уверен, что нажал, а не нажал.
            if (-not (Test-PointFree $scene.front $item.x $item.y)) { continue }
            $candidates += @{ kind = 'ui'; name = $item.name; item = $item
                              score = $score + 30; why = 'элемент окна' }
        }
    }

    if ($scene -and $scene.windows) {
        foreach ($window in $scene.windows) {
            $title = [string]$window.title
            if (-not $title) { continue }
            $score = Get-NameScore $needle $title
            if ($score -le 0) { continue }
            $candidates += @{ kind = 'window'; name = $title; window = $window
                              score = $score + 18; why = 'открытое окно' }
        }
    }

    $folder = Resolve-MindFolder $needle
    if ($folder) {
        $candidates += @{ kind = $folder.kind; name = $folder.name; path = $folder.path
                          score = $folder.score + 12; why = 'папка' }
    }

    foreach ($app in (Get-MindApps)) {
        $score = Get-NameScore $needle $app.name
        if ($score -le 0) { continue }
        $candidates += @{ kind = 'app'; name = $app.name; path = $app.path
                          score = $score + 10; why = ("программа · {0}" -f $app.source) }
    }

    # За файлами идём только тогда, когда ничего убедительного не нашлось:
    # запрос к индексу стоит миллисекунды, но обход по запасному пути — секунды.
    $best = $candidates | Sort-Object { -$_.score } | Select-Object -First 1
    if (-not $best -or $best.score -lt 80 -or $prefer -eq 'file') {
        foreach ($file in (Get-MindFiles $needle 8)) {
            $score = Get-NameScore $needle $file.name
            if ($score -le 0) { continue }
            $candidates += @{ kind = $file.kind; name = $file.name; path = $file.path
                              score = $score + 4; why = ("файл · {0}" -f $file.source) }
        }
    }

    if ($prefer) {
        foreach ($candidate in $candidates) {
            if ($candidate.kind -eq $prefer) { $candidate.score += 25 }
        }
    }

    $ranked = @($candidates | Sort-Object { -$_.score })
    if ($ranked.Count -eq 0) { return $null }

    $winner = $ranked[0]
    $winner.rival = if ($ranked.Count -gt 1) { $ranked[1] } else { $null }
    return $winner
}

# -------------------------------------------------------------- разбор -------

<#
    Действие, которое просят совершить.

    Здесь остались ТОЛЬКО глаголы — то, что в русском языке действительно
    определяется словом. Цель отсюда убрана намеренно: её ищет заземление, и
    это единственное место, где о ней можно судить.
#>
$Script:MindVerbs = @(
    @{ act = 'stop';    words = @('стоп','хватит','остановись','отмена','прекрати') }
    @{ act = 'click';   words = @('нажми','кликни','щелкни','щёлкни','тапни','жми','выбери','ткни') }
    @{ act = 'open';    words = @('открой','запусти','включи','открыть','запустить','покажи','перейди') }
    @{ act = 'close';   words = @('закрой','выключи','закрыть','сверни') }
    @{ act = 'type';    words = @('напиши','напечатай','введи','набери','печатай') }
    @{ act = 'find';    words = @('найди','найти','поищи','ищи') }
    @{ act = 'scroll';  words = @('полистай','пролистай','прокрути','листай','промотай','скролл') }
    @{ act = 'draw';    words = @('нарисуй','нарисовать','рисуй','изобрази') }
    @{ act = 'save';    words = @('сохрани','сохранить') }
    @{ act = 'copy';    words = @('скопируй','копируй') }
    @{ act = 'paste';   words = @('вставь','вставить') }
    @{ act = 'delete';  words = @('удали','удалить') }
    @{ act = 'rename';  words = @('переименуй','переименовать') }
    @{ act = 'wait';    words = @('подожди','погоди') }
)

function Get-MindVerb([string]$chunk) {
    $words = @(($chunk -replace 'ё','е').ToLower() -split '[^\p{L}\p{Nd}]+' | Where-Object { $_ })
    if ($words.Count -eq 0) { return $null }

    # Глагол ищется в первых двух словах: «пожалуйста открой», «а теперь нажми».
    # Дальше искать нельзя — «открой папку и закрой окно» иначе нашло бы
    # «закрой» в первой же части.
    for ($i = 0; $i -lt [Math]::Min(2, $words.Count); $i++) {
        foreach ($verb in $Script:MindVerbs) {
            foreach ($word in $verb.words) {
                if ($words[$i] -eq $word -or (Get-NameScore $words[$i] $word) -ge 88) {
                    $rest = @($words[($i + 1)..($words.Count - 1)]) -join ' '
                    if ($i + 1 -ge $words.Count) { $rest = '' }
                    return @{ act = $verb.act; word = $words[$i]; rest = $rest.Trim() }
                }
            }
        }
    }
    return @{ act = ''; word = ''; rest = ($words -join ' ') }
}

<#
    Спросить обученную модель.

    Это своя модель из models\nlu, обученная на своих данных, и работает она
    без сети. Если служба не поднята — не беда: ниже есть свои правила. Ждать
    её долго нельзя, поэтому срок ожидания короткий: понимание, за которым
    приходится ждать секунду, хуже понимания попроще, но мгновенного.
#>
$Script:MindNluAlive = $null
$Script:MindNluAt = [datetime]::MinValue

function Test-MindNlu {
    if ($null -ne $Script:MindNluAlive -and ((Get-Date) - $Script:MindNluAt).TotalSeconds -lt 60) {
        return $Script:MindNluAlive
    }
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:4478/nlu/health' -TimeoutSec 1
        $Script:MindNluAlive = $true
    } catch { $Script:MindNluAlive = $false }
    $Script:MindNluAt = Get-Date
    return $Script:MindNluAlive
}

function Get-MindNlu([string]$text) {
    if (-not (Test-MindNlu)) { return $null }
    try {
        $body = @{ text = $text } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:4478/nlu/plan' -Method Post -Body $bytes `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 3 -UseBasicParsing
        return ([Text.Encoding]::UTF8.GetString($response.Content) | ConvertFrom-Json)
    } catch {
        $Script:MindNluAlive = $false
        return $null
    }
}

# ---------------------------------------------------------------- план -------

<#
    Собрать план под то, что есть на самом деле.

    Фраза режется на части так же, как раньше, — люди действительно говорят
    поручения через «и», «потом», «затем». Но дальше начинается разница: для
    каждой части сначала находится ЦЕЛЬ среди существующего, и только потом
    выбирается шаг. Не нашлось цели — в план идёт честный отказ с объяснением,
    а не действие наугад.
#>
function New-MindPlan([string]$text, $scene = $null) {
    $normalized = ($text -replace 'ё', 'е').Trim()
    if (-not $normalized) { return @() }

    if (-not $scene) { $scene = Get-Scene }

    $chunks = @($normalized -split '(?:\s+и\s+|\s*,\s*потом\s+|\s*,\s*затем\s+|\s+потом\s+|\s+затем\s+|\s*;\s*)' |
                ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $plan = @()

    foreach ($chunk in $chunks) {
        $verb = Get-MindVerb $chunk
        $act = if ($verb) { $verb.act } else { '' }
        $rest = if ($verb) { $verb.rest } else { $chunk }

        switch ($act) {

            'stop' { $plan += New-Step 'stop' @{ say = 'Останавливаюсь' }; break }

            'wait' {
                $seconds = 2
                if ($chunk -match '(\d{1,2})') { $seconds = [Math]::Min(20, [int]$Matches[1]) }
                $plan += New-Step 'wait' @{ ms = $seconds * 1000; say = ("Жду {0} с" -f $seconds) }
                break
            }

            'draw' {
                $subject = $rest -replace '^(мне|нам|пожалуйста|свой|свое|свою)\s+', ''
                if (-not $subject) { $subject = 'логотип' }
                $plan += New-Step 'draw' @{ what = $subject; say = ("Рисую: {0}" -f $subject) }
                break
            }

            'type' {
                $plan += New-Step 'type' @{ text = $chunk -replace '^[^\s]+\s+', ''
                                            say = 'Печатаю' }
                break
            }

            'scroll' {
                $direction = if ($chunk -match '(вверх|наверх|назад)') { 'up' } else { 'down' }
                $amount = 4
                if ($chunk -match '(\d{1,2})') { $amount = [Math]::Min(25, [int]$Matches[1]) }
                $plan += New-Step 'scroll' @{ amount = $amount; direction = $direction
                                              say = ("Листаю {0}" -f $(if ($direction -eq 'up') { 'вверх' } else { 'вниз' })) }
                break
            }

            'save'   { $plan += New-Step 'keys' @{ combo = 'ctrl+s'; say = 'Сохраняю' }; break }
            'copy'   { $plan += New-Step 'keys' @{ combo = 'ctrl+c'; say = 'Копирую' }; break }
            'paste'  { $plan += New-Step 'keys' @{ combo = 'ctrl+v'; say = 'Вставляю' }; break }

            'close' {
                if (-not $rest -or $rest -match '^(окно|это|программу)$') {
                    $plan += New-Step 'keys' @{ combo = 'alt+f4'; say = 'Закрываю окно' }
                    break
                }
                $target = Resolve-Target $scene $rest 'window'
                if ($target -and $target.kind -eq 'window') {
                    $plan += New-Step 'close_window' @{ title = $target.name
                                                        say = ("Закрываю «{0}»" -f $target.name) }
                } else {
                    $plan += New-Step 'close_app' @{ target = $rest; say = ("Закрываю {0}" -f $rest) }
                }
                break
            }

            'click' {
                if (-not $rest) {
                    $plan += New-Step 'click' @{ button = 'left'; count = 1; say = 'Клик' }
                    break
                }
                $target = Resolve-Target $scene $rest 'ui'
                if ($target -and $target.kind -eq 'ui') {
                    $plan += New-Step 'click_point' @{ x = $target.item.x; y = $target.item.y
                                                       name = $target.name
                                                       say = ("Нажимаю «{0}»" -f $target.name) }
                } else {
                    # Не нашли сейчас — поищем в момент выполнения: окно могло
                    # ещё не открыться, если это второй шаг плана.
                    $plan += New-Step 'click_text' @{ name = $rest
                                                      say = ("Ищу «{0}» в окне" -f $rest) }
                }
                break
            }

            'find' {
                $plan += New-Step 'find' @{ what = $rest; say = ("Ищу «{0}»" -f $rest) }
                break
            }

            'delete' { $plan += New-Step 'keys' @{ combo = 'delete'; say = 'Удаляю выделенное' }; break }

            'open' {
                if (-not $rest) { $plan += New-Step 'unknown' @{ text = $chunk }; break }
                $target = Resolve-Target $scene $rest
                if (-not $target) {
                    $plan += New-Step 'missing' @{ what = $rest
                                                   say = ("Не нашёл «{0}» — ни среди программ, ни среди папок, ни на экране." -f $rest) }
                    break
                }
                switch ($target.kind) {
                    'ui'     { $plan += New-Step 'click_point' @{ x = $target.item.x; y = $target.item.y
                                                                  name = $target.name
                                                                  say = ("Открываю «{0}»" -f $target.name) } }
                    'window' { $plan += New-Step 'raise_window' @{ title = $target.name
                                                                   say = ("Перехожу в «{0}»" -f $target.name) } }
                    'folder' { $plan += New-Step 'open_path' @{ path = $target.path; browse = $true
                                                                say = ("Открываю папку «{0}»" -f $target.name) } }
                    'file'   { $plan += New-Step 'open_path' @{ path = $target.path
                                                                say = ("Открываю «{0}»" -f $target.name) } }
                    default  { $plan += New-Step 'open_app' @{ path = $target.path; name = $target.name
                                                               say = ("Открываю {0}" -f $target.name) } }
                }
                break
            }

            default {
                # Глагола не нашлось. Прежде чем сдаваться, спросим обученную
                # модель: она видела фразы, которых нет в списке выше.
                $understood = Get-MindNlu $chunk
                if ($understood -and $understood.PSObject.Properties['intent'] -and $understood.intent) {
                    $plan += New-Step 'intent' @{ intent = [string]$understood.intent; text = $chunk
                                                  say = ("Понял как «{0}»" -f $understood.intent) }
                } else {
                    $plan += New-Step 'unknown' @{ text = $chunk }
                }
            }
        }
    }

    # «Нарисуй кота» без единого слова про Paint — всё равно про Paint: рисовать
    # больше негде, и просить человека сначала открыть редактор было бы
    # занудством.
    $needsCanvas = @($plan | Where-Object { $_.act -eq 'draw' }).Count -gt 0
    $hasPaint = @($plan | Where-Object {
        $_.act -eq 'paint' -or
        (($_.act -eq 'open_app' -or $_.act -eq 'open') -and "$($_.name)$($_.target)" -match 'paint|пейнт|краск')
    }).Count -gt 0
    if ($needsCanvas -and -not $hasPaint) {
        $plan = @(New-Step 'ensure_paint' @{ say = 'Готовлю холст' }) + $plan
    }

    return $plan
}

# ------------------------------------------------------- модель ведёт руки ---
#
# Поручение, которое правила не разобрали целиком, выполняет модель — шаг за
# шагом: посмотреть на экран → одно действие → посмотреть снова
# (python/cloudhdr_qwen/hands.py, 127.0.0.1:8080/v1/act). Так она видит, что
# окно открылось не тем боком, и поправляется, а не выполняет вслепую план,
# составленный до первого движения.

$Script:AgentUrl = 'http://127.0.0.1:8080/v1/act'
$Script:AgentMaxSteps = 15
# Наши собственные окна — не цель: поручение отдают из них, а делать его нужно
# в том, что было впереди до этого.
$Script:AgentOwn = @('CloudHDR', 'powershell', 'pwsh')

$Script:AgentTypes = @{
    'Button' = 'кнопка'; 'MenuItem' = 'пункт меню'; 'TabItem' = 'вкладка'; 'CheckBox' = 'флажок'
    'RadioButton' = 'переключатель'; 'Hyperlink' = 'ссылка'; 'Edit' = 'поле'; 'ListItem' = 'строка списка'
    'TreeItem' = 'узел дерева'; 'ComboBox' = 'поле'; 'SplitButton' = 'кнопка'; 'Document' = 'документ'
}

function Test-AgentModel {
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:8080/health' -TimeoutSec 2
        return $true
    } catch { return $false }
}

function Get-AgentFront {
    $windows = @()
    try {
        $windows = @(Get-Windows | Where-Object {
            $owner = Get-Process -Id $_.pid -ErrorAction SilentlyContinue
            -not ($owner -and $Script:AgentOwn -contains $owner.ProcessName)
        })
    } catch { }
    $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
    $candidates = @()
    if ($handle -ne [IntPtr]::Zero) { $candidates += Get-WindowInfo $handle }
    $candidates += $windows          # дальше — по порядку поверх друг друга
    foreach ($window in $candidates) {
        if (-not $window -or -not $window.title) { continue }
        $owner = Get-Process -Id $window.pid -ErrorAction SilentlyContinue
        if ($owner -and $Script:AgentOwn -contains $owner.ProcessName) { continue }
        if ([CloudHdrCursor.Native]::IsIconic([IntPtr]$window.handle)) { continue }
        return @{ front = $window; windows = $windows; process = $(if ($owner) { $owner.ProcessName } else { '' }) }
    }
    return @{ front = $null; windows = $windows; process = '' }
}

<#
    Экран текстом для модели: окно впереди, его видимые элементы с номерами
    (сверху вниз, слева направо) и открытые окна. Номер действует только на
    этом шаге — следующий шаг смотрит на экран заново.
#>
function Get-AgentScene {
    $view = Get-AgentFront
    $items = @()
    if ($view.front) {
        foreach ($type in @('Button', 'MenuItem', 'TabItem', 'CheckBox', 'RadioButton', 'Hyperlink', 'Edit', 'ComboBox', 'SplitButton')) {
            try { $items += @(Get-UiItems $view.front $type 150) } catch { }
        }
        foreach ($type in @('ListItem', 'TreeItem')) {
            try { $items += @(Get-UiItems $view.front $type 60) } catch { }
        }
    }

    $screen = Get-ScreenRect
    $seen = @{}
    $visible = @()
    foreach ($item in $items) {
        $name = ([string]$item.name -replace '\s+', ' ').Trim()
        # «Вкладка "YouTube" использует 372 МБ памяти» → «YouTube»
        $name = $name -replace '^Вкладка "(.+)" использует .+$', '$1'
        $short = ([string]$item.type) -replace '^ControlType\.', ''
        if (-not $name -and $short -ne 'Edit') { continue }
        if ($name.Length -gt 80) { $name = $name.Substring(0, 80) + '…' }
        $key = '{0}|{1}|{2}' -f $short, $name, [int]($item.y / 8)
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if ($item.x -lt $screen.left -or $item.y -lt $screen.top -or
            $item.x -gt ($screen.left + $screen.width) -or $item.y -gt ($screen.top + $screen.height)) { continue }
        if ($view.front -and -not (Test-PointFree $view.front $item.x $item.y)) { continue }
        $item['label'] = $name
        $item['kind'] = $(if ($Script:AgentTypes.ContainsKey($short)) { $Script:AgentTypes[$short] } else { 'элемент' })
        $visible += $item
    }
    $visible = @($visible | Sort-Object { [int]($_.y / 12) }, { $_.x } | Select-Object -First 90)

    $map = @{}
    $lines = New-Object System.Collections.Generic.List[string]
    if ($view.front) {
        $lines.Add(("Окно впереди: «{0}» ({1})" -f $view.front.title, $view.process))
    } else {
        $lines.Add('Окно впереди: рабочий стол')
    }
    $lines.Add('Элементы:')
    $index = 0
    foreach ($item in $visible) {
        $index++
        $map[$index] = $item
        $label = if ($item.label) { '«{0}»' -f $item.label } else { '(без названия)' }
        $lines.Add(('[{0}] {1} {2}' -f $index, $item.kind, $label))
    }
    if ($index -eq 0) { $lines.Add('(в этом окне ничего не видно)') }
    $titles = @($view.windows | Where-Object { $_.title } | ForEach-Object { '«{0}»' -f $_.title } | Select-Object -First 12)
    $lines.Add(('Открытые окна: {0}' -f ($titles -join ', ')))

    return @{ text = ($lines -join "`n"); map = $map; front = $view.front; windows = $view.windows
              items = $visible; count = $index }
}

# Сочетание, которое руки действительно умеют нажать: каждая часть — известная
# клавиша. «Пауза на видео» клавишей не является, и нажимать «ничего» с
# отчётом «готово» — худший исход.
function ConvertTo-AgentChord([string]$text) {
    $combo = ($text.ToLower() -replace '[«»"]', '' -replace '\s*\+\s*', '+').Trim()
    if (-not $combo -or $combo -match '\s') { return '' }
    foreach ($part in ($combo -split '\+')) { if ((ConvertTo-KeyCode $part) -le 0) { return '' } }
    return $combo
}

# «нажми «Мессенджер»» → «Мессенджер»; «нажми на кнопку Сохранить» → «Сохранить».
function Get-AgentTarget([string]$text) {
    if ($text -match '«([^»]+)»') { return $Matches[1].Trim() }
    if ($text -match '^(?:нажми|кликни|щелкни|щёлкни|выбери)\s+(?:на\s+)?(?:кнопку|пункт|ссылку|вкладку)?\s*(.+)$') {
        $name = $Matches[1].Trim(' ', '"', '.')
        if ($name -notmatch 'перв|втор|трет|последн|видео в|результат') { return $name }
    }
    return ''
}

function Find-AgentItem($scene, [string]$name) {
    $best = 0; $bestScore = 0
    foreach ($pair in $scene.map.GetEnumerator()) {
        $score = Get-NameScore $name ([string]$pair.Value.label)
        if ($score -gt $bestScore) { $best = [int]$pair.Key; $bestScore = $score }
    }
    if ($bestScore -ge 74) { return $best }
    return 0
}

<#
    Перейти к окну или вкладке по названию — без модели: окно по заголовку,
    потом вкладки открытых браузеров, и если нигде нет, а это сайт — открыть.
#>
function Invoke-AgentSwitch([string]$name) {
    $best = $null; $bestScore = 0
    foreach ($window in @(Get-Windows)) {
        $score = Get-NameScore $name ([string]$window.title)
        if ($score -gt $bestScore) { $best = $window; $bestScore = $score }
    }
    if ($best -and $bestScore -ge 60) {
        Set-WindowFront $best | Out-Null
        Wait-Idle 400
        return @{ ok = $true; say = ("Перешёл в «{0}»." -f $best.title) }
    }
    foreach ($window in @(Get-Windows)) {
        $owner = Get-Process -Id $window.pid -ErrorAction SilentlyContinue
        if (-not $owner -or $owner.ProcessName -notmatch '^(chrome|msedge|firefox|browser|opera|brave|vivaldi)$') { continue }
        foreach ($tab in @(Get-UiItems $window 'TabItem' 60)) {
            $label = ([string]$tab.name) -replace '^Вкладка "(.+)" использует .+$', '$1'
            if ((Get-NameScore $name $label) -ge 60) {
                Set-WindowFront $window | Out-Null
                Invoke-ClickAt $tab.x $tab.y 'left' 1
                Wait-Idle 500
                return @{ ok = $true; say = ("Перешёл на вкладку «{0}»." -f $label) }
            }
        }
    }
    $opened = Invoke-AgentOpen $name @{ front = $null; windows = @(Get-Windows) }
    if ($opened.ok) { return $opened }
    return @{ ok = $false; say = ("Окна «{0}» не нашёл." -f $name) }
}

function Invoke-AgentModel([string]$goal, [string]$scene, $history, [string]$context = '', [switch]$Plan) {
    $body = @{ goal = $goal; scene = $scene; history = @($history); context = $context; plan = [bool]$Plan } |
            ConvertTo-Json -Depth 4 -Compress
    $response = Invoke-WebRequest -Uri $Script:AgentUrl -Method Post -UseBasicParsing -TimeoutSec 90 `
        -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
    return ([Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json)
}

# Разделы «Параметров» Windows: у них нет ни окна, ни файла, только адрес.
$Script:AgentSettings = [ordered]@{
    'звук|громкост|динамик|микрофон' = 'sound'; 'дисплей|экран|яркост|разрешени' = 'display'
    'bluetooth|блютуз' = 'bluetooth'; 'wi-?fi|вай-?фай|сеть|интернет' = 'network-wifi'
    'обновлени' = 'windowsupdate'; 'приложени|программ' = 'appsfeatures'
    'фон|обо[ий]|персонализ|тем[аыу]' = 'personalization-background'; 'мыш' = 'mousetouchpad'
    'клавиатур|язык' = 'regionlanguage'; 'уведомлени' = 'notifications'
    'питани|батаре|сон|спящ' = 'powersleep'; 'хранилищ|памят|диск' = 'storagesense'
    'конфиденциальн|приватн' = 'privacy'; 'учетн|аккаунт' = 'yourinfo'; 'врем|дат[аыу]' = 'dateandtime'
}

# Сайты, которые называют словом, а не адресом. Без этого «открой youtube»
# находило на диске картинку youtube.png.
$Script:AgentSites = [ordered]@{
    '^(ютуб|youtube|ютьюб)$' = 'youtube.com'; '^(вк|вконтакте|vk)$' = 'vk.com'
    '^(яндекс|yandex)$' = 'ya.ru'; '^(гугл|google)$' = 'google.com'
    '^(яндекс ?музык[аи]|yandex ?music)$' = 'music.yandex.ru'; '^(кинопоиск)$' = 'kinopoisk.ru'
    '^(рутуб|rutube)$' = 'rutube.ru'; '^(вк видео|vk video)$' = 'vkvideo.ru'; '^(twitch|твич)$' = 'twitch.tv'
    '^(github|гитхаб)$' = 'github.com'; '^(википеди[яю]|wikipedia)$' = 'ru.wikipedia.org'
    '^(почт[ау]|яндекс почт[ау])$' = 'mail.yandex.ru'; '^(gmail|джимейл)$' = 'mail.google.com'
    '^(chatgpt|чат ?gpt)$' = 'chatgpt.com'; '^(озон|ozon)$' = 'ozon.ru'; '^(вайлдберриз|wildberries|вб)$' = 'wildberries.ru'
    '^(авито|avito)$' = 'avito.ru'; '^(steam|стим) ?(сайт|магазин)$' = 'store.steampowered.com'
}

function Invoke-AgentOpen([string]$what, $scene) {
    $target = $what.Trim(' ', '"', '«', '»', '.')
    if (-not $target) { return @{ ok = $false; say = 'Не понял, что открыть.' } }
    $plain = ($target.ToLower() -replace 'ё', 'е' -replace '^(сайт|страниц[уа])\s+', '').Trim()
    foreach ($pair in $Script:AgentSites.GetEnumerator()) {
        if ($plain -match $pair.Key) { $target = $pair.Value; break }
    }
    if ($target -match '(настройк|параметр)') {
        $page = ''
        foreach ($pair in $Script:AgentSettings.GetEnumerator()) {
            if ($target -match $pair.Key) { $page = $pair.Value; break }
        }
        Start-Process ('ms-settings:' + $page)
        Wait-Idle 1500
        return @{ ok = $true; say = ("Открыл {0}." -f $target) }
    }
    # Сайт: домен в начале — сразу браузером по умолчанию. Пробелы в запросе
    # после адреса («…?search_query=обзор айфона») кодируем, а не отбрасываем.
    if ($target -match '^(https?://)?(?:[\w-]+\.)+[a-zа-я]{2,}(?:[/?#]|$)') {
        $url = if ($target -match '^https?://') { $target } else { 'https://' + $target }
        $cut = $url.IndexOfAny([char[]]'?/', 8)
        if ($cut -gt 0) { $url = $url.Substring(0, $cut) + ($url.Substring($cut) -replace '\s+', '+') }
        Start-Process $url
        Wait-Idle 2500
        # Windows не всегда пускает браузер вперёд — выводим сами, иначе
        # следующий шаг смотрел бы на старое окно.
        $browser = @(Get-Windows | Where-Object {
            $owner = Get-Process -Id $_.pid -ErrorAction SilentlyContinue
            $owner -and $owner.ProcessName -match '^(chrome|msedge|firefox|browser|opera|brave|vivaldi)$'
        }) | Select-Object -First 1
        if ($browser) { Set-WindowFront $browser | Out-Null; Wait-Idle 400 }
        return @{ ok = $true; say = 'Открыл сайт.' }
    }
    $steps = @(New-MindPlan ("открой " + $target) @{ front = $scene.front; items = @(); windows = $scene.windows })
    $result = @{ ok = $false; say = ("Не нашёл «{0}»." -f $target) }
    foreach ($step in $steps) {
        if ($step.act -eq 'missing' -or $step.act -eq 'unknown') { return $result }
        # Файл открываем, только если о файле и просили (есть расширение):
        # иначе похожее имя на диске побеждает программу или сайт.
        if ($step.act -eq 'open_path' -and -not $step.browse -and $target -notmatch '\.\w{2,4}$') { return $result }
        $result = Invoke-Step $step
    }
    Wait-Idle 1200
    return $result
}

function Invoke-AgentClick($scene, [int]$id, [string]$button, [int]$count) {
    if (-not $scene.map.ContainsKey($id)) { return @{ ok = $false; say = ("Элемента [{0}] нет на экране." -f $id) } }
    $item = $scene.map[$id]
    if ($scene.front) { Set-WindowFront $scene.front | Out-Null }
    Invoke-ClickAt $item.x $item.y $button $count
    Wait-Idle 600
    return @{ ok = $true; say = ("{0} «{1}»" -f $item.kind, $item.label) }
}

<#
    Поручение целиком: модель один раз режет его на подзадачи. «Открой …»
    выполняется сразу (программа, папка, сайт, поиск ссылкой), остальное —
    циклом по экрану, но уже с одной короткой целью, а не со всей просьбой:
    так маленькая модель не теряет нить.
#>
function Invoke-MindAgent([string]$goal) {
    $plan = $null
    $view = Get-AgentFront
    $screen = 'Окно впереди: {0}' -f $(if ($view.front) { '«' + $view.front.title + '»' } else { 'рабочий стол' })
    $titles = @($view.windows | Where-Object { $_.title } | ForEach-Object { '«{0}»' -f $_.title } | Select-Object -First 10)
    $screen += "`nОткрытые окна: " + ($titles -join ', ')
    try { $plan = Invoke-AgentModel $goal $screen @() -Plan } catch { $plan = $null }
    if (-not $plan -or $plan.failed) { return (Invoke-AgentLoop $goal $goal $Script:AgentMaxSteps) }
    if ($plan.ask) { return @{ ok = $false; say = [string]$plan.ask } }
    $steps = @($plan.steps | Where-Object { $_ })
    if ($steps.Count -eq 0) { return (Invoke-AgentLoop $goal $goal $Script:AgentMaxSteps) }

    # Названо приложение или сайт, который открыт, но не впереди («поставь
    # ютуб на паузу», пока впереди настройки), — сначала переходим к нему.
    # Проверяется без модели: планировщик об этом забывает.
    $first = [string]$steps[0]
    if ($first -notmatch '^(?:открой|запусти|перейди|переключись)(?![а-яё])') {
        $frontTitle = if ($view.front) { [string]$view.front.title } else { '' }
        foreach ($word in @($goal.ToLower() -replace 'ё', 'е' -split '[^а-яa-z0-9]+' | Where-Object { $Script:MindSynonyms.ContainsKey($_) })) {
            if ((Get-NameScore $word $frontTitle) -ge 80) { break }
            $there = @($view.windows | Where-Object { (Get-NameScore $word ([string]$_.title)) -ge 80 }).Count -gt 0
            if ($there -or $word -match '^(ютуб|ютьюб|вк|вконтакте)$') {
                $steps = @("перейди в окно «$word»") + $steps
                break
            }
        }
    }

    Write-Cursor ('План: ' + ($steps -join ' → '))
    $index = 0
    foreach ($sub in $steps) {
        Assert-Running
        $index++
        if ($Script:CursorBoard) { $Script:CursorBoard['done'] = $index - 1; $Script:CursorBoard['total'] = $steps.Count }
        $text = ([string]$sub).Trim()
        Write-Cursor $text

        if ($text -match '^(?:открой|запусти|перейди на сайт)\s+(.+)$') {
            $result = Invoke-AgentOpen $Matches[1] (Get-AgentScene)
        } elseif ($text -match '^(?:перейди|переключись)\s+(?:в|на)\s+(?:окно|вкладку)\s+(.+)$') {
            $result = Invoke-AgentSwitch ($Matches[1].Trim(' ', '«', '»', '"'))
        } elseif ($text -match '^(?:прокрути|полистай|листай)\s*(вверх|наверх)?') {
            Invoke-Scroll 5 $(if ($Matches[1]) { 'up' } else { 'down' })
            $result = @{ ok = $true }
        } elseif ($text -match '^нажми клавишу\s+(.+)$' -and (ConvertTo-AgentChord $Matches[1])) {
            Send-Chord (ConvertTo-AgentChord $Matches[1])
            Wait-Idle 600
            $result = @{ ok = $true }
        } else {
            $result = $null
            $wanted = Get-AgentTarget $text
            if ($wanted) {
                # Страница могла ещё грузиться после «открой …» — даём ей до
                # трёх секунд, прежде чем звать модель.
                $hit = 0
                for ($try = 0; $try -lt 4 -and -not $hit; $try++) {
                    if ($try -gt 0) { Wait-Idle 800 }
                    $scene = Get-AgentScene
                    $hit = Find-AgentItem $scene $wanted
                }
                if ($hit) {
                    Write-Cursor ('Нашёл «{0}» — нажимаю' -f $scene.map[$hit].label)
                    $result = Invoke-AgentClick $scene $hit 'left' 1
                }
            }
            if (-not $result) { $result = Invoke-AgentLoop $text $goal 7 }
        }
        if (-not $result.ok) {
            $why = if ($result.say) { [string]$result.say } else { 'не вышло' }
            return @{ ok = $false; say = ('Шаг «{0}»: {1}' -f $text, $why) }
        }
    }
    return @{ ok = $true; say = $(if ($plan.say) { [string]$plan.say } else { 'Готово.' }) }
}

function Invoke-AgentLoop([string]$goal, [string]$context, [int]$maxSteps) {
    $history = New-Object System.Collections.Generic.List[string]
    $last = ''; $repeats = 0; $said = ''
    # Подзадача из одного действия («нажми …», «напечатай …») закончена, как
    # только это действие удалось. Модель сама этого часто не замечает и
    # начинает бродить по уже открытому — проверено на «включи первое видео».
    $oneClick = $goal -match '^(?:нажми|кликни|щелкни|щёлкни|выбери|включи|открой)(?![а-яё])'
    $oneType = $goal -match '^(?:напечатай|напиши|введи|набери)(?![а-яё])'

    for ($step = 1; $step -le $maxSteps; $step++) {
        Assert-Running

        $scene = Get-AgentScene
        try {
            $move = Invoke-AgentModel $goal $scene.text $history $context
        } catch {
            return @{ ok = $false; say = 'Модель не ответила — попробуйте ещё раз.' }
        }
        $action = [string]$move.action; $id = [int]$move.id; $text = [string]$move.text
        try { Add-Content -LiteralPath $Script:CoreLog -Encoding UTF8 -Value ('    модель: ' + ($move | ConvertTo-Json -Compress)) } catch { }
        if ($move.why) { Write-Cursor ([string]$move.why) }

        # Одно и то же трижды подряд — модель застряла; честно останавливаемся.
        $signature = '{0}|{1}|{2}' -f $action, $id, $text
        if ($signature -eq $last) { $repeats++ } else { $repeats = 0; $last = $signature }
        if ($repeats -ge 2) { return @{ ok = $false; say = 'Застрял на одном шаге — остановился.' } }

        $result = @{ ok = $true; say = '' }
        switch ($action) {
            'done'  { return @{ ok = $true; say = $(if ($move.say) { [string]$move.say } else { 'Готово.' }) } }
            'ask'   { return @{ ok = $false; say = $(if ($move.say) { [string]$move.say } else { 'Уточните, что сделать.' }) } }
            'fail'  { return @{ ok = $false; say = $(if ($move.say) { [string]$move.say } else { 'Не получилось.' }) } }
            'click'        { $result = Invoke-AgentClick $scene $id 'left' 1 }
            'double_click' { $result = Invoke-AgentClick $scene $id 'left' 2 }
            'right_click'  { $result = Invoke-AgentClick $scene $id 'right' 1 }
            'type' {
                if ($id -gt 0) { $result = Invoke-AgentClick $scene $id 'left' 1 }
                if ($result.ok) {
                    Send-Text $text
                    $result = @{ ok = $true; say = ("напечатал «{0}»" -f $text) }
                }
            }
            'key' {
                $combo = ConvertTo-AgentChord $text
                if (-not $combo) {
                    $result = @{ ok = $false; say = ("клавиши «{0}» нет — нажми кнопку на экране" -f $text) }
                } else {
                    if ($scene.front) { Set-WindowFront $scene.front | Out-Null }
                    Send-Chord $combo
                    Wait-Idle 700
                    $result = @{ ok = $true; say = ("нажал {0}" -f $combo) }
                }
            }
            'scroll' {
                $direction = if ($text -match 'up|вверх') { 'up' } else { 'down' }
                if ($scene.front) {
                    $cx = $scene.front.left + $scene.front.width / 2; $cy = $scene.front.top + $scene.front.height / 2
                    Move-Cursor $cx $cy
                }
                Invoke-Scroll 5 $direction
                Wait-Idle 400
                $result = @{ ok = $true; say = ("прокрутил {0}" -f $(if ($direction -eq 'up') { 'вверх' } else { 'вниз' })) }
            }
            'open'  { $result = Invoke-AgentOpen $text $scene }
            'focus' {
                $result = Invoke-AgentSwitch ($text -replace '^(вкладк[аиу]|окно)\s*', '').Trim(' ', '«', '»', '"')
                if (-not $result.ok) {
                    # «вкладка «YouTube»» — это не окно, а элемент на экране: жмём его.
                    $needle = ($text -replace '^(вкладк[аиу]|окно)\s*', '').Trim(' ', '«', '»', '"')
                    $best = $null; $bestScore = 0
                    foreach ($pair in $scene.map.GetEnumerator()) {
                        $score = Get-NameScore $needle ([string]$pair.Value.label)
                        if ($score -gt $bestScore) { $best = $pair.Key; $bestScore = $score }
                    }
                    if ($best -and $bestScore -ge 60) { $result = Invoke-AgentClick $scene ([int]$best) 'left' 1 }
                }
            }
            'wait'  { Wait-Idle 1500; $result = @{ ok = $true; say = 'подождал' } }
            default { $result = @{ ok = $false; say = ("неизвестное действие {0}" -f $action) } }
        }

        if ($result.ok -and (($oneClick -and $action -match 'click') -or ($oneType -and $action -eq 'type'))) {
            # Если в подзадаче названо, что нажать, засчитываем только попадание
            # в похожее: клик «куда-нибудь» — не выполненная просьба.
            $wanted = Get-AgentTarget $goal
            $label = if ($scene.map.ContainsKey($id)) { [string]$scene.map[$id].label } else { '' }
            if ($oneType -or -not $wanted -or (Get-NameScore $wanted $label) -ge 50 -or $goal -match 'перв|втор|трет|последн') {
                return @{ ok = $true; say = [string]$result.say }
            }
        }
        $mark = if ($result.ok) { 'получилось' } else { 'НЕ получилось' }
        $history.Add(('{0}. {1} {2} {3} → {4}: {5}' -f $step, $action, $(if ($id) { "[$id]" } else { '' }), $text, $mark, $result.say))
        if ($result.say) { $said = [string]$result.say }
    }
    return @{ ok = $false; say = ('Сделал {0} шагов, но до конца не дошёл. Последнее: {1}.' -f $maxSteps, $said) }
}
