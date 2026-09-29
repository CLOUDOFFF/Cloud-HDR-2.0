"""
Cloud HDR AI — командный интерфейс.

    python -m cloudhdr_ai info                     паспорт железа и готовность
    python -m cloudhdr_ai dialogues                сборка диалогового корпуса
    python -m cloudhdr_ai ultra                    корпус для Cloud HDR Ultra
    python -m cloudhdr_ai train --from-brain       дообучение на корпусе проекта
    python -m cloudhdr_ai eval                     перплексия и живые ответы
    python -m cloudhdr_ai serve                    сервер для интерфейса Cloud HDR
    python -m cloudhdr_ai chat                     разговор прямо в терминале
    python -m cloudhdr_ai verify                   сверка реализации с эталоном HF
"""

from __future__ import annotations

import argparse
import sys

from .branding import PRODUCT, VERSION, banner, log, rule
from .config import DEFAULT_MODEL, ALIASES, TrainConfig
from .corpus import DEFAULT_SOURCE as CORPUS_DEFAULT, SOURCES as CORPUS_SOURCES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudhdr_ai",
        description=f"{PRODUCT} v{VERSION} — своя языковая модель Cloud HDR",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------ info --
    info = sub.add_parser("info", help="показать железо, версии и оценку памяти")
    info.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])

    # ----------------------------------------------------------------- train --
    defaults = TrainConfig()
    train = sub.add_parser("train", help="дообучить модель", formatter_class=parser.formatter_class)
    train.add_argument("--model", default=DEFAULT_MODEL,
                       help=f"болванка Hugging Face ({', '.join(sorted(ALIASES))})")
    train.add_argument("--data", default=None, help="файл или папка с .txt / .jsonl")
    train.add_argument("--from-brain", action="store_true",
                       help="добавить корпус классификатора из public/js/brain.js")
    train.add_argument("--resume", default=None, help="продолжить с чекпоинта")

    train.add_argument("--batch-size", type=int, default=defaults.batch_size,
                       help="микробатч: 4 или 8 на 8 ГБ видеопамяти")
    train.add_argument("--grad-accum", type=int, default=defaults.grad_accum_steps,
                       help="шагов накопления градиента")
    train.add_argument("--block-size", type=int, default=defaults.block_size,
                       help="длина обучающей последовательности (максимум 1024)")
    train.add_argument("--steps", type=int, default=defaults.max_steps, help="шагов оптимизатора")
    train.add_argument("--max-minutes", type=float, default=None,
                       help="остановиться по времени, не дожидаясь --steps")
    train.add_argument("--lr", type=float, default=defaults.learning_rate)
    train.add_argument("--warmup", type=int, default=defaults.warmup_steps)
    train.add_argument("--dropout", type=float, default=defaults.dropout)
    train.add_argument("--precision", default="auto", choices=["auto", "bf16", "fp16", "fp32"],
                       help="auto = bf16 на Blackwell, иначе fp16")
    train.add_argument("--no-checkpointing", dest="grad_checkpointing", action="store_false",
                       help="не пересчитывать активации (быстрее, но памяти нужно больше)")
    train.add_argument("--compile", action="store_true", help="включить torch.compile")
    train.add_argument("--lora", action="store_true",
                       help="обучать поправки низкого ранга вместо всех весов — "
                            "нужно для моделей, которые целиком в память не влезают")
    train.add_argument("--lora-rank", type=int, default=defaults.lora_rank)
    train.add_argument("--lora-alpha", type=int, default=defaults.lora_alpha)
    train.add_argument("--neftune", type=float, default=defaults.neftune_alpha,
                       help="NEFTune: шум на эмбеддингах при обучении (5 — для диалога, 0 — выкл)")
    train.add_argument("--out", default=defaults.out_dir, help="куда класть чекпоинты")
    train.add_argument("--seed", type=int, default=defaults.seed)
    train.add_argument("--eval-interval", type=int, default=defaults.eval_interval,
                       help="как часто проверяться на отложенной выборке")
    train.add_argument("--log-interval", type=int, default=defaults.log_interval,
                       help="как часто печатать строку о ходе обучения")
    train.add_argument("--save-interval", type=int, default=defaults.save_interval,
                       help="как часто сохранять last.pt")
    train.add_argument("--val-split", type=float, default=defaults.val_split,
                       help="доля данных на отложенную выборку")
    train.set_defaults(grad_checkpointing=defaults.grad_checkpointing)

    # ----------------------------------------------------------------- serve --
    serve = sub.add_parser("serve", help="поднять сервер модели")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080,
                       help="8080, 1234 или 11434 — их llm.js находит сам")
    # Основные веса — checkpoints/v6 (760M, дообучена на коде и стихах). Если их
    # нет, serve.resolve_checkpoint сам отступит на v5, затем на v4 и так до
    # самых первых, поэтому смена значения по умолчанию ничего не ломает у тех,
    # кто ещё не переобучался.
    serve.add_argument("--checkpoint", default="checkpoints/v7/best.pt")
    serve.add_argument("--model", default=DEFAULT_MODEL)
    serve.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    serve.add_argument("--precision", default="auto", choices=["auto", "bf16", "fp16", "fp32"])
    serve.add_argument("--no-wiki", dest="wiki", action="store_false",
                       help="не подставлять справку из Википедии; модель будет "
                            "отвечать по памяти и чаще выдумывать факты")
    serve.set_defaults(wiki=True)

    # ------------------------------------------------------------------ chat --
    chat = sub.add_parser("chat", help="разговор в терминале")
    chat.add_argument("--checkpoint", default="checkpoints/v7/best.pt")
    chat.add_argument("--model", default=DEFAULT_MODEL)
    chat.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    chat.add_argument("--temperature", type=float, default=0.8)
    chat.add_argument("--max-tokens", type=int, default=200)

    # ---------------------------------------------------------------- verify --
    verify = sub.add_parser("verify", help="сверить реализацию с эталоном transformers")
    verify.add_argument("--model", default=DEFAULT_MODEL)

    # ------------------------------------------------------------------ eval --
    ev = sub.add_parser("eval", help="оценить модель: перплексия и живые ответы",
                        formatter_class=parser.formatter_class)
    ev.add_argument("--checkpoint", default="checkpoints/v7/best.pt")
    ev.add_argument("--data", default=None,
                    help="корпус для перплексии; без него считаются только живые ответы")
    ev.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ev.add_argument("--temperature", type=float, default=0.7)
    ev.add_argument("--max-tokens", type=int, default=120)
    ev.add_argument("--batches", type=int, default=60, help="окон на замер перплексии")
    ev.add_argument("--block-size", type=int, default=1024)
    ev.add_argument("--no-brain", dest="from_brain", action="store_false",
                    help="не подмешивать корпус brain.js — указывайте, только если "
                         "обучение шло без --from-brain, иначе разойдётся разбиение "
                         "на обучение и проверку")
    ev.set_defaults(from_brain=True)

    # ---------------------------------------------------------------- corpus --
    corpus = sub.add_parser("corpus", help="скачать открытый текстовый корпус",
                            formatter_class=parser.formatter_class)
    corpus.add_argument("--source", default=CORPUS_DEFAULT,
                        help=f"готовый источник ({', '.join(CORPUS_SOURCES)})")
    corpus.add_argument("--docs", type=int, default=20000, help="сколько документов взять")
    corpus.add_argument("--out", default="data/corpus.txt", help="куда сохранить")
    corpus.add_argument("--max-mb", type=float, default=None, help="предел размера файла")
    corpus.add_argument("--min-chars", type=int, default=400,
                        help="пропускать документы короче этого")
    corpus.add_argument("--dataset", default=None,
                        help="произвольный датасет Hugging Face вместо готового источника")
    corpus.add_argument("--config", default=None, help="конфигурация датасета")
    corpus.add_argument("--field", default=None, help="поле с текстом (по умолчанию text)")

    # ------------------------------------------------------------- dialogues --
    dial = sub.add_parser("dialogues", help="собрать диалоговый корпус для второго этапа",
                          formatter_class=parser.formatter_class)
    dial.add_argument("--out", default="data/dialogues.jsonl")
    dial.add_argument("--saiga", type=int, default=30000,
                      help="разговоров на широкие темы из saiga_scored")
    dial.add_argument("--instruct", type=int, default=8000, help="сколько инструкций взять")
    dial.add_argument("--chat", type=int, default=6000, help="сколько живых диалогов взять")
    dial.add_argument("--persona-repeat", type=int, default=12,
                      help="сколько копий делать с диалогов о самом себе")
    dial.add_argument("--drills", type=int, default=700,
                      help="разговоров на память: факт называют, потом о нём спрашивают")
    dial.add_argument("--max-chars", type=int, default=2500,
                      help="предел длины реплики для тем, где нужен разбор")
    dial.add_argument("--explain-max-chars", type=int, default=1300,
                      help="предел для тем, где спрашивают «почему» и «как»")
    dial.add_argument("--chat-max-chars", type=int, default=700,
                      help="предел для разговорных тем — строже намеренно")
    dial.add_argument("--min-score", type=int, default=8,
                      help="нижняя граница оценки качества диалога (1-10)")
    dial.add_argument("--topic-cap", type=float, default=0.10,
                      help="предел доли одной темы, чтобы корпус не перекосило")
    dial.add_argument("--multiturn-share", type=float, default=0.35,
                      help="доля мест под разговоры с продолжением")
    dial.add_argument("--seed", type=int, default=20260808)

    # ----------------------------------------------------------------- ultra --
    ultra = sub.add_parser("ultra", help="собрать корпус для Cloud HDR Ultra: код, стихи, разговор",
                           formatter_class=parser.formatter_class)
    ultra.add_argument("--out", default="data/ultra.jsonl")
    ultra.add_argument("--source", default="data/dialogues-v5.jsonl",
                       help="разговорный корпус, из которого берётся выборка «чтобы не забыть»")
    ultra.add_argument("--code-repeat", type=int, default=10,
                       help="сколько вариантов делать из каждой формулировки задачи")
    ultra.add_argument("--verse", type=int, default=900, help="сколько стихотворений взять")
    ultra.add_argument("--keep", type=int, default=9000,
                       help="сколько прежних диалогов подмешать, чтобы модель не разучилась говорить")
    ultra.add_argument("--seed", type=int, default=20260819)

    return parser


# ------------------------------------------------------------------- команды --

def cmd_info(args) -> int:
    banner("Проверка окружения...")
    try:
        import torch
    except ImportError:
        log("PyTorch не установлен. Из папки python/: pip install -r requirements.txt")
        return 1

    from . import device as dev

    info = dev.preflight(args.device)
    rule("Оценка памяти для модели на 125M")
    log("веса fp32 ~0.50 ГБ · градиенты ~0.50 ГБ · состояния AdamW ~1.00 ГБ = ~2.0 ГБ")
    if info.vram_gb:
        log(f"свободно под активации: примерно {max(0.0, info.vram_gb - 2.5):.1f} ГБ "
            f"— хватает на микробатч 4 при контексте 1024")
    log(f"рекомендуемая точность: {dev.choose_precision(info)}")

    try:
        import transformers
        log(f"transformers {transformers.__version__} — болванка {DEFAULT_MODEL} доступна")
    except ImportError:
        log("transformers не установлен — предобученные веса загрузить не выйдет")
        return 1

    rule()
    return 0 if info.compatible else 2


def cmd_train(args) -> int:
    from .train import run

    config = TrainConfig(
        data=args.data,
        block_size=args.block_size,
        batch_size=args.batch_size,
        grad_accum_steps=args.grad_accum,
        learning_rate=args.lr,
        max_steps=args.steps,
        max_minutes=args.max_minutes,
        warmup_steps=args.warmup,
        precision=args.precision,
        grad_checkpointing=args.grad_checkpointing,
        compile=args.compile,
        lora=args.lora,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        neftune_alpha=args.neftune,
        out_dir=args.out,
        dropout=args.dropout,
        seed=args.seed,
        eval_interval=args.eval_interval,
        log_interval=args.log_interval,
        save_interval=args.save_interval,
        val_split=args.val_split,
    )
    if not args.data and not args.from_brain:
        log("Источник данных не указан — беру корпус из brain.js (как при --from-brain)")
        args.from_brain = True

    run(config, args.model, args.from_brain, args.resume)
    return 0


def cmd_serve(args) -> int:
    from .serve import run

    run(args.host, args.port, args.checkpoint, args.model, args.device,
        args.precision, args.wiki)
    return 0


def cmd_chat(args) -> int:
    from .serve import Engine

    banner("Запуск модуля диалога...")
    engine = Engine.load(args.checkpoint, args.model, args.device)
    history = []

    rule("Пустая строка или «выход» — завершить")
    while True:
        try:
            question = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in ("выход", "exit", "quit"):
            break

        history.append({"role": "user", "content": question})
        print("Cloud HDR: ", end="", flush=True)
        answer = ""
        try:
            for piece in engine.stream(
                history,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                top_p=0.95,
                top_k=40,
            ):
                print(piece, end="", flush=True)
                answer += piece
        except KeyboardInterrupt:
            print("  [прервано]")
        print()
        history.append({"role": "assistant", "content": answer.strip()})
        history = history[-16:]          # держим контекст в разумных рамках

    log("Диалог завершён")
    return 0


def cmd_verify(args) -> int:
    from .model import CloudHDRGPT

    banner("Проверка модели...")
    model = CloudHDRGPT.from_pretrained(args.model)
    log(model.describe())
    ok = model.verify_against_hf(args.model)
    log("Реализация совпадает с эталоном — веса перенесены верно"
        if ok else "Расхождение с эталоном: перенос весов требует разбирательства")
    return 0 if ok else 1


def cmd_eval(args) -> int:
    from .evaluate import run

    return run(
        checkpoint=args.checkpoint,
        data=args.data,
        prefer_device=args.device,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        batches=args.batches,
        block_size=args.block_size,
        from_brain=args.from_brain,
    )


def cmd_corpus(args) -> int:
    from .corpus import download

    banner("Загрузка корпуса...")
    download(
        source=args.source,
        docs=args.docs,
        out=args.out,
        min_chars=args.min_chars,
        max_mb=args.max_mb,
        dataset=args.dataset,
        config=args.config,
        field=args.field,
    )
    log(f"Дальше: python -m cloudhdr_ai train --data {args.out} --from-brain")
    return 0


def cmd_dialogues(args) -> int:
    from .dialogues import build

    banner("Сборка диалогового корпуса")
    build(out=args.out, saiga=args.saiga, instruct=args.instruct, chat=args.chat,
          persona_repeat=args.persona_repeat, drills=args.drills,
          max_chars=args.max_chars, chat_max_chars=args.chat_max_chars,
          explain_max_chars=args.explain_max_chars,
          min_score=args.min_score, topic_cap=args.topic_cap,
          multiturn_share=args.multiturn_share, seed=args.seed)
    return 0


def cmd_ultra(args) -> int:
    from .ultra import build

    banner("Сборка корпуса Cloud HDR Ultra")
    build(out=args.out, source=args.source, code_repeat=args.code_repeat,
          verse=args.verse, keep=args.keep, seed=args.seed)
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "info": cmd_info,
        "train": cmd_train,
        "serve": cmd_serve,
        "chat": cmd_chat,
        "verify": cmd_verify,
        "eval": cmd_eval,
        "corpus": cmd_corpus,
        "dialogues": cmd_dialogues,
        "ultra": cmd_ultra,
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
