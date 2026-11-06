# 16 — Motor de identidade visual da vitrine

Escrito em 06/11/2026, a partir da reclamação da SGPipas ("praticamente nada mudou, mesmo
depois de mandar mais material"). Horários em Brasília. Fontes: `landing_drafts`,
`media_assets`, `tenant_settings` e `audit_log` do schema `mucommerce` na hel1, e o log da
`api-agents` (`/internal/commerce/llm/complete`).

---

## A. Diagnóstico

### A.1 A execução da SGPipas, reconstruída

| Proposta | Quando | Resultado | Modelo | Entrada / saída |
|---|---|---|---|---|
| `01a0ee01…` | 29/09 16:31 | falhou: gateway fora do ar | — | — |
| `01a0ee0e…` | 29/09 16:45 | falhou: gateway fora do ar | — | — |
| `01a0ee74…` | 29/09 18:36 | falhou: `INVALID_ARGUMENT` (esquema recusado pelo Gemini) | — | — |
| `01a0ee7d…` | 29/09 18:47 | falhou: `INVALID_ARGUMENT` | — | — |
| `01a0eedd…` | 29/09 20:31 | publicada às 20:33 | gemini-3.5-flash, 2 tentativas | 6.550 / 1.953 |
| `01a1ad3a…` | 05/11 16:41 | publicada às 16:45 | claude-sonnet-5-5, 1 tentativa | 10.932 / 2.255 |

As quatro falhas de setembro eram nossas (gateway sem deploy, esquema com chave inventada).
Foram corrigidas nos commits `1379c7f`/`ccd2f1c` e a cota foi devolvida. Elas não explicam a
reclamação.

**O que aconteceu em 05/11** (`audit_log`):

- 15:36–16:20: a lojista cadastrou 6 produtos com 9 fotos, arquivou uma categoria e criou
  outra;
- 15:57: **editou a página inicial à mão**: hero "SG PIPAS — Tudo para quem vive a paixão por
  pipas!", botão "COMPRE AGORA";
- 16:40: completou o brief: "cores vibrantes, imagens atrativas e botões de destaque",
  "página moderna, profissional e atrativa, com foco em vendas";
- 16:41: pediu uma proposta, que chegou em 16 s. Às 16:45 publicou, e **a proposta
  sobrescreveu a edição manual de 48 minutos antes**, sem mostrar a diferença e sem caminho
  de volta.

**O que o modelo recebeu** (`inventory_snapshot` da `01a1ad3a`):

- um brief completo e bom: ramo, público, diferenciais, tom "divertido", palavras, o que
  evitar e três referências visuais;
- **duas imagens, ambas com `role = null` e `alt = null`**. Chegaram ao modelo como
  "sem papel definido · sem descrição". Eram o emblema da loja (`logo.jpeg`, enviado como
  material da vitrine) e o logotipo horizontal (`sg branco.jpeg`, enviado como marca);
- 14 produtos e 3 categorias (ids e nomes);
- **nenhum pixel**. O pedido é só texto: o modelo não viu o logotipo, nem as fotos dos
  produtos, nem cor nenhuma.

**O que o modelo devolveu**: 7 blocos, nesta ordem: hero `image_right`/`brand` usando o
**emblema como foto de destaque**, depois categorias `cards`, destaques `grid`, benefícios
`cards`/`soft`, horário `table`, contato e convite. A proposta de setembro tem o mesmo
esqueleto, com os mesmos arranjos e a mesma imagem no hero. Só o texto mudou.

**O que foi gravado e o que chegou à tela**: só a chave `landing` mudou. A `branding` está
igual desde 28/09: `primary_color #111111`, `secondary_color #111111`, fonte do sistema, e
raio, densidade e papel no padrão. Logo, a loja inteira (cabeçalho, cards, produto,
carrinho, checkout) continua no tema neutro, e o "tom brand" dos blocos é preto.

**A identidade real da marca** (os dois arquivos, olhados): fundo azul-noite quase preto,
contorno ciano neon, letra pincelada de rua em branco, pipa verde-amarela e acento amarelo.
É uma identidade urbana, enérgica e noturna, o oposto de uma página clara em preto e
fonte do sistema.

### A.2 Por que "nada mudou": causas, da que mais pesa para a que menos pesa

1. **A IA só alcança os blocos da página inicial.** O motor existe para escrever texto e
   escolher arranjo de 12 blocos. Cor, fonte, botão, cabeçalho, cards, página de produto,
   carrinho e checkout estão fora do esquema de saída. "Cores vibrantes e botões de
   destaque" era um pedido sem alavanca, e a tela não avisa isso.
2. **O modelo é cego.** Nenhuma imagem viaja no pedido. Com `role` e `alt` nulos, ele não
   distingue logotipo de foto. A `api-agents` já aceita imagem (partes `image_url`,
   convertidas para o formato nativo do Claude); o commerce nunca mandou.
3. **A paleta do logotipo nunca existiu.** A extração de cor entrou em 29/09 (`b2eb503`), e os
   logotipos da SGPipas subiram em 28/09. Não houve backfill, então `media_assets.palette`
   ficou nula e `/branding/suggestions` devolve lista vazia para essa loja até hoje.
4. **E o extrator teria errado.** Rodado agora nos dois arquivos, devolve `#042229` e `#052927`
   (petróleo quase preto) e nenhuma secundária. O peso por presença deixa o fundo escuro ganhar
   do ciano neon e do amarelo, que são as cores que alguém reconhece como da marca.
5. **Logotipo como foto de destaque.** `usable_media` trata `role = null` como imagem de cena.
   O emblema subiu antes do seletor de papel existir, e virou o hero nas duas propostas.
6. **As fotos de produto, que eram o material novo, nem entram.** O inventário só lê os donos
   `landing` e `tenant_brand`, e a conferência de referências só aceita `landing` em bloco.
   As 9 fotos de 05/11 não podiam aparecer em destaque nem em galeria, nem informar estilo.
7. **O mesmo esqueleto toda vez.** O prompt fixa "4 a 7 blocos começando por hero", os
   arranjos caem no padrão, e não existe um perfil da marca que puxe a composição para um lado
   ou para outro. Duas rodadas com o mesmo material dão a mesma página.
8. **Publicar é destrutivo.** `apply` grava por cima, sem diff contra o que está no ar e sem
   versão anterior restaurável. A poda de rascunhos (`landing_drafts_kept = 5`) apaga até as
   propostas aplicadas.
9. **Custo desperdiçado.** O JSON Schema dos blocos vai **duas vezes** no pedido: no texto do
   `prompt.py` e de novo pela `api-agents` (`with_schema_instruction`, que o anexa para o
   Claude). Isso dá cerca de 5 mil tokens repetidos por chamada.

Não houve falha silenciosa no caminho do modelo em 05/11: uma tentativa, `end_turn`,
validação limpa, sem reparo. O motor fez exatamente o que foi desenhado para fazer, e o
desenho é que é estreito demais para o pedido.

### A.3 Usabilidade

- **Material e marca em três lugares.** O logotipo fica em Configurações › Marca, o material
  em Vitrine › Propostas e o brief num assistente de 4 passos. A sugestão de cor fica longe
  de onde se pede a proposta.
- **Papel opcional e não editável.** Imagem enviada sem papel (ou antes de ele existir) fica
  assim para sempre. A tela mostra "sem descrição" e não oferece como corrigir.
- **Nenhuma explicação.** A proposta não diz o que entendeu da marca, o que mudou em relação
  ao que está no ar, nem o que ela *não* pode mudar.
- **Prévia parcial.** Mostra só a home, no tema atual. Não mostra cabeçalho, cards, produto
  nem checkout, e não tem visão de celular.
- **Publicar apaga edição manual sem aviso**, e não existe "voltar para a versão anterior".
- **Pedir de novo gasta cota para obter a mesma coisa.** Sem material novo e sem perfil, o
  refinamento só reescreve frases.

### A.4 A arquitetura que já existe

| Camada pedida | O que existe | Onde | Avaliação |
|---|---|---|---|
| 1. Brand Intelligence | brief (texto) + paleta Pillow do logotipo | `landing/brief.py`, `landing/color.py` | sem visão, sem perfil persistido, paleta frágil |
| 2. Design System | `BrandingV1` (2 cores, fonte, raio, densidade, papel) → `storeTheme()` deriva a paleta inteira com contraste calculado → variáveis CSS | `settings_schemas.py`, `web/lib/theme.ts` | **bom e reaproveitável**: já atravessa todas as páginas (layout da vitrine), garante contraste |
| 3. Component Registry | 12 blocos com `variant`/`tone`, vocabulário fechado, `extra="forbid"` | `landing/blocks.py` | bom, mas só cobre a home |
| 4. Layout Composer | prompt → blocos | `landing/prompt.py`, `generation.py` | fatiamento de transação, cota e reparo exemplares, mas a saída é só a home |
| 5. Validation | `validate_setting` + `check_setting_references` + `repair_blocks` | `tenancy/`, `landing/repair.py` | sólido: "endureça o validador, não o prompt" |
| 6. Renderer | `landing-blocks.tsx` + `store.module.css` (334 usos de variável) | `web/app/(storefront)/_store` | determinístico e sem HTML do lojista: certo. Cabeçalho, card, produto e checkout têm um desenho só |

A conclusão é que a fundação está certa. O que falta é largura: um perfil da marca, tokens
mais expressivos, variantes por área da loja e um compositor que escreva tudo isso pela mesma
porta de validação. Não há motivo para uma segunda arquitetura.

---

## B. Proposta arquitetural

### B.1 Atual × proposta

```
                ATUAL                                   PROPOSTA (este ramo)
brief (texto) ─┐                         materiais (logo, banners, fotos de produto) + brief
inventário ────┼─► LLM ─► blocos home    │
               │                         ▼
               │                  [1] Perfil da marca  (visão, incremental, persistido)
               │                         │   brand_profiles: perfil + análise por arquivo
               │                         ▼
               │                  [4] Compositor (LLM, texto) ─► proposta = tokens
               │                         │                       + design por área + blocos
               │                         ▼                       + decisões
               │                  [5] Validação: esquema, referências, contraste,
               │                         compatibilidade, reparo determinístico, lint
               │                         ▼
               └─► apply ─► landing  [versão] apply grava branding + design + landing
                                         numa transação, guardando o anterior (restaurar)
                                         ▼
                                  [6] Vitrine: theme.ts (tokens) + data-* por área
```

| Camada | Módulo | Reaproveita | Novo |
|---|---|---|---|
| 1. Perfil da marca | `landing/brand.py` (puro), chamada em `generation.py` | brief, `color.py` | `brand_profiles`, análise por arquivo com cache por checksum, visão |
| 2. Design System | `BrandingV1` + `theme.ts` | tudo | tokens novos (fonte de título display/elegante/geométrica, caixa alta, sombra, botão) |
| 3. Registro | `landing/registry.py` | `blocks.py` | áreas da loja e variantes com descrição, compatibilidade e rótulo legível |
| 4. Compositor | `generation.py` + `landing/design_prompt.py` | fatiamento de transação, cota, reparo | proposta completa, uma rodada de autocorreção guiada pelo lint |
| 5. Validação | `landing/design_rules.py` | `validate_setting`, refs | contraste, compatibilidade, chaves que a IA não toca (logotipo), diff verdadeiro |
| 6. Renderer | `theme.ts`, `store.module.css` | tudo | `data-header`, `data-card`, `data-product`, `data-checkout`… só CSS, sem estrutura nova |

### B.2 Persistência

- **`brand_profiles`** (uma linha por loja): `data` (o perfil), `materials` (análise por
  arquivo: `{media_id: {checksum, kind, relevance, description, colors}}`), `brief_digest`,
  `version`, `source` (`llm` ou `deterministic`), modelo e tokens. É o cache que impede
  reinterpretar tudo a cada proposta: só arquivo novo ou alterado vai para o modelo.
- **`tenant_settings.design`** (chave nova, `DesignV1`): variantes por área. A loja sem a
  linha lê os padrões, que reproduzem o desenho de hoje pixel a pixel.
- **`landing_drafts`** ganha `branding`, `design`, `summary` (decisões e o que muda),
  `profile_snapshot` e `previous_settings` (o que estava no ar quando foi aplicada). A
  proposta continua explicando a si mesma, e a versão anterior fica restaurável.

### B.3 Composição

A IA escolhe valores de listas fechadas: token de cor (hex), fonte de título entre quatro,
caixa, sombra, botão, cabeçalho entre quatro desenhos, card entre cinco, proporção da foto,
colunas, layout da página de produto, ênfase do preço, estilo do checkout e rodapé, além dos
blocos da home. São mais de 10⁶ combinações coerentes sem nenhuma linha de CSS vinda do
modelo. Cada valor é um desenho que o front já sabe fazer.

### B.4 Validação

Em ordem: esquema (Pydantic, `extra="forbid"`) → chaves proibidas (logotipo, logo_url) →
referências de mídia, produto e categoria da própria loja → regras de design (contraste ≥
3:1 entre a cor da marca e o papel para elementos não textuais, combinações que não fecham)
→ reparo determinístico → lint. Se o lint achar um problema grave, há uma rodada de
correção com feedback. O texto continua com contraste garantido por construção, porque
`theme.ts` deriva toda tinta de texto.

**O checkout fica fora do alcance da estrutura**: a IA escolhe só o estilo (`branded` ou
`neutral`), que é CSS. Formulário, passos, cálculo, validação e pagamento são os mesmos
componentes de sempre.

### B.5 Versionamento

`apply` grava as três chaves numa transação pela mesma porta da edição à mão
(`TenantService.set_setting`, com auditoria) e guarda o anterior em `previous_settings`.
"Restaurar" grava o anterior pela mesma porta. A poda nunca apaga as últimas 3 aplicadas.

### B.6 Custo

- Perfil: uma chamada com visão **só quando há material novo** (cache por checksum do
  arquivo). Com 8 imagens de 600 px, são cerca de 4 mil tokens de entrada.
- Proposta: uma chamada só de texto, que recebe o perfil em vez das imagens, e no máximo
  uma rodada de correção. O esquema deixa de ir duplicado.
- Nada roda no acesso à loja: a vitrine lê settings, como hoje.
- Sem material novo, o perfil sai do cache e a proposta custa uma chamada.

---

## C. Plano de implementação

| # | Prioridade | Entrega | Neste ramo |
|---|---|---|---|
| 1 | Bugs e usabilidade | paleta preferindo o acento vivo; backfill no perfil; logotipo nunca vira cena; esquema não duplicado; publicar guarda o anterior e permite restaurar; poda poupa as aplicadas | sim |
| 2 | Pipeline | fotos de produto entram no inventário e em hero/galeria; visão no perfil | sim |
| 3 | Perfil da marca | `brand_profiles`, análise incremental por arquivo, contradição, material temporário, fallback determinístico | sim |
| 4 | Catálogo de componentes | registro, tokens novos, `DesignV1` por área | sim |
| 5 | Vitrine | card, colunas, proporção, cabeçalho e navegação | sim |
| 6 | Produto e carrinho | layout e ênfase de preço | sim |
| 7 | Checkout | `branded`/`neutral` só por CSS | sim |
| 8 | Prévia e avaliação | prévia com tema, amostras, antes/depois e celular; lint + uma rodada de correção | sim (sem screenshot) |
| 9 | Avaliação por screenshot | renderizar a prévia num navegador headless sob demanda (worker separado, Playwright), mandar 3 capturas ao modelo crítico e aplicar uma rodada de correção | **não**: ver C.1 |

### C.1 Fora deste ramo, com alternativa

- **Crítica por screenshot**: exige um navegador headless no worker (cerca de 400 MB de
  imagem e CPU na hel1) e a vitrine renderizando rascunho por um caminho autenticado. A
  alternativa entregue é um lint determinístico sobre a configuração, seguido de uma rodada
  de correção. O próximo passo é um serviço `commerce-render` com fila própria, acionado só
  pelo botão "avaliar visualmente", com teto de uma avaliação por proposta.
- **Fontes enviadas pelo lojista**: ficam fora por segurança (arquivo de fonte é vetor de
  ataque) e por licença. O catálogo tem quatro famílias servidas pelo próprio app
  (`next/font`).
- **Mega menu e navegação em gaveta**: a navegação atual é uma faixa rolável por decisão
  documentada em `store-shell.tsx`. As variantes de faixa cobrem o estilo, não a estrutura.

---

## D. Implementação (ramo `feat/motor-identidade-visual`)

### D.1 O que cada causa virou

| Causa (§A.2) | Correção | Onde |
|---|---|---|
| 1. IA só alcança a home | A proposta passa a ser marca + desenho por área + blocos + explicação (`ProposalV1`). Publicar grava `branding`, `design` e `landing` | `design_rules.py`, `generation.py`, `drafts.py` |
| 2. Modelo cego | Leitura da marca com visão: miniaturas de 320px como `image_url` (data URL), só do material novo | `materials.py`, `prompt.build_brand_prompt`, feature `brand_profile` |
| 3. Paleta nunca extraída | O logotipo sem paleta ganha a paleta na primeira proposta (backfill fora da transação) | `materials.load_thumbnails`, `save_palettes` |
| 4. Extrator escolhia o fundo | Quantiza só os pixels com luz e saturação de marca. O emblema da SGPipas agora rende petróleo, amarelo e verde (antes `#042229`) | `color._ranked_accents`, `brand_accents` |
| 5. Emblema como hero | Material sem papel não vira mais cena; `kind` da leitura vence o papel; emblema e logotipo saem de bloco, e "texto sobre foto" sem foto vira "só texto" | `inventory._usable`, `design_rules.strip_logo_scenes`, `repair.py` |
| 6. Fotos de produto fora | As capas dos 6 primeiros produtos publicados entram como material e como imagem de bloco; a conferência de referências aceita dono `product` | `materials.collect_materials`, `inventory.with_analysis`, `setting_refs.py` |
| 7. Mesmo esqueleto | O compositor recebe o perfil e o catálogo de desenhos; uma proposta que fica na cor de fábrica numa marca com cor volta ao modelo com o motivo, e na última tentativa recebe a cor do perfil | `prompt.build_compose_prompt`, `design_rules.check_brand_fidelity` |
| 8. Publicar sem volta | `previous_settings` guardado ao publicar; "Voltar ao que estava antes" (só a última publicada); aviso quando a loja foi editada depois da proposta; a poda poupa as 3 últimas publicadas | `drafts.apply/revert/edited_since`, `generation._prune` |
| 9. Esquema duplicado | O texto do prompt leva o catálogo em linhas curtas (menos de 6 mil caracteres); o JSON Schema vai só em `response_json_schema` | `prompt.py`, `registry.catalogue_text` |

### D.2 Peças novas

- **`brand_profiles`** (migration `0052`): perfil, leitura por arquivo com checksum, digest
  do brief, versão, origem (`llm`/`deterministic`), tokens. O plano de releitura
  (`brand.plan_refresh`) só manda ao modelo o que é novo ou mudou, no máximo 8 por vez; o
  excedente fica marcado como pendente.
- **Leitura sem modelo** (`brand.deterministic_profile`): cor do Pillow, fundo escuro quando o
  logotipo é dominado por cor escura, tipografia e formas pelo ramo e pelo tom. É a reserva
  quando a visão falha ou a api-agents ainda não conhece `brand_profile`, e a semente que o
  modelo corrige. Sai com confiança baixa e com o que deduziu em `inferred`.
- **Tokens novos em `BrandingV1`**: `heading_case`, `shadow` (`soft`, `none`, `strong`,
  `glow`), `button_style` (`solid`, `pill`, `outline`, `block`); fontes `display` (Oswald),
  `elegant` (Playfair Display) e `geometric` (Montserrat), servidas pelo app.
- **`tenant_settings.design`** (`DesignV1`): cabeçalho (4), menu (3), card (5), proporção (3),
  grade (3), página de produto (2), preço (2), carrinho/checkout (2), rodapé (2).
- **Registro** (`registry.py`): rótulo para a lojista e dica para o modelo de cada valor. Um
  teste confere que todo `Literal` do motor está no catálogo e vice-versa.
- **Vitrine**: `storeStyle()` e `storeAttributes()` em `lib/theme.ts`; as variantes são CSS
  sob `data-*` na casca da loja (`store.module.css`, bloco "motor de identidade"). O padrão de
  cada atributo não tem regra: loja sem proposta não muda um pixel.
- **Painel** (Vitrine › Identidade da loja): o que a gente viu em cada imagem, com o papel
  editável; a identidade lida (resumo, personalidade, paleta, o que preserva, o que mudou,
  contradições, pendentes); a proposta com "o que muda" (antes → depois, montado dos valores,
  não do texto do modelo) e "por quê" (só das áreas que mudam); prévia da loja inteira
  (cabeçalho, home, vitrine, compra e rodapé) no computador e no celular, e "ver como está
  hoje"; versões com restaurar.

### D.3 Ordem de deploy e operação

1. **api-agents primeiro** (ramo `feat/brand-profile-feature`): feature `brand_profile` (teto
   de 3.000 tokens de saída, 100 por dia, temperatura até 0,4) e teto de `landing_draft` e
   `landing_refine` de 4.000 para 6.000. Sem isso o commerce funciona — a leitura cai no perfil
   sem modelo e a proposta sai —, mas sem visão.
2. **commerce**: migration `0052` (só acrescenta), API e web juntos. Nenhuma variável de
   ambiente nova. O teto de saída padrão (`landing_llm_max_output_tokens`) subiu de 4.000 para
   6.000; se o Env da stack fixar `LANDING_LLM_MAX_OUTPUT_TOKENS`, subir lá também. Vale o menor
   entre o commerce e a api-agents, então a ordem dos deploys não quebra nada.
3. **Rollback**: a migration desce limpa (`downgrade` testado no SQLite; no MariaDB só derruba
   tabela e colunas novas). Loja que publicou uma proposta com `design` fica com a chave gravada;
   a versão anterior do web a ignora (lê só `branding`), e os campos novos de `branding` são
   ignorados pelo tema antigo — a loja volta ao desenho antigo com as cores novas.

### D.4 Custo por proposta (estimado, Claude Sonnet)

- Leitura da marca, só com material novo: cerca de 1.500 tokens de imagem (8 de 320px) +
  ~2.500 de texto, e ~1.500 de saída. Sem material novo: zero.
- Proposta: ~6 a 8 mil de entrada (era 10.932 com o esquema duplicado e só a home), ~3 mil de
  saída. Uma rodada de correção só quando a proposta ignora a marca.

## E. Validação

- **Testes novos** (`tests/test_brand_identity.py`, 35): paleta do emblema escuro (ciano e
  amarelo, não o fundo); plano de releitura (sem perfil, nada novo, só o novo, arquivo
  trocado, brief novo, apagado, excedente); duas marcas opostas (esportiva escura × cosmético
  premium) saindo diferentes já no perfil sem modelo; material sem papel não vira hero; foto
  de produto entra e marca nunca; IA não troca o logotipo; valor inválido cai sozinho;
  regras de brilho e de fonte elegante; cor de fábrica volta ao modelo e depois é consertada;
  "o que muda" e "por quê" só do que muda; catálogo × `Literal`; esquema fora do texto; fluxo
  completo no formato da SGPipas (visão com 3 imagens, proposta com o perfil, emblema fora do
  hero, foto de produto na galeria, backfill da paleta); cache sem visão de novo e só o
  material novo na releitura; visão fora do ar sem derrubar a proposta; publicar as três
  chaves e restaurar; aviso de edição manual; perfil na tela e releitura; isolamento entre
  lojas; compatibilidade com lojas já configuradas.
- **Suítes**: backend com 1.351 passando na última rodada inteira (9 pulados; o 35º teste do
  arquivo novo entrou depois e passa), ruff e mypy limpos; 143 do web (18 do tema: padrões
  inalterados, compra neutra só onde há o que neutralizar, duas identidades com atributos
  diferentes); `next build` compila; 14 do gateway da api-agents.
- **E2E (Playwright, API semeada + vitrine)**: 36 de 36 — os 12 do painel (inclusive pedir,
  ver a prévia nova, publicar e conferir na loja, e nenhuma tela do painel rolando de lado no
  celular) e os 24 de vitrine, checkout, pagamento e visibilidade.
- **Visual** (`docs/16-validacao/`): a mesma plataforma servindo três lojas pela porta real de
  publicação — a SGPipas como está no ar (`01`), a SGPipas com uma proposta do motor (`02` a
  `06`, `10` no celular, com o logotipo e as fotos reais da loja) e um cosmético premium
  (`07` a `09`). A proposta foi escrita no formato que o compositor devolve: sem a api-agents
  local não há modelo, então o que está validado é a capacidade do motor e da vitrine, não a
  escolha do modelo. A validação dela é a primeira proposta real depois do deploy.
- **Achado da validação visual**, corrigido: a faixa de tom "escuro" numa loja de papel escuro
  saía clara (pintava com a tinta do texto). Agora fica um degrau abaixo do papel.

## Estado

Implementado no ramo `feat/motor-identidade-visual` (mucommerce) e
`feat/brand-profile-feature` (api-agents), sem deploy. Fora deste ramo: crítica por
screenshot (§C.1), fontes enviadas pelo lojista, mega menu.

Recomendação para a SGPipas depois do deploy: mandar o logotipo em PNG sem fundo (o atual é
JPEG com fundo branco e fica numa caixa branca sobre a faixa da marca) e pedir uma proposta
nova — a primeira já lê o material inteiro, porque o perfil ainda não existe.
