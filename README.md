# prompt-engineering

## Учебный агент ВШЭ

Каркас сайта для команды хакатона: **письма и задания → дедлайны → план дня**.
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
Живые интеграции, LLM, хранение задач и напоминания ещё не реализованы.
Все источники честно показываются как неподключённые.

Необязательная конфигурация: скопируйте `.env.example` в `.env`.
Пустые значения используют значения приложения по умолчанию.
`HSE_DEMO_MODE=false` отключает примеры и показывает пустое состояние.
Без коннекторов этот режим не выдаёт реальные данные.

### Структура

~~~text
app/
  main.py                 FastAPI и подключение интерфейса
  config.py               Настройки из окружения
  api/schemas.py          Общие модели и контракт
  api/routes.py           HTTP-маршруты
  connectors/base.py      Граница для будущих интеграций
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

### PostgreSQL

Первая живая интеграция — [вики ФКН](docs/WIKI.md): получение страниц,
хранение версий, двухэтапная обработка OpenAI, кеш и API материалов.

Основа подключения, сессии и миграции готовы. Настройка сервера и локальной БД:
[docs/POSTGRESQL.md](docs/POSTGRESQL.md). Предметная схема и сохранение заданий
будут добавлены после уточнения требований.

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
Это dev-каркас без авторизации. Перед подключением персональных данных
нужно реализовать авторизацию и разграничение доступа. Фоновых процессов
в каркасе пока нет; команда запуска будет добавлена вместе с их реализацией.

## GitHub authorization

Authorize your GitHub account from an interactive terminal:

```bash
bash authorize-github.sh
```

The script installs GitHub CLI when Homebrew is available. Otherwise, install
[GitHub CLI](https://cli.github.com/) first (on Windows, run the script in Git Bash).
Follow the browser prompts to enter the one-time code and approve access.
Existing sign-ins are reused, and Git is configured to use GitHub CLI credentials.
