import { fetchDashboard } from "./api.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { dashboard: null, filter: "all" };
const viewCopy = {
  overview: ["ФОКУС НА СЕГОДНЯ", "Понятно, с чего начать.", "Задания, материалы и план на день — в одном месте."],
  tasks: ["НИЧЕГО НЕ ПОТЕРЯЕТСЯ", "Всё, что предстоит.", "Сроки и подтверждающие источники под рукой."],
  sources: ["СОБИРАЕМ ОБЩУЮ КАРТИНУ", "Откуда приходят знания.", "Состояние подключений и актуальность учебных данных."],
};
const statusLabels = {
  not_connected: "Не подключён", connected: "Подключён", syncing: "Обновляется",
  error: "Ошибка подключения", stale: "Данные устарели", partial: "Загружено частично",
};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function dateKey(value) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: state.dashboard.meta.timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(value));
}

function clock(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: state.dashboard.meta.timezone, hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function deadlineDay(task) {
  return task.due_at ? dateKey(task.due_at) : task.due_date;
}

function deadlineLabel(task) {
  const day = deadlineDay(task);
  if (!day) return "Срок уточняется";
  const label = day === state.dashboard.plan.date
    ? "Сегодня"
    : new Intl.DateTimeFormat("ru-RU", {
      timeZone: "UTC", day: "numeric", month: "short",
    }).format(new Date(day + "T12:00:00Z"));
  return label + (task.due_at ? ", " + clock(task.due_at) : " · время не указано");
}

function sourceName(id) {
  return state.dashboard.sources.find((source) => source.id === id)?.name || id;
}

function evidenceLink(item) {
  if (!item.url) return node("span", "", sourceName(item.source_id) + " · " + item.title);
  let url;
  try { url = new URL(item.url); } catch { return node("span", "", item.title); }
  if (!["https:", "http:"].includes(url.protocol)) return node("span", "", item.title);
  const link = node("a", "", sourceName(item.source_id) + " · " + item.title);
  link.href = url.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function taskCard(task) {
  const item = node("li", "priority-" + task.priority);
  const details = node("details");
  const summary = node("summary", "task-summary");
  const marker = node("span", "task-marker");
  marker.setAttribute("aria-hidden", "true");
  const copy = node("span", "task-copy");
  copy.append(node("span", "task-course", task.course), node("span", "task-title", task.title));
  const meta = node("span", "task-meta");
  const due = node("span", "due-label", deadlineLabel(task));
  if (deadlineDay(task) === state.dashboard.plan.date) due.classList.add("due-today");
  meta.append(due);
  if (task.estimated_minutes) meta.append(node("span", "", "≈ " + task.estimated_minutes + " мин"));
  if (task.status === "done") meta.append(node("span", "", "Выполнено"));
  copy.append(meta);
  const chevron = node("span", "task-chevron", "›");
  chevron.setAttribute("aria-hidden", "true");
  summary.append(marker, copy, chevron);
  const evidence = node("div", "task-evidence");
  evidence.append(node("h3", "", "Источник и подтверждение"));
  for (const entry of task.evidence) {
    evidence.append(evidenceLink(entry), node("blockquote", "", entry.quote));
  }
  if (task.materials.length) {
    evidence.append(node("h3", "", "Материалы"));
    const materials = node("ul");
    materials.append(...task.materials.map((title) => node("li", "", title)));
    evidence.append(materials);
  }
  details.append(summary, evidence);
  item.append(details);
  return item;
}

function renderTasks() {
  const tasks = state.dashboard.tasks.filter((task) => {
    if (state.filter === "today") return deadlineDay(task) === state.dashboard.plan.date;
    if (state.filter === "no-deadline") return !deadlineDay(task);
    return true;
  });
  $("#task-list").replaceChildren(...tasks.map(taskCard));
  $("#task-count").textContent = String(tasks.length);
  $("#tasks-empty").hidden = tasks.length > 0;
  $("#tasks-empty-text").textContent = state.dashboard.tasks.length
    ? "Для выбранного фильтра заданий нет."
    : "Когда появятся данные из источников, здесь будут твои задания.";
}

function renderPlan() {
  const { plan, tasks } = state.dashboard;
  const minutes = plan.blocks.reduce((sum, block) => sum + block.minutes, 0);
  $("#stat-minutes").textContent = String(minutes);
  $("#stat-budget").textContent = "Из " + plan.available_minutes + " доступных минут";
  $("#plan-budget-label").textContent = minutes + " / " + plan.available_minutes + " мин";
  $("#plan-meter").max = Math.max(plan.available_minutes, minutes, 1);
  $("#plan-meter").value = minutes;
  $("#plan-note").textContent = plan.note;
  $("#plan-empty").hidden = plan.blocks.length > 0;
  $("#plan-list").replaceChildren(...plan.blocks.map((block, index) => {
    const item = node("li", "plan-item");
    const number = node("span", "step-number", String(index + 1).padStart(2, "0"));
    const copy = node("div");
    copy.append(
      node("time", "plan-time", clock(block.starts_at) + " · " + block.minutes + " мин"),
      node("h3", "", tasks.find((task) => task.id === block.task_id)?.title || "Учебная задача"),
      node("p", "plan-reason", block.reason),
    );
    item.append(number, copy);
    return item;
  }));
}

function renderSources() {
  const { sources } = state.dashboard;
  $("#source-total").textContent =
    sources.filter((source) => source.status === "connected").length + " из " + sources.length + " подключено";
  $("#source-list").replaceChildren(...sources.map((source) => {
    const item = node("li", "source-card");
    const header = node("div", "source-top");
    const avatar = node("span", "source-avatar", source.name.slice(0, 1));
    avatar.setAttribute("aria-hidden", "true");
    header.append(avatar, node("h3", "source-name", source.name));
    const status = node("span", "source-status");
    status.dataset.status = source.status;
    status.append(node("span", "status-dot"), node("span", "", statusLabels[source.status]));
    item.append(header, node("p", "source-description", source.description), status);
    if (source.last_synced_at) {
      item.append(node("span", "source-updated", "Обновлён " + clock(source.last_synced_at)));
    }
    if (source.error_message) item.append(node("p", "source-description", source.error_message));
    return item;
  }));
}

function showView() {
  const requested = location.hash.slice(1);
  const view = Object.hasOwn(viewCopy, requested) ? requested : "overview";
  const [eyebrow, title, description] = viewCopy[view];
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
  $$("[data-nav]").forEach((link) => {
    if (link.dataset.nav === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $$("[data-views]").forEach((section) => {
    section.hidden = !section.dataset.views.split(" ").includes(view);
  });
  $(".workspace-grid").classList.toggle("single-view", view !== "overview");
}

async function loadDashboard() {
  $("#refresh-button").disabled = true;
  $("#dashboard-content").hidden = true;
  $("#mode-notice").hidden = true;
  $("#load-state").hidden = false;
  $("#load-state").classList.remove("is-error");
  $("#retry-button").hidden = true;
  $("#load-title").textContent = "Загружаем учебный день";
  $("#load-description").textContent = "Сейчас здесь появятся задания и план.";
  try {
    const scenario = new URLSearchParams(location.search).get("scenario") || "default";
    state.dashboard = await fetchDashboard(scenario);
    const { meta, tasks, plan } = state.dashboard;
    $("#mode-notice-text").textContent = meta.notice;
    $("#mode-notice").hidden = !meta.notice;
    $("#stat-tasks").textContent = String(tasks.filter((task) => task.status !== "done").length);
    $("#nav-task-count").textContent = String(tasks.length);
    $("#stat-today").textContent = String(tasks.filter(
      (task) => task.status !== "done" && deadlineDay(task) === plan.date,
    ).length);
    $("#today-label").textContent = new Intl.DateTimeFormat("ru-RU", {
      timeZone: meta.timezone, day: "numeric", month: "long", weekday: "short",
    }).format(new Date(meta.generated_at));
    $("#today-label").dateTime = meta.generated_at;
    $("#updated-at").textContent = "Ответ получен в " + clock(meta.generated_at) + " · " + meta.timezone;
    renderTasks();
    renderPlan();
    renderSources();
    showView();
    $("#dashboard-content").hidden = false;
    $("#load-state").hidden = true;
  } catch (error) {
    $("#load-state").classList.add("is-error");
    $("#load-title").textContent = "Не получилось загрузить данные";
    $("#load-description").textContent = error.message;
    $("#retry-button").hidden = !error.retryable;
  } finally {
    $("#refresh-button").disabled = false;
  }
}

$$("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  $$("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  if (state.dashboard) renderTasks();
}));
$("#refresh-button").addEventListener("click", loadDashboard);
$("#retry-button").addEventListener("click", loadDashboard);
window.addEventListener("hashchange", showView);
showView();
loadDashboard();
