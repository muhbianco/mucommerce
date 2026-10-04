"use client";

import { type FormEvent, useEffect, useRef } from "react";

/** Marca "acabei de pedir o frete" entre o envio e o resultado (sessionStorage, com prazo). */
const ROLAR = "mb-frete-rolar";
const PRAZO_MS = 30_000;

/** "01002020" → "01002-020", enquanto a pessoa digita. */
function mascara(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  return digits.length > 5 ? `${digits.slice(0, 5)}-${digits.slice(5)}` : digits;
}

/**
 * Campo de CEP do frete, com máscara. Sem JavaScript é um `<input>` comum: o servidor aceita
 * com ou sem traço. Não controlado de propósito: preenchimento automático e colagem chegam
 * como evento nativo, e a máscara só ajeita o que já está no campo.
 */
export function CepInput({ defaultValue = "" }: { defaultValue?: string }) {
  const campo = useRef<HTMLInputElement>(null);

  // O envio marca a intenção; quem rola é o resultado, quando chega (ver `EstimateScroll`).
  useEffect(() => {
    const form = campo.current?.form;
    if (!form) return;
    const marcar = () => {
      try {
        sessionStorage.setItem(ROLAR, String(Date.now()));
      } catch {
        // Sem sessionStorage (aba privada, bloqueio): só não rola até o resultado.
      }
    };
    form.addEventListener("submit", marcar);
    return () => form.removeEventListener("submit", marcar);
  }, []);

  function onInput(event: FormEvent<HTMLInputElement>): void {
    const alvo = event.currentTarget;
    const formatado = mascara(alvo.value);
    if (formatado !== alvo.value) alvo.value = formatado;
  }
  return (
    <input
      ref={campo}
      name="cep"
      inputMode="numeric"
      autoComplete="postal-code"
      placeholder="00000-000"
      maxLength={9}
      pattern="\d{5}-?\d{3}"
      required
      defaultValue={defaultValue}
      onInput={onInput}
    />
  );
}

/**
 * Leva a pessoa de volta ao frete depois do "Calcular". Sem JavaScript, o `#frete` do redirect
 * faz isso; com ele, a navegação do Next volta ao topo da página e o resultado ficaria fora da
 * tela. Só rola se o pedido saiu desta aba há pouco: abrir um produto com o CEP já guardado não
 * pode arrastar a página até o frete.
 */
export function EstimateScroll({ targetId }: { targetId: string }) {
  useEffect(() => {
    let marcado: string | null = null;
    try {
      marcado = sessionStorage.getItem(ROLAR);
      sessionStorage.removeItem(ROLAR);
    } catch {
      return;
    }
    if (!marcado || Date.now() - Number(marcado) > PRAZO_MS) return;
    document.getElementById(targetId)?.scrollIntoView({ block: "start" });
  }, [targetId]);
  return null;
}
