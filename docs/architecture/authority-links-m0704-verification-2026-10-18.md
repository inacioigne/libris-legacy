# M07.04 — Evidências de consulta e vínculos de autoridades — 2026-10-18

Entrega local da [issue #64](https://github.com/inacioigne/libris/issues/64).
Este relatório registra execução técnica; não declara homologação institucional,
aceite biblioteconômico ou integração Keycloak real nesta entrega.

## A. Resumo e diagnóstico

Estado inicial comprovado: branch `main`, `git status --short` vazio e
`git diff --stat` vazio. Não havia alterações locais preexistentes. A issue foi
consultada somente em leitura, aberta na milestone 7. Nenhum recurso remoto foi
modificado. Instruções, contratos, código e evidências M05.04/M07.01–03 foram
consultados. A ausência de relatório autônomo M06.07 já registrada nas entregas
anteriores não foi tratada como homologação.

Implementados seletor compartilhado Agent/Subject e painel de vínculos nos detalhes
administrativos Work, Instance e Item, em português e inglês. Consulta paginada,
filtros exatos UUID/URI e filtro de rótulos da página permitem selecionar registros
existentes sem conhecer identificadores. Rótulo, UUID e URI distinguem homônimos.
Referências estruturadas são transmitidas integralmente.

Work oferece contribuições com múltiplos papéis e assuntos; Instance permite
publicadores em atividades; Item restringe a seleção de detentor a Organization.
Remoção requer confirmação. O painel utiliza add/remove relacionais, autorização
efetiva, sessão BFF, proteção de origem, ETag próprio e comparação explícita em 412.
Sucesso atualiza relações e os demais dados da página; falhas mantêm seleção local.

Não foi necessária mudança no backend: os serviços existentes já resolvem
referências, validam a matriz e preservam o snapshot integral na transação.
Os testes novos demonstram a independência das revisões. Contratos v1, OpenAPI,
perfis, SHACL, migrações, dependências e arquivos gerados permanecem intactos.

## B. Arquivos criados ou alterados

| Arquivo | Finalidade |
| --- | --- |
| [README](../../README.md) | Entrada para a funcionalidade e evidências |
| [authorities.ts — catálogo](../../apps/web/src/catalog/authorities.ts) | Tipos derivados do cliente, identidade, igualdade semântica e construção de vínculos permitidos |
| [AuthoritySelector](../../apps/web/src/components/catalog/AuthoritySelector.tsx) | Consulta, paginação, filtro local, seleção acessível e estados |
| [AuthorityRelations](../../apps/web/src/components/catalog/AuthorityRelations.tsx) | Apresentação, papéis, add/remove, confirmação e comparação 412 |
| [ConnectedAuthorityRelations](../../apps/web/src/components/catalog/ConnectedAuthorityRelations.tsx) | Atualização Next da página após sucesso |
| [authorities.ts — BFF](../../apps/web/src/server/catalog/authorities.ts) | Cliente autenticado de autoridades e relações; rótulos locais |
| [authority-actions](../../apps/web/src/server/catalog/authority-actions.ts) | Server Actions, proteção de origem e diagnósticos |
| [authority-panel](../../apps/web/src/server/catalog/authority-panel.tsx) | Carregamento server-side e apresentação por permissões |
| [catalog/index](../../apps/web/src/server/catalog/index.ts) | Requisito conjuntivo authority:read + metadata_revision:read |
| [Detalhe Work](../../apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx) | Integração do painel |
| [Detalhe Instance](../../apps/web/src/app/[locale]/admin/catalog/instances/[id]/page.tsx) | Integração de publicadores |
| [Detalhe Item](../../apps/web/src/app/[locale]/admin/catalog/items/[id]/page.tsx) | Integração de detentores bibliográficos |
| [messages](../../apps/web/src/i18n/messages.ts) | Textos pt/en |
| [authority-bff.test](../../apps/web/tests/authority-bff.test.ts) | 12 testes de cliente, filtros, erros, permissões, identidades e papéis |
| [authority-fixture](../../apps/web/tests/authority-fixture.ts) | Dados sintéticos compartilhados |
| [authority-browser](../../apps/web/tests/fixtures/authority-browser.tsx) | Harness React de componentes com ações simuladas |
| [authority-editor.e2e](../../apps/web/tests/authority-editor.e2e.ts) | Chromium: teclado, acessibilidade, pt/en, estados, vínculos e 412 |
| [authority-editor-http.e2e](../../apps/web/tests/authority-editor-http.e2e.ts) | Navegador → Next/BFF → JWT/FastAPI → PostgreSQL reais |
| [test_authority_links_m0704_postgres](../../tests/integration/test_authority_links_m0704_postgres.py) | Dois casos parametrizados: revisões/snapshots, integridade, CAS e permissões |
| [test-authority-editor.sh](../../scripts/test-authority-editor.sh) | Runner de navegador com PostgreSQL novo em tmpfs |
| [authority-links](authority-links.md) | Fluxos, contratos, autorização e limites |
| Este relatório | Evidência nova; relatórios históricos preservados |

## C. Endpoints e permissões efetivas

Reutilizados GET `/api/v1/catalog/agents`, GET `/subjects`, GET `/agents/{id}`,
GET `/subjects/{id}` e GET/PATCH `/{collection}/{id}/relationships`, sob o mesmo
prefixo catalográfico. Nenhum endpoint duplicado ou novo contrato foi introduzido.
As relações são consultadas sem inversas e escritas com seu próprio ETag.

| Operação | Política existente verificada |
| --- | --- |
| Consulta editorial de autoridade | authority:read + metadata_revision:read |
| Consulta editorial bibliográfica/relações | bibliographic:read + metadata_revision:read |
| Vínculo/desvínculo bibliográfico | bibliographic:update e precondições do proprietário |
| Criação/edição da autoridade pela API | authority:manage |

Cataloger consulta e vincula, mas recebe 403 ao editar a autoridade. O papel
authority-manager isolado gerencia autoridades pela API, mas não tem
metadata_revision:read nem bibliographic:update: consulta editorial e vínculos
são negados. Usuário sem concessões recebe 403; ausência de autenticação, 401.
Essa particularidade não foi corrigida por ampliar permissões unilateralmente.
O painel não acrescenta CRUD visual de autoridades.

## D. Matriz de aceite

| Critério | Resultado e evidência |
| --- | --- |
| Consultar e selecionar Agent/Subject | Atendido e testado: BFF, Chromium e fluxo HTTP real |
| Rótulos e URIs, inclusive homônimos | Atendido e testado: dois Agents homônimos no componente |
| Vincular/desvincular agentes e assuntos | Atendido e testado: Work no navegador e PostgreSQL |
| Publicadores/Organization nas origens compatíveis | Atendido e testado: Instance/Item no fluxo HTTP real |
| Preservar papéis e demais dados | Atendido e testado: múltiplos papéis, nota concorrente, data/lugar de publicação |
| Distinguir local/externa/não reconciliada | Atendido na apresentação e testado; persistência não reconciliada permanece recusada pela política existente |
| Revisões independentes de autoridades | Atendido e testado: ETags, revisão, grafo isomórfico, histórico, estado e linhas de snapshots |
| Permissões cataloger/authority-manager, 401/403 | Atendido e testado: backend real e BFF |
| Referências inválidas/inexistentes e duplicadas | Atendido e testado: rejeição sem alteração parcial |
| ETag, If-Match ausente/obsoleto, 412 | Atendido e testado: API, PostgreSQL, componente e fluxo completo |
| Preservar seleção e conciliar explicitamente | Atendido e testado: 412 não repete escrita nem descarta seleção |
| Diagnósticos 400/401/403/404/409/412/422 | Atendido e testado no BFF; mensagens e falhas exercitadas na UI |
| pt/en, labels, teclado e foco | Atendido nos testes Chromium de componentes; não equivale a auditoria integral WCAG/leitor de tela |
| Contratos v1, snapshots e artefatos históricos | Atendido: checks sem drift, suítes API/PostgreSQL aprovadas e diff sem alteração desses artefatos |
| Git exclusivamente local, sem commit | Atendido: main, somente working tree, sem staging ou escrita remota |
| Keycloak real e aceite por catalogador | Pendentes; não executados nem declarados como homologados |

## E. Comandos e resultados reais

Node 24.16.0 e Python 3.13.15. O sandbox não encontrava Node/uv/Python do venv;
os comandos foram executados no host com aprovação, usando instalações existentes.
Não houve instalação de dependências. Nenhum banco de trabalho foi utilizado.

| Comando | Resultado |
| --- | --- |
| `bash scripts/check.sh` — tentativa no sandbox | Bloqueado: uv não encontrado; repetido no host |
| `bash scripts/check.sh` — host | Exit 0: contratos/OpenAPI, API, Ruff, mypy, frontend e Compose |
| `uv run pytest` — pelo gate | 2.088 passed, zero falhas/skips, 513 warnings, 232,93 s |
| `uv run ruff check . ../../tests` | Primeira execução apontou linhas longas no teste novo; formatadas. Gate posterior aprovado, incluindo os scripts adicionais |
| `uv run mypy src` — pelo gate | Aprovado em 106 arquivos |
| `node --conditions=react-server --import tsx --test tests/authority-bff.test.ts` | 12 passed, zero falhas/skips |
| `npm test` — gate e repetição final | 236 passed, zero falhas/skips; cliente OpenAPI sem drift |
| `npm run build` — gate e repetição final | Aprovado, build de produção Next |
| `npm run typecheck` — gate e repetição final | Aprovado; erros intermediários de tipos corrigidos |
| `bash scripts/test-postgres.sh -k 'authority_revision_is_independent'` | 2 passed, 172 deselected, zero skips; 7,62 s |
| `bash scripts/test-postgres.sh` | 172 passed, 2 deselected (Keycloak), zero skips; 3.659 warnings, 173,48 s |
| `TEST_WEB_BROWSER_ENABLED=1 node --conditions=react-server --import tsx --test tests/authority-editor.e2e.ts` | Execução corrigida: 1 passed, zero falhas/skips |
| `bash scripts/test-authority-editor.sh` — final | Exit 0: 2 passed, zero falhas/skips, 14,71 s |
| `git diff --check` | Sem erros |

O runner PostgreSQL não herda URLs de trabalho: cria container exclusivo, dados
em tmpfs, porta loopback dinâmica e bancos separados, aplica migrações existentes
explicitamente e remove somente o container criado. O runner de navegador segue
o mesmo isolamento e cria seu próprio banco. Não foi usado SQLite nem removido
volume persistente. Os dois testes Keycloak são deselected, não aprovados.

O fluxo HTTP usa sessão PostgreSQL real, cliente gerado, server actions reais e
JWT assinado validado pelo FastAPI; apenas discovery/JWKS do provedor são locais
ao teste. A prova é diferente de um login Keycloak real. O teste de componentes
usa React/Chromium reais com ações simuladas e não substitui o teste HTTP.

Falhas intermediárias de Chromium: evento Enter do harness sem texto, asserção
retornando DOM em vez de booleano e importação do adaptador Next no bundle de
componentes. Foram corrigidas; o adaptador de atualização da página ficou separado
do componente. A primeira execução conjunta teve HTTP aprovado e componente
reprovado; a execução final aprovou ambos. Nenhuma falha foi convertida em skip.
Warnings observados pertencem a depreciações Starlette/HTTPX e RDFLib.

Logs temporários da execução: `/tmp/libris-m0704-check.log`,
`/tmp/libris-m0704-web-final.log`, `/tmp/libris-m0704-postgres.log` e
`/tmp/libris-m0704-browser-final.log`. Os resultados acima não dependem de manter
esses arquivos. O arquivo next-env gerado pelo build foi restaurado ao conteúdo
inicial, sem alteração entregue.

## F. Limites e pendências concretas

- A API não oferece busca textual: filtro por nome cobre somente a página atual;
  paginação e filtros exatos permitem localizar os demais registros.
- Referências externas são apresentadas sem resolução remota. Não reconciliadas
  podem ser apresentadas pelo componente, mas não persistidas pelo contrato atual.
- Atividades de publicação sem agentes não aparecem na coleção relacional; seus
  demais dados continuam no editor Instance. Não há nova engine de formulários.
- Edição/criação dos dados da autoridade continua na API existente, fora do painel
  de vínculos. A distinção de permissões foi testada, sem ampliar concessões.
- Remover contribuições anotadas continua sujeito ao bloqueio seguro do backend.
- Aceite biblioteconômico, leitor de tela, Keycloak real e homologação institucional
  permanecem pendentes de execução própria.

## G. Estado final do Git

Branch utilizada: **main**. Novas branches, commits, PRs, push e staging: **nenhum**.
Nenhum checkout, merge, rebase, cherry-pick, reset, clean ou alteração de issue foi
executado. Todas as mudanças permanecem no working tree local para revisão.

`git diff --stat` dos arquivos rastreados: **6 files changed, 90 insertions(+),
2 deletions(-)**. Os 16 arquivos novos, incluindo este relatório, aparecem como
`??` em `git status --short` e não são incluídos no diff --stat convencional.
Total entregue: **22 arquivos**, seis modificados e dezesseis novos.

O inventário da seção B corresponde ao status final. `git diff --check` não
produziu saída. O estado inicial era limpo e nenhum arquivo preexistente foi
descartado. Relatórios anteriores permaneceram intactos.
