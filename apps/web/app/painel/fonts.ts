import { Manrope, Syne } from "next/font/google";

// As fontes do site MuhBianco, servidas pelo próprio app (next/font baixa no build): o CSP do
// painel só aceita fonte de 'self', e o painel fica com a mesma cara da conta MuhBianco.
export const bodyFont = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--panel-font-body",
  display: "swap",
});

export const displayFont = Syne({
  subsets: ["latin"],
  weight: ["600", "700", "800"],
  variable: "--panel-font-display",
  display: "swap",
});

export const panelFonts = `${bodyFont.variable} ${displayFont.variable}`;
