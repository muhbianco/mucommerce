import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { money } from "@/lib/store/pricing";

import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";
import { Notice, PageHead, Section, Split } from "../_store/ui";
import { placeOrder } from "./actions";

export const metadata: Metadata = { title: "Finalizar compra", robots: { index: false, follow: false } };

interface Cart {
  id: string | null;
  version: number;
  items: { id: string; name: string; quantity: string; modifiers: { name: string }[]; subtotal_cents: number | null }[];
  quote: {
    subtotal_cents: number;
    discount_cents: number;
    delivery_fee_cents: number;
    total_cents: number;
    currency: string;
    fulfillment: { type: string; snapshot: Record<string, unknown> } | null;
    can_checkout: boolean;
  };
}

interface Session {
  customer: { name: string | null };
}

interface Policies {
  terms: { version: number } | null;
  privacy: { version: number } | null;
}

const ERRORS: Record<string, string> = {
  consent_required: "Aceite os termos e a política de privacidade para finalizar.",
  too_many_open_orders: "Você tem pedidos aguardando pagamento. Pague ou cancele antes de fazer outro.",
  validation_error: "Confira seu nome e telefone.",
  rate_limited: "Muitas tentativas seguidas. Aguarde um minuto.",
};

function fulfillmentLabel(quote: Cart["quote"]): string {
  const f = quote.fulfillment;
  if (!f || f.type === "none") return "Sem entrega (ingressos e serviços)";
  // Transportadora não tem "nome do local": o que identifica é a empresa e o serviço.
  if (f.type === "shipping") {
    const carrier = String(f.snapshot.carrier ?? "");
    const service = String(f.snapshot.service_name ?? "");
    return `Envio por ${[carrier, service].filter(Boolean).join(" ") || "transportadora"}`;
  }
  const name = String(f.snapshot.name ?? "");
  return f.type === "pickup" ? `Retirada em ${name}` : `Entrega (${name})`;
}

export default async function CheckoutPage({ searchParams }: { searchParams: Promise<{ erro?: string }> }) {
  const context = await getStorefrontContext();
  if (!context || !context.features.checkout) notFound();
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect("/entrar?next=%2Fcheckout");
  let cart: Cart;
  let session: Session;
  try {
    [cart, session] = await Promise.all([customerApi<Cart>("/cart"), customerApi<Session>("/me/session")]);
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect("/entrar?next=%2Fcheckout");
    if (error instanceof CustomerApiError && error.status === 403) redirect("/acesso-pendente?next=%2Fcheckout");
    throw error;
  }
  if (!cart.id || !cart.quote.can_checkout) redirect("/carrinho");
  const policies = await storefrontApi<Policies>(context, "/policies", {}, { anonymous: true, fresh: true });
  const docs = policies.kind === "ok" ? policies.data : { terms: null, privacy: null };
  const { erro } = await searchParams;
  const { quote } = cart;

  return (
    <StoreShell context={context}>
      <PageHead title="Finalizar compra" lead="Confira o pedido e confirme. O pagamento vem na tela seguinte." />
      {erro ? <Notice kind="error">{ERRORS[erro] ?? "Não foi possível finalizar. Tente de novo."}</Notice> : null}
      <Split
        aside={
          <div className={styles.summary} aria-label="Resumo do pedido">
            <h2>Seu pedido</h2>
            <ul className={styles.reviewItems}>
              {cart.items.map((item) => (
                <li key={item.id}>
                  <span>
                    {item.quantity} × {item.name}
                    {item.modifiers.length ? (
                      <span className="muted"> ({item.modifiers.map((m) => m.name).join(", ")})</span>
                    ) : null}
                  </span>
                  <span className={styles.cartSubtotal}>
                    {item.subtotal_cents !== null ? money(item.subtotal_cents, quote.currency) : "—"}
                  </span>
                </li>
              ))}
            </ul>
            <dl className={styles.totals}>
              <div>
                <dt>{fulfillmentLabel(quote)}</dt>
                <dd>{quote.delivery_fee_cents ? money(quote.delivery_fee_cents, quote.currency) : "—"}</dd>
              </div>
              {quote.discount_cents ? (
                <div>
                  <dt>Desconto</dt>
                  <dd className={styles.discount}>−{money(quote.discount_cents, quote.currency)}</dd>
                </div>
              ) : null}
              <div className={styles.totalRow}>
                <dt>Total</dt>
                <dd>{money(quote.total_cents, quote.currency)}</dd>
              </div>
            </dl>
            <Link href="/carrinho" className={styles.keepShopping}>
              Alterar carrinho
            </Link>
          </div>
        }
      >
        <Section title="Seus dados" variant="card">
          <form action={placeOrder} className={styles.checkoutForm}>
            <input type="hidden" name="cart_id" value={cart.id} />
            <input type="hidden" name="cart_version" value={cart.version} />
            <input type="hidden" name="expected_total_cents" value={quote.total_cents} />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            {docs.terms ? <input type="hidden" name="terms_version" value={docs.terms.version} /> : null}
            {docs.privacy ? <input type="hidden" name="privacy_version" value={docs.privacy.version} /> : null}
            <label className={styles.field}>
              Nome
              <input name="name" required maxLength={120} defaultValue={session.customer.name ?? ""} autoComplete="name" />
            </label>
            <label className={styles.field}>
              Telefone (opcional)
              <input name="phone" inputMode="tel" maxLength={20} autoComplete="tel" />
              <span className={styles.fieldHint}>Ajuda a loja a falar com você se algo mudar.</span>
            </label>
            <label className={styles.field}>
              Observações para a loja
              <textarea name="notes" rows={3} maxLength={500} />
            </label>
            {docs.terms || docs.privacy ? (
              <label className={styles.check}>
                <input type="checkbox" name="accept" required /> Li e aceito{" "}
                {docs.terms ? <Link href="/politicas/termos">os termos de uso</Link> : null}
                {docs.terms && docs.privacy ? " e " : null}
                {docs.privacy ? <Link href="/politicas/privacidade">a política de privacidade</Link> : null}.
              </label>
            ) : null}
            <button type="submit" className={`button ${styles.checkoutCta}`}>
              Fazer pedido
            </button>
          </form>
        </Section>
      </Split>
    </StoreShell>
  );
}
