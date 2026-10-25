// Public response shapes observed on dsba.online on 2026-10-25.
// All content below is synthetic and explicitly used only for tests.
const http = require("http");
let revision = 1;
let mode = "ok";
const requests = [];
function reply(res, code, value) {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}
async function body(req) {
  if (req.body) return req.body;
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/__control") {
    const data = await body(req);
    if (data.mode) mode = data.mode;
    if (data.revision) revision = data.revision;
    return reply(res, 200, { mode, revision, requests });
  }
  if (url.pathname === "/__stats") return reply(res, 200, { requests });
  requests.push(url.pathname + url.search);
  if (mode === "slow") await new Promise((resolve) => setTimeout(resolve, 250));
  if (mode === "auth") return reply(res, 401, { error: "Need login" });
  if (url.pathname === "/api/config") {
    if (mode === "catalog-error") return reply(res, 503, { error: "Unavailable" });
    return reply(res, 200, {
      access: "open", academicYear: "2026/2027", catalog: [{ id: "dsba", name: "DSBA", years: [{
        year: 1, label: "1 курс", groups: ["261", "262"], subgroups: ["1", "2"],
        subjects: ["cpp", "calculus"], names: { cpp: "C++", calculus: "Calculus" },
      }] }],
    });
  }
  if (!['/api/subjects', '/api/materials', '/api/deadlines'].includes(url.pathname)) {
    return reply(res, 501, { error: "not_modeled" });
  }
  if (url.searchParams.get("program") !== "dsba" || url.searchParams.get("year") !== "1") {
    return reply(res, 400, { error: "Invalid selection" });
  }
  if (mode === "all-error" || (mode === "deadline-error" && url.pathname === "/api/deadlines")) {
    res.writeHead(500, { "Content-Type": "text/html" });
    return res.end("<h1>Upstream temporarily unavailable</h1>");
  }
  if (url.pathname === "/api/subjects") return reply(res, 200, [
    { id: "cpp", name: "C++", wiki: "https://wiki.cs.hse.ru/Test" },
    { id: "calculus", name: "Calculus" },
  ]);
  if (url.pathname === "/api/materials" && mode === "no-materials") return reply(res, 200, []);
  if (url.pathname === "/api/materials") return reply(res, 200, [
    { id: 53, kind: "lecture", subject_id: "cpp", number: 1, title: null,
      url: "https://example.test/lecture.pdf", ext: "pdf", has_file: 1, source: "wiki",
      created_at: "2026-10-20T08:00:00.000Z", updated_at: "2026-10-20T08:00:00.000Z" },
  ]);
  if (mode === "malformed") return reply(res, 200, { unexpected: [] });
  const first = {
    id: 31, subject_id: "cpp", type: "hw", title: "Учебная лабораторная 2", number: 2,
    due_at: `2026-10-${revision > 1 ? "29" : "28"}T20:59:00.000Z`, all_day: 0,
    url: "https://example.test/lab02.pdf", details: "Тестовые данные симуляции", source: "wiki",
    status: "сдано", personal: 0, group: url.searchParams.get("group"),
    conflict: mode === "conflict" ? { other_due_at: "2026-10-30", source: "manual" } : null,
  };
  if (mode === "invalid-date") first.due_at = "not-a-date";
  return reply(res, 200, mode === "deleted" ? [] : [first,
    { id: 36, subject_id: "cpp", type: "kr", title: "Учебная контрольная", due_at: "2026-11-08", all_day: 1 },
    { id: 37, subject_id: "calculus", type: "hw", title: "Срок пока неизвестен", due_at: null, all_day: 0 },
  ]);
});
server.listen(Number(process.env.PORT || 4513), "127.0.0.1", () => {
  console.log(JSON.stringify({ port: server.address().port }));
});
