export type Row = Record<string, any>;
export async function api<T = Row>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...(body !== undefined
      ? {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      message =
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail ?? data);
    } catch {
      /* retain status */
    }
    if (
      response.status === 401 &&
      path !== "/auth/login" &&
      path !== "/auth/me"
    )
      window.dispatchEvent(new Event("sentinel-session-expired"));
    throw new Error(`${response.status}: ${message}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export const text = (value: unknown): string =>
  value == null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
export const pretty = (value: string) => value.toLowerCase().replace(/_/g, " ");
export const when = (value: string) =>
  value ? new Date(value).toLocaleString() : "—";
export const iso = (value: string) => new Date(value).toISOString();
