from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.api.schemas import (
    Dashboard,
    DashboardMeta,
    DayPlan,
    Evidence,
    PlanBlock,
    PreviewScenario,
    Source,
    Task,
)
from app.config import Settings
from app.services.source_catalog import SOURCE_CATALOG


def get_dashboard(settings: Settings, scenario: PreviewScenario) -> Dashboard:
    now = datetime.now(ZoneInfo(settings.timezone))
    today = now.date()
    sources = [
        Source(id=key, name=name, description=description, status="not_connected")
        for key, name, description in SOURCE_CATALOG
    ]
    demo = settings.demo_mode
    result = Dashboard(
        meta=DashboardMeta(
            mode="demo" if demo else "unconfigured",
            generated_at=now,
            timezone=settings.timezone,
            notice=(
                "Учебные примеры для разработки интерфейса. Источники ещё не подключены."
                if demo
                else "Источники ещё не подключены. Реальные задания появятся после настройки."
            ),
        ),
        sources=sources,
        tasks=[],
        plan=DayPlan(
            date=today,
            available_minutes=120,
            note="План появится, когда будут доступны задания.",
        ),
    )
    if not demo or scenario == PreviewScenario.EMPTY:
        return result

    def moment(offset: int, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(
            today + timedelta(days=offset), time(hour, minute), tzinfo=now.tzinfo
        )

    result.tasks = [
        Task(
            id="demo-statistics",
            title="Решить задачи на проверку гипотез",
            course="Математическая статистика",
            priority="high",
            due_at=moment(0, 23, 0),
            estimated_minutes=60,
            materials=["Семинар 6. Проверка гипотез", "Задачи 1–4 из учебного примера"],
            evidence=[
                Evidence(
                    source_id="smartlms",
                    title="Учебный пример задания",
                    quote="Решите задачи 1–4. Срок сдачи — сегодня, 23:00.",
                )
            ],
        ),
        Task(
            id="demo-algorithms",
            title="Подготовить разбор обхода графа",
            course="Алгоритмы и структуры данных",
            priority="medium",
            due_at=moment(1, 18, 0),
            estimated_minutes=45,
            materials=["Конспект по BFS и DFS"],
            evidence=[
                Evidence(
                    source_id="mail",
                    title="Учебный пример письма",
                    quote="Подготовьте разбор BFS и DFS к завтрашнему семинару, 18:00.",
                )
            ],
        ),
        Task(
            id="demo-project",
            title="Обсудить план учебного проекта",
            course="Программный проект",
            priority="medium",
            due_date=today + timedelta(days=3),
            estimated_minutes=30,
            evidence=[
                Evidence(
                    source_id="telegram",
                    title="Учебный пример сообщения",
                    quote="Встречаемся через три дня. Время ещё согласуем.",
                )
            ],
        ),
        Task(
            id="demo-reading",
            title="Прочитать материал о регуляризации",
            course="Машинное обучение",
            priority="low",
            estimated_minutes=40,
            evidence=[
                Evidence(
                    source_id="wiki",
                    title="Учебный пример страницы курса",
                    quote="Дополнительное чтение: регуляризация линейных моделей. Срок не указан.",
                )
            ],
        ),
    ]
    result.plan.blocks = [
        PlanBlock(
            task_id="demo-statistics",
            starts_at=moment(0, 16, 0),
            minutes=60,
            reason="Сдать нужно сегодня до 23:00, это ближайший срок.",
        ),
        PlanBlock(
            task_id="demo-algorithms",
            starts_at=moment(0, 17, 15),
            minutes=45,
            reason="Семинар завтра в 18:00, сегодня есть время начать.",
        ),
    ]
    result.plan.unscheduled_task_ids = ["demo-project", "demo-reading"]
    result.plan.note = (
        "Учебный пример плана: 105 минут работы и 15 минут запаса. "
        "Это подготовленные данные, а не результат работы ИИ."
    )
    return result
