/* ============================================================================
   Cloud HDR AI — навыки для повседневных задач
   ----------------------------------------------------------------------------
   Каждый навык — детерминированный вычислитель, а не генератор текста.
   Он либо уверенно решает задачу и возвращает ответ с ходом решения,
   либо честно возвращает null и уступает место другому навыку.
   ========================================================================== */
(function (global) {
  'use strict';

  const N = (value) => {
    const rounded = Math.round(value * 1e10) / 1e10;
    if (!isFinite(rounded)) return String(value);
    return String(rounded).replace(/\B(?=(\d{3})+(?!\d))/g, ' ').replace('.', ',');
  };

  const plural = (count, one, few, many) => {
    const n = Math.abs(count) % 100;
    const n1 = n % 10;
    if (n > 10 && n < 20) return many;
    if (n1 > 1 && n1 < 5) return few;
    if (n1 === 1) return one;
    return many;
  };

  /* ======================================================================= */
  /*  1. Математика                                                          */
  /* ======================================================================= */

  /**
   * ВАЖНО: в JavaScript \b опирается на ASCII-\w, поэтому /\bплюс\b/ НИКОГДА не
   * совпадёт с русским словом — граница слова просто не определяется. Границы
   * здесь заданы явно через \p{L} с флагом u.
   *
   * Порядок важен: длинные варианты идут раньше коротких, иначе «разделить на»
   * съест «делить на» и остаток строки станет мусором.
   */
  const B1 = '(?<![\\p{L}\\p{Nd}])';   // начало слова
  const B2 = '(?![\\p{L}\\p{Nd}])';    // конец слова
  const op = (body) => new RegExp(B1 + '(?:' + body + ')' + B2, 'gu');

  const WORD_OPS = [
    [op('корен[ья]\\s*из'), 'sqrt'],
    [op('плюс|прибав(?:ь|ить)|сложи(?:ть)?'), '+'],
    [op('минус|отним(?:и|ить)|выч(?:ти|есть)'), '-'],
    [op('умнож(?:ь|ить)?\\s*на|помнож(?:ь|ить)\\s*на|умножить'), '*'],
    [op('раздели?ть?\\s*на|подели?ть?\\s*на|делить\\s*на'), '/'],
    [op('в\\s*степени|степень'), '^'],
    [op('пополам'), '/2'],
    [/(\d)\s*[хx]\s*(?=\d)/gu, '$1*']
  ];

  /**
   * Рекурсивный спуск: выражение → слагаемые → множители → степень → атом.
   * Своя реализация вместо eval() — в поле ввода чата может оказаться что угодно.
   */
  function evaluate(source) {
    const text = source.replace(/,(\d)/g, '.$1').replace(/\s+/g, '');
    let pos = 0;

    const peek = () => text[pos];
    const eat = (char) => (text[pos] === char ? (pos++, true) : false);

    function expression() {
      let value = term();
      for (;;) {
        if (eat('+')) value += term();
        else if (eat('-')) value -= term();
        else return value;
      }
    }
    function term() {
      let value = power();
      for (;;) {
        if (eat('*')) value *= power();
        else if (eat('/')) {
          const divisor = power();
          if (divisor === 0) throw new Error('деление на ноль');
          value /= divisor;
        } else if (eat('%')) {
          // «10 % 3» — остаток; проценты обрабатываются отдельным навыком
          value %= power();
        } else return value;
      }
    }
    function power() {
      const base = unary();
      if (eat('^')) return Math.pow(base, power());
      return base;
    }
    function unary() {
      if (eat('-')) return -unary();
      if (eat('+')) return unary();
      return atom();
    }
    function atom() {
      if (eat('(')) {
        const value = expression();
        if (!eat(')')) throw new Error('не закрыта скобка');
        return value;
      }
      const fn = /^(sqrt|abs|round|floor|ceil|sin|cos|tan|ln|log|exp)/.exec(text.slice(pos));
      if (fn) {
        pos += fn[0].length;
        const argument = eat('(') ? (() => { const v = expression(); if (!eat(')')) throw new Error('не закрыта скобка'); return v; })() : unary();
        switch (fn[0]) {
          case 'sqrt': if (argument < 0) throw new Error('корень из отрицательного числа');
            return Math.sqrt(argument);
          case 'abs': return Math.abs(argument);
          case 'round': return Math.round(argument);
          case 'floor': return Math.floor(argument);
          case 'ceil': return Math.ceil(argument);
          case 'sin': return Math.sin(argument);
          case 'cos': return Math.cos(argument);
          case 'tan': return Math.tan(argument);
          case 'ln': return Math.log(argument);
          case 'log': return Math.log10(argument);
          case 'exp': return Math.exp(argument);
        }
      }
      if (text.startsWith('pi', pos)) { pos += 2; return Math.PI; }
      if (peek() === 'e' && !/\d/.test(text[pos + 1] || '')) { pos++; return Math.E; }

      const number = /^\d+(\.\d+)?/.exec(text.slice(pos));
      if (!number) throw new Error('не понял выражение');
      pos += number[0].length;
      return parseFloat(number[0]);
    }

    const result = expression();
    if (pos < text.length) throw new Error('лишние символы: ' + text.slice(pos));
    if (!isFinite(result)) throw new Error('результат не определён');
    return result;
  }

  /** «15% от 800», «сколько процентов 30 от 150», «скидка 20% с 4500». */
  function percentSkill(text) {
    let match = /(-?[\d\s.,]+)\s*(?:%|процент[а-яё]*)\s*(?:от|из|с)\s*(-?[\d\s.,]+)/.exec(text);
    if (match) {
      const percent = parseFloat(match[1].replace(/\s/g, '').replace(',', '.'));
      const base = parseFloat(match[2].replace(/\s/g, '').replace(',', '.'));
      const value = base * percent / 100;
      const discount = /скидк|дешевл|минус|снизи/.test(text);
      return {
        answer: discount
          ? `${N(percent)}% от ${N(base)} — это ${N(value)}. Со скидкой останется ${N(base - value)}.`
          : `${N(percent)}% от ${N(base)} = ${N(value)}.`,
        trace: [`выделяю базу ${N(base)} и долю ${N(percent)}%`, `${N(base)} × ${N(percent)} ÷ 100 = ${N(value)}`]
      };
    }

    match = /сколько\s+процент[а-яё]*\s+(-?[\d\s.,]+)\s+(?:от|из)\s+(-?[\d\s.,]+)/.exec(text);
    if (match) {
      const part = parseFloat(match[1].replace(/\s/g, '').replace(',', '.'));
      const whole = parseFloat(match[2].replace(/\s/g, '').replace(',', '.'));
      if (whole === 0) return { answer: 'Доля от нуля не определена.', trace: ['знаменатель равен нулю'] };
      return {
        answer: `${N(part)} — это ${N(part / whole * 100)}% от ${N(whole)}.`,
        trace: [`${N(part)} ÷ ${N(whole)} × 100 = ${N(part / whole * 100)}`]
      };
    }
    return null;
  }

  function mathSkill(raw) {
    const text = String(raw).toLowerCase().replace(/ё/g, 'е');

    const percent = percentSkill(text);
    if (percent) return percent;

    // отбрасываем обёртку-вопрос, оставляем само выражение
    let expression = text
      .replace(/^(посчитай|вычисли|реши|сколько\s+будет|сколько|чему\s+равно|calculate|compute|what\s+is)\s*/i, '')
      .replace(/[?!]+$/, '')
      .trim();

    for (const [pattern, replacement] of WORD_OPS) expression = expression.replace(pattern, replacement);

    // Одно слово-связка перед числом («выйдет 340*6», «получится 12+5») не должно
    // рушить разбор. Имена функций при этом трогать нельзя — иначе «sqrt 144»
    // превратилось бы просто в «144».
    const FUNCTIONS = /^(sqrt|abs|round|floor|ceil|sin|cos|tan|ln|log|exp|pi)$/i;
    expression = expression.replace(/^([\p{L}]+)\s+(?=[\d(])/u,
      (whole, word) => (FUNCTIONS.test(word) ? whole : ''));

    if (!/[\d)]/.test(expression) || !/[+\-*/^%]|sqrt|sin|cos|log|ln|abs|round/.test(expression)) return null;
    // в выражении не должно остаться слов — иначе это не математика, а фраза
    if (/[a-zа-я]{2,}/.test(expression.replace(/sqrt|abs|round|floor|ceil|sin|cos|tan|ln|log|exp|pi/g, ''))) return null;

    try {
      const value = evaluate(expression);
      return {
        // пробелы вокруг бинарных знаков — «17 × 23 + 5», а не «17 × 23+5»;
        // унарный минус («-5», «2*-3») остаётся прижатым к числу
        answer: `${expression.replace(/\s+/g, '').replace(/([\d)a-z])([+\-*/^])/gi, '$1 $2 ')
          .replace(/\*/g, '×').replace(/\//g, '÷')} = ${N(value)}`,
        trace: ['разбираю выражение рекурсивным спуском', `результат: ${N(value)}`]
      };
    } catch (error) {
      return { answer: `Не смог вычислить: ${error.message}.`, trace: ['ошибка разбора выражения'] };
    }
  }

  /* ======================================================================= */
  /*  2. Единицы измерения                                                   */
  /* ======================================================================= */

  // всё сводится к базовой единице своей группы, затем разворачивается в целевую
  const UNITS = {
    length: { base: 'м', map: {
      'мм': 0.001, 'миллиметр': 0.001, 'см': 0.01, 'сантиметр': 0.01, 'дм': 0.1,
      'м': 1, 'метр': 1, 'км': 1000, 'километр': 1000,
      'миля': 1609.344, 'миль': 1609.344, 'mile': 1609.344, 'miles': 1609.344,
      'фут': 0.3048, 'дюйм': 0.0254, 'inch': 0.0254, 'ft': 0.3048, 'cm': 0.01, 'mm': 0.001, 'km': 1000, 'm': 1
    } },
    mass: { base: 'кг', map: {
      'мг': 1e-6, 'миллиграмм': 1e-6, 'г': 0.001, 'грамм': 0.001, 'кг': 1, 'килограмм': 1, 'кило': 1,
      'т': 1000, 'тонна': 1000, 'фунт': 0.45359237, 'унция': 0.0283495,
      'g': 0.001, 'kg': 1, 'lb': 0.45359237, 'oz': 0.0283495
    } },
    data: { base: 'мб', map: {
      'бит': 1.192e-7, 'байт': 9.5367e-7, 'кб': 0.0009765625, 'килобайт': 0.0009765625,
      'мб': 1, 'мегабайт': 1, 'гб': 1024, 'гигабайт': 1024, 'тб': 1048576, 'терабайт': 1048576,
      'kb': 0.0009765625, 'mb': 1, 'gb': 1024, 'tb': 1048576
    } },
    time: { base: 'с', map: {
      'мс': 0.001, 'миллисекунда': 0.001, 'с': 1, 'сек': 1, 'секунда': 1,
      'мин': 60, 'минута': 60, 'ч': 3600, 'час': 3600,
      'день': 86400, 'сутки': 86400, 'неделя': 604800, 'месяц': 2629800, 'год': 31557600,
      'sec': 1, 'min': 60, 'hour': 3600, 'day': 86400
    } },
    speed: { base: 'м/с', map: {
      'м/с': 1, 'мс': 1, 'км/ч': 0.2777778, 'кмч': 0.2777778, 'миль/ч': 0.44704,
      'узел': 0.514444, 'kmh': 0.2777778, 'mph': 0.44704
    } }
  };

  function findUnit(token) {
    const needle = token.toLowerCase().replace(/[.,]$/, '');
    for (const [group, data] of Object.entries(UNITS)) {
      for (const [name, factor] of Object.entries(data.map)) {
        if (needle === name || needle === name + 'ов' || needle === name + 'ы' ||
            needle === name + 'а' || needle === name + 'и' || needle === name + 'е' ||
            needle.startsWith(name) && needle.length - name.length <= 3 && name.length >= 4) {
          return { group, name, factor, base: data.base };
        }
      }
    }
    return null;
  }

  function convertSkill(raw) {
    const text = String(raw).toLowerCase().replace(/ё/g, 'е');

    // температура — не пропорциональная шкала, отдельная формула
    const temp = /(-?[\d.,]+)\s*(?:градус[а-яё]*\s*)?(?:по\s*)?(цельси[а-яё]*|c\b|фаренгейт[а-яё]*|f\b|кельвин[а-яё]*|k\b)/.exec(text);
    if (temp && /(фаренгейт|цельси|кельвин|celsius|fahrenheit)/.test(text)) {
      const value = parseFloat(temp[1].replace(',', '.'));
      const from = temp[2];
      const toF = /фаренгейт/.test(text.slice(temp.index + temp[0].length)) || (/цельси|c\b/.test(from) && /фаренгейт/.test(text));
      if (/цельси|^c$/.test(from) && /фаренгейт/.test(text)) {
        return { answer: `${N(value)} °C = ${N(value * 9 / 5 + 32)} °F.`, trace: ['°F = °C × 9/5 + 32'] };
      }
      if (/фаренгейт|^f$/.test(from)) {
        return { answer: `${N(value)} °F = ${N((value - 32) * 5 / 9)} °C.`, trace: ['°C = (°F − 32) × 5/9'] };
      }
      if (/кельвин|^k$/.test(from)) {
        return { answer: `${N(value)} K = ${N(value - 273.15)} °C.`, trace: ['°C = K − 273,15'] };
      }
      if (toF) return { answer: `${N(value)} °C = ${N(value * 9 / 5 + 32)} °F.`, trace: ['°F = °C × 9/5 + 32'] };
    }

    // «переведи 5 км в метры», «сколько граммов в 3 кг»
    let match = /(-?[\d.,]+)\s*([a-zа-я/]+)\s*(?:в|to|→|=)\s*([a-zа-я/]+)/.exec(text);
    if (!match) {
      const reversed = /сколько\s+([a-zа-я/]+)\s+в\s+(-?[\d.,]+)\s*([a-zа-я/]+)/.exec(text);
      if (reversed) match = [reversed[0], reversed[2], reversed[3], reversed[1]];
    }
    if (!match) return null;

    const value = parseFloat(match[1].replace(',', '.'));
    const from = findUnit(match[2]);
    const to = findUnit(match[3]);
    if (!from || !to || isNaN(value)) return null;
    if (from.group !== to.group) {
      return { answer: `«${match[2]}» и «${match[3]}» — величины разной природы, перевести нельзя.`, trace: ['единицы из разных групп'] };
    }

    const result = value * from.factor / to.factor;
    return {
      answer: `${N(value)} ${match[2]} = ${N(result)} ${match[3]}.`,
      trace: [`${match[2]} → ${from.base}: ×${from.factor}`, `${from.base} → ${match[3]}: ÷${to.factor}`]
    };
  }

  /* ======================================================================= */
  /*  3. Дата и время                                                        */
  /* ======================================================================= */

  const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const WEEKDAYS = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];

  function datetimeSkill(raw) {
    const text = String(raw).toLowerCase().replace(/ё/g, 'е');
    const now = new Date();

    if (/сколько\s+(сейчас\s+)?времен|котор[а-яё]*\s+час|what\s+time/.test(text)) {
      return {
        answer: `Сейчас ${now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}, ` +
                `${now.getDate()} ${MONTHS[now.getMonth()]} ${now.getFullYear()} года, ${WEEKDAYS[now.getDay()]}.`,
        trace: ['беру системное время компьютера']
      };
    }

    if (/как(ое|ая)\s+(сегодня\s+)?(число|дата)|what\s+date|сегодняшн[а-яё]*\s+дат/.test(text)) {
      return {
        answer: `Сегодня ${now.getDate()} ${MONTHS[now.getMonth()]} ${now.getFullYear()} года, ${WEEKDAYS[now.getDay()]}.`,
        trace: ['системная дата']
      };
    }

    if (/как(ой|ая)\s+(сегодня\s+)?день\s+недел/.test(text)) {
      return { answer: `Сегодня ${WEEKDAYS[now.getDay()]}.`, trace: ['день недели по системной дате'] };
    }

    if (/как(ой|ая)\s+(сейчас\s+)?месяц/.test(text)) {
      const nominative = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
        'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
      return { answer: `Сейчас ${nominative[now.getMonth()]} ${now.getFullYear()} года.`, trace: ['системная дата'] };
    }

    // «сколько дней до нового года / до 5 марта / до лета»
    const until = /сколько\s+(дн[а-яё]*|недел[а-яё]*|час[а-яё]*)\s+(?:осталось\s+)?до\s+(.+)$/.exec(text);
    if (until) {
      const target = parseTargetDate(until[2].trim(), now);
      if (!target) return { answer: `Не понял, до какой даты считать: «${until[2].trim()}».`, trace: ['цель не распознана'] };
      const ms = target.date - now;
      const days = Math.ceil(ms / 86400000);
      if (ms < 0) return { answer: `${target.label} уже прошло — это было ${Math.abs(days)} ${plural(Math.abs(days), 'день', 'дня', 'дней')} назад.`, trace: ['дата в прошлом'] };

      let value = days, unit = plural(days, 'день', 'дня', 'дней');
      if (/недел/.test(until[1])) { value = Math.floor(days / 7); unit = plural(value, 'неделя', 'недели', 'недель'); }
      if (/час/.test(until[1])) { value = Math.ceil(ms / 3600000); unit = plural(value, 'час', 'часа', 'часов'); }

      return {
        answer: `До ${target.label} осталось ${value} ${unit} (${target.date.getDate()} ${MONTHS[target.date.getMonth()]} ${target.date.getFullYear()}).`,
        trace: [`целевая дата: ${target.date.toLocaleDateString('ru-RU')}`, `разница: ${N(ms / 86400000)} суток`]
      };
    }

    if (/сколько\s+(дней|недель|месяцев)\s+в\s+год/.test(text)) {
      const year = now.getFullYear();
      const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
      if (/недел/.test(text)) return { answer: 'В году 52 полные недели и 1–2 дня сверху.', trace: ['365 ÷ 7 = 52,14'] };
      if (/месяц/.test(text)) return { answer: 'В году 12 месяцев.', trace: [] };
      return { answer: `В ${year} году ${leap ? 366 : 365} дней — год ${leap ? 'високосный' : 'обычный'}.`, trace: [`${year} % 4 = ${year % 4}`] };
    }

    return null;
  }

  /** «нового года», «5 марта», «01.09.2027», «лета» → конкретная дата. */
  function parseTargetDate(raw, now) {
    const text = raw.replace(/[?.!]+$/, '').trim();

    if (/нов[а-яё]*\s+год/.test(text)) {
      return { date: new Date(now.getFullYear() + (now.getMonth() === 11 && now.getDate() > 31 ? 1 : 1), 0, 1), label: 'Нового года' };
    }
    if (/лет[аоу]?$/.test(text)) {
      const year = now.getMonth() >= 5 ? now.getFullYear() + 1 : now.getFullYear();
      return { date: new Date(year, 5, 1), label: 'лета' };
    }
    if (/зим[ыау]?$/.test(text)) {
      const year = now.getMonth() >= 11 ? now.getFullYear() + 1 : now.getFullYear();
      return { date: new Date(year, 11, 1), label: 'зимы' };
    }
    if (/весн[ыаы]?$/.test(text)) {
      const year = now.getMonth() >= 2 ? now.getFullYear() + 1 : now.getFullYear();
      return { date: new Date(year, 2, 1), label: 'весны' };
    }
    if (/осен[иь]$/.test(text)) {
      const year = now.getMonth() >= 8 ? now.getFullYear() + 1 : now.getFullYear();
      return { date: new Date(year, 8, 1), label: 'осени' };
    }

    let match = /^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?$/.exec(text);
    if (match) {
      const year = match[3] ? (match[3].length === 2 ? 2000 + +match[3] : +match[3]) : now.getFullYear();
      const date = new Date(year, +match[2] - 1, +match[1]);
      if (!match[3] && date < now) date.setFullYear(year + 1);
      return { date, label: `${+match[1]} ${MONTHS[+match[2] - 1]}` };
    }

    match = /^(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?/.exec(text);
    if (match) {
      const monthIndex = MONTHS.findIndex((name) => name.startsWith(match[2].slice(0, 4)));
      if (monthIndex < 0) return null;
      const year = match[3] ? +match[3] : now.getFullYear();
      const date = new Date(year, monthIndex, +match[1]);
      if (!match[3] && date < now) date.setFullYear(year + 1);
      return { date, label: `${+match[1]} ${MONTHS[monthIndex]}` };
    }

    return null;
  }

  /* ======================================================================= */
  /*  4. Личная память                                                       */
  /* ======================================================================= */

  const MEMORY_KEY = 'cloudhdr.memory.v1';

  function loadMemory() {
    try { return JSON.parse(localStorage.getItem(MEMORY_KEY) || '[]'); } catch { return []; }
  }
  function saveMemory(list) {
    try { localStorage.setItem(MEMORY_KEY, JSON.stringify(list.slice(-200))); } catch {}
  }

  function rememberSkill(raw) {
    const text = String(raw).trim();
    const match = /^(?:запомни|запиши|сохрани|remember)[,:]?\s*(?:что\s+|пожалуйста\s+)?(.+)$/i.exec(text);
    if (!match) return null;

    const fact = match[1].trim().replace(/[.!]+$/, '');
    if (fact.length < 2) return { answer: 'Что именно запомнить? Скажите, например: «запомни, что мой ник — Миро».', trace: [] };

    const memory = loadMemory();
    memory.push({ fact, at: Date.now(), keys: Brain.tokenize(fact).map(Brain.stem) });
    saveMemory(memory);

    // Несколько живых вариантов вместо одной канцелярской строки: одинаковое
    // «Запомнил: …» на каждую просьбу звучит как автоответчик.
    const replies = [
      `Отлично, запомнил: ${fact}. Теперь буду это учитывать.`,
      `Записал себе: ${fact}. Если понадобится — просто спросите, что я о вас помню.`,
      `Понял вас! «${fact}» — сохранил, никуда не денется.`,
      `Готово, это теперь в моей памяти: ${fact}. Спасибо, что рассказали!`
    ];
    const count = memory.length > 1 ? `\nВсего я помню о вас уже ${memory.length} ${memory.length % 10 === 1 && memory.length % 100 !== 11 ? 'вещь' : ([2, 3, 4].includes(memory.length % 10) && ![12, 13, 14].includes(memory.length % 100) ? 'вещи' : 'вещей')}.` : '';
    return {
      answer: replies[Math.floor(Math.random() * replies.length)] + count,
      trace: ['сохраняю факт в локальную память', `индексирую по ${Brain.tokenize(fact).length} словам`]
    };
  }

  function recallSkill(raw) {
    const memory = loadMemory();
    if (!memory.length) {
      return {
        answer: 'Пока я о вас ничего не знаю. Скажите «запомни, что …» — и я сохраню это локально, прямо на вашем компьютере.',
        trace: ['память пуста']
      };
    }

    const query = Brain.tokenize(raw).map(Brain.stem)
      .filter((word) => !['что', 'ты', 'обо', 'мне', 'помн', 'как', 'мо', 'я', 'напомн', 'какой', 'кака'].includes(word));

    if (query.length) {
      const scored = memory
        .map((item) => ({ item, score: item.keys.filter((key) => query.includes(key)).length }))
        .filter((entry) => entry.score > 0)
        .sort((a, b) => b.score - a.score);

      if (scored.length) {
        return {
          answer: scored.length === 1
            ? `Вы просили запомнить: ${scored[0].item.fact}.`
            : 'Вот что подходит под вопрос:\n' + scored.slice(0, 5).map((entry) => '• ' + entry.item.fact).join('\n'),
          trace: [`ищу по ${query.length} ключевым основам`, `совпадений: ${scored.length}`]
        };
      }
    }

    return {
      answer: `Я помню о вас ${memory.length} ${plural(memory.length, 'факт', 'факта', 'фактов')}:\n` +
              memory.slice(-10).map((item) => '• ' + item.fact).join('\n'),
      trace: ['точных совпадений нет — показываю всё']
    };
  }

  /* ======================================================================= */
  /*  5. Таймер                                                              */
  /* ======================================================================= */

  const timers = [];

  function timerSkill(raw, onDone) {
    const text = String(raw).toLowerCase().replace(/ё/g, 'е');
    let seconds = 0;

    if (/полчаса|пол\s*часа/.test(text)) seconds = 1800;
    else {
      // границы слова — явные: \b с кириллицей не работает
      const match = /(\d+(?:[.,]\d+)?)\s*(секунд\p{L}*|сек|минут\p{L}*|мин|час\p{L}*|ч)(?![\p{L}\p{Nd}])/u.exec(text);
      if (!match) return null;
      const value = parseFloat(match[1].replace(',', '.'));
      if (/сек/.test(match[2])) seconds = value;
      else if (/мин/.test(match[2])) seconds = value * 60;
      else seconds = value * 3600;
    }
    if (!seconds || seconds > 86400) return null;

    const label = seconds >= 3600 ? `${N(seconds / 3600)} ч` : seconds >= 60 ? `${N(seconds / 60)} мин` : `${N(seconds)} с`;
    const id = setTimeout(() => {
      const index = timers.findIndex((timer) => timer.id === id);
      if (index >= 0) timers.splice(index, 1);
      if (typeof onDone === 'function') onDone(label);
    }, seconds * 1000);
    timers.push({ id, label, endsAt: Date.now() + seconds * 1000 });

    return {
      answer: `Таймер на ${label} пошёл. Я сообщу, когда время выйдет.\nОн живёт в этой вкладке — если закрыть окно, отсчёт прервётся.`,
      trace: [`распознал длительность: ${N(seconds)} с`, 'ставлю локальный таймер']
    };
  }

  /* ======================================================================= */
  /*  6. Анализ всех разговоров                                              */
  /* ======================================================================= */

  const STOP_WORDS = new Set(['и', 'в', 'на', 'с', 'а', 'но', 'что', 'как', 'это', 'мне', 'ты', 'я',
    'не', 'да', 'нет', 'мой', 'мо', 'ну', 'же', 'бы', 'вот', 'по', 'до', 'за', 'из', 'к', 'у', 'о',
    'для', 'при', 'от', 'то', 'все', 'так', 'уже', 'еще', 'или', 'если', 'быть', 'the', 'a', 'is']);

  /**
   * Полный разбор истории: сколько, о чём, когда, что сработало.
   * @param {Array} chats массив чатов из хранилища
   * @param {string} question вопрос пользователя — уточняет фокус ответа
   */
  function analyzeChats(chats, question) {
    const all = Array.isArray(chats) ? chats : [];
    const messages = all.flatMap((chat) => (chat.messages || []).map((message) => ({ ...message, chat: chat.title })));

    if (!messages.length) {
      return {
        answer: 'История пока пуста — анализировать нечего. Напишите пару запросов, и я смогу делать выводы.',
        trace: ['в хранилище нет сообщений']
      };
    }

    const userMessages = messages.filter((message) => message.role === 'user');
    const aiMessages = messages.filter((message) => message.role !== 'user');
    const commands = messages.filter((message) => message.cmd);
    const successful = commands.filter((message) => message.cmd.state === 'done');

    // частотный анализ по основам слов
    const frequency = new Map();
    userMessages.forEach((message) => {
      new Set(Brain.tokenize(message.text).map(Brain.stem)).forEach((word) => {
        if (word.length < 3 || STOP_WORDS.has(word)) return;
        frequency.set(word, (frequency.get(word) || 0) + 1);
      });
    });
    const topWords = [...frequency.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);

    // какие намерения встречались — прогоняем историю через нейросеть
    const intents = new Map();
    userMessages.slice(-200).forEach((message) => {
      const best = Brain.classify(message.text, 1)[0];
      if (best && best.p > 0.35) intents.set(best.intent, (intents.get(best.intent) || 0) + 1);
    });
    const topIntents = [...intents.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

    const INTENT_LABELS = {
      open_app: 'запуск программ', open_folder: 'открытие папок', open_url: 'открытие сайтов',
      web_search: 'поиск в браузере', close_app: 'закрытие программ', screenshot: 'скриншоты',
      scan_files: 'поиск файлов и игр', math: 'вычисления', datetime: 'дата и время',
      convert: 'перевод единиц', volume_up: 'громкость', volume_down: 'громкость',
      volume_mute: 'звук', greeting: 'приветствия', help: 'вопросы о возможностях',
      smalltalk: 'разговоры', analyze_chats: 'анализ истории', remember: 'заметки',
      recall: 'вопросы к памяти', settings: 'настройки', lock: 'блокировка', timer: 'таймеры',
      identity: 'вопросы обо мне', thanks: 'благодарности', denied: 'запрещённые действия'
    };

    const firstAt = Math.min(...messages.map((message) => message.ts || Date.now()));
    const lastAt = Math.max(...messages.map((message) => message.ts || Date.now()));
    const days = Math.max(1, Math.round((lastAt - firstAt) / 86400000));

    // почасовая активность
    const hours = new Array(24).fill(0);
    userMessages.forEach((message) => { hours[new Date(message.ts || Date.now()).getHours()]++; });
    const peakHour = hours.indexOf(Math.max(...hours));

    const query = String(question || '').toLowerCase();

    // --- узконаправленные вопросы отвечаем точечно ---
    if (/чаще\s+всего|популярн|часто/.test(query) && topIntents.length) {
      return {
        answer: `Чаще всего вы просите: ${topIntents.map(([intent, count]) => `${INTENT_LABELS[intent] || intent} — ${count} ${plural(count, 'раз', 'раза', 'раз')}`).join('; ')}.\n` +
                `Ключевые слова ваших запросов: ${topWords.slice(0, 5).map(([word]) => word).join(', ')}.`,
        trace: [`классифицирую ${userMessages.length} сообщений сетью`, `${intents.size} различных намерений`]
      };
    }

    if (/сколько\s+(всего\s+)?сообщен|сколько\s+чат/.test(query)) {
      return {
        answer: `Всего ${messages.length} ${plural(messages.length, 'сообщение', 'сообщения', 'сообщений')} в ${all.length} ${plural(all.length, 'чате', 'чатах', 'чатах')}: ` +
                `${userMessages.length} ваших и ${aiMessages.length} моих. Команд выполнено — ${successful.length} из ${commands.length}.`,
        trace: ['прямой подсчёт по хранилищу']
      };
    }

    const recall = /вспомни|что\s+я\s+просил|о\s+чем\s+мы\s+говорил/.test(query);
    if (recall) {
      const keywords = Brain.tokenize(query).map(Brain.stem)
        .filter((word) => word.length > 2 && !STOP_WORDS.has(word) && !['вспомн', 'прос', 'говор', 'чем'].includes(word));
      const relevant = keywords.length
        ? userMessages.filter((message) => {
            const stems = Brain.tokenize(message.text).map(Brain.stem);
            return keywords.some((word) => stems.includes(word));
          })
        : userMessages.slice(-12);

      if (!relevant.length) {
        return { answer: 'По этим словам в истории ничего не нашлось.', trace: [`искал по: ${keywords.join(', ')}`] };
      }
      return {
        answer: `Нашёл ${relevant.length} ${plural(relevant.length, 'запрос', 'запроса', 'запросов')}:\n` +
                relevant.slice(-8).map((message) =>
                  `• «${message.text.slice(0, 70)}» — ${new Date(message.ts).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`
                ).join('\n'),
        trace: [`поиск по основам: ${keywords.join(', ') || '— (последние сообщения)'}`]
      };
    }

    // --- общий отчёт ---
    const successRate = commands.length ? Math.round(successful.length / commands.length * 100) : null;
    const lines = [
      `Разобрал всю историю: ${all.length} ${plural(all.length, 'чат', 'чата', 'чатов')}, ${messages.length} ${plural(messages.length, 'сообщение', 'сообщения', 'сообщений')} за ${days} ${plural(days, 'день', 'дня', 'дней')}.`,
      '',
      `📊  Ваших сообщений — ${userMessages.length}, моих — ${aiMessages.length}. Средняя длина вашего запроса: ${Math.round(userMessages.reduce((sum, message) => sum + message.text.length, 0) / userMessages.length)} символов.`
    ];
    if (topIntents.length) {
      lines.push(`🎯  Основные темы: ${topIntents.map(([intent, count]) => `${INTENT_LABELS[intent] || intent} (${count})`).join(', ')}.`);
    }
    if (topWords.length) {
      lines.push(`🔤  Чаще всего звучит: ${topWords.map(([word, count]) => `${word} ×${count}`).join(', ')}.`);
    }
    if (commands.length) {
      lines.push(`⚙️  Команд отправлено агенту — ${commands.length}, успешно — ${successful.length} (${successRate}%).`);
    }
    lines.push(`🕒  Пик активности — около ${peakHour}:00.`);
    lines.push('', 'Спросите точнее: «что я просил чаще всего», «вспомни про роблокс», «сколько всего сообщений».');

    return {
      answer: lines.join('\n'),
      trace: [
        `собрал ${messages.length} сообщений из ${all.length} чатов`,
        `частотный анализ: ${frequency.size} уникальных основ`,
        `классификация намерений сетью: ${intents.size} категорий`
      ]
    };
  }

  /* ======================================================================= */
  /*  7. Разговорные ответы                                                  */
  /* ======================================================================= */

  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  const SMALLTALK = {
    'как дела': [
      'Работаю штатно: сеть загружена, агент на связи. А у вас как?',
      'Отлично — все нейроны на месте. Чем займёмся?'
    ],
    'что нового': ['Жду ваших команд. Могу запустить программу, найти игру на дисках или посчитать что-нибудь.'],
    'скучно': ['Давайте займёмся делом: скажите «просканируй компьютер на игры» — покажу, что у вас установлено.'],
    'анекдот': [
      'Программист ставит будильник на 7:00. Просыпается в 6:59 и ждёт, чтобы не было конфликта версий.',
      'Заходит нейросеть в бар. Бармен: «Вам чего?» Сеть: «С вероятностью 0,87 — пива».'
    ],
    'пока': ['До связи! Окно можно закрыть — история чата сохранится.'],
    'до свидания': ['До встречи! Всё, что мы обсуждали, останется на вашем компьютере.'],
    'спокойной ночи': ['Спокойной ночи! Могу заблокировать экран — скажите «заблокируй пк».'],
    'устал': ['Тогда разгружу: скажите, что нужно открыть или найти, — сделаю сам.'],
    'ты классный': ['Спасибо! Я всего лишь перцептрон на 96 нейронов, но стараюсь.']
  };

  function smalltalkSkill(raw) {
    const text = String(raw).toLowerCase().replace(/ё/g, 'е');
    for (const [key, replies] of Object.entries(SMALLTALK)) {
      if (text.includes(key)) return { answer: pick(replies), trace: ['разговорный шаблон'] };
    }
    return {
      answer: pick([
        'Понял вас. Если нужно действие — скажите, что открыть, найти или посчитать.',
        'Я больше про дело: запуск программ, поиск файлов, вычисления и работа с историей чата.'
      ]),
      trace: ['общий разговорный ответ']
    };
  }

  global.Skills = {
    math: mathSkill,
    convert: convertSkill,
    datetime: datetimeSkill,
    remember: rememberSkill,
    recall: recallSkill,
    timer: timerSkill,
    smalltalk: smalltalkSkill,
    analyzeChats,
    evaluate,
    memory: { load: loadMemory, save: saveMemory, clear: () => saveMemory([]) },
    timers,
    format: N,
    plural
  };
})(window);
