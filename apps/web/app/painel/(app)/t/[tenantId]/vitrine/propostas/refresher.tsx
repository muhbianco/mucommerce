"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const POLL_MS = 3000;
//: ~3 min. Depois disso o varredor do servidor já resolveu a proposta travada de um jeito ou de
//: outro, e continuar pedindo seria bater na API sem motivo com a aba esquecida aberta.
const POLL_LIMIT = 60;

/**
 * Recarrega a lista enquanto houver proposta sendo montada.
 *
 * É melhoria, não requisito: sem JavaScript a página mostra "Atualizar" e a lojista clica. Por
 * isso a contagem mora aqui e não num `<meta refresh>` — recarregar a página inteira a cada três
 * segundos perderia o texto do campo de refino que ela estivesse escrevendo.
 */
export function DraftRefresher({ active }: { active: boolean }) {
  const router = useRouter();
  const [ticks, setTicks] = useState(0);

  useEffect(() => {
    if (!active || ticks >= POLL_LIMIT) return;
    const timer = setTimeout(() => {
      setTicks((n) => n + 1);
      router.refresh();
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [active, ticks, router]);

  return null;
}
