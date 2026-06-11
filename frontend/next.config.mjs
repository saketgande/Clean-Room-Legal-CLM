/** @type {import('next').NextConfig} */
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
};

export default nextConfig;
