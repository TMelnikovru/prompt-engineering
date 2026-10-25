# prompt-engineering

## Учебный агент ВШЭ

Прототип учебного агента для команды хакатона: **источники → задания и материалы → план дня**.
Python 3.12+, FastAPI, Jinja2 и обычные HTML/CSS/JS. Для фронтенда не нужны
Node.js, сборка и ключ OpenAI. Правила проекта — в [AGENTS.md](AGENTS.md).

### Быстрый запуск

Установите [uv](https://docs.astral.sh/uv/getting-started/installation/), затем:

~~~sh
uv sync --frozen
uv run uvicorn app.main:app --reload
~~~

Открыть сайт: http://127.0.0.1:8000. API: http://127.0.0.1:8000/docs.
Без uv: создайте виртуальное окружение, установите
`python -m pip install -r requirements.txt` и запустите
`python -m uvicorn app.main:app --reload`.

По умолчанию включены **учебные примеры для разработки фронтенда**.
В live-режиме работают вики ФКН и DSBA Online, библиотека PostgreSQL и два
ИИ-этапа обработки вики. При входе выбираются параметры группы DSBA, данные
подключаются автоматически. Дедлайны и материалы сохраняются с историей.
Чтение выбранных Telegram-чатов и их ИИ-сводка настраиваются оператором отдельно,
см. ниже. Персональный план и напоминания ещё не реализованы. Учебные примеры
не означают успешного подключения источников.

Необязательная конфигурация: скопируйте `.env.example` в `.env`.
Пустые значения используют значения приложения по умолчанию.
`HSE_DEMO_MODE=false` отключает примеры и читает сохранённые данные PostgreSQL.
До подключения источников этот режим показывает пустое состояние.

### Структура

~~~text
app/
  main.py                 FastAPI и подключение интерфейса
  config.py               Настройки из окружения
  api/schemas.py          Общие модели и контракт
  api/routes.py           HTTP-маршруты
  connectors/             Загрузка и нормализация вики/DSBA
  db/                     PostgreSQL, сессии и версии документов
  services/dashboard.py   Учебные примеры и пустой ответ
frontend/
  templates/index.html    Разметка
  static/css/app.css      Стили и адаптивность
  static/js/api.js        HTTP-клиент
  static/js/app.js        Отображение, фильтры, навигация
docs/
  FRONTEND.md             Передача фронтенда и сценарии
  openapi.json            Снимок контракта
scripts/export_openapi.py Экспорт и проверка схемы
tests/                    Проверки контракта и режимов
~~~

Фронтенду начинать с [frontend/README.md](frontend/README.md) и
[контракта](docs/FRONTEND.md). Пустое состояние:
http://127.0.0.1:8000/?scenario=empty. Ошибка:
http://127.0.0.1:8000/?scenario=error.

### Проверки

~~~sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m scripts.export_openapi --check
~~~

После изменения API: `uv run python -m scripts.export_openapi`.
Зависимости зафиксированы в `uv.lock`. После их изменения обновите
pip-вариант: `uv export --frozen --no-dev --no-hashes --no-header -o requirements.txt`.

### Telegram: переписка → ИИ-сводка

Реализованы чтение выбранных чатов через Telethon, сохранение сообщений и правок
в PostgreSQL и ИИ-сводка с подтверждающими цитатами. Страница — `/telegram`.
Нужны собственные API-параметры Telegram, интерактивный вход на сервере и ключ OpenAI.
Без них интеграция не считается подключённой.
Настройка, команды и ограничения: [docs/TELEGRAM.md](docs/TELEGRAM.md).

Записи Telegram в библиотеке (`/documents?source_id=telegram`, их `/documents/{id}`),
источник `telegram` в `/sources` и `/knowledge/*` требуют `X-Ingestion-Token`:
переписка может содержать личные данные. Публичные вики и DSBA читаются без ключа.
Токен не встраивается в код сайта и не сохраняется браузером; оператор вводит его
на странице сводки.

### PostgreSQL

Первая живая интеграция — [вики ФКН](docs/WIKI.md): получение страниц,
хранение версий, двухэтапная обработка OpenAI, кеш и API материалов.

Подключение, сессии, миграции и схема источников/документов/версий готовы.
Настройка сервера и локальной БД: [docs/POSTGRESQL.md](docs/POSTGRESQL.md).
DSBA-задания и материалы берутся только из выбранного набора публичной группы.

Без `HSE_DATABASE_URL` фронтенд запускается как прежде.
`GET /api/v1/health` проверяет приложение, `GET /api/v1/ready` —
соединение с PostgreSQL. Миграции: `uv run alembic upgrade head`.

### Сервер

Рабочее развёртывание на `docker-prod`, PostgreSQL и автоматическое обновление
из `main`: [инструкция](docs/DEPLOYMENT.md). Прокси настраивается отдельно.

DSBA Online: [подключение через фронтенд и контракт API](docs/DSBA.md).

~~~sh
docker build -t hse-study-agent .
docker run --rm -p 8000:8000 --env-file .env hse-study-agent
~~~

Для контейнера создайте `.env` из примера. Healthcheck: `GET /api/v1/health`.
Это прототип без серверной авторизации: имя и группа в локальном профиле
сохраняются только в браузере. Для приватных данных требуется отдельная авторизация;
пока записи Telegram и ИИ-сводки защищены токеном одного оператора, разделения
нескольких студентов нет. Compose автоматически запускает wiki-worker и dsba-worker,
Telegram-worker включается профилем `telegram`. Команды и параметры повторов описаны
в инструкциях [вики](docs/WIKI.md), [DSBA](docs/DSBA.md) и [Telegram](docs/TELEGRAM.md).

## GitHub authorization

Authorize your GitHub account from an interactive terminal:

```bash
bash authorize-github.sh
```

The script installs GitHub CLI when Homebrew is available. Otherwise, install
[GitHub CLI](https://cli.github.com/) first (on Windows, run the script in Git Bash).
Follow the browser prompts to enter the one-time code and approve access.
Existing sign-ins are reused, and Git is configured to use GitHub CLI credentials.
