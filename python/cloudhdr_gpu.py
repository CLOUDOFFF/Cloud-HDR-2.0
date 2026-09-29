"""
Cloud HDR — очередь видеопамяти.

Три тяжёлые части работают по очереди, а не одновременно: распознавание речи
(Hub), модель разговора (сервер 8080) и голос (служба 4482). Каждая перед
работой смотрит, хватает ли свободной видеопамяти, и если нет — просит
остальных уступить: те уезжают в оперативную память (распознавание и голос —
за доли секунды) или закрываются целиком (модель на llama.cpp — поднимается
снова за пару секунд).

Зачем. Все три сразу занимали 6-7 ГБ. По очереди пик — самая большая из них
плюс немного служебной памяти, около 3,5-4 ГБ: приложение идёт на ноутбуке с
6 ГБ видеопамяти.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request

NO_WINDOW = 0x08000000

PARTS = {
    'llm': 'http://127.0.0.1:8080/internal/release',
    'asr': 'http://127.0.0.1:4480/hub/asr/park',
    'tts': 'http://127.0.0.1:4482/voice/park',
}


def free_gb() -> float:
    """Свободная видеопамять по всей карте (а не только своего процесса)."""
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW).stdout
        return float(out.strip().splitlines()[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 99.0          # не узнали — не мешаем работать


#: Строгая очередь: на карте всегда одна тяжёлая часть, сколько бы памяти ни было.
#: Правило «уступай, только если не хватает» на карте 8 ГБ пускало голос рядом с
#: моделью — 6,6 ГБ вместе с Windows. Хозяину нужен потолок 4 ГБ на любом
#: компьютере (показ на ноутбуке с 6 ГБ), поэтому уступают всегда.
EXCLUSIVE = True


def make_room(need_gb: float, me: str) -> float:
    """Попросить остальных уступить карту. Вернуть, сколько свободно."""
    free = free_gb()
    if free >= need_gb and not EXCLUSIVE:
        return free
    for who, url in PARTS.items():
        if who == me:
            continue
        try:
            request = urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=8) as response:
                json.loads(response.read() or b'{}')
        except Exception:  # noqa: BLE001 — соседа может не быть, это нормально
            pass
        if not EXCLUSIVE:
            free = free_gb()
            if free >= need_gb:
                break
    return free_gb()
