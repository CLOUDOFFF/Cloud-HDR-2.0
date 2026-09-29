"""
Cloud HDR AI — тренировочный цикл под NVIDIA RTX 5060.

Три приёма, которые вместе позволяют дообучать 124-миллионную модель на карте с
8 ГБ памяти.

1. СМЕШАННАЯ ТОЧНОСТЬ. Прямой и обратный проход считаются в bf16 (на Blackwell)
   или fp16, а веса и шаг оптимизатора остаются в fp32. Активации занимают вдвое
   меньше, матмулы идут на тензорных ядрах. Для fp16 подключается GradScaler —
   без него мелкие градиенты обнулялись бы, не дойдя до весов; для bf16 он не
   нужен и создаётся выключенным (см. device.choose_precision).

2. НАКОПЛЕНИЕ ГРАДИЕНТА. Пик памяти определяет РАЗМЕР МИКРОБАТЧА, а качество
   градиента — эффективный батч. Считаем 4 микробатча по 4 примера, складывая
   градиенты, и делаем один шаг оптимизатора по 16. Существенно: функция потерь
   каждого микробатча делится на число накоплений, иначе градиент вырастет
   вчетверо и обрезка по норме съест обучение.

3. ПЕРЕСЧЁТ АКТИВАЦИЙ (gradient checkpointing). Промежуточные активации блоков
   не хранятся, а считаются заново на обратном проходе: около 30% времени в
   обмен примерно на 40% памяти.

Плюс страховка: если OOM всё-таки случился, цикл не падает, а вдвое уменьшает
микробатч и вдвое увеличивает накопление — эффективный батч сохраняется, обучение
продолжается с того же шага.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

import torch
from torch.utils.data import DataLoader

from . import device as dev
from .branding import banner, log, rule
from .config import ModelConfig, TrainConfig
from .model import CloudHDRGPT


class Trainer:
    """Обучение Cloud HDR AI с контролем памяти."""

    def __init__(
        self,
        model: CloudHDRGPT,
        train_dataset,
        val_dataset,
        config: TrainConfig,
        info: dev.DeviceInfo,
    ):
        config.validate()
        self.config = config
        self.info = info
        self.train_dataset = train_dataset
        self.val_dataset = val_dataset

        self.precision = dev.choose_precision(info, config.precision)
        dev.apply_speedups(info, config.tf32)

        torch.manual_seed(config.seed)
        if info.is_cuda:
            torch.cuda.manual_seed_all(config.seed)

        self.model = model.to(info.device)
        if config.grad_checkpointing:
            self.model.enable_gradient_checkpointing(True)

        self.optimizer = self.model.configure_optimizers(
            weight_decay=config.weight_decay,
            learning_rate=config.learning_rate,
            betas=(config.beta1, config.beta2),
            device_type=info.type,
        )
        self.scaler = dev.make_grad_scaler(self.precision, info.type)

        # Рабочие копии: OOM-страховка меняет их, не трогая исходный конфиг.
        self.micro_batch = config.batch_size
        self.accum_steps = config.grad_accum_steps

        self.train_loader = self._make_loader(train_dataset, self.micro_batch, shuffle=True)
        self.val_loader = (
            self._make_loader(val_dataset, self.micro_batch, shuffle=False)
            if val_dataset is not None and len(val_dataset) else None
        )
        self._train_iter = iter(self.train_loader)

        self.step = 0
        self.best_val = float("inf")
        self.out_dir = Path(config.out_dir)
        self.oom_events = 0

        if config.compile and hasattr(torch, "compile"):
            log("torch.compile: первый шаг займёт минуту-другую на компиляцию")
            self.model = torch.compile(self.model)

    # ---------------------------------------------------------------- данные --

    def _make_loader(self, dataset, batch_size: int, shuffle: bool) -> DataLoader:
        return DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=self.config.num_workers,
            pin_memory=self.info.is_cuda,
            drop_last=shuffle,
        )

    def _next_batch(self):
        """Бесконечный поток батчей: эпохи закольцованы, шаги считаем сами."""
        try:
            batch = next(self._train_iter)
        except StopIteration:
            self._train_iter = iter(self.train_loader)
            batch = next(self._train_iter)
        x, y = batch
        return (
            x.to(self.info.device, non_blocking=True),
            y.to(self.info.device, non_blocking=True),
        )

    # ------------------------------------------------------------ расписание --

    def _lr_for(self, step: int) -> float:
        """Линейный разогрев, затем косинусное затухание до нижней полки."""
        config = self.config
        if not config.lr_decay:
            return config.learning_rate
        if step < config.warmup_steps:
            # +1, чтобы на нулевом шаге скорость не была ровно нулевой
            return config.learning_rate * (step + 1) / max(1, config.warmup_steps)

        progress = (step - config.warmup_steps) / max(1, config.max_steps - config.warmup_steps)
        progress = min(1.0, max(0.0, progress))
        coefficient = 0.5 * (1.0 + math.cos(math.pi * progress))
        min_lr = config.learning_rate * config.min_lr_ratio
        return min_lr + coefficient * (config.learning_rate - min_lr)

    # ----------------------------------------------------------- OOM-страховка --

    def _handle_oom(self) -> bool:
        """
        Реакция на нехватку памяти: вдвое меньше микробатч, вдвое больше
        накоплений. Эффективный батч не меняется, поэтому расписание скорости
        обучения остаётся осмысленным. Возвращает False, если делить уже некуда.
        """
        self.oom_events += 1
        self.optimizer.zero_grad(set_to_none=True)
        if self.info.is_cuda:
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()

        if self.micro_batch <= 1:
            log("OOM при микробатче 1 — уменьшать больше нечего. "
                "Попробуйте --block-size поменьше или --grad-checkpointing.")
            return False

        self.micro_batch //= 2
        self.accum_steps *= 2
        log(f"OOM: микробатч → {self.micro_batch}, накопление → {self.accum_steps} "
            f"(эффективный батч прежний: {self.micro_batch * self.accum_steps})")

        self.train_loader = self._make_loader(self.train_dataset, self.micro_batch, shuffle=True)
        self._train_iter = iter(self.train_loader)
        if self.val_loader is not None:
            self.val_loader = self._make_loader(self.val_dataset, self.micro_batch, shuffle=False)
        return True

    # ------------------------------------------------------------------ шаг ---

    def _optimizer_step(self) -> Optional[float]:
        """
        Один шаг оптимизатора = accum_steps микробатчей.
        Возвращает функцию потерь или None, если шаг сорвался по памяти.
        """
        self.optimizer.zero_grad(set_to_none=True)
        total_loss = 0.0

        for _ in range(self.accum_steps):
            x, y = self._next_batch()
            try:
                with dev.autocast_context(self.info.type, self.precision):
                    _, loss = self.model(x, y)
                    # деление на число накоплений — иначе градиент завышен в accum раз
                    loss = loss / self.accum_steps
                self.scaler.scale(loss).backward()
                total_loss += loss.item() * self.accum_steps
            except torch.cuda.OutOfMemoryError:
                return None if not self._handle_oom() else float("nan")

        # Обрезка нормы делается по НЕмасштабированным градиентам: у fp16 они
        # domножены на коэффициент scaler, и обрезать их до unscale_ значило бы
        # резать по случайному порогу.
        if self.config.grad_clip > 0:
            self.scaler.unscale_(self.optimizer)
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.config.grad_clip)

        self.scaler.step(self.optimizer)
        self.scaler.update()
        return total_loss / self.accum_steps

    @torch.no_grad()
    def evaluate(self) -> Optional[float]:
        """Средняя функция потерь на отложенной выборке."""
        if self.val_loader is None:
            return None
        self.model.eval()
        losses = []
        for index, (x, y) in enumerate(self.val_loader):
            if index >= self.config.eval_batches:
                break
            x = x.to(self.info.device, non_blocking=True)
            y = y.to(self.info.device, non_blocking=True)
            with dev.autocast_context(self.info.type, self.precision):
                _, loss = self.model(x, y)
            losses.append(loss.item())
        self.model.train()
        return sum(losses) / len(losses) if losses else None

    # ---------------------------------------------------------------- запуск --

    def train(self) -> dict:
        config = self.config
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.model.train()

        rule("Обучение начато")
        model = getattr(self.model, "_orig_mod", self.model)   # снимаем обёртку compile
        log(f"Модель: {model.describe()}")
        log(f"Точность: {self.precision}"
            + (" (GradScaler включён)" if self.precision == "fp16" else "")
            + f" · checkpointing: {'вкл' if config.grad_checkpointing else 'выкл'}")
        log(f"Батч: {self.micro_batch} × накопление {self.accum_steps} = "
            f"{self.micro_batch * self.accum_steps} примеров на шаг")
        log(f"Шагов: {config.max_steps} · разогрев {config.warmup_steps} · "
            f"lr {config.learning_rate:g} · контекст {config.block_size}")
        rule()

        started = time.time()
        history = []
        running = None

        while self.step < config.max_steps:
            lr = self._lr_for(self.step)
            for group in self.optimizer.param_groups:
                group["lr"] = lr

            tick = time.time()
            loss = self._optimizer_step()

            if loss is None:                       # OOM и уменьшать больше нечего
                log("Обучение остановлено из-за нехватки видеопамяти")
                break
            if math.isnan(loss):                   # шаг пересобран после OOM
                continue

            self.step += 1
            elapsed = time.time() - tick
            running = loss if running is None else running * 0.9 + loss * 0.1

            if self.step % config.log_interval == 0 or self.step == 1:
                tokens = self.micro_batch * self.accum_steps * config.block_size
                remaining = (config.max_steps - self.step) * elapsed
                log(
                    f"шаг {self.step:>5}/{config.max_steps} · "
                    f"потери {loss:.4f} (сглаж. {running:.4f}) · "
                    f"lr {lr:.2e} · {tokens / max(elapsed, 1e-6):,.0f} ток/с · "
                    f"{dev.memory_report(self.info)} · осталось ~{remaining / 60:.0f} мин"
                    .replace(",", " ")
                )

            if config.eval_interval and self.step % config.eval_interval == 0:
                val_loss = self.evaluate()
                if val_loss is not None:
                    marker = ""
                    if val_loss < self.best_val:
                        self.best_val = val_loss
                        # без оптимизатора: best.pt нужен для ответов, а
                        # продолжают обучение с last.pt
                        self.save_checkpoint("best.pt", val_loss, with_optimizer=False)
                        marker = "  ← лучший, сохранён"
                    log(f"проверка · потери {val_loss:.4f} · "
                        f"перплексия {math.exp(min(val_loss, 20)):.1f}{marker}")
                    history.append({"step": self.step, "train": loss, "val": val_loss})

            if config.save_interval and self.step % config.save_interval == 0:
                self.save_checkpoint("last.pt", running)

            if config.max_minutes and (time.time() - started) / 60 >= config.max_minutes:
                log(f"Достигнут лимит времени {config.max_minutes:g} мин — останавливаюсь "
                    f"на шаге {self.step}. Продолжить: --resume {self.out_dir}/last.pt")
                break

        total = time.time() - started
        self.save_checkpoint("last.pt", running)

        rule("Обучение завершено")
        log(f"Шагов пройдено: {self.step} за {total / 60:.1f} мин")
        if running is not None:
            log(f"Потери на обучении: {running:.4f}")
        if self.best_val < float("inf"):
            log(f"Лучшие потери на проверке: {self.best_val:.4f} "
                f"(перплексия {math.exp(min(self.best_val, 20)):.1f})")
        if self.oom_events:
            log(f"Событий OOM обработано: {self.oom_events} — обучение не прерывалось")
        log(f"Чекпоинты: {self.out_dir.resolve()}")
        rule()

        return {
            "steps": self.step,
            "minutes": round(total / 60, 2),
            "train_loss": running,
            "best_val_loss": None if self.best_val == float("inf") else self.best_val,
            "oom_events": self.oom_events,
            "precision": self.precision,
            "effective_batch": self.micro_batch * self.accum_steps,
            "history": history,
        }

    # ----------------------------------------------------------- чекпоинты ----

    def save_checkpoint(self, filename: str, loss: Optional[float],
                        with_optimizer: bool = True) -> Path:
        """
        Сохраняет веса и конфигурацию; при with_optimizer — ещё и оптимизатор.

        Состояния AdamW (два момента на каждый вес) весят ВДВОЕ больше самой
        модели: у 355M это 2.8 ГБ поверх 1.4 ГБ. Нужны они ровно для одного —
        продолжить обучение через --resume, и для этого достаточно last.pt.
        А best.pt переписывается на каждом улучшении проверки, то есть десятки
        раз за прогон, и таскать в нём оптимизатор значит гонять на диск лишние
        три гигабайта каждый раз. На модели 355M это переводится в лишние
        полчаса записи за обучение — при том, что для ответов эти данные
        не нужны вовсе.
        """
        model = getattr(self.model, "_orig_mod", self.model)   # снимаем обёртку compile
        path = self.out_dir / filename

        # При обучении с LoRA поправки вписываются в веса ПРИ СОХРАНЕНИИ, а не
        # в модели: наружу уходит обычный чекпоинт обычной формы, и ни сервер,
        # ни загрузка не знают, что модель обучалась поправками. Живая модель
        # при этом остаётся с адаптерами и продолжает учиться.
        weights = model.merged_state_dict() if self.config.lora else model.state_dict()

        torch.save(
            {
                "model": weights,
                **({"optimizer": self.optimizer.state_dict()} if with_optimizer else {}),
                "model_config": model.config.to_dict(),
                "train_config": self.config.to_dict(),
                # Имя болванки едет вместе с весами: по нему сервер поймёт, каким
                # словарём эти веса пользуются. Без него чекпоинт, у которого рядом
                # не оказалось копии токенизатора, невозможно прочитать правильно —
                # русские и английские словари несовместимы при одинаковом размере.
                "base_model": getattr(model, "base_model", None),
                "step": self.step,
                "loss": loss,
                "best_val": self.best_val,
                "brand": "Cloud HDR AI v1.0",
            },
            path,
        )
        (self.out_dir / "meta.json").write_text(
            json.dumps(
                {
                    "brand": "Cloud HDR AI v1.0",
                    "step": self.step,
                    "loss": loss,
                    "best_val": None if self.best_val == float("inf") else self.best_val,
                    "precision": self.precision,
                    "device": self.info.name,
                    "base_model": getattr(model, "base_model", None),
                    "model_config": model.config.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


def load_checkpoint(path: Path, device: torch.device) -> CloudHDRGPT:
    """Восстанавливает модель из чекпоинта, включая её геометрию."""
    # weights_only=False: в чекпоинте лежат ещё и конфиги, а файл наш собственный.
    #
    # map_location="cpu", а НЕ device, и это стоит ровно трёх гигабайт
    # видеопамяти. Раньше здесь стояло device, и веса ехали на карту дважды:
    # сначала torch.load клал туда весь словарь целиком (2.9 ГБ для 760M в
    # fp32), а model.to(device) в конце заводил на карте вторую копию — при том,
    # что первая ещё жива, ссылка на неё лежит в checkpoint. На пике загрузки на
    # карте оказывалось 5.7 ГБ вместо 2.9.
    #
    # Само по себе это пережилось бы: после выхода из функции словарь
    # освобождается. Но кэширующий аллокатор PyTorch освобождённое драйверу НЕ
    # возвращает — он держит блоки под будущие запросы. То есть лишние 2.9 ГБ
    # оставались занятыми до конца работы сервера, и nvidia-smi показывал 7.2 ГБ
    # из 8 на модели, которой хватает трёх. На карте с 8 ГБ это разница между
    # «работает» и «больше ничего не запускай».
    #
    # Модель от этого не меняется ни на бит: те же веса, то же устройство на
    # выходе — меняется только маршрут, которым они туда попадают.
    #
    # Второе сбережение — уже оперативной памяти, и оно ещё крупнее. Прежде
    # модель сперва создавалась со случайными весами (2.8 ГБ), потом в неё
    # копировался чекпоинт (ещё 2.9 ГБ), и после переезда на карту Windows эту
    # память процессу не возвращала: сервер модели держал 5-7 ГБ ОЗУ, хотя все
    # веса уже лежали в видеопамяти. На машине с 16 ГБ это треть памяти впустую.
    #
    # Теперь модель собирается на устройстве «meta» — без памяти вовсе, — а
    # веса подключаются прямо из файла через отображение (mmap), без копии.
    # Итог замера: 0.7 ГБ ОЗУ вместо 5 ГБ, веса и ответы те же до бита.
    checkpoint = torch.load(path, map_location="cpu", weights_only=False, mmap=True)
    config = ModelConfig(**checkpoint["model_config"])
    with torch.device("meta"):
        model = CloudHDRGPT(config)
    model.load_state_dict(checkpoint["model"], assign=True)
    # assign ставит параметры как есть и связь «эмбеддинги = выходной слой»
    # по пути теряет — восстанавливаем её, иначе на карте было бы две копии.
    model.transformer.wte.weight = model.lm_head.weight
    model.base_model = checkpoint.get("base_model") or infer_base_model(config)

    # Шаг забираем ДО освобождения словаря — он нужен только для строки в логе.
    step = checkpoint.get("step", "?")

    # Словарь больше не нужен: веса уже скопированы в модель. Освобождаем его до
    # переноса на карту, иначе экономия выше пропала бы впустую — обе копии
    # снова сошлись бы в памяти, просто теперь в оперативной.
    del checkpoint

    log(f"Чекпоинт загружен: {path.name} · шаг {step} · "
        f"болванка {model.base_model}")
    return model.to(device)


def infer_base_model(config: ModelConfig) -> str:
    """
    Определяет болванку по геометрии — для чекпоинтов, снятых до того, как её имя
    начали записывать.

    Решает размер словаря: 50264 бывает только у ruGPT-3 small. При 50257 смотрим
    на ширину признаков, потому что этот размер словаря делят английский GPT-2
    (768) и ruGPT-3 medium (1024).
    """
    from .config import LEGACY_MODEL

    if config.vocab_size == 50264:
        return "ai-forever/rugpt3small_based_on_gpt2"
    if config.vocab_size == 50257 and config.n_embd == 1024 and config.n_layer == 24:
        return "ai-forever/rugpt3medium_based_on_gpt2"
    return LEGACY_MODEL


def run(config: TrainConfig, model_name: str, from_brain: bool, resume: Optional[str] = None) -> dict:
    """Точка входа команды `train`: железо → данные → модель → цикл."""
    from .data import (TOKENIZER_DIRNAME, build_datasets, load_documents,
                       load_tokenizer, save_tokenizer)

    banner("Запуск модуля обучения...")
    info = dev.preflight()
    if info.is_cuda and not info.compatible:
        raise SystemExit(
            "Обучение остановлено: установленная сборка PyTorch не поддерживает эту видеокарту "
            "(подробности выше). Переустановите torch — и запускайте снова."
        )

    config.auto_tune(info.vram_gb)
    for note in config.notes:
        log(note)

    # Копия токенизатора рядом с чекпоинтами: со второго запуска сеть не нужна.
    tokenizer = load_tokenizer(model_name, local_dir=Path(config.out_dir) / TOKENIZER_DIRNAME)
    save_tokenizer(tokenizer, config.out_dir)

    brain_js = Path(__file__).resolve().parents[2] / "public" / "js" / "brain.js"
    documents = load_documents(config.data, brain_js if from_brain else None)
    train_dataset, val_dataset = build_datasets(
        documents, tokenizer, config.block_size, config.val_split, seed=config.seed
    )

    if resume:
        model = load_checkpoint(Path(resume), torch.device("cpu"))
        # dropout приходит из сохранённой конфигурации — навязываем запрошенный
        model.set_dropout(config.dropout)
        log(f"Dropout для этого этапа: {config.dropout}")
    else:
        # block_size модели НЕ ограничиваем длиной обучающих последовательностей.
        # Учить можно окнами по 512 — так дешевле по памяти, — но у модели должны
        # остаться все 1024 позиционных эмбеддинга GPT-2: обрезав их здесь, мы
        # навсегда сократили бы контекст при разговоре, ради экономии 0.8 млн
        # весов, которая ничего не решает.
        model = CloudHDRGPT.from_pretrained(model_name, dropout=config.dropout)

    if config.lora:
        model.apply_lora(config.lora_rank, config.lora_alpha, config.lora_dropout)

    model.neftune_alpha = config.neftune_alpha
    if config.neftune_alpha > 0:
        log(f"NEFTune: шум на эмбеддингах, alpha {config.neftune_alpha:g}")

    return Trainer(model, train_dataset, val_dataset, config, info).train()
