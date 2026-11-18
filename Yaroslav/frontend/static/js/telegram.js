let token = "";
const $ = (id) => document.getElementById(id);
const feedback = (text) => { $("feedback").textContent = text; };
const statusNames = {
  connected: "Подключён", syncing: "Синхронизация", stale: "Данные устарели",
  partial: "Загружено частично", error: "Ошибка подключения", not_connected: "Не подключён",
};
function node(tag, text) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  return result;
}
async function api(path, method = "GET") {
  const response = await fetch("/api/v1" + path, {
    method, headers: { "X-Ingestion-Token": token }, cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) {
    const code = data.error?.code || `HTTP ${response.status}`;
    const syncError = data.errors?.[0]?.code;
    throw new Error(syncError || code);
  }
  return data;
}
function citations(ids, sources) {
  const container = node("div");
  container.className = "telegram-citations";
  for (const id of ids || []) {
    const source = sources.get(id);
    if (!source) continue;
    try {
      const url = new URL(source.url);
      if (!["https:", "tg:"].includes(url.protocol)) continue;
      const link = node("a", source.title);
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      container.append(link);
    } catch { /* An invalid source URL is rendered without a link. */ }
  }
  return container;
}
function renderDigest(data) {
  const container = $("digest");
  container.replaceChildren();
  container.hidden = !data.summary;
  if (!data.summary) {
    feedback("Сводки пока нет. После настройки подключения прочитайте выбранные чаты.");
    return;
  }
  const digest = data.summary;
  const sources = new Map((digest.sources || []).map((source) => [source.message_id, source]));
  container.append(node("h2", "Общая картина"), node("p", digest.summary));
  const scope = digest.scope || {};
  const metadata = node("p", `Сообщений: ${scope.message_count || 0}. Период: ${scope.history_days || "—"} дн. `
    + (scope.coverage === "partial" ? "Показана часть переписки. " : "")
    + (data.stale ? "Сводка устарела: появились изменения или изменился период." : ""));
  metadata.className = "telegram-meta";
  container.append(metadata);
  for (const [key, title] of [
    ["topics", "Что обсуждают"], ["agreements", "О чём договорились"],
    ["assignments", "Задания и сроки"], ["open_questions", "Что осталось выяснить"],
    ["conflicts", "Противоречия и изменения"],
  ]) {
    if (!digest[key]?.length) continue;
    container.append(node("h3", title));
    const list = node("ul");
    for (const point of digest[key]) {
      const item = node("li");
      item.append(node("p", point.text || point.title));
      if (key === "assignments") {
        item.append(node("p", point.due_text ? `Срок из сообщения: ${point.due_text}` : "Срок не указан."));
        item.append(node("blockquote", point.evidence_quote));
      }
      item.append(citations(point.message_ids || [point.message_id], sources));
      list.append(item);
    }
    container.append(list);
  }
  container.append(node("p", "Это ИИ-сводка. Проверяйте условия и сроки по исходным сообщениям."));
}
async function load() {
  const [sources, digest] = await Promise.all([api("/sources"), api("/knowledge/telegram")]);
  const source = sources.find((item) => item.id === "telegram");
  const connection = $("connection");
  connection.replaceChildren(node("h2", "Подключение Telegram"));
  connection.append(node("p", source ? (statusNames[source.status] || source.status) : "Ещё не настроено"));
  if (source?.last_synced_at) connection.append(node("p", "Последнее успешное обновление: "
    + new Date(source.last_synced_at).toLocaleString("ru-RU", { timeZoneName: "short" })));
  if (source?.last_error) connection.append(node("p", `Ошибка: ${source.last_error}`));
  if (source?.last_run?.status === "partial") connection.append(node("p", "Прочитана часть данных; фоновая обработка продолжит загрузку."));
  connection.hidden = false;
  renderDigest(digest);
}
async function run(action) {
  for (const button of document.querySelectorAll("button")) button.disabled = true;
  try { await action(); } catch (error) { feedback(`Не удалось выполнить действие: ${error.message}`); }
  finally { for (const button of document.querySelectorAll("button")) button.disabled = false; }
}
$("access-form").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("access-token").value;
  $("access-token").value = "";
  run(async () => {
    feedback("Загружаю сохранённые данные…");
    await load();
    if (!$("digest").hidden) feedback("Сохранённая сводка загружена.");
    $("controls").hidden = false;
    $("access-form").hidden = true;
  });
});
$("reload").addEventListener("click", () => run(async () => { feedback(""); await load(); }));
$("refresh").addEventListener("click", () => run(async () => {
  feedback("Читаю выбранные чаты…");
  const sync = await api("/integrations/telegram/sync", "POST");
  feedback("Готовлю ИИ-сводку переписки…");
  await api("/knowledge/telegram/refresh", "POST");
  feedback(sync.has_more ? "Часть истории ещё загружается. Фоновый процесс продолжит чтение." : "Сводка обновлена.");
  await load();
}));
$("logout").addEventListener("click", () => {
  token = "";
  $("controls").hidden = true;
  $("access-form").hidden = false;
  $("digest").replaceChildren();
  $("connection").replaceChildren();
  $("digest").hidden = $("connection").hidden = true;
  feedback("Доступ закрыт.");
});
