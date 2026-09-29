/* ============================================================================
   Cloud HDR — понимание запроса
   ----------------------------------------------------------------------------
   Гибридная схема: правила + нейросеть.

     • Правила (словари, регулярные выражения, расстояние Левенштейна) дают
       высокую точность там, где формулировка однозначна: «открой D:\Games»,
       «открой youtube.com», «закрой дискорд».
     • Нейросеть Brain обобщает всё остальное: понимает фразы, которых нет ни
       в одном словаре, терпит падежи, опечатки и свободный порядок слов.

   Когда оба источника согласны — уверенность растёт. Когда расходятся,
   выигрывает более уверенный, а разногласие попадает в trace, чтобы было
   видно, КАК принято решение. Результат бэкенд всё равно проверяет заново.
   ========================================================================== */
(function (global) {
  'use strict';

  /* --------------------------------------------------------- словари ---- */

  const APP_ALIASES = {
    roblox:    ['роблокс', 'роблокc', 'рублокс', 'роблоск', 'роблакс', 'roblox', 'рбх', 'робло'],
    discord:   ['дискорд', 'дискорт', 'диcкорд', 'дискордик', 'диск', 'дс', 'discord'],
    chrome:    ['хром', 'хроме', 'гугл хром', 'chrome', 'google chrome', 'браузер', 'гугл', 'browser'],
    edge:      ['эдж', 'едж', 'edge', 'microsoft edge', 'майкрософт эдж'],
    firefox:   ['фаерфокс', 'файрфокс', 'firefox', 'мозила', 'мозилла'],
    steam:     ['стим', 'стем', 'steam'],
    telegram:  ['телеграм', 'телеграмм', 'телега', 'тг', 'telegram', 'tg'],
    spotify:   ['спотифай', 'спотифи', 'spotify', 'музыку', 'музыка'],
    vscode:    ['вскод', 'вс код', 'вижуал студио', 'вижуал студио код', 'vscode', 'vs code', 'код', 'редактор кода'],
    explorer:  ['проводник', 'эксплорер', 'explorer', 'файлы', 'мой компьютер', 'этот компьютер'],
    notepad:   ['блокнот', 'нотпад', 'notepad'],
    calc:      ['калькулятор', 'калькулятр', 'калк', 'calc', 'calculator'],
    terminal:  ['терминал', 'консоль', 'командная строка', 'cmd', 'powershell', 'terminal'],
    settings:  ['настройки windows', 'параметры windows', 'настройки виндовс', 'панель управления'],
    taskmgr:   ['диспетчер задач', 'диспетчер', 'taskmgr', 'task manager'],
    paint:     ['пейнт', 'мспейнт', 'paint', 'рисовалка'],
    obs:       ['обс', 'obs', 'obs studio'],
    epicgames: ['эпик', 'эпик геймс', 'epic', 'epic games']
  };

  const APP_TITLES = {
    roblox: 'Roblox', discord: 'Discord', chrome: 'Google Chrome', edge: 'Microsoft Edge',
    firefox: 'Mozilla Firefox', steam: 'Steam', telegram: 'Telegram', spotify: 'Spotify',
    vscode: 'Visual Studio Code', explorer: 'Проводник', notepad: 'Блокнот', calc: 'Калькулятор',
    terminal: 'Терминал', settings: 'Параметры Windows', taskmgr: 'Диспетчер задач',
    paint: 'Paint', obs: 'OBS Studio', epicgames: 'Epic Games Launcher'
  };

  const APP_ICONS = {
    roblox: 'game', discord: 'chat', chrome: 'globe', edge: 'globe', firefox: 'globe',
    steam: 'game', telegram: 'chat', spotify: 'music', vscode: 'code', explorer: 'folder',
    notepad: 'note', calc: 'calc', terminal: 'code', settings: 'gear', taskmgr: 'gear',
    paint: 'brush', obs: 'video', epicgames: 'game'
  };

  const FOLDER_ALIASES = {
    desktop:   ['рабочий стол', 'рабочего стола', 'рабочем столе', 'десктоп', 'desktop'],
    downloads: ['загрузки', 'загрузок', 'загрузка', 'скачанное', 'скачанные', 'downloads', 'даунлоадс'],
    documents: ['документы', 'документов', 'доки', 'documents'],
    pictures:  ['изображения', 'картинки', 'фото', 'фотки', 'pictures', 'images'],
    music:     ['музыка', 'музыку', 'музыки', 'music'],
    videos:    ['видео', 'видосы', 'фильмы', 'videos'],
    home:      ['профиль', 'домашняя папка', 'домашнюю папку', 'юзер', 'home']
  };

  const FOLDER_TITLES = {
    desktop: 'Рабочий стол', downloads: 'Загрузки', documents: 'Документы',
    pictures: 'Изображения', music: 'Музыка', videos: 'Видео', home: 'Папка пользователя'
  };

  /* Сайты по имени. «Перейди на гугл карты» — это адрес, которого в запросе нет:
     домен надо знать, а не извлечь. Список ровно для этого, и он же — источник
     значений для обучения модели: cloudhdr_nlu читает его отсюда. */
  const SITE_ALIASES = {
    gmaps:      ['гугл карты', 'гугл карта', 'гугл мапс', 'google maps', 'google map', 'карты гугл', 'гуглокарты'],
    ymaps:      ['яндекс карты', 'яндекс карта', 'yandex maps', 'карты яндекса', 'яндекс мапс'],
    gis:        ['2гис', '2gis', 'дубльгис', 'двагис'],
    gmail:      ['гмейл', 'гмайл', 'gmail', 'гугл почта', 'почта гугл'],
    gdrive:     ['гугл диск', 'google drive', 'гугл драйв'],
    gdocs:      ['гугл документы', 'google docs', 'гугл докс'],
    translate:  ['гугл переводчик', 'переводчик', 'google translate', 'транслейт', 'гугл транслейт'],
    youtube:    ['ютуб', 'ютьюб', 'youtube', 'ютубчик', 'ю туб'],
    ytmusic:    ['ютуб музыка', 'youtube music', 'ютуб мьюзик'],
    github:     ['гитхаб', 'гит хаб', 'github'],
    chatgpt:    ['чатгпт', 'чат гпт', 'chatgpt', 'чатжпт'],
    claude:     ['клод', 'claude', 'клауд ии'],
    wiki:       ['википедия', 'википедию', 'вики', 'wikipedia'],
    vk:         ['вконтакте', 'вк', 'vk', 'вкантакте', 'в контакте'],
    telegramweb:['телеграм веб', 'telegram web', 'веб телеграм'],
    twitch:     ['твич', 'twitch'],
    kinopoisk:  ['кинопоиск', 'кинопоиск hd', 'kinopoisk'],
    rutube:     ['рутуб', 'rutube'],
    dzen:       ['дзен', 'dzen', 'яндекс дзен'],
    mailru:     ['майл ру', 'mail.ru', 'майл'],
    ozon:       ['озон', 'ozon'],
    wildberries:['вайлдберриз', 'вб', 'wildberries', 'вайлдбериз'],
    avito:      ['авито', 'avito'],
    aliexpress: ['алиэкспресс', 'али', 'aliexpress', 'алик'],
    dnsshop:    ['днс', 'днс шоп', 'dns shop'],
    hh:         ['хедхантер', 'хх ру', 'headhunter'],
    steamstore: ['магазин стима', 'steam store', 'стим магазин'],
    gosuslugi:  ['госуслуги', 'gosuslugi'],
    speedtest:  ['спидтест', 'speedtest', 'тест скорости']
  };

  const SITE_URLS = {
    gmaps: 'https://www.google.com/maps', ymaps: 'https://yandex.ru/maps',
    gis: 'https://2gis.ru', gmail: 'https://mail.google.com',
    gdrive: 'https://drive.google.com', gdocs: 'https://docs.google.com',
    translate: 'https://translate.google.com', youtube: 'https://www.youtube.com',
    ytmusic: 'https://music.youtube.com', github: 'https://github.com',
    chatgpt: 'https://chat.openai.com', claude: 'https://claude.ai',
    wiki: 'https://ru.wikipedia.org', vk: 'https://vk.com',
    telegramweb: 'https://web.telegram.org', twitch: 'https://www.twitch.tv',
    kinopoisk: 'https://www.kinopoisk.ru', rutube: 'https://rutube.ru',
    dzen: 'https://dzen.ru', mailru: 'https://mail.ru',
    ozon: 'https://www.ozon.ru', wildberries: 'https://www.wildberries.ru',
    avito: 'https://www.avito.ru', aliexpress: 'https://aliexpress.ru',
    dnsshop: 'https://www.dns-shop.ru', hh: 'https://hh.ru',
    steamstore: 'https://store.steampowered.com', gosuslugi: 'https://www.gosuslugi.ru',
    speedtest: 'https://www.speedtest.net'
  };

  const SITE_TITLES = {
    gmaps: 'Google Карты', ymaps: 'Яндекс Карты', gis: '2ГИС', gmail: 'Gmail',
    gdrive: 'Google Диск', gdocs: 'Google Документы', translate: 'Google Переводчик',
    youtube: 'YouTube', ytmusic: 'YouTube Music', github: 'GitHub', chatgpt: 'ChatGPT',
    claude: 'Claude', wiki: 'Википедия', vk: 'ВКонтакте', telegramweb: 'Telegram Web',
    twitch: 'Twitch', kinopoisk: 'Кинопоиск', rutube: 'RuTube', dzen: 'Дзен',
    mailru: 'Mail.ru', ozon: 'Ozon', wildberries: 'Wildberries', avito: 'Авито',
    aliexpress: 'AliExpress', dnsshop: 'DNS', hh: 'hh.ru', steamstore: 'Steam',
    gosuslugi: 'Госуслуги', speedtest: 'Speedtest'
  };

  /* Игры по-русски. Таблица дублирует ту, что живёт в агенте, и это осознанно:
     агент подставляет её при ЗАПУСКЕ, а браузеру она нужна раньше — на этапе
     «команда это или обрывок разговора». Без неё «открой кс го» для
     постоянного прослушивания — просто два неизвестных слова. */
  const GAME_ALIASES = {
    'Among Us':        ['амонг ас', 'амонгас', 'амонг ус', 'амонг', 'среди нас', 'among us', 'amongus'],
    // Падежи выписаны руками: «доту» и «дота» расстоянием не сближаются —
    // порог опечаток для четырёх букв равен нулю, иначе в игры полезло бы
    // полсловаря. А в речи винительный падеж как раз обычное дело.
    'Minecraft':       ['майнкрафт', 'майнкравт', 'майн', 'майна', 'minecraft'],
    'Counter-Strike':  ['кс го', 'ксго', 'кс 2', 'кс2', 'кску', 'контра', 'контру', 'counter strike', 'csgo', 'cs2', 'cs go'],
    'Grand Theft Auto':['гта', 'гта 5', 'гта5', 'гта 6', 'gta', 'grand theft auto'],
    'Fortnite':        ['фортнайт', 'фортнит', 'фортнайте', 'fortnite'],
    'Valorant':        ['валорант', 'валоранте', 'valorant'],
    'Dota':            ['дота', 'доту', 'дотку', 'дота 2', 'дота2', 'dota'],
    'Terraria':        ['террария', 'terraria'],
    'Stardew Valley':  ['стардью', 'стардев', 'stardew'],
    'Genshin Impact':  ['геншин', 'genshin'],
    'Brawl Stars':     ['бравл старс', 'бравл', 'brawl stars'],
    'Cyberpunk 2077':  ['киберпанк', 'киберпанк 2077', 'сайберпанк', 'cyberpunk'],
    'The Witcher':     ['ведьмак', 'witcher'],
    'PUBG':            ['пабг', 'пубг', 'pubg'],
    'Apex Legends':    ['апекс', 'apex legends'],
    'Rust':            ['раст', 'rust'],
    'Subnautica':      ['субнавтика', 'субнатика', 'сабнатика', 'subnautica'],
    'Fall Guys':       ['фолл гайс', 'фал гайз', 'fall guys'],
    'Phasmophobia':    ['фазмофобия', 'phasmophobia'],
    "Garry's Mod":     ['гарис мод', 'гаррис мод', 'гмод', 'garrys mod', 'gmod'],
    'BeamNG.drive':    ['бимка', 'бимэнджи', 'бим эн джи', 'beamng'],
    'Resident Evil':   ['резидент ивел', 'резик', 'обитель зла', 'resident evil'],
    'Geometry Dash':   ['геометри даш', 'geometry dash'],
    'Roblox':          ['роблокс', 'рублокс', 'roblox']
  };

  const OPEN_VERBS = ['открой', 'откройте', 'открыть', 'открои', 'откраой', 'запусти', 'запустить', 'включи', 'включить', 'врубай', 'врубить', 'стартуй', 'давай', 'open', 'launch', 'start', 'run'];
  const CLOSE_VERBS = ['закрой', 'закрыть', 'заверши', 'завершить', 'выключи', 'убей', 'вырубай', 'close', 'kill', 'quit', 'exit'];
  const SEARCH_VERBS = ['загугли', 'погугли', 'search', 'google'];
  const SCAN_VERBS = ['найди', 'найти', 'поищи', 'ищи', 'просканируй', 'сканируй', 'отыщи', 'find', 'scan', 'locate'];

  /* ------------------------------------------------ команда или разговор -- */
  //
  // Действие запускается только по прямой просьбе. Различает просьбу и разговор
  // не смысл, а ФОРМА: команда стоит в повелительном наклонении и начинается
  // глаголом. «Открой стим» — команда. «Что лучше открыть, стим или эпик»,
  // «не могу открыть стим», «ты умеешь открывать программы» — разговор, хотя
  // слово одно и то же.
  //
  // Прежде эту разницу не проверял никто: намерение предлагала сеть по общему
  // виду фразы, и любое упоминание программы, файла или игры оборачивалось
  // карточкой действия. Разговор на этом кончался — вместо ответа человек
  // получал «Открываю приложение» или полминуты сканирования дисков.
  //
  // Цена ошибки несимметрична, поэтому правило строгое. Не понятая команда —
  // это один переспрос. Команда, увиденная в обычной фразе, — оборванный
  // разговор и выполненное действие, о котором не просили.

  //: Что разрешено стоять перед глаголом: обращения, вежливость, связки.
  const COMMAND_PREFIX = /^(?:(?:а|и|ну|эй|слушай|слышь|окей|ok|ладно|пожалуйста|плиз|давай|давай-ка|теперь|потом|сначала|затем|быстро|срочно|можешь|сможешь|попробуй|надо|нужно)[\s,]+)*/;

  //: Какими словами вызывается каждое действие. Ничем другим — не вызывается.
  const COMMAND_VERBS = {
    scan_files:  SCAN_VERBS.concat(['поиск', 'разыщи', 'поищем']),
    open_app:    OPEN_VERBS,
    open_folder: OPEN_VERBS.concat(['покажи', 'зайди', 'перейди']),
    open_url:    OPEN_VERBS.concat(['зайди', 'перейди', 'открывай']),
    close_app:   CLOSE_VERBS,
    web_search:  SEARCH_VERBS.concat(SCAN_VERBS)
  };

  //: Единственное исключение: список установленного спрашивают вопросом, а не
  //: приказом — «какие у меня игры». На это заведены и карточка на
  //: приветственном экране, и кнопка «Мои игры» под строкой ввода.
  const SCAN_LIST_RE = /(как\p{L}*\s+(у\s+меня\s+)?(игр|программ)|список\s+(игр|программ)|покажи\s+(все\s+)?игр|мои\s+игр|что\s+(у\s+меня\s+)?установлен)/u;

  //: Чем подсказать, если действие не запустилось из-за формы фразы.
  const COMMAND_HINT = {
    scan_files:  'Искать на компьютере — «найди файл …», «найди игру …» или кнопка «Найти на ПК» под строкой ввода.',
    open_app:    'Запустить программу — «открой хром».',
    open_folder: 'Открыть папку — «открой папку загрузки».',
    open_url:    'Открыть сайт — «открой youtube.com».',
    close_app:   'Закрыть программу — «закрой дискорд».',
    web_search:  'Поискать в браузере — «загугли …».'
  };

  //: Прямой вопрос о себе — и только он. Всё остальное, что начинается с «ты»,
  //: разговор: «ты умеешь готовить борщ» — это вопрос про борщ.
  const SELF_QUESTION = /(^|\s)(кто\s+ты|ты\s+кто|ты\s+сам(а)?\s+кто|кто\s+ты\s+сам(а)?|что\s+ты\s+такое?|как\s+тебя\s+зовут|как\s+тебя\s+называть|твое\s+имя|представься|расскажи\s+о\s+себе|что\s+ты\s+за\s+(модель|программа|нейросет\p{L}*|штука)|ты\s+(кто|что)\s+так(ой|ая|ое)|ты\s+(бот|робот|человек|программа|машина|нейросет\p{L}*|нейронка|ии|ai|чатгпт|chatgpt|gpt|живой|настоящий)|кто\s+тебя\s+(создал|сделал|написал|обучил)|на\s+чем\s+ты\s+(работаешь|написан)|как\s+ты\s+устроен|сколько\s+у\s+тебя\s+(параметров|весов)|чем\s+ты\s+отличаешься)(\s|$)/u;

  //: Чистое приветствие и чистое спасибо — без вопроса или просьбы следом.
  const PURE_GREETING = /^(привет\p{L}*|здравствуй(те)?|здрасьте|добр(ый|ое|ой|ого)\s+(день|утро|вечер|ночи)|доброе|хай|хелло|хеллоу|салют|здорово|ку|йо|hi|hello|hey)([\s,!.)]*(клауд|cloud|бро|друг))?[\s,!.)?]*$/iu;
  const PURE_THANKS = /^(спасибо|спс|пасиб\p{L}*|благодарю|сенкс|thanks?|thx)(\s+(большое|огромное|тебе|вам|друг|бро|клауд))*[\s,!.)]*$/iu;

  /* ---------------------------------------------------- вспомогательное -- */

  function normalize(text) {
    return String(text == null ? '' : text)
      .toLowerCase()
      .replace(/ё/g, 'е')
      .replace(/[!?.,;:«»"'`]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Расстояние Левенштейна — терпимость к опечаткам («роблокc», «дискорт»). */
  function levenshtein(a, b) {
    if (a === b) return 0;
    if (!a.length || !b.length) return Math.max(a.length, b.length);
    let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const row = [i];
      for (let j = 1; j <= b.length; j++) {
        row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      }
      prev = row;
    }
    return prev[b.length];
  }

  /** Порог опечаток растёт вместе с длиной слова. */
  function tolerance(word) {
    if (word.length <= 4) return 0;
    if (word.length <= 7) return 1;
    return 2;
  }

  /**
   * Ищет ключ в словаре алиасов: точное вхождение → подстрока → фаззи.
   * @returns {{key: string, score: number}|null}
   */
  function matchDictionary(phrase, dictionary) {
    const text = normalize(phrase);
    if (!text) return null;
    let best = null;

    for (const [key, aliases] of Object.entries(dictionary)) {
      for (const alias of [key, ...aliases]) {
        if (text === alias) return { key, score: 1 };
        if (text.startsWith(alias + ' ') || text.endsWith(' ' + alias) || text.includes(' ' + alias + ' ')) {
          if (!best || best.score < 0.9) best = { key, score: 0.9 };
        }
        if (alias.length > 3 && text.includes(alias) && (!best || best.score < 0.8)) {
          best = { key, score: 0.8 };
        }
      }
    }
    if (best) return best;

    for (const word of text.split(' ')) {
      if (word.length < 3) continue;
      for (const [key, aliases] of Object.entries(dictionary)) {
        for (const alias of [key, ...aliases]) {
          if (alias.includes(' ')) continue;
          const distance = levenshtein(word, alias);
          if (distance <= tolerance(alias)) {
            const score = 0.75 - distance * 0.1;
            if (!best || score > best.score) best = { key, score };
          }
        }
      }
    }
    return best;
  }

  const FILLERS = /^(мне|пожалуйста|плиз|давай|ка|быстро|срочно)\s+/i;

  function stripVerb(text, verbs) {
    let rest = text;
    for (const verb of verbs) {
      if (rest === verb) return '';
      if (rest.startsWith(verb + ' ')) { rest = rest.slice(verb.length + 1); break; }
    }
    return rest.replace(FILLERS, '').trim();
  }

  /** То же, но по ИСХОДНОЙ строке: сохраняет регистр, точки и слэши (пути, адреса). */
  function stripVerbRaw(rawTrim, verbs) {
    const pattern = new RegExp('^\\s*(' + verbs.join('|') + ')\\s+', 'i');
    return rawTrim.replace(pattern, '').replace(FILLERS, '').trim();
  }

  /**
   * Замена «\b» для кириллицы: в JS \w — только ASCII, поэтому /привет\b/
   * никогда не совпадёт. EDGE проверяет, что дальше нет буквы или цифры.
   */
  const EDGE = '(?![a-zа-я0-9])';

  const FOLDER_WORD = /^(папку|папка|папке|директорию|директория|каталог|folder|directory)\s+/i;
  const looksLikePath = (value) => /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith('\\') || value.includes('\\');
  const hasVerb = (text, verbs) => verbs.some((verb) => text === verb || text.startsWith(verb + ' '));

  /** Сказано ли это командой — глаголом из списка, не считая вежливых зачинов. */
  const isCommand = (text, verbs) => hasVerb(text.replace(COMMAND_PREFIX, ''), verbs);

  /**
   * Можно ли запускать действие, о котором догадался разборщик.
   *
   * Проверка одна на оба разборщика — и на встроенную сеть, и на модель spaCy:
   * догадка у них разная, а ошибка одинаковая. Намерения, которых нет в списке
   * (громкость, скриншот, навыки, разговор), не ограничиваются: они короткие,
   * однословные и в обычной фразе не встречаются.
   */
  function commandAllowed(intent, text) {
    const verbs = COMMAND_VERBS[intent];
    if (!verbs) return true;
    if (isCommand(text, verbs)) return true;
    return intent === 'scan_files' && SCAN_LIST_RE.test(text);
  }

  /** Фраза без команды — это разговор: отвечает модель, а без неё — заготовка. */
  function asChat(intent, p, trace) {
    const hint = COMMAND_HINT[intent];
    return {
      intent: 'chat', confidence: Math.min(p, 0.5), freeform: true, icon: 'chat',
      reply: 'Отвечаю как на обычный вопрос — действия я выполняю по прямой просьбе.' +
             (hint ? '\n' + hint : ''),
      trace: (trace || []).concat(`«${intent}» без команды — это разговор`)
    };
  }

  // Кириллические зоны: «рф» была изначально, «ру» и «сайт» добавлены — без них
  // «открывай хабр.ру» уходило в запуск программы, а не в браузер.
  const URL_RE = /^(https?:\/\/)?([a-zа-я0-9-]+\.)+(ru|com|net|org|io|dev|me|tv|gg|app|xyz|info|biz|ua|by|kz|su|рф|ру|сайт|орг|дети)(\/\S*)?$/i;
  const PATH_RE = /^[a-zA-Z]:[\\/]/;

  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  /* ================================================ 1. Правила высокой точности */

  /**
   * Возвращает разбор, если формулировка однозначна, иначе null.
   * Здесь живёт только то, в чём словарь надёжнее нейросети.
   */
  function ruleParse(rawTrim, text, context) {
    /* --- системные действия с фиксированными формулировками --- */
    if (new RegExp('(скриншот|снимок экрана|screenshot|скрин)' + EDGE).test(text)) {
      return { intent: 'screenshot', title: 'Снимок экрана', icon: 'camera', confidence: .95,
        reply: 'Делаю снимок экрана и сохраняю его в «Изображения\\CloudHDR».' };
    }
    // «Включи звук» — раньше этого правила не было, и фраза уходила в «открой
    // программу», а там нашлась «Звукозапись».
    if (/^(включи|верни|вруби|врубай)\s+(звук|громкость)|^(убери|сними)\s+(без\s+звука|беззвучн)|^unmute$/.test(text)) {
      return { intent: 'volume_unmute', title: 'Звук включён', icon: 'volume', confidence: .95, reply: 'Включаю звук.' };
    }
    if (/(выключи|отключи|убери|выруби|вырубь|вырубай)\s*(звук|громкость)|(без звука|mute|мьют)/.test(text)) {
      return { intent: 'volume_mute', title: 'Звук выключен', icon: 'volume', confidence: .95, reply: 'Выключаю звук.' };
    }
    if (/(громче|повыс[а-яё]* громкость|прибавь звук|увеличь громкость|volume up)/.test(text)) {
      return { intent: 'volume_up', title: 'Громкость +', icon: 'volume', confidence: .95, reply: 'Делаю громче.' };
    }
    if (/(тише|потише|убавь звук|уменьши громкость|понизь громкость|volume down)/.test(text)) {
      return { intent: 'volume_down', title: 'Громкость −', icon: 'volume', confidence: .95, reply: 'Делаю тише.' };
    }
    if (/(открепи|отцепи|сними закрепление|убери закрепление|unpin)/.test(text)) {
      return { intent: 'window_unpin', title: 'Окно откреплено', icon: 'pin', confidence: .9,
        reply: 'Открепляю окно — оно больше не поверх остальных.' };
    }
    if (/(закрепи|прикрепи|зафиксируй)( окно| приложение| сверху)?|поверх (всех )?окон|always on top/.test(text)) {
      return { intent: 'window_pin', title: 'Окно поверх остальных', icon: 'pin', confidence: .9,
        reply: 'Закрепляю окно Cloud HDR поверх остальных — теперь оно всегда на виду.' };
    }
    if (/(заблокируй|заблокировать|блокировка)\s*(пк|компьютер|экран|windows)?|lock (pc|screen)/.test(text)) {
      return { intent: 'lock', title: 'Блокировка Windows', icon: 'lock', confidence: .9, needsConfirm: true,
        reply: 'Блокирую рабочую станцию — войдите заново, когда вернётесь.' };
    }
    if (/(выключи|выключить|перезагрузи|перезагрузка)\s*(пк|компьютер|систему|windows)|удали\s+(все\s+)?файл|отформатируй/.test(text)) {
      return { intent: 'denied', confidence: 1,
        reply: 'Форматирование дисков и безвозвратное удаление я намеренно не выполняю — это ограничение безопасности Cloud HDR. ' +
          'Выключить или перезагрузить компьютер могу — скажите «выключи компьютер», я переспрошу. Заблокировать экран — «заблокируй пк».' };
    }

    /* --- справка и вопрос «кто ты»: формулировки устойчивые, словарь надёжнее --- */
    if (/(что ты умеешь|что ты можешь|твои возможности|список команд|покажи (все )?команды|какие есть функции|^помощь$|^help$|what can you do)/.test(text)) {
      return { intent: 'help', confidence: .95, reply: HELP_TEXT };
    }

    /* --- явный поиск в вебе: «загугли …» --- */
    if (hasVerb(text, SEARCH_VERBS) || /^найди\s+в\s+(гугле|google|интернете|сети|яндексе|браузере)\s+/.test(text)) {
      const query = stripVerbRaw(rawTrim, SEARCH_VERBS.concat(['найди', 'найти', 'поищи']))
        .replace(/^(в\s+)?(гугле|google|интернете|сети|яндексе|браузере)\s+/i, '')
        .trim();
      if (query) {
        return { intent: 'web_search', target: query, query, title: 'Поиск: ' + query, icon: 'globe', confidence: .92,
          reply: `Ищу «${query}» в браузере.` };
      }
    }

    /* --- закрытие приложения ---
       Подтверждения здесь нет: просьба закрыть программу и есть подтверждение.
       Раньше на «закрой дискорд» открывалось окно «вы уверены?», а следом ещё и
       агент отвечал «требуется подтверждение пользователя» — помощник дважды
       переспрашивал о том, что человек только что сказал вслух. Действие
       обратимо: программа открывается заново той же фразой. */
    if (hasVerb(text, CLOSE_VERBS)) {
      const rest = stripVerb(text, CLOSE_VERBS);
      // «закрой его» — берём приложение из предыдущей реплики
      if (/^(его|это|их|ее|окно)$/.test(rest) && context && context.lastApp) {
        return { intent: 'close_app', target: context.lastApp.key, title: context.lastApp.title,
          icon: context.lastApp.icon || 'app', confidence: .8, anaphora: true,
          reply: `Закрываю ${context.lastApp.title}.` };
      }
      const app = matchDictionary(rest, APP_ALIASES);
      if (app) {
        return { intent: 'close_app', target: app.key, title: APP_TITLES[app.key], icon: APP_ICONS[app.key],
          confidence: app.score,
          reply: `Закрываю ${APP_TITLES[app.key]}.` };
      }
      // Название не из словаря — не отказ, а работа для агента: он поищет
      // среди открытого прямо сейчас. Так закрываются программы, которых нет
      // в списке из двух десятков известных.
      if (rest && rest.length >= 2 && rest.split(' ').length <= 4) {
        return { intent: 'close_app', target: rest, title: rest, icon: 'app', confidence: .72,
          reply: `Закрываю «${rest}».` };
      }
    }

    /* --- открытие: папка / приложение / сайт --- */
    if (hasVerb(text, OPEN_VERBS) || new RegExp('^(папк[уа]|folder)' + EDGE).test(text)) {
      const rest = stripVerb(text, OPEN_VERBS);
      const restRaw = stripVerbRaw(rawTrim, OPEN_VERBS);

      const bareFolderWord = /^(папку|папка|папке|директорию|директория|каталог|folder|directory)$/i.test(restRaw);
      if (FOLDER_WORD.test(restRaw) || bareFolderWord) {
        const name = bareFolderWord ? '' : restRaw.replace(FOLDER_WORD, '').trim();
        if (!name) {
          return { intent: 'ask', confidence: .5, reply: 'Какую папку открыть? Например: «открой папку загрузки» или «открой папку D:\\Games».' };
        }
        if (!looksLikePath(name)) {
          const named = matchDictionary(name, FOLDER_ALIASES);
          if (named && named.score >= 0.7) {
            return { intent: 'open_folder', target: named.key, title: FOLDER_TITLES[named.key], icon: 'folder',
              confidence: named.score, reply: `Открываю папку «${FOLDER_TITLES[named.key]}» в Проводнике.` };
          }
        }
        return { intent: 'open_folder', target: name, title: name, icon: 'folder', confidence: .7,
          reply: `Открываю папку «${name}» в Проводнике.` };
      }

      // Сайт по имени проверяется ДО приложений, и это не произвол. «Гугл» —
      // псевдоним Chrome в APP_ALIASES, поэтому «открой гугл карты» без этой
      // проверки уходило бы в запуск браузера, а не на maps.google.com. Порог
      // 0,8 не даёт одному слову «гугл» перетянуть на себя весь запрос.
      const siteMatch = matchDictionary(rest, SITE_ALIASES);
      if (siteMatch && siteMatch.score >= 0.8) {
        return { intent: 'open_url', target: SITE_URLS[siteMatch.key], site: siteMatch.key,
          title: SITE_TITLES[siteMatch.key], icon: 'globe', confidence: siteMatch.score,
          reply: `Открываю ${SITE_TITLES[siteMatch.key]}.` };
      }

      const bareFolder = matchDictionary(rest, FOLDER_ALIASES);
      const appMatch = matchDictionary(rest, APP_ALIASES);
      if (bareFolder && bareFolder.score >= 0.9 && (!appMatch || bareFolder.score > appMatch.score)) {
        return { intent: 'open_folder', target: bareFolder.key, title: FOLDER_TITLES[bareFolder.key], icon: 'folder',
          confidence: bareFolder.score, reply: `Открываю «${FOLDER_TITLES[bareFolder.key]}».` };
      }
      if (appMatch && appMatch.score >= 0.55) {
        return { intent: 'open_app', target: appMatch.key, title: APP_TITLES[appMatch.key], icon: APP_ICONS[appMatch.key],
          confidence: appMatch.score, reply: pick([
            `Запускаю ${APP_TITLES[appMatch.key]}…`,
            `Открываю ${APP_TITLES[appMatch.key]} — секунду.`,
            `Готово, запускаю ${APP_TITLES[appMatch.key]}.`
          ]) };
      }
      if (PATH_RE.test(restRaw)) {
        return { intent: 'open_folder', target: restRaw, title: restRaw, icon: 'folder', confidence: .8,
          reply: `Открываю ${restRaw} в Проводнике.` };
      }
      if (URL_RE.test(restRaw)) {
        return { intent: 'open_url', target: restRaw, title: restRaw, icon: 'globe', confidence: .85,
          reply: `Открываю ${restRaw} в браузере.` };
      }
      if (restRaw && !/^(настройк|настроек|параметр)/.test(rest)) {
        return { intent: 'open_app', target: restRaw, title: restRaw, icon: 'app', confidence: .5,
          reply: `Ищу «${restRaw}» среди установленных программ и запускаю…` };
      }
    }

    /* --- вопрос о мире, а не о компьютере --- */
    // Проверяется ПОСЛЕ команд, чтобы «расскажи как открыть папку» осталось
    // командой. Формулировки узкие и с командами не пересекаются: «что такое…»
    // не спутать с «что ты умеешь», а «почему…» вообще не начинает команд.
    // Дополнительная страховка: если внутри есть глагол действия, это всё-таки
    // просьба сделать, а не объяснить («расскажи как открыть папку загрузки»).
    const HAS_COMMAND_VERB = /(открой|открыть|запусти|запустить|включи|включить|закрой|закрыть|найди|найти|поищи|просканируй|скриншот|заблокируй|громче|тише)/.test(text);

    // Список зачинов был вдвое короче и покрывал только «объясняющие» вопросы:
    // «почему…», «что такое…», «чем отличается…». Обычный вопрос «что лучше,
    // видеокарта или процессор» под него не подходил, проваливался к
    // классификатору, тот с низкой уверенностью выбирал что-нибудь из своих
    // 27 намерений — и пользователь получал заготовку модуля памяти вместо
    // ответа. Отсюда добавлены сравнения, советы, вопросы выбора и количества.
    const WORLD_QUESTION = new RegExp(
      '^(' + [
        // объяснения
        'почему', 'зачем', 'отчего', 'объясни', 'поясни', 'что такое',
        'кто такой', 'кто такая', 'кто такие', 'как работает', 'как устроен',
        'как устроена', 'правда ли', 'откуда бер[ёе]тся', 'для чего нужен',
        // сравнения и выбор
        'что лучше', 'что выгоднее', 'что важнее', 'что быстрее', 'что надежнее',
        'что над[её]жнее', 'какой лучше', 'какая лучше', 'какое лучше',
        'какой выбрать', 'какую выбрать', 'какое выбрать', 'что выбрать',
        'чем отличается', 'чем отличаются', 'в ч[ёе]м разница', 'в ч[ёе]м отличие',
        'лучше ли', 'стоит ли', 'нужно ли', 'можно ли', 'бывает ли',
        // советы
        'посоветуй', 'подскажи', 'как выбрать', 'как научиться', 'как сделать так',
        'что делать если', 'что делать при', 'как лучше',
        // количество и факты
        'сколько стоит', 'сколько нужно', 'сколько времени', 'сколько лет',
      ].join('|') + ')'
    );

    // Отдельно — вопросы выбора со словом внутри: «какой ПРОЦЕССОР выбрать»,
    // «что за ПРОГРАММУ поставить». Списком зачинов такое не покрыть, потому что
    // между вопросительным словом и глаголом стоит произвольное существительное.
    // Команды сюда не попадают: глагол действия отсекается проверкой выше
    // («какой файл открыть» останется командой).
    const CHOICE_QUESTION =
      /^(как(ой|ая|ое|ие|ую|их|им|ом)|что)\s+\S+\s+(выбрать|лучше|взять|купить|поставить|посоветуешь)/.test(text);

    if (!HAS_COMMAND_VERB &&
        (WORLD_QUESTION.test(text) || CHOICE_QUESTION ||
         /^расскажи\s+(мне\s+)?(о|про|как|что|почему|зачем)\s/.test(text))) {
      // Этот ответ виден ТОЛЬКО когда модель разговора не поднята: при живой
      // модели вопрос уходит к ней (app.js, шаг 4), и заготовка не показывается.
      // Прежний текст отправлял «включить модель в ⚙ Настройках», но включать
      // там нечего — панель справочная, а модель поднимается отдельным
      // процессом. Человек шёл в настройки, не находил переключателя и решал,
      // что приложение сломано.
      return { intent: 'chat', confidence: .9, freeform: true, icon: 'chat',
        reply: 'Это вопрос о мире, а не о вашем компьютере. Ответить на него может модель ' +
               'разговора, но сейчас она не запущена — внутри меня работает только ' +
               'классификатор команд.\n\n' +
               'Поднять модель: двойной клик по start-ai.bat в папке cloud-hdr. ' +
               'Или вручную: cd cloud-hdr\\python, затем python -m cloudhdr_ai serve. ' +
               'Загрузка весов занимает 10–30 секунд, после неё обновите страницу.\n\n' +
               'Всё считается на этом компьютере: ни интернета, ни ключей, ни чужих серверов.' };
    }

    /* --- голая ссылка или путь --- */
    if (URL_RE.test(rawTrim)) {
      return { intent: 'open_url', target: rawTrim, title: rawTrim, icon: 'globe', confidence: .8, reply: `Открываю ${rawTrim}.` };
    }
    if (PATH_RE.test(rawTrim)) {
      return { intent: 'open_folder', target: rawTrim, title: rawTrim, icon: 'folder', confidence: .75,
        reply: `Открываю ${rawTrim} в Проводнике.` };
    }

    /* --- название приложения без глагола: «роблокс» --- */
    const solo = matchDictionary(text, APP_ALIASES);
    if (solo && solo.score >= 0.85 && text.split(' ').length <= 3) {
      return { intent: 'open_app', target: solo.key, title: APP_TITLES[solo.key], icon: APP_ICONS[solo.key],
        confidence: solo.score * 0.9, reply: `Понял — запускаю ${APP_TITLES[solo.key]}.` };
    }

    return null;
  }

  /* ================================================ 2. Слоты для намерений сети */

  // Границы слова заданы явно: \b в JS работает только с ASCII и с кириллицей
  // не совпадает вообще — на этом уже спотыкались выше (см. константу EDGE).
  const NOISE_WORDS = /(?<![\p{L}\p{Nd}])(игру|игра|игры|игрой|файл|файлы|файлов|программу|программа|приложение|на\s+компьютере|на\s+компе|на\s+пк|на\s+диске|на\s+дисках|у\s+меня|мне|пожалуйста|где|которая|весь|всю|все|всё|мой|моем|моём)(?![\p{L}\p{Nd}])/gu;

  /**
   * Просят ли ИМЕННО поиск по компьютеру — или это обычный вопрос.
   *
   * Зачем проверка. Классификатор относил к поиску по дискам всё, где мелькало
   * «найди», «где», «ищи», — включая «найди мне занятие на вечер» и «где растёт
   * баобаб». Приложение уходило сканировать диски вместо ответа, и модель к
   * разговору не подключалась вовсе.
   *
   * Правило: одного глагола мало, нужен признак КОМПЬЮТЕРА — файл, игра,
   * программа, папка, диск, «на пк». Либо глагол, который ничего другого
   * означать не может: «просканируй», «сканируй».
   *
   * Цена ошибки несимметрична, поэтому правило строгое. Не распознали поиск —
   * человек переспросит словом «файл» или нажмёт кнопку, это секунда. Ушли
   * сканировать диски вместо ответа — это полминуты ожидания и ответ не по делу.
   */
  // Каждый корень обязан НАЧИНАТЬ слово — отсюда проверка слева.
  //
  // Без неё «игр» совпадало внутри «поиграть», и просьба «посоветуй, во что
  // поиграть вечером» уезжала сканировать диски вместо ответа. Тот же капкан
  // ждал «папк» в «папке» (это как раз нужно) и «программ» в «программировани»
  // (а это уже нет). \b в JS с кириллицей не работает вовсе, поэтому граница
  // задана явным просмотром назад — так же, как в NOISE_WORDS выше.
  const PC_MARKERS = new RegExp(
    '(?<![\\p{L}\\p{Nd}])(файл|документ|папк|каталог|директор|игр[аыуоеи]|игр(?![\\p{L}])|' +
    'программ[аыуеой]|приложени|ярлык|exe|установлен|скачанн|загруж|' +
    'на\\s+компьютере|на\\s+компе|на\\s+пк|на\\s+диске|на\\s+дисках|' +
    'по\\s+диск|в\\s+системе|у\\s+себя\\s+на)', 'u');

  const UNAMBIGUOUS_SCAN = /^\s*(просканируй|сканируй|отсканируй|scan)\b/u;

  function looksLikePcSearch(text) {
    return UNAMBIGUOUS_SCAN.test(text) || PC_MARKERS.test(text);
  }

  /** Извлекает, ЧТО искать: «найди игру амонг ас на компе» → «амонг ас». */
  function scanSlots(rawTrim, text) {
    const games = /игр[аыуой]|игр$/u.test(text);
    const deep = /(все\s+файл|весь\s+компьютер|все\s+диск|по\s+всем\s+диск|полност|глубок|везде|повсюду)/u.test(text);
    const listAll = /(как\p{L}*\s+игр|покажи\s+(все\s+)?игр|список\s+игр|что\s+установлен|все\s+игр)/u.test(text);

    let query = stripVerbRaw(rawTrim, SCAN_VERBS)
      .replace(/^(мне|пожалуйста|-|—)\s*/i, '')
      .replace(/^(где\s+(находится|лежит|установлен[а-яё]*)|куда\s+установлен[а-яё]*)\s*/i, '')
      .replace(NOISE_WORDS, ' ')
      .replace(/\s+/g, ' ')
      .replace(/^[\s,.-]+|[\s,.?!-]+$/g, '')
      .trim();

    // «просканируй компьютер и найди амонг ас» — берём хвост после последнего «найди»
    const tail = /(?:найди|найти|отыщи|find)\s+(.+)$/i.exec(rawTrim);
    if (tail) {
      const candidate = tail[1].replace(NOISE_WORDS, ' ').replace(/\s+/g, ' ').trim();
      if (candidate.length >= 2) query = candidate;
    }

    return {
      query: query.length >= 2 ? query : '',
      mode: listAll || (games && !query) ? 'games' : 'files',
      deep: deep || listAll
    };
  }

  /** Достаёт цель для open_app из фразы, где нет знакомого глагола. */
  /**
   * Ищет в запросе сайт, названный по имени.
   *
   * Порог здесь выше обычного (0,8 против 0,6 у приложений) намеренно. Названия
   * сайтов пересекаются с названиями программ: «телеграм» — это и приложение, и
   * web.telegram.org, «стим» — и клиент, и магазин. Открывать браузер вместо
   * программы из-за нечёткого совпадения хуже, чем не узнать сайт: во втором
   * случае сработает обычный запуск, в первом пользователь получит не то.
   */
  /** Словесные уровни громкости: их человек называет не реже чисел. */
  const VOLUME_WORDS = {
    'максимум': 100, 'максималку': 100, 'полную': 100, 'полностью': 100,
    'минимум': 0, 'ноль': 0, 'нуля': 0, 'тишину': 0,
    'половину': 50, 'половина': 50, 'середину': 50, 'треть': 33, 'четверть': 25
  };

  /** Уровень громкости из фразы: число, процент или слово. */
  function volumeLevel(raw) {
    const lowered = String(raw || '').toLowerCase();
    for (const [word, value] of Object.entries(VOLUME_WORDS)) {
      if (lowered.includes(word)) return value;
    }
    const match = lowered.match(/(\d{1,3})\s*(?:%|процент[а-яё]*)?/);
    return match ? Math.min(100, Math.max(0, Number(match[1]))) : null;
  }

  function siteFromText(rawTrim, text) {
    const match = matchDictionary(text || rawTrim, SITE_ALIASES);
    return match && match.score >= 0.8 ? match.key : null;
  }

  function appSlot(rawTrim, text) {
    const dictionary = matchDictionary(text, APP_ALIASES);
    if (dictionary && dictionary.score >= 0.7) {
      return { target: dictionary.key, title: APP_TITLES[dictionary.key], icon: APP_ICONS[dictionary.key], confidence: dictionary.score };
    }
    const rest = stripVerbRaw(rawTrim, OPEN_VERBS.concat(['хочу', 'можешь', 'нужно', 'надо']))
      .replace(/^(открыть|запустить|включить)\s+/i, '')
      .replace(/(?<![а-яёa-z0-9_])(пожалуйста|мне|please)(?![а-яёa-z0-9_])/gi, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
    if (rest && rest.length <= 60) {
      return { target: rest, title: rest, icon: 'app', confidence: .5 };
    }
    return null;
  }

  /* ============================================================ 3. Слияние */

  const EXECUTABLE = new Set(['open_app', 'open_folder', 'open_url', 'web_search', 'close_app',
    'volume_up', 'volume_down', 'volume_mute', 'volume_unmute', 'volume_set', 'screenshot', 'lock',
    'window_pin', 'window_unpin', 'record_screen', 'record_stop', 'scan_files']);

  const HELP_TEXT =
    'Много всего! Вот что я умею прямо сейчас:\n\n' +
    '💬  Разговор — спрашивайте о чём угодно обычными словами, объясню и помогу с текстом\n' +
    '🗂  Порядок в файлах — «разбери загрузки», «разбери рабочий стол». Сначала покажу план,\n' +
    '     а всё сделанное верну одной кнопкой\n' +
    '🛡  Защита — «проверь компьютер»: вирусы, шпионы, майнеры, автозагрузка, подмена сайтов\n' +
    '     и сетевые подключения. Найденное — в карантин, откуда можно вернуть\n' +
    '🌐  Перевод — Ctrl+Alt+T в любой программе, обведите английский текст — перевод появится\n' +
    '     поверх. Без интернета\n' +
    '📱  Телефон — «подключи телефон»: покажу QR-код, и задачи можно давать с телефона\n' +
    '🖱  Руки — скажите «curs», и я сам поработаю мышью: открою, нажму, нарисую в Paint\n' +
    '🚀  Запуск любой программы — «открой роблокс», «запусти дискорд», «включи фотошоп».\n' +
    '     Ищу по всему, что установлено: меню «Пуск», классические программы и приложения Store\n' +
    '🔎  Поиск на дисках — «найди игру амонг ас», «просканируй все файлы и найди отчёт»,\n' +
    '     «какие игры у меня установлены». Нахожу через Steam, Epic, реестр и обход дисков\n' +
    '📁  Папки — «открой папку загрузки», «открой папку D:\\Games», «открой рабочий стол»\n' +
    '🎮  Игры Steam — «запусти в стиме кс 2», «включи амонг ас». Запускаю через Steam по appid\n' +
    '🌐  Браузер — «загугли погоду», «открой youtube.com», «перейди на гугл карты»\n' +
    '🎬  Запись экрана — «запиши экран», «останови запись». Файл ложится в «Видео\\Captures»\n' +
    '🧮  Повседневное — «посчитай 15% от 800», «переведи 5 км в метры», «сколько дней до нового года»\n' +
    '🧠  Память и анализ — «запомни, что мой ник Миро», «что ты обо мне помнишь»,\n' +
    '     «проанализируй наши разговоры», «что я просил чаще всего»\n' +
    '⏱  Таймеры — «поставь таймер на 5 минут»\n' +
    '🔊  Звук — «громче», «тише», «выключи звук»\n' +
    '📸  Экран — «сделай скриншот», «заблокируй пк»\n' +
    '👁  Зрение — покажите фото или скриншот (скрепка, перетаскивание или Ctrl+V),\n' +
    '     и я разберу свет, цвет, резкость, палитру и паспорт съёмки. Можно и словами:\n' +
    '     «разбери мой последний скриншот». Считаю прямо здесь, картинка никуда не уходит\n' +
    '❌  Закрыть — «закрой дискорд» (спрошу подтверждение)\n\n' +
    'Можно диктовать голосом — опечатки и падежи я прощаю. С чего начнём? ☁';

  /**
   * Главная точка входа.
   * @param {string} raw фраза пользователя
   * @param {object} [context] {lastApp, lastIntent, lastTarget}
   */
  function parse(raw, context) {
    const rawTrim = String(raw == null ? '' : raw).trim();
    const text = normalize(rawTrim);
    if (!text) return { intent: 'none', confidence: 0, trace: [], reply: 'Кажется, запрос пустой.' };

    const trace = [];

    // --- нейросеть ---
    const ranked = Brain.classify(text, 3);
    const netBest = ranked[0];
    trace.push(`сеть: ${netBest.intent} (${Math.round(netBest.p * 100)}%)` +
               (ranked[1] && ranked[1].p > 0.12 ? `, затем ${ranked[1].intent} (${Math.round(ranked[1].p * 100)}%)` : ''));

    // --- правила ---
    const rule = ruleParse(rawTrim, text, context);
    if (rule) trace.push(`правило: ${rule.intent} (${Math.round(rule.confidence * 100)}%)`);

    // Порог подобран под КАЛИБРОВАННЫЕ вероятности. После настройки температуры
    // сеть перестала выдавать «100% на всё»: типичный уверенный ответ — 0,45–0,85,
    // и прежний порог 0,62 означал бы, что сеть почти никогда не спорит с правилом.
    const NET_TRUSTED = 0.45;

    let result;
    if (rule && (rule.confidence >= 0.75 || rule.intent === netBest.intent || netBest.p < NET_TRUSTED)) {
      // словарь надёжен, либо оба согласны, либо сеть не уверена
      result = { ...rule, source: rule.intent === netBest.intent ? 'правило+сеть' : 'правило' };
      if (rule.intent === netBest.intent) {
        result.confidence = Math.min(0.99, rule.confidence + netBest.p * 0.1);
        trace.push('оба источника согласны — уверенность повышена');
      }
    } else if (rule && netBest.p >= NET_TRUSTED && netBest.p > rule.confidence) {
      trace.push(`расхождение: доверяю сети (${Math.round(netBest.p * 100)}% против ${Math.round(rule.confidence * 100)}%)`);
      result = fromNet(netBest, rawTrim, text, context);
      result.source = 'сеть';
    } else {
      result = fromNet(netBest, rawTrim, text, context);
      result.source = 'сеть';
    }

    result.trace = trace.concat(result.trace || []);
    result.alternatives = ranked.slice(1).filter((entry) => entry.p > 0.1).map((entry) => entry.intent);
    result.raw = rawTrim;
    if (result.confidence == null) result.confidence = netBest.p;
    return result;
  }

  /** Строит полный разбор из намерения, предсказанного сетью. */
  function fromNet(best, rawTrim, text, context) {
    const trace = [];

    // Сеть предлагает намерение по общему виду фразы: на «не могу открыть стим,
    // что делать» она предлагает запуск — по словам верно, по смыслу нет.
    // Форма фразы решает, просьба это или разговор.
    if (!commandAllowed(best.intent, text)) return asChat(best.intent, best.p, trace);

    switch (best.intent) {
      /* ------------------------------------------------- поиск на дисках -- */
      case 'scan_files': {
        // Сеть решила «поиск», но признаков компьютера в фразе нет — значит это
        // обычный вопрос, и отвечать на него должна модель, а не сканер дисков.
        if (!looksLikePcSearch(text)) {
          trace.push('похоже на вопрос, а не на поиск по компьютеру');
          return { intent: 'chat', confidence: Math.min(best.p, 0.5), freeform: true, icon: 'chat',
            reply: 'Это похоже на обычный вопрос. Если нужно искать на компьютере, скажите прямо: '
                 + '«найди файл …», «найди игру …» или нажмите «Найти на ПК» под строкой ввода.',
            trace };
        }

        const slots = scanSlots(rawTrim, text);
        trace.push(slots.query ? `цель поиска: «${slots.query}»` : 'цель не указана — покажу всё, что найду');
        if (slots.deep) trace.push('режим глубокого сканирования: больше корней и времени');

        const title = slots.query ? `Поиск: ${slots.query}` : slots.mode === 'games' ? 'Игры на компьютере' : 'Сканирование файлов';
        const reply = slots.query
          ? `Сканирую компьютер и ищу «${slots.query}».\nСмотрю библиотеки Steam и Epic, список установленных программ, затем прохожу по дискам.${slots.deep ? ' Режим глубокий — это займёт до полуминуты.' : ''}`
          : slots.mode === 'games'
            ? 'Собираю список игр: библиотеки Steam, Epic Games и установленные программы.'
            : 'Запускаю сканирование файлов. Скажите, что именно искать, — найду быстрее.';

        return { intent: 'scan_files', target: slots.query, query: slots.query, mode: slots.mode, deep: slots.deep,
          title, icon: 'search', confidence: best.p, reply, trace };
      }

      /* --------------------------------------------------- запуск и папки -- */
      case 'open_app': {
        const slot = appSlot(rawTrim, text);
        if (!slot) return ask('Что именно открыть? Например: «открой хром», «открой роблокс».', best.p, trace);
        trace.push(`цель запуска: «${slot.target}»`);
        return { intent: 'open_app', target: slot.target, title: slot.title, icon: slot.icon,
          confidence: Math.min(best.p, slot.confidence + 0.2),
          reply: slot.icon === 'app' ? `Ищу «${slot.target}» среди установленных программ и запускаю…` : `Запускаю ${slot.title}…`,
          trace };
      }
      case 'open_folder': {
        const rest = stripVerbRaw(rawTrim, OPEN_VERBS.concat(['покажи', 'зайди', 'перейди']))
          .replace(FOLDER_WORD, '').replace(/^(в|во)\s+/i, '').trim();
        const named = matchDictionary(rest || text, FOLDER_ALIASES);
        if (named && named.score >= 0.6) {
          trace.push(`именованная папка: ${FOLDER_TITLES[named.key]}`);
          return { intent: 'open_folder', target: named.key, title: FOLDER_TITLES[named.key], icon: 'folder',
            confidence: best.p, reply: `Открываю «${FOLDER_TITLES[named.key]}».`, trace };
        }
        if (rest) {
          return { intent: 'open_folder', target: rest, title: rest, icon: 'folder', confidence: best.p * 0.8,
            reply: `Открываю папку «${rest}» в Проводнике.`, trace };
        }
        return ask('Какую папку открыть? Например: «открой папку загрузки».', best.p, trace);
      }
      case 'open_url': {
        const candidate = (rawTrim.match(/(https?:\/\/\S+|(?:[a-z0-9-]+\.)+[a-z]{2,6}(?:\/\S*)?)/i) || [])[0];
        if (candidate) {
          return { intent: 'open_url', target: candidate, title: candidate, icon: 'globe', confidence: best.p,
            reply: `Открываю ${candidate} в браузере.`, trace };
        }
        // Домена в запросе нет — возможно, сайт назван по имени: «гугл карты».
        const site = siteFromText(rawTrim, text);
        if (site) {
          trace.push(`сайт по имени: ${SITE_TITLES[site]} → ${SITE_URLS[site]}`);
          return { intent: 'open_url', target: SITE_URLS[site], site, title: SITE_TITLES[site],
            icon: 'globe', confidence: best.p, reply: `Открываю ${SITE_TITLES[site]}.`, trace };
        }
        return ask('Какой сайт открыть? Например: «открой гугл карты» или «зайди на habr.com».', best.p, trace);
      }
      case 'web_search': {
        const query = stripVerbRaw(rawTrim, SCAN_VERBS.concat(['загугли', 'погугли']))
          .replace(/^(в\s+)?(гугле|google|интернете|сети|яндексе|браузере)\s+/i, '')
          .replace(/^(информацию|инфу)\s+(о|про)\s+/i, '')
          .trim();
        if (!query) return ask('Что найти в браузере?', best.p, trace);
        trace.push(`поисковый запрос: «${query}»`);
        return { intent: 'web_search', target: query, query, title: 'Поиск: ' + query, icon: 'globe',
          confidence: best.p, reply: `Ищу «${query}» в браузере.`, trace };
      }
      case 'close_app': {
        const rest = stripVerb(text, CLOSE_VERBS);
        if (/^(его|это|их|ее|окно)$/.test(rest) && context && context.lastApp) {
          return { intent: 'close_app', target: context.lastApp.key, title: context.lastApp.title,
            icon: context.lastApp.icon || 'app', confidence: best.p,
            reply: `Закрываю ${context.lastApp.title}.`, trace: trace.concat('цель взята из предыдущей реплики') };
        }
        const app = matchDictionary(rest || text, APP_ALIASES);
        if (app) {
          return { intent: 'close_app', target: app.key, title: APP_TITLES[app.key], icon: APP_ICONS[app.key],
            confidence: best.p, reply: `Закрываю ${APP_TITLES[app.key]}.`, trace };
        }
        // Незнакомое название уходит агенту как есть: он поищет его среди
        // открытых окон. Спрашивать «какое приложение закрыть?» там, где
        // название уже названо, — переспрашивать о сказанном.
        if (rest && rest.length >= 2 && rest.split(' ').length <= 4) {
          return { intent: 'close_app', target: rest, title: rest, icon: 'app', confidence: best.p,
            reply: `Закрываю «${rest}».`, trace: trace.concat('название ищется среди открытых окон') };
        }
        return ask('Какое приложение закрыть? Например: «закрой дискорд».', best.p, trace);
      }

      /* ---------------------------------------------- системные действия -- */
      case 'volume_up':   return { intent: 'volume_up', title: 'Громкость +', icon: 'volume', confidence: best.p, reply: 'Делаю громче.', trace };
      case 'volume_down': return { intent: 'volume_down', title: 'Громкость −', icon: 'volume', confidence: best.p, reply: 'Делаю тише.', trace };
      case 'volume_set': {
        // Уровень ищем и здесь: без сервиса разбор идёт в браузере, а команда
        // «поставь громкость на 30» без числа бессмысленна — это не «громче».
        const level = volumeLevel(rawTrim);
        if (level == null) {
          return ask('На сколько поставить громкость? Например: «поставь громкость на 30».', best.p, trace);
        }
        trace.push(`уровень громкости: ${level}%`);
        return { intent: 'volume_set', level, title: `Громкость ${level}%`, icon: 'volume',
          confidence: best.p, reply: `Ставлю громкость на ${level}%.`, trace };
      }
      case 'volume_mute': return { intent: 'volume_mute', title: 'Звук выключен', icon: 'volume', confidence: best.p, reply: 'Выключаю звук.', trace };
      case 'screenshot':  return { intent: 'screenshot', title: 'Снимок экрана', icon: 'camera', confidence: best.p,
        reply: 'Делаю снимок экрана и сохраняю его в «Изображения\\CloudHDR».', trace };
      case 'lock':        return { intent: 'lock', title: 'Блокировка Windows', icon: 'lock', confidence: best.p, needsConfirm: true,
        reply: 'Блокирую рабочую станцию — войдите заново, когда вернётесь.', trace };
      case 'window_pin':  return { intent: 'window_pin', title: 'Окно поверх остальных', icon: 'pin', confidence: best.p,
        reply: 'Закрепляю окно Cloud HDR поверх остальных.', trace };
      case 'window_unpin': return { intent: 'window_unpin', title: 'Окно откреплено', icon: 'pin', confidence: best.p,
        reply: 'Открепляю окно.', trace };
      case 'record_screen': return { intent: 'record_screen', title: 'Запись экрана', icon: 'video', confidence: best.p,
        reply: 'Включаю запись экрана. Готовый файл ляжет в «Видео\\Captures» — скажите «останови запись», когда закончим.',
        trace };
      case 'record_stop': return { intent: 'record_stop', title: 'Запись остановлена', icon: 'video', confidence: best.p,
        reply: 'Останавливаю запись и сохраняю файл.', trace };
      case 'denied': return { intent: 'denied', confidence: best.p, trace,
        reply: 'Форматирование дисков и безвозвратное удаление я намеренно не выполняю — это ограничение безопасности Cloud HDR. ' +
          'Выключить или перезагрузить компьютер могу — скажите «выключи компьютер», я переспрошу. Заблокировать экран — «заблокируй пк».' };

      /* ----------------------------------------------------------- навыки -- */
      case 'math': {
        const solved = Skills.math(rawTrim) || Skills.convert(rawTrim);
        if (solved) return { intent: 'chat', confidence: best.p, reply: solved.answer, trace: trace.concat(solved.trace) };
        return ask('Напишите выражение — например, «посчитай 15% от 800» или «12*7».', best.p, trace);
      }
      case 'convert': {
        const solved = Skills.convert(rawTrim) || Skills.math(rawTrim);
        if (solved) return { intent: 'chat', confidence: best.p, reply: solved.answer, trace: trace.concat(solved.trace) };
        return ask('Что перевести? Например: «переведи 5 км в метры».', best.p, trace);
      }
      case 'datetime': {
        const solved = Skills.datetime(rawTrim) || Skills.math(rawTrim);
        if (solved) return { intent: 'chat', confidence: best.p, reply: solved.answer, trace: trace.concat(solved.trace) };
        // Без точки в конце: дата по-русски сама кончается на «г.», и
        // вторая точка давала «2026 г..».
        const now = new Date();
        return { intent: 'chat', confidence: best.p, trace,
          reply: `Сейчас ${now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}, ` +
                 now.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', weekday: 'long' }) };
      }
      case 'remember': {
        const solved = Skills.remember(rawTrim);
        if (solved) return { intent: 'chat', confidence: best.p, reply: solved.answer, trace: trace.concat(solved.trace) };
        return ask('Что запомнить? Скажите: «запомни, что мой ник — Миро».', best.p, trace);
      }
      case 'recall': {
        const solved = Skills.recall(rawTrim);
        return { intent: 'chat', confidence: best.p, reply: solved.answer, trace: trace.concat(solved.trace) };
      }
      case 'timer':
        return { intent: 'timer', confidence: best.p, deferred: 'timer', title: 'Таймер', icon: 'bolt', trace, reply: '' };
      case 'analyze_chats':
        return { intent: 'analyze_chats', confidence: best.p, deferred: 'analyze', title: 'Анализ истории', icon: 'chat', trace, reply: '' };
      case 'settings':
        return { intent: 'settings', confidence: best.p, deferred: 'settings', title: 'Настройки', icon: 'gear', trace, reply: '' };

      /* ------------------------------------------------------- разговоры -- */
      /* Заготовка — только на чистое «привет» / «спасибо». Сеть относила к
         приветствию всё, что начинается с «привет», и «привет, объясни, как
         работает SSD» получало визитку вместо ответа. Остальное — модели
         (freeform), а заготовка остаётся запасной, если модели нет. */
      case 'greeting': return { intent: 'chat', confidence: best.p, trace,
        freeform: !PURE_GREETING.test(rawTrim), reply: pick([
          'Привет! Я Cloud HDR AI. Скажите, что открыть или найти, — сделаю это на вашем ПК.',
          'Здравствуйте! Могу запустить программу, просканировать диски в поисках игры или посчитать что-нибудь.',
          'Привет! Готов работать: запуск приложений, поиск файлов, вычисления, анализ истории чатов.'
        ]) };
      case 'thanks': return { intent: 'chat', confidence: best.p, trace,
        freeform: !PURE_THANKS.test(rawTrim), reply: pick([
          'Всегда пожалуйста ☁', 'Обращайтесь — Cloud HDR AI на связи.', 'Рад помочь!'
        ]) };
      case 'help': return { intent: 'help', confidence: best.p, reply: HELP_TEXT, trace };
      /* Рассказ о себе — только на прямой вопрос о себе.

         Сеть относит к «identity» почти всё, что начинается с «ты»: её примеры
         устроены именно так — «ты кто», «ты умный», «ты живой». Из-за этого
         «ты можешь объяснить, как работает двигатель» получало в ответ рассказ
         про перцептрон, и разговор на этом обрывался. Теперь без прямого
         вопроса фраза уходит модели, а она отвечает по существу. */
      case 'identity': {
        if (!SELF_QUESTION.test(text)) {
          trace.push('«ты …» без прямого вопроса о себе — это разговор');
          return { intent: 'chat', confidence: Math.min(best.p, 0.5), freeform: true, trace,
            reply: 'Отвечу по существу вопроса. А если интересно про меня — спросите прямо: «кто ты».' };
        }
        return { intent: 'chat', confidence: best.p, trace, reply:
        'Я Cloud HDR AI — помощник Cloud HDR 2.0. Работаю целиком на вашем компьютере: без облака, ключей и интернета.\n\n' +
        // размер модели — от сервера: какая основа отвечает сейчас, такой и назван
        `Отвечаю своей языковой моделью Cloud HDR — ${((window.LLM && LLM.catalog[0]) || {}).params || '760 млн'} параметров, ` +
        'дообучена на этом компьютере. Команды разбирает отдельная ' +
        `быстрая сеть (${Brain.shape}, ${Brain.params.toLocaleString('ru-RU')} весов): она узнаёт ` +
        `${Math.round((Brain.meta.valAccuracy || 0) * 100)}% фраз, которых не видела при обучении.\n\n` +
        'Открываю программы и папки, ищу файлы, управляю системой и музыкой, перевожу с английского, ' +
        'ставлю напоминания и говорю своим голосом. И учусь на ваших исправлениях.' };
      }
      // freeform — метка «здесь команды нет, это разговор». Если к приложению
      // подключена локальная языковая модель, такие реплики уходят к ней;
      // без неё работает заготовленный ответ, как и раньше.
      case 'smalltalk': {
        const solved = Skills.smalltalk(rawTrim);
        return { intent: 'chat', confidence: best.p, freeform: true, reply: solved.answer, trace: trace.concat(solved.trace) };
      }
    }

    return { intent: 'unknown', confidence: best.p, trace, freeform: true, reply: pick([
      'Не уверен, что вы имеете в виду. Скажите «что ты умеешь» — покажу список команд.',
      'Такого я пока не понимаю. Попробуйте: «открой дискорд», «найди игру амонг ас», «посчитай 15% от 800».'
    ]) };
  }

  function ask(reply, confidence, trace) {
    return { intent: 'ask', confidence: Math.min(confidence, 0.5), freeform: true, reply, trace };
  }

  /* ========================================== 4. Разбор моделью на spaCy  */

  /**
   * Достраивает ответ сервиса до полного разбора.
   *
   * Сервис присылает намерение и цель — то, чему обучена модель. Всё
   * остальное: тексты ответов, подтверждение опасных действий, вычисления,
   * память заметок — берётся из кода ниже, где оно и было. Повторять эту
   * логику в Python значило бы завести ей второе место для расхождений, а
   * заодно тащить туда историю чатов и localStorage, которых там нет.
   *
   * Разобраны здесь только те намерения, где цель важна: что открыть, что
   * закрыть, что искать. Навыки и разговор уходят в fromNet без изменений.
   */
  function fromRemote(remote, rawTrim, text, context) {
    const p = remote.confidence;
    const slots = remote.slots || {};
    const trace = (remote.trace || []).slice();

    // Та же проверка формы, что и у встроенной сети. Модель на spaCy выделяет
    // цель почти в любой фразе — «не могу открыть стим» даёт ей приложение
    // «стим», — и без этой проверки размеченная цель превращалась в запуск.
    if (!commandAllowed(remote.intent, text)) return asChat(remote.intent, p, trace);

    switch (remote.intent) {
      case 'open_app': {
        if (slots.app && APP_TITLES[slots.app]) {
          return { intent: 'open_app', target: slots.app, title: APP_TITLES[slots.app],
            icon: APP_ICONS[slots.app], confidence: p, reply: `Запускаю ${APP_TITLES[slots.app]}…`, trace };
        }
        if (slots.text) {
          // Названия нет в словаре — это штатный случай: агент ищет цель среди
          // игр Steam и всего установленного, и модель для того и размечает её,
          // чтобы список известных приложений перестал быть ограничением.
          //
          // slots.via === 'steam' означает, что в запросе был упомянут Steam
          // («запусти в стиме кс 2»): тогда агент ищет только среди игр и
          // запускает через steam://, а не подсовывает похожую программу.
          const viaSteam = slots.via === 'steam';
          if (viaSteam) trace.push('в запросе упомянут Steam — ищу среди игр библиотеки');
          return { intent: 'open_app', target: slots.text, via: slots.via || null,
            title: slots.text, icon: viaSteam ? 'game' : 'app', confidence: p,
            reply: viaSteam
              ? `Ищу «${slots.text}» в библиотеке Steam и запускаю…`
              : `Ищу «${slots.text}» среди игр и установленных программ, запускаю…`,
            trace };
        }
        break;
      }

      case 'close_app': {
        // «закрой его» — цель в предыдущей реплике, а не в этой фразе
        if (/^(его|это|их|ее|её|окно)$/.test(stripVerb(text, CLOSE_VERBS)) && context && context.lastApp) break;
        if (slots.app && APP_TITLES[slots.app]) {
          return { intent: 'close_app', target: slots.app, title: APP_TITLES[slots.app],
            icon: APP_ICONS[slots.app], confidence: p,
            reply: `Закрываю ${APP_TITLES[slots.app]}.`,
            trace };
        }
        if (slots.text) {
          return { intent: 'close_app', target: slots.text, title: slots.text, icon: 'app',
            confidence: p,
            reply: `Закрываю «${slots.text}».`, trace };
        }
        break;
      }

      case 'open_folder': {
        if (slots.path) {
          return { intent: 'open_folder', target: slots.path, title: slots.path, icon: 'folder',
            confidence: p, reply: `Открываю ${slots.path} в Проводнике.`, trace };
        }
        if (slots.folder && FOLDER_TITLES[slots.folder]) {
          return { intent: 'open_folder', target: slots.folder, title: FOLDER_TITLES[slots.folder],
            icon: 'folder', confidence: p, reply: `Открываю «${FOLDER_TITLES[slots.folder]}».`, trace };
        }
        if (slots.text) {
          return { intent: 'open_folder', target: slots.text, title: slots.text, icon: 'folder',
            confidence: p, reply: `Открываю папку «${slots.text}» в Проводнике.`, trace };
        }
        break;
      }

      case 'open_url': {
        // Сайт, названный по имени: адрес пришёл из словаря, а не из текста.
        if (slots.site && SITE_URLS[slots.site]) {
          return { intent: 'open_url', target: SITE_URLS[slots.site], site: slots.site,
            title: SITE_TITLES[slots.site], icon: 'globe', confidence: p,
            reply: `Открываю ${SITE_TITLES[slots.site]}.`, trace };
        }
        if (slots.url) {
          return { intent: 'open_url', target: slots.url, title: slots.url, icon: 'globe',
            confidence: p, reply: `Открываю ${slots.url} в браузере.`, trace };
        }
        break;
      }

      case 'web_search': {
        if (slots.query) {
          return { intent: 'web_search', target: slots.query, query: slots.query,
            title: 'Поиск: ' + slots.query, icon: 'globe', confidence: p,
            reply: `Ищу «${slots.query}» в браузере.`, trace };
        }
        break;
      }

      case 'volume_set': {
        const level = slots.level != null ? slots.level : volumeLevel(rawTrim);
        if (level == null) break;
        return { intent: 'volume_set', level, title: `Громкость ${level}%`, icon: 'volume',
          confidence: p, reply: `Ставлю громкость на ${level}%.`, trace };
      }

      case 'volume_up':
      case 'volume_down': {
        if (slots.amount == null) break;
        const up = remote.intent === 'volume_up';
        return { intent: remote.intent, amount: slots.amount,
          title: up ? 'Громкость +' : 'Громкость −', icon: 'volume', confidence: p,
          reply: up ? `Прибавляю громкость на ${slots.amount}%.` : `Убавляю громкость на ${slots.amount}%.`,
          trace };
      }

      case 'scan_files': {
        // Та же защита, что и в разборе локальной сетью: модель на spaCy тоже
        // относит к поиску по дискам всё, где есть «найди» и «где».
        // Условие «и цель не выделена» отсюда убрано, и это было ошибкой.
        // Модель на spaCy выделяет цель почти в любой фразе: у «найди мне
        // занятие на вечер» целью становится «занятие на вечер», проверка
        // пропускалась, и запрос всё равно уходил сканировать диски. Признак
        // компьютера обязателен вне зависимости от того, что выделил разметчик.
        if (!looksLikePcSearch(text)) {
          trace.push('похоже на вопрос, а не на поиск по компьютеру');
          return { intent: 'chat', confidence: Math.min(p, 0.5), freeform: true, icon: 'chat',
            reply: 'Это похоже на обычный вопрос. Если нужно искать на компьютере, скажите прямо: '
                 + '«найди файл …», «найди игру …» или нажмите «Найти на ПК» под строкой ввода.',
            trace };
        }

        // Режим и глубину определяет локальный разбор: они читаются из формы
        // фразы («все диски», «покажи игры»), а не из выделенной цели.
        const local = scanSlots(rawTrim, text);
        const query = slots.query || local.query;
        if (query) trace.push(`цель поиска: «${query}»`);
        if (local.deep) trace.push('режим глубокого сканирования: больше корней и времени');

        return { intent: 'scan_files', target: query, query, mode: local.mode, deep: local.deep,
          title: query ? `Поиск: ${query}` : local.mode === 'games' ? 'Игры на компьютере' : 'Сканирование файлов',
          icon: 'search', confidence: p,
          reply: query
            ? `Сканирую компьютер и ищу «${query}».\nСмотрю библиотеки Steam и Epic, список установленных программ, затем прохожу по дискам.${local.deep ? ' Режим глубокий — это займёт до полуминуты.' : ''}`
            : local.mode === 'games'
              ? 'Собираю список игр: библиотеки Steam, Epic Games и установленные программы.'
              : 'Запускаю сканирование файлов. Скажите, что именно искать, — найду быстрее.',
          trace };
      }
    }

    const built = fromNet({ intent: remote.intent, p }, rawTrim, text, context);
    built.trace = trace.concat(built.trace || []);
    return built;
  }

  // Ниже этого порога сервис считается сомневающимся, и с ним спорит браузерный
  // разбор. Порог имеет смысл только потому, что уверенность модели откалибрована
  // температурой при обучении: без калибровки она отвечала «100%» и на ошибках.
  const SPACY_TRUSTED = 0.55;
  // Точное совпадение со словарём — «открой D:\Games», «закрой дискорд».
  const RULE_STRONG = 0.9;

  /**
   * Основной разбор: модель на spaCy, если сервис поднят, иначе прежняя схема.
   *
   * Локальный разбор считается ВСЕГДА, и не для страховки: он занимает
   * миллисекунды и служит вторым мнением. Точное совпадение со словарём —
   * знание, которого у модели нет и быть не может, и отдавать его молча
   * сомневающейся сети было бы шагом назад.
   */
  // Намерения, которых нет в модели spaCy (обучена раньше них): здесь правило
  // из браузера решает само — иначе spaCy перебивала его ближайшим знакомым
  // намерением, и «включи звук» выключало звук.
  const LOCAL_ONLY = new Set(['volume_unmute']);

  async function parseSmart(raw, context) {
    const local = parse(raw, context);
    if (!global.SpacyNLU || LOCAL_ONLY.has(local.intent)) return local;

    let remote = null;
    try {
      remote = await global.SpacyNLU.plan(raw, context);
    } catch {
      remote = null;
    }
    if (!remote || !remote.intent || remote.intent === 'none') return local;

    const rawTrim = String(raw == null ? '' : raw).trim();
    const text = normalize(rawTrim);
    const percent = Math.round((remote.confidence || 0) * 100);

    // Отмена действия: «не открывай хром». Выполнять тут нечего, и спорить с
    // разбором в браузере не о чем — он про отрицание ничего не знает.
    if (remote.route === 'chat') {
      const step = (remote.steps || [])[0] || {};
      return { intent: 'chat', confidence: remote.confidence, source: 'Mind', raw: rawTrim,
        trace: remote.trace || [], reply: step.reply || 'Хорошо, ничего не делаю.' };
    }

    const built = fromRemote(remote, rawTrim, text, context);

    // Несколько команд в одной фразе. Первая — обычный разбор, остальные
    // складываем в plan: интерфейс выполнит их по очереди.
    const steps = remote.steps || [];
    if (steps.length > 1) {
      built.plan = steps.slice(1).map((step) => fromRemote(step, step.raw || rawTrim,
        normalize(step.raw || rawTrim), context));
      built.trace = (remote.trace || []).concat(built.trace || []);
      built.source = 'Mind';
      built.raw = rawTrim;
      built.alternatives = [];
      return built;
    }
    built.source = 'spaCy';
    built.raw = rawTrim;
    built.alternatives = remote.alternatives || [];
    if (built.confidence == null) built.confidence = remote.confidence;

    if (remote.confidence >= SPACY_TRUSTED) {
      const byRule = local.source && local.source.indexOf('правило') === 0;
      if (byRule && local.confidence >= RULE_STRONG && local.intent !== built.intent && remote.confidence < 0.8) {
        local.trace = (local.trace || []).concat(
          `модель предлагала ${built.intent} (${percent}%), но словарь совпал точно — остаюсь при своём`);
        return local;
      }
      if (local.intent === built.intent) {
        built.confidence = Math.min(0.99, Math.max(built.confidence, local.confidence || 0));
        built.trace = (built.trace || []).concat('разбор в браузере согласен с моделью');
      }
      return built;
    }

    if ((local.confidence || 0) > remote.confidence) {
      local.trace = (local.trace || []).concat(
        `модель не уверена (${percent}%) — беру разбор из браузера`);
      return local;
    }

    built.trace = (built.trace || []).concat('уверенность невысокая — поправьте меня, если ошибся');
    return built;
  }

  /** Короткий заголовок для истории чата. */
  function titleFor(text) {
    const clean = String(text).trim().replace(/\s+/g, ' ');
    return clean.length > 34 ? clean.slice(0, 34).trimEnd() + '…' : clean || 'Новый чат';
  }

  global.NLU = {
    parse, parseSmart, normalize, levenshtein, titleFor, matchDictionary,
    APP_ALIASES, APP_TITLES, APP_ICONS, FOLDER_ALIASES, FOLDER_TITLES, HELP_TEXT,
    SITE_ALIASES, SITE_URLS, SITE_TITLES, GAME_ALIASES,
    OPEN_VERBS, CLOSE_VERBS, SEARCH_VERBS, SCAN_VERBS,
    URL_RE, PATH_RE,
    EXECUTABLE
  };
})(window);
