import type { NextConfig } from "next";

// CSP: Mercado Pago SDK/Bricks and MinIO public bucket are the only third parties (phase 2).
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://sdk.mercadopago.com https://http2.mlstatic.com",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https://storage.s3.muhbianco.com.br https://*.mlstatic.com",
  "font-src 'self' data:",
  "connect-src 'self' https://api.mercadopago.com",
  "frame-src https://sdk.mercadopago.com https://www.mercadopago.com.br",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  // Standalone output needs symlinks; on Windows dev boxes set NEXT_STANDALONE=0 to build locally.
  output: process.env.NEXT_STANDALONE === "0" ? undefined : "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  images: {
    remotePatterns: [{ protocol: "https", hostname: "storage.s3.muhbianco.com.br" }],
  },
  async headers() {
    return [
      {
        // /cw-app is embedded by Chatwoot (Dashboard App); everything else denies framing.
        source: "/((?!cw-app).*)",
        headers: [
          { key: "Content-Security-Policy", value: `${csp}; frame-ancestors 'none'` },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      {
        source: "/cw-app/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `${csp}; frame-ancestors https://chatwoot.muhbianco.com.br`,
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
