import { useState } from "react";
import { fileUrl, openFile } from "../api";
import { Icon, ToolIcon } from "../labels";
import type { Card } from "../types";

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function OpenButton({ path, label = "Open" }: { path?: string; label?: string }) {
  const [problem, setProblem] = useState("");
  if (!path) return null;

  return (
    <>
      <button className="btn btn-small" onClick={() => openFile(path).catch((e) => setProblem(e.message))}>
        {label}
      </button>
      {problem && <span className="card-problem">{problem}</span>}
    </>
  );
}

export function ResultCard({ card, onZoom }: { card: Card; onZoom: (src: string) => void }) {
  switch (card.type) {
    case "image": {
      const src = card.path ? fileUrl(card.path) : "";
      return (
        <article className="card card-image">
          {src && (
            <button className="image-button" onClick={() => onZoom(src)} title="Show full size">
              <img src={src} alt={card.title || "Image made by FATMEE"} loading="lazy" />
            </button>
          )}
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Image · {card.size}</div>
            {card.title && <p className="card-text clamp-2">{card.title}</p>}
            <div className="card-actions">
              <span className="card-file">{card.file_name}</span>
              <OpenButton path={card.path} />
            </div>
          </div>
        </article>
      );
    }

    case "document":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Word document</div>
            <h4 className="card-title">{card.title || card.file_name}</h4>
            <p className="card-meta">{card.words} words · {card.sections} sections</p>
            <div className="card-actions">
              <span className="card-file">{card.file_name}</span>
              <OpenButton path={card.path} label="Open in Word" />
            </div>
          </div>
        </article>
      );

    case "post":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Post · {card.characters} characters</div>
            <p className="card-post">{card.text}</p>
          </div>
        </article>
      );

    case "linkedin":
      return (
        <article className={`card ${card.posted ? "card-success" : ""}`}>
          <div className="card-body">
            <div className="card-kind">
              <ToolIcon tool={card.tool} size={14} />
              {card.posted ? "Posted on LinkedIn" : card.dry_run ? "Dry run: nothing was published" : "Not posted"}
            </div>
            {card.as && <p className="card-meta">As {card.as}</p>}
            <p className="card-post clamp-4">{card.text}</p>
            {card.url && (
              <div className="card-actions">
                <a className="btn btn-small btn-accent" href={card.url} target="_blank" rel="noreferrer">
                  View on LinkedIn <Icon name="link" size={13} />
                </a>
              </div>
            )}
          </div>
        </article>
      );

    case "practice":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Practice post saved</div>
            <p className="card-meta">{card.note}</p>
            <div className="card-actions">
              <span className="card-file">{card.file_name}</span>
              <OpenButton path={card.path} />
            </div>
          </div>
        </article>
      );

    case "research":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Research</div>
            <h4 className="card-title">{card.title}</h4>
            {card.summary && <p className="card-text clamp-4">{card.summary}</p>}
            {!!card.sources?.length && (
              <ul className="sources">
                {card.sources.map((s, i) => (
                  <li key={s.url + i}>
                    <a href={s.url} target="_blank" rel="noreferrer" title={s.title}>
                      <span className="source-n">{i + 1}</span> {host(s.url)}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </article>
      );

    case "file":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind">
              <ToolIcon tool={card.tool} size={14} /> {card.coverage}{card.complete ? "" : " (part of a long file)"}
            </div>
            <h4 className="card-title">{card.title}</h4>
            {card.summary && <p className="card-text clamp-4">{card.summary}</p>}
            {!!card.points?.length && (
              <ul className="card-points">{card.points.map((p, i) => <li key={i}>{p}</li>)}</ul>
            )}
            <div className="card-actions">
              <span className="card-file">{card.path}</span>
              <OpenButton path={card.path} />
            </div>
          </div>
        </article>
      );

    case "text":
      return (
        <article className="card">
          <div className="card-body">
            <div className="card-kind"><ToolIcon tool={card.tool} size={14} /> Note</div>
            <p className="card-text clamp-4">{card.text}</p>
          </div>
        </article>
      );

    default:
      return null;
  }
}

export function CardGrid({ cards, onZoom }: { cards: Card[]; onZoom: (src: string) => void }) {
  if (!cards.length) return null;

  return (
    <div className="card-grid">
      {cards.map((card, i) => <ResultCard key={`${card.tool}-${i}`} card={card} onZoom={onZoom} />)}
    </div>
  );
}
