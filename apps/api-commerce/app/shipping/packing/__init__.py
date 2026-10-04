"""Motor de embalagem (puro, sem I/O): das linhas do carrinho para o plano de volumes.

- `model`: tipos e constantes; `placement`: onde cada unidade fica (grade e extreme points);
- `candidates`: as estratégias e os planos candidatos; `custom`: a caixa sob medida, para quando
  nenhuma embalagem cadastrada serve (cadastrar embalagem é opcional);
- `scoring`: estimativa local para escolher o que vale cotar; `canonical`: forma e hash do plano;
- `declared`: a regra da capacidade declarada pela loja.

Ver docs/13-frete-v2.md.
"""
