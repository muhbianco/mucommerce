import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { formatPrice } from "@/lib/storefront";

import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";
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

function money(cents: number, currency: string): string {
  return formatPrice({ amount_cents: cents, compare_at_cents: null, promo_active: false, promo_ends_at: null, currency });
}

function fulfillmentLabel(quote: Cart["quote"]): string {
  const f = quote.fulfillment;
  if (!f || f.type === "none") return "Sem entrega (ingressos e serviços)";
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
      <h1>Finalizar compra</h1>
      {erro ? <p role="alert">{ERRORS[erro] ?? "Não foi possível finalizar. Tente de novo."}</p> : null}
      <section className={styles.section}>
        <h2>Resumo</h2>
        <ul>
          {cart.items.map((item) => (
            <li key={item.id}>
              {item.quantity} × {item.name}
              {item.modifiers.length ? ` (${item.modifiers.map((m) => m.name).join(", ")})` : ""}
              {item.subtotal_cents !== null ? ` — ${money(item.subtotal_cents, quote.currency)}` : ""}
            </li>
          ))}
        </ul>
        <p>{fulfillmentLabel(quote)}</p>
        {quote.delivery_fee_cents ? <p>Entrega: {money(quote.delivery_fee_cents, quote.currency)}</p> : null}
        {quote.discount_cents ? <p>Desconto: −{money(quote.discount_cents, quote.currency)}</p> : null}
        <p>
          <strong>Total: {money(quote.total_cents, quote.currency)}</strong>
        </p>
        <p>
          <Link href="/carrinho">Alterar carrinho</Link>
        </p>
      </section>

      <form action={placeOrder} className={styles.section}>
        <input type="hidden" name="cart_id" value={cart.id} />
        <input type="hidden" name="cart_version" value={cart.version} />
        <input type="hidden" name="expected_total_cents" value={quote.total_cents} />
        <input type="hidden" name="idempotency_key" value={randomUUID()} />
        {docs.terms ? <input type="hidden" name="terms_version" value={docs.terms.version} /> : null}
        {docs.privacy ? <input type="hidden" name="privacy_version" value={docs.privacy.version} /> : null}
        <h2>Seus dados</h2>
        <p>
          <label>
            Nome <input name="name" required maxLength={120} defaultValue={session.customer.name ?? ""} />
          </label>
        </p>
        <p>
          <label>
            Telefone (opcional) <input name="phone" inputMode="tel" maxLength={20} />
          </label>
        </p>
        <p>
          <label>
            Observações para a loja <textarea name="notes" rows={2} maxLength={500} />
          </label>
        </p>
        {docs.terms || docs.privacy ? (
          <p>
            <label>
              <input type="checkbox" name="accept" required /> Li e aceito{" "}
              {docs.terms ? <Link href="/politicas/termos">os termos de uso</Link> : null}
              {docs.terms && docs.privacy ? " e " : null}
              {docs.privacy ? <Link href="/politicas/privacidade">a política de privacidade</Link> : null}.
            </label>
          </p>
        ) : null}
        <button type="submit" className="button">
          Fazer pedido
        </button>
      </form>
    </StoreShell>
  );
}
