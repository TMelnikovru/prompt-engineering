from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.api.schemas import Evidence, Task
from app.services.planner import DEFAULT_MINUTES, build_plan

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 18, 10, 2, tzinfo=MSK)


def task(task_id, due_at=None, due_date=None, minutes=None, **extra):
    return Task(
        id=task_id,
        title=task_id,
        course="Курс",
        priority="medium",
        due_at=due_at,
        due_date=due_date,
        estimated_minutes=minutes,
        evidence=[
            Evidence(
                source_id="smartlms",
                title=task_id,
                quote="Срок",
                url="https://edu.hse.ru",
                fetched_at=NOW,
            )
        ],
        **extra,
    )


def test_urgent_first_with_reasons_and_default_estimate():
    tasks = [
        task("later", due_at=NOW + timedelta(days=5)),
        task("tomorrow", due_at=NOW + timedelta(days=1), minutes=30),
        task("overdue", due_at=NOW - timedelta(days=2)),
        task("no-date"),
    ]
    tasks, plan = build_plan(tasks, NOW, 240, set())
    assert [b.task_id for b in plan.blocks] == ["tomorrow", "later", "overdue", "no-date"]
    first, second, third = plan.blocks[:3]
    assert first.starts_at == datetime(2026, 11, 18, 10, 5, tzinfo=MSK)
    assert "завтра" in first.reason and "оценка" not in first.reason
    assert second.starts_at == first.starts_at + timedelta(minutes=30)
    assert "прошёл 2 дн." in third.reason and f"оценка {DEFAULT_MINUTES} мин" in third.reason
    priorities = {t.id: t.priority for t in tasks}
    assert priorities == {
        "later": "medium",
        "tomorrow": "high",
        "overdue": "high",
        "no-date": "low",
    }


def test_overdue_task_does_not_push_out_an_upcoming_deadline():
    tasks = [
        task("closed-quiz", due_at=NOW - timedelta(days=4)),
        task("soon", due_at=NOW + timedelta(days=2)),
    ]
    _, plan = build_plan(tasks, NOW, 60, set())
    assert [b.task_id for b in plan.blocks] == ["soon"]
    assert plan.unscheduled_task_ids == ["closed-quiz"]


def test_overload_is_shown_instead_of_impossible_schedule():
    tasks = [task(f"t{i}", due_at=NOW + timedelta(days=i)) for i in range(4)]
    _, plan = build_plan(tasks, NOW, 120, set())
    assert [b.task_id for b in plan.blocks] == ["t0", "t1"]
    assert plan.unscheduled_task_ids == ["t2", "t3"]
    assert "Не поместилось заданий: 2" in plan.note


def test_completed_tasks_and_far_deadlines_are_not_planned():
    tasks = [
        task("done", due_at=NOW + timedelta(days=1)),
        task("far", due_date=date(2027, 1, 30)),
        task("soon", due_date=date(2026, 11, 20)),
    ]
    tasks, plan = build_plan(tasks, NOW, 300, {"done"})
    assert [b.task_id for b in plan.blocks] == ["soon"]
    assert next(t for t in tasks if t.id == "done").status == "done"
    assert "Ещё 1 с более поздним сроком" in plan.note


def test_plan_never_runs_past_midnight():
    late = NOW.replace(hour=23, minute=20)
    _, plan = build_plan([task("a", due_at=late + timedelta(hours=5))], late, 300, set())
    assert plan.blocks == [] and plan.unscheduled_task_ids == ["a"]
