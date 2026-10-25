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

export async function fetchDashboard(scenario = "default") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const query = new URLSearchParams({ scenario });
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
