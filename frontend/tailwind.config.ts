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
        // Fluent 2 "communication blue" brand ramp (Microsoft Fluent design
        // language). 600 = #0F6CBD (Fluent brand-80, primary): 4.8:1 on white,
        // so it passes WCAG AA for both link text and white-on-brand buttons.
        brand: {
          50: "#EBF3FC",
          100: "#CFE4FA",
          200: "#B4D6FA",
          300: "#96C6FA",
          400: "#479EF5", // dark-mode accent (links, active nav)
          500: "#2886DE",
          600: "#0F6CBD", // primary action button background + light-mode links
          700: "#115EA3", // button hover
          800: "#0F548C",
          900: "#0C3B5E",
          950: "#082338",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Newsreader", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        // Theme-aware depth (values per theme in globals.css): subtle ink on
        // light, deep black + faint violet lift on dark.
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
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
