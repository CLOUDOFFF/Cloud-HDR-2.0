# Cloud HDR 2.0

Локальный ИИ-помощник для Windows со своей дообученной языковой моделью Cloud HDR (4 млрд параметров, русский язык).
Всё приложение укладывается в 4 ГБ видеопамяти: распознавание речи, модель и голос работают по очереди.
Открывает программы и папки, ищет файлы, управляет системой, переводит, говорит своим голосом.
Всё считается на вашем компьютере — без облака, ключей и интернета.

## Установка

1. Откройте [последний релиз](../../releases/latest) и скачайте в **одну папку** все части `CloudHDR-2.0.zip.001`, `.002`, …, а также `SHA256SUMS.txt`, `Install-CloudHDR-2.0.cmd` и `install-release.ps1`.
2. Запустите `Install-CloudHDR-2.0.cmd`. Он сверит каждую часть (битую назовёт — её нужно перекачать), склеит архив, распакует, поставит Cloud HDR и запустит его.
3. Готово: окно вызывается по **Ctrl+Alt+C**. Первый ответ — 10–30 секунд, пока модель ложится в видеопамять.

Или одной командой в PowerShell (скачает части сама): `powershell -ExecutionPolicy Bypass -File setup\get-weights.ps1`.

**Нужно:** Windows 10/11, видеокарта NVIDIA от 6 ГБ, драйвер NVIDIA 580 или новее, ~30 ГБ свободного места на диске C на время установки (после — ~13 ГБ).

## Что умеет

- **Разговор** — своя модель Cloud HDR (4 млрд параметров), держит нить разговора, объясняет, пишет код.
- **Голос** — слушает «Клауд, …» и отвечает клоном вашего голоса за 0,6–1 с; перед синтезом числа, латиница и сокращения превращаются в слова, каждая фраза сверяется на слух.
- **Компьютер простыми словами** — программы и окна, вкладки, файлы (удаление только в корзину), звук и микрофон, «не беспокоить», режимы («игровой режим», «запомни рабочий режим: …»), сеть и скорость интернета, очистка, обновления, pdf и сжатие фото из выделенного, печать, «как там пк».
- **Руки** — там, где нет готовой команды, модель сама ведёт курсор по окну.
- **Перевод, защита, телефон как пульт** — всё локально.

## Части проекта

| Папка | Что внутри | Язык |
|---|---|---|
| [`desktop/`](desktop) | Окно приложения `CloudHDR.exe` на WebView2, иконка | .NET |
| [`public/`](public) | Интерфейс: чат, голос, зрение, навыки, оформление | HTML · CSS · JS |
| [`server/`](server) | Локальный агент Windows: запуск служб, команды, спутник, слух, курсор | PowerShell · Node.js |
| [`python/`](python) | Модель `cloudhdr_ai`, разбор команд `cloudhdr_nlu`, службы Hub / Phone / Voice | Python |
| [`setup/`](setup) | Установка, удаление, скачивание весов | PowerShell |

### Службы

| Порт | Служба | Файл |
|---|---|---|
| 4477 | Агент Windows | `server/agent.ps1`, `python/server.py` |
| 4478 | Разбор команд (spaCy) | `python/cloudhdr_nlu` |
| 4480 | Hub: перевод EN→RU, защита, распознавание речи, система, программы и окна, файлы | `python/cloudhdr_hub.py` |
| 4481 | Телефон по QR (локальная сеть, по ключу) | `python/cloudhdr_phone.py` |
| 4482 | Свой голос (XTTS v2) | `python/cloudhdr_voice.py` |

Все службы поднимает `server/launch.ps1`. Кроме телефона, все слушают только `127.0.0.1`.

## Что не лежит в репозитории

| Что | Размер | Где взять |
|---|---|---|
| Модель Cloud HDR (основа Qwen3-4B + дообучение) | 2,6 ГБ | основа — [Qwen/Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B), перевод в GGUF и дообучение — `python/cloudhdr_qwen/`; включается файлом `python/checkpoints/active.json` `{"engine": "qwen"}` |
| llama.cpp (`runtime/llama/`) | 0,6 ГБ | [ggml-org/llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), сборка win-cuda-13.4 |
| Python 3.12 + torch (`runtime/`) | 5 ГБ | Python 3.12, затем `pip install torch --index-url https://download.pytorch.org/whl/cu128` и `pip install -r python/requirements.txt` |
| Перевод `python/models/translate-en-ru` | 300 МБ | [Helsinki-NLP/opus-mt-en-ru](https://huggingface.co/Helsinki-NLP/opus-mt-en-ru) |
| Распознавание речи `python/models/whisper-turbo` | 1,6 ГБ | [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) (MIT), скрипт `setup/get-weights.ps1` |
| Голос `python/models/xtts-v2` | 2 ГБ | [coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2) (лицензия CPML, некоммерческая) |
| Обучающие корпуса, клон голоса | — | личные, не публикуются |

## Сборка из исходников

Модели, Python и llama.cpp в репозиторий не входят (см. таблицу выше) — проще всего взять их из релиза:
его архив содержит ровно этот код плюс `runtime/` и `python/models/`.

```powershell
git clone https://github.com/CLOUDOFFF/Cloud-HDR-2.0.git
cd Cloud-HDR-2.0
# Python 3.12 положить в runtime\ и поставить зависимости:
runtime\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
runtime\python.exe -m pip install -r python\requirements.txt -r python\requirements-nlu.txt
.\Установить.cmd
```

## Лицензия

Код — [MIT](LICENSE). Модель Cloud HDR основана на Qwen3-4B (Alibaba Cloud, лицензия Apache 2.0) и дообучена для этого приложения; запасная модель v7 дообучена поверх ruGPT-3 (ai-forever) и наследует условия её лицензии.
Сторонние части под своими лицензиями: WebView2 (`desktop/WebView2-LICENSE.txt`), шрифты Onest и Unbounded (SIL OFL), opus-mt-en-ru (CC-BY 4.0), XTTS v2 (Coqui CPML — только некоммерческое использование).
