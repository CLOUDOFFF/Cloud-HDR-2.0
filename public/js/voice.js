/* ============================================================================
   Cloud HDR — постоянное прослушивание (окно приложения)
   ----------------------------------------------------------------------------
   Микрофон включён всегда, ключевое слово говорить не нужно. Приложение молчит
   до тех пор, пока не услышит ФРАЗУ-КОМАНДУ: «открой блокнот», «закрой хром»,
   «сделай скриншот». Разговор рядом, телевизор, музыка и шум проходят мимо.

   ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ И ПОЧЕМУ. Раньше в этом файле жил весь режим целиком:
   он брал микрофон через getUserMedia, отдавал звук в webkitSpeechRecognition,
   получал текст и сам решал, команда это или нет. Два обстоятельства заставили
   это переделать.

     1. Звук уходил наружу. webkitSpeechRecognition — не движок в браузере, а
        отправка записи в сервис Google. Это было единственное место, где
        приложение выходило за пределы компьютера, и в помощнике, который
        слушает комнату непрерывно, это самое неудачное место из возможных.

     2. В самом приложении оно не работало вовсе. Ярлык открывает окно на
        WebView2, а WebView2 распознавания речи не имеет — оно есть только в
        полном Chrome. То есть режим считался рабочим, а в приложении молчал
        всегда; заметить это было можно, только открыв приложение в браузере.

   Теперь слушает служба ears.ps1: свой захват микрофона, свои признаки, своё
   сопоставление, ничего наружу. Здесь остался тонкий клиент — показать
   состояние и дать выключатель.

   ПОЧЕМУ КОМАНДЫ ВЫПОЛНЯЕТ НЕ СТРАНИЦА. Служба бьёт узнанной фразой прямо в
   /api/quick — тем же путём, которым ходит быстрая строка. Так команда
   срабатывает примерно за треть секунды после последнего звука и работает,
   даже когда окно закрыто. Если бы выполняла страница, добавился бы лишний
   переход «служба → страница → агент», а закрытое окно означало бы глухоту.
   Поэтому onCommand отсюда НЕ вызывается: иначе команда выполнилась бы дважды.
   Страница узнаёт об услышанном и показывает это — не более.
   ========================================================================== */
(function (global) {
  'use strict';

  /* Названия чувствительности. Внутри приложения профили исторически зовутся
     по-английски, у службы — по-русски; таблица держит оба имени в одном
     месте, чтобы соответствие не пришлось искать по файлам. */
  const PROFILES = {
    strict: { sense: 'строгий', label: 'строгий' },
    normal: { sense: 'обычный', label: 'обычный' },
    open:   { sense: 'чуткий',  label: 'чуткий'  }
  };
  const BY_SENSE = {};
  Object.keys(PROFILES).forEach((key) => { BY_SENSE[PROFILES[key].sense] = key; });

  const state = {
    on: false,
    available: false,
    probed: false,
    profile: localStorage.getItem('cloudhdr.listen.profile') || 'normal',
    templates: 0,
    level: 0,
    floor: 0,
    heard: '',
    lastHeard: '',
    busy: false,
    timer: 0,
    reason: ''
  };

  const handlers = { command: [], state: [] };
  const on = (list, fn) => { if (typeof fn === 'function') list.push(fn); };
  const emit = (list, ...args) => list.forEach((fn) => {
    try { fn(...args); } catch (error) { console.error(error); }
  });

  function report(kind, data) {
    emit(handlers.state, Object.assign(
      { kind, on: state.on, profile: state.profile }, data || {}
    ));
  }

  /* ------------------------------------------------------------ опрос ---- */

  /*
     Два интервала, а не один.

     Когда режим включён, важно быстро показать услышанное — иначе человек
     говорит, компьютер уже выполняет, а строка состояния об этом молчит ещё
     секунду, и выглядит это как будто помощник тормозит. Когда режим выключен,
     опрашивать часто незачем: мы ждём только того, что службу подняли извне.
  */
  const FAST = 600;
  const SLOW = 3000;

  async function poll() {
    let data = null;
    try {
      data = await Agent.ears();
    } catch {
      data = null;
    }

    const wasOn = state.on;

    if (!data || !data.available) {
      state.available = false;
      state.on = false;
      state.reason = (data && data.reason) || 'Служба прослушивания не отвечает';
      if (wasOn) report('off', { why: state.reason });
      else if (!state.probed) report('idle', {});
    } else {
      state.available = true;
      state.on = Boolean(data.on);
      state.templates = data.templates || 0;
      state.level = data.level || 0;
      state.floor = data.floor || 0;
      if (data.sense && BY_SENSE[data.sense]) state.profile = BY_SENSE[data.sense];

      // «Услышано» приходит как последняя принятая фраза. Показываем её один
      // раз: без этой проверки одна команда мигала бы в строке состояния при
      // каждом опросе, пока её не сменит следующая.
      const heard = String(data.heard || '');
      if (heard && heard !== state.lastHeard) {
        state.lastHeard = heard;
        state.heard = heard;
        report('accepted', { heard, text: heard, why: 'услышано и выполнено' });
      } else if (state.on && !wasOn) {
        report('listening', {});
      } else if (state.on) {
        report('listening', { quiet: true });
      }
    }

    state.probed = true;
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, state.on ? FAST : SLOW);
  }

  /* ------------------------------------------------------- включение ----- */

  async function start() {
    if (state.on) return true;
    report('starting', {});
    try {
      await Agent.ears('on');
    } catch (error) {
      state.reason = error.message || 'Не удалось включить прослушивание';
      report('denied', { why: state.reason });
      return false;
    }

    // Служба поднимается пару секунд: ей нужно прочитать словарь и открыть
    // микрофон. Опрашиваем, пока не отзовётся, но недолго — если за восемь
    // секунд не поднялась, значит дело не в ожидании.
    for (let i = 0; i < 16; i++) {
      await new Promise((done) => setTimeout(done, 500));
      try {
        const data = await Agent.ears();
        if (data && data.available && data.on) {
          state.on = true;
          state.available = true;
          state.templates = data.templates || 0;
          localStorage.setItem('cloudhdr.listen', 'on');
          clearTimeout(state.timer);
          state.timer = setTimeout(poll, FAST);
          report('listening', {});
          return true;
        }
        if (data && data.reason) state.reason = data.reason;
      } catch { /* ещё поднимается */ }
    }
    report('denied', { why: state.reason || 'Микрофон недоступен' });
    return false;
  }

  async function stop(quiet) {
    state.on = false;
    localStorage.setItem('cloudhdr.listen', 'off');
    try { await Agent.ears('off'); } catch { /* уже не отвечает */ }
    report('off', { quiet: Boolean(quiet) });
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, SLOW);
  }

  /* -------------------------------------------------------- интерфейс ---- */

  global.Listener = {
    /* Служба есть всегда, когда есть агент: это его же файл, а не возможность
       браузера. Поэтому «поддерживается» здесь означает «агент отвечает», и
       сообщение «распознавание есть только в Chrome» больше не имеет смысла. */
    get supported() { return true; },
    get on() { return state.on; },
    get available() { return state.available; },
    get reason() { return state.reason; },
    get profile() { return state.profile; },
    get profileLabel() { return (PROFILES[state.profile] || PROFILES.normal).label; },
    get vocabularySize() { return state.templates; },

    start,
    stop,
    toggle() { return state.on ? (stop(), false) : (start(), true); },

    async setProfile(name) {
      if (!PROFILES[name]) return false;
      state.profile = name;
      localStorage.setItem('cloudhdr.listen.profile', name);
      if (state.on) {
        try { await Agent.ears('sense', PROFILES[name].sense); } catch { /* останется прежней */ }
      }
      report(state.on ? 'listening' : 'off', {});
      return true;
    },

    /* Словарь теперь живёт в службе и собирается из имён программ ЭТОГО
       компьютера при её запуске. Метод оставлен, чтобы не ломать вызов из
       app.js, и намеренно ничего не делает: вторая точка правды о словаре —
       ровно та ошибка, ради ухода от которой всё и переносилось в службу. */
    setVocabulary() { return false; },

    /** Приложение занято — служба про это знать не обязана, но UI пусть знает. */
    busy(flag) { state.busy = Boolean(flag); },

    /*
       Заглушить на время, пока говорит сама страница.

       Служба глушит себя, когда отвечает своим голосом, но озвучивать ответы
       умеет и страница — через /api/tts и колонки. Для микрофона это
       неотличимо от человека в комнате, и без заглушки помощник принимал бы
       собственный ответ за новую команду.
    */
    mute(ms) {
      if (!state.on) return;
      Agent.ears('mute', String(Math.max(200, ms || 2000))).catch(() => {});
    },
    unmute() {
      if (!state.on) return;
      Agent.ears('unmute').catch(() => {});
    },

    level() {
      return { floor: state.floor, peak: state.level, ready: state.available };
    },

    onCommand(fn) { on(handlers.command, fn); },
    onState(fn) { on(handlers.state, fn); }
  };

  // Опрос начинается сразу: строка состояния должна показывать правду ещё до
  // того, как кто-нибудь нажмёт выключатель. Служба могла быть поднята вместе
  // с приложением — тогда режим уже включён, и «Включить» на кнопке было бы
  // враньём.
  poll();
})(window);
