import { useEffect, useRef } from "react";
import { fileUrl } from "../api";
import { ToolIcon, toolLabel } from "../labels";
import type { PendingStep } from "../types";

const PUBLIC_TOOLS = ["linkedin_create_post"];

function StepPreview({ step, live }: { step: PendingStep; live: boolean }) {
  const text = step.args.text ?? step.args.post_text;
  const image = step.args.image_path;
  const isPublic = PUBLIC_TOOLS.includes(step.tool);
  const known = new Set(["text", "post_text", "image_path", "alt_text", "title"]);
  const rest = Object.entries(step.args).filter(([key, value]) => !known.has(key) && value !== "" && value != null);

  return (
    <section className="approve-step">
      <div className="approve-tool">
        <ToolIcon tool={step.tool} size={18} />
        <span>{toolLabel(step.tool)}</span>
        {isPublic && (
          <span className={live ? "tag tag-live" : "tag tag-calm"}>{live ? "LIVE: will be published" : "DRY RUN: nothing is published"}</span>
        )}
      </div>

      {step.action && <p className="muted small">{step.action}</p>}

      {(text || image) && (
        <div className="post-preview">
          {text && <p className="post-text">{text}</p>}
          {image && <img src={fileUrl(image)} alt={step.args.alt_text || "Image for the post"} />}
        </div>
      )}

      {!!rest.length && (
        <dl className="approve-args">
          {rest.map(([key, value]) => (
            <div key={key}><dt>{key.replace(/_/g, " ")}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>
          ))}
        </dl>
      )}
    </section>
  );
}

export function ApprovalModal({
  question, pending, live, busy, onDecide,
}: {
  question: string;
  pending: PendingStep[];
  live: boolean;
  busy: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const approve = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    approve.current?.focus();
  }, []);

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="approve-title">
      <div className="modal">
        <header className="modal-head">
          <span className="pulse pulse-waiting" aria-hidden="true" />
          <h2 id="approve-title">FATMEE needs your OK</h2>
        </header>
        <p className="muted small modal-question">For: {question}</p>

        <div className="modal-body">
          {pending.map((step) => <StepPreview key={step.step_id} step={step} live={live} />)}
        </div>

        <footer className="modal-actions">
          <button className="btn" disabled={busy} onClick={() => onDecide("reject")}>Reject</button>
          <button ref={approve} className={`btn ${live && pending.some((p) => PUBLIC_TOOLS.includes(p.tool)) ? "btn-danger" : "btn-accent"}`}
            disabled={busy} onClick={() => onDecide("approve")}>
            {busy ? "Sending…" : live && pending.some((p) => PUBLIC_TOOLS.includes(p.tool)) ? "Approve and publish" : "Approve"}
          </button>
        </footer>
      </div>
    </div>
  );
}
