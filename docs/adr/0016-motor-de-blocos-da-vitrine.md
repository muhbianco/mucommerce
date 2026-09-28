# ADR 0016 — Motor de blocos da vitrine: o esquema alarga, o gosto mora no editor

Data: 28/09/2026 · Estado: aceito

## Contexto

A página inicial de cada loja é uma lista de blocos guardada em `tenant_settings.landing`,
validada por Pydantic e desenhada pela vitrine. O desenho é bom: união discriminada por `type`,
texto puro, ids conferidos contra as linhas do próprio tenant. Nada que o lojista escreva vira
marcação.

O que não estava bom era o alcance. Seis tipos de bloco, uma forma só de mostrar cada um, e o
editor do painel expondo quatro deles. Quem montava a página saía com a mesma pilha vertical de
sempre — e foi o que aconteceu: a SG Pipas tem um logotipo bonito e uma página inicial com
"Bem-vindo" e um bloco de contato.

Ampliar um esquema que já tem dado salvo em produção levanta três perguntas, e é sobre elas que
esta decisão é.

## Decisão

1. **O esquema só alarga, nunca estreita.** Campo novo entra opcional e com padrão; valor novo
   de enum entra no fim da lista. O JSON que já está salvo continua validando, e nenhuma loja
   acorda com a página quebrada por causa de um deploy.

2. **Sobe `schema_version` só quando o valor armazenado deixaria de validar.** Foi o caso da
   migração `0017`, em que `fulfillment` tinha `modes: [...]` e passou a ter `pickup: {...}` —
   com `extra="forbid"`, a linha antiga virava 422 e precisava de migração de dados. Campo
   opcional com padrão não faz isso. **Consequência prática: a ampliação dos blocos não tem
   migration nenhuma.**

3. **Todo padrão reproduz o desenho anterior.** `variant` e `tone` entram com o valor que já era
   renderizado, e há um teste por bloco cobrando isso. É a diferença entre "a loja ganhou
   opções" e "a loja mudou sem pedir".

4. **Gosto mora no editor e no gerador, não no validador.** "No máximo um destaque por página"
   é opinião. Opinião no Pydantic transforma em erro de validação uma página que já está salva e
   funcionando — o lojista descobre ao tentar mudar outra coisa. O validador cuida de forma:
   tipo, tamanho, formato.

5. **Limite agregado no resolver, não no esquema.** Dezesseis blocos podendo pedir doze imagens
   cada dão cento e noventa e duas imagens numa consulta. O resolver corta no teto da página (32
   imagens, 48 produtos, 20 categorias) e a página sai sem o excedente. Recusar na escrita seria
   transformar em erro o que já está gravado.

6. **Nome de ícone é append-only.** O valor fica em `tenant_settings`; remover um membro do
   `Literal` invalida dado salvo. Ícone aposentado continua na lista e o front desenha um
   genérico.

7. **Tom é enum, não cor.** `brand` usa `--brand-primary` com o par que `onColor` garante
   legível. Um seletor de cor por bloco seria um jeito de deixar a loja ilegível em dois cliques,
   e ninguém pediu isso.

8. **Cada bloco tem id estável.** Sem ele o editor identificaria bloco por posição, e subir o
   terceiro com outra aba aberta gravaria "o terceiro" querendo dizer outro bloco. O id é
   atribuído na escrita, no mesmo molde que locais de retirada e zonas de entrega já usam. Id que
   a loja nunca teve é recusado, em vez de virar bloco fantasma.

9. **Um resolver, duas portas.** `app/landing/resolver.py` serve a vitrine e a prévia do painel.
   A prévia não diverge do que o cliente vê por construção, não por disciplina — se divergisse,
   o lojista publicaria confiando numa tela que mente.

10. **Sem iframe na prévia.** O CSP manda `frame-ancestors 'none'`, e abrir exceção cobriria só o
    endereço da plataforma, não o domínio próprio de cada loja. Painel e vitrine são o mesmo app
    Next: o painel importa o renderizador direto.

## Consequências

**A favor**

- Doze tipos de bloco e vários arranjos sem uma linha de DDL.
- A prévia é o componente de produção, então "ficou diferente no ar" deixa de ser possível.
- O editor pode mover, duplicar e remover com segurança, porque fala de id.
- Perguntas frequentes rendem `FAQPage` no JSON-LD — o bloco que mais dá retorno em busca.

**Contra, e aceito**

- A lista de ícones nunca encolhe. Em alguns anos ela terá nomes que ninguém usa. É o preço de
  não invalidar dado salvo, e o custo é um `Literal` comprido.
- O teto por página descarta silenciosamente. Uma loja que salvar quarenta imagens vê trinta e
  duas. Preferimos isso a recusar a escrita de algo que já estava lá.
- Duas fontes de verdade sobre "quais tipos existem": o `Literal` do Pydantic e a lista que o
  editor oferece. Um teste compara as duas, porque um tipo oferecido e não implementado vira um
  422 na cara do lojista.
- Sem vídeo e sem mapa embutido. Os dois pedem `frame-src`, que é um buraco no CSP de **toda**
  loja para um recurso que um link resolve. O endereço vira link de busca; o vídeo, um link.

## Alternativas descartadas

- **`LandingV2` com migração de dados**: seria o caminho se algum campo tivesse mudado de forma.
  Nenhum mudou; a versão existe para quando isso acontecer, e gastá-la agora tiraria o sinal dela.
- **Permitir HTML ou Markdown no bloco de texto**: devolveria ao lojista o poder de quebrar a
  própria página e abriria a porta de injeção que o motor fecha desde o primeiro dia.
- **Seletor de cor por bloco**: contraste deixaria de ser garantia e viraria sorte.
- **Prévia por iframe**: exigiria afrouxar `frame-ancestors` em todo domínio de loja.
- **Editor com arrastar e soltar**: exige JavaScript, e o painel inteiro funciona sem ele. Subir
  e descer com botão resolve o mesmo problema e sobrevive a um celular ruim.
