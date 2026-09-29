"""
Cloud HDR AI — decoder-only Transformer в конфигурации GPT-2.

Что здесь и почему именно так.

Прежняя модель проекта (public/js/brain.js) — трёхслойный перцептрон
8192 → 192 → 27, классификатор намерений на 1.6 млн весов. Он отвечает на
вопрос «какую команду выполнить» и делает это за миллисекунды. Здесь решается
ДРУГАЯ задача — порождение текста, — и архитектура нужна другая: авторегрессивный
трансформер, предсказывающий следующий токен.

Ключевое отличие от перцептрона — self-attention: каждый токен смотрит на все
предыдущие и сам решает, какие из них важны. Маска причинности (causal) не даёт
заглянуть вперёд, иначе модель обучалась бы списывать ответ из будущего.

Слои реализованы явно, а не взяты готовым классом из transformers, потому что
задача формулировалась как «переписать модель на архитектуру Transformer». Но
имена параметров специально повторяют раскладку HF GPT-2 — благодаря этому
from_pretrained() кладёт предобученные веса прямо в этот код, один в один.
Проверить совпадение можно вызовом verify_against_hf().

Какие именно веса кладутся — вопрос отдельный и важный. Архитектура GPT-2 не
привязана к английскому языку, привязан к нему СЛОВАРЬ. У болванки OpenAI он
байтовый и собран на английском: русская буква не входит в него целиком и
раскладывается на два-три токена (замер на корпусе проекта — 0.96 символа на
токен против 4.28 у русского словаря). Поэтому по умолчанию берётся
«ai-forever/rugpt3small_based_on_gpt2»: ровно та же геометрия — 12 слоёв,
12 голов, 768 признаков, — но словарь русский, контекст 2048 вместо 1024, а
предобучение шло на русском тексте, а не на английском. Код от этого не меняется
ни на строку: обе болванки — GPT2LMHeadModel с одинаковой раскладкой имён.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import torch
import torch.nn as nn
from torch.nn import functional as F

from .branding import log
from .config import DEFAULT_MODEL, ModelConfig, resolve_model_name


# ------------------------------------------------------------------- внимание --

class CausalSelfAttention(nn.Module):
    """
    Многоголовое причинное самовнимание.

    Q, K, V считаются одной матрицей c_attn шириной 3·n_embd — это ровно та
    раскладка, что лежит в весах OpenAI, и один большой матмул на GPU заметно
    выгоднее трёх маленьких.
    """

    def __init__(self, config: ModelConfig):
        super().__init__()
        config.validate()
        self.n_head = config.n_head
        self.n_embd = config.n_embd
        self.dropout = config.dropout

        self.c_attn = nn.Linear(config.n_embd, 3 * config.n_embd, bias=config.bias)
        self.c_proj = nn.Linear(config.n_embd, config.n_embd, bias=config.bias)
        self.attn_dropout = nn.Dropout(config.dropout)
        self.resid_dropout = nn.Dropout(config.dropout)

        # PyTorch 2.x умеет flash-attention: ту же математику, но без
        # материализации матрицы T×T в памяти. На 8 ГБ это решающая экономия.
        self.flash = hasattr(F, "scaled_dot_product_attention")
        if not self.flash:
            self.register_buffer(
                "mask",
                torch.tril(torch.ones(config.block_size, config.block_size))
                .view(1, 1, config.block_size, config.block_size),
                persistent=False,
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, C = x.size()

        q, k, v = self.c_attn(x).split(self.n_embd, dim=2)
        # (B, T, C) → (B, головы, T, размер головы): головы уходят в batch-измерение
        head_dim = C // self.n_head
        q = q.view(B, T, self.n_head, head_dim).transpose(1, 2)
        k = k.view(B, T, self.n_head, head_dim).transpose(1, 2)
        v = v.view(B, T, self.n_head, head_dim).transpose(1, 2)

        if self.flash:
            y = F.scaled_dot_product_attention(
                q, k, v,
                dropout_p=self.dropout if self.training else 0.0,
                is_causal=True,
            )
        else:
            att = (q @ k.transpose(-2, -1)) * (1.0 / math.sqrt(head_dim))
            att = att.masked_fill(self.mask[:, :, :T, :T] == 0, float("-inf"))
            att = self.attn_dropout(F.softmax(att, dim=-1))
            y = att @ v

        y = y.transpose(1, 2).contiguous().view(B, T, C)
        return self.resid_dropout(self.c_proj(y))


class MLP(nn.Module):
    """Позиционный полносвязный блок: расширение вчетверо, GELU, сжатие обратно."""

    def __init__(self, config: ModelConfig):
        super().__init__()
        self.c_fc = nn.Linear(config.n_embd, 4 * config.n_embd, bias=config.bias)
        self.c_proj = nn.Linear(4 * config.n_embd, config.n_embd, bias=config.bias)
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # tanh-приближение GELU — именно им обучался оригинальный GPT-2.
        return self.dropout(self.c_proj(F.gelu(self.c_fc(x), approximate="tanh")))


class LoRALinear(nn.Module):
    """
    Линейный слой с обучаемой поправкой низкого ранга.

    Считает Wx + B(Ax)·scale, где W заморожена, а A и B — узкие обучаемые
    матрицы. Порядок умножения именно такой (сначала Ax, потом B) и это не
    придирка: Ax даёт вектор длины rank, то есть шестнадцать чисел вместо
    полутора тысяч, и вся поправка обходится в две тонкие матрицы вместо одной
    толстой. Посчитай мы сперва BA, получилась бы матрица размером с исходную —
    и вся экономия пропала бы.
    """

    def __init__(self, base: nn.Linear, rank: int, alpha: int, dropout: float):
        super().__init__()
        self.base = base
        self.base.weight.requires_grad = False
        if self.base.bias is not None:
            self.base.bias.requires_grad = False

        self.a = nn.Parameter(torch.zeros(rank, base.in_features))
        self.b = nn.Parameter(torch.zeros(base.out_features, rank))
        nn.init.normal_(self.a, std=0.02)      # B остаётся нулевой: см. apply_lora
        self.scale = alpha / rank
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        update = self.dropout(x) @ self.a.t() @ self.b.t()
        return self.base(x) + update * self.scale

    @torch.no_grad()
    def merged(self) -> nn.Linear:
        """Возвращает обычный nn.Linear с вписанной поправкой."""
        delta = (self.b @ self.a) * self.scale
        self.base.weight.add_(delta.to(self.base.weight.dtype))
        self.base.weight.requires_grad = True
        if self.base.bias is not None:
            self.base.bias.requires_grad = True
        return self.base


class Block(nn.Module):
    """
    Блок трансформера с пре-нормализацией.

    LayerNorm стоит ДО подслоя, а не после: тогда остаточная связь идёт от входа
    к выходу без единой нормализации на пути, градиент доходит до нижних слоёв
    неискажённым, и 12 слоёв обучаются без разогрева нормализаций.
    """

    def __init__(self, config: ModelConfig):
        super().__init__()
        self.ln_1 = nn.LayerNorm(config.n_embd, bias=config.bias)
        self.attn = CausalSelfAttention(config)
        self.ln_2 = nn.LayerNorm(config.n_embd, bias=config.bias)
        self.mlp = MLP(config)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln_1(x))
        x = x + self.mlp(self.ln_2(x))
        return x


# ---------------------------------------------------------------------- модель --

class CloudHDRGPT(nn.Module):
    """
    Языковая модель Cloud HDR: decoder-only Transformer.

    Размер задаётся конфигурацией, а не жёстко. Значения ПО УМОЛЧАНИЮ — 12
    слоёв, 12 голов, 768 признаков, словарь 50264: это 125 млн параметров.
    Рабочая модель проекта (Cloud HDR 3.2) крупнее — 24 слоя, 16 голов, 1536
    признаков, 760 млн параметров, — и собирается тем же классом: геометрия
    приходит из ModelConfig при загрузке чекпоинта или болванки.

    Для сравнения, у прежнего классификатора намерений (public/js/brain.js)
    весов 1.6 млн — в пятьсот раз меньше.
    """

    def __init__(self, config: ModelConfig):
        super().__init__()
        config.validate()
        self.config = config
        #: Имя болванки, с которой начались веса. Нужно серверу: по нему он
        #: находит правильный токенизатор, если локальной копии рядом нет.
        self.base_model: Optional[str] = None

        self.transformer = nn.ModuleDict(dict(
            wte=nn.Embedding(config.vocab_size, config.n_embd),   # эмбеддинги токенов
            wpe=nn.Embedding(config.block_size, config.n_embd),   # эмбеддинги позиций
            drop=nn.Dropout(config.dropout),
            h=nn.ModuleList([Block(config) for _ in range(config.n_layer)]),
            ln_f=nn.LayerNorm(config.n_embd, bias=config.bias),
        ))
        self.lm_head = nn.Linear(config.n_embd, config.vocab_size, bias=False)

        # Связывание весов: матрица эмбеддингов и матрица выхода — одна и та же.
        # Так сделано в GPT-2, и это экономит 38 млн параметров из 124 млн.
        self.transformer.wte.weight = self.lm_head.weight

        self.gradient_checkpointing = False
        self.apply(self._init_weights)

        # Масштабирование инициализации выходных проекций: по остаточному пути
        # складываются 2·n_layer вкладов, и без деления на sqrt(2·n_layer)
        # дисперсия активаций росла бы линейно с глубиной.
        for name, param in self.named_parameters():
            if name.endswith("c_proj.weight"):
                nn.init.normal_(param, mean=0.0, std=0.02 / math.sqrt(2 * config.n_layer))

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    # ------------------------------------------------------------- статистика --

    def num_parameters(self, non_embedding: bool = False) -> int:
        """Число параметров. wte не вычитаем отдельно — она связана с lm_head."""
        total = sum(p.numel() for p in self.parameters())
        if non_embedding:
            total -= self.transformer.wpe.weight.numel()
        return total

    def describe(self) -> str:
        config = self.config
        return (
            f"decoder-only Transformer · {config.n_layer} слоёв · {config.n_head} голов · "
            f"{config.n_embd} признаков · контекст {config.block_size} · "
            f"{self.num_parameters() / 1e6:.1f}M параметров"
        )

    def enable_gradient_checkpointing(self, enabled: bool = True) -> None:
        """Пересчитывать активации блоков на обратном проходе вместо хранения."""
        self.gradient_checkpointing = enabled

    def set_dropout(self, probability: float) -> None:
        """
        Меняет dropout уже собранной модели.

        Нужно при продолжении обучения с чекпоинта: величина dropout лежит в
        сохранённой конфигурации, и без этого метода модель, обученная первым
        этапом на большом корпусе без регуляризации, продолжила бы учиться без
        неё же на шестистах диалогах — то есть заучила бы их наизусть.

        Одним изменением config не обойтись: слои nn.Dropout получают вероятность
        при создании, а внимание держит её отдельным полем для flash-attention.
        """
        self.config.dropout = probability
        for module in self.modules():
            if isinstance(module, nn.Dropout):
                module.p = probability
            elif isinstance(module, CausalSelfAttention):
                module.dropout = probability

    # ------------------------------------------------------------ прямой проход --

    def forward(
        self,
        idx: torch.Tensor,
        targets: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        :param idx: (B, T) индексы токенов
        :param targets: (B, T) сдвинутые цели; -100 маскирует позицию
        :returns: (логиты, функция потерь или None)
        """
        device = idx.device
        B, T = idx.size()
        if T > self.config.block_size:
            raise ValueError(
                f"Последовательность длиной {T} длиннее контекста {self.config.block_size}"
            )

        pos = torch.arange(0, T, dtype=torch.long, device=device)
        tokens = self.transformer.wte(idx)

        # NEFTune: равномерный шум на эмбеддингах слов, только при обучении.
        # Модель перестаёт заучивать точные формулировки ответов и перенимает
        # манеру: ответы выходят развёрнутее и живее. Величина по статье —
        # alpha / sqrt(T·d); на ответах (eval) шума нет вовсе.
        if self.training and getattr(self, "neftune_alpha", 0.0) > 0:
            scale = self.neftune_alpha / math.sqrt(T * tokens.size(-1))
            tokens = tokens + torch.empty_like(tokens).uniform_(-scale, scale)

        x = self.transformer.drop(tokens + self.transformer.wpe(pos))

        for block in self.transformer.h:
            if self.gradient_checkpointing and self.training:
                # use_reentrant=False — вариант, дружащий с AMP и с flash-attention.
                x = torch.utils.checkpoint.checkpoint(block, x, use_reentrant=False)
            else:
                x = block(x)

        x = self.transformer.ln_f(x)

        if targets is not None:
            logits = self.lm_head(x)
            loss = F.cross_entropy(
                logits.view(-1, logits.size(-1)),
                targets.reshape(-1),
                ignore_index=-100,
            )
            return logits, loss

        # На инференсе логиты нужны только для последней позиции: считать их для
        # всех T значит зря умножать на матрицу 768×50257 сотни раз.
        logits = self.lm_head(x[:, [-1], :])
        return logits, None

    # ------------------------------------------------------------- генерация ---

    @torch.no_grad()
    def generate(
        self,
        idx: torch.Tensor,
        max_new_tokens: int = 128,
        temperature: float = 0.8,
        top_k: Optional[int] = 40,
        top_p: Optional[float] = 0.95,
        eos_token_id: Optional[int] = None,
        repetition_penalty: float = 1.05,
    ):
        """
        Авторегрессивная генерация. Отдаёт токены по одному (генератор), чтобы
        сервер мог стримить ответ, не дожидаясь конца.
        """
        self.eval()
        for _ in range(max_new_tokens):
            # обрезаем контекст слева: позиционных эмбеддингов всего block_size
            idx_cond = idx if idx.size(1) <= self.config.block_size else idx[:, -self.config.block_size:]
            logits, _ = self(idx_cond)
            logits = logits[:, -1, :].float()

            # Штраф за повтор: GPT-2 малого размера охотно зацикливается.
            #
            # Значение по умолчанию 1.05, а не привычные 1.1–1.2, и причина
            # конкретно в русском языке. Словарь GPT-2 побайтовый: одна буква
            # кириллицы занимает два-три токена, и эти байтовые токены неизбежно
            # повторяются в любом русском тексте — они и есть алфавит. Наказывая
            # повтор, мы наказываем сам язык: на 1.1 модель начинала обрывать
            # последовательности UTF-8, и ответ превращался в «Крупник, считав
            # вядет» с битыми символами. Для английского, где токен — это слово,
            # такой беды нет, оттого и разошлись рекомендации.
            if repetition_penalty and repetition_penalty != 1.0:
                for b in range(idx.size(0)):
                    seen = torch.unique(idx[b])
                    scores = logits[b, seen]
                    # отрицательные логиты штрафуются умножением, положительные — делением
                    logits[b, seen] = torch.where(
                        scores < 0, scores * repetition_penalty, scores / repetition_penalty
                    )

            if temperature <= 0:
                next_token = torch.argmax(logits, dim=-1, keepdim=True)
            else:
                logits = logits / temperature

                if top_k:
                    k = min(top_k, logits.size(-1))
                    threshold = torch.topk(logits, k, dim=-1).values[:, [-1]]
                    logits = logits.masked_fill(logits < threshold, float("-inf"))

                if top_p and 0 < top_p < 1.0:
                    ordered, order = torch.sort(logits, descending=True, dim=-1)
                    cumulative = torch.cumsum(F.softmax(ordered, dim=-1), dim=-1)
                    remove = cumulative - F.softmax(ordered, dim=-1) > top_p
                    ordered = ordered.masked_fill(remove, float("-inf"))
                    logits = torch.full_like(logits, float("-inf")).scatter(-1, order, ordered)

                next_token = torch.multinomial(F.softmax(logits, dim=-1), num_samples=1)

            idx = torch.cat((idx, next_token), dim=1)
            yield next_token

            if eos_token_id is not None and (next_token == eos_token_id).all():
                return

    # ------------------------------------------------- предобученные веса (HF) --

    @classmethod
    def from_pretrained(
        cls,
        name: str = DEFAULT_MODEL,
        dropout: Optional[float] = None,
        block_size: Optional[int] = None,
    ) -> "CloudHDRGPT":
        """
        Загружает болванку OpenAI с Hugging Face и переносит веса в эту реализацию.

        Тонкость, из-за которой нельзя просто вызвать load_state_dict: HF-версия
        GPT-2 старше nn.Linear в её нынешнем виде и хранит четыре матрицы в
        классе Conv1D с раскладкой (вход, выход), тогда как nn.Linear ждёт
        (выход, вход). Их — и только их — переносим транспонированными.
        """
        from transformers import GPT2LMHeadModel  # импорт здесь: нужен лишь однажды

        resolved = resolve_model_name(name)
        config = ModelConfig.from_pretrained_name(resolved, dropout=dropout, block_size=block_size)

        log(f"Загружаю предобученные веса «{resolved}»...")
        model = cls(config)
        hf_model = GPT2LMHeadModel.from_pretrained(resolved)
        hf_state = hf_model.state_dict()

        target = model.state_dict()
        transposed = ("attn.c_attn.weight", "attn.c_proj.weight", "mlp.c_fc.weight", "mlp.c_proj.weight")
        skip = (".attn.masked_bias", ".attn.bias")  # буферы маски, а не веса

        # Раскладку определяем по факту, а не по версии библиотеки. Исторически
        # HF хранит эти четыре матрицы в классе Conv1D как (вход, выход), но в
        # новых ветках transformers GPT-2 переводят на обычный nn.Linear, где
        # порядок обратный. Судим по c_attn: она неквадратная (768 × 2304), и по
        # первой размерности видно, какая это раскладка. На квадратной
        # attn.c_proj (768 × 768) такой проверки не сделать — потому и смотрим
        # именно на c_attn, а решение применяем ко всем четырём.
        probe_key = next((k for k in hf_state if k.endswith("attn.c_attn.weight")), None)
        needs_transpose = True
        if probe_key is not None:
            needs_transpose = hf_state[probe_key].shape[0] == config.n_embd
            log(f"Раскладка весов HF: {'Conv1D (вход, выход)' if needs_transpose else 'nn.Linear (выход, вход)'}")

        copied = 0
        for key, tensor in hf_state.items():
            if key.endswith(skip):
                continue
            if key not in target:
                continue

            source = tensor.t() if (needs_transpose and key.endswith(transposed)) else tensor
            if source.shape != target[key].shape:
                # Обрезка позиционных эмбеддингов при block_size < 1024.
                if key == "transformer.wpe.weight" and source.shape[0] > target[key].shape[0]:
                    source = source[: target[key].shape[0]]
                else:
                    raise RuntimeError(
                        f"Несовпадение формы у «{key}»: {tuple(source.shape)} против "
                        f"{tuple(target[key].shape)}"
                    )
            with torch.no_grad():
                target[key].copy_(source)
            copied += 1

        # Если имена параметров в новой версии transformers разошлись с нашими,
        # цикл выше тихо не скопирует ничего, и модель останется случайной — а
        # выглядеть это будет как «загрузилось, просто отвечает ерундой».
        # Поэтому проверяем объём переноса: у GPT-2 из 12 слоёв это ~148 тензоров.
        expected = 4 + 12 * config.n_layer
        if copied < expected * 0.9:
            raise RuntimeError(
                f"Перенесено лишь {copied} тензоров из ожидаемых ~{expected}. "
                f"Похоже, раскладка имён в transformers изменилась. "
                f"Примеры ключей HF: {list(hf_state)[:5]}"
            )

        del hf_model, hf_state

        # lm_head.weight связан с wte.weight, поэтому в state_dict он один; если
        # HF отдал только одну из двух форм, вторая подтянется сама.
        model.transformer.wte.weight = model.lm_head.weight

        model.base_model = resolved
        log(f"Перенесено тензоров: {copied} · {model.describe()}")
        return model

    @torch.no_grad()
    def verify_against_hf(self, name: str = DEFAULT_MODEL, tolerance: float = 1e-4) -> bool:
        """
        Самопроверка: прогоняет один и тот же вход через эту реализацию и через
        эталон из transformers и сравнивает логиты. Если сходятся — перенос
        весов и математика блоков верны.
        """
        from transformers import GPT2LMHeadModel

        was_training = self.training
        self.eval()
        try:
            reference = GPT2LMHeadModel.from_pretrained(resolve_model_name(name)).eval()
            probe = torch.randint(0, self.config.vocab_size, (1, 24))

            mine = self.lm_head(self._hidden_states(probe))
            theirs = reference(probe).logits

            delta = (mine - theirs).abs().max().item()
            ok = delta < tolerance
            log(("Сверка с эталоном HF пройдена" if ok else "Сверка с эталоном HF ПРОВАЛЕНА")
                + f": максимальное расхождение логитов {delta:.2e}")
            return ok
        finally:
            self.train(was_training)

    def _hidden_states(self, idx: torch.Tensor) -> torch.Tensor:
        """Скрытые состояния всех позиций — нужны только для сверки с эталоном."""
        pos = torch.arange(0, idx.size(1), dtype=torch.long, device=idx.device)
        x = self.transformer.drop(self.transformer.wte(idx) + self.transformer.wpe(pos))
        for block in self.transformer.h:
            x = block(x)
        return self.transformer.ln_f(x)

    # ---------------------------------------------------------- оптимизатор ----

    # ---------------------------------------------------------------- LoRA ---

    def apply_lora(self, rank: int = 16, alpha: int = 32, dropout: float = 0.05) -> int:
        """
        Замораживает модель и подвешивает к её матрицам обучаемые поправки.

        Зачем. Полное обучение модели на 760M требует держать в памяти веса
        (2.8 ГБ), градиент к каждому весу (ещё 2.8) и два момента AdamW на
        каждый вес (5.7) — итого 11.3 ГБ, чего на карте с восемью нет и не
        будет. LoRA снимает именно эту статью расходов: базовые веса
        замораживаются, градиенты и моменты нужны только поправкам, а их в
        двести раз меньше.

        Как. Рядом с каждой большой матрицей W ставится пара узких: A (rank ×
        вход) и B (выход × rank). Слой считает Wx + BAx·(alpha/rank). Матрица
        BA имеет ранг не выше rank — отсюда название, — и этого хватает, чтобы
        менять манеру ответа, ради которой всё и делается.

        Ключевая деталь: B инициализируется НУЛЯМИ. Поэтому в первый момент
        поправка равна нулю и модель ведёт себя ровно как исходная — обучение
        начинается не со случайного шума, а с уже осмысленной болванки. Если бы
        обе матрицы были случайными, первые шаги ломали бы предобучение.

        :returns: сколько обучаемых параметров получилось
        """
        targets = ("c_attn", "c_proj", "c_fc")

        for param in self.parameters():
            param.requires_grad = False

        replaced = 0
        for block in self.transformer.h:
            for parent in (block.attn, block.mlp):
                for name, child in list(parent.named_children()):
                    if name in targets and isinstance(child, nn.Linear):
                        setattr(parent, name, LoRALinear(child, rank, alpha, dropout))
                        replaced += 1

        trainable = sum(p.numel() for p in self.parameters() if p.requires_grad)
        total = sum(p.numel() for p in self.parameters())
        log(f"LoRA: ранг {rank}, альфа {alpha} · заменено матриц {replaced} · "
            f"обучается {trainable / 1e6:.2f}M из {total / 1e6:.1f}M "
            f"({100 * trainable / total:.2f}%)")
        return trainable

    @torch.no_grad()
    def merged_state_dict(self) -> dict:
        """
        Веса с вписанными поправками — НЕ трогая живую модель.

        Нужно именно так, а не через merge_lora: чекпоинты сохраняются по ходу
        обучения, каждые несколько сотен шагов. Слей мы поправки на месте —
        обучение после первого же сохранения продолжилось бы без них, то есть
        сломалось бы молча и в самом незаметном месте.
        """
        state = dict(self.state_dict())
        for name, module in self.named_modules():
            if not isinstance(module, LoRALinear):
                continue
            weight = state.pop(f"{name}.base.weight")
            delta = (module.b @ module.a) * module.scale
            state[f"{name}.weight"] = (weight + delta.to(weight.dtype)).clone()
            bias = state.pop(f"{name}.base.bias", None)
            if bias is not None:
                state[f"{name}.bias"] = bias
            state.pop(f"{name}.a", None)
            state.pop(f"{name}.b", None)
        return state

    def merge_lora(self) -> int:
        """
        Вписывает поправки в сами веса и убирает обёртки.

        Делается перед сохранением, и это важнее, чем кажется: после слияния
        получается обычный чекпоинт обычной модели. Серверу, токенизатору и
        всему остальному коду не нужно знать про LoRA вообще — они видят те же
        веса той же формы, что и всегда. Иначе пришлось бы учить загрузку
        отличать «модель» от «модель плюс поправки» и тащить это различие
        через весь проект.
        """
        merged = 0
        for block in self.transformer.h:
            for parent in (block.attn, block.mlp):
                for name, child in list(parent.named_children()):
                    if isinstance(child, LoRALinear):
                        setattr(parent, name, child.merged())
                        merged += 1

        for param in self.parameters():
            param.requires_grad = True
        log(f"Поправки LoRA вписаны в веса: {merged} матриц — чекпоинт обычный")
        return merged

    def configure_optimizers(
        self,
        weight_decay: float,
        learning_rate: float,
        betas: Tuple[float, float],
        device_type: str = "cuda",
    ) -> torch.optim.AdamW:
        """
        AdamW с раздельными группами.

        Затухание весов применяется к матрицам, но НЕ к смещениям и не к
        параметрам LayerNorm: у них нет избыточности, которую надо гасить, и
        штраф за их величину только мешает нормализации делать свою работу.
        """
        decay, no_decay = [], []
        for _, param in self.named_parameters():
            if not param.requires_grad:
                continue
            (decay if param.dim() >= 2 else no_decay).append(param)

        groups = [
            {"params": decay, "weight_decay": weight_decay},
            {"params": no_decay, "weight_decay": 0.0},
        ]

        # fused-версия держит шаг оптимизатора целиком на GPU — заметно быстрее.
        extra = {}
        if device_type == "cuda" and "fused" in torch.optim.AdamW.__init__.__code__.co_varnames:
            extra["fused"] = True

        return torch.optim.AdamW(groups, lr=learning_rate, betas=betas, **extra)
