import { fetchDashboard } from "./api.js";
import { sourceStatusLabel, sourceNeedsAttention } from "./source-status.js";
import { savedDsbaSelection, savedDataSource, setupDsba } from "./dsba.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const STORE = "hse-agent:";
const MAIN_VIEWS = ["today", "tasks", "materials", "grades", "settings", "sources", "account"];
const priorityLabels = { high: "высокий", medium: "средний", low: "низкий" };

const state = {
  dashboard: null,
  dsbaSourceId: savedDsbaSelection()?.source_id || null,
  dataSource: savedDataSource(),
  filter: "all",
  course: "",
  materials: { query: "", course: "", today: false },
  view: null,
  loading: false,
  reloadRequested: false,
  changes: load("changes", {}),
  fresh: new Set(),
  done: new Set(load("done", [])),
  overrides: load("overrides", {}),
  settings: load("settings", null),
};
const splashStarted = performance.now();

/* ---------- Хранилище браузера ---------- */
function load(key, fallback) {
  try {
    const raw = localStorage.getItem(STORE + key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function save(key, value) {
  try { localStorage.setItem(STORE + key, JSON.stringify(value)); } catch { /* приватный режим */ }
}

/* ---------- Форматирование ---------- */
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "/static/icons.svg#" + name);
  svg.append(use);
  return svg;
}

const tz = () => state.dashboard.meta.timezone;

function dateKey(value) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: tz(), year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(value));
}

function clock(value) {
  return new Intl.DateTimeFormat("ru-RU", { timeZone: tz(), hour: "2-digit", minute: "2-digit" })
    .format(new Date(value));
}

function zoneOffset() {
  const part = new Intl.DateTimeFormat("ru-RU", { timeZone: tz(), timeZoneName: "shortOffset" })
    .formatToParts(new Date()).find((item) => item.type === "timeZoneName");
  return part ? part.value : "";
}

function dayMonth(day, month = "short") {
  return new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", day: "numeric", month })
    .format(new Date(day + "T12:00:00Z")).replace(".", "");
}

function daysBetween(from, to) {
  return Math.round((new Date(to + "T12:00:00Z") - new Date(from + "T12:00:00Z")) / 86400000);
}

function plural(count, one, few, many) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function duration(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return rest + " мин";
  return hours + " ч" + (rest ? " " + rest + " мин" : "");
}

/* Срок: точное время, только дата или неизвестно. Дату без времени не превращаем в 23:59. */
function deadline(task) {
  if (task.due_requires_confirmation) return { kind: "unknown", day: null, time: null };
  if (task.due_at) return { kind: "exact", day: dateKey(task.due_at), time: clock(task.due_at) };
  if (task.due_date) return { kind: "date", day: task.due_date, time: null };
  return { kind: "unknown", day: null, time: null };
}

function deadlineSignature(task) {
  return task.due_at || task.due_date || "";
}

function deadlineText(task, { long = false } = {}) {
  if (task.due_requires_confirmation) return "Срок требует уточнения";
  const due = deadline(task);
  if (due.kind === "unknown") return "Срок не указан";
  const today = state.dashboard.plan.date;
  const offset = daysBetween(today, due.day);
  let day = dayMonth(due.day, long ? "long" : "short");
  if (offset === 0) day = "Сегодня";
  else if (offset === 1) day = "Завтра";
  else if (offset < 0) day = "Просрочено, " + day;
  return day + (due.time ? ", " + due.time : long ? ", время не указано" : " · время не указано");
}

function signatureText(signature) {
  if (!signature) return "срок не указан";
  if (signature.includes("T")) return dayMonth(dateKey(signature)) + ", " + clock(signature);
  return dayMonth(signature);
}

function sourceName(id) {
  return state.dashboard.sources.find((source) => source.id === id)?.name || id;
}

const isDone = (task) => task.status === "done" || state.done.has(task.id);
const isToday = (task) => deadline(task).day === state.dashboard.plan.date;
const taskById = (id) => state.dashboard.tasks.find((task) => task.id === id);

function compareTasks(a, b) {
  const da = deadline(a);
  const db = deadline(b);
  if (!da.day && !db.day) return a.title.localeCompare(b.title, "ru");
  if (!da.day) return 1;
  if (!db.day) return -1;
  return (a.due_at || a.due_date + "T23:59:59").localeCompare(b.due_at || b.due_date + "T23:59:59");
}

/* ---------- Перенос срока: история из API или сравнение с прошлой загрузкой ---------- */
function detectChanges(tasks) {
  const known = load("deadlines", {});
  const now = Date.now();
  state.fresh = new Set();
  for (const task of tasks) {
    const current = deadlineSignature(task);
    const previous = task.previous_due_at || task.previous_due_date || known[task.id];
    if (previous !== undefined && previous !== current) {
      const stored = state.changes[task.id];
      if (!stored || stored.to !== current) {
        state.changes[task.id] = { from: previous, to: current, at: now };
        state.fresh.add(task.id);
      }
    }
    known[task.id] = current;
  }
  for (const [id, change] of Object.entries(state.changes)) {
    if (now - change.at > 3 * 86400000) delete state.changes[id];
  }
  save("deadlines", known);
  save("changes", state.changes);
  for (const id of state.fresh) {
    const task = taskById(id);
    const change = state.changes[id];
    toast("Срок изменился", task.title + ": " + signatureText(change.from) + " → " + signatureText(change.to));
  }
}

function changeFor(task) {
  const change = state.changes[task.id];
  return change && change.to === deadlineSignature(task) ? change : null;
}

/* ---------- Анимации ---------- */
const animate = () => !reducedMotion.matches;

/* FLIP: элементы плавно переезжают на новые места после пересчёта. */
function snapshot(list) {
  return new Map($$("[data-key]", list).map((item) => [item.dataset.key, item.getBoundingClientRect()]));
}

function flip(list, before) {
  if (!animate() || !before.size) return;
  for (const item of $$("[data-key]", list)) {
    const old = before.get(item.dataset.key);
    if (!old) continue;
    const now = item.getBoundingClientRect();
    const dy = old.top - now.top;
    if (Math.abs(dy) < 2) continue;
    item.animate(
      [{ transform: `translateY(${dy}px)` }, { transform: "none" }],
      { duration: 600, easing: "cubic-bezier(.22, 1, .36, 1)" },
    );
  }
}

function stagger(items) {
  items.forEach((item, index) => {
    item.style.setProperty("--i", Math.min(index, 10));
    item.classList.remove("reveal");
    void item.offsetWidth;
    item.classList.add("reveal");
  });
}

function toast(title, text, tone = "info") {
  const item = node("div", "toast");
  item.dataset.tone = tone;
  const mark = node("span", "toast-mark");
  mark.setAttribute("aria-hidden", "true");
  const copy = node("div");
  copy.append(node("strong", "", title));
  if (text) copy.append(node("span", "", text));
  item.append(mark, copy);
  $("#toasts").append(item);
  setTimeout(() => {
    item.classList.add("is-leaving");
    item.addEventListener("animationend", () => item.remove(), { once: true });
    if (!animate()) item.remove();
  }, 5200);
}

/* ---------- Сегодня ---------- */
function renderHero() {
  const { plan, tasks } = state.dashboard;
  const planned = plan.blocks.reduce((sum, block) => sum + block.minutes, 0);
  const unscheduled = plan.unscheduled_task_ids.map(taskById).filter((task) => task && !isDone(task));
  const dueToday = tasks.filter((task) => !isDone(task) && isToday(task)).sort(compareTasks);
  const steps = plan.blocks.length;

  let eyebrow = "Вааась, не кипишуй";
  let title = "Сегодня " + steps + " " + plural(steps, "задание", "задания", "заданий") + " на " + duration(planned);
  if (!tasks.length) {
    eyebrow = "Вааась, учёба началась";
    title = "Пока пусто: задания придут из источников";
  } else if (!steps) {
    title = "На сегодня в плане ничего нет";
  } else if (unscheduled.length) {
    eyebrow = "Вааась, сегодня плотно";
  }
  $("#hero-eyebrow").textContent = eyebrow;
  $("#hero-title").textContent = title;

  const facts = [];
  const next = dueToday[0] || tasks.filter((task) => !isDone(task) && deadline(task).day).sort(compareTasks)[0];
  if (next) {
    const due = deadline(next);
    const when = isToday(next) ? (due.time ? "до " + due.time : "сегодня") : deadlineText(next).toLowerCase();
    facts.push(["Ближайшая сдача", next.course + ", " + when, isToday(next)]);
  }
  if (tasks.length) {
    const reserve = plan.available_minutes - planned;
    facts.push(reserve >= 0
      ? ["В запасе", duration(reserve) + " из " + duration(plan.available_minutes)]
      : ["Не хватает", duration(-reserve), true]);
    const week = tasks.filter((task) => {
      const day = deadline(task).day;
      return !isDone(task) && day && daysBetween(plan.date, day) >= 0 && daysBetween(plan.date, day) <= 7;
    });
    if (week.length) {
      const byDay = new Map();
      for (const task of week) byDay.set(deadline(task).day, (byDay.get(deadline(task).day) || 0) + 1);
      const [peak, count] = [...byDay].sort((a, b) => b[1] - a[1])[0];
      const text = week.length + " " + plural(week.length, "сдача", "сдачи", "сдач");
      facts.push(["На этой неделе", count > 1 ? text + ", больше всего " + weekdayIn(peak) : text]);
    }
    const done = tasks.filter(isDone).length;
    facts.push(["Выполнено", done + " из " + tasks.length]);
  }
  $("#hero-facts").replaceChildren(...facts.map(([label, value, hot]) => {
    const item = node("div", "fact");
    const dd = node("dd", hot ? "is-hot" : "", value);
    item.append(node("dt", "", label), dd);
    return item;
  }));
}

function weekdayIn(day) {
  const names = ["в воскресенье", "в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу"];
  return names[new Date(day + "T12:00:00Z").getUTCDay()];
}

function materialList(task) {
  const list = node("ul", "material-list");
  for (const title of task.materials) {
    const item = node("li", "material");
    item.append(icon("doc"), document.createTextNode(title));
    list.append(item);
  }
  return list;
}

function renderPlan() {
  const { plan } = state.dashboard;
  const list = $("#plan-list");
  const before = snapshot(list);
  const planned = plan.blocks.reduce((sum, block) => sum + block.minutes, 0);

  const fill = $("#budget-fill");
  const ratio = plan.available_minutes ? planned / plan.available_minutes : 0;
  requestAnimationFrame(() => { fill.style.width = Math.min(ratio, 1) * 100 + "%"; });
  fill.classList.toggle("is-over", ratio > 1);

  list.replaceChildren(...plan.blocks.map((block) => {
    const task = taskById(block.task_id);
    const item = node("li", "plan-item");
    item.dataset.key = block.task_id;
    if (task && isToday(task)) item.classList.add("is-due-today");
    if (task && state.fresh.has(task.id)) item.classList.add("just-changed");

    const time = node("div", "plan-time");
    time.append(node("strong", "", clock(block.starts_at)), node("span", "", block.minutes + " мин"));
    const rail = node("div", "plan-rail");
    rail.setAttribute("aria-hidden", "true");
    rail.append(node("span", "plan-node"), node("span", "plan-line"));

    const card = node("div", "plan-card");
    const course = node("span", "plan-course", task ? task.course + " · " + deadlineText(task).toLowerCase() : "");
    if (task && changeFor(task)) course.append(node("span", "changed-chip", "срок перенесён"));
    if (task && isDone(task)) course.append(node("span", "chip chip-done", "готово"));
    card.append(course, node("strong", "plan-title", task?.title || "Учебная задача"));
    card.append(node("p", "plan-reason", block.reason));
    if (task?.materials.length) card.append(materialList(task));
    item.append(time, rail, card);
    return item;
  }));
  flip(list, before);
  $("#plan-empty").hidden = plan.blocks.length > 0;

  const unscheduled = plan.unscheduled_task_ids.map(taskById).filter((task) => task && !isDone(task));
  $("#overload").hidden = !unscheduled.length;
  if (unscheduled.length) {
    const count = unscheduled.length;
    $("#overload-title").textContent = "За пределами плана на сегодня: " + count + " " + plural(count, "задание", "задания", "заданий");
    $("#overload-text").textContent = "Агент перенёс их на потом. Добавь время или выбери главное.";
    $("#overload-list").replaceChildren(...unscheduled.map((task) => {
      const item = node("li");
      const minutes = minutesFor(task);
      item.append(node("strong", "", task.title), node("span", "", (minutes ? "≈ " + minutes + " мин · " : "") + deadlineText(task)));
      return item;
    }));
  }
  $("#plan-note").textContent = plan.note;
  $("#plan-note").hidden = !plan.note;
}

function dateTile(task) {
  const due = deadline(task);
  const tile = node("span", "date-tile");
  tile.setAttribute("aria-hidden", "true");
  if (isDone(task)) {
    tile.classList.add("is-done");
    tile.append(icon("check"));
  } else if (due.kind === "unknown") {
    tile.classList.add("is-unknown");
    tile.append(node("strong", "", "?"));
  } else if (due.day === state.dashboard.plan.date) {
    tile.classList.add("is-today");
    tile.append(node("strong", "", "СЕГОДНЯ"), node("span", "", due.time || "весь день"));
  } else if (daysBetween(state.dashboard.plan.date, due.day) < 0) {
    tile.classList.add("is-overdue");
    tile.append(node("strong", "", "СРОК"), node("span", "", "прошёл"));
  } else if (daysBetween(state.dashboard.plan.date, due.day) === 1) {
    tile.classList.add("is-tomorrow");
    tile.append(node("strong", "", "ЗАВТРА"), node("span", "", due.time || "весь день"));
  } else {
    const [, , day] = due.day.split("-");
    tile.append(node("strong", "", String(Number(day))), node("span", "", dayMonth(due.day).split(" ")[1] || ""));
  }
  return tile;
}

function renderDeadlines() {
  const list = $("#deadline-list");
  const before = snapshot(list);
  const tasks = state.dashboard.tasks.filter((task) => !isDone(task)).sort(compareTasks).slice(0, 6);
  list.replaceChildren(...tasks.map((task) => {
    const item = node("li", "deadline-item");
    item.dataset.key = task.id;
    if (state.fresh.has(task.id)) item.classList.add("just-changed");
    const copy = node("span", "deadline-copy");
    const due = deadline(task);
    const parts = [task.course];
    if (due.kind === "exact" && !isToday(task)) parts.push(due.time);
    if (due.kind === "date") parts.push("время не указано");
    if (due.kind === "unknown") parts.push("срок не указан в источнике");
    if (task.evidence[0]) parts.push(sourceName(task.evidence[0].source_id));
    const meta = node("span", "", parts.join(" · "));
    if (changeFor(task)) meta.prepend(node("span", "changed-chip", "перенесён"), document.createTextNode(" "));
    copy.append(node("strong", "", task.title), meta);
    item.append(dateTile(task), copy);
    return item;
  }));
  flip(list, before);
  $("#deadlines-empty").hidden = tasks.length > 0;
}

function sourceStatusText(source) {
  if (source.status === "connected" && source.last_synced_at) return "обновлён в " + clock(source.last_synced_at);
  if (source.status === "error") return source.error_message || "ошибка подключения";
  if (source.status === "stale" && source.last_synced_at) return "устарели · " + clock(source.last_synced_at);
  return sourceStatusLabel(source.status).toLowerCase();
}

function renderStrip() {
  const { sources } = state.dashboard;
  const connected = sources.filter((source) => source.status === "connected").length;
  $("#strip-summary").textContent = "Работают " + connected + " из " + sources.length;
  $("#source-strip").replaceChildren(...sources.map((source) => {
    const item = node("li", "source-chip");
    item.dataset.status = source.status;
    const name = node("strong");
    const dot = node("span", "status-dot");
    dot.setAttribute("aria-hidden", "true");
    name.append(dot, document.createTextNode(source.name));
    item.append(name, node("span", "", sourceStatusText(source)));
    return item;
  }));
}

/* ---------- Задания ---------- */
function minutesFor(task) {
  return state.overrides[task.id]?.minutes ?? task.estimated_minutes;
}

function priorityFor(task) {
  return state.overrides[task.id]?.priority ?? task.priority;
}

function setOverride(task, patch) {
  state.overrides[task.id] = { ...state.overrides[task.id], ...patch };
  save("overrides", state.overrides);
  toast("Браузер запомнил правку", "Сервер начнёт её учитывать вместе с API заданий.");
}

function duePanel(task) {
  const due = deadline(task);
  const change = changeFor(task);
  const panel = node("div", "due-panel");
  if (task.due_requires_confirmation) {
    panel.classList.add("is-unknown");
    panel.append(node("strong", "due-new", "Источник сообщает противоречивые сроки"));
    panel.append(node("span", "due-zone", "Обе версии сохранены в подтверждении ниже. Уточни срок."));
    return panel;
  }
  if (due.kind === "unknown" && !change) {
    panel.classList.add("is-unknown");
    panel.append(node("span", "due-label", "Срок"), node("strong", "due-new", "Не указан в источнике"));
    panel.append(node("span", "due-zone", "Уточни срок у преподавателя или в оригинале."));
    return panel;
  }
  panel.append(node("span", "due-label", change ? "Срок перенесён" : "Срок"));
  const value = node("div", "due-value");
  if (change) {
    value.append(node("s", "due-old", signatureText(change.from)), icon("arrow", "icon due-arrow"));
  }
  value.append(node("strong", "due-new", deadlineText(task, { long: true })));
  panel.append(value);
  const zone = due.kind === "date" ? "Источник указал дату без времени" : "";
  const noticed = change ? "агент заметил перенос в " + clock(change.at) : "";
  const line = [zone, noticed].filter(Boolean).join(" · ");
  if (line) panel.append(node("span", "due-zone", line));
  return panel;
}

function evidenceBlock(task) {
  const wrap = node("div", "evidence-list");
  wrap.append(node("h3", "section-label", "Откуда это известно"));
  for (const entry of task.evidence) {
    const figure = node("figure", "evidence");
    figure.append(node("blockquote", "", entry.quote));
    const caption = node("figcaption");
    let label = sourceName(entry.source_id) + " · " + entry.title;
    let safeUrl = null;
    if (entry.url) {
      try {
        const url = new URL(entry.url);
        if (["https:", "http:"].includes(url.protocol)) safeUrl = url.href;
      } catch { /* некорректная ссылка: показываем текстом */ }
    }
    if (safeUrl) {
      const link = node("a", "", label);
      link.href = safeUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      caption.append(link);
    } else {
      caption.append(document.createTextNode(label));
    }
    if (entry.published_at) {
      caption.append(document.createTextNode(" · опубликовано " + dayMonth(dateKey(entry.published_at)) + ", " + clock(entry.published_at)));
    }
    if (entry.fetched_at) {
      caption.append(document.createTextNode(" · получено " + dayMonth(dateKey(entry.fetched_at)) + ", " + clock(entry.fetched_at)));
    }
    figure.append(caption);
    wrap.append(figure);
  }
  return wrap;
}

function suggestions(task) {
  const row = node("div", "suggestions");
  row.append(node("span", "suggestions-label", "Оценка агента:"));

  const priority = node("label", "suggestion");
  priority.append(document.createTextNode("приоритет "));
  const select = node("select");
  select.setAttribute("aria-label", "Приоритет");
  for (const [value, label] of Object.entries(priorityLabels)) {
    const option = node("option", "", label);
    option.value = value;
    option.selected = value === priorityFor(task);
    select.append(option);
  }
  select.addEventListener("change", () => setOverride(task, { priority: select.value }));
  priority.append(select);

  const minutes = node("label", "suggestion");
  minutes.append(document.createTextNode("≈ "));
  const input = node("input");
  input.type = "number";
  input.min = "5";
  input.step = "5";
  input.inputMode = "numeric";
  input.value = minutesFor(task) ?? "";
  input.placeholder = "?";
  input.setAttribute("aria-label", "Сколько минут займёт");
  input.addEventListener("change", () => {
    const value = Number(input.value);
    if (value >= 1) setOverride(task, { minutes: value });
  });
  minutes.append(input, document.createTextNode(" мин"));
  row.append(priority, minutes);
  return row;
}

function taskCard(task) {
  const item = node("li", "task");
  item.dataset.key = task.id;
  if (isDone(task)) item.classList.add("is-done");
  if (state.fresh.has(task.id)) item.classList.add("just-changed");
  const details = node("details");
  if (state.fresh.has(task.id)) details.open = true;
  const summary = node("summary", "task-summary");
  const copy = node("span", "task-copy");
  copy.append(node("span", "task-course", task.course), node("span", "task-title", task.title));
  const meta = node("span", "task-meta");
  const offset = deadline(task).day ? daysBetween(state.dashboard.plan.date, deadline(task).day) : null;
  const tone = isDone(task) ? "" : offset === 0 ? " chip-today" : offset < 0 && offset !== null ? " chip-overdue" : "";
  const dueChip = node("span", "chip" + tone, deadlineText(task));
  meta.append(dueChip);
  if (changeFor(task)) meta.append(node("span", "changed-chip", "срок перенесён"));
  const minutes = minutesFor(task);
  if (minutes) meta.append(node("span", "chip", "≈ " + minutes + " мин"));
  if (isDone(task)) meta.append(node("span", "chip chip-done", "готово"));
  copy.append(meta);
  const chevron = node("span", "task-chevron");
  chevron.setAttribute("aria-hidden", "true");
  chevron.append(icon("chevron"));
  const origin = task.evidence[0];
  const source = node("span", "task-source", origin ? sourceName(origin.source_id) : "");
  summary.append(dateTile(task), copy, source, chevron);

  const body = node("div", "task-body");
  body.append(duePanel(task), evidenceBlock(task));
  if (task.deadline_changed) body.prepend(node("p", "deadline-change", task.deadline_changed));
  if (task.materials.length) {
    const materials = node("div", "evidence-list");
    materials.append(node("h3", "section-label", "Материалы"), materialList(task));
    body.append(materials);
  }
  body.append(suggestions(task));

  const actions = node("div", "task-actions");
  const original = task.evidence.find((entry) => entry.url);
  if (original) {
    const link = node("a", "button button-ghost", "Открыть в " + sourceName(original.source_id));
    link.href = original.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.prepend(icon("external"));
    actions.append(link);
  }
  if (task.status !== "done" || state.done.has(task.id)) {
    const done = node("button", "button button-primary", state.done.has(task.id) ? "Вернуть в работу" : "Готово");
    done.type = "button";
    done.addEventListener("click", () => toggleDone(task));
    actions.append(done);
  }
  body.append(actions);
  details.append(summary, body);
  item.append(details);
  return item;
}

function toggleDone(task) {
  const wasDone = state.done.has(task.id);
  if (wasDone) state.done.delete(task.id);
  else state.done.add(task.id);
  save("done", [...state.done]);
  toast(wasDone ? "Задание снова в работе" : "Задание готово", state.dataSource === "fake"
    ? "Отметка сохранена в браузере. Пример плана пересчитывается."
    : "Отметку запомнил этот браузер. Сервер узнает о ней вместе с API заданий.");
  renderAll();
  if (state.dataSource === "fake") loadDashboard();
}

function renderCourses() {
  const select = $("#course-filter");
  const courses = [...new Set(state.dashboard.tasks.map((task) => task.course))].sort((a, b) => a.localeCompare(b, "ru"));
  if (state.course && !courses.includes(state.course)) state.course = "";
  const all = node("option", "", "Все курсы");
  all.value = "";
  select.replaceChildren(all, ...courses.map((course) => {
    const option = node("option", "", course);
    option.value = course;
    return option;
  }));
  select.value = state.course;
}

function renderTasks() {
  const { tasks, plan } = state.dashboard;
  const list = $("#task-list");
  const before = snapshot(list);
  const visible = tasks.filter((task) => {
    if (state.course && task.course !== state.course) return false;
    const due = deadline(task);
    if (state.filter === "done") return isDone(task);
    if (state.filter === "today") return !isDone(task) && due.day === plan.date;
    if (state.filter === "week") return !isDone(task) && due.day && daysBetween(plan.date, due.day) <= 7;
    if (state.filter === "no-deadline") return !isDone(task) && !due.day;
    return true;
  }).sort((a, b) => Number(isDone(a)) - Number(isDone(b)) || compareTasks(a, b));
  list.replaceChildren(...visible.map(taskCard));
  flip(list, before);
  $("#tasks-empty").hidden = visible.length > 0;
  $("#tasks-empty-title").textContent = tasks.length ? "Здесь пока свободно" : "Заданий пока нет";
  $("#tasks-empty-text").textContent = tasks.length
    ? "Для этого фильтра заданий нет."
    : "Агент покажет их после первых сообщений из источников.";
}

/* ---------- Материалы: библиотека выбранной группы и ссылки из заданий ---------- */
function collectMaterials() {
  const { tasks, plan } = state.dashboard;
  const planned = new Set(plan.blocks.map((block) => block.task_id));
  const byKey = new Map();
  for (const material of state.dashboard.materials || []) {
    byKey.set(material.course + "::" + material.title, { ...material, tasks: [], today: false });
  }
  for (const task of tasks) {
    for (const title of task.materials) {
      const key = task.course + "::" + title;
      if (!byKey.has(key)) byKey.set(key, { title, course: task.course, tasks: [], today: false });
      const entry = byKey.get(key);
      entry.tasks.push(task);
      if (!isDone(task) && (planned.has(task.id) || isToday(task))) entry.today = true;
    }
  }
  return [...byKey.values()].sort((a, b) =>
    a.course.localeCompare(b.course, "ru") || Number(b.today) - Number(a.today) || a.title.localeCompare(b.title, "ru"));
}

function highlight(text, query) {
  const fragment = document.createDocumentFragment();
  const index = query ? text.toLowerCase().indexOf(query) : -1;
  if (index < 0) { fragment.append(text); return fragment; }
  fragment.append(text.slice(0, index), node("mark", "", text.slice(index, index + query.length)), text.slice(index + query.length));
  return fragment;
}

function materialCard(entry, query) {
  const item = node("li", "material-card");
  if (entry.today) item.classList.add("is-today");
  const mark = node("span", "material-icon");
  mark.setAttribute("aria-hidden", "true");
  mark.append(icon("doc"));
  const body = node("div", "material-body");
  let sourceUrl = null;
  try {
    const url = new URL(entry.url || entry.title, location.origin);
    if (entry.url && ["http:", "https:"].includes(url.protocol)) sourceUrl = url.href;
    else if (/^https?:\/\//i.test(entry.title) && ["http:", "https:"].includes(url.protocol)) sourceUrl = url.href;
  } catch { /* The title remains readable if a source link is invalid. */ }
  const title = node(sourceUrl ? "a" : "strong", "material-title");
  if (sourceUrl) {
    title.href = sourceUrl;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
  }
  title.append(highlight(entry.title, query));
  body.append(title);
  if (entry.today) body.append(node("span", "chip chip-today material-badge", "нужно сегодня"));
  const links = node("span", "material-tasks");
  links.append("К заданию: ");
  entry.tasks.forEach((task, index) => {
    if (index) links.append(", ");
    const link = node("a", "");
    link.href = "#tasks";
    link.dataset.openTask = task.id;
    link.append(highlight(task.title, query));
    links.append(link);
  });
  const first = [...entry.tasks].sort(compareTasks)[0];
  const sources = [...new Set([
    ...entry.tasks.flatMap((task) => task.evidence.map((item) => sourceName(item.source_id))),
    ...(entry.source_id ? [sourceName(entry.source_id)] : []),
  ])];
  const meta = node("span", "material-meta", [first ? deadlineText(first) : null, ...sources].filter(Boolean).join(" · "));
  if (entry.tasks.length) body.append(links);
  body.append(meta);
  item.append(mark, body);
  return item;
}

function renderMaterials() {
  const all = collectMaterials();
  const filters = state.materials;
  const query = filters.query.trim().toLowerCase();
  const select = $("#materials-course");
  const courses = [...new Set(all.map((entry) => entry.course))];
  if (filters.course && !courses.includes(filters.course)) filters.course = "";
  const any = node("option", "", "Все курсы");
  any.value = "";
  select.replaceChildren(any, ...courses.map((course) => {
    const option = node("option", "", course);
    option.value = course;
    return option;
  }));
  select.value = filters.course;
  const todayCount = all.filter((entry) => entry.today).length;
  $("#materials-today-count").textContent = todayCount ? String(todayCount) : "";
  $("#materials-today").setAttribute("aria-pressed", String(filters.today));

  const visible = all.filter((entry) => {
    if (filters.course && entry.course !== filters.course) return false;
    if (filters.today && !entry.today) return false;
    if (!query) return true;
    return [entry.title, entry.course, ...entry.tasks.map((task) => task.title)].some((text) => text.toLowerCase().includes(query));
  });
  const groups = new Map();
  for (const entry of visible) {
    if (!groups.has(entry.course)) groups.set(entry.course, []);
    groups.get(entry.course).push(entry);
  }
  $("#materials-list").replaceChildren(...[...groups].map(([course, entries]) => {
    const group = node("section", "material-group");
    const heading = node("div", "material-group-head");
    const name = node("h2", "material-course");
    name.append(highlight(course, query));
    heading.append(name, node("span", "nav-count", String(entries.length)));
    const list = node("ul", "material-cards");
    list.append(...entries.map((entry) => materialCard(entry, query)));
    group.append(heading, list);
    return group;
  }));
  stagger($$("#materials-list .material-group"));
  const empty = !visible.length;
  $("#materials-empty").hidden = !empty;
  if (empty) {
    $("#materials-empty-title").textContent = all.length ? "Ничего не нашлось" : "Материалов пока нет";
    $("#materials-empty-text").textContent = all.length
      ? "Попробуй другое слово или сбрось фильтры."
      : "Материалы выбранной при входе группы загрузятся автоматически. Здесь также появятся ссылки из заданий.";
  }
}

$("#materials-search").addEventListener("input", (event) => {
  state.materials.query = event.target.value;
  renderMaterials();
});
$("#materials-course").addEventListener("change", (event) => {
  state.materials.course = event.target.value;
  renderMaterials();
});
$("#materials-today").addEventListener("click", () => {
  state.materials.today = !state.materials.today;
  renderMaterials();
});

/* Переход из материала к его заданию: фильтры сбрасываются, карточка раскрывается. */
document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-open-task]");
  if (!link) return;
  event.preventDefault();
  const id = link.dataset.openTask;
  state.filter = "all";
  state.course = "";
  $$("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.filter === "all")));
  renderTasks();
  location.hash = "tasks";
  setTimeout(() => {
    const card = $(`#task-list [data-key="${CSS.escape(id)}"]`);
    if (!card) return;
    $("details", card).open = true;
    card.scrollIntoView({ block: "center", behavior: animate() ? "smooth" : "auto" });
    card.animate([{ boxShadow: "0 0 0 4px rgb(112 86 172 / 40%)" }, { boxShadow: "0 0 0 0 rgb(112 86 172 / 0%)" }], { duration: 1600 });
  }, animate() ? 450 : 0);
});

/* ---------- Источники ---------- */
function renderSources() {
  const { sources } = state.dashboard;
  const connected = sources.filter((source) => source.status === "connected").length;
  $("#sources-lead").textContent = "Полностью обновлены " + connected + " из " + sources.length + " источников. В каждой строке видно время последнего успешного обновления.";
  $("#source-grid").replaceChildren(...sources.map((source) => {
    const card = node("li", "source-card");
    const main = node("div", "source-main");
    main.append(node("h2", "source-name", source.name), node("p", "source-description", source.description));
    const side = node("div", "source-side");
    const status = node("span", "source-status");
    status.dataset.status = source.status;
    const dot = node("span", "status-dot");
    dot.setAttribute("aria-hidden", "true");
    status.append(dot, document.createTextNode(sourceStatusLabel(source.status)));
    side.append(status, node("span", "source-meta", source.last_synced_at
      ? "Последнее успешное обновление: " + dayMonth(dateKey(source.last_synced_at)) + ", " + clock(source.last_synced_at)
      : "Полного успешного обновления пока не было"));
    card.append(main, side);
    if (source.error_message) card.append(node("p", "source-error", source.error_message));
    return card;
  }));
  const alert = sources.some((source) => sourceNeedsAttention(source.status));
  $("#nav-source-alert").hidden = !alert;
}

/* ---------- Настройки ---------- */
function renderSettings() {
  const form = $("#settings-form");
  $("#settings-mode-hint").textContent = state.dataSource === "fake"
    ? "Настройки запоминает этот браузер. Доступное время учитывается в примере плана."
    : "Настройки запоминает этот браузер. Сервер начнёт их учитывать вместе с API настроек.";
  const defaults = { minutes: state.dashboard.plan.available_minutes, channels: ["site"], remind: "1440", sync: "30" };
  const settings = { ...defaults, ...state.settings };
  const choices = $$("input[name=minutes]", form);
  const closest = choices.reduce((best, input) =>
    Math.abs(input.value - settings.minutes) < Math.abs(best.value - settings.minutes) ? input : best, choices[0]);
  closest.checked = true;
  for (const input of $$("input[name=channel]", form)) input.checked = settings.channels.includes(input.value);
  form.elements.remind.value = settings.remind;
  form.elements.sync.value = settings.sync;
  $("#settings-zone").textContent = "Часовой пояс: " + tz() + " (" + zoneOffset() + "). Его задаёт сервер.";
}

$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  state.settings = {
    minutes: Number(form.elements.minutes.value),
    channels: $$("input[name=channel]:checked", form).map((input) => input.value),
    remind: form.elements.remind.value,
    sync: form.elements.sync.value,
  };
  save("settings", state.settings);
  $("#settings-saved").textContent = "Браузер запомнил настройки.";
  toast("Браузер запомнил настройки");
  if (state.dataSource === "fake") loadDashboard();
});

/* ---------- Навигация ---------- */
function moveIndicator() {
  const indicator = $("#nav-indicator");
  const active = $(".nav-wrap .nav-link[aria-current='page']");
  if (!active) { indicator.style.opacity = "0"; return; }
  const wrap = $("#nav-wrap").getBoundingClientRect();
  const rect = active.getBoundingClientRect();
  indicator.style.opacity = "1";
  indicator.style.height = rect.height + "px";
  indicator.style.transform = `translateY(${rect.top - wrap.top}px)`;
}

function applyView(name) {
  for (const view of $$("[data-view]")) view.hidden = view.dataset.view !== name || !state.dashboard;
  $$("[data-nav]").forEach((link) => {
    if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const current = $(`[data-view="${name}"]`);
  if (state.dashboard) stagger([...current.children]);
  moveIndicator();
}

function showView({ focus = true } = {}) {
  const requested = location.hash.slice(1);
  const name = MAIN_VIEWS.includes(requested) ? requested : "today";
  if (name === state.view) return;
  const first = state.view === null;
  state.view = name;
  if (!first && animate() && document.startViewTransition) {
    document.startViewTransition(() => applyView(name));
  } else {
    applyView(name);
  }
  if (focus && !first) {
    $(`[data-view="${name}"]`)?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: animate() ? "smooth" : "auto" });
  }
}

/* ---------- Загрузка ---------- */
function renderMeta() {
  const { meta, tasks } = state.dashboard;
  $("#today-label").textContent = new Intl.DateTimeFormat("ru-RU", {
    timeZone: meta.timezone, weekday: "long", day: "numeric", month: "long",
  }).format(new Date(meta.generated_at));
  $("#today-label").dateTime = meta.generated_at;
  $("#mode-notice-text").textContent = meta.notice;
  $("#mode-notice").hidden = !meta.notice;
  $("#nav-task-count").textContent = String(tasks.filter((task) => !isDone(task)).length);
}

function renderAll() {
  renderMeta();
  renderHero();
  renderPlan();
  renderDeadlines();
  renderStrip();
  renderCourses();
  renderTasks();
  renderMaterials();
  renderSources();
  renderSettings();
}

function setSync(stateName, text) {
  $("#sync-state").dataset.state = stateName;
  $("#sync-text").textContent = text;
}

function hideSplash() {
  const splash = $("#splash");
  if (splash.classList.contains("is-done")) return;
  const wait = animate() ? Math.max(0, 1300 - (performance.now() - splashStarted)) : 0;
  setTimeout(() => {
    splash.classList.add("is-done");
    setTimeout(() => { splash.hidden = true; profileController.openIfNeeded(); }, animate() ? 900 : 0);
  }, wait);
}

async function loadDashboard() {
  if (state.loading) { state.reloadRequested = true; return; }
  state.loading = true;
  const button = $("#refresh-button");
  button.disabled = true;
  button.classList.add("is-loading");
  setSync("loading", "Обновляем…");
  try {
    const scenario = new URLSearchParams(location.search).get("scenario") || "default";
    const sourceId = state.dsbaSourceId;
    const dataSource = state.dataSource;
    const dashboard = await fetchDashboard(scenario, sourceId, {
      dataSource, availableMinutes: state.settings?.minutes ?? 120, completed: [...state.done],
    });
    if (sourceId !== state.dsbaSourceId || dataSource !== state.dataSource) {
      state.reloadRequested = true; return;
    }
    state.dashboard = dashboard;
    detectChanges(state.dashboard.tasks);
    $("#load-error").hidden = true;
    renderAll();
    if (state.fresh.size) setTimeout(() => state.fresh.clear(), 4000);
    if (state.view === null) showView({ focus: false });
    else applyView(state.view);
    const { mode, generated_at: generatedAt } = state.dashboard.meta;
    const suffix = mode === "live" ? "" : mode === "demo" ? " · учебные примеры" : " · источники не подключены";
    setSync(mode === "live" ? "ok" : "demo", "Связь Вааааась");
    $("#sync-state").title = "Ответ сервера в " + clock(generatedAt) + suffix;
  } catch (error) {
    setSync("error", "Связь потеряна, Вааась");
    if (state.dashboard) {
      toast("Связь потеряна, Вааась", error.message, "error");
    } else {
      $("#load-error-text").textContent = error.message;
      $("#retry-button").hidden = !error.retryable;
      $("#load-error").hidden = false;
      if (state.view === null) state.view = "today";
    }
  } finally {
    state.loading = false;
    button.disabled = false;
    button.classList.remove("is-loading");
    hideSplash();
    if (state.reloadRequested) {
      state.reloadRequested = false;
      queueMicrotask(loadDashboard);
    }
  }
}

/* ---------- Тема: ВШЭ или «Вкусно и точка» ---------- */
function currentTheme() {
  return document.documentElement.dataset.theme === "vit" ? "vit" : "hse";
}

function syncThemeControls() {
  const theme = currentTheme();
  $("#theme-button").setAttribute("aria-pressed", String(theme === "vit"));
  $("#theme-button").setAttribute("aria-label", theme === "vit" ? "Вернуть тему ВШЭ" : "Тема «Вкусно и точка»");
  for (const input of $$("input[name=theme]")) input.checked = input.value === theme;
}

function setTheme(theme, origin) {
  if (theme === currentTheme()) return;
  const apply = () => {
    if (theme === "vit") document.documentElement.dataset.theme = "vit";
    else delete document.documentElement.dataset.theme;
    syncThemeControls();
  };
  save("theme", theme);
  if (!animate() || !document.startViewTransition) { apply(); return; }
  const x = origin?.x ?? innerWidth - 60;
  const y = origin?.y ?? 50;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.documentElement.classList.add("theme-switching");
  const transition = document.startViewTransition(apply);
  transition.ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 750, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" },
    );
  });
  transition.finished.finally(() => document.documentElement.classList.remove("theme-switching"));
}

$("#theme-button").addEventListener("click", (event) => {
  setTheme(currentTheme() === "vit" ? "hse" : "vit", { x: event.clientX, y: event.clientY });
});
for (const input of $$("input[name=theme]")) {
  input.addEventListener("change", () => {
    const rect = input.parentElement.getBoundingClientRect();
    setTheme(input.value, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  });
}
syncThemeControls();

/* ---------- Ширина меню: перетаскивание, стрелки, двойной клик ---------- */
const SIDEBAR = { min: 208, max: 380, initial: 264 };

function setSidebarWidth(width, persist = true) {
  const value = Math.round(Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, width)));
  $(".app-shell").style.setProperty("--sidebar-w", value + "px");
  $("#sidebar").classList.toggle("is-narrow", value < 236);
  const handle = $("#sidebar-resizer");
  handle.setAttribute("aria-valuenow", String(value));
  handle.setAttribute("aria-valuemin", String(SIDEBAR.min));
  handle.setAttribute("aria-valuemax", String(SIDEBAR.max));
  if (persist) save("sidebar", value);
  moveIndicator();
}

(() => {
  const handle = $("#sidebar-resizer");
  const shell = $(".app-shell");
  setSidebarWidth(load("sidebar", SIDEBAR.initial), false);
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    shell.classList.add("is-resizing");
    const move = (moveEvent) => setSidebarWidth(moveEvent.clientX, false);
    const up = (upEvent) => {
      setSidebarWidth(upEvent.clientX);
      shell.classList.remove("is-resizing");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
  handle.addEventListener("dblclick", () => setSidebarWidth(SIDEBAR.initial));
  handle.addEventListener("keydown", (event) => {
    const current = Number(handle.getAttribute("aria-valuenow"));
    const step = event.shiftKey ? 40 : 16;
    if (event.key === "ArrowLeft") setSidebarWidth(current - step);
    else if (event.key === "ArrowRight") setSidebarWidth(current + step);
    else if (event.key === "Home") setSidebarWidth(SIDEBAR.min);
    else if (event.key === "End") setSidebarWidth(SIDEBAR.max);
    else return;
    event.preventDefault();
  });
})();

$$("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  $$("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  if (state.dashboard) renderTasks();
}));
$("#course-filter").addEventListener("change", (event) => {
  state.course = event.target.value;
  renderTasks();
});
$("#refresh-button").addEventListener("click", loadDashboard);
$("#retry-button").addEventListener("click", loadDashboard);
window.addEventListener("hashchange", () => showView());
window.addEventListener("resize", moveIndicator);
window.addEventListener("offline", () => {
  setSync("error", "Связь потеряна, Вааась");
  toast("Связь потеряна, Вааась", "Браузер потерял интернет. Данные на экране не обновляются.", "error");
});
window.addEventListener("online", loadDashboard);
document.fonts?.ready.then(moveIndicator);
const profileController = setupDsba(async (sourceId, resetPersonal, dataSource) => {
  if (resetPersonal) {
    state.dashboard = null;
    applyView(state.view || "today");
    state.done = new Set();
    state.overrides = {};
    state.changes = {};
    state.fresh.clear();
    state.settings = null;
    state.course = "";
    state.filter = "all";
    state.materials = { query: "", course: "", today: false };
    $("#materials-search").value = "";
    $$("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.filter === "all")));
  }
  state.dsbaSourceId = sourceId;
  state.dataSource = dataSource;
  await loadDashboard();
});
setInterval(() => {
  if ((state.dsbaSourceId || state.dataSource === "fake") && !document.hidden) loadDashboard();
}, 60000);
loadDashboard();
