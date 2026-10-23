import { useEffect, useRef } from "react";

// A faint network drifting behind the screen. Calm when idle; while
// FATMEE works, pulses travel along the links. About 30 frames a
// second, paused when the tab is hidden, a still frame if the user
// prefers less motion.

type Mood = "idle" | "working" | "waiting";

const COLORS: Record<Mood, string> = { idle: "96, 150, 255", working: "90, 209, 255", waiting: "251, 191, 36" };
const LINK_DISTANCE = 150;

interface Node { x: number; y: number; vx: number; vy: number }
interface Pulse { a: number; b: number; t: number }

export function Backdrop({ mood }: { mood: Mood }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const moodRef = useRef(mood);
  moodRef.current = mood;

  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext("2d")!;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let nodes: Node[] = [];
    let pulses: Pulse[] = [];
    let frame = 0;
    let last = 0;
    let width = 0;
    let height = 0;

    function resize() {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      el.width = width * ratio;
      el.height = height * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

      const count = Math.round(Math.min(90, (width * height) / 18000));
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
      }));
      pulses = [];
    }

    function draw(time: number) {
      frame = requestAnimationFrame(draw);
      if (document.hidden || time - last < 33) return;
      const step = Math.min(3, (time - last) / 16.7);
      last = time;

      const current = moodRef.current;
      const rgb = COLORS[current];
      const speed = current === "working" ? 2.2 : 1;
      ctx.clearRect(0, 0, width, height);

      for (const n of nodes) {
        n.x += n.vx * speed * step;
        n.y += n.vy * speed * step;
        if (n.x < -20) n.x = width + 20;
        if (n.x > width + 20) n.x = -20;
        if (n.y < -20) n.y = height + 20;
        if (n.y > height + 20) n.y = -20;
      }

      const links: [number, number][] = [];
      ctx.lineWidth = 1;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const d = Math.hypot(dx, dy);
          if (d > LINK_DISTANCE) continue;
          links.push([i, j]);
          ctx.strokeStyle = `rgba(${rgb}, ${0.16 * (1 - d / LINK_DISTANCE)})`;
          ctx.beginPath();
          ctx.moveTo(nodes[i].x, nodes[i].y);
          ctx.lineTo(nodes[j].x, nodes[j].y);
          ctx.stroke();
        }
      }

      for (const n of nodes) {
        ctx.fillStyle = `rgba(${rgb}, 0.5)`;
        ctx.beginPath();
        ctx.arc(n.x, n.y, 1.3, 0, Math.PI * 2);
        ctx.fill();
      }

      // Pulses: "thoughts" travelling along links, many while working.
      const wanted = current === "working" ? 14 : current === "waiting" ? 4 : 2;
      if (pulses.length < wanted && links.length && Math.random() < 0.3) {
        const [a, b] = links[Math.floor(Math.random() * links.length)];
        pulses.push({ a, b, t: 0 });
      }

      pulses = pulses.filter((p) => p.t <= 1 && nodes[p.a] && nodes[p.b]);
      for (const p of pulses) {
        p.t += 0.02 * speed * step;
        const a = nodes[p.a];
        const b = nodes[p.b];
        const x = a.x + (b.x - a.x) * p.t;
        const y = a.y + (b.y - a.y) * p.t;
        const glow = ctx.createRadialGradient(x, y, 0, x, y, 7);
        glow.addColorStop(0, `rgba(${rgb}, 0.9)`);
        glow.addColorStop(1, `rgba(${rgb}, 0)`);
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    resize();
    window.addEventListener("resize", resize);

    if (still) {
      draw(1000);
      cancelAnimationFrame(frame);
    } else {
      frame = requestAnimationFrame(draw);
    }

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvas} className="backdrop" aria-hidden="true" />;
}
