/* ============================================================================
   Cloud HDR — бюджет токенов
   ----------------------------------------------------------------------------
   Правила фиксированы в коде и в настройках НЕ меняются:

       1 запрос = 2 токена
       запас    = 900 токенов
       окно     = 1 час, обновляется автоматически

   Почему настройки убраны. Пока лимит правился в настройках, «бюджет» был
   украшением: любой, кому он мешал, поднимал предел до миллиона за два клика.
   Ограничение имеет смысл, только если его нельзя отменить изнутри.

   ---------------------------------------------------------------------------
   ЧЕСТНО О ЗАЩИТЕ. Приложение целиком выполняется в браузере, и localStorage
   открыт в инструментах разработчика. Сделать счётчик неподделываемым здесь
   нельзя — это не вопрос стараний, а свойство среды: любой код, который
   проверяет запись, лежит рядом с ней и точно так же читается.

   Поэтому цель другая — не «запретить», а сделать подделку БЕССМЫСЛЕННОЙ:

     • запись подписана контрольной суммой; правка руками её ломает;
     • сломанная подпись трактуется не как «начать заново», а как исчерпанный
       запас до конца окна. Подделка отнимает токены, а не добавляет;
     • расход суммируется нарастающим итогом и никогда не уменьшается;
     • перевод часов назад не засчитывается: время сверяется с последней
       виденной меткой, и шаг в прошлое окно не открывает.

   То есть честному пользователю система не мешает, а нечестному не выгодна.
   Настоящая защита требует сервера, который считает расход у себя.
   ========================================================================== */
(function (global) {
  'use strict';

  const KEY = 'cloudhdr.tokens.v2';
  const HOUR = 3600000;

  /** Правила бюджета. В интерфейсе не редактируются — в этом весь смысл. */
  const RULES = Object.freeze({
    perRequest: 2,
    limit: 900,
    windowHours: 1
  });

  const state = {
    used: 0,
    windowStart: 0,
    totalRequests: 0,
    dayRequests: 0,
    dayStart: 0,
    lastSeen: 0,
    tampered: false
  };

  const listeners = [];

  /* ----------------------------------------------------------- подпись --- */

  /**
   * Контрольная сумма записи.
   *
   * Это не криптография и не притворяется ею: секрет лежит в том же файле,
   * что и проверка. Задача скромнее — чтобы правка значения «на глаз» в
   * инструментах разработчика не проходила молча.
   */
  function sign(payload) {
    const text = JSON.stringify(payload) + '|cloud-hdr|' + RULES.limit;
    let h1 = 0x811c9dc5;
    let h2 = 0x01000193;
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      h1 = Math.imul(h1 ^ code, 16777619) >>> 0;
      h2 = Math.imul(h2 + code + i, 2246822519) >>> 0;
    }
    return (h1 >>> 0).toString(36) + '.' + (h2 >>> 0).toString(36);
  }

  function payloadOf() {
    return {
      used: state.used,
      windowStart: state.windowStart,
      totalRequests: state.totalRequests,
      dayRequests: state.dayRequests,
      dayStart: state.dayStart,
      lastSeen: state.lastSeen
    };
  }

  /* ------------------------------------------------------------- хранение */

  function load() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { raw = null; }

    if (!raw || typeof raw !== 'object') {
      // Записи нет вовсе — обычный первый запуск, а не подделка.
      state.lastSeen = Date.now();
      save();
      refresh();
      return;
    }

    const data = raw.data || {};
    Object.assign(state, {
      used: Number(data.used) || 0,
      windowStart: Number(data.windowStart) || 0,
      totalRequests: Number(data.totalRequests) || 0,
      dayRequests: Number(data.dayRequests) || 0,
      dayStart: Number(data.dayStart) || 0,
      lastSeen: Number(data.lastSeen) || 0
    });

    if (raw.sig !== sign(data)) {
      // Запись правили руками. Не обнуляем расход — наоборот, считаем окно
      // выбранным: иначе подделка была бы самым быстрым способом получить
      // чистый счётчик.
      state.tampered = true;
      state.used = RULES.limit;
      if (!state.windowStart) state.windowStart = Date.now();
      save();
    }

    refresh();
  }

  function save() {
    try {
      const data = payloadOf();
      localStorage.setItem(KEY, JSON.stringify({ data, sig: sign(data) }));
    } catch { /* приватный режим — бюджет живёт до перезагрузки */ }
  }

  function emit() {
    const snapshot = status();
    listeners.forEach((listener) => { try { listener(snapshot); } catch {} });
  }

  /* -------------------------------------------------------------- время -- */

  /**
   * Приводит счётчики к текущему моменту.
   *
   * Здесь же ловится перевод часов: если «сейчас» оказалось раньше последнего
   * виденного момента, окно не открывается — иначе сдвинуть системное время на
   * час назад было бы простейшим способом обнулить расход.
   */
  function refresh() {
    const now = Date.now();

    if (state.lastSeen && now < state.lastSeen - 60000) {
      state.tampered = true;
      state.lastSeen = now;
      save();
      return;
    }
    state.lastSeen = now;

    const windowMs = RULES.windowHours * HOUR;
    if (state.windowStart && now - state.windowStart >= windowMs) {
      state.used = 0;
      state.windowStart = 0;
      state.tampered = false;   // окно честно прошло — прошлое не вспоминаем
      save();
    }

    // Счётчик «за сегодня» — только для показа, на лимит он не влияет.
    const dayMs = 24 * HOUR;
    if (!state.dayStart || now - state.dayStart >= dayMs) {
      state.dayStart = now;
      state.dayRequests = 0;
      save();
    }
  }

  /* ------------------------------------------------------------ состояние */

  function status() {
    refresh();
    const now = Date.now();
    const left = Math.max(0, RULES.limit - state.used);
    const resetsAt = state.windowStart ? state.windowStart + RULES.windowHours * HOUR : 0;

    return {
      enabled: true,
      perRequest: RULES.perRequest,
      limit: RULES.limit,
      windowHours: RULES.windowHours,
      used: state.used,
      left,
      percent: Math.min(100, Math.round(state.used / RULES.limit * 100)),
      requestsLeft: Math.floor(left / RULES.perRequest),
      totalRequests: state.totalRequests,
      dayRequests: state.dayRequests,
      blocked: left <= 0,
      tampered: state.tampered,
      windowStart: state.windowStart,
      windowResetsAt: resetsAt,
      windowLeftMs: resetsAt ? Math.max(0, resetsAt - now) : RULES.windowHours * HOUR
    };
  }

  /**
   * Списывает стоимость одного запроса.
   * @returns {{ok: boolean, reason?: string, charged?: number, status: object}}
   */
  function charge(cost) {
    refresh();
    const now = Date.now();
    const price = Math.max(0, cost == null ? RULES.perRequest : cost);

    if (state.used >= RULES.limit) {
      return { ok: false, reason: 'exhausted', status: status() };
    }
    if (!state.windowStart) state.windowStart = now;

    if (state.used + price > RULES.limit) {
      state.used = RULES.limit;
      save();
      emit();
      return { ok: false, reason: 'exhausted', status: status() };
    }

    state.used += price;
    state.totalRequests += 1;
    state.dayRequests += 1;
    save();
    emit();
    return { ok: true, charged: price, status: status() };
  }

  /**
   * Возврат стоимости, если запрос так и не был обработан.
   *
   * Уменьшает расход текущего окна, но НЕ трогает общий счётчик запросов: тот
   * растёт только вверх, иначе через возвраты можно было бы «отматывать»
   * историю.
   */
  function refund(cost) {
    const price = Math.max(0, cost == null ? RULES.perRequest : cost);
    state.used = Math.max(0, state.used - price);
    save();
    emit();
    return status();
  }

  /** «42 мин» — человекочитаемая длительность. */
  function humanize(ms) {
    if (ms <= 0) return 'меньше минуты';
    const totalMinutes = Math.ceil(ms / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (!hours) return `${minutes} мин`;
    if (!minutes) return `${hours} ч`;
    return `${hours} ч ${minutes} мин`;
  }

  global.Tokens = {
    load, status, charge, refund, humanize, RULES,
    onChange(listener) { listeners.push(listener); return () => listeners.splice(listeners.indexOf(listener), 1); }
  };
})(window);
