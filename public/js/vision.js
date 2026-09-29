/* ============================================================================
   Cloud HDR — зрение
   ----------------------------------------------------------------------------
   Разбор фотографий и картинок, которые человек присылает в чат. Целиком на
   этом компьютере: ни одного байта наружу, ни одного ключа, ни одного чужого
   сервиса. Картинка декодируется браузером, считается здесь, и здесь же
   превращается в слова.

   Что именно тут происходит и чего тут НЕТ — сказать честно важнее, чем
   красиво. Здесь нет узнавания предметов («на фото кот породы мейн-кун»): для
   этого нужна сеть, обученная на миллионах размеченных снимков, а её пришлось
   бы скачать чужую и готовую — то самое, чего проект не делает принципиально.

   Зато здесь есть то, что можно ИЗМЕРИТЬ, и измерить точно:

     • свет      — гистограмма, экспозиция, пересветы, провалы, перепад в ступенях;
     • цвет      — палитра, насыщенность, уклон баланса белого, температура;
     • резкость  — акутанс, шум, следы пережатия, направление смаза;
     • кадр      — где главный объект, завален ли горизонт, правило третей;
     • съёмка    — камера, объектив, выдержка, ISO, дата и место из EXIF.

   А род картинки (фотография, снимок экрана, рисунок, документ, график)
   определяет маленький классификатор — свой, обучаемый прямо здесь. Он
   стартует с весов, выставленных по смыслу признаков, и доучивается на
   поправках хозяина: сказали «это не скриншот, а фотография» — запомнил и
   пересчитал веса. Это честная своя сеть: маленькая, локальная, обученная на
   том, что видел этот человек.

   Роли в приложении при этом не меняются:
     • разбор команд решает, ЧТО сделать с Windows;
     • языковая модель отвечает словами;
     • зрение отвечает на вопрос «что это за картинка и что с ней не так»,
       а её измерения кладутся модели в запрос как факты — чтобы та говорила
       по делу, а не сочиняла про снимок, которого не видела.
   ========================================================================== */
(function (global) {
  'use strict';

  /* ==================================================== 1. Общие мелочи === */

  /** Рода картинок. Порядок ключей — это порядок классов в сети, менять нельзя. */
  const KINDS = {
    photo:        { title: 'фотография',           icon: 'camera' },
    screenshot:   { title: 'снимок экрана',        icon: 'app' },
    illustration: { title: 'рисунок или рендер',   icon: 'brush' },
    document:     { title: 'документ или скан',    icon: 'note' },
    chart:        { title: 'схема или график',     icon: 'calc' },
    graphic:      { title: 'графика с прозрачностью', icon: 'spark' }
  };
  const CLASSES = Object.keys(KINDS);

  const LIMITS = {
    // Сторона рабочего холста. Все пороги в этом файле подобраны ИМЕННО под
    // это число: акутанс и плотность краёв зависят от масштаба, и считать их
    // на исходных 4000 точках значило бы получать у каждой камеры свою шкалу.
    work: 512,
    // Кусок в натуральную величину — по нему видно настоящую резкость и шум,
    // которые уменьшение затирает начисто.
    native: 320,
    // Миниатюра ложится в историю чата, а история живёт в localStorage. Отсюда
    // и скромный размер: 220 точек — это 10-20 КБ, полсотни картинок в запасе.
    thumb: 220,
    bytes: 48 * 1024 * 1024
  };

  const TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp', 'image/avif'];

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const round = (value, digits = 1) => {
    const factor = Math.pow(10, digits);
    return Math.round(value * factor) / factor;
  };
  const pct = (share, digits = 0) => round(share * 100, digits) + '%';

  function bytes(size) {
    if (!(size > 0)) return '—';
    if (size < 1024) return size + ' Б';
    if (size < 1024 * 1024) return round(size / 1024, 0) + ' КБ';
    return round(size / 1048576, 1) + ' МБ';
  }

  /** «12,2 Мп» — мегапиксели с запятой, как принято в русском тексте. */
  function ru(value, digits = 1) {
    return String(round(value, digits)).replace('.', ',');
  }

  const uid = () => 'v' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  /* ============================== 2. Приём картинки ======================= */

  /*
     Поднос вложений. Держит то, что человек уже принёс, но ещё не отправил:
     выбранное кнопкой, перетащенное в окно, вставленное из буфера обмена.

     Живёт отдельно от чата намеренно. Картинку часто приносят раньше вопроса —
     сначала перетащил, потом задумался, что спросить, — и до нажатия «отправить»
     она не должна становиться сообщением.
  */
  const tray = [];

  function accepts(file) {
    if (!file) return false;
    const type = String(file.type || '').toLowerCase();
    if (type.startsWith('image/')) return TYPES.includes(type) || type === 'image/jpg';
    return /\.(jpe?g|png|webp|gif|bmp|avif)$/i.test(file.name || '');
  }

  /**
   * Кладёт картинку на поднос: декодирует, считает миниатюру, запоминает байты.
   *
   * Байты сохраняются ЦЕЛИКОМ и не выбрасываются после декодирования: EXIF
   * лежит в них, а из готового растра его уже не достать — браузер отдаёт
   * только точки. Именно поэтому разбор съёмки (камера, выдержка, ISO)
   * возможен лишь для файла, а не для картинки, срисованной с экрана.
   */
  async function hold(file) {
    if (!accepts(file)) throw new Error('Это не картинка — я понимаю JPEG, PNG, WebP, GIF, BMP и AVIF');
    if (file.size > LIMITS.bytes) {
      throw new Error(`Картинка тяжелее ${bytes(LIMITS.bytes)} — такую я не возьму`);
    }

    const buffer = await file.arrayBuffer();
    const blob = file instanceof Blob ? file : new Blob([buffer], { type: file.type });
    const bitmap = await decode(blob);

    const item = {
      id: uid(),
      name: file.name || 'изображение',
      type: (file.type || sniff(buffer) || 'image/*').toLowerCase(),
      size: file.size || buffer.byteLength,
      width: bitmap.width,
      height: bitmap.height,
      buffer,
      bitmap,
      blob,
      url: URL.createObjectURL(blob),
      thumb: thumbnail(bitmap)
    };
    tray.push(item);
    return item;
  }

  function drop(id) {
    const index = tray.findIndex((item) => item.id === id);
    if (index < 0) return;
    release(tray[index]);
    tray.splice(index, 1);
  }

  function clear() {
    tray.splice(0).forEach(release);
  }

  /** Забирает всё с подноса — вызывается в момент отправки сообщения. */
  function take() {
    return tray.splice(0);
  }

  function release(item) {
    if (item && item.url) { try { URL.revokeObjectURL(item.url); } catch { /* уже отозван */ } }
    if (item && item.bitmap && item.bitmap.close) { try { item.bitmap.close(); } catch { /* не ImageBitmap */ } }
  }

  /* ========================= 3. Декодирование и растр ===================== */

  async function decode(blob) {
    if (global.createImageBitmap) {
      // from-image — это разворот кадра по метке EXIF силами браузера. Без него
      // снимки с телефона, снятые вертикально, разбирались бы лежащими на боку:
      // и «горизонт завален», и «объект слева» оказались бы про повёрнутый кадр.
      try { return await createImageBitmap(blob, { imageOrientation: 'from-image' }); }
      catch { /* старая сборка без этой опции — падаем на обычный путь */ }
      try { return await createImageBitmap(blob); }
      catch { /* формат браузеру незнаком — пробуем через <img> */ }
    }

    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const image = new Image();
      image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Не смог раскодировать картинку')); };
      image.src = url;
    });
  }

  /** Рисует картинку в холст нужного размера и отдаёт точки. */
  function raster(bitmap, side) {
    const source = { w: bitmap.width || bitmap.naturalWidth, h: bitmap.height || bitmap.naturalHeight };
    const scale = Math.min(1, side / Math.max(source.w, source.h));
    const w = Math.max(1, Math.round(source.w * scale));
    const h = Math.max(1, Math.round(source.h * scale));

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap, 0, 0, w, h);

    return { w, h, data: ctx.getImageData(0, 0, w, h).data, canvas };
  }

  /**
   * Кусок из середины кадра в натуральную величину.
   *
   * Нужен ровно для двух замеров — резкости и шума, — и оба уменьшение убивает.
   * Сглаживание при масштабировании усредняет соседние точки: зерно исчезает,
   * мыло подтягивается. По уменьшенной копии любой снимок выглядит одинаково
   * приличным, и именно поэтому оба числа берутся отсюда, а не с рабочего холста.
   */
  function centerCrop(bitmap, side) {
    const sw = bitmap.width || bitmap.naturalWidth;
    const sh = bitmap.height || bitmap.naturalHeight;
    const w = Math.min(side, sw);
    const h = Math.min(side, sh);
    const sx = Math.round((sw - w) / 2);
    const sy = Math.round((sh - h) / 2);

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(bitmap, sx, sy, w, h, 0, 0, w, h);
    return { w, h, data: ctx.getImageData(0, 0, w, h).data, scaled: sw > side || sh > side };
  }

  function thumbnail(bitmap) {
    const view = raster(bitmap, LIMITS.thumb);
    // Белая подложка под прозрачность: JPEG альфы не знает, и без заливки
    // логотип с прозрачным фоном превратился бы в чёрный прямоугольник.
    const canvas = document.createElement('canvas');
    canvas.width = view.w;
    canvas.height = view.h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, view.w, view.h);
    ctx.drawImage(view.canvas, 0, 0);
    try { return canvas.toDataURL('image/jpeg', 0.72); } catch { return null; }
  }

  /** Яркость по Rec.709 — та, которую видит глаз, а не среднее трёх каналов. */
  function luminance(px) {
    const size = px.w * px.h;
    const lum = new Float32Array(size);
    for (let i = 0, p = 0; i < size; i++, p += 4) {
      lum[i] = 0.2126 * px.data[p] + 0.7152 * px.data[p + 1] + 0.0722 * px.data[p + 2];
    }
    return lum;
  }

  /** Гамма-снятие: доля света, а не число из файла. Нужно для ступеней и баланса. */
  function toLinear(value) {
    const v = value / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  }

  /* ================================ 4. Метаданные ======================== */

  function sniff(buffer) {
    const head = new Uint8Array(buffer, 0, Math.min(16, buffer.byteLength));
    if (head[0] === 0xFF && head[1] === 0xD8) return 'image/jpeg';
    if (head[0] === 0x89 && head[1] === 0x50) return 'image/png';
    if (head[0] === 0x47 && head[1] === 0x49) return 'image/gif';
    if (head[0] === 0x42 && head[1] === 0x4D) return 'image/bmp';
    if (head[8] === 0x57 && head[9] === 0x45) return 'image/webp';
    return null;
  }

  /*
     Разбор EXIF своими руками.

     Библиотеки для этого есть, и хорошие, но каждая — это чужой код в проекте,
     который держится на том, что чужого кода в нём нет. А формат простой:
     TIFF-заголовок, каталоги записей, у каждой номер, тип и значение. Ниже
     ровно столько, сколько нужно, чтобы прочитать паспорт кадра.
  */
  const EXIF_TAGS = {
    0x010F: 'make', 0x0110: 'model', 0x0112: 'orientation', 0x0131: 'software',
    0x8769: 'exifIfd', 0x8825: 'gpsIfd',
    0x829A: 'exposureTime', 0x829D: 'fNumber', 0x8827: 'iso', 0x8833: 'isoSpeed',
    0x9003: 'taken', 0x9004: 'digitized', 0x9204: 'exposureBias', 0x9207: 'metering',
    0x9209: 'flash', 0x920A: 'focal', 0xA002: 'pixelX', 0xA003: 'pixelY',
    0xA402: 'exposureMode', 0xA403: 'whiteBalance', 0xA405: 'focal35',
    0xA406: 'sceneType', 0xA434: 'lens', 0x8822: 'program'
  };

  const GPS_TAGS = { 1: 'latRef', 2: 'lat', 3: 'lonRef', 4: 'lon', 6: 'altitude' };

  const TYPE_SIZE = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8 };

  function readValue(view, entry, little) {
    const { type, count, offset } = entry;
    const size = TYPE_SIZE[type] || 1;
    const total = size * count;
    const at = total > 4 ? entry.pointer : offset;
    if (at + total > view.byteLength) return null;

    const out = [];
    for (let i = 0; i < count && i < 64; i++) {
      const p = at + i * size;
      switch (type) {
        case 1: case 7: out.push(view.getUint8(p)); break;
        case 2: out.push(String.fromCharCode(view.getUint8(p))); break;
        case 3: out.push(view.getUint16(p, little)); break;
        case 4: out.push(view.getUint32(p, little)); break;
        case 5: out.push(view.getUint32(p, little) / (view.getUint32(p + 4, little) || 1)); break;
        case 9: out.push(view.getInt32(p, little)); break;
        case 10: out.push(view.getInt32(p, little) / (view.getInt32(p + 4, little) || 1)); break;
        default: out.push(view.getUint8(p));
      }
    }
    if (type === 2) return out.join('').replace(/\0.*$/, '').trim();
    return count === 1 ? out[0] : out;
  }

  function readIfd(view, tiff, offset, little, names) {
    const found = {};
    if (offset + 2 > view.byteLength) return found;
    const count = view.getUint16(offset, little);
    for (let i = 0; i < count; i++) {
      const entry = offset + 2 + i * 12;
      if (entry + 12 > view.byteLength) break;
      const tag = view.getUint16(entry, little);
      const name = names[tag];
      if (!name) continue;
      const record = {
        type: view.getUint16(entry + 2, little),
        count: view.getUint32(entry + 4, little),
        offset: entry + 8,
        pointer: tiff + view.getUint32(entry + 8, little)
      };
      const value = readValue(view, record, little);
      if (value !== null && value !== '') found[name] = value;
    }
    return found;
  }

  function parseExif(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < 12) return null;
    if (view.getUint16(0) !== 0xFFD8) return null;         // не JPEG — EXIF там не живёт

    let offset = 2;
    let tiff = -1;
    while (offset + 4 < view.byteLength) {
      if (view.getUint8(offset) !== 0xFF) break;
      const marker = view.getUint8(offset + 1);
      const size = view.getUint16(offset + 2);
      if (marker === 0xE1 && offset + 10 < view.byteLength) {
        const tag = String.fromCharCode(view.getUint8(offset + 4), view.getUint8(offset + 5),
                                        view.getUint8(offset + 6), view.getUint8(offset + 7));
        if (tag === 'Exif') { tiff = offset + 10; break; }
      }
      if (marker === 0xDA) break;                          // пошли данные картинки
      offset += 2 + size;
    }
    if (tiff < 0 || tiff + 8 > view.byteLength) return null;

    const little = view.getUint16(tiff) === 0x4949;
    if (view.getUint16(tiff + 2, little) !== 0x2A) return null;

    const first = tiff + view.getUint32(tiff + 4, little);
    const zero = readIfd(view, tiff, first, little, EXIF_TAGS);
    const exif = zero.exifIfd ? readIfd(view, tiff, tiff + zero.exifIfd, little, EXIF_TAGS) : {};
    const gps = zero.gpsIfd ? readIfd(view, tiff, tiff + zero.gpsIfd, little, GPS_TAGS) : {};

    return { ...zero, ...exif, gps };
  }

  /** Текстовые куски PNG: там иногда лежит «чем нарисовано». */
  function parsePngText(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < 12 || view.getUint32(0) !== 0x89504E47) return null;

    const out = {};
    let offset = 8;
    let guard = 0;
    while (offset + 8 < view.byteLength && guard++ < 64) {
      const size = view.getUint32(offset);
      const type = String.fromCharCode(view.getUint8(offset + 4), view.getUint8(offset + 5),
                                       view.getUint8(offset + 6), view.getUint8(offset + 7));
      if (type === 'IDAT' || type === 'IEND') break;

      if ((type === 'tEXt' || type === 'iTXt') && size < 8192) {
        const raw = new Uint8Array(buffer, offset + 8, Math.min(size, view.byteLength - offset - 8));
        const text = new TextDecoder('utf-8', { fatal: false }).decode(raw);
        const stop = text.indexOf('\0');
        if (stop > 0) {
          const key = text.slice(0, stop);
          const body = text.slice(stop + 1).replace(/\0/g, ' ').trim();
          if (body) out[key] = body.slice(0, 400);
        }
      }
      if (type === 'IHDR' && size >= 13) {
        out['~bits'] = view.getUint8(offset + 16);
        out['~color'] = view.getUint8(offset + 17);
      }
      offset += 12 + size;
    }
    return Object.keys(out).length ? out : null;
  }

  /** Собирает из EXIF человеческий паспорт кадра. */
  function shotCard(exif) {
    if (!exif) return null;
    const card = {};

    const make = String(exif.make || '').trim();
    const model = String(exif.model || '').trim();
    if (model) card.camera = model.toLowerCase().startsWith(make.toLowerCase()) || !make ? model : `${make} ${model}`;
    if (exif.lens) card.lens = String(exif.lens).trim();
    if (exif.software) card.software = String(exif.software).trim();

    if (exif.exposureTime > 0) {
      card.exposure = exif.exposureTime >= 1
        ? `${ru(exif.exposureTime)} с`
        : `1/${Math.round(1 / exif.exposureTime)} с`;
      card.shutter = exif.exposureTime;
    }
    if (exif.fNumber > 0) card.aperture = 'f/' + ru(exif.fNumber);
    const iso = exif.iso || exif.isoSpeed;
    if (iso > 0) card.iso = Array.isArray(iso) ? iso[0] : iso;
    if (exif.focal > 0) {
      card.focal = Math.round(exif.focal) + ' мм';
      if (exif.focal35 > 0 && Math.abs(exif.focal35 - exif.focal) > 2) {
        card.focal += ` (${Math.round(exif.focal35)} мм в эквиваленте)`;
      }
    }
    if (typeof exif.flash === 'number') card.flash = (exif.flash & 1) === 1;
    if (exif.exposureBias) card.bias = (exif.exposureBias > 0 ? '+' : '') + ru(exif.exposureBias) + ' EV';

    const taken = String(exif.taken || exif.digitized || '');
    const parts = taken.match(/^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2})/);
    if (parts) card.date = `${parts[3]}.${parts[2]}.${parts[1]}, ${parts[4]}:${parts[5]}`;

    const gps = exif.gps || {};
    if (Array.isArray(gps.lat) && Array.isArray(gps.lon)) {
      const dms = (v, ref) => {
        const value = (v[0] || 0) + (v[1] || 0) / 60 + (v[2] || 0) / 3600;
        return /[SW]/i.test(String(ref || '')) ? -value : value;
      };
      card.gps = { lat: round(dms(gps.lat, gps.latRef), 5), lon: round(dms(gps.lon, gps.lonRef), 5) };
    }

    return Object.keys(card).length ? card : null;
  }

  /* =================================== 5. Свет =========================== */

  function readTone(px, lum) {
    const size = px.w * px.h;
    const hist = new Float64Array(256);
    let sum = 0;

    for (let i = 0; i < size; i++) {
      const value = clamp(Math.round(lum[i]), 0, 255);
      hist[value]++;
      sum += lum[i];
    }

    const mean = sum / size;
    const at = (share) => {
      let seen = 0;
      const need = share * size;
      for (let v = 0; v < 256; v++) {
        seen += hist[v];
        if (seen >= need) return v;
      }
      return 255;
    };

    const p1 = at(0.01), p5 = at(0.05), p50 = at(0.5), p95 = at(0.95), p99 = at(0.99);

    // Клиппинг считается по КАНАЛАМ, а не по яркости, и это важно: небо часто
    // выбивает только синий, а яркость при этом остаётся приличной. Снимок с
    // выжженным одним каналом уже не спасти — цвет там потерян.
    let blown = 0, crushed = 0;
    for (let i = 0, p = 0; i < size; i++, p += 4) {
      const r = px.data[p], g = px.data[p + 1], b = px.data[p + 2];
      if (r >= 250 && g >= 250 && b >= 250) blown++;
      else if (r >= 254 || g >= 254 || b >= 254) blown += 0.5;
      if (r <= 4 && g <= 4 && b <= 4) crushed++;
    }

    let variance = 0;
    for (let i = 0; i < size; i++) { const d = lum[i] - mean; variance += d * d; }
    const std = Math.sqrt(variance / size);

    // Перепад в ступенях — по проценту снизу и сверху, а не по крайним точкам:
    // одна битая точка в тени иначе давала бы «двадцать ступеней» на любом кадре.
    const low = Math.max(toLinear(p1), 1 / 4096);
    const high = Math.max(toLinear(p99), low * 1.01);
    const range = Math.log2(high / low);

    // Приговор согласован со словом «свет»: он подставляется в «Свет ровный»
    // и в карточке, и в ответе, и в факт-листе для модели. Прежние «ровная» и
    // «тёмная» достались от слова «картинка», и в готовой фразе выходило
    // «Свет ровная» — мелочь, которая разом выдаёт машинную сборку текста.
    let verdict = 'ровный';
    if (p50 < 58 && crushed / size > 0.02) verdict = 'тёмный, тени провалены';
    else if (p50 < 70) verdict = 'тёмный';
    else if (p50 > 196 && blown / size > 0.02) verdict = 'светлый, света выбиты';
    else if (p50 > 186) verdict = 'светлый';

    return {
      hist: Array.from(hist, (n) => n / size),
      mean: round(mean, 1),
      median: p50,
      p1, p5, p95, p99,
      contrast: round(std / 255, 3),
      blown: blown / size,
      crushed: crushed / size,
      range: round(range, 1),
      key: mean < 72 ? 'низкий' : mean > 182 ? 'высокий' : 'средний',
      verdict
    };
  }

  /* =================================== 6. Цвет =========================== */

  const HUE_NAMES = [
    [12, 'красный'], [34, 'оранжевый'], [56, 'жёлтый'], [82, 'салатовый'],
    [140, 'зелёный'], [172, 'бирюзовый'], [200, 'голубой'], [242, 'синий'],
    [275, 'фиолетовый'], [300, 'сиреневый'], [330, 'розовый'], [360, 'малиновый']
  ];

  function hsv(r, g, b) {
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const delta = max - min;
    let h = 0;
    if (delta > 0) {
      if (max === r) h = 60 * (((g - b) / delta) % 6);
      else if (max === g) h = 60 * ((b - r) / delta + 2);
      else h = 60 * ((r - g) / delta + 4);
    }
    if (h < 0) h += 360;
    return { h, s: max === 0 ? 0 : delta / max, v: max / 255 };
  }

  function colorName(r, g, b) {
    const { h, s, v } = hsv(r, g, b);
    if (s < 0.10) {
      if (v < 0.12) return 'чёрный';
      if (v < 0.34) return 'тёмно-серый';
      if (v < 0.66) return 'серый';
      if (v < 0.90) return 'светло-серый';
      return 'белый';
    }
    const base = (HUE_NAMES.find((item) => h <= item[0]) || HUE_NAMES[0])[1];
    // Коричневый — это тёмный оранжевый, и называть его оранжевым нельзя:
    // «палитра: оранжевый 40%» про снимок деревянного стола звучит неправдой.
    if ((h < 45 || h > 350) && v < 0.55 && s > 0.25) return 'коричневый';
    if (v < 0.32) return 'тёмно-' + base;
    if (v > 0.86 && s < 0.35) return 'светло-' + base;
    return base;
  }

  const hex = (r, g, b) => '#' + [r, g, b].map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('');

  /**
   * Палитра — k-средних по цветам кадра.
   *
   * Почему не «самые частые точки»: у фотографии одинаковых точек почти нет,
   * и список частот превратился бы в шесть оттенков одного и того же неба.
   * Кластеры отвечают на другой вопрос — на какие цветовые пятна распадается
   * картинка, — а это и есть палитра.
   *
   * Начальные центры берутся «как можно дальше друг от друга», а не наугад:
   * случайный старт на однотонном кадре сходится к шести соседним оттенкам, и
   * палитра выходит бессмысленной.
   */
  function palette(px, k = 6) {
    const step = Math.max(1, Math.floor((px.w * px.h) / 9000));
    const points = [];
    for (let i = 0, p = 0; p < px.data.length; i++, p += 4 * step) {
      if (px.data[p + 3] < 24) continue;                   // прозрачное не в счёт
      points.push([px.data[p], px.data[p + 1], px.data[p + 2]]);
    }
    if (points.length < k) return [];

    const centers = [points[Math.floor(points.length / 2)]];
    while (centers.length < k) {
      let best = null, bestDistance = -1;
      for (let i = 0; i < points.length; i += 3) {
        let nearest = Infinity;
        for (const center of centers) {
          const d = Math.pow(points[i][0] - center[0], 2) +
                    Math.pow(points[i][1] - center[1], 2) +
                    Math.pow(points[i][2] - center[2], 2);
          if (d < nearest) nearest = d;
        }
        if (nearest > bestDistance) { bestDistance = nearest; best = points[i]; }
      }
      centers.push(best ? best.slice() : [0, 0, 0]);
    }

    const owner = new Int32Array(points.length);
    for (let pass = 0; pass < 12; pass++) {
      let moved = false;
      for (let i = 0; i < points.length; i++) {
        let bestIndex = 0, bestDistance = Infinity;
        for (let c = 0; c < centers.length; c++) {
          const d = Math.pow(points[i][0] - centers[c][0], 2) +
                    Math.pow(points[i][1] - centers[c][1], 2) +
                    Math.pow(points[i][2] - centers[c][2], 2);
          if (d < bestDistance) { bestDistance = d; bestIndex = c; }
        }
        if (owner[i] !== bestIndex) { owner[i] = bestIndex; moved = true; }
      }
      const sums = centers.map(() => [0, 0, 0, 0]);
      for (let i = 0; i < points.length; i++) {
        const s = sums[owner[i]];
        s[0] += points[i][0]; s[1] += points[i][1]; s[2] += points[i][2]; s[3]++;
      }
      for (let c = 0; c < centers.length; c++) {
        if (sums[c][3] > 0) {
          centers[c] = [sums[c][0] / sums[c][3], sums[c][1] / sums[c][3], sums[c][2] / sums[c][3]];
        }
      }
      if (!moved) break;
    }

    const counts = centers.map(() => 0);
    for (let i = 0; i < points.length; i++) counts[owner[i]]++;

    return centers
      .map((center, index) => ({
        hex: hex(center[0], center[1], center[2]),
        name: colorName(center[0], center[1], center[2]),
        share: counts[index] / points.length
      }))
      .filter((item) => item.share > 0.012)
      .sort((a, b) => b.share - a.share);
  }

  function readColor(px, lum) {
    const size = px.w * px.h;
    let sumR = 0, sumG = 0, sumB = 0, sumS = 0, alpha = 0;
    let rgSum = 0, ybSum = 0, rgSq = 0, ybSq = 0;
    // Баланс белого меряется по средним тонам без выраженного цвета: на кадре с
    // красной машиной во весь экран «серый мир» объявил бы красный уклон и
    // потребовал бы его убрать — то есть испортить снимок ради формулы.
    let neutralR = 0, neutralG = 0, neutralB = 0, neutralCount = 0;
    const hues = new Float64Array(12);

    for (let i = 0, p = 0; i < size; i++, p += 4) {
      const r = px.data[p], g = px.data[p + 1], b = px.data[p + 2];
      if (px.data[p + 3] < 250) alpha++;
      sumR += r; sumG += g; sumB += b;

      const rg = r - g;
      const yb = 0.5 * (r + g) - b;
      rgSum += rg; ybSum += yb; rgSq += rg * rg; ybSq += yb * yb;

      const color = hsv(r, g, b);
      sumS += color.s;
      if (color.s > 0.12) hues[Math.min(11, Math.floor(color.h / 30))] += color.s;

      if (lum[i] > 55 && lum[i] < 235 && color.s < 0.34) {
        neutralR += r; neutralG += g; neutralB += b; neutralCount++;
      }
    }

    const meanRg = rgSum / size, meanYb = ybSum / size;
    const stdRg = Math.sqrt(Math.max(0, rgSq / size - meanRg * meanRg));
    const stdYb = Math.sqrt(Math.max(0, ybSq / size - meanYb * meanYb));
    const colorfulness = Math.sqrt(stdRg * stdRg + stdYb * stdYb) +
                         0.3 * Math.sqrt(meanRg * meanRg + meanYb * meanYb);

    const base = neutralCount > size * 0.03
      ? { r: neutralR / neutralCount, g: neutralG / neutralCount, b: neutralB / neutralCount }
      : { r: sumR / size, g: sumG / size, b: sumB / size };
    const grey = (base.r + base.g + base.b) / 3 || 1;
    const gainR = base.r / grey, gainB = base.b / grey;

    let cast = 'нейтральный';
    const warm = gainR - gainB;
    if (warm > 0.055) cast = 'тёплый';
    else if (warm < -0.055) cast = 'холодный';
    const green = base.g / grey - 1;
    if (Math.abs(green) > 0.05 && Math.abs(green) > Math.abs(warm) / 2) {
      cast = green > 0 ? 'зеленоватый' : 'пурпурный';
    }

    return {
      palette: palette(px),
      saturation: round(sumS / size, 3),
      colorfulness: round(colorfulness, 1),
      mono: colorfulness < 9 && sumS / size < 0.10,
      cast,
      castStrength: round(Math.max(Math.abs(warm), Math.abs(green)), 3),
      temperature: colorTemperature(base),
      alpha: alpha / size,
      hues: Array.from(hues, (value) => value / size)
    };
  }

  /**
   * Оценка цветовой температуры сцены по формуле Мак-Кэми.
   *
   * Число это ОЦЕНОЧНОЕ и честно называется оценкой: настоящая температура
   * съёмки лежит в EXIF, а здесь — цветность среднего нейтрального тона,
   * пересчитанная в кельвины. Для «свет заметно теплее дневного» этого хватает,
   * для колориметрии — нет.
   */
  function colorTemperature(base) {
    const r = toLinear(base.r), g = toLinear(base.g), b = toLinear(base.b);
    const X = 0.4124 * r + 0.3576 * g + 0.1805 * b;
    const Y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const Z = 0.0193 * r + 0.1192 * g + 0.9505 * b;
    const sum = X + Y + Z;
    if (sum <= 0) return null;
    const x = X / sum, y = Y / sum;
    const n = (x - 0.3320) / (0.1858 - y || 1e-6);
    const cct = 449 * n * n * n + 3525 * n * n + 6823.3 * n + 5520.33;
    return cct > 1200 && cct < 26000 ? Math.round(cct / 50) * 50 : null;
  }

  /* ====================== 7. Резкость, шум, артефакты ==================== */

  function readDetail(px, lum, crop) {
    const { w, h } = px;
    let lapSum = 0, lapSq = 0, gradSum = 0, edges = 0, axis = 0;
    const orient = new Float64Array(36);
    let count = 0;

    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        const lap = 4 * lum[i] - lum[i - 1] - lum[i + 1] - lum[i - w] - lum[i + w];
        lapSum += Math.abs(lap);
        lapSq += lap * lap;

        const gx = lum[i + 1] - lum[i - 1];
        const gy = lum[i + w] - lum[i - w];
        const magnitude = Math.hypot(gx, gy);
        gradSum += magnitude;
        if (magnitude > 42) {
          edges++;
          // Направление края. У снимка экрана края лежат строго по осям —
          // это рамки окон и строки текста; у фотографии они смотрят куда попало.
          const angle = Math.atan2(gy, gx) * 180 / Math.PI;
          const norm = ((angle % 180) + 180) % 180;
          orient[Math.min(35, Math.floor(norm / 5))] += magnitude;
          if (norm < 6 || norm > 174 || Math.abs(norm - 90) < 6) axis++;
        }
        count++;
      }
    }

    const meanLap = lapSum / Math.max(1, count);
    const std = Math.sqrt(Math.max(0, lapSq / Math.max(1, count) - meanLap * meanLap));

    // Акутанс нормирован на контраст: без деления тёмный вечерний кадр всегда
    // объявлялся бы мягким, а контрастная вывеска — резкой, независимо от фокуса.
    let contrastSum = 0;
    for (let i = 0; i < lum.length; i++) contrastSum += lum[i];
    const mean = contrastSum / lum.length;
    let variance = 0;
    for (let i = 0; i < lum.length; i++) { const d = lum[i] - mean; variance += d * d; }
    const spread = Math.sqrt(variance / lum.length) || 1;
    const acutance = meanLap / spread;

    // Шум по Иммеркеру: свёртка, которая гасит всё, кроме случайных отклонений.
    // Медиана вместо среднего — чтобы края и мелкие детали не считались зерном.
    const noise = estimateNoise(crop);
    const native = nativeAcutance(crop);

    // Направленный смаз. Если энергия краёв собрана в одном направлении, а
    // резкость низкая — это не «мягкий объектив», а движение камеры или объекта.
    let peak = 0, peakIndex = 0, total = 0;
    for (let i = 0; i < 36; i++) { total += orient[i]; if (orient[i] > peak) { peak = orient[i]; peakIndex = i; } }
    const anisotropy = total > 0 ? (peak / (total / 36)) : 0;

    return {
      acutance: round(acutance, 3),
      native: round(native.acutance, 3),
      nativeMeasured: native.measured,
      edges: round(edges / Math.max(1, count), 4),
      axisEdges: edges > 40 ? round(axis / edges, 3) : 0,
      orientation: { peak: peakIndex * 5 + 2.5, anisotropy: round(anisotropy, 2) },
      noise: round(noise, 2),
      blockiness: round(blockiness(lum, w, h), 2),
      lapStd: round(std, 1)
    };
  }

  function estimateNoise(crop) {
    const { w, h, data } = crop;
    if (w < 8 || h < 8) return 0;
    const values = [];
    for (let y = 1; y < h - 1; y += 2) {
      for (let x = 1; x < w - 1; x += 2) {
        const at = (dx, dy) => {
          const p = ((y + dy) * w + (x + dx)) * 4;
          return 0.2126 * data[p] + 0.7152 * data[p + 1] + 0.0722 * data[p + 2];
        };
        const value = Math.abs(
          at(-1, -1) - 2 * at(0, -1) + at(1, -1) -
          2 * at(-1, 0) + 4 * at(0, 0) - 2 * at(1, 0) +
          at(-1, 1) - 2 * at(0, 1) + at(1, 1)
        );
        values.push(value);
      }
    }
    if (!values.length) return 0;
    values.sort((a, b) => a - b);
    const median = values[Math.floor(values.length / 2)];
    return median * Math.sqrt(Math.PI / 2) / 6;
  }

  function nativeAcutance(crop) {
    const { w, h, data } = crop;
    if (w < 16 || h < 16) return { acutance: 0, measured: false };
    let sum = 0, mean = 0, count = 0;
    const lum = new Float32Array(w * h);
    for (let i = 0, p = 0; i < w * h; i++, p += 4) {
      lum[i] = 0.2126 * data[p] + 0.7152 * data[p + 1] + 0.0722 * data[p + 2];
      mean += lum[i];
    }
    mean /= w * h;
    let variance = 0;
    for (let i = 0; i < lum.length; i++) { const d = lum[i] - mean; variance += d * d; }
    const spread = Math.sqrt(variance / lum.length) || 1;

    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        sum += Math.abs(4 * lum[i] - lum[i - 1] - lum[i + 1] - lum[i - w] - lum[i + w]);
        count++;
      }
    }
    return { acutance: sum / Math.max(1, count) / spread, measured: true };
  }

  /**
   * Следы пережатия: JPEG делит кадр на квадраты 8×8 и на их границах оставляет
   * ступеньки. Если перепад на линиях сетки заметно больше, чем внутри
   * квадратов, — картинку жали, и жали сильно.
   */
  function blockiness(lum, w, h) {
    let onGrid = 0, onGridCount = 0, inside = 0, insideCount = 0;
    for (let y = 2; y < h - 2; y++) {
      for (let x = 2; x < w - 2; x++) {
        const diff = Math.abs(lum[y * w + x] - lum[y * w + x - 1]);
        if (x % 8 === 0) { onGrid += diff; onGridCount++; }
        else { inside += diff; insideCount++; }
      }
    }
    if (!onGridCount || !insideCount) return 0;
    const gridMean = onGrid / onGridCount;
    const insideMean = inside / insideCount || 1;
    return Math.max(0, gridMean / insideMean - 1);
  }

  /* ============================ 8. Структура кадра ======================= */

  function readFrame(px, lum) {
    const { w, h } = px;

    // --- плоские области: у снимка экрана их много, у фотографии почти нет ---
    let flat = 0, checked = 0;
    for (let y = 1; y < h - 1; y += 2) {
      for (let x = 1; x < w - 1; x += 2) {
        const i = y * w + x;
        const same = Math.abs(lum[i] - lum[i - 1]) < 0.6 &&
                     Math.abs(lum[i] - lum[i + 1]) < 0.6 &&
                     Math.abs(lum[i] - lum[i - w]) < 0.6 &&
                     Math.abs(lum[i] - lum[i + w]) < 0.6;
        if (same) flat++;
        checked++;
      }
    }

    // --- белый фон, тёмные точки: признак документа --------------------------
    let white = 0, dark = 0, skin = 0, green = 0;
    for (let i = 0, p = 0; i < w * h; i++, p += 4) {
      const r = px.data[p], g = px.data[p + 1], b = px.data[p + 2];
      if (r > 232 && g > 232 && b > 232) white++;
      if (lum[i] < 48) dark++;

      // Тон кожи в YCbCr — то же правило, что в старых детекторах лиц. Оно
      // грубое и честно называется «похоже на кожу»: дерево и песок иногда
      // попадают тоже, поэтому одного этого признака для «здесь человек» мало.
      const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
      const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;
      if (cb >= 77 && cb <= 130 && cr >= 133 && cr <= 176 && r > 60 && r > b) skin++;

      const tone = hsv(r, g, b);
      if (tone.h > 65 && tone.h < 175 && tone.s > 0.18 && tone.v > 0.12) green++;
    }
    const size = w * h;

    // --- текст: строки дают частые перепады, между строками — пусто ----------
    const rows = [];
    for (let y = 0; y < h; y++) {
      let transitions = 0;
      let sign = 0;
      for (let x = 1; x < w; x++) {
        const delta = lum[y * w + x] - lum[y * w + x - 1];
        if (Math.abs(delta) < 16) continue;
        const next = delta > 0 ? 1 : -1;
        if (sign && next !== sign) transitions++;
        sign = next;
      }
      rows.push(transitions / w);
    }
    // Порог подобран замером: строка мелкого текста на рабочем холсте даёт
    // 0,1-0,25 перепада на точку ширины, лист без текста — меньше 0,02.
    // Сплошная фактура (листва, гравий) перепадов даёт не меньше, но там
    // ЗАНЯТЫ ВСЕ строки подряд, а это одна полоса, и до счёта полос дело не
    // доходит: у текста полосы обязаны чередоваться с пустыми промежутками.
    let bands = 0, inBand = false, banded = 0;
    for (let y = 0; y < h; y++) {
      const busy = rows[y] > 0.038;
      if (busy) banded++;
      if (busy && !inBand) { bands++; inBand = true; }
      if (!busy) inBand = false;
    }
    const textScore = bands >= 3 ? clamp((banded / h) * clamp(bands / 8, 0, 1) * 2.2, 0, 1) : 0;

    // --- заметность: чем область отличается от своего окружения --------------
    const map = saliency(px, lum);

    // --- небо: верхняя треть, светлая и без фактуры --------------------------
    let skyHits = 0, skyChecked = 0;
    const skyEnd = Math.floor(h * 0.34);
    for (let y = 0; y < skyEnd; y++) {
      for (let x = 0; x < w; x += 2) {
        const i = y * w + x, p = i * 4;
        const r = px.data[p], g = px.data[p + 1], b = px.data[p + 2];
        const blue = b > r + 12 && lum[i] > 92;
        const overcast = Math.max(r, g, b) - Math.min(r, g, b) < 26 && lum[i] > 155;
        if (blue || overcast) skyHits++;
        skyChecked++;
      }
    }

    // --- наклон: медиана отклонения сильных почти-горизонтальных краёв -------
    const tilt = horizonTilt(lum, w, h);

    return {
      flat: round(flat / Math.max(1, checked), 3),
      white: round(white / size, 3),
      dark: round(dark / size, 3),
      skin: round(skin / size, 4),
      green: round(green / size, 3),
      sky: round(skyChecked ? skyHits / skyChecked : 0, 3),
      text: round(textScore, 3),
      textLines: bands,
      subject: map.box,
      spread: map.spread,
      thirds: map.thirds,
      tilt,
      faces: faceBlobs(px, w, h),
      orientation: w > h * 1.1 ? 'горизонтальный' : h > w * 1.1 ? 'вертикальный' : 'квадратный'
    };
  }

  /**
   * Карта заметности «центр против окружения».
   *
   * Считается по разнице кадра с его сильно размытой копией: там, где точка
   * заметно отличается от общего фона вокруг, глаз и цепляется. Это не
   * распознавание объекта, а именно карта внимания — но её хватает, чтобы
   * сказать, в какой части кадра лежит главное и попадает ли оно в трети.
   */
  function saliency(px, lum) {
    const { w, h } = px;
    const gw = 48, gh = Math.max(8, Math.round(gw * h / w));
    const cell = new Float64Array(gw * gh);
    const count = new Float64Array(gw * gh);

    for (let y = 0; y < h; y++) {
      const gy = Math.min(gh - 1, Math.floor(y * gh / h));
      for (let x = 0; x < w; x++) {
        const gx = Math.min(gw - 1, Math.floor(x * gw / w));
        cell[gy * gw + gx] += lum[y * w + x];
        count[gy * gw + gx]++;
      }
    }
    for (let i = 0; i < cell.length; i++) cell[i] /= count[i] || 1;

    let mean = 0;
    for (let i = 0; i < cell.length; i++) mean += cell[i];
    mean /= cell.length;

    const score = new Float64Array(cell.length);
    let max = 0;
    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++) {
        let around = 0, seen = 0;
        for (let dy = -4; dy <= 4; dy++) {
          for (let dx = -4; dx <= 4; dx++) {
            const ny = gy + dy, nx = gx + dx;
            if (ny < 0 || nx < 0 || ny >= gh || nx >= gw) continue;
            around += cell[ny * gw + nx];
            seen++;
          }
        }
        const value = Math.abs(cell[gy * gw + gx] - around / (seen || 1));
        score[gy * gw + gx] = value;
        if (value > max) max = value;
      }
    }
    if (max <= 0) return { box: null, spread: 1, thirds: null };

    let sumX = 0, sumY = 0, weight = 0, hot = 0;
    let minX = gw, minY = gh, maxX = 0, maxY = 0;
    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++) {
        const value = score[gy * gw + gx] / max;
        if (value < 0.45) continue;
        hot++;
        sumX += gx * value; sumY += gy * value; weight += value;
        if (gx < minX) minX = gx;
        if (gy < minY) minY = gy;
        if (gx > maxX) maxX = gx;
        if (gy > maxY) maxY = gy;
      }
    }
    if (!weight || hot < 4) return { box: null, spread: 1, thirds: null };

    const cx = sumX / weight / gw, cy = sumY / weight / gh;
    const box = {
      x: round(minX / gw, 3), y: round(minY / gh, 3),
      w: round((maxX - minX + 1) / gw, 3), h: round((maxY - minY + 1) / gh, 3),
      cx: round(cx, 3), cy: round(cy, 3)
    };

    // Правило третей: насколько центр внимания близок к одной из четырёх точек
    // пересечения третей. Расстояние меряется в долях кадра.
    let best = 2;
    for (const tx of [1 / 3, 2 / 3]) {
      for (const ty of [1 / 3, 2 / 3]) {
        best = Math.min(best, Math.hypot(cx - tx, cy - ty));
      }
    }
    const middle = Math.hypot(cx - 0.5, cy - 0.5);

    return {
      box,
      spread: round(hot / (gw * gh), 3),
      thirds: { distance: round(best, 3), centered: middle < 0.12, cx: round(cx, 3), cy: round(cy, 3) }
    };
  }

  function horizonTilt(lum, w, h) {
    const angles = [];
    for (let y = 2; y < h - 2; y += 2) {
      for (let x = 2; x < w - 2; x += 2) {
        const i = y * w + x;
        const gx = lum[i + 1] - lum[i - 1];
        const gy = lum[i + w] - lum[i - w];
        const magnitude = Math.hypot(gx, gy);
        if (magnitude < 55) continue;
        // Край горизонтальной линии смотрит вертикально: интересен угол самой
        // линии, а он на 90° повёрнут относительно градиента.
        const line = Math.atan2(gy, gx) * 180 / Math.PI + 90;
        const norm = ((line % 180) + 180) % 180;
        if (norm < 12) angles.push(norm);
        else if (norm > 168) angles.push(norm - 180);
      }
    }
    if (angles.length < 40) return null;
    angles.sort((a, b) => a - b);
    const median = angles[Math.floor(angles.length / 2)];
    return { angle: round(median, 1), samples: angles.length };
  }

  /**
   * Пятна телесного цвета, похожие на лицо.
   *
   * Честная граница возможностей: это НЕ распознавание лиц. Здесь только связные
   * области кожи подходящей формы и размера — по ним можно сказать «в кадре,
   * похоже, есть люди», но не «их трое и вот они».
   */
  function faceBlobs(px, w, h) {
    const gw = 96, gh = Math.max(8, Math.round(gw * h / w));
    const mask = new Uint8Array(gw * gh);

    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++) {
        const x = Math.min(w - 1, Math.floor(gx * w / gw));
        const y = Math.min(h - 1, Math.floor(gy * h / gh));
        const p = (y * w + x) * 4;
        const r = px.data[p], g = px.data[p + 1], b = px.data[p + 2];
        const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
        const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;
        if (cb >= 77 && cb <= 130 && cr >= 133 && cr <= 176 && r > 60 && r > b && g > 40) {
          mask[gy * gw + gx] = 1;
        }
      }
    }

    const seen = new Uint8Array(gw * gh);
    const blobs = [];
    const stack = [];
    for (let start = 0; start < mask.length; start++) {
      if (!mask[start] || seen[start]) continue;
      stack.length = 0;
      stack.push(start);
      seen[start] = 1;
      let area = 0, minX = gw, minY = gh, maxX = 0, maxY = 0;

      while (stack.length) {
        const at = stack.pop();
        const x = at % gw, y = (at / gw) | 0;
        area++;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;

        const neighbours = [at - 1, at + 1, at - gw, at + gw];
        for (const next of neighbours) {
          if (next < 0 || next >= mask.length || seen[next] || !mask[next]) continue;
          if ((next === at - 1 && x === 0) || (next === at + 1 && x === gw - 1)) continue;
          seen[next] = 1;
          stack.push(next);
        }
      }

      const bw = maxX - minX + 1, bh = maxY - minY + 1;
      const fill = area / (bw * bh);
      const aspect = bw / bh;
      const share = area / mask.length;
      if (share > 0.004 && share < 0.62 && fill > 0.42 && aspect > 0.45 && aspect < 2.1) {
        blobs.push({
          x: round(minX / gw, 3), y: round(minY / gh, 3),
          w: round(bw / gw, 3), h: round(bh / gh, 3),
          share: round(share, 4)
        });
      }
    }
    return blobs.sort((a, b) => b.share - a.share).slice(0, 6);
  }

  /* ========================= 9. Что это за картинка ====================== */

  /*
     Маленькая своя сеть — softmax-регрессия на измеренных признаках.

     Почему не набор «если-иначе». Правила здесь пришлось бы пересекать: у
     скриншота игры и фотографии много общего, у схемы и документа тоже, и
     каждая новая поправка ломала бы соседнее правило. Линейная модель делает то
     же самое, но одним взвешенным голосованием — и, главное, умеет учиться.

     Веса выставлены руками ПО СМЫСЛУ, а не обучены на чужом наборе: «много
     ровных заливок» голосует за снимок экрана, «есть паспорт EXIF» — за
     фотографию, «белый фон и строки текста» — за документ. Это стартовое
     знание, с которым модуль работает сразу, из коробки.

     А дальше он доучивается на поправках хозяина. Нажали «это фотография» —
     пример лёг в память, веса пересчитались за миллисекунды. Тянутся они при
     этом к исходным (штраф за отход): десяток поправок должен подвинуть модель,
     а не стереть ей смысл. Всё локально, в localStorage этого браузера.
  */
  const FEATURES = [
    'flat', 'axisEdges', 'concentration', 'text', 'noise', 'saturation',
    'colorfulness', 'exif', 'lossless', 'screenSize', 'white', 'edges',
    'skin', 'sky', 'green', 'alpha', 'blockiness', 'dark', 'native', 'bias'
  ];

  const PRIOR = {
    //          flat  axis conc text noise satu colo exif loss scrn white edge skin  sky green alpha block dark nativ bias
    photo:      [-2.6, -1.5, -1.0, -0.8, 2.2, 0.6, 1.4, 3.4, -0.7, -1.1, -1.2, 0.5, 1.5, 1.6, 1.1, -1.4, 0.3, 0.2, 0.9, 0.2],
    screenshot: [2.9, 2.2, 1.4, 0.9, -1.9, -0.9, -1.1, -1.8, 1.5, 2.6, 0.4, -0.3, -0.9, -1.0, -0.6, -0.5, -0.8, 0.1, -0.9, 0.0],
    illustration: [1.4, -0.6, 1.2, -0.7, -1.6, 1.9, 1.2, -1.2, 0.9, -0.8, -0.5, 0.2, -0.2, -0.3, 0.1, 0.6, -0.5, 0.2, -0.4, 0.0],
    document:   [1.8, 1.1, 0.7, 2.8, -1.0, -2.2, -2.0, -0.9, 0.8, -0.4, 2.6, 0.4, -0.8, -0.9, -0.8, -0.3, -0.2, -0.6, 0.1, -0.4],
    chart:      [2.2, 1.4, 1.6, 1.5, -1.4, 0.4, -0.4, -1.0, 1.0, -0.5, 2.0, -0.5, -0.9, -0.8, -0.5, 0.2, -0.4, -0.7, -0.2, -0.8],
    graphic:    [1.6, 0.2, 1.8, -0.4, -1.5, 0.8, 0.2, -1.0, 1.2, -0.9, 0.3, -0.6, -0.4, -0.5, -0.2, 3.6, -0.4, 0.0, -0.3, -1.2]
  };

  const LEARN_KEY = 'cloudhdr.vision.samples.v1';
  let samples = [];
  let weights = null;

  function loadSamples() {
    try {
      const raw = JSON.parse(localStorage.getItem(LEARN_KEY) || '[]');
      samples = Array.isArray(raw)
        ? raw.filter((item) => item && Array.isArray(item.x) && item.x.length === FEATURES.length &&
                               CLASSES.includes(item.y)).slice(-300)
        : [];
    } catch { samples = []; }
  }

  function saveSamples() {
    try { localStorage.setItem(LEARN_KEY, JSON.stringify(samples.slice(-300))); } catch { /* переполнено */ }
  }

  function priorWeights() {
    return CLASSES.map((name) => PRIOR[name].slice());
  }

  function fit() {
    weights = priorWeights();
    if (!samples.length) return;

    const rate = 0.25;
    const pull = 0.04;                       // тяга к исходным весам
    const base = priorWeights();

    for (let epoch = 0; epoch < 220; epoch++) {
      const grad = CLASSES.map(() => new Float64Array(FEATURES.length));
      for (const sample of samples) {
        const probs = softmax(sample.x, weights);
        const target = CLASSES.indexOf(sample.y);
        for (let c = 0; c < CLASSES.length; c++) {
          const error = probs[c] - (c === target ? 1 : 0);
          for (let f = 0; f < FEATURES.length; f++) grad[c][f] += error * sample.x[f];
        }
      }
      for (let c = 0; c < CLASSES.length; c++) {
        for (let f = 0; f < FEATURES.length; f++) {
          weights[c][f] -= rate * (grad[c][f] / samples.length + pull * (weights[c][f] - base[c][f]));
        }
      }
    }
  }

  function softmax(x, w) {
    const scores = w.map((row) => {
      let sum = 0;
      for (let i = 0; i < x.length; i++) sum += row[i] * x[i];
      return sum;
    });
    const max = Math.max(...scores);
    const exps = scores.map((value) => Math.exp(value - max));
    const total = exps.reduce((a, b) => a + b, 0) || 1;
    return exps.map((value) => value / total);
  }

  /** Известные размеры экранов — сильный довод в пользу снимка экрана. */
  const SCREENS = [
    [1280, 720], [1366, 768], [1440, 900], [1536, 864], [1600, 900], [1680, 1050],
    [1920, 1080], [1920, 1200], [2048, 1152], [2256, 1504], [2560, 1080], [2560, 1440],
    [2560, 1600], [2880, 1800], [3440, 1440], [3840, 2160], [1080, 1920], [1170, 2532],
    [1179, 2556], [1284, 2778], [1290, 2796], [828, 1792], [750, 1334], [1440, 3200]
  ];

  function screenMatch(w, h) {
    return SCREENS.some(([sw, sh]) => (Math.abs(w - sw) < 3 && Math.abs(h - sh) < 3) ||
                                      (Math.abs(w - sh) < 3 && Math.abs(h - sw) < 3)) ? 1 : 0;
  }

  function featurize(meta, tone, color, detail, frame) {
    const top3 = color.palette.slice(0, 3).reduce((sum, item) => sum + item.share, 0);
    const values = {
      flat: clamp(frame.flat, 0, 1),
      axisEdges: clamp(detail.axisEdges, 0, 1),
      concentration: clamp(top3, 0, 1),
      text: clamp(frame.text, 0, 1),
      noise: clamp(detail.noise / 6, 0, 1),
      saturation: clamp(color.saturation * 2, 0, 1),
      colorfulness: clamp(color.colorfulness / 70, 0, 1),
      exif: meta.shot && (meta.shot.camera || meta.shot.exposure) ? 1 : 0,
      lossless: /png|webp|bmp/.test(meta.type) ? 1 : 0,
      screenSize: screenMatch(meta.width, meta.height),
      white: clamp(frame.white * 1.6, 0, 1),
      edges: clamp(detail.edges * 12, 0, 1),
      skin: clamp(frame.skin * 6, 0, 1),
      sky: clamp(frame.sky, 0, 1),
      green: clamp(frame.green * 3, 0, 1),
      alpha: clamp(color.alpha * 3, 0, 1),
      blockiness: clamp(detail.blockiness, 0, 1),
      dark: clamp(frame.dark, 0, 1),
      native: clamp(detail.native / 0.6, 0, 1),
      bias: 1
    };
    return FEATURES.map((name) => round(values[name], 4));
  }

  function classify(features) {
    if (!weights) fit();
    const probs = softmax(features, weights);
    const ranked = CLASSES
      .map((name, index) => ({ id: name, p: probs[index] }))
      .sort((a, b) => b.p - a.p);

    return {
      id: ranked[0].id,
      title: KINDS[ranked[0].id].title,
      icon: KINDS[ranked[0].id].icon,
      confidence: round(ranked[0].p, 3),
      runnerUp: ranked[1].p > 0.18 ? { id: ranked[1].id, title: KINDS[ranked[1].id].title, p: round(ranked[1].p, 3) } : null,
      learned: samples.length
    };
  }

  /** Поправка хозяина: «это не то, что ты решила». Учится сразу. */
  function learn(features, kind) {
    if (!Array.isArray(features) || features.length !== FEATURES.length) return null;
    if (!CLASSES.includes(kind)) return null;
    samples.push({ x: features, y: kind, ts: Date.now() });
    if (samples.length > 300) samples = samples.slice(-300);
    saveSamples();
    fit();
    return { samples: samples.length, kind, title: KINDS[kind].title };
  }

  function forget() {
    samples = [];
    saveSamples();
    fit();
  }

  /** Перечитать поправки из хранилища. Нужно самопроверке, чтобы вернуть всё как было. */
  function reload() {
    loadSamples();
    fit();
    return samples.length;
  }

  /* ========================== 10. Оценка и советы ======================== */

  /**
   * Оценка технического качества — и только техники.
   *
   * Про удачный момент, выражение лица и смысл кадра здесь ничего сказать
   * нельзя, поэтому и не говорится. Считаются вещи измеримые: экспозиция,
   * потери в светах и тенях, резкость, шум, баланс белого, следы пережатия.
   *
   * У рисунков и снимков экрана оценка не выставляется вовсе. Требовать от
   * скриншота «правильной экспозиции» бессмысленно: он ровно такой, каким был
   * экран, и «исправлять» там нечего.
   */
  function score(kind, tone, color, detail) {
    if (kind.id !== 'photo') return null;
    let value = 100;

    if (tone.blown > 0.02) value -= Math.min(22, tone.blown * 320);
    if (tone.crushed > 0.03) value -= Math.min(16, tone.crushed * 200);
    if (tone.median < 62) value -= Math.min(14, (62 - tone.median) * 0.5);
    if (tone.median > 198) value -= Math.min(14, (tone.median - 198) * 0.5);
    if (tone.contrast < 0.10) value -= 10;

    const sharp = detail.nativeMeasured ? detail.native : detail.acutance;
    if (sharp < 0.16) value -= 26;
    else if (sharp < 0.24) value -= 14;
    else if (sharp < 0.32) value -= 6;

    if (detail.noise > 4.5) value -= 16;
    else if (detail.noise > 2.6) value -= 8;

    if (color.castStrength > 0.12) value -= 10;
    else if (color.castStrength > 0.07) value -= 5;

    if (detail.blockiness > 0.55) value -= 10;

    return clamp(Math.round(value), 5, 100);
  }

  function advise(kind, meta, tone, color, detail, frame) {
    const list = [];
    const photo = kind.id === 'photo';

    if (tone.blown > 0.015) {
      list.push({
        title: `Вернуть света: выбито ${pct(tone.blown, 1)} кадра`,
        detail: 'Опустить света на -20…-35 и приподнять экспозицию тенями. Там, где выбиты ' +
                'все три канала, цвета уже нет — вытянуть можно только из RAW.'
      });
    }
    if (tone.crushed > 0.03) {
      list.push({
        title: `Поднять тени: провалено ${pct(tone.crushed, 1)}`,
        detail: 'Тени +25…+40, чёрная точка чуть выше. Осторожно: в проваленных участках ' +
                'вместе с деталями поднимется и шум.'
      });
    }
    if (photo && tone.median < 66 && tone.crushed <= 0.03) {
      list.push({ title: 'Кадр тёмный', detail: `Медиана яркости ${tone.median} из 255 — экспозиция просит +0,5…+1 ступени.` });
    }
    if (photo && tone.median > 196 && tone.blown <= 0.015) {
      list.push({ title: 'Кадр светлый', detail: `Медиана ${tone.median} из 255 — минус треть-половина ступени вернёт объём.` });
    }
    if (tone.contrast < 0.11 && kind.id !== 'document') {
      list.push({ title: 'Мало контраста', detail: 'Разброс яркости ' + pct(tone.contrast, 1) + ' — кадр выглядит плоским. Помогут чёрная и белая точки, а не ползунок «контраст».' });
    }
    if (color.castStrength > 0.07 && !color.mono) {
      list.push({
        title: `Уклон баланса белого: ${color.cast}`,
        detail: `Отклонение нейтрального тона ${pct(color.castStrength, 1)}` +
                (color.temperature ? `, оценка температуры ${color.temperature} К.` : '.') +
                ' Пипетка по-настоящему серому участку выправит одним касанием.'
      });
    }

    const sharp = detail.nativeMeasured ? detail.native : detail.acutance;
    if (sharp < 0.2) {
      const motion = detail.orientation.anisotropy > 4.5;
      list.push({
        title: motion ? 'Смаз, похоже, от движения' : 'Мягкая резкость',
        detail: motion
          ? `Края собраны в одном направлении (около ${Math.round(detail.orientation.peak)}°) — так выглядит ` +
            'смаз камеры или объекта. Резкостью это не лечится, нужна короче выдержка.'
          : 'Акутанс ' + ru(sharp, 2) + ' при норме от 0,3. Причина — промах фокуса, длинная выдержка или сильное пережатие.'
      });
    }
    if (detail.noise > 3) {
      list.push({
        title: `Заметное зерно: σ ≈ ${ru(detail.noise)}`,
        detail: 'Шумодав слабой рукой (детали уходят быстрее шума) плюс лёгкая резкость после него.' +
                (meta.shot && meta.shot.iso > 1600 ? ` На съёмке стояло ISO ${meta.shot.iso} — этого и следовало ждать.` : '')
      });
    }
    if (detail.blockiness > 0.55) {
      list.push({
        title: 'Следы сильного пережатия',
        detail: 'На границах квадратов 8×8 видны ступеньки — картинку жали несколько раз. ' +
                'Улучшить нельзя, можно только взять исходник.'
      });
    }
    if (frame.tilt && Math.abs(frame.tilt.angle) > 0.8 && photo) {
      list.push({
        title: `Горизонт завален на ${ru(Math.abs(frame.tilt.angle))}°`,
        detail: `Повернуть на ${frame.tilt.angle > 0 ? '−' : '+'}${ru(Math.abs(frame.tilt.angle))}° и обрезать края.`
      });
    }
    if (photo && frame.thirds && frame.thirds.centered && frame.subject && frame.subject.w < 0.6) {
      list.push({
        title: 'Главное — ровно посередине',
        detail: 'Кадрирование со сдвигом объекта к трети обычно собирает композицию. Правило не закон, но начинать стоит с него.'
      });
    }
    if (photo && tone.range > 11) {
      list.push({
        title: `Широкий перепад: ≈ ${ru(tone.range)} ступени`,
        detail: 'Сцена шире, чем вытягивает один кадр. Это тот случай, ради которого и придумали HDR: ' +
                'два-три снимка с разной экспозицией и сведение.'
      });
    }
    if (meta.size > 6 * 1024 * 1024 && kind.id !== 'photo') {
      list.push({ title: 'Тяжёлый файл', detail: `${bytes(meta.size)} для такой картинки много — пересохранить в JPEG или WebP.` });
    }

    return list.slice(0, 7);
  }

  /* ================================ 11. Слова ============================ */

  function summarize(card) {
    const lines = [];
    const kind = card.kind;
    const sure = kind.confidence > 0.62 ? '' : ', хотя уверенности немного';

    lines.push(`Передо мной ${kind.title}${sure}: ${card.w}×${card.h} точек` +
               (card.mp >= 0.8 ? `, ${ru(card.mp)} Мп` : '') + `, ${format(card.type)}, ${bytes(card.size)}.`);

    if (card.shot && (card.shot.camera || card.shot.exposure)) {
      const parts = [];
      if (card.shot.camera) parts.push(card.shot.camera);
      if (card.shot.exposure) parts.push(card.shot.exposure);
      if (card.shot.aperture) parts.push(card.shot.aperture);
      if (card.shot.iso) parts.push('ISO ' + card.shot.iso);
      if (card.shot.focal) parts.push(card.shot.focal);
      lines.push('Снято: ' + parts.join(', ') + '.');
    }

    const tone = card.tone;
    const light = [`Свет ${tone.verdict}: медиана ${tone.median} из 255`];
    if (tone.blown > 0.005) light.push(`пересветов ${pct(tone.blown, 1)}`);
    if (tone.crushed > 0.01) light.push(`провалов ${pct(tone.crushed, 1)}`);
    light.push(`перепад ≈ ${ru(tone.range)} ступени`);
    lines.push(light.join(', ') + '.');

    const color = card.color;
    if (color.mono) {
      lines.push('Кадр практически чёрно-белый — цвета в нём почти нет.');
    } else {
      const top = color.palette.slice(0, 3).map((item) => `${item.name} ${pct(item.share)}`).join(', ');
      lines.push(`Цвет: ${color.cast === 'нейтральный' ? 'баланс белого ровный' : 'уклон ' + color.cast}` +
                 (top ? `, в палитре ${top}` : '') + '.');
    }

    const detail = card.detail;
    lines.push(`Резкость ${detail.verdict}, шум ${detail.noiseText}.`);

    if (card.score != null) lines.push(`Техническая оценка — ${card.score} из 100.`);

    if (card.advice.length) {
      lines.push('Что я бы поправил: ' + card.advice.slice(0, 2).map((item) => item.title.toLowerCase()).join('; ') + '.');
    } else if (card.kind.id === 'photo') {
      lines.push('Придраться, по правде говоря, не к чему: свет, цвет и резкость в порядке.');
    }

    return lines.join('\n');
  }

  function format(type) {
    const map = {
      'image/jpeg': 'JPEG', 'image/jpg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WebP',
      'image/gif': 'GIF', 'image/bmp': 'BMP', 'image/avif': 'AVIF'
    };
    return map[type] || String(type || '').replace('image/', '').toUpperCase() || 'неизвестный формат';
  }

  /**
   * Факт-лист для языковой модели.
   *
   * Коротко и без единого лишнего слова — и это не забота о трафике. У модели
   * контекст 2048 токенов, и каждая строка отсюда вытесняет строку разговора.
   * Плюс главное: модель картинку НЕ ВИДИТ. Всё, что она может, — говорить о
   * присланных числах, поэтому числа должны быть теми, о которых спрашивают,
   * и подписаны так, чтобы их нельзя было понять иначе.
   *
   * Слов-приманок вроде «покажи код» или «напиши текст» здесь нет намеренно:
   * сервер выбирает профиль генерации по словам запроса, и одно неудачное слово
   * увело бы разбор фотографии в профиль вёрстки.
   */
  function brief(card, question) {
    const lines = [];
    lines.push(`Пользователь прислал изображение. Модуль зрения Cloud HDR его измерил — вот факты:`);
    lines.push(`- род: ${card.kind.title} (уверенность ${pct(card.kind.confidence)})`);
    lines.push(`- кадр: ${card.w}×${card.h}, ${format(card.type)}, ${bytes(card.size)}, ${card.frame.orientation}`);

    if (card.shot && card.shot.camera) {
      const parts = [card.shot.camera];
      if (card.shot.exposure) parts.push(card.shot.exposure);
      if (card.shot.aperture) parts.push(card.shot.aperture);
      if (card.shot.iso) parts.push('ISO ' + card.shot.iso);
      lines.push('- съёмка: ' + parts.join(', '));
    }
    lines.push(`- свет: ${card.tone.verdict}, медиана ${card.tone.median}/255, ` +
               `пересветы ${pct(card.tone.blown, 1)}, провалы ${pct(card.tone.crushed, 1)}, ` +
               `перепад ${ru(card.tone.range)} ступени`);
    lines.push(`- цвет: ${card.color.mono ? 'почти монохром' : 'уклон ' + card.color.cast}, ` +
               `палитра ${card.color.palette.slice(0, 3).map((item) => item.name).join(', ') || 'не выделилась'}`);
    lines.push(`- резкость ${card.detail.verdict}, шум ${card.detail.noiseText}`);

    const notes = [];
    if (card.frame.faces > 0) notes.push(`пятен телесного цвета: ${card.frame.faces}`);
    if (card.frame.sky > 0.3) notes.push('сверху похоже на небо');
    if (card.frame.text > 0.25) notes.push(`есть текст, строк примерно ${card.frame.textLines}`);
    if (card.frame.green > 0.2) notes.push('много зелени');
    if (notes.length) lines.push('- ещё замечено: ' + notes.join('; '));

    if (card.advice.length) {
      lines.push('- что стоит поправить: ' + card.advice.slice(0, 3).map((item) => item.title).join('; '));
    }

    lines.push('');
    lines.push(question
      ? `Вопрос человека: «${question}». Ответь на него по этим фактам, по-русски, ` +
        'коротко и спокойно. Чего в фактах нет — того не придумывай: предметы на изображении ' +
        'ты не видишь и об этом честно скажи, если спрашивают именно о них.'
      : 'Скажи по этим фактам два-три предложения по-русски: что за изображение и что с ним стоит сделать. ' +
        'Ничего сверх фактов не добавляй.');

    return lines.join('\n');
  }

  /* =============================== 12. Разбор ============================ */

  /*
     Передышка между шагами разбора.

     Нужна, чтобы страница успевала перерисовать панель размышления: без неё все
     замеры идут одним куском в полсекунды, и человек видит вместо живых шагов
     подвисшее окно.

     Гонка кадра с таймером — не перестраховка. Браузер останавливает
     requestAnimationFrame у невидимых вкладок и свёрнутых окон: свернул окно
     сразу после отправки картинки — и разбор замер бы навсегда, ожидая кадра,
     которого не будет. Таймер в свёрнутом окне тоже замедляется, но идёт.
  */
  const pause = () => new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    if (global.requestAnimationFrame) global.requestAnimationFrame(finish);
    setTimeout(finish, 40);
  });

  /**
   * Полный разбор картинки.
   * @param {object} item вложение с подноса
   * @param {(stage:string)=>void} [onStage] куда сообщать о шаге — панель размышления
   * @returns {Promise<object>} карточка разбора, готовая и к показу, и к записи в историю
   */
  async function analyze(item, onStage) {
    const say = (text) => { if (onStage) onStage(text); };

    say('Раскладываю картинку на точки');
    const bitmap = item.bitmap || await decode(item.blob);
    const px = raster(bitmap, LIMITS.work);
    const lum = luminance(px);
    await pause();

    say('Читаю паспорт кадра');
    const exif = item.buffer ? safely(() => parseExif(item.buffer)) : null;
    const png = item.buffer ? safely(() => parsePngText(item.buffer)) : null;
    const meta = {
      name: item.name,
      type: item.type,
      size: item.size,
      width: bitmap.width || bitmap.naturalWidth,
      height: bitmap.height || bitmap.naturalHeight,
      shot: shotCard(exif),
      png
    };
    await pause();

    say('Считаю свет и гистограмму');
    const tone = readTone(px, lum);
    await pause();

    say('Разбираю цвет и палитру');
    const color = readColor(px, lum);
    await pause();

    say('Меряю резкость и шум');
    const crop = centerCrop(bitmap, LIMITS.native);
    const detail = readDetail(px, lum, crop);
    await pause();

    say('Смотрю на построение кадра');
    const frame = readFrame(px, lum);
    await pause();

    say('Решаю, что это такое');
    const features = featurize(meta, tone, color, detail, frame);
    const kind = classify(features);

    const sharp = detail.nativeMeasured ? detail.native : detail.acutance;
    detail.verdict = sharp > 0.42 ? 'высокая' : sharp > 0.3 ? 'нормальная' : sharp > 0.2 ? 'мягкая' : 'низкая';
    detail.noiseText = detail.noise > 4.5 ? 'сильный' : detail.noise > 2.6 ? 'заметный' :
                       detail.noise > 1.2 ? 'слабый' : 'почти нет';

    const card = {
      id: item.id,
      at: Date.now(),
      name: meta.name,
      type: meta.type,
      size: meta.size,
      w: meta.width,
      h: meta.height,
      mp: round(meta.width * meta.height / 1e6, 1),
      thumb: item.thumb || null,
      kind,
      shot: meta.shot,
      tone: {
        median: tone.median, mean: tone.mean, contrast: tone.contrast,
        blown: tone.blown, crushed: tone.crushed, range: tone.range,
        key: tone.key, verdict: tone.verdict,
        hist: compressHistogram(tone.hist)
      },
      color: {
        palette: color.palette.slice(0, 6).map((item2) => ({
          hex: item2.hex, name: item2.name, share: round(item2.share, 3)
        })),
        cast: color.cast, castStrength: color.castStrength,
        temperature: color.temperature, saturation: color.saturation,
        colorfulness: color.colorfulness, mono: color.mono, alpha: round(color.alpha, 3)
      },
      detail: {
        acutance: detail.acutance, native: detail.native, verdict: detail.verdict,
        noise: detail.noise, noiseText: detail.noiseText,
        blockiness: detail.blockiness, edges: detail.edges,
        motion: detail.orientation.anisotropy > 4.5 ? round(detail.orientation.peak, 0) : null
      },
      frame: {
        orientation: frame.orientation,
        subject: frame.subject,
        thirds: frame.thirds,
        tilt: frame.tilt ? frame.tilt.angle : null,
        text: frame.text, textLines: frame.textLines,
        sky: frame.sky, green: frame.green, skin: frame.skin,
        faces: frame.faces.length, faceBoxes: frame.faces
      },
      notes: notesFrom(meta, frame, color),
      features
    };

    card.score = score(kind, tone, color, detail);
    card.advice = advise(kind, meta, tone, color, detail, frame);
    card.summary = summarize(card);
    return card;
  }

  function safely(action) {
    try { return action(); } catch { return null; }
  }

  /** 256 корзин в карточку не нужны — для рисунка хватает 64. */
  function compressHistogram(hist) {
    const out = [];
    for (let i = 0; i < 64; i++) {
      out.push(round(hist[i * 4] + hist[i * 4 + 1] + hist[i * 4 + 2] + hist[i * 4 + 3], 5));
    }
    return out;
  }

  function notesFrom(meta, frame, color) {
    const notes = [];
    if (frame.faces.length === 1) notes.push('Похоже, в кадре есть человек — нашлось пятно телесного тона подходящей формы.');
    else if (frame.faces.length > 1) notes.push(`Похоже, людей несколько: пятен телесного тона — ${frame.faces.length}.`);
    if (frame.sky > 0.35) notes.push('Верх кадра похож на небо.');
    if (frame.green > 0.25) notes.push('Много зелени — снято, похоже, среди растительности.');
    if (frame.text > 0.25) notes.push(`Есть текст: строк примерно ${frame.textLines}. Прочитать я его не умею — вижу только, что он есть.`);
    if (color.alpha > 0.05) notes.push('Есть прозрачные участки — картинка с вырезанным фоном.');

    const software = (meta.shot && meta.shot.software) || (meta.png && (meta.png.Software || meta.png.parameters));
    if (software && /stable\s*diffusion|midjourney|dall|comfyui|automatic1111|invokeai|flux/i.test(String(software))) {
      notes.push('В метаданных остался след нейросетевого генератора — картинку, похоже, рисовала модель.');
    } else if (meta.shot && meta.shot.software) {
      notes.push(`В метаданных записан редактор: ${meta.shot.software}.`);
    }
    if (meta.shot && meta.shot.gps) {
      notes.push(`В файле осталась геометка: ${ru(meta.shot.gps.lat, 4)}, ${ru(meta.shot.gps.lon, 4)}. ` +
                 'Если картинку куда-то выкладывать — координаты уедут вместе с ней.');
    }
    return notes;
  }

  /* ========================= 13. Просьбы посмотреть ====================== */

  /*
     Признак «попросили посмотреть, но ничего не приложили».

     Правило намеренно строгое: нужен И глагол разглядывания, И предмет
     разглядывания — либо прямой вопрос «что на …». Свободного «скриншот» в
     строке мало, и вот почему.

     «Сделай скриншот» — команда, её выполняет агент, и перехватить её здесь
     значило бы сломать рабочую функцию. «Что такое скриншот» — вопрос о мире,
     на него отвечает языковая модель. Оба содержат нужное слово, и ни один не
     является просьбой посмотреть. Ранняя версия ловила и их: в списке глаголов
     стояло слово «что», и определение уводило приложение искать файл на диске.
  */
  /*
     Границы слов размечаются пробелами, а не \b, и это не вкусовщина.

     В регулярных выражениях JavaScript \b — граница между «словесным» и
     «несловесным» знаком, а словесными там считаются только латиница, цифры и
     подчёркивание. Кириллица для \b — знак несловесный, поэтому \bпосмотри\b
     не совпадает НИКОГДА: слева от «п» стоит пробел, и оба знака для \b
     одинаково несловесные, границы между ними нет.

     Выглядит это особенно скверно: выражение написано, читается правильно, а
     молча не срабатывает ни разу. Поэтому строка приводится к «слова через
     один пробел», и слово ищется вместе с пробелами вокруг — приём топорный,
     зато у него нет тихих отказов.
  */
  const LOOK_VERB = / (посмотри|посмотрите|погляди|глянь|взгляни|разбер[а-я]*|проанализируй|оцени|опиши|изучи|проверь) /;
  const SUBJECT = / (фото[а-я]*|фотк[а-я]*|снимок|снимк[а-я]*|картинк[а-я]*|изображени[а-я]*|скрин[а-я]*) /;
  const SCREEN = / (скрин[а-я]*|снимок экрана|снимка экрана|экран[а-я]*) /;
  const WHATS_ON = / (что|кто|чего) ([а-я]+ ){0,3}на (эт[а-я]+ )?(фото[а-я]*|снимке|картинке|изображении|скрин[а-я]*|экране) /;

  /** Спрашивают ли про картинку, которой ещё нет в сообщении. */
  function asks(text) {
    const value = ' ' + String(text || '')
      .toLowerCase()
      .replace(/ё/g, 'е')
      .replace(/[^a-zа-я0-9]+/g, ' ')
      .trim() + ' ';
    if (value.trim() === '') return null;

    const looks = LOOK_VERB.test(value) && SUBJECT.test(value);
    if (!looks && !WHATS_ON.test(value)) return null;

    // Снимок экрана лежит на диске, и его можно взять самому. За всем прочим
    // придётся открыть окно выбора: где эта картинка, знает только человек.
    return SCREEN.test(value) ? 'screenshot' : 'image';
  }

  /*
     Свежие картинки у агента.

     Нужны ровно для одного сценария, зато частого: «разбери мой последний
     скриншот». Cloud HDR умеет снимать экран сам и складывает снимки в свою
     папку — просить после этого человека найти файл через окно выбора было бы
     насмешкой над собственной же функцией.

     Отдаёт только список; сами байты берутся вторым запросом и только для
     файла из этого списка.
  */
  async function recent(limit = 12) {
    if (!global.Agent || Agent.mode !== 'live') return [];
    try {
      const data = await agentCall('/api/vision/recent', { limit });
      return Array.isArray(data.files) ? data.files : [];
    } catch { return []; }
  }

  async function fromAgent(path, name) {
    if (!global.Agent || Agent.mode !== 'live') throw new Error('Агент не подключён — картинку с диска взять неоткуда');

    const response = await fetch(agentBase() + '/api/vision/file', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(Agent.state.token ? { 'X-Cloud-Token': Agent.state.token } : {})
      },
      body: JSON.stringify({ path })
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `Агент не отдал файл (${response.status})`);
    }

    const blob = await response.blob();
    const file = new File([blob], name || path.split('\\').pop() || 'снимок.png', { type: blob.type });
    return hold(file);
  }

  function agentBase() {
    return location.protocol.startsWith('http') && location.port === '4477'
      ? '' : 'http://127.0.0.1:4477';
  }

  async function agentCall(path, body) {
    const response = await fetch(agentBase() + path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(Agent.state.token ? { 'X-Cloud-Token': Agent.state.token } : {})
      },
      body: JSON.stringify(body || {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || 'Агент отказал');
    return data;
  }

  /* ============================== 14. Наружу ============================= */

  loadSamples();
  fit();

  global.Vision = {
    KINDS, CLASSES, FEATURES, LIMITS,

    accepts, hold, drop, clear, take,
    /** Отпустить разобранную картинку: снимок в памяти браузера весит мегабайты. */
    free: release,
    get pending() { return tray.slice(); },
    get count() { return tray.length; },

    analyze, summarize, brief, format, bytes, ru, pct,
    learn, forget, reload,
    get taught() { return samples.length; },

    asks, recent, fromAgent,

    // наружу — для отладки в консоли и для будущих проверок
    _internals: { raster, luminance, readTone, readColor, readDetail, readFrame, classify, featurize, parseExif, palette }
  };
})(window);
