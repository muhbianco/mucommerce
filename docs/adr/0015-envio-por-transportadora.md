# ADR 0015 — Envio por transportadora via agregador, com a conta da própria loja

Data: 28/09/2026 · Estado: aceito

## Contexto

A entrega que existe (`app/fulfillment/`) é de frota própria: zonas por faixa de CEP ou bairro,
taxa fixa por zona, pedido mínimo e janelas de horário. Serve pizzaria e mercado de bairro. Não
serve quem vende para o Brasil inteiro: não cota preço real por endereço, não emite etiqueta e
não tem rastreio.

O dono pediu Correios (PAC e SEDEX, pelo rastreio), Jadlog e Azul Cargo, sempre com cálculo pelo
endereço de entrega, e a atualização do pedido quando o operador despachar.

Três integrações diretas significam três contratos (Correios exige contrato + cartão de
postagem no CWS; Jadlog exige contrato de embarcador; Azul Cargo, credencial própria), três
autenticações, três formatos de etiqueta e três formas de rastrear. Um agregador entrega as três
numa API só, sem contrato próprio com cada transportadora.

A plataforma é multi-loja. Cada loja já conecta as credenciais de pagamento dela; a MuhBianco
não fica no meio da conta de ninguém.

## Decisão

1. **Envio por transportadora é um terceiro modo de fulfillment**, ao lado de `pickup` e da
   entrega por zona. A entrega por zona **fica** — lojas com frota própria continuam usando. A
   loja escolhe o que oferece.

2. **Agregador, não integração direta.** O primeiro (e por ora único) provedor é o
   **Melhor Envio**, que cobre Correios (PAC, SEDEX, Mini Envios), Jadlog e Azul Cargo Express
   com cotação, etiqueta e rastreio.

3. **A conta é da loja, não da plataforma.** Cada tenant conecta a conta dela por OAuth; o
   token fica cifrado em `tenant_integration_credentials` (AES-GCM com AAD
   `tenant:provider:key_name`), o mesmo cofre dos pagamentos. Quem paga o frete e quem responde
   pela carteira pré-paga é a loja.

4. **`ShippingProvider` como porta**, no molde do `PaymentProvider` (ADR 0005): `quote`, `ship`,
   `track`, `cancel`. O domínio não conhece transportadora nenhuma; o registry resolve o
   provedor pela flag `shipping.<provider>` e pela allowlist da plataforma. Um `FakeProvider`
   cobre os testes, como em pagamentos.

5. **A cotação é congelada no pedido.** Ela vale por um tempo; no `place`, se venceu, recota, e
   se o preço mudou o cliente confirma antes de pagar. O pedido guarda transportadora, serviço,
   prazo e preço no snapshot de `fulfillment`, sem FK para configuração que muda.

6. **Despachar é gasto irreversível.** Comprar etiqueta debita a carteira da loja, então a ação
   é idempotente por `Idempotency-Key`, a tela mostra o valor antes de confirmar, e saldo
   insuficiente é erro esperado — com recado claro e sem mexer no estado do pedido.

7. **Produto sem peso e sem as três dimensões não cota.** A loja não liga o modo enquanto tiver
   produto ativo sem medida, e a tela lista quais faltam. Cotar com medida chutada é errar o
   preço do frete e comer a diferença no despacho.

## Consequências

**A favor**
- Uma integração em vez de três; as três transportadoras pedidas saem juntas.
- Sem contrato de transportadora para a MuhBianco nem para a loja pequena.
- Sandbox para fechar cotação e checkout sem conta real (saldo fictício; simula Correios e
  Jadlog).
- O molde de pagamentos já resolve credencial por loja, flag por módulo e teste com fake.

**Contra, e aceito**
- Dependemos de um intermediário: instabilidade dele é instabilidade nossa. Mitigação: timeout
  curto, cache da cotação, e o checkout **diz** que o frete está indisponível e oferece retirada
  em vez de sumir com a opção ou inventar preço.
- Carteira pré-paga: sem saldo, não há despacho. É operação da loja, e a mensagem precisa dizer
  isso sem rodeio.
- Tarifa do agregador pode perder para um contrato direto em volume alto. Quando isso doer, a
  porta `ShippingProvider` permite um provedor `correios` direto sem tocar no domínio — é
  exatamente para isso que ela existe.
- O preço cotado pode divergir do cobrado na etiqueta se peso/dimensão estiverem errados. O
  pedido guarda os dois valores para a loja ver a diferença.

## Alternativas descartadas

- **Integração direta com as três**: triplica superfície e manutenção, exige contratos que a
  loja pequena não tem, e adia a primeira venda com frete.
- **Tirar a entrega por zona**: fecharia a porta para lojas de bairro sem ganho real; os dois
  modos convivem no mesmo `FulfillmentService`.
- **Conta única da MuhBianco repassando o frete**: nos colocaria como intermediários
  financeiros do frete de terceiros, com repasse, conciliação e responsabilidade que ninguém
  pediu.

## Verificado na fonte

Cobertura das transportadoras e fluxo (cotação → carrinho → compra → etiqueta → rastreio) na
documentação do Melhor Envio; ambientes de produção e sandbox são separados e os dados não
passam de um para o outro. Os caminhos exatos de cada chamada ficam num só lugar
(`app/shipping/providers/melhorenvio.py`) e são confirmados contra o OpenAPI deles antes de
valer em produção.
