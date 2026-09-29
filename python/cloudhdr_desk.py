"""
Cloud HDR — интеграция с ПК, группа 2: медиа, буфер обмена, напоминания.

Всё здесь зовёт служба cloudhdr_hub.py (маршруты /hub/media, /hub/clip,
/hub/remind). Модуль отдельный, потому что хаб и так длинный, а эти три умения
не делят с остальным ничего, кроме записи в журнал.

  • медиа        — «пауза», «следующий трек», «что играет». Управляем не
                   клавишами мультимедиа вслепую, а через «Сейчас играет»
                   Windows (GlobalSystemMediaTransportControls): так известно,
                   какой плеер отвечает и какой трек стал играть.
  • буфер        — прочитать, перевести, посчитать, очистить. Только текст:
                   картинки и файлы из буфера сюда не берём.
  • напоминания  — «напомни через 20 минут выключить духовку», «в 18:30»,
                   «завтра в 9». Хранятся в файле и переживают перезапуск;
                   срабатывают, даже если окно приложения закрыто, —
                   уведомлением Windows от имени Cloud HDR.
"""
from __future__ import annotations

import ctypes
import json
import re
import subprocess
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

NO_WINDOW = 0x08000000
ROOT = Path(__file__).resolve().parent


def run_ps(script: str, timeout: int = 20) -> str:
    """Сценарий PowerShell из файла: через -Command WinRT-обёртки ломаются на кавычках."""
    tmp = Path(__import__('tempfile').gettempdir()) / f'cloudhdr-{uuid.uuid4().hex}.ps1'
    tmp.write_text('[Console]::OutputEncoding=[Text.Encoding]::UTF8\n' + script, encoding='utf-8-sig')
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-File', str(tmp)], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.stdout.decode('utf-8', 'replace')
    except (OSError, subprocess.TimeoutExpired):
        return ''
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ================================================================== медиа ===

MEDIA_PS = r'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); [void]$t.Wait(5000); $t.Result }
[void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$s = $mgr.GetCurrentSession()
if (-not $s) {
  # «текущей» Windows считает последнюю активную; если её нет — берём любую играющую
  $s = $mgr.GetSessions() | Where-Object { "$($_.GetPlaybackInfo().PlaybackStatus)" -eq 'Playing' } | Select-Object -First 1
}
if (-not $s) { '{"session":false}'; exit }
$act = '__ACTION__'
$ok = $true
switch ($act) {
  'toggle' { $ok = Await ($s.TryTogglePlayPauseAsync()) ([bool]) }
  'play'   { $ok = Await ($s.TryPlayAsync()) ([bool]) }
  'pause'  { $ok = Await ($s.TryPauseAsync()) ([bool]) }
  'next'   { $ok = Await ($s.TrySkipNextAsync()) ([bool]) }
  'prev'   { $ok = Await ($s.TrySkipPreviousAsync()) ([bool]) }
  'stop'   { $ok = Await ($s.TryStopAsync()) ([bool]) }
}
# после «следующего» плеер меняет трек не мгновенно — даём ему время
if ($act -in @('next', 'prev', 'toggle', 'play', 'pause', 'stop')) { Start-Sleep -Milliseconds 900 }
$p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
[pscustomobject]@{ session = $true; ok = [bool]$ok; app = $s.SourceAppUserModelId;
  status = "$($s.GetPlaybackInfo().PlaybackStatus)"; artist = $p.Artist; title = $p.Title } | ConvertTo-Json -Compress
'''

# Имена плееров так, как их называют люди, а не по идентификатору приложения
PLAYERS = [
    ('yandex', 'Яндекс Музыка'), ('spotify', 'Spotify'), ('vk', 'VK Музыка'), ('chrome', 'браузер'),
    ('msedge', 'браузер'), ('firefox', 'браузер'), ('opera', 'браузер'), ('zunemusic', 'Медиаплеер'),
    ('media player', 'Медиаплеер'), ('vlc', 'VLC'), ('aimp', 'AIMP'), ('foobar', 'foobar2000'),
    ('telegram', 'Telegram'), ('itunes', 'iTunes'), ('apple', 'Apple Music'),
]


def player_name(app_id: str) -> str:
    low = (app_id or '').lower()
    for key, name in PLAYERS:
        if key in low:
            return name
    return 'плеер'


MEDIA_PATTERNS = [
    # (выражение, действие) — сначала точные, потом общие
    (r'^(что|какая|какой)\s+(сейчас\s+)?(у\s+меня\s+)?(играет|звучит)$|(что|какая|какой)\s+(сейчас\s+)?(играет|звучит)\s+(музык|песн|трек)'
     r'|(что|какая|какой)\s+(это\s+|сейчас\s+)?(за\s+)?(песня|трек|музыка)(\s+играет|\s+звучит)?$|как\s+называется\s+(эта\s+)?(песня|трек)', 'status'),
    (r'(следующ\w*|другую|другой|переключи|пропусти|дальше)\s+(\w+\s+)?(трек|песн|композици|музык)|^следующ\w*$|^некст$|^next$|^skip$', 'next'),
    (r'(предыдущ\w*|прошл\w*|назад)\s*(трек|песн|композици)|верни\s+(прошл|предыдущ)\w*\s+(трек|песн)', 'prev'),
    (r'(поставь|поставить)\s+(музыку\s+|трек\s+|песню\s+)?на\s+паузу|^пауза$|пауза\s+(музык|трек|песн)|останови\s+(музык|трек|песн|плеер)|(выключи|стоп)\s+музык', 'pause'),
    (r'(продолжи|возобнови|сними\s+с\s+паузы|включи\s+обратно)\s+(\w+\s+)?(музык|трек|песн|воспроизведени)|^(продолжи|возобнови|сними\s+с\s+паузы|плей|play)$'
     r'|(включи|запусти)\s+музык\w*$', 'play'),
]


def match_media(text: str) -> str:
    low = text.lower().strip().rstrip('.!?')
    for pattern, action in MEDIA_PATTERNS:
        if re.search(pattern, low):
            return action
    return ''


def media(action: str) -> dict:
    """Действие с активным плеером; ответ — что играет после него."""
    raw = run_ps(MEDIA_PS.replace('__ACTION__', action), 25).strip()
    try:
        data = json.loads(raw.splitlines()[-1]) if raw else {}
    except ValueError:
        data = {}
    return data


def describe_track(info: dict) -> str:
    title = (info.get('title') or '').strip()
    artist = (info.get('artist') or '').strip()
    if not title:
        return ''
    return f'«{title}»' + (f' — {artist}' if artist else '')


def media_answer(action: str, text: str = '') -> dict:
    info = media(action)
    if not info.get('session'):
        # «Включи музыку», когда ничего не играет, — это не «продолжи», а просьба
        # выбрать: пусть решает модель («Что сегодня по душе?»).
        if action == 'play' and re.search(r'(включи|запусти|поставь)\s+(\w+\s+)?музык', text.lower()):
            return {'ok': False, 'known': False}
        return {'ok': False, 'known': True, 'say': 'Сейчас ничего не играет — ни один плеер не открыт. '
                                                   'Включите музыку в Яндекс Музыке, Spotify или браузере, и я смогу ею управлять.'}
    who = player_name(info.get('app', ''))
    track = describe_track(info)
    playing = info.get('status') == 'Playing'
    if action == 'status':
        if not track:
            return {'ok': True, 'known': True, 'say': f'В {who} что-то {"играет" if playing else "стоит на паузе"}, но название плеер не сообщает.'}
        return {'ok': True, 'known': True, 'say': f'{"Играет" if playing else "На паузе"} {track} ({who}).', 'track': track}
    if not info.get('ok'):
        return {'ok': False, 'known': True, 'say': f'{who.capitalize()} не дал это сделать — у некоторых плееров кнопки для Windows отключены.'}
    if action == 'pause':
        say = 'Поставил на паузу.' + (f' Остановились на {track}.' if track else '')
    elif action == 'play':
        say = f'Продолжаю: {track}.' if track else 'Продолжаю воспроизведение.'
    elif action in ('next', 'prev'):
        say = f'{"Следующий" if action == "next" else "Предыдущий"} трек: {track}.' if track else 'Переключил.'
    elif action == 'stop':
        say = 'Остановил.'
    else:
        say = ('Играет ' + track + '.') if playing and track else 'Поставил на паузу.' if not playing else 'Включил.'
    return {'ok': True, 'known': True, 'say': say, 'track': track, 'playing': playing, 'player': who}


# ========================================================= буфер обмена ===

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]


def _open_clipboard() -> bool:
    # Буфер держит одна программа за раз: если его как раз заняли, пробуем ещё
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def clip_get() -> str | None:
    """Текст из буфера; None — буфер занят; '' — в нём не текст или пусто."""
    if not _open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ''
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ''
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ''
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clip_set(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if not text:
            return True
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


CLIP_PATTERNS = [
    (r'(очисти|очистить|сотри|стереть)\s+(мне\s+)?буфер', 'clear'),
    (r'перевед\w*\s+(то\s*,?\s*)?(что\s+)?(я\s+)?(скопировал|скопирова\w*)|перевед\w*\s+.*буфер|перевод\s+.*буфер', 'translate'),
    (r'(прочитай|прочти|зачитай|озвучь)\s+(вслух\s+)?(то\s*,?\s*)?(что\s+)?(я\s+)?(скопировал|.*буфер)', 'read'),
    (r'(сколько|посчитай)\s+.*(слов|символ|букв|знаков)\s+.*(буфер|скопировал)', 'count'),
    (r'(что|покажи|посмотри)\s+.*(в\s+буфере|скопировал)|что\s+в\s+буфере|содержимое\s+буфера', 'show'),
]


def match_clip(text: str) -> str:
    low = text.lower().strip()
    for pattern, action in CLIP_PATTERNS:
        if re.search(pattern, low):
            return action
    return ''


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return one
    if 2 <= a <= 4 and not 12 <= b <= 14:
        return few
    return many


def clip_answer(action: str, translate: Callable[[str], str] | None) -> dict:
    if action == 'clear':
        ok = clip_set('')
        return {'ok': ok, 'known': True, 'say': 'Очистил буфер обмена.' if ok else 'Буфер обмена сейчас занят другой программой — попробуйте ещё раз.'}
    text = clip_get()
    if text is None:
        return {'ok': False, 'known': True, 'say': 'Буфер обмена сейчас занят другой программой — попробуйте ещё раз.'}
    if not text.strip():
        return {'ok': True, 'known': True, 'say': 'В буфере обмена нет текста. Скопируйте текст (Ctrl+C) и повторите.'}
    text = text.strip()
    words = len(re.findall(r'\S+', text))
    if action == 'count':
        return {'ok': True, 'known': True, 'say': f'В буфере {words} {plural(words, "слово", "слова", "слов")} и '
                                                    f'{len(text)} {plural(len(text), "символ", "символа", "символов")}.'}
    if action == 'translate':
        latin = len(re.findall(r'[A-Za-z]', text))
        cyr = len(re.findall(r'[А-Яа-яЁё]', text))
        if cyr > latin:
            return {'ok': True, 'known': True, 'say': 'Текст в буфере уже на русском — переводить нечего. Я перевожу с английского на русский.'}
        if translate is None:
            return {'ok': False, 'known': True, 'say': 'Переводчик ещё не готов — попробуйте через несколько секунд.'}
        if len(text) > 6000:
            text = text[:6000]
        result = translate(text)
        clip_set(result)
        return {'ok': True, 'known': True, 'say': 'Перевёл и положил перевод в буфер обмена — можно вставлять (Ctrl+V).',
                'text': result, 'source': text}
    if action == 'read':
        return {'ok': True, 'known': True, 'say': text[:1500], 'speak': True}
    shown = text if len(text) <= 1200 else text[:1200] + ' …'
    return {'ok': True, 'known': True, 'say': f'В буфере {words} {plural(words, "слово", "слова", "слов")}:', 'text': shown}


# ============================================================ напоминания ===

NUM_WORDS = {
    'ноль': 0, 'одну': 1, 'один': 1, 'одна': 1, 'две': 2, 'два': 2, 'пару': 2, 'три': 3, 'четыре': 4, 'пять': 5,
    'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10, 'одиннадцать': 11, 'двенадцать': 12,
    'тринадцать': 13, 'четырнадцать': 14, 'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17,
    'восемнадцать': 18, 'девятнадцать': 19, 'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50,
}
UNITS = [(r'сек\w*', 1), (r'мин\w*', 60), (r'час\w*|ч\b', 3600), (r'сут\w*|дн\w*|день', 86400)]
NUM = r'(\d+(?:[.,]\d+)?|(?:' + '|'.join(sorted(NUM_WORDS, key=len, reverse=True)) + r')(?:\s+(?:' + '|'.join(
    k for k, v in NUM_WORDS.items() if v < 10) + r'))?)'


def to_number(token: str) -> float:
    token = token.strip()
    if re.match(r'\d', token):
        return float(token.replace(',', '.'))
    return float(sum(NUM_WORDS.get(part, 0) for part in token.split()))


def unit_seconds(word: str) -> int:
    for pattern, sec in UNITS:
        if re.fullmatch(pattern, word):
            return sec
    return 0


WEEKDAYS = {'понедельник': 0, 'вторник': 1, 'сред': 2, 'четверг': 3, 'пятниц': 4, 'суббот': 5, 'воскресень': 6}


def parse_when(text: str, now: datetime) -> tuple[datetime | None, str]:
    """Когда сработать и какой текст остался после вырезания времени."""
    low = ' ' + text.lower().replace('ё', 'е') + ' '
    spans: list[tuple[int, int]] = []

    def cut(match: re.Match) -> None:
        spans.append(match.span())

    # 1. «через …»: полчаса, полтора часа, час, 10 минут, 2 часа 15 минут, минуту
    rel = 0.0
    m = re.search(r'\bчерез\s+(полчаса|полтора\s+часа|полторы\s+минуты|час\b|минуту\b|секунду\b|сутки\b|' +
                  NUM + r'\s*(сек\w*|мин\w*|час\w*|ч\b|сут\w*|дн\w*|день))(?:\s*(?:и\s+)?' + NUM +
                  r'\s*(сек\w*|мин\w*))?', low)
    if m:
        whole = m.group(1)
        if whole.startswith('полчаса'):
            rel = 1800
        elif whole.startswith('полтора'):
            rel = 5400
        elif whole.startswith('полторы'):
            rel = 90
        elif whole.startswith('час'):
            rel = 3600
        elif whole.startswith('минуту'):
            rel = 60
        elif whole.startswith('секунду'):
            rel = 1
        elif whole.startswith('сутки'):
            rel = 86400
        else:
            rel = to_number(m.group(2)) * unit_seconds(m.group(3))
        if m.group(4):
            rel += to_number(m.group(4)) * unit_seconds(m.group(5))
        cut(m)

    # 1б. таймер без «через»: «таймер на 5 минут», «засеки 10 минут»
    if not rel:
        m = re.search(r'(?:таймер\w*|засеки|отсчитай|отсчет\w*|пикни)\s+(?:на\s+)?(?:время\s+)?' + NUM +
                      r'\s*(сек\w*|мин\w*|час\w*|ч\b)', low)
        if m:
            rel = to_number(m.group(1)) * unit_seconds(m.group(2))
            spans.append((m.start(1), m.end()))
    if rel:
        rest = low
        for a, b in sorted(spans, reverse=True):
            rest = rest[:a] + ' ' + rest[b:]
        # «через 99999999 дней» роняло службу: такой даты в календаре нет.
        # Срезаем до чуть больше года — дальше remind_answer вежливо откажет.
        return now + timedelta(seconds=min(rel, 367 * 86400)), rest

    # 2. день: сегодня / завтра / послезавтра / в пятницу
    day = now.date()
    day_given = False
    m = re.search(r'\b(сегодня|завтра|послезавтра)\b', low)
    if m:
        day = now.date() + timedelta(days={'сегодня': 0, 'завтра': 1, 'послезавтра': 2}[m.group(1)])
        day_given = True
        cut(m)
    else:
        m = re.search(r'\bв(?:о)?\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b', low)
        if m:
            target = next(v for k, v in WEEKDAYS.items() if m.group(1).startswith(k))
            ahead = (target - now.weekday()) % 7 or 7
            day = now.date() + timedelta(days=ahead)
            day_given = True
            cut(m)

    # 3. время: «в 18:30», «в 9», «в 7 утра», «в 8 вечера», «в полдень», «к 10»
    hour = minute = None
    m = re.search(r'\b(?:в|к|на)\s+(\d{1,2})[:.](\d{2})\b', low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        cut(m)
    else:
        m = re.search(r'\b(?:в|к)\s+(\d{1,2}|' + '|'.join(k for k, v in NUM_WORDS.items() if 1 <= v <= 12) +
                      r')(?:\s*(?:час\w*|ч\b))?(?:\s+(\d{1,2})\s*мин\w*)?(?:\s+(утра|дня|вечера|ночи))?\b', low)
        if m:
            hour = int(to_number(m.group(1)))
            minute = int(m.group(2)) if m.group(2) else 0
            part = m.group(3)
            if part in ('дня', 'вечера') and hour < 12:
                hour += 12
            elif part == 'ночи' and hour == 12:
                hour = 0
            elif part is None and not day_given and hour < 12:
                # «в 5» без уточнения: ближайшее будущее — 5 утра или 17:00
                if datetime.combine(now.date(), datetime.min.time()).replace(hour=hour) <= now:
                    hour = hour + 12 if hour + 12 < 24 else hour
            cut(m)
        else:
            m = re.search(r'\bв\s+(полдень|полночь)\b', low)
            if m:
                hour, minute = (12, 0) if m.group(1) == 'полдень' else (0, 0)
                if m.group(1) == 'полночь' and not day_given:
                    day = now.date() + timedelta(days=1)
                    day_given = True
                cut(m)
            else:
                m = re.search(r'\b(утром|днем|вечером|ночью)\b', low)
                if m and day_given:
                    hour, minute = {'утром': 9, 'днем': 13, 'вечером': 19, 'ночью': 23}[m.group(1)], 0
                    cut(m)
    if hour is None:
        if day_given:
            hour, minute = 9, 0
        else:
            return None, low
    if not (0 <= hour <= 23 and 0 <= (minute or 0) <= 59):
        return None, low
    due = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute or 0)
    if due <= now and not day_given:
        due += timedelta(days=1)
    rest = low
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + ' ' + rest[b:]
    return due, rest


def clean_what(rest: str) -> str:
    """Что напомнить: убрать сам глагол и служебные слова."""
    s = ' ' + rest + ' '
    s = re.sub(r'\b(пожалуйста|плиз|cloud\s*hdr|клауд)\b', ' ', s)
    s = re.sub(r'\b(напомни|напомнить|напоминай|поставь|заведи|засеки|отсчитай|создай|сделай|разбуди|пикни)\w*\b', ' ', s)
    s = re.sub(r'\b(мне|меня|нам|напоминание|таймер|будильник|время|отсчет)\b', ' ', s)
    # «про встречу», «о созвоне» оставляем с предлогом — так фраза читается
    s = re.sub(r'^\s*(на\s+|о\s+том\s*,?\s*что|о\s+том\s*,?\s*чтобы|чтобы|что|насчет)\b', ' ', s.strip())
    s = re.sub(r'\s+', ' ', s).strip(' ,.!?-—:')
    return '' if s in ('на', 'о', 'об', 'про') else s


def stems(text: str) -> list[str]:
    return [w[:max(4, len(w) - 2)] for w in re.findall(r'[а-яёa-z0-9]+', text.lower()) if len(w) > 2]


def human_due(due: datetime, now: datetime) -> str:
    delta = (due - now).total_seconds()
    clock = due.strftime('%H:%M')
    if delta < 3600:
        mins = max(1, round(delta / 60))
        if delta < 90:
            secs = max(1, round(delta))
            return f'через {secs} {plural(secs, "секунду", "секунды", "секунд")}'
        return f'через {mins} {plural(mins, "минуту", "минуты", "минут")} ({clock})'
    if due.date() == now.date():
        return f'сегодня в {clock}'
    if due.date() == now.date() + timedelta(days=1):
        return f'завтра в {clock}'
    names = ['в понедельник', 'во вторник', 'в среду', 'в четверг', 'в пятницу', 'в субботу', 'в воскресенье']
    if (due.date() - now.date()).days < 7:
        return f'{names[due.weekday()]} в {clock}'
    return due.strftime('%d.%m в %H:%M')


AUMID = 'CloudHDR.App'


def register_toast_app(icon: Path) -> None:
    """Уведомления идут от имени «Cloud HDR» с нашим значком, а не от PowerShell."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\AppUserModelId\{AUMID}') as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'Cloud HDR')
            if icon.exists():
                winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon))
    except OSError:
        pass


def xml_escape(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;'))


def toast(title: str, body: str) -> None:
    xml = (f'<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>{xml_escape(title)}</text>'
           f'<text>{xml_escape(body)}</text></binding></visual>'
           '<actions><action content="Понятно" arguments="dismiss" activationType="system"/></actions>'
           '<audio src="ms-winsoundevent:Notification.Reminder"/></toast>')
    script = ('[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]\n'
              '[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]\n'
              '$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n'
              f"$x.LoadXml(@'\n{xml}\n'@)\n"
              f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{AUMID}')"
              '.Show([Windows.UI.Notifications.ToastNotification]::new($x))\n')
    threading.Thread(target=run_ps, args=(script, 20), daemon=True).start()


class Reminders:
    """Список напоминаний в файле; фоновый поток раз в секунду проверяет сроки."""

    def __init__(self, path: Path, log: Callable[[str], None]) -> None:
        self.path = path
        self.log = log
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self.fired: list[dict] = []   # сработавшие, но ещё не показанные окну
        try:
            self.items = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.items = []
        threading.Thread(target=self.loop, daemon=True).start()

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError as error:
            self.log(f'напоминания: не сохранить — {error}')

    def add(self, due: datetime, what: str, timer: bool) -> dict:
        item = {'id': uuid.uuid4().hex[:8], 'due': due.timestamp(), 'what': what, 'timer': timer,
                'created': time.time()}
        with self.lock:
            self.items.append(item)
            self.items.sort(key=lambda i: i['due'])
            self.save()
        self.log(f'напоминание {item["id"]}: {due:%d.%m %H:%M:%S} «{what}»')
        return item

    def cancel(self, needle: str = '') -> list[dict]:
        with self.lock:
            if needle:
                # «про врача» должно найти «встречу с врачом»: сравниваем основы слов
                want = stems(needle)
                gone = [i for i in self.items if all(any(w.startswith(s) or s.startswith(w[:len(s)]) for w in stems(i['what']))
                                                     for s in want)]
            else:
                gone = list(self.items)
            self.items = [i for i in self.items if i not in gone]
            self.save()
        return gone

    def drop(self, item_id: str) -> None:
        with self.lock:
            self.items = [i for i in self.items if i['id'] != item_id]
            self.save()

    def upcoming(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    def take_fired(self) -> list[dict]:
        with self.lock:
            out, self.fired = self.fired, []
        return out

    def loop(self) -> None:
        while True:
            now = time.time()
            due = []
            with self.lock:
                for item in self.items:
                    if item['due'] <= now:
                        due.append(item)
                if due:
                    self.items = [i for i in self.items if i not in due]
                    # Уведомление, пропущенное больше чем на сутки (ПК был выключен), всё
                    # равно показываем — но помечаем, что оно запоздало.
                    for item in due:
                        item['late'] = now - item['due'] > 120
                        self.fired.append(item)
                    self.fired = self.fired[-20:]
                    self.save()
            for item in due:
                if item.get('timer'):
                    title, body = 'Таймер', item['what'] or 'Время вышло.'
                else:
                    title, body = 'Напоминание', item['what'] or 'Вы просили напомнить.'
                if item.get('late'):
                    body += f' (было на {datetime.fromtimestamp(item["due"]):%d.%m %H:%M})'
                toast(title, body)
                self.log(f'напоминание {item["id"]} сработало')
            time.sleep(1)


REMIND_HINT = r'напомн|напомин|таймер|засеки|отсчитай|разбуди|будильник|пикни'
LIST_RE = r'(какие|мои|покажи|список|есть\s+ли)\s+.*(напоминани|таймер)|^напоминания$'
CANCEL_RE = r'(отмени|удали|убери|сотри|сними)\w*\s+(все\s+|мой\s+|мои\s+|это\s+)?(напоминани|таймер)'


def remind_answer(text: str, store: Reminders, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    low = text.lower().strip()
    if re.search(LIST_RE, low):
        items = store.upcoming()
        if not items:
            return {'ok': True, 'known': True, 'say': 'Напоминаний нет. Скажите, например: «напомни через 20 минут снять чайник».'}
        lines = [f'{human_due(datetime.fromtimestamp(i["due"]), now)} — {i["what"] or ("таймер" if i["timer"] else "без текста")}'
                 for i in items[:10]]
        return {'ok': True, 'known': True, 'say': f'{len(items)} {plural(len(items), "напоминание", "напоминания", "напоминаний")}:',
                'text': '\n'.join(lines), 'items': items}
    if re.search(CANCEL_RE, low):
        about = re.search(r'(?:про|о|об|насчет)\s+(.+)$', low)
        needle = about.group(1).strip() if about else ''
        if not needle and not re.search(r'\bвсе\b', low):
            items = store.upcoming()
            if len(items) > 1:
                # «отмени напоминание» при нескольких — снимаем ближайшее
                store.drop(items[0]['id'])
                return {'ok': True, 'known': True, 'say': f'Отменил ближайшее: «{items[0]["what"] or "таймер"}». Остальные на месте.'}
        gone = store.cancel(needle)
        if not gone:
            return {'ok': True, 'known': True, 'say': 'Отменять нечего — таких напоминаний нет.'}
        return {'ok': True, 'known': True, 'say': f'Отменил {len(gone)} {plural(len(gone), "напоминание", "напоминания", "напоминаний")}.'}
    if not re.search(REMIND_HINT, low):
        return {'ok': False, 'known': False}
    timer = bool(re.search(r'таймер|засеки|отсчитай|пикни', low))
    due, rest = parse_when(text, now)
    if due is None:
        # «напомни мой ник» — это память, а не будильник: уступаем
        if timer:
            return {'ok': True, 'known': True, 'say': 'На сколько поставить таймер? Например: «таймер на 5 минут».'}
        return {'ok': False, 'known': False}
    what = clean_what(rest)
    if not what and re.search(r'разбуди|будильник', low):
        what = 'Подъём!'
    if (due - now).total_seconds() > 366 * 86400:
        return {'ok': False, 'known': True, 'say': 'Так далеко вперёд я напоминания не ставлю — не больше года.'}
    store.add(due, what, timer)
    when = human_due(due, now)
    if timer and not what:
        say = f'Засёк — сработает {when}.'
    elif re.match(r'(про|о|об)\s', what):
        say = f'Напомню {when} {what}.'
    else:
        say = f'Напомню {when}' + (f': «{what}».' if what else '.')
    return {'ok': True, 'known': True, 'say': say + ' Уведомление придёт, даже если окно Cloud HDR будет закрыто.',
            'due': due.timestamp()}
