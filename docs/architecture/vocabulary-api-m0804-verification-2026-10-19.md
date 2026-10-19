# Verificação M08.04 — API de vocabulários — 2026-10-19

## Estado e escopo da execução

Verificação local da implementação da issue #70 em `main`. O checkout iniciou
limpo, no commit `d745d3b M08.03`; nenhuma migration foi criada para esta API. As
issues observadas nesta execução eram #70 aberta, #68 fechada, #69 aberta e #24/#25
fechadas. A leitura não alterou issues ou outros recursos remotos.

O runtime Node 24 não estava inicialmente disponível. Para executar a validação web,
foi usado Node `v24.22.0` e npm `11.19.1` em `/tmp`, baixados da distribuição oficial
e verificados pelo SHA-256 publicado. O runtime temporário não foi adicionado ao
repositório.

## Evidências executadas

| Comando/execução | Resultado |
| --- | --- |
| `PATH=/tmp/libris-node24/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin bash scripts/check.sh` | Exit 0; contratos e OpenAPI verificados; API: **2118 passed**, 513 warnings; Ruff passou; mypy passou em 113 arquivos |
| `cd apps/web && npm test` (executado por `scripts/check.sh`) | OpenAPI client sem drift; **236 passed**, zero falhas/skips |
| `cd apps/web && npm run build` (executado por `scripts/check.sh`) | Build Next.js 16.4.0 completado; compilação e geração das páginas passaram |
| `cd apps/web && npm run typecheck` (executado por `scripts/check.sh`) | TypeScript passou |
| `docker compose --env-file .env.example config --quiet` (executado por `scripts/check.sh`) | Exit 0 |
| `bash scripts/test-postgres.sh -k vocabulary_api` | PostgreSQL 17 descartável; **1 passed**, 178 deselected, zero skips; migração até head aplicada pelo runner |
| `cd apps/api && uv run python ../../scripts/generate_catalogographic_openapi.py --check` | Baseline runtime M08.04 íntegra, sem drift |
| `cd apps/web && npm run openapi:generate` | Cliente e tipos gerados a partir do snapshot OpenAPI M08.04 |
| `cd apps/web && npm run openapi:check` | Cliente TypeScript gerado sem drift |

O teste PostgreSQL cobre listagem/cache condicional, busca por label/notação/URI,
fallback pt/en, Unicode composto/decomposto e sensibilidade a acentos, filtros de
status/idioma/release, detalhe, releases completas/parciais, validação `resolved`,
`deprecated`, `invalid`, `unverified` e `unavailable`, e preservação de evidência
resolvida após tentativa transitória malsucedida.

O dataset do teste contém 104 conceitos. Duas páginas consecutivas cobriram todos
eles sem duplicação/perda; duas consultas de página executaram no máximo oito
instruções SQL no total, e a busca paginada executou no máximo quatro. A contagem é
independente do número de resultados, sem padrão N+1.

As 513 warnings da suíte API não falharam a execução; entre os avisos observados
estão depreciações do RDFLib (`ConjunctiveGraph`) e do `TestClient` Starlette/httpx.

## Limites da evidência

- O runner executou a integração PostgreSQL da API com banco descartável, mas não
  executou downgrade de migration; não houve migration nova nesta entrega.
- Não foi feita homologação com Keycloak real, fonte LC remota, provedor externo ou
  navegador autenticado. A validação foi local e sem acesso remoto a vocabulários.
- O catálogo não possui estado separado de aprovação de fonte; o filtro operacional
  é vocabulário `active` mais release `active`, com autorização backend exigida.
  Direitos e homologação institucional permanecem pendentes como na documentação
  M08.03.
- Testes de performance usam contagem de SQL e dataset reproduzível, sem threshold
  de tempo arbitrário ou benchmark de produção.

## Estado do Git desta execução

Branch `main`. Nenhum commit, push, branch, pull request ou alteração remota foi
feito. As alterações de código/documentação ficam locais para revisão manual.
