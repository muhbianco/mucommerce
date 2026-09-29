"use client";

import { useEffect, useRef, useState } from "react";

import styles from "../../_store/store.module.css";

/**
 * O campo de CEP que preenche o resto do endereço sozinho.
 *
 * **Escuta o evento nativo, não o sintético do React.** Parece detalhe e não é: o React ignora
 * um `input` quando o rastreador dele acha que o valor não mudou, e é exatamente o que acontece
 * quando o valor chega sem alguém digitar — preenchimento automático do navegador, gerenciador
 * de senhas, colagem por script. O cliente que deixa o endereço salvo no Chrome é justamente
 * quem mais merece a busca funcionando. Ouvir `input` e `change` no próprio nó cobre os três
 * caminhos (digitar, colar, autocompletar) sem exceção.
 *
 * Outras três decisões que mudam como isso se comporta na mão de quem compra:
 *
 * **Busca quando os oito dígitos existem, não a cada tecla.** Um pedido de rede por caractere
 * gasta a banda da pessoa e a paciência do ViaCEP para nada.
 *
 * **Não sobrescreve o que a pessoa escreveu.** Se ela já digitou a rua (ou está corrigindo o que
 * o ViaCEP erra — condomínio, loteamento novo, zona rural), aquele campo fica como está.
 * Automatismo que apaga o que a pessoa acabou de escrever é pior que nenhum.
 *
 * **Nunca trava o formulário.** CEP não encontrado ou serviço fora do ar viram um recado e nada
 * mais. Endereço é o que separa a venda da não-venda: ele não pode depender de um terceiro
 * gratuito estar de pé.
 *
 * Sem JavaScript, isto é um `<input>` comum e o formulário inteiro continua funcionando.
 */

/** Os campos que a consulta preenche. */
const PREENCHE = ["street", "district", "city", "state"] as const;

type Estado = "" | "buscando" | "achou" | "nao_achou" | "indisponivel";

const RECADO: Record<Exclude<Estado, "">, string> = {
  buscando: "Procurando o endereço…",
  achou: "Endereço preenchido. Confira e complete o número.",
  nao_achou: "Não achamos esse CEP. Pode preencher à mão.",
  indisponivel: "A busca de CEP não respondeu. Pode preencher à mão.",
};

/** "01002020" → "01002-020". */
function mascara(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  return digits.length > 5 ? `${digits.slice(0, 5)}-${digits.slice(5)}` : digits;
}

export function CepField({ defaultValue = "" }: { defaultValue?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [estado, setEstado] = useState<Estado>("");
  // A busca só existe depois que esta ilha está de pé. Antes disso, prometer que a gente
  // preenche o resto é promessa que não se cumpre — e é o sinal que o e2e espera.
  const [vivo, setVivo] = useState(false);
  // O último CEP consultado: sem isto, sair e voltar ao campo repete a mesma busca.
  const ultimo = useRef(defaultValue.replace(/\D/g, ""));

  useEffect(() => {
    const campo = input.current;
    if (!campo) return;

    async function buscar(): Promise<void> {
      const node = input.current;
      if (!node) return;
      const digits = node.value.replace(/\D/g, "");
      if (digits.length !== 8 || digits === ultimo.current) return;
      ultimo.current = digits;
      setEstado("buscando");

      let data: {
        found: boolean;
        street?: string;
        district?: string;
        city?: string;
        state?: string;
      };
      try {
        data = await (await fetch(`/api/cep/${digits}`)).json();
      } catch {
        setEstado("indisponivel");
        return;
      }
      if (!data.found) {
        setEstado("nao_achou");
        return;
      }

      const form = node.form;
      if (!form) return;
      for (const nome of PREENCHE) {
        const outro = form.elements.namedItem(nome) as HTMLInputElement | null;
        const novo = data[nome] ?? "";
        // Campo já escrito fica como está: a pessoa pode estar corrigindo o que o ViaCEP erra.
        if (outro && !outro.value.trim() && novo) outro.value = novo;
      }
      setEstado("achou");
      // O que falta é sempre o número, então o cursor vai para lá.
      (form.elements.namedItem("number") as HTMLInputElement | null)?.focus();
    }

    function aoDigitar(): void {
      const node = input.current;
      if (!node) return;
      const anterior = node.value;
      const mascarado = mascara(anterior);
      if (mascarado !== anterior) {
        // O cursor se recoloca **contando dígitos**, não pela posição antiga. A máscara insere
        // um hífen, então a posição antiga passa a ficar antes do dígito recém-digitado — e a
        // tecla seguinte entra no lugar errado. Digitar "01002020" virava "01002-200": um CEP
        // que não é o da pessoa, escrito corretamente por ela.
        const digitosAntes = (node.selectionStart ?? anterior.length) > 0
          ? anterior.slice(0, node.selectionStart ?? anterior.length).replace(/\D/g, "").length
          : 0;
        let cursor = 0;
        for (let vistos = 0; cursor < mascarado.length && vistos < digitosAntes; cursor += 1) {
          if (/\d/.test(mascarado[cursor] ?? "")) vistos += 1;
        }
        node.value = mascarado;
        node.setSelectionRange(cursor, cursor);
      }
      if (mascarado.replace(/\D/g, "").length === 8) void buscar();
      else setEstado("");
    }

    function aoSair(): void {
      void buscar();
    }

    // O campo pode já estar cheio quando isto começa a valer: o navegador preencheu sozinho, ou
    // a pessoa digitou antes de a página terminar de carregar. Nos dois casos não virá evento
    // nenhum, e sem esta consulta de partida o endereço ficaria pela metade sem explicação.
    // Endereço em edição não dispara nada: `ultimo` já nasce com o CEP salvo.
    void buscar();
    setVivo(true);

    campo.addEventListener("input", aoDigitar);
    // `change` e `blur` cobrem o valor que chega sem ninguém digitar: preenchimento automático
    // do navegador e gerenciador de senhas.
    campo.addEventListener("change", aoDigitar);
    campo.addEventListener("blur", aoSair);
    return () => {
      campo.removeEventListener("input", aoDigitar);
      campo.removeEventListener("change", aoDigitar);
      campo.removeEventListener("blur", aoSair);
    };
  }, []);

  return (
    <>
      <label className={styles.field}>
        CEP
        <input
          ref={input}
          name="postal_code"
          required
          inputMode="numeric"
          autoComplete="postal-code"
          maxLength={9}
          placeholder="00000-000"
          defaultValue={defaultValue}
        />
      </label>
      <p className={styles.fieldHint} role={estado ? "status" : undefined} data-cep-ready={vivo ? "" : undefined}>
        {estado ? RECADO[estado] : vivo ? "Digite o CEP e a gente preenche o resto." : " "}
      </p>
    </>
  );
}
