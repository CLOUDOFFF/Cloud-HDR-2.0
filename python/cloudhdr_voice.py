"""
Cloud HDR Voice — запись своего голоса и его клон для озвучки ответов.

    voice-env\\Scripts\\python.exe cloudhdr_voice.py      127.0.0.1:4482

Работает в отдельном окружении python\\voice-env: модели XTTS нужна старая
ветка transformers (4.57), а модель разговора живёт на 5.x. Окружение берёт
torch из общего runtime, своё у него только то, что отличается.

Маршруты:
  GET  /record          окно записи: текст-суфлёр, запись, прослушивание клона
  POST /voice/sample    WAV с записью → чистка → голосовой профиль → пример
  GET  /voice/status    готов ли голос
  GET  /voice/preview   пример фразы клонированным голосом
  POST /voice/tts       {"text": "..."} → WAV моим голосом (для озвучки ответов)

Где лежит: python\\voices\\me\\ — исходная запись, очищенный образец,
профиль (latents.pt) и ready.json — знак, что голос готов.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloudhdr_gpu import make_room  # noqa: E402
from cloudhdr_safety import caller_allowed  # noqa: E402
from cloudhdr_saytext import say, words_of  # noqa: E402

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / 'models' / 'xtts-v2'
VOICE_DIR = ROOT / 'voices' / 'me'
VOICE_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get('CLOUDHDR_VOICE_PORT', '4482'))
SR = 24000                       # частота XTTS
os.environ.setdefault('COQUI_TOS_AGREED', '1')


class Utf8Json(JSONResponse):
    media_type = 'application/json; charset=utf-8'


app = FastAPI(title='Cloud HDR Voice', docs_url=None, redoc_url=None, openapi_url=None,
              default_response_class=Utf8Json)


@app.middleware('http')
async def cors(request: Request, call_next):
    # Только страница приложения, своё окно записи и программы без браузера —
    # не любой сайт: чужая страница иначе говорила бы голосом хозяина.
    allowed, origin = caller_allowed(request.headers, PORT)
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


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with (VOICE_DIR / 'voice.log').open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


# ------------------------------------------------------------------ модель --

class Voice:
    def __init__(self) -> None:
        self.model = None
        self.device = 'cpu'
        self.latents = None
        self.lock = threading.Lock()
        self.state = 'idle'       # idle | cloning | ready | error
        self.message = ''

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        import TTS.tts.models.xtts as xtts_module
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        # Чтение файла — через soundfile, а не torchaudio.load: свежий torchaudio
        # читает звук через torchcodec, а тому нужны библиотеки FFmpeg, которых
        # на машине нет. Пересэмплирование в XTTS обходится без них.
        def load_audio(path, sampling_rate):
            import soundfile
            data, rate = soundfile.read(str(path), dtype='float32', always_2d=True)
            audio = torch.from_numpy(data.mean(axis=1)).unsqueeze(0)
            if rate != sampling_rate:
                import torchaudio
                audio = torchaudio.functional.resample(audio, rate, sampling_rate)
            return audio.clip_(-1, 1)
        xtts_module.load_audio = load_audio

        # Видеокарта — только если на ней не идёт обучение модели разговора и
        # есть запас памяти. Проверка по одной лишь свободной памяти пропустила
        # XTTS на карту посреди обучения: память там освобождается и занимается
        # рывками, и мгновенный замер врёт.
        self.device = 'cpu'
        training = any(('cloudhdr_ai' in ' '.join(p.info.get('cmdline') or []) or
                        'cloudhdr_qwen' in ' '.join(p.info.get('cmdline') or [])) and
                       'train' in ' '.join(p.info.get('cmdline') or [])
                       for p in __import__('psutil').process_iter(['cmdline']))
        if torch.cuda.is_available() and not training:
            # на карту — только на время речи (очередь видеопамяти, wake/park)
            self.device = 'cuda'
        config = XttsConfig()
        config.load_json(str(MODEL_DIR / 'config.json'))
        model = Xtts.init_from_config(config)
        model.load_checkpoint(config, checkpoint_dir=str(MODEL_DIR), use_deepspeed=False)
        # eval() у XTTS возвращает None, поэтому не в цепочке. Грузится в ОЗУ:
        # на видеокарту голос заезжает только на время речи (wake).
        model.eval()
        self.model = model
        self.parked = True
        log(f'XTTS загружен (ждёт в ОЗУ, говорит на {self.device})')

    def load_profile(self) -> bool:
        path = VOICE_DIR / 'latents.pt'
        if not path.exists():
            return False
        import torch
        data = torch.load(path, map_location='cpu')
        self.latents = (data['gpt_cond_latent'], data['speaker_embedding'])
        self.state = 'ready'
        return True

    def clone(self, sample: Path) -> None:
        import torch
        with self.lock:
            self.load()
            gpt, spk = self.model.get_conditioning_latents(
                audio_path=[str(sample)], gpt_cond_len=30, gpt_cond_chunk_len=6, max_ref_length=60)
            torch.save({'gpt_cond_latent': gpt.cpu(), 'speaker_embedding': spk.cpu()}, VOICE_DIR / 'latents.pt')
            self.latents = (gpt.cpu(), spk.cpu())

    # --- очередь видеопамяти (cloudhdr_gpu.py) ------------------------------
    # Голос звучит в ответ на реплику и потом молчит. Вернуть веса на карту —
    # доли секунды, а 2 ГБ видеопамяти нужнее модели разговора и распознаванию.
    # Поэтому на карте он только пока говорит, и ещё десять секунд — вдруг
    # следом ещё одна фраза.
    IDLE_SECONDS = 10
    last_used = 0.0
    parked = False            # веса отъехали в оперативную память
    yield_requested = False   # соседу нужна карта (очередь видеопамяти)

    def wake(self) -> None:
        if self.parked and self.model is not None and self.device == 'cuda':
            make_room(2.4, 'tts')
            self.model.to(self.device)
            self.parked = False
            log('голос вернулся на видеокарту')
        self.last_used = time.time()

    def sleep_if_idle(self) -> None:
        if self.model is None or self.device != 'cuda' or self.parked:
            return
        if time.time() - self.last_used < self.IDLE_SECONDS or not self.lock.acquire(blocking=False):
            return
        try:
            import torch
            self.model.to('cpu')
            self.parked = True
            torch.cuda.empty_cache()
            log('простой — видеокарта освобождена')
        finally:
            self.lock.release()

    # Сверка слухом: каждую фразу слушает Whisper хаба. Сказано не то (слово
    # скомкано, в конце бормотание) — новый дубль, до трёх; звучит лучший.
    GOOD = 0.82
    TAKES = 3
    last_score = 1.0          # худшая фраза последней озвучки — для кэша

    def speak(self, text: str, temperature: float = 0.62) -> np.ndarray:
        """temperature 0.62 вместо прежних 0.72: при высокой XTTS чаще
        комкает слова и дописывает хвосты после конца фразы."""
        with self.lock:
            self.load()
            self.wake()
            if self.latents is None and not self.load_profile():
                raise RuntimeError('Голос ещё не записан')
            gpt, spk = (t.to(self.device) for t in self.latents)
            parts = []
            self.last_score = 1.0
            for sentence in split_sentences(say(text)):
                parts.append(self.sentence(sentence, gpt, spk, temperature))
                parts.append(np.zeros(int(SR * 0.12), dtype=np.float32))   # вдох между фразами
            if self.device == 'cuda':
                import torch
                torch.cuda.empty_cache()    # промежуточные буферы — драйверу, а не «про запас»
            return np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)

    def sentence(self, sentence: str, gpt, spk, temperature: float) -> np.ndarray:
        import torch
        best, best_score = None, -1.0
        for take in range(self.TAKES):
            if take:
                torch.manual_seed(7919 * take + int(time.time()) % 1000)
            out = self.model.inference(sentence, 'ru', gpt, spk,
                                       temperature=temperature if take == 0 else 0.5,
                                       repetition_penalty=5.0, top_p=0.85, top_k=50,
                                       enable_text_splitting=False)
            wav = np.asarray(out['wav'], dtype=np.float32)
            score = heard_score(sentence, wav)
            if score is None:            # сверить не с чем (хаб спит) — как есть
                return wav
            if score > best_score:
                best, best_score = wav, score
            if score >= self.GOOD:
                break
            log(f'дубль {take + 1} неточен ({score:.2f}), переозвучиваю: {sentence[:70]}')
        self.last_score = min(getattr(self, 'last_score', 1.0), best_score)
        return best


def heard_score(sentence: str, wav: np.ndarray) -> float | None:
    """Насколько сказанное совпало с задуманным (0..1), по распознаванию хаба.
    None — хаб не ответил или его распознавание не на карте."""
    import base64
    import difflib
    import urllib.request
    from scipy.signal import resample_poly
    pcm = (np.clip(resample_poly(wav, 2, 3), -1, 1) * 32767).astype('<i2').tobytes()   # 24 → 16 кГц
    request = urllib.request.Request(
        'http://127.0.0.1:4480/hub/asr', method='POST',
        data=json.dumps({'pcm': base64.b64encode(pcm).decode(), 'rate': 16000, 'check': True}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            heard = json.loads(response.read().decode('utf-8'))
    except (OSError, ValueError):
        return None
    if not heard.get('ok'):
        return None
    want, got = words_of(sentence), words_of(heard.get('text') or '')
    return difflib.SequenceMatcher(None, want, got).ratio() if want else 1.0


VOICE = Voice()
VOICE.load_profile()


def split_sentences(text: str) -> list[str]:
    """XTTS держит интонацию в пределах ~250 знаков — длинное режем по фразам."""
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    out, current = [], ''
    for sentence in sentences:
        if current and len(current) + len(sentence) > 220:
            out.append(current)
            current = sentence
        else:
            current = (current + ' ' + sentence).strip()
    if current:
        out.append(current)
    return out


# --------------------------------------------------------------- звук ------

def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.int32).astype(np.float32)
    audio /= 32768.0 if width == 2 else 2147483648.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def wav_bytes(audio: np.ndarray, rate: int = SR) -> bytes:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def clean(audio: np.ndarray, rate: int) -> np.ndarray:
    """Пересэмплирование, срез тишины по краям и длинных пауз, выравнивание громкости."""
    import librosa
    if rate != SR:
        audio = librosa.resample(audio, orig_sr=rate, target_sr=SR)
    audio = audio - float(np.mean(audio))
    intervals = librosa.effects.split(audio, top_db=38, frame_length=1024, hop_length=256)
    pieces = []
    gap = np.zeros(int(SR * 0.25), dtype=np.float32)
    for start, end in intervals:
        pieces.append(audio[max(0, start - 1200):end + 1200])
        pieces.append(gap)
    speech = np.concatenate(pieces) if pieces else audio
    peak = float(np.max(np.abs(speech))) or 1.0
    return (speech / peak * 0.9).astype(np.float32)


# -------------------------------------------------------------- маршруты ---

@app.get('/voice/status')
def status():
    ready = (VOICE_DIR / 'ready.json').exists()
    return {'ok': True, 'state': 'ready' if ready and VOICE.state != 'cloning' else VOICE.state,
            'message': VOICE.message, 'ready': ready}


def clone_job(sample_path: Path, seconds: float) -> None:
    try:
        VOICE.state, VOICE.message = 'cloning', 'Создаю голосовой профиль…'
        started = time.time()
        VOICE.clone(sample_path)
        shutil.rmtree(VOICE_DIR / 'cache', ignore_errors=True)
        VOICE.message = 'Записываю пример твоим голосом…'
        preview = VOICE.speak('Привет! Теперь я говорю твоим голосом. Звучит похоже? '
                              'Если да — я буду так отвечать тебе в Cloud HDR.')
        (VOICE_DIR / 'preview.wav').write_bytes(wav_bytes(preview))
        (VOICE_DIR / 'ready.json').write_text(json.dumps({
            'ts': time.time(), 'seconds': round(seconds, 1), 'device': VOICE.device,
            'took': round(time.time() - started, 1)}, ensure_ascii=False), encoding='utf-8')
        VOICE.state, VOICE.message = 'ready', 'Готово'
        log(f'голос готов: образец {seconds:.1f} с, клон за {time.time() - started:.1f} с')
    except Exception as error:  # noqa: BLE001
        VOICE.state, VOICE.message = 'error', f'Не получилось: {error}'
        log(f'ошибка клонирования: {error!r}')


@app.post('/voice/sample')
async def sample(request: Request):
    data = await request.body()
    try:
        audio, rate = read_wav(data)
    except Exception:  # noqa: BLE001
        return {'ok': False, 'error': 'Не удалось прочитать запись. Попробуйте ещё раз.'}
    seconds = len(audio) / rate
    level = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    if seconds < 20:
        return {'ok': False, 'error': f'Запись слишком короткая ({seconds:.0f} с). Нужно хотя бы 30 секунд — прочитайте весь текст.'}
    if level < 0.004:
        return {'ok': False, 'error': 'Запись очень тихая — похоже, микрофон не тот или выключен. Проверьте и запишите заново.'}
    (VOICE_DIR / 'raw.wav').write_bytes(data)
    cleaned = clean(audio, rate)
    sample_path = VOICE_DIR / 'sample.wav'
    sample_path.write_bytes(wav_bytes(cleaned))
    (VOICE_DIR / 'ready.json').unlink(missing_ok=True)
    VOICE.state, VOICE.message = 'cloning', 'Загружаю модель клонирования…'
    threading.Thread(target=clone_job, args=(sample_path, len(cleaned) / SR), daemon=True).start()
    return {'ok': True, 'seconds': round(seconds, 1), 'speech': round(len(cleaned) / SR, 1)}


@app.get('/voice/preview')
def preview():
    path = VOICE_DIR / 'preview.wav'
    if not path.exists():
        return Response(status_code=404)
    return Response(path.read_bytes(), media_type='audio/wav')


@app.post('/voice/park')
def voice_park():
    """Очередь видеопамяти: модель разговора просит карту — уезжаем в ОЗУ,
    если прямо сейчас не говорим."""
    VOICE.last_used = 0
    VOICE.yield_requested = True      # заготовка фраз прервётся и уступит карту
    VOICE.sleep_if_idle()
    return {'ok': True, 'parked': VOICE.parked}


# Короткие фразы («Открываю Яндекс Музыку», «Готово») повторяются весь день.
# Синтез каждой — пробуждение XTTS на видеокарте (и выселение оттуда модели
# разговора) плюс секунда работы; с диска та же фраза звучит мгновенно и карту
# не трогает. Длинные ответы не кэшируем: они не повторяются.
CACHE_DIR = VOICE_DIR / 'cache'
CACHE_MAX_TEXT = 160
CACHE_MAX_FILES = 400


def cache_path(text: str) -> Path:
    # «v2»: до подготовки текста и сверки слухом в кэш попадали и скомканные
    # дубли — с новым ключом они больше не звучат (и уходят по старости).
    key = 'v2|' + re.sub(r'\s+', ' ', text).strip().lower()
    return CACHE_DIR / (hashlib.sha1(key.encode('utf-8')).hexdigest()[:20] + '.wav')


def speak_cached(text: str) -> bytes:
    path = cache_path(text) if len(text) <= CACHE_MAX_TEXT else None
    if path is not None and path.exists():
        return path.read_bytes()
    data = wav_bytes(VOICE.speak(text))
    # неточный дубль прозвучит один раз, но не станет «вечной» заготовкой
    if path is not None and VOICE.last_score >= Voice.GOOD:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files = sorted(CACHE_DIR.glob('*.wav'), key=lambda f: f.stat().st_atime)
            for old in files[:-CACHE_MAX_FILES]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    return data


@app.post('/voice/tts')
async def tts(request: Request):
    body = await request.json()
    text = str(body.get('text') or '').strip()[:1200]
    if not text:
        return Response(status_code=400)
    try:
        data = speak_cached(text)
    except Exception as error:  # noqa: BLE001
        return Utf8Json({'ok': False, 'error': str(error)}, status_code=503)
    return Response(data, media_type='audio/wav')


@app.post('/voice/prepare')
async def prepare(request: Request):
    """Заранее озвучить частые фразы (уши присылают их при запуске): один
    раз при первом включении, дальше всё уже лежит на диске."""
    body = await request.json()
    texts = [str(t).strip() for t in (body.get('texts') or []) if str(t).strip()]
    missing = [t for t in texts[:60] if len(t) <= CACHE_MAX_TEXT and not cache_path(t).exists()]
    if missing and (VOICE_DIR / 'ready.json').exists():
        def job():
            VOICE.yield_requested = False
            for text in missing:
                if VOICE.yield_requested:
                    log('заготовка фраз уступила карту — остальное озвучится по ходу')
                    VOICE.last_used = 0
                    VOICE.sleep_if_idle()
                    return
                try:
                    speak_cached(text)
                except Exception as error:  # noqa: BLE001
                    log(f'заготовка «{text}» не удалась: {error}')
                    return
            log(f'заготовлено фраз: {len(missing)}')
        threading.Thread(target=job, daemon=True).start()
    return {'ok': True, 'missing': len(missing)}


@app.get('/record', response_class=HTMLResponse)
def record_page():
    return (ROOT / 'voice_recorder.html').read_text(encoding='utf-8')


@app.get('/voice/health')
def health():
    return {'ok': True, 'name': 'Cloud HDR Voice'}


if __name__ == '__main__':
    import uvicorn
    if sys.stdout is None or sys.stderr is None:
        sink = open(VOICE_DIR / 'voice-console.log', 'a', encoding='utf-8', buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    log(f'Cloud HDR Voice: http://127.0.0.1:{PORT}/record')

    def idle_watch():
        while True:
            time.sleep(5)
            try:
                VOICE.sleep_if_idle()
            except Exception as error:  # noqa: BLE001
                log(f'выгрузка не удалась: {error}')
    threading.Thread(target=idle_watch, daemon=True).start()

    # Голос уже записан — модель грузим сразу, в фоне: иначе первый ответ
    # приложения ждал бы полминуты, пока XTTS читается с диска.
    if (VOICE_DIR / 'ready.json').exists():
        def warm():
            with VOICE.lock:
                VOICE.load()
                VOICE.last_used = time.time()
        threading.Thread(target=warm, daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning', access_log=False)
