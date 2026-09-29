"""
Cloud HDR — ответы о мире из Википедии.

Зачем это здесь, если проект работает офлайн. Собственная языковая модель на
760 млн параметров знаний о мире не имеет и иметь не может: на вопрос «кто такой
Гагарин» она сочиняет правдоподобную неправду. Википедия даёт факт, а не догадку,
и цена этому — единственный сетевой запрос, который в проекте вообще есть.

Он необязателен и отключается: нет интернета — модуль молча возвращает None, и
приложение отвечает как раньше. Ни ключей, ни аккаунтов, ни отправки данных:
уходит только сам вопрос, к открытому API Википедии.

Ответ намеренно короткий. Статья на экран — это не разговор; человек спросил и
хочет услышать суть. Поэтому берётся главная мысль (первое-второе предложение),
из неё вычищается то, что в устной речи не произносят: транскрипции, даты в
скобках, ударения. Дальше фраза подаётся живой связкой — «если коротко», «в
двух словах» — чтобы это читалось как ответ, а не как выписка.
"""

from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import List, Optional

API_SEARCH = "https://ru.wikipedia.org/w/api.php"
API_SUMMARY = "https://ru.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "CloudHDR/1.0 (local assistant)"
TIMEOUT = 6

#: Вопрос о мире? Не всякая фраза без команды достойна похода в сеть.
_QUESTION_RE = re.compile(
    r"^\s*(кто|что|где|когда|почему|зачем|какой|какая|какие|какое|сколько|чем|"
    r"расскажи|объясни|знаешь\s+ли|правда\s+ли)\b", re.IGNORECASE)

#: Слова, после которых начинается собственно предмет вопроса.
_LEAD_RE = re.compile(
    r"^\s*(расскажи(\s+мне)?\s*(про|о|об)?|объясни(\s+мне)?\s*(про|о|об)?|"
    r"что\s+(такое|это\s+за)|кто\s+(такой|такая|такие|это)|"
    r"что\s+ты\s+знаешь\s+(про|о|об)|знаешь\s+(про|о|об)|"
    # Вопросительные слова снимаются обязательно: с ними поиск ищет статью,
    # НАЧИНАЮЩУЮСЯ с этого слова. «Почему идёт дождь» находило сериал «Почему
    # женщины убивают» — совпал зачин, а не смысл. Без «почему» остаётся «идёт
    # дождь», и находится статья «Дождь».
    r"почему|зачем|отчего|каким\s+образом|как\s+(получается|выходит|происходит)|"
    r"сколько|когда|где|куда|откуда|правда\s+ли|верно\s+ли|"
    r"кто|что)\s+", re.IGNORECASE)

#: Служебные слова, которые остаются после снятия вопросительного зачина.
_STOP_HEAD_RE = re.compile(
    r"^\s*(это|такое|такой|такая|нужен|нужна|нужно|бывает|есть|у\s+нас|у\s+меня|"
    r"вообще|именно|ли)\s+", re.IGNORECASE)

#: Хвосты вежливости, которые в заголовок статьи не входят.
_TAIL_RE = re.compile(r"[\s,]*(пожалуйста|плиз|коротко|вкратце|в\s+двух\s+словах)\s*[?!.]*\s*$",
                      re.IGNORECASE)

#: Живые связки. Один и тот же зачин на каждый ответ звучит как автоответчик.
#:
#: Все связки заканчиваются двоеточием или точкой — то есть за ними идёт новое
#: предложение с заглавной буквы. Вариант «насколько знаю, ...» со строчной был
#: убран: он требует опустить первую букву, а она сплошь и рядом оказывается
#: именем — получалось «насколько знаю, юрий Гагарин» и «суть такая: канберра».
_OPENERS = (
    "Если коротко. {}",
    "В двух словах. {}",
    "Кратко: {}",
    "Суть такая: {}",
    "Вот главное. {}",
    "{}",
)


def looks_like_question(text: str) -> bool:
    """Похоже ли на вопрос о мире, ради которого стоит идти в сеть."""
    stripped = (text or "").strip()
    if len(stripped) < 5:
        return False
    return bool(_QUESTION_RE.search(stripped) or stripped.endswith("?"))


#: Разговор о себе и светская болтовня: в Википедии про это нет ничего, а
#: поиск всегда что-нибудь находит. «Как дела?» приносило статью о фильме
#: «Как дома, как дела?».
_PERSONAL_RE = re.compile(
    r"(?<![а-яёa-z])(ты|тебя|тебе|тобой|твой|твоя|твоё|твое|твои|я|меня|мне|мной|мой|моя|моё|мое|мои|"
    r"нас|нам|наш|наша|наши|вы|вас|вам|ваш)(?![а-яёa-z])|"
    r"^\s*как\s+(дела|жизнь|сам|ты|настроение|оно|поживаешь|успехи)|"
    r"^\s*(что|чем)\s+(делаешь|занят|нового)|"
    r"погод|температур\w*\s+(на\s+улице|за\s+окном|сегодня|завтра)|дожд\w*\s+(сегодня|завтра)|"
    r"который\s+час|сколько\s+времени|какое\s+(сегодня\s+)?число",
    re.IGNORECASE)


def is_world_question(text: str) -> bool:
    """Строже looks_like_question: вопрос о мире, а не разговор.

    Нужен вопросительный зачин («кто», «что», «почему», «расскажи») — одного
    знака вопроса мало: «как дела?» и «ты тут?» тоже кончаются на «?».
    """
    stripped = (text or "").strip()
    if len(stripped) < 5 or not _QUESTION_RE.search(stripped):
        return False
    return not _PERSONAL_RE.search(stripped)


def topic_of(text: str) -> str:
    """
    Достаёт предмет вопроса: «расскажи про Байкал» → «Байкал».

    Заголовок статьи ищется по этому куску, а не по всей фразе: «расскажи мне
    пожалуйста про Байкал» в поиске Википедии даёт мусор, «Байкал» — статью.
    """
    value = (text or "").strip().rstrip("?!.").strip()
    value = _LEAD_RE.sub("", value)
    for _ in range(3):
        shorter = _STOP_HEAD_RE.sub("", value)
        if shorter == value:
            break
        value = shorter
    value = _TAIL_RE.sub("", value)
    value = value.strip(" ,.—-")

    # У вопросов «почему/зачем» предмет стоит в конце: «почему идёт дождь» — про
    # дождь, а не про «идёт». Поиск по всей фразе находил «В Сантьяго идёт
    # дождь»: совпало сказуемое, а не предмет. Берём последнее знаменательное
    # слово — в русском вопросе оно и есть подлежащее.
    if re.match(r"^\s*(почему|зачем|отчего)\b", (text or ""), re.IGNORECASE):
        words = [w for w in re.split(r"\s+", value) if len(w) > 2]
        if len(words) > 1:
            value = words[-1]

    return value or (text or "").strip()


def _get(url: str) -> Optional[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------- та ли статья? --

#: Поиск Википедии ВСЕГДА что-нибудь возвращает, и в этом вся беда.
#:
#: Запрос там полнотекстовый: он ищет слова где угодно в статье и ранжирует по
#: своей мере, а не по смыслу вопроса. Пока спрашивают про то, у чего есть
#: одноимённая статья, первый результат верен и проверять нечего. Но стоит
#: спросить про то, чего в русской Википедии нет под этим именем, — и первым
#: приходит случайная статья, где нужные слова просто встретились в тексте.
#:
#: Живой промах, ради которого всё это и написано:
#:
#:     «что такое гаррис мод» → «Рихтер, Энди»
#:
#: Игра Garry's Mod в русской Википедии называется латиницей, перенаправления
#: «Гаррис Мод» на неё нет, и поиск отдал статью про американского комика: в
#: списке его ролей нашлось слово «Гаррис». Дальше хуже — справка отдаётся
#: пользователю ДОСЛОВНО и со ссылкой на источник (knowledge.direct_answer), то
#: есть приложение уверенно и со ссылкой рассказывало про актёра.
#:
#: Такой ответ хуже незнания: выдумку модели человек ещё поставит под сомнение, а
#: цитату из Википедии — нет. Поэтому найденная статья теперь проверяется, и не
#: прошедшая проверку отбрасывается: пусть лучше отвечает сама модель.
_WORD_RE = re.compile(r"[a-zа-я0-9]+")

#: Слова, которые есть в вопросе, но ничего не говорят о его предмете.
#:
#: Список нужен потому, что двухбуквенные слова из проверки не выброшены. Их
#: заманчиво было бы отсечь по длине — предлогов в русском как раз два-три
#: знака, — но вместе с ними ушли бы аббревиатуры, а «что такое ИИ» спрашивают
#: чаще, чем всё остальное: у статьи «Искусственный интеллект» есть
#: перенаправление «ИИ», и по нему вопрос попадает точно в цель.
_NOISE = {"это", "эти", "этот", "эта", "такое", "такой", "такая", "такие",
          "вообще", "именно", "типа", "the", "and", "for",
          "на", "по", "за", "из", "от", "до", "об", "во", "со", "ко",
          "не", "ни", "но", "то", "же", "ли", "бы", "их", "ее",
          "он", "она", "они", "мы", "вы", "ты", "про", "для", "под",
          "над", "без", "или", "при", "как", "что", "кто", "где", "чем"}

#: Русские окончания — чтобы «чёрные дыры» узнавались в статье «Чёрная дыра».
#:
#: Это не морфология, а её дешёвая замена: отрезаем хвост, если от слова
#: остаётся хотя бы три буквы. Полноценный анализ потребовал бы pymorphy, а он
#: тут не окупается — сравниваются два-три слова заголовка.
_ENDINGS = ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях",
            "ов", "ев", "ой", "ей", "ая", "яя", "ое", "ее", "ые", "ие", "ый",
            "ий", "ым", "им", "ом", "ем", "ью", "ья", "ям", "ам", "а", "я",
            "ы", "и", "е", "о", "у", "ю", "ь", "й")


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _words(text: str) -> List[str]:
    """Значимые слова: строчные, без ё, без однобуквенных и служебных."""
    found = _WORD_RE.findall((text or "").lower().replace("ё", "е"))
    return [_stem(w) for w in found if len(w) >= 2 and w not in _NOISE]


def _same_word(one: str, other: str) -> bool:
    """
    Одно ли это слово с точностью до падежа, опечатки и транслитерации.

    Порог намеренно мягкий: он должен пропускать «garris» к «Garry» — иначе
    латинский запасной путь ниже не найдёт ничего. Строгость здесь и не нужна:
    совпасть обязаны ВСЕ слова предмета, а случайной статье это не под силу.
    """
    if one == other:
        return True
    short, long_ = sorted((one, other), key=len)
    if len(short) >= 4 and long_.startswith(short):
        return True
    return len(short) >= 5 and SequenceMatcher(None, one, other).ratio() >= 0.7


def _about(topic_words: List[str], *titles: Optional[str]) -> bool:
    """Правда ли статья о предмете вопроса, а не просто содержит его слова."""
    haystack: List[str] = []
    for title in titles:
        haystack += _words(title or "")
    if not topic_words or not haystack:
        return False
    return all(any(_same_word(word, known) for known in haystack)
               for word in topic_words)


#: Кириллица → латиница: без этого до статей с латинским названием не дойти.
_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _to_latin(text: str) -> str:
    return "".join(_LATIN.get(ch, ch) for ch in (text or "").lower())


def _search(query: str, limit: int = 5) -> List[dict]:
    """
    Список кандидатов. redirecttitle говорит, ПОЧЕМУ статья нашлась.

    Свойство важное: «майнкрафт» приводит к статье «Minecraft», и по одному
    заголовку родство не разглядеть — общих букв нет вовсе. Но поиск сообщает,
    что попал через перенаправление «Майнкрафт», и этого достаточно.
    """
    url = API_SEARCH + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": limit, "srprop": "redirecttitle", "format": "json", "utf8": 1,
    })
    data = _get(url)
    return ((data or {}).get("query") or {}).get("search") or []


def find_title(query: str) -> Optional[str]:
    """
    Заголовок статьи ПРО ЗАПРОШЕННОЕ либо None, если такой не нашлась.

    None здесь — полноценный и частый ответ, а не сбой: в русской Википедии
    просто нет статьи про каждое, о чём спрашивают. Раньше на этом месте
    возвращался первый результат поиска, чем бы он ни был, и «нет статьи»
    превращалось в «есть, но не та».
    """
    wanted = _words(query)
    if not wanted:
        return None

    for candidate in _search(query):
        if _about(wanted, candidate.get("title"), candidate.get("redirecttitle")):
            return candidate["title"]

    # Запасной путь для игр, программ и прочих названий, которые по-русски
    # произносят, а пишут латиницей. Поиск по «гаррис мод» не находит ничего
    # похожего, зато по «garris~ mod~» первым идёт «Garry’s Mod»: тильда просит
    # Википедию искать с точностью до опечаток, а транслитерация как раз и
    # выглядит для неё опечаткой в английском слове.
    latin = _to_latin(query)
    if latin != (query or "").lower():
        fuzzy = " ".join(w + "~" for w in _WORD_RE.findall(latin) if len(w) >= 3)
        if fuzzy:
            wanted_latin = _words(latin)
            for candidate in _search(fuzzy):
                if _about(wanted_latin, candidate.get("title"),
                          candidate.get("redirecttitle")):
                    return candidate["title"]

    return None


def summary(title: str) -> Optional[str]:
    data = _get(API_SUMMARY + urllib.parse.quote(title.replace(" ", "_")))
    if not data or data.get("type") == "disambiguation":
        return None
    return (data.get("extract") or "").strip() or None


# ------------------------------------------------------------ главная мысль --

#: Скобки с транскрипцией, датами, латиницей — то, что вслух не произносят.
_BRACKETS_RE = re.compile(r"\s*\([^)]*\)")
_STRESS_RE = re.compile(r"[̀-ͯ́]")
_SPACES_RE = re.compile(r"\s+")


def main_idea(text: str, sentences: int = 1, limit: int = 260) -> str:
    """
    Оставляет от статьи главную мысль.

    Правка чисто механическая, и это честно: перефразировать по-настоящему
    может только языковая модель, а наша для этого слаба — она бы исказила факт.
    Поэтому факт остаётся дословным, а убирается лишь то, что мешает его
    услышать: ударения, скобки со справкой, повторные пробелы.
    """
    clean = _STRESS_RE.sub("", text or "")
    clean = _BRACKETS_RE.sub("", clean)
    clean = _SPACES_RE.sub(" ", clean).strip()
    if not clean:
        return ""

    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", clean)
    idea = " ".join(parts[:sentences]).strip()

    if len(idea) > limit:
        cut = idea.rfind(". ", 0, limit)
        idea = idea[:cut + 1] if cut > 60 else idea[:limit].rsplit(" ", 1)[0] + "…"
    return idea


def answer(question: str, seed: Optional[int] = None) -> Optional[dict]:
    """
    Ответ на вопрос о мире или None, если ответа нет.

    :returns: {"text": живой ответ, "title": статья, "url": ссылка}
    """
    topic = topic_of(question)
    if len(topic) < 2:
        return None

    title = find_title(topic)
    if not title:
        return None

    extract = summary(title)
    if not extract:
        return None

    idea = main_idea(extract)
    if not idea:
        return None

    rng = random.Random(seed if seed is not None else hash(title) & 0xFFFF)
    opener = rng.choice(_OPENERS)

    return {
        "text": opener.format(idea),
        "title": title,
        "url": "https://ru.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
    }


if __name__ == "__main__":
    import sys

    result = answer(" ".join(sys.argv[1:]) or "кто такой гагарин")
    print(json.dumps(result, ensure_ascii=False, indent=2) if result else "ответа нет")
