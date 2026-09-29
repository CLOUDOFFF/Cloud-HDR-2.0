/* ============================================================================
   Cloud HDR — умения второй версии в окне чата.

     • разбор папок      «разбери загрузки» → план → «Разложить» → «Вернуть»
     • защита            «проверь компьютер» → кольцо, шесть категорий,
                         находки с кнопкой «В карантин» и возвратом
     • перевод           подсказка по Ctrl+Alt+T и прогрев переводчика
     • телефон           подключение по QR-коду (см. connectPhone)

   Считает всё служба python/cloudhdr_hub.py на 127.0.0.1:4480. Здесь только
   вид и разговор: узнать фразу, показать карточку, передать нажатие.
   Ничего не удаляется и не перемещается без нажатия человека.
   ========================================================================== */
(function () {
  'use strict';

  const HUB = 'http://127.0.0.1:4480';

  async function hub(path, body, timeout = 15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(HUB + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal
      });
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  const HUB_DOWN = 'Служба умений Cloud HDR не отвечает. Перезапустите приложение — она поднимается вместе с ним.';

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, kind, onClick) {
    const node = el('button', 'fx-btn' + (kind ? ' fx-btn--' + kind : ''), label);
    node.type = 'button';
    node.onclick = onClick;
    return node;
  }

  function plural(n, one, few, many) {
    const a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return one;
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
    return many;
  }

  /* ================================================================ файлы == */

  // Системные папки называем так, как их показывает Проводник, а не по
  // английскому имени на диске.
  const FOLDER_NAMES = { downloads: 'Загрузки', desktop: 'Рабочий стол', documents: 'Документы', pictures: 'Изображения' };
  function folderLabel(path) {
    const last = String(path).split('\\').pop();
    return FOLDER_NAMES[last.toLowerCase()] || last;
  }

  // Нужна и просьба, и папка: «разбери эту картинку» — это зрение, а не файлы.
  const SORT_RE = /^(?:пожалуйста\s+)?(?:разбери|разложи|рассортируй|отсортируй|наведи порядок в|прибери)\s+(?:мне\s+|папку\s+|мои\s+)?(?:загрузк|рабоч|документ|изображени|download|desktop|[a-z]:\\)/i;
  const UNDO_RE = /^(?:верни как было|отмени разбор|верни файлы обратно|отмени сортировку)/i;

  async function sortFolder(text) {
    CloudChat.user(text);
    CloudChat.setBusy(true);
    let plan;
    try {
      plan = await hub('/hub/files/sort', { text });
    } catch {
      await CloudChat.ai(HUB_DOWN);
      return CloudChat.setBusy(false);
    }
    if (!plan.ok) {
      await CloudChat.ai(plan.error);
      return CloudChat.setBusy(false);
    }
    const name = folderLabel(plan.folder);
    if (!plan.count) {
      await CloudChat.ai(`Заглянул в «${name}» — там уже порядок, раскладывать нечего.`);
      return CloudChat.setBusy(false);
    }

    const card = el('div', 'fx-card');
    const groups = el('div', 'fx-groups');
    Object.entries(plan.groups).forEach(([group, count]) => {
      const chip = el('span', 'fx-group');
      chip.append(el('b', null, String(count)), document.createTextNode(' ' + group));
      groups.appendChild(chip);
    });
    card.appendChild(groups);
    if (plan.sample.length) card.appendChild(el('p', 'fx-note', plan.sample.join(' · ') + (plan.count > plan.sample.length ? ' …' : '')));
    const actions = el('div', 'fx-actions');
    card.appendChild(actions);

    const node = await CloudChat.ai(
      `В «${name}» ${plan.count} ${plural(plan.count, 'файл', 'файла', 'файлов')}, которые можно разложить по папкам. ` +
      'Вложенные папки не трогаю, а всё сделанное можно вернуть одной кнопкой. Раскладываю?', card);
    CloudChat.setBusy(false);

    actions.append(
      button('Разложить', 'primary', async () => {
        actions.textContent = '';
        actions.appendChild(el('span', 'fx-note', 'Раскладываю…'));
        let done;
        try { done = await hub('/hub/files/sort', { text, apply: true }, 60000); } catch { done = { ok: false, error: HUB_DOWN }; }
        actions.textContent = '';
        if (!done.ok) { CloudChat.update(node, done.error); return; }
        const parts = Object.entries(done.groups).map(([g, n]) => `${g.toLowerCase()} — ${n}`).join(', ');
        const folders = Object.keys(done.groups).length;
        CloudChat.update(node, `Готово! Разложил ${done.moved} ${plural(done.moved, 'файл', 'файла', 'файлов')} по ${folders} ${plural(folders, 'папке', 'папкам', 'папкам')}: ${parts}.` +
          (done.failed ? ` ${done.failed} не удалось переместить — они заняты другой программой.` : ''));
        CloudChat.toast(`Разложил ${done.moved} ${plural(done.moved, 'файл', 'файла', 'файлов')}`, 'ok');
        actions.appendChild(button('Вернуть как было', '', async () => {
          actions.textContent = '';
          const back = await hub('/hub/files/undo', {}).catch(() => ({ ok: false, error: HUB_DOWN }));
          CloudChat.update(node, back.ok ? `Вернул всё как было: ${back.restored} ${plural(back.restored, 'файл', 'файла', 'файлов')} снова на своих местах.` : back.error);
        }));
      }),
      button('Не надо', 'ghost', () => {
        actions.textContent = '';
        CloudChat.update(node, `Хорошо, оставляю «${name}» как есть.`);
      })
    );
  }

  async function undoSort(text) {
    CloudChat.user(text);
    const back = await hub('/hub/files/undo', {}).catch(() => ({ ok: false, error: HUB_DOWN }));
    await CloudChat.ai(back.ok ? `Вернул всё как было: ${back.restored} ${plural(back.restored, 'файл', 'файла', 'файлов')} снова на своих местах.` : back.error);
  }

  /* =============================================================== защита == */

  // [а-яё]*, а не \w*: в JavaScript \w — только латиница, и «провер\w*» не
  // доходил до мягкого знака в «проверь».
  const SCAN_RE = /(провер[а-яё]*|просканируй|сканируй)\s.*(компьютер|пк|систем|вирус|угроз|безопасн)|^(на вирусы|защита|есть ли вирусы|нет ли вирусов)/i;

  const ICON_OK = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const ICON_BAD = '<svg viewBox="0 0 24 24"><path d="M12 5v9M12 19v.1"/></svg>';

  function buildScanCard() {
    const card = el('div', 'fx-card fx-scan');
    card.innerHTML =
      '<div class="fx-scan__body">' +
        '<div class="fx-ring"><svg viewBox="0 0 120 120"><circle class="fx-ring__bg" cx="60" cy="60" r="52"/>' +
        '<circle class="fx-ring__fg" cx="60" cy="60" r="52" pathLength="1"/></svg>' +
        '<div class="fx-ring__mid"><svg class="ic"><use href="#i-shield"/></svg><b>0%</b><span>файлы: 0</span></div></div>' +
        '<ul class="fx-checks"></ul>' +
      '</div>' +
      '<div class="fx-scan__foot"><span class="fx-scan__path">Подготовка к проверке…</span></div>' +
      '<div class="fx-findings"></div>';
    return card;
  }

  function renderScan(card, s) {
    const pct = Math.round((s.progress || 0) * 100);
    card.querySelector('.fx-ring__fg').style.strokeDashoffset = 1 - (s.progress || 0);
    card.querySelector('.fx-ring__mid b').textContent = pct + '%';
    card.querySelector('.fx-ring__mid span').textContent = 'файлы: ' + (s.files || 0).toLocaleString('ru-RU');
    const list = card.querySelector('.fx-checks');
    list.textContent = '';
    (s.categories || []).forEach((c) => {
      const row = el('li', 'fx-check fx-check--' + c.state);
      const mark = el('span', 'fx-check__ic');
      mark.innerHTML = c.state === 'bad' || c.state === 'warn' ? ICON_BAD : ICON_OK;
      const threats = c.items.filter((i) => i.level === 'threat').length;
      const warns = c.items.filter((i) => i.level === 'warn').length;
      const result = c.state === 'wait' ? '' : c.state === 'run' ? 'проверяю…'
        : threats ? `${threats} ${plural(threats, 'угроза', 'угрозы', 'угроз')}`
        : warns ? `${warns} ${plural(warns, 'вопрос', 'вопроса', 'вопросов')}` : 'чисто';
      row.append(mark, el('span', 'fx-check__t', c.title), el('span', 'fx-check__r', result));
      if (c.note && c.state !== 'run' && c.state !== 'wait') row.title = c.note;
      list.appendChild(row);
    });
    const foot = card.querySelector('.fx-scan__path');
    if (s.state === 'running') {
      foot.textContent = s.current || s.stage || 'Проверяю…';
    } else {
      // Итог уже написан в пузыре над карточкой — здесь то, чего там нет.
      const seconds = s.finished && s.started ? Math.max(1, Math.round(s.finished - s.started)) : null;
      const defender = s.defender && s.defender.RealTimeProtectionEnabled ? 'Защитник Windows включён' : 'Защитник Windows выключен';
      foot.textContent = [seconds ? `Проверено за ${seconds} с` : 'Проверка завершена', defender].join(' · ');
    }
    card.classList.toggle('fx-scan--done', s.state === 'done');
  }

  function renderFindings(card, s) {
    const box = card.querySelector('.fx-findings');
    box.textContent = '';
    const items = (s.categories || []).flatMap((c) => c.items);
    items.sort((a, b) => (a.level === 'threat' ? 0 : 1) - (b.level === 'threat' ? 0 : 1));
    items.forEach((item) => {
      const row = el('div', 'fx-finding fx-finding--' + item.level);
      const text = el('div', 'fx-finding__text');
      text.append(el('b', null, item.title), el('span', null, item.detail));
      if (item.path) text.appendChild(el('code', null, item.path));
      row.appendChild(text);
      const fixable = item.kind && item.kind !== 'info';
      if (fixable && !item.fixed) {
        row.appendChild(button(item.kind === 'autorun' ? 'Отключить' : 'В карантин', item.level === 'threat' ? 'danger' : '', async (event) => {
          const btn = event.currentTarget;
          btn.disabled = true;
          btn.textContent = '…';
          const done = await hub('/hub/scan/fix', { id: item.id }, 30000).catch(() => ({ ok: false, error: HUB_DOWN }));
          if (done.ok) {
            btn.replaceWith(el('span', 'fx-finding__ok', 'обезврежено'));
            CloudChat.toast(done.done, 'ok');
          } else {
            btn.disabled = false;
            btn.textContent = 'Повторить';
            CloudChat.toast(done.error, 'err');
          }
        }));
      }
      box.appendChild(row);
    });
  }

  async function runScan(text) {
    CloudChat.user(text);
    let started;
    try {
      started = await hub('/hub/scan/start', {});
    } catch {
      await CloudChat.ai(HUB_DOWN);
      return;
    }
    const card = buildScanCard();
    renderScan(card, started);
    const node = await CloudChat.ai('Проверяю компьютер: вирусы, шпионские программы, скрытые майнеры, автозагрузку, подмену сайтов и сетевые подключения. Это займёт около минуты — можно продолжать работать.', card);

    const tick = async () => {
      let s;
      try { s = await hub('/hub/scan/status'); } catch { setTimeout(tick, 1500); return; }
      renderScan(card, s);
      if (s.state === 'running') { setTimeout(tick, 400); return; }
      renderFindings(card, s);
      CloudChat.update(node, s.summary || 'Проверка завершена.');
      if (window.Voice) Voice.speak(s.summary || 'Проверка завершена.');
      CloudChat.scroll();
    };
    setTimeout(tick, 400);
  }

  /* =============================================================== перевод == */

  const TRANSLATE_RE = /(как|где|можешь|умеешь)\s.*перев[оеё]д|переведи\s+(с\s+)?экран|ctrl\s*\+?\s*alt\s*\+?\s*t/i;

  async function explainTranslate(text) {
    CloudChat.user(text);
    const card = el('div', 'fx-card fx-keys');
    ['Ctrl', '+', 'Alt', '+', 'T'].forEach((k) => card.appendChild(el(k === '+' ? 'span' : 'kbd', k === '+' ? 'fx-plus' : null, k)));
    await CloudChat.ai('Нажмите Ctrl + Alt + T в любой программе и обведите мышью английский текст — перевод на русский появится прямо поверх экрана. ' +
      'Работает офлайн: и распознавание букв, и перевод идут на этом компьютере. Подходит для статей, инструкций в PDF, текстов ошибок и меню программ.', card);
    hub('/hub/translate/warm', {}).catch(() => {});
  }

  /* ============================================================== телефон == */

  const PHONE_RE = /(подключи|привяжи|соедини|добавь)\s.*телефон|управлени[а-яё]*\s+с\s+телефона|^телефон$/i;

  // Служба телефона — отдельная, она единственная видна в домашней сети
  // (python/cloudhdr_phone.py). Окно приложения говорит с ней по 127.0.0.1.
  const PHONE = 'http://127.0.0.1:4481';

  async function phone(path, body) {
    const response = await fetch(PHONE + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    return response.json();
  }

  function fillPair(card, info) {
    card.querySelector('.fx-qr').innerHTML = info.svg;
    card.querySelector('.fx-pair__addr').textContent = info.address;
    const state = card.querySelector('.fx-pair__state');
    // Без правила брандмауэра телефон до компьютера не достучится — и раньше
    // никто об этом не говорил: страница на телефоне просто не открывалась.
    const blocked = info.firewall === false && !info.phone;
    state.textContent = info.phone ? 'Телефон подключён — можно отойти от компьютера'
      : blocked ? 'Брандмауэр Windows не пустит телефон — нужно разрешить один раз' : 'Ждёт телефон…';
    card.classList.toggle('fx-pair--on', Boolean(info.phone));
    card.classList.toggle('fx-pair--blocked', blocked);
    const allow = card.querySelector('.fx-pair__allow');
    if (allow) allow.hidden = !blocked;
  }

  async function connectPhone(text) {
    CloudChat.user(text);
    let info;
    try { info = await phone('/pair'); } catch {
      await CloudChat.ai('Служба телефона не отвечает. Перезапустите Cloud HDR — она поднимается вместе с приложением.');
      return;
    }
    const card = el('div', 'fx-card fx-pair');
    card.innerHTML = '<div class="fx-qr"></div><div class="fx-pair__side"><b>Наведите камеру телефона на код</b>' +
      '<span>Телефон и компьютер должны быть в одной сети Wi-Fi. Откроется страница «Мой ПК» — с неё можно давать задачи.</span>' +
      '<span class="fx-pair__state"></span><code class="fx-pair__addr"></code></div>';
    fillPair(card, info);
    const actions = el('div', 'fx-actions');
    const allow = button('Разрешить в брандмауэре', 'primary', async () => {
      allow.disabled = true;
      allow.textContent = 'Подтвердите запрос Windows…';
      try {
        const done = await phone('/pair/firewall', {});
        if (done.firewall) CloudChat.toast('Готово — телефон теперь увидит компьютер', 'ok');
        else CloudChat.toast('Windows не дала создать правило — без прав администратора не выйдет', 'err');
        fillPair(card, await phone('/pair'));
      } catch { /* служба перезапускается */ }
      allow.disabled = false;
      allow.textContent = 'Разрешить в брандмауэре';
    });
    allow.classList.add('fx-pair__allow');
    allow.hidden = true;
    actions.appendChild(allow);
    actions.appendChild(button('Сбросить ключ', 'ghost', async () => {
      if (!confirm('Сбросить ключ? Все уже подключённые телефоны потеряют доступ, их нужно будет подключить заново.')) return;
      fillPair(card, await phone('/pair/reset', {}));
      CloudChat.toast('Ключ сброшен — отсканируйте новый код', 'ok');
    }));
    card.appendChild(actions);
    fillPair(card, info);   // ещё раз: теперь есть кнопка «Разрешить», и её видно сразу
    await CloudChat.ai('Отсканируйте QR-код камерой телефона — и занимайтесь своими делами: задачи компьютеру можно давать прямо с телефона. ' +
      'Доступ есть только у того, кто отсканировал этот код, и только из вашей домашней сети.', card);

    const watch = setInterval(async () => {
      if (!document.body.contains(card)) return clearInterval(watch);
      try { fillPair(card, await phone('/pair')); } catch { /* служба перезапускается */ }
    }, 2000);
  }

  /*
     Задачи с телефона. Окно забирает их каждые полторы секунды и выполняет
     ровно тем же путём, что набранные руками, — поэтому с телефона работает
     всё: команды, файлы, защита, разговор с моделью. Ответ уходит обратно.
  */
  const phoneQueue = [];
  let phoneWorking = false;

  async function phoneTick() {
    try {
      const data = await phone('/desk/next');
      (data.tasks || []).forEach((task) => phoneQueue.push(task));
    } catch { /* служба телефона не запущена — это нормально */ }
    if (phoneWorking || !phoneQueue.length || !window.CloudChat) return;
    phoneWorking = true;
    try {
      while (phoneQueue.length) {
        const task = phoneQueue.shift();
        while (CloudChat.busy) await new Promise((r) => setTimeout(r, 300));
        CloudChat.toast('Задача с телефона: «' + task.text + '»', 'info');
        const before = CloudChat.lastAi();
        await CloudChat.send(task.text);
        while (CloudChat.busy) await new Promise((r) => setTimeout(r, 300));
        let answer = CloudChat.lastAi();
        const long = /^Проверяю компьютер/.test(answer);
        await phone('/desk/reply', { id: task.id, text: answer && answer !== before ? answer : 'Готово.', done: !long });
        // Долгие дела (проверка на угрозы) заканчиваются позже, чем ответ:
        // ждём итога и досылаем его на телефон.
        if (long) {
          for (let i = 0; i < 240 && CloudChat.lastAi() === answer; i++) await new Promise((r) => setTimeout(r, 500));
          answer = CloudChat.lastAi();
          await phone('/desk/reply', { id: task.id, text: answer, done: true });
        }
      }
    } catch { /* следующая попытка через полторы секунды */ }
    phoneWorking = false;
  }
  setInterval(phoneTick, 1500);

  // Презентация: «Cloud HDR сам предложит подключить телефон». Один раз.
  setTimeout(() => {
    try {
      if (localStorage.getItem('cloudhdr.phoneHint')) return;
      localStorage.setItem('cloudhdr.phoneHint', '1');
    } catch { return; }
    if (window.CloudChat) CloudChat.toast('Хотите управлять компьютером с телефона? Скажите «подключи телефон» — покажу QR-код.', 'info');
  }, 25000);

  /* ================================================================ видео == */

  // «открой и включи фильм про Бэтмена», «включи сериал Друзья», «поставь
  // мультик Шрек», «найди видео как приготовить плов». Ищет Rutube, выбирает
  // полный фильм в лучшем качестве; нет на Rutube — открывает поиск VK Видео.
  const VIDEO_RE = /^(?:пожалуйста\s+)?(?:открой\s+и\s+)?(?:включи|поставь|запусти|найди|покажи|открой)\s+(?:мне\s+)?(?:какой-нибудь\s+|какое-нибудь\s+)?(?:фильм|кино|сериал|мультфильм|мультик|видео|ролик|клип)/i;

  async function playVideo(text) {
    CloudChat.user(text);
    CloudChat.setBusy(true);
    let found;
    try {
      found = await hub('/hub/video/play', { text }, 45000);
    } catch {
      found = { ok: false, error: HUB_DOWN };
    }
    CloudChat.setBusy(false);
    if (!found.ok) { await CloudChat.ai(found.error); return; }

    if (found.source === 'vk') {
      await CloudChat.ai(`На Rutube подходящего не нашлось — открыл поиск VK Видео по запросу «${found.query}». Выберите вариант там.`);
      return;
    }

    const card = el('div', 'fx-card fx-video');
    const head = el('div', 'fx-video__head');
    if (found.thumb) {
      const img = el('img', 'fx-video__thumb');
      img.src = found.thumb; img.alt = '';
      head.appendChild(img);
    }
    const info = el('div', 'fx-video__info');
    info.append(el('b', null, found.title));
    const meta = el('div', 'fx-groups');
    [found.quality, `${found.minutes} мин`, found.author].filter(Boolean).forEach((m) => meta.appendChild(el('span', 'fx-group', m)));
    info.appendChild(meta);
    head.appendChild(info);
    card.appendChild(head);

    if (found.alternatives && found.alternatives.length) {
      card.appendChild(el('p', 'fx-note', 'Не то? Другие варианты:'));
      const list = el('div', 'fx-actions');
      found.alternatives.forEach((alt) => {
        list.appendChild(button(`${alt.title.replace(/\s*\|.*$/, '')} · ${alt.quality}`, 'ghost', async () => {
          await hub('/hub/video/open', { url: alt.url }).catch(() => null);
          CloudChat.toast('Открываю: ' + alt.title, 'ok');
        }));
      });
      list.appendChild(button('Искать в VK Видео', 'ghost', () => hub('/hub/video/open', { url: found.vk }).catch(() => null)));
      card.appendChild(list);
    }

    const what = /сериал/i.test(text) ? 'серию' : /мульт/i.test(text) ? 'мультфильм' : /фильм|кино/i.test(text) ? 'фильм' : 'видео';
    await CloudChat.ai(`Включаю ${what} «${found.title.replace(/\s*\|.*$/, '')}» на Rutube — выбрал лучшее качество, ${found.quality}. Приятного просмотра!`, card);
  }

  /* ============================================================= система == */

  // Грубый отбор: только фразы со словами о системе идут в службу, а она уже
  // точно решает, её ли это просьба. «Выключи звук» она не узнает — и фраза
  // пойдёт обычным путём, к агенту, как раньше.
  const SYS_HINT = /(выключи|отключи|перезагрузи|усни|спящ|(?<![а-яё])сон(?![а-яё])|вай[\s-]?фай|wi[\s-]?fi|блютуз|bluetooth|тёмн\S*\s+тем|темн\S*\s+тем|светл\S*\s+тем|ночн|яркост|питани|производительн|энергосбереж|мест\S*\s+на\s|диск|свободн|грузит|нагружает|загруж|нагруз|процессор|(?<![а-яё])цп(?![а-яё])|тормоз|тупит|лагает|виснет|греется|перегрев|памят|оперативк|(?<![а-яё])озу(?![а-яё])|температур|заряд|батаре|аккумулятор|отмен\S*\s+(выключ|перезагр)|не\s+(выключай|перезагружай)|^(ну\s+|а\s+)*(как|что|все|всё|в\s+каком|состояни)[^?]*?(пк|комп|ноут|систем|желез))/i;

  async function systemRequest(text) {
    let answer;
    try { answer = await hub('/hub/sys', { text }, 40000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);

    if (answer.confirm) {
      const card = el('div', 'fx-card');
      const actions = el('div', 'fx-actions');
      card.appendChild(actions);
      const node = await CloudChat.ai(`${answer.title} ${answer.say}`, card);
      const verbs = { shutdown: 'Выключить', restart: 'Перезагрузить', sleep: 'В сон' };
      actions.append(
        button(verbs[answer.action] || 'Да', 'danger', async () => {
          actions.textContent = '';
          const done = await hub('/hub/sys', { action: answer.action, confirm: true }).catch(() => ({ say: HUB_DOWN }));
          CloudChat.update(node, done.say);
          if (window.Voice) Voice.speak(done.say);
        }),
        button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, ничего не трогаю.'); })
      );
      return true;
    }
    await CloudChat.ai(answer.say);
    return true;
  }

  /* ================================================ медиа, буфер, напоминания == */

  // Как и с системой: грубый отбор здесь, точное решение — в службе. Не узнала —
  // фраза идёт дальше обычным путём («следующий шаг», «напомни мой ник»).
  const MEDIA_HINT = /(пауз|трек|песн|музык|композици|играет|звучит|^некст$|^next$|^skip$|^плей$|^play$|^следующ\S*$|^продолжи$|^возобнови$)/i;
  const CLIP_HINT = /(буфер|скопировал)/i;
  const REMIND_HINT = /(напомн|напоминани|таймер|засеки|отсчитай|разбуди|будильник|пикни)/i;

  function textCard(text) {
    const card = el('div', 'fx-card');
    card.appendChild(el('pre', 'fx-clip', text));
    return card;
  }

  async function mediaRequest(text) {
    let answer;
    try { answer = await hub('/hub/media', { text }, 30000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);
    await CloudChat.ai(answer.say);
    return true;
  }

  async function clipRequest(text) {
    let answer;
    try { answer = await hub('/hub/clip', { text }, 60000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);
    if (answer.speak) {
      await CloudChat.ai('Читаю вслух то, что в буфере.', textCard(answer.say));
      if (window.Voice) Voice.speak(answer.say);
      return true;
    }
    await CloudChat.ai(answer.say, answer.text ? textCard(answer.text) : undefined);
    return true;
  }

  async function remindRequest(text) {
    let answer;
    try { answer = await hub('/hub/remind', { text }); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);
    await CloudChat.ai(answer.say, answer.text ? textCard(answer.text) : undefined);
    if (answer.due) CloudChat.toast('Напоминание поставлено', 'ok');
    return true;
  }

  /*
     Сработавшие напоминания. Уведомление Windows служба показывает сама — даже
     при закрытом окне. Если окно открыто, напоминание ещё и появляется в чате
     и звучит голосом. Старые (окно было закрыто дольше десяти минут) в чат не
     выводим: о них уже сказало уведомление, а внезапная реплика про вчерашний
     чайник только сбивает.
  */
  async function remindTick() {
    if (!window.CloudChat) return;
    let data;
    try { data = await hub('/hub/remind/fired', undefined, 4000); } catch { return; }
    for (const item of (data && data.items) || []) {
      if (Date.now() / 1000 - item.due > 600) continue;
      const say = item.timer
        ? (item.what ? `Таймер: ${item.what}` : 'Время вышло — таймер отработал.')
        : `Напоминаю: ${item.what || 'вы просили напомнить.'}`;
      CloudChat.toast('⏰ ' + say, 'ok');
      await CloudChat.ai(say);
      if (window.Voice) Voice.speak(say);
    }
  }
  setInterval(remindTick, 3000);

  /* ================================= программы, окна, автозагрузка (группа 3) == */

  // Грубый отбор, как у системы: точно решает служба (python/cloudhdr_apps.py).
  const APPS_HINT = /(установи|инсталлируй|поставь\s+(программ|приложени)|скачай\s+(и\s+установи|программ)|обнови|удали\s+(программ|приложени|игру)|деинсталлируй|снеси\s+(программ|приложени|игру)|окн[аоы]|открыто|запущено|сверни|разверни|раскрой|весь\s+экран|переключись|переключи\s+на|перейди\s+(на|в)|выведи|подними|слева|справа|налево|направо|влево|вправо|раздели\s+экран|монитор|перезапусти|перезагрузи|перезапуск|завис|не\s+отвечает|автозагрузк|автозапуск|рабочий\s+стол|запуска\S*\s+(вместе\s+)?с\s+(windows|виндовс|компьютером|системой)|вкладк|^назад$|^вперед$|^вперёд$|прокрути|листай|пролистай|обнови\s+(эту\s+)?страниц|закрой\s+(это|окно)|^сверни$|весь\s+экран)/i;

  const APPS_VERBS = { install: 'Установить', uninstall: 'Удалить', upgrade_all: 'Обновить всё', hung_kill: 'Закрыть зависшие' };

  function jobCard() {
    const card = el('div', 'fx-job');
    card.innerHTML = '<div class="fx-job__bar"><i></i></div><span class="fx-note fx-job__stage">Готовлюсь…</span>';
    return card;
  }

  /** Следит за фоновой задачей winget и дописывает итог в тот же ответ. */
  function watchJob(node, card) {
    const bar = card.querySelector('.fx-job__bar i');
    const stage = card.querySelector('.fx-job__stage');
    const tick = async () => {
      let s;
      try { s = await hub('/hub/apps/job', undefined, 5000); } catch { setTimeout(tick, 2000); return; }
      bar.style.width = Math.round((s.progress || 0) * 100) + '%';
      stage.textContent = s.stage || '';
      if (s.state === 'running') { setTimeout(tick, 700); return; }
      card.classList.add(s.state === 'done' ? 'fx-job--done' : 'fx-job--err');
      stage.textContent = s.state === 'done' ? 'Готово' : 'Не вышло';
      CloudChat.update(node, s.say || 'Готово.');
      CloudChat.toast(s.say || 'Готово', s.state === 'done' ? 'ok' : 'err');
      if (window.Voice) Voice.speak(s.say || 'Готово.');
    };
    setTimeout(tick, 600);
  }

  async function runApps(node, actions, kind, pkg, label) {
    actions.textContent = '';
    const done = await hub('/hub/apps/run', { kind, package: pkg }, 30000).catch(() => ({ ok: false, error: HUB_DOWN }));
    if (!done.ok) { CloudChat.update(node, done.error || done.say || 'Не получилось.'); return; }
    if (!done.job) { CloudChat.update(node, done.say || 'Готово.'); return; }   // закрыть зависшие — сразу
    CloudChat.update(node, label);
    const card = jobCard();
    actions.parentNode.insertBefore(card, actions);
    watchJob(node, card);
  }

  async function appsRequest(text) {
    let answer;
    try { answer = await hub('/hub/apps', { text }, 180000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);

    const card = el('div', 'fx-card');
    if (answer.text) card.appendChild(el('pre', 'fx-clip', answer.text));
    const actions = el('div', 'fx-actions');
    card.appendChild(actions);

    if (answer.confirm === 'install' && answer.packages) {
      answer.packages.forEach((pkg, i) => {
        const label = `${pkg.name}${pkg.version && pkg.version !== 'Unknown' ? ' · ' + pkg.version : ''}`;
        actions.appendChild(button(label, i === 0 ? 'primary' : 'ghost', () => runApps(node, actions, 'install', pkg, `Устанавливаю ${pkg.name}…`)));
      });
      actions.appendChild(button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, ничего не ставлю.'); }));
    } else if (answer.confirm === 'uninstall' && answer.package) {
      actions.append(
        button('Удалить', 'danger', () => runApps(node, actions, 'uninstall', answer.package, `Удаляю ${answer.package.name}…`)),
        button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, оставляю как есть.'); }));
    } else if (answer.confirm) {
      const kind = answer.confirm;
      actions.append(
        button(APPS_VERBS[kind] || 'Да', kind === 'hung_kill' ? 'danger' : 'primary',
          () => runApps(node, actions, kind, null, kind === 'upgrade_all' ? 'Обновляю программы — это может занять несколько минут…' : '…')),
        button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, не трогаю.'); }));
    }
    const node = await CloudChat.ai(answer.say, answer.text || answer.job || actions.childNodes.length ? card : undefined);
    if (answer.job) {
      const jc = jobCard();
      card.insertBefore(jc, actions);
      watchJob(node, jc);
    }
    return true;
  }

  /* ===================================================== файлы (группа 4) == */

  // Удаление — только в корзину и только кнопкой; всё остальное с «Вернуть».
  const FILES_HINT = /(удали|сотри|выкинь|выброси|корзин|переименуй|перемести|перенеси|переложи|скопируй|копируй|создай\s+(новую\s+)?папку|сделай\s+(новую\s+)?папку|скача|загрузк|занима\S*\s+(\S+\s+){0,3}мест|самые\s+(большие|тяж)|делось\s+место|ушло\s+место|забит|весит|дубл|дубликат|одинаков\S*\s+файл|сожми|заархивируй|упакуй|запакуй|распакуй|разархивируй|извлеки|открой\s+(мне\s+)?(файл|документ|фото|картинк|таблиц|презентаци|pdf|пдф|папку)|где\s+(лежит|находится|хранится|сохранился)|покажи\s+(в\s+папке|где))/i;

  async function filesAct(payload) {
    return hub('/hub/fs/act', payload, 120000).catch(() => ({ ok: false, say: HUB_DOWN }));
  }

  function undoButton(getNode, actions, id) {
    return button('Вернуть', 'ghost', async () => {
      actions.textContent = '';
      const back = await filesAct({ kind: 'undo', id });
      CloudChat.update(getNode(), back.say);
    });
  }

  function fileRow(file, offer, getNode, answer) {
    const row = el('div', 'fx-file');
    const text = el('div', 'fx-file__text');
    text.append(el('b', null, file.name), el('span', null, [file.where, file.size].filter(Boolean).join(' · ')));
    row.appendChild(text);
    const side = el('div', 'fx-file__actions');
    const done = (say, ok) => { side.textContent = ''; side.appendChild(el('span', ok ? 'fx-finding__ok' : 'fx-note', say)); };
    if (offer === 'bin') {
      side.appendChild(button('В корзину', 'danger', async () => {
        const r = await filesAct({ kind: 'bin', path: file.path });
        done(r.ok ? 'в корзине' : 'не вышло', r.ok);
        CloudChat.toast(r.say, r.ok ? 'ok' : 'err');
      }));
    } else if (offer === 'open') {
      side.append(
        button('Открыть', 'ghost', () => filesAct({ kind: 'open', path: file.path })),
        button('Показать', 'ghost', () => filesAct({ kind: 'reveal', path: file.path })));
    } else {
      side.appendChild(button('Этот', 'primary', async () => {
        const r = await filesAct({ kind: offer, path: file.path, arg: answer.arg || '', copy: Boolean(answer.copy) });
        const card = side.closest('.fx-card');
        CloudChat.update(getNode(), r.say);
        card.querySelectorAll('.fx-file__actions').forEach((box) => { box.textContent = ''; });
        if (r.undo) card.appendChild(undoButton(getNode, card, r.undo));
      }));
    }
    row.appendChild(side);
    return row;
  }

  async function filesRequest(text) {
    let answer;
    try { answer = await hub('/hub/fs', { text }, 60000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);

    const card = el('div', 'fx-card');
    let node = null;
    if (answer.text) card.appendChild(el('pre', 'fx-clip', answer.text));
    if (answer.note) card.appendChild(el('p', 'fx-note', answer.note));
    (answer.files || []).forEach((file) => card.appendChild(fileRow(file, answer.offer, () => node, answer)));
    const actions = el('div', 'fx-actions');
    card.appendChild(actions);

    if (answer.confirm === 'bin_empty') {
      actions.append(
        button('Очистить корзину', 'danger', async () => {
          actions.textContent = '';
          const r = await filesAct({ kind: 'bin_empty' });
          CloudChat.update(node, r.say);
        }),
        button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, корзину не трогаю.'); }));
    } else if (answer.confirm === 'bin_many') {
      actions.append(
        button(`Убрать ${answer.count} ${plural(answer.count, 'копию', 'копии', 'копий')} в корзину`, 'danger', async () => {
          actions.textContent = '';
          const r = await filesAct({ kind: 'bin', paths: answer.paths });
          CloudChat.update(node, r.say);
        }),
        button('Не надо', 'ghost', () => { actions.textContent = ''; CloudChat.update(node, 'Хорошо, копии остаются.'); }));
    } else if (answer.undo) {
      actions.appendChild(undoButton(() => node, actions, answer.undo));
    }
    const hasCard = answer.text || answer.note || (answer.files || []).length || actions.childNodes.length;
    node = await CloudChat.ai(answer.say, hasCard ? card : undefined);
    return true;
  }

  /* ======================== управление компьютером (группа 5, cloudhdr_control) == */

  // Грубый отбор: точно решает служба. Фразы о звуке, настройках, режимах,
  // сети, очистке, обновлениях, pdf и печати.
  const CTL_HINT = /(настройк|параметры\s|звук\s+(на|в|через)\s|вывод\S*\s+звук|устройств\S*\s+звук|куда\s+(идет|идёт|выводится)\s+звук|микрофон|не\s+беспокоить|уведомлени|оповещени|режим|сценари|(^|\s)(ip|айпи)(\s|$)|вай\s?фай|wi-?fi|сет[иь](\s|$)|скорост\S*\s+(\S+\s+)?(интернет|инет|сети|соединени)|спидтест|пинг|почисти|очисти|вычисти|мусор|временн\S*\s+файл|кэш|кеш|обновлени\S*\s+(windows|виндовс|системы)|обнови\s+(windows|виндовс|систему)|драйвер|pdf|пдф|сконверт|конверт|сожми|уменьши\s+(фото|картин)|распечатай|напечатай|на\s+печать)/i;

  async function ctlRun(payload) {
    return hub('/hub/ctl/run', payload, 180000).catch(() => ({ ok: false, say: HUB_DOWN }));
  }

  async function ctlRequest(text) {
    let answer;
    try { answer = await hub('/hub/ctl', { text }, 60000); } catch { return false; }
    if (!answer || !answer.known) return false;
    CloudChat.user(text);

    const card = el('div', 'fx-card');
    if (answer.text) card.appendChild(el('pre', 'fx-clip', answer.text));
    const actions = el('div', 'fx-actions');
    card.appendChild(actions);
    let node = null;
    const done = (say) => { actions.textContent = ''; CloudChat.update(node, say); if (window.Voice) Voice.speak(say); };

    (answer.choices || []).forEach((choice, i) => {
      actions.appendChild(button(choice.label, i === 0 ? 'primary' : 'ghost', async () => {
        actions.textContent = '';
        const r = await ctlRun({ kind: choice.kind, id: choice.id });
        done(r.say);
      }));
    });
    if (answer.confirm === 'clean') {
      actions.append(
        button('Удалить мусор', 'danger', async () => {
          actions.textContent = '';
          CloudChat.update(node, 'Чищу…');
          done((await ctlRun({ kind: 'clean' })).say);
        }),
        button('Не надо', 'ghost', () => done('Хорошо, ничего не удаляю.')));
    } else if (answer.confirm === 'print') {
      actions.append(
        button('Печатать', 'primary', async () => done((await ctlRun({ kind: 'print', paths: answer.paths })).say)),
        button('Не надо', 'ghost', () => done('Хорошо, не печатаю.')));
    }
    if (answer.reveal) {
      actions.appendChild(button('Показать файл', 'ghost', () => hub('/hub/ctl/reveal', { path: answer.reveal }).catch(() => {})));
    }

    const hasCard = answer.text || actions.childNodes.length;
    node = await CloudChat.ai(answer.say, hasCard ? card : undefined);

    // Режим — это цепочка обычных команд: каждая идёт своим проверенным путём.
    for (const command of answer.run || []) {
      while (CloudChat.busy) await new Promise((r) => setTimeout(r, 150));
      await CloudChat.send(command);
    }
    return true;
  }

  /* ============================================================ перехват == */

  const ROUTES = [
    // первым: «не беспокоить на час» иначе забрали бы напоминания,
    // «обнови драйвер» — программы; служба сама уступает чужие фразы
    [CTL_HINT, ctlRequest],
    [REMIND_HINT, remindRequest],
    [CLIP_HINT, clipRequest],
    [MEDIA_HINT, mediaRequest],
    [SYS_HINT, systemRequest],
    [VIDEO_RE, playVideo],
    [APPS_HINT, appsRequest],
    [FILES_HINT, filesRequest],
    [UNDO_RE, undoSort],
    [SORT_RE, sortFolder],
    [SCAN_RE, runScan],
    [TRANSLATE_RE, explainTranslate],
    [PHONE_RE, connectPhone]
  ];

  window.Features = {
    /** true — фраза узнана и обработана здесь. */
    async intercept(text) {
      const clean = String(text || '').trim();
      for (const [pattern, handler] of ROUTES) {
        if (pattern.test(clean)) {
          // Обработчик может вернуть false — «это не моё» (так делает
          // системный: он спрашивает службу и уступает, если та не узнала).
          if ((await handler(clean)) === false) continue;
          return true;
        }
      }
      return false;
    }
  };

  /*
     Рельса слева: по кнопке «История» панель раскрывается поверх чата — с
     поиском, списком разговоров и настройками. Закрывается щелчком мимо,
     Escape или выбором разговора.
  */
  const setDrawer = (open) => document.body.classList.toggle('drawer-open', open);
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!target.closest) return;
    if (target.closest('#railHistory')) { setDrawer(!document.body.classList.contains('drawer-open')); return; }
    if (!document.body.classList.contains('drawer-open')) return;
    if (!target.closest('#sidebar') || target.closest('.hitem, #newChat, .rail__btn[data-prompt]')) setDrawer(false);
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setDrawer(false); });
})();
