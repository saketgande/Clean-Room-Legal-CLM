import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // Neutral ladder — driven by CSS variables so it swaps between the
        // light (:root) and dark (.dark) themes defined in globals.css. The
        // whole app uses `slate-*`, so flipping the .dark class on <html>
        // recolors everything with zero component edits. `<alpha-value>` keeps
        // Tailwind opacity modifiers (e.g. bg-slate-50/50) working.
        slate: {
          50: "rgb(var(--color-slate-50) / <alpha-value>)",
          100: "rgb(var(--color-slate-100) / <alpha-value>)",
          200: "rgb(var(--color-slate-200) / <alpha-value>)",
          300: "rgb(var(--color-slate-300) / <alpha-value>)",
          400: "rgb(var(--color-slate-400) / <alpha-value>)",
          500: "rgb(var(--color-slate-500) / <alpha-value>)",
          600: "rgb(var(--color-slate-600) / <alpha-value>)",
          700: "rgb(var(--color-slate-700) / <alpha-value>)",
          800: "rgb(var(--color-slate-800) / <alpha-value>)",
          900: "rgb(var(--color-slate-900) / <alpha-value>)",
          950: "rgb(var(--color-slate-950) / <alpha-value>)",
        },
        // "Mission Control" brand ramp — warm orange accent, now driven by CSS
        // variables (like `slate`) so each theme tunes it: bright #EE8034 on the
        // dark navy ground, a contrast-safe burnt orange on light. Values live
        // in globals.css (:root = light, .dark = navy).
        brand: {
          50: "rgb(var(--color-brand-50) / <alpha-value>)",
          100: "rgb(var(--color-brand-100) / <alpha-value>)",
          200: "rgb(var(--color-brand-200) / <alpha-value>)",
          300: "rgb(var(--color-brand-300) / <alpha-value>)",
          400: "rgb(var(--color-brand-400) / <alpha-value>)", // accent (links, active nav)
          500: "rgb(var(--color-brand-500) / <alpha-value>)",
          600: "rgb(var(--color-brand-600) / <alpha-value>)", // primary button bg + links
          700: "rgb(var(--color-brand-700) / <alpha-value>)", // button hover
          800: "rgb(var(--color-brand-800) / <alpha-value>)",
          900: "rgb(var(--color-brand-900) / <alpha-value>)",
          950: "rgb(var(--color-brand-950) / <alpha-value>)",
        },
        // Semantic status colors — their own tokens (never ride slate/brand).
        // Each has a solid (text/icon/border) and a -subtle (badge/banner bg).
        success: "rgb(var(--color-success) / <alpha-value>)",
        "success-subtle": "rgb(var(--color-success-subtle) / <alpha-value>)",
        warning: "rgb(var(--color-warning) / <alpha-value>)",
        "warning-subtle": "rgb(var(--color-warning-subtle) / <alpha-value>)",
        danger: "rgb(var(--color-danger) / <alpha-value>)",
        "danger-subtle": "rgb(var(--color-danger-subtle) / <alpha-value>)",
        info: "rgb(var(--color-info) / <alpha-value>)",
        "info-subtle": "rgb(var(--color-info-subtle) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        // Theme-aware depth (values per theme in globals.css). Resting cards are
        // border-only (card = none); pop/modal carry real elevation.
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
        modal: "var(--shadow-modal)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(9px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        // Fluent 2 motion — decelerate-mid curve for entrances.
        "fade-in": "fade-in 0.2s cubic-bezier(0,0,0.2,1)",
        "rise-in": "rise-in 0.4s cubic-bezier(0,0,0.2,1) both",
      },
    },
  },
  plugins: [],
};

export default config;
