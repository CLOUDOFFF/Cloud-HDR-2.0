'use strict';
/**
 * Cloud HDR — синтез речи на этом компьютере.
 *
 * Обёртка над tts.ps1: держит один живой процесс PowerShell, шлёт ему строки
 * запросов и отдаёт готовый WAV наверх. Наружу — единственная асинхронная
 * функция synthesize() и список голосов.
 *
 * Почему worker, а не «запустил powershell, дождался, забрал файл»: подъём
 * powershell.exe вместе с WinRT стоит около секунды, сам синтез — 150-350 мс.
 * На разовой задаче разница незаметна, но помощник говорит фразами, и секунда
 * тишины перед каждой из них превращает живую речь в диалог по рации.
 *
 * Процесс поднимается ЛЕНИВО — при первом запросе голоса, а не при старте
 * агента. Человек, который голосом не пользуется, не должен платить за него
 * лишним процессом в памяти.
 */

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');

const SCRIPT = path.join(__dirname, 'tts.ps1');
const TEMP = path.join(os.tmpdir(), 'cloud-hdr-voice');

/** Дольше этого синтез не идёт никогда — значит, worker завис и его надо поднять заново. */
const TIMEOUT_MS = 15000;
/** Столько раз пробуем поднять worker, прежде чем признать, что голоса на этой системе нет. */
const MAX_STARTS = 3;

const state = {
  child: null,
  starting: null,
  voices: [],
  ready: false,
  starts: 0,
  lastError: null,
  seq: 0
};

/** Незакрытые запросы: id → {resolve, reject, timer, file}. */
const pending = new Map();

function cleanupTemp() {
  try {
    fs.mkdirSync(TEMP, { recursive: true });
    // Файлы старше получаса — мусор от прошлых сеансов: WAV отдаётся клиенту
    // сразу и больше никому не нужен.
    const deadline = Date.now() - 30 * 60 * 1000;
    for (const name of fs.readdirSync(TEMP)) {
      const file = path.join(TEMP, name);
      try {
        if (fs.statSync(file).mtimeMs < deadline) fs.unlinkSync(file);
      } catch { /* занят другим процессом — пусть полежит */ }
    }
  } catch { /* нет доступа к temp — синтез всё равно попробуем */ }
}

function failAll(reason) {
  for (const [, job] of pending) {
    clearTimeout(job.timer);
    job.reject(new Error(reason));
  }
  pending.clear();
}

function handleLine(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return;                       // не наш вывод (предупреждение PowerShell) — молча пропускаем
  }

  if (message.id === 'ready') {
    state.voices = Array.isArray(message.voices) ? message.voices : [];
    state.ready = true;
    return;
  }

  const job = pending.get(message.id);
  if (!job) return;
  pending.delete(message.id);
  clearTimeout(job.timer);

  if (!message.ok) {
    state.lastError = message.error || 'синтез не удался';
    job.reject(new Error(state.lastError));
    return;
  }

  fs.readFile(job.file, (error, data) => {
    // Файл прочитан — он больше не нужен ни нам, ни кому-либо ещё.
    fs.unlink(job.file, () => {});
    if (error) return job.reject(new Error('Готовый звук не прочитался: ' + error.message));
    job.resolve({ audio: data, ms: message.ms, voice: message.voice });
  });
}

function start() {
  if (state.child) return Promise.resolve(true);
  if (state.starting) return state.starting;
  if (state.starts >= MAX_STARTS) return Promise.resolve(false);
  if (process.platform !== 'win32') return Promise.resolve(false);

  state.starts++;
  cleanupTemp();

  state.starting = new Promise((resolve) => {
    let child;
    try {
      child = spawn('powershell.exe',
        ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT],
        { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (error) {
      state.lastError = error.message;
      state.starting = null;
      return resolve(false);
    }

    state.child = child;
    child.stdin.setDefaultEncoding('utf8');

    let buffer = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      buffer += chunk;
      let cut;
      while ((cut = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (line) handleLine(line);
      }
    });

    // stderr читаем, но не считаем отказом: PowerShell охотно пишет туда
    // предупреждения, после которых прекрасно работает дальше.
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => { state.lastError = String(chunk).trim().slice(0, 300); });

    const finish = () => {
      state.child = null;
      state.ready = false;
      state.starting = null;
      failAll('Синтезатор речи остановился');
    };
    child.on('error', (error) => { state.lastError = error.message; finish(); resolve(false); });
    child.on('exit', finish);

    // Ждём рукопожатия со списком голосов: без него нельзя ни выбрать голос,
    // ни честно ответить интерфейсу, что синтез доступен.
    const started = Date.now();
    const poll = setInterval(() => {
      if (state.ready) { clearInterval(poll); state.starting = null; return resolve(true); }
      if (!state.child || Date.now() - started > 12000) {
        clearInterval(poll);
        state.starting = null;
        state.lastError = state.lastError || 'синтезатор не отозвался';
        resolve(false);
      }
    }, 60);
  });

  return state.starting;
}

/**
 * Озвучивает текст и возвращает готовый WAV.
 *
 * @param {object} options
 * @param {string} options.text   что сказать
 * @param {string} [options.voice] имя голоса; пусто — выбирается лучший русский
 * @param {number} [options.rate]  темп, проценты от обычного (-100…100)
 * @param {number} [options.pitch] высота тона в полутонах (-12…12)
 * @returns {Promise<{audio: Buffer, ms: number, voice: string}>}
 */
async function synthesize(options = {}) {
  const text = String(options.text == null ? '' : options.text).trim();
  if (!text) throw new Error('Нечего произносить');
  if (text.length > 1200) throw new Error('Слишком длинная фраза для одной озвучки');

  const up = await start();
  if (!up) throw new Error(state.lastError || 'Синтез речи недоступен на этой системе');

  const id = String(++state.seq);
  const file = path.join(TEMP, `${Date.now().toString(36)}-${crypto.randomBytes(4).toString('hex')}.wav`);

  const request = {
    id,
    text,
    file,
    voice: String(options.voice || ''),
    // Границы не косметические: за ними синтезатор начинает либо тараторить,
    // либо гудеть, и «приятный голос» превращается в свою противоположность.
    rate: Math.max(-50, Math.min(50, Number(options.rate) || 0)),
    pitch: Math.max(-12, Math.min(12, Number(options.pitch) || 0))
  };

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('Синтезатор речи не ответил вовремя'));
      // Зависший worker уже не оживёт: следующий запрос поднимет новый.
      if (state.child) { try { state.child.kill(); } catch { /* уже мёртв */ } }
    }, TIMEOUT_MS);

    pending.set(id, { resolve, reject, timer, file });
    try {
      state.child.stdin.write(JSON.stringify(request) + '\n');
    } catch (error) {
      clearTimeout(timer);
      pending.delete(id);
      reject(new Error('Не удалось передать текст синтезатору: ' + error.message));
    }
  });
}

/** Список доступных голосов; поднимает worker, если тот ещё не запущен. */
async function voices() {
  const up = await start();
  return up ? state.voices : [];
}

function shutdown() {
  if (!state.child) return;
  try { state.child.stdin.end(); } catch { /* уже закрыт */ }
  try { state.child.kill(); } catch { /* уже мёртв */ }
  state.child = null;
}

module.exports = {
  synthesize,
  voices,
  shutdown,
  get available() { return state.ready; },
  get lastError() { return state.lastError; }
};
