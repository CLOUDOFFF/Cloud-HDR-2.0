'use strict';
/**
 * Cloud HDR — библиотека игр Steam: поиск по названию и запуск через appid.
 *
 * Зачем отдельный модуль, если scan.js уже находит игры Steam. Тот обходит
 * steamapps\common и знает про игры только имя папки — этого хватает, чтобы
 * показать находку в списке, но не хватает, чтобы игру ЗАПУСТИТЬ. Запуск exe
 * напрямую у многих игр либо не работает, либо ломает то, что рассчитывает на
 * Steam: античит, облачные сохранения, оверлей, права DRM. Штатный путь один —
 * steam://rungameid/<appid>.
 *
 * appid лежит рядом: на каждую установленную игру Steam держит файл
 * steamapps\appmanifest_<appid>.acf, и внутри есть и appid, и настоящее
 * название, и состояние установки. Читаем их.
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

/** Служебные пакеты Steam: формально приложения, играть в них нельзя. */
const JUNK = /(redistributable|proton|steam linux runtime|steamworks|dedicated server|sdk)/i;

/**
 * Русские названия и сленг, которые не берутся ни транслитом, ни расстоянием.
 * «Среди нас» и «Among Us» — разные слова, их не сближает никакой алгоритм.
 */
const ALIASES = [
  { name: 'Counter-Strike', words: ['контра', 'кс го', 'ксго', 'кс 2', 'кс2', 'counter strike', 'csgo', 'cs2'] },
  { name: 'Among Us', words: ['амонг ас', 'амонгас', 'амонг', 'среди нас', 'among us'] },
  { name: "Garry's Mod", words: ['гарис мод', 'гаррис мод', 'гмод', 'garrys mod', 'gmod'] },
  { name: 'BeamNG.drive', words: ['бимка', 'бимэнджи', 'бим эн джи', 'beamng'] },
  { name: 'Black Myth: Wukong', words: ['вуконг', 'укун', 'чёрный миф', 'черный миф', 'wukong'] },
  { name: 'Resident Evil', words: ['резидент ивел', 'резик', 'обитель зла', 'resident evil'] },
  { name: 'Slay the Spire', words: ['слей зе спайр', 'спайр', 'slay the spire'] },
  { name: 'Tabletop Simulator', words: ['тейблтоп', 'настольный симулятор', 'tabletop simulator'] },
  { name: 'Buckshot Roulette', words: ['бакшот', 'buckshot roulette'] },
  { name: 'DCS World', words: ['дцс', 'dcs world'] },
  { name: 'Geometry Dash', words: ['геометри даш', 'geometry dash'] },
  { name: 'Cyberpunk', words: ['киберпанк', 'сайберпанк', 'cyberpunk'] },
  { name: 'Subnautica', words: ['субнавтика', 'субнатика', 'subnautica'] },
  { name: 'Minecraft', words: ['майнкрафт', 'майн', 'minecraft'] },
  { name: 'Dota', words: ['дота', 'дота 2', 'dota'] },
  { name: 'Grand Theft Auto', words: ['гта', 'гта 5', 'gta'] },
  { name: 'The Witcher', words: ['ведьмак', 'witcher'] },
  { name: 'Terraria', words: ['террария', 'terraria'] },
  { name: 'Rust', words: ['раст', 'rust'] }
];

const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u',
  ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e',
  ю: 'yu', я: 'ya'
};

const comparable = (value) =>
  String(value || '').toLowerCase().replace(/ё/g, 'е').replace(/[^\p{L}\p{Nd}]/gu, '');

const translit = (value) =>
  String(value || '').toLowerCase().split('').map((c) => (c in TRANSLIT ? TRANSLIT[c] : c)).join('');

/**
 * «Мягкая» форма — только для сравнения.
 *
 * Транслитерация неоднозначна в обе стороны: «гарис мод» даёт garismod, а
 * Garry's Mod — garrysmod, и расстояние 2 не дотягивает до порога. Дело не в
 * опечатке, а в том, что одну букву принято писать по-разному: и/й/ы → i или y,
 * w или v, ck или k, з → z или s, сдвоенные согласные то есть, то нет. Здесь эти
 * различия стираются, и обе стороны совпадают точно.
 */
function loose(value) {
  return comparable(value)
    .replace(/ck/g, 'k')
    .replace(/w/g, 'v')
    .replace(/[yj]/g, 'i')
    .replace(/z/g, 's')
    .replace(/(.)\1+/g, '$1');
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

/* ---------------------------------------------------------------- библиотеки */

function libraries() {
  const roots = new Set();

  for (const hive of ['HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam',
                      'HKLM\\SOFTWARE\\Valve\\Steam',
                      'HKCU\\SOFTWARE\\Valve\\Steam']) {
    try {
      const out = execFileSync('reg.exe', ['query', hive, '/v', 'InstallPath'],
                               { encoding: 'utf8', timeout: 4000, windowsHide: true });
      const match = out.match(/InstallPath\s+REG_SZ\s+(.+)/);
      if (match) {
        const install = match[1].trim();
        if (install && fs.existsSync(install)) roots.add(install);
      }
    } catch { /* Steam не установлен — не ошибка */ }
  }
  for (const guess of ['C:\\Program Files (x86)\\Steam', 'D:\\Steam', 'D:\\SteamLibrary', 'E:\\SteamLibrary']) {
    if (fs.existsSync(guess)) roots.add(guess);
  }

  const all = new Set(roots);
  for (const root of roots) {
    const vdf = path.join(root, 'steamapps', 'libraryfolders.vdf');
    if (!fs.existsSync(vdf)) continue;
    try {
      const text = fs.readFileSync(vdf, 'utf8');
      for (const match of text.matchAll(/"path"\s*"([^"]+)"/g)) {
        const library = match[1].replace(/\\\\/g, '\\');
        if (fs.existsSync(library)) all.add(library);
      }
    } catch { /* повреждённый vdf */ }
  }
  return [...all];
}

let cache = null;
let cachedAt = 0;

/** Установленные игры: {appid, name, installed, library}. Кеш на 10 минут. */
function index(force = false) {
  if (cache && !force && Date.now() - cachedAt < 10 * 60 * 1000) return cache;

  const games = [];
  const seen = new Set();
  for (const library of libraries()) {
    const steamapps = path.join(library, 'steamapps');
    if (!fs.existsSync(steamapps)) continue;
    let entries;
    try { entries = fs.readdirSync(steamapps); } catch { continue; }
    for (const entry of entries) {
      if (!/^appmanifest_\d+\.acf$/i.test(entry)) continue;
      let text;
      try { text = fs.readFileSync(path.join(steamapps, entry), 'utf8'); } catch { continue; }

      const appid = (text.match(/"appid"\s*"(\d+)"/) || [])[1];
      const name = (text.match(/"name"\s*"([^"]+)"/) || [])[1];
      const state = (text.match(/"StateFlags"\s*"(\d+)"/) || [])[1];
      if (!appid || !name || JUNK.test(name) || seen.has(appid)) continue;
      seen.add(appid);

      // StateFlags — битовая маска Steam; бит 4 означает «установлено
      // полностью». Со снятым битом Steam начнёт докачивать игру, и до запуска
      // дело не дойдёт.
      games.push({ appid, name, installed: (Number(state) & 4) !== 0, library });
    }
  }

  cache = games;
  cachedAt = Date.now();
  return games;
}

/* -------------------------------------------------------------------- поиск */

function score(needle, name) {
  if (!needle || !name || needle.length < 2) return 0;
  if (name === needle) return 100;
  if (name.startsWith(needle)) return 90 - Math.min(name.length - needle.length, 20);
  if (name.includes(needle) && needle.length >= 3) return 76;
  if (needle.includes(name) && name.length >= 4) return 70;
  if (needle.length >= 4) {
    const distance = levenshtein(needle, name);
    const allowed = Math.max(1, Math.floor(needle.length / 4));
    if (distance <= allowed) return 66 - distance * 6;
  }
  return 0;
}

function resolveAlias(query) {
  const needle = comparable(query);
  if (!needle) return null;
  for (const game of ALIASES) {
    for (const word of game.words) {
      const key = comparable(word);
      if (key && (needle === key || needle.includes(key) || key.includes(needle))) return game.name;
    }
  }
  return null;
}

/** @returns {{best: object|null, suggestions: string[]}} */
function find(query) {
  const games = index();
  if (!games.length) return { best: null, suggestions: [] };

  const variants = [comparable(query), comparable(translit(query))];
  const alias = resolveAlias(query);
  if (alias) variants.push(comparable(alias));

  const scored = [];
  for (const game of games) {
    const name = comparable(game.name);
    if (!name) continue;
    const nameLoose = loose(game.name);

    let best = 0;
    for (const needle of variants) {
      best = Math.max(best, score(needle, name));
      // Второй проход по «мягкой» форме — со штрафом, чтобы точное совпадение
      // написания всегда выигрывало у приблизительного.
      const soft = score(loose(needle), nameLoose);
      if (soft > 0) best = Math.max(best, soft - 6);
    }
    if (best > 0 && !game.installed) best -= 20;
    if (best > 0) scored.push({ game, score: best });
  }

  scored.sort((a, b) => b.score - a.score);
  return {
    best: scored.length && scored[0].score >= 60 ? scored[0].game : null,
    suggestions: scored.slice(0, 3).map((row) => row.game.name)
  };
}

const runUri = (game) => `steam://rungameid/${game.appid}`;

module.exports = { index, find, runUri, comparable, loose, resolveAlias };
