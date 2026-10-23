import type { Status } from "../types";

export function Header({ status, offline, onNewChat }: { status: Status | null; offline: boolean; onNewChat: () => void }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true"><span /><span /></span>
        <span className="brand-name">FATMEE</span>
        <span className="brand-tagline">Multi-agent AI coworker for your desktop</span>
      </div>

      <div className="status-strip">
        {offline ? (
          <span className="pill pill-bad"><span className="light light-off" aria-hidden="true" /> Offline: start FATMEE on port 8000</span>
        ) : (
          <span className="pill">
            <span className={`light ${status ? "light-ok" : "light-warn"}`} aria-hidden="true" />
            {status ? "Online" : "Connecting…"}
          </span>
        )}
      </div>

      <button className="btn btn-ghost" onClick={onNewChat}>New chat</button>
    </header>
  );
}
