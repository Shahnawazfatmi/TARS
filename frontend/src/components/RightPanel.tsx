import { useEffect, useState } from "react";
import * as api from "../api";
import { dayAndTime, Icon, QUICK_ACTIONS, toolIcon, toolLabel } from "../labels";
import type { Card, Episode, SystemInfo, TaskState } from "../types";
import { MissionControl } from "./TaskPanel";

const STATUS_ICON: Record<string, { icon: "check" | "clock" | "x"; tone: string; text: string }> = {
  answered: { icon: "check", tone: "ok", text: "Done" },
  partial: { icon: "clock", tone: "warn", text: "Partly done" },
  waiting_approval: { icon: "clock", tone: "warn", text: "Waiting for approval" },
  failed: { icon: "x", tone: "bad", text: "Failed" },
};

export function statusOf(status: string) {
  return STATUS_ICON[status] || { icon: "clock" as const, tone: "warn", text: status };
}

function RecentActivity({ refresh, onViewAll }: { refresh: number; onViewAll: () => void }) {
  const [items, setItems] = useState<Episode[] | null>(null);

  useEffect(() => {
    const load = () => api.episodes(5).then((r) => setItems(r.episodes)).catch(() => setItems([]));
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <section className="panel-card">
      <header className="panel-card-head">
        <h2>Recent Activity</h2>
        <button className="link-button" onClick={onViewAll}>View all <Icon name="arrow" size={13} /></button>
      </header>

      {items === null ? (
        <p className="muted small">Loading…</p>
      ) : !items.length ? (
        <p className="muted small">No tasks yet. Your finished tasks show up here.</p>
      ) : (
        <ul className="activity">
          {items.map((e) => {
            const s = statusOf(e.status);
            const tool = e.tools[0];
            return (
              <li key={e.task_id} className="activity-item">
                <span className="activity-icon"><Icon name={tool ? toolIcon(tool) : "chat"} size={17} /></span>
                <div className="activity-text">
                  <span className="activity-title" title={e.question}>{e.question}</span>
                  <span className="activity-sub">{tool ? toolLabel(tool) : "Answered from knowledge"} · {dayAndTime(e.started_at)}</span>
                </div>
                <span className={`activity-status tone-${s.tone}`} title={s.text}><Icon name={s.icon} size={14} /></span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function QuickTools({ onPick }: { onPick: (text: string) => void }) {
  return (
    <section className="panel-card">
      <header className="panel-card-head"><h2>Quick Tools</h2></header>
      <div className="quick-grid">
        {QUICK_ACTIONS.filter((a) => ["Research", "Generate image", "Create doc", "Read a file"].includes(a.short)).map((a) => (
          <button key={a.label} className="quick-tool" onClick={() => onPick(a.text)}>
            <span className="quick-icon"><Icon name={a.icon} size={18} /></span>
            <span className="quick-text"><b>{a.short}</b><small>{a.hint}</small></span>
          </button>
        ))}
      </div>
    </section>
  );
}

function SystemStatus() {
  const [info, setInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    const load = () => api.system().then(setInfo).catch(() => setInfo(null));
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, []);

  const ram = info?.ram.usage_percent;
  const battery = info?.battery;
  const net = info?.internet;

  return (
    <section className="panel-card">
      <header className="panel-card-head"><h2>System Status</h2></header>
      <ul className="sys">
        <li>
          <span className="sys-icon"><Icon name="chip" size={17} /></span>
          <div className="sys-main">
            <span>RAM usage</span>
            <span className="meter"><span style={{ width: `${ram ?? 0}%` }} className={ram !== undefined && ram > 85 ? "meter-hot" : ""} /></span>
          </div>
          <b className="sys-value">{ram !== undefined ? `${Math.round(ram)}%` : "—"}</b>
        </li>
        <li>
          <span className="sys-icon"><Icon name="battery" size={17} /></span>
          <div className="sys-main"><span>Battery</span></div>
          <b className="sys-value">
            {!battery || battery.error ? "—" : !battery.has_battery ? "No battery" : `${battery.percent}% · ${battery.plugged_in ? "Charging" : "On battery"}`}
          </b>
        </li>
        <li>
          <span className="sys-icon"><Icon name="wifi" size={17} /></span>
          <div className="sys-main"><span>Network</span></div>
          <b className={`sys-value ${net && !net.online ? "tone-bad" : ""}`}>
            {!net || net.error ? "—" : net.online ? `Connected · ${net.latency_ms} ms` : "Offline"}
          </b>
        </li>
      </ul>
    </section>
  );
}

export function RightPanel({
  task, cards, refresh, onZoom, onPick, onViewAll,
}: {
  task: TaskState | null;
  cards: Card[];
  refresh: number;
  onZoom: (src: string) => void;
  onPick: (text: string) => void;
  onViewAll: () => void;
}) {
  return (
    <aside className="right-panel">
      <MissionControl task={task} cards={cards} onZoom={onZoom} />
      <RecentActivity refresh={refresh} onViewAll={onViewAll} />
      <QuickTools onPick={onPick} />
      <SystemStatus />
    </aside>
  );
}
