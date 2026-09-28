/**
 * Kit da vitrine. Toda página da loja monta com estes blocos, para a loja inteira ter a mesma
 * cara e a mesma ordem de leitura. É o irmão do `ui.tsx` do painel, com o vocabulário de quem
 * está comprando em vez de quem está vendendo.
 *
 * Tudo aqui é componente de servidor. As ilhas de cliente moram em arquivos próprios, para uma
 * página que só precisa de HTML não arrastar JavaScript junto.
 *
 * Duas regras que este arquivo não pode quebrar, porque a vitrine inteira depende delas:
 *   1. um `<header>` por documento — por isso `PageHead` é `<div>`, e não `<header>`;
 *   2. cada rótulo aparece uma vez só — nada de repetir botão para "a versão do celular".
 */
import Link from "next/link";
import type { ReactNode } from "react";

import { type StorePillState } from "@/lib/store/states";
import type { StorefrontContext } from "@/lib/tenant";
import { bestPlan, type PublicPayments } from "@/lib/store/installments";
import { discountPercent, freeShippingGap, money } from "@/lib/store/pricing";
import { AVAILABILITY_LABEL, type Availability, formatPrice, type StorePrice } from "@/lib/storefront";
import { AVAILABILITY_STATE, stateOf } from "@/lib/store/states";

import styles from "./store.module.css";

/** Título da página. `<div>` de propósito: o único `<header>` do documento é o da loja. */
export function PageHead({
  title,
  lead,
  actions,
  as: Heading = "h1",
}: {
  title: string;
  lead?: ReactNode;
  actions?: ReactNode;
  as?: "h1" | "h2";
}) {
  return (
    <div className={styles.pageHead}>
      <div className={styles.pageHeadRow}>
        <Heading>{title}</Heading>
        {actions ? <div className={styles.pageActions}>{actions}</div> : null}
      </div>
      {lead ? <p className={styles.lead}>{lead}</p> : null}
    </div>
  );
}

/** Um trecho da página, com ou sem moldura de cartão. */
export function Section({
  title,
  description,
  actions,
  children,
  id,
  variant = "plain",
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
  variant?: "plain" | "card";
}) {
  return (
    <section className={variant === "card" ? styles.card : styles.section} id={id}>
      {title || actions ? (
        <div className={styles.sectionHead}>
          {title ? <h2>{title}</h2> : null}
          {description ? <p>{description}</p> : null}
          {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

/** Conteúdo com uma coluna lateral que acompanha a rolagem no computador. */
export function Split({
  children,
  aside,
  stickyAside = true,
}: {
  children: ReactNode;
  aside: ReactNode;
  stickyAside?: boolean;
}) {
  return (
    <div className={styles.split}>
      <div className={styles.splitMain}>{children}</div>
      <aside className={stickyAside ? `${styles.splitAside} ${styles.sticky}` : styles.splitAside}>{aside}</aside>
    </div>
  );
}

export function Grid({ children }: { children: ReactNode }) {
  return <div className={styles.grid}>{children}</div>;
}

/**
 * O preço, que é a informação mais olhada da loja inteira.
 *
 * O riscado vem antes e pequeno, o valor vem grande, e a porcentagem só aparece quando o
 * desconto é grande o bastante para não virar ruído.
 */
export function Price({
  price,
  size = "md",
  showCompare = true,
  unitLabel,
}: {
  price: StorePrice;
  size?: "md" | "lg";
  showCompare?: boolean;
  unitLabel?: string | null;
}) {
  const compare = showCompare && price.compare_at_cents ? price.compare_at_cents : null;
  return (
    <span className={styles.priceBlock}>
      {compare ? (
        <del className={styles.compareLine}>{money(compare, price.currency)}</del>
      ) : null}
      <span className={size === "lg" ? `${styles.price} ${styles.priceLg}` : styles.price}>
        {formatPrice(price)}
        {unitLabel ? <span className={styles.unit}>/{unitLabel}</span> : null}
      </span>
    </span>
  );
}

/** "25% OFF", ou nada. */
export function DiscountBadge({ price }: { price: StorePrice }) {
  const percent = discountPercent(price);
  if (percent === null) return null;
  return (
    <span className={styles.pill} data-state="ok">
      {percent}% OFF
    </span>
  );
}

/**
 * "em até 12x de R$ 24,90 sem juros".
 *
 * Não escreve nada quando não há o que dizer — loja sem cartão, sem parcelamento, preço baixo
 * demais, ou o bloco de pagamento faltando (API mais velha, meio de um deploy). Silêncio aqui
 * é melhor do que uma parcela que o checkout não vai oferecer.
 */
export function Installments({
  payments,
  amountCents,
  currency,
}: {
  payments: PublicPayments | null | undefined;
  amountCents: number;
  currency: string;
}) {
  const plan = bestPlan(payments, amountCents);
  if (!plan) return null;
  return (
    <p className={styles.installments}>
      em até <strong>{plan.count}x</strong> de <strong>{money(plan.perInstallmentCents, currency)}</strong>{" "}
      {plan.interestFree ? "sem juros" : `com juros (${money(plan.totalCents, currency)} no total)`}
    </p>
  );
}

/** "Últimas unidades": escassez só quando é verdade, e sem entregar o número do estoque. */
export function LowStockPill({ low }: { low: boolean }) {
  if (!low) return null;
  return <Pill state="warn">Últimas unidades</Pill>;
}


/**
 * O que esta loja garante, com o que ela configurou de verdade.
 *
 * Nada de lista genérica de e-commerce: cada linha sai da configuração de entrega da loja, e
 * some quando não se aplica. Prometer "entrega rápida" a quem só tem retirada no balcão é o
 * jeito mais barato de gerar reclamação.
 */
export function StoreGuarantees({ context }: { context: StorefrontContext }) {
  const f = context.fulfillment;
  const modes = f.modes ?? [];
  const linhas: string[] = [];

  if (modes.includes("pickup")) {
    const locais = f.pickup_locations ?? [];
    linhas.push(locais.length === 1 ? `Retire em ${locais[0]!.name}` : "Retirada no local");
  }

  if (modes.includes("delivery")) {
    // O prazo que vale anunciar é o pior entre as zonas: chegar antes é boa surpresa,
    // chegar depois do prometido é reclamação.
    const prazos = (f.delivery_zones ?? []).map((z) => z.eta_minutes).filter((m): m is number => !!m);
    const pior = prazos.length ? Math.max(...prazos) : null;
    linhas.push(
      pior === null
        ? "Entrega pela loja"
        : pior < 60
          ? `Entrega em até ${pior} minutos`
          : `Entrega em até ${Math.round(pior / 60)}h`,
    );
  }

  if (modes.includes("shipping")) {
    const gratis = f.shipping?.free_above_cents ?? null;
    linhas.push(
      gratis ? `Frete grátis acima de ${money(gratis, context.tenant.currency)}` : "Envio para todo o Brasil",
    );
  }

  if (f.min_order_cents) {
    linhas.push(`Pedido mínimo de ${money(f.min_order_cents, context.tenant.currency)}`);
  }

  if (context.features.checkout) linhas.push("Pagamento pela loja, com recibo");
  if (linhas.length === 0) return null;

  return (
    <ul className={styles.trust}>
      {linhas.map((linha) => (
        <li key={linha}>{linha}</li>
      ))}
    </ul>
  );
}

/** Selo de situação. A cor ajuda; quem informa é o texto. */
export function Pill({ state, children }: { state: StorePillState; children: ReactNode }) {
  return (
    <span className={styles.pill} data-state={state}>
      {children}
    </span>
  );
}

export function AvailabilityPill({ availability }: { availability: Availability }) {
  if (availability === "available") return null;
  return <Pill state={stateOf(AVAILABILITY_STATE, availability)}>{AVAILABILITY_LABEL[availability]}</Pill>;
}

/**
 * Recado ao cliente. `role` continua explícito porque é por ele que leitor de tela — e a suíte
 * de ponta a ponta — encontram a mensagem.
 */
export function Notice({
  kind,
  role = kind === "error" ? "alert" : "status",
  children,
}: {
  kind: "ok" | "warn" | "error" | "info";
  role?: "status" | "alert";
  children: ReactNode;
}) {
  return (
    <p className={styles.notice} data-kind={kind} role={role}>
      {children}
    </p>
  );
}

/** Trilha de navegação. A última migalha é a página atual e não é link. */
export function Breadcrumb({ trail }: { trail: { name: string; href?: string }[] }) {
  return (
    <nav className={styles.breadcrumb} aria-label="Você está em">
      {trail.map((step, i) => (
        <span key={`${step.name}-${i}`}>
          {i > 0 ? <span aria-hidden="true"> › </span> : null}
          {step.href ? <Link href={step.href}>{step.name}</Link> : <span aria-current="page">{step.name}</span>}
        </span>
      ))}
    </nav>
  );
}

/** Lista vazia: diz o que aconteceu e qual é o próximo passo, em vez de sumir com a página. */
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      {children ? <p className={styles.emptyText}>{children}</p> : null}
      {action ? <div className={styles.emptyAction}>{action}</div> : null}
    </div>
  );
}

export function Chips({ children, label }: { children: ReactNode; label: string }) {
  return (
    <nav className={styles.chips} aria-label={label}>
      {children}
    </nav>
  );
}

export function Chip({
  href,
  children,
  active = false,
}: {
  href: string;
  children: ReactNode;
  active?: boolean;
}) {
  return (
    <Link href={href} className={styles.chip} aria-current={active ? "true" : undefined}>
      {children}
      {active ? <span aria-hidden="true">×</span> : null}
    </Link>
  );
}

/** Miniatura de linha de tabela: decorativa de propósito — o nome do item está do lado. */
export function Thumb({ url, size = 56 }: { url: string | null; size?: number }) {
  if (!url) return <span className={styles.thumb} style={{ width: size, height: size }} aria-hidden="true" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="" width={size} height={size} className={styles.thumb} loading="lazy" decoding="async" />
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className={styles.tableWrap}>{children}</div>;
}

/**
 * "Faltam R$ 20 para o frete sair de graça."
 *
 * Só vale para envio por transportadora: retirada e entrega por zona cobram à parte, e a frase
 * precisa dizer isso, senão vira promessa que o checkout não cumpre.
 */
export function FreeShippingBar({
  subtotalCents,
  freeAboveCents,
  currency,
}: {
  subtotalCents: number;
  freeAboveCents: number | null;
  currency: string;
}) {
  const gap = freeShippingGap(subtotalCents, freeAboveCents);
  if (gap === null) return null;
  const reached = gap === 0;
  const progress = freeAboveCents ? Math.min(100, Math.round((subtotalCents / freeAboveCents) * 100)) : 0;
  return (
    <div className={styles.freeShip} data-reached={reached ? "true" : undefined}>
      <p className={styles.freeShipText}>
        {reached ? (
          <strong>Frete grátis no envio por transportadora.</strong>
        ) : (
          <>
            Faltam <strong>{money(gap, currency)}</strong> para o frete sair de graça no envio por transportadora.
          </>
        )}
      </p>
      <span className={styles.freeShipTrack} aria-hidden="true">
        <span className={styles.freeShipFill} style={{ width: `${progress}%` }} />
      </span>
    </div>
  );
}

/** Esqueleto enquanto a página espera a API. */
export function Skeleton({ kind, count = 1 }: { kind: "line" | "photo" | "card"; count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className={styles.skeleton} data-kind={kind} aria-hidden="true" />
      ))}
    </>
  );
}
