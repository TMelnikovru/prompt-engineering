"""План дня по реальным заданиям: объяснимый, с учётом доступного времени и выполненного.

Приоритет и длительность — рекомендации, а не факты источника: длительность берётся
из задания, если она известна, иначе подставляется оценка по умолчанию и это сказано
в причине. Если всё не помещается, оставшиеся задания честно показываются отдельно.
"""

from datetime import date, datetime, time, timedelta

from app.api.schemas import DayPlan, PlanBlock, Task

DEFAULT_MINUTES = 60
HORIZON_DAYS = 14


def _due_day(task: Task) -> date | None:
    if task.due_at is not None:
        return task.due_at.date()
    return task.due_date


def _priority(days_left: int | None) -> str:
    if days_left is None:
        return "low"
    if days_left <= 2:
        return "high"
    if days_left <= 7:
        return "medium"
    return "low"


def _reason(task: Task, days_left: int | None, estimated: bool) -> str:
    if days_left is None:
        text = "Срок не указан в источнике: можно заняться, если останется время."
    elif days_left < 0:
        text = f"Срок прошёл {-days_left} дн. назад: проверь, можно ли ещё сдать."
    elif days_left == 0:
        text = "Срок сегодня."
    elif days_left == 1:
        text = "Срок завтра: лучше закончить сегодня."
    else:
        text = f"До срока {days_left} дн.: начни заранее."
    if task.deadline_changed:
        text += " Срок недавно менялся."
    if task.due_requires_confirmation:
        text += " Срок нужно уточнить."
    if estimated:
        text += f" Длительность — оценка {DEFAULT_MINUTES} мин, её можно поправить."
    return text


def build_plan(
    tasks: list[Task], now: datetime, available_minutes: int, completed: set[str]
) -> tuple[list[Task], DayPlan]:
    today = now.date()
    for task in tasks:
        if task.id in completed:
            task.status = "done"
        due = _due_day(task)
        task.priority = _priority((due - today).days if due else None)

    def urgency(task: Task):
        # Сначала то, что ещё можно сдать в срок; просроченное — после, свежее первым:
        # закрытый тест не должен вытеснять задание со сроком послезавтра.
        due = _due_day(task)
        if due is None:
            return (2, 0, task.id)
        if due < today:
            return (1, (today - due).days, task.id)
        return (
            0,
            (task.due_at or datetime.combine(due, time.max, now.tzinfo)).timestamp(),
            task.id,
        )

    candidates = [
        t
        for t in sorted(tasks, key=urgency)
        if t.status != "done"
        and (_due_day(t) is None or (_due_day(t) - today).days <= HORIZON_DAYS)
    ]
    # Ближайший пятиминутный слот; работу не переносим на завтра.
    cursor = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    midnight = datetime.combine(today + timedelta(days=1), time(), tzinfo=now.tzinfo)
    remaining = min(available_minutes, max(0, int((midnight - cursor).total_seconds() // 60)))
    blocks, unscheduled = [], []
    for task in candidates:
        minutes = task.estimated_minutes or DEFAULT_MINUTES
        if minutes <= remaining:
            due = _due_day(task)
            blocks.append(
                PlanBlock(
                    task_id=task.id,
                    starts_at=cursor,
                    minutes=minutes,
                    reason=_reason(
                        task, (due - today).days if due else None, not task.estimated_minutes
                    ),
                )
            )
            cursor += timedelta(minutes=minutes)
            remaining -= minutes
        else:
            unscheduled.append(task.id)

    if not candidates:
        note = "На ближайшие две недели заданий нет — план на сегодня пуст."
    else:
        note = (
            f"План на {available_minutes} мин рассчитан по срокам заданий из источников. "
            "Приоритеты и длительность — рекомендации."
        )
        if unscheduled:
            note += f" Не поместилось заданий: {len(unscheduled)}, они показаны отдельно."
    later = sum(
        1 for t in tasks if t.status != "done" and t not in candidates and _due_day(t) is not None
    )
    if later:
        note += f" Ещё {later} с более поздним сроком в план не включены."
    return tasks, DayPlan(
        date=today,
        available_minutes=available_minutes,
        blocks=blocks,
        unscheduled_task_ids=unscheduled,
        note=note,
    )
