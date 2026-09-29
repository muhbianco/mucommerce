"use client";

import { useEffect, useRef } from "react";

/**
 * Dispara o pagamento sozinho quando só há um jeito de pagar.
 *
 * **Uma vez por pedido, nunca por visita.** O pai só monta isto quando o pedido ainda não tem
 * pagamento nenhum; assim que este envio cria um, a condição deixa de valer e recarregar a
 * página não cria outro. Sem esse limite, cada abertura da tela abriria uma cobrança nova no
 * provedor — barato de escrever, caro de conciliar depois.
 *
 * Sem JavaScript o botão continua ali e a pessoa clica. É melhoria, não requisito.
 */
export function AutoSubmit({ formId }: { formId: string }) {
  const disparado = useRef(false);

  useEffect(() => {
    if (disparado.current) return;
    disparado.current = true;
    const form = document.getElementById(formId);
    if (form instanceof HTMLFormElement) form.requestSubmit();
  }, [formId]);

  return null;
}
