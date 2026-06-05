/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
  // Produce a self-contained Node server (.next/standalone) for a tiny prod
  // Docker image. Caddy terminates TLS + sets security headers in front; this
  // service only needs to be reachable on the internal network.
  output: "standalone",
  // This app fetches everything client-side from the API and does not rely on
  // Next's image optimizer; disabling it removes that attack surface entirely
  // (the remaining Next image-optimization advisory) and keeps the image
  // pipeline build-step-free.
  images: { unoptimized: true },
  // Don't leak the framework/version in response headers.
  poweredByHeader: false,
};

export default nextConfig;
