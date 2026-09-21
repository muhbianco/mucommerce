# Traefik — provider HTTP para domínios de tenant

Hoje (`/root/traefik.yaml` no hel1) o Traefik v3.7 tem: provider Swarm (labels), provider file
(`/root/dynamic_conf.yaml`), entrypoints `web` (redirect 308 → `websecure`) e `websecure`, ACME
**HTTP-01** (`letsencryptresolver`, storage `/etc/traefik/letsencrypt/acme.json`).

Domínios de tenant são dinâmicos. Em vez de editar labels ou o arquivo, o Traefik passa a **puxar**
a configuração da `api-commerce`:

```yaml
# adicionar em services.traefik.command (stack traefik)
- "--providers.http.endpoint=http://commerce-api:8000/api/v1/internal/edge/traefik"
- "--providers.http.pollInterval=15s"
- "--providers.http.pollTimeout=5s"
- "--providers.http.headers.X-Internal-Token=${INTERNAL_TOKEN_TRAEFIK}"
```

O endpoint devolve `{"http": {"routers", "services", "middlewares"}}` só com hosts em
`tenant_domains.status = active`. Cada router tem `tls.certResolver = letsencryptresolver`, então o
Traefik pede o certificado HTTP-01 por host no primeiro acesso. Aliases (`www`) recebem
`redirectRegex` permanente (308) para o host primário; `chat.<tenant>` recebe 302 para o Chatwoot.

## Aplicar (janela curta: o Traefik reinicia em ~5 s)

1. Colocar `INTERNAL_TOKEN_TRAEFIK` no Env da stack `traefik` do Portainer (mesmo valor que a stack `commerce`).
2. Adicionar as quatro linhas acima no `command` via Portainer (Editor YAML) e Update.
3. Verificar: `docker service logs traefik_traefik --since 2m | grep -i provider` sem erro; o dashboard
   lista os routers `<slug8>-<n>-web/-api` quando houver domínio ativo.
4. Rollback: remover as linhas e Update. Labels e file provider continuam intactos.

## Comportamento em falha

- API fora do ar: o Traefik mantém a última configuração em memória; hosts estáticos (labels) não
  dependem do provider. Após restart do Traefik com a API fora, só os hosts dinâmicos ficam
  indisponíveis até a API voltar.
- JSON inválido: o provider ignora a resposta e loga; a config anterior permanece.
- `ETag`/`Cache-Control: max-age=15` reduzem o custo do polling.

## Também necessário

- DNS: `edge.muhbianco.com.br` (A → IP do hel1) como alvo de CNAME dos subdomínios dos clientes;
  `EDGE_PUBLIC_IPS` na stack `commerce` com o mesmo IP para a verificação de A records.
- Rate limit Let's Encrypt: 50 certificados/semana por domínio registrado. Domínios só entram no
  provider depois de verificados (TXT + A/CNAME), então não há tentativa ACME em DNS quebrado.
