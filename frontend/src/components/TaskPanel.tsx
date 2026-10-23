import { useEffect, useState } from "react";
import { Icon, seconds, ToolIcon, toolLabel } from "../labels";
import { PHASE_TEXT } from "../task";
import type { Card, StepState, TaskState } from "../types";
import { CardGrid } from "./Cards";
import { TarsCore, type CoreState } from "./TarsCore";

const STAGES: { key: string; label: string }[] = [
  { key: "planning", label: "Plan" },
  { key: "running", label: "Run" },
  { key: "checking", label: "Check" },
  { key: "answering", label: "Answer" },
];

export function coreState(phase: TaskState["phase"]): CoreState {
  if (phase === "waiting" || phase === "done" || phase === "failed") return phase;
  return "working";
}

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
      <span className="step-light" aria-hidden="true">{step.status === "done" && <svg viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6 9.6 3.6" /></svg>}</span>
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

/** The live task: stages, steps with timers, results and the activity log. */
export function MissionControl({ task, cards, onZoom }: { task: TaskState | null; cards: Card[]; onZoom: (src: string) => void }) {
  const live = !!task && !["done", "failed", "waiting"].includes(task.phase);
  useTick(live);

  if (!task) {
    return (
      <section className="panel-card mission">
        <header className="panel-card-head">
          <span className="mission-icon"><TarsCore state="idle" /></span>
          <h2>Mission Control</h2>
          <span className="light light-ok" title="Ready" />
        </header>
        <p className="muted small">Give FATMEE a task. Each step shows up here live: searching, writing, making images, posting. Anything public waits for your OK.</p>
      </section>
    );
  }

  const total = ((task.endedAt || Date.now()) - task.startedAt) / 1000;

  return (
    <section className={`panel-card mission mission-${task.phase}`}>
      <header className="panel-card-head">
        <span className="mission-icon"><TarsCore state={coreState(task.phase)} /></span>
        <h2>Mission Control</h2>
        <span className="panel-clock">{seconds(total)}</span>
      </header>

      <div className="mission-status">{PHASE_TEXT[task.phase]}</div>
      <p className="mission-question" title={task.question}>{task.question}</p>

      <ol className="stages">
        {STAGES.map((s) => <li key={s.key} className={`stage stage-${stageState(task, s.key)}`}>{s.label}</li>)}
      </ol>

      {task.steps.length ? (
        <ul className="steps">{task.steps.map((step) => <StepRow key={step.id} step={step} />)}</ul>
      ) : (
        <p className="muted small">{task.phase === "planning" ? "Working out the plan…" : "No tools needed: answering from knowledge."}</p>
      )}

      {!!cards.length && (
        <div className="mission-results">
          <h3 className="section-label">Results</h3>
          <CardGrid cards={cards} onZoom={onZoom} />
        </div>
      )}

      <details className="log">
        <summary className="section-label"><Icon name="tasks" size={12} /> Activity log ({task.log.length})</summary>
        <ul>
          {task.log.map((e) => <li key={e.n}><span className="log-time">{e.time}</span> {e.message}</li>)}
        </ul>
      </details>
    </section>
  );
}
