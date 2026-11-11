export const PROFILE_KEY = "hse-agent:profile";
export const SELECTION_KEY = "hse-dsba-selection";
// The server decides whether the fictional example exists at all.
export const FAKE_ENABLED = globalThis.document?.body?.dataset.fakeEnabled === "true";

export function normalizeName(value) {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
}

export function validSavedSelection(value) {
  const s = value?.selection;
  return !!(value && /^dsba:[a-f0-9]{32}$/.test(value.source_id) && s
    && typeof s.program === "string" && /^[a-z0-9-]+$/.test(s.program)
    && Number.isInteger(s.year) && s.year >= 1 && s.year <= 6
    && typeof s.group === "string" && typeof s.sub === "string"
    && (s.subjects === undefined || (Array.isArray(s.subjects)
      && s.subjects.every((id) => typeof id === "string"))));
}

export function savedSelection(profile, legacy) {
  const value = profile && Object.hasOwn(profile, "dsba") ? profile.dsba : legacy;
  return validSavedSelection(value) ? value : null;
}

export function completeProfile(profile, choice, fakeEnabled = FAKE_ENABLED) {
  return normalizeName(profile?.name).split(" ").filter(Boolean).length >= 2
    && ((fakeEnabled && profile?.data_source === "fake") || validSavedSelection(choice));
}

export function selectedDataSource(profile, choice, fakeEnabled = FAKE_ENABLED) {
  if (profile?.data_source === "live" || (fakeEnabled && profile?.data_source === "fake")) {
    return profile.data_source;
  }
  return validSavedSelection(choice) || !fakeEnabled ? "live" : "fake";
}

export function selectionFromCatalog(catalog, value) {
  const program = catalog?.programs.find((p) => p.id === value.program);
  const year = program?.years.find((y) => y.year === Number(value.year));
  if (!year || (value.subjects !== undefined && !Array.isArray(value.subjects))) return null;
  const group = value.group || "";
  const sub = value.sub || "";
  const subjects = [...new Set(value.subjects || [])].sort();
  if ((year.groups.length ? !year.groups.includes(group) : group !== "")
    || (sub && !year.subgroups.includes(sub))
    || subjects.some((id) => !year.subjects.includes(id))) return null;
  return { program: program.id, year: year.year, group, sub, subjects };
}
