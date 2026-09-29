/* ============================================================================
   Cloud HDR — мост к сервису понимания команд на spaCy
   ----------------------------------------------------------------------------
   Разбор запроса переезжает из браузера в сервис на 127.0.0.1:4478, где живёт
   обученная модель: textcat решает, что за команда, ner размечает её цель.

   Мост построен ровно по той же схеме, что и llm.js: сервис ищется в фоне при
   запуске, и его отсутствие ничего не ломает. Не ответил — приложение работает
   на brain.js в браузере, как работало раньше. Это не запасной путь на случай
   аварии, а штатный режим для тех, кто не поднимал Python.

   Ответ сервиса — данные, а не команда. Что показать пользователю и что
   выполнить, решает nlu.js: там уже написаны и тексты ответов, и проверки
   безопасности, и повторять их на другой стороне значило бы завести им второе
   место для расхождений.
   ========================================================================== */
(function (global) {
  'use strict';

  const BASE = 'http://127.0.0.1:4478';
  const PROBE_TIMEOUT = 2500;   // загрузка модели идёт до запуска сервера
  const PARSE_TIMEOUT = 2000;   // разбор укладывается в 20–40 мс
  const RETRY_AFTER = 60000;    // как скоро пробовать снова после отказа

  const state = {
    available: false,
    info: null,
    lastError: null,
    failedAt: 0,
    misses: 0
  };

  /** fetch с ограничением по времени: висящий сервис не должен вешать ввод. */
  async function request(path, options, timeout) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(BASE + path, { ...options, signal: controller.signal });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  /** Есть ли сервис и какая модель поднята. */
  async function probe() {
    try {
      const info = await request('/nlu/health', { method: 'GET' }, PROBE_TIMEOUT);
      state.available = Boolean(info && info.ok);
      state.info = info;
      state.lastError = null;
      state.misses = 0;
      return { available: state.available, info };
    } catch (error) {
      state.available = false;
      state.info = null;
      state.lastError = error.name === 'AbortError' ? 'сервис не ответил вовремя' : String(error.message || error);
      state.failedAt = Date.now();
      return { available: false, info: null };
    }
  }

  /**
   * Полный разбор: план из одного или нескольких шагов.
   *
   * Отличается от parse() не подробностями, а формой ответа. parse() отдаёт
   * одно намерение на всю фразу — и этого не хватало ровно там, где фраза не
   * была одной командой: «не открывай хром» выполнялось как запуск, «открой
   * хром и найди котиков» теряло половину. Здесь приходит список шагов и
   * маршрут: выполнять, ответить или это вообще отмена.
   */
  async function plan(text, context) {
    return ask('/nlu/plan', text, context);
  }

  /**
   * Разбор фразы сервисом: одно намерение на всю фразу.
   * @returns {Promise<object|null>} null — сервиса нет, решает brain.js
   */
  async function parse(text, context) {
    return ask('/nlu/parse', text, context);
  }

  /** Общий вызов разбора: доступность сервиса, тело запроса, учёт осечек. */
  async function ask(path, text, context) {
    if (!state.available) {
      // Сервис могли поднять уже после загрузки страницы — переспрашиваем,
      // но редко: дёргать недоступный порт на каждой реплике незачем.
      if (Date.now() - state.failedAt < RETRY_AFTER) return null;
      const result = await probe();
      if (!result.available) return null;
    }

    try {
      const parsed = await request(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: String(text || ''), context: context || null })
      }, PARSE_TIMEOUT);
      state.misses = 0;
      return parsed;
    } catch (error) {
      state.lastError = String(error.message || error);
      state.misses += 1;
      // Одна осечка бывает и у живого сервиса; три подряд — он умер.
      if (state.misses >= 3) {
        state.available = false;
        state.failedAt = Date.now();
      }
      return null;
    }
  }

  /**
   * Исправление пользователя («Не угадал?»). Копится на стороне сервиса и
   * попадает в корпус при следующем обучении.
   */
  /**
   * Поиск файла по имени в готовом индексе.
   *
   * Отличается от обхода дисков агентом двумя вещами, и обе важны. Он идёт по
   * заранее построенному индексу и укладывается в миллисекунды вместо секунд.
   * И сравнивает имена «мягко», поэтому русское название находит английский
   * файл: «клоуд мап» → Cloud map.
   *
   * @returns {Promise<object|null>} null — сервиса нет или индекс не построен
   */
  async function searchFiles(query, limit) {
    if (!state.available) return null;
    try {
      const url = '/files/search?q=' + encodeURIComponent(String(query || '')) +
                  '&limit=' + (limit || 25);
      return await request(url, { method: 'GET' }, 8000);
    } catch (error) {
      // 409 — индекс не построен. Это не поломка: агент отработает обходом.
      state.lastError = String(error.message || error);
      return null;
    }
  }

  async function feedback(text, intent) {
    if (!state.available) return false;
    try {
      await request('/nlu/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: String(text || ''), intent: String(intent || '') })
      }, PARSE_TIMEOUT);
      return true;
    } catch {
      return false;
    }
  }

  global.SpacyNLU = {
    probe, parse, plan, feedback, searchFiles, state, BASE,
    get available() { return state.available; }
  };
})(window);
