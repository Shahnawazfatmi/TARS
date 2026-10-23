import { useEffect, useState } from "react";
import { seconds, ToolIcon, toolLabel } from "../labels";
import { PHASE_TEXT } from "../task";
import type { Card, StepState, TaskState } from "../types";
import { CardGrid } from "./Cards";

const STAGES: { key: string; label: string }[] = [
  { key: "planning", label: "Plan" },
  { key: "running", label: "Run" },
  { key: "checking", label: "Check" },
  { key: "answering", label: "Answer" },
];

function stageState(task: TaskState, key: string) {
  const order = ["planning", "running", "checking", "answering", "done"];
  const phase = task.phase === "waiting" || task.phase === "replanning" ? "running" : task.phase;

  if (phase === "failed") return "idle";
  const current = order.indexOf(phase);
  const index = order.indexOf(key);
  return index < current ? "done" : index === current ? "active" : "idle";
}

function useTick(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setTick((t) => t + 1), 500);
    return () => clearInterval(timer);
  }, [active]);
}

function StepRow({ step }: { step: StepState }) {
  const running = step.status === "running";
  const elapsed = running && step.startedAt ? (Date.now() - step.startedAt) / 1000 : step.seconds;

  return (
    <li className={`step step-${step.status}`}>
      <span className="step-light" aria-hidden="true" />
      <span className="step-icon"><ToolIcon tool={step.tool} /></span>
      <div className="step-text">
        <span className="step-name">{toolLabel(step.tool, running)}{running && <span className="dots" />}</span>
        {step.status === "waiting" && <span className="step-note warn">Needs your OK</span>}
        {step.status === "rejected" && <span className="step-note">Rejected by you</span>}
        {step.status === "failed" && step.error && <span className="step-note bad" title={step.error}>{step.error}</span>}
        {step.status === "pending" && step.action && <span className="step-note">{step.action}</span>}
      </div>
      <span className="step-time">{elapsed !== undefined ? seconds(elapsed) : ""}</span>
    </li>
  );
}

export function TaskPanel({ task, cards, onZoom }: { task: TaskState | null; cards: Card[]; onZoom: (src: string) => void }) {
  const live = !!task && !["done", "failed", "waiting"].includes(task.phase);
  useTick(live);

  if (!task) {
    return (
      <aside className="panel panel-empty">
        <div className="empty">
          <div className="empty-mark" aria-hidden="true"><span /><span /></div>
          <h3>Mission control</h3>
          <p>Give FATMEE a task. Each step shows up here live: searching, writing, making images, posting. Anything public waits for your OK.</p>
        </div>
      </aside>
    );
  }

  const total = ((task.endedAt || Date.now()) - task.startedAt) / 1000;

  return (
    <aside className="panel">
      <header className="panel-head">
        <div className="panel-title">
          <span className={`pulse pulse-${task.phase}`} aria-hidden="true" />
          <span>{PHASE_TEXT[task.phase]}</span>
          <span className="panel-clock">{seconds(total)}</span>
        </div>
        <p className="panel-question" title={task.question}>{task.question}</p>
        <ol className="stages">
          {STAGES.map((s) => <li key={s.key} className={`stage stage-${stageState(task, s.key)}`}>{s.label}</li>)}
        </ol>
      </header>

      <section className="panel-section">
        <h4 className="section-label">Steps</h4>
        {task.steps.length ? (
          <ul className="steps">{task.steps.map((step) => <StepRow key={step.id} step={step} />)}</ul>
        ) : (
          <p className="muted small">{task.phase === "planning" ? "Working out the plan…" : "No tools needed for this one."}</p>
        )}
      </section>

      {!!cards.length && (
        <section className="panel-section">
          <h4 className="section-label">Results</h4>
          <CardGrid cards={cards} onZoom={onZoom} />
        </section>
      )}

      <details className="panel-section log">
        <summary className="section-label">Activity log ({task.log.length})</summary>
        <ul>
          {task.log.map((e) => <li key={e.n}><span className="log-time">{e.time}</span> {e.message}</li>)}
        </ul>
      </details>
    </aside>
  );
}
