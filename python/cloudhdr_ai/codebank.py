"""
Cloud HDR AI — банк задач по коду для дообучения Cloud HDR Ultra.

Зачем банк написан вручную, а не скачан.

Готовые наборы с кодом есть, и их много, но все они плохи ровно тем, что здесь
важнее всего. Инструкции в них английские, а спрашивать будут по-русски. Ответы
в них — голый код без объяснения либо объяснение без кода. И главное: формат.
Модель учится не «программировать вообще» — на 760 млн параметров этого не
выйдет и не нужно. Она учится ОТВЕЧАТЬ КОДОМ: русская вводная строка, блок в
тройных кавычках с указанием языка, закрывающие кавычки, строка о том, как этим
пользоваться. Ни один скачанный набор такого формата не даёт, а именно на нём
держится и подсветка в чате, и правило остановки на сервере (profiles.can_finish
ждёт закрытый блок).

Отсюда и устройство. Каждая задача — это несколько способов её попросить, язык,
готовый код и пояснение. Дальше ultra.py разворачивает их в диалоги: каждый
вопрос сочетается с разными вводными строками, и из полусотни задач выходит
несколько тысяч примеров одного и того же формата, но не одного и того же текста.

Код здесь настоящий и рабочий: он и есть то, чему модель подражает. Ошибка в
образце — это ошибка, которую она выучит и будет повторять.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Чем начинается ответ с кодом. Не для красоты: без вводной строки модель
#: приучается открывать ответ сразу тройными кавычками, и на любую реплику потом
#: норовит выдать блок кода.
OPENERS = (
    "Готово:",
    "Вот рабочий вариант:",
    "Держите:",
    "Пожалуйста:",
    "Вот код:",
    "Сделал:",
)

#: Задачи. Поля: asks — как об этом просят, lang — язык блока, code — сам код,
#: note — что сказать после блока.
TASKS: List[Dict[str, Any]] = [

    # ------------------------------------------------------------------ HTML --
    {
        "asks": [
            "напиши код html страницы с кнопкой",
            "сделай простую html страницу с кнопкой",
            "нужна html страница с одной кнопкой",
            "создай html с кнопкой",
        ],
        "lang": "html",
        "code": """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Кнопка</title>
  <style>
    body { display: grid; place-items: center; height: 100vh; margin: 0; font-family: system-ui, sans-serif; }
    button { padding: 14px 28px; font-size: 16px; border: 0; border-radius: 10px; background: #5b8cff; color: #fff; cursor: pointer; }
    button:hover { background: #4a78e8; }
  </style>
</head>
<body>
  <button onclick="alert('Кнопка нажата')">Нажми меня</button>
</body>
</html>""",
        "note": "Сохраните как index.html и откройте двойным щелчком — страница откроется в браузере.",
    },
    {
        "asks": [
            "напиши html страницу с формой обратной связи",
            "сделай форму на html",
            "нужна html форма с именем и сообщением",
        ],
        "lang": "html",
        "code": """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Обратная связь</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #f5f7fb; margin: 0; padding: 40px; }
    form { max-width: 420px; margin: 0 auto; background: #fff; padding: 24px; border-radius: 14px; }
    label { display: block; margin: 14px 0 6px; font-size: 14px; color: #445; }
    input, textarea { width: 100%; padding: 10px; border: 1px solid #d5dced; border-radius: 8px; font: inherit; }
    button { margin-top: 18px; width: 100%; padding: 12px; border: 0; border-radius: 8px; background: #5b8cff; color: #fff; font-size: 15px; cursor: pointer; }
  </style>
</head>
<body>
  <form onsubmit="event.preventDefault(); alert('Спасибо, ' + this.name.value);">
    <h2>Напишите нам</h2>
    <label for="name">Имя</label>
    <input id="name" name="name" required>
    <label for="message">Сообщение</label>
    <textarea id="message" name="message" rows="4" required></textarea>
    <button type="submit">Отправить</button>
  </form>
</body>
</html>""",
        "note": "Форма ничего не отправляет на сервер — она показывает окно с именем. Чтобы отправлять по-настоящему, замените обработчик на fetch к своему адресу.",
    },
    {
        "asks": [
            "сделай html таблицу",
            "напиши таблицу на html",
            "нужна табличка в html с рамками",
        ],
        "lang": "html",
        "code": """<table>
  <thead>
    <tr><th>Товар</th><th>Цена</th><th>Наличие</th></tr>
  </thead>
  <tbody>
    <tr><td>Клавиатура</td><td>3 200 ₽</td><td>да</td></tr>
    <tr><td>Мышь</td><td>1 450 ₽</td><td>да</td></tr>
    <tr><td>Монитор</td><td>18 900 ₽</td><td>нет</td></tr>
  </tbody>
</table>

<style>
  table { border-collapse: collapse; font-family: system-ui, sans-serif; }
  th, td { border: 1px solid #d5dced; padding: 8px 14px; text-align: left; }
  th { background: #f0f3fa; }
</style>""",
        "note": "border-collapse: collapse убирает двойные линии между ячейками — без него каждая клетка рисует свою рамку.",
    },
    {
        "asks": [
            "сделай карточку товара на html",
            "напиши html карточку с картинкой и ценой",
        ],
        "lang": "html",
        "code": """<div class="card">
  <img src="photo.jpg" alt="Товар">
  <h3>Наушники Cloud</h3>
  <p class="price">5 900 ₽</p>
  <button>В корзину</button>
</div>

<style>
  .card { width: 240px; padding: 16px; border: 1px solid #e2e7f3; border-radius: 14px; font-family: system-ui, sans-serif; }
  .card img { width: 100%; border-radius: 10px; }
  .card h3 { margin: 12px 0 4px; font-size: 16px; }
  .price { margin: 0 0 14px; font-size: 18px; font-weight: 700; color: #1c2a4a; }
  .card button { width: 100%; padding: 10px; border: 0; border-radius: 8px; background: #5b8cff; color: #fff; cursor: pointer; }
</style>""",
        "note": "Замените photo.jpg на путь к своей картинке — файл должен лежать рядом со страницей.",
    },
    {
        "asks": [
            "напиши html страницу с тёмной темой",
            "сделай тёмную страницу на html",
        ],
        "lang": "html",
        "code": """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Тёмная тема</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 48px; background: #0d1120; color: #e8eefc; font-family: system-ui, sans-serif; }
    h1 { margin: 0 0 12px; font-size: 32px; }
    p { color: #93a4c8; line-height: 1.7; max-width: 560px; }
    a { color: #7aaaff; }
  </style>
</head>
<body>
  <h1>Тёмная тема</h1>
  <p>Фон почти чёрный, но не чёрный: чистый #000 на светящемся экране даёт слишком резкий контраст с текстом и утомляет глаза.</p>
  <a href="#">Ссылка</a>
</body>
</html>""",
        "note": "Строка color-scheme: dark сообщает браузеру о тёмном оформлении — полосы прокрутки и поля ввода станут тёмными сами.",
    },

    # ------------------------------------------------------------------- CSS --
    {
        "asks": [
            "как отцентрировать блок на css",
            "напиши css чтобы отцентрировать div",
            "css для центрирования по горизонтали и вертикали",
        ],
        "lang": "css",
        "code": """.wrapper {
  display: grid;
  place-items: center;
  min-height: 100vh;
}""",
        "note": "place-items: center центрирует и по горизонтали, и по вертикали сразу. Раньше для этого писали три правила flexbox или трюк с transform.",
    },
    {
        "asks": [
            "напиши css для красивой кнопки",
            "сделай стиль кнопки на css",
            "css кнопка с наведением",
        ],
        "lang": "css",
        "code": """.btn {
  padding: 12px 24px;
  border: 0;
  border-radius: 10px;
  background: linear-gradient(180deg, #6f9bff, #5b8cff);
  color: #fff;
  font: 600 15px system-ui, sans-serif;
  cursor: pointer;
  transition: transform .18s ease, box-shadow .18s ease;
}

.btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 20px -8px rgba(91, 140, 255, .8);
}

.btn:active {
  transform: translateY(0);
}""",
        "note": "Сдвиг на один пиксель вверх при наведении и возврат при нажатии — этого хватает, чтобы кнопка ощущалась живой; больше двигать не стоит.",
    },
    {
        "asks": [
            "напиши css сетку из карточек",
            "сделай адаптивную сетку на css grid",
            "css grid который сам переносит колонки",
        ],
        "lang": "css",
        "code": """.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}""",
        "note": "auto-fit с minmax сам решает, сколько колонок поместится: медиазапросы для этого не нужны.",
    },
    {
        "asks": [
            "напиши css анимацию появления",
            "сделай плавное появление на css",
            "css анимация fade in",
        ],
        "lang": "css",
        "code": """.appear {
  animation: appear .5s cubic-bezier(.22, 1, .36, 1) both;
}

@keyframes appear {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: none; }
}""",
        "note": "both в конце сохраняет начальное состояние до старта и конечное после — без него элемент моргнёт в первом кадре.",
    },

    # -------------------------------------------------------------- JavaScript --
    {
        "asks": [
            "напиши функцию на javascript которая считает сумму массива",
            "js функция суммы массива",
            "как сложить все числа массива в javascript",
        ],
        "lang": "js",
        "code": """function sum(numbers) {
  return numbers.reduce((total, value) => total + value, 0);
}

console.log(sum([1, 2, 3, 4]));   // 10""",
        "note": "Ноль вторым аргументом reduce обязателен: без него пустой массив бросит ошибку вместо того, чтобы вернуть 0.",
    },
    {
        "asks": [
            "напиши калькулятор на javascript",
            "сделай простой калькулятор на js",
        ],
        "lang": "js",
        "code": """function calculate(a, operator, b) {
  switch (operator) {
    case '+': return a + b;
    case '-': return a - b;
    case '*': return a * b;
    case '/':
      if (b === 0) throw new Error('Деление на ноль');
      return a / b;
    default:
      throw new Error('Неизвестная операция: ' + operator);
  }
}

console.log(calculate(15, '*', 4));   // 60""",
        "note": "Деление на ноль и неизвестный знак выбрасывают ошибку, а не возвращают Infinity или undefined: молчаливый неверный ответ хуже остановки.",
    },
    {
        "asks": [
            "напиши js который меняет текст по клику",
            "javascript обработчик клика по кнопке",
            "как повесить клик на кнопку в js",
        ],
        "lang": "js",
        "code": """const button = document.querySelector('#hello');
const output = document.querySelector('#output');

button.addEventListener('click', () => {
  output.textContent = 'Нажато в ' + new Date().toLocaleTimeString('ru-RU');
});""",
        "note": "querySelector вернёт null, если элемента нет, — скрипт должен стоять после разметки либо внутри обработчика DOMContentLoaded.",
    },
    {
        "asks": [
            "напиши на js функцию которая переворачивает строку",
            "js перевернуть строку",
        ],
        "lang": "js",
        "code": """function reverse(text) {
  return [...text].reverse().join('');
}

console.log(reverse('Cloud HDR'));   // RDH duolC""",
        "note": "Разбор через [...text], а не split(''): так корректно переворачиваются эмодзи и составные символы, которые занимают две ячейки строки.",
    },
    {
        "asks": [
            "напиши таймер на javascript",
            "js обратный отсчёт",
            "сделай секундомер на js",
        ],
        "lang": "js",
        "code": """function countdown(seconds, onTick, onDone) {
  let left = seconds;
  const timer = setInterval(() => {
    left -= 1;
    onTick(left);
    if (left <= 0) {
      clearInterval(timer);
      onDone();
    }
  }, 1000);
  return () => clearInterval(timer);
}

const cancel = countdown(10, (left) => console.log(left), () => console.log('Время вышло'));""",
        "note": "Функция возвращает способ отменить отсчёт: без него таймер продолжит тикать, даже когда страница о нём забыла.",
    },
    {
        "asks": [
            "напиши js для сортировки массива объектов",
            "как отсортировать массив объектов по полю в javascript",
        ],
        "lang": "js",
        "code": """const people = [
  { name: 'Аня', age: 31 },
  { name: 'Борис', age: 24 },
  { name: 'Вера', age: 45 },
];

const byAge = [...people].sort((a, b) => a.age - b.age);
console.log(byAge.map((p) => p.name));   // ['Борис', 'Аня', 'Вера']""",
        "note": "Копия через [...people] нужна потому, что sort меняет исходный массив на месте — а исходные данные обычно ещё пригодятся.",
    },

    # ------------------------------------------------------------------ Python --
    {
        "asks": [
            "напиши на python функцию факториала",
            "python факториал",
            "сделай функцию которая считает факториал на питоне",
        ],
        "lang": "python",
        "code": """def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("Факториал определён только для неотрицательных чисел")
    result = 1
    for value in range(2, n + 1):
        result *= value
    return result


print(factorial(10))   # 3628800""",
        "note": "Цикл, а не рекурсия: на больших числах рекурсия упрётся в предел глубины, а цикл — нет.",
    },
    {
        "asks": [
            "напиши на python чтение файла построчно",
            "как прочитать файл в питоне",
            "python открыть текстовый файл",
        ],
        "lang": "python",
        "code": """from pathlib import Path


def read_lines(path: str) -> list[str]:
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"Файла нет: {file}")
    with file.open(encoding="utf-8") as handle:
        return [line.rstrip("\\n") for line in handle]


for line in read_lines("notes.txt"):
    print(line)""",
        "note": "encoding=\"utf-8\" указан явно: на Windows кодировка по умолчанию не UTF-8, и русский текст без этого читается как набор вопросительных знаков.",
    },
    {
        "asks": [
            "напиши на питоне поиск максимума в списке",
            "python найти самое большое число",
        ],
        "lang": "python",
        "code": """def largest(values: list[int]) -> int:
    if not values:
        raise ValueError("Список пуст")
    best = values[0]
    for value in values[1:]:
        if value > best:
            best = value
    return best


print(largest([4, 17, 3, 9]))   # 17""",
        "note": "В настоящей программе достаточно встроенной max(); здесь цикл написан явно, чтобы был виден сам ход поиска.",
    },
    {
        "asks": [
            "напиши класс на python",
            "python пример класса",
            "сделай класс с методами на питоне",
        ],
        "lang": "python",
        "code": """class Account:
    def __init__(self, owner: str, balance: float = 0.0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Пополнение должно быть больше нуля")
        self.balance += amount

    def withdraw(self, amount: float) -> None:
        if amount > self.balance:
            raise ValueError("Недостаточно средств")
        self.balance -= amount

    def __repr__(self) -> str:
        return f"Account({self.owner!r}, {self.balance:.2f})"


account = Account("Мирослав", 1000)
account.withdraw(250)
print(account)   # Account('Мирослав', 750.00)""",
        "note": "__repr__ окупается на первой же отладке: без него print покажет адрес объекта в памяти вместо его содержимого.",
    },
    {
        "asks": [
            "напиши на python подсчёт слов в тексте",
            "питон посчитать частоту слов",
        ],
        "lang": "python",
        "code": """import re
from collections import Counter


def word_counts(text: str, top: int = 5) -> list[tuple[str, int]]:
    words = re.findall(r"\\w+", text.lower())
    return Counter(words).most_common(top)


print(word_counts("Кот сел на кот и кот ушёл"))""",
        "note": "lower() до подсчёта обязателен, иначе «Кот» и «кот» будут разными словами и статистика разъедется.",
    },
    {
        "asks": [
            "напиши на python сортировку пузырьком",
            "питон пузырьковая сортировка",
        ],
        "lang": "python",
        "code": """def bubble_sort(values: list[int]) -> list[int]:
    items = list(values)
    for end in range(len(items) - 1, 0, -1):
        swapped = False
        for i in range(end):
            if items[i] > items[i + 1]:
                items[i], items[i + 1] = items[i + 1], items[i]
                swapped = True
        if not swapped:
            break
    return items


print(bubble_sort([5, 2, 9, 1]))   # [1, 2, 5, 9]""",
        "note": "Флаг swapped выходит из цикла, как только список оказался отсортирован; на почти упорядоченных данных это экономит почти всю работу.",
    },
    {
        "asks": [
            "напиши на python работу со словарём",
            "python пример словаря",
        ],
        "lang": "python",
        "code": """prices = {"клавиатура": 3200, "мышь": 1450, "монитор": 18900}

# перебор пар
for name, price in prices.items():
    print(f"{name}: {price} ₽")

# безопасное чтение — без KeyError
print(prices.get("наушники", 0))

# самое дорогое
print(max(prices, key=prices.get))   # монитор""",
        "note": "get с запасным значением вместо квадратных скобок — тогда отсутствующий ключ не роняет программу.",
    },

    # ------------------------------------------------------------------- SQL --
    {
        "asks": [
            "напиши sql запрос выборки",
            "sql запрос с условием и сортировкой",
            "нужен запрос sql к таблице заказов",
        ],
        "lang": "sql",
        "code": """SELECT
    customer_id,
    COUNT(*)      AS orders,
    SUM(total)    AS revenue
FROM orders
WHERE created_at >= '2026-01-01'
GROUP BY customer_id
HAVING SUM(total) > 10000
ORDER BY revenue DESC
LIMIT 20;""",
        "note": "Условие на сумму стоит в HAVING, а не в WHERE: WHERE отбирает строки до группировки, когда суммы ещё не существует.",
    },
    {
        "asks": [
            "напиши sql создание таблицы",
            "sql create table пример",
        ],
        "lang": "sql",
        "code": """CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    email      TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_users_created ON users (created_at);""",
        "note": "Индекс по дате нужен, если по ней будут искать и сортировать: без него база просматривает всю таблицу целиком.",
    },

    # ------------------------------------------------------------ PowerShell --
    {
        "asks": [
            "напиши скрипт powershell",
            "powershell скрипт для поиска файлов",
            "сделай батник powershell",
        ],
        "lang": "powershell",
        "code": """# Крупнейшие файлы в папке «Загрузки»
$folder = Join-Path $env:USERPROFILE 'Downloads'

Get-ChildItem -Path $folder -File -Recurse -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending |
    Select-Object -First 10 Name, @{ Name = 'МБ'; Expression = { [math]::Round($_.Length / 1MB, 1) } }""",
        "note": "ErrorAction SilentlyContinue нужен из-за системных папок, куда нет доступа: без него перебор остановится на первой такой.",
    },

    # ------------------------------------------------------------------ JSON --
    {
        "asks": [
            "напиши пример json",
            "покажи структуру json файла",
        ],
        "lang": "json",
        "code": """{
  "name": "Cloud HDR",
  "version": "1.0",
  "offline": true,
  "model": {
    "params": 760000000,
    "context": 2048
  },
  "features": ["голос", "поиск по ПК", "разговор"]
}""",
        "note": "В JSON нет комментариев и запятой после последнего элемента — оба случая ломают разбор.",
    },

    # =========================== второй набор задач ===========================
    # Добавлен, чтобы формулировок в корпусе стало заметно больше. Одних
    # повторов для этого мало: повтор учит форме, а разные задачи — тому, что
    # форма не привязана к конкретной странице с кнопкой.

    {
        "asks": [
            "напиши html меню навигации",
            "сделай шапку сайта с меню",
            "нужна навигация на html",
        ],
        "lang": "html",
        "code": """<nav class="nav">
  <a class="nav__logo" href="/">Cloud</a>
  <ul class="nav__links">
    <li><a href="#about">О нас</a></li>
    <li><a href="#price">Цены</a></li>
    <li><a href="#contacts">Контакты</a></li>
  </ul>
</nav>

<style>
  .nav { display: flex; align-items: center; justify-content: space-between; padding: 14px 28px; background: #0d1120; font-family: system-ui, sans-serif; }
  .nav__logo { color: #fff; font-weight: 700; text-decoration: none; font-size: 18px; }
  .nav__links { display: flex; gap: 22px; list-style: none; margin: 0; padding: 0; }
  .nav__links a { color: #93a4c8; text-decoration: none; font-size: 14px; }
  .nav__links a:hover { color: #fff; }
</style>""",
        "note": "justify-content: space-between разводит логотип и ссылки по краям — распорка между ними не нужна.",
    },
    {
        "asks": [
            "напиши html список дел",
            "сделай туду лист на html",
            "нужен список задач с галочками",
        ],
        "lang": "html",
        "code": """<ul class="todo">
  <li><label><input type="checkbox" checked> Собрать корпус</label></li>
  <li><label><input type="checkbox"> Запустить обучение</label></li>
  <li><label><input type="checkbox"> Проверить ответы</label></li>
</ul>

<style>
  .todo { list-style: none; padding: 0; font-family: system-ui, sans-serif; max-width: 320px; }
  .todo li { padding: 10px 12px; border-bottom: 1px solid #e6eaf4; }
  .todo label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
  .todo input:checked + * , .todo li:has(input:checked) { color: #98a2b8; text-decoration: line-through; }
</style>""",
        "note": "Обёртка в label делает кликабельной всю строку, а не только сам квадратик.",
    },
    {
        "asks": [
            "напиши модальное окно на html и css",
            "сделай всплывающее окно",
        ],
        "lang": "html",
        "code": """<button onclick="dlg.showModal()">Открыть</button>

<dialog id="dlg">
  <h3>Подтвердите действие</h3>
  <p>Это нельзя будет отменить.</p>
  <form method="dialog">
    <button value="no">Отмена</button>
    <button value="yes">Выполнить</button>
  </form>
</dialog>

<style>
  dialog { border: 0; border-radius: 14px; padding: 24px; font-family: system-ui, sans-serif; max-width: 380px; }
  dialog::backdrop { background: rgba(8, 12, 26, .6); }
</style>""",
        "note": "Тег dialog умеет модальность сам: затемнение фона, Escape и запрет кликов мимо — всё это уже встроено, писать на JavaScript нечего.",
    },
    {
        "asks": [
            "напиши css для flex строки",
            "как расставить элементы по краям в css",
        ],
        "lang": "css",
        "code": """.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}""",
        "note": "flex-wrap: wrap спасает на узком экране: без него элементы сожмутся до нечитаемого вместо того, чтобы перенестись на строку ниже.",
    },
    {
        "asks": [
            "напиши css переменные",
            "как задать цвета через переменные css",
        ],
        "lang": "css",
        "code": """:root {
  --bg: #0d1120;
  --text: #e8eefc;
  --accent: #5b8cff;
  --radius: 12px;
}

.card {
  background: var(--bg);
  color: var(--text);
  border-radius: var(--radius);
  border: 1px solid var(--accent);
}""",
        "note": "Переменные наследуются, поэтому тему можно поменять целиком, переопределив :root — трогать сами правила не придётся.",
    },
    {
        "asks": [
            "напиши js для сохранения в localstorage",
            "как сохранить данные в браузере javascript",
        ],
        "lang": "js",
        "code": """const store = {
  save(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;   // приватный режим или переполнено хранилище
    }
  },
  load(key, fallback = null) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
};

store.save('settings', { theme: 'dark' });
console.log(store.load('settings', {}));""",
        "note": "Оба вызова обёрнуты в try: в приватном режиме localStorage бросает ошибку, и без обёртки страница ляжет целиком.",
    },
    {
        "asks": [
            "напиши js функцию с задержкой вызова",
            "javascript debounce",
            "как не вызывать функцию на каждое нажатие клавиши",
        ],
        "lang": "js",
        "code": """function debounce(fn, delay = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

const search = debounce((text) => console.log('Ищу:', text), 400);
input.addEventListener('input', (event) => search(event.target.value));""",
        "note": "Вызов происходит через 400 мс после ПОСЛЕДНЕГО нажатия: пока человек печатает, таймер сбрасывается заново.",
    },
    {
        "asks": [
            "напиши класс на javascript",
            "js пример класса с методами",
        ],
        "lang": "js",
        "code": """class Timer {
  #start = null;

  begin() {
    this.#start = performance.now();
    return this;
  }

  stop() {
    if (this.#start === null) throw new Error('Таймер не запущен');
    const ms = performance.now() - this.#start;
    this.#start = null;
    return Math.round(ms);
  }
}

const timer = new Timer().begin();
setTimeout(() => console.log(timer.stop() + ' мс'), 500);""",
        "note": "Решётка перед именем делает поле по-настоящему закрытым: снаружи к нему не добраться даже случайно.",
    },
    {
        "asks": [
            "напиши js который считывает данные формы",
            "javascript получить значения формы",
        ],
        "lang": "js",
        "code": """document.querySelector('form').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  console.log(data);   // { name: 'Аня', email: 'a@b.ru' }
});""",
        "note": "FormData берёт значения по атрибуту name — поле без него в объект не попадёт, даже если у него есть id.",
    },
    {
        "asks": [
            "напиши на python работу с json",
            "python сохранить и прочитать json",
        ],
        "lang": "python",
        "code": """import json
from pathlib import Path


def save(path: str, data: dict) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load(path: str, default: dict | None = None) -> dict:
    file = Path(path)
    if not file.exists():
        return default or {}
    return json.loads(file.read_text(encoding="utf-8"))


save("settings.json", {"тема": "тёмная", "громкость": 0.85})
print(load("settings.json"))""",
        "note": "ensure_ascii=False оставляет русские буквы буквами: иначе в файле окажется \\u0442\\u0435\\u043c\\u0430 вместо «тема».",
    },
    {
        "asks": [
            "напиши на python декоратор",
            "питон пример декоратора замера времени",
        ],
        "lang": "python",
        "code": """import functools
import time


def timed(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            print(f"{func.__name__}: {time.perf_counter() - started:.3f} с")
    return wrapper


@timed
def slow():
    time.sleep(0.4)


slow()""",
        "note": "functools.wraps сохраняет имя и документацию исходной функции — без него отладка показывает «wrapper» вместо настоящего имени.",
    },
    {
        "asks": [
            "напиши на python поиск файлов в папке",
            "питон обойти все файлы в каталоге",
        ],
        "lang": "python",
        "code": """from pathlib import Path


def find(folder: str, pattern: str = "*.txt") -> list[Path]:
    root = Path(folder).expanduser()
    return sorted(path for path in root.rglob(pattern) if path.is_file())


for path in find("~/Documents", "*.pdf"):
    print(path, path.stat().st_size // 1024, "КБ")""",
        "note": "rglob обходит и вложенные папки; expanduser разворачивает «~» в домашний каталог пользователя.",
    },
    {
        "asks": [
            "напиши на python обработку ошибок",
            "питон try except пример",
        ],
        "lang": "python",
        "code": """def divide(a: float, b: float) -> float:
    try:
        return a / b
    except ZeroDivisionError:
        raise ValueError("Делить на ноль нельзя") from None
    finally:
        print("Попытка деления выполнена")


try:
    divide(10, 0)
except ValueError as error:
    print("Ошибка:", error)""",
        "note": "Ловим конкретный ZeroDivisionError, а не except без типа: голый except прячет и опечатки в коде, и Ctrl+C.",
    },
    {
        "asks": [
            "напиши sql с объединением таблиц",
            "sql join пример",
        ],
        "lang": "sql",
        "code": """SELECT
    u.name,
    u.email,
    COUNT(o.id) AS orders
FROM users AS u
LEFT JOIN orders AS o ON o.user_id = u.id
GROUP BY u.id, u.name, u.email
ORDER BY orders DESC;""",
        "note": "LEFT JOIN, а не INNER: с внутренним объединением из выборки пропали бы пользователи вообще без заказов, а обычно нужны именно они.",
    },
    {
        "asks": [
            "напиши регулярное выражение для почты",
            "regex для email на python",
        ],
        "lang": "python",
        "code": """import re

EMAIL = re.compile(r"^[\\w.+-]+@[\\w-]+\\.[\\w.-]+$")


def is_email(text: str) -> bool:
    return bool(EMAIL.match(text.strip()))


print(is_email("user@example.com"))   # True
print(is_email("не почта"))           # False""",
        "note": "Выражение намеренно нестрогое. Полная проверка адреса по стандарту занимает несколько строк и всё равно ничего не гарантирует — единственная надёжная проверка это письмо с кодом.",
    },
    {
        "asks": [
            "напиши батник для windows",
            "сделай bat файл",
        ],
        "lang": "bat",
        "code": """@echo off
chcp 65001 >nul
title Резервная копия документов

set "SRC=%USERPROFILE%\\Documents"
set "DST=D:\\Backup\\%DATE:~-4%-%DATE:~3,2%-%DATE:~0,2%"

if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /E /R:1 /W:1 /NFL /NDL

echo Готово: %DST%
pause""",
        "note": "chcp 65001 включает UTF-8, иначе русские буквы в окне превратятся в мусор. robocopy умеет докопировать только изменившееся — второй запуск пройдёт быстро.",
    },
]


# ============================================================== при ответе ===
#
# Банк нужен не только обучению. Модель на 760 млн параметров формат ответа
# кодом выучила, а сам код пишет с ошибками: на «напиши на питоне факториал»
# она уверенно выдавала функцию, которая считает n·(1−n)/2. Если просьба
# почти дословно совпадает с задачей из банка, честнее отдать проверенный код,
# чем сгенерированный. Совпадение считаем по значимым основам слов: служебные
# («напиши», «код», «функцию», «пожалуйста») не в счёт, язык проверяется
# отдельно — «факториал на js» не должен получить ответ на Python.

import re as _re

_FILLER = {
    "напиш", "сдела", "созда", "покаж", "нужна", "нужен", "нужно", "код", "кодом", "функц", "функци",
    "скрип", "прогр", "пожал", "пример", "прост", "прост", "котор", "котора", "которы", "мне", "как",
    "на", "для", "из", "в", "с", "и", "по", "что", "чтобы", "это", "эту", "этот", "так", "меня",
    "можно", "можеш", "помог", "дай", "давай", "рабоч", "готов", "небол", "очень", "самый", "самы",
}
_LANGS = {
    "python": ("python", "питон", "пайтон", "py"),
    "js": ("javascript", "js", "джаваскрипт", "яваскрипт", "node"),
    "html": ("html", "хтмл"),
    "css": ("css", "цсс"),
    "sql": ("sql", "скл"),
    "powershell": ("powershell", "пауэршелл"),
    "bat": ("bat", "батник", "cmd"),
    "json": ("json",),
}


def _stems(text: str) -> set[str]:
    words = _re.findall(r"[a-zа-яё0-9#+]+", str(text).lower().replace("ё", "е"))
    out = set()
    for word in words:
        if any(word.startswith(alias) for aliases in _LANGS.values() for alias in aliases if len(alias) > 2) \
                or word in {a for aliases in _LANGS.values() for a in aliases}:
            continue
        stem = word[:4]
        if word[:5] in _FILLER or stem in _FILLER or word in _FILLER or len(word) < 2:
            continue
        out.add(stem)
    return out


def _lang_of(text: str) -> str | None:
    low = str(text).lower()
    for lang, aliases in _LANGS.items():
        for alias in aliases:
            if _re.search(r"(?<![a-zа-яё])" + _re.escape(alias), low):
                return lang
    return None


def recall(question: str) -> str | None:
    """Готовый ответ из банка, если просьба почти совпадает с одной из задач."""
    want = _stems(question)
    if not want:
        return None
    lang = _lang_of(question)
    best, best_score = None, 0.0
    for task in TASKS:
        if lang and task["lang"] != lang:
            continue
        asks = [_stems(ask) for ask in task["asks"]]
        vocabulary = set().union(*asks)
        # Оба направления. Хотя бы одна формулировка задачи целиком названа в
        # просьбе — и почти всё значимое из просьбы задача покрывает (словарь
        # всех её формулировок: «прочитать» из одной, «построчно» из другой).
        named = max((len(want & have) / len(have) for have in asks if have), default=0.0)
        covered = len(want & vocabulary) / len(want)
        score = min(named, covered)
        if score > best_score:
            best, best_score = task, score
    if best is None or best_score < 0.75:
        return None
    return f"{OPENERS[0]}\n\n```{best['lang']}\n{best['code']}\n```\n\n{best['note']}"
