import styles from "../../../panel.module.css";

const OK: Record<string, string> = {
  criado: "Produto criado.",
  salvo: "Alterações salvas.",
  publish: "Produto publicado.",
  unpublish: "Produto tirado da vitrine.",
  pause: "Venda pausada: o produto segue na vitrine como indisponível.",
  resume: "Venda retomada.",
  variante_pause: "Variante pausada.",
  variante_resume: "Variante retomada.",
  opcoes: "Opções salvas; variantes atualizadas.",
  arquivado: "Produto arquivado.",
  variante: "Variante atualizada.",
  imagem: "Imagem atualizada.",
  imagem_removida: "Imagem removida.",
  criada: "Categoria criada.",
  salva: "Categoria salva.",
  arquivada: "Categoria arquivada.",
  estoque: "Estoque atualizado.",
  minimo: "Nível mínimo salvo.",
  marca: "Marca salva.",
  seo: "SEO salvo.",
  landing: "Página inicial salva.",
  legal: "Documento publicado.",
  acesso_approved: "Acesso liberado.",
  acesso_blocked: "Cliente bloqueado nesta loja.",
  acesso_revoked: "Acesso revogado.",
};

const ERRORS: Record<string, string> = {
  validation_error: "Algum campo está inválido.",
  conflict: "Conflito com o estado atual (ex.: SKU/slug em uso ou produto sem imagem).",
  insufficient_stock: "Estoque insuficiente para essa saída.",
  permission_denied: "Seu papel não permite essa ação.",
  feature_disabled: "Esse módulo está desligado para a loja.",
  not_found: "Item não encontrado.",
  preco_obrigatorio: "Informe o preço.",
  preco_invalido: "Preço inválido (use 12,50).",
  quantidade_invalida: "Quantidade inválida (até 3 casas decimais).",
  data_invalida: "Data inválida.",
  escolha_produtos: "Escolha ao menos um produto para o bloco de destaques.",
  idempotency_in_progress: "Essa operação ainda está em andamento; aguarde.",
  invalid_transition: "Essa mudança não é possível a partir da situação atual.",
};

export function Flash({ ok, erro }: { ok?: string; erro?: string }) {
  if (erro) {
    return <p className={styles.error}>{ERRORS[erro] ?? `Não foi possível salvar (${erro}).`}</p>;
  }
  if (ok && OK[ok]) return <p className={styles.ok}>{OK[ok]}</p>;
  return null;
}
