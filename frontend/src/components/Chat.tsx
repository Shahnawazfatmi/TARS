import { useEffect, useRef, useState, type RefObject } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { clockTime, Icon, QUICK_ACTIONS } from "../labels";
import type { ChatMessage } from "../types";
import { CardGrid } from "./Cards";
import { TarsCore } from "./TarsCore";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard refused: nothing to undo */
    }
  }

  return (
    <button className="msg-tool" onClick={copy} title={done ? "Copied" : "Copy the answer"} aria-label="Copy the answer">
      <Icon name={done ? "check" : "copy"} size={14} />
    </button>
  );
}

export function Chat({
  messages, busy, draft, onDraft, inputRef, onSend, onZoom,
}: {
  messages: ChatMessage[];
  busy: boolean;
  draft: string;
  onDraft: (text: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string) => void;
  onZoom: (src: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function submit() {
    const question = draft.trim();
    if (!question || busy) return;
    onSend(question);
    onDraft("");
  }

  function quick(text: string) {
    onDraft(text);
    inputRef.current?.focus();
  }

  return (
    <section className="chat" aria-label="Chat">
      <div className="messages">
        {!messages.length && (
          <div className="welcome">
            <span className="welcome-eyebrow">Your personal AI coworker, like TARS</span>
            <h1>Hi, I'm FATMEE. What should we get done?</h1>
            <p>A team of AI agents on your desktop. I plan the work, research, write, design and post for you, and show every step live on the right. Anything public waits for your OK.</p>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg msg-${m.role} ${m.error ? "msg-error" : ""}`}>
            <span className={`avatar avatar-${m.role}`} aria-hidden="true">
              {m.role === "assistant" ? <TarsCore state={m.pending ? "working" : "idle"} /> : <Icon name="user" size={18} />}
            </span>

            <div className="msg-body">
              {m.role === "assistant" && (
                <div className="msg-head"><span className="msg-who">FATMEE</span><span className="msg-badge">AI Assistant</span></div>
              )}

              {m.pending ? (
                <div className="bubble bubble-pending"><span className="dots" /> {m.content}</div>
              ) : (
                <div className="bubble">
                  {m.role === "assistant" ? (
                    <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
                      {m.content}
                    </Markdown>
                  ) : (
                    m.content
                  )}
                  <div className="msg-foot">
                    {m.time && <time>{clockTime(m.time)}</time>}
                    {m.role === "assistant" && !m.error && <CopyButton text={m.content} />}
                  </div>
                </div>
              )}

              {!!m.cards?.length && m.role === "assistant" && (
                <CardGrid cards={m.cards.filter((c) => ["image", "linkedin", "document", "file"].includes(c.type))} onZoom={onZoom} />
              )}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <span className="composer-spark"><Icon name="sparkle" size={20} /></span>
          <textarea
            id="task-input"
            ref={inputRef}
            value={draft}
            rows={1}
            aria-label="Give FATMEE a task"
            placeholder={busy ? "FATMEE is working…" : "Ask FATMEE anything…"}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button className="send" onClick={submit} disabled={busy || !draft.trim()} aria-label="Send">
            <Icon name="send" size={20} />
          </button>
        </div>

        <div className="chips">
          {QUICK_ACTIONS.filter((a) => a.chip).map((a) => (
            <button key={a.label} className="chip" onClick={() => quick(a.text)} disabled={busy}>
              <Icon name={a.icon} size={15} /> {a.label}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
