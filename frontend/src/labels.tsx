// Friendly names and icons for FATMEE's tools. A new tool works without
// an entry here (its name is shown); add one line to give it a nicer label.

import type { JSX } from "react";

export type IconName =
  | "search" | "doc" | "image" | "pen" | "send" | "brain" | "chip" | "book" | "link" | "dot"
  | "chat" | "tasks" | "tools" | "folder" | "sparkle" | "copy" | "user" | "globe" | "monitor"
  | "battery" | "wifi" | "check" | "clock" | "x" | "arrow" | "plus" | "bolt" | "open";

const TOOLS: Record<string, { running: string; done: string; icon: IconName }> = {
  research_topic: { running: "Searching the web", done: "Web research", icon: "globe" },
  tavily_extract: { running: "Reading web pages", done: "Read web pages", icon: "globe" },
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
  open_file: { running: "Opening the file", done: "File opened", icon: "open" },
  list_files: { running: "Looking at your files", done: "Files listed", icon: "folder" },
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

export function toolIcon(tool: string): IconName {
  return TOOLS[tool]?.icon || (tool.startsWith("get_") || tool.startsWith("check_") ? "chip" : "dot");
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
  chat: <><path d="M5 5h14a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H10l-4 3v-3H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" /><path d="M8.5 11h.01M12 11h.01M15.5 11h.01" /></>,
  tasks: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4V3h6v1M9 10h6M9 14h6M9 18h3" /></>,
  tools: <><path d="M14.5 6.5a3.5 3.5 0 0 0 4.6 4.6L20 12l-8 8-2-2 8-8z" /><path d="M4 6l2-2 4 4-2 2z" /></>,
  folder: <><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></>,
  sparkle: <><path d="M12 3c.6 4.2 2.8 6.4 7 7-4.2.6-6.4 2.8-7 7-.6-4.2-2.8-6.4-7-7 4.2-.6 6.4-2.8 7-7z" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>,
  monitor: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  battery: <><rect x="3" y="7" width="16" height="10" rx="2" /><path d="M21 11v2M7 10v4M10 10v4" /></>,
  wifi: <><path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0" /><path d="M12 19.5h.01" /></>,
  check: <path d="m5 12 4.5 4.5L19 7" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  bolt: <path d="M13 3 5 13h6l-1 8 8-10h-6z" />,
  open: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
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
  return <Icon name={toolIcon(tool)} size={size} />;
}

export function seconds(value?: number) {
  if (value === undefined) return "";
  return value < 60 ? `${value.toFixed(1)}s` : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

export function clockTime(iso?: string) {
  if (!iso) return "";
  const date = new Date(iso);
  return isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function dayAndTime(iso?: string) {
  if (!iso) return "";
  const date = new Date(iso);
  if (isNaN(date.getTime())) return "";
  const today = new Date().toDateString() === date.toDateString();
  return today ? clockTime(iso) : `${date.toLocaleDateString([], { day: "numeric", month: "short" })} · ${clockTime(iso)}`;
}

// Starter texts for the quick actions: they fill the box, you finish the sentence.
export const QUICK_ACTIONS: { label: string; short: string; hint: string; icon: IconName; text: string; chip: boolean }[] = [
  { label: "Search the web", short: "Research", chip: true, hint: "Web + news", icon: "globe", text: "Research the latest news about " },
  { label: "Create image", short: "Generate image", chip: true, hint: "LinkedIn sizes", icon: "image", text: "Make a square LinkedIn image about " },
  { label: "Write a post", short: "Write post", chip: true, hint: "LinkedIn", icon: "pen", text: "Write a LinkedIn post about " },
  { label: "Create document", short: "Create doc", chip: true, hint: "Word report", icon: "doc", text: "Make a one-page Word report on " },
  { label: "Read a file", short: "Read a file", chip: false, hint: "PDF, Word, text", icon: "folder", text: "Read and summarize the file " },
  { label: "Check system", short: "Check system", chip: true, hint: "RAM, disk, battery", icon: "monitor", text: "How is my PC doing? Check RAM, disk and battery." },
];
