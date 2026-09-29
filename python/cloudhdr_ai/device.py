"""
Cloud HDR AI — определение устройства и выбор смешанной точности.

Отдельный модуль появился из-за одной конкретной ловушки. RTX 5060 построена на
архитектуре Blackwell, её вычислительная способность — sm_120. Сборки PyTorch
под CUDA 12.4 и старше кода для sm_120 не содержат вовсе, и карта на них не
работает: torch.cuda.is_available() честно вернёт True, память покажется, а на
первом же матмуле прилетит

    CUDA error: no kernel image is available for execution on the device

Сообщение не намекает на причину, и его легко принять за поломку драйвера или
нехватку памяти. Поэтому список архитектур сборки проверяется ДО обучения, а не
после часа отладки: preflight() говорит прямо, что ставить.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch

from .branding import log

#: Вычислительная способность Blackwell (RTX 50xx).
BLACKWELL_SM = "sm_120"

#: Индекс колёс PyTorch, собранных с CUDA 12.8 — первых с поддержкой sm_120.
CU128_INDEX = "https://download.pytorch.org/whl/cu128"


@dataclass
class DeviceInfo:
    device: torch.device
    type: str                       # cuda | cpu
    name: str
    vram_gb: Optional[float]
    capability: Optional[str]       # например «sm_120»
    supports_bf16: bool
    arch_list: tuple
    compatible: bool                # умеет ли эта сборка torch эту карту
    warning: Optional[str] = None

    @property
    def is_cuda(self) -> bool:
        return self.type == "cuda"


def detect(prefer: str = "auto") -> DeviceInfo:
    """Собирает всё, что нужно знать о железе перед запуском."""
    if prefer == "cpu" or not torch.cuda.is_available():
        reason = "выбран вручную" if prefer == "cpu" else "CUDA недоступна"
        return DeviceInfo(
            device=torch.device("cpu"), type="cpu", name=f"CPU ({reason})",
            vram_gb=None, capability=None, supports_bf16=False,
            arch_list=(), compatible=True,
            warning=None if prefer == "cpu" else
            "CUDA не найдена — обучение на CPU медленнее в десятки раз",
        )

    index = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(index)
    major, minor = torch.cuda.get_device_capability(index)
    capability = f"sm_{major}{minor}"
    vram_gb = props.total_memory / (1024 ** 3)

    try:
        arch_list = tuple(torch.cuda.get_arch_list())
    except Exception:
        arch_list = ()

    # Карта поддерживается, если её sm есть в сборке напрямую либо покрыт
    # PTX-кодом более ранней версии (…_compute_XX), который драйвер скомпилирует
    # на лету — медленнее на старте, но работает.
    compatible = (not arch_list) or (capability in arch_list) or any(
        item.startswith("compute_") and int(item.split("_")[1]) >= major * 10 + minor
        for item in arch_list
    )

    warning = None
    if not compatible:
        warning = (
            f"Сборка PyTorch {torch.__version__} (CUDA {torch.version.cuda}) не содержит кода "
            f"для {capability} — доступны только {', '.join(arch_list) or 'нет данных'}.\n"
            f"       {props.name} на ней работать НЕ будет: первый же матмул упадёт с "
            f"«no kernel image is available».\n"
            f"       Лечится переустановкой torch со сборкой под CUDA 12.8:\n"
            f"       pip install --force-reinstall torch --index-url {CU128_INDEX}"
        )

    return DeviceInfo(
        device=torch.device("cuda", index),
        type="cuda",
        name=props.name,
        vram_gb=vram_gb,
        capability=capability,
        supports_bf16=torch.cuda.is_bf16_supported(),
        arch_list=arch_list,
        compatible=compatible,
        warning=warning,
    )


def choose_precision(info: DeviceInfo, requested: str = "auto") -> str:
    """
    Выбирает рабочую точность.

    Почему bf16 предпочтительнее fp16 на этой карте. У fp16 всего 5 бит
    экспоненты: градиенты мельче ~6e-8 обнуляются, и, чтобы этого избежать,
    приходится держать GradScaler, который домножает функцию потерь на большой
    коэффициент и откатывает шаги при переполнении. У bf16 экспонента такая же,
    как у fp32 (8 бит) — недолёта нет, масштабирование не нужно, шаги не
    пропадают. Мантисса короче, но для обучения это почти не мешает.
    Blackwell считает bf16 на тензорных ядрах с той же скоростью, что и fp16.
    """
    if requested != "auto":
        if requested == "bf16" and info.is_cuda and not info.supports_bf16:
            log("bf16 не поддерживается этой картой — перехожу на fp16")
            return "fp16"
        return requested

    if not info.is_cuda:
        return "fp32"                       # на CPU автокаст даёт только замедление
    return "bf16" if info.supports_bf16 else "fp16"


def autocast_context(device_type: str, precision: str):
    """
    Контекст смешанной точности.

    torch.amp.autocast — нынешнее имя того, что раньше звалось
    torch.cuda.amp.autocast; старый путь работает, но с предупреждением об
    устаревании. Ветка ниже оставлена для torch < 2.0.
    """
    if precision == "fp32" or device_type != "cuda":
        return torch.autocast(device_type="cpu", enabled=False)

    dtype = torch.bfloat16 if precision == "bf16" else torch.float16
    try:
        return torch.amp.autocast(device_type=device_type, dtype=dtype)
    except (AttributeError, TypeError):
        return torch.cuda.amp.autocast(dtype=dtype)


def make_grad_scaler(precision: str, device_type: str):
    """
    GradScaler нужен ровно одному режиму — fp16.

    Для bf16 и fp32 создаётся выключенный: тогда вызывающий код не обрастает
    ветвлениями, scaler.scale(loss) и scaler.step(opt) работают как no-op.
    """
    enabled = precision == "fp16" and device_type == "cuda"
    try:
        return torch.amp.GradScaler(device_type, enabled=enabled)
    except (AttributeError, TypeError):
        return torch.cuda.amp.GradScaler(enabled=enabled)


def apply_speedups(info: DeviceInfo, tf32: bool = True) -> None:
    """
    Разрешает тензорным ядрам считать матмулы fp32 в формате TF32.

    Мантисса урезается с 23 бит до 10, диапазон остаётся прежним. Для обучения
    сетей потеря незаметна, а скорость матмулов вырастает в разы. Касается тех
    операций, что остались вне автокаста.
    """
    if not (info.is_cuda and tf32):
        return
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    try:
        torch.set_float32_matmul_precision("high")
    except AttributeError:
        pass


def memory_report(info: DeviceInfo) -> str:
    """Сколько видеопамяти занято прямо сейчас — для логов обучения."""
    if not info.is_cuda:
        return "CPU"
    allocated = torch.cuda.memory_allocated() / (1024 ** 3)
    reserved = torch.cuda.memory_reserved() / (1024 ** 3)
    peak = torch.cuda.max_memory_allocated() / (1024 ** 3)
    total = info.vram_gb or 0.0
    return f"VRAM {allocated:.2f}/{total:.1f} ГБ (резерв {reserved:.2f}, пик {peak:.2f})"


def preflight(prefer: str = "auto", quiet: bool = False) -> DeviceInfo:
    """Печатает паспорт железа и предупреждает о несовместимости сборки."""
    info = detect(prefer)
    if quiet:
        return info

    if info.is_cuda:
        log(f"Устройство: {info.name} · {info.vram_gb:.1f} ГБ · {info.capability}")
        log(f"PyTorch {torch.__version__} · CUDA {torch.version.cuda} · "
            f"bf16 {'есть' if info.supports_bf16 else 'нет'}")
    else:
        log(f"Устройство: {info.name} · PyTorch {torch.__version__}")

    if info.warning:
        log("ВНИМАНИЕ: " + info.warning)
    return info
