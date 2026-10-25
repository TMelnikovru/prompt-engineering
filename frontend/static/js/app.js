import { fetchDashboard } from "./api.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const STORE = "hse-agent:";
const MAIN_VIEWS = ["today", "tasks", "sources", "settings"];
const SOON = {
  calendar: {
    title: "Календарь",
    lead: "Расписание пар и нагрузка по дням в виде недели. Данные РУЗ агент читает уже в MVP.",
    points: ["Пары, дедлайны и свободные окна на одной шкале", "Тяжёлые недели видно заранее"],
  },
  events: {
    title: "Мероприятия",
    lead: "Студенческие события, на которые хватает времени между дедлайнами.",
    points: ["События факультета и университета", "Подсказка, когда на событие есть время"],
  },
  clubs: {
    title: "Клубы и кружки",
    lead: "Поиск студенческих сообществ по интересам.",
    points: ["Клубы, кружки и проектные команды", "Встречи в календаре рядом с учёбой"],
  },
  minors: {
    title: "Майноры",
    lead: "Майноры и образовательная траектория: требования программ и твои интересы.",
    points: ["Проверяемые требования программ", "Сравнение майноров по нагрузке и темам"],
  },
  tutor: {
    title: "ИИ-репетитор",
    lead: "Объяснения и разбор учебных материалов. Команда возьмётся за него после основного сценария: задания → сроки → план.",
    points: ["Разбор задач по материалам курса", "Ссылка на источник у каждого объяснения"],
  },
  account: {
    title: "Аккаунт",
    lead: "Вход, подключённые аккаунты и твои данные.",
    points: ["Вход и первое подключение источников", "Выбор курсов и Telegram-чатов"],
    badge: "Скоро",
  },
};
const statusLabels = {
  not_connected: "Не подключён", connected: "Подключён", syncing: "Обновляется",
  error: "Ошибка подключения", stale: "Данные устарели",
};
const priorityLabels = { high: "высокий", medium: "средний", low: "низкий" };

const state = {
  dashboard: null,
  filter: "all",
  course: "",
  view: null,
  loading: false,
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

function hm(minutes) {
  return Math.floor(minutes / 60) + ":" + String(minutes % 60).padStart(2, "0");
}

function duration(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return rest + " мин";
  return hours + " ч" + (rest ? " " + rest + " мин" : "");
}

/* Срок: точное время, только дата или неизвестно. Дату без времени не превращаем в 23:59. */
function deadline(task) {
  if (task.due_at) return { kind: "exact", day: dateKey(task.due_at), time: clock(task.due_at) };
  if (task.due_date) return { kind: "date", day: task.due_date, time: null };
  return { kind: "unknown", day: null, time: null };
}

function deadlineSignature(task) {
  return task.due_at || task.due_date || "";
}

function deadlineText(task, { long = false } = {}) {
  const due = deadline(task);
  if (due.kind === "unknown") return "Срок не указан";
  const today = state.dashboard.plan.date;
  const offset = daysBetween(today, due.day);
  let day = dayMonth(due.day, long ? "long" : "short");
  if (offset === 0) day = "Сегодня";
  else if (offset === 1) day = "Завтра";
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

function countUp(element, to, format) {
  const from = Number(element.dataset.value || 0);
  element.dataset.value = to;
  if (!animate() || from === to) { element.textContent = format(to); return; }
  const started = performance.now();
  const length = 900;
  const step = (now) => {
    const progress = Math.min((now - started) / length, 1);
    const eased = 1 - Math.pow(1 - progress, 4);
    element.textContent = format(Math.round(from + (to - from) * eased));
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

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
  let title = [steps + " " + plural(steps, "шаг", "шага", "шагов") + " на сегодня.", "Ты успеваешь."];
  if (!tasks.length) {
    eyebrow = "Вааась, учёба началась";
    title = ["Пока пусто.", "Задания придут из источников."];
  } else if (!steps) {
    title = ["Плана на сегодня нет.", "Загляни в задания."];
  } else if (unscheduled.length) {
    eyebrow = "Вааась, сегодня плотно";
    title[1] = "Остальное подождёт.";
  }
  $("#hero-eyebrow").textContent = eyebrow;
  $("#hero-title").replaceChildren(...title.map((line) => node("span", "", line)));

  const next = tasks.filter((task) => !isDone(task) && deadline(task).day).sort(compareTasks)[0];
  let lead = "Подключи источники, и агент соберёт задания и составит план дня.";
  if (dueToday.length) {
    const time = deadline(dueToday[0]).time;
    lead = "Срок по «" + dueToday[0].title + "» истекает сегодня" + (time ? " в " + time : "") + ".";
  } else if (next) {
    lead = "Ближайший срок: «" + next.title + "», " + deadlineText(next).toLowerCase() + ".";
  } else if (tasks.length) {
    lead = "У заданий нет точных сроков. Агент расставил их по объёму.";
  }
  $("#hero-lead").textContent = lead;
  countUp($("#stat-planned"), planned, hm);
  countUp($("#stat-available"), plan.available_minutes, hm);
  countUp($("#stat-today"), dueToday.length, String);
  $("#stat-today").classList.toggle("is-hot", dueToday.length > 0);
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
  const { plan, meta } = state.dashboard;
  const list = $("#plan-list");
  const before = snapshot(list);
  const planned = plan.blocks.reduce((sum, block) => sum + block.minutes, 0);

  $("#plan-sub").textContent = "Пересчитан в " + clock(meta.generated_at) + " · порядок по срокам и объёму";
  $("#budget-label").textContent = duration(planned) + " из " + duration(plan.available_minutes);
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
    $("#overload-title").textContent = "Не помещается сегодня: " + count + " " + plural(count, "задание", "задания", "заданий");
    $("#overload-text").textContent = "Агент перенёс их на потом. Добавь время или выбери главное.";
    $("#overload-list").replaceChildren(...unscheduled.map((task) => {
      const item = node("li");
      const minutes = minutesFor(task);
      item.append(node("strong", "", task.title), node("span", "", (minutes ? "≈ " + minutes + " мин · " : "") + deadlineText(task)));
      return item;
    }));
  }
  $("#plan-note").textContent = plan.note;
  $("#plan-note").hidden = !plan.note || !plan.blocks.length;
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
  return statusLabels[source.status].toLowerCase();
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
  const zone = due.kind === "exact"
    ? "Время: " + tz() + ", " + zoneOffset()
    : due.kind === "date" ? "Источник указал дату без времени" : "";
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
  const dueChip = node("span", isToday(task) && !isDone(task) ? "chip chip-today" : "chip", deadlineText(task));
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
  if (task.materials.length) {
    const materials = node("div", "evidence-list");
    materials.append(node("h3", "section-label", "Материалы"), materialList(task));
    body.append(materials);
  }
  body.append(suggestions(task));

  const actions = node("div", "task-actions");
  const original = task.evidence.find((entry) => entry.url);
  if (original) {
    const link = node("a", "button button-ghost", "Открыть оригинал");
    link.href = original.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.prepend(icon("external"));
    actions.append(link);
  }
  if (task.status !== "done") {
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
  toast(wasDone ? "Задание снова в работе" : "Задание готово", "Отметку запомнил этот браузер. Сервер узнает о ней вместе с API заданий.");
  renderAll();
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

/* ---------- Источники ---------- */
function renderSources() {
  const { sources } = state.dashboard;
  const connected = sources.filter((source) => source.status === "connected").length;
  $("#sources-lead").textContent = "Агент читает " + connected + " из " + sources.length + " источников. В каждой строке видно время последнего успешного обновления.";
  $("#source-grid").replaceChildren(...sources.map((source) => {
    const card = node("li", "source-card");
    const main = node("div", "source-main");
    main.append(node("h2", "source-name", source.name), node("p", "source-description", source.description));
    const side = node("div", "source-side");
    const status = node("span", "source-status");
    status.dataset.status = source.status;
    const dot = node("span", "status-dot");
    dot.setAttribute("aria-hidden", "true");
    status.append(dot, document.createTextNode(statusLabels[source.status]));
    side.append(status, node("span", "source-meta", source.last_synced_at
      ? "Последнее успешное обновление: " + dayMonth(dateKey(source.last_synced_at)) + ", " + clock(source.last_synced_at)
      : "Агент ещё не получал отсюда данные"));
    card.append(main, side);
    if (source.error_message) card.append(node("p", "source-error", source.error_message));
    return card;
  }));
  const alert = sources.some((source) => source.status === "error" || source.status === "stale");
  $("#nav-source-alert").hidden = !alert;
}

/* ---------- Настройки ---------- */
function renderSettings() {
  const form = $("#settings-form");
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

function renderSoon(name) {
  const config = SOON[name];
  $("#soon-badge").textContent = config.badge || "После MVP";
  $("#soon-title").textContent = config.title;
  $("#soon-lead").textContent = config.lead;
  $("#soon-points").replaceChildren(...config.points.map((point) => node("li", "", point)));
  stagger($$("#view-soon .soon-pattern polygon"));
}

function applyView(name) {
  const section = SOON[name] ? "soon" : name;
  if (SOON[name]) renderSoon(name);
  for (const view of $$("[data-view]")) view.hidden = view.dataset.view !== section || !state.dashboard;
  $$("[data-nav]").forEach((link) => {
    if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const current = $(`[data-view="${section}"]`);
  if (state.dashboard) stagger([...current.children]);
  moveIndicator();
}

function showView({ focus = true } = {}) {
  const requested = location.hash.slice(1);
  const name = MAIN_VIEWS.includes(requested) || SOON[requested] ? requested : "today";
  if (name === state.view) return;
  const first = state.view === null;
  state.view = name;
  if (!first && animate() && document.startViewTransition) {
    document.startViewTransition(() => applyView(name));
  } else {
    applyView(name);
  }
  if (focus && !first) {
    $(`[data-view="${SOON[name] ? "soon" : name}"]`)?.focus({ preventScroll: true });
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
  $("#today-zone").textContent = "Часовой пояс: " + meta.timezone + ", " + zoneOffset();
  $("#mode-notice-text").textContent = meta.notice;
  $("#mode-notice").hidden = !meta.notice;
  $("#nav-task-count").textContent = String(tasks.filter((task) => !isDone(task)).length);
  $("#updated-at").textContent = "Ответ сервера в " + clock(meta.generated_at) + " · " + meta.timezone;
}

function renderAll() {
  renderMeta();
  renderHero();
  renderPlan();
  renderDeadlines();
  renderStrip();
  renderCourses();
  renderTasks();
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
    setTimeout(() => { splash.hidden = true; }, animate() ? 900 : 0);
  }, wait);
}

async function loadDashboard() {
  if (state.loading) return;
  state.loading = true;
  const button = $("#refresh-button");
  button.disabled = true;
  button.classList.add("is-loading");
  setSync("loading", "Обновляем…");
  try {
    const scenario = new URLSearchParams(location.search).get("scenario") || "default";
    state.dashboard = await fetchDashboard(scenario);
    detectChanges(state.dashboard.tasks);
    $("#load-error").hidden = true;
    renderAll();
    if (state.fresh.size) setTimeout(() => state.fresh.clear(), 4000);
    if (state.view === null) showView({ focus: false });
    else applyView(state.view);
    const { mode, generated_at: generatedAt } = state.dashboard.meta;
    if (mode === "live") setSync("ok", "Обновлено в " + clock(generatedAt));
    else if (mode === "demo") setSync("demo", "Учебные примеры · " + clock(generatedAt));
    else setSync("demo", "Источники не подключены");
  } catch (error) {
    setSync("error", "Не обновилось");
    if (state.dashboard) {
      toast("Не получилось обновить", error.message, "error");
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
  }
}

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
document.fonts?.ready.then(moveIndicator);
loadDashboard();
