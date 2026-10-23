import { Icon, type IconName } from "../labels";
import type { Status } from "../types";
import { TarsCore, type CoreState } from "./TarsCore";

export type View = "chat" | "tasks" | "tools" | "files";

const NAV: { view: View; label: string; icon: IconName }[] = [
  { view: "chat", label: "Chat", icon: "chat" },
  { view: "tasks", label: "Tasks", icon: "tasks" },
  { view: "tools", label: "Tools", icon: "tools" },
  { view: "files", label: "Files", icon: "folder" },
];

export function Sidebar({
  view, onView, status, offline, core,
}: {
  view: View;
  onView: (view: View) => void;
  status: Status | null;
  offline: boolean;
  core: CoreState;
}) {
  const local = status?.llm[0]?.provider === "local";

  return (
    <nav className="sidebar" aria-label="FATMEE">
      <div className="brand">
        <TarsCore state={core} />
        <div className="brand-text">
          <span className="brand-name">FATMEE</span>
          <span className="brand-sub">Your AI Co-Worker</span>
        </div>
      </div>

      <ul className="nav">
        {NAV.map((item) => (
          <li key={item.view}>
            <button className={`nav-item ${view === item.view ? "nav-active" : ""}`} onClick={() => onView(item.view)}
              aria-current={view === item.view ? "page" : undefined}>
              <Icon name={item.icon} size={20} />
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ul>

      <div className="assistant-card">
        <TarsCore state={core} size="large" />
        <p>{core === "working" ? "Working on it…" : core === "waiting" ? "I need your OK." : "Ready to get things done? I'm here."}</p>
        <span className="assistant-sign">— FATMEE</span>
      </div>

      <div className="sidebar-status">
        <span className={`light ${offline ? "light-off" : status ? "light-ok" : "light-warn"}`} aria-hidden="true" />
        <span>{offline ? "Offline" : status ? "Online" : "Connecting…"}</span>
        {status && !offline && (
          <span className="sidebar-model"><Icon name="bolt" size={13} /> {local ? "Local LLM" : "Cloud AI"}</span>
        )}
      </div>
    </nav>
  );
}
