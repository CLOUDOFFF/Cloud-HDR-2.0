/* ============================================================================
   Cloud HDR — клиент локального агента
   ----------------------------------------------------------------------------
   Одинаково работает с Node- и Python-бэкендом (у них общий контракт API).
   Если агент не запущен — включается демо-режим: интерфейс живёт полностью,
   но реальные действия в системе не выполняются.
   ========================================================================== */
(function (global) {
  'use strict';

  const AGENT_PORT = 4477;
  // Страницу может отдавать сам агент (тогда origin совпадает) либо file:// — тогда бьём в localhost.
  const BASE = location.protocol.startsWith('http') && location.port === String(AGENT_PORT)
    ? ''
    : `http://127.0.0.1:${AGENT_PORT}`;

  const state = {
    token: sessionStorage.getItem('cloudhdr.token') || null,
    mode: 'offline',      // offline | live | demo
    info: null,
    permissions: null
  };

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 7000);
    try {
      const response = await fetch(BASE + path, {
        method: options.method || 'GET',
        headers: {
          'Content-Type': 'application/json',
          ...(state.token ? { 'X-Cloud-Token': state.token } : {}),
          ...(options.headers || {})
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        const error = new Error(data.error || `Агент вернул ошибку ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Есть ли агент на 127.0.0.1 и какие у него права. */
  async function probe() {
    try {
      state.info = await request('/api/health', { timeout: 2500 });
      return state.info;
    } catch {
      state.info = null;
      return null;
    }
  }

  /** Пользователь нажал «Разрешить» — просим у агента сессионный токен. */
  async function grant(pin) {
    const data = await request('/api/session/grant', { method: 'POST', body: { pin: pin || null } });
    state.token = data.token;
    state.permissions = data.permissions;
    state.mode = 'live';
    sessionStorage.setItem('cloudhdr.token', data.token);
    return data;
  }

  async function revoke() {
    if (state.token) { try { await request('/api/session/revoke', { method: 'POST' }); } catch { /* агент мог уже упасть */ } }
    state.token = null;
    state.mode = 'offline';
    sessionStorage.removeItem('cloudhdr.token');
  }

  function enableDemo() {
    state.mode = 'demo';
    state.token = null;
    sessionStorage.removeItem('cloudhdr.token');
  }

  /* ------------------------------------------------------- демо-имитация - */

  const DEMO_DELAY = () => 550 + Math.random() * 700;

  /** Правдоподобные образцы для демо-режима: интерфейс поиска виден без агента. */
  const DEMO_SCAN = [
    { name: 'Among Us', path: 'D:\\SteamLibrary\\steamapps\\common\\Among Us\\Among Us.exe', source: 'Steam', kind: 'exe', launchable: true, size: 62914560 },
    { name: 'Minecraft Launcher', path: 'C:\\Program Files (x86)\\Minecraft Launcher\\MinecraftLauncher.exe', source: 'Установленные программы', kind: 'exe', launchable: true, size: 2097152 },
    { name: 'Roblox', path: 'C:\\Users\\Public\\Roblox\\RobloxPlayerBeta.exe', source: 'Установленные программы', kind: 'exe', launchable: true }
  ];

  function demoExec(payload) {
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        const titles = {
          open_app: 'Приложение запущено',
          open_folder: 'Папка открыта',
          open_url: 'Ссылка открыта',
          web_search: 'Поиск выполнен',
          close_app: 'Приложение закрыто',
          volume_up: 'Громкость увеличена',
          volume_down: 'Громкость уменьшена',
          volume_mute: 'Звук выключен',
          volume_unmute: 'Звук включён',
          screenshot: 'Скриншот сохранён',
          lock: 'Рабочая станция заблокирована',
          window_pin: 'Окно закреплено',
          window_unpin: 'Окно откреплено',
          open_path: 'Файл запущен',
          reveal_path: 'Показано в Проводнике'
        };

        if (payload.intent === 'scan_files') {
          const query = String(payload.query || payload.target || '').toLowerCase();
          const results = query
            ? DEMO_SCAN.filter((item) => item.name.toLowerCase().includes(query.slice(0, 4)))
            : DEMO_SCAN;
          return resolve({
            ok: true, demo: true, intent: 'scan_files',
            title: 'Демо-поиск', method: 'demo', detail: 'имитация — агент не запущен',
            results: results.length ? results : DEMO_SCAN.slice(0, 1),
            found: results.length, scanned: 1240, elapsed: 820, truncated: false
          });
        }

        if (!titles[payload.intent]) return reject(new Error('Демо-режим не знает такого действия'));
        resolve({
          ok: true,
          demo: true,
          intent: payload.intent,
          title: titles[payload.intent],
          method: 'demo',
          detail: 'имитация — агент не запущен'
        });
      }, DEMO_DELAY());
    });
  }

  /**
   * Выполнение действия: живой агент или имитация.
   * @param {number} [timeout] сканирование дисков идёт дольше обычной команды
   */
  async function exec(payload, timeout) {
    if (state.mode === 'demo') return demoExec(payload);
    if (state.mode !== 'live' || !state.token) throw new Error('Нет разрешения на управление системой');
    return request('/api/exec', { method: 'POST', body: payload, timeout: timeout || 12000 });
  }

  async function apps() {
    if (state.mode !== 'live') return null;
    try { return await request('/api/apps'); } catch { return null; }
  }

  /**
   * Привычки хозяина: любимые программы и сайты.
   *
   * Срок ожидания больше обычного: первый за двадцать минут запрос читает
   * историю браузеров, а это отдельный процесс Python с разбором нескольких
   * баз SQLite. Дальше ответ идёт из кеша агента мгновенно.
   *
   * Ошибка здесь ничего не ломает: блок привычек просто не появится.
   */
  async function usage() {
    if (state.mode !== 'live') return null;
    try { return await request('/api/usage', { timeout: 20000 }); } catch { return null; }
  }

  /**
   * Постоянное прослушивание.
   *
   * Без аргументов — чтение состояния, и оно намеренно НЕ требует разрешения:
   * показать «слушаю» или «не слушаю» может и демо-режим, ничего в системе это
   * не меняет. А вот включение и выключение — меняет, и токен там обязателен.
   *
   * @param {string} [action] on | off | rebuild | sense | mute | unmute
   * @param {string} [value]  для sense — чувствительность, для mute — миллисекунды
   */
  async function ears(action, value) {
    if (!action) return request('/api/ears', { timeout: 3000 });
    if (state.mode !== 'live' || !state.token) throw new Error('Нет разрешения на управление системой');
    return request('/api/ears', { method: 'POST', body: { action, value: value || '' }, timeout: 15000 });
  }

  /**
   * Просит агента поднять упавшие службы — модель, разбор команд, Hub.
   * Агент запускает только молчащие и сам не даёт звать себя чаще раза в
   * 90 секунд. Разрешения не требует: ничего, кроме своих служб, не трогает.
   */
  async function startServices() {
    try { return await request('/api/services/start', { method: 'POST', timeout: 4000 }); } catch { return null; }
  }

  global.Agent = {
    state, probe, grant, revoke, enableDemo, exec, apps, usage, ears, startServices,
    get mode() { return state.mode; },
    get isLive() { return state.mode === 'live'; }
  };
})(window);
