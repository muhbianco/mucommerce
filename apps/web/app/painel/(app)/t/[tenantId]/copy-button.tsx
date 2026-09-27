"use client";

import { useState } from "react";

import styles from "../../../panel.module.css";

/** Plano B para navegador sem Clipboard API ou sem permissão (http, iframe, política). */
function copyWithSelection(value: string): boolean {
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

/** Copia um valor (registro de DNS, endereço) e sempre diz o que aconteceu. */
export function CopyButton({ value, label = "Copiar" }: { value: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const flash = (next: "copied" | "failed") => {
    setState(next);
    window.setTimeout(() => setState("idle"), 2200);
  };
  return (
    <button
      type="button"
      className={styles.buttonSmall}
      aria-label={`${label}: ${value}`}
      aria-live="polite"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          flash("copied");
        } catch {
          flash(copyWithSelection(value) ? "copied" : "failed");
        }
      }}
    >
      {state === "copied" ? "Copiado ✓" : state === "failed" ? "Selecione e copie" : label}
    </button>
  );
}
