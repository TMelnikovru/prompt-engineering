// Stand-in for the documented Responses API structured-output contract.
const http = require("http");
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
  if (req.url === "/__control") {
    const data = await body(req);
    if (data.reset) { mode = "ok"; requests = 0; }
    if (data.mode) mode = data.mode;
    return reply(res, 200, { mode, requests });
  }
  if (req.url === "/__stats") return reply(res, 200, { requests });
  if (req.url !== "/v1/responses" || req.method !== "POST") {
    return reply(res, 501, { error: "not_modeled" });
  }
  requests++;
  if (mode === "rate-limit") return reply(res, 429, { error: { code: "rate_limit_exceeded" } });
  const data = await body(req);
  if (!data.model || data.text?.format?.type !== "json_schema" || !data.text.format.strict) {
    return reply(res, 400, { error: { code: "invalid_request" } });
  }
  const input = JSON.parse(data.input[0].content);
  let result;
  if (data.text.format.name === "ExtractedChunk") {
    const quote = input.source_text.match(/Домашняя работа 1: сдайте \d+ октября 2026 года\./)?.[0];
    result = {
      summary: "Учебные материалы и условия сдачи.", topics: ["Учебный курс"],
      items: quote ? [{
        title: "Домашняя работа 1", kind: "assignment",
        due_text: quote.match(/\d+ октября 2026 года/)[0],
        evidence_quote: mode === "bad-evidence" ? "Такой цитаты в источнике нет." : quote,
      }] : [],
    };
  } else {
    result = {
      summary: "Сводка подготовленных учебных материалов.",
      highlights: [{ text: "Есть материал учебного курса.", document_ids: [input[0].document_id] }],
    };
  }
  reply(res, 200, {
    id: "resp_simulation", object: "response", status: "completed", created_at: 1,
    model: data.model,
    output: [{ type: "message", role: "assistant", content: [
      { type: "output_text", text: JSON.stringify(result), annotations: [] },
    ] }],
    usage: { input_tokens: 120, output_tokens: 50, total_tokens: 170 },
  });
});
server.listen(Number(process.env.PORT || 4512), "127.0.0.1", () => {
  console.log(JSON.stringify({ port: server.address().port }));
});
