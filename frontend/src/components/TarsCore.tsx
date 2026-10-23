// FATMEE's core, drawn like TARS: four monolith slabs. They breathe
// when idle, shift one after another while working, glow amber when
// FATMEE needs you, and flash green when done.

export type CoreState = "idle" | "working" | "waiting" | "done" | "failed";

export function TarsCore({ state, size = "small" }: { state: CoreState; size?: "small" | "large" }) {
  return (
    <span className={`core core-${size} core-${state}`} aria-hidden="true">
      <span className="slab" />
      <span className="slab" />
      <span className="slab" />
      <span className="slab" />
      {size === "large" && <span className="core-ring" />}
    </span>
  );
}
