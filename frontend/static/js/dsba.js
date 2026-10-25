import { fetchDsbaCatalog, syncDsba } from "./api.js";
import {
  PROFILE_KEY, SELECTION_KEY, normalizeName, savedSelection, completeProfile, selectionFromCatalog,
} from "./dsba-profile.js";

function read(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
}

export function savedDsbaSelection() {
  return savedSelection(read(PROFILE_KEY), read(SELECTION_KEY));
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
  const dialog = document.querySelector("#signup");
  const form = document.querySelector("#signup-form");
  const fields = document.querySelector("#signup-fields");
  const error = document.querySelector("#signup-error");
  const message = document.querySelector("#signup-message");
  const retry = document.querySelector("#signup-retry");
  const submit = document.querySelector("#signup-submit");
  const cancel = document.querySelector("#signup-cancel");
  const subjectsList = document.querySelector("#signup-subjects");
  const program = form.elements.program;
  const year = form.elements.year;
  const group = form.elements.group;
  const sub = form.elements.sub;
  let profile = read(PROFILE_KEY);
  let saved = savedDsbaSelection();
  let catalog = null;
  let loadingCatalog = null;
  let submitting = false;
  let revision = 0;
  let resumed = false;
  let resumeRunning = false;
  let resumeTimer = null;

  function validResult(result) {
    return /^dsba:[a-f0-9]{32}$/.test(result.source_id)
      && ["success", "partial", "failed"].includes(result.status);
  }

  function persistProfile() {
    try {
      localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
      localStorage.setItem(SELECTION_KEY, JSON.stringify(saved));
    } catch { /* The in-memory profile still works for this tab. */ }
  }

  function showError(text) {
    error.textContent = text;
    error.hidden = false;
  }

  function clearPersonalData() {
    for (const key of ["done", "overrides", "deadlines", "changes", "settings"]) {
      try { localStorage.removeItem("hse-agent:" + key); } catch { /* Current tab still works. */ }
    }
  }

  function renderProfile() {
    const name = normalizeName(profile?.name);
    const s = saved?.selection;
    document.querySelector("#account-name").textContent = name
      ? name.split(" ").slice(0, 2).join(" ") : "Аккаунт";
    document.querySelector("#account-group").textContent = s
      ? (s.group ? "группа " + s.group : s.year + " курс") : "вход и данные";
    document.querySelector("#account-view-name").textContent = name || "—";
    document.querySelector("#account-view-group").textContent = s ? s.group || "Общие материалы курса" : "—";
    document.querySelector("#account-view-program").textContent = profile?.program_name || s?.program.toUpperCase() || "—";
    document.querySelector("#account-view-year").textContent = s ? s.year + " курс" : "—";
    document.querySelector("#account-view-sub").textContent = s?.sub || "Все доступные";
    const names = Array.isArray(profile?.subject_names) ? profile.subject_names : [];
    document.querySelector("#account-view-subjects").textContent = names.join(", ")
      || (s?.subjects?.length ? "Выбрано дисциплин: " + s.subjects.length : "Предметы группы");
    const avatar = document.querySelector("#account-avatar");
    avatar.textContent = name ? name.split(" ").slice(0, 2).map((part) => part[0]).join("").toUpperCase() : "?";
  }

  function selectedYear() {
    return catalog?.programs.find((p) => p.id === program.value)?.years.find((y) => y.year === Number(year.value));
  }

  function fillGroup(selection = null) {
    const y = selectedYear();
    options(group, (y?.groups || []).map((value) => [value, value]),
      y?.groups.length ? "Выберите группу" : "Общие материалы курса");
    group.disabled = !y?.groups.length;
    group.required = !!y?.groups.length;
    group.value = selection?.group || "";
    options(sub, (y?.subgroups || []).map((value) => [value, value]), "Все доступные");
    sub.disabled = !y?.subgroups.length;
    sub.value = selection?.sub || "";
    subjectsList.replaceChildren(...(y?.subjects || []).map((id) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "subjects";
      input.value = id;
      input.checked = !!selection?.subjects?.includes(id);
      label.append(input, document.createTextNode(y.names[id] || id));
      return label;
    }));
    document.querySelector("#signup-subject-options").hidden = !y?.subjects.length;
  }

  function fillYear(selection = null) {
    const p = catalog?.programs.find((item) => item.id === program.value);
    options(year, (p?.years || []).map((value) => [value.year, value.label]), "Выберите курс");
    year.disabled = !p;
    year.value = selection?.year || "";
    fillGroup(selection);
  }

  async function loadCatalog() {
    if (loadingCatalog) return loadingCatalog;
    loadingCatalog = (async () => {
      fields.disabled = true;
      submit.disabled = true;
      retry.hidden = true;
      message.textContent = "Загружаем программы и группы DSBA…";
      try {
        catalog = catalog || await fetchDsbaCatalog();
        options(program, catalog.programs.map((p) => [p.id, p.name]), "Выберите программу");
        program.value = saved?.selection.program || "";
        fillYear(saved?.selection);
        fields.disabled = false;
        submit.disabled = false;
        message.textContent = "После входа задания и материалы загрузятся автоматически.";
      } catch (e) {
        catalog = null;
        showError(e.message);
        message.textContent = "Не удалось загрузить список программ. Повторите попытку.";
        retry.hidden = false;
      }
    })();
    try { await loadingCatalog; } finally { loadingCatalog = null; }
  }

  function open(edit = false) {
    if (dialog.open || (!edit && completeProfile(profile, saved))) return;
    form.elements.name.value = normalizeName(profile?.name);
    error.hidden = true;
    cancel.hidden = !completeProfile(profile, saved);
    dialog.showModal();
    loadCatalog();
  }

  async function resume() {
    if (resumeRunning || dialog.open || !completeProfile(profile, saved)) return;
    resumeRunning = true;
    const token = revision;
    const choice = saved;
    try {
      const result = await syncDsba(choice.selection);
      if (token !== revision) return;
      if (!validResult(result)) throw new Error("Не удалось обновить DSBA.");
      const changed = choice.source_id !== result.source_id;
      saved = { ...choice, source_id: result.source_id };
      profile = { ...profile, dsba: saved };
      persistProfile();
      renderProfile();
      await onConnected(result.source_id, changed);
    } catch (e) {
      if (token !== revision) return;
      if (e.status === 400) {
        catalog = null;
        open(true);
        showError(e.message);
      } else {
        clearTimeout(resumeTimer);
        resumeTimer = setTimeout(resume, 60000);
      }
    } finally { resumeRunning = false; }
  }

  program.addEventListener("change", () => fillYear());
  year.addEventListener("change", () => fillGroup());
  retry.addEventListener("click", () => { error.hidden = true; loadCatalog(); });
  cancel.addEventListener("click", () => { if (!submitting) dialog.close(); });
  dialog.addEventListener("cancel", (event) => {
    if (submitting || !completeProfile(profile, saved)) event.preventDefault();
  });
  document.querySelector("#edit-profile-button").addEventListener("click", () => open(true));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !catalog) return;
    const name = normalizeName(form.elements.name.value);
    if (name.split(" ").length < 2) {
      showError("Напишите фамилию и имя, можно с отчеством.");
      return;
    }
    const selection = selectionFromCatalog(catalog, {
      program: program.value, year: year.value, group: group.value, sub: sub.value,
      subjects: [...subjectsList.querySelectorAll("input:checked")].map((input) => input.value),
    });
    if (!selection) {
      showError("Выберите программу, курс и группу из списка DSBA.");
      return;
    }
    submitting = true;
    revision += 1;
    clearTimeout(resumeTimer);
    let accepted = false;
    error.hidden = true;
    fields.disabled = true;
    submit.disabled = true;
    cancel.disabled = true;
    form.setAttribute("aria-busy", "true");
    message.textContent = "Загружаем задания и материалы вашей группы…";
    submit.textContent = "Загружаем…";
    try {
      const result = await syncDsba(selection);
      if (!validResult(result)) throw new Error("Сервер вернул неполный ответ. Попробуйте ещё раз.");
      const resetPersonal = saved?.source_id !== result.source_id;
      const choice = { source_id: result.source_id, selection };
      const y = selectedYear();
      profile = {
        name, group: selection.group, dsba: choice,
        program_name: catalog.programs.find((p) => p.id === selection.program).name,
        subject_names: selection.subjects.map((id) => y.names[id] || id),
      };
      saved = choice;
      accepted = true;
      resumed = true;
      persistProfile();
      if (resetPersonal) clearPersonalData();
      renderProfile();
      await onConnected(result.source_id, resetPersonal);
      dialog.close();
    } catch (e) {
      showError(e.message);
      if (!accepted && e.status === 400) {
        catalog = null;
        retry.hidden = false;
      }
      message.textContent = accepted ? "Профиль сохранён. Обновите страницу для загрузки данных."
        : "Профиль не изменён. Проверьте данные и повторите вход.";
    } finally {
      submitting = false;
      fields.disabled = !catalog;
      submit.disabled = !catalog;
      cancel.disabled = false;
      form.removeAttribute("aria-busy");
      submit.textContent = "Войти";
    }
  });

  document.querySelector("#logout-button").addEventListener("click", async () => {
    revision += 1;
    resumed = false;
    clearTimeout(resumeTimer);
    for (const key of [PROFILE_KEY, SELECTION_KEY]) {
      try { localStorage.removeItem(key); } catch { /* Reset this tab too. */ }
    }
    profile = null;
    saved = null;
    clearPersonalData();
    renderProfile();
    await onConnected(null, true);
    open();
  });
  renderProfile();
  return { openIfNeeded: () => {
    if (document.body.dataset.demoMode === "true") return;
    if (completeProfile(profile, saved)) {
      if (!resumed) { resumed = true; resume(); }
    } else open();
  } };
}
