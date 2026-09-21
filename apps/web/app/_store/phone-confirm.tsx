import { cookies } from "next/headers";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { WHATSAPP_LINK_COOKIE } from "@/lib/customer-cookies";
import type { StorefrontContext } from "@/lib/tenant";

import { startWhatsApp } from "./actions";

interface Phone {
  phone_masked: string | null;
  verified: boolean;
}

const ERRORS: Record<string, string> = {
  validation_error: "Informe um celular com DDD.",
  rate_limited: "Muitas tentativas. Aguarde alguns minutos.",
  phone_unavailable: "A confirmação por WhatsApp está indisponível agora. Tente em instantes.",
  feature_disabled: "Esta loja não confirma WhatsApp.",
};

/**
 * WhatsApp status of the signed-in customer, and the form that opens WhatsApp with
 * "CONFIRMAR <código>" to the official number (see ./actions.ts).
 */
export async function PhoneConfirm({
  context,
  back,
  error,
}: {
  context: StorefrontContext;
  back: string;
  error?: string;
}) {
  if (!context.features.customer_phone_otp) return null;
  let phone: Phone;
  try {
    phone = await customerApi<Phone>("/me/phone");
  } catch (failure) {
    if (failure instanceof CustomerApiError) return null;
    throw failure;
  }
  const link = (await cookies()).get(WHATSAPP_LINK_COOKIE)?.value;
  const pending = link?.startsWith("https://wa.me/") ? link : null;
  return (
    <section aria-labelledby="whatsapp-title">
      <h2 id="whatsapp-title">WhatsApp</h2>
      {phone.verified ? (
        <p>Confirmado: {phone.phone_masked}</p>
      ) : (
        <>
          <p className="muted">
            Confirme seu WhatsApp para a loja falar com você sobre pedidos. Vamos abrir o WhatsApp com uma
            mensagem pronta; é só enviar e voltar aqui.
          </p>
          {error ? <p role="alert">{ERRORS[error] ?? "Não foi possível começar a confirmação."}</p> : null}
          {pending ? (
            <p>
              <a className="button" href={pending} target="_blank" rel="noopener noreferrer">
                Abrir WhatsApp e enviar a mensagem
              </a>{" "}
              <a href={back}>Já enviei</a>
            </p>
          ) : null}
          <form action={startWhatsApp}>
            <input type="hidden" name="back" value={back} />
            <label>
              Celular com DDD
              <input name="phone" type="tel" inputMode="tel" autoComplete="tel" required maxLength={32} />
            </label>
            <button type="submit" className={pending ? undefined : "button"}>
              {pending ? "Gerar de novo" : "Confirmar WhatsApp"}
            </button>
          </form>
        </>
      )}
    </section>
  );
}
