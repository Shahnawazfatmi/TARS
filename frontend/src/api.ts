// FATMEE's API. In dev the screen calls /api (Vite forwards it to :8000);
// when FATMEE serves the built screen at /app, the API is on the same origin.

import type { ChatMessage, Episode, ProgressEvent, RecentFile, Reply, Status, SystemInfo, ToolSpec, WaitingTask } from "./types";

export const API = import.meta.env.DEV ? "/api" : "";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.response || (typeof body.detail === "string" ? body.detail : body.detail?.message) || detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }

  return response.json();
}

export const ask = (question: string, sessionId: string, taskId: string) =>
  call<Reply>("/ask", { method: "POST", body: JSON.stringify({ question, session_id: sessionId, task_id: taskId }) });

export const decide = (taskId: string, decision: "approve" | "reject") =>
  call<Reply>(`/approvals/${taskId}`, { method: "POST", body: JSON.stringify({ decision }) });

export const waitingTasks = () => call<{ waiting: number; tasks: WaitingTask[] }>("/approvals");

export const status = () => call<Status>("/status");

export const episodes = (limit = 20, status = "") =>
  call<{ found: number; episodes: Episode[] }>(`/episodes?limit=${limit}${status ? `&status=${status}` : ""}`);

export const tools = () => call<ToolSpec[]>("/tools");

export const system = () => call<SystemInfo>("/system");

export const recentFiles = (limit = 60) => call<{ files: RecentFile[]; total: number }>(`/files/recent?limit=${limit}`);

export const openFile = (path: string) =>
  call<{ opened: string }>("/files/open", { method: "POST", body: JSON.stringify({ path }) });

export const fileUrl = (path: string) => `${API}/files?path=${encodeURIComponent(path)}`;

export async function history(sessionId: string): Promise<ChatMessage[]> {
  const body = await call<{ messages: any[] }>(`/sessions/${sessionId}`);

  return body.messages.map((m) => ({
    role: m.role,
    content: m.content,
    time: m.created_at,
    taskId: m.task_id,
    cards: m.meta?.cards || [],
    status: m.meta?.status,
  }));
}

export const newId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");

/**
 * Follow a task's live events (Server-Sent Events). Any event name is
 * accepted, so new kinds from the backend show up without changes here.
 * Returns a function that stops following.
 */
export function follow(taskId: string, after: number, onEvent: (event: ProgressEvent) => void): () => void {
  const controller = new AbortController();

  (async () => {
    let last = after;

    // Reconnect if the connection drops before the task pauses or ends.
    for (let attempt = 0; attempt < 5 && !controller.signal.aborted; attempt++) {
      try {
        const response = await fetch(`${API}/tasks/${taskId}/stream?after=${last}`, { signal: controller.signal });
        const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;

          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            const data = block.split("\n").find((line) => line.startsWith("data: "));
            if (!data) continue;

            const event: ProgressEvent = JSON.parse(data.slice(6));
            last = event.n;
            onEvent(event);

            if (["done", "waiting_approval", "failed"].includes(event.kind)) return;
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      }

      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  })();

  return () => controller.abort();
}
