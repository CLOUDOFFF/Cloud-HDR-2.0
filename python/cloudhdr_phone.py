"""
Cloud HDR Phone — управление компьютером с телефона по QR-коду.

    python cloudhdr_phone.py          слушает 0.0.0.0:4481 (домашняя сеть)

Как это устроено. Окно Cloud HDR показывает QR-код со ссылкой вида
http://<адрес ПК>:4481/m#k=<ключ>. Телефон открывает её в браузере, запоминает
ключ и отправляет задачи. Окно приложения забирает их отсюда, выполняет тем же
путём, что и набранные руками, и возвращает ответ — телефон его показывает.

Безопасность — единственная причина, по которой это отдельная служба:
  • в сеть смотрит только она; службы умений (4480) и агент (4477) остаются
    доступны лишь этому компьютеру;
  • всё, что делает телефон, требует ключ из QR-кода (18 случайных байт);
    ключ сбрасывается одной кнопкой, и старые телефоны сразу теряют доступ;
  • маршруты для окна приложения (/desk/*, /pair) отвечают только запросам
    с этого же компьютера.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from cloudhdr_safety import caller_allowed

PORT = int(os.environ.get('CLOUDHDR_PHONE_PORT', '4481'))
DATA = Path(os.environ.get('LOCALAPPDATA', str(Path(__file__).parent))) / 'Cloud HDR'
DATA.mkdir(parents=True, exist_ok=True)
KEY_FILE = DATA / 'phone.json'
LOCAL = ('127.0.0.1', '::1')


class Utf8Json(JSONResponse):
    media_type = 'application/json; charset=utf-8'


app = FastAPI(title='Cloud HDR Phone', docs_url=None, redoc_url=None, openapi_url=None,
              default_response_class=Utf8Json)


def load_key() -> str:
    try:
        key = json.loads(KEY_FILE.read_text(encoding='utf-8')).get('key')
        if key:
            return key
    except (OSError, ValueError):
        pass
    key = secrets.token_urlsafe(18)
    KEY_FILE.write_text(json.dumps({'key': key}), encoding='utf-8')
    return key


KEY = {'value': load_key()}


class Bridge:
    """Очередь «телефон → окно приложения → ответ телефону»."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tasks: list[dict] = []
        self.desk_seen = 0.0
        self.phone_seen = 0.0

    def add(self, text: str) -> dict:
        task = {'id': uuid.uuid4().hex[:10], 'text': text, 'ts': time.time(), 'status': 'new', 'reply': ''}
        with self.lock:
            self.tasks.append(task)
            self.tasks = self.tasks[-40:]
        return dict(task)

    def take(self) -> list[dict]:
        with self.lock:
            fresh = [t for t in self.tasks if t['status'] == 'new']
            for task in fresh:
                task['status'] = 'taken'
            return [dict(t) for t in fresh]

    def reply(self, task_id: str, text: str, done: bool) -> bool:
        with self.lock:
            for task in self.tasks:
                if task['id'] == task_id:
                    task['reply'] = text
                    if done:
                        task['status'] = 'done'
                    return True
        return False

    def feed(self) -> list[dict]:
        with self.lock:
            now = time.time()
            # Окно приложения закрыто дольше 10 секунд — честно говорим об этом,
            # а не оставляем «выполняю…» висеть бесконечно.
            if now - self.desk_seen > 10:
                for task in self.tasks:
                    if task['status'] == 'new' and now - task['ts'] > 10:
                        task['status'] = 'done'
                        task['reply'] = 'Окно Cloud HDR на компьютере закрыто. Откройте его — и повторите задачу.'
            return [dict(t) for t in self.tasks[-30:]]


BRIDGE = Bridge()


def local_only(request: Request) -> None:
    if not request.client or request.client.host not in LOCAL:
        raise HTTPException(403, 'Только с этого компьютера')


def check_key(request: Request) -> None:
    key = request.headers.get('x-phone-key') or ''
    if not key or not secrets.compare_digest(key, KEY['value']):
        raise HTTPException(401, 'Телефон не сопряжён')
    BRIDGE.phone_seen = time.time()


SKIP_IFACES = ('tun', 'tap', 'vpn', 'virtual', 'vethernet', 'loopback', 'happ', 'wireguard', 'hyper-v', 'vmware', 'virtualbox')


def lan_ip() -> str:
    """
    Адрес компьютера в домашней сети — тот, по которому его найдёт телефон.

    Не «адрес маршрута по умолчанию»: при включённом VPN им оказывается
    туннель (172.18.0.1 у Happ), и телефон по такому адресу ПК не увидит.
    Берём настоящий Wi-Fi или Ethernet: сначала 192.168.*, потом 10.*, 172.16-31.*.
    """
    import psutil
    stats = psutil.net_if_stats()
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        if any(word in name.lower() for word in SKIP_IFACES) or not stats.get(name, None) or not stats[name].isup:
            continue
        for address in addresses:
            ip = address.address
            if address.family == socket.AF_INET and not ip.startswith(('127.', '169.254.')):
                rank = 0 if ip.startswith('192.168.') else 1 if ip.startswith('10.') else 2
                found.append((rank, ip))
    if found:
        return sorted(found)[0][1]
    return socket.gethostbyname(socket.gethostname())


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return image.to_string(encoding='unicode')


@app.middleware('http')
async def headers(request: Request, call_next):
    # Окно приложения (127.0.0.1:4477) — другой источник, ему нужен CORS.
    # Телефону не нужен: его страница пришла с этой же службы. Раньше «свой»
    # определялся только по адресу клиента, а браузер на этом же компьютере
    # тоже приходит с 127.0.0.1 — и любой открытый в нём сайт забирал /pair
    # вместе с ключом. Теперь с этого компьютера пускаем только окно
    # приложения и программы без браузера, из сети — только страницу телефона.
    local = bool(request.client and request.client.host in LOCAL)
    if local:
        allowed, origin = caller_allowed(request.headers, PORT)
    else:
        origin = request.headers.get('origin')
        allowed = not origin or origin.lower() == f"http://{(request.headers.get('host') or '').lower()}"
        origin = None
    if not allowed:
        return Utf8Json({'ok': False, 'error': 'Запрос не со страницы Cloud HDR'}, status_code=403)
    response = Response(status_code=204) if request.method == 'OPTIONS' else await call_next(request)
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Cache-Control'] = 'no-store'
    return response


# ------------------------------------------------------- для окна приложения --

# Правило брандмауэра Windows, без которого телефон до службы не достучится.
# Раньше его на этом компьютере создавали руками, а в коде его не было вовсе:
# на чистой установке QR-код открывался на телефоне пустой страницей, и никто
# не говорил почему. Читать правила можно без прав, создавать — только с ними.
FIREWALL_RULE = 'Cloud HDR - телефон'
FIREWALL_CACHE = {'ok': None, 'ts': 0.0}
NO_WINDOW = 0x08000000


def _powershell(command: str, timeout: int = 15) -> str:
    import subprocess
    full = '[Console]::OutputEncoding=[Text.Encoding]::UTF8;' + command
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', full],
                              capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.stdout.decode('utf-8', 'replace').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def firewall_ok() -> bool | None:
    """Есть ли разрешающее правило для порта. None — узнать не удалось."""
    now = time.time()
    if FIREWALL_CACHE['ok'] is not None and now - FIREWALL_CACHE['ts'] < 20:
        return FIREWALL_CACHE['ok']
    out = _powershell(
        f"$r = Get-NetFirewallPortFilter -Protocol TCP -ErrorAction SilentlyContinue | Where-Object {{ $_.LocalPort -eq '{PORT}' }} | "
        "Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' }; "
        "if ($r) { 'yes' } else { 'no' }")
    FIREWALL_CACHE['ok'] = True if out.endswith('yes') else False if out.endswith('no') else None
    FIREWALL_CACHE['ts'] = now
    return FIREWALL_CACHE['ok']


def pair_info() -> dict:
    url = f"http://{lan_ip()}:{PORT}/m#k={KEY['value']}"
    now = time.time()
    return {'ok': True, 'url': url, 'address': f'{lan_ip()}:{PORT}', 'svg': qr_svg(url),
            'phone': now - BRIDGE.phone_seen < 8, 'firewall': firewall_ok()}


@app.post('/pair/firewall')
def pair_firewall(request: Request):
    """Создать правило: Windows спросит разрешение администратора (UAC)."""
    local_only(request)
    rule = (f"New-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -Direction Inbound -Protocol TCP -LocalPort {PORT} "
            "-RemoteAddress LocalSubnet -Action Allow -Profile Any")
    # -Wait: ответ уходит, когда человек уже ответил на запрос UAC, и карточка
    # сразу показывает итог. Отказ в UAC — просто правило не появится.
    _powershell(f'Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '
                f"'-NoProfile','-Command',\"{rule}\"", timeout=120)
    FIREWALL_CACHE['ok'] = None
    return {'ok': True, 'firewall': firewall_ok()}


@app.get('/phone/health')
def health():
    return {'ok': True, 'name': 'Cloud HDR Phone'}


@app.get('/pair')
def pair(request: Request):
    local_only(request)
    return pair_info()


@app.post('/pair/reset')
def pair_reset(request: Request):
    local_only(request)
    KEY['value'] = secrets.token_urlsafe(18)
    KEY_FILE.write_text(json.dumps({'key': KEY['value']}), encoding='utf-8')
    BRIDGE.phone_seen = 0
    return pair_info()


@app.get('/desk/next')
def desk_next(request: Request):
    local_only(request)
    BRIDGE.desk_seen = time.time()
    return {'ok': True, 'tasks': BRIDGE.take(), 'phone': time.time() - BRIDGE.phone_seen < 8}


@app.post('/desk/reply')
async def desk_reply(request: Request):
    local_only(request)
    body = await request.json()
    return {'ok': BRIDGE.reply(str(body.get('id')), str(body.get('text') or ''), bool(body.get('done', True)))}


# ------------------------------------------------------------- для телефона --

@app.post('/api/task')
async def phone_task(request: Request):
    check_key(request)
    body = await request.json()
    text = str(body.get('text') or '').strip()[:500]
    if not text:
        return {'ok': False, 'error': 'Пустая задача'}
    return {'ok': True, 'task': BRIDGE.add(text)}


@app.get('/api/feed')
def phone_feed(request: Request):
    check_key(request)
    return {'ok': True, 'tasks': BRIDGE.feed(), 'desk': time.time() - BRIDGE.desk_seen < 8,
            'host': socket.gethostname()}


@app.get('/api/screen')
def phone_screen(request: Request):
    check_key(request)
    from PIL import ImageGrab
    shot = ImageGrab.grab()
    shot.thumbnail((1080, 1080))
    buffer = io.BytesIO()
    shot.convert('RGB').save(buffer, 'JPEG', quality=65)
    return Response(buffer.getvalue(), media_type='image/jpeg')


PHONE_PAGE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#F3DFC1"><title>Cloud HDR — мой ПК</title>
<style>
:root{--bg:#F3DFC1;--paper:#FFF7EA;--paper2:#F9EAD3;--line:#DDBE93;--ink:#3A2412;--ink2:#6E4B2C;--or:#EE7B24;--on:#2A1608;--em:#A9440F}
@media (prefers-color-scheme:dark){:root{--bg:#1F140B;--paper:#2B1C10;--paper2:#342214;--line:#5B3D21;--ink:#F7E6CE;--ink2:#CFAE88;--or:#F58A36;--em:#FFB578}}
*{box-sizing:border-box;margin:0}html,body{height:100%}
body{background:linear-gradient(var(--paper2),var(--bg) 55%);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;display:flex;flex-direction:column;gap:12px;padding:max(16px,env(safe-area-inset-top)) 16px max(14px,env(safe-area-inset-bottom))}
header{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px}
header svg{width:36px;height:23px;fill:var(--or)}
header i{margin-left:auto;width:10px;height:10px;border-radius:50%;background:var(--line);transition:.3s}
header i.on{background:var(--or);box-shadow:0 0 0 5px color-mix(in srgb,var(--or) 22%,transparent)}
.sub{font-size:13px;color:var(--ink2);margin:-6px 0 0 46px}
.mirror{border-radius:20px;overflow:hidden;background:var(--paper);box-shadow:inset 0 0 0 1.5px var(--line)}
.mirror img{display:block;width:100%;height:auto}
.mirror button{width:100%;padding:12px;border:0;background:none;color:var(--ink2);font:inherit;font-size:14px}
.feed{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding:4px 0}
.b{max-width:86%;padding:10px 14px;border-radius:20px;white-space:pre-wrap;overflow-wrap:anywhere}
.me{align-self:flex-end;background:var(--or);color:var(--on);border-bottom-right-radius:6px}
.ai{align-self:flex-start;background:var(--paper);box-shadow:inset 0 0 0 1.5px var(--line);border-bottom-left-radius:6px}
.ai.wait{color:var(--ink2)}
.chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none}
.chips button{flex:none;padding:8px 14px;border-radius:99px;border:0;background:var(--paper);box-shadow:inset 0 0 0 1.5px var(--line);color:var(--ink);font:inherit;font-size:14px}
form{display:flex;gap:8px;padding:6px 6px 6px 18px;border-radius:99px;background:var(--paper);box-shadow:inset 0 0 0 1.5px var(--line)}
input{flex:1;min-width:0;border:0;background:none;color:var(--ink);font:inherit;outline:none}
form button{width:44px;height:44px;border-radius:50%;border:0;background:var(--or);color:var(--on);font-size:20px;font-weight:700}
.err{padding:16px;border-radius:18px;background:var(--paper);box-shadow:inset 0 0 0 1.5px var(--em);color:var(--em)}
</style></head><body>
<header><svg viewBox="4 6 120 76"><path d="M34 72H88A20 20 0 0 0 90 32A28 28 0 0 0 38 36A18 18 0 0 0 34 72Z"/></svg><span id="host">Мой ПК</span><i id="dot"></i></header>
<p class="sub" id="state">подключаюсь…</p>
<div class="mirror" id="mirror"><button id="shot" type="button">Показать экран компьютера</button></div>
<div class="feed" id="feed"></div>
<div class="chips"><button>Разбери загрузки</button><button>Проверь компьютер</button><button>Сделай скриншот</button><button>Что ты умеешь?</button></div>
<form id="f"><input id="q" placeholder="Задача для ПК…" autocomplete="off" enterkeyhint="send"><button aria-label="Отправить">↑</button></form>
<script>
(()=>{
const m=location.hash.match(/k=([\w-]+)/);let key=m?m[1]:'';
try{if(key)localStorage.setItem('cloudhdrKey',key);else key=localStorage.getItem('cloudhdrKey')||''}catch(e){}
if(m)history.replaceState(null,'',location.pathname);
const $=id=>document.getElementById(id),feed=$('feed');
const api=(p,o={})=>fetch(p,{...o,headers:{'Content-Type':'application/json','X-Phone-Key':key}}).then(r=>{if(r.status===401)throw new Error('pair');return r.json()});
function fail(t){if(!document.querySelector('.err'))feed.insertAdjacentHTML('beforebegin','<p class="err">'+t+'</p>')}
function render(tasks){feed.textContent='';for(const t of tasks){const a=document.createElement('div');a.className='b me';a.textContent=t.text;feed.appendChild(a);
const b=document.createElement('div');b.className='b ai'+(t.reply?'':' wait');b.textContent=t.reply||'выполняю…';feed.appendChild(b)}feed.scrollTop=feed.scrollHeight}
let last='';
async function poll(){try{const d=await api('/api/feed');$('dot').className=d.desk?'on':'';$('host').textContent=d.host||'Мой ПК';
$('state').textContent=d.desk?'компьютер на связи':'окно Cloud HDR на компьютере закрыто';
const sig=JSON.stringify(d.tasks);if(sig!==last){last=sig;render(d.tasks)}}
catch(e){$('dot').className='';if(e.message==='pair'){$('state').textContent='';fail('Телефон не сопряжён. Откройте Cloud HDR на компьютере → «Управление с телефона» и отсканируйте QR-код ещё раз.');return}
$('state').textContent='нет связи — телефон и компьютер в одной сети Wi-Fi?'}
setTimeout(poll,1500)}
async function send(text){text=text.trim();if(!text)return;$('q').value='';try{await api('/api/task',{method:'POST',body:JSON.stringify({text})});last='';poll()}catch(e){}}
$('f').onsubmit=e=>{e.preventDefault();send($('q').value)};
document.querySelectorAll('.chips button').forEach(b=>b.onclick=()=>send(b.textContent));
async function screen(){$('mirror').querySelector('button').textContent='снимаю экран…';try{const r=await fetch('/api/screen',{headers:{'X-Phone-Key':key}});if(!r.ok)throw 0;
const url=URL.createObjectURL(await r.blob());$('mirror').innerHTML='<img alt="Экран компьютера"><button type="button">Обновить снимок</button>';
$('mirror').querySelector('img').src=url;$('mirror').querySelector('button').onclick=screen}catch(e){$('mirror').querySelector('button').textContent='не вышло — ещё раз'}}
$('shot').onclick=screen;
if(!key){$('state').textContent='';fail('Откройте эту страницу, отсканировав QR-код в Cloud HDR на компьютере.')}else poll();
})();
</script></body></html>"""


@app.get('/m', response_class=HTMLResponse)
def phone_page():
    return PHONE_PAGE


ICON = Path(__file__).resolve().parent.parent / 'public' / 'icons' / 'icon-192.png'


@app.get('/favicon.ico')
@app.get('/icon.png')
def icon():
    # иконка вкладки и ярлыка «на главный экран» телефона
    if ICON.exists():
        return Response(ICON.read_bytes(), media_type='image/png', headers={'Cache-Control': 'max-age=86400'})
    return Response(status_code=404)


if __name__ == '__main__':
    import uvicorn
    if sys.stdout is None or sys.stderr is None:
        sink = open(DATA / 'phone-console.log', 'a', encoding='utf-8', buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    print(f'Cloud HDR Phone: http://{lan_ip()}:{PORT}/m', flush=True)
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning', access_log=False)
