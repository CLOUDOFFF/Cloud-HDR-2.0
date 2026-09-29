"""
Cloud HDR — управление компьютером простыми словами (группа 5).

    настройки   «открой настройки звука», «где настройки мыши»      → нужная страница Windows
    звук        «звук на наушники», «выключи микрофон»              → устройство по умолчанию
    тишина      «не беспокоить на час», «включи уведомления»        → всплывающие уведомления
    режимы      «игровой режим», «запомни рабочий режим: …»         → цепочка обычных команд
    сеть        «какой у меня ip», «скорость интернета», «подключись к сети Дом»
    очистка     «почисти компьютер»                                  → временные файлы и кэш, по кнопке
    обновления  «проверь обновления windows», «обнови драйвер видеокарты»
    файлы       «сделай pdf из выделенных фото», «сожми картинки»  → рядом с исходниками
    печать      «распечатай выделенное»                             → по кнопке

match_control(text) узнаёт просьбу, control_answer(text) отвечает, run_confirmed()
выполняет то, что требует кнопки. Ничего не удаляется и не печатается без нажатия.
Проверка разбора: ..\\runtime\\python.exe -m tests.test_control (из папки python).
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
import winreg
from pathlib import Path

import psutil

NO_WINDOW = 0x08000000
ROOT = Path(__file__).resolve().parent
AUDIO_PS = ROOT.parent / 'server' / 'audio.ps1'
STATE_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Cloud HDR'
SCENES_FILE = STATE_DIR / 'scenes.json'
QUIET_FILE = STATE_DIR / 'quiet.json'


def _norm(text: str) -> str:
    low = str(text or '').lower().replace('ё', 'е').strip()
    low = re.sub(r'^(пожалуйста|плиз|слушай|эй|ну|а|и)\s+', '', low)
    low = re.sub(r'\s+(пожалуйста|плиз)$', '', low)
    return re.sub(r'\s+', ' ', low).strip(' .!?')


def _gb(n: float) -> str:
    if n >= 1024 ** 3:
        return f'{n / 1024 ** 3:.1f} ГБ'.replace('.', ',')
    if n >= 1024 ** 2:
        return f'{n / 1024 ** 2:.0f} МБ'
    return f'{max(n / 1024, 1):.0f} КБ'


def _run(args: list, timeout: int = 20, encoding: str = 'utf-8') -> str:
    # Консольные утилиты (netsh, ping) пишут то в UTF-8, то в cp866 — смотря
    # как настроена консоль. Сначала UTF-8 строго, не вышло — старая кодировка.
    try:
        done = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ''
    try:
        return done.stdout.decode('utf-8')
    except UnicodeDecodeError:
        return done.stdout.decode(encoding if encoding != 'utf-8' else 'cp866', 'replace')


def _ps(script: str, timeout: int = 30) -> str:
    tmp = Path(os.environ.get('TEMP', '.')) / f'cloudhdr-{uuid.uuid4().hex}.ps1'
    tmp.write_text('[Console]::OutputEncoding=[Text.Encoding]::UTF8\n' + script, encoding='utf-8-sig')
    try:
        return _run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(tmp)], timeout)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _open(target: str) -> None:
    os.startfile(target)  # noqa: S606 — только адреса ms-settings: и найденные файлы/программы


# ================================================================ настройки ==

# Раздел → страница. Ключи — корни слов, как их говорят люди; первый совпавший
# выигрывает, поэтому частные («звук микрофона») стоят раньше общих («звук»).
SETTINGS = [
    (r'микрофон', 'ms-settings:sound', 'звука (микрофоны внизу страницы)'),
    (r'звук|громкост|динамик|колонк|наушник|аудио', 'ms-settings:sound', 'звука'),
    (r'блютуз|bluetooth|устройств', 'ms-settings:bluetooth', 'Bluetooth и устройств'),
    (r'мыш|курсор|тачпад|сенсорн\w* панел', 'ms-settings:mousetouchpad', 'мыши'),
    (r'клавиатур|раскладк|язык ввода', 'ms-settings:typing', 'клавиатуры'),
    (r'язык', 'ms-settings:regionlanguage', 'языка'),
    (r'экран|дисплей|монитор|разрешени|масштаб', 'ms-settings:display', 'экрана'),
    (r'ночн\w* свет', 'ms-settings:nightlight', 'ночного света'),
    (r'обои|фон рабочего|персонализ|оформлени|тем[аыу]?\b', 'ms-settings:personalization', 'персонализации'),
    (r'цвет', 'ms-settings:colors', 'цветов'),
    (r'панел\w* задач', 'ms-settings:taskbar', 'панели задач'),
    (r'пуск', 'ms-settings:personalization-start', 'меню «Пуск»'),
    (r'уведомлени|не беспокоить', 'ms-settings:notifications', 'уведомлений'),
    (r'вай ?фай|wi-?fi|беспроводн', 'ms-settings:network-wifi', 'Wi-Fi'),
    (r'сет[ьи]|интернет|ethernet|vpn|впн|прокси', 'ms-settings:network-status', 'сети'),
    (r'питани|батаре|аккумулятор|спящ|энерги', 'ms-settings:powersleep', 'питания'),
    (r'хранилищ|памят\w* диска|место на диске|диск', 'ms-settings:storagesense', 'хранилища'),
    (r'обновлени|windows update|апдейт', 'ms-settings:windowsupdate', 'обновлений Windows'),
    (r'приложени|программ', 'ms-settings:appsfeatures', 'приложений'),
    (r'по умолчанию|браузер по умолчанию', 'ms-settings:defaultapps', 'приложений по умолчанию'),
    (r'автозагрузк|автозапуск', 'ms-settings:startupapps', 'автозагрузки'),
    (r'принтер|печат|сканер', 'ms-settings:printers', 'принтеров'),
    (r'камер', 'ms-settings:camera', 'камеры'),
    (r'конфиденциальн|приватн|разрешени', 'ms-settings:privacy', 'конфиденциальности'),
    (r'учетн|аккаунт|пароль|вход|пин', 'ms-settings:signinoptions', 'входа в систему'),
    (r'врем|дат|часов\w* пояс', 'ms-settings:dateandtime', 'даты и времени'),
    (r'игр|game ?bar|игров', 'ms-settings:gaming-gamebar', 'игр'),
    (r'специальн\w* возможност|экранн\w* лупа|диктор', 'ms-settings:easeofaccess', 'специальных возможностей'),
    (r'защит|безопасност|антивирус|defender', 'windowsdefender:', 'Безопасности Windows'),
    (r'активаци', 'ms-settings:activation', 'активации'),
    (r'о системе|характеристик|сведени\w* о (компьютере|системе)', 'ms-settings:about', 'сведений о системе'),
    (r'буфер', 'ms-settings:clipboard', 'буфера обмена'),
    (r'фокус', 'ms-settings:quiethours', 'фокусировки'),
]
SETTINGS_RE = r'^(?:открой|покажи|зайди в|перейди в|где|как открыть|мне нужны|нужны)?\s*(?:мне\s+)?(?:параметры|настройк\w*)\s+(?P<what>.+)$'


def match_settings(low: str):
    m = re.match(SETTINGS_RE, low)
    if not m:
        if re.fullmatch(r'(открой |покажи )?(настройки|параметры)( windows| виндовс| компьютера| системы| винды)', low):
            return ('ms-settings:', 'Windows')
        return None
    what = m.group('what')
    if re.fullmatch(r'(windows|виндовс|винды|компьютера|системы)', what):
        return ('ms-settings:', 'Windows')
    for pattern, uri, name in SETTINGS:
        if re.search(pattern, what):
            return (uri, name)
    return None


# ==================================================================== звук ===

DEVICE_WORDS = {
    'наушник': ('наушник', 'headphone', 'headset', 'гарнитур', 'airpods', 'buds', 'earphone'),
    'гарнитур': ('гарнитур', 'headset', 'наушник', 'headphone'),
    # «Realtek» — не признак колонок: у Realtek и цифровой выход (S/PDIF),
    # на котором обычно ничего не висит. Лучше спросить, чем оставить без звука.
    'колонк': ('speaker', 'динамик', 'колонк', 'speakers'),
    'динамик': ('speaker', 'динамик', 'колонк'),
    'монитор': ('nvidia', 'hdmi', 'display', 'монитор', 'amd high definition', 'intel(r) display'),
    'телевизор': ('hdmi', 'nvidia', 'tv', 'телевизор'),
    'hdmi': ('hdmi', 'nvidia'),
    'блютуз': ('bluetooth', 'hands-free', 'stereo', 'a2dp'),
}
AUDIO_SET_RE = (r'^(?:переключи|переведи|выведи|пусти|сделай|перекинь|включи)?\s*(?:весь\s+)?(?:звук|аудио|вывод звука|воспроизведение)'
                r'\s+(?:на|в|через)\s+(?P<dev>.+)$|^(?:включи|сделай)\s+звук\s+(?:на|в|через)\s+(?P<dev2>.+)$')
AUDIO_LIST_RE = r'(какие|список)\s+(\w+\s+)?(устройств\w*\s+звука|аудиоустройств|устройств\w* вывода)|куда\s+(идет|выводится)\s+звук|через что\s+(идет\s+)?звук'
MIC_RE = r'^(?P<act>выключи|отключи|заглуши|замьють|замьюти|мьют|включи|верни)\s+(?:мне\s+)?микрофон\w*$|^микрофон\s+(?P<act2>выкл\w*|вкл\w*)$'


def audio_devices() -> list[dict]:
    out = _run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(AUDIO_PS), 'list'], 30)
    rows = []
    for line in out.splitlines():
        parts = line.strip().split('|', 3)
        if len(parts) == 4:
            rows.append({'kind': parts[0], 'default': parts[1] == '1', 'id': parts[2], 'name': parts[3]})
    return rows


def pick_devices(query: str, devices: list[dict]) -> list[dict]:
    q = query.lower()
    q = re.sub(r'\b(мои|мой|моя|мое|свои|эти|это)\b', ' ', q).strip()
    outs = [d for d in devices if d['kind'] == 'out']
    words = set()
    for key, variants in DEVICE_WORDS.items():
        if key in q:
            words.update(variants)
    hits = [d for d in outs if any(w in d['name'].lower() for w in words)] if words else []
    # прямое имя: «на vg27a», «на realtek»
    tokens = [t for t in re.findall(r'[\w-]{3,}', q) if t not in ('звук', 'устройство')]
    direct = [d for d in outs if any(t in d['name'].lower() for t in tokens)]
    return direct or hits


def set_audio(device_id: str) -> bool:
    out = _run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(AUDIO_PS), 'set', device_id], 30)
    return 'ok' in out


def short_name(name: str) -> str:
    return re.sub(r'\s*\((?:NVIDIA|Realtek|AMD|Intel)[^)]*\)', '', name).strip() or name


def mic(action: str) -> str:
    return _run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(AUDIO_PS), 'mic', action], 30).strip()


# ============================================================ не беспокоить ==

QUIET_ON_RE = (r'^(?:включи\s+)?(?:режим\s+)?не\s+беспокоить(?P<for>.*)$|^(?:выключи|отключи|убери)\s+(?:все\s+)?(?:уведомлени\w*|оповещени\w*)(?P<for2>.*)$'
               r'|^без\s+уведомлений(?P<for3>.*)$')
QUIET_OFF_RE = (r'^(?:выключи|отключи|убери)\s+(?:режим\s+)?не\s+беспокоить$|^(?:включи|верни)\s+(?:все\s+)?(?:уведомлени\w*|оповещени\w*)$'
                r'|^можно\s+беспокоить$')
_TOAST_KEY = r'Software\Microsoft\Windows\CurrentVersion\PushNotifications'
_quiet_timer: threading.Timer | None = None


def _minutes(text: str) -> int | None:
    t = text.strip()
    if not t:
        return None
    if re.search(r'\bчас\w*', t) and not re.search(r'\d', t):
        return 60 * (2 if 'два' in t or 'пару' in t else 1)
    if re.search(r'полчас', t):
        return 30
    m = re.search(r'(\d+)\s*(час|ч\b|мин)', t)
    if m:
        return int(m.group(1)) * (60 if m.group(2).startswith('ч') else 1)
    return None


def set_quiet(on: bool, minutes: int | None = None) -> None:
    global _quiet_timer
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _TOAST_KEY) as key:
        winreg.SetValueEx(key, 'ToastEnabled', 0, winreg.REG_DWORD, 0 if on else 1)
    if _quiet_timer:
        _quiet_timer.cancel()
        _quiet_timer = None
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    until = time.time() + minutes * 60 if (on and minutes) else 0
    QUIET_FILE.write_text(json.dumps({'until': until}), encoding='utf-8')
    if until:
        _quiet_timer = threading.Timer(minutes * 60, lambda: set_quiet(False))
        _quiet_timer.daemon = True
        _quiet_timer.start()


def restore_quiet_on_start() -> None:
    """Служба перезапускалась посреди «не беспокоить на час» — довести дело до конца."""
    try:
        until = json.loads(QUIET_FILE.read_text(encoding='utf-8')).get('until') or 0
    except (OSError, ValueError):
        return
    if not until:
        return
    left = until - time.time()
    if left <= 0:
        set_quiet(False)
    else:
        set_quiet(True, max(1, int(left // 60)))


# ================================================================== режимы ===

SCENE_SAVE_RE = (r'^(?:запомни|создай|сохрани|сделай|добавь|настрой)\s+(?:мне\s+)?(?:режим|сценарий)\s+(?P<name>[\w -]{2,40}?)'
                 r'\s*(?::|—|-|это|это когда)\s*(?P<cmds>.+)$'
                 r'|^(?:запомни|сохрани|создай|сделай|добавь|настрой)\s*,?\s*(?:что\s+)?(?:мне\s+)?(?P<name2>[\w -]{2,40}?)\s+режим\s*(?::|—|-|это)\s*(?P<cmds2>.+)$')
SCENE_RUN_RE = (r'^(?:включи|запусти|активируй|давай|врубай|вруби|перейди в|переключись на)?\s*(?:режим|сценарий)\s+(?P<name>[\w -]{2,40})$'
                r'|^(?:включи|запусти|активируй|давай|вруби|перейди в)?\s*(?P<name2>[\w-]{3,20})\s+(?:режим|сценарий)$')
SCENE_OFF_RE = r'^(?:выключи|отключи|заверши|выйди из)\s+(?P<name>[\w-]{3,20})\s+режим\w*$|^(?:обычный|нормальный|стандартный)\s+режим$'
SCENE_LIST_RE = r'(какие|список|покажи)\s+(\w+\s+){0,2}(режим\w*|сценари\w*)$'
SCENE_DEL_RE = r'^(?:удали|забудь|убери)\s+(?:режим|сценарий)\s+(?P<name>[\w -]{2,40})$'

BUILTIN = {
    'игровой': 'закрыть лишнее в фоне не буду — покажу, что больше всего ест память; питание на максимум, уведомления выключены',
}
_SCENE_SPLIT = re.compile(r'\s*(?:,|;|\s+и\s+потом\s+|\s+потом\s+|\s+затем\s+|\s+после этого\s+|\s+а также\s+|\s+и\s+(?=(?:'
                          r'открой|запусти|включи|выключи|отключи|сделай|поставь|сверни|разверни|закрой|найди|переключи|тише|громче|'
                          r'звук|не беспокоить|напомни|перейди|покажи|пауза|продолжи|выведи|очисти|почисти|проверь|заблокируй|обнови)))\s*')


_VERB = re.compile(r'^(открой|запусти|включи|выключи|отключи|сделай|поставь|сверни|разверни|закрой|найди|переключи|тише|громче|'
                   r'звук|не беспокоить|напомни|перейди|покажи|режим|пауза|продолжи|следующ|предыдущ|выведи|перемести|очисти|'
                   r'почисти|проверь|сожми|заблокируй|скриншот|сделай|обнови|установи)')


def _with_verbs(parts: list[str]) -> list[str]:
    """«открой телеграм, хром» — у «хром» глагола нет, он тот же, что у соседа слева."""
    out, verb = [], ''
    for part in parts:
        m = _VERB.match(part)
        # «открой хром и вс код» — два окна; а «найди борщ и щи» — один запрос
        if m and re.match(r'(открой|запусти|закрой|сверни|разверни)\s', part) and ' и ' in part:
            verb = part.split()[0]
            first, *rest = part.split(' и ')
            out.append(first.strip())
            out.extend(f'{verb} {r.strip()}' for r in rest if r.strip())
            continue
        if m:
            verb = part.split()[0]
            out.append(part)
        elif verb:
            pieces = part.split(' и ') if verb in ('открой', 'запусти', 'закрой', 'сверни', 'разверни') else [part]
            out.extend(f'{verb} {p.strip()}' for p in pieces if p.strip())
        else:
            out.append(part)
    return out


def _scene_key(name: str) -> str:
    name = re.sub(r'\b(режим\w*|сценари\w*|мой|мою|мне)\b', ' ', name.lower().replace('ё', 'е'))
    name = re.sub(r'(ый|ий|ой|ая|ое|ую)$', '', re.sub(r'\s+', ' ', name).strip())
    return name


def load_scenes() -> dict:
    try:
        data = json.loads(SCENES_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_scenes(scenes: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SCENES_FILE.write_text(json.dumps(scenes, ensure_ascii=False, indent=1), encoding='utf-8')


def find_scene(name: str, scenes: dict) -> tuple[str, dict] | None:
    key = _scene_key(name)
    for title, scene in scenes.items():
        if _scene_key(title) == key:
            return title, scene
    return None


def heavy_apps(limit: int = 5) -> list[tuple[str, int]]:
    mine = {'cloudhdr', 'python', 'pythonw', 'llama-server', 'msedgewebview2', 'system', 'registry', 'memory compression',
            'explorer', 'dwm', 'svchost', 'csrss', 'lsass', 'services', 'wininit', 'winlogon', 'smss', 'audiodg', 'fontdrvhost'}
    by_name: dict[str, int] = {}
    for p in psutil.process_iter(['name', 'memory_info']):
        name = (p.info.get('name') or '').lower().removesuffix('.exe')
        if not name or name in mine or not p.info.get('memory_info'):
            continue
        by_name[name] = by_name.get(name, 0) + p.info['memory_info'].rss
    return sorted(by_name.items(), key=lambda kv: -kv[1])[:limit]


# ==================================================================== сеть ===

IP_RE = r'(какой|мой|узнай|покажи|скажи)\s+(\w+\s+){0,2}(ip|айпи|ай пи)\b|^(ip|айпи)( адрес)?$'
WIFI_NOW_RE = r'(к какой|какая|какой)\s+(\w+\s+)?(сет[иь]|вай ?фай|wi-?fi)\s*(я\s+)?(подключ|сейчас|$)|какой\s+(у меня\s+)?(вай ?фай|wi-?fi)$|сигнал\s+(вай ?фай|wi-?fi|сети)'
WIFI_LIST_RE = r'(какие|покажи|найди)\s+(\w+\s+)?(сети|вай ?фай\w*|wi-?fi)\s*(рядом|вокруг|есть|доступн\w*)?$|доступные\s+(сети|вай ?фай)'
WIFI_JOIN_RE = r'^(?:подключись|подключи(?:\s+меня)?|присоединись|переключись)\s+(?:к|на)\s+(?:сети|вай ?фаю?|wi-?fi)\s+(?P<ssid>.+)$'
SPEED_RE = r'(скорост\w*|спидтест|speed ?test)\s+(\w+\s+)?(интернет\w*|инета|сети|соединени\w*)|(проверь|замерь|измерь)\s+(\w+\s+)?(интернет|инет)|^спидтест$|(какой|быстрый ли)\s+(\w+\s+)?интернет$|пинг'


VIRTUAL_IF = re.compile(r'(?i)(tunnel|tun\b|tap|xray|v2ray|clash|happ|hiddify|wireguard|\bwg\d*|openvpn|vpn|outline|nekoray|singbox|sing-box|'
                        r'zapret|wsl|hyper-v|vethernet|virtual|virtualbox|vmware|loopback|bluetooth|teredo|isatap)')
LOCATION_BLOCK = re.compile(r'(?i)location permission|местоположени')


def _interfaces() -> list[tuple[str, str]]:
    """(имя, адрес) живых IPv4-интерфейсов, кроме петли и автоадресов 169.254."""
    stats = psutil.net_if_stats()
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(('127.', '169.254.')):
                out.append((name, a.address))
    return out


def local_ip() -> str:
    """Настоящий адрес в сети — у Wi-Fi или кабеля, а не у VPN и виртуальных адаптеров."""
    real = [(n, ip) for n, ip in _interfaces() if not VIRTUAL_IF.search(n)]
    preferred = [ip for _, ip in real if ip.startswith(('192.168.', '10.')) or re.match(r'172\.(1[6-9]|2\d|3[01])\.', ip)]
    return (preferred or [ip for _, ip in real] or [''])[0]


def vpn_name() -> str:
    """Имя включённого VPN/туннеля, если он есть: весь трафик идёт через него."""
    for name, _ in _interfaces():
        if re.search(r'(?i)(tunnel|tun\b|xray|v2ray|clash|happ|hiddify|wireguard|\bwg\d*|openvpn|vpn|outline|nekoray|sing)', name):
            return name
    return ''


def wifi_now() -> dict:
    """Сеть — из профиля подключения Windows: netsh wlan в Windows 11 без
    доступа к местоположению ничего не отдаёт, а профилю он не нужен."""
    raw = _ps("Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + $_.Name + '|' + $_.IPv4Connectivity }", 15)
    info = {}
    for line in raw.splitlines():
        alias, _, rest = line.partition('|')
        name, _, conn = rest.partition('|')
        if not alias or VIRTUAL_IF.search(alias):
            continue
        wireless = bool(re.search(r'(?i)wi-?fi|wireless|wlan|беспровод', alias))
        info.setdefault('wifi' if wireless else 'cable', name.strip())
    return info


def wifi_profiles() -> list[str]:
    raw = _run(['netsh', 'wlan', 'show', 'profiles'], 10, 'cp866')
    return [m.group(1).strip() for m in re.finditer(
        r'^\s*(?:All User Profile|Все профили пользователей|Профиль всех пользователей)\s*:\s*(.+)$', raw, re.M)]


def wifi_nearby() -> list[tuple[str, str]] | None:
    raw = _run(['netsh', 'wlan', 'show', 'networks', 'mode=bssid'], 15, 'cp866')
    if LOCATION_BLOCK.search(raw):
        return None
    nets, name = {}, None
    for line in raw.splitlines():
        m = re.match(r'\s*SSID\s+\d+\s*:\s*(.*)$', line)
        if m:
            name = m.group(1).strip() or '(скрытая сеть)'
            continue
        m = re.match(r'\s*(?:Signal|Сигнал)\s*:\s*(\d+)%', line)
        if m and name:
            nets[name] = max(int(m.group(1)), int(nets.get(name, '0').rstrip('%'))) if name in nets else int(m.group(1))
    return sorted(((n, f'{s}%') for n, s in nets.items()), key=lambda x: -int(x[1][:-1]))


def speed_test() -> str:
    # Задержка — настоящим ping (ICMP): TCP-соединение перехватывает локальный
    # прокси или VPN и отвечает за 0 мс, что неправда.
    raw = _run(['ping', '-n', '3', '-w', '2000', '1.1.1.1'], 15, 'cp866')
    pings = [int(v) for v in re.findall(r'(?:time|время)[=<](\d+)', raw)]
    if not pings and not re.search(r'TTL=', raw, re.I):
        started = time.perf_counter()
        try:
            urllib.request.urlopen('https://speed.cloudflare.com/__down?bytes=1', timeout=5).read()
            pings = [int((time.perf_counter() - started) * 1000)]
        except OSError:
            pass
    if not pings:
        return 'Интернета сейчас нет — до внешних серверов не достучаться.'
    size, got, started = 25_000_000, 0, time.perf_counter()
    try:
        request = urllib.request.Request(f'https://speed.cloudflare.com/__down?bytes={size}', headers={'User-Agent': 'CloudHDR'})
        with urllib.request.urlopen(request, timeout=20) as response:
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                got += len(chunk)
                if time.perf_counter() - started > 12:
                    break
    except OSError:
        pass
    seconds = max(time.perf_counter() - started, 0.001)
    mbit = got * 8 / seconds / 1_000_000
    verdict = ('очень быстро' if mbit > 200 else 'быстро' if mbit > 50 else 'нормально' if mbit > 15 else 'медленно')
    vpn = vpn_name()
    if vpn:
        # туннель отвечает на пинг сам — задержка «1 мс» была бы неправдой
        return f'Загрузка — {mbit:.0f} Мбит/с ({verdict}). Замер шёл через VPN ({vpn}), без него скорость может быть другой.'
    return f'Загрузка — {mbit:.0f} Мбит/с ({verdict}), задержка — {min(pings):.0f} мс.'


# ================================================================= очистка ===

CLEAN_RE = (r'(почисти|очисти|вычисти|освободи|убери)\s+(\w+\s+)?(компьютер|комп|пк|систем\w*|мусор|временн\w*|кэш|кеш|место|виндовс|windows)'
            r'|^(очистка|чистка)(\s+\w+)?$|удали\s+(\w+\s+)?(мусор|временн\w* файл\w*|кэш|кеш)')


def _clean_targets() -> list[Path]:
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    temp = Path(os.environ.get('TEMP', ''))
    targets = [temp, local / 'CrashDumps', local / 'Microsoft' / 'Windows' / 'INetCache', local / 'D3DSCache',
               local / 'NVIDIA' / 'DXCache', local / 'NVIDIA' / 'GLCache']
    for browser in (local / 'Google' / 'Chrome' / 'User Data', local / 'Microsoft' / 'Edge' / 'User Data',
                    local / 'Yandex' / 'YandexBrowser' / 'User Data'):
        if browser.exists():
            for profile in browser.iterdir():
                for sub in ('Cache', 'Code Cache', 'GPUCache'):
                    if (profile / sub).exists():
                        targets.append(profile / sub)
    return [t for t in targets if t.exists()]


def _old_files(folder: Path, min_age: float):
    now = time.time()
    for base, dirs, files in os.walk(folder):
        # своя папка Cloud HDR во временных — не трогаем
        dirs[:] = [d for d in dirs if not d.lower().startswith(('claude', 'cloudhdr', 'cloud hdr'))]
        for name in files:
            path = Path(base) / name
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime >= min_age:
                yield path, st.st_size


def clean_scan() -> tuple[int, int]:
    total = count = 0
    for folder in _clean_targets():
        for _, size in _old_files(folder, 24 * 3600):
            total += size
            count += 1
    return total, count


def clean_run() -> tuple[int, int]:
    freed = skipped = 0
    for folder in _clean_targets():
        for path, size in _old_files(folder, 24 * 3600):
            try:
                path.unlink()
                freed += size
            except OSError:
                skipped += 1           # занят программой — пусть лежит
    return freed, skipped


# =============================================================== обновления ==

WU_RE = r'(проверь|поищи|найди|есть ли|скачай|установи|запусти)\s+(\w+\s+)?обновлени\w*\s+(windows|виндовс|системы)|обнови\s+(windows|виндовс|систему)$|обновлени\w* (windows|виндовс)$'
DRIVER_RE = r'обнови\w*\s+(\w+\s+)?драйвер\w*|драйвер\w*\s+(на\s+)?(видеокарт\w*|nvidia|нвидиа)|(проверь|есть ли|новый|новые)\s+(\w+\s+)?драйвер'


def nvidia_driver() -> str:
    raw = _run(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'], 10).strip()
    return raw.splitlines()[0] if raw else ''


def open_nvidia_app() -> bool:
    for path in (r'C:\Program Files\NVIDIA Corporation\NVIDIA App\CEF\NVIDIA App.exe',
                 r'C:\Program Files\NVIDIA Corporation\NVIDIA GeForce Experience\NVIDIA GeForce Experience.exe'):
        if Path(path).exists():
            _open(path)
            return True
    return False


# ======================================================= файлы: pdf, сжатие ==

IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.heic'}
PDF_RE = r'(сделай|собери|сохрани|сконвертируй|конвертируй|переведи|преврати|объедини)\s+(\w+\s+){0,4}(в|из)?\s*(pdf|пдф)|(pdf|пдф)\s+из\s+'
CONVERT_RE = r'(сконвертируй|конвертируй|переведи|преврати|пересохрани|сохрани)\s+(\w+\s+){0,4}в\s+(?P<fmt>jpg|jpeg|джипег|png|пнг|webp|вебп)\b'
SHRINK_RE = r'(сожми|уменьши|облегчи|ужми)\s+(\w+\s+){0,3}(фото\w*|картин\w*|изображени\w*|снимк\w*|скриншот\w*)'
PRINT_RE = r'^(?:распечатай|напечатай|отправь на печать|печатай)\s*(?P<what>.*)$'


def explorer_selection() -> list[Path]:
    """Выделенное в открытых окнах Проводника — самое естественное «вот эти»."""
    out = _ps("$sh = New-Object -ComObject Shell.Application\n"
              "foreach ($w in $sh.Windows()) { try { if ($w.FullName -like '*explorer.exe') {"
              " foreach ($i in $w.Document.SelectedItems()) { $i.Path } } } catch {} }", 15)
    paths = []
    for line in out.splitlines():
        p = Path(line.strip())
        if line.strip() and p.exists() and p not in paths:
            paths.append(p)
    return paths


def _known_folder(low: str) -> Path | None:
    home = Path.home()
    for pattern, sub in ((r'загрузк', 'Downloads'), (r'рабоч\w* стол', 'Desktop'), (r'изображени|картинк\w* (?:папк|в папке)', 'Pictures'),
                         (r'документ', 'Documents'), (r'скриншот|снимк\w* экрана', 'Pictures/Screenshots')):
        if re.search(r'(из|в)\s+(папк\w+\s+)?' + pattern, low):
            folder = home / sub
            return folder if folder.exists() else None
    return None


def _sources(low: str, exts: set[str]) -> tuple[list[Path], str]:
    folder = _known_folder(low)
    if folder:
        files = sorted([p for p in folder.iterdir() if p.suffix.lower() in exts], key=lambda p: p.stat().st_mtime, reverse=True)[:200]
        return files, f'в папке «{folder.name}»'
    picked = [p for p in explorer_selection() if p.is_file() and p.suffix.lower() in exts]
    return picked, 'из выделенного в Проводнике'


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f'{path.stem} ({i}){path.suffix}')
        if not candidate.exists():
            return candidate
    return path.with_name(f'{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}')


def _load_image(path: Path):
    from PIL import Image, ImageOps
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    return image


def make_pdf(files: list[Path]) -> Path:
    pages = [_load_image(p).convert('RGB') for p in files]
    target = _unique(files[0].parent / (f'{files[0].stem}.pdf' if len(files) == 1 else 'Фото в PDF.pdf'))
    pages[0].save(target, 'PDF', save_all=True, append_images=pages[1:], resolution=150)
    return target


def convert_images(files: list[Path], fmt: str) -> list[Path]:
    ext = {'jpeg': 'jpg', 'джипег': 'jpg', 'пнг': 'png', 'вебп': 'webp'}.get(fmt, fmt)
    out = []
    for path in files:
        if path.suffix.lower().lstrip('.') == ext:
            continue
        image = _load_image(path)
        if ext == 'jpg':
            image = image.convert('RGB')
        target = _unique(path.with_suffix('.' + ext))
        image.save(target, quality=90) if ext in ('jpg', 'webp') else image.save(target)
        out.append(target)
    return out


def shrink_images(files: list[Path], longest: int = 1920) -> tuple[list[Path], int, int]:
    out, before, after = [], 0, 0
    for path in files:
        image = _load_image(path)
        image.thumbnail((longest, longest))
        target = _unique(path.with_name(f'{path.stem} (сжато).jpg'))
        image.convert('RGB').save(target, quality=82, optimize=True)
        before += path.stat().st_size
        after += target.stat().st_size
        out.append(target)
    return out, before, after


# =================================================================== разбор ===

def match_control(text: str) -> tuple[str, dict]:
    low = _norm(text)
    if not low:
        return '', {}
    if re.search(QUIET_OFF_RE, low):
        return 'quiet_off', {}
    m = re.search(QUIET_ON_RE, low)
    if m:
        rest = next((g for g in m.groups() if g), '') or ''
        return 'quiet_on', {'minutes': _minutes(rest)}
    m = re.search(SCENE_SAVE_RE, low)
    if m:
        name = (m.group('name') or m.group('name2') or '').strip()
        cmds = (m.group('cmds') or m.group('cmds2') or '').strip()
        return 'scene_save', {'name': name, 'commands': _with_verbs([c.strip() for c in _SCENE_SPLIT.split(cmds) if c and c.strip()])}
    if re.search(SCENE_LIST_RE, low):
        return 'scene_list', {}
    m = re.search(SCENE_DEL_RE, low)
    if m:
        return 'scene_delete', {'name': m.group('name')}
    m = re.search(SCENE_OFF_RE, low)
    if m:
        return 'scene_off', {'name': m.group('name') or 'игровой'}
    m = re.search(SCENE_RUN_RE, low)
    if m:
        name = (m.group('name') or m.group('name2') or '').strip()
        if not re.match(r'(питани|энергосбережени|экономи|производительн|сна|спящ|ночн|темн|светл|полноэкранн|самолет|полет|инкогнито|'
                        r'совместимост|разработчик|планшет|отладк|безопасн|чтени|прост|без звук|беззвучн)', name):
            return 'scene_run', {'name': name}
    if re.search(MIC_RE, low):
        m = re.search(MIC_RE, low)
        act = (m.group('act') or m.group('act2') or '')
        return 'mic', {'on': act.startswith(('включи', 'верни', 'вкл'))}
    m = re.search(AUDIO_SET_RE, low)
    if m:
        device = (m.group('dev') or m.group('dev2') or '').strip()
        # «звук на 50», «на максимум» — это громкость, её ведает агент
        if not re.match(r'(\d|процент|максимум|минимум|полную|половин|громч|тиш|ноль|сто(?!\w))', device):
            return 'audio_set', {'device': device}
    if re.search(AUDIO_LIST_RE, low):
        return 'audio_list', {}
    settings = match_settings(low)
    if settings:
        return 'settings', {'uri': settings[0], 'name': settings[1]}
    m = re.search(WIFI_JOIN_RE, low)
    if m:
        return 'wifi_join', {'ssid': m.group('ssid').strip()}
    if re.search(WIFI_LIST_RE, low):
        return 'wifi_list', {}
    if re.search(WIFI_NOW_RE, low):
        return 'wifi_now', {}
    if re.search(IP_RE, low):
        return 'ip', {'public': bool(re.search(r'внешн|публичн|белый|в интернете', low))}
    if re.search(SPEED_RE, low):
        return 'speed', {}
    if re.search(CLEAN_RE, low) and not re.search(r'корзин|буфер|историю|вирус|угроз|троян', low):
        return 'clean', {}
    if re.search(WU_RE, low):
        return 'wu', {}
    if re.search(DRIVER_RE, low):
        return 'driver', {}
    if re.search(PDF_RE, low):
        return 'pdf', {'low': low}
    m = re.search(CONVERT_RE, low)
    if m and re.search(r'фото|картин|изображени|снимк|скриншот|выделенн|эти|их|файл', low):
        return 'convert', {'fmt': m.group('fmt'), 'low': low}
    if re.search(SHRINK_RE, low):
        return 'shrink', {'low': low}
    m = re.match(PRINT_RE, low)
    if m and (not m.group('what') or re.search(r'выделенн|это|эти|файл|документ', m.group('what'))):
        return 'print', {}
    return '', {}


# ================================================================== ответы ===

def control_answer(text: str) -> dict:
    action, arg = match_control(text)
    if not action:
        return {'ok': False, 'known': False}

    if action == 'settings':
        _open(arg['uri'])
        return {'ok': True, 'known': True, 'say': f'Открыл настройки {arg["name"]}.'}

    if action in ('audio_set', 'audio_list'):
        devices = audio_devices()
        outs = [d for d in devices if d['kind'] == 'out']
        if not outs:
            return {'ok': False, 'known': True, 'say': 'Не вижу ни одного устройства вывода звука.'}
        current = next((short_name(d['name']) for d in outs if d['default']), '—')
        if action == 'audio_list':
            lines = '\n'.join(('● ' if d['default'] else '○ ') + short_name(d['name']) for d in outs)
            return {'ok': True, 'known': True, 'say': f'Звук сейчас идёт на {current}.', 'text': lines,
                    'choices': [{'label': short_name(d['name']), 'kind': 'audio_set', 'id': d['id']} for d in outs if not d['default']]}
        hits = pick_devices(arg['device'], devices)
        if len(hits) == 1:
            if hits[0]['default']:
                return {'ok': True, 'known': True, 'say': f'Звук уже идёт на {short_name(hits[0]["name"])}.'}
            ok = set_audio(hits[0]['id'])
            return {'ok': ok, 'known': True, 'say': f'Готово, звук на {short_name(hits[0]["name"])}.' if ok else 'Windows не дала переключить звук.'}
        if not hits and not any(key in arg['device'] for key in DEVICE_WORDS) and not re.search(r'устройств|друг|другое|другие', arg['device']):
            return {'ok': False, 'known': False}
        options = hits or [d for d in outs if not d['default']]
        head = 'Таких устройств несколько — на какое?' if hits else f'Устройства «{arg["device"]}» не вижу. Сейчас звук на {current}. Куда переключить?'
        return {'ok': True, 'known': True, 'say': head,
                'choices': [{'label': short_name(d['name']), 'kind': 'audio_set', 'id': d['id']} for d in options]}

    if action == 'mic':
        state = mic('on' if arg['on'] else 'off')
        if state == 'none':
            return {'ok': False, 'known': True, 'say': 'Микрофон не подключён — Windows не видит ни одного.'}
        return {'ok': True, 'known': True, 'say': 'Микрофон выключен.' if state == 'muted' else 'Микрофон включён.'}

    if action == 'quiet_on':
        set_quiet(True, arg['minutes'])
        minutes = arg['minutes']
        tail = (' — пока не скажете «включи уведомления»' if not minutes else
                f' на {minutes // 60} ч' if minutes % 60 == 0 else f' на {minutes} мин')
        return {'ok': True, 'known': True, 'say': f'Не беспокою: всплывающие уведомления выключены{tail}.'}
    if action == 'quiet_off':
        set_quiet(False)
        return {'ok': True, 'known': True, 'say': 'Уведомления снова включены.'}

    if action == 'scene_save':
        if not arg['commands']:
            return {'ok': False, 'known': True, 'say': 'А что в этом режиме делать? Например: «запомни рабочий режим: открой телеграм, хром и тише».'}
        scenes = load_scenes()
        old = find_scene(arg['name'], scenes)
        if old:
            scenes.pop(old[0])
        title = arg['name'].strip()
        scenes[title] = {'commands': arg['commands'], 'created': time.strftime('%Y-%m-%d')}
        save_scenes(scenes)
        steps = '\n'.join(f'{i}. {c}' for i, c in enumerate(arg['commands'], 1))
        return {'ok': True, 'known': True, 'say': f'Запомнил режим «{title}». Скажите «{title} режим» — сделаю по шагам:', 'text': steps}

    if action == 'scene_list':
        scenes = load_scenes()
        lines = [f'• {t}: ' + ', '.join(s.get('commands', [])) for t, s in scenes.items()]
        lines.append('• игровой (встроенный): питание на максимум, без уведомлений')
        return {'ok': True, 'known': True, 'say': 'Ваши режимы:', 'text': '\n'.join(lines)}

    if action == 'scene_delete':
        scenes = load_scenes()
        found = find_scene(arg['name'], scenes)
        if not found:
            return {'ok': False, 'known': True, 'say': f'Режима «{arg["name"]}» у меня нет.'}
        scenes.pop(found[0])
        save_scenes(scenes)
        return {'ok': True, 'known': True, 'say': f'Забыл режим «{found[0]}».'}

    if action == 'scene_run':
        scenes = load_scenes()
        found = find_scene(arg['name'], scenes)
        if found:
            title, scene = found
            return {'ok': True, 'known': True, 'say': f'Включаю режим «{title}».', 'run': scene.get('commands', [])}
        if _scene_key(arg['name']) == _scene_key('игровой'):
            subprocess.run(['powercfg', '/setactive', '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'], capture_output=True, creationflags=NO_WINDOW)
            set_quiet(True)
            heavy = heavy_apps()
            lines = '\n'.join(f'{n} — {_gb(r)}' for n, r in heavy)
            return {'ok': True, 'known': True, 'text': 'Больше всего памяти сейчас занимают:\n' + lines,
                    'say': 'Игровой режим: питание на максимум, уведомления выключены. '
                           'Если игра тормозит — закройте лишнее из списка («закрой …»). Выйти: «выключи игровой режим».'}
        if not re.search(r'(ый|ий|ой)$', arg['name']):
            return {'ok': False, 'known': False}
        return {'ok': False, 'known': True,
                'say': f'Режима «{arg["name"]}» ещё нет. Скажите, что в нём делать: «запомни {arg["name"]} режим: открой …, включи …».'}

    if action == 'scene_off':
        subprocess.run(['powercfg', '/setactive', '381b4222-f694-41f0-9685-ff5bb260df2e'], capture_output=True, creationflags=NO_WINDOW)
        set_quiet(False)
        return {'ok': True, 'known': True, 'say': 'Обычный режим: питание сбалансированное, уведомления включены.'}

    if action == 'ip':
        ip = local_ip()
        say = f'Адрес в домашней сети — {ip}.' if ip else 'Похоже, сети сейчас нет.'
        if vpn_name():
            say += f' Включён VPN ({vpn_name()}) — в интернет вы выходите через него.'
        if arg['public']:
            try:
                with urllib.request.urlopen('https://api.ipify.org', timeout=8) as r:
                    say += f' В интернете вас видят как {r.read().decode().strip()}.'
            except OSError:
                say += ' Внешний адрес узнать не вышло — нет интернета.'
        return {'ok': True, 'known': True, 'say': say}

    if action == 'wifi_now':
        info = wifi_now()
        vpn = f' Поверх неё включён VPN ({vpn_name()}).' if vpn_name() else ''
        if info.get('wifi'):
            return {'ok': True, 'known': True, 'say': f'Подключён к Wi-Fi «{info["wifi"]}».{vpn}'}
        if info.get('cable'):
            return {'ok': True, 'known': True, 'say': f'Подключён по кабелю, сеть «{info["cable"]}».{vpn}'}
        return {'ok': True, 'known': True, 'say': 'Сейчас компьютер не подключён ни к одной сети.'}

    if action == 'wifi_list':
        nets = wifi_nearby()
        if nets is None:
            return {'ok': False, 'known': True, 'open': 'ms-settings:privacy-location',
                    'say': 'Список сетей Windows 11 отдаёт программам только при включённом доступе к местоположению. '
                           'Открыл эту настройку — включите «Службы определения местоположения», и я покажу сети.'}
        if not nets:
            return {'ok': True, 'known': True, 'say': 'Сетей рядом не вижу (или Wi-Fi выключен).'}
        return {'ok': True, 'known': True, 'say': f'Вижу сетей: {len(nets)}.', 'text': '\n'.join(f'{n} — {s}' for n, s in nets[:12])}

    if action == 'wifi_join':
        want = arg['ssid'].lower()
        profiles = wifi_profiles()
        match = next((p for p in profiles if p.lower() == want), None) or next((p for p in profiles if want in p.lower()), None)
        if not match:
            return {'ok': False, 'known': True, 'say': f'К сети «{arg["ssid"]}» этот компьютер ещё не подключался — пароль ей нужно ввести один раз вручную. Открыл список сетей.',
                    'open': 'ms-availablenetworks:'}
        out = _run(['netsh', 'wlan', 'connect', f'name={match}'], 15, 'cp866')
        if LOCATION_BLOCK.search(out):
            return {'ok': False, 'known': True, 'open': 'ms-settings:privacy-location',
                    'say': 'Переключать Wi-Fi Windows 11 разрешает только при включённом доступе к местоположению — открыл эту настройку.'}
        ok = 'успешно' in out.lower() or 'successfully' in out.lower()
        return {'ok': ok, 'known': True, 'say': f'Подключаюсь к «{match}».' if ok else f'Не вышло подключиться к «{match}».'}

    if action == 'speed':
        return {'ok': True, 'known': True, 'say': speed_test()}

    if action == 'clean':
        total, count = clean_scan()
        if total < 5 * 1024 ** 2:
            return {'ok': True, 'known': True, 'say': 'Чисто: временных файлов почти нет. Корзину я не трогаю — «очисти корзину», если нужно.'}
        return {'ok': True, 'known': True, 'confirm': 'clean',
                'say': f'Нашёл {_gb(total)} мусора: временные файлы, кэш браузеров и видеокарты ({count} файлов, всё старше суток). '
                       'Документы и загрузки не трогаю. Удалить?'}

    if action == 'wu':
        _run(['UsoClient.exe', 'StartInteractiveScan'], 10)
        _open('ms-settings:windowsupdate')
        return {'ok': True, 'known': True, 'say': 'Запустил проверку обновлений Windows и открыл их страницу — там видно, что нашлось.'}

    if action == 'driver':
        now = nvidia_driver()
        opened = open_nvidia_app()
        if not opened:
            _open('https://www.nvidia.com/ru-ru/drivers/')
        head = f'Сейчас стоит драйвер {now.split(",")[-1].strip()} ({now.split(",")[0].strip()}). ' if now else ''
        return {'ok': True, 'known': True, 'say': head + ('Открыл NVIDIA App — новый драйвер ставится там, во вкладке «Драйверы».'
                                                          if opened else 'Открыл страницу драйверов NVIDIA.')}

    if action in ('pdf', 'convert', 'shrink'):
        files, where = _sources(arg['low'], IMG_EXT)
        if not files:
            return {'ok': False, 'known': True, 'say': 'Не вижу, какие картинки взять. Выделите их в Проводнике и скажите ещё раз '
                                                        '(или назовите папку: «из загрузок», «с рабочего стола»).'}
        if action == 'pdf':
            target = make_pdf(files)
            return {'ok': True, 'known': True, 'say': f'Собрал PDF из {len(files)} картинок ({where}): {target.name}.', 'reveal': str(target)}
        if action == 'convert':
            made = convert_images(files, arg['fmt'])
            if not made:
                return {'ok': True, 'known': True, 'say': 'Они уже в этом формате.'}
            return {'ok': True, 'known': True, 'say': f'Готово: {len(made)} шт. рядом с исходниками, оригиналы на месте.', 'reveal': str(made[0])}
        made, before, after = shrink_images(files)
        return {'ok': True, 'known': True, 'reveal': str(made[0]),
                'say': f'Сжал {len(made)} шт.: было {_gb(before)}, стало {_gb(after)}. Копии «(сжато)» рядом, оригиналы на месте.'}

    if action == 'print':
        files = [p for p in explorer_selection() if p.is_file()]
        if not files:
            return {'ok': False, 'known': True, 'say': 'Выделите в Проводнике, что распечатать, и скажите ещё раз.'}
        printer = _ps("(Get-CimInstance Win32_Printer | Where-Object Default).Name", 15).strip()
        return {'ok': True, 'known': True, 'confirm': 'print', 'paths': [str(p) for p in files[:20]],
                'say': f'Распечатать {len(files)} файл(ов) на «{printer or "принтер по умолчанию"}»?',
                'text': '\n'.join(p.name for p in files[:20])}

    return {'ok': False, 'known': False}


def run_confirmed(kind: str, body: dict) -> dict:
    if kind == 'audio_set':
        device_id = str(body.get('id') or '')
        if not re.fullmatch(r'\{[\d.]+\}\.\{[0-9a-fA-F-]{36}\}', device_id):
            return {'ok': False, 'say': 'Непонятное устройство.'}
        ok = set_audio(device_id)
        name = next((short_name(d['name']) for d in audio_devices() if d['id'] == device_id), 'выбранное устройство')
        return {'ok': ok, 'say': f'Готово, звук на {name}.' if ok else 'Windows не дала переключить звук.'}
    if kind == 'clean':
        freed, skipped = clean_run()
        tail = f' {skipped} файлов заняты программами — остались.' if skipped else ''
        return {'ok': True, 'say': f'Освободил {_gb(freed)}.{tail}'}
    if kind == 'print':
        paths = [Path(p) for p in body.get('paths') or [] if isinstance(p, str)]
        sent = 0
        for path in paths[:20]:
            if path.is_file():
                try:
                    os.startfile(str(path), 'print')  # noqa: S606
                    sent += 1
                except OSError:
                    pass
        return {'ok': sent > 0, 'say': f'Отправил на печать: {sent}.' if sent else 'Не получилось отправить на печать.'}
    return {'ok': False, 'say': 'Неизвестное действие.'}

