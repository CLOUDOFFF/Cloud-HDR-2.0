/* ============================================================================
   Cloud HDR — голос помощника
   ----------------------------------------------------------------------------
   Ответы читаются вслух. Голос синтезируется на этом компьютере: агент отдаёт
   готовый WAV, браузер его пропускает через звуковой тракт и играет. Ни одного
   обращения наружу — как и везде в Cloud HDR.

   Почему не ограничиться speechSynthesis браузера, раз он уже есть. Chrome на
   Windows видит голоса через SAPI5, а там из русских стоит одна «Microsoft
   Irina Desktop» — механическая озвучка родом из Windows 7. Остальное, что он
   предлагает («Google русский»), синтезируется НА СЕРВЕРЕ Google, то есть текст
   ответа уходит в сеть. Ни то ни другое не годится. Агент берёт голоса OneCore,
   среди которых есть мужской «Microsoft Pavel»: ровный, спокойный, без
   металлического призвука, и целиком локальный. Браузерный синтез оставлен
   запасным вариантом — на случай, если агент не поднят.

   Приятность голоса тут не только в выборе голоса. Три вещи делают из
   синтезатора собеседника:

     1. ФРАЗИРОВКА. Текст режется на предложения, и каждое озвучивается
        отдельно, с короткой паузой после. Синтезатор, которому отдали абзац
        целиком, читает его на одном дыхании и ровно — отсюда и берётся
        ощущение робота. Человек между мыслями останавливается.

     2. ПРОСОДИЯ. Темп чуть ниже обычного, тон опущен на полтора полутона.
        Спокойная речь звучит уверенно; быстрая и высокая — суетливо.

     3. ЗВУКОВОЙ ТРАКТ. WAV идёт не прямо в колонки, а через Web Audio:
        обрезка низа, лёгкий подъём груди и присутствия, мягкая компрессия и
        едва слышный отзвук помещения. Ровно этим отличается голос «из
        телефона» от голоса «в комнате».

   Всё это доступно только потому, что звук приходит буфером. У
   speechSynthesis выход в Web Audio не выведен вовсе: его можно лишь запустить
   и слушать как есть.
   ========================================================================== */
(function (global) {
  'use strict';

  const AGENT_PORT = 4477;
  const BASE = location.protocol.startsWith('http') && location.port === String(AGENT_PORT)
    ? ''
    : `http://127.0.0.1:${AGENT_PORT}`;

  const STORAGE = 'cloudhdr.voice.v1';

  /* ================================================== 0. Характеры голоса == */

  /*
     Из чего вообще складывается «голос Джарвиса».

     Разобрать это стоит по частям, потому что одна из частей здесь
     недостижима, а остальные — вполне.

       1. АКЦЕНТ. Пола Беттани отличает выговор образованного англичанина. Из
          русского синтезатора его не достать никакими настройками: акцент
          живёт в самих записанных звуках, а не в обработке над ними. В системе
          мужской голос ровно один — Microsoft Pavel, русский. Это предел, и
          честнее назвать его прямо, чем делать вид, что регулятором тембра
          можно переехать в Лондон.

       2. РЕГИСТР. Джарвис — баритон, заметно ниже среднего мужского голоса.
          Достижимо, но не одним лишь SSML: разметка опускает высоту, СОХРАНЯЯ
          форманты, — получается тот же человек, говорящий ниже. У Беттани
          голос не просто низкий, а КРУПНЫЙ: большой резонатор, широкие
          форманты. Это даёт замедление воспроизведения (depth): оно тянет
          вниз и высоту, и форманты сразу, и говорящий начинает звучать
          физически крупнее. Поэтому здесь работают оба механизма вместе.

       3. ТЕМП. Джарвис никогда не торопится. Ровная неспешная подача — половина
          впечатления собранности.

       4. ОБРАБОТКА. Голос звучит не «изо рта», а из помещения: мастерская
          Старка большая и глухая. Узнаваемых приёмов три — короткий двойник
          (копия с задержкой в пару десятков миллисекунд), широкая подача и
          тёмный отзвук, всё поверх плотной компрессии, которая держит
          громкость идеально ровной. Именно это сочетание и читается на слух
          как «искусственный интеллект», а не «диктор».

     Пункты 2-4 сделаны ниже. Пункт 1 — нет, и не будет.
  */
  const PRESETS = {
    jarvis: {
      label: 'Джарвис',
      note: 'Баритон, ровная подача, сухо и близко — без эха и «зала».',
      // Темп: −8, а не −3. Замер показал, что мелкие значения синтезатор
      // просто проглатывает — на −3% длительность фразы не менялась вовсе
      // (6.01 с против 6.01 с). Просить о том, что не исполняется, смысла нет.
      rate: -8,
      /*
         Понижение — ТОЛЬКО разметкой, и это разворот прежнего решения.

         Раньше основную работу делало замедление воспроизведения: оно тянет
         вниз и высоту, и форманты, и я считал это достоинством — «говорящий
         звучит физически крупнее». Ровно это и оказалось жалобой: «как из
         бочки». И это не совпадение слов, а точное попадание. Форманты
         задаются размером резонатора; опустить их — значит сообщить уху, что
         звук родился в полости побольше. Бочка и есть полость побольше.

         Разметка SSML опускает высоту, СОХРАНЯЯ форманты: тот же человек,
         говорящий ниже. Голос остаётся своим и близким. Поэтому глубина здесь
         теперь ровно 1.0, а вся низкость ушла в тон.

         Величина −4, а не −2.5, потому что синтезатор исполняет просьбу лишь
         частично: замер дал −1.11 полутона на запрошенных −2.5, то есть около
         45%. На −4 выходит примерно −1.8 — понижение, которое уже слышно, но
         ещё не тянет за собой призвуки. Дальше можно ползунком «Тон».
      */
      pitch: -4,
      depth: 1,
      chain: {
        chest: 3, box: -2, presence: 3, air: 1.2,
        /*
           Двойника и «ширины» здесь больше нет, и это разбор измеренной
           ошибки, а не смена вкуса.

           Задумка была верная: короткая копия голоса — известный приём, из
           которого и складывается «нечеловеческая собранность». Беда в
           величинах. Копия на 11 мс складывается с прямым сигналом и выгрызает
           в спектре гребёнку с шагом около 91 Гц — ровно там, где живёт голос.
           Замер подтвердил: провалов стало 41 против 31 у необработанного
           сигнала, а расхождение каналов дошло до −31.8 дБ при уровне −23.3,
           то есть копии звучали почти вровень с оригиналом. На слух это не
           «искусственный интеллект», а «говорит из бочки»: гулко, пусто,
           далеко.

           Приём работает на задержках, которые ухо слышит отдельным отзвуком
           (от 40 мс), и на уровнях втрое ниже — но тогда он даёт заметное эхо,
           а Джарвис говорит близко и сухо. Поэтому убран совсем: узнаваемость
           держится на регистре, ровности и подаче, они никуда не делись.
        */
        double: 0, width: 0,
        // Отзвука нет совсем. Любое эхо отодвигает голос от слушателя, а
        // претензия была именно на «далеко». Джарвис говорит рядом.
        verb: 0, verbSeconds: 0,
        compress: { threshold: -22, ratio: 3.5, knee: 20, attack: 0.005, release: 0.2 }
      }
    },
    warm: {
      label: 'Тёплый',
      note: 'Обычный мужской регистр, мягче и свободнее по громкости. Для тихой комнаты.',
      // Тоже без замедления: сдвиг формант давал ту самую «бочку», и держать
      // его в запасном характере значило бы оставить грабли лежать.
      rate: -6, pitch: -1, depth: 1,
      chain: {
        chest: 2, box: -1.5, presence: 2.5, air: 0.8,
        double: 0, width: 0, verb: 0, verbSeconds: 0,
        compress: { threshold: -18, ratio: 2.5, knee: 24, attack: 0.008, release: 0.16 }
      }
    },
    plain: {
      label: 'Без обработки',
      note: 'Чистый синтезатор как есть — на случай, если обработка мешает.',
      rate: -6, pitch: 0, depth: 1,
      chain: null
    }
  };

  const DEFAULTS = {
    enabled: true,
    voice: '',          // пусто — агент сам выберет лучший русский мужской
    preset: 'jarvis',
    rate: -8,           // проценты от обычного темпа (SSML)
    pitch: -4,          // полутоны (SSML) — понижение без сдвига формант
    depth: 1,           // скорость воспроизведения: ниже — крупнее и «бочковатее»
    volume: 0.85
  };

  const settings = Object.assign({}, DEFAULTS, load());

  function load() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(STORAGE) || '{}') || {}; }
    catch { return {}; }

    // Перенос со старых настроек: раньше обработка была одним выключателем
    // warmth, теперь это характер голоса. Выключенная обработка — «без
    // обработки», включённая — Джарвис.
    if (saved.preset === undefined && saved.warmth !== undefined) {
      saved.preset = saved.warmth ? 'jarvis' : 'plain';
      delete saved.warmth;
    }
    return saved;
  }

  const preset = () => PRESETS[settings.preset] || PRESETS.jarvis;

  function save() {
    try { localStorage.setItem(STORAGE, JSON.stringify(settings)); } catch { /* переполнено */ }
  }

  const state = {
    /** 'unknown' | 'agent' | 'browser' | 'none' — чем именно говорим. */
    engine: 'unknown',
    voices: [],
    speaking: false,
    lastError: null,
    /** Отказы агента подряд: два — и временно переходим на голос браузера. */
    agentFails: 0,
    /** Когда снова пробовать агента после перехода на браузер. */
    retryAgentAt: 0,
    /** Растёт при каждом stop(): всё, что было запущено раньше, само себя отменит. */
    epoch: 0
  };

  const handlers = { start: [], end: [] };
  const emit = (list, ...args) => list.forEach((fn) => { try { fn(...args); } catch (error) { console.error(error); } });

  /* ==================================================== 1. Текст для речи == */

  /*
     Написанное и произносимое — разные тексты, и разница не косметическая.
     Пути, ссылки, разметка и эмодзи в письменном ответе полезны, а вслух
     превращаются в поток букв, из которого невозможно выбраться. Синтезатор
     прочитает «C:\Users\11\Downloads» посимвольно и потратит на это восемь
     секунд.
  */
  const SPEECH_MAP = [
    [/```[\s\S]*?```/g, ' Показываю код на экране. '],
    [/`([^`]+)`/g, '$1'],
    [/\*\*([^*]+)\*\*/g, '$1'],
    [/^\s*[-•*]\s+/gm, ''],
    [/https?:\/\/\S+/g, ' ссылка '],
    [/[A-Za-zА-Яа-я]:\\[^\s]+/g, ' путь на диске '],
    [/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{2190}-\u{21FF}]/gu, ' '],
    [/[«»"'`_*#>|]/g, ' '],
    [/\s*[·–—]\s*/g, ', '],
    [/(\d)\s*%/g, '$1 процентов'],
    // Границы слова — просмотрами: \b в JavaScript кириллицу не видит.
    [/(?<![А-ЯЁа-яёA-Za-z0-9_])мс(?![А-ЯЁа-яёA-Za-z0-9_])/g, 'миллисекунд'],
    [/(?<![А-ЯЁа-яёA-Za-z0-9_])с\.\s/g, 'секунд '],
    [/(?<![А-ЯЁа-яёA-Za-z0-9_])ГБ(?![А-ЯЁа-яёA-Za-z0-9_])/g, 'гигабайт'],
    [/(?<![А-ЯЁа-яёA-Za-z0-9_])МБ(?![А-ЯЁа-яёA-Za-z0-9_])/g, 'мегабайт'],
    [/(?<![А-ЯЁа-яёA-Za-z0-9_])КБ(?![А-ЯЁа-яёA-Za-z0-9_])/g, 'килобайт'],
    [/\s{2,}/g, ' ']
  ];

  function speakable(text) {
    let out = String(text == null ? '' : text);
    for (const [pattern, replacement] of SPEECH_MAP) out = out.replace(pattern, replacement);
    return out.trim();
  }

  /**
   * Режет текст на произносимые куски.
   *
   * Граница — конец предложения, но с оговоркой: слишком короткие куски
   * склеиваются с соседями. Отдельная фраза «Готово.» между двумя паузами
   * звучит рвано — пауза должна отмечать мысль, а не каждую точку.
   */
  function phrases(text, limit = 220) {
    const clean = speakable(text);
    if (!clean) return [];

    const rough = clean.split(/(?<=[.!?…])\s+|\n+/).map((part) => part.trim()).filter(Boolean);
    const out = [];

    for (const part of rough) {
      // Длинное предложение без точек всё равно надо делить: синтезатор глотает
      // конец фразы, если она не помещается в его собственный буфер.
      if (part.length > limit) {
        let rest = part;
        while (rest.length > limit) {
          const cut = rest.lastIndexOf(',', limit);
          const at = cut > limit * 0.4 ? cut + 1 : rest.lastIndexOf(' ', limit);
          const piece = rest.slice(0, at > 0 ? at : limit).trim();
          if (piece) out.push(piece);
          rest = rest.slice(at > 0 ? at : limit).trim();
        }
        if (rest) out.push(rest);
        continue;
      }
      /*
         Склеиваются только ОБРЫВКИ, а не соседние предложения.

         Первая версия проверяла суммарную длину («меньше девяноста знаков —
         склеить»), и это оказалось не тем правилом: два нормальных
         предложения по сорок знаков давали восемьдесят и слипались в одно.
         Пауза пропадала ровно там, где она и нужна — между мыслями.

         Значение имеет длина ПРИСОЕДИНЯЕМОГО куска. Отдельная фраза «Готово.»
         между двумя паузами звучит рвано, поэтому такие хвосты прирастают к
         соседу; всё, что длиннее пары слов, — самостоятельная фраза.
      */
      const previous = out[out.length - 1];
      const tiny = part.length < 16 || (previous && previous.length < 16);
      if (previous && tiny && previous.length + part.length < 120) {
        out[out.length - 1] = previous + ' ' + part;
      } else {
        out.push(part);
      }
    }
    return out;
  }

  /* ================================================== 2. Звуковой тракт ==== */

  const chain = { ctx: null, input: null, output: null, gain: null, built: false };

  function context() {
    if (!chain.ctx) {
      const Ctx = global.AudioContext || global.webkitAudioContext;
      if (!Ctx) return null;
      chain.ctx = new Ctx();
    }
    if (chain.ctx.state === 'suspended') chain.ctx.resume().catch(() => {});
    return chain.ctx;
  }

  /**
   * Отзвук помещения — сгенерированный, а не загруженный файлом.
   *
   * Файл импульса пришлось бы где-то взять, а это либо лишний мегабайт в
   * проекте, либо запрос наружу. Затухающий шум даёт ровно то, что нужно.
   *
   * Шум ТЁМНЫЙ: перед затуханием он пропускается через простейший фильтр
   * первого порядка, срезающий верх. Так звучат большие помещения с мягкими
   * стенами — высокие частоты в них гаснут быстрее низких. Яркий отзвук
   * ставит голос в кафельную ванную; тёмный — в просторную мастерскую, а
   * именно это и нужно.
   *
   * Каналы генерируются независимо: одинаковый шум слева и справа сложился бы
   * в точку посреди головы, а разный даёт объём.
   */
  function buildImpulse(ctx, seconds = 1.05, decay = 2.6) {
    const rate = ctx.sampleRate;
    const length = Math.max(1, Math.floor(rate * seconds));
    const impulse = ctx.createBuffer(2, length, rate);
    for (let channel = 0; channel < 2; channel++) {
      const data = impulse.getChannelData(channel);
      let previous = 0;
      for (let i = 0; i < length; i++) {
        const white = Math.random() * 2 - 1;
        previous = previous * 0.72 + white * 0.28;      // однополюсный срез верха
        data[i] = previous * Math.pow(1 - i / length, decay);
      }
    }
    return impulse;
  }

  /**
   * Собирает звуковой тракт — один раз на выбранный характер голоса.
   *
   * Порядок узлов и их назначение:
   *
   *   срез низа 80    — гул и толчки воздуха ниже голоса, чистого сигнала там нет;
   *   полка 130 +3.5  — грудь: без неё голос звучит тонко и по-телефонному;
   *   480 −2.5        — снимает «коробку», самый частый дефект синтеза;
   *   2.6 кГц +3      — присутствие: речь остаётся разборчивой поверх музыки;
   *   полка 9к +1.5   — воздух, чтобы плотная компрессия не задушила верх;
   *   компрессор      — ровная громкость; у синтезатора она скачет от фразы
   *                     к фразе, а у Джарвиса она не скачет никогда;
   *
   * дальше сигнал расходится на четыре пути, и вот они-то и делают голос
   * узнаваемым:
   *
   *   прямой          — сам голос, по центру;
   *   двойник 24 мс   — копия чуть позже и тише, приглушённая сверху и сдвинутая
   *                     влево. Ухо не слышит её отдельно, но перестаёт слышать
   *                     одного говорящего — появляется та самая «нечеловеческая
   *                     собранность». Верх у неё срезан намеренно: иначе она
   *                     размазывает согласные и речь теряет чёткость;
   *   ширина 11 мс    — то же вправо и ещё тише. На таких задержках ухо
   *                     складывает копию с оригиналом в один звук, но широкий:
   *                     голос перестаёт быть точкой и занимает пространство;
   *   отзвук          — большое тёмное помещение, совсем немного. По отдельности
   *                     почти не слышен, а убери его — голос сразу «в наушниках».
   */
  /**
   * Собирает тракт в ЛЮБОМ звуковом контексте и возвращает его концы.
   *
   * Отделено от кеширования намеренно: ровно этот же граф можно собрать в
   * OfflineAudioContext и посчитать, что он делает со звуком, — а не спорить
   * на слух о том, стало лучше или хуже. Тракт из десятка узлов с задержками
   * и отзвуком слишком легко испортить незаметно для автора.
   *
   * @returns {{input: AudioNode, output: GainNode}} выход НЕ подключён к колонкам
   */
  function buildGraph(ctx, volume) {
    const recipe = preset().chain;
    const input = ctx.createGain();
    const master = ctx.createGain();
    master.gain.value = volume == null ? settings.volume : volume;

    if (!recipe) {                       // характер «без обработки»
      input.connect(master);
      return { input, output: master };
    }

    const peak = (type, frequency, q, gain) => {
      const node = ctx.createBiquadFilter();
      node.type = type;
      node.frequency.value = frequency;
      if (q != null) node.Q.value = q;
      if (gain != null) node.gain.value = gain;
      return node;
    };

    /*
       Срез низа поднят с 80 до 110 Гц, а «грудь» перестала быть полкой.

       Оба изменения — про одну и ту же жалобу на гулкость. Полка на 130 Гц
       поднимает ВСЁ, что ниже неё, до самого среза: вместе с телом голоса
       прибавляется и гул, которого в синтезе хватает. Узкий колокол на 180 Гц
       добавляет ровно тело — в той полосе, где у мужского голоса первая
       гармоника, — и не трогает подвал. А срез на 110 Гц этот подвал заодно
       и убирает: у Pavel основной тон около 105 Гц, ниже нет ничего полезного.
    */
    const highpass = peak('highpass', 110, 0.7);
    const chest = peak('peaking', 180, 1.2, recipe.chest);
    const box = peak('peaking', 430, 1.2, recipe.box);
    const presence = peak('peaking', 2800, 0.9, recipe.presence);
    const air = peak('highshelf', 9000, null, recipe.air);

    const compressor = ctx.createDynamicsCompressor();
    Object.entries(recipe.compress).forEach(([name, value]) => { compressor[name].value = value; });

    input.connect(highpass).connect(chest).connect(box).connect(presence).connect(air).connect(compressor);

    const dry = ctx.createGain();
    dry.gain.value = 1;
    compressor.connect(dry).connect(master);

    // Ответвление с задержкой и панорамой. StereoPannerNode есть не везде —
    // без него просто не разводим копии по сторонам, всё остальное работает.
    const branch = (seconds, gain, pan, cutoff) => {
      if (!gain) return;
      const delay = ctx.createDelay(0.2);
      delay.delayTime.value = seconds;
      const level = ctx.createGain();
      level.gain.value = gain;

      compressor.connect(delay);
      let tail = delay;
      if (cutoff) tail = delay.connect(peak('lowpass', cutoff, 0.7));
      tail = tail.connect(level);

      if (ctx.createStereoPanner) {
        const panner = ctx.createStereoPanner();
        panner.pan.value = pan;
        tail.connect(panner).connect(master);
      } else {
        tail.connect(master);
      }
    };

    branch(0.024, recipe.double, -0.55, 3400);
    branch(0.011, recipe.width, 0.60, null);

    if (recipe.verb) {
      const reverb = ctx.createConvolver();
      reverb.buffer = buildImpulse(ctx, recipe.verbSeconds);
      const wet = ctx.createGain();
      wet.gain.value = recipe.verb;
      compressor.connect(reverb).connect(wet).connect(master);
    }

    return { input, output: master };
  }

  /** Тот же тракт, но собранный один раз и подключённый к колонкам. */
  function buildChain(ctx) {
    if (chain.built) return chain;
    const graph = buildGraph(ctx);
    graph.output.connect(ctx.destination);
    chain.input = graph.input;
    chain.gain = graph.output;
    chain.built = true;
    return chain;
  }

  /**
   * Сбрасывает тракт, чтобы он собрался заново под новый характер голоса.
   *
   * Частоты и уровни зашиты в узлы при сборке, менять их по одному нечем — да
   * и незачем: узлов десяток, а характеров три.
   *
   * Старый выход обязательно отключается от колонок. Без этого прежний тракт
   * остаётся подключённым к destination, новый подключается рядом, и дальше
   * каждая фраза звучит через оба сразу: громче, с двойным отзвуком и всё
   * хуже с каждой сменой настройки. Web Audio не собирает такие узлы сам,
   * пока они куда-то подключены.
   */
  function rebuildChain() {
    if (chain.gain) { try { chain.gain.disconnect(); } catch { /* уже отключён */ } }
    if (chain.input) { try { chain.input.disconnect(); } catch { /* уже отключён */ } }
    chain.built = false;
    chain.input = null;
    chain.gain = null;
  }

  /* ================================================ 3. Синтез через агента = */

  /** Кэш готовых фраз: короткие ответы повторяются, а синтез не бесплатен. */
  const cache = new Map();
  const CACHE_LIMIT = 24;

  function cacheKey(text) {
    return `${settings.voice}|${settings.rate}|${settings.pitch}|${text}`;
  }

  /*
     Свой голос — клон хозяина (python/cloudhdr_voice.py, 127.0.0.1:4482).
     Если он записан, говорим им; если служба молчит или голоса ещё нет —
     тихо возвращаемся к голосу Windows, как было всегда. Проверка готовности
     кэшируется на минуту, чтобы не стучаться в службу перед каждой фразой.
  */
  const OWN_VOICE = 'http://127.0.0.1:4482';
  const ownBuffers = new WeakSet();
  let ownReady = null;
  let ownCheckedAt = 0;

  async function ownVoiceReady() {
    if (ownReady !== null && Date.now() - ownCheckedAt < 60000) return ownReady;
    ownCheckedAt = Date.now();
    try {
      const response = await fetch(OWN_VOICE + '/voice/status', { cache: 'no-store' });
      ownReady = Boolean((await response.json()).ready);
    } catch {
      ownReady = false;
    }
    return ownReady;
  }

  async function synthesizeOwn(text) {
    const key = 'own|' + text;
    if (cache.has(key)) return cache.get(key);
    const controller = new AbortController();
    // Клон на процессоре думает дольше голоса Windows: даём ему время.
    const timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch(OWN_VOICE + '/voice/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: controller.signal
      });
      if (!response.ok) throw new Error('свой голос недоступен');
      const ctx = context();
      if (!ctx) throw new Error('в этом браузере нет Web Audio');
      const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
      ownBuffers.add(buffer);
      cache.set(key, buffer);
      if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value);
      return buffer;
    } finally {
      clearTimeout(timer);
    }
  }

  async function synthesize(text) {
    if (await ownVoiceReady()) {
      try { return await synthesizeOwn(text); } catch { ownReady = false; /* дальше — голос Windows */ }
    }

    const key = cacheKey(text);
    if (cache.has(key)) return cache.get(key);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(BASE + '/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          voice: settings.voice,
          rate: settings.rate,
          pitch: settings.pitch
        }),
        signal: controller.signal
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.error || `агент вернул ${response.status}`);
      }
      const audio = await response.arrayBuffer();
      const ctx = context();
      if (!ctx) throw new Error('в этом браузере нет Web Audio');
      const buffer = await ctx.decodeAudioData(audio);

      cache.set(key, buffer);
      // Кэш ограничен по числу фраз, а не по памяти: несколько секунд моно —
      // сотни килобайт, и два десятка таких никому не мешают.
      if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value);
      return buffer;
    } finally {
      clearTimeout(timer);
    }
  }

  function playBuffer(buffer, epoch) {
    return new Promise((resolve) => {
      const ctx = context();
      if (!ctx) return resolve();

      const source = ctx.createBufferSource();
      source.buffer = buffer;

      /*
         Замедление воспроизведения — второй механизм понижения голоса, и он
         делает не то же самое, что разметка SSML.

         SSML опускает высоту тона, СОХРАНЯЯ форманты: получается тот же
         человек, говорящий ниже. Замедление тянет вниз всё сразу — и высоту,
         и форманты, — а форманты определяются размером голосового тракта. На
         слух это читается однозначно: говорит человек крупнее. Джарвис звучит
         именно так — не «мужчина, который понизил голос», а большой спокойный
         резонатор.

         Мера здесь важнее величины: за 0.85 начинает слышаться замедленная
         запись, и обаяние пропадает вместе с правдоподобием. Поэтому 0.91, а
         темп подачи выправлен разметкой обратно, чтобы речь не растянулась.
      */
      // Свой голос не замедляем: он и так звучит как хозяин, а «крупность»
      // придумана, чтобы оживить голос Windows.
      const depth = ownBuffers.has(buffer) ? 1 : Math.max(0.75, Math.min(1.25, Number(settings.depth) || 1));
      if (depth !== 1) source.playbackRate.value = depth;

      buildChain(ctx);
      chain.gain.gain.value = settings.volume;
      source.connect(chain.input);

      let done = false;
      const finish = () => { if (!done) { done = true; current.source = null; resolve(); } };
      source.onended = finish;
      current.source = source;
      current.epoch = epoch;
      try { source.start(); } catch { finish(); }
    });
  }

  /* =============================================== 4. Запасной синтезатор == */

  /*
     Голос браузера. Включается, только если агент недоступен: качество заметно
     хуже, зато он есть всегда. Голоса, которые синтезируются на сервере
     (`localService === false` — у Chrome это «Google русский»), исключены
     намеренно: текст ответа не должен уходить наружу ради озвучки.
  */
  function browserVoice() {
    if (!global.speechSynthesis) return null;
    const all = speechSynthesis.getVoices() || [];
    const local = all.filter((voice) => voice.localService !== false);
    const russian = local.filter((voice) => /^ru/i.test(voice.lang));
    if (settings.voice) {
      const named = russian.find((voice) => voice.name === settings.voice) ||
                    local.find((voice) => voice.name === settings.voice);
      if (named) return named;
    }
    return russian.find((voice) => /pavel|dmitr|male|муж/i.test(voice.name)) || russian[0] || local[0] || null;
  }

  function speakInBrowser(text, epoch) {
    return new Promise((resolve) => {
      if (!global.speechSynthesis) return resolve();
      const utterance = new SpeechSynthesisUtterance(text);
      const voice = browserVoice();
      if (voice) { utterance.voice = voice; utterance.lang = voice.lang; }
      else utterance.lang = 'ru-RU';

      /*
         Те же величины, что и у агента, но в единицах Web Speech: темп и тон
         здесь множители от единицы, а не проценты и полутоны.

         Глубина учитывается в обеих: у браузерного синтеза нет выхода в Web
         Audio — его нельзя ни замедлить при воспроизведении, ни обработать,
         только запустить и слушать как есть. Поэтому то, что в основном тракте
         делает замедление, тут приходится приближать разметкой: тон ниже,
         темп медленнее. Форманты при этом не сдвинутся, и голос останется
         заметно мельче — это запасной вариант, а не равноценная замена.
      */
      const deeper = (1 - (Number(settings.depth) || 1)) * 4;      // 0.91 → примерно 0.36
      utterance.rate = Math.max(0.5, Math.min(1.6, (1 + settings.rate / 100) * (Number(settings.depth) || 1)));
      utterance.pitch = Math.max(0.5, Math.min(1.5, 1 + settings.pitch / 12 - deeper * 0.5));
      utterance.volume = settings.volume;

      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      utterance.onend = finish;
      utterance.onerror = finish;
      current.utterance = utterance;
      if (state.epoch !== epoch) return finish();
      try { speechSynthesis.speak(utterance); } catch { finish(); }
    });
  }

  /* ================================================== 5. Очередь и режим == */

  const current = { source: null, utterance: null, epoch: 0 };
  const queue = [];
  let draining = false;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  /*
     Весь цикл обёрнут в try/finally, и это не перестраховка.

     На событие «замолчал» завязано снятие глушения микрофона: пока помощник
     говорит, прослушивание не слышит ничего, чтобы не поймать собственный
     голос. Если бы из цикла можно было выйти, не отправив это событие, —
     любая неожиданная ошибка внутри оставляла бы микрофон глухим навсегда, и
     выглядело бы это как «прослушивание перестало работать без причины».
     Пара, у которой начало без конца ломает не свою функцию, а чужую, обязана
     закрываться в finally.
  */
  async function drain() {
    if (draining) return;
    draining = true;

    if (!state.speaking) {
      state.speaking = true;
      emit(handlers.start);
    }

    try {
      while (queue.length) {
        const item = queue.shift();
        if (item.epoch !== state.epoch) continue;      // отменено, пока стояло в очереди

        // Срок «отсидки» вышел — пробуем агента снова: он мог просто быть
        // занят, а его голос заметно лучше браузерного.
        if (state.engine === 'browser' && state.retryAgentAt && Date.now() > state.retryAgentAt) {
          state.engine = 'agent';
          state.agentFails = 0;
          state.retryAgentAt = 0;
        }

        try {
          if (state.engine !== 'browser') {
            const buffer = await synthesize(item.text);
            if (item.epoch !== state.epoch) continue;
            state.engine = 'agent';
            state.agentFails = 0;
            await playBuffer(buffer, item.epoch);
          } else {
            await speakInBrowser(item.text, item.epoch);
          }
        } catch (error) {
          state.lastError = error.message;

          /*
             Переход на голос браузера — не с первого отказа и не навсегда.

             Сначала было именно так: одна ошибка агента — и до конца сеанса
             говорит браузер. Логика была в том, чтобы не платить таймаутом на
             каждой фразе. Но отказ агента чаще всего мимолётный: он
             однопоточный, и пока он ищет файлы по дискам или делает скриншот,
             запрос на озвучку может не дождаться очереди. Наказывать за это
             понижением голоса до конца сеанса — несоразмерно: человек один раз
             попал в неудачную секунду и дальше слушает заметно худший синтез,
             не понимая, почему голос вдруг стал другим.

             Теперь нужны два отказа подряд, а через минуту агента пробуют
             снова. Цена ошибки — одна фраза браузерным голосом; цена прежнего
             поведения — весь сеанс.
          */
          if (state.engine !== 'browser') {
            state.agentFails++;
            if (state.agentFails >= 2) {
              state.engine = 'browser';
              state.retryAgentAt = Date.now() + 60000;
            }
          }
          try { await speakInBrowser(item.text, item.epoch); } catch { /* и этот молчит */ }
        }

        // Пауза между фразами. Ровно она отличает речь от чтения: без неё
        // синтезатор выпаливает абзац на одном дыхании.
        if (queue.length && item.epoch === state.epoch) await sleep(item.gap || 190);
      }
    } finally {
      draining = false;
      if (state.speaking && !queue.length) {
        state.speaking = false;
        emit(handlers.end);
      }
    }
  }

  /**
   * Произносит текст.
   *
   * @param {string} text
   * @param {object} [options] {force: озвучить даже при выключенном голосе}
   */
  function speak(text, options = {}) {
    if (!settings.enabled && !options.force) return false;
    const parts = phrases(text);
    if (!parts.length) return false;

    const epoch = state.epoch;
    parts.forEach((part, index) => {
      queue.push({ text: part, epoch, gap: index === parts.length - 1 ? 0 : 190 });
    });
    drain();
    return true;
  }

  /** Немедленно замолчать: и то, что играет, и то, что стоит в очереди. */
  function stop() {
    state.epoch++;
    queue.length = 0;
    if (current.source) {
      try { current.source.onended = null; current.source.stop(); } catch { /* уже кончилось */ }
      current.source = null;
    }
    if (global.speechSynthesis) { try { speechSynthesis.cancel(); } catch { /* нечего отменять */ } }
    if (state.speaking) {
      state.speaking = false;
      emit(handlers.end);
    }
  }

  /* ================================================== 6. Список голосов ==== */

  async function probe() {
    try {
      const response = await fetch(BASE + '/api/tts/voices', { cache: 'no-store' });
      const data = await response.json();
      if (data && data.available && Array.isArray(data.voices) && data.voices.length) {
        state.voices = data.voices;
        state.engine = 'agent';
        return { available: true, engine: 'agent', voices: state.voices };
      }
      state.lastError = (data && data.error) || 'агент не смог поднять синтез речи';
    } catch (error) {
      state.lastError = 'агент не отвечает';
    }

    // Агента нет — смотрим, что умеет браузер. Голоса он отдаёт не сразу:
    // список приходит событием, иногда через сотни миллисекунд после загрузки.
    const browser = await browserVoices();
    if (browser.length) {
      state.voices = browser.map((voice) => ({ name: voice.name, language: voice.lang, gender: '', browser: true }));
      state.engine = 'browser';
      return { available: true, engine: 'browser', voices: state.voices };
    }

    state.engine = 'none';
    return { available: false, engine: 'none', voices: [] };
  }

  function browserVoices() {
    return new Promise((resolve) => {
      if (!global.speechSynthesis) return resolve([]);
      const pick = () => (speechSynthesis.getVoices() || []).filter((voice) => voice.localService !== false);
      const first = pick();
      if (first.length) return resolve(first);
      const timer = setTimeout(() => resolve(pick()), 900);
      speechSynthesis.addEventListener('voiceschanged', () => {
        clearTimeout(timer);
        resolve(pick());
      }, { once: true });
    });
  }

  /* ==================================================== 7. Внешний интерфейс */

  global.Voice = {
    get enabled() { return settings.enabled; },
    get speaking() { return state.speaking; },
    get engine() { return state.engine; },
    get voices() { return state.voices; },
    get lastError() { return state.lastError; },
    get settings() { return Object.assign({}, settings); },

    speak,
    stop,
    probe,
    phrases,
    speakable,

    /**
     * Собирает звуковой тракт в переданном контексте.
     *
     * Нужно, чтобы его можно было прогнать в OfflineAudioContext и ИЗМЕРИТЬ,
     * что он делает со звуком: гребёнку от задержек, расхождение каналов,
     * потерю при сведении в моно, длину отзвука. На слух такие вещи спорны, а
     * испортить ими голос легче лёгкого.
     */
    buildGraph,

    get presets() {
      return Object.entries(PRESETS).map(([key, value]) => ({ key, label: value.label, note: value.note }));
    },
    get preset() { return settings.preset; },
    get presetNote() { return preset().note; },

    /** Переключает характер голоса, подставляя его просодию и тракт целиком. */
    usePreset(name) {
      if (!PRESETS[name]) return false;
      const chosen = PRESETS[name];
      this.set({ preset: name, rate: chosen.rate, pitch: chosen.pitch, depth: chosen.depth });
      return true;
    },

    set(patch) {
      const wasEnabled = settings.enabled;
      const wasPreset = settings.preset;
      Object.assign(settings, patch || {});
      settings.rate = Math.max(-50, Math.min(50, Number(settings.rate) || 0));
      settings.pitch = Math.max(-12, Math.min(12, Number(settings.pitch) || 0));
      settings.depth = Math.max(0.75, Math.min(1.25, Number(settings.depth) || 1));
      settings.volume = Math.max(0, Math.min(1, Number(settings.volume)));
      if (!PRESETS[settings.preset]) settings.preset = DEFAULTS.preset;
      save();

      // Тракт пересобирается только при смене характера: его узлы настроены
      // при сборке, а громкость и глубина применяются на лету.
      if (settings.preset !== wasPreset) rebuildChain();
      else if (chain.gain) chain.gain.gain.value = settings.volume;

      // Смена голоса или просодии обесценивает уже озвученное. Глубина —
      // не обесценивает: она применяется при воспроизведении, а не при
      // синтезе, и кеш от неё не зависит.
      if (patch && ('voice' in patch || 'rate' in patch || 'pitch' in patch)) cache.clear();
      if (wasEnabled && !settings.enabled) stop();
      return Object.assign({}, settings);
    },

    toggle() { return this.set({ enabled: !settings.enabled }).enabled; },

    /** Пробная фраза — чтобы услышать настройку, а не догадываться о ней. */
    preview(text) {
      stop();
      return speak(text || 'Все системы в норме, сэр. Реактор стабилен, периметр под наблюдением. Готов приступить к работе.', { force: true });
    },

    onStart(fn) { if (typeof fn === 'function') handlers.start.push(fn); },
    onEnd(fn) { if (typeof fn === 'function') handlers.end.push(fn); },

    DEFAULTS
  };
})(window);
