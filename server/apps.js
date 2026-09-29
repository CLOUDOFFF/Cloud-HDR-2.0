'use strict';
/**
 * Cloud HDR — реестр разрешённых приложений и папок.
 *
 * Это единственный источник правды о том, что агенту вообще позволено запускать.
 * Клиент присылает только КЛЮЧ из этого объекта (например "chrome"), но никогда —
 * готовую команду. Всё, чего нет здесь, запустить невозможно.
 *
 * Порядок разрешения запуска:
 *   1) paths  — первый существующий .exe на диске (самый надёжный вариант);
 *   2) uri    — протокол-хендлер (roblox://, discord://, steam://…);
 *   3) shell  — запасной вариант через `start` (ищет по PATH / App Paths реестра).
 */

const path = require('path');
const fs = require('fs');

/** Разворачивает %VAR% в путях Windows. */
function expandEnv(input) {
  return String(input).replace(/%([^%]+)%/g, (whole, name) => {
    const value = process.env[name] || process.env[name.toUpperCase()];
    return value === undefined ? whole : value;
  });
}

const APPS = {
  roblox: {
    title: 'Roblox',
    icon: 'game',
    aliases: ['роблокс', 'роблокc', 'рублокс', 'роблокс плеер', 'roblox', 'roblox player', 'рбх'],
    paths: ['%LOCALAPPDATA%/Roblox/Versions'], // особый случай: каталог версий, см. resolveApp()
    dynamic: 'roblox',
    uri: 'roblox://',
    shell: 'RobloxPlayerBeta.exe',
    image: 'RobloxPlayerBeta.exe'
  },

  discord: {
    title: 'Discord',
    icon: 'chat',
    aliases: ['дискорд', 'дискорт', 'диcкорд', 'диск', 'discord', 'дс'],
    paths: ['%LOCALAPPDATA%/Discord/Update.exe'],
    args: ['--processStart', 'Discord.exe'],
    uri: 'discord://',
    image: 'Discord.exe'
  },

  chrome: {
    title: 'Google Chrome',
    icon: 'globe',
    aliases: ['хром', 'гугл хром', 'chrome', 'google chrome', 'браузер', 'гугл', 'browser'],
    paths: [
      '%ProgramFiles%/Google/Chrome/Application/chrome.exe',
      '%ProgramFiles(x86)%/Google/Chrome/Application/chrome.exe',
      '%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe'
    ],
    shell: 'chrome',
    image: 'chrome.exe',
    browser: true
  },

  edge: {
    title: 'Microsoft Edge',
    icon: 'globe',
    aliases: ['эдж', 'едж', 'edge', 'microsoft edge', 'майкрософт эдж'],
    paths: [
      '%ProgramFiles(x86)%/Microsoft/Edge/Application/msedge.exe',
      '%ProgramFiles%/Microsoft/Edge/Application/msedge.exe'
    ],
    shell: 'msedge',
    image: 'msedge.exe',
    browser: true
  },

  firefox: {
    title: 'Mozilla Firefox',
    icon: 'globe',
    aliases: ['фаерфокс', 'файрфокс', 'firefox', 'мозила', 'мозилла'],
    paths: ['%ProgramFiles%/Mozilla Firefox/firefox.exe', '%ProgramFiles(x86)%/Mozilla Firefox/firefox.exe'],
    shell: 'firefox',
    image: 'firefox.exe',
    browser: true
  },

  steam: {
    title: 'Steam',
    icon: 'game',
    aliases: ['стим', 'steam', 'стем'],
    paths: ['%ProgramFiles(x86)%/Steam/steam.exe', '%ProgramFiles%/Steam/steam.exe'],
    uri: 'steam://open/main',
    image: 'steam.exe'
  },

  telegram: {
    title: 'Telegram',
    icon: 'chat',
    aliases: ['телеграм', 'телега', 'тг', 'telegram', 'tg'],
    paths: ['%APPDATA%/Telegram Desktop/Telegram.exe', '%LOCALAPPDATA%/Programs/Telegram Desktop/Telegram.exe'],
    uri: 'tg://',
    image: 'Telegram.exe'
  },

  spotify: {
    title: 'Spotify',
    icon: 'music',
    aliases: ['спотифай', 'спотифи', 'spotify', 'музыка'],
    paths: ['%APPDATA%/Spotify/Spotify.exe'],
    uri: 'spotify:',
    image: 'Spotify.exe'
  },

  vscode: {
    title: 'Visual Studio Code',
    icon: 'code',
    aliases: ['вскод', 'вс код', 'вижуал студио код', 'vscode', 'vs code', 'code', 'редактор кода'],
    paths: [
      '%LOCALAPPDATA%/Programs/Microsoft VS Code/Code.exe',
      '%ProgramFiles%/Microsoft VS Code/Code.exe'
    ],
    shell: 'code',
    image: 'Code.exe'
  },

  explorer: {
    title: 'Проводник',
    icon: 'folder',
    aliases: ['проводник', 'эксплорер', 'explorer', 'файлы', 'мой компьютер', 'этот компьютер'],
    paths: ['%WINDIR%/explorer.exe'],
    image: null // explorer убивать нельзя — это оболочка Windows
  },

  notepad: {
    title: 'Блокнот',
    icon: 'note',
    aliases: ['блокнот', 'notepad', 'заметки', 'нотпад'],
    paths: ['%WINDIR%/system32/notepad.exe'],
    shell: 'notepad',
    image: 'notepad.exe'
  },

  calc: {
    title: 'Калькулятор',
    icon: 'calc',
    aliases: ['калькулятор', 'калькулятр', 'calc', 'calculator', 'счёты'],
    shell: 'calc',
    uri: 'calculator://',
    image: 'CalculatorApp.exe'
  },

  terminal: {
    title: 'Терминал',
    icon: 'code',
    aliases: ['терминал', 'консоль', 'командная строка', 'cmd', 'terminal', 'powershell'],
    paths: ['%LOCALAPPDATA%/Microsoft/WindowsApps/wt.exe', '%WINDIR%/system32/cmd.exe'],
    shell: 'cmd',
    image: null
  },

  settings: {
    title: 'Параметры Windows',
    icon: 'gear',
    aliases: ['настройки', 'параметры', 'settings', 'настройки виндовс', 'панель управления'],
    uri: 'ms-settings:',
    image: null
  },

  taskmgr: {
    title: 'Диспетчер задач',
    icon: 'gear',
    aliases: ['диспетчер задач', 'диспетчер', 'task manager', 'taskmgr'],
    paths: ['%WINDIR%/system32/Taskmgr.exe'],
    image: null
  },

  paint: {
    title: 'Paint',
    icon: 'brush',
    aliases: ['пейнт', 'paint', 'рисовалка', 'мспейнт'],
    paths: ['%WINDIR%/system32/mspaint.exe'],
    shell: 'mspaint',
    image: 'mspaint.exe'
  },

  obs: {
    title: 'OBS Studio',
    icon: 'video',
    aliases: ['обс', 'obs', 'obs studio', 'стрим'],
    paths: ['%ProgramFiles%/obs-studio/bin/64bit/obs64.exe'],
    image: 'obs64.exe'
  },

  epicgames: {
    title: 'Epic Games Launcher',
    icon: 'game',
    aliases: ['эпик геймс', 'эпик', 'epic', 'epic games'],
    paths: ['%ProgramFiles(x86)%/Epic Games/Launcher/Portal/Binaries/Win64/EpicGamesLauncher.exe'],
    uri: 'com.epicgames.launcher://',
    image: 'EpicGamesLauncher.exe'
  }
};

/** Именованные папки: «открой папку загрузки». */
const FOLDERS = {
  desktop: { title: 'Рабочий стол', aliases: ['рабочий стол', 'рабочего стола', 'десктоп', 'desktop'], path: '%USERPROFILE%/Desktop' },
  downloads: { title: 'Загрузки', aliases: ['загрузки', 'загрузок', 'скачанное', 'downloads', 'даунлоадс'], path: '%USERPROFILE%/Downloads' },
  documents: { title: 'Документы', aliases: ['документы', 'документов', 'documents', 'доки'], path: '%USERPROFILE%/Documents' },
  pictures: { title: 'Изображения', aliases: ['изображения', 'картинки', 'фото', 'pictures', 'images'], path: '%USERPROFILE%/Pictures' },
  music: { title: 'Музыка', aliases: ['музыка', 'музыки', 'music'], path: '%USERPROFILE%/Music' },
  videos: { title: 'Видео', aliases: ['видео', 'видосы', 'videos', 'фильмы'], path: '%USERPROFILE%/Videos' },
  home: { title: 'Профиль пользователя', aliases: ['профиль', 'домашняя папка', 'юзер', 'home'], path: '%USERPROFILE%' }
};

/**
 * Roblox ставится в %LOCALAPPDATA%\Roblox\Versions\version-<hash>\RobloxPlayerBeta.exe —
 * ищем самую свежую версию.
 */
function resolveRobloxPlayer() {
  const base = expandEnv('%LOCALAPPDATA%/Roblox/Versions');
  if (!fs.existsSync(base)) return null;
  let newest = null;
  for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const exe = path.join(base, entry.name, 'RobloxPlayerBeta.exe');
    if (!fs.existsSync(exe)) continue;
    const mtime = fs.statSync(exe).mtimeMs;
    if (!newest || mtime > newest.mtime) newest = { exe, mtime };
  }
  return newest ? newest.exe : null;
}

/** Находит исполняемый файл приложения на диске (или null). */
function resolveExecutable(app) {
  if (app.dynamic === 'roblox') return resolveRobloxPlayer();
  for (const candidate of app.paths || []) {
    const full = path.normalize(expandEnv(candidate));
    if (fs.existsSync(full) && fs.statSync(full).isFile()) return full;
  }
  return null;
}

/** Ключ приложения по алиасу / ключу (без фаззи-поиска — точное совпадение). */
function findAppKey(raw) {
  if (!raw) return null;
  const needle = String(raw).toLowerCase().trim();
  if (APPS[needle]) return needle;
  for (const [key, app] of Object.entries(APPS)) {
    if (key === needle) return key;
    if (app.title.toLowerCase() === needle) return key;
    if ((app.aliases || []).includes(needle)) return key;
  }
  return null;
}

/** Ключ именованной папки по алиасу. */
function findFolderKey(raw) {
  if (!raw) return null;
  const needle = String(raw).toLowerCase().trim().replace(/^папк[аиуе]\s+/, '');
  if (FOLDERS[needle]) return needle;
  for (const [key, folder] of Object.entries(FOLDERS)) {
    if ((folder.aliases || []).includes(needle)) return key;
  }
  return null;
}

/** Публичный каталог для фронтенда: что доступно и что реально установлено. */
function catalog() {
  return {
    apps: Object.entries(APPS).map(([key, app]) => ({
      key,
      title: app.title,
      icon: app.icon || 'app',
      aliases: app.aliases || [],
      found: Boolean(resolveExecutable(app)) || Boolean(app.uri) || Boolean(app.shell),
      installed: Boolean(resolveExecutable(app))
    })),
    folders: Object.entries(FOLDERS).map(([key, folder]) => ({
      key,
      title: folder.title,
      aliases: folder.aliases,
      path: expandEnv(folder.path),
      exists: fs.existsSync(expandEnv(folder.path))
    }))
  };
}

module.exports = { APPS, FOLDERS, expandEnv, resolveExecutable, findAppKey, findFolderKey, catalog };
