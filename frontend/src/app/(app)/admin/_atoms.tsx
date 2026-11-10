"use client";

const hue = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const initials = (s: string) => s.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";

export function Tile({ name, size = 40 }: { name: string; size?: number }) {
  const h = hue(name);
  return (
    <span className="rt-tile" aria-hidden style={{ width: size, height: size, fontSize: size * 0.36, background: `color-mix(in srgb, hsl(${h} 65% 50%) 16%, var(--surface))`, color: `hsl(${h} 55% 42%)` }}>
      {initials(name)}
    </span>
  );
}

export function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={`rtd-sw${on ? " on" : ""}`} onClick={() => onChange(!on)}><i /></button>;
}
