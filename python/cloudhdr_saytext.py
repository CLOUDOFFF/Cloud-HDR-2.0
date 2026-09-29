"""
Cloud HDR — текст для голоса: то, что написано, превращается в то, что произносится.

XTTS обучен на русской речи, записанной словами. Цифры, латиница и сокращения
он читает наугад — отсюда «коверкание»: «0,6» звучит как набор слогов,
«Windows» — как «виндо-увс», «ПК» — как «пк» одним звуком, а после короткой
фразы модель иногда дописывает бормотание. Здесь текст приводится к словам
ДО синтеза:

    числа, время, дроби, проценты   → слова с правильным родом и падежом
    латиница                        → русская запись (словарь + транслит)
    сокращения                      → как их произносят

Функция say() идемпотентна: её можно звать поверх уже готового текста.
Проверка: ..\\runtime\\python.exe -m tests.test_saytext (из папки python).
"""
from __future__ import annotations

import re

from num2words import num2words

# ------------------------------------------------------------ словари -----

# Слова и марки, которые звучат не так, как читаются по буквам.
LATIN = {
    'cloud': 'клауд', 'hdr': 'эйч ди ар', 'windows': 'виндоус', 'youtube': 'ютуб',
    'google': 'гугл', 'chrome': 'хром', 'telegram': 'телеграм', 'discord': 'дискорд',
    'steam': 'стим', 'yandex': 'яндекс', 'microsoft': 'майкрософт', 'word': 'ворд',
    'excel': 'эксель', 'powerpoint': 'пауэр поинт', 'wifi': 'вайфай', 'wi-fi': 'вайфай',
    'bluetooth': 'блютус', 'ok': 'окей', 'okay': 'окей', 'email': 'имейл', 'e-mail': 'имейл',
    'python': 'пайтон', 'github': 'гитхаб', 'rutube': 'рутуб', 'spotify': 'спотифай',
    'whatsapp': 'ватсап', 'iphone': 'айфон', 'android': 'андроид', 'nvidia': 'энвидиа',
    'intel': 'интел', 'qwen': 'квен', 'whisper': 'виспер', 'dota': 'дота',
    'minecraft': 'майнкрафт', 'roblox': 'роблокс', 'tiktok': 'тикток', 'instagram': 'инстаграм',
    'twitch': 'твич', 'edge': 'эдж', 'firefox': 'файрфокс', 'opera': 'опера', 'store': 'стор',
    'online': 'онлайн', 'offline': 'офлайн', 'update': 'апдейт', 'explorer': 'эксплорер',
    'notepad': 'ноутпад', 'paint': 'пэйнт', 'studio': 'студио', 'code': 'код',
    'vk': 'вэ ка', 'geforce': 'джифорс', 'radeon': 'радеон', 'ryzen': 'райзен',
    'office': 'офис', 'zoom': 'зум', 'skype': 'скайп', 'netflix': 'нетфликс',
    'gmail': 'джимейл', 'mail': 'мейл', 'music': 'мьюзик', 'player': 'плеер',
    'max': 'макс', 'pro': 'про', 'plus': 'плюс', 'mini': 'мини', 'lite': 'лайт',
    'garry': 'гэрри', 'mod': 'мод', 'resident': 'резидент', 'evil': 'ивл',
    'the': 'зе', 'and': 'энд', 'of': 'оф', 'new': 'нью', 'my': 'май', 'you': 'ю',
    'hello': 'хэллоу', 'hi': 'хай', 'bye': 'бай', 'yes': 'йес', 'no': 'ноу',
    'wave': 'вейв', 'mesh': 'меш', 'gradient': 'градиент', 'chat': 'чат', 'bot': 'бот',
}

# Английские буквы — как их называют вслух (для аббревиатур: GPU, USB, PDF).
LETTERS = {
    'a': 'эй', 'b': 'би', 'c': 'си', 'd': 'ди', 'e': 'и', 'f': 'эф', 'g': 'джи', 'h': 'эйч',
    'i': 'ай', 'j': 'джей', 'k': 'кей', 'l': 'эл', 'm': 'эм', 'n': 'эн', 'o': 'оу', 'p': 'пи',
    'q': 'кью', 'r': 'ар', 's': 'эс', 't': 'ти', 'u': 'ю', 'v': 'ви', 'w': 'дабл ю',
    'x': 'экс', 'y': 'уай', 'z': 'зед',
}

# Русские сокращения — как их произносят.
RU_ABBR = {
    'ПК': 'пэ ка', 'ИИ': 'и и', 'ОС': 'о эс', 'США': 'сэ шэ а', 'РФ': 'эр эф',
    'ОЗУ': 'о зэ у', 'ЦП': 'цэ пэ', 'ГПУ': 'гэ пэ у', 'ТВ': 'тэ вэ', 'СМС': 'эс эм эс',
    'ГБ': 'гигабайт', 'МБ': 'мегабайт', 'КБ': 'килобайт', 'ТБ': 'терабайт',
    'ГГц': 'гигагерц', 'МГц': 'мегагерц', 'Гц': 'герц', 'км': 'километров', 'кг': 'килограмм',
    'мс': 'миллисекунд', 'т.е.': 'то есть', 'т.к.': 'так как', 'и т.д.': 'и так далее',
    'и т.п.': 'и тому подобное', 'млн': 'миллионов', 'млрд': 'миллиардов',
    'тыс.': 'тысяч', 'руб.': 'рублей',
}

SYMBOLS = [
    (r'\s*\+\s*', ' плюс '), (r'\s*=\s*', ' равно '), (r'\s*&\s*', ' и '), (r'№\s*', 'номер '),
    (r'\s*°\s*[CС]\b', ' градусов'), (r'\s*°', ' градусов'), (r'\s*₽', ' рублей'),
    (r'\$\s*(\d[\d\s]*)', r'\1 долларов'), (r'\s*€', ' евро'),
]

WORD = r'А-Яа-яЁёA-Za-z0-9_'
NB = rf'(?<![{WORD}])'      # начало слова (\b не видит кириллицу в части движков)
NE = rf'(?![{WORD}])'       # конец слова


# ------------------------------------------------------------- числа -------

def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _int_words(n: int, feminine: bool | str = False) -> str:
    """feminine: True — «одна/две», 'acc' — «одну/две» (через одну минуту)."""
    words = num2words(n, lang='ru')
    if feminine:
        words = re.sub(r'(?:^|(?<= ))один$', 'одну' if feminine == 'acc' else 'одна', words)
        words = re.sub(r'(?:^|(?<= ))два$', 'две', words)
    return words


# Мужской род с окончанием на -а/-ы/-и после числа: «2 файла», «3 раза».
_MASC_AFTER_NUMBER = re.compile(
    r'(?:файл|раз|час|день|дн|год|процент|гигабайт|мегабайт|килобайт|рубл|доллар|градус|шаг|балл|'
    r'пункт|урок|город|дом|месяц|километр|метр|байт|бит|кадр|фрагмент)[а-яё]*')


def _feminine_next(word: str) -> bool | str:
    """Женский род по окончанию: «одна минута», «две папки», «одну вкладку».
    Грубо, но для чисел в ответах хватает; мужские на -а/-ы — исключения."""
    w = word.lower()
    if _MASC_AFTER_NUMBER.fullmatch(w):
        return False
    if re.fullmatch(r'[а-яё]+[ую]', w):
        return 'acc'
    return bool(re.fullmatch(r'[а-яё]+[аяыи]', w))


def _numbers(text: str) -> str:
    # 1 000 000 → 1000000 (пробел как разделитель тысяч)
    text = re.sub(r'(?<=\d)[\s ](?=\d{3}(?!\d))', '', text)

    # версии: «HDR 2.0», «v1.2» — «два ноль», как говорят вслух
    # только после латиницы или слова «версия» — «в 3.5 раза» остаётся дробью
    def version(m):
        return m.group(1) + ' '.join(_int_words(int(p)) for p in m.group(2).split('.'))
    text = re.sub(rf'((?:[A-Za-z]|верси[яиюей])\s?)(\d+(?:\.\d+)+){NE}', version, text)

    # время 14:30, 9:05
    def clock(m):
        h, mi = int(m.group(1)), m.group(2)
        tail = 'ноль ' + _int_words(int(mi)) if mi.startswith('0') and mi != '00' else ('ноль ноль' if mi == '00' else _int_words(int(mi)))
        return f'{_int_words(h)} {tail}'
    text = re.sub(rf'{NB}([01]?\d|2[0-3]):([0-5]\d){NE}', clock, text)

    # проценты — с согласованием
    def percent(m):
        raw = m.group(1).replace(',', '.')
        if '.' in raw:
            return f'{_decimal(raw)} процента'
        n = int(raw)
        return f'{_int_words(n)} {_plural(n, "процент", "процента", "процентов")}'
    text = re.sub(r'(\d+(?:[.,]\d+)?)\s*(?:%|процент(?:а|ов)?' + NE + ')', percent, text)

    # порядковые: 1-й, 2-я, 3-е, 5-го
    def ordinal(m):
        base = num2words(int(m.group(1)), lang='ru', to='ordinal')
        end = m.group(2)
        stem = re.sub(r'(ый|ий|ой)$', '', base)
        soft = base.endswith('ий')
        forms = {'й': base, 'я': stem + ('яя' if soft else 'ая'), 'е': stem + ('ее' if soft else 'ое'),
                 'го': stem + ('его' if soft else 'ого'), 'му': stem + ('ему' if soft else 'ому'),
                 'м': stem + ('им' if soft else 'ым'), 'х': stem + ('их' if soft else 'ых')}
        return forms.get(end, base)
    text = re.sub(rf'{NB}(\d+)-(й|я|е|го|му|м|х){NE}', ordinal, text)

    # диапазоны 5-10 → «пять — десять»
    text = re.sub(r'(\d)\s*[-–—]\s*(\d)', r'\1 — \2', text)

    # дроби 0,6 / 3.5
    text = re.sub(rf'{NB}(\d+[.,]\d+){NE}', lambda m: _decimal(m.group(1)), text)

    # целые — с родом по следующему слову
    def integer(m):
        nxt = m.group(2)
        return _int_words(int(m.group(1)), feminine=_feminine_next(nxt) if nxt else False)
    text = re.sub(r'(\d+)(?=(?:\s+([А-Яа-яЁё]+))?)', integer, text)
    return text


def _decimal(raw: str) -> str:
    whole, _, frac = raw.replace(',', '.').partition('.')
    frac = frac.rstrip('0') or '0'
    if frac == '0':
        return _int_words(int(whole))
    w = int(whole)
    f = int(frac)
    unit = {1: ('десятая', 'десятых', 'десятых'), 2: ('сотая', 'сотых', 'сотых'),
            3: ('тысячная', 'тысячных', 'тысячных')}.get(len(frac))
    if unit is None:                                   # длинный хвост — по цифрам
        return f'{_int_words(w)} точка ' + ' '.join(_int_words(int(d)) for d in frac)
    whole_word = 'целая' if w % 10 == 1 and w % 100 != 11 else 'целых'
    return f'{_int_words(w, feminine=True)} {whole_word} {_int_words(f, feminine=True)} {_plural(f, *unit)}'


# ----------------------------------------------------------- латиница ------

_TRANSLIT = [
    ('tion', 'шн'), ('sh', 'ш'), ('ch', 'ч'), ('th', 'т'), ('ph', 'ф'), ('oo', 'у'), ('ee', 'и'),
    ('ea', 'и'), ('ck', 'к'), ('qu', 'кв'), ('ou', 'ау'), ('ow', 'оу'), ('ai', 'эй'), ('ay', 'эй'),
    ('oy', 'ой'), ('igh', 'ай'), ('kh', 'х'), ('zh', 'ж'), ('ts', 'ц'), ('ya', 'я'), ('yu', 'ю'),
]
_CHAR = {'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г', 'h': 'х', 'i': 'и',
         'j': 'дж', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'q': 'к', 'r': 'р',
         's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс', 'y': 'и', 'z': 'з'}


def _translit(word: str) -> str:
    w = word.lower()
    w = re.sub(r'c(?=[eiy])', 's', w)
    w = re.sub(r'^y(?=[aeiou])', 'й', w)
    # немая e на конце: «game», «store» — если до неё в слове есть гласная
    if len(w) > 3 and w.endswith('e') and not w.endswith('ee') and re.search(r'[aeiouy]', w[:-1]):
        w = w[:-1]
    for a, b in _TRANSLIT:
        w = w.replace(a, b)
    return ''.join(_CHAR.get(ch, ch) for ch in w)


def _latin(text: str) -> str:
    def word(m):
        w = m.group(0)
        low = w.lower()
        if low in LATIN:
            return LATIN[low]
        # аббревиатура: 2–5 заглавных (GPU, USB, PDF, RTX) — по буквам
        if re.fullmatch(r'[A-Z]{2,5}', w):
            return ' '.join(LETTERS[c.lower()] for c in w)
        if len(w) == 1:
            return LETTERS.get(low, w)
        return _translit(w)
    def token(m):
        whole = m.group(0)
        if whole.lower().endswith("'s"):               # «Garry's» → «гэррис»
            return token(re.match(r'.+', whole[:-2])) + 'с'
        if whole.lower() in LATIN:                    # «wi-fi», «e-mail» целиком
            return LATIN[whole.lower()]
        return ' '.join(word(re.match(r'.+', part)) for part in whole.split('-'))
    return re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)?(?:'s)?", token, text)


# ------------------------------------------------------------ главное ------

def say(text: str) -> str:
    """Текст → то, что XTTS произнесёт правильно."""
    out = str(text or '')
    out = re.sub(r'https?://\S+', ' ссылка ', out)
    out = re.sub(r'[←-⇿☀-➿\U0001F300-\U0001FAFF️]', ' ', out)
    for abbr in sorted(RU_ABBR, key=len, reverse=True):
        out = re.sub(NB + re.escape(abbr) + (NE if abbr[-1] != '.' else ''), RU_ABBR[abbr], out)
    for pattern, repl in SYMBOLS:
        out = re.sub(pattern, repl, out)
    out = _numbers(out)
    out = _latin(out)
    out = re.sub(r'[«»"“”„`*_#|<>\[\]{}\\/]', ' ', out)
    out = re.sub(r'\s*[·•]\s*', ', ', out)
    out = re.sub(r'\s+([,.!?…:;])', r'\1', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    # Фраза без точки в конце — частая причина «хвоста»: модель не знает, где
    # остановиться, и дописывает бормотание.
    if out and out[-1] not in '.!?…':
        out += '.'
    return out


def words_of(text: str) -> str:
    """Для сравнения сказанного с задуманным: только буквы, ё→е, нижний регистр."""
    t = say(text).lower().replace('ё', 'е')
    return ' '.join(re.findall(r'[а-яa-z]+', t))
