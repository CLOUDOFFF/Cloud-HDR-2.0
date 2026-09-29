"""
HTTP-сервис понимания команд.

Слушает 127.0.0.1:4478 — рядом с агентом (4477) и языковой моделью (8080).
Отдельный процесс, а не эндпоинт внутри агента, по той же причине, по которой
отдельно живёт модель: загрузка spaCy занимает секунду с лишним, и агент,
который должен отвечать на «открой хром» мгновенно, не обязан её ждать. Упал
сервис — интерфейс просто вернётся к brain.js в браузере и продолжит работать.

CORS открыт для локальных адресов: страница отдаётся с 4477, запрос уходит на
4478, и без заголовков доступа браузер его не выпустит.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from . import DEFAULT_PORT, PRODUCT, VERSION
from .branding import banner, log, rule
from .mind import Mind
from .parse import DEFAULT_MODEL_DIR, Parser

FEEDBACK_PATH = Path("data/nlu/feedback.jsonl")


# Модели запросов объявлены на уровне модуля, и это не стилистика.
# Вверху файла стоит `from __future__ import annotations`, из-за которого все
# аннотации становятся строками, а FastAPI разрешает их через get_type_hints по
# глобальным именам модуля. Пока эти классы жили внутри build_app, имя
# «ParseRequest» снаружи не находилось, FastAPI не узнавал в нём модель pydantic
# и считал параметр строковым — то есть ждал его в query, а не в теле запроса.
# Проявлялось это как 422 на каждый разбор, и ни одна проверка модели такого не
# видит: ломался только HTTP.
class ParseRequest(BaseModel):
    text: str
    context: Optional[Dict[str, Any]] = None


class FeedbackRequest(BaseModel):
    text: str
    intent: str


def build_app(model_dir: str = DEFAULT_MODEL_DIR):
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    parser = Parser.load(model_dir)
    mind = Mind(parser)
    started = time.time()
    counters = {"parsed": 0, "feedback": 0}

    app = FastAPI(title=f"{PRODUCT} v{VERSION}")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/nlu/health")
    def health() -> dict:
        return {
            "ok": True,
            "product": PRODUCT,
            "version": VERSION,
            "model": str(parser.model_dir),
            "components": parser.nlp.pipe_names,
            "temperature": parser.temperature,
            "intents": parser.lex.intents,
            "uptime": round(time.time() - started, 1),
            "parsed": counters["parsed"],
        }

    @app.post("/nlu/parse")
    def parse(request: ParseRequest) -> dict:
        text = (request.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Пустой запрос")
        if len(text) > 2000:
            text = text[:2000]

        started_at = time.perf_counter()
        result = parser.parse(text, request.context)
        result["ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        counters["parsed"] += 1
        return result

    @app.post("/nlu/plan")
    def plan(request: ParseRequest) -> dict:
        """
        Разбор целиком: отмена, контекст, несколько команд в одной фразе.

        Отдельно от /nlu/parse, а не вместо него: тот отвечает одним намерением
        и таким останется — на него завязаны и проверки, и внешние вызовы.
        Здесь ответ другой формы: список шагов и маршрут, что с ними делать.
        """
        text = (request.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Пустой запрос")

        started_at = time.perf_counter()
        result = mind.plan(text[:2000], request.context)
        result["ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        counters["parsed"] += 1
        return result

    @app.get("/files/info")
    def files_info() -> dict:
        import cloudhdr_files

        return cloudhdr_files.index_info()

    @app.post("/files/index")
    def files_index() -> dict:
        """
        Строит индекс имён файлов. Разовая операция: обход дисков занимает
        полминуты, после неё поиск идёт по индексу за миллисекунды.
        """
        import cloudhdr_files

        return cloudhdr_files.build_index(quiet=True)

    @app.get("/files/search")
    def files_search(q: str, limit: int = 25) -> dict:
        """
        Поиск файла по имени, в том числе русским названием английского файла:
        «клоуд мап» находит Cloud map. Подробности сопоставления — в
        cloudhdr_files.loose().
        """
        import cloudhdr_files

        query = (q or "").strip()
        if len(query) < 2:
            raise HTTPException(status_code=400, detail="Слишком короткий запрос")

        started_at = time.perf_counter()
        try:
            items = cloudhdr_files.search(query, limit=max(1, min(limit, 50)))
        except FileNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error))

        return {"query": query, "items": items,
                "ms": round((time.perf_counter() - started_at) * 1000, 1)}

    @app.post("/nlu/feedback")
    def feedback(request: FeedbackRequest) -> dict:
        """
        Исправление пользователя. Копится в файл и попадает в обучение при
        следующем `train` — так же, как поправки в brain.js копились в
        localStorage. Дообучать на одном примере на лету бессмысленно: сеть на
        13 тысячах фраз от одной новой не сдвинется, а испортиться может.
        """
        if request.intent not in parser.lex.intents:
            raise HTTPException(status_code=400, detail=f"Неизвестное намерение: {request.intent}")

        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"text": request.text, "intent": request.intent, "ts": int(time.time())},
                ensure_ascii=False,
            ) + "\n")

        counters["feedback"] += 1
        return {"ok": True, "saved": counters["feedback"], "path": str(FEEDBACK_PATH)}

    return app


def ensure_file_index() -> None:
    """
    Строит индекс файлов, если его ещё нет.

    Делается при запуске сервиса, а не по первому запросу пользователя: обход
    дисков занимает полминуты, и человек, спросивший «найди клоуд мап», не
    должен ждать её молча. Индекс уже есть — ничего не происходит.
    """
    try:
        import cloudhdr_files
    except ImportError:
        return

    info = cloudhdr_files.index_info()
    if info.get("exists"):
        log(f"Индекс файлов: {info['files']} записей · {info['mb']} МБ · от {info['built']}")
        return

    log("Индекса файлов нет — строю (разово, около полуминуты)...")
    try:
        result = cloudhdr_files.build_index(quiet=True)
        log(f"Индекс готов: {result['files']} записей · {result['mb']} МБ · {result['seconds']} с")
    except Exception as error:
        log(f"Индекс построить не удалось: {error}. Поиск пойдёт обходом дисков.")


def run(host: str = "127.0.0.1", port: int = DEFAULT_PORT, model_dir: str = DEFAULT_MODEL_DIR) -> None:
    import uvicorn

    banner("Сервис понимания команд")
    app = build_app(model_dir)
    ensure_file_index()

    rule()
    log(f"Слушаю http://{host}:{port}")
    log("Проверка:  /nlu/health   ·  разбор:  /nlu/parse")
    log("Интерфейс Cloud HDR найдёт сервис сам. Ctrl+C — остановить.")
    rule()

    uvicorn.run(app, host=host, port=port, log_level="warning")
