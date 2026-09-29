"""
Cloud HDR AI — языковой модуль проекта Cloud HDR.

Decoder-only Transformer Cloud HDR (до 760M параметров) с загрузкой
предобученных весов «openai-community/gpt2», тренировочным циклом под RTX 5060
и сервером, совместимым с диалектом OpenAI.

Быстрый старт из папки python/:

    pip install -r requirements.txt
    python -m cloudhdr_ai info                 # что за железо и потянет ли
    python -m cloudhdr_ai train --from-brain   # дообучить на корпусе проекта
    python -m cloudhdr_ai serve                # поднять модель для интерфейса

Тяжёлые зависимости (torch, transformers) намеренно не импортируются на уровне
пакета: команда `info` должна уметь объяснить, чего не хватает, а не падать с
ImportError до первой строчки вывода.
"""

from .branding import BRAND, BRAND_AI, PRODUCT, TAG, VERSION, banner, log

__version__ = VERSION
__all__ = ["BRAND", "BRAND_AI", "PRODUCT", "TAG", "VERSION", "banner", "log", "__version__"]
