# ADR 0003 — Domínios de tenant via `providers.http` do Traefik, certificados HTTP-01 por host

Data: 2026-09-20 · Status: aceito

## Contexto

O Traefik v3.7 do hel1 usa ACME **HTTP-01** (sem DNS-01, sem wildcard) e rotas estáticas por labels Swarm ou pelo file provider `/root/dynamic_conf.yaml`. Domínios de tenant (`lunares.com.br`, `www.lunares.com.br`, `chat.lunares.com.br`) são dinâmicos. Caddy existe, mas só como roteador interno do Muchat; on-demand TLS exigiria passthrough TCP `HostSNI(*)` ou trocar a borda.

## Decisão

A `api-commerce` expõe `GET /internal/edge/traefik` (token interno, ETag) com routers/services/middlewares de todos os `tenant_domains.status = active`. O Traefik ganha `--providers.http.endpoint=...` com `pollInterval=15s`. Cada router leva `tls.certResolver = letsencryptresolver`, então o certificado é emitido por host no primeiro acesso. Domínios só entram após verificação de TXT (`_muhbianco-verify`) e A/CNAME apontando para o hel1, o que evita tentativas ACME em DNS quebrado e impede que um domínio alheio caia num tenant.

## Consequências

- Borda única, pipeline de deploy inalterado, Caddy intacto.
- API fora do ar: Traefik mantém a última config em memória; hosts estáticos não dependem do provider.
- Wildcard `*.loja.muhbianco.com.br` fica para quando houver API do provedor DNS (DNS-01).
- Aliases fazem 308 para o primário via `redirectRegex`; `chat.<tenant>` faz 302 para o Chatwoot.
