import { Icon } from "../labels";

export function Header({ title, busy, offline, onNewChat }: { title: string; busy: boolean; offline: boolean; onNewChat: () => void }) {
  return (
    <header className={`topbar ${busy ? "topbar-busy" : ""}`}>
      <span className="topbar-spark"><Icon name="sparkle" size={20} /></span>
      <span className="topbar-title">{title}</span>
      {offline && <span className="pill pill-bad"><span className="light light-off" aria-hidden="true" /> Start FATMEE on port 8000</span>}
      <button className="btn btn-ghost" onClick={onNewChat}><Icon name="plus" size={15} /> New chat</button>
    </header>
  );
}
