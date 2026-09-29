/* ============================================================================
   Cloud HDR — руки: управление мышью из окна приложения
   ----------------------------------------------------------------------------
   Слово «curs» в строке ввода открывает разрешение, после которого помощник
   водит настоящим курсором: открывает Проводник, листает, находит папки,
   рисует в Paint. При включённом постоянном прослушивании то же самое делается
   голосом — распознанная фраза приходит сюда же, потому что и голос, и
   клавиатура сходятся в одной функции send().

   ГДЕ НА САМОМ ДЕЛЕ ЖИВУТ РУКИ. Не здесь и не в агенте, а в спутнике — том
   процессе, что рисует панель по Ctrl+Alt+P. Страница в браузере мышью
   управлять не может и не должна: это работа для программы на этом
   компьютере. Отсюда уходит только просьба, а всё остальное — движение
   курсора, разбор чужих окон, остановка по Escape — делается там.

   Агент между ними — почтальон: складывает просьбу в файл, отдаёт обратно то,
   что спутник написал о себе. Никаких новых портов и служб ради этого не
   заведено.
   ========================================================================== */
(function (global) {
  'use strict';

  const AGENT_PORT = 4477;
  const BASE = location.protocol.startsWith('http') && location.port === String(AGENT_PORT)
    ? ''
    : `http://127.0.0.1:${AGENT_PORT}`;

  /* Слова, которыми включают и выключают режим. Латинское «curs» — основное;
     остальные добавлены потому, что по-русски его наберут как «курс», а
     голосом продиктуют как «мышь». */
  const CALL = /^\s*(curs|cursor|курс|курсор|мышь|мышка|руки)\s*$/i;
  const CALL_WITH_TASK = /^\s*(?:curs|cursor|курс|курсор(?:ом)?|мышк(?:ой|а)|руками)[\s,]+(.{2,})$/i;
  const QUIT = /^\s*(выход|хватит управлять|отключи курсор|верни мышь|обычный режим)\s*$/i;
  const HALT = /^\s*(стоп|стой|хватит|остановись|отмена|прекрати)\s*$/i;

  /*
     Что считать поручением рукам.

     Выданное разрешение не превращает помощника в одни только руки: на «как
     дела» он обязан отвечать, а не сообщать, что не умеет такого делать.
     Поэтому в режиме рук перехватываются лишь фразы, начинающиеся с глагола
     действия, — остальное идёт обычным путём, к разбору и модели.

     Список нарочно короткий и состоит из начал фраз. Полный разбор живёт на той
     стороне, в спутнике, и повторять его здесь было бы обманом: две копии
     правил разойдутся в первый же день. Если что-то сюда не попало, помогает
     явное «curs открой…» — оно уходит рукам без всяких проверок.
  */
  const ACTION = new RegExp('^\\s*(?:' + [
    'открой', 'открыть', 'запусти', 'включи',
    'нарисуй', 'рисуй', 'изобрази',
    'полистай', 'пролистай', 'прокрути', 'листай', 'промотай',
    'найди', 'найти', 'поищи',
    'нажми', 'жми', 'кликни', 'щёлкни', 'щелкни', 'тапни',
    'напиши', 'напечатай', 'введи', 'набери', 'печатай',
    'наведи', 'подведи', 'перемести',
    'подожди', 'погоди',
    'сверни', 'двойной клик', 'правой кнопкой', 'сделай скриншот', 'сними экран',
    'зайди', 'перейди', 'выбери', 'поставь', 'сделай', 'продолжи', 'закрой', 'скопируй', 'вставь', 'сохрани'
  ].join('|') + ')(?![а-яёa-z0-9_])', 'i');

  const state = {
    on: false,          // разрешение выдано
    running: false,     // прямо сейчас работает
    queued: '',         // поручение, ждущее разрешения
    timer: null,
    lastSay: '',
    steps: []
  };

  /* --------------------------------------------------------------- связь -- */

  async function ask(path, options = {}) {
    const token = (global.Agent && Agent.state && Agent.state.token) || null;
    const response = await fetch(BASE + path, {
      method: options.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-Cloud-Token': token } : {})
      },
      body: options.body ? JSON.stringify(options.body) : undefined
    });
    return response.json().catch(() => ({ ok: false }));
  }

  const status = () => ask('/api/cursor');
  const order = (action, text) => ask('/api/cursor', { method: 'POST', body: { action, text: text || '' } });

  /* ------------------------------------------------------------ разметка -- */
  //
  // Стили и узлы создаются кодом, а не лежат в index.html. Причина простая:
  // всё, что относится к рукам, собрано в одном файле — его можно удалить
  // целиком, и приложение останется прежним.

  const CSS = `
  .hands-veil{position:fixed;inset:0;background:rgba(4,6,12,.72);backdrop-filter:blur(6px);
    display:flex;align-items:center;justify-content:center;z-index:9000;animation:handsIn .18s ease-out}
  .hands-ask{width:min(440px,92vw);background:#0a0a0e;border:1px solid #3b2d12;border-radius:18px;
    padding:22px 22px 18px;box-shadow:0 30px 80px rgba(0,0,0,.6),0 0 40px rgba(240,169,60,.14)}
  .hands-ask h3{margin:0 0 10px;font-size:17px;color:#ffe0b0;display:flex;align-items:center;gap:10px}
  .hands-ask h3 svg{width:17px;height:17px;fill:#ffc469}
  .hands-ask p{margin:0;color:#a9b4c8;font-size:13.5px;line-height:1.5}
  .hands-ask ul{margin:12px 0 0;padding:0;list-style:none}
  .hands-ask li{color:#7e8ca6;font-size:12.5px;line-height:1.9}
  .hands-ask li::before{content:'•';color:#f0a93c;margin-right:8px}
  .hands-row{display:flex;gap:9px;margin-top:18px}
  .hands-btn{border:0;border-radius:12px;padding:11px 17px;font-size:13.5px;font-weight:600;cursor:pointer;
    font-family:inherit;transition:filter .15s,transform .08s}
  .hands-btn:active{transform:scale(.97)}
  .hands-yes{background:linear-gradient(180deg,#ffd79a,#f0a93c);color:#08101f;box-shadow:0 8px 24px rgba(240,169,60,.3)}
  .hands-yes:hover{filter:brightness(1.06)}
  .hands-no{background:#0c1018;color:#8fa0c0;border:1px solid #1e2739}
  .hands-no:hover{background:#141b28}

  .hands-hud{position:fixed;right:18px;bottom:18px;width:min(360px,90vw);z-index:8800;
    background:rgba(7,8,12,.96);border:1px solid #1c2434;border-radius:18px;padding:14px 15px;
    box-shadow:0 24px 60px rgba(0,0,0,.55);animation:handsIn .2s ease-out;font-size:13px}
  .hands-hud[hidden]{display:none}
  .hands-head{display:flex;align-items:center;gap:9px;margin-bottom:10px}
  .hands-dot{width:8px;height:8px;border-radius:50%;background:#f0a93c;flex:0 0 auto}
  .hands-hud.idle .hands-dot{background:#4ade80;animation:none}
  .hands-hud.work .hands-dot{animation:handsPulse .9s ease-in-out infinite}
  .hands-title{color:#dce6fa;font-weight:600;font-size:13px;flex:1}
  .hands-time{color:#f0a93c;font-size:11px;background:#17110a;border:1px solid #4a3617;
    border-radius:8px;padding:3px 8px}
  .hands-track{height:3px;border-radius:2px;background:#0e1420;overflow:hidden;margin-bottom:10px}
  .hands-fill{height:100%;width:0;border-radius:2px;background:linear-gradient(90deg,#f0a93c,#ffd79a);
    transition:width .3s ease-out}
  .hands-steps{max-height:168px;overflow:auto;margin:0;padding:0;list-style:none}
  .hands-steps li{display:flex;gap:10px;color:#b9c6de;font-size:11.5px;line-height:1.5;padding:2px 0}
  .hands-steps li span{color:#46536e;flex:0 0 auto}
  .hands-steps li.good{color:#4ade80}
  .hands-steps li.warn{color:#f87171}
  .hands-foot{display:flex;gap:8px;margin-top:11px}
  .hands-foot .hands-btn{flex:1;padding:8px 12px;font-size:12px}
  @keyframes handsIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  @keyframes handsPulse{0%,100%{opacity:1}50%{opacity:.25}}`;

  let hud = null;
  let hudSteps = null;
  let hudTitle = null;
  let hudTime = null;
  let hudFill = null;
  let hudStop = null;

  function styles() {
    if (document.getElementById('hands-css')) return;
    const tag = document.createElement('style');
    tag.id = 'hands-css';
    tag.textContent = CSS;
    document.head.appendChild(tag);
  }

  function buildHud() {
    if (hud) return hud;
    styles();
    hud = document.createElement('div');
    hud.className = 'hands-hud work';
    hud.hidden = true;
    hud.innerHTML = `
      <div class="hands-head">
        <i class="hands-dot"></i>
        <span class="hands-title">Работаю руками</span>
        <span class="hands-time">30 мин</span>
      </div>
      <div class="hands-track"><div class="hands-fill"></div></div>
      <ul class="hands-steps"></ul>
      <div class="hands-foot">
        <button class="hands-btn hands-no" data-hands="stop">Стоп</button>
        <button class="hands-btn hands-no" data-hands="off">Забрать мышь</button>
      </div>`;
    document.body.appendChild(hud);

    hudSteps = hud.querySelector('.hands-steps');
    hudTitle = hud.querySelector('.hands-title');
    hudTime = hud.querySelector('.hands-time');
    hudFill = hud.querySelector('.hands-fill');
    hudStop = hud.querySelector('[data-hands="stop"]');

    hudStop.onclick = () => order('stop');
    hud.querySelector('[data-hands="off"]').onclick = () => revoke();
    return hud;
  }

  /* Окно разрешения. Обещание возвращается, чтобы вызвавший мог дождаться
     решения человека и только потом отправлять поручение. */
  function askPermission() {
    styles();
    return new Promise((resolve) => {
      const veil = document.createElement('div');
      veil.className = 'hands-veil';
      veil.innerHTML = `
        <div class="hands-ask" role="dialog" aria-modal="true">
          <h3>
            <svg viewBox="0 0 12 17"><path d="M0 0 L0 13.5 L3.4 10.6 L5.6 15.4 L8 14.2 L5.8 9.6 L10 9.2 Z"/></svg>
            Разрешить управлять мышью?
          </h3>
          <p>Cloud HDR будет водить курсор, нажимать, крутить колесо и печатать — так же, как это делаете вы.</p>
          <ul>
            <li>Разрешение действует 30 минут, потом гаснет само</li>
            <li>Escape в любую секунду прекращает работу</li>
            <li>Возьмитесь за мышь — управление сразу вернётся к вам</li>
          </ul>
          <div class="hands-row">
            <button class="hands-btn hands-yes">Разрешить</button>
            <button class="hands-btn hands-no">Не сейчас</button>
          </div>
        </div>`;

      const close = (answer) => { veil.remove(); document.removeEventListener('keydown', onKey); resolve(answer); };
      const onKey = (event) => {
        if (event.key === 'Escape') close(false);
        if (event.key === 'Enter') close(true);
      };

      veil.querySelector('.hands-yes').onclick = () => close(true);
      veil.querySelector('.hands-no').onclick = () => close(false);
      veil.onclick = (event) => { if (event.target === veil) close(false); };
      document.addEventListener('keydown', onKey);
      document.body.appendChild(veil);
      veil.querySelector('.hands-yes').focus();
    });
  }

  /* ------------------------------------------------------------ обновление - */

  function paint(data) {
    if (!hud) return;
    const steps = data.steps || [];
    state.running = Boolean(data.running);
    state.on = Boolean(data.granted);

    hud.classList.toggle('work', state.running);
    hud.classList.toggle('idle', !state.running);
    hudStop.hidden = !state.running;

    const minutes = Math.max(0, Math.ceil((data.seconds || 0) / 60));
    hudTime.textContent = state.on ? `${minutes} мин` : 'выключено';

    if (state.running) {
      const total = data.total || 0;
      hudTitle.textContent = total
        ? `Работаю руками · шаг ${Math.min(total, (data.done || 0) + 1)} из ${total}`
        : 'Работаю руками';
      hudFill.style.width = total ? `${Math.min(100, ((data.done || 0) / total) * 100)}%` : '0';
    } else if (data.say) {
      hudTitle.textContent = data.success === false ? 'Не вышло' : 'Готово';
      hudFill.style.width = '100%';
    } else {
      hudTitle.textContent = 'Мышь в моих руках';
      hudFill.style.width = '0';
    }

    // Список перерисовывается только когда шагов стало больше: он обновляется
    // дважды в секунду, а перебор узлов на каждом такте заметно дёргает текст
    // под курсором.
    if (steps.length !== state.steps.length) {
      state.steps = steps;
      hudSteps.innerHTML = steps.slice(-8).map((step) => {
        const kind = step.kind === 'good' || step.kind === 'warn' ? step.kind : '';
        return `<li class="${kind}"><span>${step.at || ''}</span>${escape(step.text || '')}</li>`;
      }).join('');
      hudSteps.scrollTop = hudSteps.scrollHeight;
    }

    // Итог проговаривается вслух один раз — тем же голосом, которым помощник
    // отвечает на всё остальное.
    if (!state.running && data.say && data.say !== state.lastSay) {
      state.lastSay = data.say;
      if (global.Voice && data.say) Voice.speak(data.say);
    }
  }

  function escape(text) {
    return String(text).replace(/[&<>]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch]));
  }

  function watch() {
    if (state.timer) return;
    state.timer = setInterval(async () => {
      const data = await status().catch(() => null);
      if (!data || !data.available) { hide(); return; }
      if (!data.granted && !data.running) {
        // Разрешение погасло само — панель об этом уже знает, окно тоже должно.
        state.on = false;
        paint(data);
        setTimeout(hide, 2500);
        return;
      }
      paint(data);
    }, 600);
  }

  function hide() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.on = false;
    state.running = false;
    state.steps = [];
    if (hud) hud.hidden = true;
  }

  async function revoke() {
    await order('revoke').catch(() => null);
    hide();
  }

  /* Просьба выдать разрешение и, если оно дано, сразу поручить дело. */
  async function turnOn(task) {
    const check = await status().catch(() => null);
    if (!check || !check.available) {
      alert(check && check.reason ? check.reason : 'Панель Cloud HDR не запущена — откройте приложение ярлыком.');
      return false;
    }

    const yes = await askPermission();
    if (!yes) return false;

    const granted = await order('grant');
    if (!granted.ok) { alert(granted.error || 'Не удалось выдать разрешение'); return false; }

    state.on = true;
    state.lastSay = '';
    buildHud().hidden = false;
    watch();

    if (task) { await order('task', task); }
    return true;
  }

  async function run(text) {
    if (state.running && !HALT.test(text)) {
      buildHud().hidden = false;
      hudTitle.textContent = 'Занят прошлым поручением';
      return;
    }
    state.lastSay = '';
    buildHud().hidden = false;
    watch();
    const sent = await order('task', text);
    if (!sent.ok) alert(sent.error || 'Поручение не ушло');
  }

  /*
     Точка входа из общего потока сообщений.

     Возвращает true, если фраза относится к рукам и обычному разбору её
     показывать не нужно. Стоит в самом начале send(), до расхода токенов и до
     нейросети: в режиме рук «открой проводник» — это не вопрос помощнику, а
     работа, и толковать её второй раз незачем.
  */
  function intercept(rawText) {
    const text = String(rawText || '').trim();
    if (!text) return false;

    if (CALL.test(text)) {
      if (state.on) { revoke(); } else { turnOn(''); }
      return true;
    }

    const withTask = text.match(CALL_WITH_TASK);
    if (withTask) {
      if (state.on) { run(withTask[1].trim()); } else { turnOn(withTask[1].trim()); }
      return true;
    }

    if (!state.on) return false;

    if (QUIT.test(text)) { revoke(); return true; }
    if (HALT.test(text)) { order('stop'); return true; }
    if (!ACTION.test(text)) return false;      // это разговор, а не поручение

    run(text);
    return true;
  }

  global.CursorHands = {
    intercept,
    turnOn,
    revoke,
    run,
    get on() { return state.on; },
    get running() { return state.running; }
  };
})(window);
