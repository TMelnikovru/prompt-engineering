import { fetchDsbaCatalog, fetchDocuments, syncDsba } from "./api.js";

const key = "hse-dsba-selection";

export function savedDsbaSelection() {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && /^dsba:[a-f0-9]{32}$/.test(value.source_id) ? value : null;
  } catch { return null; }
}

function options(select, values, placeholder) {
  const first = document.createElement("option");
  first.value = "";
  first.textContent = placeholder;
  select.replaceChildren(first, ...values.map(([value, label]) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = label;
    return option;
  }));
}

export function setupDsba(onConnected) {
  const panel = document.querySelector("#dsba-connection");
  if (!panel) return;
  const form = document.querySelector("#dsba-form");
  const message = document.querySelector("#dsba-message");
  const fields = form.querySelector("fieldset");
  const program = form.elements.program;
  const year = form.elements.year;
  const group = form.elements.group;
  const sub = form.elements.sub;
  let catalog = null;
  let loading = false;
  let saved = savedDsbaSelection();

  function fillGroup() {
    const p = catalog.programs.find((item) => item.id === program.value);
    const y = p?.years.find((item) => item.year === Number(year.value));
    options(group, (y?.groups || []).map((value) => [value, value]),
      y?.groups.length ? "Выберите группу" : "Общие материалы курса");
    group.disabled = !y?.groups.length;
    group.required = !!y?.groups.length;
    options(sub, (y?.subgroups || []).map((value) => [value, value]), "Не выбрана");
    sub.disabled = !y?.subgroups.length;
  }

  function fillYear() {
    const p = catalog.programs.find((item) => item.id === program.value);
    options(year, (p?.years || []).map((value) => [value.year, value.label]), "Выберите курс");
    year.disabled = !p;
    fillGroup();
  }

  async function materials() {
    if (!saved) return;
    const list = document.querySelector("#dsba-materials");
    list.replaceChildren();
    document.querySelector("#dsba-materials-section").hidden = true;
    try {
      const data = await fetchDocuments(saved.source_id);
      const items = data.items.filter((item) => item.external_id.startsWith("material:"));
      list.replaceChildren(...items.map((item) => {
        const row = document.createElement("li");
        const link = document.createElement("a");
        const url = new URL(item.url, location.origin);
        if (!["https:", "http:"].includes(url.protocol)) return row;
        link.href = url.href;
        link.textContent = item.title;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
        return row;
      }));
      document.querySelector("#dsba-materials-section").hidden = !items.length;
    } catch { /* Source status and the next refresh retain the last visible result. */ }
  }

  panel.addEventListener("toggle", async () => {
    if (!panel.open || loading) return;
    if (catalog) { await materials(); return; }
    loading = true;
    message.textContent = "Загружаем каталог dsba.online…";
    try {
      catalog = await fetchDsbaCatalog();
      options(program, catalog.programs.map((item) => [item.id, item.name]), "Выберите программу");
      fields.disabled = false;
      program.value = saved?.selection?.program || "";
      fillYear();
      year.value = saved?.selection?.year || "";
      fillGroup();
      group.value = saved?.selection?.group || "";
      sub.value = saved?.selection?.sub || "";
      program.addEventListener("change", fillYear);
      year.addEventListener("change", fillGroup);
      message.textContent = "Выберите свою учебную группу. Материалы обновляются и после закрытия сайта.";
      await materials();
    } catch (error) {
      message.textContent = error.message + " Закройте и снова откройте подключение для повтора.";
    } finally {
      loading = false;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const selection = {
      program: program.value, year: Number(year.value), group: group.value, sub: sub.value,
    };
    fields.disabled = true;
    message.textContent = "Загружаем материалы и дедлайны выбранной группы…";
    try {
      const result = await syncDsba(selection);
      saved = { source_id: result.source_id, selection };
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch { /* Current tab still works. */ }
      await onConnected(result.source_id);
      await materials();
      message.textContent = result.status === "failed"
        ? "Обновление не удалось. Сохранённые данные доступны; сервер повторит попытку."
        : result.status === "partial"
          ? "Часть данных загружена. Состояние подключения и ошибки показаны ниже."
          : "Группа подключена. Дедлайны появились в заданиях; обновление выполняется автоматически.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      fields.disabled = false;
    }
  });
}
