# Verificação M07.03 — editor de Item — 2026-10-18

## Contexto e fontes

Issue #63 e dependência #62 consultadas integralmente, somente leitura. #63 estava
aberta e #62 fechada; a implementação foi conferida na main local. Branch inicial
`main`, `git status --short` e `git diff --stat` vazios: não havia alterações
preexistentes. Nenhuma branch, commit, PR, staging, push ou alteração remota foi
realizada. Nenhum checkout, merge, rebase, cherry-pick, reset ou stash foi usado.

Foram consultados AGENTS, README, arquitetura/domínios, contrato catalográfico,
contrato HTTP e testes, perfis, ciclo editorial, autorização, plano M07, editor de
Instance, ADRs 0028/0029/0040, DTOs, OpenAPI e fixtures/testes existentes. O relatório
M07.02 foi tratado como evidência histórica. Não foi localizado relatório autônomo
M06.07 pelo nome da entrega; os registros M06.04, M07.01/M07.02 e o código atual
foram usados sem presumir homologação. Os documentos obrigatórios estavam presentes.

## Implementação e limites deliberados

- Rotas administrativas pt/en de criação, detalhe, edição e lista geral de Item;
  listagem contextual de relações inversas em Work e Instance.
- Work → Instance → Item usa relações persistidas e identidades explícitas.
  Nenhum registro pai é duplicado/revisado na criação de Item.
- BFF com cliente gerado; sessão OIDC server-side, proteção de origem/CSRF,
  permissões e If-Match. FastAPI conserva decisão efetiva e CAS transacional.
- Identificadores com fontes, notas e histórico de custódia repetíveis/idiomas;
  demais grupos somente leitura e omitidos do PATCH. Item exige identificador Local.
- itemOf fixo na edição; criação confere Instance por UUID e URI. Conflito que
  altera associação bloqueia conciliação, preservando entrada local.
- Perfil histórico de escrita 1.1, metadados editoriais da mesma geração, sem
  transições/publicação automática ou adoção fictícia de perfil 2.0.
- Reforço compatível no adaptador de Item: compara subgrafos por isomorfismo e
  bloqueia substituição de grupos não representáveis sem perda, incluindo
  datatype explícito. Erro existente 400/request.malformed; sem novo contrato.
- Documentação e UI distinguem bf:Item de inventário/circulação. Sem novas tabelas,
  migrações, dependências, papéis, serviços remotos ou clientes gerados editados.

Detalhes de uso/contratos: [editor de Item](item-editor.md).

## Execuções e evidências

Data local: 2026-10-18, America/Manaus. Node 24.16.0 e Python 3.13.15/uv do projeto.
Node/uv não estavam no PATH do sandbox; comandos de teste executados com aprovação
no host, usando as instalações existentes. Docker acessível somente fora do sandbox.
Sem instalação de dependências e sem uso de bancos de trabalho.

| Comando | Resultado observado |
| --- | --- |
| `git branch --show-current`, `git status --short`, `git diff --stat` | main e working tree inicialmente limpo |
| `npm run typecheck` — intermediário | Passou após correção dos tipos; tentativas anteriores falharam e foram corrigidas |
| `node --conditions=react-server --import tsx --test tests/item-bff.test.ts tests/item-editor.test.ts` | 19 passed, zero falhas/skips |
| `bash scripts/check.sh` — primeira execução | Exit 0: 2.086 API; Ruff; mypy 106 arquivos; 224 web; OpenAPI sem drift; build, typecheck e Compose aprovados |
| `bash scripts/test-postgres.sh` — antes do reforço de datatype | 170 passed, 2 deselected (Keycloak), zero skips; 170,80 s |
| `uv run pytest ../../tests/api/test_dto_rdf_adapters.py` — após reforço | 47 passed, zero falhas/skips; 14,52 s |
| `uv run ruff check . ../../tests` | Aprovado após reforço |
| `uv run mypy src` | Aprovado em 106 arquivos após reforço |
| `TEST_WEB_BROWSER_ENABLED=1 node --conditions=react-server --import tsx --test tests/item-editor.e2e.ts` — repetição corrigida | 1 passed, zero falhas/skips; React/Chromium reais, ações simuladas |
| `bash scripts/test-item-editor.sh` — repetição corrigida | 2 passed, zero falhas/skips; 11,69 s; componente + HTTP real/PostgreSQL |
| `bash scripts/test-instance-editor.sh` — regressão M07.02 | 2 passed, zero falhas/skips; 10,57 s |

### Execução final PostgreSQL

`bash scripts/test-postgres.sh`: exit 0, **170 passed, 2 deselected (Keycloak),
zero falhas/skips**, 168,03 s. Inclui o bloqueio novo de datatype com rollback
verificado por ETag/grafo, seguido de edição permitida em grupo independente.
JUnit temporário: `/tmp/libris-postgres-junit.GW3VaV.xml`. Os 3.499 warnings são
de depreciação do parser JSON-LD RDFLib; não são falhas nem skips.

### Execução final do gate

`bash scripts/check.sh`: **exit 0** sobre o reforço de preservação de Item.

| Comando executado pelo gate | Resultado final |
| --- | --- |
| Geradores de contrato e OpenAPI em `--check` | Sem drift |
| `uv run pytest` | 2.088 passed, zero falhas/skips; 513 warnings; 229,46 s |
| `uv run ruff check . ../../tests` (gate inclui scripts adicionais) | Aprovado |
| `uv run mypy src` | Aprovado em 106 arquivos |
| `npm test` | 224 passed, zero falhas/skips; cliente OpenAPI atualizado |
| `npm run build` | Aprovado; rotas Item incluídas no build de produção |
| `npm run typecheck` | Aprovado |
| `docker compose --env-file .env.example config --quiet` | Aprovado com credenciais efêmeras do runner |

A alteração incidental que Next.js produziu em `next-env.d.ts` foi revertida ao
conteúdo inicial por cópia de leitura do HEAD, sem checkout ou mudança de histórico.
Após a restauração, `npm run typecheck` passou (exit 0). O ensaio final
`bash scripts/test-item-editor.sh` passou no build final: **2 passed, zero falhas,
zero skips**, 12,20 s. Inclui os últimos ajustes de conciliação/visibilidade.
`git diff --check` passou; `git diff --cached --stat` ficou vazio.

### Tentativas que falharam e correções

O primeiro typecheck detectou erros de adaptação de nomes e uma permissão não
aceita pelo tipo da guarda de páginas. Corrigidos sem enfraquecer autorização:
a guarda exige leitura de revisões e o BFF/API exigem leitura bibliográfica.

O primeiro ensaio de componente expirou ao navegar com dados locais pendentes;
o harness passou a abrir uma aba nova para cada cenário, sem aceitar
implicitamente o diálogo nativo. A repetição passou. Os processos daquela
execução foram identificados e encerrados individualmente.

O primeiro ensaio HTTP passou criação/edição/CAS e falhou na asserção final dos
links de retorno: a espera textual podia encontrar dados do streaming antes da
navegação terminar. Corrigido para aguardar pathname e elemento de navegação
real. A repetição passou com os dois links presentes. Essas tentativas não são
contadas como aprovações.

### Cobertura verificada

| Camada | Evidências |
| --- | --- |
| Payload/modelo UI | PATCH somente dos grupos alterados, clone de valores repetidos/idiomas, proibição de reatribuição/forja, null/[]/omissão, escolhas explícitas |
| BFF | Bearer server-side/no-store, cookie não encaminhado, ETags exatos, 400/401/403/404/409/412/422/428, CSRF/origem, metadados da mesma geração, cliente gerado |
| Navegação | Inversas filtradas por predicado/tipo; resolução de URI exata; Work → Instance → Item e retorno no navegador |
| Componente Chromium | pt/en, labels, IDs/aria-describedby, árvore acessível, Tab, foco em repetidores/erro, carregamento, 401/403/409/412/422, descarte confirmado, vínculo concorrente bloqueado |
| HTTP real | Next Server Actions → BFF → JWT assinado/FastAPI → PostgreSQL; pré-validação, criação, edição, 412/reconciliação, recarga, UUID/vínculo/revisão, nenhum token no HTML/logs |
| Autorização real | Catalogador escreve; leitor/revisor/publicador não escrevem; revisor/publicador leem; leitor sem revisão recebe 403; catalogador não aprova/publica; anônimo 401 e rascunho público 404 |
| PostgreSQL | UUID/URI/perfil/identificadores/heldBy/shelfMark/proveniência, extensões raiz/auxiliar, snapshots históricos isomórficos, revisão +2 após conflito, relações válidas/inválidas, rollback, pai inalterado |
| Adaptador | Bloqueio de perda de datatype direto/aninhado; campos não alterados conservam termos RDF; grafos comparados por isomorfismo |

Os runners PostgreSQL usam containers novos, tmpfs, portas loopback dinâmicas e
bancos próprios. Somente seus recursos são removidos. Não foi utilizado SQLite.
Os testes HTTP usam discovery/JWKS sintéticos e sessão cifrada de teste, não login
Keycloak real. A suíte padrão não comprova integração quando suas variáveis
opt-in estão ausentes; apenas execuções isoladas acima contam como evidência.

## Avaliação dos critérios de aceite

| Requisito | Classificação / evidência |
| --- | --- |
| Work → Instance → bf:Item com vínculos explícitos | Atendido; navegação real e relações PostgreSQL |
| Criação por APIs existentes | Atendido; POST v1 via BFF e navegador |
| Consulta e edição funcionais | Atendido; GET/PATCH/recarga real |
| UUID/URI/referências preservados | Atendido; testes BFF/HTTP/PostgreSQL |
| itemOf correto e persistente | Atendido; Instance conferida, PATCH UI não o altera |
| Estruturados e não editados preservados | Atendido dentro do escopo editável; omissão e rejeição conservadora quando o DTO não representa o grupo |
| Perfis/releases respeitados | Atendido; release 1.1 fixa na criação e persistida na edição |
| Pré-validação/diagnósticos conforme contratos | Atendido; pré-validação de criação e validação transacional ao editar |
| ETag/CAS e entrada local | Atendido; testes BFF, Chromium e concorrência PostgreSQL |
| Autorização no backend | Atendido; matriz com JWT assinado e testes existentes |
| UI pt/en | Atendido; dicionários simétricos e Chromium |
| Distinção Item/inventário na UI | Atendido; ajuda nas telas e seção contextual |
| Testes automatizados pertinentes | Atendido; unitários, BFF, componente, HTTP e persistência |
| PostgreSQL real isolado | Atendido; 170 casos na versão final e runner HTTP |
| Build/verificações | Atendido; gate final exit 0, incluindo build/typecheck |
| Documentação | Atendido; documento funcional, README, atualização da navegação Instance e este relatório novo |
| Sem inventário/circulação | Atendido; nenhum módulo/tabela/fluxo operacional introduzido |
| Sem histórico Git/alteração remota | Atendido; todas as alterações apenas no working tree main |
| Homologação humana/WCAG completa/Keycloak real | Não atendido nesta execução; não fazem parte da evidência técnica automatizada e permanecem pendentes |

## Pendências e limites

Aceite por catalogador, auditoria WCAG completa, Keycloak real e homologação
institucional/produção não executados. Não são inferidos de testes sintéticos.
A suíte HTTP geral de M04 não foi reexecutada; seus problemas históricos de shell
não são declarados resolvidos. A regressão específica de Instance é registrada
separadamente. Inversas não têm paginação no backend atual e podem custar uma
varredura; desempenho em grandes catálogos não foi homologado.

HeldBy, marcas de estante e proveniência semântica existentes são somente leitura;
itemOf não é reatribuído neste formulário. Campos de perfis 2.0 e interface completa
de comandos editoriais continuam fora desta entrega. O DTO Read não é exportação
integral de RDF. Alterações incompatíveis são bloqueadas, não convertidas em texto.

## Inventário de arquivos e situação final do Git

### Arquivos rastreados modificados (8)

| Arquivo | Alteração |
| --- | --- |
| [README.md](../../README.md) | Entrada documental da M07.03. |
| [apps/api/src/libris/modules/bibliographic/infrastructure/serialization/adapters.py](../../apps/api/src/libris/modules/bibliographic/infrastructure/serialization/adapters.py) | Bloqueio de perda de identidade/datatype/extensões em grupos de Item. |
| [apps/web/src/app/[locale]/admin/catalog/instances/[id]/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/[id]/page.tsx) | Itens vinculados e ação de criação contextual. |
| [apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx) | Instâncias vinculadas e leitura por permissões; ação de edição condicionada. |
| [apps/web/src/i18n/messages.ts](../../apps/web/src/i18n/messages.ts) | Traduções pt/en completas. |
| [apps/web/src/server/catalog/problems.ts](../../apps/web/src/server/catalog/problems.ts) | Diagnósticos de Item e correlação segura por campo. |
| [docs/architecture/instance-editor.md](instance-editor.md) | Atualiza a navegação contextual entregue pela M07.03. |
| [tests/api/test_dto_rdf_adapters.py](../../tests/api/test_dto_rdf_adapters.py) | Bloqueio de perda de identidade/datatype/extensões em grupos de Item. |

### Arquivos novos não rastreados (24)

| Arquivo | Alteração |
| --- | --- |
| [apps/web/src/app/[locale]/admin/catalog/items/[id]/edit/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/items/[id]/edit/page.tsx) | Rota administrativa de Item: listagem, criação, detalhe, edição ou carregamento. |
| [apps/web/src/app/[locale]/admin/catalog/items/[id]/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/items/[id]/page.tsx) | Rota administrativa de Item: listagem, criação, detalhe, edição ou carregamento. |
| [apps/web/src/app/[locale]/admin/catalog/items/loading.tsx](../../apps/web/src/app/[locale]/admin/catalog/items/loading.tsx) | Rota administrativa de Item: listagem, criação, detalhe, edição ou carregamento. |
| [apps/web/src/app/[locale]/admin/catalog/items/new/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/items/new/page.tsx) | Rota administrativa de Item: listagem, criação, detalhe, edição ou carregamento. |
| [apps/web/src/app/[locale]/admin/catalog/items/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/items/page.tsx) | Rota administrativa de Item: listagem, criação, detalhe, edição ou carregamento. |
| [apps/web/src/catalog/item-editor.ts](../../apps/web/src/catalog/item-editor.ts) | Rascunho, PATCH mínimo, parsing e conciliação. |
| [apps/web/src/components/catalog/ItemEditorFields.tsx](../../apps/web/src/components/catalog/ItemEditorFields.tsx) | Controles, formulário, descrição ou lista acessível de relações. |
| [apps/web/src/components/catalog/ItemEditorForm.tsx](../../apps/web/src/components/catalog/ItemEditorForm.tsx) | Controles, formulário, descrição ou lista acessível de relações. |
| [apps/web/src/components/catalog/ItemViews.tsx](../../apps/web/src/components/catalog/ItemViews.tsx) | Controles, formulário, descrição ou lista acessível de relações. |
| [apps/web/src/components/catalog/RelatedRecords.tsx](../../apps/web/src/components/catalog/RelatedRecords.tsx) | Controles, formulário, descrição ou lista acessível de relações. |
| [apps/web/src/server/catalog/item-actions.ts](../../apps/web/src/server/catalog/item-actions.ts) | Cliente gerado, sessão/permissões, ações, ETags ou resolução de relações. |
| [apps/web/src/server/catalog/item-page.tsx](../../apps/web/src/server/catalog/item-page.tsx) | Cliente gerado, sessão/permissões, ações, ETags ou resolução de relações. |
| [apps/web/src/server/catalog/items.ts](../../apps/web/src/server/catalog/items.ts) | Cliente gerado, sessão/permissões, ações, ETags ou resolução de relações. |
| [apps/web/src/server/catalog/related.ts](../../apps/web/src/server/catalog/related.ts) | Cliente gerado, sessão/permissões, ações, ETags ou resolução de relações. |
| [apps/web/tests/fixtures/item-editor-browser.tsx](../../apps/web/tests/fixtures/item-editor-browser.tsx) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [apps/web/tests/item-bff.test.ts](../../apps/web/tests/item-bff.test.ts) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [apps/web/tests/item-editor-fixture.ts](../../apps/web/tests/item-editor-fixture.ts) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [apps/web/tests/item-editor-http.e2e.ts](../../apps/web/tests/item-editor-http.e2e.ts) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [apps/web/tests/item-editor.e2e.ts](../../apps/web/tests/item-editor.e2e.ts) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [apps/web/tests/item-editor.test.ts](../../apps/web/tests/item-editor.test.ts) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |
| [docs/architecture/item-editor-m0703-verification-2026-10-18.md](item-editor-m0703-verification-2026-10-18.md) | Documentação funcional ou evidência datada desta execução. |
| [docs/architecture/item-editor.md](item-editor.md) | Documentação funcional ou evidência datada desta execução. |
| [scripts/test-item-editor.sh](../../scripts/test-item-editor.sh) | Runner de navegador/API com cluster PostgreSQL novo em tmpfs. |
| [tests/integration/test_item_editor_postgres.py](../../tests/integration/test_item_editor_postgres.py) | Fixture ou regressão automatizada de Item (BFF, UI, navegador e persistência). |

```text
$ git branch --show-current
main
```

```text
$ git status --short
 M README.md
 M apps/api/src/libris/modules/bibliographic/infrastructure/serialization/adapters.py
 M apps/web/src/app/[locale]/admin/catalog/instances/[id]/page.tsx
 M apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx
 M apps/web/src/i18n/messages.ts
 M apps/web/src/server/catalog/problems.ts
 M docs/architecture/instance-editor.md
 M tests/api/test_dto_rdf_adapters.py
?? apps/web/src/app/[locale]/admin/catalog/items/
?? apps/web/src/catalog/item-editor.ts
?? apps/web/src/components/catalog/ItemEditorFields.tsx
?? apps/web/src/components/catalog/ItemEditorForm.tsx
?? apps/web/src/components/catalog/ItemViews.tsx
?? apps/web/src/components/catalog/RelatedRecords.tsx
?? apps/web/src/server/catalog/item-actions.ts
?? apps/web/src/server/catalog/item-page.tsx
?? apps/web/src/server/catalog/items.ts
?? apps/web/src/server/catalog/related.ts
?? apps/web/tests/fixtures/item-editor-browser.tsx
?? apps/web/tests/item-bff.test.ts
?? apps/web/tests/item-editor-fixture.ts
?? apps/web/tests/item-editor-http.e2e.ts
?? apps/web/tests/item-editor.e2e.ts
?? apps/web/tests/item-editor.test.ts
?? docs/architecture/item-editor-m0703-verification-2026-10-18.md
?? docs/architecture/item-editor.md
?? scripts/test-item-editor.sh
?? tests/integration/test_item_editor_postgres.py
```

```text
$ git diff --stat
 README.md                                          |  6 ++++
 .../infrastructure/serialization/adapters.py       | 25 +++++++++++++
 .../[locale]/admin/catalog/instances/[id]/page.tsx |  5 +++
 .../app/[locale]/admin/catalog/works/[id]/page.tsx | 11 +++---
 apps/web/src/i18n/messages.ts                      | 42 ++++++++++++++++++++++
 apps/web/src/server/catalog/problems.ts            | 21 +++++++++++
 docs/architecture/instance-editor.md               |  4 ++-
 tests/api/test_dto_rdf_adapters.py                 | 23 ++++++++++++
 8 files changed, 132 insertions(+), 5 deletions(-)
```

O diff stat não inclui os 24 arquivos novos, enumerados separadamente acima.
A inspeção abrangeu também os arquivos não rastreados, não apenas o diff de arquivos
rastreados. Nenhuma branch, commit, PR, operação de staging, alteração de histórico
ou publicação/alteração remota foi realizada. Tudo permanece no working tree da
main local. Nenhum arquivo com alteração preexistente foi descartado; o working
tree estava limpo ao iniciar.
