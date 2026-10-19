# Verificação M08.02 — catálogo operacional persistente — 2026-10-19

## Escopo e ambiente

Verificação local da implementação da issue #68 na branch `main`, HEAD inicial
`6d7107e M08.01`, Python 3.13.15 e PostgreSQL 17 descartável em container com
`tmpfs`, sem volumes. O diretório de trabalho estava limpo no início. Não houve
branch, commit, push, PR, alteração remota ou banco de trabalho utilizado.

O acesso direto às issues pelo navegador falhou e `gh` não estava instalado. O
texto integral da #68 fornecido na tarefa e os artefatos versionados da M08.01/#67,
#24 e #25 foram as fontes disponíveis. ADR 0041 permanece proposto.

## Comandos e resultados

| Comando | Resultado real |
| --- | --- |
| `git branch --show-current` | `main` |
| `git status --short` (inicial) | vazio |
| `git log -1 --oneline` | `6d7107e M08.01` |
| `uv run pytest ../../tests/api/test_vocabulary_catalog.py ../../tests/api/test_controlled_vocabularies.py` | 64 passed; 20 warnings rdflib |
| `uv run mypy src` | sucesso, 108 arquivos após correção de quatro retornos tipados |
| `uv run ruff check . ../../tests ../../scripts/verify-m04-integration.py ../../scripts/generate_catalogographic_openapi.py` | sucesso |
| `bash scripts/test-postgres.sh -k 'vocabulary_catalog or vocabulary_upgrade'` | 2 passed, 175 deselected, zero skips; cobre integração e migration M08.02 |
| `bash scripts/check.sh` | geradores e OpenAPI íntegros; API 2091 passed/513 warnings; Ruff e mypy passaram; interrompido no frontend porque `npm` não existe |
| `git diff --check` | sucesso nas execuções intermediárias; repetir no fechamento |

## Execuções PostgreSQL e correções

A primeira execução completa de `bash scripts/test-postgres.sh` encontrou cinco
falhas: quatro eram drift de nome de constraint nova e uma comparação de objetos ORM
de sessões diferentes. Após correção, a execução ampla repetida teve 173 passed,
2 failed e 2 deselected: um inventário do teste M08.02 não incluía a tabela plural
`vocabularies`, corrigido em seguida; a outra falha foi um teste preexistente de
listagem que recebeu 409 ao editar registro compartilhado entre testes. Não houve
falha nas tabelas M08.02 nessa repetição.

A seleção final M08.02 executou em bancos novos e exclusivos e passou duas vezes. A
última execução inclui reimportação concorrente e comprova:

- upgrade `0005_web_auth -> 0006_vocabulary_catalog`, `alembic check` e reupgrade;
- preservação das tabelas `experimental_semantic_*`;
- recusa de downgrade populado e downgrade vazio controlado;
- importação inicial e repetição idêntica sem duplicação;
- divergência explícita e rollback atômico após falha no meio;
- mesma URI em releases completa e parcial;
- labels multilíngues, labels/notações coincidentes entre conceitos distintos;
- duas importações concorrentes idênticas convergindo à mesma release;
- sucesso confiável preservado após falha transitória mais recente;
- TTL, retry, stale e escopo provider/configuração/contexto persistidos.

## Limites da evidência

- O script completo não chegou a frontend/build/Compose porque `npm` está ausente.
- A regressão PostgreSQL ampla teve a falha não determinística de listagem descrita;
  a suíte API completa, incluindo contratos HTTP/listagem sem PostgreSQL compartilhado,
  passou com 2091 testes.
- Não houve homologação de fonte LC, direitos, conectores, API/UI ou operação real.
- Warnings existentes: depreciação `ConjunctiveGraph` do rdflib e aviso FastAPI/
  Starlette TestClient; nenhum foi introduzido como falha da M08.02.
