# F1 fatia 1 — go-live na loja modelo (`loja.muhbianco.com.br`)

Pré-requisitos: pedido explícito de deploy (skill `deploy`). Desde a migração para o Woodpecker, **deploy = push no `main`**: `.woodpecker/ci.yaml` precisa ficar verde e `.woodpecker/deploy.yaml` faz os passos 1–4 abaixo sozinho (`infra/scripts/deploy.sh <sha12>` dentro de `hel1-deploy exec`). Os comandos abaixo ficam como referência/break-glass e rodam no hel1. Nenhum valor secreto passa pelo chat.

## 1. Deploy (skill `deploy`, produto Commerce)

1. Build/push das imagens com `TAG=$(git rev-parse --short=12 HEAD)`.
2. Migrations `0004_catalog`, `0005_media_assets` e `0006_inventory`. Só criam tabelas; rollback de código não exige downgrade.
   ```bash
   infra/scripts/commerce-migrate.sh $TAG
   ```
3. StackUpdate com as chaves do MinIO, que entram pela primeira vez:
   ```bash
   python3 /usr/src/hel1-ops/scripts/portainer-stack-update.py --stack commerce --yaml infra/docker-stack.yml \
     --set-env COMMERCE_TAG=$TAG --env-file /root/.mucommerce-minio.env --dry-run
   ```
   O dry-run precisa listar `MINIO_ACCESS_KEY` e `MINIO_SECRET_KEY` em `env_keys` e não abortar. Depois rodar sem `--dry-run`. Serviço novo: `commerce_commerce-media` (fila `commerce.media`, 1 processo).
4. Verificar que `commerce_commerce-api|web|worker|media|beat` estão 1/1 na tag nova.
5. Rodar `wget -qO- http://commerce-api:8000/readyz` de dentro da `chatbot-net`. O esperado é `"storage": true`. Se vier `false`, a API está sem acesso ao MinIO (endpoint `http://minio:9000` ou chaves).

## 2. Acesso ao painel

Não há admin separado: o painel entra com a conta MuhBianco (ADR 0009). Quem é `admin` no site entra como superadmin da plataforma. O comando `admin bootstrap` da CLI fica só para emergência, quando o site estiver fora do ar.

## 3. Ligar a loja modelo (admin do site → Lojas, depois painel)

1. Em `https://muhbianco.com.br/admin.html#lojas`, na loja `muhbianco`:
   - defina o dono (a sua conta);
   - em Módulos, ligue `catalog` e `inventory`;
   - em "Quem vê a vitrine", escolha `Pública`.
2. Em `https://painel.muhbianco.com.br`, entre com Google e abra a loja. Em Configurações:
   - envie o logo e escolha a cor;
   - preencha o SEO e marque "aparecer no Google";
   - monte a página inicial (hero, destaques e contato).
3. Cadastre categorias e cerca de 10 produtos, cada um com pelo menos uma imagem. Publicar exige preço maior que zero e imagem pronta. Dê entrada de estoque nos produtos com estoque controlado.

Rollback funcional sem deploy: desligar `catalog` (a vitrine e as rotas do painel voltam a 404/403) ou trocar o acesso para `whitelist`.

## 4. Aceite

Pela internet:

```bash
curl -s https://loja.muhbianco.com.br/api/v1/storefront/catalog/products | head -c 300   # 200 com itens
curl -sI https://loja.muhbianco.com.br/loja/produto/<slug>                               # 200
curl -s https://loja.muhbianco.com.br/robots.txt                                          # Allow + Sitemap
curl -s https://loja.muhbianco.com.br/sitemap.xml | head                                  # produtos listados
curl -s https://painel.muhbianco.com.br/robots.txt                                        # Disallow: /
curl -sI https://storage.s3.muhbianco.com.br/commerce-public/tenants/<tenant>/media/<id>/<hash>/w600.webp
#   → 200, content-type image/webp, cache-control immutable
curl -s -o /dev/null -w '%{http_code}\n' https://storage.s3.muhbianco.com.br/commerce-public/  # 403 (sem listagem)
```

- Enviar uma foto pelo painel: fica `pronta` em até 30 s.
- Ajuste de estoque repetido (reenviar o mesmo formulário) não duplica o movimento.
- Trocar para `whitelist`: `/api/v1/storefront/catalog/products` passa a responder 401, `/loja` redireciona para `/entrar` e a home fica sem produtos. Depois voltar para `public`.
- `npx lighthouse https://loja.muhbianco.com.br/loja/produto/<slug> --only-categories=seo` ≥ 90, e o mesmo na home. Exige loja `public` e "aparecer no Google" marcado.
- No dia seguinte, o log do `commerce-worker` mostra `audit_inventory_ledger` sem "Inventory ledger mismatch".

## O que esta fatia não tem (próximas)

Login de cliente e whitelist por Google (fatia 2), Chatwoot (fatia 3), domínios próprios (fatia 4), carrinho e pedidos (F2). A página de produto mostra "Pedidos online chegam em breve".
