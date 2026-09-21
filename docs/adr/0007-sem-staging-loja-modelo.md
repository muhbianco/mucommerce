# ADR 0007 — Sem staging: `loja.muhbianco.com.br` é a loja modelo em produção

Data: 2026-09-21 · Status: aceito

## Contexto

O desenho original (02, 08, 09) previa um staging no hel1: stack `commerce-staging`, banco `mucommerce_staging` e hosts `staging.*`. Na Fase 0 só a stack `commerce` subiu, e `staging.loja.muhbianco.com.br` virou um alias do tenant de produção no mesmo banco. Não era staging nem isolado; só um segundo host para a mesma loja.

O hel1 é uma VPS única (8 vCPU / 16 GB, dezenas de containers), e ainda não há tenant pagante. O dono da plataforma quer testar na própria loja.

## Decisão

- Não haverá ambiente de staging nem hosts alias. O tenant `muhbianco` responde só em `loja.muhbianco.com.br`. Ele é a **loja modelo**: vitrine de demonstração e lugar dos testes em produção.
- `staging.loja.muhbianco.com.br` e `muhbianco.loja.muhbianco.com.br` ficam desativados (`python -m app.cli tenant disable-domain`). `PLATFORM_ALIAS_HOSTS` deixa de existir.
- `loja.`, `painel.` e `api-commerce.` são hosts estáticos, roteados por labels da stack. Nunca entram na config dinâmica do Traefik e a re-verificação de DNS nunca os desativa (`settings.static_edge_hosts`).
- Testes antes do deploy: `pytest` (SQLite) mais migrations e suíte de vazamento em MariaDB 10.11 no CI, `make dev` local quando precisar de integração.

## Mitigações (obrigatórias, porque produção é o único ambiente)

- **Feature flag por tenant** em toda funcionalidade nova. Ela é ligada primeiro só na loja modelo. Rollback = desligar a flag.
- **Migrations expand/contract**: só adicionar no deploy da feature; remover/renomear em deploy posterior. Rodam antes do `StackUpdate` (`infra/scripts/commerce-migrate.sh <tag>`).
- **Imagens por sha** (`COMMERCE_TAG`), nunca `:latest`: rollback = voltar o tag anterior.
- **Recuperação**: snapshots da VM feitos pelo operador (decisão de 21/09: sem backup agendado por
  banco por enquanto). Antes de migration irreversível, dump manual com
  `infra/backup/mariadb_backup.sh`; restore testado em `docs/runbooks/backup-restore.md`.
- Deploy fora de horário de pico. Verificação na loja modelo (`/healthz`, vitrine, `outbox ping`).

## Consequências

- Não há como ensaiar Let's Encrypt, webhooks do Chatwoot ou OAuth fora de produção. Esses fluxos entram atrás de flag e são exercitados primeiro na loja modelo.
- Um bug que escape da flag atinge produção. A suíte de vazamento e os testes de contrato são a defesa principal.
- Se surgir tenant pagante com SLA, reavaliar: um staging efêmero (compose no CI ou stack sob demanda) volta a ser opção.
- Documentos 02, 08 e 09 que citam `commerce-staging` descrevem o desenho anterior. O aceite das fases passa a ser na loja modelo.
