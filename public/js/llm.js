/* ============================================================================
   Cloud HDR — мост к СВОЕЙ языковой модели
   ----------------------------------------------------------------------------
   Никаких облаков, ключей и аккаунтов. Здесь только HTTP-запрос к программе,
   которая крутится на ЭТОМ же компьютере, — ровно так же, как Cloud HDR уже
   разговаривает со своим агентом на 127.0.0.1. Наружу не уходит ничего.

   Раньше мост искал чужие движки (Ollama, LM Studio, llama.cpp) на трёх портах.
   Теперь адрес один — 8080, где поднимается собственная модель проекта
   (python/cloudhdr_ai). Смысл в честности: приложение работает на том, что
   обучено внутри проекта, а не на подставленной снаружи модели.

   Диалект остался openai-совместимым (/v1/models, /v1/chat/completions) —
   на нём говорит наш же сервер, и менять протокол ради одного клиента незачем.

   Роли разделены осознанно:
     • разбор команд решает, ЧТО сделать, за считаные миллисекунды;
     • языковая модель подключается только там, где команды нет, —
       на свободный разговор и вопросы. Она думает секунды, и гонять её
       ради «открой хром» было бы расточительством.
   ========================================================================== */
(function (global) {
  'use strict';

  // Ключа хранения здесь больше нет, и это следствие уже второго столкновения с
  // одной и той же ловушкой. Настройки лежали в localStorage и накладывались
  // поверх значений из кода, поэтому у того, кто пользовался приложением раньше,
  // новые значения не применялись. В прошлый раз это лечили сменой ключа с v2 на
  // v3 — приём рабочий, но одноразовый: следующая правда потребовала бы v4, о
  // чём никто не вспомнит. Теперь параметры генерации просто берутся из кода
  // (см. load ниже), и вопрос закрыт насовсем.

  /*
     Выбранное имя модели — единственное, что здесь ЗАПОМИНАЕТСЯ между запусками.

     Параметры генерации сознательно берутся из кода (см. длинный комментарий
     ниже про ловушку с localStorage), но выбор модели — не параметр, а решение
     человека. Забывать его при каждом открытии окна значило бы спрашивать одно
     и то же по десять раз на дню.

     Ключ отдельный и узкий: он хранит одну строку и не может, как прежний,
     перекрыть собой значения из кода.
  */
  const PICK_KEY = 'cloudhdr.model.pick';

  const DEFAULTS = {
    enabled: true,
    endpoint: '',            // пусто — искать на своём порту
    model: '',               // пусто — взять первую предложенную

    // Параметры подобраны под РАЗМЕР своей модели (760 млн параметров), а не
    // под чужие движки, для которых этот мост писался изначально. Прежние 0.7
    // и 512 достались от Ollama и LM Studio, где крутятся модели в полсотни раз
    // крупнее, и там они уместны.
    //
    // Температура. Замер на пятиходовом разговоре: на 0.7 модель перепутала
    // названное имя («Мирослав» → «Ярослав») и отвечала невпопад — «что важнее
    // для монтажа, процессор или видеокарта?» → «смотря какой монтаж, если это
    // видеоигра то да». На 0.4 имя называется верно, а речь становится связной.
    // Ниже 0.3 опускать не стоит: ответы делаются однообразными.
    temperature: 0.4,

    // Длина ответа.
    //
    // Стояло 140 токенов — около 500 знаков. Причина была записана такая:
    // длинные ответы ложатся в историю разговора, вытесняют оттуда слова
    // пользователя, и модель начинает отвечать сама себе. Наблюдение верное, а
    // лекарство лечило не ту болезнь: резали ГЕНЕРАЦИЮ, хотя переполнялась
    // ИСТОРИЯ. Платили за это все ответы подряд — чуть более развёрнутый обрывался
    // на полуслове, и выглядело это как поломка модели.
    //
    // Теперь переполнение истории закрыто там, где оно происходит: реплики
    // обрезаются при сборке контекста (buildMessages ниже), а сервер при нехватке
    // места жертвует хвостом разговора, а не системной подсказкой. А главное —
    // сервер больше не режет посреди слова: он добавляет запас, чтобы договорить
    // начатое предложение, и ответ всегда кончается точкой.
    //
    // Отсюда и число. Замер на трёх развёрнутых вопросах: при пределе 300 модель
    // выбирает его ЦЕЛИКОМ каждый раз — 301 фрагмент во всех трёх ответах, по
    // 1300-1600 знаков. Свободное место она заполняет не содержанием, а
    // повторами и правдоподобной выдумкой: в ответе про SSD к третьему пункту
    // пошло «средняя скорость зависит от нескольких факторов». Для модели на
    // 760 млн параметров длина ответа и его качество связаны обратно.
    //
    // 200 токенов — это 800-900 знаков, четыре-шесть предложений. Столько она
    // держит по делу, а обрыв на полуслове теперь невозможен при любом значении.
    //
    // v7: замер показал, что при 280-320 модель заполняет место списками,
    // а 220 — пара-тройка живых фраз или связный абзац. Подобрано вместе с
    // температурой 0.55 в python/cloudhdr_ai/profiles.py.
    maxTokens: 220,

    historyTurns: 8          // сколько прошлых реплик отдавать модели
  };

  /** Порт своей модели: python -m cloudhdr_ai serve. */
  const KNOWN_PORTS = [8080];

  /*
     Подписи на случай, если сервер их не прислал.

     Свой сервер отдаёт title и about прямо в /v1/models — так подписи живут в
     одном месте с профилями и не могут разойтись. Но клиент обязан пережить и
     сервер постарше, который об этих полях не знает: тогда список моделей
     оказался бы из голых идентификаторов вида «cloud-hdr-ultra». Здесь запасной
     вариант ровно на этот случай.
  */
  const FALLBACK_META = {
    'cloud-hdr-3.2': {
      title: 'Cloud HDR 3.2',
      about: 'Разговор, объяснения и точные факты из Википедии.'
    },
    'cloud-hdr-ultra': {
      title: 'Cloud HDR Ultra',
      about: 'Всё то же плюс код, разметка, стихи и длинные тексты.'
    }
  };

  function metaFor(id) {
    const known = FALLBACK_META[id];
    if (known) return { id, ...known };
    return { id, title: id, about: '' };
  }

  const state = {
    settings: { ...DEFAULTS },
    available: false,
    url: null,
    models: [],              // идентификаторы, как их вернул сервер
    catalog: [],             // они же с подписями: {id, title, about}
    model: null,
    lastError: null,
    busy: false
  };

  /**
   * Что известно про выбранную сейчас модель — для подписей в интерфейсе.
   *
   * Запасное имя в конце цепочки не косметика: подпись под строкой ввода
   * рисуется ДО первого опроса сервера, и без него первые секунды после запуска
   * там стояла бы пустота вместо названия модели.
   */
  function current() {
    const id = state.model || state.settings.model || 'cloud-hdr-3.2';
    return state.catalog.find((item) => item.id === id) || metaFor(id);
  }

  /**
   * Список моделей для интерфейса — даже когда сервер не отвечает.
   *
   * Пока модель не поднялась, /v1/models спросить не у кого, и список пуст. Но
   * показывать в этот момент пустоту неправильно: человек не узнает, что моделей
   * вообще две, ровно тогда, когда ждёт запуска и разглядывает интерфейс.
   * Поэтому в отсутствие ответа берём то, что знаем из кода, и помечаем как
   * недоступное. Выбрать при этом можно: он запомнится и применится сам, когда
   * сервер поднимется.
   */
  function known() {
    if (state.catalog.length) return state.catalog.map((item) => ({ ...item, live: true }));
    return Object.keys(FALLBACK_META).map((id) => ({ ...metaFor(id), live: false }));
  }

  /*
     Параметры генерации берутся ИЗ КОДА, а не из localStorage — и это не
     упрощение, а починка тихой ловушки.

     Полей для температуры, длины ответа и глубины истории в интерфейсе нет с
     тех пор, как модель у проекта стала одна: менять их стало нечем. А записи,
     сделанные прежними версиями, никуда не делись, и load() накладывал их
     ПОВЕРХ значений из кода. Получалось, что правка предела ответа в этом файле
     не действует ровно у тех, кто пользуется приложением давно, — а именно у них
     ответы и обрывались. Заметить это со стороны нельзя никак: в исходниках одно
     число, в работе другое, и оба «правильные».

     Ключ не стираем: чужие записи трогать незачем, достаточно перестать их
     слушать. Если поля когда-нибудь вернутся, вернётся и чтение — вместе с ними.
  */
  function load() {
    state.settings = { ...DEFAULTS };
    // Единственное исключение — выбранное имя модели: это решение человека,
    // а не подобранный параметр, и переживать перезагрузку оно обязано.
    try {
      const saved = localStorage.getItem(PICK_KEY);
      if (saved) state.settings.model = saved;
    } catch {}
  }

  /** Разовая правка на время сеанса: перезагрузка возвращает значения из кода. */
  function update(patch) {
    state.settings = { ...state.settings, ...patch };
    state.settings.temperature = Math.min(2, Math.max(0, Number(state.settings.temperature) || 0));
    state.settings.maxTokens = Math.min(8192, Math.max(32, Number(state.settings.maxTokens) || DEFAULTS.maxTokens));
    state.settings.historyTurns = Math.min(40, Math.max(0, Number(state.settings.historyTurns) || 0));
    return state.settings;
  }

  /** Нормализует «11434», «127.0.0.1:1234», «http://localhost:8080» к базовому URL. */
  function normalizeEndpoint(value) {
    let text = String(value || '').trim().replace(/\/+$/, '');
    if (!text) return null;
    if (/^\d+$/.test(text)) text = '127.0.0.1:' + text;
    if (!/^https?:\/\//i.test(text)) text = 'http://' + text;
    return text.replace(/\/v1$/, '');
  }

  /**
   * Спрашивает у движка список моделей.
   * @returns {Promise<Array<{id:string,title:string,about:string}>|null>}
   *          null — движок не ответил; пустой массив — ответил, но моделей нет.
   */
  async function listModels(base, timeout = 1800) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(base + '/v1/models', { signal: controller.signal });
      if (!response.ok) return null;
      const data = await response.json();
      return (data && Array.isArray(data.data) ? data.data : [])
        .filter((item) => item && item.id)
        .map((item) => ({
          ...metaFor(item.id),
          ...(item.title ? { title: item.title } : {}),
          ...(item.about ? { about: item.about } : {}),
          ...(item.params ? { params: item.params } : {}),
          ...(item.context ? { context: item.context } : {})
        }));
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Ищет локальный движок. Сначала — адрес из настроек, потом известные порты.
   * @returns {Promise<{available:boolean, url:string|null, models:string[]}>}
   */
  async function probe() {
    state.lastError = null;
    const candidates = [];
    const configured = normalizeEndpoint(state.settings.endpoint);
    if (configured) candidates.push(configured);
    KNOWN_PORTS.forEach((port) => {
      const url = 'http://127.0.0.1:' + port;
      if (!candidates.includes(url)) candidates.push(url);
    });

    for (const base of candidates) {
      const catalog = await listModels(base);
      if (catalog && catalog.length) {
        const ids = catalog.map((item) => item.id);
        state.available = true;
        state.url = base;
        state.catalog = catalog;
        state.models = ids;
        // Запомненный выбор уважаем, но только если сервер такое имя ещё знает:
        // иначе запрос ушёл бы с идентификатором, которого нет, и модель
        // отвечала бы профилем по умолчанию молча, без всякого объяснения.
        state.model = ids.includes(state.settings.model) ? state.settings.model : ids[0];
        return { available: true, url: base, models: ids, catalog };
      }
      if (catalog && !catalog.length) {
        // движок отвечает, но моделей нет — это стоит сказать вслух
        state.lastError = `Движок на ${base} отвечает, но ни одной модели не загружено.`;
      }
    }

    state.available = false;
    state.url = null;
    state.models = [];
    state.catalog = [];
    state.model = null;
    return { available: false, url: null, models: [], catalog: [] };
  }

  /* ------------------------------------------------------------- контекст -- */

  const SYSTEM_PROMPT =
    'Ты — Cloud HDR AI, помощник программы Cloud HDR. Ты работаешь полностью на компьютере пользователя, ' +
    'без интернета и без облачных сервисов.\n\n' +
    'Как устроена работа: команды к Windows (открыть программу, найти файл или игру на дисках, ' +
    'открыть папку, скриншот, громкость, блокировка экрана) разбирает и выполняет не ты, а отдельная ' +
    'локальная нейросеть-классификатор вместе с агентом. Ты подключаешься тогда, когда команды нет: ' +
    'на разговор, объяснения и вопросы.\n\n' +
    'Правила: отвечай по-русски, если пользователь пишет по-русски. Говори живо и тепло, полными ' +
    'фразами, как внимательный собеседник; сложное объясняй связно, но без воды. Не выдумывай факты о компьютере пользователя: если нужно что-то узнать о ' +
    'файлах или программах, предложи соответствующую команду («найди игру …», «какие игры установлены»). ' +
    'Не притворяйся, что выполнил действие в системе, — этого ты не умеешь.';

  /*
     Сколько знаков реплики отдавать модели как контекст.

     Стояло 2000 на любую реплику. При восьми кругах разговора это до 32 тысяч
     знаков — примерно в пять раз больше всего контекстного окна модели (2048
     токенов). То есть переполнение было не редким случаем, а нормой: почти
     каждый разговор длиннее трёх реплик уже не помещался, и лишнее отрезалось
     на сервере — вместе с системной подсказкой.

     Свои прошлые ответы модели нужны короче, чем слова человека: из ответа
     достаточно помнить, о чём шла речь, а вопрос важен целиком — в нём условие
     задачи. Отсюда разные пределы.
  */
  const CANNED_TEXT = /^(Привет! Я Cloud HDR AI|Здравствуйте! Могу запустить программу|Привет! Готов работать|Всегда пожалуйста|Обращайтесь — Cloud HDR AI|Рад помочь!$|Не уверен, что вы имеете в виду|Такого я пока не понимаю)/;
  const KEEP_USER = 1200;
  const KEEP_AI = 600;

  /**
   * Собирает историю для модели из сообщений чата.
   * @param {Array} messages сообщения активного чата
   */
  function buildMessages(messages, question) {
    const turns = Math.max(0, state.settings.historyTurns);
    // Команды компьютеру в разговор не отдаём: «громче» → «Громкость 89%» в
    // истории сбивали модель, и на «как дела?» она отвечала про звук и про то,
    // что ей «не нравятся последовательные команды». Убираем и карточку
    // выполнения (у неё есть cmd), и реплику, которая её вызвала.
    const list = (messages || []).filter((message) => message && message.text);
    const drop = new Set();
    list.forEach((message, i) => {
      // Заготовки приложения («Привет! Я Cloud HDR AI…») — тоже не разговор.
      // Модель видела их в истории как свои прошлые ответы и повторяла слово
      // в слово, дописывая дежурную вежливость. В старых чатах пометки нет —
      // приветствия узнаются по тексту.
      const canned = message.role !== 'user' && (message.canned || CANNED_TEXT.test(message.text));
      if (message.role !== 'user' && (message.cmd || canned)) {
        drop.add(i);
        if (i > 0 && list[i - 1].role === 'user') drop.add(i - 1);
      }
    });
    const history = list
      .filter((_, i) => !drop.has(i))
      .slice(-turns * 2)
      .map((message) => {
        const user = message.role === 'user';
        const limit = user ? KEEP_USER : KEEP_AI;
        const text = String(message.text);
        return {
          role: user ? 'user' : 'assistant',
          content: text.length > limit ? text.slice(0, limit).trimEnd() + '…' : text
        };
      });

    // последняя реплика пользователя уже могла попасть в историю — не дублируем
    if (history.length && history[history.length - 1].role === 'user' &&
        history[history.length - 1].content === question) {
      history.pop();
    }

    return [{ role: 'system', content: SYSTEM_PROMPT }, ...history, { role: 'user', content: question }];
  }

  /*
     Про длину ответа здесь больше не решается — и это перенос обязанности,
     а не её потеря.

     Раньше тут стояло правило: если в вопросе есть слово «код», «html» или
     «напиши статью» — дать вдвое больший предел. Правило работало наполовину.
     Предел оно поднимало, но остановку не меняло, а обрывался ответ именно на
     остановке: сервер досчитывал до конца ПРЕДЛОЖЕНИЯ, а у программы
     предложений нет. Получалась страница с незакрытым тегом <style> —
     структурно похожая на верстку и полностью нерабочая.

     Теперь и предел, и правило остановки выбирает сервер (python/cloudhdr_ai/
     profiles.py): он один раз разбирает, о чём просят, и подбирает бюджет,
     температуру и признак законченности согласованно. Разносить это решение
     по двум сторонам HTTP было ошибкой: клиент видел вопрос, но не управлял
     генерацией, а сервер управлял, но пользовался чужим числом.

     Наше max_tokens сервер по-прежнему получает и уважает — как пожелание для
     обычного разговора. Там, где он видит просьбу о коде, он берёт своё, большее.
  */

  /* ---------------------------------------------------------- генерация --- */

  let controller = null;

  /**
   * Потоковая генерация ответа.
   * @param {string} question
   * @param {Array} history сообщения активного чата
   * @param {(chunk:string, full:string)=>void} onToken
   * @returns {Promise<{text:string, model:string, ms:number, tokens:number, stopped:boolean}>}
   */
  async function ask(question, history, onToken) {
    if (!state.available || !state.url) throw new Error('Локальная модель не подключена');

    controller = new AbortController();
    state.busy = true;
    const started = performance.now();
    let text = '';
    let tokens = 0;
    let stopped = false;

    try {
      const response = await fetch(state.url + '/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          model: state.model,
          messages: buildMessages(history, question),
          stream: true,
          temperature: state.settings.temperature,
          max_tokens: state.settings.maxTokens
        })
      });

      if (!response.ok) {
        const detail = await response.text().catch(() => '');
        throw new Error(`Модель вернула ошибку ${response.status}${detail ? ': ' + detail.slice(0, 200) : ''}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // поток идёт строками «data: {…}», последняя — «data: [DONE]»
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (!payload || payload === '[DONE]') continue;

          let chunk;
          try { chunk = JSON.parse(payload); } catch { continue; }
          const delta = chunk.choices && chunk.choices[0] &&
                        (chunk.choices[0].delta ? chunk.choices[0].delta.content : chunk.choices[0].text);
          if (!delta) continue;

          text += delta;
          tokens++;
          if (onToken) onToken(delta, text);
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        stopped = true;
      } else if (error instanceof TypeError) {
        // fetch отдаёт TypeError на любую сетевую беду и НЕ говорит, на какую
        // именно: и закрытый доступ с чужого адреса, и оборванное соединение,
        // и остановленный движок выглядят одинаково. Поэтому называем обе
        // причины — молча указать на одну значило бы отправить чинить не то.
        // Текст про Ollama остался с тех пор, когда мост искал чужие движки, и
        // вводил в заблуждение: своя модель поднимается одной командой, а
        // пользователя отправляли настраивать OLLAMA_ORIGINS для программы,
        // которой в проекте нет. Показывается это сообщение ровно тогда, когда
        // модель не запущена, — то есть в самый неподходящий момент, чтобы
        // давать совет не по делу.
        const alive = await listModels(state.url, 1200);
        throw new Error(alive
          ? 'Соединение с моделью оборвалось на середине запроса. Модель отвечает, так что, ' +
            'скорее всего, она занята другим запросом — попробуйте ещё раз.'
          : 'Модель разговора не запущена. Поднимите её: двойной клик по start-ai.bat, ' +
            'либо в PowerShell — cd cloud-hdr\\python, затем python -m cloudhdr_ai serve. ' +
            'Загрузка весов занимает 10-30 секунд. Команды Windows работают и без неё.');
      } else {
        throw error;
      }
    } finally {
      state.busy = false;
      controller = null;
    }

    return { text: text.trim(), model: state.model, ms: Math.round(performance.now() - started), tokens, stopped };
  }

  /**
   * Просьба → команды приложения (python/cloudhdr_qwen/router.py, /v1/route).
   * {commands: [...], ask: '...', say: '...'}; null — сервер не умеет или молчит.
   */
  async function route(text, history) {
    if (!state.available || !state.url) return null;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(state.url + '/v1/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, history: history || [] }),
        signal: controller.signal
      });
      if (!response.ok) return null;
      const data = await response.json();
      return data && Array.isArray(data.commands) ? data : null;
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Прерывает генерацию — ответ сохраняется таким, каким успел получиться. */
  function stop() {
    if (controller) { try { controller.abort(); } catch {} }
  }

  global.LLM = {
    load, probe, ask, stop, update, current, known, route,
    normalizeEndpoint,
    get state() { return state; },
    get settings() { return { ...state.settings }; },
    get available() { return state.available && state.settings.enabled; },
    get busy() { return state.busy; },
    /** Список моделей с подписями — то, что показывает выбор под строкой ввода. */
    get catalog() { return state.catalog.slice(); },
    setModel(name) {
      state.model = name;
      update({ model: name });
      try { localStorage.setItem(PICK_KEY, name); } catch {}
      return current();
    },
    DEFAULTS, KNOWN_PORTS, SYSTEM_PROMPT
  };
})(window);
