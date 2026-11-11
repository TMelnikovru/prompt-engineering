// MediaWiki action=parse, formatversion=2. Shape verified against wiki.cs.hse.ru.
// Course text is synthetic; no credentials or real student data.
const http = require("http");
let revision = 1;
let mode = "ok";
let requests = 0;
function reply(res, status, value) {
  res.writeHead(status, { "Content-Type": "application/json" });
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
    if (data.reset) { requests = 0; revision = 1; mode = "ok"; }
    if (data.revision) revision = data.revision;
    if (data.mode) mode = data.mode;
    return reply(res, 200, { revision, mode, requests });
  }
  if (url.pathname === "/__stats") return reply(res, 200, { requests });
  if (url.pathname !== "/api.php") return reply(res, 501, { error: "not_modeled" });
  requests++;
  if (mode === "error") {
    res.writeHead(500, { "Content-Type": "text/html" });
    return res.end("<html>Upstream unavailable</html>");
  }
  if (mode === "malformed") return reply(res, 200, []);
  if (mode === "slow") await new Promise((resolve) => setTimeout(resolve, 250));
  if (url.searchParams.get("action") !== "parse" || url.searchParams.get("formatversion") !== "2") {
    return reply(res, 400, { error: { code: "invalid_request" } });
  }
  const title = url.searchParams.get("page");
  const home = ["Заглавная страница", "Wiki ФКН"].includes(title);
  if (!home && title !== "Тестовый курс") {
    return reply(res, 200, { error: { code: "missingtitle" } });
  }
  const page = {
    title: home ? "Wiki ФКН" : "Тестовый курс",
    pageid: home ? 1 : 2,
    revid: home ? 10 : revision + 20,
    displaytitle: home ? "Wiki ФКН" : "Тестовый курс",
    text: home
      ? '<div class="mw-parser-output"><h1>Учебные курсы</h1><a href="/Тестовый_курс">Тестовый курс</a><script>untrusted()</script></div>'
      : '<div class="mw-parser-output"><h1>Тестовый курс</h1><p>Домашняя работа 1: сдайте '
        + (revision === 1 ? "30" : revision >= 4 ? "28" : "29") + ' октября 2026 года.</p><a href="/lecture.pdf">Конспект</a></div>',
    links: home ? [
      { ns: 0, title: "Тестовый курс", exists: true },
      { ns: 0, title: "Отсутствующая страница", exists: false },
      { ns: 14, title: "Категория:Курсы", exists: true },
    ] : [],
  };
  reply(res, 200, { parse: page });
});
server.listen(Number(process.env.PORT || 4511), "127.0.0.1", () => {
  console.log(JSON.stringify({ port: server.address().port }));
});
