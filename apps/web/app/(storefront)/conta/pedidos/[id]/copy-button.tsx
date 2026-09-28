"use client";

import { useState } from "react";

/** Copies the Pix code; the code stays selectable in its field when the clipboard is refused. */
export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
          } catch {
            setCopied(false);
          }
        }}
      >
        Copiar código Pix
      </button>
      <span role="status" className="muted">
        {copied ? " Código copiado." : ""}
      </span>
    </>
  );
}
