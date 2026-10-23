// ESLint flat config (ESLint 9) for Next.js 15 — the shape `create-next-app`
// generates. The repo had eslint-config-next installed but no config, so
// `npm run lint` failed and nothing was ever linted. Note: with a config present,
// `next build` lints too and fails on errors (warnings don't fail the build).
import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
];

export default eslintConfig;
