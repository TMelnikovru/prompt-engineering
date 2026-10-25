// HTTP-клиент: интерфейс не зависит от того, кто реализует Python-сервисы.
// Формат ответов зафиксирован в docs/openapi.json и доступен в /docs.
export class ApiError extends Error {
  constructor(message, status = 0, retryable = true) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryable = retryable;
  }
}

export async function fetchDashboard(scenario = "default", dsbaSourceId = null) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const query = new URLSearchParams({ scenario });
    if (dsbaSourceId) query.set("dsba_source_id", dsbaSourceId);
    const response = await fetch("/api/v1/dashboard?" + query, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const body = await response.json();
    if (!response.ok) {
      throw new ApiError(
        body.error?.message || "Не удалось загрузить данные.",
        response.status,
        body.error?.retryable ?? response.status >= 500,
      );
    }
    return body;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      error.name === "AbortError"
        ? "Сервер отвечает слишком долго. Попробуйте ещё раз."
        : "Не удалось связаться с сервером. Проверьте соединение.",
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function dsbaRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    const body = await response.json();
    if (!response.ok && !body.source_id) {
      throw new ApiError(body.error?.message || "DSBA временно недоступен.", response.status);
    }
    return body;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Не удалось получить данные DSBA. Попробуйте ещё раз.");
  } finally {
    clearTimeout(timeout);
  }
}

export const fetchDsbaCatalog = () => dsbaRequest("/api/v1/integrations/dsba/catalog");
export const syncDsba = (selection) => dsbaRequest("/api/v1/integrations/dsba/sync", {
  method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
  body: JSON.stringify(selection),
});
export const fetchDocuments = (sourceId) => dsbaRequest(
  "/api/v1/documents?" + new URLSearchParams({ source_id: sourceId, limit: "100" }),
);
