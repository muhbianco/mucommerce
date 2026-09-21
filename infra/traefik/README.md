# Traefik — provider HTTP para domínios de tenant

O Traefik v3.7 da hel1 tem provider Swarm (labels), provider file (`/root/dynamic_conf.yaml`),
entrypoints `web` (redirect 308 → `websecure`) e `websecure`, ACME **HTTP-01**
(`letsencryptresolver`, storage `/etc/traefik/letsencrypt/acme.json`).

Domínios de tenant são dinâmicos. Em vez de editar labels ou o arquivo, o Traefik **puxa** a
configuração da `api-commerce`. A stack é versionada no repo **hel1-ops**
(`traefik/docker-stack.yml`; antes era só `/root/traefik.yaml`), com os args:

```yaml
- "--providers.http.endpoint=http://commerce-api:8000/api/v1/internal/edge/traefik"
- "--providers.http.pollInterval=15s"
- "--providers.http.pollTimeout=5s"
- "--providers.http.headers.X-Internal-Token=${INTERNAL_TOKEN_TRAEFIK}"
```

O endpoint devolve `{"http": {"routers", "services", "middlewares"}}` só com hosts em
`tenant_domains.status = active` (fora os de `STATIC_EDGE_HOSTS`, que continuam nas labels). Cada
router tem `tls.certResolver = letsencryptresolver`, então o Traefik pede o certificado HTTP-01 por
host assim que o router aparece. Aliases recebem `redirectRegex` permanente (308) para o host
primário; `chat.<tenant>` recebe 302 para o Chatwoot. O router `-api` de host de tenant exclui
`/api/(v1|latest)/internal`, como as labels.

## Aplicar

Na hel1: `cd /usr/src/hel1-ops && git pull && traefik/apply.sh --check && traefik/apply.sh`
(detalhes, rollback e cuidados com o token em `hel1-ops/traefik/README.md`). O Traefik reinicia:
~5-10 s sem borda para todos os sites. Antes, a `commerce` em produção precisa ter o router `-api`
sem `/internal` (commit "Edge: tenant-host API routers exclude /api/*/internal").

Verificar depois: `SMOKE_CUSTOM_HOSTS="<host de teste>" infra/scripts/smoke.sh --public-only`
(certificado válido, loja responde, `/internal` fechado no host).

## Comportamento em falha

- API fora do ar: o Traefik mantém a última configuração em memória; hosts estáticos (labels) não
  dependem do provider. Após restart do Traefik com a API fora, só os hosts dinâmicos ficam
  indisponíveis até a API voltar.
- JSON inválido: o provider ignora a resposta e loga; a config anterior permanece.
- `ETag`/`Cache-Control: max-age=15` reduzem o custo do polling.

## Também necessário

- DNS: `edge.muhbianco.com.br` (A → IP do hel1) como alvo de CNAME dos subdomínios dos clientes;
  `EDGE_PUBLIC_IPS` na stack `commerce` com o mesmo IP para a verificação de A records.
- Subdomínios de plataforma (`<slug>.loja.muhbianco.com.br`): registro curinga `*.loja` → CNAME
  `edge.muhbianco.com.br` na zona `muhbianco.com.br`. Sem ele o `apply.sh` recusa o deploy (o ACME
  falharia para esses hosts).
- Rate limit Let's Encrypt: 50 certificados/semana por domínio registrado. Domínios só entram no
  provider depois de verificados (TXT + A/CNAME), então não há tentativa ACME em DNS quebrado.
