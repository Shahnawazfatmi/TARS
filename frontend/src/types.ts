// Shapes of what FATMEE's API sends (see backend/main.py, progress.py, cards.py).

export type CardType = "research" | "document" | "image" | "post" | "linkedin" | "practice" | "text" | "file";

export interface Card {
  tool: string;
  type: CardType;
  title?: string;
  summary?: string;
  points?: string[];
  sources?: { title: string; url: string }[];
  text?: string;
  characters?: number;
  path?: string;
  file_name?: string;
  folder?: string;
  size?: string;
  model?: string;
  words?: number;
  sections?: number;
  posted?: boolean;
  dry_run?: boolean;
  url?: string;
  as?: string;
  image_path?: string;
  note?: string;
  coverage?: string;
  complete?: boolean;
}

export interface ProgressEvent {
  n: number;
  time: string;
  kind: string;
  message: string;
  data: Record<string, any>;
}

export interface PendingStep {
  step_id: string;
  tool: string;
  action: string;
  args: Record<string, any>;
}

export interface Reply {
  response: string;
  status: "done" | "waiting_approval";
  task_id: string;
  session_id: string;
  tools_used?: string[];
  approvals?: PendingStep[];
  cards?: Card[];
}

export interface WaitingTask {
  task_id: string;
  question: string;
  session_id: string;
  started_at: string;
  pending: PendingStep[];
}

export interface Status {
  llm: { provider: string; model: string }[];
  images: { provider: string; model: string }[];
  linkedin: { connected: boolean; dry_run: boolean; name: string };
  search: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  cards?: Card[];
  taskId?: string | null;
  status?: string;
  pending?: boolean;
  error?: boolean;
}

export type StepStatus = "pending" | "running" | "done" | "failed" | "waiting" | "rejected";

export interface StepState {
  id: string;
  tool: string;
  action: string;
  needsApproval: boolean;
  status: StepStatus;
  seconds?: number;
  startedAt?: number;
  card?: Card | null;
  error?: string | null;
}

export type Phase = "planning" | "running" | "checking" | "replanning" | "answering" | "waiting" | "done" | "failed";

export interface TaskState {
  id: string;
  question: string;
  phase: Phase;
  steps: StepState[];
  startedAt: number;
  endedAt?: number;
  lastEvent: number;
  log: ProgressEvent[];
}
