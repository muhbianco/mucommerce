/**
 * Os ícones dos blocos de benefício.
 *
 * Conjunto fechado e desenhado aqui, em SVG de traço, com `currentColor`: o lojista escolhe da
 * lista e nunca envia arquivo. Ícone vindo de upload seria SVG de terceiro na página — que é
 * script, não figura — e emoji ficaria diferente em cada aparelho, quebrando o alinhamento da
 * grade.
 *
 * Todos `aria-hidden`: quem informa é o título ao lado. Ícone sozinho não diz nada a quem usa
 * leitor de tela.
 *
 * Um nome desconhecido (bloco salvo por uma API mais nova, meio de um deploy) cai no genérico
 * em vez de deixar um buraco na linha.
 */
import type { ReactElement } from "react";

const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const PATHS: Record<string, ReactElement> = {
  truck: (
    <>
      <path d="M3 7h10v9H3z" {...P} />
      <path d="M13 10h4l3 3v3h-7z" {...P} />
      <circle cx="7" cy="18" r="1.8" {...P} />
      <circle cx="17" cy="18" r="1.8" {...P} />
    </>
  ),
  motorcycle: (
    <>
      <circle cx="5.5" cy="16" r="3" {...P} />
      <circle cx="18.5" cy="16" r="3" {...P} />
      <path d="M5.5 16h5l4-7h3M12 9h5" {...P} />
    </>
  ),
  store: (
    <>
      <path d="M4 10v9h16v-9" {...P} />
      <path d="M3 10l1.5-5h15L21 10a3 3 0 01-6 0 3 3 0 01-6 0 3 3 0 01-6 0z" {...P} />
    </>
  ),
  pix: <path d="M12 3l4 4-4 4-4-4 4-4zm0 10l4 4-4 4-4-4 4-4zM3 12l4-4 4 4-4 4-4-4zm10 0l4-4 4 4-4 4-4-4z" {...P} />,
  card: (
    <>
      <rect x="3" y="6" width="18" height="12" rx="2" {...P} />
      <path d="M3 10h18M6 15h4" {...P} />
    </>
  ),
  installments: (
    <>
      <rect x="3" y="5" width="12" height="9" rx="1.5" {...P} />
      <path d="M8 18h11a2 2 0 002-2v-6" {...P} />
    </>
  ),
  whatsapp: <path d="M20 12a8 8 0 01-11.8 7L4 20l1.1-4A8 8 0 1120 12z" {...P} />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" {...P} />
      <path d="M12 7v5l3 2" {...P} />
    </>
  ),
  shield: <path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3z" {...P} />,
  leaf: <path d="M20 4C10 4 4 9 4 16c0 2 1 4 1 4s2-9 15-12c0 0-3 8-11 10" {...P} />,
  heart: <path d="M12 20s-7-4.4-7-9a4 4 0 017-2.6A4 4 0 0119 11c0 4.6-7 9-7 9z" {...P} />,
  star: <path d="M12 4l2.5 5.2 5.5.8-4 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4-3.9 5.5-.8L12 4z" {...P} />,
  gift: (
    <>
      <rect x="3.5" y="9" width="17" height="11" rx="1.5" {...P} />
      <path d="M3.5 13h17M12 9v11M12 9S9.5 4 7.5 5.5 12 9 12 9zm0 0s2.5-5 4.5-3.5S12 9 12 9z" {...P} />
    </>
  ),
  tag: (
    <>
      <path d="M11 3H3v8l10 10 8-8L11 3z" {...P} />
      <circle cx="7" cy="7" r="1.3" {...P} />
    </>
  ),
  box: (
    <>
      <path d="M3 8l9-4 9 4-9 4-9-4z" {...P} />
      <path d="M3 8v8l9 4 9-4V8M12 12v8" {...P} />
    </>
  ),
  phone: <path d="M6 3h4l1.5 4.5-2.5 2a12 12 0 005 5l2-2.5L20.5 14v4a2 2 0 01-2.2 2A16 16 0 014 5.2 2 2 0 016 3z" {...P} />,
  pin: (
    <>
      <path d="M12 21s7-6 7-11a7 7 0 10-14 0c0 5 7 11 7 11z" {...P} />
      <circle cx="12" cy="10" r="2.5" {...P} />
    </>
  ),
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="15" rx="2" {...P} />
      <path d="M3.5 10h17M8 3v4M16 3v4" {...P} />
    </>
  ),
  sparkles: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM18.5 16l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z" {...P} />,
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" {...P} />
      <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16 5.2a3.2 3.2 0 010 5.6M17.5 14.8c2 .7 3.5 2.6 3.5 5.2" {...P} />
    </>
  ),
};

const FALLBACK = <circle cx="12" cy="12" r="8.5" {...P} />;

export function BlockIcon({ name, className }: { name: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" className={className} aria-hidden="true" focusable="false">
      {PATHS[name] ?? FALLBACK}
    </svg>
  );
}
