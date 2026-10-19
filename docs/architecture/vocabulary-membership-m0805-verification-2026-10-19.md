# Verificação M08.05 — Integração de pertença vocabular — 2026-10-19

## Escopo e evidência

Execução local da issue #71. A implementação conecta o catálogo persistido de
vocabulários com a pré-validação, criação, PATCH e transição editorial para
publicação. Nenhuma consulta remota, migration, alteração de snapshot publicado,
Keycloak real ou migração do contrato DTO v1 foi incluída. Os perfis históricos
permanecem imutáveis; uma nova release de perfil, 1.2, seleciona a política de
pertença para as novas criações. O PATCH de registros 1.1 valida somente novos
valores.

| Comando | Resultado |
| --- | --- |
| `cd apps/api && uv run pytest ../../tests/api -q` | **2123 passed**, 514 warnings |
| `bash scripts/check.sh` | API, Ruff, mypy e artefatos: aprovados; script terminou com código 127 ao chegar a `npm`, não instalado no ambiente |
| `cd apps/api && uv run ruff check . ../../tests ../../scripts/generate_controlled_value_artifacts.py` | Aprovado |
| `cd apps/api && uv run mypy src` | Aprovado, 113 arquivos |
| `cd apps/api && uv run python ../../scripts/generate_controlled_value_artifacts.py --check` | 8 artefatos verificados |
| `cd apps/api && uv run python ../../scripts/generate_catalogographic_contract.py --check && uv run python ../../scripts/generate_catalogographic_openapi.py --check` | Ambos aprovados; OpenAPI sem drift |
| `bash scripts/test-postgres.sh` | PostgreSQL 17 descartável: **178 passed**, 2 deselected, zero skips; migrations até head |
| `git diff --check` | Aprovado |

O teste PostgreSQL de M08.05 registra um vocabulário completo em release local,
ativa-o com CAS, verifica pré-validação e rejeição de criação fora do conjunto,
aceita URI presente, e verifica que um PATCH fora do conjunto não avança revisão
nem ETag. A fixture remove seu release temporário e restaura o estado ativo
anterior mesmo em caso de falha. Testes unitários exercitam ausência em conjunto
completo/parcial, URI exata, estado deprecated/sucessor, indisponibilidade e
barreira de publicação sem substituição automática.

Os testes API completos incluem as regressões históricas de validação, contratos,
SHACL, edição e diagnósticos; a execução PostgreSQL isolada não teve skips.
Avisos observados incluem depreciação do RDFLib `ConjunctiveGraph` e integração
TestClient Starlette/httpx.

## Verificação web e limites

`npm run typecheck` não pôde iniciar porque `npm`/Node não estão disponíveis no
ambiente atual. `apps/web/node_modules/.bin/tsc` existe, mas seu shebang exige
`node`, que também não está instalado. Os testes de feedback e BFF foram
atualizados, mas não executados. `npm run build`, `npm test` e homologação visual
no navegador não foram executados. Nenhum runtime temporário foi baixado.

As mensagens pt/en distinguem pertença incompatível, pertença não verificada,
conceito descontinuado e fonte indisponível. Diagnósticos incluem caminho RDF e
URI; sucessor é somente sugestão. O frontend não foi homologado neste ambiente.
Não houve teste com provedor/LC online nem alegação de integração externa.

## Estado da execução

Resultados datados desta execução são independentes dos relatórios M08.02–M08.04.
Nenhum relatório histórico ou recurso remoto foi alterado. Alterações permanecem
locais para revisão; não houve commit, push ou PR.
