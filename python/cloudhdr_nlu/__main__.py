"""
Cloud HDR NLU — командный интерфейс.

    python -m cloudhdr_nlu build        собрать корпус из brain.js и шаблонов
    python -m cloudhdr_nlu train        обучить модель понимания команд
    python -m cloudhdr_nlu eval         точность на отложенной выборке
    python -m cloudhdr_nlu check        сквозная проверка на живых фразах
    python -m cloudhdr_nlu try "фраза"  разобрать фразу и показать результат
    python -m cloudhdr_nlu serve        поднять сервис для интерфейса Cloud HDR
"""

from __future__ import annotations

import argparse
import sys

from . import DEFAULT_PORT, PRODUCT, VERSION
from .branding import banner, log, rule


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudhdr_nlu",
        description=f"{PRODUCT} v{VERSION} — понимание команд на spaCy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="собрать обучающий корпус")
    build.add_argument("--out", default="data/nlu")
    build.add_argument("--per-template", type=int, default=14,
                       help="сколько фраз порождать на один шаблон")
    build.add_argument("--augment", type=int, default=3,
                       help="сколько искажённых копий делать на каждую фразу")
    build.add_argument("--seed", type=int, default=20260808)

    train = sub.add_parser("train", help="обучить модель")
    train.add_argument("--data", default="data/nlu")
    train.add_argument("--out", default="models/nlu")
    train.add_argument("--epochs", type=int, default=None)
    train.add_argument("--dropout", type=float, default=None)
    train.add_argument("--seed", type=int, default=None)
    train.add_argument("--skip-build", action="store_true",
                       help="не пересобирать корпус, взять готовый")
    train.add_argument("--light", action="store_true",
                       help="лёгкая сборка: без нейроморфологии, обрезанные векторы")

    ev = sub.add_parser("eval", help="проверить точность")
    ev.add_argument("--model", default="models/nlu/model-best")
    ev.add_argument("--data", default="data/nlu")

    cal = sub.add_parser("calibrate", help="подобрать температуру уверенности")
    cal.add_argument("--model", default="models/nlu/model-best")
    cal.add_argument("--data", default="data/nlu")

    check = sub.add_parser("check", help="сквозная проверка на живых фразах")
    check.add_argument("--model", default="models/nlu/model-best")
    check.add_argument("--quiet", action="store_true", help="показать только итог")

    attempt = sub.add_parser("try", help="разобрать фразу")
    attempt.add_argument("phrase", nargs="+")
    attempt.add_argument("--model", default="models/nlu/model-best")

    serve = sub.add_parser("serve", help="поднять сервис понимания команд")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--model", default="models/nlu/model-best")

    return parser


def cmd_build(args) -> int:
    from .corpus import build

    banner("Сборка корпуса")
    build(out_dir=args.out, per_template=args.per_template,
          augment_factor=args.augment, seed=args.seed)
    log("Дальше: python -m cloudhdr_nlu train")
    return 0


def cmd_train(args) -> int:
    from .corpus import build
    from .train import run

    banner("Обучение модели понимания команд")
    if not args.skip_build:
        rule("Корпус")
        build(out_dir=args.data)

    run(data_dir=args.data, out_dir=args.out, max_epochs=args.epochs,
        dropout=args.dropout, seed=args.seed, light=args.light)
    log("Дальше: python -m cloudhdr_nlu eval")
    return 0


def cmd_eval(args) -> int:
    from .train import evaluate

    banner("Проверка")
    evaluate(model_dir=args.model, data_dir=args.data)
    return 0


def cmd_calibrate(args) -> int:
    from .train import calibrate

    banner("Калибровка уверенности")
    calibrate(args.model, args.data)
    return 0


def cmd_check(args) -> int:
    from .selftest import run

    result = run(model_dir=args.model, verbose=not args.quiet)
    return 0 if result["failures"] == 0 else 1


def cmd_try(args) -> int:
    from .parse import Parser

    phrase = " ".join(args.phrase)
    parser = Parser.load(args.model)
    result = parser.parse(phrase)

    banner("Разбор фразы")
    log(f"Запрос: {phrase}")
    rule()
    log(f"Намерение: {result['intent']} ({result['confidence'] * 100:.0f}%)")
    if result.get("target"):
        log(f"Цель: {result['target']}" + (f" — {result['title']}" if result.get("title") else ""))
    for line in result.get("trace", []):
        log(f"  · {line}")
    if result.get("alternatives"):
        log("Другие версии: " + ", ".join(result["alternatives"]))
    rule()
    return 0


def cmd_serve(args) -> int:
    from .serve import run

    run(host=args.host, port=args.port, model_dir=args.model)
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "build": cmd_build,
        "train": cmd_train,
        "eval": cmd_eval,
        "calibrate": cmd_calibrate,
        "check": cmd_check,
        "try": cmd_try,
        "serve": cmd_serve,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        log("Прервано пользователем")
        return 130
    except (FileNotFoundError, ValueError) as error:
        log(f"Ошибка: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
