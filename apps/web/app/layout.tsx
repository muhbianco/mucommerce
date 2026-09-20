import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "MuhBianco Loja",
  description: "Loja online SaaS MuhBianco",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
