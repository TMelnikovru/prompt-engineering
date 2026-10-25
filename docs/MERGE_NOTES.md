# Подготовка backend к main

База согласования: `main` 559de32 (интерфейс ВШЭ) и `backend/postgresql` 641caab
(PostgreSQL, вики, два ИИ-этапа и серверный worker).

В backend влит этот main: сохранены новые HTML, иконки, шрифты, темы,
навигация и анимации. Старые стили каркаса не используются. Конфликт CSS
разрешён на основе main с добавлением состояния partial.

Контракт dashboard сохранён: meta/tasks/sources/plan, due_at/due_date,
estimated_minutes, evidence и plan.blocks[].task_id. Общие поля Source и
SourceView теперь определены одной моделью. Технический last_error остаётся
для совместимости, пользовательский текст приходит в error_message.
Подробная карта полей и фактические ограничения — в FRONTEND.md.

Перед будущим слиянием:

1. Обновить refs и проверить, появились ли новые изменения main после 559de32.
2. Согласовать и влить отдельные незавершённые интеграции в backend. Telegram
   из незакоммиченной рабочей копии и DSBA из отдельной ветки в эту подготовку
   не включены.
3. Выполнить проверки из README, включая PostgreSQL integration tests и
   `python -m scripts.export_openapi --check`.
4. После слияния deploy применит миграции до запуска приложения и wiki-worker.
   Нужны серверные HSE_DATABASE_URL/PG-параметры и OPENAI_API_KEY;
   токены в браузер не передаются. Данные main и preview остаются в разных БД.

Это подготовка backend, main данной работой не изменяется.
