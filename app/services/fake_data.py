"""Explicit, isolated sample data; never ingested into the university source store."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.api.schemas import (
    Dashboard,
    DashboardMeta,
    DayPlan,
    Evidence,
    Material,
    PlanBlock,
    Source,
    Task,
)
from app.config import Settings

FAKE_MATERIALS = {
    "statistics": (
        "Семинар · Проверка гипотез",
        "Математическая статистика",
        "Сформулируйте нулевую и альтернативную гипотезы. Выберите статистику критерия, "
        "проверьте предпосылки и сравните p-value с уровнем значимости 0,05. "
        "В учебном решении объясните, почему результат не доказывает истинность гипотезы.",
    ),
    "graphs": (
        "Конспект · BFS и DFS",
        "Алгоритмы и структуры данных",
        "BFS использует очередь и находит кратчайшие пути в невзвешенном графе. "
        "DFS использует стек или рекурсию. Для списка смежности оба обхода занимают O(V + E). "
        "Разберите обход графа с рёбрами A–B, A–C, B–D и C–D.",
    ),
    "project": (
        "План учебного проекта",
        "Программный проект",
        "Подготовьте описание задачи, распределите роли и выберите один сквозной сценарий. "
        "Для демонстрации запишите входные данные, ожидаемый результат и критерии готовности. "
        "Этот текст — вымышленное условие, а не требование преподавателя.",
    ),
    "regularization": (
        "Заметки · Регуляризация",
        "Машинное обучение",
        "L2-регуляризация добавляет к функции потерь штраф за сумму квадратов весов. "
        "L1 использует сумму модулей и может обнулять коэффициенты. "
        "Параметр регуляризации выбирают на валидационных данных, не на тестовой выборке.",
    ),
}


def get_fake_dashboard(
    settings: Settings,
    available_minutes: int = 120,
    completed: set[str] | None = None,
    *,
    now: datetime | None = None,
) -> Dashboard:
    now = (now or datetime.now(ZoneInfo(settings.timezone))).astimezone(ZoneInfo(settings.timezone))
    today = now.date()
    prefix = f"fake-{today.isoformat()}-"

    def moment(offset: int, hour: int) -> datetime:
        return datetime.combine(today + timedelta(days=offset), time(hour), tzinfo=now.tzinfo)

    def evidence(slug: str, quote: str, *, offset: int = -1) -> list[Evidence]:
        return [
            Evidence(
                source_id="fake",
                title="Фейк-данные · пример условия",
                quote=quote,
                url=f"/demo/materials/{slug}",
                external_id=prefix + slug,
                published_at=moment(offset, 10),
                fetched_at=now,
            )
        ]

    tasks = [
        Task(
            id=prefix + "report",
            title="Дополнить выводы к лабораторной работе",
            course="Математическая статистика",
            priority="high",
            due_at=moment(-1, 20),
            estimated_minutes=20,
            materials=[FAKE_MATERIALS["statistics"][0]],
            evidence=evidence(
                "statistics", "Учебный пример: дополните выводы. Срок был вчера, 20:00."
            ),
        ),
        Task(
            id=prefix + "statistics",
            title="Решить задачи на проверку гипотез",
            course="Математическая статистика",
            priority="high",
            due_at=moment(0, 23),
            estimated_minutes=45,
            materials=[FAKE_MATERIALS["statistics"][0]],
            evidence=evidence("statistics", "Учебный пример: решите задачи 1–4 сегодня до 23:00."),
        ),
        Task(
            id=prefix + "graphs",
            title="Подготовить разбор BFS и DFS",
            course="Алгоритмы и структуры данных",
            priority="medium",
            due_at=moment(1, 18),
            previous_due_at=moment(0, 18),
            deadline_changed="Учебный пример: срок перенесён с сегодня на завтра, 18:00.",
            estimated_minutes=40,
            materials=[FAKE_MATERIALS["graphs"][0]],
            evidence=evidence(
                "graphs",
                "Учебный пример обновления: вместо сегодня сдайте завтра до 18:00.",
                offset=0,
            ),
        ),
        Task(
            id=prefix + "project",
            title="Согласовать этапы учебного проекта",
            course="Программный проект",
            priority="medium",
            due_date=today + timedelta(days=3),
            estimated_minutes=30,
            materials=[FAKE_MATERIALS["project"][0]],
            evidence=evidence(
                "project", "Учебный пример: согласуйте этапы через три дня. Время не указано."
            ),
        ),
        Task(
            id=prefix + "reading",
            title="Прочитать заметки о регуляризации",
            course="Машинное обучение",
            priority="low",
            estimated_minutes=25,
            materials=[FAKE_MATERIALS["regularization"][0]],
            evidence=evidence(
                "regularization", "Учебный пример: дополнительное чтение. Срок не указан."
            ),
        ),
        Task(
            id=prefix + "questions",
            title="Уточнить срок защиты проекта",
            course="Программный проект",
            priority="medium",
            estimated_minutes=15,
            due_requires_confirmation=True,
            evidence=evidence(
                "project",
                "Учебный пример, первая версия: защита "
                f"{today + timedelta(days=2):%d.%m.%Y}, время не указано.",
            )
            + [
                Evidence(
                    source_id="fake",
                    title="Фейк-данные · противоречащая версия",
                    quote="Учебный пример, вторая версия без пояснения о переносе: защита "
                    f"{today + timedelta(days=3):%d.%m.%Y}, время не указано.",
                    url="/demo/materials/project",
                    published_at=moment(-1, 12),
                    fetched_at=now,
                    external_id=prefix + "project-conflict",
                )
            ],
        ),
        Task(
            id=prefix + "done",
            title="Повторить определения графа и пути",
            course="Алгоритмы и структуры данных",
            priority="low",
            status="done",
            due_date=today,
            estimated_minutes=15,
            materials=[FAKE_MATERIALS["graphs"][0]],
            evidence=evidence("graphs", "Учебный пример уже выполненного задания."),
        ),
    ]
    for task in tasks:
        if task.id in (completed or set()):
            task.status = "done"

    # Round up to the next five-minute slot; never schedule work into tomorrow.
    cursor = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    midnight = datetime.combine(today + timedelta(days=1), time(), tzinfo=now.tzinfo)
    remaining = min(available_minutes, max(0, int((midnight - cursor).total_seconds() // 60)))
    blocks = []
    unscheduled = []
    reasons = {
        "report": "Срок уже прошёл: сначала закрой короткую просроченную работу.",
        "statistics": "Срок сегодня вечером; оставь время проверить решение.",
        "graphs": "Срок перенесён на завтра; подготовку можно начать сегодня.",
        "project": "Начни проект заранее, чтобы согласовать работу с командой.",
        "reading": "Дополнительное чтение без указанного срока.",
        "questions": "Уточни условия, прежде чем планировать защиту.",
    }
    for task in tasks:
        if task.status == "done":
            continue
        minutes = task.estimated_minutes
        if minutes <= remaining:
            blocks.append(
                PlanBlock(
                    task_id=task.id,
                    starts_at=cursor,
                    minutes=minutes,
                    reason=reasons[task.id.removeprefix(prefix)],
                )
            )
            cursor += timedelta(minutes=minutes)
            remaining -= minutes
        else:
            unscheduled.append(task.id)
    note = "Пример плана рассчитан по фейк-данным. Приоритеты и длительность можно изменить."
    if unscheduled:
        note += " Все задания не помещаются в доступное время; оставшиеся показаны отдельно."
    if cursor.date() != today or available_minutes > max(
        0, int((midnight - now).total_seconds() // 60)
    ):
        note += " Время ограничено концом сегодняшнего дня."
    return Dashboard(
        meta=DashboardMeta(
            mode="demo",
            generated_at=now,
            timezone=settings.timezone,
            notice="Фейк-данные: все задания, сроки и материалы вымышлены. "
            "Это демонстрация возможностей, а не данные университетских сервисов.",
        ),
        sources=[
            Source(
                id="fake",
                name="Фейк-данные",
                status="connected",
                last_synced_at=now,
                description="Вымышленные задания и материалы для знакомства с сайтом. "
                "Обновляются относительно сегодняшней даты; реальные сервисы не подключаются.",
            )
        ],
        tasks=tasks,
        plan=DayPlan(
            date=today,
            available_minutes=available_minutes,
            blocks=blocks,
            unscheduled_task_ids=unscheduled,
            note=note,
        ),
        materials=[
            Material(
                id="fake-" + slug,
                title=title,
                course=course,
                source_id="fake",
                url=f"/demo/materials/{slug}",
                fetched_at=now,
            )
            for slug, (title, course, _) in FAKE_MATERIALS.items()
        ],
    )
