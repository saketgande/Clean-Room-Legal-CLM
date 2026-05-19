import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // Warm editorial-prestige palette. The whole app uses `slate-*` and
        // `brand-*`, so remapping just these two scales recolors everything
        // to warm paper + a restrained deep-legal-green accent.
        slate: {
          50: "#F6F4EF",
          100: "#EDEAE1",
          200: "#E0DCD0",
          300: "#CBC5B5",
          400: "#A39A86",
          500: "#7F7665",
          600: "#635C4D",
          700: "#4B4639",
          800: "#322E25",
          900: "#201C15",
          950: "#14110B",
        },
        brand: {
          50: "#ECEFEA",
          100: "#D8DFD3",
          200: "#C0CBB8",
          300: "#9DAE92",
          400: "#6F8463",
          500: "#4C6647",
          600: "#2F4A38",
          700: "#274032",
          800: "#1F3228",
          900: "#182720",
          950: "#0E1813",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Newsreader", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(40 33 22 / 0.04), 0 1px 3px 0 rgb(40 33 22 / 0.05)",
        pop: "0 1px 1px rgb(40 33 22 / 0.04), 0 6px 14px -4px rgb(40 33 22 / 0.07), 0 22px 40px -16px rgb(40 33 22 / 0.13)",
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
        "fade-in": "fade-in 0.2s ease-out",
        "rise-in": "rise-in 0.6s cubic-bezier(0.16,1,0.3,1) both",
      },
    },
  },
  plugins: [],
};

export default config;
