import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { ApprovalModal } from "./components/ApprovalModal";
import { Backdrop } from "./components/Backdrop";
import { Chat } from "./components/Chat";
import { Header } from "./components/Header";
import { RightPanel } from "./components/RightPanel";
import { Sidebar, type View } from "./components/Sidebar";
import { coreState } from "./components/TaskPanel";
import { FilesView, TasksView, ToolsView } from "./components/Views";
import { applyEvent, newTask } from "./task";
import type { Card, ChatMessage, PendingStep, Reply, Status, TaskState } from "./types";

const SESSION_KEY = "fatmee.session";

function loadSession() {
  try {
    return localStorage.getItem(SESSION_KEY) || "default";
  } catch {
    return "default";
  }
}

function saveSession(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    /* private mode: the session lasts until reload */
  }
}

interface Waiting {
  taskId: string;
  question: string;
  pending: PendingStep[];
}

export default function App() {
  const [sessionId, setSessionId] = useState(loadSession);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [task, setTask] = useState<TaskState | null>(null);
  const [waiting, setWaiting] = useState<Waiting | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  const [zoom, setZoom] = useState("");
  // Each page has its own link: /app/#tasks, #tools, #files.
  const [view, setViewState] = useState<View>(() => {
    const hash = window.location.hash.slice(1);
    return (["chat", "tasks", "tools", "files"] as View[]).includes(hash as View) ? (hash as View) : "chat";
  });
  const setView = useCallback((next: View) => {
    setViewState(next);
    try {
      history.replaceState(null, "", next === "chat" ? window.location.pathname : `#${next}`);
    } catch {
      /* not allowed here: the page still switches */
    }
  }, []);
  const [draft, setDraft] = useState("");
  // Bumped when a task finishes, so Recent Activity reloads.
  const [finished, setFinished] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stopFollowing = useRef<() => void>(() => {});

  // Header status, refreshed every 30 s.
  useEffect(() => {
    const load = () => api.status().then((s) => { setStatus(s); setOffline(false); }).catch(() => setOffline(true));
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, []);

  // The conversation so far, and any task still waiting for your OK.
  useEffect(() => {
    api.history(sessionId).then(setMessages).catch(() => setMessages([]));
    api.waitingTasks().then(({ tasks }) => {
      const mine = tasks.find((t) => t.session_id === sessionId) || tasks[0];
      if (mine) setWaiting({ taskId: mine.task_id, question: mine.question, pending: mine.pending });
    }).catch(() => {});
  }, [sessionId]);

  const followTask = useCallback((taskId: string, after: number) => {
    stopFollowing.current();
    stopFollowing.current = api.follow(taskId, after, (event) => {
      setTask((current) => (current && current.id === taskId ? applyEvent(current, event) : current));
    });
  }, []);

  const cards = (task?.steps || []).map((s) => s.card).filter((c): c is Card => !!c);

  function showReply(reply: Reply, question: string) {
    setMessages((list) => [
      ...list.filter((m) => !m.pending),
      { role: "assistant", content: reply.response, cards: reply.status === "done" ? reply.cards : [], taskId: reply.task_id, status: reply.status, time: new Date().toISOString() },
    ]);
    setFinished((n) => n + 1);

    if (reply.status === "waiting_approval" && reply.approvals?.length) {
      setWaiting({ taskId: reply.task_id, question, pending: reply.approvals });
    }
  }

  function showError(error: unknown) {
    setMessages((list) => [
      ...list.filter((m) => !m.pending),
      { role: "assistant", content: `Something went wrong: ${error instanceof Error ? error.message : String(error)}`, error: true, time: new Date().toISOString() },
    ]);
    setTask((current) => (current ? { ...current, phase: "failed", endedAt: Date.now() } : current));
  }

  async function send(question: string) {
    const taskId = api.newId();

    setBusy(true);
    setTask(newTask(taskId, question));
    setView("chat");
    setMessages((list) => [...list, { role: "user", content: question, time: new Date().toISOString() }, { role: "assistant", content: "Working on it", pending: true }]);

    // Follow first, so no event is missed while /ask runs.
    followTask(taskId, 0);

    try {
      showReply(await api.ask(question, sessionId, taskId), question);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function decideWaiting(decision: "approve" | "reject") {
    if (!waiting) return;
    const { taskId, question } = waiting;

    setDeciding(true);

    // A task from before a reload has no panel yet: start a fresh one.
    setTask((current) =>
      current && current.id === taskId ? { ...current, phase: "running" } : { ...newTask(taskId, question), phase: "running" },
    );

    setMessages((list) => [
      ...list,
      { role: "user", content: decision === "approve" ? "Approved." : "Rejected.", time: new Date().toISOString() },
      { role: "assistant", content: decision === "approve" ? "Continuing" : "Skipping that step", pending: true },
    ]);
    setWaiting(null);
    setBusy(true);

    // Events already applied are skipped, so following from the start is safe.
    followTask(taskId, 0);

    try {
      showReply(await api.decide(taskId, decision), question);
    } catch (error) {
      showError(error);
    } finally {
      setDeciding(false);
      setBusy(false);
    }
  }

  function newChat() {
    stopFollowing.current();
    const id = `chat-${api.newId().slice(0, 12)}`;
    saveSession(id);
    setSessionId(id);
    setTask(null);
    setWaiting(null);
    setView("chat");
  }

  function pick(text: string) {
    setView("chat");
    setDraft(text);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  const core = waiting ? "waiting" : busy ? "working" : task ? coreState(task.phase) === "failed" ? "failed" : "idle" : "idle";
  const titles: Record<View, string> = {
    chat: "Multi-agent AI coworker for your desktop", tasks: "Task history", tools: "What FATMEE can do", files: "Files FATMEE made",
  };

  return (
    <div className="app">
      <Backdrop mood={waiting ? "waiting" : busy ? "working" : "idle"} />
      <Sidebar view={view} onView={setView} status={status} offline={offline} core={core} />

      <main className="main">
        <Header title={titles[view]} busy={busy} offline={offline} onNewChat={newChat} />
        {view === "chat" && (
          <Chat messages={messages} busy={busy} draft={draft} onDraft={setDraft} inputRef={inputRef} onSend={send} onZoom={setZoom} />
        )}
        {view === "tasks" && <TasksView />}
        {view === "tools" && <ToolsView />}
        {view === "files" && <FilesView onZoom={setZoom} />}
      </main>

      <RightPanel task={task} cards={cards} refresh={finished} onZoom={setZoom} onPick={pick} onViewAll={() => setView("tasks")} />

      {waiting && !deciding && (
        <ApprovalModal
          question={waiting.question}
          pending={waiting.pending}
          live={!!status && !status.linkedin.dry_run}
          busy={deciding}
          onDecide={decideWaiting}
        />
      )}

      {zoom && (
        <div className="overlay lightbox" onClick={() => setZoom("")} role="dialog" aria-label="Image, full size">
          <img src={zoom} alt="Full size" />
        </div>
      )}
    </div>
  );
}
