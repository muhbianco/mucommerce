# F1 fatia 1 — go-live na loja modelo (`loja.muhbianco.com.br`)

Pré-requisitos: CI verde no commit a subir e pedido explícito de deploy (skill `deploy`). Tudo abaixo roda no hel1. Nenhum valor secreto passa pelo chat.

## 1. Deploy (skill `deploy`, produto Commerce)

1. Build/push das imagens com `TAG=$(git rev-parse --short=12 HEAD)`.
2. Migrations `0004_catalog`, `0005_media_assets` e `0006_inventory`. Só criam tabelas; rollback de código não exige downgrade.
   ```bash
   infra/scripts/commerce-migrate.sh $TAG
   ```
3. StackUpdate com as chaves do MinIO, que entram pela primeira vez:
   ```bash
   python3 infra/scripts/portainer-stack-update.py --stack commerce --yaml infra/docker-stack.yml \
     --set-env COMMERCE_TAG=$TAG --env-file /root/.mucommerce-minio.env --dry-run
   ```
   O dry-run precisa listar `MINIO_ACCESS_KEY` e `MINIO_SECRET_KEY` em `env_keys` e não abortar. Depois rodar sem `--dry-run`. Serviço novo: `commerce_commerce-media` (fila `commerce.media`, 1 processo).
4. Verificar que `commerce_commerce-api|web|worker|media|beat` estão 1/1 na tag nova.
5. Rodar `wget -qO- http://commerce-api:8000/readyz` de dentro da `chatbot-net`. O esperado é `"storage": true`. Se vier `false`, a API está sem acesso ao MinIO (endpoint `http://minio:9000` ou chaves).

## 2. Acesso ao painel (operador, no terminal do hel1)

Hoje não há admin no banco. A senha é digitada por você; nunca em argv nem no chat:

```bash
infra/scripts/commerce-cli.sh $TAG python -m app.cli admin bootstrap --email <seu-email>
```

O comando cria o superadmin e pede a senha duas vezes (mínimo de 12 caracteres). Com isso você entra em `https://painel.muhbianco.com.br` e tem acesso a qualquer loja pelo papel de plataforma. Um membro com papel `owner` só da loja modelo, se quiser, sai com `admin grant --email … --tenant-slug muhbianco --role owner --create`.

## 3. Ligar a loja modelo (painel → Ops → muhbianco)

1. Módulos: ligar `catalog` e `inventory` (os demais não mudam).
2. Acesso à vitrine: `public`.
3. Em Configurações da loja:
   - enviar o logo e escolher a cor;
   - preencher o SEO e marcar "aparecer no Google";
   - montar a página inicial (hero, destaques e contato).
4. Categorias e cerca de 10 produtos, cada um com pelo menos uma imagem. Publicar exige preço maior que zero e imagem pronta. Dar entrada de estoque nos produtos com estoque controlado.

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
