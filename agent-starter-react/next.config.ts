import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  allowedDevOrigins: ['voicebot.atirath.com'],
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
