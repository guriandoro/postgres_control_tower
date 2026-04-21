/**
 * Tiny typed wrapper over `fetch` for the manager's `/api/v1/*` endpoints.
 *
 * - Reads the JWT from `localStorage` on every call (cheap; lets the auth
 *   context overwrite without prop drilling).
 * - Throws :class:`ApiError` on non-2xx so React Query surfaces it as
 *   `query.error` rather than a misleading "success with bad data".
 * - Calls `onUnauthorized` from the auth bootstrap when the manager
 *   returns 401, so the UI can drop the user back to /login.
 */

const TOKEN_KEY = "pct.jwt";

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string | null): void {
  if (token === null) {
    localStorage.removeItem(TOKEN_KEY);
  } else {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;
  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export type QueryValue = string | number | boolean | undefined | null;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  /**
   * Query string values; ``undefined``/``null``/``""`` entries are dropped.
   *
   * Typed as plain ``object`` (rather than ``Record<string, QueryValue>``)
   * so callers can pass typed filter interfaces (``AlertFilters`` etc.)
   * directly. Interfaces in TS aren't structurally assignable to a
   * ``Record`` with a string index signature even when every property's
   * value type would satisfy it. Each value is coerced via ``String(v)``
   * below, so accidental non-stringifiables degrade gracefully.
   */
  query?: object;
  /** When true, send the body as URL-encoded form (used by /auth/login). */
  form?: boolean;
}

export async function apiRequest<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, query, form } = opts;
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query as Record<string, unknown>)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  const token = getStoredToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      payload = new URLSearchParams(body as Record<string, string>);
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }

  const response = await fetch(url.toString().replace(window.location.origin, ""), {
    method,
    headers,
    body: payload,
  });

  if (response.status === 401 && unauthorizedHandler) {
    unauthorizedHandler();
  }

  const contentType = response.headers.get("content-type") ?? "";
  const data: unknown = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text();

  if (!response.ok) {
    const message =
      (data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : null) ?? `HTTP ${response.status}`;
    throw new ApiError(response.status, message, data);
  }

  return data as T;
}
