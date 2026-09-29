"""
Cloud HDR AI — конфигурации модели и обучения.

Значения по умолчанию подобраны под NVIDIA RTX 5060 (8 ГБ, архитектура
Blackwell, sm_120). Главное, что диктует эти цифры, — 8 ГБ видеопамяти:

    GPT-2 124M в fp32              ~0.50 ГБ  веса
    градиенты fp32                 ~0.50 ГБ
    состояния AdamW (m и v)        ~1.00 ГБ
    ------------------------------------------
    только оптимизатор             ~2.00 ГБ

Остаток (~5.5 ГБ с учётом работы дисплея) уходит на активации, и именно они
растут линейно по batch size и по длине контекста. Отсюда batch_size = 4 и
накопление градиента 4: эффективный батч получается 16 при памяти как у 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# --------------------------------------------------------------------- модель --

#: Готовые «болванки» на Hugging Face и их геометрия.
#:
#: Все они — одна и та же архитектура (GPT2LMHeadModel), поэтому раскладка весов
#: совпадает и любая из них ложится в CloudHDRGPT без единой правки кода.
#: Различаются размером, длиной контекста и — главное — ЯЗЫКОМ, на котором
#: обучались болванка и её токенизатор.
PRETRAINED = {
    # --- английские болванки OpenAI ---
    "openai-community/gpt2":
        dict(n_layer=12, n_head=12, n_embd=768,  block_size=1024, vocab_size=50257),   # 124M
    "openai-community/gpt2-medium":
        dict(n_layer=24, n_head=16, n_embd=1024, block_size=1024, vocab_size=50257),   # 355M
    "openai-community/gpt2-large":
        dict(n_layer=36, n_head=20, n_embd=1280, block_size=1024, vocab_size=50257),   # 774M
    "openai-community/gpt2-xl":
        dict(n_layer=48, n_head=25, n_embd=1600, block_size=1024, vocab_size=50257),   # 1558M

    # --- русские болванки ---
    # Та же геометрия, что у gpt2 (12 слоёв, 12 голов, 768 признаков), но словарь
    # собран на русском тексте, а не на английском, и контекст вдвое длиннее.
    "ai-forever/rugpt3small_based_on_gpt2":
        dict(n_layer=12, n_head=12, n_embd=768,  block_size=2048, vocab_size=50264),   # 125M
    "ai-forever/rugpt3medium_based_on_gpt2":
        dict(n_layer=24, n_head=16, n_embd=1024, block_size=2048, vocab_size=50257),   # 355M
    "ai-forever/rugpt3large_based_on_gpt2":
        dict(n_layer=24, n_head=16, n_embd=1536, block_size=2048, vocab_size=50257),   # 760M
}

#: Короткие псевдонимы, чтобы не писать префикс организации в командной строке.
ALIASES = {
    "gpt2": "openai-community/gpt2",
    "gpt2-medium": "openai-community/gpt2-medium",
    "gpt2-large": "openai-community/gpt2-large",
    "gpt2-xl": "openai-community/gpt2-xl",
    "rugpt3": "ai-forever/rugpt3small_based_on_gpt2",
    "rugpt3-small": "ai-forever/rugpt3small_based_on_gpt2",
    "rugpt3-medium": "ai-forever/rugpt3medium_based_on_gpt2",
    "rugpt3-large": "ai-forever/rugpt3large_based_on_gpt2",
}

#: Болванка по умолчанию — русская.
#:
#: Причина в токенизаторе, и она решающая. Словарь GPT-2 собран на английском и
#: работает по байтам: русская буква не входит в него ни одним куском и
#: раскладывается на два-три байтовых токена. Замер на корпусе проекта:
#:
#:     GPT-2   — 0.96 символа на токен
#:     ruGPT-3 — 4.28 символа на токен
#:
#: То есть при равном контексте русского текста помещается вчетверо меньше, а
#: половина ёмкости модели уходит не на смысл, а на сборку UTF-8 из байтов.
#: При контексте 2048 у ruGPT-3 против 1024 у GPT-2 разрыв по реальной памяти
#: разговора выходит почти девятикратный: ~8800 символов против ~980.
DEFAULT_MODEL = "ai-forever/rugpt3small_based_on_gpt2"

#: Прежняя болванка. Оставлена явным именем, чтобы старые чекпоинты и команды
#: из README продолжали работать без изменений.
LEGACY_MODEL = "openai-community/gpt2"


def resolve_model_name(name: Optional[str]) -> str:
    """«gpt2» → «openai-community/gpt2». Незнакомое имя возвращаем как есть."""
    if not name:
        return DEFAULT_MODEL
    return ALIASES.get(name, name)


def pretrained_geometry(name: str) -> dict:
    """
    Геометрия болванки: из таблицы, а если её там нет — из конфига на Hugging Face.

    Обращение к сети нужно лишь для моделей, которых нет в PRETRAINED, и делается
    один раз: transformers кеширует config.json локально. Благодаря этому в
    --model можно подставить любую GPT-2-совместимую болванку, не трогая код.
    """
    resolved = resolve_model_name(name)
    if resolved in PRETRAINED:
        return dict(PRETRAINED[resolved])

    from transformers import AutoConfig

    hf = AutoConfig.from_pretrained(resolved)
    if getattr(hf, "model_type", None) != "gpt2":
        raise ValueError(
            f"Болванка «{resolved}» имеет архитектуру «{getattr(hf, 'model_type', '?')}», "
            "а CloudHDRGPT воспроизводит именно GPT-2. Возьмите модель на GPT2LMHeadModel "
            f"или одну из готовых: {', '.join(sorted(ALIASES))}"
        )
    return dict(
        n_layer=hf.n_layer,
        n_head=hf.n_head,
        n_embd=hf.n_embd,
        block_size=hf.n_positions,
        vocab_size=hf.vocab_size,
    )


@dataclass
class ModelConfig:
    """Геометрия decoder-only трансформера в конфигурации GPT-2."""

    block_size: int = 2048          # длина контекста ruGPT-3
    vocab_size: int = 50264         # словарь BPE ruGPT-3 (русский)
    n_layer: int = 12
    n_head: int = 12
    n_embd: int = 768
    dropout: float = 0.0            # при дообучении на малом корпусе поднять до 0.1
    bias: bool = True               # в оригинальном GPT-2 смещения есть везде

    @classmethod
    def from_pretrained_name(cls, name: str, **overrides: Any) -> "ModelConfig":
        """Конфиг под конкретную болванку с Hugging Face."""
        geometry = pretrained_geometry(name)
        limit = geometry["block_size"]

        config = cls(**geometry)
        for key, value in overrides.items():
            if value is not None and hasattr(config, key):
                setattr(config, key, value)

        # Позиционные эмбеддинги — обученные веса, а не формула: попросить у
        # модели контекст длиннее, чем их есть в болванке, нельзя — брать
        # координаты для лишних позиций попросту неоткуда.
        if config.block_size > limit:
            raise ValueError(
                f"block_size={config.block_size} превышает {limit}: у болванки "
                f"«{resolve_model_name(name)}» ровно {limit} обученных позиционных "
                "эмбеддингов, дальше их просто нет"
            )
        return config

    @property
    def head_dim(self) -> int:
        return self.n_embd // self.n_head

    def validate(self) -> None:
        if self.n_embd % self.n_head:
            raise ValueError(
                f"n_embd={self.n_embd} не делится на n_head={self.n_head} — "
                "размерность головы должна быть целой"
            )
        if self.block_size < 1:
            raise ValueError(f"block_size={self.block_size} должен быть положительным")

    def to_dict(self) -> dict:
        return asdict(self)


# -------------------------------------------------------------------- обучение --

@dataclass
class TrainConfig:
    """
    Параметры тренировочного цикла.

    Значения по умолчанию — те, что просил пользователь и что выдерживает
    RTX 5060: микробатч 4, накопление 4, смешанная точность.
    """

    # --- данные ---
    data: Optional[str] = None          # файл/папка с .txt или .jsonl
    val_split: float = 0.05             # доля на отложенную выборку

    # Длина обучающей последовательности. С русским словарём ruGPT-3 (4.3 символа
    # на токен) 1024 токена — это около 4400 символов, то есть диалог на пять-шесть
    # реплик целиком. На словаре GPT-2 столько же токенов дали бы всего ~980
    # символов: обрезанный на середине один вопрос.
    block_size: int = 1024

    # --- размер батча ---
    # Память держит микробатч, а качество градиента — эффективный батч.
    # Накопление разводит эти два требования: считаем 4 микробатча по 4 примера
    # и делаем ОДИН шаг оптимизатора по 16 — память как у 4, шум как у 16.
    batch_size: int = 4                 # микробатч (4 или 8 на 8 ГБ)
    grad_accum_steps: int = 4           # шагов накопления градиента

    # --- оптимизатор ---
    # 1e-4 — для обучения диалогу поверх готовой языковой болванки. Прежние 3e-5
    # ставились под другую задачу: английский GPT-2 учили русскому языку с нуля,
    # и там осторожность оправданна — большой шаг просто стёр бы предобучение.
    # Здесь болванка уже знает русский, а научить нужно ФОРМАТУ разговора, и на
    # 3e-5 модель за разумное число шагов формат толком не перенимает.
    learning_rate: float = 1e-4
    weight_decay: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.95
    grad_clip: float = 1.0

    # --- расписание ---
    max_steps: int = 3000               # шаги оптимизатора, не микробатчи
    warmup_steps: int = 100

    # Потолок по времени для запуска без присмотра. Скорость шага заранее не
    # известна — она зависит от длины контекста, батча и того, чем ещё занята
    # карта, — поэтому «учись не дольше полутора часов» надёжнее, чем угаданное
    # число шагов. Косинусное расписание при этом считается по max_steps, так что
    # обрыв по времени оставляет скорость обучения на полпути: это нормально,
    # веса сохраняются, и обучение можно продолжить через --resume.
    max_minutes: Optional[float] = None
    min_lr_ratio: float = 0.1           # нижняя полка косинуса = 10% от lr
    lr_decay: bool = True

    # --- смешанная точность и память ---
    # "auto" выбирает bf16 на Blackwell: у него тот же диапазон экспоненты, что
    # у fp32, поэтому не нужен GradScaler и не бывает срыва в NaN, как у fp16.
    precision: str = "auto"             # auto | bf16 | fp16 | fp32
    grad_checkpointing: bool = True     # меняем ~30% скорости на ~40% памяти
    tf32: bool = True                   # матмулы fp32 на тензорных ядрах
    compile: bool = False               # torch.compile: быстрее, но долгий старт

    # --- LoRA: обучение поправок вместо всех весов ---
    # Нужно там, где модель не помещается в память целиком: 760M требует 11.3 ГБ
    # на полное обучение и 1.5 ГБ на LoRA. Ранг 16 — обычный рабочий выбор:
    # меньше 8 модель не успевает перенять манеру, больше 32 растёт расход
    # без заметной пользы. Альфа вдвое больше ранга — тоже общепринято.
    lora: bool = False
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05

    # --- NEFTune: шум на эмбеддингах слов при обучении диалогу ---
    # 5 — значение из статьи для чат-моделей; 0 выключает.
    neftune_alpha: float = 0.0

    # --- служебное ---
    seed: int = 20260807
    eval_interval: int = 200
    eval_batches: int = 20
    log_interval: int = 10
    save_interval: int = 500
    out_dir: str = "checkpoints"
    dropout: float = 0.1                # на дообучении регуляризация нужна
    num_workers: int = 0                # на Windows потоки загрузчика дороже пользы

    #: Заполняется автоматически в auto_tune() — для отчёта.
    notes: list = field(default_factory=list)

    @property
    def effective_batch(self) -> int:
        return self.batch_size * self.grad_accum_steps

    def validate(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size должен быть >= 1")
        if self.grad_accum_steps < 1:
            raise ValueError("grad_accum_steps должен быть >= 1")
        if self.precision not in ("auto", "bf16", "fp16", "fp32"):
            raise ValueError(f"Неизвестная точность «{self.precision}»")
        if not 0.0 <= self.val_split < 1.0:
            raise ValueError("val_split должен лежать в [0, 1)")

    def auto_tune(self, vram_gb: Optional[float]) -> "TrainConfig":
        """
        Подгоняет батч и контекст под реальный объём видеопамяти.

        Пользовательские значения не трогаем вслепую: правило срабатывает только
        вниз (на картах меньше 8 ГБ), потому что вылет по OOM на середине
        обучения обходится дороже, чем лишние полчаса на более мелком батче.
        """
        if vram_gb is None:
            self.notes.append("GPU не найден — параметры оставлены как есть")
            return self

        if vram_gb < 6:
            self.batch_size = min(self.batch_size, 2)
            self.grad_accum_steps = max(self.grad_accum_steps, 8)
            self.block_size = min(self.block_size, 256)
            self.notes.append(f"{vram_gb:.0f} ГБ VRAM — микробатч снижен до {self.batch_size}")
        elif vram_gb < 10:
            # Ровно наш случай: RTX 5060 с 8 ГБ.
            self.notes.append(
                f"{vram_gb:.0f} ГБ VRAM — микробатч {self.batch_size} × накопление "
                f"{self.grad_accum_steps} (эффективный батч {self.effective_batch})"
            )
        else:
            self.grad_checkpointing = False
            self.notes.append(
                f"{vram_gb:.0f} ГБ VRAM — checkpointing выключен, памяти хватает без него"
            )
        return self

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("notes", None)
        return data
