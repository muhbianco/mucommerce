import { NextResponse } from "next/server";

/**
 * Consulta de CEP, pela nossa própria origem.
 *
 * O navegador **não** pode falar com o ViaCEP direto: a CSP da loja manda
 * `connect-src 'self' ...`, e um `fetch` para fora morre ali. Então a ida é daqui, do servidor.
 *
 * O que isso também resolve, e é o motivo de valer a pena mesmo se a CSP não existisse:
 *
 * - **SSRF não tem porta.** O endereço é montado aqui, com host fixo, e o único pedaço variável
 *   são oito dígitos conferidos antes. Não existe URL que o cliente escolha;
 * - **cache.** Um CEP não muda; a resposta fica guardada e a maioria das consultas nem sai
 *   daqui. O ViaCEP é gratuito e sem contrato — bater nele a cada tecla seria abusar de graça
 *   alheia e ainda deixar a loja refém da disponibilidade dele;
 * - **degradação honesta.** Qualquer falha vira `null`, e o formulário continua editável à mão.
 *   Endereço é o que separa a venda da não-venda: ele nunca pode depender de um terceiro.
 */

const VIACEP = "https://viacep.com.br/ws";
//: O ViaCEP costuma responder em poucas centenas de ms. Passou disso, a pessoa digita sozinha —
//: esperar mais é pior que não ter a busca.
const TIMEOUT_MS = 4000;

interface ViaCepReply {
  erro?: boolean | string;
  logradouro?: string;
  complemento?: string;
  bairro?: string;
  localidade?: string;
  uf?: string;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ cep: string }> },
): Promise<NextResponse> {
  const { cep } = await params;
  const digits = cep.replace(/\D/g, "");
  if (digits.length !== 8) {
    return NextResponse.json({ found: false }, { status: 400 });
  }

  let data: ViaCepReply;
  try {
    const response = await fetch(`${VIACEP}/${digits}/json/`, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      // Um CEP não muda. O `revalidate` longo é o que impede a loja de bater no ViaCEP a cada
      // vez que alguém digita o mesmo CEP do bairro.
      next: { revalidate: 60 * 60 * 24 * 30 },
    });
    if (!response.ok) return NextResponse.json({ found: false });
    data = (await response.json()) as ViaCepReply;
  } catch {
    // Fora do ar, lento ou respondendo bobagem: o formulário continua à mão.
    return NextResponse.json({ found: false });
  }

  // O ViaCEP responde 200 com `{"erro": true}` para CEP que não existe.
  if (data.erro || !data.localidade) return NextResponse.json({ found: false });

  return NextResponse.json(
    {
      found: true,
      street: data.logradouro ?? "",
      complement: data.complemento ?? "",
      district: data.bairro ?? "",
      city: data.localidade ?? "",
      state: (data.uf ?? "").toUpperCase(),
    },
    { headers: { "Cache-Control": "public, max-age=86400" } },
  );
}
