import type { ErrorBody } from "./types";

const BASE_KEY = "crucible.apiBase";
const TOKEN_KEY = "crucible.apiToken";

function ls(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function setLs(key: string, val: string): void {
  try {
    if (val) localStorage.setItem(key, val);
    else localStorage.removeItem(key);
  } catch {
    /* private mode — in-memory only for this session */
  }
}

export const auth = {
  getBase: () => ls(BASE_KEY) || "",
  setBase: (v: string) => setLs(BASE_KEY, v.replace(/\/$/, "")),
  getToken: () => ls(TOKEN_KEY),
  setToken: (v: string) => setLs(TOKEN_KEY, v.trim()),
};

export class ApiError extends Error {
  status: number;
  detail?: string | null;
  constructor(status: number, body: ErrorBody | string) {
    const b = typeof body === "string" ? { error: body } : body;
    super(b.error || `HTTP ${status}`);
    this.status = status;
    this.detail = typeof body === "string" ? null : body.detail;
  }
}

export interface FetchOpts {
  method?: string;
  query?: Record<string, string | number | boolean | undefined | (string | number)[]>;
  body?: unknown;
  /** return the raw Response text instead of parsing JSON */
  text?: boolean;
}

function qs(query: FetchOpts["query"]): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const url = auth.getBase() + path + qs(opts.query);
  const headers: Record<string, string> = {};
  const token = auth.getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, { error: "network error", detail: String(e) });
  }

  if (!res.ok) {
    let body: ErrorBody | string;
    try {
      body = (await res.json()) as ErrorBody;
    } catch {
      body = (await res.text()) || res.statusText;
    }
    throw new ApiError(res.status, body);
  }
  if (opts.text) return (await res.text()) as unknown as T;
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}
