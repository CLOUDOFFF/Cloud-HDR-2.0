'use strict';
/**
 * Cloud HDR — исполнитель действий Windows.
 *
 * Принцип: наружу торчат только интенты с фиксированным набором целей.
 * Ни одна строка из браузера не попадает в командную строку без валидации,
 * поэтому shell-инъекция невозможна — везде, где можно, spawn вызывается
 * без shell и с массивом аргументов.
 */

const { spawn, execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const { APPS, FOLDERS, expandEnv, resolveExecutable, findAppKey, findFolderKey } = require('./apps');
const { scan } = require('./scan');
const steam = require('./steam');

class ExecError extends Error {
  constructor(message, code = 400) {
    super(message);
    this.code = code;
  }
}

/* ------------------------------------------------------------------ низкий уровень */

/** Отсоединённый запуск процесса: агент не ждёт и не держит приложение. */
function detach(command, args = [], options = {}) {
  const child = spawn(command, args, {
    detached: true,
    stdio: 'ignore',
    windowsHide: options.hidden !== false,
    ...options
  });
  child.unref();
  return child.pid || null;
}

/** Открытие URI через оболочку Windows (протокол-хендлер или ссылка). */
function openUri(uri, config) {
  const scheme = String(uri).split(':')[0].toLowerCase();
  const allowed = config.allowedUriSchemes || [];
  if (!/^[a-z][a-z0-9+.-]{0,20}$/.test(scheme) || !allowed.includes(scheme)) {
    throw new ExecError(`Протокол "${scheme}:" не разрешён`, 403);
  }
  if (/[\r\n\0]/.test(uri)) throw new ExecError('Некорректный URI', 400);
  // start вызывается через cmd, но URI передаётся отдельным аргументом,
  // а схема проверена по белому списку выше.
  return detach('cmd.exe', ['/c', 'start', '', uri]);
}

/** Один PowerShell-вызов с заранее заданным (не пользовательским) скриптом. */
function powershell(script) {
  return detach('powershell.exe', ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script]);
}

/**
 * PowerShell с ожиданием результата.
 *
 * Нужен там, где важен не запуск, а исход: запись экрана включается горячей
 * клавишей, которая ничего не возвращает, и «выполнено» без проверки было бы
 * неправдой. Агент однопоточный, поэтому вызов держит его на время ожидания —
 * так же ведёт себя поиск файлов, и та же плата здесь оправдана.
 */
function powershellSync(script, timeout = 12000) {
  return execFileSync('powershell.exe',
    ['-NoProfile', '-NonInteractive', '-Command', script],
    { encoding: 'utf8', timeout, windowsHide: true });
}

/** Запуск игры из библиотеки Steam — через appid, а не по exe. */
function launchSteamGame(game, config) {
  const uri = steam.runUri(game);
  openUri(uri, config);
  return { ok: true, app: `steam:${game.appid}`, title: game.name, method: 'steam', detail: uri };
}

/* ------------------------------------------------------------------ пути и папки */

/** Приводит путь к каноничному виду и проверяет вхождение в разрешённые корни. */
function safeFolderPath(rawPath, config) {
  if (!rawPath || typeof rawPath !== 'string') throw new ExecError('Не указан путь к папке');
  if (/[\r\n\0*?"<>|]/.test(rawPath)) throw new ExecError('Недопустимые символы в пути');

  let candidate = expandEnv(rawPath.trim().replace(/^["']|["']$/g, '')).replace(/\//g, '\\');
  if (candidate.startsWith('\\\\')) throw new ExecError('Сетевые пути (UNC) запрещены', 403);

  // относительный путь трактуем как папку внутри профиля пользователя
  if (!/^[a-zA-Z]:\\/.test(candidate)) {
    candidate = path.join(process.env.USERPROFILE || 'C:\\Users\\Public', candidate);
  }
  const resolved = path.resolve(candidate);

  const roots = (config.allowedFolderRoots || []).map((root) => path.resolve(expandEnv(root)));
  const inside = roots.some((root) => {
    const normalizedRoot = root.endsWith('\\') ? root : root + '\\';
    return resolved === root.replace(/\\$/, '') || resolved.toLowerCase().startsWith(normalizedRoot.toLowerCase());
  });
  if (!inside) throw new ExecError('Папка вне разрешённых каталогов. Добавьте её в allowedFolderRoots.', 403);

  // path.join('C:', 'Program Files') дал бы относительный путь — берём переменные окружения
  const forbidden = [process.env.WINDIR, process.env.ProgramFiles, process.env['ProgramFiles(x86)']].filter(Boolean);
  if (forbidden.some((dir) => resolved.toLowerCase().startsWith(path.resolve(dir).toLowerCase()))) {
    throw new ExecError('Системные каталоги открывать запрещено', 403);
  }
  if (!fs.existsSync(resolved)) throw new ExecError(`Папка не найдена: ${resolved}`, 404);
  if (!fs.statSync(resolved).isDirectory()) throw new ExecError('Указанный путь — не папка', 400);
  return resolved;
}

/* ------------------------------------------------------------------ интенты */

function openApp(target, config, via = '') {
  if (!config.permissions.allowApps) throw new ExecError('Запуск приложений отключён в config.json', 403);

  // «Запусти В СТИМЕ кс 2» — Steam назван прямо, и искать надо только среди игр.
  // Без этой ветки запрос ушёл бы в общий разбор, где по слову «кс» может
  // найтись что-то посторонее: пользователь получил бы не то, что просил,
  // вместо внятного «такой игры в библиотеке нет».
  if (via === 'steam') {
    const found = steam.find(target);
    if (!found.best) {
      const hint = found.suggestions.length ? ` В библиотеке есть: ${found.suggestions.join(', ')}.` : '';
      throw new ExecError(`Не нашёл «${target}» в библиотеке Steam.${hint}`, 404);
    }
    return launchSteamGame(found.best, config);
  }

  const key = findAppKey(target);

  if (!key) {
    // Игра из библиотеки Steam проверяется до отказа: запуск через steam://
    // единственный, при котором работают античит, облачные сохранения и оверлей.
    const game = steam.find(target);
    if (game.best) return launchSteamGame(game.best, config);

    const hint = game.suggestions.length ? ` В библиотеке Steam есть: ${game.suggestions.join(', ')}.` : '';
    throw new ExecError(`Приложение "${target}" не входит в белый список.${hint}`, 404);
  }

  const app = APPS[key];
  const exe = resolveExecutable(app);

  if (exe) {
    const pid = detach(exe, app.args || [], { cwd: path.dirname(exe), hidden: false });
    return { ok: true, app: key, title: app.title, method: 'exe', detail: exe, pid };
  }
  if (app.uri) {
    openUri(app.uri, config);
    return { ok: true, app: key, title: app.title, method: 'uri', detail: app.uri };
  }
  if (app.shell) {
    detach('cmd.exe', ['/c', 'start', '', app.shell]);
    return { ok: true, app: key, title: app.title, method: 'shell', detail: app.shell };
  }
  throw new ExecError(`${app.title} не найден на этом компьютере`, 404);
}

function openFolder(target, config) {
  if (!config.permissions.allowFolders) throw new ExecError('Открытие папок отключено в config.json', 403);

  const key = findFolderKey(target);
  const raw = key ? FOLDERS[key].path : target;
  const resolved = safeFolderPath(raw, config);

  detach('explorer.exe', [resolved], { hidden: false });
  return { ok: true, method: 'explorer', title: key ? FOLDERS[key].title : path.basename(resolved), detail: resolved };
}

function openUrl(target, config) {
  if (!config.permissions.allowWeb) throw new ExecError('Работа с браузером отключена в config.json', 403);
  let url = String(target || '').trim();
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url.replace(/^\/+/, '');
  try {
    // eslint-disable-next-line no-new
    new URL(url);
  } catch {
    throw new ExecError('Некорректный адрес сайта', 400);
  }
  openUri(url, config);
  return { ok: true, method: 'browser', title: url.replace(/^https?:\/\//, ''), detail: url };
}

function webSearch(query, config) {
  if (!config.permissions.allowWeb) throw new ExecError('Работа с браузером отключена в config.json', 403);
  const text = String(query || '').trim();
  if (!text) throw new ExecError('Пустой поисковый запрос');
  if (text.length > 300) throw new ExecError('Слишком длинный запрос');
  const url = (config.searchEngine || 'https://www.google.com/search?q=') + encodeURIComponent(text);
  openUri(url, config);
  return { ok: true, method: 'search', title: text, detail: url };
}

/**
 * Ищет среди запущенного процесс, похожий на названное человеком.
 *
 * Нужна потому, что белый список описывает далеко не всё, что стоит на
 * компьютере. «Закрой капкат» не должно упираться в «нет в белом списке»:
 * программа открыта, человек на неё смотрит и просит её закрыть — отказ здесь
 * выглядит не осторожностью, а неисправностью.
 *
 * Сравнение идёт и по имени процесса, и по заголовку окна: у половины программ
 * имя файла не совпадает с тем, как их называют люди.
 */
const PROTECTED_IMAGES = new Set([
  'explorer', 'csrss', 'winlogon', 'wininit', 'services', 'lsass', 'smss',
  'svchost', 'dwm', 'system', 'fontdrvhost', 'sihost', 'ctfmon', 'audiodg',
  'runtimebroker', 'shellexperiencehost', 'startmenuexperiencehost',
  'searchhost', 'searchapp', 'textinputhost', 'registry'
]);

function findRunningApp(query) {
  const needle = String(query || '').toLowerCase().replace(/[^\p{L}\p{Nd}]/gu, '');
  if (needle.length < 2) return null;

  let listing;
  try {
    // Get-Process — единственный доступный способ увидеть заголовки окон:
    // tasklist их не отдаёт, а без заголовка «телега» не найдёт Telegram.exe.
    listing = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      "Get-Process | Where-Object { $_.Id -ne $PID } | " +
      "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json -Compress"],
      { encoding: 'utf8', timeout: 8000, windowsHide: true });
  } catch {
    return null;
  }

  let processes;
  try { processes = JSON.parse(listing); } catch { return null; }
  if (!Array.isArray(processes)) processes = [processes];

  let best = null;
  for (const item of processes) {
    const image = String(item.ProcessName || '');
    if (!image || PROTECTED_IMAGES.has(image.toLowerCase())) continue;

    // Окно Cloud HDR исключается отдельно: помощник живёт в окне браузера, и
    // «закрой хром» иначе закрывало бы того, кто эту команду выполняет.
    const windowTitle = String(item.MainWindowTitle || '');
    if (windowTitle.startsWith('Cloud HDR')) continue;

    const candidates = [image];
    if (windowTitle) {
      candidates.push(windowTitle);
      const tail = windowTitle.split(/\s[-—|]\s/).pop();
      if (tail) candidates.push(tail.trim());
    }

    for (const candidate of candidates) {
      const name = candidate.toLowerCase().replace(/[^\p{L}\p{Nd}]/gu, '');
      if (name.length < 2) continue;
      let score = 0;
      if (name === needle) score = 100;
      else if (needle.length >= 3 && name.startsWith(needle)) score = 84;
      else if (needle.length >= 4 && name.includes(needle)) score = 70;
      else if (name.length >= 4 && needle.includes(name)) score = 66;
      // Программа с окном на экране весомее фоновой службы с похожим именем.
      if (score > 0 && windowTitle) score += 8;
      if (score > (best ? best.score : 0)) best = { pid: item.Id, title: windowTitle || image, image, score };
    }
  }

  return best && best.score >= 60 ? best : null;
}

function closeApp(target, config) {
  if (!config.permissions.allowClose) throw new ExecError('Завершение процессов отключено в config.json', 403);

  // Подтверждения здесь нет и не должно быть. Раньше агент отвечал отказом
  // «требуется подтверждение пользователя», а интерфейс поверх этого показывал
  // ещё и окно «вы уверены?»: на просьбу закрыть программу помощник дважды
  // переспрашивал, хотя просьба и была подтверждением. Закрытие окна обратимо —
  // программа открывается заново той же фразой.
  const key = findAppKey(target);
  if (key) {
    const app = APPS[key];
    if (!app.image) throw new ExecError(`${app.title} закрывать нельзя — это часть Windows`, 403);
    detach('taskkill.exe', ['/IM', app.image, '/T', '/F']);
    return { ok: true, app: key, method: 'taskkill', title: app.title, detail: app.image };
  }

  const running = findRunningApp(target);
  if (!running) {
    throw new ExecError(`Не нашёл открытую программу «${target}» — возможно, она уже закрыта`, 404);
  }
  detach('taskkill.exe', ['/PID', String(running.pid), '/T', '/F']);
  return { ok: true, app: running.image, method: 'taskkill', title: running.title,
    detail: `${running.image}.exe · найдено среди запущенного` };
}

const VOLUME_KEYS = { volume_up: 175, volume_down: 174, volume_mute: 173 };

/*
 * Громкость.
 *
 * Клавиши мультимедиа умеют ровно одно: подвинуть уровень на один системный шаг.
 * Ими нельзя ни узнать текущую громкость, ни поставить нужную — «поставь на 30»
 * превращалось в несколько нажатий вслепую, и результат зависел от того, что
 * было до команды. Точный уровень даёт Core Audio (IAudioEndpointVolume).
 *
 * Объявлены только нужные методы интерфейса, пропущенные заменены заглушками:
 * порядок в таблице виртуальных функций обязан совпасть с оригиналом.
 */
const AUDIO_CS = `
using System; using System.Runtime.InteropServices;
namespace CH {
 [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IAEV { int a(IntPtr p); int b(IntPtr p); int c(out uint n);
  int SetMasterVolumeLevel(float v, ref Guid g); int SetMasterVolumeLevelScalar(float v, ref Guid g);
  int GetMasterVolumeLevel(out float v); int GetMasterVolumeLevelScalar(out float v);
  int d(uint n, float v, ref Guid g); int e(uint n, float v, ref Guid g);
  int f(uint n, out float v); int g2(uint n, out float v);
  int SetMute(bool m, ref Guid g); int GetMute(out bool m); }
 [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IMMD { int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
 [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IMMDE { int h(int f, int m, IntPtr d); int GetDefaultAudioEndpoint(int f, int r, out IMMD d); }
 [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class DE { }
 public static class A {
  static IAEV E() { IMMDE e = (IMMDE)(new DE()); IMMD d; e.GetDefaultAudioEndpoint(0,1,out d);
   Guid i = typeof(IAEV).GUID; object o; d.Activate(ref i, 23, IntPtr.Zero, out o); return (IAEV)o; }
  public static float Get(){ float v; E().GetMasterVolumeLevelScalar(out v); return v; }
  public static void Set(float v){ Guid g = Guid.Empty; E().SetMasterVolumeLevelScalar(v, ref g); }
  public static bool GetMute(){ bool m; E().GetMute(out m); return m; }
  public static void SetMute(bool m){ Guid g = Guid.Empty; E().SetMute(m, ref g); } } }`;

function audioScript(body) {
  return `Add-Type -TypeDefinition @'\n${AUDIO_CS}\n'@\n${body}`;
}

function volume(intent, config, payload = {}) {
  if (!config.permissions.allowSystem) throw new ExecError('Системные действия отключены в config.json', 403);

  if (intent === 'volume_set') {
    const level = Number(payload.level);
    if (!Number.isFinite(level)) {
      throw new ExecError('Не понял, какую громкость поставить. Например: «поставь громкость на 30».', 400);
    }
    const target = Math.min(100, Math.max(0, Math.round(level)));
    const out = powershellSync(audioScript(
      `$was=[int][Math]::Round([CH.A]::Get()*100); [CH.A]::Set(${target}/100.0); ` +
      `if (${target} -gt 0 -and [CH.A]::GetMute()) { [CH.A]::SetMute($false) }; Write-Output $was`), 15000);
    const was = parseInt(String(out).trim(), 10);
    return { ok: true, method: 'audio', title: `Громкость ${target}%`, level: target,
      detail: Number.isFinite(was) ? `было ${was}% → стало ${target}%` : `${target}%` };
  }

  if (intent === 'volume_mute') {
    const out = powershellSync(audioScript(
      '$m = -not [CH.A]::GetMute(); [CH.A]::SetMute($m); Write-Output $m'), 15000);
    const muted = String(out).trim().toLowerCase().startsWith('t');
    return { ok: true, method: 'audio', title: muted ? 'Звук выключен' : 'Звук включён',
      detail: muted ? 'без звука' : 'звук вернулся' };
  }

  // Шаг задаётся В ПРОЦЕНТАХ: «сделай громче на 20» поднимет на 20 пунктов,
  // а не на 20 нажатий клавиши, как было раньше.
  const step = Math.min(100, Math.max(1, Number(payload.amount) || 10));
  const sign = intent === 'volume_up' ? '+' : '-';
  const out = powershellSync(audioScript(
    `$was=[int][Math]::Round([CH.A]::Get()*100); $t=[Math]::Min([Math]::Max($was ${sign} ${step},0),100); ` +
    `[CH.A]::Set($t/100.0); if ('${intent}' -eq 'volume_up' -and [CH.A]::GetMute()) { [CH.A]::SetMute($false) }; ` +
    `Write-Output ("$was $t")`), 15000);
  const [was, now] = String(out).trim().split(/\s+/).map(Number);
  const label = intent === 'volume_up' ? 'Громкость увеличена' : 'Громкость уменьшена';
  return { ok: true, method: 'audio', title: label, level: now,
    detail: Number.isFinite(was) ? `${was}% → ${now}%` : `шаг ${step}%` };
}

function screenshot(config) {
  if (!config.permissions.allowSystem) throw new ExecError('Системные действия отключены в config.json', 403);
  const dir = expandEnv(config.screenshotDir || '%USERPROFILE%\\Pictures\\CloudHDR');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `cloudhdr-${new Date().toISOString().replace(/[:.]/g, '-')}.png`);

  const script = [
    'Add-Type -AssemblyName System.Windows.Forms,System.Drawing;',
    '$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;',
    '$img = New-Object System.Drawing.Bitmap $b.Width, $b.Height;',
    '$g = [System.Drawing.Graphics]::FromImage($img);',
    '$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $img.Size);',
    `$img.Save('${file.replace(/'/g, "''")}', [System.Drawing.Imaging.ImageFormat]::Png);`,
    '$g.Dispose(); $img.Dispose();'
  ].join(' ');

  powershell(script);
  return { ok: true, method: 'screenshot', title: 'Скриншот сохранён', detail: file };
}

function lockWorkstation(config) {
  if (!config.permissions.allowSystem) throw new ExecError('Системные действия отключены в config.json', 403);
  detach('rundll32.exe', ['user32.dll,LockWorkStation']);
  return { ok: true, method: 'lock', title: 'Рабочая станция заблокирована', detail: 'user32.dll,LockWorkStation' };
}

/* ------------------------------------------------------------- поиск файлов */

/**
 * Пути, найденные агентом в этой сессии. Запустить через open_path можно
 * только то, что здесь есть: произвольная строка из браузера не выполняется.
 */
const foundPaths = new Set();

function scanFiles(payload, config) {
  if (!config.permissions.allowScan) throw new ExecError('Поиск файлов отключён в config.json', 403);
  const result = scan(payload, config);
  result.results.forEach((item) => foundPaths.add(String(item.path).toLowerCase()));
  return result;
}

function openPath(payload, config) {
  if (!config.permissions.allowScan) throw new ExecError('Поиск файлов отключён в config.json', 403);
  if (!payload.confirm) throw new ExecError('Запуск найденного файла требует подтверждения', 428);

  const target = String(payload.target || '').trim();
  if (!target) throw new ExecError('Не указан путь');
  if (!foundPaths.has(target.toLowerCase())) {
    throw new ExecError('Этот путь не найден агентом в текущей сессии — запуск отклонён. Сначала выполните поиск.', 403);
  }
  if (!fs.existsSync(target)) throw new ExecError('Файл больше не существует', 404);

  const extension = path.extname(target).toLowerCase();
  if (!['.exe', '.lnk', '.url'].includes(extension)) throw new ExecError(`Файлы «${extension}» агент не запускает`, 403);

  detach(target, [], { cwd: path.dirname(target), hidden: false });
  return { ok: true, method: 'launch', title: path.basename(target, extension), detail: target };
}

function revealPath(payload, config) {
  if (!config.permissions.allowScan) throw new ExecError('Поиск файлов отключён в config.json', 403);
  const target = String(payload.target || '').trim();
  if (!target) throw new ExecError('Не указан путь');
  if (!foundPaths.has(target.toLowerCase())) throw new ExecError('Этот путь не найден агентом в текущей сессии.', 403);
  if (!fs.existsSync(target)) throw new ExecError('Путь больше не существует', 404);

  const args = fs.statSync(target).isDirectory() ? [target] : ['/select,', target];
  detach('explorer.exe', args, { hidden: false });
  return { ok: true, method: 'explorer', title: 'Показано в Проводнике', detail: target };
}

/* ------------------------------------------------------------- запись экрана */
/*
 * Пишет экран встроенный в Windows Game Bar — тот же, что по Win+Alt+R. Выбран
 * он не от бедности: ffmpeg пришлось бы устанавливать, а Game Bar есть в системе,
 * умеет звук и не требует прав администратора. Файл ложится в «Видео\Captures».
 *
 * Тонкость, из-за которой здесь код, а не одна строка: у Game Bar нет ни
 * командной строки, ни COM-интерфейса — только горячая клавиша. Клавишу Win
 * нельзя послать через SendKeys, которым здесь переключается громкость:
 * WScript.Shell её не знает. Поэтому нажатие идёт через keybd_event.
 *
 * И главное: клавиша ничего не возвращает. Если запись выключена в параметрах
 * или окно защищено от захвата, Game Bar молча ничего не сделает, а агент
 * отчитался бы об успехе. Поэтому результат проверяется по появлению файла.
 */

const GAMEBAR_CHORD = [
  // Атрибут полным именем и без -UsingNamespace: Add-Type подставляет
  // System.Runtime.InteropServices сам, а вторая директива using — это
  // предупреждение, которое компилятор считает ошибкой.
  '$m = \'[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, System.UIntPtr dwExtraInfo);\'',
  'Add-Type -Namespace CH -Name K -MemberDefinition $m',
  '$z = [UIntPtr]::Zero',
  '[CH.K]::keybd_event([byte]0x5B,0,0,$z); [CH.K]::keybd_event([byte]0x12,0,0,$z); [CH.K]::keybd_event([byte]0x52,0,0,$z)',
  'Start-Sleep -Milliseconds 70',
  '[CH.K]::keybd_event([byte]0x52,0,2,$z); [CH.K]::keybd_event([byte]0x12,0,2,$z); [CH.K]::keybd_event([byte]0x5B,0,2,$z)'
].join('; ');

let recording = { active: false, file: null, startedAt: null };

function captureDir() {
  return path.join(process.env.USERPROFILE || 'C:\\', 'Videos', 'Captures');
}

function captureFiles(dir) {
  try {
    return fs.readdirSync(dir)
      .filter((name) => name.toLowerCase().endsWith('.mp4'))
      .map((name) => ({ name, ...fs.statSync(path.join(dir, name)) }))
      .sort((a, b) => b.mtimeMs - a.mtimeMs);
  } catch {
    return [];
  }
}

const sleep = (ms) => {
  // Ожидание в синхронном исполнителе: Atomics.wait блокирует поток честно,
  // без цикла на процессоре.
  const shared = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(shared, 0, 0, ms);
};

function recordStart(config) {
  if (!config.permissions.allowSystem) throw new ExecError('Системные действия отключены в config.json', 403);

  const dir = captureDir();
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const before = new Set(captureFiles(dir).map((file) => file.name));

  powershellSync(GAMEBAR_CHORD, 10000);

  // Game Bar создаёт файл сразу, но не мгновенно: ему надо поднять оверлей.
  // Пятнадцать секунд — это время ПЕРВОГО вызова, когда GameBar.exe ещё не
  // запущен; когда он уже поднят, цикл выходит через полсекунды.
  let appeared = null;
  for (let i = 0; i < 75 && !appeared; i++) {
    sleep(200);
    appeared = captureFiles(dir).find((file) => !before.has(file.name)) || null;
  }

  if (!appeared) {
    throw new ExecError('Game Bar не начал запись. Обычно причина одна из двух: запись выключена в ' +
      '«Параметры → Игры → Записи», либо активное окно защищено от захвата. ' +
      'Проверьте, что Win+Alt+R работает вручную.', 409);
  }

  recording = { active: true, file: path.join(dir, appeared.name), startedAt: Date.now() };
  return { ok: true, method: 'gamebar', title: 'Запись экрана идёт', detail: recording.file, file: appeared.name, folder: dir };
}

function recordStop(config) {
  if (!config.permissions.allowSystem) throw new ExecError('Системные действия отключены в config.json', 403);

  const dir = captureDir();
  const tracked = recording.file;
  powershellSync(GAMEBAR_CHORD, 10000);

  // На остановке Game Bar досводит файл: размер растёт ещё секунду-две. Ждём,
  // пока он перестанет меняться, — только тогда файл действительно готов.
  let target = null;
  let previous = -1;
  let stable = 0;
  for (let i = 0; i < 40; i++) {
    sleep(200);
    const files = captureFiles(dir);
    if (!files.length) continue;
    target = (tracked && files.find((file) => path.join(dir, file.name) === tracked)) || files[0];
    if (target.size > 0 && target.size === previous) {
      if (++stable >= 3) break;
    } else {
      stable = 0;
    }
    previous = target.size;
  }

  const seconds = recording.active && recording.startedAt
    ? Math.round((Date.now() - recording.startedAt) / 1000) : null;
  recording = { active: false, file: null, startedAt: null };

  if (!target) {
    // Записи не было — но и ошибки нет: пользователь мог сказать «останови
    // запись», когда ничего не писалось. Сообщаем как есть.
    return { ok: true, method: 'gamebar', title: 'Записи не было', detail: 'файл записи не найден', folder: dir };
  }

  const mb = (target.size / (1024 * 1024)).toFixed(1);
  const detail = seconds === null ? `${target.name} · ${mb} МБ` : `${target.name} · ${mb} МБ · ${seconds} с`;
  return { ok: true, method: 'gamebar', title: 'Запись сохранена', detail,
    file: target.name, path: path.join(dir, target.name), folder: dir, bytes: target.size };
}

/* ------------------------------------------------------------------ диспетчер */

const INTENTS = new Set([
  'open_app', 'open_folder', 'open_url', 'web_search', 'close_app',
  'volume_up', 'volume_down', 'volume_mute', 'volume_set', 'screenshot', 'lock',
  'record_screen', 'record_stop',
  'scan_files', 'open_path', 'reveal_path'
]);

/**
 * Единая точка входа. Повторно валидирует всё, что прислал клиент:
 * разбор фразы в браузере — только для UX, доверять ему нельзя.
 */
function execute(payload, config) {
  const intent = String(payload.intent || '').trim();
  if (!INTENTS.has(intent)) throw new ExecError(`Неизвестное действие: ${intent || '—'}`, 400);

  const target = payload.target != null ? String(payload.target).slice(0, 512) : '';

  switch (intent) {
    case 'open_app': return openApp(target, config, String(payload.via || '').trim());
    case 'open_folder': return openFolder(target, config);
    case 'open_url': return openUrl(target, config);
    case 'web_search': return webSearch(payload.query || target, config);
    // Отдельной проверки confirm здесь нет намеренно: сама произнесённая
    // команда и есть подтверждение, см. комментарий у closeApp().
    case 'close_app': return closeApp(target, config);
    case 'volume_up':
    case 'volume_down':
    case 'volume_mute':
    case 'volume_set': return volume(intent, config, payload);
    case 'screenshot': return screenshot(config);
    case 'record_screen': return recordStart(config);
    case 'record_stop': return recordStop(config);
    case 'lock': return lockWorkstation(config);
    case 'scan_files': return scanFiles(payload, config);
    case 'open_path': return openPath({ ...payload, target }, config);
    case 'reveal_path': return revealPath({ ...payload, target }, config);
    default: throw new ExecError('Действие не поддерживается', 400);
  }
}

module.exports = { execute, ExecError, INTENTS, safeFolderPath };
