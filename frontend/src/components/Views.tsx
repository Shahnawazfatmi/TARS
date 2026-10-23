import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "../api";
import { dayAndTime, Icon, seconds, toolIcon, toolLabel } from "../labels";
import type { Episode, RecentFile, ToolSpec } from "../types";
import { statusOf } from "./RightPanel";

const FILTERS: { label: string; status: string }[] = [
  { label: "All", status: "" },
  { label: "Done", status: "answered" },
  { label: "Waiting", status: "waiting_approval" },
  { label: "Partly done", status: "partial" },
  { label: "Failed", status: "failed" },
];

/** FATMEE's diary of past tasks (GET /episodes). */
export function TasksView() {
  const [filter, setFilter] = useState("");
  const [items, setItems] = useState<Episode[] | null>(null);
  const [found, setFound] = useState(0);
  const [open, setOpen] = useState("");

  useEffect(() => {
    setItems(null);
    api.episodes(100, filter).then((r) => { setItems(r.episodes); setFound(r.found); }).catch(() => setItems([]));
  }, [filter]);

  return (
    <section className="view">
      <header className="view-head">
        <div>
          <h1>Tasks</h1>
          <p className="muted">Every task FATMEE has done, newest first{items ? ` · ${found} found` : ""}.</p>
        </div>
        <div className="segmented" role="tablist">
          {FILTERS.map((f) => (
            <button key={f.label} role="tab" aria-selected={filter === f.status}
              className={filter === f.status ? "seg-active" : ""} onClick={() => setFilter(f.status)}>{f.label}</button>
          ))}
        </div>
      </header>

      {items === null ? <p className="muted">Loading…</p> : !items.length ? <p className="muted">No tasks here yet.</p> : (
        <ul className="task-list">
          {items.map((e) => {
            const s = statusOf(e.status);
            const expanded = open === e.task_id;
            return (
              <li key={e.task_id} className={`task-row ${expanded ? "task-open" : ""}`}>
                <button className="task-summary" onClick={() => setOpen(expanded ? "" : e.task_id)} aria-expanded={expanded}>
                  <span className={`activity-status tone-${s.tone}`} title={s.text}><Icon name={s.icon} size={14} /></span>
                  <span className="task-question">{e.question}</span>
                  <span className="task-meta">{dayAndTime(e.started_at)}{e.duration_s ? ` · ${seconds(e.duration_s)}` : ""}</span>
                </button>
                {expanded && (
                  <div className="task-detail">
                    <div className="tool-tags">
                      {e.tools.length ? e.tools.map((t, i) => (
                        <span key={t + i} className="tool-tag"><Icon name={toolIcon(t)} size={13} /> {toolLabel(t)}</span>
                      )) : <span className="tool-tag">Answered from knowledge</span>}
                      <span className={`tool-tag tone-${s.tone}`}>{s.text}</span>
                    </div>
                    {e.summary && (
                      <div className="bubble task-answer">
                        <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>{e.summary}</Markdown>
                      </div>
                    )}
                    {e.problems && <p className="tone-bad small">Problems: {e.problems}</p>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Everything FATMEE can use (GET /tools). */
export function ToolsView() {
  const [items, setItems] = useState<ToolSpec[] | null>(null);

  useEffect(() => {
    api.tools().then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <section className="view">
      <header className="view-head">
        <div>
          <h1>Tools</h1>
          <p className="muted">What FATMEE can do{items ? ` · ${items.length} tools` : ""}. The planner picks them for each task; tools marked "Needs your OK" always wait for you.</p>
        </div>
      </header>

      {items === null ? <p className="muted">Loading…</p> : (
        <div className="tool-grid">
          {items.map((t) => (
            <article key={t.name} className="tool-card">
              <header>
                <span className="quick-icon"><Icon name={toolIcon(t.name)} size={18} /></span>
                <div>
                  <h3>{toolLabel(t.name)}</h3>
                  <code>{t.name}</code>
                </div>
                {t.needs_approval && <span className="tag tag-warn">Needs your OK</span>}
              </header>
              <p>{t.description}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

/** Pictures, reports and practice posts FATMEE made (GET /files/recent). */
export function FilesView({ onZoom }: { onZoom: (src: string) => void }) {
  const [items, setItems] = useState<RecentFile[] | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    api.recentFiles(120).then((r) => setItems(r.files)).catch(() => setItems([]));
  }, []);

  return (
    <section className="view">
      <header className="view-head">
        <div>
          <h1>Files</h1>
          <p className="muted">Pictures, Word reports and practice posts FATMEE made, newest first. They live in the FATMEE folders on your Desktop.</p>
        </div>
      </header>
      {problem && <p className="tone-bad small">{problem}</p>}

      {items === null ? <p className="muted">Loading…</p> : !items.length ? <p className="muted">No files yet. Ask FATMEE to make an image or a report.</p> : (
        <div className="file-grid">
          {items.map((f) => (
            <article key={f.path} className="file-card">
              {f.kind === "image" ? (
                <button className="image-button" onClick={() => onZoom(api.fileUrl(f.path))} title="Show full size">
                  <img src={api.fileUrl(f.path)} alt={f.name} loading="lazy" />
                </button>
              ) : (
                <div className="file-thumb"><Icon name={f.kind === "docx" ? "doc" : "tasks"} size={34} /><span>.{f.kind}</span></div>
              )}
              <div className="file-info">
                <span className="file-name" title={f.name}>{f.name}</span>
                <span className="muted small">{f.folder} · {dayAndTime(f.modified)} · {f.size_kb} KB</span>
              </div>
              <button className="btn btn-small" onClick={() => api.openFile(f.path).catch((e) => setProblem(e.message))}>
                <Icon name="open" size={13} /> Open
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
