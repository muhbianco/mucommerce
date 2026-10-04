"use client";

/** Abre a impressão do navegador; o CSS de impressão da página deixa só a lista de embalagem. */
export function PrintButton({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <button type="button" className={className} onClick={() => window.print()}>
      {children}
    </button>
  );
}
