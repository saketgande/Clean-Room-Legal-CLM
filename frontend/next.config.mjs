/** @type {import('next').NextConfig} */

// Security response headers applied to every route.
//
// These are all safe to send unconditionally (they don't restrict script/style/
// connect sources, so they can't break the app):
//   - Strict-Transport-Security : force HTTPS for a year incl. subdomains
//   - X-Frame-Options / frame-ancestors 'none' : block clickjacking (both the
//     legacy header and the modern CSP directive; frame-ancestors alone here
//     does NOT restrict anything else, so it won't break API calls or inline JS)
//   - X-Content-Type-Options : stop MIME sniffing
//   - Referrer-Policy : don't leak full URLs (incl. share tokens) cross-origin
//   - Permissions-Policy : drop powerful features this app never uses
//
// A full Content-Security-Policy (script-src/style-src/connect-src) is the
// strongest control but must be validated against a real build + the deployed
// API origin first, because a wrong connect-src silently breaks every API call
// and Next's hydration needs inline bootstrap scripts (nonces or 'unsafe-inline').
// A ready-to-tune starting point is in CSP_TEMPLATE below — enable it once tested.
const securityHeaders = [
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), interest-cohort=()" },
];

// eslint-disable-next-line no-unused-vars
const CSP_TEMPLATE = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'", // replace 'unsafe-inline' with per-request nonces when feasible
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${process.env.NEXT_PUBLIC_API_BASE_URL || ""}`.trim(),
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig = {
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
  // The production VM runs `next start` behind Nginx.
  // This app fetches everything client-side from the API and does not rely on
  // Next's image optimizer; disabling it removes that attack surface entirely
  // and keeps the image pipeline build-step-free.
  images: { unoptimized: true },
  // Don't leak the framework/version in response headers.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
