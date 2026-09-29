import "server-only";

/**
 * O questionário, descrito uma vez.
 *
 * Quatro passos curtos, e não uma pergunta por tela: quem quer se livrar da tarefa precisa
 * conseguir. Cada passo salva sozinho, então abandonar no meio deixa gravado o que já foi
 * respondido — a alternativa (um formulário só, salvando no fim) perde tudo quando a lojista
 * fecha a aba.
 *
 * **Só `segment` e `sells` importam.** Todo o resto é opcional, e o gerador degrada: com pouco,
 * ele escreve pouco. É por isso que existe o botão de pular.
 *
 * Os textos de exemplo mudam com o segmento escolhido. "Ex.: bolo de pote, brownie, torta" diz
 * mais sobre o que se espera ali do que qualquer instrução — e é o que evita o campo voltar com
 * "vendo várias coisas".
 */

export const STEPS = ["negocio", "publico", "onde", "jeito"] as const;
export type StepKey = (typeof STEPS)[number];

export function isStep(value: string): value is StepKey {
  return (STEPS as readonly string[]).includes(value);
}

export const STEP_TITLE: Record<StepKey, string> = {
  negocio: "O seu negócio",
  publico: "Quem compra, e por que de você",
  onde: "Onde te achar",
  jeito: "O jeito da página",
};

export const STEP_LEAD: Record<StepKey, string> = {
  negocio: "Duas respostas e já dá para montar algo. O resto é bônus.",
  publico: "Isto é o que vira a parte da página que convence.",
  onde: "Cidade, entrega e contato — o que o cliente procura antes de comprar.",
  jeito: "Palavras que você quer ver, e o que prefere que a gente não escreva.",
};

/** Segmentos, na ordem em que aparecem no `<select>`. Espelha `Segment` em `app/landing/schemas.py`. */
export const SEGMENTS: { value: string; label: string }[] = [
  { value: "padaria_confeitaria", label: "Padaria ou confeitaria" },
  { value: "restaurante_lanchonete", label: "Restaurante ou lanchonete" },
  { value: "moda", label: "Roupas e moda" },
  { value: "beleza_cosmeticos", label: "Beleza e cosméticos" },
  { value: "joias_acessorios", label: "Joias e acessórios" },
  { value: "artesanato", label: "Artesanato" },
  { value: "pet", label: "Pet" },
  { value: "casa_decoracao", label: "Casa e decoração" },
  { value: "papelaria_presentes", label: "Papelaria e presentes" },
  { value: "suplementos", label: "Suplementos" },
  { value: "floricultura", label: "Flores" },
  { value: "bebidas", label: "Bebidas" },
  { value: "mercearia", label: "Mercearia" },
  { value: "brinquedos", label: "Brinquedos" },
  { value: "eletronicos", label: "Eletrônicos" },
  { value: "servicos", label: "Serviços" },
  { value: "eventos", label: "Eventos e ingressos" },
  { value: "esporte", label: "Esporte" },
  { value: "outro", label: "Outro" },
];

/** Como a loja fala. Cada valor vira um pedaço de instrução que nós escrevemos, não ela. */
export const VOICES: { value: string; label: string; hint: string }[] = [
  { value: "proximo", label: "Próximo", hint: "Como quem conversa: “a gente faz”, “chama no zap”." },
  { value: "classico", label: "Clássico", hint: "Educado e direto, sem gíria." },
  { value: "divertido", label: "Divertido", hint: "Leve, com humor, sem exagero." },
  { value: "tecnico", label: "Técnico", hint: "Detalhe e precisão: medida, material, prazo." },
  { value: "elegante", label: "Elegante", hint: "Poucas palavras, escolhidas com cuidado." },
];

export const SERVES: { value: string; label: string }[] = [
  { value: "retirada", label: "O cliente retira" },
  { value: "entrega_local", label: "Eu entrego na região" },
  { value: "envio_brasil", label: "Envio pelo Brasil" },
  { value: "online", label: "É digital ou online" },
];

/** Exemplo de "o que você vende", por segmento. */
const SELLS_EXAMPLE: Record<string, string> = {
  padaria_confeitaria: "bolo de pote, brownie, torta salgada por encomenda",
  restaurante_lanchonete: "marmita do dia, hambúrguer artesanal, porções",
  moda: "vestido midi, alfaiataria feminina, peças de linho",
  beleza_cosmeticos: "sabonete artesanal, óleo capilar, kit de skincare",
  joias_acessorios: "anel de prata 925, brinco de pedra natural, colar personalizado",
  artesanato: "pipa de papel de seda, crochê sob encomenda, macramê",
  pet: "ração natural, coleira de couro, brinquedo de corda",
  casa_decoracao: "vaso de cerâmica, quadro em canvas, cesto de palha",
  papelaria_presentes: "caderno artesanal, caixa de presente, cartão personalizado",
  suplementos: "whey, creatina, pré-treino",
  floricultura: "buquê de rosas, arranjo de mesa, cesta de café da manhã",
  bebidas: "cerveja artesanal, vinho de pequeno produtor, kombucha",
  mercearia: "queijo da serra, doce de leite, café especial",
  brinquedos: "brinquedo de madeira, jogo educativo, pelúcia",
  eletronicos: "fone bluetooth, carregador, capa de celular",
  servicos: "consultoria de imagem, aula de violão, conserto de bicicleta",
  eventos: "ingresso para o show, mesa para o jantar, passaporte do festival",
  esporte: "camisa de time, suplemento, acessório de academia",
};

export function sellsPlaceholder(segment: string): string {
  const exemplo = SELLS_EXAMPLE[segment];
  return exemplo ? `Ex.: ${exemplo}` : "Ex.: diga em uma frase o que você vende";
}

/** Exemplo de "quem compra de você". */
const AUDIENCE_EXAMPLE: Record<string, string> = {
  padaria_confeitaria: "Ex.: quem quer encomendar doce para festa e aniversário na região",
  restaurante_lanchonete: "Ex.: gente do bairro no almoço e famílias no fim de semana",
  moda: "Ex.: mulheres de 25 a 45 que procuram peça diferente do shopping",
  pet: "Ex.: tutor que se preocupa com o que o bicho come",
  eventos: "Ex.: público do bairro que acompanha os shows da casa",
};

export function audiencePlaceholder(segment: string): string {
  return AUDIENCE_EXAMPLE[segment] ?? "Ex.: quem compra de você, e em que momento";
}
