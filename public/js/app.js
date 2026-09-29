/* ============================================================================
   Cloud HDR AI — интерфейс приложения
   Чат, история, анимации, голосовой ввод и мост к локальному агенту.
   ========================================================================== */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const icon = (name) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ic');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-' + name);
    svg.appendChild(use);
    return svg;
  };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

  /**
   * Анимации по умолчанию ВКЛЮЧЕНЫ независимо от настроек Windows:
   * системный prefers-reduced-motion гасил весь визуал, ради которого всё делалось.
   * Выключаются вручную кнопкой в боковой панели.
   */
  let motionOn = localStorage.getItem('cloudhdr.motion') !== 'off';
  const reduced = () => !motionOn;

  function applyMotion(on) {
    motionOn = on;
    document.documentElement.dataset.motion = on ? 'full' : 'off';
    localStorage.setItem('cloudhdr.motion', on ? 'full' : 'off');
  }

  /* ======================================================= 1. Хранилище === */

  const STORAGE_KEY = 'cloudhdr.chats.v1';
  const store = {
    chats: [],
    activeId: null,

    load() {
      try {
        const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        this.chats = Array.isArray(raw.chats) ? raw.chats : [];
        this.activeId = raw.activeId || (this.chats[0] && this.chats[0].id) || null;
      } catch {
        this.chats = [];
        this.activeId = null;
      }
      if (!this.chats.length) this.create();
      if (!this.byId(this.activeId)) this.activeId = this.chats[0].id;
    },
    save() {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ chats: this.chats.slice(0, 120), activeId: this.activeId }));
      } catch {
        /*
           Переполнено. Раньше здесь было просто «не критично» — и это было
           правдой, пока в истории лежал один текст: полсотни разговоров не
           занимают и мегабайта.

           С картинками правда изменилась. Миниатюра присланного снимка весит
           десяток килобайт, и однажды запас кончается — а кончается он ТИХО:
           перестаёт сохраняться история целиком, вместе с последним ответом.
           Человек закрывает окно и не находит разговор, который только что был.

           Поэтому место освобождается по одной миниатюре, начиная с самых
           старых чатов. Текст разбора при этом остаётся весь: он и есть ответ,
           а картинку человек и так помнит — она лежит у него на диске.
        */
        if (this.forgetOldestPicture()) this.save();
      }
    },

    /** Стирает самую старую миниатюру. false — стирать больше нечего. */
    forgetOldestPicture() {
      for (let i = this.chats.length - 1; i >= 0; i--) {
        const messages = this.chats[i].messages || [];
        for (const message of messages) {
          if (message.image && message.image.thumb) {
            message.image.thumb = null;
            return true;
          }
        }
      }
      return false;
    },
    byId(id) { return this.chats.find((chat) => chat.id === id) || null; },
    get active() { return this.byId(this.activeId); },

    create() {
      const chat = { id: uid(), title: 'Новый чат', created: Date.now(), updated: Date.now(), messages: [] };
      this.chats.unshift(chat);
      this.activeId = chat.id;
      this.save();
      return chat;
    },
    remove(id) {
      this.chats = this.chats.filter((chat) => chat.id !== id);
      if (!this.chats.length) this.create();
      else if (this.activeId === id) this.activeId = this.chats[0].id;
      this.save();
    },
    push(message) {
      const chat = this.active;
      chat.messages.push(message);
      chat.updated = Date.now();
      if (chat.title === 'Новый чат' && message.role === 'user') chat.title = NLU.titleFor(message.text);
      // активный чат всплывает наверх истории
      this.chats = [chat, ...this.chats.filter((item) => item.id !== chat.id)];
      this.save();
      return message;
    }
  };

  /* ========================================================= 2. Тосты ===== */

  function toast(text, kind = 'info') {
    const node = el('div', 'toast toast--' + kind);
    node.append(icon(kind === 'ok' ? 'check' : kind === 'err' ? 'x' : 'bolt'), el('span', null, text));
    $('toasts').appendChild(node);
    setTimeout(() => {
      node.classList.add('toast--out');
      setTimeout(() => node.remove(), 350);
    }, 4200);
  }

  /* ================================================== 3. История в панели = */

  function groupLabel(timestamp) {
    const day = 86400000;
    const start = new Date(); start.setHours(0, 0, 0, 0);
    if (timestamp >= start.getTime()) return 'Сегодня';
    if (timestamp >= start.getTime() - day) return 'Вчера';
    if (timestamp >= start.getTime() - day * 7) return 'На этой неделе';
    return 'Ранее';
  }

  function renderHistory() {
    const list = $('historyList');
    const query = NLU.normalize($('historySearch').value);
    list.textContent = '';

    const chats = store.chats.filter((chat) => {
      if (!query) return true;
      const haystack = NLU.normalize(chat.title + ' ' + chat.messages.map((m) => m.text).join(' '));
      return haystack.includes(query);
    });

    if (!chats.length) {
      const empty = el('p', 'history__group', query ? 'Ничего не найдено' : 'История пуста');
      list.appendChild(empty);
      return;
    }

    let lastGroup = null;
    chats.forEach((chat, index) => {
      const label = groupLabel(chat.updated);
      if (label !== lastGroup) {
        lastGroup = label;
        list.appendChild(el('div', 'history__group', label));
      }

      const item = el('div', 'hitem');
      item.setAttribute('role', 'listitem');
      item.dataset.active = String(chat.id === store.activeId);
      item.style.animationDelay = Math.min(index * 22, 260) + 'ms';

      const body = el('div', 'hitem__body');
      body.append(
        el('div', 'hitem__title', chat.title),
        el('div', 'hitem__meta', `${chat.messages.length} сообщ. · ${new Date(chat.updated).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`)
      );

      const rename = el('button', 'icon-btn');
      rename.title = 'Переименовать';
      rename.appendChild(icon('edit'));
      rename.onclick = (event) => {
        event.stopPropagation();
        const next = prompt('Новое название чата:', chat.title);
        if (next && next.trim()) { chat.title = next.trim().slice(0, 60); store.save(); renderHistory(); renderChat(); }
      };

      const del = el('button', 'icon-btn danger');
      del.title = 'Удалить';
      del.appendChild(icon('trash'));
      del.onclick = (event) => {
        event.stopPropagation();
        item.classList.add('hitem--leaving');
        setTimeout(() => { store.remove(chat.id); renderHistory(); renderChat(); }, 260);
      };

      item.append(body, rename, del);
      item.onclick = () => {
        if (chat.id === store.activeId) return;
        store.activeId = chat.id;
        store.save();
        renderHistory();
        renderChat();
        $('sidebar').dataset.open = 'false';
      };
      list.appendChild(item);
    });
  }

  /* ================================================= 4. Отрисовка чата ==== */

  const messagesBox = $('messages');
  const welcome = $('welcome');

  function timeLabel(ts) {
    return new Date(ts).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  /* ------------------------------------------- просьба что-то написать ---- */

  /*
     Признак «просят написать код, стихи или текст».

     Сознательно повторяет серверный (python/cloudhdr_ai/profiles.py): там он
     выбирает параметры генерации, здесь — решает, дойдёт ли запрос до модели
     вообще. Разъехаться им нельзя: просьба, которую сервер считает кодом,
     обязана до сервера доехать.

     Два независимых признака, хватает любого. Первый — глагол-поручение рядом
     со словом о том, что пишут. Второй — прямое называние технологии: «html
     страница с формой» команды не содержит, а ответ там всё равно кодом.
  */
  const WANTS_WRITING = /(?:напиши|сочини|составь|сгенерируй|набросай|создай|сделай|покажи|перепиши|исправь|дополни|оформи)(?![а-яёa-z0-9_])[\s\S]{0,40}?(?<![а-яёa-z0-9_])(?:код[а-яё]*|скрипт[а-яё]*|программ[а-яё]*|функци[а-яё]*|класс|метод|стих[а-яё]*|четверостиши[а-яё]*|поэм[а-яё]*|рассказ[а-яё]*|сочинени[а-яё]*|стать[а-яё]*|письм[а-яё]*|текст[а-яё]*|инструкци[а-яё]*|разметк[а-яё]*|верстк[а-яё]*|стил[а-яё]*|запрос[а-яё]*|страниц[а-яё]*|сайт[а-яё]*|форм[а-яё]*|таблиц[а-яё]*|бота)/i;
  // \b в JavaScript видит только латиницу: после «напиши» и у «питон» он не
  // срабатывал никогда. Для кириллицы граница слова — явные просмотры.
  const NAMES_TECH = /(?<![а-яёa-z0-9_])(html|css|javascript|js|jsx|typescript|python|питон[а-яё]*|sql|json|xml|powershell|батник[а-яё]*|react|vue|node|flask|django)(?![а-яёa-z0-9_])/i;

  /** Выше этой уверенности разбора его решение не перебивается. */
  const WRITING_CONFIDENCE = 0.85;

  function wantsWriting(text) {
    return WANTS_WRITING.test(text) || NAMES_TECH.test(text);
  }

  /* ------------------------------------------------------- код в ответах -- */

  /*
     Разбор ответа на текст и блоки кода.

     Появилось вместе с Cloud HDR Ultra, который пишет программы. До этого весь
     ответ клался в пузырь как обычный текст — и это работало ровно до первой
     разметки: пропорциональный шрифт съедал отступы, схлопывал пробелы, и
     страница на html читалась как сплошная строка. Скопировать её тоже было
     нечем: выделять мышью код вперемешку с пояснением — занятие на минуту.

     Полноценный markdown здесь не нужен и был бы лишним: жирный текст и списки
     модель ставит редко, а тащить ради них разбор всего языка разметки — это
     килобайты кода и целый класс новых способов ошибиться. Тройные кавычки —
     единственное, что действительно меняет смысл содержимого.
  */
  const FENCE = /```([a-zA-Z0-9+#._-]*)[ \t]*\r?\n?([\s\S]*?)(?:```|$)/g;

  /** Делит текст на куски {kind: 'text'|'code', lang, body}. */
  function splitFences(text) {
    const source = String(text == null ? '' : text);
    const parts = [];
    let last = 0;

    FENCE.lastIndex = 0;
    for (let match = FENCE.exec(source); match; match = FENCE.exec(source)) {
      if (match.index > last) parts.push({ kind: 'text', body: source.slice(last, match.index) });
      parts.push({ kind: 'code', lang: match[1] || '', body: match[2] });
      last = FENCE.lastIndex;
      // Незакрытый блок: регулярное выражение доело строку до конца, и следующего
      // совпадения не будет. Специально не отбрасываем такой кусок — во время
      // генерации ответ ВСЕГДА выглядит незакрытым, и показать код как код
      // важнее, чем дождаться закрывающих кавычек.
      if (FENCE.lastIndex >= source.length) break;
    }
    if (last < source.length) parts.push({ kind: 'text', body: source.slice(last) });
    return parts;
  }

  /** Текст без блоков кода — то, что имеет смысл читать вслух. */
  function speakable(text) {
    const parts = splitFences(text);
    if (!parts.some((part) => part.kind === 'code')) return String(text || '');

    const spoken = parts
      .filter((part) => part.kind === 'text')
      .map((part) => part.body.trim())
      .filter(Boolean)
      .join(' ');
    // Молчать про сам код нельзя: без этой фразы помощник прочитает пояснение
    // и замолчит, как будто ответ на том и кончился.
    return (spoken ? spoken + ' ' : '') + 'Код показан в чате.';
  }

  /** Кладёт текст в пузырь, оформляя блоки кода. */
  function renderRich(host, text) {
    host.textContent = '';
    const parts = splitFences(text);

    if (!parts.some((part) => part.kind === 'code')) {
      host.textContent = String(text == null ? '' : text);
      return;
    }

    parts.forEach((part) => {
      if (part.kind === 'text') {
        const trimmed = part.body.replace(/^\s*\n|\n\s*$/g, '');
        if (trimmed.trim()) host.appendChild(el('p', 'bubble__text', trimmed));
        return;
      }
      host.appendChild(buildCodeBlock(part.lang, part.body));
    });
  }

  function buildCodeBlock(lang, body) {
    const box = el('div', 'codeblock');

    const head = el('div', 'codeblock__head');
    head.append(el('span', 'codeblock__lang', lang || 'код'));

    const copy = el('button', 'codeblock__copy', 'Копировать');
    copy.type = 'button';
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(body);
        copy.textContent = 'Скопировано';
      } catch {
        // Буфер обмена закрыт (нет разрешения, страница не в фокусе) — молча
        // это оставлять нельзя: кнопка выглядела бы сработавшей.
        copy.textContent = 'Не вышло';
      }
      setTimeout(() => { copy.textContent = 'Копировать'; }, 1600);
    };
    head.append(copy);

    const pre = el('pre', 'codeblock__pre');
    pre.appendChild(el('code', null, body.replace(/\s+$/, '')));

    box.append(head, pre);
    return box;
  }

  function buildMessage(message) {
    const wrap = el('div', 'msg msg--' + (message.role === 'user' ? 'user' : 'ai'));

    const avatar = el('div', 'msg__ava');
    if (message.role === 'user') {
      avatar.textContent = 'Вы';
    } else {
      const cloud = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      cloud.setAttribute('class', 'logo-cloud');
      cloud.setAttribute('viewBox', '4 6 120 76');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', '#i-cloud');
      cloud.appendChild(use);
      avatar.appendChild(cloud);
    }

    const column = el('div', 'msg__col');

    // Присланная картинка стоит НАД текстом, как в любом мессенджере: сначала
    // видно, о чём речь, потом — что про это спросили.
    if (message.image) column.appendChild(buildPicture(message.image));

    const bubble = el('div', 'bubble');
    renderRich(bubble, message.text);
    column.append(bubble, el('div', 'msg__time', timeLabel(message.ts)));

    // Разбор картинки — карточка между текстом и временем, на том же месте,
    // где живут карточки выполненных команд.
    if (message.shot) column.insertBefore(buildShotCard(message.shot), column.lastChild);

    wrap.append(avatar, column);
    wrap._bubble = bubble;
    wrap._column = column;
    return wrap;
  }

  function buildCommandCard(cmd) {
    const card = el('div', 'cmd');
    card.dataset.state = cmd.state || 'run';

    const head = el('div', 'cmd__head');
    const iconBox = el('div', 'cmd__icon');
    iconBox.appendChild(icon(cmd.icon || 'app'));

    const body = el('div', 'cmd__body');
    body.append(el('div', 'cmd__title', cmd.title || 'Действие'), el('div', 'cmd__sub', cmd.sub || 'Передаю команду агенту…'));

    const state = el('div', 'cmd__state', cmd.state === 'done' ? 'Готово' : cmd.state === 'error' ? 'Ошибка' : 'Выполняю');
    head.append(iconBox, body, state);

    const bar = el('div', 'cmd__bar');
    bar.appendChild(el('i'));

    card.append(head, bar);
    card._icon = iconBox;
    card._title = body.firstChild;
    card._sub = body.lastChild;
    card._state = state;
    return card;
  }

  /** Смена состояния карточки: морфинг иконки + всплеск частиц. */
  function settleCard(card, ok, subtitle) {
    card.dataset.state = ok ? 'done' : 'error';
    card._state.textContent = ok ? 'Готово' : 'Ошибка';
    card._sub.textContent = subtitle;

    card._icon.textContent = '';
    const mark = icon(ok ? 'check' : 'x');
    mark.classList.add('cmd__pop');
    card._icon.appendChild(mark);

    if (ok && !reduced()) {
      const burst = el('div', 'burst');
      for (let i = 0; i < 10; i++) {
        const spark = el('i');
        const angle = (Math.PI * 2 * i) / 10;
        const radius = 26 + Math.random() * 18;
        spark.style.setProperty('--bx', Math.cos(angle) * radius + 'px');
        spark.style.setProperty('--by', Math.sin(angle) * radius + 'px');
        spark.style.animationDelay = i * 12 + 'ms';
        burst.appendChild(spark);
      }
      card.appendChild(burst);
      setTimeout(() => burst.remove(), 900);
    }
  }

  /* ------------------------------------------------ картинка и её разбор -- */

  /** Присланная картинка в ленте: миниатюра, подпись, увеличение по щелчку. */
  function buildPicture(image) {
    const box = el('div', 'pic');

    if (image.thumb) {
      const shot = el('img', 'pic__img');
      shot.src = image.thumb;
      shot.alt = image.name || 'присланное изображение';
      shot.loading = 'lazy';
      box.appendChild(shot);
      // Щелчок разворачивает миниатюру во всю ширину пузыря. Отдельного окна
      // просмотра нет намеренно: исходник лежит у человека на диске, а здесь
      // осталась миниатюра — растягивать её на весь экран нечестно.
      box.onclick = () => { box.dataset.big = box.dataset.big === 'true' ? 'false' : 'true'; };
    } else {
      box.appendChild(el('div', 'pic__gone', 'Миниатюра стёрта, чтобы освободить место в истории'));
    }

    const caption = el('div', 'pic__cap');
    const size = window.Vision ? Vision.bytes(image.size) : image.size + ' Б';
    caption.append(icon('image'), el('span', null,
      `${image.name} · ${image.w}×${image.h} · ${size}` + (image.more > 0 ? ` · и ещё ${image.more}` : '')));
    box.appendChild(caption);

    return box;
  }

  /**
   * Карточка разбора картинки.
   *
   * Здесь намеренно много чисел. Ответ словами лежит выше, в пузыре, и его
   * достаточно, чтобы понять главное; карточка — для того, кто хочет посмотреть,
   * НА ЧЁМ этот ответ основан. Прятать измерения за словами «фото хорошее» было
   * бы ровно тем, чего проект избегает: непроверяемым мнением машины.
   */
  function buildShotCard(card) {
    const box = el('div', 'shot');
    const kind = card.kind || { title: 'изображение', icon: 'image', confidence: 0 };

    // --- шапка: что это, из чего состоит, сколько стоит по технике ---------
    const head = el('div', 'shot__head');
    const iconBox = el('div', 'shot__icon');
    iconBox.appendChild(icon(kind.icon || 'image'));

    const body = el('div', 'shot__body');
    const title = el('div', 'shot__title');
    title.append(el('b', null, capitalize(kind.title)));
    title.append(el('span', 'shot__sure', 'уверенность ' + Vision.pct(kind.confidence || 0)));
    body.appendChild(title);
    body.appendChild(el('div', 'shot__sub',
      `${card.w}×${card.h}` + (card.mp >= 0.8 ? ` · ${Vision.ru(card.mp)} Мп` : '') +
      ` · ${Vision.format(card.type)} · ${Vision.bytes(card.size)}`));
    head.append(iconBox, body);

    if (card.score != null) {
      const score = el('div', 'shot__score');
      score.dataset.level = card.score >= 80 ? 'good' : card.score >= 55 ? 'fair' : 'poor';
      score.append(el('b', null, String(card.score)), el('span', null, 'из 100'));
      score.title = 'Оценка только техники: свет, резкость, шум, баланс белого. О содержании кадра она ничего не говорит.';
      head.appendChild(score);
    }
    box.appendChild(head);

    // --- гистограмма: единственная картинка, которая говорит больше слов ----
    box.appendChild(buildHistogram(card.tone));

    // --- измерения ---------------------------------------------------------
    const grid = el('div', 'shot__grid');
    const tone = card.tone;
    metric(grid, 'Свет', `${tone.verdict}, медиана ${tone.median} из 255, перепад ≈ ${Vision.ru(tone.range)} ступени` +
      (tone.blown > 0.005 ? `, пересветы ${Vision.pct(tone.blown, 1)}` : '') +
      (tone.crushed > 0.01 ? `, провалы ${Vision.pct(tone.crushed, 1)}` : ''));

    const color = card.color;
    metric(grid, 'Цвет', color.mono
      ? 'почти монохром, цвета в кадре нет'
      : `${color.cast === 'нейтральный' ? 'баланс белого ровный' : 'уклон ' + color.cast}` +
        (color.temperature ? `, оценка ${color.temperature} К` : '') +
        `, насыщенность ${Vision.pct(color.saturation)}`);

    metric(grid, 'Резкость', `${card.detail.verdict}, шум ${card.detail.noiseText}` +
      (card.detail.motion != null ? `, похоже на смаз около ${card.detail.motion}°` : '') +
      (card.detail.blockiness > 0.55 ? ', видны следы пережатия' : ''));

    const frame = card.frame;
    const frameParts = [frame.orientation];
    if (frame.tilt != null && Math.abs(frame.tilt) > 0.5) frameParts.push(`наклон ${Vision.ru(frame.tilt)}°`);
    if (frame.thirds) frameParts.push(frame.thirds.centered ? 'главное в центре' : 'главное смещено к трети');
    if (frame.faces) frameParts.push(`пятен телесного тона: ${frame.faces}`);
    metric(grid, 'Кадр', frameParts.join(', '));

    if (card.shot) {
      const shot = card.shot;
      const parts = [];
      if (shot.camera) parts.push(shot.camera);
      if (shot.lens) parts.push(shot.lens);
      if (shot.exposure) parts.push(shot.exposure);
      if (shot.aperture) parts.push(shot.aperture);
      if (shot.iso) parts.push('ISO ' + shot.iso);
      if (shot.focal) parts.push(shot.focal);
      if (shot.date) parts.push(shot.date);
      if (parts.length) metric(grid, 'Съёмка', parts.join(' · '));
    }
    box.appendChild(grid);

    // --- палитра -----------------------------------------------------------
    if (color.palette && color.palette.length) {
      const strip = el('div', 'shot__palette');
      color.palette.forEach((tint) => {
        const chip = el('i', 'shot__tint');
        chip.style.background = tint.hex;
        chip.style.flexGrow = String(Math.max(0.4, tint.share * 10));
        chip.title = `${tint.name} · ${tint.hex} · ${Vision.pct(tint.share)}`;
        strip.appendChild(chip);
      });
      box.appendChild(strip);
      box.appendChild(el('div', 'shot__tints',
        color.palette.slice(0, 4).map((tint) => `${tint.name} ${Vision.pct(tint.share)}`).join(' · ')));
    }

    // --- что замечено сверх измерений --------------------------------------
    if (card.notes && card.notes.length) {
      const notes = el('ul', 'shot__notes');
      card.notes.forEach((note) => notes.appendChild(el('li', null, note)));
      box.appendChild(notes);
    }

    // --- что улучшить ------------------------------------------------------
    if (card.advice && card.advice.length) {
      const more = el('details', 'shot__more');
      more.appendChild(el('summary', null, `Что улучшить — ${card.advice.length}`));
      const list = el('ol', 'shot__advice');
      card.advice.forEach((item) => {
        const line = el('li');
        line.append(el('b', null, item.title), el('span', null, item.detail));
        list.appendChild(line);
      });
      more.appendChild(list);
      box.appendChild(more);
    }

    // --- поправка: тут сеть и учится ---------------------------------------
    if (card.features) box.appendChild(buildKindFix(card, title));

    return box;
  }

  function metric(grid, label, value) {
    const row = el('div', 'metric');
    row.append(el('b', null, label), el('span', null, value));
    grid.appendChild(row);
  }

  function capitalize(text) {
    const value = String(text || '');
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  /**
   * Гистограмма яркости — 64 столбика от чёрного к белому.
   *
   * Рисуется на холсте, а не сотней <i> с высотой в стилях: столбиков много,
   * они в каждой карточке, и разметка из них заметно тяжелее одной картинки.
   */
  function buildHistogram(tone) {
    const box = el('div', 'shot__hist');
    const canvas = el('canvas');
    const width = 520, height = 64;
    const scale = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);

    const hist = tone.hist || [];
    const peak = Math.max(0.0001, ...hist);
    const step = width / (hist.length || 1);

    for (let i = 0; i < hist.length; i++) {
      // Высота по корню, а не напрямую: у обычного кадра один-два столбика выше
      // остальных в десятки раз, и в линейном масштабе всё прочее слипается в
      // полоску у самого низа. Форма распределения при этом сохраняется.
      const value = Math.sqrt(hist[i] / peak);
      const bar = Math.max(1, value * (height - 6));
      const shade = Math.round((i / Math.max(1, hist.length - 1)) * 255);
      ctx.fillStyle = `rgba(${shade}, ${shade}, ${Math.min(255, shade + 24)}, .85)`;
      ctx.fillRect(i * step, height - bar, Math.max(1, step - 0.6), bar);
    }

    // Отметки провалов и пересветов — по краям, там же, где они и происходят.
    if (tone.crushed > 0.01) {
      ctx.fillStyle = 'rgba(251, 113, 133, .75)';
      ctx.fillRect(0, 0, 3, height);
    }
    if (tone.blown > 0.005) {
      ctx.fillStyle = 'rgba(251, 191, 36, .8)';
      ctx.fillRect(width - 3, 0, 3, height);
    }

    canvas.style.width = '100%';
    canvas.style.height = height + 'px';
    box.appendChild(canvas);
    box.appendChild(el('div', 'shot__scale', 'тени · средние тона · света'));
    return box;
  }

  /**
   * Строка поправки рода картинки.
   *
   * Ради неё всё и затевалось с обучаемым классификатором: приложение может
   * ошибиться (снимок экрана с фотографией внутри — законный повод), и вместо
   * «спорить с машиной» человек нажимает верное слово. Пример ложится в память
   * браузера, веса пересчитываются мгновенно, следующая такая картинка
   * определяется правильно. Никуда не отправляется ничего.
   */
  function buildKindFix(card, titleNode) {
    const box = el('div', 'shot__fix');
    box.appendChild(el('span', 'shot__fix-label', 'Не тот род?'));

    Vision.CLASSES.forEach((id) => {
      if (id === card.kind.id) return;
      const button = el('button', 'shot__fix-btn', Vision.KINDS[id].title);
      button.type = 'button';
      button.onclick = () => {
        const result = Vision.learn(card.features, id);
        if (!result) { toast('Поправку принять не вышло', 'err'); return; }
        card.kind = { ...card.kind, id, title: result.title, icon: Vision.KINDS[id].icon, confidence: 1, corrected: true };
        store.save();

        titleNode.textContent = '';
        titleNode.append(el('b', null, capitalize(result.title)), el('span', 'shot__sure', 'по вашей поправке'));
        box.textContent = '';
        box.appendChild(el('span', 'shot__fix-done',
          `Запомнил: это ${result.title}. Учтено примеров — ${result.samples}.`));
        toast('Запомнил поправку — следующая такая картинка определится верно', 'ok');
      };
      box.appendChild(button);
    });

    return box;
  }

  function renderChat() {
    const chat = store.active;
    $('chatTitle').textContent = chat.title;
    $('chatSubtitle').textContent = chat.messages.length
      ? `${chat.messages.length} сообщений · ${new Date(chat.created).toLocaleDateString('ru-RU')}`
      : 'Cloud HDR AI готов управлять вашим ПК';

    messagesBox.textContent = '';
    welcome.hidden = chat.messages.length > 0;

    chat.messages.forEach((message, index) => {
      const node = buildMessage(message);
      node.style.animationDelay = Math.min(index * 35, 300) + 'ms';
      messagesBox.appendChild(node);
      if (message.cmd) node._column.insertBefore(buildCommandCard(message.cmd), node._column.lastChild);
    });
    scrollDown(false);
  }

  function scrollDown(smooth = true) {
    requestAnimationFrame(() => {
      messagesBox.scrollTo({ top: messagesBox.scrollHeight, behavior: smooth && !reduced() ? 'smooth' : 'auto' });
    });
  }

  /* ============================================ 5. Размышление и печать === */

  /**
   * Запасные фразы — используются, только пока разбор ещё не вернул свой trace.
   * Как только он готов, панель показывает НАСТОЯЩИЕ шаги рассуждения:
   * что предсказала сеть, что сказало правило, какие слоты извлечены.
   */
  const THINKING_PHRASES = [
    'Считаю признаки фразы',
    'Прогоняю через нейросеть',
    'Сверяю с правилами',
    'Извлекаю цель запроса'
  ];

  function showThinking() {
    const wrap = el('div', 'msg msg--ai');
    const avatar = el('div', 'msg__ava');
    const cloud = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    cloud.setAttribute('class', 'logo-cloud');
    cloud.setAttribute('viewBox', '4 6 120 76');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-cloud');
    cloud.appendChild(use);
    avatar.appendChild(cloud);

    const column = el('div', 'msg__col');
    const box = el('div', 'thinking');

    const core = el('div', 'core');
    core.append(el('i', 'core__ring'), el('i', 'core__dot'), el('i', 'core__wave'), el('i', 'core__wave'));

    const text = el('div', 'thinking__text', THINKING_PHRASES[0]);
    const dots = el('div', 'dots');
    dots.append(el('i'), el('i'), el('i'));

    box.append(core, text, dots);
    column.appendChild(box);
    wrap.append(avatar, column);
    messagesBox.appendChild(wrap);
    scrollDown();

    let steps = THINKING_PHRASES.slice();
    let index = 0;
    text.style.transition = 'opacity .18s ease';

    const timer = setInterval(() => {
      index = (index + 1) % steps.length;
      text.style.opacity = '0';
      setTimeout(() => { text.textContent = steps[index]; text.style.opacity = '1'; }, 180);
    }, 900);

    return {
      node: wrap,
      /** Подменяет заглушки реальными шагами рассуждения. */
      reason(trace) {
        if (!Array.isArray(trace) || !trace.length) return;
        steps = trace.slice(0, 6);
        index = 0;
        text.textContent = steps[0];
        box.dataset.real = 'true';
      },
      stop() { clearInterval(timer); wrap.remove(); }
    };
  }

  /**
   * Появление ответа — по словам, а не по буквам.
   *
   * Раньше текст набирался посимвольно, по два-четыре знака за кадр. Это
   * похоже на печатную машинку, но читать так невозможно: глаз ловит слово
   * целиком, а слово всё время достраивается, и приходится либо ждать конца
   * строки, либо перечитывать. Плюс на каждый кадр менялась ширина последнего
   * слова, и хвост строки дёргался.
   *
   * Теперь слово появляется готовым — проявляется из размытия и чуть
   * приподнимается. Читать можно с первого кадра, никакого дрожания нет, а
   * ощущение «текст рождается на глазах» становится только сильнее: движение
   * идёт не по одной букве, а волной по всей строке.
   *
   * Общее время осталось прежним — около секунды на средний ответ. Скорость
   * привязана к длине: короткая реплика не должна выкладываться по слогам,
   * длинная — заставлять ждать.
   */
  function typewrite(bubble, text) {
    return new Promise((resolve) => {
      if (reduced()) { bubble.textContent = text; return resolve(); }

      bubble.textContent = '';
      bubble.dataset.writing = 'true';
      const caret = el('span', 'caret');
      bubble.appendChild(caret);

      // Пробелы сохраняются отдельными узлами: у пузыря white-space:pre-wrap,
      // и переносы строк в ответе значимы — «схлопнуть» их нельзя.
      const parts = text.split(/(\s+)/).filter((part) => part !== '');
      const words = parts.filter((part) => !/^\s+$/.test(part)).length;

      // Чем длиннее ответ, тем быстрее слова: 34 мс на слово в короткой
      // реплике и 14 мс в длинной — так и то и другое укладывается примерно в
      // секунду.
      const step = words > 60 ? 14 : words > 24 ? 22 : 34;

      let index = 0;
      let sinceScroll = 0;

      const tick = () => {
        if (!bubble.isConnected) { resolve(); return; }

        // За кадр выкладывается всё, что помещается в его бюджет: при 144 Гц
        // кадры идут вдвое чаще, и по одному слову на кадр текст летел бы
        // вдвое быстрее, чем на 60 Гц.
        const budget = performance.now() + 8;
        do {
          const part = parts[index++];
          if (/^\s+$/.test(part)) {
            caret.before(document.createTextNode(part));
          } else {
            const word = el('span', 'word', part);
            caret.before(word);
            sinceScroll += part.length;
          }
        } while (index < parts.length && performance.now() < budget && step < 16);

        if (sinceScroll > 40) { sinceScroll = 0; scrollDown(); }

        if (index < parts.length) {
          setTimeout(() => requestAnimationFrame(tick), step);
        } else {
          // Готовый текст кладётся обычной строкой: разметка из сотни span-ов
          // нужна была только для появления, а дальше мешает — её копируют
          // вместе с текстом и по ней ищут в истории.
          setTimeout(() => {
            caret.remove();
            bubble.textContent = text;
            delete bubble.dataset.writing;
            scrollDown();
          }, 260);
          resolve();
        }
      };

      requestAnimationFrame(tick);
    });
  }

  /* ================================================ 6. Отправка запроса == */

  let busy = false;

  /** Кратковременная память диалога: позволяет понимать «а теперь закрой его». */
  const context = { lastApp: null, lastIntent: null, lastTarget: null, route: null };

  // Похоже на просьбу что-то СДЕЛАТЬ — только такие фразы спрашиваем у модели
  // как команды; разговор и вопросы о мире идут в чат сразу, без лишней секунды.
  const ACTION_START = /^(?:пожалуйста\s+|ну\s+|давай\s+|слушай\s+|а\s+)?(?:открой|закрой|запусти|включи|выключи|вруби|выруби|найди|поищи|загугли|покажи|глянь|сверни|разверни|перемести|перенеси|закинь|скопируй|переименуй|создай|установи|поставь|сделай|переключи|перейди|перезапусти|проверь|разбери|напомни|засеки|переведи|убери|очисти|верни|сожми|распакуй|скачай|прибавь|убавь|выведи|отправь|мне\s+надо|хочу|нужно)(?![а-яё])/i;

  async function routeRequest(text, parsed) {
    if (!LLM.available || typeof LLM.route !== 'function') return null;
    const answering = context.route && context.route.until > Date.now();
    const history = answering ? context.route.history : [];
    context.route = null;
    if (!answering) {
      if (!ACTION_START.test(text) || wantsWriting(text)) return null;
      // Уверенно разобранную команду не трогаем: «открой хром» (0.99) идёт как
      // раньше. Кроме фраз из нескольких действий — «открой браузер и найди
      // котиков»: разбор уверенно узнавал первое и терял второе.
      const several = /\s(и|а\s+потом|потом|затем|после\s+этого)\s+\S/i.test(text) || /,\s*\S+\s/.test(text);
      const sure = NLU.EXECUTABLE.has(parsed.intent) && !parsed.freeform && (parsed.confidence || 0) >= 0.85;
      // план разбора считаем, только если все его шаги — выполнимые команды:
      // «найди котиков» он делил в шаг «chat», и поиск молча пропадал
      const planned = parsed.plan && parsed.plan.length > 0 && parsed.plan.every((step) => NLU.EXECUTABLE.has(step.intent));
      if (sure && !(several && !planned)) return null;
    }
    const routed = await LLM.route(text, history);
    return routed && (routed.ask || routed.commands.length) ? routed : null;
  }

  /** Выполнить команды от модели по очереди — тем же путём, что набранные руками. */
  async function runRouted(commands) {
    for (const command of commands) {
      while (busy) await wait(150);
      await send(command);
    }
  }

  async function send(rawText) {
    const text = String(rawText || '').trim();
    // Пустая строка больше не означает «отправлять нечего»: к сообщению могла
    // быть приложена картинка, а вопрос к ней не обязателен — «посмотри» и есть
    // весь вопрос.
    const attached = window.Vision ? Vision.count : 0;
    if (!text && !attached) return;

    /*
       Занятость экстренную фразу не останавливает.

       Обычный запрос при занятом помощнике просто игнорируется — и правильно:
       два разбора одновременно перепутали бы карточки в чате. Но занят
       помощник может быть надолго: языковая модель отвечает секунд десять.
       Отказать в эти секунды именно экстренной фразе значит отказать ровно
       тогда, когда она и понадобилась. Поэтому она прерывает то, что идёт:
       генерация останавливается, голос замолкает, действие выполняется.
    */
    const urgent = text && window.SOS ? SOS.find(text) : null;
    if (busy && !urgent) return;
    if (busy && urgent) {
      if (window.LLM) LLM.stop();
      if (window.Voice) Voice.stop();
      busy = false;
    }

    // 0а. экстренная фраза — раньше всего остального
    //
    // Порядок здесь и есть смысл функции. Ниже по тексту: списание токенов,
    // размышление, нейросеть, spaCy, словари, агент — полторы-две секунды и
    // с десяток мест, каждое из которых может решить, что не поняло запрос.
    // Для обычной фразы это правильно. Для той, которую человек назначил
    // руками и заранее, любая из этих ступеней — только лишний шанс НЕ
    // сработать. Совпало — выполняем.
    if (urgent) {
      await runEmergency(urgent.rule, text);
      return;
    }

    /*
       0в. руки — сразу после экстренной фразы и до всего остального.

       «curs» включает управление мышью, а пока оно включено, любая фраза —
       это поручение рукам, а не вопрос помощнику. Разбирать её ещё раз нельзя
       и не нужно: «открой проводник» в этом режиме означает открыть его
       по-настоящему, курсором, а не сопоставить намерение с заготовкой.

       Голос попадает сюда же: и клавиатура, и постоянное прослушивание
       сходятся в этой функции, поэтому отдельной ветки для речи не требуется.

       Проверка стоит ДО списания токенов намеренно — запас тратится на
       размышления, а не на передачу поручения соседнему процессу.
    */
    if (text && window.CursorHands && CursorHands.intercept(text)) return;

    // 0в'. умения второй версии: «разбери загрузки», «проверь компьютер»,
    // «подключи телефон», «как переводить с экрана». Узнаются по фразе и
    // выполняются своими окнами, мимо разбора команд и языковой модели.
    if (text && !busy && window.Features && await Features.intercept(text)) return;

    /*
       0г. картинка на подносе — дальше смотрит зрение, а не разбор команд.

       Стоит здесь, до всего остального, по той же причине, по которой выше
       стоят руки: приложенная картинка сама по себе означает, о чём речь.
       Пропусти мы её через обычный путь — «что тут не так» ушло бы
       классификатору намерений, тот нашёл бы что-нибудь про настройки, и
       приложение ответило бы не на то, что видит перед собой.

       Разбор идёт полностью здесь, в браузере: ни агент, ни языковая модель для
       него не нужны. Работает и без запущенной модели, и в демо-режиме.
    */
    if (attached) {
      await runVision(text);
      return;
    }

    /*
       0д. попросили посмотреть, но ничего не приложили.

       Два случая. «Разбери последний скриншот» — снимок лежит на диске, и
       забрать его может агент: своей же функцией «сделай скриншот» приложение
       их и наделало. «Посмотри на фото» — где это фото, знает только человек,
       и правильный ответ здесь — открыть окно выбора, а не переспрашивать.
    */
    const look = window.Vision ? Vision.asks(text) : null;
    if (look) {
      await runLook(look, text);
      return;
    }

    // 0б. бюджет токенов — проверяем ДО того, как что-либо делать
    const bill = Tokens.charge();
    if (!bill.ok) {
      showBudgetBlock(bill);
      return;
    }
    renderTokens();

    busy = true;
    setComposerEnabled(false);
    if (window.Listener) Listener.busy(true);      // услышанное следом встанет в очередь, а не пропадёт

    // 1. сообщение пользователя
    const userMessage = store.push({ id: uid(), role: 'user', text, ts: Date.now() });
    welcome.hidden = true;
    const userNode = buildMessage(userMessage);
    messagesBox.appendChild(userNode);
    scrollDown();
    renderHistory();
    $('chatTitle').textContent = store.active.title;

    // 2. размышление — панель показывает реальные шаги разбора
    const thinking = showThinking();
    const parsed = await NLU.parseSmart(text, context);
    thinking.reason(parsed.trace);
    await wait(reduced() ? 100 : 420 + Math.min(parsed.trace.length, 5) * 130);
    thinking.stop();

    // 1б. модель как переводчик просьб в команды.
    //
    // Шаблоны понимают точные формулировки; «открой браузер и глянь погоду»
    // или «включи что-нибудь послушать» они не узнают, и раньше такая фраза
    // уходила в чат — модель отвечала текстом, а не делала. Теперь модель
    // переписывает её в команды из каталога, а выполняет их обычный путь —
    // тот же send(). Или задаёт один уточняющий вопрос, и ответ на него
    // уходит обратно вместе с вопросом.
    const routed = await routeRequest(text, parsed);
    if (routed && routed.ask) {
      await reply(routed.ask, null);
      context.route = { until: Date.now() + 3 * 60 * 1000,
        history: [{ role: 'user', content: text }, { role: 'assistant', content: routed.ask }] };
      return finish();
    }
    if (routed && routed.commands.length) {
      await reply(routed.say || 'Делаю: ' + routed.commands.join(' → '), null);
      finish();
      runRouted(routed.commands);
      return;
    }

    // 2а. просьба что-то НАПИСАТЬ — код, стихи, текст
    //
    // Стоит ДО отложенных навыков, и это выяснилось дорогой ценой. Разбор
    // определяет «напиши код html страницы с кнопкой» как analyze_chats с
    // уверенностью 0.18 — то есть как «проанализируй наши разговоры». Намерение
    // отложенное, обрабатывается вот прямо ниже, и до развилки со свободным
    // разговором дело не доходит вовсе: на просьбу о вёрстке приложение
    // отвечало «Разобрал всю историю: 1 сообщение».
    //
    // Почему разбор так ошибается — понятно и неисправимо на его уровне: он
    // обучен на поручениях компьютеру, и «напиши», «сделай», «покажи» для него
    // сильные глаголы действия. Перечислить все формы просьбы о коде в его
    // корпусе нельзя, а вот узнать их отдельной проверкой — можно.
    //
    // Порог высокий (0.85 против обычных 0.7) намеренно: перебить разобранное
    // намерение — шаг серьёзный, и делать это можно только там, где разбор сам
    // не уверен. «Открой хром» (0.99) и «найди игру амонг ас» (0.98) сюда не
    // попадают и работают как раньше.
    if (wantsWriting(text) && (parsed.confidence || 0) < WRITING_CONFIDENCE && LLM.available) {
      await streamFromModel(text, parsed);
      return finish();
    }

    /*
       2б. отложенное намерение, в котором разбор не уверен, — не выполняем.

       Отложенные навыки (анализ истории, таймер, настройки) до сих пор
       выполнялись при ЛЮБОЙ уверенности, включая четверть. Для остальных
       намерений такое правило есть с давних пор — заготовка выигрывает только
       от 0.7 (CANNED_CONFIDENCE ниже), — а отложенные его почему-то обходили.

       Чего это стоило на практике. Разбор относит к `settings` всё, где звучит
       «модель», «сеть», «обучи», «покажи параметры»: в корпусе рядом лежат
       «переобучи модель» и «какая точность у твоей модели». А стоит человеку
       спросить про модель своими словами — открывается окно настроек ПОВЕРХ
       чата и приходит ответ, начинающийся со слов «Израсходовано 2% часового
       запаса». Выглядит это как «приложение упёрлось в лимит токенов и не
       пускает дальше», хотя запас цел, а окно открылось по ошибке разбора.
       Закрыл, переспросил иначе — и снова то же самое.

       Порог тот же, что у заготовок, и по той же причине: цена ошибки здесь
       выше обычной. Заготовка не по делу — это неудачная реплика. Отложенный
       навык не по делу — это открытое окно, перебитый разговор и потраченная
       минута на то, чтобы понять, что вообще произошло.
    */
    const DEFERRED_CONFIDENCE = 0.7;
    if (parsed.deferred && (parsed.confidence || 0) < DEFERRED_CONFIDENCE && LLM.available) {
      await streamFromModel(text, parsed);
      return finish();
    }

    // 3. отложенные навыки — им нужны данные, которых нет внутри разбора
    if (parsed.deferred === 'analyze') {
      const report = Skills.analyzeChats(store.chats, text);
      await reply(report.answer, null, { trace: report.trace });
      return finish();
    }
    if (parsed.deferred === 'timer') {
      const timer = Skills.timer(text, (label) => {
        toast(`⏰ Таймер на ${label} завершён`, 'ok');
        reply(`Время вышло — таймер на ${label} отработал.`, null);
      });
      await reply(timer ? timer.answer : 'На сколько поставить таймер? Например: «поставь таймер на 5 минут».', null,
        { trace: timer ? timer.trace : [] });
      return finish();
    }
    if (parsed.deferred === 'settings') {
      openSettings();
      // Сначала о том, ЧТО произошло, и только потом цифры. Прежде ответ
      // открывался строкой «Израсходовано N% часового запаса», и человек читал
      // её как отказ по лимиту — тем более что окно в этот момент закрывало
      // собой чат. Сообщение должно объяснять действие, а не начинаться со
      // сводки, о которой не спрашивали.
      await reply('Открыл настройки — там лимиты токенов, голос и обучение.\n' + budgetSummary(), null);
      return finish();
    }

    // 4. свободный разговор — если подключена локальная модель, отвечает она
    //
    // Кроме явно помеченных freeform сюда уходят НЕисполнимые намерения, в
    // которых разбор не уверен. Причина конкретная: перечислить все формы
    // вопроса регулярным выражением нельзя. «Что лучше, видеокарта или
    // процессор» под список зачинов не попадало, проваливалось к
    // классификатору, тот с уверенностью в четверть выбирал `recall` — и
    // пользователь получал «я помню о вас один факт» вместо ответа.
    //
    // Порог сравнивает две ошибки. Заготовка, выбранная наугад, — ответ не по
    // делу и выглядит поломкой. Ответ маленькой модели — по теме, пусть и
    // небезупречный. Поэтому заготовка выигрывает только там, где разбор
    // ДЕЙСТВИТЕЛЬНО уверен: правила и точные совпадения дают 0,9 и выше, так
    // что «что ты умеешь» и «что ты обо мне помнишь» по-прежнему отвечает
    // приложение, а не модель. Исполнимых команд это не касается вовсе —
    // они разбираются до этой развилки и идут своим путём.
    const CANNED_CONFIDENCE = 0.7;
    const unsureAnswer = !NLU.EXECUTABLE.has(parsed.intent) &&
                         !parsed.deferred &&
                         (parsed.confidence || 0) < CANNED_CONFIDENCE;

    if ((parsed.freeform || unsureAnswer) && LLM.available) {
      await streamFromModel(text, parsed);
      return finish();
    }

    // 5. не действие — отвечаем заготовкой
    if (!NLU.EXECUTABLE.has(parsed.intent)) {
      // порог по калиброванной уверенности: ниже 0,3 сеть действительно
      // сомневается, и подсказка пользователя реально чему-то научит
      await reply(parsed.reply, null, { correction: parsed.confidence < 0.3, text });
      return finish();
    }

    // 5. без агента выполнять нечего
    if (Agent.mode === 'offline') {
      await reply('Я разобрал запрос, но агент управления системой не подключён. Запустите start.bat в папке cloud-hdr, ' +
        'нажмите кнопку питания слева и разрешите доступ — либо переключитесь в демо-режим.', null);
      return finish();
    }

    // 6. опасные действия — только после подтверждения
    //
    // Закрытия программ здесь больше нет, и это не упущение. Раньше на «закрой
    // дискорд» помощник открывал окно «вы уверены?», а следом агент отвечал
    // «требуется подтверждение пользователя»: человек говорил вслух, что хочет
    // сделать, и дважды получал вопрос о том же самом. Осталась блокировка
    // рабочей станции — её обратно одним словом не отменишь, там переспросить
    // уместно.
    if (parsed.needsConfirm) {
      const confirmed = await askConfirm('Подтвердите действие', parsed.reply);
      if (!confirmed) {
        await reply('Хорошо, отменяю — ничего не трогаю.', null);
        return finish();
      }
      const card = await reply(`Выполняю: ${parsed.title}.`, parsed);
      await runCommand(parsed, card, true);
      return finish();
    }

    // 7. обычное выполнение
    const card = await reply(parsed.reply, parsed);
    await runCommand(parsed, card, false);

    // 8. остальные шаги плана — «открой хром и найди там котиков» это две
    //    команды, и раньше вторая просто терялась. Выполняются по очереди:
    //    вторая часто зависит от первой (сначала открыть, потом искать).
    for (const step of parsed.plan || []) {
      if (!NLU.EXECUTABLE.has(step.intent)) continue;
      if (step.needsConfirm) {
        const ok = await askConfirm('Подтвердите действие', step.reply);
        if (!ok) {
          await reply('Хорошо, этот шаг пропускаю.', null);
          continue;
        }
      }
      const stepCard = await reply(step.reply, step);
      await runCommand(step, stepCard, Boolean(step.needsConfirm));
    }
    finish();

    function finish() {
      // запоминаем контекст для местоимений в следующей реплике
      if (parsed.intent === 'open_app' && parsed.target) {
        context.lastApp = { key: parsed.target, title: parsed.title, icon: parsed.icon };
      }
      context.lastIntent = parsed.intent;
      context.lastTarget = parsed.target || null;
      // «а теперь то же самое» повторяет прошлую фразу целиком
      context.lastPhrase = text;

      busy = false;
      setComposerEnabled(true);
      if (window.Listener) Listener.busy(false);
      $('input').focus();
    }
  }

  /* ================================================= 6б. Зрение =========== */

  /** «Посмотри» без вопроса — тогда отвечает разбор, а не языковая модель. */
  function isPlainLook(text) {
    const value = String(text || '').trim().toLowerCase();
    if (!value) return true;
    if (value.length < 4) return true;
    return /^(посмотри|глянь|взгляни|разбери|проанализируй|оцени|опиши|что тут|что здесь|что это|что на (этом|этой|картинке|фото|снимке))[\s.,!?]*$/i.test(value);
  }

  /**
   * Разбор присланных картинок.
   *
   * Порядок шагов тот же, что у обычного запроса, и это не совпадение: человек
   * видит привычную цепочку — его сообщение, панель размышления с настоящими
   * шагами, ответ. Разница в том, что шаги здесь не выдуманы для вида: панель
   * показывает, что именно сейчас считается, и считается это по-настоящему.
   */
  async function runVision(question) {
    // Бюджет проверяем ДО того, как забрать картинки с подноса: иначе отказ по
    // лимиту оставил бы человека и без ответа, и без приложенного файла.
    const bill = Tokens.charge();
    if (!bill.ok) {
      showBudgetBlock(bill);
      return;
    }
    renderTokens();

    busy = true;
    setComposerEnabled(false);
    if (window.Listener) Listener.busy(true);

    const shots = Vision.take();
    renderTray();

    const first = shots[0];
    const text = question || (shots.length > 1
      ? `Посмотри на эти ${shots.length} картинки`
      : 'Посмотри, что на этой картинке');

    const userMessage = store.push({
      id: uid(), role: 'user', text, ts: Date.now(),
      image: {
        thumb: first.thumb, name: first.name, w: first.width, h: first.height,
        size: first.size, more: shots.length - 1
      }
    });
    welcome.hidden = true;
    messagesBox.appendChild(buildMessage(userMessage));
    scrollDown();
    renderHistory();
    $('chatTitle').textContent = store.active.title;

    const thinking = showThinking();
    let opened = false;

    try {
      for (const shot of shots) {
        const report = await Vision.analyze(shot, (stage) => thinking.reason([stage]));
        if (!opened) { thinking.stop(); opened = true; }

        // Миниатюра уже показана в сообщении человека прямо над карточкой —
        // второй копии в истории браузера делать незачем, место не резиновое.
        report.thumb = null;
        await answerVision(report, question, shots.length > 1);
        Vision.free(shot);
      }
    } catch (error) {
      if (!opened) thinking.stop();
      shots.forEach(Vision.free);
      await reply('Разобрать картинку не вышло: ' + (error.message || error), null);
      toast('Картинку разобрать не удалось', 'err');
    } finally {
      busy = false;
      setComposerEnabled(true);
      if (window.Listener) Listener.busy(false);
      renderTray();
      scrollDown();
    }
  }

  /**
   * Ответ по разобранной картинке.
   *
   * Кто отвечает словами — решается здесь, и решение осознанное.
   *
   * Просто «посмотри» отвечает сам разбор: у него на руках все измерения, он
   * не ошибается в числах и не зависит от того, поднята ли языковая модель.
   * Заданный вопрос («почему тут так темно?», «стоит ли это печатать?»)
   * уходит модели — но не с картинкой, которой она не видит, а с факт-листом
   * измерений. Так модель говорит о том, что действительно измерено.
   *
   * История разговора модели при этом НЕ передаётся, и это не забывчивость.
   * Факт-лист сам по себе занимает изрядную часть окна в 2048 токенов, и
   * прошлые реплики вытеснили бы оттуда именно те строки, ради которых запрос
   * и делается.
   */
  async function answerVision(report, question, many) {
    const message = { id: uid(), role: 'ai', text: '', ts: Date.now(), shot: report };
    const node = buildMessage(message);
    messagesBox.appendChild(node);
    scrollDown();

    const ask = !isPlainLook(question) && LLM.available;

    if (!ask) {
      const text = report.summary;
      message.text = text;
      if (window.Voice) Voice.speak(text);
      await typewrite(node._bubble, text);
      node._column.insertBefore(buildTrace([
        `зрение: ${report.kind.title}, уверенность ${Vision.pct(report.kind.confidence)}`,
        `измерено на месте: свет, цвет, резкость, кадр${report.shot ? ', паспорт EXIF' : ''}`,
        report.advice.length ? `нашлось замечаний: ${report.advice.length}` : 'замечаний нет',
        'картинка не покидала компьютер'
      ]), node._column.lastChild);
      store.push(message);
      return;
    }

    const bubble = node._bubble;
    bubble.textContent = '';
    const caret = el('span', 'caret');
    bubble.appendChild(caret);
    node.dataset.streaming = 'true';
    setModelBusy(true);
    setStopVisible(true);
    let sinceScroll = 0;

    try {
      const result = await LLM.ask(Vision.brief(report, question), [], (chunk) => {
        caret.before(document.createTextNode(chunk));
        sinceScroll += chunk.length;
        if (sinceScroll > 40) { sinceScroll = 0; scrollDown(); }
      });

      caret.remove();
      node.dataset.streaming = 'false';

      // Пустой ответ модели — не повод оставить человека ни с чем: разбор свой
      // ответ уже посчитал, и он ничем не хуже.
      const answer = (result.text || '').trim() || report.summary;
      renderRich(bubble, answer);
      message.text = answer;
      if (window.Voice && !result.stopped) Voice.speak(speakable(answer));

      node._column.insertBefore(buildTrace([
        `зрение: ${report.kind.title}, уверенность ${Vision.pct(report.kind.confidence)}`,
        `измерения переданы модели ${LLM.current().title} как факты`,
        `фрагментов: ${result.tokens}, за ${(result.ms / 1000).toFixed(1)} с`,
        'ни картинка, ни измерения не покидали компьютер'
      ]), node._column.lastChild);
    } catch (error) {
      caret.remove();
      node.dataset.streaming = 'false';
      // Модель не поднялась или оборвалась — отвечаем своим разбором и говорим,
      // почему ответ такой, какой есть.
      const answer = report.summary + '\n\n⚠ Ответить словами на ваш вопрос не вышло: ' + error.message;
      renderRich(bubble, answer);
      message.text = answer;
      if (window.Voice) Voice.speak(report.summary);
    } finally {
      setModelBusy(false);
      setStopVisible(false);
      store.push(message);
      if (many) scrollDown();
    }
  }

  /** Попросили посмотреть, но ничего не приложили. */
  async function runLook(kind, text) {
    if (kind !== 'screenshot') {
      const picker = $('visionFile');
      if (picker) picker.click();
      await reply('Открыл окно выбора — покажите картинку, и я её разберу. Можно ещё перетащить файл прямо в окно ' +
                  'или вставить из буфера обмена по Ctrl+V.', null);
      return;
    }

    if (!Agent.isLive) {
      await reply('Снимки с диска берёт агент, а разрешения на управление системой сейчас нет. ' +
                  'Нажмите кнопку питания слева и разрешите доступ — либо просто перетащите картинку в окно.', null);
      return;
    }

    busy = true;
    setComposerEnabled(false);
    let picked = null;
    try {
      const files = await Vision.recent(1);
      picked = files[0] || null;
      if (picked) await Vision.fromAgent(picked.path, picked.name);
    } catch (error) {
      busy = false;
      setComposerEnabled(true);
      await reply('Не смог взять снимок с диска: ' + (error.message || error), null);
      return;
    }
    busy = false;
    setComposerEnabled(true);

    if (!picked) {
      await reply('Свежих снимков экрана не нашлось. Скажите «сделай скриншот» — я сниму экран и тут же его разберу.', null);
      return;
    }

    renderTray();
    toast(`Взял снимок «${picked.name}»`, 'ok');
    await runVision(text);
  }

  /* ============================================ 6a. Экстренная помощь ===== */

  /**
   * Выполняет заранее настроенное действие по фразе-сигналу.
   *
   * Отличается от обычного пути тремя вещами, и все три — про скорость:
   *
   *   • ответ не печатается посимвольно, а появляется целиком. Печать длится
   *     секунду-полторы и здесь работает против себя;
   *   • команда уходит агенту СРАЗУ, не дожидаясь появления сообщения на
   *     экране: сначала действие, потом рассказ о нём;
   *   • токены не списываются. Экстренная фраза не обращается ни к языковой
   *     модели, ни к разбору — платить за неё нечем и не за что, а упереться в
   *     исчерпанный лимит именно в этот момент было бы издевательством.
   */
  async function runEmergency(rule, said) {
    busy = true;
    setComposerEnabled(false);
    if (window.Listener) Listener.busy(true);

    const action = rule.action || {};
    SOS.markFired(rule);

    // Действие первым делом — до отрисовки чего бы то ни было.
    const running = Agent.mode === 'offline'
      ? Promise.reject(new Error('агент управления системой не подключён'))
      : Agent.exec({
          intent: action.intent,
          target: action.target,
          query: action.query,
          confirm: true
        }, execTimeout(action.intent));
    running.catch(() => {});          // отказ разбирается ниже, здесь только глушим «необработанное»

    const asked = store.push({ id: uid(), role: 'user', text: said, ts: Date.now() });
    welcome.hidden = true;
    messagesBox.appendChild(buildMessage(asked));

    const headline = `Экстренная фраза «${rule.phrase}» — открываю ${action.label || action.title}.`;
    const message = store.push({ id: uid(), role: 'ai', text: headline, ts: Date.now() });
    const node = buildMessage(message);
    node.classList.add('msg--sos');
    messagesBox.appendChild(node);

    const card = buildCommandCard({ title: action.title || 'Экстренное действие',
      sub: 'Выполняю немедленно…', icon: action.icon || 'sos', state: 'run' });
    card.classList.add('cmd--sos');
    node._column.insertBefore(card, node._column.lastChild);
    scrollDown();

    if (SOS.announce && window.Voice) Voice.speak(`Экстренная команда принята. Открываю ${action.title}.`);

    try {
      const result = await running;
      const suffix = result.demo ? 'демо-режим · действие имитировано' : (result.detail || result.method || 'выполнено');
      settleCard(card, true, suffix);
      message.cmd = { title: action.title, sub: suffix, icon: action.icon || 'sos', state: 'done' };
      toast(`Экстренно: ${action.title}`, 'ok');
    } catch (error) {
      const reason = error.message || 'агент не ответил';
      settleCard(card, false, reason);
      message.cmd = { title: action.title, sub: reason, icon: action.icon || 'sos', state: 'error' };
      toast('Экстренная команда не выполнилась: ' + reason, 'err');
      if (window.Voice) Voice.speak('Не удалось выполнить. ' + reason);
    }

    store.save();
    renderHistory();
    renderSosList();
    scrollDown();

    busy = false;
    setComposerEnabled(true);
    if (window.Listener) Listener.busy(false);
  }

  /**
   * Добавляет ответ ИИ (с печатью) и, если нужно, карточку команды.
   * @param {object} [extra] {trace, correction, text}
   */
  async function reply(text, parsed, extra = {}) {
    // canned — ответ приложения, а не модели: в историю для модели он не идёт
    // (llm.js, buildMessages), иначе модель копирует заготовки как образец.
    const message = { id: uid(), role: 'ai', text, ts: Date.now(), canned: true };
    const node = buildMessage(message);
    messagesBox.appendChild(node);
    scrollDown();

    let card = null;
    if (parsed) {
      card = buildCommandCard({ title: parsed.title || 'Действие', sub: 'Передаю команду агенту…', icon: parsed.icon, state: 'run' });
      node._column.insertBefore(card, node._column.lastChild);
    }

    // Голос запускается ВМЕСТЕ с печатью, а не после неё. Иначе выходит
    // странное: текст уже дописан и прочитан глазами, и только тогда помощник
    // начинает читать его вслух. Синтез первой фразы занимает те же доли
    // секунды, что и печать первой строки, — они идут вровень.
    if (window.Voice) Voice.speak(text);
    await typewrite(node._bubble, text);

    if (extra.trace && extra.trace.length) node._column.insertBefore(buildTrace(extra.trace), node._column.lastChild);
    if (extra.correction && extra.text) node._column.insertBefore(buildCorrection(extra.text), node._column.lastChild);

    store.push(message);
    node._message = message;
    if (card) card._message = message;
    return card || node;
  }

  /*
     Мост для умений второй версии (features.js): разбор папок, защита,
     телефон, перевод. Им нужно ровно то, что делают встроенные ответы, —
     показать реплику человека, ответить с печатью и голосом, вложить в ответ
     свою карточку, — и ничего больше. Внутренности чата наружу не уходят.
  */
  window.CloudChat = {
    get busy() { return busy; },
    setBusy(flag) {
      busy = Boolean(flag);
      setComposerEnabled(!busy);
      if (window.Listener) Listener.busy(busy);
    },
    user(text) {
      const message = store.push({ id: uid(), role: 'user', text, ts: Date.now() });
      welcome.hidden = true;
      messagesBox.appendChild(buildMessage(message));
      scrollDown();
      renderHistory();
      $('chatTitle').textContent = store.active.title;
      return message;
    },
    async ai(text, extra) {
      const node = await reply(text, null, {});
      if (extra) {
        node._column.insertBefore(extra, node._column.lastChild);
        scrollDown();
      }
      store.save();
      return node;
    },
    /** Меняет текст уже показанного ответа — для итогов долгих дел. */
    update(node, text) {
      if (!node || !node._bubble) return;
      node._bubble.textContent = text;
      if (node._message) { node._message.text = text; store.save(); }
    },
    send: (text) => send(text),
    lastAi() {
      const list = (store.active && store.active.messages) || [];
      for (let i = list.length - 1; i >= 0; i--) if (list[i].role === 'ai') return list[i].text || '';
      return '';
    },
    toast: (text, kind) => toast(text, kind),
    scroll: () => scrollDown()
  };

  /**
   * Ответ локальной языковой модели: текст льётся в пузырь по мере генерации,
   * а не появляется целиком через десять секунд молчания.
   */
  async function streamFromModel(question, parsed) {
    const message = { id: uid(), role: 'ai', text: '', ts: Date.now() };
    const node = buildMessage(message);
    messagesBox.appendChild(node);

    const bubble = node._bubble;
    bubble.textContent = '';
    const caret = el('span', 'caret');
    bubble.appendChild(caret);
    node.dataset.streaming = 'true';
    scrollDown();

    // Подпись под строкой ввода показывает, что отвечает именно модель и что
    // она сейчас работает. Полоски «печатает…» в чате для этого мало: она
    // выглядит одинаково и когда думает нейросеть, и когда ответ собирает
    // классификатор, а разница для пользователя существенная.
    setModelBusy(true);
    setStopVisible(true);
    let sinceScroll = 0;

    try {
      const history = store.active.messages.slice(0, -1);   // без только что добавленного вопроса
      const result = await LLM.ask(question, history, (chunk) => {
        caret.before(document.createTextNode(chunk));
        sinceScroll += chunk.length;
        if (sinceScroll > 40) { sinceScroll = 0; scrollDown(); }
      });

      caret.remove();
      node.dataset.streaming = 'false';

      const answer = result.text || (result.stopped ? '(остановлено)' : '(модель вернула пустой ответ)');
      // По ходу генерации текст льётся в пузырь как есть — иначе на каждом
      // фрагменте пришлось бы перестраивать разметку, и код мигал бы. А вот
      // готовый ответ уже можно разобрать на текст и блоки кода.
      renderRich(bubble, answer);
      message.text = answer;
      store.push(message);

      // Ответ модели озвучивается ЦЕЛИКОМ и только по её окончании — в отличие
      // от заготовок, которые читаются вместе с печатью. Причина в том, как
      // модель генерирует: кусками по несколько символов, часто обрывая слово
      // посередине. Отдавать такие куски синтезатору по мере поступления
      // значит получить речь по слогам с паузами в неожиданных местах.
      //
      // Код вслух не читается: «меньше div класс равно контейнер больше» — это
      // полминуты бессмыслицы вместо ответа. Вместо него одна фраза о том, что
      // код в чате, — иначе помощник дочитает пояснение и замолчит, как будто
      // на этом всё.
      if (window.Voice && !result.stopped) Voice.speak(speakable(answer));

      const speed = result.ms > 0 ? (result.tokens / (result.ms / 1000)).toFixed(1) : '—';
      node._column.insertBefore(buildTrace([
        `свободный разговор → ${LLM.current().title}`,
        `фрагментов: ${result.tokens}, за ${(result.ms / 1000).toFixed(1)} с (${speed}/с)`,
        result.stopped ? 'генерация остановлена вами' : 'ответ сгенерирован полностью',
        'запрос не покидал компьютер'
      ]), node._column.lastChild);
    } catch (error) {
      caret.remove();
      node.dataset.streaming = 'false';
      const fallback = parsed.reply || 'Не смог получить ответ от локальной модели.';
      bubble.textContent = `${fallback}\n\n⚠ ${error.message}`;
      message.text = bubble.textContent;
      store.push(message);
      toast(error.message, 'err');
      renderLlmStatus();
    } finally {
      setModelBusy(false);
      setStopVisible(false);
      scrollDown();
    }
  }

  function setStopVisible(on) {
    const button = $('stopBtn');
    if (!button) return;
    button.hidden = !on;
    $('sendBtn').hidden = on;
  }

  /** Раскрывающийся блок «как я это понял» — ход рассуждения. */
  function buildTrace(trace) {
    const box = el('details', 'trace');
    const summary = el('summary', null, 'Как я это понял');
    const list = el('ul');
    trace.forEach((step) => list.appendChild(el('li', null, step)));
    box.append(summary, list);
    return box;
  }

  /**
   * Строка исправления: если сеть не уверена, пользователь одним нажатием
   * говорит, чем на самом деле был запрос, — и это становится обучающим примером.
   */
  function buildCorrection(text) {
    const OPTIONS = [
      ['open_app', 'запустить программу'],
      ['scan_files', 'найти файл или игру'],
      ['open_folder', 'открыть папку'],
      ['web_search', 'поискать в браузере'],
      ['math', 'посчитать']
    ];
    const box = el('div', 'teach');
    box.appendChild(el('span', 'teach__label', 'Не угадал? Подскажите — я запомню:'));

    OPTIONS.forEach(([intent, label]) => {
      const button = el('button', 'teach__btn ripple', label);
      button.onclick = () => {
        Brain.learn(text, intent);
        // Сервису поправка тоже нужна, но иначе: сеть в браузере доучивается
        // на месте, а модель на spaCy стоит на 13 тысячах фраз и от одной новой
        // не сдвинется. Поправка копится в файл и попадает в корпус при
        // следующем обучении — там от неё будет толк.
        if (window.SpacyNLU) SpacyNLU.feedback(text, intent);
        box.dataset.done = 'true';
        box.textContent = '';
        box.appendChild(el('span', 'teach__label', `Запомнил: «${text}» → ${label}. Переспросите — теперь пойму.`));
        toast('Нейросеть дообучена на вашем примере', 'ok');
        renderBrainStats();
      };
      box.appendChild(button);
    });
    return box;
  }

  /** Отправляет интент агенту и обновляет карточку. */
  /**
   * Сколько ждать агента. Значения не «с запасом», а по самому долгому пути:
   *   • поиск по дискам укладывается в бюджет 9 с, в глубоком режиме — 25 с;
   *   • запись экрана ждёт, пока Game Bar создаст файл, и ПЕРВЫЙ запуск, когда
   *     GameBar.exe ещё не поднят, занимает до пятнадцати секунд. С прежними
   *     двенадцатью браузер отваливался по таймауту раньше, чем запись
   *     начиналась, — и выглядело это как отказ на ровном месте.
   */
  function execTimeout(intent) {
    if (intent === 'scan_files') return 40000;
    if (intent === 'record_screen' || intent === 'record_stop') return 25000;
    return 12000;
  }

  /**
   * Поиск файла по индексу — до обхода дисков агентом.
   *
   * Порядок именно такой, и он не про скорость ради скорости. Обход диска
   * ограничен бюджетом в девять секунд и успевает заглянуть лишь в часть
   * дерева: он находит не то, что есть, а то, до чего дошёл. Индекс знает про
   * весь диск сразу и отвечает за миллисекунды. Нет индекса или нет сервиса —
   * работает прежний обход, поэтому ничего не ломается.
   *
   * @returns {Promise<object|null>} результат в формате агента либо null
   */
  async function searchByIndex(query) {
    if (!query || !window.SpacyNLU) return null;
    const found = await SpacyNLU.searchFiles(query, 25);
    if (!found || !Array.isArray(found.items) || !found.items.length) return null;

    const LAUNCHABLE = /\.(exe|lnk|bat|cmd|msi|apk)$/i;
    return {
      results: found.items.map((item) => ({
        name: item.name,
        path: item.path,
        size: item.size,
        launchable: !item.is_dir && LAUNCHABLE.test(item.name),
        kind: item.is_dir ? 'folder' : 'file',
        score: item.score
      })),
      scanned: 0,
      elapsed: Math.round(found.ms || 0),
      fromIndex: true
    };
  }

  /**
   * Показывает найденный файл в Проводнике с подсветкой строки.
   *
   * Делается только для уверенного совпадения: открывать окно на каждой догадке
   * навязчиво. Признак уверенности — высокий счёт И заметный отрыв от второго
   * места: если два файла похожи одинаково, выбирать за человека нельзя.
   */
  async function revealBest(items) {
    if (!items.length || Agent.mode === 'offline') return false;
    const best = items[0];
    const second = items[1];
    const confident = (best.score || 0) >= 85 &&
                      (!second || (best.score - (second.score || 0)) >= 3);
    if (!confident) return false;
    try {
      await Agent.exec({ intent: 'reveal_path', target: best.path });
      return true;
    } catch {
      return false;
    }
  }

  async function runCommand(parsed, card, confirmed) {
    if (!card || !card.dataset || card.dataset.state !== 'run') return;
    try {
      // Поиск файла: сперва индекс, и только если он пуст — обход дисков.
      if (parsed.intent === 'scan_files' && parsed.query) {
        const indexed = await searchByIndex(parsed.query);
        if (indexed) {
          settleCard(card, true, `по индексу: ${indexed.results.length} совпадений · ${indexed.elapsed} мс`);
          if (card._message) {
            card._message.cmd = { title: parsed.title, sub: `найдено ${indexed.results.length}`,
              icon: parsed.icon, state: 'done' };
          }
          await showScanResults(card, indexed, parsed);
          const shown = await revealBest(indexed.results);
          if (shown) toast(`Показываю «${indexed.results[0].name}» в Проводнике`, 'ok');
          store.save();
          scrollDown();
          return;
        }
      }
      const result = await Agent.exec({
        intent: parsed.intent,
        target: parsed.target,
        query: parsed.query,
        mode: parsed.mode,
        deep: parsed.deep,
        // via === 'steam' — «запусти в стиме кс 2»: агент ищет только среди игр
        via: parsed.via,
        // level — точная громкость в процентах, amount — шаг вверх или вниз
        level: parsed.level,
        amount: parsed.amount,
        // Закрытие программы подтверждено самой просьбой её закрыть: старые
        // сборки агента ещё ждут здесь флаг и без него отвечают отказом.
        confirm: confirmed || parsed.intent === 'close_app'
      }, execTimeout(parsed.intent));

      const suffix = result.demo ? 'демо-режим · действие имитировано' : (result.detail || result.method || 'выполнено');
      settleCard(card, true, suffix);
      // У громкости заголовок — итог, а не направление: «Громкость 42%»
      // говорит больше, чем «Громкость −».
      let title = parsed.title;
      if (/^volume_/.test(parsed.intent) && typeof result.level === 'number') {
        title = `Громкость ${result.level}%`;
        if (card._title) card._title.textContent = title;
      }
      if (card._message) card._message.cmd = { title, sub: suffix, icon: parsed.icon, state: 'done' };

      if (parsed.intent === 'scan_files') await showScanResults(card, result, parsed);

      store.save();
      toast(result.demo ? 'Демо: ' + (parsed.title || 'команда выполнена') : (parsed.title || 'Команда выполнена'), 'ok');
    } catch (error) {
      const reason = error.message || 'Агент не ответил';

      // Программы нет в белом списке агента — ПРЕДЛАГАЕМ поиск, а не запускаем.
      //
      // Раньше поиск стартовал здесь сам. Задумка была доброй: модель узнаёт
      // любые названия, а белый список короткий, и вместо отказа человек
      // получал список найденного с кнопкой «Запустить».
      //
      // На деле это стало главным источником непрошеных сканирований. Сюда
      // попадает не только «открой фотошоп», но и любая фраза, которую разбор
      // ошибочно принял за просьбу что-то открыть, — а таких хватает. Человек
      // спрашивал одно, а получал полминуты обхода дисков и список файлов.
      //
      // Теперь фраза для поиска кладётся в строку ввода: одно нажатие Enter,
      // если поиск действительно нужен, и ничего не происходит, если нет.
      // Сначала — модель: «открой ютуб» это сайт, «открой что-нибудь
      // посмотреть» — просьба уточнить. Только если и она не знает, что это,
      // предлагаем поиск по дискам. Команду, совпадающую с исходной, не
      // повторяем — иначе круг.
      if (parsed.intent === 'open_app' && error.status === 404 && parsed.target && LLM.available) {
        const routed = await LLM.route(`открой ${parsed.target}`, []);
        const same = (c) => c.toLowerCase().replace(/ё/g, 'е').trim() === `открой ${parsed.target}`.toLowerCase().replace(/ё/g, 'е');
        if (routed && routed.ask) {
          settleCard(card, false, 'уточняю');
          await reply(routed.ask, null);
          context.route = { until: Date.now() + 3 * 60 * 1000,
            history: [{ role: 'user', content: `открой ${parsed.target}` }, { role: 'assistant', content: routed.ask }] };
          return;
        }
        if (routed && routed.commands.length && !routed.commands.every(same)) {
          settleCard(card, false, 'это не программа');
          await reply(routed.say || 'Делаю: ' + routed.commands.join(' → '), null);
          runRouted(routed.commands.filter((c) => !same(c)));
          return;
        }
      }
      if (parsed.intent === 'open_app' && error.status === 404 && parsed.target) {
        settleCard(card, false, 'нет в списке разрешённых');
        await reply(
          `«${parsed.target}» нет в списке разрешённых программ, поэтому запустить напрямую не могу.\n\n` +
          `Могу поискать на дисках — фраза уже в строке ввода, нажмите Enter. ` +
          `Или спросите меня о чём-нибудь другом.`,
          null);
        // Поле берём через $(), а не через переменную input: она объявлена
        // в другой функции и сюда не видна. Событие 'input' рассылаем, чтобы
        // включилась кнопка отправки — её состояние висит на этом слушателе.
        const field = $('input');
        if (field) {
          field.value = `найди файл ${parsed.target}`;
          field.dispatchEvent(new Event('input', { bubbles: true }));
          autoResize();
          field.focus();
          field.setSelectionRange(field.value.length, field.value.length);
        }
        return;
      }

      settleCard(card, false, reason);
      if (card._message) card._message.cmd = { title: parsed.title, sub: reason, icon: parsed.icon, state: 'error' };
      store.save();
      toast(reason, 'err');
      if (error.status === 401) {
        Agent.state.mode = 'offline';
        updateAgentUi();
        resetPermModal();
        $('permModal').dataset.open = 'true';
      }
    }
    scrollDown();
  }

  /* ============================================ 6b. Результаты сканирования */

  const SIZE_UNITS = ['Б', 'КБ', 'МБ', 'ГБ'];
  function humanSize(bytes) {
    if (!bytes) return '';
    let value = Number(bytes), unit = 0;
    while (value >= 1024 && unit < SIZE_UNITS.length - 1) { value /= 1024; unit++; }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${SIZE_UNITS[unit]}`;
  }

  /** Список найденного: запустить или показать в Проводнике. */
  async function showScanResults(card, result, parsed) {
    const items = Array.isArray(result.results) ? result.results : result.results ? [result.results] : [];
    const host = card.parentNode;
    if (!host) return;

    const box = el('div', 'scan');
    const head = el('div', 'scan__head');
    head.append(
      el('b', null, items.length
        ? `Найдено ${items.length} ${Skills.plural(items.length, 'совпадение', 'совпадения', 'совпадений')}`
        : 'Ничего не нашлось'),
      el('span', 'scan__meta', `просмотрено объектов: ${result.scanned || 0} · ${result.elapsed || 0} мс${result.truncated ? ' · поиск остановлен по лимиту времени' : ''}`)
    );
    box.appendChild(head);

    if (!items.length) {
      box.appendChild(el('p', 'scan__empty',
        parsed.query
          ? `«${parsed.query}» не найдено в библиотеках Steam и Epic, в списке установленных программ и на просмотренных дисках. Попробуйте другое написание или скажите «просканируй все диски и найди ${parsed.query}» — тогда я пройду глубже.`
          : 'Попробуйте уточнить, что искать: «найди игру амонг ас».'));
    }

    items.slice(0, 25).forEach((item, index) => {
      const row = el('div', 'scan__row');
      row.style.animationDelay = Math.min(index * 26, 320) + 'ms';

      const info = el('div', 'scan__info');
      info.append(
        el('div', 'scan__name', item.name || '—'),
        el('div', 'scan__path', String(item.path || ''))
      );

      const tags = el('div', 'scan__tags');
      tags.appendChild(el('span', 'tag', item.source || 'Диск'));
      if (item.size) tags.appendChild(el('span', 'tag', humanSize(item.size)));
      info.appendChild(tags);

      const actions = el('div', 'scan__actions');
      if (item.launchable) {
        const run = el('button', 'btn btn--mini ripple', 'Запустить');
        run.onclick = async () => {
          run.disabled = true;
          try {
            await Agent.exec({ intent: 'open_path', target: item.path, confirm: true });
            toast(`Запускаю ${item.name}`, 'ok');
          } catch (error) {
            toast(error.message, 'err');
            run.disabled = false;
          }
        };
        actions.appendChild(run);
      }
      const reveal = el('button', 'btn btn--mini btn--ghost ripple', 'В папке');
      reveal.onclick = async () => {
        try { await Agent.exec({ intent: 'reveal_path', target: item.path }); }
        catch (error) { toast(error.message, 'err'); }
      };
      actions.appendChild(reveal);

      row.append(info, actions);
      box.appendChild(row);
    });

    if (items.length > 25) {
      box.appendChild(el('p', 'scan__empty', `Показаны первые 25 из ${items.length}. Уточните запрос, чтобы сузить список.`));
    }

    host.insertBefore(box, card.nextSibling);
    scrollDown();
  }

  function setComposerEnabled(enabled) {
    const attached = window.Vision ? Vision.count : 0;
    $('input').disabled = !enabled;
    $('sendBtn').disabled = !enabled || (!$('input').value.trim() && !attached);
    $('micBtn').disabled = !enabled;
    const attach = $('attachBtn');
    if (attach) attach.disabled = !enabled;
  }

  /* =================================================== 7. Подтверждение == */

  function askConfirm(title, text) {
    return new Promise((resolve) => {
      const modal = $('confirmModal');
      $('confirmTitle').textContent = title;
      $('confirmText').textContent = text;
      modal.dataset.open = 'true';

      const close = (value) => {
        modal.dataset.open = 'false';
        $('confirmYes').onclick = null;
        $('confirmNo').onclick = null;
        resolve(value);
      };
      $('confirmYes').onclick = () => close(true);
      $('confirmNo').onclick = () => close(false);
      modal.querySelector('.modal__scrim').onclick = () => close(false);
    });
  }

  /* ================================================= 8. Голосовой ввод === */

  /*
     Голосовой ввод по кнопке микрофона.

     Раньше звук уходил в webkitSpeechRecognition — это не движок в браузере,
     а онлайн-сервис, и во встроенном окне WebView2 он не работал вовсе:
     кнопка показывала «Слушаю…» и ничего не распознавала. Теперь запись идёт
     здесь, а распознаёт Whisper на этом компьютере (Hub, /hub/asr). Конец
     фразы — по тишине: после речи 1,2 с тихо, и запись останавливается сама.
  */
  const ASR_URL = 'http://127.0.0.1:4480/hub/asr';

  const voice = {
    overlay: $('voiceOverlay'),
    stream: null,
    audioCtx: null,
    processor: null,
    chunks: [],
    rate: 48000,
    raf: 0,
    bars: [],
    finalText: '',
    active: false,
    heard: false,
    quietSince: 0,
    startedAt: 0
  };

  function buildEq() {
    const box = $('eq');
    box.textContent = '';
    voice.bars = [];
    for (let i = 0; i < 32; i++) {
      const bar = el('i');
      box.appendChild(bar);
      voice.bars.push(bar);
    }
  }

  function micProblem(label, text) {
    $('voiceLabel').textContent = label;
    $('voiceTranscript').textContent = text;
    idleEq();
    setTimeout(() => { if (voice.active) stopVoice(false); }, 3200);
  }

  async function startVoice() {
    if (voice.active) return;
    voice.active = true;
    voice.finalText = '';
    voice.chunks = [];
    voice.heard = false;
    voice.quietSince = 0;
    voice.startedAt = performance.now();
    voice.overlay.dataset.open = 'true';
    $('micBtn').dataset.rec = 'true';
    // Распознавание будим сразу: пока человек говорит, модель успевает подняться.
    fetch(ASR_URL + '/warm', { method: 'POST' }).catch(() => {});
    $('voiceLabel').textContent = 'Слушаю…';
    $('voiceTranscript').textContent = 'Скажите, например: «открой роблокс» или «перемести отчёт в документы»';
    buildEq();

    try {
      voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (error) {
      if (error && error.name === 'NotFoundError') {
        micProblem('Микрофон не подключён', 'Windows не видит ни одного микрофона. Подключите его — и нажмите ещё раз.');
      } else if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
        micProblem('Доступ к микрофону запрещён', 'Разрешите микрофон: Параметры Windows → Конфиденциальность → Микрофон.');
      } else {
        micProblem('Микрофон недоступен', 'Его заняла другая программа или он отключён. Введите запрос текстом.');
      }
      return;
    }
    if (!voice.active) { voice.stream.getTracks().forEach((t) => t.stop()); return; }

    voice.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    voice.rate = voice.audioCtx.sampleRate;
    const source = voice.audioCtx.createMediaStreamSource(voice.stream);
    const analyser = voice.audioCtx.createAnalyser();
    analyser.fftSize = 128;
    analyser.smoothingTimeConstant = 0.72;
    source.connect(analyser);

    // Сырой звук для распознавания. ScriptProcessor устарел, но в Chromium
    // работает везде и не требует отдельного файла для AudioWorklet.
    const processor = voice.audioCtx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => {
      if (!voice.active) return;
      const data = event.inputBuffer.getChannelData(0);
      voice.chunks.push(new Float32Array(data));
      let sum = 0;
      for (let i = 0; i < data.length; i += 4) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / (data.length / 4));
      const now = performance.now();
      if (rms > 0.02) { voice.heard = true; voice.quietSince = 0; }
      else if (voice.heard && !voice.quietSince) voice.quietSince = now;
      if (voice.heard && voice.quietSince && now - voice.quietSince > 1200) stopVoice(true);
      else if (now - voice.startedAt > 15000) stopVoice(true);
      else if (!voice.heard && now - voice.startedAt > 7000) {
        $('voiceLabel').textContent = 'Ничего не слышно';
        $('voiceTranscript').textContent = 'Говорите ближе к микрофону — или проверьте, тот ли выбран в Windows.';
        stopVoice(false);
      }
    };
    source.connect(processor);
    processor.connect(voice.audioCtx.destination);
    voice.processor = processor;

    const data = new Uint8Array(analyser.frequencyBinCount);
    const draw = () => {
      analyser.getByteFrequencyData(data);
      for (let i = 0; i < voice.bars.length; i++) {
        const value = data[Math.floor((i / voice.bars.length) * data.length * 0.7)] / 255;
        voice.bars[i].style.height = (6 + Math.pow(value, 0.85) * 50).toFixed(1) + 'px';
        voice.bars[i].style.opacity = (0.5 + value * 0.5).toFixed(2);
      }
      voice.raf = requestAnimationFrame(draw);
    };
    draw();
  }

  /** Склейка, понижение до 16 кГц и упаковка в base64 (int16) для Whisper. */
  function packAudio(chunks, rate) {
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const joined = new Float32Array(total);
    let at = 0;
    chunks.forEach((c) => { joined.set(c, at); at += c.length; });
    const ratio = rate / 16000;
    const out = new Int16Array(Math.floor(total / ratio));
    for (let i = 0; i < out.length; i++) {
      const v = Math.max(-1, Math.min(1, joined[Math.floor(i * ratio)]));
      out[i] = v * 32767;
    }
    const bytes = new Uint8Array(out.buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  /* ------------------------------------------- постоянное прослушивание -- */
  /*
   * Ключевое слово больше не нужно: микрофон слушает всегда, а решение
   * «сказали мне или говорят рядом» принимает модуль Listener (js/voice.js) по
   * трём признакам сразу — громкости относительно тишины в комнате, форме
   * команды и словарю установленного на этом компьютере.
   *
   * Здесь остаётся только интерфейс: индикатор, отчёт о том, что услышано, и
   * передача принятой команды в общий поток send().
   */

  let hearTimer = 0;

  function setListenUi(kind, data) {
    const button = $('earBtn');
    const strip = $('hearStrip');
    const label = $('hearText');
    const runBtn = $('hearRun');
    if (!button || !strip) return;

    const on = Listener.on;
    button.dataset.rec = on ? 'true' : 'false';
    button.setAttribute('aria-pressed', on ? 'true' : 'false');

    /*
      Отказ показывается тостом, а не строкой, и это исправление живой ошибки.

      Строка состояния живёт, только пока режим включён. А отказ приходит ровно
      в тот момент, когда режим выключается: «микрофона нет» → строка есть
      миллисекунду → выключение → строка спрятана. Снаружи это выглядело так,
      будто кнопка не нажимается: ни включения, ни объяснения. Тост переживает
      выключение и договаривает, почему не вышло.
    */
    if (kind === 'denied' || kind === 'unsupported') {
      toast(data && data.why ? data.why : 'Микрофон недоступен', 'err');
    }

    strip.hidden = !on;
    if (!on) { strip.dataset.state = 'off'; return; }

    const show = (state, text, offer) => {
      strip.dataset.state = state;
      label.textContent = text;
      runBtn.hidden = !offer;
      if (offer) runBtn.dataset.text = offer;

      // Всё, кроме ровного «слушаю», держится на экране несколько секунд:
      // строка должна успеть быть прочитанной, но не залипать навсегда.
      clearTimeout(hearTimer);
      if (state !== 'idle') {
        hearTimer = setTimeout(() => {
          if (!Listener.on) return;
          strip.dataset.state = 'idle';
          label.textContent = 'Слушаю';
          runBtn.hidden = true;
        }, offer ? 9000 : 3200);
      }
    };

    const heard = data && data.heard ? `«${data.heard}»` : '';

    switch (kind) {
      case 'starting':  show('idle', 'Включаю микрофон…'); break;
      case 'listening': show('idle', 'Слушаю'); break;
      case 'hearing':   show('hearing', heard || 'Слышу речь…'); break;
      case 'waiting':   show('hearing', `${heard} — договаривайте`); break;
      case 'accepted':
        // Экстренная фраза отмечается отдельно, и не ради красоты: она
        // выполняется мимо всех проверок, и человек должен видеть, что
        // сработала именно она, а не обычный разбор.
        if (data && data.emergency) show('sos', `Экстренно: ${heard}`);
        else show('ok', `Команда: ${heard}`);
        break;
      case 'queued':    show('ok', `Приму следом: ${heard}`); break;
      case 'hint':      show('hint', `${heard} — не уверен, что это мне`, data.text); break;
      case 'ignored':   show('skip', `${heard} — мимо: ${data.why || 'не команда'}`); break;
      case 'nolevel':   show('hint', 'Слушаю. Уровень микрофона не измеряется — сужу только по словам'); break;
      case 'denied':    show('skip', data.why || 'Микрофон недоступен'); break;
      case 'unsupported': show('skip', data.why || 'Распознавание речи недоступно'); break;
      default:          show('idle', 'Слушаю');
    }
  }

  function initListener() {
    if (!window.Listener) return;

    Listener.onState((event) => setListenUi(event.kind, event));

    Listener.onCommand((text) => {
      if (!text) return;
      send(text);
    });

    const run = $('hearRun');
    if (run) {
      run.onclick = () => {
        const text = run.dataset.text;
        run.hidden = true;
        if (text) send(text);
      };
    }
    const off = $('hearOff');
    if (off) off.onclick = () => Listener.stop();

    // Режим переживает перезагрузку страницы. Восстановление отложено на два
    // такта: запрос микрофона поверх заставки выглядит как приставание, да и
    // разрешение браузер отдаёт охотнее после первого касания страницы.
    if (localStorage.getItem('cloudhdr.listen') === 'on') {
      const arm = () => { if (!Listener.on) Listener.start(); };
      addEventListener('pointerdown', arm, { once: true });
      addEventListener('keydown', arm, { once: true });
    }
  }

  async function toggleListening() {
    if (!Listener.supported) {
      toast('Распознавание речи есть только в Chrome и Edge', 'err');
      return;
    }
    if (Listener.on) {
      Listener.stop();
      toast('Постоянное прослушивание выключено', 'info');
      return;
    }
    if (voice.active) stopVoice(true);
    await Listener.start();

    // Пауза перед похвальным сообщением не для красоты. Распознавание падает
    // не при запуске, а через доли секунды после него — например, когда в
    // системе вовсе нет микрофона. Без этой паузы человек получал сразу два
    // сообщения подряд: «слушаю» и «микрофон не найден».
    await wait(500);
    if (!Listener.on) return;

    const known = Listener.vocabularySize;
    toast(known
      ? `Слушаю. Просто скажите: «открой хром». Знаю ${known} программ и игр на этом ПК`
      : 'Слушаю. Просто скажите: «открой хром» — ключевое слово не нужно', 'ok');
  }

  function idleEq() {
    const draw = () => {
      const now = performance.now() / 260;
      voice.bars.forEach((bar, i) => {
        const height = 6 + (Math.sin(now + i * 0.45) * 0.5 + 0.5) * 22;
        bar.style.height = height.toFixed(1) + 'px';
      });
      voice.raf = requestAnimationFrame(draw);
    };
    draw();
  }

  async function stopVoice(autoSend) {
    if (!voice.active) return;
    voice.active = false;
    cancelAnimationFrame(voice.raf);
    $('micBtn').dataset.rec = 'false';

    if (voice.processor) { voice.processor.onaudioprocess = null; try { voice.processor.disconnect(); } catch {} voice.processor = null; }
    if (voice.stream) { voice.stream.getTracks().forEach((track) => track.stop()); voice.stream = null; }
    if (voice.audioCtx) { voice.audioCtx.close().catch(() => {}); voice.audioCtx = null; }

    const chunks = voice.chunks;
    voice.chunks = [];
    if (!autoSend || !voice.heard || !chunks.length) {
      setTimeout(() => { if (!voice.active) voice.overlay.dataset.open = 'false'; }, autoSend ? 0 : 1600);
      return;
    }

    $('voiceLabel').textContent = 'Распознаю…';
    idleEq();
    let answer = null;
    try {
      const response = await fetch(ASR_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pcm: packAudio(chunks, voice.rate), rate: 16000 })
      });
      answer = await response.json();
    } catch { answer = null; }
    cancelAnimationFrame(voice.raf);

    // Обращение по имени в кнопке не нужно, но если сказали «Клауд, …» —
    // имя срезаем, в чат уходит сама просьба.
    if (!answer || !answer.ok) {
      $('voiceLabel').textContent = 'Распознавание недоступно';
      $('voiceTranscript').textContent = (answer && answer.error) || 'Служба умений не отвечает. Введите запрос текстом.';
      setTimeout(() => { voice.overlay.dataset.open = 'false'; }, 2600);
      return;
    }
    const text = (answer.wake && answer.command ? answer.command : answer.text || '').trim();
    if (!text) {
      $('voiceLabel').textContent = 'Не расслышал';
      $('voiceTranscript').textContent = 'Повторите чуть громче или ближе к микрофону.';
      setTimeout(() => { voice.overlay.dataset.open = 'false'; }, 1800);
      return;
    }
    voice.finalText = text;
    $('voiceLabel').textContent = 'Распознано';
    $('voiceTranscript').textContent = text;
    setTimeout(() => {
      voice.overlay.dataset.open = 'false';
      send(text);
    }, 450);
  }

  /* ============================================ 9. Разрешение и агент ==== */

  function updateAgentUi() {
    const status = $('agentStatus');
    const pill = $('permPill');
    const map = {
      live: ['online', 'Агент подключён · управление разрешено', 'Доступ разрешён', 'true'],
      demo: ['demo', 'Демо-режим · команды имитируются', 'Демо-режим', 'demo'],
      offline: ['offline', 'Агент не подключён', 'Нет доступа', 'false']
    };
    const [state, statusText, pillText, on] = map[Agent.mode] || map.offline;
    status.dataset.state = state;
    status.querySelector('.status__text').textContent = statusText;
    pill.dataset.on = on;
    pill.lastChild.textContent = ' ' + pillText;
  }

  /** Возвращает окно разрешений в исходное состояние (после отзыва доступа). */
  function resetPermModal() {
    $('permState').hidden = true;
    $('permFail').hidden = true;
    document.querySelector('.perm__actions').style.opacity = '1';
  }

  /* Порядок запуска: заставка → слайд продуктов → окно разрешений → чат. */
  let introVisible = true;
  let permissionPending = false;
  let splashDone = false;

  /** Заставка Cloud HDR. Пропускается кликом; сама уходит по окончании анимации. */
  function runSplash() {
    const splash = $('splash');
    if (!splash) { splashDone = true; showIntro(); return; }

    const finish = () => {
      if (splashDone) return;
      splashDone = true;
      splash.dataset.open = 'false';
      splash.addEventListener('transitionend', () => splash.remove(), { once: true });
      setTimeout(() => splash.remove(), 1200);
      showIntro();
    };

    splash.addEventListener('click', finish);
    addEventListener('keydown', function skip(event) {
      if (event.key === 'Escape' || event.key === 'Enter' || event.key === ' ') { removeEventListener('keydown', skip); finish(); }
    });
    // 2,4 с вместо прежних 3,6: заставку видят при каждом запуске, и лишняя
    // секунда ожидания у собственного приложения ощущается как тормоза.
    // 1,9 с: новая заставка — одна сцена без линий и полосок, дольше её
    // держать незачем.
    setTimeout(finish, reduced() ? 400 : 1900);
  }

  /*
     Слайд «Официальные продукты» — раз в день, а не на каждом запуске.
     Приложение открывают по многу раз за день, и одно и то же окно, которое
     нужно закрыть, прежде чем начать, быстро превращается в препятствие.
  */
  function showIntro() {
    const today = new Date().toDateString();
    let seen = null;
    try { seen = localStorage.getItem('cloudhdr.introDay'); } catch { /* приватный режим */ }
    if (seen === today) { closeIntro(); return; }
    try { localStorage.setItem('cloudhdr.introDay', today); } catch { /* не страшно */ }
    $('introSlide').dataset.open = 'true';
  }

  function maybeShowPermission() {
    if (!splashDone || introVisible || !permissionPending) return;
    resetPermModal();
    $('permModal').dataset.open = 'true';
  }

  let pendingVoice = false;

  function closeIntro() {
    introVisible = false;
    $('introSlide').dataset.open = 'false';
    maybeShowPermission();
    // голосовой ярлык срабатывает только после всей заставки, иначе оверлей
    // микрофона окажется под ней
    if (pendingVoice) {
      pendingVoice = false;
      setTimeout(() => { if (!voice.active) startVoice(); }, 500);
    }
  }

  async function bootPermission() {
    const modal = $('permModal');
    const info = await Agent.probe();

    if (info && info.requirePin) $('pinRow').hidden = false;

    // токен уже выдан в этой вкладке — проверяем, жив ли он
    if (info && Agent.state.token) {
      Agent.state.mode = 'live';
      const check = await Agent.apps();
      if (check) {
        loadVocabulary(check);
        modal.dataset.open = 'false';
        updateAgentUi();
        toast('Разрешение действует — Cloud HDR AI готов', 'ok');
        // без await: блок появится сам, когда данные придут. И без выброса
        // наружу — иначе сбой украшения всплывает как необработанный отказ
        // промиса и попадает в showFatal, хотя приложение полностью работает.
        renderHabits().catch((error) => console.warn('[Cloud HDR] привычки:', error));
        return;
      }
      Agent.state.mode = 'offline';
      Agent.state.token = null;
      sessionStorage.removeItem('cloudhdr.token');
    }
    updateAgentUi();
    permissionPending = true;
    maybeShowPermission();
  }

  /**
   * Отдаёт прослушиванию словарь ЭТОГО компьютера: имена установленных
   * программ и игр библиотеки Steam.
   *
   * Это главный признак, по которому «открой брав старс» отличается от обрывка
   * чужого разговора: дело не в том, что фраза складная, а в том, что такая
   * игра здесь стоит. Без словаря режим работает, но осторожничает.
   */
  /**
   * Блок «Вы обычно здесь» на приветственном экране.
   *
   * Данные считает агент: своё измеренное время в программах плюс счётчики
   * запусков, которые Windows ведёт сама, плюс история браузеров. Здесь только
   * показ — и молчание, если показывать нечего.
   *
   * Строки кликабельны и работают через тот же data-prompt, что и карточки
   * выше: нажатие превращается в обычную команду «открой …» и проходит весь
   * путь разбора, а не запускает что-то в обход него.
   */
  async function renderHabits() {
    const box = $('habits');
    if (!box) return;

    const data = await Agent.usage();
    if (!data) return;

    // Проверка именно на массив, а не «|| []».
    //
    // Агент на PowerShell умел прислать "sites":{} вместо [] — пустой объект
    // истинный, и «|| []» его пропускал, а .slice у объекта нет. Одна строка
    // приветственного экрана валила весь init и вешала поверх страницы красную
    // плашку. Агент починен, но привычки — украшение, а не смысл приложения:
    // что бы ни пришло, дальше идёт либо список, либо пусто.
    const list = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);

    // По четыре строки в столбце — ровно столько помещается на приветственном
    // экране вместе с карточками, не заставляя прокручивать.
    const apps = list(data.apps).filter((item) => item.minutes > 0 || item.launches > 0).slice(0, 4);
    const sites = list(data.sites).slice(0, 4);
    if (!apps.length && !sites.length) return;

    const row = ({ name, value, prompt, share, index, fun }) => {
      const button = document.createElement('button');
      button.className = 'habit' + (fun ? ' habit--fun' : '');
      button.style.setProperty('--i', String(index));
      button.dataset.prompt = prompt;
      button.title = prompt;

      const title = document.createElement('span');
      title.className = 'habit__name';
      title.textContent = name;                 // имена приходят из истории браузера — только текстом

      const amount = document.createElement('span');
      amount.className = 'habit__value';
      amount.textContent = value;

      const bar = document.createElement('i');
      bar.className = 'habit__bar';
      const fill = document.createElement('i');
      fill.style.width = Math.max(6, Math.round(share * 100)) + '%';
      bar.appendChild(fill);

      button.append(title, amount, bar);
      return button;
    };

    const appsBox = $('habitApps');
    const sitesBox = $('habitSites');
    if (!appsBox || !sitesBox) return;
    appsBox.textContent = '';
    sitesBox.textContent = '';

    const topApp = Math.max(1, ...apps.map((item) => item.score || 0));
    apps.forEach((item, index) => {
      const minutes = item.minutes || 0;
      const value = minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`
        : minutes > 0 ? `${minutes} мин`
          : `${item.launches} запусков`;
      appsBox.appendChild(row({
        name: item.name, value, prompt: `открой ${item.name}`,
        share: (item.score || 0) / topApp, index, fun: item.category === 'fun'
      }));
    });

    const topSite = Math.max(1, ...sites.map((item) => item.visits || 0));
    sites.forEach((item, index) => {
      sitesBox.appendChild(row({
        name: item.title || item.domain, value: `${item.visits} заходов`,
        prompt: `открой ${item.domain}`, share: (item.visits || 0) / topSite,
        index: index + apps.length
      }));
    });

    appsBox.parentElement.hidden = !apps.length;
    sitesBox.parentElement.hidden = !sites.length;
    box.hidden = false;
    welcome.dataset.habits = 'on';       // освобождает ряд под четвёртой карточкой
  }

  function loadVocabulary(catalogue) {
    if (!window.Listener || !catalogue) return;
    // Только массив считается списком: пустой список агент на PowerShell мог
    // прислать объектом {}, а «|| []» такую подмену пропускает — см. renderHabits.
    const list = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);
    const names = list(catalogue.names).concat(
      list(catalogue.apps).filter((app) => app.installed).map((app) => app.title)
    );
    const known = Listener.setVocabulary({ names, games: list(catalogue.games) });
    if (known) console.log(`[Cloud HDR] словарь компьютера: ${known} названий`);
  }

  async function requestPermission() {
    const modal = $('permModal');
    $('permState').hidden = false;
    $('permFail').hidden = true;
    document.querySelector('.perm__actions').style.opacity = '.35';

    const info = await Agent.probe();
    if (!info) {
      $('permState').hidden = true;
      $('permFail').hidden = false;
      document.querySelector('.perm__actions').style.opacity = '1';
      return;
    }
    try {
      await Agent.grant($('pinInput').value.trim());
      await wait(650); // даём проиграть анимации подключения
      modal.dataset.open = 'false';
      updateAgentUi();
      toast('Доступ к управлению Windows разрешён', 'ok');

      renderHabits().catch((error) => console.warn('[Cloud HDR] привычки:', error));
      const catalogue = await Agent.apps();
      if (catalogue) {
        loadVocabulary(catalogue);
        const installed = (Array.isArray(catalogue.apps) ? catalogue.apps : [])
          .filter((app) => app && app.installed).map((app) => app.title);
        if (installed.length) {
          setTimeout(() => toast(`Найдено программ: ${installed.length} (${installed.slice(0, 3).join(', ')}…)`, 'info'), 900);
        }
      }
    } catch (error) {
      $('permState').hidden = true;
      document.querySelector('.perm__actions').style.opacity = '1';
      toast(error.message, 'err');
      if (/код/i.test(error.message)) $('pinInput').focus();
    }
  }

  function enableDemoMode() {
    Agent.enableDemo();
    $('permModal').dataset.open = 'false';
    updateAgentUi();
    toast('Демо-режим: команды разбираются, но не выполняются', 'info');
  }

  /* ============================================ 9b. Токены и настройки ==== */

  function renderTokens() {
    const status = Tokens.status();
    const pill = $('tokenPill');
    if (!pill) return;

    const percent = status.percent;
    pill.dataset.state = status.blocked ? 'blocked' : percent >= 85 ? 'low' : 'ok';
    pill.querySelector('.pill__value').textContent = status.blocked ? '0%' : `${percent}%`;
    pill.title = status.blocked
      ? `Запас израсходован. Обновится через ${Tokens.humanize(status.windowLeftMs)}.`
      : `Израсходовано ${percent}% часового запаса — осталось ${status.requestsLeft} ` +
        `${Skills.plural(status.requestsLeft, 'запрос', 'запроса', 'запросов')}. ` +
        `Обновление через ${Tokens.humanize(status.windowLeftMs)}.`;

    const bar = $('tokenBar');
    if (bar) bar.style.width = percent + '%';

    // та же полоса в панели настроек
    const box = $('setBar') && $('setBar').closest('.budget');
    if (box) {
      box.dataset.state = pill.dataset.state;
      $('setBar').style.width = percent + '%';
      $('setPercent').textContent = percent + '%';
      $('setUsed').textContent = `${status.used} из ${status.limit} токенов`;
      $('setWindowLeft').textContent = status.windowStart
        ? `обновление через ${Tokens.humanize(status.windowLeftMs)}`
        : 'запас полный';
      $('setTotal').textContent = `за сутки: ${status.dayRequests} ` +
        `${Skills.plural(status.dayRequests, 'запрос', 'запроса', 'запросов')}`;

      const tamper = $('setTamper');
      if (tamper) {
        tamper.hidden = !status.tampered;
        tamper.textContent = status.tampered
          ? 'Счётчик расхода правили извне. Запас считается израсходованным до конца часа.'
          : '';
      }
    }

    renderTokenFacts(status);
  }

  /**
   * Сводка по бюджету плитками.
   *
   * Полоса расхода показывает «сколько потрачено», но не отвечает на вопросы,
   * которые возникают следом: сколько ещё можно спросить, когда всё вернётся,
   * во что обходится один запрос. Раньше это приходилось выводить из процентов
   * в уме.
   */
  function renderTokenFacts(status) {
    const box = $('tokenFacts');
    if (!box) return;
    box.textContent = '';

    const facts = [
      ['Осталось', status.blocked ? 'исчерпано'
        : `${status.requestsLeft} ${Skills.plural(status.requestsLeft, 'запрос', 'запроса', 'запросов')}`],
      ['Потрачено', `${status.used} из ${status.limit}`],
      ['Один запрос', `${status.perRequest} ${Skills.plural(status.perRequest, 'токен', 'токена', 'токенов')}`],
      ['Обновление', status.windowStart ? Tokens.humanize(status.windowLeftMs) : 'запас полный'],
      ['За сутки', `${status.dayRequests} ${Skills.plural(status.dayRequests, 'запрос', 'запроса', 'запросов')}`],
      ['Считается', 'на этом ПК']
    ];

    facts.forEach(([label, value]) => {
      const card = el('div', 'fact');
      card.append(el('span', 'fact__k', label), el('span', 'fact__v', String(value)));
      box.appendChild(card);
    });
  }

  function budgetSummary() {
    const status = Tokens.status();
    if (status.blocked) {
      return `Токены на этот час закончились. Запас обновится через ${Tokens.humanize(status.windowLeftMs)}.`;
    }
    return `Израсходовано ${status.percent}% часового запаса — это ещё ${status.requestsLeft} ` +
           `${Skills.plural(status.requestsLeft, 'запрос', 'запроса', 'запросов')}. ` +
           `Обновление через ${Tokens.humanize(status.windowLeftMs)}.`;
  }

  /** Запрос не принят: показываем это сообщением в чате, а не молчаливым отказом. */
  function showBudgetBlock(bill) {
    const status = bill.status;
    const text = status.tampered
      ? 'Счётчик расхода правили извне, поэтому запас считается израсходованным.\n' +
        `Он обновится через ${Tokens.humanize(status.windowLeftMs)} — как обычно, по часам.`
      : `Израсходованы все ${status.limit} ${Skills.plural(status.limit, 'токен', 'токена', 'токенов')} этого часа.\n` +
        `Запас обновится через ${Tokens.humanize(status.windowLeftMs)}.`;

    const message = store.push({ id: uid(), role: 'ai', text, ts: Date.now() });
    welcome.hidden = true;
    const node = buildMessage(message);
    node.classList.add('msg--limit');
    messagesBox.appendChild(node);
    scrollDown();
    renderHistory();
    renderTokens();
    toast('Токены закончились — запас обновится в начале следующего часа', 'err');
  }

  /** Индикатор обучения в шапке — виден только при первом запуске. */
  function showTrainingProgress(step) {
    const pill = $('brainPill');
    if (!pill) return;
    pill.hidden = false;
    pill.querySelector('.pill__value').textContent = `обучаюсь ${step.percent}%`;
    pill.title = `Эпоха ${step.epoch} из ${step.maxEpochs}. ` +
                 `Лучшая точность на проверке: ${Math.round(Math.max(0, step.valAccuracy) * 100)}%. ` +
                 'До конца обучения запросы разбираются правилами.';
    const button = $('brainRetrain');
    if (button && button.disabled) button.textContent = `Обучаю… ${step.percent}%`;
  }

  function hideTrainingProgress() {
    const pill = $('brainPill');
    if (pill) pill.hidden = true;
  }

  function renderBrainStats() {
    const box = $('brainStats');
    if (!box) return;
    const meta = Brain.meta;
    box.textContent = '';
    [
      ['Архитектура', Brain.shape],
      ['Скрытых нейронов', `${Brain.hidden} (tanh, dropout)`],
      ['Всего весов', Brain.params.toLocaleString('ru-RU')],
      ['Обучающих примеров', `${meta.samples} (встроенных ${Brain.corpusSize}, ваших ${Brain.userSamples.length})`],
      ['Обучение / проверка', `${meta.trainSize || 0} / ${meta.valSize || 0} фраз`],
      ['Эпох пройдено', `${meta.ranEpochs || 0}, веса усреднены по последним ${meta.averagedOver || 0}`],
      ['Точность на обучении', `${Math.round(meta.accuracy * 100)}%`],
      ['Точность на проверке', `${Math.round((meta.valAccuracy || 0) * 100)}%`],
      ['Ошибка обучения / проверки', `${(meta.loss || 0).toFixed(3)} / ${(meta.valLoss || 0).toFixed(3)}`],
      ['Температура калибровки', (meta.temperature || 1).toFixed(2)],
      ['Слабые классы', meta.weakest && meta.weakest.length ? meta.weakest.join(', ') : 'нет — все распознаны'],
      ['Время обучения', `${meta.ms} мс`],
      ['Обучена', meta.at ? new Date(meta.at).toLocaleString('ru-RU') : '—']
    ].forEach(([label, value]) => {
      const row = el('div', 'stat');
      row.append(el('span', 'stat__k', label), el('span', 'stat__v', value));
      box.appendChild(row);
    });
  }

  /* --------------------------------------------- постоянное прослушивание */

  const LISTEN_NOTES = {
    strict: 'Выполняются только команды с известной целью — программой из этого компьютера, папкой или сайтом. ' +
            'Незнакомое название останется предложением, а не действием. Режим для комнаты, где всегда кто-то говорит.',
    normal: 'Обычное равновесие: команда с любой внятной целью выполняется, вопросы и разговор — нет. ' +
            'Подходит почти всем.',
    open:   'Слышит с более тихого голоса и с большего расстояния — команду не нужно говорить в упор. ' +
            'Требования к самой фразе те же, что в обычном; смысл имеет в тихой комнате, ' +
            'при работающем телевизоре начнёт ловить лишнее.'
  };

  function renderListenSettings() {
    if (!window.Listener) return;

    const box = $('listenStats');
    if (box) {
      box.textContent = '';
      const level = Listener.level();
      const rows = [
        ['Состояние', Listener.supported ? (Listener.on ? 'слушает' : 'выключено') : 'браузер не умеет распознавать речь'],
        ['Чувствительность', Listener.profileLabel],
        ['Словарь компьютера', Listener.vocabularySize
          ? `${Listener.vocabularySize} названий программ и игр`
          : 'пуст — разрешите управление, чтобы агент прислал список']
      ];
      if (Listener.on) {
        rows.push(['Уровень микрофона', level.ready
          ? `тишина ${level.floor.toFixed(3)}, последняя фраза ${level.peak.toFixed(3)}`
          : 'не измеряется — сужу только по словам']);
      }
      rows.forEach(([label, value]) => {
        const row = el('div', 'stat');
        row.append(el('span', 'stat__k', label), el('span', 'stat__v', String(value)));
        box.appendChild(row);
      });
    }

    document.querySelectorAll('#listenProfile .seg__btn').forEach((button) => {
      const active = button.dataset.profile === Listener.profile;
      button.dataset.on = String(active);
      button.setAttribute('aria-checked', String(active));
    });
    const note = $('listenProfileNote');
    if (note) note.textContent = LISTEN_NOTES[Listener.profile] || '';

    const toggle = $('listenToggle');
    if (toggle) {
      toggle.textContent = Listener.on ? 'Выключить' : 'Включить';
      toggle.disabled = !Listener.supported;
    }
  }

  function bindListenSettings() {
    if (!window.Listener) return;

    document.querySelectorAll('#listenProfile .seg__btn').forEach((button) => {
      button.onclick = () => {
        Listener.setProfile(button.dataset.profile);
        renderListenSettings();
        toast(`Чувствительность: ${Listener.profileLabel}`, 'info');
      };
    });

    const toggle = $('listenToggle');
    if (toggle) toggle.onclick = async () => { await toggleListening(); renderListenSettings(); };

    // Промежуточные «слышу речь» приходят по нескольку раз в секунду, и
    // перерисовывать по ним панель — значит мигать таблицей всё время, пока
    // рядом кто-то говорит. Панель показывает состояние, а не поток.
    Listener.onState((event) => {
      if (event.kind === 'hearing') return;
      if ($('settingsModal').dataset.open === 'true') renderListenSettings();
    });
    renderListenSettings();
  }

  /* ------------------------------------------------- экстренная помощь --- */

  function renderSosList() {
    const box = $('sosList');
    if (!box || !window.SOS) return;
    box.textContent = '';

    const rules = SOS.rules;
    if (!rules.length) {
      box.appendChild(el('p', 'sos__empty',
        'Пока ни одной фразы. Добавьте свою — например, «код красный» → «телеграм».'));
      return;
    }

    rules.forEach((rule, index) => {
      const row = el('div', 'sos__row');
      row.style.animationDelay = Math.min(index * 32, 240) + 'ms';

      const body = el('div', 'sos__body');
      const phrase = el('div', 'sos__phrase');
      phrase.append(el('b', null, `«${rule.phrase}»`), el('span', 'sos__arrow', '→'),
        el('span', 'sos__target', rule.action.label || rule.action.title));
      body.appendChild(phrase);

      body.appendChild(el('div', 'sos__meta', rule.hits
        ? `сработала ${rule.hits} ${Skills.plural(rule.hits, 'раз', 'раза', 'раз')} · последний раз ${new Date(rule.lastAt).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`
        : 'ещё ни разу не срабатывала'));

      const test = el('button', 'btn btn--mini btn--ghost ripple', 'Проверить');
      test.title = 'Выполнить прямо сейчас — так же, как по фразе';
      test.onclick = () => {
        $('settingsModal').dataset.open = 'false';
        send(rule.phrase);
      };

      const del = el('button', 'icon-btn danger');
      del.title = 'Удалить фразу';
      del.appendChild(icon('trash'));
      del.onclick = () => {
        row.classList.add('sos__row--leaving');
        setTimeout(() => { SOS.remove(rule.id); renderSosList(); syncSosToListener(); }, 240);
      };

      row.append(body, test, del);
      box.appendChild(row);
    });
  }

  /**
   * Отдаёт прослушиванию актуальный список экстренных фраз.
   *
   * Гейт микрофона спрашивает SOS напрямую, поэтому передавать список ему не
   * нужно — но лог в консоли помогает понять, что фраза действительно
   * подхвачена, если она вдруг не срабатывает.
   */
  function syncSosToListener() {
    if (!window.SOS || !window.Listener) return;
    const count = SOS.phrases.length;
    console.log(`[Cloud HDR] экстренных фраз: ${count}`);
  }

  function bindSosSettings() {
    if (!window.SOS) return;

    const phraseField = $('sosPhrase');
    const targetField = $('sosTarget');
    const hint = $('sosHint');

    const add = () => {
      const result = SOS.put(phraseField.value, targetField.value);
      if (!result.ok) {
        hint.textContent = result.error;
        hint.dataset.state = 'err';
        return;
      }
      hint.textContent = `Готово: «${result.rule.phrase}» открывает ${result.rule.action.label || result.rule.action.title}.`;
      hint.dataset.state = 'ok';
      phraseField.value = '';
      targetField.value = '';
      renderSosList();
      syncSosToListener();
      toast(`Экстренная фраза «${result.rule.phrase}» настроена`, 'ok');
    };

    $('sosAdd').onclick = add;
    [phraseField, targetField].forEach((field) => {
      field.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); add(); }
      });
      // Подсказка о разборе цели показывается ДО добавления: иначе человек
      // узнаёт, что «фотошоп» не опознан, только создав правило.
      field.addEventListener('input', () => {
        hint.dataset.state = '';
        if (field !== targetField || !targetField.value.trim()) {
          hint.textContent = 'Что открыть — обычными словами: «телеграм», «папку документы», «D:\\важное», «https://112.ru».';
          return;
        }
        const action = SOS.resolve(targetField.value);
        hint.textContent = action
          ? `Открою ${action.label || action.title}.`
          : 'Пока не понял, что открывать.';
      });
    });

    const announce = $('sosAnnounce');
    announce.checked = SOS.announce;
    announce.onchange = () => SOS.setAnnounce(announce.checked);

    renderSosList();
    syncSosToListener();
  }

  /* ------------------------------------------------------------- голос --- */

  function renderVoiceStatus() {
    const box = $('voiceStatus');
    if (!box || !window.Voice) return;
    box.textContent = '';

    const ENGINES = {
      agent: 'голос Windows через агента — локально',
      browser: 'запасной голос браузера',
      none: 'синтез речи недоступен',
      unknown: 'проверяю…'
    };
    const rows = [
      ['Состояние', Voice.enabled ? (Voice.speaking ? 'говорит' : 'включён') : 'выключен'],
      ['Источник', ENGINES[Voice.engine] || Voice.engine],
      ['Доступно голосов', Voice.voices.length || '—']
    ];
    if (Voice.lastError && Voice.engine !== 'agent') rows.push(['Замечание', Voice.lastError]);

    rows.forEach(([label, value]) => {
      const row = el('div', 'stat');
      row.append(el('span', 'stat__k', label), el('span', 'stat__v', String(value)));
      box.appendChild(row);
    });
  }

  function renderVoiceControls() {
    if (!window.Voice) return;
    const settings = Voice.settings;

    const select = $('voiceName');
    if (select) {
      select.textContent = '';
      const auto = el('option', null, 'Автоматически — лучший мужской русский');
      auto.value = '';
      select.appendChild(auto);
      Voice.voices.forEach((voice) => {
        const option = el('option', null,
          `${voice.name}${voice.language ? ' · ' + voice.language : ''}${voice.gender ? ' · ' + (voice.gender === 'Male' ? 'мужской' : 'женский') : ''}`);
        option.value = voice.name;
        select.appendChild(option);
      });
      select.value = settings.voice || '';
    }

    // Характеры голоса рисуются из самого модуля: список живёт там, где
    // описаны их просодия и тракт, а не продублирован в разметке.
    const seg = $('voicePreset');
    if (seg && !seg.childElementCount) {
      Voice.presets.forEach((item) => {
        const button = el('button', 'seg__btn', item.label);
        button.type = 'button';
        button.dataset.preset = item.key;
        button.setAttribute('role', 'radio');
        seg.appendChild(button);
      });
    }
    if (seg) {
      seg.querySelectorAll('.seg__btn').forEach((button) => {
        const active = button.dataset.preset === Voice.preset;
        button.dataset.on = String(active);
        button.setAttribute('aria-checked', String(active));
      });
    }
    const note = $('voicePresetNote');
    if (note) note.textContent = Voice.presetNote || '';

    $('voiceEnabled').checked = settings.enabled;
    $('voiceDepth').value = settings.depth;
    $('voiceRate').value = settings.rate;
    $('voicePitch').value = settings.pitch;
    $('voiceVolume').value = Math.round(settings.volume * 100);
    renderVoiceNumbers();
    renderVoiceStatus();
    updateVoiceButton();
  }

  function renderVoiceNumbers() {
    const sign = (value) => (value > 0 ? '+' : value < 0 ? '−' : '');
    const rate = Number($('voiceRate').value);
    const pitch = Number($('voicePitch').value);
    $('voiceRateValue').textContent = `${sign(rate)}${Math.abs(rate)}%`;
    $('voicePitchValue').textContent = `${sign(pitch)}${Math.abs(pitch)}`;
    $('voiceVolumeValue').textContent = `${$('voiceVolume').value}%`;
    $('voiceDepthValue').textContent = Number($('voiceDepth').value).toFixed(2);
  }

  function updateVoiceButton() {
    const button = $('voiceToggle');
    if (!button || !window.Voice) return;
    button.dataset.on = Voice.enabled ? 'true' : 'false';
    button.title = Voice.enabled ? 'Ответы озвучиваются — выключить' : 'Ответы не озвучиваются — включить';
  }

  /** Строка «говорю» с кнопкой прервать. */
  function setSpeakingUi(on) {
    const strip = $('speakingStrip');
    if (strip) strip.hidden = !on;
    document.documentElement.dataset.speaking = on ? 'true' : 'false';
  }

  function bindVoiceSettings() {
    if (!window.Voice) return;

    const apply = (patch) => {
      Voice.set(patch);
      renderVoiceNumbers();
      renderVoiceStatus();
      updateVoiceButton();
    };

    $('voiceEnabled').onchange = () => {
      apply({ enabled: $('voiceEnabled').checked });
      toast(Voice.enabled ? 'Ответы озвучиваются' : 'Голос выключен', 'info');
    };
    $('voiceName').onchange = () => apply({ voice: $('voiceName').value });

    // Выбор характера подставляет свою просодию и пересобирает тракт целиком,
    // поэтому ползунки надо перерисовать: их значения только что изменились.
    const seg = $('voicePreset');
    if (seg) {
      seg.onclick = (event) => {
        const button = event.target.closest('.seg__btn');
        if (!button) return;
        Voice.usePreset(button.dataset.preset);
        renderVoiceControls();
        toast(`Голос: ${button.textContent}`, 'info');
        Voice.preview();
      };
    }

    // Ползунки применяются по отпусканию, а не по каждому движению: иначе на
    // каждый пиксель уходил бы запрос к синтезатору и сбрасывался кэш фраз.
    ['voiceRate', 'voicePitch', 'voiceVolume', 'voiceDepth'].forEach((id) => {
      $(id).addEventListener('input', renderVoiceNumbers);
      $(id).addEventListener('change', () => apply({
        rate: Number($('voiceRate').value),
        pitch: Number($('voicePitch').value),
        depth: Number($('voiceDepth').value),
        volume: Number($('voiceVolume').value) / 100
      }));
    });

    $('voicePreview').onclick = () => {
      Voice.preview();
      renderVoiceStatus();
    };
    $('voiceStopBtn').onclick = () => Voice.stop();
    $('voiceReset').onclick = () => {
      apply(Object.assign({}, Voice.DEFAULTS));
      Voice.usePreset(Voice.DEFAULTS.preset);
      renderVoiceControls();
      toast('Настройки голоса возвращены к исходным', 'info');
    };

    const stopButton = $('speakingStop');
    if (stopButton) stopButton.onclick = () => Voice.stop();

    const toggle = $('voiceToggle');
    if (toggle) {
      toggle.onclick = () => {
        const on = Voice.toggle();
        updateVoiceButton();
        renderVoiceControls();
        toast(on ? 'Ответы озвучиваются' : 'Голос выключен', 'info');
      };
    }

    /*
       Пока помощник говорит, микрофон приглушён.

       Динамики и микрофон стоят в одной комнате, и распознаватель отлично
       слышит синтезированный голос. Без этой связки «Открываю Chrome»
       возвращалось бы обратно как услышанная фраза, и помощник разговаривал бы
       сам с собой. Небольшой хвост после окончания нужен на затухание звука в
       комнате и на задержку самого распознавателя.
    */
    let muteKeeper = 0;
    Voice.onStart(() => {
      setSpeakingUi(true);
      renderVoiceStatus();
      if (!window.Listener) return;

      /*
         Глушение выставляется НЕ «на десять минут вперёд», а короткими
         отрезками, которые продлеваются, пока голос звучит.

         Разница в том, что происходит при сбое. Одно длинное глушение
         держится ровно столько, сколько попросили, — и если сообщение о
         конце речи почему-то не придёт, микрофон останется глухим до
         истечения срока, а человек будет говорить в пустоту, не понимая,
         почему помощник перестал слышать. Короткий отрезок в такой ситуации
         истекает сам, и худшее, что случится, — распознаватель услышит
         последнее слово собственной фразы.
      */
      clearInterval(muteKeeper);
      const hold = () => { if (Listener.on) Listener.mute(2500); };
      hold();
      muteKeeper = setInterval(() => {
        if (!Voice.speaking) { clearInterval(muteKeeper); muteKeeper = 0; return; }
        hold();
      }, 1200);
    });
    Voice.onEnd(() => {
      setSpeakingUi(false);
      clearInterval(muteKeeper);
      muteKeeper = 0;
      // Хвост после окончания — на затухание звука в комнате и на задержку
      // самого распознавателя: он отдаёт услышанное с опозданием.
      if (window.Listener && Listener.on) Listener.unmute(700);
      renderVoiceStatus();
    });

    Voice.probe().then(() => {
      renderVoiceControls();
      if (Voice.engine === 'none') {
        console.warn('[Cloud HDR] синтез речи недоступен:', Voice.lastError);
      }
    });

    renderVoiceControls();
  }

  /* ------------------------------------------------- модель понимания ---- */

  function renderNluStatus() {
    const pill = $('nluPill');
    if (!pill || !window.SpacyNLU) return;

    const on = SpacyNLU.available;
    pill.hidden = !on;
    if (!on) return;

    const info = SpacyNLU.state.info || {};
    pill.querySelector('.pill__value').textContent = 'spaCy';
    pill.title = 'Команды разбирает модель на spaCy (' + SpacyNLU.BASE + '): ' +
                 (info.intents ? info.intents.length : 27) + ' намерений, ' +
                 'лемматизация и предобученные векторы. Без сервиса работает нейросеть в браузере.';
  }

  /* ----------------------------------------------- своя языковая модель -- */

  function renderLlmStatus() {
    const pill = $('llmPill');
    const box = $('llmStatus');
    const on = LLM.available;

    if (pill) {
      pill.hidden = !on;
      pill.querySelector('.pill__value').textContent = LLM.current().title;
      pill.title = 'Свободные вопросы обрабатывает языковая модель проекта ' +
                   `(${LLM.state.url}). Команды разбирает модель понимания на spaCy.`;
    }

    if (!box) return;
    box.textContent = '';
    // Панель справочная: она отвечает на единственный вопрос «поднята ли
    // модель», а если нет — что сделать. Выбор модели живёт под строкой ввода,
    // здесь он только повторён карточками ниже.
    const rows = on
      ? [['Состояние', 'подключена'],
         ['Адрес', LLM.state.url],
         ['Отвечает', LLM.current().title]]
      // Плитки узкие, и длинное значение в них переносится на две строки,
      // ломая ряд. Поэтому здесь коротко — а команда целиком и объяснение,
      // что без модели работает, лежат в «Подробнее» тут же под панелью.
      : [['Состояние', 'не запущена'],
         ['Адрес', '127.0.0.1:' + LLM.KNOWN_PORTS[0]],
         ['Как поднять', 'start-ai.bat']];
    if (LLM.state.lastError) rows.push(['Замечание', LLM.state.lastError]);

    rows.forEach(([label, value]) => {
      const row = el('div', 'stat');
      row.append(el('span', 'stat__k', label), el('span', 'stat__v', String(value)));
      box.appendChild(row);
    });

    renderModelCards();
    renderLlmFacts();
    renderComposerModel();
  }

  /** Те же две модели в настройках — с описанием и переключением. */
  function renderModelCards() {
    const box = $('modelCards');
    if (!box) return;
    box.textContent = '';

    const items = LLM.known();
    const picked = LLM.current().id;

    items.forEach((item) => {
      const chosen = item.id === picked;
      const card = el('button', 'modelcard');
      card.type = 'button';
      card.dataset.on = chosen ? 'true' : 'false';
      card.append(icon(chosen ? 'check' : 'spark'));

      const text = el('div', 'modelcard__text');
      text.append(el('b', null, item.title), el('small', null, item.about));
      card.append(text);

      if (chosen) card.append(el('span', 'modelcard__badge', 'сейчас'));

      card.onclick = () => {
        LLM.setModel(item.id);
        renderLlmStatus();
        toast(`Отвечает ${LLM.current().title}`, 'ok');
      };
      box.appendChild(card);
    });
  }

  /* Паспорт модели в настройках — что это за сеть и на чём она работает. */
  function renderLlmFacts() {
    const box = $('llmFacts');
    if (!box) return;
    box.textContent = '';

    // Размер и окно сообщает сервер модели (/v1/models) — от той основы, что
    // сейчас отвечает. Пока сервер молчит, показываем прежние значения.
    const meta = (LLM.catalog && LLM.catalog[0]) || {};
    const params = meta.params || '760 млн';
    const small = params === '760 млн';
    const facts = [
      ['Параметров', params],
      ['Контекст', `${(meta.context || 2048).toLocaleString('ru-RU')} токенов`],
      ['Язык', 'русский'],
      ['Дообучена', 'на этом компьютере'],
      ['Факты о мире', small ? 'Википедия, дословно' : 'свои знания и Википедия'],
      ['Данные наружу', 'не уходят']
    ];

    facts.forEach(([label, value]) => {
      const card = el('div', 'fact');
      card.append(el('span', 'fact__k', label), el('span', 'fact__v', value));
      box.appendChild(card);
    });
  }

  /**
   * Подпись под строкой ввода: какая модель отвечает и жива ли она.
   *
   * Состояние вынесено на видное место намеренно. От него зависит, ответят на
   * свободный вопрос или придёт заготовка, а узнать это раньше можно было
   * только зайдя в настройки — то есть уже после того, как ответ разочаровал.
   */
  /** Помечает подпись модели как «сейчас думает» — на время генерации. */
  function setModelBusy(on) {
    const host = $('composerModel');
    if (!host) return;
    host.dataset.busy = on ? 'true' : 'false';
    const state = $('modelState');
    if (state) state.textContent = on ? 'думает…' : (LLM.available ? 'на связи' : 'не запущена');
  }

  function renderComposerModel() {
    const dot = $('modelDot');
    const state = $('modelState');
    if (!dot || !state) return;

    const on = LLM.available;
    const model = LLM.current();

    dot.dataset.on = on ? 'true' : 'false';
    state.textContent = on ? 'на связи' : 'не запущена';

    const name = $('modelName');
    if (name) name.textContent = model.title;

    const host = $('composerModel');
    if (host) {
      host.dataset.on = on ? 'true' : 'false';
      host.title = on
        ? `${model.title}: ${model.about} Отвечает на 127.0.0.1, команды разбирает классификатор. ` +
          'Нажмите, чтобы сменить модель.'
        : 'Модель разговора не запущена — свободные вопросы останутся без ответа. Запустите start-ai.bat.';
    }
    renderModelMenu();
  }

  /* ------------------------------------------------- выбор модели под вводом -- */

  /**
   * Список моделей под строкой ввода.
   *
   * Содержимое берётся из ответа /v1/models, а не прописано здесь. Разница
   * важная: подписи «Cloud HDR 3.2» и «Cloud HDR Ultra» живут в одном месте с
   * профилями генерации (python/cloudhdr_ai/profiles.py), и разойтись им негде.
   * Если в интерфейсе окажется имя, которого сервер не знает, — запрос уйдёт с
   * несуществующим идентификатором, а модель ответит профилем по умолчанию
   * молча. Такую рассинхронизацию заметить почти невозможно, поэтому источник
   * списка ровно один.
   */
  function renderModelMenu() {
    const menu = $('modelPickMenu');
    if (!menu) return;

    const items = LLM.known();
    const picked = LLM.current().id;
    menu.textContent = '';

    items.forEach((item) => {
      const chosen = item.id === picked;
      const row = el('button', 'modelpick__item');
      row.type = 'button';
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', chosen ? 'true' : 'false');
      row.dataset.id = item.id;
      row.dataset.on = chosen ? 'true' : 'false';

      const text = el('span', 'modelpick__text');
      text.append(el('b', null, item.title), el('small', null, item.about));
      row.append(text, icon('check'));

      row.onclick = () => {
        const chose = LLM.setModel(item.id);
        closeModelMenu();
        renderComposerModel();
        renderLlmStatus();
        toast(`Отвечает ${chose.title}`, 'ok');
      };
      menu.appendChild(row);
    });

    // Пока сервер молчит, выбор всё равно показан — но должно быть видно, что
    // сейчас он ни к чему не приведёт. Строка внизу, а не вместо списка:
    // «моделей нет» и «модель ещё не поднялась» — разные вещи, и вторая обычно
    // проходит сама через полминуты.
    if (!LLM.available) {
      menu.appendChild(el('p', 'modelpick__empty',
        'Модель ещё не запущена — выбор запомнится и применится, как только она поднимется.'));
    }
  }

  function closeModelMenu() {
    const pick = $('modelPick');
    if (!pick) return;
    pick.dataset.open = 'false';
    $('composerModel').setAttribute('aria-expanded', 'false');
  }

  function bindModelPicker() {
    const pick = $('modelPick');
    const button = $('composerModel');
    if (!pick || !button) return;

    button.onclick = (event) => {
      event.stopPropagation();
      const open = pick.dataset.open !== 'true';
      pick.dataset.open = open ? 'true' : 'false';
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      // Список опрашивается при открытии, а не только при загрузке страницы.
      // Модель поднимается 10-30 секунд, и без этого выбор оставался бы пустым
      // ровно у того, кто открыл приложение и сразу полез выбирать.
      if (open && !LLM.catalog.length) LLM.probe().then(renderComposerModel).catch(() => {});
    };

    // Закрытие по клику мимо и по Escape. Оба нужны: мышью список закрывают
    // промахом, с клавиатуры — Escape, и отсутствие любого из двух ощущается
    // как залипшее меню.
    document.addEventListener('click', (event) => {
      if (pick.dataset.open === 'true' && !pick.contains(event.target)) closeModelMenu();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && pick.dataset.open === 'true') closeModelMenu();
    });
  }

  function bindLlmSettings() {
    // Кнопка «стоп» останавливает и генерацию, и голос: для человека это одно
    // действие — «замолчи», а не два разных механизма.
    $('stopBtn').onclick = () => {
      LLM.stop();
      if (window.Voice) Voice.stop();
      toast('Генерация остановлена', 'info');
    };
    bindModelPicker();
  }

  function openSettings() {
    // Полей здесь больше нет ни у бюджета, ни у модели. Причина одна: лимит,
    // который отключается в два клика, ничего не ограничивает, а модель у
    // проекта своя и единственная — выбирать не из чего.
    renderTokens();
    renderBrainStats();
    renderLlmStatus();
    renderListenSettings();
    renderSosList();
    renderVoiceControls();

    // Открываем всегда на «Модели». Запоминать последнюю вкладку заманчиво, но
    // ошибочно: чаще всего в настройки заходят посмотреть, поднялась ли модель,
    // а «где я остановился в прошлый раз» здесь никого не занимает.
    showSettingsTab('model');

    // Панель опрашивает модель ЗАНОВО при каждом открытии, а не показывает то,
    // что запомнилось при загрузке страницы.
    //
    // Прежнее поведение сбивало с толку в самом частом сценарии: модель
    // поднимается 10-30 секунд, за это время страница успевает открыться и
    // записать «не запущена». Дальше человек ждёт, заходит в настройки
    // проверить — и видит ту же надпись, хотя модель давно готова. Помогало
    // только обновление страницы, о котором догадаться неоткуда.
    LLM.probe().then(renderLlmStatus).catch(() => {});

    $('settingsModal').dataset.open = 'true';
  }

  /* --------------------------------------------------- вкладки настроек -- */

  /**
   * Показывает один раздел настроек.
   *
   * Разделы не перерисовываются, а прячутся: у них живое содержимое (ползунки
   * голоса, статистика прослушивания, список экстренных фраз), и пересобирать
   * его при каждом переключении значило бы терять и позицию, и обработчики.
   *
   * Нижняя черта у последнего ВИДИМОГО раздела снимается отдельно. Правило
   * :last-child здесь не годится: последним ребёнком остаётся раздел из другой
   * вкладки, и в открытой вкладке под нижним разделом висела бы линия в никуда.
   */
  function showSettingsTab(name) {
    const body = $('settingsBody');
    if (!body) return;
    body.dataset.tab = name;

    const panes = Array.from(body.querySelectorAll('.pane[data-tab]'));
    const shown = [];
    panes.forEach((pane) => {
      const visible = pane.dataset.tab === name;
      pane.hidden = !visible;
      pane.classList.remove('pane--last');
      if (visible) shown.push(pane);
    });
    if (shown.length) shown[shown.length - 1].classList.add('pane--last');

    Array.from(document.querySelectorAll('.settings__tab')).forEach((tab) => {
      tab.setAttribute('aria-selected', tab.dataset.tab === name ? 'true' : 'false');
    });
    body.scrollTop = 0;
  }

  function bindSettingsTabs() {
    const tabs = $('settingsTabs');
    if (!tabs) return;
    tabs.addEventListener('click', (event) => {
      const tab = event.target.closest('.settings__tab');
      if (tab) showSettingsTab(tab.dataset.tab);
    });
  }

  function bindSettings() {
    $('settingsBtn').onclick = openSettings;
    $('settingsClose').onclick = () => { $('settingsModal').dataset.open = 'false'; };
    $('settingsModal').querySelector('.modal__scrim').onclick = () => { $('settingsModal').dataset.open = 'false'; };
    bindSettingsTabs();

    $('brainRetrain').onclick = async () => {
      const button = $('brainRetrain');
      button.disabled = true;
      const epochs = Math.max(10, Math.min(600, Number($('brainEpochs').value) || Brain.defaultEpochs));
      const meta = await Brain.trainAsync(epochs, showTrainingProgress);
      hideTrainingProgress();
      renderBrainStats();
      button.disabled = false;
      button.textContent = 'Переобучить';
      toast(`Обучение завершено за ${(meta.ms / 1000).toFixed(1)} с: ${meta.ranEpochs} эпох, ` +
            `лучшая на ${meta.bestEpoch}-й, точность на проверке ${Math.round(meta.valAccuracy * 100)}%`, 'ok');
    };

    $('brainForget').onclick = async () => {
      const button = $('brainForget');
      button.disabled = true;
      button.textContent = 'Сбрасываю…';
      const meta = await Brain.reset(showTrainingProgress);
      hideTrainingProgress();
      renderBrainStats();
      button.disabled = false;
      button.textContent = 'Сбросить обучение';
      toast(`Сеть переобучена с нуля, поправки забыты. Точность на проверке ${Math.round(meta.valAccuracy * 100)}%`, 'info');
    };

    $('memoryClear').onclick = () => {
      Skills.memory.clear();
      toast('Личные заметки удалены', 'info');
    };

    bindListenSettings();
    bindSosSettings();
    bindVoiceSettings();

    Tokens.onChange(renderTokens);
    // счётчик паузы должен идти сам, без действий пользователя
    setInterval(() => {
      const status = Tokens.status();
      if (status.blocked || $('settingsModal').dataset.open === 'true') renderTokens();
    }, 30000);
  }

  /* ================================================= 10. Фон ============== */

  // Живой mesh-градиент — отдельный файл js/mesh.js.

  /* ======================================================= 11. Прочее ==== */

  function autoResize() {
    const input = $('input');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 190) + 'px';
  }

  /* ------------------------------------------------- поднос с картинками -- */

  /** Сколько картинок берём в одно сообщение. */
  const TRAY_LIMIT = 4;

  function renderTray() {
    const tray = $('visionTray');
    if (!tray || !window.Vision) return;

    const items = Vision.pending;
    tray.textContent = '';
    tray.hidden = items.length === 0;

    items.forEach((item) => {
      const chip = el('div', 'tray__item');
      const shot = el('img');
      shot.src = item.thumb;
      shot.alt = item.name;
      chip.appendChild(shot);

      const label = el('div', 'tray__meta');
      label.append(el('b', null, item.name.length > 22 ? item.name.slice(0, 21) + '…' : item.name),
                   el('span', null, `${item.width}×${item.height} · ${Vision.bytes(item.size)}`));
      chip.appendChild(label);

      const remove = el('button', 'tray__x');
      remove.type = 'button';
      remove.title = 'Убрать';
      remove.setAttribute('aria-label', 'Убрать картинку');
      remove.appendChild(icon('x'));
      remove.onclick = () => { Vision.drop(item.id); renderTray(); };
      chip.appendChild(remove);

      tray.appendChild(chip);
    });

    // Кнопка отправки оживает и от одной приложенной картинки: вопрос к ней
    // писать не обязательно.
    const input = $('input');
    $('sendBtn').disabled = busy || (!input.value.trim() && !items.length);
  }

  /**
   * Приём файлов из любого источника: кнопка, перетаскивание, буфер обмена.
   *
   * Каждая отказавшая картинка называется по имени. Молча пропустить одну из
   * трёх значило бы, что человек узнает о потере только по ответу не про то.
   */
  async function intake(list) {
    const files = Array.from(list || []).filter(Boolean);
    if (!files.length || !window.Vision) return 0;

    const room = TRAY_LIMIT - Vision.count;
    if (room <= 0) {
      toast(`Больше ${TRAY_LIMIT} картинок за раз не беру — отправьте эти`, 'info');
      return 0;
    }

    let added = 0;
    for (const file of files.slice(0, room)) {
      if (!Vision.accepts(file)) {
        toast(`«${file.name || 'файл'}» — не картинка, такое я смотреть не умею`, 'err');
        continue;
      }
      try {
        await Vision.hold(file);
        added++;
      } catch (error) {
        toast(error.message || 'Картинку прочитать не вышло', 'err');
      }
    }

    if (files.length > room) toast(`Взял ${room}, остальные — следующим сообщением`, 'info');
    if (added) {
      renderTray();
      $('input').focus();
      toast(added === 1
        ? 'Картинка на месте — спросите о ней или просто отправьте'
        : `Взял картинок: ${added}`, 'ok');
    }
    return added;
  }

  function bindVision() {
    if (!window.Vision) return;

    const picker = $('visionFile');
    const openPicker = () => { if (!busy && picker) picker.click(); };

    const attach = $('attachBtn');
    if (attach) attach.onclick = openPicker;
    const chip = $('visionChip');
    if (chip) chip.onclick = openPicker;
    const card = $('visionCard');
    if (card) card.onclick = openPicker;

    if (picker) {
      picker.onchange = async () => {
        await intake(picker.files);
        picker.value = '';        // тот же файл можно выбрать ещё раз
      };
    }

    /*
       Вставка из буфера обмена ловится на всём документе, а не на поле ввода.

       Так Ctrl+V работает сразу после Print Screen или ножниц Windows, когда
       фокуса в поле ещё нет: человек снял кусок экрана и вставляет, не
       раздумывая, куда именно нажимать. Событие с текстом при этом не трогаем —
       обычная вставка текста должна работать как всегда.
    */
    document.addEventListener('paste', (event) => {
      if (busy) return;
      const items = Array.from((event.clipboardData && event.clipboardData.items) || []);
      const files = items.filter((item) => item.kind === 'file').map((item) => item.getAsFile()).filter(Boolean);
      if (!files.length) return;
      event.preventDefault();
      intake(files);
    });

    // --- перетаскивание -----------------------------------------------------
    //
    // Счётчик глубины вместо простого «навели/увели»: dragleave прилетает при
    // каждом переходе между вложенными узлами, и без счётчика полотно моргало
    // бы на каждой границе элемента под курсором.
    const zone = $('dropZone');
    let depth = 0;
    const carriesFiles = (event) => {
      const transfer = event.dataTransfer;
      return Boolean(transfer && Array.from(transfer.types || []).includes('Files'));
    };

    document.addEventListener('dragenter', (event) => {
      if (busy || !carriesFiles(event)) return;
      event.preventDefault();
      depth++;
      if (zone) zone.hidden = false;
    });
    document.addEventListener('dragover', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
    });
    document.addEventListener('dragleave', (event) => {
      if (!carriesFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (!depth && zone) zone.hidden = true;
    });
    document.addEventListener('drop', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth = 0;
      if (zone) zone.hidden = true;
      if (busy) { toast('Секунду — я ещё отвечаю', 'info'); return; }
      intake(event.dataTransfer.files);
    });

    renderTray();
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('cloudhdr.theme.v2', theme);
  }

  function exportChat() {
    const chat = store.active;
    const lines = [`# ${chat.title}`, `Cloud HDR · ${new Date(chat.created).toLocaleString('ru-RU')}`, ''];
    chat.messages.forEach((message) => {
      lines.push(`**${message.role === 'user' ? 'Вы' : 'Cloud HDR AI'}** (${timeLabel(message.ts)}):`);
      if (message.image) lines.push(`🖼 ${message.image.name} — ${message.image.w}×${message.image.h}`);
      lines.push(message.text);
      if (message.cmd) lines.push(`> ${message.cmd.state === 'done' ? '✅' : '❌'} ${message.cmd.title} — ${message.cmd.sub}`);
      // Советы по картинке уходят в выгрузку списком: ради них разбор и
      // затевается, а в тексте ответа помещаются в лучшем случае два первых.
      if (message.shot && message.shot.advice && message.shot.advice.length) {
        lines.push('', '_Что улучшить:_');
        message.shot.advice.forEach((item, index) => lines.push(`${index + 1}. **${item.title}** — ${item.detail}`));
      }
      lines.push('');
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const link = el('a');
    link.href = URL.createObjectURL(blob);
    link.download = `cloud-hdr-${chat.id}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    toast('Чат сохранён в Markdown', 'ok');
  }

  function attachRipples() {
    document.addEventListener('pointerdown', (event) => {
      const host = event.target.closest('.ripple');
      if (!host || reduced()) return;
      const rect = host.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height) * 2.2;
      const rip = el('span', 'rip');
      rip.style.width = rip.style.height = size + 'px';
      rip.style.left = event.clientX - rect.left + 'px';
      rip.style.top = event.clientY - rect.top + 'px';
      host.appendChild(rip);
      setTimeout(() => rip.remove(), 620);
    });
  }

  /* ============================== 12. Десктопный режим (PWA) ============= */

  /** Service worker — мгновенный старт окна и предложение «Установить». */
  function registerServiceWorker() {
    if (!('serviceWorker' in navigator) || !location.protocol.startsWith('http')) return;
    navigator.serviceWorker.register('sw.js').catch(() => { /* не критично */ });
  }

  /**
   * Восстанавливает закрепление окна поверх остальных и обрабатывает
   * ярлык «Голосовой запрос» (?voice=1) — окно открывается сразу слушающим.
   */
  function restoreWindowState() {
    const wantPinned = localStorage.getItem('cloudhdr.pinned') === '1';
    const wantVoice = new URLSearchParams(location.search).get('voice') === '1';

    const whenReady = (action) => {
      let tries = 0;
      const timer = setInterval(() => {
        if (Agent.mode === 'live') { clearInterval(timer); action(); }
        else if (++tries > 30) clearInterval(timer);
      }, 400);
    };

    if (wantPinned) {
      whenReady(async () => {
        try {
          await Agent.exec({ intent: 'window_pin' });
          $('pinToggle').dataset.on = 'true';
        } catch { /* окно ещё не найдено — не страшно */ }
      });
    }
    if (wantVoice) pendingVoice = true;
  }

  /* ========================================================= 13. Старт === */

  function bindEvents() {
    const input = $('input');

    $('composer').addEventListener('submit', (event) => {
      event.preventDefault();
      const text = input.value.trim();
      // Приложенная картинка — самостоятельный повод отправить сообщение:
      // «посмотри» в таком случае и есть весь вопрос.
      if (!text && !(window.Vision && Vision.count)) return;
      input.value = '';
      autoResize();
      send(text);
    });

    input.addEventListener('input', () => {
      autoResize();
      $('sendBtn').disabled = busy || (!input.value.trim() && !(window.Vision && Vision.count));
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        $('composer').requestSubmit();
      }
    });

    document.addEventListener('click', (event) => {
      const prompt = event.target.closest('[data-prompt]');
      if (prompt && !busy) {
        input.value = prompt.dataset.prompt;
        autoResize();
        $('composer').requestSubmit();
      }
    });

    // Кнопка «Найти на ПК»: не отправляет запрос сама, а готовит строку ввода.
    //
    // Сделано так намеренно. Поиск без цели («покажи всё, что найдёшь») почти
    // никому не нужен и занимает полминуты, поэтому кнопка ставит начало фразы
    // и ждёт, что человек допишет искомое. Слово «файл» в подставленном тексте
    // не случайно: оно же служит признаком, по которому разбор понимает, что
    // речь о компьютере, а не обычный вопрос.
    const pcSearch = $('pcSearchChip');
    if (pcSearch) {
      pcSearch.onclick = () => {
        if (busy) return;
        input.value = 'найди файл ';
        autoResize();
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        toast('Допишите, что искать на дисках — например «найди файл отчёт»', 'info');
      };
    }

    $('newChat').onclick = () => {
      store.create();
      renderHistory();
      renderChat();
      input.focus();
    };
    $('historySearch').oninput = renderHistory;
    $('exportChat').onclick = exportChat;
    $('themeToggle').onclick = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
    $('motionToggle').onclick = () => {
      applyMotion(!motionOn);
      toast(motionOn ? 'Анимации включены' : 'Анимации выключены', 'info');
    };

    $('pinToggle').onclick = async () => {
      const button = $('pinToggle');
      const next = button.dataset.on !== 'true';
      if (Agent.mode === 'offline') { toast('Агент не подключён', 'err'); return; }
      try {
        await Agent.exec({ intent: next ? 'window_pin' : 'window_unpin' });
        button.dataset.on = String(next);
        localStorage.setItem('cloudhdr.pinned', next ? '1' : '0');
        toast(next ? 'Окно закреплено поверх остальных' : 'Окно откреплено', 'ok');
      } catch (error) {
        toast(error.message, 'err');
      }
    };
    $('menuToggle').onclick = () => {
      const sidebar = $('sidebar');
      sidebar.dataset.open = sidebar.dataset.open === 'true' ? 'false' : 'true';
    };
    $('revokeAccess').onclick = async () => {
      await Agent.revoke();
      updateAgentUi();
      resetPermModal();
      $('permModal').dataset.open = 'true';
      toast('Разрешение на управление отозвано', 'info');
    };

    $('micBtn').onclick = () => (voice.active ? stopVoice(true) : startVoice());
    $('earBtn').onclick = toggleListening;
    $('voiceStop').onclick = () => stopVoice(true);
    $('voiceCancel').onclick = () => { voice.finalText = ''; stopVoice(false); };

    $('introContinue').onclick = closeIntro;
    $('permAllow').onclick = requestPermission;
    $('permRetry').onclick = requestPermission;
    $('permDemo').onclick = enableDemoMode;
    $('permDemo2').onclick = enableDemoMode;
    $('pinInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') requestPermission(); });

    // подсветка карточек за курсором
    document.addEventListener('pointermove', (event) => {
      const card = event.target.closest('.card');
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mx', event.clientX - rect.left + 'px');
      card.style.setProperty('--my', event.clientY - rect.top + 'px');
    }, { passive: true });

    addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.key.toLowerCase() === 'k') { event.preventDefault(); $('newChat').click(); }
      if (event.key === 'Escape') {
        // Порядок разбора — от самого «громкого» к самому тихому: сначала
        // запись голоса, потом окно подтверждения, и только потом речь.
        // Прервать говорящего помощника Escape должен всегда, но не ценой
        // того, что окно подтверждения перестанет закрываться.
        if (voice.active) stopVoice(false);
        else if ($('confirmModal').dataset.open === 'true') $('confirmNo').click();
        else if (window.Voice && Voice.speaking) Voice.stop();
      }
      if (event.key === '/' && document.activeElement !== input && !event.ctrlKey) {
        event.preventDefault();
        input.focus();
      }
    });
  }

  /**
   * Досматривает, не поднялись ли модели уже после открытия окна.
   *
   * Раньше здесь стояла ОДНА повторная проверка через тридцать секунд, и в
   * комментарии было написано, что она закрывает случай полностью. Она
   * закрывала половину: тот случай, когда модель уже грузилась, пока окно
   * открывалось. А если её запускают ПОЗЖЕ — через start-ai.bat, ярлыком, или
   * просто потому, что весам нужно больше тридцати секунд, — надпись «не
   * запущена» оставалась висеть до ручной перезагрузки страницы. Догадаться,
   * что приложение врёт, а не сломалось, было неоткуда.
   *
   * Теперь опрос идёт по расписанию с растущими промежутками — часто в первую
   * минуту, когда модель обычно и появляется, и редко дальше. Всего семь
   * запросов к двум локальным портам за шесть минут; как только служба
   * найдена, её из расписания убирают.
   */
  /*
     Сторож служб. Раньше он досматривал модель только первые пять минут после
     запуска и дальше молчал: если модель падала или её окно закрывали, окно
     приложения узнавало об этом лишь по ошибке в ответе и до перезапуска
     отвечало заготовками. Теперь он работает всё время: пока службы не на
     связи — часто, потом раз в полминуты. Молчит служба дольше двух проверок
     подряд — просит агента поднять её (агент сам не даст делать это чаще раза
     в 90 секунд, пока грузятся веса).
  */
  function watchServices() {
    const startGaps = [8000, 12000, 20000, 30000];
    const STEADY = 30000;
    let step = 0;
    let llmMisses = 0;
    let nluMisses = 0;
    let kicks = 0;
    const allSeen = () => LLM.available && (!window.SpacyNLU || SpacyNLU.available);

    const tick = async () => {
      const hadLlm = LLM.available;
      const hadNlu = !window.SpacyNLU || SpacyNLU.available;
      const jobs = [LLM.probe().then(renderLlmStatus)];
      if (window.SpacyNLU) jobs.push(SpacyNLU.probe().then(renderNluStatus));
      await Promise.all(jobs).catch(() => {});

      if (LLM.available && !hadLlm) toast(`Модель разговора подключена: ${LLM.current().title}`, 'ok');
      if (!LLM.available && hadLlm) toast('Модель разговора перестала отвечать — поднимаю её заново.', 'info');
      if (window.SpacyNLU && SpacyNLU.available && !hadNlu) toast('Модель понимания команд подключена: spaCy', 'ok');

      llmMisses = LLM.available ? 0 : llmMisses + 1;
      nluMisses = !window.SpacyNLU || SpacyNLU.available ? 0 : nluMisses + 1;
      if (allSeen()) kicks = 0;
      // Три попытки без толку — значит, служб на этом компьютере просто нет
      // (веса не скачаны). Дальше не дёргаем агента, пока служба не появится.
      if ((llmMisses >= 2 || nluMisses >= 2) && kicks < 3) {
        kicks++;
        Agent.startServices();
      }

      // Пока кто-то молчит, смотрим чаще: отказ соединения стоит миллисекунды,
      // а вернувшуюся модель хочется подхватить сразу, а не через полминуты.
      setTimeout(tick, allSeen() ? STEADY : step < startGaps.length ? startGaps[step++] : 10000);
    };

    setTimeout(tick, startGaps[step++]);
  }

  /**
   * Показывает ошибку прямо на странице. Без этого любое исключение в init()
   * оставляло бы .app с opacity:0 — то есть пустой чёрный экран без объяснений.
   *
   * Плашка намеренно НЕ ловит нажатия: она висит внизу, ровно над строкой
   * ввода, и раньше перехватывала все клики по ней. Один сбой в необязательном
   * блоке — и приложение выглядело намертво заблокированным, хотя работало.
   * Теперь нажатия проходят сквозь неё, а закрыть её можно крестиком.
   */
  function showFatal(error) {
    const message = (error && (error.message || error.reason || error)) + '';
    const stack = (error && error.stack) ? String(error.stack).split('\n').slice(0, 4).join('\n') : '';
    let box = document.getElementById('fatalBox');
    let text = document.getElementById('fatalText');
    if (!box) {
      box = el('div');
      box.id = 'fatalBox';
      box.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;z-index:999;padding:14px 44px 14px 18px;' +
        'border-radius:14px;border:1px solid rgba(251,113,133,.5);background:rgba(40,10,20,.96);color:#ffe4e6;' +
        'font:13px/1.5 ui-monospace,monospace;white-space:pre-wrap;box-shadow:0 20px 50px rgba(0,0,0,.6);' +
        'pointer-events:none;max-height:38vh;overflow:hidden';

      text = el('span');
      text.id = 'fatalText';

      const close = el('button', null, '×');
      close.type = 'button';
      close.title = 'Закрыть сообщение';
      close.setAttribute('aria-label', 'Закрыть сообщение об ошибке');
      close.style.cssText = 'position:absolute;top:8px;right:10px;width:26px;height:26px;line-height:1;' +
        'border:0;border-radius:8px;background:transparent;color:#ffe4e6;font-size:18px;cursor:pointer;' +
        'pointer-events:auto';
      close.addEventListener('click', () => box.remove());

      box.append(text, close);
      document.body.appendChild(box);
    }
    // Обработчик ошибок сам падать не имеет права: исключение здесь ушло бы в
    // addEventListener('error') и вернулось бы сюда же по кругу.
    if (text) text.textContent = '⚠ Ошибка Cloud HDR: ' + message + (stack ? '\n' + stack : '');
    console.error('[Cloud HDR]', error);
  }

  addEventListener('error', (event) => showFatal(event.error || event.message));
  addEventListener('unhandledrejection', (event) => showFatal(event.reason));

  function init() {
    try {
      // Брендированный заголовок запуска — виден в консоли разработчика (F12).
      console.log(
        '%c[CLOUD HDR AI v1.0]%c Запуск модуля...',
        'color:#38bdf8;font-weight:700', 'color:inherit'
      );

      applyTheme(localStorage.getItem('cloudhdr.theme.v2') || 'light');
      applyMotion(motionOn);

      // сеть поднимается до первого запроса: либо веса из localStorage,
      // либо обучение с нуля — на этой архитектуре это доли секунды
      // Если веса уже в localStorage — сеть готова мгновенно. Если нет,
      // обучение идёт в фоне: до его конца запросы разбираются правилами,
      // а прогресс виден в шапке.
      Brain.boot({ onProgress: showTrainingProgress });
      Brain.ready.then((meta) => {
        hideTrainingProgress();
        renderBrainStats();
        console.log(`[Cloud HDR] нейросеть ${Brain.shape}: ${meta.samples} примеров, ` +
                    `точность на проверке ${Math.round((meta.valAccuracy || 0) * 100)}%, ${meta.ms} мс`);
      });
      Tokens.load();

      // сервис понимания команд ищется в фоне; без него разбор остаётся в браузере
      if (window.SpacyNLU) {
        SpacyNLU.probe().then((result) => {
          renderNluStatus();
          if (result.available) toast('Модель понимания команд подключена: spaCy', 'ok');
        });
      }

      // своя языковая модель ищется в фоне: нет — команды работают как обычно
      LLM.load();
      renderComposerModel();
      LLM.probe().then((result) => {
        renderLlmStatus();
        if (result.available) toast(`Модель разговора подключена: ${LLM.current().title}`, 'ok');
      });

      watchServices();

      store.load();
      renderHistory();
      renderChat();
      bindEvents();
      bindVision();
      bindSettings();
      bindLlmSettings();
      renderTokens();
      renderLlmStatus();
      renderNluStatus();
      attachRipples();
      initListener();
      updateAgentUi();
      runSplash();
      bootPermission();
      registerServiceWorker();
      restoreWindowState();
    } catch (error) {
      showFatal(error);
    } finally {
      // интерфейс показываем всегда, даже если часть инициализации упала
      setTimeout(() => { $('app').dataset.booted = 'true'; }, 60);
    }
  }

  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();
