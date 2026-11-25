import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1",
  timeout: 15000,
});

/** Extracts the backend's consistent error body ({error: {code, message}}) into a readable string. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const body = err.response?.data as { error?: { message?: string; code?: string } } | undefined;
    if (body?.error?.message) return body.error.message;
    if (err.code === "ERR_NETWORK") return "Cannot reach the AgentOps backend";
    return err.message;
  }
  return err instanceof Error ? err.message : "Unknown error";
}

export const get = async <T,>(url: string, params?: Record<string, unknown>): Promise<T> => (await api.get<T>(url, { params })).data;
export const post = async <T,>(url: string, body?: unknown): Promise<T> => (await api.post<T>(url, body)).data;
