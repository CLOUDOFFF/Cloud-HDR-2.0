<#
    Cloud HDR Cursor Core — руки помощника.

    Здесь всё, чем помощник трогает компьютер по-настоящему: курсор ездит по
    экрану, кнопки нажимаются, колесо крутится, буквы набираются. Не «эмуляция
    команды», а тот же поток ввода, что идёт от живой мыши, — поэтому работает
    везде, где работает человек: в Проводнике, в Paint, в любом чужом окне.

    ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. Движок нужен в двух местах сразу: в службе
    cursor.ps1, которая принимает задачи по HTTP, и в рабочем пространстве, где
    задача выполняется минутами. Общий файл подключается точкой в оба места и
    остаётся одним исходником — иначе правка «как ведёт себя курсор» неизбежно
    попадала бы в одну копию из двух.

    ПОЧЕМУ ДВИЖЕНИЕ НЕ ПРЯМОЕ. SetCursorPos умеет ставить курсор в точку
    мгновенно, и это первое, что приходит в голову. Так делать нельзя по двум
    причинам, и обе важнее красоты. Первая: человек обязан видеть, что
    происходит, — прыгнувший курсор невозможно перехватить, а едущий видно за
    полсекунды до клика. Вторая: части интерфейса Windows (подсказки, всплывающие
    меню, ленты) открываются по наведению и требуют, чтобы курсор реально
    провёл по ним хотя бы кадр. Поэтому курсор едет по дуге Безье с разгоном и
    торможением — и потому же скорость взята из закона Фиттса, а не выдумана.

    ЧТО ОСТАНАВЛИВАЕТ РАБОТУ. Escape в любой момент. Истечение срока разрешения.
    Команда «стоп» из окна. И — главное — сам человек: если курсор оказался не
    там, куда его вёл помощник, значит, за мышь взялись руками, и помощник
    немедленно отпускает управление. Это последняя защита, и она честнее всех
    остальных: не нужно ничего нажимать, достаточно потянуть мышь на себя.
#>

# ------------------------------------------------------------------ Win32 ----
#
# Ввод идёт через SendInput, а не через устаревшие mouse_event/keybd_event:
# только он атомарно отправляет пачку событий и только его видят приложения,
# отсеивающие «синтетику» старого образца.
#
# Вспомогательные обёртки написаны на C#, а не на PowerShell, намеренно.
# Маршалинг структуры INPUT из PowerShell возможен, но каждое движение курсора
# — это десятки вызовов, и на каждом пришлось бы собирать массив структур
# заново. В C# это один вызов без единого лишнего объекта.

if (-not ('CloudHdrCursor.Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace CloudHdrCursor {

    public static class Native {

        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        public struct MOUSEINPUT {
            public int dx; public int dy; public uint mouseData;
            public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct KEYBDINPUT {
            public ushort wVk; public ushort wScan; public uint dwFlags;
            public uint time; public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct HARDWAREINPUT { public uint uMsg; public ushort wParamL; public ushort wParamH; }

        [StructLayout(LayoutKind.Explicit)]
        public struct INPUTUNION {
            [FieldOffset(0)] public MOUSEINPUT mi;
            [FieldOffset(0)] public KEYBDINPUT ki;
            [FieldOffset(0)] public HARDWAREINPUT hi;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct INPUT { public uint type; public INPUTUNION u; }

        const uint INPUT_MOUSE = 0, INPUT_KEYBOARD = 1;
        const uint MOVE = 0x0001, LEFTDOWN = 0x0002, LEFTUP = 0x0004, RIGHTDOWN = 0x0008, RIGHTUP = 0x0010;
        const uint MIDDLEDOWN = 0x0020, MIDDLEUP = 0x0040, WHEEL = 0x0800, HWHEEL = 0x1000;
        const uint ABSOLUTE = 0x8000, VIRTUALDESK = 0x4000;
        const uint KEYUP = 0x0002, UNICODE = 0x0004, SCANCODE = 0x0008;

        [DllImport("user32.dll", SetLastError = true)]
        static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

        [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vKey);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
        [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
        [DllImport("user32.dll")] public static extern IntPtr GetShellWindow();
        [DllImport("user32.dll")] public static extern bool AttachThreadInput(int idAttach, int idAttachTo, bool attach);
        [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
        [DllImport("user32.dll")] public static extern uint GetDoubleClickTime();
        [DllImport("kernel32.dll")] public static extern int GetCurrentThreadId();
        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int GetWindowTextW(IntPtr h, StringBuilder text, int count);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int GetClassNameW(IntPtr h, StringBuilder text, int count);

        delegate bool EnumProc(IntPtr h, IntPtr param);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr param);

        // --- экран -----------------------------------------------------------

        public static int VirtualLeft   { get { return GetSystemMetrics(76); } }
        public static int VirtualTop    { get { return GetSystemMetrics(77); } }
        public static int VirtualWidth  { get { return GetSystemMetrics(78); } }
        public static int VirtualHeight { get { return GetSystemMetrics(79); } }

        public static int[] Cursor() {
            POINT p; GetCursorPos(out p);
            return new int[] { p.X, p.Y };
        }

        // --- мышь ------------------------------------------------------------
        //
        // Координаты у абсолютного движения — доли 65535 по всему виртуальному
        // рабочему столу, а не пиксели. Отсюда и пересчёт: без него на втором
        // мониторе курсор уезжал бы в другую точку, чем просили.

        static void Send(INPUT[] items) { SendInput((uint)items.Length, items, Marshal.SizeOf(typeof(INPUT))); }

        static INPUT MouseEvent(uint flags, int dx, int dy, uint data) {
            INPUT input = new INPUT();
            input.type = INPUT_MOUSE;
            input.u.mi.dx = dx; input.u.mi.dy = dy;
            input.u.mi.mouseData = data;
            input.u.mi.dwFlags = flags;
            return input;
        }

        public static void MoveTo(int x, int y) {
            int w = VirtualWidth, h = VirtualHeight;
            if (w < 2) w = 2; if (h < 2) h = 2;
            int nx = (int)Math.Round((double)(x - VirtualLeft) * 65535.0 / (w - 1));
            int ny = (int)Math.Round((double)(y - VirtualTop) * 65535.0 / (h - 1));
            if (nx < 0) nx = 0; if (nx > 65535) nx = 65535;
            if (ny < 0) ny = 0; if (ny > 65535) ny = 65535;
            Send(new INPUT[] { MouseEvent(MOVE | ABSOLUTE | VIRTUALDESK, nx, ny, 0) });
        }

        public static void Button(int button, bool down) {
            uint flag;
            if (button == 1) flag = down ? RIGHTDOWN : RIGHTUP;
            else if (button == 2) flag = down ? MIDDLEDOWN : MIDDLEUP;
            else flag = down ? LEFTDOWN : LEFTUP;
            Send(new INPUT[] { MouseEvent(flag, 0, 0, 0) });
        }

        public static void Wheel(int delta) {
            Send(new INPUT[] { MouseEvent(WHEEL, 0, 0, unchecked((uint)delta)) });
        }

        public static void WheelSide(int delta) {
            Send(new INPUT[] { MouseEvent(HWHEEL, 0, 0, unchecked((uint)delta)) });
        }

        // --- клавиатура -------------------------------------------------------
        //
        // Текст набирается кодами Unicode, а не виртуальными клавишами. Разница
        // видна на первой же русской букве: виртуальная клавиша зависит от
        // текущей раскладки, и «привет» при английской раскладке превратился бы
        // в «ghbdtn». Юникоду раскладка безразлична.

        static INPUT KeyEvent(ushort vk, ushort scan, uint flags) {
            INPUT input = new INPUT();
            input.type = INPUT_KEYBOARD;
            input.u.ki.wVk = vk; input.u.ki.wScan = scan; input.u.ki.dwFlags = flags;
            return input;
        }

        public static void Key(ushort vk, bool down) {
            Send(new INPUT[] { KeyEvent(vk, 0, down ? 0u : KEYUP) });
        }

        public static void Char(char c) {
            Send(new INPUT[] {
                KeyEvent(0, (ushort)c, UNICODE),
                KeyEvent(0, (ushort)c, UNICODE | KEYUP)
            });
        }

        // --- окна -------------------------------------------------------------

        public static string TitleOf(IntPtr h) {
            StringBuilder buffer = new StringBuilder(512);
            GetWindowTextW(h, buffer, buffer.Capacity);
            return buffer.ToString();
        }

        public static string ClassOf(IntPtr h) {
            StringBuilder buffer = new StringBuilder(256);
            GetClassNameW(h, buffer, buffer.Capacity);
            return buffer.ToString();
        }

        public static IntPtr[] TopWindows() {
            List<IntPtr> found = new List<IntPtr>();
            IntPtr shell = GetShellWindow();
            EnumWindows(delegate(IntPtr h, IntPtr param) {
                if (h != shell && IsWindowVisible(h) && TitleOf(h).Length > 0) found.Add(h);
                return true;
            }, IntPtr.Zero);
            return found.ToArray();
        }

        public static int PidOf(IntPtr h) { int pid; GetWindowThreadProcessId(h, out pid); return pid; }

        /*
            Вынести чужое окно вперёд.

            SetForegroundWindow сам по себе почти всегда возвращает false:
            Windows отдаёт передний план только процессу, который сейчас
            получает ввод. Обход — на время присоединиться к потоку того окна,
            что впереди: тогда система считает нас тем же вводом и разрешает.
            Иначе помощник печатал бы в предыдущее окно, а не в открытое им.
        */
        public static bool Focus(IntPtr h) {
            if (h == IntPtr.Zero) return false;
            if (IsIconic(h)) ShowWindow(h, 9);
            IntPtr front = GetForegroundWindow();
            if (front == h) return true;
            int me = GetCurrentThreadId();
            int ignored;
            int other = GetWindowThreadProcessId(front, out ignored);
            bool attached = (other != 0 && other != me) && AttachThreadInput(other, me, true);
            BringWindowToTop(h);
            bool ok = SetForegroundWindow(h);
            if (attached) AttachThreadInput(other, me, false);
            return ok || GetForegroundWindow() == h;
        }

        public static void Maximize(IntPtr h) { ShowWindow(h, 3); }
        public static void Restore(IntPtr h) { ShowWindow(h, 9); }

        public static int[] RectOf(IntPtr h) {
            RECT r;
            if (!GetWindowRect(h, out r)) return new int[] { 0, 0, 0, 0 };
            return new int[] { r.Left, r.Top, r.Right, r.Bottom };
        }

        public static bool KeyDown(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }
    }
}
'@ | Out-Null
}

# Координаты берутся в физических пикселях. Без этого на экране с масштабом
# 125% (а это заводская настройка почти любого ноутбука) курсор уезжал бы
# правее и ниже цели ровно на четверть расстояния от левого верхнего угла.
try { [void][CloudHdrCursor.Native]::SetProcessDPIAware() } catch { }

Add-Type -AssemblyName System.Drawing | Out-Null

# --------------------------------------------------------------- состояние ---
#
# Всё общее состояние живёт в одной синхронизированной таблице: её создаёт
# панель, а рабочее пространство меняет. Так окно видит шаги в тот же миг,
# когда они происходят, а не после конца задачи.
#
# Имена здесь нарочито длинные, и это не любовь к многословию. Файл
# подключается точкой ПРЯМО В ЧУЖУЮ ОБЛАСТЬ ВИДИМОСТИ — и всё, что он объявляет
# наверху, затирает одноимённые переменные хозяина. Однажды это уже стоило
# полутора часов: рабочее пространство принимало таблицу в параметр $shared, а
# первая же строка подключения выполняла «$Script:Shared = $null» и обнуляла
# её — имена в PowerShell не различают регистр, и это оказалась одна и та же
# переменная. Внешне всё работало: курсор ездил, рисунок рисовался, — но окно
# не видело ни одного шага и считало, что работа не кончилась.

$Script:CursorBoard   = $null   # общая таблица: шаги, «стоп», срок разрешения
$Script:CursorMark    = $null   # куда помощник сам поставил курсор
$Script:CursorKeyCalm = [DateTime]::MinValue

$Script:CoreRoot  = $PSScriptRoot
if (-not $Script:CoreRoot) { $Script:CoreRoot = (Get-Location).Path }
$Script:CoreLog   = Join-Path $Script:CoreRoot 'logs\cursor.log'

function Initialize-Cursor($board) {
    $Script:CursorBoard = $board
    $Script:CursorMark = $null
}

function Write-Cursor([string]$message, [string]$kind = 'step') {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    try {
        $directory = Split-Path -Parent $Script:CoreLog
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        Add-Content -LiteralPath $Script:CoreLog -Value $line -Encoding UTF8
    } catch { }

    # Общая таблица читается и пишется ТОЛЬКО через квадратные скобки.
    #
    # Через точку — нельзя, и это стоило вечера поисков. Синхронизированная
    # таблица ([hashtable]::Synchronized) — это не совсем Hashtable, а его
    # закрытый наследник, и в чужом пространстве выполнения PowerShell берёт для
    # него обычный переходник вместо словарного: «$shared.running» перестаёт
    # означать ключ «running» и начинает означать несуществующее свойство.
    # Чтение молча даёт пустоту, запись молча падает — задача при этом выглядит
    # вечно выполняющейся, а шаги не появляются вовсе.
    if ($Script:CursorBoard -and $null -ne $Script:CursorBoard['steps']) {
        # Список шагов не растёт бесконечно: окно показывает последние, а всё
        # остальное уже лежит в журнале.
        [void]$Script:CursorBoard['steps'].Add(@{ at = (Get-Date).ToString('HH:mm:ss'); text = $message; kind = $kind })
        while ($Script:CursorBoard['steps'].Count -gt 60) { $Script:CursorBoard['steps'].RemoveAt(0) }
    }
}

# ------------------------------------------------------------------ стоп -----
#
# Остановка бросается обычным исключением с меткой в начале текста, а не своим
# классом. Причина техническая: класс, объявленный в подключаемом точкой файле,
# в PowerShell 5.1 не виден в том месте, где файл подключили, — а ловить
# остановку нужно именно там, в службе. Метка работает всюду одинаково.

$Script:AbortMark = 'CURSOR_STOP'

function Test-AbortError($errorRecord) {
    return ([string]$errorRecord.Exception.Message).StartsWith($Script:AbortMark)
}

function Get-AbortText($errorRecord) {
    $message = [string]$errorRecord.Exception.Message
    if ($message.StartsWith($Script:AbortMark)) { return $message.Substring($Script:AbortMark.Length + 1) }
    return $message
}

<#
    Причина остановиться — или пустая строка.

    Проверяется перед каждым шагом и внутри каждого движения. Дёшево: четыре
    сравнения и один вызов GetAsyncKeyState.
#>
function Get-AbortReason {
    if (-not $Script:CursorBoard) { return '' }
    if ($Script:CursorBoard['stop']) { return 'остановлено из окна' }

    if ($Script:CursorBoard['until'] -and (Get-Date) -gt [DateTime]$Script:CursorBoard['until']) {
        return 'срок разрешения истёк'
    }

    # Escape не считается сразу после того, как помощник сам его нажал, — иначе
    # закрытие меню собственной командой выглядело бы как отказ человека.
    if ((Get-Date) -gt $Script:CursorKeyCalm -and [CloudHdrCursor.Native]::KeyDown(0x1B)) {
        return 'нажат Escape'
    }

    # Человек взялся за мышь. Курсор оказался далеко от того места, куда его
    # поставил помощник, — значит, его двигали руками.
    if ($Script:CursorMark) {
        $point = [CloudHdrCursor.Native]::Cursor()
        $dx = $point[0] - $Script:CursorMark[0]
        $dy = $point[1] - $Script:CursorMark[1]
        if (($dx * $dx + $dy * $dy) -gt (150 * 150)) { return 'человек взял мышь' }
    }
    return ''
}

function Assert-Running {
    $reason = Get-AbortReason
    if ($reason) { throw ("{0}|{1}" -f $Script:AbortMark, $reason) }
}

# --------------------------------------------------------------- ожидание ----
#
# Start-Sleep не годится для движения: у планировщика Windows шаг около 15 мс,
# и просьба «подожди 7 мс» превращается в 15. Кадры движения идут вдвое реже,
# чем задумано, и вместо плавной дуги получается лесенка. Поэтому короткие
# паузы отсчитываются секундомером: процессор занят десятые доли секунды за
# движение, зато курсор едет так, как должен.

function Wait-Tick([double]$milliseconds) {
    if ($milliseconds -le 0) { return }
    if ($milliseconds -ge 25) {
        Start-Sleep -Milliseconds ([int]($milliseconds - 8))
        $milliseconds = 8
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalMilliseconds -lt $milliseconds) { [Threading.Thread]::SpinWait(600) }
}

function Wait-Idle([int]$milliseconds) {
    # Долгое ожидание — с проверкой стопа: иначе Escape во время «жду окно»
    # срабатывал бы только через несколько секунд.
    $deadline = (Get-Date).AddMilliseconds($milliseconds)
    while ((Get-Date) -lt $deadline) {
        Assert-Running
        Start-Sleep -Milliseconds 40
    }
}

# ----------------------------------------------------------------- экран -----

function Get-ScreenRect {
    return @{
        left   = [CloudHdrCursor.Native]::VirtualLeft
        top    = [CloudHdrCursor.Native]::VirtualTop
        width  = [CloudHdrCursor.Native]::VirtualWidth
        height = [CloudHdrCursor.Native]::VirtualHeight
    }
}

function Get-CursorPoint {
    $point = [CloudHdrCursor.Native]::Cursor()
    return @{ x = $point[0]; y = $point[1] }
}

# --------------------------------------------------------------- движение ----

function Get-Ease([double]$t) {
    # Сглаживание пятой степени: у него ноль не только у скорости на концах, но
    # и у ускорения. Курсор трогается и останавливается без рывка, который
    # выдаёт кубическое сглаживание на длинных дистанциях.
    return $t * $t * $t * (10 - 15 * $t + 6 * $t * $t)
}

<#
    Довести курсор до точки.

    Путь — квадратичная кривая Безье со случайно отклонённой серединой: прямая
    линия читается как работа программы, лёгкая дуга — как движение руки.
    Длительность считается по закону Фиттса (чем дальше, тем дольше, но не
    пропорционально), поэтому короткие поправки происходят мгновенно, а бросок
    через весь экран занимает полсекунды и его видно.
#>
function Move-Cursor([double]$x, [double]$y, [int]$milliseconds = 0, [switch]$Straight) {
    Assert-Running

    $start = [CloudHdrCursor.Native]::Cursor()
    $x0 = [double]$start[0]; $y0 = [double]$start[1]
    $dx = $x - $x0; $dy = $y - $y0
    $distance = [Math]::Sqrt($dx * $dx + $dy * $dy)

    if ($distance -lt 1.5) {
        [CloudHdrCursor.Native]::MoveTo([int][Math]::Round($x), [int][Math]::Round($y))
        $Script:CursorMark = @([int][Math]::Round($x), [int][Math]::Round($y))
        return
    }

    if ($milliseconds -le 0) {
        $milliseconds = [int][Math]::Min(760, [Math]::Max(120, 90 + 105 * [Math]::Log(1 + $distance / 22, 2)))
    }

    # Середина дуги. Отклонение — вбок от направления движения, тем заметнее,
    # чем длиннее путь, но не больше двадцати пикселей: дуга должна читаться как
    # неровность руки, а не как объезд препятствия.
    $bend = [Math]::Min(20.0, $distance * 0.09)
    if (-not $Straight) { $bend = $bend * (Get-Random -Minimum -100 -Maximum 100) / 100.0 } else { $bend = 0 }
    $mx = ($x0 + $x) / 2 - ($dy / $distance) * $bend
    $my = ($y0 + $y) / 2 + ($dx / $distance) * $bend

    $steps = [int][Math]::Max(10, [Math]::Min(80, $milliseconds / 7))
    $perStep = $milliseconds / $steps

    for ($i = 1; $i -le $steps; $i++) {
        if (($i % 6) -eq 0) { Assert-Running }
        $t = Get-Ease ($i / $steps)
        $inv = 1 - $t
        $px = $inv * $inv * $x0 + 2 * $inv * $t * $mx + $t * $t * $x
        $py = $inv * $inv * $y0 + 2 * $inv * $t * $my + $t * $t * $y

        # Дрожь только в середине пути: у цели рука успокаивается, и промах в
        # последнем кадре означал бы клик мимо кнопки.
        if (-not $Straight -and $i -lt ($steps - 3)) {
            $px += (Get-Random -Minimum -60 -Maximum 60) / 100.0
            $py += (Get-Random -Minimum -60 -Maximum 60) / 100.0
        }

        [CloudHdrCursor.Native]::MoveTo([int][Math]::Round($px), [int][Math]::Round($py))
        $Script:CursorMark = @([int][Math]::Round($px), [int][Math]::Round($py))
        Wait-Tick $perStep
    }

    [CloudHdrCursor.Native]::MoveTo([int][Math]::Round($x), [int][Math]::Round($y))
    $Script:CursorMark = @([int][Math]::Round($x), [int][Math]::Round($y))
    Wait-Tick 12
}

function Invoke-Click([string]$button = 'left', [int]$count = 1) {
    Assert-Running
    $code = 0
    if ($button -eq 'right') { $code = 1 } elseif ($button -eq 'middle') { $code = 2 }

    # Пауза между нажатиями двойного клика заведомо короче системного порога:
    # у Windows он по умолчанию 500 мс, и 90 мс распознаются как двойной клик
    # с любыми настройками.
    for ($i = 0; $i -lt $count; $i++) {
        [CloudHdrCursor.Native]::Button($code, $true)
        Wait-Tick (Get-Random -Minimum 38 -Maximum 72)
        [CloudHdrCursor.Native]::Button($code, $false)
        if ($i -lt ($count - 1)) { Wait-Tick 90 }
    }
    Wait-Tick 60
}

function Invoke-ClickAt([double]$x, [double]$y, [string]$button = 'left', [int]$count = 1) {
    Move-Cursor $x $y
    Wait-Tick 90            # кадр на подсветку под курсором — так виден прицел
    Invoke-Click $button $count
}

<#
    Прокрутка колесом.

    Notches — «щелчки» колеса, как у руки. Крутится не одним махом, а по
    одному щелчку с паузой: приложения со сглаженной прокруткой (Проводник,
    браузеры) на одну большую посылку отвечают рывком в конец списка.
#>
function Invoke-Scroll([int]$notches = 3, [string]$direction = 'down') {
    Assert-Running
    $sign = if ($direction -eq 'up') { 1 } else { -1 }
    for ($i = 0; $i -lt [Math]::Abs($notches); $i++) {
        Assert-Running
        [CloudHdrCursor.Native]::Wheel($sign * 120)
        Wait-Tick (Get-Random -Minimum 70 -Maximum 130)
    }
}

function Invoke-Drag($points, [int]$msPerSegment = 8) {
    if (-not $points -or $points.Count -lt 2) { return }
    Assert-Running

    $first = $points[0]
    Move-Cursor $first[0] $first[1]
    Wait-Tick 60
    [CloudHdrCursor.Native]::Button(0, $true)
    Wait-Tick 70

    try {
        for ($i = 1; $i -lt $points.Count; $i++) {
            if (($i % 8) -eq 0) { Assert-Running }
            $point = $points[$i]
            [CloudHdrCursor.Native]::MoveTo([int][Math]::Round($point[0]), [int][Math]::Round($point[1]))
            $Script:CursorMark = @([int][Math]::Round($point[0]), [int][Math]::Round($point[1]))
            Wait-Tick $msPerSegment
        }
    } finally {
        # Кнопка отпускается даже при остановке. Брошенная зажатой левая кнопка
        # — худшее, что можно оставить после себя: следующее движение мыши
        # выделит пол-экрана или перетащит файл.
        Wait-Tick 40
        [CloudHdrCursor.Native]::Button(0, $false)
        Wait-Tick 60
    }
}

# ------------------------------------------------------------- клавиатура ----

$Script:Keys = @{
    'enter' = 0x0D; 'ввод' = 0x0D; 'return' = 0x0D
    'esc' = 0x1B; 'escape' = 0x1B; 'выход' = 0x1B
    'tab' = 0x09; 'таб' = 0x09
    'space' = 0x20; 'пробел' = 0x20
    'backspace' = 0x08; 'delete' = 0x2E; 'del' = 0x2E; 'удалить' = 0x2E
    'home' = 0x24; 'end' = 0x23; 'pgup' = 0x21; 'pgdn' = 0x22; 'pageup' = 0x21; 'pagedown' = 0x22
    'up' = 0x26; 'down' = 0x28; 'left' = 0x25; 'right' = 0x27
    'вверх' = 0x26; 'вниз' = 0x28; 'влево' = 0x25; 'вправо' = 0x27
    'win' = 0x5B; 'ctrl' = 0x11; 'alt' = 0x12; 'shift' = 0x10
    'f1' = 0x70; 'f2' = 0x71; 'f3' = 0x72; 'f4' = 0x73; 'f5' = 0x74; 'f6' = 0x75
    'f7' = 0x76; 'f8' = 0x77; 'f9' = 0x78; 'f10' = 0x79; 'f11' = 0x7A; 'f12' = 0x7B
}

function ConvertTo-KeyCode([string]$name) {
    $key = $name.Trim().ToLower()
    if ($Script:Keys.ContainsKey($key)) { return [int]$Script:Keys[$key] }
    if ($key.Length -eq 1 -and $key -match '[a-z0-9]') { return [int][char]$key.ToUpper()[0] }
    return 0
}

<#
    Сочетание клавиш: «ctrl+l», «win+e», «alt+f4», «enter».

    Модификаторы нажимаются по порядку и отпускаются в обратном — так же, как
    их отпускает рука. Обратный порядок обязателен: отпустив Ctrl раньше буквы,
    можно отправить приложению одиночную букву вдогонку.
#>
function Send-Chord([string]$combination) {
    Assert-Running
    $parts = @($combination -split '\+' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($parts.Count -eq 0) { return }

    $codes = @()
    foreach ($part in $parts) {
        $code = ConvertTo-KeyCode $part
        if ($code -gt 0) { $codes += $code }
    }
    if ($codes.Count -eq 0) { return }

    foreach ($code in $codes) {
        [CloudHdrCursor.Native]::Key([uint16]$code, $true)
        Wait-Tick (Get-Random -Minimum 22 -Maximum 45)
    }
    Wait-Tick 40
    for ($i = $codes.Count - 1; $i -ge 0; $i--) {
        [CloudHdrCursor.Native]::Key([uint16]$codes[$i], $false)
        Wait-Tick 18
    }
    $Script:CursorKeyCalm = (Get-Date).AddMilliseconds(500)
    Wait-Tick 110
}

<#
    Набрать текст.

    Скорость неровная намеренно: ровный поток символов с шагом в 10 мс некоторые
    поля ввода (адресная строка Проводника, поиск в меню «Пуск») попросту теряют
    — они не успевают обработать подсказки. Пауза после пробела и знака
    препинания заодно делает набор похожим на человеческий.
#>
function Send-Text([string]$text) {
    if (-not $text) { return }
    Assert-Running
    $index = 0
    foreach ($char in $text.ToCharArray()) {
        if (($index % 12) -eq 0) { Assert-Running }
        if ($char -eq "`n") {
            Send-Chord 'enter'
        } else {
            [CloudHdrCursor.Native]::Char($char)
            $pause = Get-Random -Minimum 26 -Maximum 58
            if ($char -eq ' ') { $pause += 25 }
            if ('.,!?;:' -contains [string]$char) { $pause += 60 }
            Wait-Tick $pause
        }
        $index++
    }
    $Script:CursorKeyCalm = (Get-Date).AddMilliseconds(400)
    Wait-Tick 120
}

# ------------------------------------------------------------------ окна -----

function Get-WindowInfo([IntPtr]$handle) {
    $rect = [CloudHdrCursor.Native]::RectOf($handle)
    return @{
        handle = $handle
        title  = [CloudHdrCursor.Native]::TitleOf($handle)
        class  = [CloudHdrCursor.Native]::ClassOf($handle)
        pid    = [CloudHdrCursor.Native]::PidOf($handle)
        left   = $rect[0]; top = $rect[1]; right = $rect[2]; bottom = $rect[3]
        width  = $rect[2] - $rect[0]; height = $rect[3] - $rect[1]
    }
}

function Get-Windows {
    $list = @()
    foreach ($handle in [CloudHdrCursor.Native]::TopWindows()) {
        $info = Get-WindowInfo $handle
        if ($info.width -lt 120 -or $info.height -lt 90) { continue }
        $list += $info
    }
    return $list
}

<#
    Дождаться окна.

    Ищем по трём приметам сразу: имени процесса, классу окна и заголовку.
    Класс — самая надёжная примета (у Проводника это CabinetWClass в любой
    локализации), заголовок — самая понятная, имя процесса выручает там, где
    Microsoft переписала приложение и класс сменился: у Paint в Windows 11
    он уже не тот, что в Windows 10, а mspaint.exe остался mspaint.exe.
#>
function Wait-Window([string]$process = '', [string]$class = '', [string]$title = '', [int]$timeoutMs = 8000) {
    $deadline = (Get-Date).AddMilliseconds($timeoutMs)
    while ((Get-Date) -lt $deadline) {
        Assert-Running
        foreach ($window in Get-Windows) {
            $match = $false
            if ($class -and $window.class -match $class) { $match = $true }
            if ($title -and $window.title -match $title) { $match = $true }
            if ($process) {
                $owner = Get-Process -Id $window.pid -ErrorAction SilentlyContinue
                if ($owner -and $owner.ProcessName -match $process) { $match = $true }
            }
            if ($match) { return $window }
        }
        Start-Sleep -Milliseconds 150
    }
    return $null
}

function Set-WindowFront($window) {
    if (-not $window) { return $false }
    $ok = [CloudHdrCursor.Native]::Focus([IntPtr]$window.handle)
    Wait-Tick 220
    return $ok
}

function Set-WindowBig($window) {
    if (-not $window) { return }
    [CloudHdrCursor.Native]::Maximize([IntPtr]$window.handle)
    Wait-Tick 420
}

function Start-App([string]$path, [string]$arguments = '') {
    Assert-Running
    try {
        if ($arguments) { Start-Process -FilePath $path -ArgumentList $arguments -ErrorAction Stop | Out-Null }
        else { Start-Process -FilePath $path -ErrorAction Stop | Out-Null }
        return $true
    } catch { return $false }
}

# ----------------------------------------------------------------- зрение ----
#
# Помощнику нужно видеть ровно одно: где на экране белый лист, на котором можно
# рисовать. Полноценное распознавание интерфейса тут не нужно и было бы враньём
# о возможностях — а вот «найти самый большой белый прямоугольник» решается
# честно и работает в любой версии Paint, в любой теме и при любом масштабе.

function Get-ScreenBitmap($rect = $null) {
    if (-not $rect) { $rect = Get-ScreenRect }
    $bitmap = New-Object Drawing.Bitmap ([int]$rect.width), ([int]$rect.height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen([int]$rect.left, [int]$rect.top, 0, 0, (New-Object Drawing.Size ([int]$rect.width), ([int]$rect.height)))
    $graphics.Dispose()
    return $bitmap
}

<#
    Найти самый большой белый прямоугольник внутри области.

    Как это сделано. Картинка разбивается на клетки по 8 пикселей, у каждой
    берётся один образец; клетка «белая», если она светлая и без выраженного
    цвета. Дальше по сетке ищется наибольший прямоугольник из белых клеток —
    классическим способом с гистограммой высот, за один проход.

    Почему по образцам, а не по каждому пикселю: миллион обращений к GetPixel в
    PowerShell — это полминуты, а всё нужное видно и по каждому восьмому.
    Считываем построчно через LockBits, поэтому в память попадают только те
    строки, которые действительно нужны.
#>
function Find-WhiteRect($bounds, [int]$cell = 8, [int]$minWhite = 232) {
    $screen = Get-ScreenRect
    $left = [int][Math]::Max($screen.left, $bounds.left)
    $top = [int][Math]::Max($screen.top, $bounds.top)
    $right = [int][Math]::Min($screen.left + $screen.width, $bounds.right)
    $bottom = [int][Math]::Min($screen.top + $screen.height, $bounds.bottom)
    $width = $right - $left; $height = $bottom - $top
    if ($width -lt 60 -or $height -lt 60) { return $null }

    $cols = [int]($width / $cell)
    $rows = [int]($height / $cell)
    if ($cols -lt 4 -or $rows -lt 4) { return $null }

    $bitmap = Get-ScreenBitmap @{ left = $left; top = $top; width = $width; height = $height }
    try {
        $area = New-Object Drawing.Rectangle 0, 0, $width, $height
        $data = $bitmap.LockBits($area, [Drawing.Imaging.ImageLockMode]::ReadOnly, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $grid = New-Object 'bool[][]' $rows
        try {
            $row = New-Object byte[] ($width * 4)
            for ($r = 0; $r -lt $rows; $r++) {
                $grid[$r] = New-Object bool[] $cols
                $offset = [IntPtr]::Add($data.Scan0, ($r * $cell) * $data.Stride)
                [Runtime.InteropServices.Marshal]::Copy($offset, $row, 0, $width * 4)
                for ($c = 0; $c -lt $cols; $c++) {
                    $index = ($c * $cell) * 4
                    $b = $row[$index]; $g = $row[$index + 1]; $rr = $row[$index + 2]
                    $max = [Math]::Max($rr, [Math]::Max($g, $b))
                    $min = [Math]::Min($rr, [Math]::Min($g, $b))
                    $grid[$r][$c] = ($min -ge $minWhite -and ($max - $min) -le 14)
                }
            }
        } finally {
            $bitmap.UnlockBits($data)
        }

        # Наибольший прямоугольник из белых клеток.
        $heights = New-Object int[] $cols
        $best = @{ area = 0; l = 0; t = 0; r = 0; b = 0 }

        for ($r = 0; $r -lt $rows; $r++) {
            for ($c = 0; $c -lt $cols; $c++) {
                if ($grid[$r][$c]) { $heights[$c] = $heights[$c] + 1 } else { $heights[$c] = 0 }
            }
            # Разбор гистограммы стеком: для каждой планки — самый широкий
            # прямоугольник, в котором она самая низкая.
            $stack = New-Object 'System.Collections.Generic.Stack[int]'
            for ($c = 0; $c -le $cols; $c++) {
                $current = if ($c -lt $cols) { $heights[$c] } else { 0 }
                while ($stack.Count -gt 0 -and $heights[$stack.Peek()] -ge $current) {
                    $topIndex = $stack.Pop()
                    $tall = $heights[$topIndex]
                    $leftEdge = if ($stack.Count -gt 0) { $stack.Peek() + 1 } else { 0 }
                    $wide = $c - $leftEdge
                    $size = $tall * $wide
                    if ($size -gt $best.area -and $tall -ge 3 -and $wide -ge 3) {
                        $best = @{ area = $size; l = $leftEdge; t = ($r - $tall + 1); r = ($c - 1); b = $r }
                    }
                }
                $stack.Push($c)
            }
        }

        if ($best.area -le 0) { return $null }
        return @{
            left   = $left + $best.l * $cell
            top    = $top + $best.t * $cell
            right  = $left + ($best.r + 1) * $cell
            bottom = $top + ($best.b + 1) * $cell
        }
    } finally {
        $bitmap.Dispose()
    }
}

<#
    Снимок экрана для окна помощника.

    Уменьшенный и в JPEG: картинка нужна только чтобы человек видел, что
    происходит, а не для разглядывания. Полный кадр в PNG — это мегабайты
    тридцать раз в минуту, и он бы задушил и службу, и панель.
#>
function Save-Shot([string]$path, [int]$maxWidth = 520) {
    try {
        $rect = Get-ScreenRect
        $bitmap = Get-ScreenBitmap $rect
        try {
            $scale = [Math]::Min(1.0, $maxWidth / [double]$rect.width)
            $w = [int]($rect.width * $scale); $h = [int]($rect.height * $scale)
            $small = New-Object Drawing.Bitmap $w, $h
            $graphics = [Drawing.Graphics]::FromImage($small)
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBilinear
            $graphics.DrawImage($bitmap, 0, 0, $w, $h)

            # Курсор рисуем сами: CopyFromScreen его не захватывает, а без него
            # непонятно, куда помощник целится, — а это единственное, ради чего
            # картинка и нужна.
            $point = [CloudHdrCursor.Native]::Cursor()
            $cx = ($point[0] - $rect.left) * $scale
            $cy = ($point[1] - $rect.top) * $scale
            $pen = New-Object Drawing.Pen ([Drawing.Color]::FromArgb(230, 251, 163, 95)), 2
            $graphics.DrawEllipse($pen, [single]($cx - 7), [single]($cy - 7), 14, 14)
            $graphics.DrawLine($pen, [single]($cx - 12), [single]$cy, [single]($cx - 3), [single]$cy)
            $graphics.DrawLine($pen, [single]($cx + 3), [single]$cy, [single]($cx + 12), [single]$cy)
            $graphics.DrawLine($pen, [single]$cx, [single]($cy - 12), [single]$cx, [single]($cy - 3))
            $graphics.DrawLine($pen, [single]$cx, [single]($cy + 3), [single]$cx, [single]($cy + 12))
            $pen.Dispose()
            $graphics.Dispose()

            $codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
            $parameters = New-Object Drawing.Imaging.EncoderParameters 1
            $parameters.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), 62L

            $directory = Split-Path -Parent $path
            if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
            $temporary = $path + '.tmp'
            $small.Save($temporary, $codec, $parameters)
            $small.Dispose()
            Move-Item -LiteralPath $temporary -Destination $path -Force
            return $true
        } finally { $bitmap.Dispose() }
    } catch { return $false }
}

# --------------------------------------------------- разбор чужого окна ------
#
# UI Automation — тот же механизм, которым пользуются экранные дикторы. Через
# него видно, что за элементы лежат в чужом окне и где именно они находятся,
# поэтому «нажми на кнопку Сохранить» превращается в честный клик по её центру,
# а не в угадывание координат.

$Script:UiaReady = $false

function Initialize-Uia {
    if ($Script:UiaReady) { return $true }
    try {
        Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
        Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
        $Script:UiaReady = $true
    } catch {
        Write-Cursor 'Разбор чужих окон недоступен — работаю по координатам' 'warn'
        $Script:UiaReady = $false
    }
    return $Script:UiaReady
}

<#
    Элементы переднего окна: имя, тип и место на экране.

    Обход ограничен и по числу, и по времени. В Проводнике с распахнутой папкой
    на тысячу файлов полный обход дерева занимает десятки секунд, а нужно из
    него, как правило, первое совпадение.
#>
function Get-UiItems($window, [string]$controlType = '', [int]$limit = 400) {
    if (-not (Initialize-Uia)) { return @() }
    if (-not $window) { return @() }

    try {
        $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$window.handle)
        if (-not $root) { return @() }

        $condition = [Windows.Automation.Condition]::TrueCondition
        if ($controlType) {
            # Отбор по типу элемента идёт условием, а не фильтром после обхода:
            # в папке с тысячей файлов разница между «спросить только строки
            # списка» и «получить всё дерево и отсеять» — это секунды.
            $type = $null
            try { $type = [Windows.Automation.ControlType]::$controlType } catch { $type = $null }
            if ($type) {
                $condition = New-Object Windows.Automation.PropertyCondition (
                    [Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
            }
        }

        $found = $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
        $items = @()
        $count = [Math]::Min($found.Count, $limit)
        for ($i = 0; $i -lt $count; $i++) {
            $element = $found[$i]
            try {
                $rect = $element.Current.BoundingRectangle
                if ($rect.Width -le 0 -or $rect.Height -le 0) { continue }
                $items += @{
                    name = [string]$element.Current.Name
                    type = [string]$element.Current.ControlType.ProgrammaticName
                    x = $rect.X + $rect.Width / 2
                    y = $rect.Y + $rect.Height / 2
                    width = $rect.Width; height = $rect.Height
                    element = $element
                }
            } catch { }
        }
        return $items
    } catch {
        return @()
    }
}

<#
    Виден ли элемент на самом деле.

    Разбор чужого окна честно сообщает, где лежит каждая строка списка, — но не
    сообщает, видно ли её. Список мог быть прокручен, а поверх окна могло
    оказаться что-то ещё: чужая подсказка, наша же панель. Клик по такой точке
    попадает не туда, куда собирался, и это худший исход из возможных: помощник
    уверен, что открыл папку, а на деле развернул адресную строку.

    Проверка простая — чей процесс владеет окном под этой точкой. Совпал с тем,
    в которое метимся, значит место свободно.
#>
function Test-PointFree($window, [double]$x, [double]$y) {
    if (-not $window) { return $true }
    $screen = Get-ScreenRect
    if ($x -lt $screen.left -or $y -lt $screen.top -or
        $x -gt ($screen.left + $screen.width) -or $y -gt ($screen.top + $screen.height)) { return $false }

    $point = New-Object CloudHdrCursor.Native+POINT
    $point.X = [int][Math]::Round($x)
    $point.Y = [int][Math]::Round($y)
    $under = [CloudHdrCursor.Native]::WindowFromPoint($point)
    if ($under -eq [IntPtr]::Zero) { return $false }
    return ([CloudHdrCursor.Native]::PidOf($under) -eq [int]$window.pid)
}

function Find-UiItem($window, [string]$name, [string]$controlType = '') {
    $needle = $name.Trim().ToLower()
    if (-not $needle) { return $null }
    $items = Get-UiItems $window $controlType
    if ($items.Count -eq 0) { return $null }

    # Сначала точное совпадение, потом начало имени, потом вхождение: иначе
    # «файл» выбирало бы «Файлы примеров» вместо меню «Файл».
    foreach ($item in $items) { if ($item.name.ToLower() -eq $needle) { return $item } }
    foreach ($item in $items) { if ($item.name.ToLower().StartsWith($needle)) { return $item } }
    foreach ($item in $items) { if ($item.name.ToLower().Contains($needle)) { return $item } }
    return $null
}

# --------------------------------------------------------------- рисование ---
#
# Всё, что помощник умеет рисовать, задано в долях единичного квадрата: 0,0 —
# левый верхний угол, 1,1 — правый нижний. Так один и тот же рисунок ложится на
# любой холст, в любом окне и при любом масштабе экрана — код рисования ничего
# не знает про пиксели, а код холста ничего не знает про картинки.
#
# Один штрих — одно нажатие мыши: перо опускается в первой точке и поднимается
# в последней. Именно поэтому рисунки собраны из длинных непрерывных линий, а
# не из отдельных отрезков: каждый лишний штрих — это лишний клик и лишняя
# секунда.

<#
    Точка рисунка.

    Функция, а не «@(x, y)» на месте, и это не вкусовщина. В PowerShell запятая
    связывает СИЛЬНЕЕ умножения: в «@($cx + [Math]::Cos($a) * $rx, $cy)» сначала
    собирается пара «$rx, $cy», а потом её пытаются умножить на косинус — и всё
    падает с жалобой на op_Multiply у Object[]. Так собранная точка перестаёт
    зависеть от того, вспомнил ли автор про скобки.

    Добавлять точку в штрих нужно ЧЕРЕЗ ЗАПЯТУЮ: «$points += ,(New-Point 1 2)».
    Без неё «+=» разложит пару на два числа, и штрих из десяти точек превратится
    в двадцать чисел подряд — рисунок при этом не падает, а тихо выходит
    бессмыслицей, что куда хуже.
#>
function New-Point([double]$x, [double]$y) {
    return ,@($x, $y)
}

function ConvertTo-Stroke([string]$definition) {
    $points = @()
    foreach ($pair in ($definition -split '\s+')) {
        if (-not $pair) { continue }
        $parts = $pair -split ','
        if ($parts.Count -lt 2) { continue }
        $points += ,(New-Point ([double]$parts[0]) ([double]$parts[1]))
    }
    return ,$points
}

function New-Arc([double]$cx, [double]$cy, [double]$rx, [double]$ry,
                 [double]$from, [double]$to, [int]$steps = 26) {
    $points = @()
    for ($i = 0; $i -le $steps; $i++) {
        $angle = $from + ($to - $from) * $i / $steps
        $points += ,(New-Point ($cx + [Math]::Cos($angle) * $rx) ($cy + [Math]::Sin($angle) * $ry))
    }
    return ,$points
}

# Штриховой шрифт: заглавные латинские буквы и цифры одной линией там, где это
# возможно. Нужен для надписей — «нарисуй логотип» без букв не логотип.
$Script:Glyphs = @{
    'A' = @('0,1 0.5,0 1,1', '0.16,0.64 0.84,0.64')
    'B' = @('0,0 0,1', '0,0 0.62,0 0.88,0.14 0.88,0.34 0.6,0.48 0,0.48', '0,0.48 0.68,0.48 0.95,0.64 0.95,0.85 0.66,1 0,1')
    'C' = @('1,0.2 0.72,0.02 0.36,0.02 0.08,0.22 0,0.5 0.08,0.78 0.36,0.98 0.72,0.98 1,0.8')
    'D' = @('0,0 0,1', '0,0 0.55,0 0.9,0.26 1,0.5 0.9,0.74 0.55,1 0,1')
    'E' = @('1,0 0,0 0,1 1,1', '0,0.5 0.78,0.5')
    'F' = @('0,1 0,0 1,0', '0,0.48 0.76,0.48')
    'G' = @('1,0.2 0.72,0.02 0.36,0.02 0.08,0.22 0,0.5 0.08,0.78 0.36,0.98 0.72,0.98 1,0.78 1,0.56 0.6,0.56')
    'H' = @('0,0 0,1', '1,0 1,1', '0,0.5 1,0.5')
    'I' = @('0.2,0 0.8,0', '0.5,0 0.5,1', '0.2,1 0.8,1')
    'J' = @('0.85,0 0.85,0.74 0.62,0.97 0.3,1 0.05,0.86')
    'K' = @('0,0 0,1', '1,0 0.05,0.52', '0.3,0.35 1,1')
    'L' = @('0,0 0,1 1,1')
    'M' = @('0,1 0,0 0.5,0.58 1,0 1,1')
    'N' = @('0,1 0,0 1,1 1,0')
    'O' = @('0.5,0 0.85,0.16 1,0.5 0.85,0.84 0.5,1 0.15,0.84 0,0.5 0.15,0.16 0.5,0')
    'P' = @('0,1 0,0 0.66,0 0.93,0.17 0.93,0.38 0.66,0.55 0,0.55')
    'Q' = @('0.5,0 0.85,0.16 1,0.5 0.85,0.84 0.5,1 0.15,0.84 0,0.5 0.15,0.16 0.5,0', '0.62,0.68 1,1.06')
    'R' = @('0,1 0,0 0.66,0 0.93,0.17 0.93,0.38 0.66,0.55 0,0.55', '0.42,0.55 1,1')
    'S' = @('1,0.16 0.7,0 0.3,0 0.05,0.16 0.08,0.4 0.5,0.5 0.9,0.6 0.96,0.84 0.7,1 0.3,1 0,0.84')
    'T' = @('0,0 1,0', '0.5,0 0.5,1')
    'U' = @('0,0 0,0.7 0.2,0.95 0.5,1 0.8,0.95 1,0.7 1,0')
    'V' = @('0,0 0.5,1 1,0')
    'W' = @('0,0 0.22,1 0.5,0.36 0.78,1 1,0')
    'X' = @('0,0 1,1', '1,0 0,1')
    'Y' = @('0,0 0.5,0.52 1,0', '0.5,0.52 0.5,1')
    'Z' = @('0,0 1,0 0,1 1,1')
    '0' = @('0.5,0 0.85,0.16 1,0.5 0.85,0.84 0.5,1 0.15,0.84 0,0.5 0.15,0.16 0.5,0', '0.24,0.82 0.76,0.18')
    '1' = @('0.18,0.22 0.5,0 0.5,1', '0.24,1 0.78,1')
    '2' = @('0.05,0.2 0.3,0 0.7,0 0.95,0.2 0.9,0.46 0,1 1,1')
    '3' = @('0.05,0.12 0.35,0 0.75,0 0.95,0.2 0.72,0.46 0.42,0.5 0.8,0.56 1,0.76 0.78,0.98 0.38,1 0.03,0.88')
    '4' = @('0.76,1 0.76,0 0,0.7 1,0.7')
    '5' = @('1,0 0.15,0 0.08,0.45 0.5,0.4 0.9,0.55 0.96,0.8 0.7,1 0.3,1 0.02,0.88')
    '6' = @('0.9,0.05 0.5,0 0.15,0.2 0.02,0.55 0.1,0.86 0.45,1 0.8,0.92 0.95,0.68 0.8,0.45 0.45,0.4 0.12,0.56')
    '7' = @('0,0 1,0 0.4,1')
    '8' = @('0.5,0.5 0.2,0.38 0.2,0.12 0.5,0 0.8,0.12 0.8,0.38 0.5,0.5 0.15,0.62 0.15,0.88 0.5,1 0.85,0.88 0.85,0.62 0.5,0.5')
    '9' = @('0.1,0.95 0.5,1 0.85,0.8 0.98,0.45 0.9,0.14 0.55,0 0.2,0.08 0.05,0.32 0.2,0.56 0.55,0.6 0.88,0.45')
    '-' = @('0.1,0.5 0.9,0.5')
    '.' = @('0.45,0.95 0.55,0.95 0.55,1 0.45,1 0.45,0.95')
    '!' = @('0.5,0 0.5,0.68', '0.5,0.92 0.5,1')
    '?' = @('0.08,0.2 0.3,0 0.7,0 0.92,0.2 0.85,0.44 0.5,0.58 0.5,0.72', '0.5,0.92 0.5,1')
    '+' = @('0.5,0.2 0.5,0.8', '0.2,0.5 0.8,0.5')
}

$Script:Translit = @{
    'а'='A'; 'б'='B'; 'в'='V'; 'г'='G'; 'д'='D'; 'е'='E'; 'ё'='E'; 'ж'='ZH'; 'з'='Z'
    'и'='I'; 'й'='Y'; 'к'='K'; 'л'='L'; 'м'='M'; 'н'='N'; 'о'='O'; 'п'='P'; 'р'='R'
    'с'='S'; 'т'='T'; 'у'='U'; 'ф'='F'; 'х'='H'; 'ц'='C'; 'ч'='CH'; 'ш'='SH'; 'щ'='SCH'
    'ъ'=''; 'ы'='Y'; 'ь'=''; 'э'='E'; 'ю'='YU'; 'я'='YA'
}

function ConvertTo-Latin([string]$text) {
    $builder = New-Object Text.StringBuilder
    foreach ($char in $text.ToLower().ToCharArray()) {
        $key = [string]$char
        if ($Script:Translit.ContainsKey($key)) { [void]$builder.Append($Script:Translit[$key]) }
        else { [void]$builder.Append($char) }
    }
    return $builder.ToString().ToUpper()
}

<#
    Надпись штрихами.

    Возвращает штрихи в долях единичного квадрата, вписанные в строку заданной
    высоты. Буквы одинаковой ширины: пропорциональный набор потребовал бы
    таблицы ширин, а на глаз в рисунке от руки разница не читается.
#>
function Get-TextStrokes([string]$text, [double]$x, [double]$y, [double]$height, [double]$gap = 0.24) {
    $letters = ConvertTo-Latin $text
    $strokes = @()
    $width = $height * 0.62
    $step = $width * (1 + $gap)
    $at = $x

    foreach ($char in $letters.ToCharArray()) {
        $key = [string]$char
        if ($key -eq ' ') { $at += $step * 0.7; continue }
        if (-not $Script:Glyphs.ContainsKey($key)) { $at += $step; continue }
        foreach ($definition in $Script:Glyphs[$key]) {
            $points = @()
            foreach ($point in (ConvertTo-Stroke $definition)) {
                $points += ,(New-Point ($at + $point[0] * $width) ($y + $point[1] * $height))
            }
            $strokes += ,$points
        }
        $at += $step
    }
    return @{ strokes = $strokes; width = ($at - $x - $step * $gap) }
}

<#
    Надпись во весь холст.

    Слова переносятся по строкам, а не жмутся в одну. Иначе «единорог» выходил
    полоской в полтора сантиметра высотой: чтобы девять букв уместились по
    ширине, их пришлось бы ужать и по высоте — надпись оказывалась ровно там,
    где ей и место, но читать её было нечем.
#>
function Get-TextDrawing([string]$text) {
    $letters = ConvertTo-Latin $text
    $words = @($letters -split '\s+' | Where-Object { $_ })
    if ($words.Count -eq 0) { return ,@() }

    # Слово длиннее строки режется пополам (или на трети), а не сжимается: две
    # строки крупных букв читаются, одна мелкая — нет.
    $perLine = 7
    $split = @()
    foreach ($word in $words) {
        if ($word.Length -le $perLine) { $split += $word; continue }
        $parts = [int][Math]::Ceiling($word.Length / [double]$perLine)
        $size = [int][Math]::Ceiling($word.Length / [double]$parts)
        for ($at = 0; $at -lt $word.Length; $at += $size) {
            $split += $word.Substring($at, [Math]::Min($size, $word.Length - $at))
        }
    }
    $words = $split

    $lines = @()
    $current = ''
    foreach ($word in $words) {
        if (-not $current) { $current = $word }
        elseif (($current.Length + 1 + $word.Length) -le $perLine) { $current = $current + ' ' + $word }
        else { $lines += $current; $current = $word }
    }
    if ($current) { $lines += $current }

    $longest = 1
    foreach ($line in $lines) { if ($line.Length -gt $longest) { $longest = $line.Length } }

    # Высота буквы ограничена и числом строк, и длиной самой длинной из них:
    # что тесней, то и решает.
    $byRows = 0.86 / ($lines.Count * 1.45)
    $byCols = 0.92 / ($longest * 0.77)
    $height = [Math]::Min(0.34, [Math]::Min($byRows, $byCols))
    $step = $height * 1.45
    $top = 0.5 - ($lines.Count * $step - $height * 0.45) / 2

    $strokes = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $measure = Get-TextStrokes $lines[$i] 0 0 $height
        $left = [Math]::Max(0.02, (1 - $measure.width) / 2)
        foreach ($stroke in (Get-TextStrokes $lines[$i] $left ($top + $i * $step) $height).strokes) {
            $strokes += ,$stroke
        }
    }
    return ,$strokes
}

<#
    Что помощник умеет рисовать.

    «Себя» — это его же сфера из панели: тело, блик, ореол и линия голоса.
    Остальное — простые узнаваемые вещи, которые собираются из дуг и линий.
    Всё, чего в списке нет, рисуется надписью: честнее написать слово, чем
    выдать случайные линии за кота.
#>
function Get-Drawing([string]$what) {
    $name = ($what -replace 'ё', 'е').Trim().ToLower()
    $pi = [Math]::PI
    $strokes = @()

    switch -Regex ($name) {

        '(себя|сам|портрет|маскот|аватар|cloud ?hdr|облачк)' {
            # Сфера-маскот: тело, внутренний блик, два ореола и линия голоса.
            #
            # Запятая перед каждым штрихом обязательна. Без неё PowerShell
            # раскладывает дугу на отдельные точки и добавляет в список штрихов
            # не одну линию, а сорок «штрихов» по одной точке — рисунок
            # превращается в россыпь щелчков по холсту.
            $strokes += ,(New-Arc 0.5 0.42 0.26 0.26 0 (2 * $pi) 40)
            # Блик — маленький замкнутый овал сверху слева, а не дужка: дужка
            # читалась завитком, будто на шаре что-то написано.
            $strokes += ,(New-Arc 0.41 0.32 0.055 0.038 0 (2 * $pi) 14)
            # Внутренняя кромка стекла снизу справа — отражённый свет.
            $strokes += ,(New-Arc 0.5 0.42 0.2 0.2 (0.12 * $pi) (0.78 * $pi) 18)
            # Ореол: две дуги по бокам, сверху и снизу разрыв — так он читается
            # свечением вокруг сферы, а не вторым кругом.
            $strokes += ,(New-Arc 0.5 0.42 0.34 0.34 (1.12 * $pi) (1.88 * $pi) 22)
            $strokes += ,(New-Arc 0.5 0.42 0.34 0.34 (0.12 * $pi) (0.88 * $pi) 22)

            # Линия голоса: две синусоиды разной длины, как в самой панели.
            $wave = @()
            for ($i = 0; $i -le 60; $i++) {
                $t = $i / 60.0
                $fade = [Math]::Sin($pi * $t)
                $value = [Math]::Sin($t * 11.0) * 0.68 + [Math]::Sin($t * 23.0) * 0.32
                $wave += ,(New-Point (0.06 + $t * 0.88) (0.86 + $fade * $value * 0.075))
            }
            $strokes += ,$wave
            break
        }

        '(логотип|лого|эмблем|значок|бренд)' {
            # Облако — три горба на одной линии и донышко под ними.
            #
            # Горбы сцеплены: каждый начинается ровно там, где кончился
            # предыдущий, потому что радиусы подобраны под их центры. Первая
            # попытка была с произвольными радиусами, и облако выходило с
            # зубцами: между горбами оставались перескоки, а одна линия
            # соединяла их напрямую через всё небо.
            #
            # Средний горб выше не радиусом, а вытянутостью: увеличь радиус —
            # и сцепление разъедется, а овал растёт вверх, ничего не смещая.
            $base = 0.58
            $cloud = @()
            foreach ($point in (New-Arc 0.30 $base 0.10 0.10 $pi (2 * $pi) 18)) { $cloud += ,$point }
            foreach ($point in (New-Arc 0.52 $base 0.12 0.17 $pi (2 * $pi) 22)) { $cloud += ,$point }
            foreach ($point in (New-Arc 0.72 $base 0.08 0.09 $pi (2 * $pi) 16)) { $cloud += ,$point }
            $cloud += ,(New-Point 0.8 $base)
            $cloud += ,(New-Point 0.2 $base)
            $strokes += ,$cloud

            $wave = @()
            for ($i = 0; $i -le 44; $i++) {
                $t = $i / 44.0
                $wave += ,(New-Point (0.16 + $t * 0.68) (0.68 + [Math]::Sin($t * 12.0) * [Math]::Sin($pi * $t) * 0.045))
            }
            $strokes += ,$wave

            foreach ($stroke in (Get-TextStrokes 'CLOUD HDR' 0.12 0.8 0.11).strokes) { $strokes += ,$stroke }
            break
        }

        '(кот|кошк|кис)' {
            $strokes += ,(New-Arc 0.5 0.52 0.26 0.24 0 (2 * $pi) 34)
            $strokes += ,(ConvertTo-Stroke '0.3,0.34 0.26,0.12 0.44,0.24')       # ухо
            $strokes += ,(ConvertTo-Stroke '0.7,0.34 0.74,0.12 0.56,0.24')
            $strokes += ,(ConvertTo-Stroke '0.4,0.46 0.4,0.52')                  # глаза
            $strokes += ,(ConvertTo-Stroke '0.6,0.46 0.6,0.52')
            $strokes += ,(ConvertTo-Stroke '0.46,0.6 0.5,0.64 0.54,0.6')         # нос
            $strokes += ,(ConvertTo-Stroke '0.5,0.64 0.5,0.68')
            $strokes += ,(ConvertTo-Stroke '0.5,0.68 0.42,0.72 0.36,0.68')       # улыбка
            $strokes += ,(ConvertTo-Stroke '0.5,0.68 0.58,0.72 0.64,0.68')
            $strokes += ,(ConvertTo-Stroke '0.24,0.6 0.06,0.56')                 # усы
            $strokes += ,(ConvertTo-Stroke '0.24,0.64 0.06,0.66')
            $strokes += ,(ConvertTo-Stroke '0.76,0.6 0.94,0.56')
            $strokes += ,(ConvertTo-Stroke '0.76,0.64 0.94,0.66')
            break
        }

        '(дом|изб|здани|хат)' {
            $strokes += ,(ConvertTo-Stroke '0.16,0.5 0.5,0.2 0.84,0.5')
            $strokes += ,(ConvertTo-Stroke '0.24,0.46 0.24,0.86 0.76,0.86 0.76,0.46')
            $strokes += ,(ConvertTo-Stroke '0.44,0.86 0.44,0.62 0.58,0.62 0.58,0.86')
            $strokes += ,(ConvertTo-Stroke '0.3,0.54 0.4,0.54 0.4,0.64 0.3,0.64 0.3,0.54')
            $strokes += ,(ConvertTo-Stroke '0.64,0.3 0.64,0.16 0.72,0.16 0.72,0.38')
            break
        }

        '(звезд|звёзд|star)' {
            $star = @()
            for ($i = 0; $i -le 10; $i++) {
                $angle = -$pi / 2 + $i * $pi / 5
                $radius = if (($i % 2) -eq 0) { 0.42 } else { 0.17 }
                $star += ,(New-Point (0.5 + [Math]::Cos($angle) * $radius) (0.5 + [Math]::Sin($angle) * $radius))
            }
            $strokes += ,$star
            break
        }

        '(сердц|любов|heart)' {
            $heart = @()
            for ($i = 0; $i -le 70; $i++) {
                $t = $i / 70.0 * 2 * $pi
                $hx = 16 * [Math]::Pow([Math]::Sin($t), 3)
                $hy = 13 * [Math]::Cos($t) - 5 * [Math]::Cos(2 * $t) - 2 * [Math]::Cos(3 * $t) - [Math]::Cos(4 * $t)
                $heart += ,(New-Point (0.5 + $hx / 40.0) (0.5 - $hy / 40.0))
            }
            $strokes += ,$heart
            break
        }

        '(солнц|солнышк|sun)' {
            $strokes += ,(New-Arc 0.5 0.5 0.22 0.22 0 (2 * $pi) 30)
            for ($i = 0; $i -lt 12; $i++) {
                $angle = $i * $pi / 6
                $ray = @()
                $ray += ,(New-Point (0.5 + [Math]::Cos($angle) * 0.28) (0.5 + [Math]::Sin($angle) * 0.28))
                $ray += ,(New-Point (0.5 + [Math]::Cos($angle) * 0.42) (0.5 + [Math]::Sin($angle) * 0.42))
                $strokes += ,$ray
            }
            break
        }

        '(смайл|улыб|рожиц|лиц)' {
            $strokes += ,(New-Arc 0.5 0.5 0.38 0.38 0 (2 * $pi) 40)
            $strokes += ,(ConvertTo-Stroke '0.36,0.4 0.36,0.5')
            $strokes += ,(ConvertTo-Stroke '0.64,0.4 0.64,0.5')
            $strokes += ,(New-Arc 0.5 0.55 0.2 0.16 (0.15 * $pi) (0.85 * $pi) 18)
            break
        }

        '(ёлк|елк|дерев|сосн)' {
            $strokes += ,(ConvertTo-Stroke '0.5,0.1 0.32,0.4 0.42,0.4 0.26,0.62 0.38,0.62 0.2,0.84 0.8,0.84 0.62,0.62 0.74,0.62 0.58,0.4 0.68,0.4 0.5,0.1')
            $strokes += ,(ConvertTo-Stroke '0.44,0.84 0.44,0.94 0.56,0.94 0.56,0.84')
            break
        }

        '(цвет|ромашк|flower)' {
            # Лепестки не заходят на сердцевину: радиус меньше расстояния до
            # центра, иначе цветок выглядел клубком окружностей.
            for ($i = 0; $i -lt 6; $i++) {
                $angle = $i * $pi / 3
                $strokes += ,(New-Arc (0.5 + [Math]::Cos($angle) * 0.19) (0.4 + [Math]::Sin($angle) * 0.19) 0.1 0.1 0 (2 * $pi) 20)
            }
            $strokes += ,(New-Arc 0.5 0.4 0.075 0.075 0 (2 * $pi) 16)
            $strokes += ,(ConvertTo-Stroke '0.5,0.62 0.53,0.95')
            $strokes += ,(ConvertTo-Stroke '0.53,0.78 0.72,0.7 0.62,0.86 0.53,0.84')
            break
        }

        default {
            # Ничего похожего не нашлось — пишем словом.
            $strokes = Get-TextDrawing $what
        }
    }

    # Запятая на выходе — по той же причине, что и при сборке: у рисунка из
    # одного штриха (звезда, сердце) PowerShell иначе вернул бы не список
    # штрихов, а сам штрих, и вызывающий принял бы каждую точку за отдельную
    # линию.
    return ,$strokes
}

<#
    Перенести рисунок на холст и провести его мышью.

    Пропорции сохраняются: круг обязан остаться кругом на любом холсте. Поля в
    восемь процентов оставлены не для красоты — у края холста Paint показывает
    маркеры изменения размера, и штрих, доведённый до самого края, схватил бы
    их вместо рисования.
#>
function Write-Strokes($strokes, $rect, [double]$margin = 0.08) {
    if (-not $strokes -or $strokes.Count -eq 0) { return 0 }

    $width = $rect.right - $rect.left
    $height = $rect.bottom - $rect.top
    $size = [Math]::Min($width, $height) * (1 - 2 * $margin)
    $ox = $rect.left + ($width - $size) / 2
    $oy = $rect.top + ($height - $size) / 2

    $drawn = 0
    $total = @($strokes).Count
    foreach ($stroke in $strokes) {
        Assert-Running
        if (-not $stroke -or $stroke.Count -lt 2) { continue }

        # Каждый штрих отмечается в журнале. Рисунок — самое долгое, что умеет
        # помощник: минута, за которую снаружи не понять, идёт работа или всё
        # встало. Одна строка на штрих решает это раз и навсегда.
        Write-Cursor ("Штрих {0} из {1}" -f ($drawn + 1), $total)

        $points = @()
        foreach ($point in $stroke) {
            $px = $ox + [Math]::Max(0.0, [Math]::Min(1.0, [double]$point[0])) * $size
            $py = $oy + [Math]::Max(0.0, [Math]::Min(1.0, [double]$point[1])) * $size
            $points += ,(New-Point $px $py)
        }

        # Пустой штрих молча не пропускается.
        #
        # Однажды из-за опечатки в сборке точек все штрихи оказались пустыми, и
        # помощник бодро отчитался «нарисовал, штрихов 15» над совершенно белым
        # листом. Ошибка, о которой никто не сообщил, — худший вид ошибки:
        # искать её пришлось не там, где она была.
        if ($points.Count -lt 2) {
            Write-Cursor 'Штрих остался без точек — пропускаю' 'warn'
            continue
        }

        # Длинные отрезки разбиваются: Paint соединяет соседние положения мыши
        # прямой, и без дробления дуга из двадцати точек выглядела бы гранёной.
        $dense = @()
        $dense += ,$points[0]
        for ($i = 1; $i -lt $points.Count; $i++) {
            $ax = $points[$i - 1][0]; $ay = $points[$i - 1][1]
            $bx = $points[$i][0]; $by = $points[$i][1]
            $length = [Math]::Sqrt(($bx - $ax) * ($bx - $ax) + ($by - $ay) * ($by - $ay))
            $parts = [int][Math]::Max(1, [Math]::Min(24, [Math]::Round($length / 14)))
            for ($k = 1; $k -le $parts; $k++) {
                $t = $k / [double]$parts
                $dense += ,(New-Point ($ax + ($bx - $ax) * $t) ($ay + ($by - $ay) * $t))
            }
        }

        Invoke-Drag $dense 7
        $drawn++
    }
    return $drawn
}

# ----------------------------------------------------------------- навыки ----
#
# Навык — это то, что помощник умеет делать целиком: не «щёлкни там», а «открой
# Paint и нарисуй логотип». Внутри он раскладывается на те же движения мыши,
# но снаружи это одна понятная просьба, и именно так её и произносят.

$Script:KnownApps = @{
    'проводник' = 'explorer.exe'; 'explorer' = 'explorer.exe'; 'папки' = 'explorer.exe'
    'paint' = 'mspaint.exe'; 'пейнт' = 'mspaint.exe'; 'краски' = 'mspaint.exe'; 'рисовалк' = 'mspaint.exe'
    'блокнот' = 'notepad.exe'; 'notepad' = 'notepad.exe'
    'калькулятор' = 'calc.exe'; 'calc' = 'calc.exe'
    'браузер' = 'chrome.exe'; 'хром' = 'chrome.exe'; 'chrome' = 'chrome.exe'
    'edge' = 'msedge.exe'; 'ворд' = 'winword.exe'; 'терминал' = 'wt.exe'
}

function Resolve-AppFile([string]$name) {
    $key = ($name -replace 'ё', 'е').Trim().ToLower()
    foreach ($known in $Script:KnownApps.Keys) {
        if ($key -match [regex]::Escape($known)) { return $Script:KnownApps[$known] }
    }
    return $key
}

<#
    Проводник: открыть, полистать, найти папку.

    Адресная строка вызывается Ctrl+L и заполняется с клавиатуры — это
    единственный способ попасть в нужное место, одинаково работающий в Windows
    10 и 11 и не зависящий от того, что человек прикрепил на боковую панель.
#>
function Invoke-SkillExplorer([string]$folder = '', [switch]$Browse, [string]$find = '') {

    # «Найди любую папку» — это не поиск по имени, а просьба выбрать хоть
    # что-нибудь. Отдельная проверка нужна: иначе помощник честно искал бы папку
    # с именем «любую», не находил и отвечал отказом на выполнимую просьбу.
    if ($find -match '^\s*(любую|любой|любая|какую[- ]?нибудь|что[- ]?нибудь|что угодно|хоть какую)\s*(папку|каталог)?\s*$') {
        $find = ''
    }

    # Открытый Проводник переиспользуется. Иначе просьба «открой проводник,
    # полистай и найди папку» оставляла бы после себя три окна: каждый шаг
    # честно жал бы Win+E.
    $opened = $false
    $window = Wait-Window 'explorer' 'CabinetWClass' '' 250
    if ($window) {
        Write-Cursor 'Перехожу в открытый Проводник'
        Set-WindowFront $window | Out-Null
    } else {
        $opened = $true
        Write-Cursor 'Открываю Проводник'
        Send-Chord 'win+e'
        $window = Wait-Window 'explorer' 'CabinetWClass' '' 9000
        if (-not $window) {
            Write-Cursor 'Проводник не открылся' 'warn'
            return @{ ok = $false; say = 'Проводник не открылся — попробуйте ещё раз.' }
        }
        Set-WindowFront $window | Out-Null
    }
    Wait-Idle 500

    # В адресную строку лезем только тогда, когда есть куда идти. Своё окно
    # открываем в домашней папке — там всегда есть что листать, — а в чужое,
    # уже открытое, не вмешиваемся: человек мог оставить его на нужном месте.
    $where = $folder
    if (-not $where -and $opened) { $where = $env:USERPROFILE }
    if ($where) {
        Write-Cursor ("Перехожу в {0}" -f $where)
        Send-Chord 'ctrl+l'
        Wait-Tick 220
        Send-Text $where
        Send-Chord 'enter'
        Wait-Idle 1300
    }

    # Курсор переезжает в середину списка: без этого колесо крутило бы ту
    # область, над которой он остался, — а это могла быть боковая панель.
    $window = Wait-Window 'explorer' 'CabinetWClass' '' 3000
    if ($window) {
        Move-Cursor ($window.left + $window.width * 0.6) ($window.top + $window.height * 0.55)
    }

    if ($Browse) {
        Write-Cursor 'Листаю содержимое'
        Invoke-Scroll 4 'down'
        Wait-Idle 500
        Invoke-Scroll 3 'up'
        Wait-Idle 300
    }

    $items = @()
    if ($window) {
        $items = @(Get-UiItems $window 'ListItem' 220)
    }

    if ($find) {
        Write-Cursor ("Ищу «{0}»" -f $find)
        $needle = $find.ToLower()
        $match = $items | Where-Object { $_.name -and $_.name.ToLower().Contains($needle) } | Select-Object -First 1
        if (-not $match) {
            # Не нашлось глазами — спрашиваем сам Проводник его же поиском.
            Send-Chord 'ctrl+f'
            Wait-Tick 300
            Send-Text $find
            Send-Chord 'enter'
            Wait-Idle 2600
            $window = Wait-Window 'explorer' 'CabinetWClass' '' 2000
            $items = @(Get-UiItems $window 'ListItem' 120)
            $match = $items | Where-Object { $_.name -and $_.name.ToLower().Contains($needle) } | Select-Object -First 1
        }
        if ($match -and (Test-PointFree $window $match.x $match.y)) {
            Write-Cursor ("Нашёл: {0}" -f $match.name) 'good'
            Invoke-ClickAt $match.x $match.y 'left' 2
            Wait-Idle 900
            return @{ ok = $true; say = ("Нашёл и открыл «{0}»." -f $match.name) }
        }
        if ($match) {
            return @{ ok = $true; say = ("«{0}» нашлось, но до него не добраться — список прокручен." -f $match.name) }
        }
        return @{ ok = $true; say = ("«{0}» в этой папке не нашлось." -f $find) }
    }

    # Просто «полистай и найди какую-нибудь папку» — берём первую же строку
    # списка, до которой действительно можно дотянуться мышью.
    $folderItem = $items |
        Where-Object { $_.name -and $_.name.Trim() -and (Test-PointFree $window $_.x $_.y) } |
        Select-Object -First 1
    if ($folderItem) {
        Write-Cursor ("Открываю «{0}»" -f $folderItem.name) 'good'
        Invoke-ClickAt $folderItem.x $folderItem.y 'left' 2
        Wait-Idle 1100
        return @{ ok = $true; say = ("Полистал и открыл «{0}»." -f $folderItem.name) }
    }

    return @{ ok = $true; say = 'Проводник открыт, содержимое пролистано.' }
}

<#
    Paint: открыть, распахнуть, найти лист и нарисовать.

    Холст ищется зрением, а не по отступам от края окна. Отступы были первым
    решением и продержались недолго: в Windows 10 у Paint лента сверху, в
    Windows 11 — панель инструментов другой высоты, а при масштабе 125% не
    сходится ни то, ни другое. Самый большой белый прямоугольник на экране — это
    и есть лист, в любой версии и при любой теме.
#>
function Invoke-SkillPaint([string]$what) {
    Write-Cursor 'Открываю Paint'
    if (-not (Start-App 'mspaint.exe')) {
        return @{ ok = $false; say = 'Paint не запустился.' }
    }
    $window = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 12000
    if (-not $window) {
        return @{ ok = $false; say = 'Окно Paint не появилось.' }
    }

    Set-WindowFront $window | Out-Null
    Set-WindowBig $window
    Wait-Idle 900

    # Размеры окна берём заново: распахнутое окно занимает совсем другой
    # прямоугольник, а искать лист надо в новом.
    $grown = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 3000
    if ($grown) { $window = $grown }

    Write-Cursor 'Ищу лист на экране'
    $bounds = @{ left = $window.left; top = $window.top; right = $window.right; bottom = $window.bottom }
    $canvas = Find-WhiteRect $bounds
    if (-not $canvas) {
        return @{ ok = $false; say = 'Не нашёл белый лист в окне Paint.' }
    }

    # Полоса под лентой инструментов иногда тоже белая и попадает в находку.
    # Лист от неё отличается тем, что он высокий: если найденное ниже сотни
    # пикселей, это точно не холст.
    if (($canvas.bottom - $canvas.top) -lt 140 -or ($canvas.right - $canvas.left) -lt 140) {
        return @{ ok = $false; say = 'Лист Paint слишком мал для рисунка.' }
    }

    $strokes = Get-Drawing $what
    Write-Cursor ("Веду мышью по холсту: штрихов {0}" -f @($strokes).Count)
    $drawn = Write-Strokes $strokes $canvas
    Write-Cursor 'Рисунок готов' 'good'
    return @{ ok = $true; say = ("Нарисовал в Paint: {0}. Штрихов — {1}." -f $what, $drawn) }
}

function Invoke-SkillDraw([string]$what) {
    # Рисовать можно и в уже открытом Paint: если он есть, второй не нужен.
    $window = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 400
    if (-not $window) { return Invoke-SkillPaint $what }

    Set-WindowFront $window | Out-Null
    Wait-Idle 400
    $bounds = @{ left = $window.left; top = $window.top; right = $window.right; bottom = $window.bottom }
    $canvas = Find-WhiteRect $bounds
    if (-not $canvas) { return @{ ok = $false; say = 'Не нашёл белый лист — откройте Paint.' } }

    $strokes = Get-Drawing $what
    Write-Cursor ("Веду мышью по холсту: штрихов {0}" -f @($strokes).Count)
    $drawn = Write-Strokes $strokes $canvas
    return @{ ok = $true; say = ("Нарисовал: {0}. Штрихов — {1}." -f $what, $drawn) }
}

function Invoke-SkillClickText([string]$name) {
    $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
    $window = Get-WindowInfo $handle

    # Сначала — по смыслу, а не по подстроке.
    #
    # Find-UiItem ищет вхождение, и этого не хватает ровно там, где чаще всего
    # и просят: «нажми сохранить» в окне, где кнопка называется «Сохранить
    # как…», вхождением находится, а «нажми настройки» при кнопке «Параметры»
    # — уже нет. Оценка по имени переживает и склонение, и синоним по началу
    # слова, и опечатку распознавания.
    $best = $null
    foreach ($item in (Get-UiItems $window '' 400)) {
        if (-not $item.name -or -not $item.name.Trim()) { continue }
        $score = Get-NameScore $name $item.name
        if ($score -le 0) { continue }
        if (-not (Test-PointFree $window $item.x $item.y)) { continue }
        if (-not $best -or $score -gt $best.score) { $best = @{ score = $score; item = $item } }
    }

    if (-not $best) {
        # Не нашлось на виду — может быть, оно в меню. Меню в Windows не
        # существует, пока его не открыли: в дереве доступности пунктов просто
        # нет. Поэтому раскрываем верхнее меню и смотрим ещё раз.
        $opened = Invoke-SkillMenu $name
        if ($opened.ok) { return $opened }
        return @{ ok = $false; say = ("Не вижу «{0}» в этом окне." -f $name) }
    }

    Write-Cursor ("Нажимаю «{0}»" -f $best.item.name) 'good'
    Invoke-ClickAt $best.item.x $best.item.y
    Wait-Idle 400
    return @{ ok = $true; say = ("Нажал «{0}»." -f $best.item.name) }
}

<#
    Поискать пункт в меню окна.

    Меню — единственная часть чужого интерфейса, которой не видно, пока её не
    тронули: до раскрытия в дереве доступности лежит только заголовок («Файл»,
    «Правка»), а пунктов внутри нет вовсе. Поэтому здесь порядок обратный
    обычному: сначала действие, потом взгляд.

    Меню закрывается за собой при неудаче. Оставленное раскрытым меню перехватит
    следующий же клик плана и уведёт его в никуда — а выглядеть это будет как
    «помощник нажал не туда».
#>
function Invoke-SkillMenu([string]$name) {
    $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
    $window = Get-WindowInfo $handle
    $bars = @(Get-UiItems $window 'MenuItem' 40)
    if ($bars.Count -eq 0) { return @{ ok = $false } }

    foreach ($bar in ($bars | Select-Object -First 8)) {
        if (-not (Test-PointFree $window $bar.x $bar.y)) { continue }
        Invoke-ClickAt $bar.x $bar.y
        Wait-Idle 350

        $best = $null
        foreach ($item in (Get-UiItems $window 'MenuItem' 200)) {
            if (-not $item.name -or -not $item.name.Trim()) { continue }
            if ($item.name -eq $bar.name) { continue }
            $score = Get-NameScore $name $item.name
            if ($score -le 0) { continue }
            if (-not $best -or $score -gt $best.score) { $best = @{ score = $score; item = $item } }
        }

        if ($best -and $best.score -ge 74) {
            Write-Cursor ("Меню «{0}» → «{1}»" -f $bar.name, $best.item.name) 'good'
            Invoke-ClickAt $best.item.x $best.item.y
            Wait-Idle 500
            return @{ ok = $true; say = ("Выбрал «{0}» в меню «{1}»." -f $best.item.name, $bar.name) }
        }
        Send-Chord 'escape'
        Wait-Tick 180
    }
    return @{ ok = $false }
}

<#
    Открыть путь: файл или папку.

    Папка открывается в Проводнике через адресную строку, файл — своей
    программой. Разделение важно: «открой отчёт.docx» через Проводник показало
    бы папку с файлом, а человек просил открыть файл.
#>
function Invoke-SkillOpenPath([string]$path, [switch]$Browse) {
    if (-not $path) { return @{ ok = $false; say = 'Путь пустой.' } }
    if (-not (Test-Path -LiteralPath $path)) {
        return @{ ok = $false; say = ("«{0}» больше нет на месте." -f (Split-Path -Leaf $path)) }
    }

    $isFolder = (Get-Item -LiteralPath $path).PSIsContainer
    if ($isFolder) {
        return Invoke-SkillExplorer $path -Browse:$Browse
    }

    Write-Cursor ("Открываю {0}" -f (Split-Path -Leaf $path))
    if (-not (Start-App $path)) {
        return @{ ok = $false; say = ("«{0}» не открылся — нечем." -f (Split-Path -Leaf $path)) }
    }
    Wait-Idle 1600

    # Своё окно выводим вперёд и распахиваем: файл, открытый за чужим окном,
    # человек считает неоткрытым.
    $leaf = [regex]::Escape([IO.Path]::GetFileNameWithoutExtension($path))
    $window = Wait-Window '' '' $leaf 6000
    if ($window) { Set-WindowFront $window | Out-Null }
    return @{ ok = $true; say = ("Открыл «{0}»." -f (Split-Path -Leaf $path)) }
}

<#
    Запустить программу.

    Три вида пути, и все три встречаются в списке установленного: ярлык из меню
    «Пуск», исполняемый файл из реестра и адрес steam://. Последний вообще не
    файл, и запускать его надо оболочкой — попытка обращаться с ним как с
    программой была бы ошибкой сразу.
#>
function Invoke-SkillOpenApp([string]$path, [string]$name) {
    if (-not $path) { return @{ ok = $false; say = ("Не знаю, чем открыть «{0}»." -f $name) } }

    Write-Cursor ("Запускаю {0}" -f $name)

    # Адреса оболочки (shell:AppsFolder\…, ms-settings:) и обычные схемы
    # (steam://) — это не файлы, и запускать их надо тем, что умеет их
    # разбирать. Для первых это Проводник, для вторых — сама оболочка.
    if ($path -like 'shell:*') {
        try { Start-Process 'explorer.exe' -ArgumentList $path | Out-Null } catch {
            return @{ ok = $false; say = ("«{0}» не запустилась." -f $name) }
        }
        Wait-Idle 2600
        $window = Wait-Window '' '' ([regex]::Escape($name)) 6000
        if ($window) { Set-WindowFront $window | Out-Null }
        return @{ ok = $true; say = ("Запустил {0}." -f $name) }
    }

    if ($path -match '^[a-z][a-z0-9+.-]*:') {
        try { Start-Process $path | Out-Null } catch {
            return @{ ok = $false; say = ("«{0}» не запустилась." -f $name) }
        }
        Wait-Idle 2500
        return @{ ok = $true; say = ("Запустил {0}." -f $name) }
    }

    if (-not (Start-App $path)) {
        return @{ ok = $false; say = ("«{0}» не запустилась." -f $name) }
    }
    Wait-Idle 1800

    $window = Wait-Window '' '' ([regex]::Escape($name)) 6000
    if ($window) { Set-WindowFront $window | Out-Null }
    return @{ ok = $true; say = ("Запустил {0}." -f $name) }
}

function Invoke-SkillRaiseWindow([string]$title) {
    $window = @(Get-Windows | Where-Object { (Get-NameScore $title $_.title) -ge 74 }) | Select-Object -First 1
    if (-not $window) { return @{ ok = $false; say = ("Окна «{0}» не нашёл." -f $title) } }
    Set-WindowFront $window | Out-Null
    Wait-Idle 400
    return @{ ok = $true; say = ("Перешёл в «{0}»." -f $window.title) }
}

<#
    Закрыть окно.

    Закрываем именно ОКНО, а не процесс. Разница существенная: у браузера одно
    окно из трёх, у редактора — несохранённый документ. Alt+F4 в наведённое
    окно ведёт себя ровно так, как если бы человек нажал крестик, включая
    вопрос «сохранить изменения?». Убийство процесса такого вопроса не задаёт,
    и потерянная работа была бы уже нашей виной.
#>
function Invoke-SkillCloseWindow([string]$title) {
    $window = @(Get-Windows | Where-Object { (Get-NameScore $title $_.title) -ge 74 }) | Select-Object -First 1
    if (-not $window) { return @{ ok = $false; say = ("Окна «{0}» не нашёл." -f $title) } }
    Set-WindowFront $window | Out-Null
    Wait-Idle 300
    Send-Chord 'alt+f4'
    Wait-Idle 700
    return @{ ok = $true; say = ("Закрыл «{0}»." -f $window.title) }
}

<#
    Найти что-нибудь и показать.

    Сначала смотрим среди файлов и папок — «найди отчёт» почти всегда про них.
    Не нашлось — ищем в текущем окне: в Проводнике это его же поиск, в
    остальных окнах Ctrl+F делает то же самое, чего ждёт человек.
#>
function Invoke-SkillFind([string]$what) {
    if (-not $what) { return @{ ok = $false; say = 'Не понял, что искать.' } }

    $found = @(Get-MindFiles $what 5)
    if ($found.Count -gt 0) {
        $first = $found[0]
        Write-Cursor ("Нашёл: {0}" -f $first.path) 'good'
        # Показываем в папке, а не открываем: «найди» — это просьба показать,
        # и открывать найденное без спроса было бы вольностью.
        Start-App 'explorer.exe' ("/select,`"{0}`"" -f $first.path) | Out-Null
        Wait-Idle 1400
        return @{ ok = $true; say = ("Нашёл «{0}» и показал в папке." -f $first.name) }
    }

    $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
    $window = Get-WindowInfo $handle
    if ($window) {
        Set-WindowFront $window | Out-Null
        Send-Chord 'ctrl+f'
        Wait-Tick 350
        Send-Text $what
        Send-Chord 'enter'
        Wait-Idle 900
        return @{ ok = $true; say = ("Поискал «{0}» в открытом окне." -f $what) }
    }
    return @{ ok = $false; say = ("«{0}» не нашлось." -f $what) }
}

# ------------------------------------------------------------------ план -----
#
# Фраза человека превращается в список шагов, и шаги — единственное, что умеет
# выполняться. Такой промежуток нужен по двум причинам: план видно в окне до
# того, как курсор двинулся, и в него нельзя протащить ничего, кроме перечня
# ниже, — даже если план пришёл от языковой модели.

function New-Step([string]$act, $data = @{}) {
    $step = @{ act = $act }
    foreach ($pair in $data.GetEnumerator()) { $step[$pair.Key] = $pair.Value }
    return $step
}

<#
    Разбор фразы.

    Фраза режется на части по «и», «потом», «затем», запятым — люди говорят
    поручения именно так: «открой проводник и полистай и найди папку». Каждая
    часть разбирается отдельно и даёт свои шаги.
#>
<#
    Отдать ли поручение модели целиком.

    Быстрые правила хороши для одиночных понятных действий — «полистай вниз»,
    «нажми Enter», «нарисуй кота»: они выполняются мгновенно и без видеокарты.
    Всё составное и всё, что правила не поняли до конца, ведёт модель по шагам
    (cursor-mind.ps1, Invoke-MindAgent): она видит экран и поправляется.
#>
$Script:QuickActs = @('scroll', 'keys', 'type', 'wait', 'stop', 'draw', 'ensure_paint', 'paint',
                      'open_app', 'open_path', 'raise_window', 'close_window', 'close_app', 'click_point', 'explorer', 'click', 'move')
$Script:AgentVerbs = '^\s*(?:курсор(?:ом)?\s+|мышк(?:ой|а)\s+|руками\s+)?(?:открой|запусти|зайди|перейди|найди|поищи|выбери|включи|поставь|сделай|скачай|отметь|заполни|выдели|скопируй|вставь|нажми|кликни|щелкни|щёлкни|напиши|введи|закрой|сверни|разверни|прокрути|полистай|листай|добавь|создай|переименуй|сохрани|посмотри|покажи|продолжи|останови|отключи|убери|верни|измени)(?![а-яё])'
$Script:AgentCheckedAt = [datetime]::MinValue
$Script:AgentReady = $false

function Test-AgentWanted([string]$text, $plan) {
    if (-not (Get-Command Invoke-MindAgent -ErrorAction SilentlyContinue)) { return $false }
    $steps = @($plan)
    if (@($steps | Where-Object { $_.act -eq 'draw' -or $_.act -eq 'stop' }).Count -gt 0) { return $false }
    $unknown = @($steps | Where-Object { $_.act -eq 'unknown' }).Count
    $simple = ($steps.Count -eq 1 -and $Script:QuickActs -contains [string]$steps[0].act)
    if ($simple) { return $false }
    # Совсем не понятое и без глагола действия — это разговор, а не поручение.
    if ($unknown -eq $steps.Count -and $text -notmatch $Script:AgentVerbs) { return $false }
    if (((Get-Date) - $Script:AgentCheckedAt).TotalSeconds -gt 30) {
        $Script:AgentCheckedAt = Get-Date
        $Script:AgentReady = Test-AgentModel
    }
    return $Script:AgentReady
}

function ConvertTo-Plan([string]$text) {
    # Составную фразу («зайди в вк и открой сообщения») и явное «курсором …»
    # правила всё равно не доведут — не тратим на них полторы-две секунды
    # обхода экрана, а сразу отдаём модели. Рисование — исключение: его умеют
    # только правила.
    $compound = ($text -match '(?:\s+и\s+|,\s*|\s+потом\s+|\s+затем\s+)' -and $text -match $Script:AgentVerbs) -or
                $text -match '^\s*(?:курсор(?:ом)?|мышк(?:ой|а)|руками)[\s,]'
    if ($compound -and $text -notmatch '(нарису|рису|изобрази)' -and (Get-Command Invoke-MindAgent -ErrorAction SilentlyContinue)) {
        if (((Get-Date) - $Script:AgentCheckedAt).TotalSeconds -gt 30) {
            $Script:AgentCheckedAt = Get-Date
            $Script:AgentReady = Test-AgentModel
        }
        if ($Script:AgentReady) {
            $goal = ($text -replace '^\s*(?:курсор(?:ом)?|мышк(?:ой|а)|руками)[\s,]+', '').Trim()
            return @(New-Step 'agent' @{ goal = $goal; say = 'Смотрю на экран и берусь за дело' })
        }
    }
    $plan = @(ConvertTo-PlanQuick $text)
    if (Test-AgentWanted $text $plan) {
        $goal = ($text -replace '^\s*(?:курсор(?:ом)?|мышк(?:ой|а)|руками)\s+', '').Trim()
        return @(New-Step 'agent' @{ goal = $goal; say = 'Смотрю на экран и берусь за дело' })
    }
    return $plan
}

function ConvertTo-PlanQuick([string]$text) {
    # Понимание живёт в cursor-mind.ps1 и подключается ниже, в конце файла.
    #
    # Разбор ниже остался запасным, а не был удалён, и это не осторожность ради
    # осторожности. Понимание смотрит на экран и ходит в индексы: обход дерева
    # окна может не ответить в чужом приложении, служба понимания может быть не
    # поднята. Провалиться в такой момент в «не понял» было бы обидно, потому
    # что простые фразы — «полистай вниз», «нарисуй кота» — разбираются и без
    # всякого экрана. Поэтому: сначала понимание, при отказе — правила.
    if (Get-Command New-MindPlan -ErrorAction SilentlyContinue) {
        try {
            $thought = @(New-MindPlan $text)
            if ($thought.Count -gt 0) {
                $lost = @($thought | Where-Object { $_.act -eq 'unknown' }).Count
                if ($lost -eq 0) { return $thought }
                # Часть фразы понимание не осилило — пробуем правилами, и берём
                # их ответ, только если он покрывает больше.
                $byRules = @(ConvertTo-PlanByRules $text)
                $lostByRules = @($byRules | Where-Object { $_.act -eq 'unknown' }).Count
                if ($lostByRules -lt $lost) { return $byRules }
                return $thought
            }
        } catch {
            Write-Cursor ("Понимание не сработало ({0}) — разбираю правилами" -f $_.Exception.Message) 'warn'
        }
    }
    return (ConvertTo-PlanByRules $text)
}

function ConvertTo-PlanByRules([string]$text) {
    $normalized = ($text -replace 'ё', 'е').Trim()
    if (-not $normalized) { return @() }

    $chunks = @($normalized -split '(?:\s+и\s+|\s*,\s*потом\s+|\s*,\s*затем\s+|\s+потом\s+|\s+затем\s+|\s*;\s*)' |
                ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $plan = @()

    foreach ($chunk in $chunks) {
        $part = $chunk.ToLower()

        switch -Regex ($part) {

            '^(стоп|хватит|остановись|отмена|прекрати)' {
                $plan += New-Step 'stop' @{ say = 'Останавливаюсь' }
                break
            }

            # --- рисование ---
            '(нарисуй|нарисовать|рисуй|изобрази)\s+(.+)$' {
                $subject = $Matches[2].Trim() -replace '^(мне|нам|пожалуйста|свой|своё|свою)\s+', ''
                $plan += New-Step 'draw' @{ what = $subject; say = ("Рисую: {0}" -f $subject) }
                break
            }
            '^(нарисуй|рисуй)$' {
                $plan += New-Step 'draw' @{ what = 'логотип'; say = 'Рисую логотип' }
                break
            }

            # --- проводник ---
            '(проводник|explorer|мои документы|папк[иу] на компьютере)' {
                $folder = ''
                if ($part -match '(?:в|открой)\s+папку\s+([^\s].*)$') { $folder = $Matches[1].Trim() }
                $find = ''
                if ($part -match '(?:найди|найти|поищи)\s+(?:папку|файл)?\s*(.+)$') { $find = $Matches[1].Trim() }
                $plan += New-Step 'explorer' @{ folder = $folder; browse = $true; find = $find
                                                say = 'Открываю Проводник' }
                break
            }

            # --- paint ---
            '(paint|пейнт|краск|рисовалк)' {
                $what = ''
                if ($part -match '(?:нарисуй|рисуй)\s+(.+)$') { $what = $Matches[1].Trim() }
                if ($what) {
                    $plan += New-Step 'paint' @{ what = $what; say = ("Открываю Paint и рисую: {0}" -f $what) }
                } else {
                    $plan += New-Step 'open' @{ target = 'paint'; say = 'Открываю Paint' }
                }
                break
            }

            # --- поиск и открытие в уже открытом окне ---
            '^(?:найди|найти|поищи)\s+(?:папку|файл|каталог)\s+(.+)$' {
                $plan += New-Step 'explorer' @{ folder = ''; browse = $true; find = $Matches[1].Trim()
                                                say = ("Ищу «{0}»" -f $Matches[1].Trim()) }
                break
            }

            '^(открой|запусти|включи|открыть)\s+(.+)$' {
                $target = $Matches[2].Trim() -replace '^(мне|нам|пожалуйста)\s+', ''
                $plan += New-Step 'open' @{ target = $target; say = ("Открываю {0}" -f $target) }
                break
            }

            # --- прокрутка ---
            '(полистай|пролистай|прокрути|скролл|листай|промотай)' {
                $direction = 'down'
                if ($part -match '(вверх|наверх|назад)') { $direction = 'up' }
                $amount = 4
                if ($part -match '(\d{1,2})') { $amount = [int]$Matches[1] }
                if ($amount -gt 25) { $amount = 25 }
                $plan += New-Step 'scroll' @{ amount = $amount; direction = $direction
                                              say = ("Листаю {0}" -f $(if ($direction -eq 'up') { 'вверх' } else { 'вниз' })) }
                break
            }

            # --- клавиатура ---
            '^(?:напиши|напечатай|введи|набери|печатай)\s+(.+)$' {
                $plan += New-Step 'type' @{ text = $Matches[1].Trim(' ', '"', '«', '»')
                                            say = 'Печатаю' }
                break
            }
            '^(?:нажми|жми)\s+(?:клавишу\s+)?(enter|ввод|esc|tab|пробел|space|f\d{1,2}|delete|удалить)$' {
                $plan += New-Step 'keys' @{ combo = $Matches[1]; say = ("Нажимаю {0}" -f $Matches[1]) }
                break
            }
            '^(?:нажми|жми|нажать)\s+((?:ctrl|alt|shift|win)\s*\+\s*\S+)$' {
                $plan += New-Step 'keys' @{ combo = ($Matches[1] -replace '\s+', '')
                                            say = ("Нажимаю {0}" -f $Matches[1]) }
                break
            }

            # --- мышь ---
            '(двойной клик|двойным кликом|открой двойным)' {
                $plan += New-Step 'click' @{ button = 'left'; count = 2; say = 'Двойной клик' }
                break
            }
            '(правой кнопкой|правый клик|контекстное меню)' {
                $plan += New-Step 'click' @{ button = 'right'; count = 1; say = 'Клик правой кнопкой' }
                break
            }
            '^(?:нажми|кликни|щелкни|щёлкни|тапни)\s+(?:на\s+|по\s+)?(?:кнопку\s+|пункт\s+|значок\s+)?(.+)$' {
                $plan += New-Step 'click_text' @{ name = $Matches[1].Trim(' ', '"', '«', '»')
                                                  say = ("Ищу «{0}» в окне" -f $Matches[1].Trim()) }
                break
            }
            '^(?:кликни|клик|нажми)$' {
                $plan += New-Step 'click' @{ button = 'left'; count = 1; say = 'Клик' }
                break
            }
            '(наведи|подведи|перемести).*(центр|середин)' {
                $plan += New-Step 'move' @{ x = 0.5; y = 0.5; say = 'Веду курсор в центр' }
                break
            }

            # --- окна ---
            '(сверни вс[её]|покажи рабочий стол)' {
                $plan += New-Step 'keys' @{ combo = 'win+d'; say = 'Сворачиваю всё' }
                break
            }
            '(сделай скриншот|сними экран|скрин)' {
                $plan += New-Step 'keys' @{ combo = 'win+shift+s'; say = 'Снимок экрана' }
                break
            }
            '^(подожди|погоди|стой)\s*(\d{1,2})?' {
                $seconds = 2
                if ($Matches[2]) { $seconds = [Math]::Min(20, [int]$Matches[2]) }
                $plan += New-Step 'wait' @{ ms = $seconds * 1000; say = ("Жду {0} с" -f $seconds) }
                break
            }

            default {
                $plan += New-Step 'unknown' @{ text = $chunk }
            }
        }
    }

    # «Нарисуй кота» без единого слова про Paint — всё равно про Paint: рисовать
    # больше негде, и просить человека сначала открыть редактор было бы
    # занудством.
    $needsCanvas = @($plan | Where-Object { $_.act -eq 'draw' }).Count -gt 0
    $hasPaint = @($plan | Where-Object { $_.act -eq 'paint' -or ($_.act -eq 'open' -and $_.target -match 'paint|пейнт|краск') }).Count -gt 0
    if ($needsCanvas -and -not $hasPaint) {
        $plan = @(New-Step 'ensure_paint' @{ say = 'Готовлю холст' }) + $plan
    }

    return $plan
}

# ------------------------------------------------------------ выполнение -----

function Invoke-Step($step) {
    Assert-Running
    if ($step.say) { Write-Cursor ([string]$step.say) }

    switch ([string]$step.act) {

        'open' {
            $file = Resolve-AppFile ([string]$step.target)
            if ($file -eq 'explorer.exe') { return Invoke-SkillExplorer -Browse:$false }
            if ($file -eq 'mspaint.exe') {
                Start-App 'mspaint.exe' | Out-Null
                $window = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 12000
                if ($window) { Set-WindowFront $window | Out-Null; Set-WindowBig $window }
                return @{ ok = $true; say = 'Paint открыт.' }
            }
            if (Start-App $file) {
                Wait-Idle 1200
                return @{ ok = $true; say = ("Открыл {0}." -f $step.target) }
            }
            return @{ ok = $false; say = ("Не смог открыть «{0}»." -f $step.target) }
        }

        'ensure_paint' {
            $window = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 400
            if ($window) { Set-WindowFront $window | Out-Null; Wait-Idle 300; return @{ ok = $true } }
            Start-App 'mspaint.exe' | Out-Null
            $window = Wait-Window 'mspaint|paint' 'MSPaintApp' 'Paint' 12000
            if (-not $window) { return @{ ok = $false; say = 'Paint не открылся.' } }
            Set-WindowFront $window | Out-Null
            Set-WindowBig $window
            Wait-Idle 900
            return @{ ok = $true }
        }

        'explorer'   { return Invoke-SkillExplorer ([string]$step.folder) -Browse:([bool]$step.browse) -find ([string]$step.find) }
        'paint'      { return Invoke-SkillPaint ([string]$step.what) }
        'draw'       { return Invoke-SkillDraw ([string]$step.what) }
        'click_text' { return Invoke-SkillClickText ([string]$step.name) }

        # ---------------------------------------------- шаги от понимания ----
        #
        # Эти шаги приходят из cursor-mind.ps1 и отличаются от старых одним:
        # цель у них УЖЕ НАЙДЕНА. Не «строка, которую надо будет как-то
        # истолковать», а точка на экране, путь на диске или конкретное окно.
        # Поэтому здесь нет ни одного разбора текста — только действие.

        'click_point' {
            # Точка найдена при составлении плана, но между планом и этим
            # мгновением прошло время: предыдущий шаг мог сдвинуть окно или
            # закрыть его. Проверка дешёвая, а клик мимо — дорогой.
            $x = [double]$step.x; $y = [double]$step.y
            $front = $null
            $handle = [CloudHdrCursor.Native]::GetForegroundWindow()
            if ($handle -ne [IntPtr]::Zero) { $front = Get-WindowInfo $handle }
            if ($front -and -not (Test-PointFree $front $x $y)) {
                Write-Cursor ("«{0}» сместилось — ищу заново" -f $step.name) 'warn'
                return Invoke-SkillClickText ([string]$step.name)
            }
            Invoke-ClickAt $x $y 'left' 1
            Wait-Idle 500
            return @{ ok = $true; say = ("Нажал «{0}»." -f $step.name) }
        }

        'open_path'  { return Invoke-SkillOpenPath ([string]$step.path) -Browse:([bool]$step.browse) }
        'open_app'   { return Invoke-SkillOpenApp ([string]$step.path) ([string]$step.name) }
        'raise_window' { return Invoke-SkillRaiseWindow ([string]$step.title) }
        'close_window' { return Invoke-SkillCloseWindow ([string]$step.title) }

        'close_app' {
            # Ищем и по заголовку, и по имени процесса. Заголовок понятнее, но
            # он меняется: у браузера это имя вкладки, а «закрой хром» про имя
            # вкладки ничего не знает. Имя процесса на такое не ведётся.
            $target = [string]$step.target
            $window = $null
            foreach ($candidate in (Get-Windows)) {
                $score = Get-NameScore $target $candidate.title
                if ($score -lt 74) {
                    $owner = Get-Process -Id $candidate.pid -ErrorAction SilentlyContinue
                    if ($owner) { $score = Get-NameScore $target $owner.ProcessName }
                }
                if ($score -ge 74) { $window = $candidate; break }
            }
            if (-not $window) { return @{ ok = $false; say = ("Окна «{0}» не нашёл." -f $target) } }
            return Invoke-SkillCloseWindow ([string]$window.title)
        }

        'find' { return Invoke-SkillFind ([string]$step.what) }

        'missing' {
            Write-Cursor ([string]$step.say) 'warn'
            return @{ ok = $false; say = [string]$step.say }
        }

        'intent' {
            # Обученная модель узнала намерение, но руками его выполнять нечем:
            # это команда агента, а не действие мышью. Честно передаём наверх.
            return @{ ok = $true; say = ("Это команда «{0}» — её выполняет сам помощник, без мыши." -f $step.intent) }
        }

        'click' {
            $count = 1
            if ($step.count) { $count = [int]$step.count }
            $button = 'left'
            if ($step.button) { $button = [string]$step.button }
            Invoke-Click $button $count
            return @{ ok = $true }
        }

        'move' {
            $screen = Get-ScreenRect
            Move-Cursor ($screen.left + $screen.width * [double]$step.x) ($screen.top + $screen.height * [double]$step.y)
            return @{ ok = $true }
        }

        'scroll' {
            $amount = 4
            if ($step.amount) { $amount = [int]$step.amount }
            Invoke-Scroll $amount ([string]$step.direction)
            return @{ ok = $true }
        }

        'type' { Send-Text ([string]$step.text); return @{ ok = $true } }
        'agent' { return Invoke-MindAgent ([string]$step.goal) }
        'keys' { Send-Chord ([string]$step.combo); return @{ ok = $true } }
        'wait' { Wait-Idle ([int]$step.ms); return @{ ok = $true } }
        'stop' { throw ("{0}|команда «стоп»" -f $Script:AbortMark) }

        'unknown' {
            Write-Cursor ("Не понял: «{0}»" -f $step.text) 'warn'
            return @{ ok = $false; say = ("Не понял «{0}». Скажите проще: «открой проводник», «нарисуй логотип», «полистай вниз»." -f $step.text) }
        }

        default { return @{ ok = $false; say = 'Неизвестный шаг.' } }
    }
}

<#
    Выполнить план целиком.

    Ответ собирается из ответов шагов, а не пишется заранее: человек должен
    узнать, что именно получилось, — «нашёл папку Загрузки» полезнее, чем
    «готово».
#>
function Invoke-Plan($plan) {
    $said = @()
    $failed = 0
    $index = 0
    $total = @($plan).Count

    foreach ($step in $plan) {
        # Счётчик пройденного нужен окну: по нему рисуется полоса выполнения.
        # Считать по строкам журнала нельзя — один шаг пишет их сколько захочет,
        # и полоса дёргалась бы вперёд-назад.
        if ($Script:CursorBoard) {
            $Script:CursorBoard['done'] = $index
            $Script:CursorBoard['total'] = $total
        }
        $index++

        $result = Invoke-Step $step
        if ($result -and $result.say) { $said += [string]$result.say }
        if ($result -and $result.ok -eq $false) { $failed++ }
        if ($Script:CursorBoard) { $Script:CursorBoard['done'] = $index }
    }

    if ($said.Count -eq 0) { $said = @('Готово.') }
    return @{ ok = ($failed -eq 0); say = ($said -join ' ') }
}

# ------------------------------------------------------------ понимание -----
#
# Подключается ПОСЛЕ всего остального, и порядок здесь имеет значение только
# для читателя: PowerShell ищет функции в момент вызова, а не в момент
# определения, поэтому cursor-mind.ps1 спокойно пользуется Get-UiItems и
# Get-Windows, объявленными выше. Место в конце файла выбрано, чтобы движок
# читался сверху вниз как «руки, потом голова», а не наоборот.
#
# Отсутствие файла не ломает ничего: ConvertTo-Plan проверяет наличие
# New-MindPlan и при его отсутствии разбирает фразу правилами.

$Script:MindFile = Join-Path (Split-Path -Parent $PSCommandPath) 'cursor-mind.ps1'
if (Test-Path -LiteralPath $Script:MindFile) {
    try { . $Script:MindFile } catch {
        Write-Warning ("Понимание не загрузилось: {0}" -f $_.Exception.Message)
    }
}
