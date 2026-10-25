"""Stable frontend source IDs and presentation shared by both read endpoints."""

SOURCE_CATALOG = (
    ("smartlms", "SmartLMS", "Задания, объявления и материалы курсов"),
    ("mail", "Почта", "Письма преподавателей и университетские рассылки"),
    ("ruz", "РУЗ", "Расписание занятий и экзаменов"),
    ("elk", "ЕЛК", "Личный кабинет студента"),
    ("telegram", "Telegram", "Выбранные учебные чаты и каналы"),
    ("wiki", "Вики ФКН", "Страницы курсов и учебные материалы"),
    ("dsba", "DSBA Online", "Материалы и дедлайны выбранной программы и группы"),
)


def source_description(kind: str) -> str:
    return next(
        (description for key, _, description in SOURCE_CATALOG if key == kind),
        "Материалы из подключённого источника",
    )


def source_error_message(code: str | None) -> str | None:
    if code is None:
        return None
    if code == "dsba_record_limit":
        return "Получены не все записи: достигнут настроенный лимит."
    if code.endswith("_timeout"):
        return "Источник долго не отвечает. Повторим попытку автоматически."
    if code.endswith("_network_error"):
        return "Не удалось связаться с источником. Повторим попытку автоматически."
    return "Не удалось обновить источник. Сохранённые материалы остаются доступны."
