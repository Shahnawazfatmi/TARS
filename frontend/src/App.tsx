import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { ApprovalModal } from "./components/ApprovalModal";
import { Chat } from "./components/Chat";
import { Header } from "./components/Header";
import { TaskPanel } from "./components/TaskPanel";
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
      { role: "assistant", content: reply.response, cards: reply.status === "done" ? reply.cards : [], taskId: reply.task_id, status: reply.status },
    ]);

    if (reply.status === "waiting_approval" && reply.approvals?.length) {
      setWaiting({ taskId: reply.task_id, question, pending: reply.approvals });
    }
  }

  function showError(error: unknown) {
    setMessages((list) => [
      ...list.filter((m) => !m.pending),
      { role: "assistant", content: `Something went wrong: ${error instanceof Error ? error.message : String(error)}`, error: true },
    ]);
    setTask((current) => (current ? { ...current, phase: "failed", endedAt: Date.now() } : current));
  }

  async function send(question: string) {
    const taskId = api.newId();

    setBusy(true);
    setTask(newTask(taskId, question));
    setMessages((list) => [...list, { role: "user", content: question }, { role: "assistant", content: "Working on it", pending: true }]);

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
      { role: "user", content: decision === "approve" ? "Approved." : "Rejected." },
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
  }

  return (
    <div className="app">
      <Header status={status} offline={offline} onNewChat={newChat} />

      <div className="layout">
        <Chat messages={messages} busy={busy} onSend={send} onZoom={setZoom} />
        <TaskPanel task={task} cards={cards} onZoom={setZoom} />
      </div>

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
