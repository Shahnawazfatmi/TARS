import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../types";
import { CardGrid } from "./Cards";

const EXAMPLES = [
  "Research the latest agentic AI news and write a LinkedIn post with an image",
  "Make a one-page Word report on AI in healthcare this month",
  "How much RAM and disk space do I have left?",
];

export function Chat({
  messages, busy, onSend, onZoom,
}: {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
  onZoom: (src: string) => void;
}) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function submit() {
    const question = text.trim();
    if (!question || busy) return;
    onSend(question);
    setText("");
  }

  return (
    <main className="chat">
      <div className="messages">
        {!messages.length && (
          <div className="welcome">
            <span className="welcome-eyebrow">Your personal AI coworker, like TARS</span>
            <h1>Hi, I'm FATMEE. What should we get done?</h1>
            <p>A team of AI agents on your desktop. I plan the work, research, write, design and post for you, and show every step live on the right. Anything public waits for your OK.</p>
            <div className="examples">
              {EXAMPLES.map((e) => <button key={e} className="example" onClick={() => setText(e)}>{e}</button>)}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg msg-${m.role} ${m.error ? "msg-error" : ""}`}>
            {m.role === "assistant" && <span className="msg-who">FATMEE</span>}
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
              </div>
            )}
            {!!m.cards?.length && m.role === "assistant" && (
              <CardGrid cards={m.cards.filter((c) => ["image", "linkedin", "document", "file"].includes(c.type))} onZoom={onZoom} />
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <div className="composer">
        <textarea
          value={text}
          rows={2}
          placeholder={busy ? "FATMEE is working…" : "Give FATMEE a task…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button className="btn btn-accent" onClick={submit} disabled={busy || !text.trim()}>
          {busy ? "Working…" : "Send"}
        </button>
      </div>
    </main>
  );
}
