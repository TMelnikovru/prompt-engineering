// The same status values are used by dashboard.sources and GET /api/v1/sources.
const labels = Object.freeze({
  not_connected: "Не подключён",
  connected: "Подключён",
  syncing: "Обновляется",
  partial: "Загружено частично",
  error: "Ошибка подключения",
  stale: "Данные устарели",
});

export function sourceStatusLabel(status) {
  return Object.hasOwn(labels, status) ? labels[status] : "Статус неизвестен";
}

export function sourceNeedsAttention(status) {
  return ["partial", "error", "stale"].includes(status);
}
