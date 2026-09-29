"""
Сквозная проверка разбора: фразы, которые действительно говорят.

Отличие от `eval` принципиальное. `eval` считает точность на отложенной
выборке — она порождена теми же шаблонами, пусть и другими значениями, и
меряет обобщение внутри знакомого мира. Здесь набор написан руками, отдельно
от корпуса, и в нём нарочно собрано то, на чём разбор ломался или мог бы
сломаться: «вырубай машину совсем» против «вырубай звук», сленг, опечатки,
пути и домены, вопросы о мире, которые командой не являются.

Проверяется разбор ЦЕЛИКОМ — модель вместе с правилами, — потому что запускают
именно его.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .branding import banner, log, rule
from .parse import DEFAULT_MODEL_DIR, Parser

#: (фраза, ожидаемое намерение, ожидаемая цель или None — цель не проверяем)
CASES: List[Tuple[str, str, Optional[str]]] = [
    # --- запуск программ: словарь, сленг, опечатки ---------------------------
    ("открой хром", "open_app", "chrome"),
    ("запусти дискорд", "open_app", "discord"),
    ("может врубишь дискордик", "open_app", "discord"),
    ("дискорт открой пж", "open_app", "discord"),
    ("мне нужен стим", "open_app", "steam"),
    ("врубай роблокс давай", "open_app", "roblox"),
    ("Открой Телеграм", "open_app", "telegram"),
    ("запусти калькулятор", "open_app", "calc"),

    # --- запуск того, чего нет в словаре: цель должна выделиться -------------
    ("открой фотошоп", "open_app", "фотошоп"),
    ("запусти ксго", "open_app", "ксго"),
    ("включи автокад пожалуйста", "open_app", "автокад"),
    ("открой программу блендер", "open_app", "блендер"),

    # --- закрытие: отличать от запуска --------------------------------------
    ("закрой дискорд", "close_app", "discord"),
    ("заверши стим", "close_app", "steam"),
    ("убей процесс хром", "close_app", "chrome"),
    ("выключи телеграм", "close_app", "telegram"),

    # --- папки и пути --------------------------------------------------------
    ("открой папку загрузки", "open_folder", "downloads"),
    ("покажи рабочий стол", "open_folder", "desktop"),
    ("открой D:\\Games", "open_folder", "D:\\Games"),
    ("зайди в C:\\Windows", "open_folder", "C:\\Windows"),
    ("открой папку проекты", "open_folder", "проекты"),
    ("открой каталог документы", "open_folder", "documents"),

    # --- игры Steam: цель не должна захватить слова про Steam ---------------
    ("запусти в стиме кс 2", "open_app", "кс 2"),
    ("включи кс 2 через стим", "open_app", "кс 2"),
    ("запусти игру амонг ас", "open_app", "амонг ас"),
    ("открой в стиме киберпанк 2077", "open_app", "киберпанк 2077"),
    ("хочу поиграть в гарис мод", "open_app", "гарис мод"),

    # --- адреса --------------------------------------------------------------
    ("открой youtube.com", "open_url", "youtube.com"),
    ("зайди на habr.com", "open_url", "habr.com"),
    ("открой https://claude.ai", "open_url", "https://claude.ai"),

    # --- сайты по имени: адреса в запросе нет, его надо знать ----------------
    ("перейди на сайт гугл карты", "open_url", "https://www.google.com/maps"),
    ("открой гугл карты", "open_url", "https://www.google.com/maps"),
    ("открой ютуб", "open_url", "https://www.youtube.com"),
    ("зайди в вконтакте", "open_url", "https://vk.com"),
    ("открой сайт кинопоиск", "open_url", "https://www.kinopoisk.ru"),
    ("перейди на гитхаб", "open_url", "https://github.com"),
    ("открой яндекс карты", "open_url", "https://yandex.ru/maps"),

    # --- запись экрана -------------------------------------------------------
    ("запиши экран", "record_screen", None),
    ("включи запись экрана", "record_screen", None),
    ("сними видео с экрана", "record_screen", None),
    ("останови запись", "record_stop", None),
    ("хватит записывать", "record_stop", None),
    ("заверши запись экрана", "record_stop", None),

    # --- поиск: в сети и на дисках — разные вещи ------------------------------
    ("загугли рецепт борща", "web_search", "рецепт борща"),
    ("найди в интернете погоду в москве", "web_search", "погоду в москве"),
    ("найди игру амонг ас", "scan_files", "амонг ас"),
    ("поищи на компе ведьмак 3", "scan_files", "ведьмак 3"),
    ("покажи все игры", "scan_files", None),
    ("просканируй диски", "scan_files", None),

    # --- запрещённое: главный разделитель ------------------------------------
    ("вырубай машину совсем", "denied", None),
    ("выключи компьютер", "denied", None),
    ("перезагрузи пк", "denied", None),
    ("отформатируй диск с", "denied", None),
    ("удали все файлы", "denied", None),

    # --- звук: не спутать с выключением ПК -----------------------------------
    ("вырубай звук", "volume_mute", None),
    ("сделай потише", "volume_down", None),
    ("сделай громче", "volume_up", None),
    ("выключи звук", "volume_mute", None),

    # --- точная громкость: направление угадывать нельзя ----------------------
    # «Поставь 30» при текущих 80 — это уменьшение, и без числа команду не
    # выполнить. Поэтому здесь проверяется не только намерение, но и уровень.
    ("поставь громкость на 30", "volume_set", None),
    ("сделай звук на 70 процентов", "volume_set", None),
    ("установи громкость 15", "volume_set", None),
    ("громкость на максимум", "volume_set", None),
    ("выставь звук на половину", "volume_set", None),

    # --- прочие системные ----------------------------------------------------
    ("сделай скриншот", "screenshot", None),
    ("заблокируй пк", "lock", None),
    ("закрепи окно поверх остальных", "window_pin", None),

    # --- навыки --------------------------------------------------------------
    ("посчитай 15% от 800", "math", None),
    ("переведи 5 км в метры", "convert", None),
    ("сколько времени", "datetime", None),
    ("поставь таймер на 5 минут", "timer", "5 минут"),
    ("запомни что мой ник — Миро", "remember", None),
    ("что ты помнишь", "recall", None),

    # --- разговор: команды здесь нет -----------------------------------------
    ("почему небо голубое", "smalltalk", None),
    ("расскажи анекдот", "smalltalk", None),
    ("посоветуй фильм на вечер", "smalltalk", None),

    # --- вежливость и справка ------------------------------------------------
    ("привет", "greeting", None),
    ("спасибо большое", "thanks", None),
    ("что ты умеешь", "help", None),
    ("кто ты такой", "identity", None),
]


def run(model_dir: str = DEFAULT_MODEL_DIR, verbose: bool = True) -> dict:
    parser = Parser.load(model_dir)

    banner("Сквозная проверка разбора")
    rule(f"{'фраза':<38} {'ожидание':<14} {'разбор':<14} цель")

    intent_hits = 0
    target_checked = target_hits = 0
    failures = []

    for phrase, expected_intent, expected_target in CASES:
        result = parser.parse(phrase)
        intent = result["intent"]
        target = result.get("target")

        intent_ok = intent == expected_intent
        intent_hits += intent_ok

        target_ok = True
        if expected_target is not None:
            target_checked += 1
            target_ok = str(target or "").lower() == expected_target.lower()
            target_hits += target_ok

        mark = "OK " if (intent_ok and target_ok) else "!! "
        if verbose:
            log(f"{mark}{phrase:<36.36} {expected_intent:<14} {intent:<14} {target or '—'}")
        if not (intent_ok and target_ok):
            failures.append((phrase, expected_intent, intent, expected_target, target))

    total = len(CASES)
    rule("Итог")
    log(f"Намерение: {intent_hits} из {total} ({intent_hits / total * 100:.0f}%)")
    if target_checked:
        log(f"Цель команды: {target_hits} из {target_checked} ({target_hits / target_checked * 100:.0f}%)")

    if failures:
        rule("Не сошлось")
        for phrase, want_intent, got_intent, want_target, got_target in failures:
            if want_intent != got_intent:
                log(f"«{phrase}» — ждали {want_intent}, получили {got_intent}")
            else:
                log(f"«{phrase}» — намерение верное, цель: ждали «{want_target}», получили «{got_target}»")

    return {
        "cases": total,
        "intent_hits": intent_hits,
        "target_checked": target_checked,
        "target_hits": target_hits,
        "failures": len(failures),
    }
