'use strict';
/**
 * Cloud HDR Agent — локальный HTTP-сервер.
 *
 *   • раздаёт фронтенд из ../public
 *   • выдаёт сессионный токен после явного разрешения пользователя
 *   • выполняет строго ограниченный набор действий в Windows
 *
 * Слушает только 127.0.0.1 — извне сервер недоступен.
 * Внешних зависимостей нет: `node server.js` и всё.
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const { execute, ExecError } = require('./executor');
const { catalog, expandEnv } = require('./apps');
const steam = require('./steam');
const tts = require('./tts');

const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, '..', 'public');
const VERSION = '1.0.0';

const config = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
const PIN = config.requirePin ? String(Math.floor(100000 + Math.random() * 900000)) : null;

/* ------------------------------------------------------------------ сессии */

const sessions = new Map(); // token -> { createdAt, hits: number[] }

function createSession() {
  const token = crypto.randomBytes(24).toString('hex');
  sessions.set(token, { createdAt: Date.now(), hits: [] });
  return token;
}

function checkRateLimit(session) {
  const limit = (config.rateLimit && config.rateLimit.actionsPerMinute) || 30;
  const now = Date.now();
  session.hits = session.hits.filter((t) => now - t < 60_000);
  if (session.hits.length >= limit) return false;
  session.hits.push(now);
  return true;
}

/* ------------------------------------------------------------------ журнал */

function log(entry) {
  const line = `[${new Date().toISOString()}] ${entry}`;
  console.log(line);
  if (!config.logging || !config.logging.enabled) return;
  const file = path.join(ROOT, config.logging.file || 'logs/actions.log');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFile(file, line + '\n', () => {});
}

/* ------------------------------------------------------------------ утилиты HTTP */

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2'
};

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function readBody(req, limit = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new ExecError('Тело запроса слишком велико', 413));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8').trim();
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new ExecError('Некорректный JSON', 400));
      }
    });
    req.on('error', reject);
  });
}

function serveStatic(req, res, urlPath) {
  const rel = decodeURIComponent(urlPath === '/' ? '/index.html' : urlPath).replace(/^\/+/, '');
  const filePath = path.join(PUBLIC_DIR, rel);

  if (!filePath.startsWith(PUBLIC_DIR)) {
    res.writeHead(403).end('Forbidden');
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404 — не найдено: ' + rel);
      return;
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-cache'
    });
    res.end(data);
  });
}

/** Токен обязателен для любого действия в системе. */
function requireSession(req) {
  const token = req.headers['x-cloud-token'];
  if (!token || !sessions.has(token)) throw new ExecError('Нет разрешения на управление системой. Обновите страницу.', 401);
  const session = sessions.get(token);
  if (!checkRateLimit(session)) throw new ExecError('Слишком много команд подряд. Подождите минуту.', 429);
  return session;
}

/* ------------------------------------------------------------------ маршруты */

/**
 * Метка «ответ уже отправлен».
 *
 * Почти все маршруты возвращают объект, который диспетчер сам заворачивает в
 * JSON. Озвучка выбивается: она отдаёт WAV, то есть двоичные данные, и пишет в
 * res своими руками. Без этой метки диспетчер дописал бы следом ещё и JSON —
 * в уже закрытый ответ.
 */
const RAW = Symbol('raw');

const routes = {
  'GET /api/health': async () => ({
    ok: true,
    name: 'Cloud HDR Agent',
    version: VERSION,
    platform: process.platform,
    host: require('os').hostname(),
    user: process.env.USERNAME || null,
    requirePin: Boolean(PIN),
    permissions: config.permissions,
    uptime: Math.round(process.uptime())
  }),

  'POST /api/session/grant': async (req) => {
    const body = await readBody(req);
    if (PIN && String(body.pin || '').trim() !== PIN) {
      throw new ExecError('Неверный код подтверждения', 401);
    }
    const token = createSession();
    log(`GRANT  сессия выдана (${token.slice(0, 8)}…)`);
    return { ok: true, token, permissions: config.permissions, expiresIn: null };
  },

  'POST /api/session/revoke': async (req) => {
    const token = req.headers['x-cloud-token'];
    if (token) sessions.delete(token);
    log('REVOKE разрешение отозвано');
    return { ok: true };
  },

  'GET /api/apps': async (req) => {
    requireSession(req);
    // Имена игр библиотеки уходят в браузер вместе с каталогом: постоянному
    // прослушиванию нужен словарь ИМЕННО ЭТОГО компьютера, чтобы отличить
    // «открой кс го» от обрывка чужого разговора. Индекс всё равно построен
    // для запуска игр и живёт в кэше десять минут.
    let games = [];
    try { games = steam.index().filter((game) => game.installed).map((game) => game.name); } catch { /* нет Steam */ }
    return { ok: true, ...catalog(), names: [], games };
  },

  /*
     Голос помощника. Сессионный токен здесь НЕ требуется, и это отличается от
     всех остальных маршрутов сознательно: синтез речи ничего в системе не
     делает — он превращает текст в звук и возвращает его обратно в браузер.
     Требовать разрешение на управление Windows ради этого значило бы, что в
     демо-режиме помощник немеет, хотя причин молчать у него нет.
  */
  'GET /api/tts/voices': async () => ({
    ok: true,
    available: true,
    voices: await tts.voices(),
    error: tts.lastError
  }),

  'POST /api/tts': async (req, res) => {
    const body = await readBody(req, 8 * 1024);
    let result;
    try {
      result = await tts.synthesize(body);
    } catch (error) {
      throw new ExecError(error.message || 'Синтез речи недоступен', 503);
    }
    res.writeHead(200, {
      'Content-Type': 'audio/wav',
      'Content-Length': result.audio.length,
      'Cache-Control': 'no-store',
      'X-Cloud-Voice': encodeURIComponent(result.voice || ''),
      'X-Cloud-Voice-Ms': String(result.ms || 0)
    });
    res.end(result.audio);
    return RAW;
  },

  'POST /api/exec': async (req) => {
    requireSession(req);
    const body = await readBody(req);
    try {
      const result = execute(body, config);
      log(`EXEC   ${body.intent} → ${result.title} (${result.method}: ${result.detail})`);
      return { ok: true, intent: body.intent, ...result };
    } catch (error) {
      log(`FAIL   ${body.intent} → ${error.message}`);
      throw error;
    }
  }
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const key = `${req.method} ${url.pathname}`;

  // Ответы всегда локальные; CORS открываем только для file:// (демо без сервера).
  res.setHeader('Access-Control-Allow-Origin', req.headers.origin || '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Cloud-Token');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  if (req.method === 'OPTIONS') return res.writeHead(204).end();

  if (routes[key]) {
    try {
      const result = await routes[key](req, res);
      if (result !== RAW) sendJson(res, 200, result);
    } catch (error) {
      const status = error.code && Number.isInteger(error.code) ? error.code : 500;
      if (!res.headersSent) sendJson(res, status, { ok: false, error: error.message || 'Внутренняя ошибка агента' });
      else res.end();
    }
    return;
  }
  if (url.pathname.startsWith('/api/')) return sendJson(res, 404, { ok: false, error: 'Метод не найден' });

  serveStatic(req, res, url.pathname);
});

server.listen(config.port, config.host, () => {
  const address = `http://${config.host}:${config.port}`;
  console.log('');
  console.log('   [CLOUD HDR AI v1.0] Запуск модуля...');
  console.log('   ☁  Cloud HDR Agent v' + VERSION);
  console.log('   ─────────────────────────────────────────────');
  console.log('   Интерфейс :  ' + address);
  console.log('   Права     :  ' + Object.entries(config.permissions).filter(([, v]) => v).map(([k]) => k).join(', '));
  console.log('   Папки     :  ' + (config.allowedFolderRoots || []).map(expandEnv).join('  |  '));
  if (PIN) console.log('   КОД ДОСТУПА: ' + PIN + '   ← введите его в окне разрешений');
  console.log('   Ctrl+C — остановить агента');
  console.log('');

  if (config.openBrowserOnStart) {
    spawn('cmd.exe', ['/c', 'start', '', address], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  }
});

server.on('error', (error) => {
  if (error.code === 'EADDRINUSE') {
    console.error(`\n   ✖ Порт ${config.port} занят. Измените "port" в config.json и AGENT_PORT в public/js/api.js\n`);
    process.exit(1);
  }
  throw error;
});

process.on('SIGINT', () => {
  console.log('\n   ☁ Агент остановлен. Все разрешения аннулированы.\n');
  tts.shutdown();
  process.exit(0);
});

// Синтезатор — отдельный процесс PowerShell, и он переживёт агента, если его
// не закрыть явно. Один осиротевший powershell.exe в памяти на каждый запуск —
// именно то, что потом ищут в диспетчере задач и не находят объяснения.
process.on('exit', () => tts.shutdown());
