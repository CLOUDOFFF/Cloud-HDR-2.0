/* ============================================================================
   Cloud HDR — экстренная помощь
   ----------------------------------------------------------------------------
   Человек заранее говорит: «вот слово, вот что по нему открыть». Дальше это
   слово — прямая кнопка. Сказал или написал — открылось. Без разбора фразы,
   без размышления, без подтверждения, без расхода токенов.

   Почему это отдельный модуль, а не ещё одно правило в разборе.

   Обычный путь запроса — это очередь: списать токены, показать размышление,
   прогнать через нейросеть и spaCy, свериться со словарями, дождаться агента.
   Полторы-две секунды, и каждая ступень может отказать: сеть не уверена,
   словарь не знает названия, гейт микрофона счёл фразу не командой. Для
   обычного «открой хром» это правильно — ошибиться там дешевле, чем открыть
   лишнее. Для экстренной фразы всё наоборот: она названа заранее, вручную, и
   единственное её назначение — сработать. Здесь любая ступень разбора это
   лишний шанс НЕ сработать.

   Поэтому проверка стоит первой: до бюджета токенов, до NLU, до гейта
   микрофона. Совпало — выполняем.

   Отдельно про микрофон. Постоянное прослушивание намеренно строгое: оно
   пропускает только фразы в форме поручения с узнанной целью. «Мне плохо» не
   проходит по всем трём признакам сразу — это не команда, у неё нет цели, и
   формы поручения у неё нет. Именно поэтому экстренные фразы проверяются ДО
   гейта и минуют его целиком: смысл настройки в том, чтобы сработало слово,
   которое человек выбрал сам, а не то, которое одобрил фильтр.
   ========================================================================== */
(function (global) {
  'use strict';

  const STORAGE = 'cloudhdr.sos.v1';

  /* ------------------------------------------------------------ хранение -- */

  const state = {
    /** [{id, phrase, action:{intent,target,query,title,icon}, note, hits, lastAt}] */
    rules: [],
    enabled: true,
    /** Озвучивать ли срабатывание — здесь отдельно от общей настройки голоса. */
    announce: true
  };

  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE) || '{}');
      state.rules = Array.isArray(raw.rules) ? raw.rules.filter((rule) => rule && rule.phrase && rule.action) : [];
      state.enabled = raw.enabled !== false;
      state.announce = raw.announce !== false;
    } catch {
      state.rules = [];
    }
    return state.rules;
  }

  function save() {
    try {
      localStorage.setItem(STORAGE, JSON.stringify({
        rules: state.rules, enabled: state.enabled, announce: state.announce
      }));
    } catch { /* переполнено — не критично */ }
    notify();
  }

  const listeners = [];
  const notify = () => listeners.forEach((fn) => { try { fn(state.rules); } catch (error) { console.error(error); } });

  /* -------------------------------------------------------- сравнение ----- */

  const norm = (value) => (global.NLU && NLU.normalize)
    ? NLU.normalize(value)
    : String(value == null ? '' : value).toLowerCase().replace(/ё/g, 'е')
        .replace(/[!?.,;:«»"'`]+/g, ' ').replace(/\s+/g, ' ').trim();

  const bare = (value) => norm(value).replace(/[^\p{L}\p{Nd}\s]/gu, '').replace(/\s+/g, ' ').trim();

  /**
   * Совпала ли фраза.
   *
   * Три степени, и они перечислены в порядке убывания уверенности:
   *
   *   1. точное совпадение — человек сказал ровно то, что настроил;
   *   2. фраза целиком входит в сказанное — «код красный» внутри «так, код
   *      красный, быстро». В панике говорят не teleграфно, и требовать
   *      дословности значило бы, что настройка работает только в спокойном
   *      состоянии, то есть тогда, когда она не нужна;
   *   3. одна опечатка или ослышка на слово — «код красны», «кот красный».
   *      Порог по длине: на коротких словах одна буква меняет смысл целиком.
   *
   * Вхождение требует границ слова: иначе фраза «жар» срабатывала бы на
   * «пожар», «жаркое» и «пожарь картошку».
   */
  function matches(rule, said) {
    const needle = bare(rule.phrase);
    const haystack = bare(said);
    if (!needle || !haystack) return 0;

    if (haystack === needle) return 1;

    const bounded = new RegExp('(^|\\s)' + needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '($|\\s)', 'u');
    if (bounded.test(haystack)) return 0.9;

    // Опечатки ищем только на фразе целиком и только когда длины сопоставимы:
    // сравнивать «сос» с длинным предложением бессмысленно.
    if (needle.length >= 5 && Math.abs(haystack.length - needle.length) <= 3) {
      const distance = (global.NLU && NLU.levenshtein) ? NLU.levenshtein(needle, haystack) : 99;
      const allowed = needle.length >= 12 ? 2 : 1;
      if (distance <= allowed) return 0.75;
    }
    return 0;
  }

  /**
   * Ищет правило, подходящее к сказанному.
   * @returns {{rule: object, score: number}|null}
   */
  function find(said) {
    if (!state.enabled || !state.rules.length) return null;
    let best = null;
    for (const rule of state.rules) {
      const score = matches(rule, said);
      // При равенстве побеждает более длинная фраза: «код красный два» точнее
      // «код красный», и выбирать надо конкретное правило, а не первое.
      if (score && (!best || score > best.score ||
          (score === best.score && rule.phrase.length > best.rule.phrase.length))) {
        best = { rule, score };
      }
    }
    return best;
  }

  /* ------------------------------------------------------ что открывать --- */

  /*
     Человек пишет, что открыть, обычными словами: «хром», «папку загрузки»,
     «https://...», «D:\игры», «телеграм». Разбирается это здесь и ОДИН раз —
     при сохранении правила, а не при срабатывании.

     Так сделано ради времени: разбор со словарями и нечётким сравнением стоит
     миллисекунды, но требует, чтобы NLU был загружен и словари построены. В
     момент, ради которого вся эта функция и написана, лучше иметь готовый
     ответ, чем быстрый способ его получить.
  */
  function resolve(rawTarget) {
    const raw = String(rawTarget == null ? '' : rawTarget).trim();
    if (!raw) return null;

    // Путь и адрес узнаются по двоеточию, слэшу и точке — до нормализации,
    // которая эти знаки снимает.
    if (global.NLU && NLU.PATH_RE && NLU.PATH_RE.test(raw)) {
      return { intent: 'open_folder', target: raw, title: raw, icon: 'folder', label: 'папка ' + raw };
    }
    if (/^(https?:\/\/|www\.)/i.test(raw) || (global.NLU && NLU.URL_RE && NLU.URL_RE.test(raw))) {
      const url = /^https?:\/\//i.test(raw) ? raw : 'https://' + raw.replace(/^\/+/, '');
      return { intent: 'open_url', target: url, title: url.replace(/^https?:\/\//, ''), icon: 'globe', label: 'сайт ' + url };
    }

    const text = norm(raw).replace(/^(открой|открыть|запусти|запустить|включи)\s+/u, '').trim();
    if (!text) return null;

    if (global.NLU && NLU.matchDictionary) {
      const folderText = text.replace(/^папк[уаи]\s+/u, '');
      const folder = NLU.matchDictionary(folderText, NLU.FOLDER_ALIASES);
      if (folder && folder.score >= 0.8 && /^папк/u.test(text)) {
        return { intent: 'open_folder', target: folder.key, title: NLU.FOLDER_TITLES[folder.key],
          icon: 'folder', label: 'папка «' + NLU.FOLDER_TITLES[folder.key] + '»' };
      }

      const app = NLU.matchDictionary(text, NLU.APP_ALIASES);
      if (app && app.score >= 0.8) {
        return { intent: 'open_app', target: app.key, title: NLU.APP_TITLES[app.key],
          icon: NLU.APP_ICONS[app.key] || 'app', label: 'программа «' + NLU.APP_TITLES[app.key] + '»' };
      }

      const site = NLU.matchDictionary(text, NLU.SITE_ALIASES);
      if (site && site.score >= 0.85) {
        return { intent: 'open_url', target: NLU.SITE_URLS ? NLU.SITE_URLS[site.key] : site.key,
          title: NLU.SITE_TITLES[site.key], icon: 'globe', label: 'сайт «' + NLU.SITE_TITLES[site.key] + '»' };
      }

      if (folder && folder.score >= 0.9) {
        return { intent: 'open_folder', target: folder.key, title: NLU.FOLDER_TITLES[folder.key],
          icon: 'folder', label: 'папка «' + NLU.FOLDER_TITLES[folder.key] + '»' };
      }
    }

    // Ничего не узнали — отдаём агенту как есть. Он умеет искать по всему
    // установленному на этом компьютере, а не только по словарю из двух
    // десятков известных программ.
    return { intent: 'open_app', target: raw, title: raw, icon: 'app', label: '«' + raw + '» среди установленного' };
  }

  /* ------------------------------------------------------------ правила --- */

  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  /**
   * Добавляет или обновляет правило.
   * @returns {{ok: boolean, rule?: object, error?: string}}
   */
  function put(phrase, target, options = {}) {
    const clean = String(phrase || '').trim();
    if (clean.length < 2) return { ok: false, error: 'Фраза слишком короткая — нужно хотя бы два знака' };
    if (clean.length > 60) return { ok: false, error: 'Фраза слишком длинная — её будет трудно выговорить' };

    const action = resolve(target);
    if (!action) return { ok: false, error: 'Не понял, что открывать. Например: «хром», «папку загрузки», «https://…»' };

    const key = bare(clean);
    const twin = state.rules.find((rule) => bare(rule.phrase) === key && rule.id !== options.id);
    if (twin) return { ok: false, error: `Такая фраза уже настроена — на ${twin.action.label || twin.action.title}` };

    const existing = options.id ? state.rules.find((rule) => rule.id === options.id) : null;
    if (existing) {
      existing.phrase = clean;
      existing.action = action;
      existing.raw = String(target).trim();
      save();
      return { ok: true, rule: existing };
    }

    const rule = { id: uid(), phrase: clean, raw: String(target).trim(), action, hits: 0, lastAt: 0, created: Date.now() };
    state.rules.push(rule);
    save();
    return { ok: true, rule };
  }

  function remove(id) {
    const before = state.rules.length;
    state.rules = state.rules.filter((rule) => rule.id !== id);
    if (state.rules.length !== before) save();
    return state.rules.length !== before;
  }

  function markFired(rule) {
    rule.hits = (rule.hits || 0) + 1;
    rule.lastAt = Date.now();
    save();
  }

  /* ------------------------------------------------------------ интерфейс - */

  global.SOS = {
    get rules() { return state.rules.slice(); },
    get enabled() { return state.enabled; },
    get announce() { return state.announce; },
    get count() { return state.rules.length; },
    /** Фразы для гейта микрофона: их надо пропускать мимо всех фильтров. */
    get phrases() { return state.enabled ? state.rules.map((rule) => rule.phrase) : []; },

    load, save, put, remove, find, matches, resolve, markFired,

    setEnabled(on) { state.enabled = Boolean(on); save(); return state.enabled; },
    setAnnounce(on) { state.announce = Boolean(on); save(); return state.announce; },

    onChange(fn) { if (typeof fn === 'function') listeners.push(fn); }
  };

  load();
})(window);
