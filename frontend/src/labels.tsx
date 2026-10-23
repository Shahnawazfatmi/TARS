// Friendly names and icons for FATMEE's tools. A new tool works without
// an entry here (its name is shown); add one line to give it a nicer label.

import type { JSX } from "react";

type IconName = "search" | "doc" | "image" | "pen" | "send" | "brain" | "chip" | "book" | "link" | "dot";

const TOOLS: Record<string, { running: string; done: string; icon: IconName }> = {
  research_topic: { running: "Searching the web", done: "Web research", icon: "search" },
  tavily_extract: { running: "Reading web pages", done: "Read web pages", icon: "search" },
  create_document: { running: "Writing the document", done: "Word document", icon: "doc" },
  create_image: { running: "Making the image", done: "Image", icon: "image" },
  write_post: { running: "Writing the post", done: "Post written", icon: "pen" },
  linkedin_create_post: { running: "Posting on LinkedIn", done: "LinkedIn post", icon: "send" },
  linkedin_status: { running: "Checking LinkedIn", done: "LinkedIn checked", icon: "link" },
  linkedin_get_profile: { running: "Reading your LinkedIn profile", done: "LinkedIn profile", icon: "link" },
  practice_post: { running: "Saving the practice post", done: "Practice post", icon: "send" },
  think: { running: "Thinking it through", done: "Thought it through", icon: "brain" },
  recall_tasks: { running: "Checking the diary", done: "Diary checked", icon: "book" },
  read_file: { running: "Reading the file", done: "File read", icon: "doc" },
  open_file: { running: "Opening the file", done: "File opened", icon: "doc" },
  list_files: { running: "Looking at your files", done: "Files listed", icon: "book" },
};

function humanize(tool: string) {
  const text = tool.replace(/^get_/, "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function toolLabel(tool: string, running = false) {
  const entry = TOOLS[tool];
  if (entry) return running ? entry.running : entry.done;
  return tool.startsWith("get_") || tool.startsWith("check_") ? `PC: ${humanize(tool).toLowerCase()}` : humanize(tool);
}

const PATHS: Record<IconName, JSX.Element> = {
  search: <><circle cx="11" cy="11" r="6" /><path d="m20 20-4.5-4.5" /></>,
  doc: <><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></>,
  image: <><rect x="4" y="5" width="16" height="14" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m4 17 5-5 4 4 3-3 4 4" /></>,
  pen: <><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="m13.5 6.5 4 4" /></>,
  send: <><path d="M4 12 20 4l-6 16-3-7z" /><path d="m11 13 9-9" /></>,
  brain: <><circle cx="12" cy="12" r="7" /><path d="M12 5v14M8.5 9h7M8.5 15h7" /></>,
  chip: <><rect x="7" y="7" width="10" height="10" rx="1.5" /><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" /></>,
  book: <><path d="M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z" /><path d="M5 17a3 3 0 0 1 3-3h10" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  dot: <circle cx="12" cy="12" r="3" />,
};

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

export function ToolIcon({ tool, size }: { tool: string; size?: number }) {
  const name = TOOLS[tool]?.icon || (tool.startsWith("get_") || tool.startsWith("check_") ? "chip" : "dot");
  return <Icon name={name} size={size} />;
}

export function seconds(value?: number) {
  if (value === undefined) return "";
  return value < 60 ? `${value.toFixed(1)}s` : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}
