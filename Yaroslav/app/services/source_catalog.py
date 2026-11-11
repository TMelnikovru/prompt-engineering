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
    if code == "smartlms_not_configured":
        return "SmartLMS не настроен на сервере: нужен ключ со страницы «Ключи безопасности»."
    if code == "smartlms_auth_failed":
        return "SmartLMS не принял ключ. Возьмите новый на странице «Ключи безопасности»."
    if code == "mail_not_configured":
        return "Почта не настроена на сервере: нужны адрес и пароль приложения."
    if code == "mail_oauth_invalid":
        return "Токен Яндекса истёк или отозван. Выполните вход заново: python -m app.mail login."
    if code == "mail_auth_failed":
        return "Почта отклонила вход. Проверьте адрес и пароль приложения Яндекс 360."
    if code == "dsba_record_limit":
        return "Получены не все записи: достигнут настроенный лимит."
    if code.endswith("_timeout"):
        return "Источник долго не отвечает. Повторим попытку автоматически."
    if code.endswith("_network_error"):
        return "Не удалось связаться с источником. Повторим попытку автоматически."
    return "Не удалось обновить источник. Сохранённые материалы остаются доступны."
