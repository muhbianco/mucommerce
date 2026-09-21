# ADR 0008 — Sessões da aplicação em READ COMMITTED

Data: 2026-09-21 · Status: aceito

## Contexto

O MariaDB do hel1 roda com o padrão `REPEATABLE-READ` (binlog desligado, formato `MIXED`). Nesse nível, uma leitura com lock (`SELECT … FOR UPDATE`) que não encontra linhas trava o *gap* do índice. O `outbox.emit` calcula a sequência do agregado com `SELECT max(sequence) … FOR UPDATE`. Para o primeiro evento de um agregado novo não há linhas, e como os ids são UUIDv7 (sempre os maiores), todos os agregados novos caem no mesmo gap, o do fim do índice.

Duas transações concorrentes criando agregados novos pegam esse gap juntas, o que é permitido. Depois, cada uma tenta inserir e espera pela outra: **deadlock**. O MariaDB mata uma delas e o cliente recebe 500. A F1 torna isso comum: criar produtos e confirmar várias fotos em paralelo.

## Decisão

- As engines da aplicação (API, workers, CLI) abrem sessões em `READ COMMITTED` (`app.core.database.APP_ISOLATION_LEVEL`, `create_app_engine`). Nesse nível, buscas com lock travam só as linhas encontradas, sem gap lock, e cada leitura vê o último commit.
- O código não depende de snapshot repetível. Toda leitura seguida de escrita já usa lock explícito (`FOR UPDATE`, `SKIP LOCKED`, upsert com `ON DUPLICATE KEY`). Onde a ordem importa, os locks são tomados em ordem determinística (saldos de estoque por `variant_id`).
- A sessão de teste em MariaDB usa as mesmas opções. `tests/test_concurrency_mariadb.py` cobre três cenários: a última unidade disputada, locks em ordens opostas e primeiros eventos concorrentes.
- As sessões dos testes também usam `autoflush=False`, como em produção.

## Consequências

- Leituras não travadas dentro de uma transação podem ver commits de outras entre um statement e outro. Quem precisa de consistência trava (já é a regra).
- Se um dia o binlog for ligado, ele precisa estar em `ROW` ou `MIXED`: `READ COMMITTED` não é seguro com `STATEMENT`.
