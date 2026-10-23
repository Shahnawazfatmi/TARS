// Turns FATMEE's live events into the task panel's state: which steps
// exist, which are running, done, failed or waiting for your OK.

import type { ProgressEvent, StepState, TaskState } from "./types";

export function newTask(id: string, question: string): TaskState {
  return { id, question, phase: "planning", steps: [], startedAt: Date.now(), lastEvent: 0, log: [] };
}

function upsert(steps: StepState[], id: string, tool: string, change: Partial<StepState>): StepState[] {
  const found = steps.some((s) => s.id === id);

  if (!found) {
    // Recovery steps (r1, r2...) are not in the first plan.
    return [...steps, { id, tool, action: "", needsApproval: false, status: "pending", ...change }];
  }

  return steps.map((s) => (s.id === id ? { ...s, ...change } : s));
}

export function applyEvent(task: TaskState, event: ProgressEvent): TaskState {
  if (event.n <= task.lastEvent) return task;

  const next: TaskState = { ...task, lastEvent: event.n, log: [...task.log, event] };
  const data = event.data || {};

  switch (event.kind) {
    case "planning":
      return { ...next, phase: "planning" };

    case "planned":
      return {
        ...next,
        phase: "running",
        steps: (data.steps || []).map((s: any) => ({
          id: s.id, tool: s.tool, action: s.action || "", needsApproval: !!s.needs_approval, status: "pending",
        })),
      };

    case "wave": {
      let steps = next.steps;
      for (const s of data.steps || []) steps = upsert(steps, s.id, s.tool, { status: "running", startedAt: Date.now() });
      return { ...next, phase: "running", steps };
    }

    case "step":
      return {
        ...next,
        steps: upsert(next.steps, data.id, data.tool, {
          status: data.ok ? "done" : String(event.message).includes("rejected") ? "rejected" : "failed",
          seconds: data.seconds,
          card: data.card,
          error: data.error,
        }),
      };

    case "approval_needed":
      return { ...next, steps: upsert(next.steps, data.step_id, data.tool, { status: "waiting", needsApproval: true }) };

    case "waiting_approval":
      return { ...next, phase: "waiting" };

    case "approved":
      return { ...next, phase: "running" };

    case "replanning":
      return { ...next, phase: "replanning" };

    case "checking":
      return { ...next, phase: "checking" };

    case "answering":
      return { ...next, phase: "answering" };

    case "done":
      return { ...next, phase: "done", endedAt: Date.now() };

    case "failed":
      return { ...next, phase: "failed", endedAt: Date.now() };

    default:
      return next;
  }
}

export const PHASE_TEXT: Record<TaskState["phase"], string> = {
  planning: "Planning the steps",
  running: "Working",
  checking: "Checking the results",
  replanning: "Something was missing, trying another way",
  answering: "Writing the answer",
  waiting: "Waiting for your OK",
  done: "Done",
  failed: "Something went wrong",
};
