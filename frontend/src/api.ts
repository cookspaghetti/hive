import type { JsonRecord } from "./types";

declare global {
  interface Window {
    __HIVE_PANEL_TOKEN__?: string;
  }
}

let panelToken = window.__HIVE_PANEL_TOKEN__ || sessionStorage.getItem("hive-panel-token") || "";
let tokenRefresh: Promise<string> | null = null;

export async function ensurePanelToken({ force = false } = {}): Promise<string> {
  if (panelToken && !force) return panelToken;
  if (!tokenRefresh) {
    tokenRefresh = fetch("/api/panel/session", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Panel authentication is unavailable");
        const data = (await response.json()) as { token?: string };
        if (!data.token) throw new Error("Panel authentication returned no token");
        panelToken = data.token;
        sessionStorage.setItem("hive-panel-token", panelToken);
        return panelToken;
      })
      .finally(() => { tokenRefresh = null; });
  }
  return tokenRefresh;
}

export async function api<T = JsonRecord>(
  path: string,
  options: RequestInit = {},
  retryAuthentication = true,
): Promise<T> {
  const token = await ensurePanelToken();
  const headers = new Headers(options.headers);
  headers.set("X-HIVE-Token", token);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (response.status === 401 && retryAuthentication) {
    await ensurePanelToken({ force: true });
    return api(path, options, false);
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string | { code?: string; message?: string } };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail?.message) detail = payload.detail.message;
      else if (payload.detail?.code) detail = payload.detail.code.replaceAll("_", " ");
    } catch { /* retain HTTP error */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function post<T = JsonRecord>(path: string, payload: JsonRecord = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(payload) });
}

export function put<T = JsonRecord>(path: string, payload: JsonRecord): Promise<T> {
  return api<T>(path, { method: "PUT", body: JSON.stringify(payload) });
}

export function authenticatedUrl(path?: string): string {
  if (!path) return "";
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}token=${encodeURIComponent(panelToken)}`;
}
