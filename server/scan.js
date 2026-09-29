'use strict';
/**
 * Cloud HDR — поиск файлов и игр на компьютере (реализация для Node-бэкенда).
 *
 * Полный аналог блока «сканирование файлов» из agent.ps1: те же источники,
 * тот же порядок и те же ограничения. Дорогой обход диска включается только
 * тогда, когда дешёвые реестры ничего убедительного не дали.
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

/* ------------------------------------------------------------------ утилиты */

const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u',
  ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya'
};

/** Сравнимая форма: только буквы и цифры, нижний регистр. */
function plain(value) {
  return String(value || '').toLowerCase().replace(/ё/g, 'е').replace(/[^\p{L}\p{Nd}]/gu, '');
}

function translit(value) {
  return String(value || '').toLowerCase().split('').map((char) => (char in TRANSLIT ? TRANSLIT[char] : char)).join('');
}

function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length || !b.length) return Math.max(a.length, b.length);
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = row;
  }
  return prev[b.length];
}

/** Русские названия игр → имена папок на диске. */
const GAME_ALIASES = [
  { name: 'Among Us', words: ['амонг ас', 'амонгас', 'амонг ус', 'амонг', 'among us', 'amongus'] },
  { name: 'Minecraft', words: ['майнкрафт', 'майнкравт', 'майн', 'minecraft'] },
  { name: 'Roblox', words: ['роблокс', 'рублокс', 'roblox'] },
  { name: 'Counter-Strike', words: ['контра', 'кс го', 'ксго', 'кс 2', 'кс2', 'counter strike', 'csgo', 'cs2'] },
  { name: 'Grand Theft Auto', words: ['гта', 'гта 5', 'гта5', 'gta', 'grand theft auto'] },
  { name: 'Fortnite', words: ['фортнайт', 'фортнит', 'fortnite'] },
  { name: 'Valorant', words: ['валорант', 'valorant'] },
  { name: 'Dota', words: ['дота', 'дота 2', 'dota'] },
  { name: 'Terraria', words: ['террария', 'terraria'] },
  { name: 'Stardew Valley', words: ['стардью', 'stardew'] },
  { name: 'Genshin Impact', words: ['геншин', 'genshin'] },
  { name: 'Brawl Stars', words: ['бравл старс', 'бравл', 'brawl stars'] },
  { name: 'Cyberpunk', words: ['киберпанк', 'cyberpunk'] },
  { name: 'The Witcher', words: ['ведьмак', 'witcher'] },
  { name: 'PUBG', words: ['пабг', 'пубг', 'pubg'] },
  { name: 'Apex Legends', words: ['апекс', 'apex legends'] },
  { name: 'Rust', words: ['раст', 'rust'] },
  { name: 'Subnautica', words: ['субнавтика', 'subnautica'] },
  { name: 'Fall Guys', words: ['фолл гайс', 'fall guys'] },
  { name: 'Phasmophobia', words: ['фазмофобия', 'phasmophobia'] }
];

function resolveGameAlias(query) {
  const needle = plain(query);
  if (!needle) return null;
  for (const game of GAME_ALIASES) {
    for (const word of game.words) {
      const key = plain(word);
      if (key && (needle === key || needle.includes(key) || key.includes(needle))) return game.name;
    }
  }
  return null;
}

function matchScore(name, needles) {
  const value = plain(name);
  if (!value) return 0;
  let best = 0;
  for (const needle of needles) {
    if (!needle) continue;
    let score = 0;
    if (value === needle) score = 100;
    else if (value.startsWith(needle)) score = 88 - Math.min(value.length - needle.length, 22);
    else if (value.includes(needle) && needle.length >= 3) score = 74;
    else if (needle.length >= 4 && value.length <= 40) {
      const distance = levenshtein(needle, value);
      const allowed = Math.max(1, Math.floor(needle.length / 4));
      if (distance <= allowed) score = 66 - distance * 6;
    }
    if (score > best) best = score;
  }
  return best;
}

/* ------------------------------------------------------------- источники --- */

/**
 * Служебные .exe рядом с игрой. Без этого фильтра «самый большой файл» в папке
 * Unity-игры — UnityCrashHandler32.exe, и «Запустить» открывало бы обработчик
 * сбоев вместо самой игры.
 */
const EXE_JUNK = /^(unitycrashhandler|unins|uninstall|setup|installer|vcredist|vc_redist|dxsetup|dxwebsetup|crashpad|crashreport|notification_helper|dotnetfx|oalinst|directx|redist|helper|updater|launcherpatcher)/i;

/**
 * Главный исполняемый файл каталога.
 * Приоритет: имя совпадает с именем папки → просто не служебный → самый крупный.
 */
function mainExe(dir, depth = 0) {
  if (!dir || !fs.existsSync(dir)) return null;
  const found = [];

  const walk = (current, level) => {
    let entries;
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch { return; }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isFile() && entry.name.toLowerCase().endsWith('.exe')) {
        let size = 0;
        try { size = fs.statSync(full).size; } catch { /* файл исчез между readdir и stat */ }
        found.push({ path: full, size, base: path.basename(entry.name, path.extname(entry.name)) });
      } else if (entry.isDirectory() && level < depth) {
        walk(full, level + 1);
      }
    }
  };
  walk(dir, 0);
  if (!found.length) return null;

  let pool = found.filter((item) => !EXE_JUNK.test(item.base));
  if (!pool.length) pool = found;

  const folderKey = plain(path.basename(dir));
  if (folderKey) {
    const named = pool.filter((item) => {
      const key = plain(item.base);
      return key && (key === folderKey || key.startsWith(folderKey) || folderKey.startsWith(key));
    });
    if (named.length) pool = named;
  }

  pool.sort((a, b) => b.size - a.size);
  return pool[0].path;
}

function steamGames() {
  const result = [];
  const roots = new Set();

  for (const key of ['HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam', 'HKLM\\SOFTWARE\\Valve\\Steam', 'HKCU\\SOFTWARE\\Valve\\Steam']) {
    try {
      const output = execFileSync('reg', ['query', key, '/v', 'InstallPath'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
      const match = /InstallPath\s+REG_SZ\s+(.+)/i.exec(output);
      if (match && fs.existsSync(match[1].trim())) roots.add(match[1].trim());
    } catch { /* Steam не установлен — не ошибка */ }
  }
  for (const guess of ['C:\\Program Files (x86)\\Steam', 'D:\\Steam', 'D:\\SteamLibrary', 'E:\\SteamLibrary']) {
    if (fs.existsSync(guess)) roots.add(guess);
  }

  const libraries = new Set(roots);
  for (const root of roots) {
    const vdf = path.join(root, 'steamapps', 'libraryfolders.vdf');
    if (!fs.existsSync(vdf)) continue;
    try {
      const text = fs.readFileSync(vdf, 'utf8');
      for (const match of text.matchAll(/"path"\s*"([^"]+)"/g)) {
        const library = match[1].replace(/\\\\/g, '\\');
        if (fs.existsSync(library)) libraries.add(library);
      }
    } catch { /* повреждённый vdf */ }
  }

  for (const library of libraries) {
    const common = path.join(library, 'steamapps', 'common');
    if (!fs.existsSync(common)) continue;
    let entries;
    try { entries = fs.readdirSync(common, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const folder = path.join(common, entry.name);
      const exe = mainExe(folder, 1);
      result.push({ name: entry.name, path: exe || folder, folder, kind: exe ? 'exe' : 'folder', source: 'Steam' });
    }
  }
  return result;
}

function epicGames() {
  const result = [];
  const manifest = path.join(process.env.ProgramData || 'C:\\ProgramData', 'Epic', 'UnrealEngineLauncher', 'LauncherInstalled.dat');
  if (!fs.existsSync(manifest)) return result;
  try {
    const data = JSON.parse(fs.readFileSync(manifest, 'utf8'));
    for (const entry of data.InstallationList || []) {
      if (!entry.InstallLocation || !fs.existsSync(entry.InstallLocation)) continue;
      const exe = mainExe(entry.InstallLocation, 2);
      result.push({
        name: entry.AppName || path.basename(entry.InstallLocation),
        path: exe || entry.InstallLocation,
        folder: entry.InstallLocation,
        kind: exe ? 'exe' : 'folder',
        source: 'Epic Games'
      });
    }
  } catch { /* формат изменился — просто пропускаем источник */ }
  return result;
}

function registryPrograms() {
  const result = [];
  const hives = [
    'HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall',
    'HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall',
    'HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
  ];
  for (const hive of hives) {
    let output;
    try {
      output = execFileSync('reg', ['query', hive, '/s', '/v', 'DisplayName'], {
        encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 12 * 1024 * 1024
      });
    } catch { continue; }

    for (const block of output.split(/\r?\n\r?\n/)) {
      const name = /DisplayName\s+REG_SZ\s+(.+)/i.exec(block);
      if (!name) continue;
      const keyPath = block.split(/\r?\n/)[0].trim();
      let location = '';
      try {
        const info = execFileSync('reg', ['query', keyPath, '/v', 'InstallLocation'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
        const match = /InstallLocation\s+REG_SZ\s+(.+)/i.exec(info);
        if (match) location = match[1].trim().replace(/^"|"$/g, '').replace(/\\+$/, '');
      } catch { /* ключа нет */ }

      const exe = location && fs.existsSync(location) ? mainExe(location, 0) : null;
      result.push({
        name: name[1].trim(),
        path: exe || location,
        folder: location,
        kind: exe ? 'exe' : location ? 'folder' : 'entry',
        source: 'Установленные программы'
      });
    }
  }
  return result;
}

/* ---------------------------------------------------------- обход диска ---- */

const SKIP = /\\(Windows|\$Recycle\.Bin|System Volume Information|WinSxS|DriverStore|node_modules|\.git|\.cache|MSOCache|Recovery|Config\.Msi|PerfLogs)(\\|$)/i;
const HUNTED = new Set(['.exe', '.lnk', '.url', '.jar']);

function searchDisk(roots, needles, budgetMs, maxDepth, maxResults) {
  const items = [];
  const started = Date.now();
  let visited = 0;
  let truncated = false;

  const stack = roots.filter((root) => root && fs.existsSync(root)).map((root) => ({ dir: root, depth: 0 }));

  while (stack.length) {
    if (Date.now() - started > budgetMs) { truncated = true; break; }
    if (items.length >= maxResults) { truncated = true; break; }

    const node = stack.pop();
    visited++;

    let entries;
    try { entries = fs.readdirSync(node.dir, { withFileTypes: true }); } catch { continue; }

    for (const entry of entries) {
      const full = path.join(node.dir, entry.name);

      if (entry.isFile()) {
        const extension = path.extname(entry.name).toLowerCase();
        if (!HUNTED.has(extension)) continue;
        const value = plain(path.basename(entry.name, extension));
        if (!value || !needles.some((needle) => needle && value.includes(needle))) continue;
        let size = 0;
        try { size = fs.statSync(full).size; } catch { /* пропало */ }
        items.push({
          name: path.basename(entry.name, extension),
          path: full, folder: node.dir,
          kind: extension === '.exe' ? 'exe' : 'file',
          source: 'Диск', size
        });
        continue;
      }

      if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
      if (SKIP.test(full)) continue;

      const value = plain(entry.name);
      if (value && needles.some((needle) => needle && value.includes(needle))) {
        const exe = mainExe(full, 1);
        items.push({
          name: entry.name, path: exe || full, folder: full,
          kind: exe ? 'exe' : 'folder', source: 'Диск'
        });
      }
      if (node.depth < maxDepth) stack.push({ dir: full, depth: node.depth + 1 });
    }
  }

  return { items, visited, truncated, elapsed: Date.now() - started };
}

function defaultRoots() {
  const roots = [process.env.USERPROFILE].filter(Boolean);
  for (const sub of ['C:\\Games', 'C:\\Program Files', 'C:\\Program Files (x86)']) {
    if (fs.existsSync(sub)) roots.push(sub);
  }
  for (const letter of 'DEFGH') {
    const drive = letter + ':\\';
    if (fs.existsSync(drive)) roots.push(drive);
  }
  return roots;
}

/* --------------------------------------------------------------- сканер ---- */

/**
 * @param {{query?:string, mode?:string, deep?:boolean}} payload
 * @param {object} config содержимое config.json
 */
function scan(payload, config) {
  const settings = config.scan || {};
  const query = String(payload.query || payload.target || '').trim().slice(0, 120);
  const mode = payload.mode === 'games' ? 'games' : 'files';
  const deep = Boolean(payload.deep);

  const maxResults = settings.maxResults || 40;
  const budget = deep ? (settings.deepBudgetMs || 25000) : (settings.budgetMs || 9000);
  const maxDepth = deep ? (settings.deepMaxDepth || 6) : (settings.maxDepth || 4);

  const variants = new Set();
  if (query) {
    variants.add(plain(query));
    variants.add(plain(translit(query)));
    const canonical = resolveGameAlias(query);
    if (canonical) variants.add(plain(canonical));
  }
  const needles = [...variants].filter((needle) => needle && needle.length >= 2);

  const started = Date.now();
  const pool = [...steamGames(), ...epicGames(), ...registryPrograms()];
  let scanned = pool.length;
  let truncated = false;

  const strong = pool.filter((item) => matchScore(item.name, needles) >= 70);
  if (needles.length && (deep || !strong.length)) {
    const roots = (settings.roots && settings.roots.length ? settings.roots : defaultRoots())
      .map((root) => root.replace(/%([^%]+)%/g, (whole, name) => process.env[name] || whole));
    const disk = searchDisk(roots, needles, budget, maxDepth, maxResults);
    pool.push(...disk.items);
    scanned += disk.visited;
    truncated = disk.truncated;
  }

  const seen = new Set();
  const ranked = [];
  for (const item of pool) {
    if (!item.path) continue;
    const key = item.path.toLowerCase();
    if (seen.has(key)) continue;

    let score = needles.length ? matchScore(item.name, needles) : 50;
    if (mode === 'games') {
      if (item.source === 'Steam' || item.source === 'Epic Games') score += 40;
      else if (!needles.length) continue;
    }
    if (score <= 0) continue;

    seen.add(key);
    ranked.push({ ...item, score, launchable: item.kind === 'exe' });
  }

  ranked.sort((a, b) => b.score - a.score);
  const top = ranked.slice(0, maxResults);

  return {
    method: 'scan',
    title: query ? `Поиск: ${query}` : mode === 'games' ? 'Игры на компьютере' : 'Сканирование файлов',
    detail: `найдено ${top.length}, просмотрено ${scanned}, ${Date.now() - started} мс`,
    query, mode,
    results: top,
    found: top.length,
    scanned,
    elapsed: Date.now() - started,
    truncated
  };
}

module.exports = { scan, matchScore, resolveGameAlias, plain, translit };
