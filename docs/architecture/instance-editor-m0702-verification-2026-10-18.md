# M07.02 — Verificação técnica do editor de Instance — 2026-10-18

Entrega local da [issue #62](https://github.com/inacioigne/libris/issues/62).
Este documento registra verificação técnica, **não homologação institucional**.
Nenhuma issue foi comentada, alterada ou fechada.

## Estado inicial e levantamento

`git branch --show-current` retornou `main`; `git status --short` e
`git diff --stat` estavam vazios. Não havia alterações preexistentes a conciliar.
As issues #62, #61 e #59 foram consultadas somente em leitura. #62 estava aberta
na milestone 7; #61 e #59 estavam fechadas, sem usar esse estado como prova de
implementação ou homologação.

Foram examinados as instruções do repositório, README, arquitetura/domínios,
contratos v1, plano M07, perfis, autorização, ciclo editorial e ADR 0040; os
DTOs Python, OpenAPI/cliente gerado, fixtures, adaptadores RDF e serviços
transacionais constituíram a fonte operacional. A M07.01 já oferecia PATCH
por grupo e comparação de conflitos. As evidências históricas M05/M06/M07.01
foram preservadas; a ausência de um relatório dedicado da M06.07 já estava
registrada na M07.01 e não foi convertida em aprovação automática.

Conclusões utilizadas:

- POST/PATCH/GET/listagem de Instance já existem; não foi necessário criar API.
- `instance_of` pertence ao DTO da Instance. Referência local é resolvida e
  validada na mesma transação da escrita. Work não recebe revisão em cascata.
- O POST fixa a release institucional 1.1; perfis 2.0 não são escrita v1.
- Título de Instance é opcional. Datas possuem datatype; textos possuem idioma.
- Listagem não filtra por Work nem contém estado/revisão/proveniência nos itens.
- PATCH preserva o grafo completo por omissão e recusa perda de anotações em
  auxiliares. Foi identificada e corrigida a lacuna para auxiliar exclusivo
  com URI própria, sem mudar schema, migração ou contrato HTTP.

## Implementação e arquivos

Fluxos e limites detalhados em [instance-editor.md](instance-editor.md).

| Arquivo criado/modificado | Finalidade |
| --- | --- |
| [README.md](../../README.md) | Apontar para o editor e esta evidência |
| [instance-editor.md](instance-editor.md) | Fluxos, campos, contratos, segurança e procedimento local |
| Este relatório novo | Evidência datada; nenhum relatório histórico substituído |
| [instances/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/page.tsx) | Lista, filtros, paginação e ação autorizada |
| [instances/loading.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/loading.tsx) | Estado acessível de carregamento pt/en |
| [instances/new/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/new/page.tsx) | Criação e Work pré-selecionada consultada no servidor |
| [instances/[id]/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/[id]/page.tsx) | Detalhe persistido, Work e geração editorial |
| [instances/[id]/edit/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/instances/[id]/edit/page.tsx) | Carregamento seguro do editor e ETag |
| [works/[id]/page.tsx](../../apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx) | Work → nova Instance e listagem geral |
| [AppSidebar.tsx](../../apps/web/src/layout/admin/AppSidebar.tsx) | Entrada de Instances no shell |
| [messages.ts](../../apps/web/src/i18n/messages.ts) | Textos pt/en |
| [instance-editor.ts](../../apps/web/src/catalog/instance-editor.ts) | Cópia estrutural, PATCH mínimo, fronteira de payload e conciliação |
| [InstanceEditorForm.tsx](../../apps/web/src/components/catalog/InstanceEditorForm.tsx) | Estado local, salvamento, erros, pré-validação e comparação |
| [InstanceEditorFields.tsx](../../apps/web/src/components/catalog/InstanceEditorFields.tsx) | Controles dos oito grupos v1 |
| [WorkSelector.tsx](../../apps/web/src/components/catalog/WorkSelector.tsx) | Consulta paginada e seleção explícita de Work |
| [InstanceViews.tsx](../../apps/web/src/components/catalog/InstanceViews.tsx) | Lista e descrição estruturada somente leitura |
| [WorkEditorFields.tsx](../../apps/web/src/components/catalog/WorkEditorFields.tsx) | Extração de títulos/notas/textos/referências reutilizáveis |
| [catalog/index.ts](../../apps/web/src/server/catalog/index.ts) | Reutilizar executor autenticado existente |
| [instances.ts](../../apps/web/src/server/catalog/instances.ts) | Cliente tipado de listagem/leitura/escrita/validação e metadados |
| [instance-actions.ts](../../apps/web/src/server/catalog/instance-actions.ts) | Server Actions, origem, seleção, validação e payloads limitados |
| [instance-page.tsx](../../apps/web/src/server/catalog/instance-page.tsx) | Guardas administrativas e erros localizados |
| [problems.ts](../../apps/web/src/server/catalog/problems.ts) | Diagnósticos de Instance, códigos/caminhos/request ID |
| [adapters.py](../../apps/api/src/libris/modules/bibliographic/infrastructure/serialization/adapters.py) | Recusar perda de URI de auxiliar não representável |
| [test_dto_rdf_adapters.py](../../tests/api/test_dto_rdf_adapters.py) | Regressão de auxiliares nomeados em Work e Instance |
| [test_instance_editor_postgres.py](../../tests/integration/test_instance_editor_postgres.py) | Persistência, referência inválida, CAS, extensões e revisão |
| [instance-editor.test.ts](../../apps/web/tests/instance-editor.test.ts) | Mapper, PATCH, conciliação, i18n e diagnósticos |
| [instance-bff.test.ts](../../apps/web/tests/instance-bff.test.ts) | Sessão, permissões, cabeçalhos, erros e metadados coerentes |
| [instance-editor-fixture.ts](../../apps/web/tests/instance-editor-fixture.ts) | Registro sintético multilíngue e estruturado |
| [fixtures/instance-editor-browser.tsx](../../apps/web/tests/fixtures/instance-editor-browser.tsx) | Harness de componente, sem rota de produção |
| [instance-editor.e2e.ts](../../apps/web/tests/instance-editor.e2e.ts) | Chromium/React: formulário, lista, detalhe, teclado e conflitos |
| [instance-editor-http.e2e.ts](../../apps/web/tests/instance-editor-http.e2e.ts) | Navegador + Server Actions + API + PostgreSQL reais |
| [test-instance-editor.sh](../../scripts/test-instance-editor.sh) | Runner dedicado com cluster novo em tmpfs |

Nenhum arquivo removido. Sem dependências novas, alterações de locks, perfis,
SHACL, migrações ou edição manual de arquivos gerados. O arquivo Next gerado
automaticamente pelo build é devolvido ao conteúdo inicial quando necessário.

## Fluxos e integridade comprovados

O catalogador seleciona uma Work real, confirma título/UUID/URI e salva uma
Instance por uma única operação. O detalhe permite voltar à Work; o contexto
da Work oferece criação com seleção prévia. Não há heurística por título/ISBN.
O teste HTTP verifica que o ETag da Work não muda após criar sua Instance.

O editor carrega valores aninhados e envia apenas grupos alterados. A suíte
PostgreSQL verifica UUID/URI/perfil, vínculo, datas/identificadores/extensão,
revisão histórica isomórfica, extensão RDF na raiz e anotação em auxiliar.
Tentativa de apagar auxiliar anotado é recusada com 400 (`request.malformed`) e rollback; testes
unitários também recusam apagar URI própria de auxiliar de Work/Instance.
Ausência, `null` e `[]` são testados separadamente. A UI usa `[]` para limpar.

Concorrência: primeiro escritor persiste nota, segundo recebe 412; o formulário
mantém o título local e ETag antigo. Consulta/comparação exige escolha explícita;
novo envio usa o ETag corrente e preserva a nota concorrente. Recarregar confirma
os dois valores. Revisões históricas não são substituídas e falha não cria revisão.

## Segurança e níveis de evidência

- O BFF conserva cookie/sessão server-side, Bearer apenas no servidor, cliente
  OpenAPI e proteção de origem existente. FastAPI valida OIDC/RBAC efetivamente.
- Testes BFF verificam anônimo, permissão insuficiente, origem ausente/estrangeira,
  host adulterado, fetch cross-site, If-Match e resposta ETag; 400/401/403/404/
  409/412/422 e diagnósticos são preservados.
- O teste HTTP usa catalogador com JWT assinado; publicação é negada com 403,
  leitura editorial anônima retorna 401 e draft público retorna 404. HTML e logs
  não contêm o token. Não há transição editorial implícita no salvamento.
- Componente: React e Chromium reais; ações simuladas. HTTP: Next/FastAPI e
  PostgreSQL reais, sessão cifrada de teste e discovery/JWKS sintéticos. Não é
  autenticação/login Keycloak real nem homologação humana.

## Execuções

Ferramentas: Node 24.16.0 via PATH local, Python 3.13.15/uv do projeto. O sandbox
inicial não expunha Node/uv; comandos de teste foram autorizados no host. Não
foram instaladas dependências ou alteradas configurações locais de segredo.

| Comando | Resultado observado |
| --- | --- |
| `bash scripts/check.sh` — primeira execução completa | Aprovado: 2.084 API, Ruff, mypy (106 arquivos), 205 web, build/typecheck e Compose |
| `bash scripts/test-postgres.sh` | 169 passed, 2 deselected (Keycloak), zero skips; 140,79 s |
| `bash scripts/test-instance-editor.sh` | 2 passed, zero falhas/skips; componente e HTTP real, 7,07 s |
| `python scripts/verify-m04-integration.py --isolated --browser` | API 169 passed/2 deselected; web-store 1 passed; HTTP 4 passed/2 failed/zero skips |
| `uv run pytest ../../tests/api/test_dto_rdf_adapters.py` — primeira correção de URI | 44 passed; após isso a regressão foi parametrizada para Work e Instance |
| `uv run ruff check . ../../tests` — execução intermediária | Reprovado por uma linha longa no teste novo; corrigida |
| `uv run mypy src` | Aprovado; 106 arquivos |

Uma execução inicial de `npm test` reprovou duas asserções novas por comparar a
ordem das chaves dos dicionários pt/en; a comparação foi corrigida para conjuntos
ordenados. O gate seguinte passou com 205 testes. Typechecks intermediários
identificaram tipos/fixtures novos incompatíveis com o schema; foram corrigidos
antes do build aprovado. Essas tentativas não foram contabilizadas como sucesso.

### Verificação final após o reforço de URI

- `bash scripts/check.sh`: **aprovado**, exit 0. API: **2.086 passed**,
  513 warnings de depreciação, 139,46 s. Ruff aprovado; mypy aprovado em 106
  arquivos. `npm test`: **205 passed, zero falhas/skips**; OpenAPI sem drift.
  `npm run build`, `npm run typecheck` e Compose também aprovados.
- `bash scripts/test-postgres.sh -k instance_editor`: **1 passed, 170 deselected,
  zero skips**, 5,04 s, em outro cluster novo. Confirma o caso reforçado com
  extensão na raiz/auxiliar, rejeição destrutiva, rollback, referências inválidas,
  troca explícita de Work, datas/idiomas e CAS. A seleção não equivale a executar
  novamente toda a suíte; as 169 aprovações integrais acima são execuções anteriores.
- Uma tentativa intermediária desse teste esperava 422 para substituição de
  auxiliar anotado. O mapeador existente de `RdfDocumentError` retorna **400 /
  request.malformed**. Corrigiu-se a expectativa, sem alterar esse contrato nem
  retirar as asserções de revisão/grafo preservados; a nova execução passou.
- Os testes unitários de URI nomeada cobrem Work e Instance e integram os
  2.086 testes finais. Os ensaios Chromium/HTTP acima passaram antes desse
  reforço do adaptador; não são apresentados como homologação Keycloak.

As execuções de integração não usaram SQLite ou bases preexistentes. Os runners
removeram somente seus próprios containers. Nenhum skip foi contado como prova
positiva de integração.

### Falhas preexistentes da regressão HTTP geral

O harness ampliado reproduziu exatamente as duas falhas já documentadas em
[M07.01](work-editor-m0701-verification-2026-10-18.md):

- `admin-api.e2e.ts:112`: esperava “Acesso não autorizado”.
- `http.e2e.ts:161`: esperava “Autenticação necessária”.

Ambas se referem à página administrativa inicial, que já não executava a guarda
esperada. Essa página e essas asserções não foram alteradas. Os quatro testes
dos editores Work/Instance passaram no mesmo harness. Não se declara a suíte
HTTP geral aprovada. O container pertencente ao harness foi removido ao final.

Logs privados ficaram no diretório temporário
`/tmp/vscode-agent-codex-sandbox-mcUC6U/libris-m0409-7f000c435a0c4001-6gxw3q17`;
não foram copiados para o repositório nem apresentados como artefatos públicos.

## Critérios de aceite e limites

| Critério solicitado | Resultado / evidência |
| --- | --- |
| Listagem real | Implementado; BFF tipado, filtros/cursor; teste BFF e renderização Chromium |
| Detalhe persistido | Implementado; teste HTTP após criação e recarga |
| Criação autorizada | Executada por catalogador no teste HTTP real |
| Campos DTO v1 | Oito grupos presentes; testes estruturais e controles Chromium |
| Seleção de Work existente | Executada no navegador com consulta BFF real |
| `instanceOf` persistido | Conferido na API e PostgreSQL após recarga |
| Sem duplicação de Work | Único POST de Instance; ETag da Work inalterado no ensaio real |
| Edição de Instance | PATCH real e recarga no navegador |
| Dados/vínculo após recarga | Asserções HTTP e PostgreSQL |
| UUID/URI/revisões | PostgreSQL: identidade/perfil/histórico e avanço esperado |
| Complexos/extensões preservados | Mapper, PostgreSQL, rejeição de auxiliar anotado; guarda de URI |
| ETag/If-Match | Testes BFF, PostgreSQL e navegador |
| Conflito mantém entrada | Chromium e fluxo HTTP: 412, comparação e envio explícito |
| Diagnósticos/status | Testes 400/401/403/404/409/412/422, campo/código/path/request ID |
| OIDC/BFF/CSRF/RBAC | Sessão de teste real, JWT assinado, guarda API; origem testada no BFF |
| Sem publicação automática | draft público 404; catalogador sem publish 403 |
| Português/inglês | Dicionários completos e duas renderizações Chromium |
| Testes automatizados | Unitários, BFF, componente, HTTP e PostgreSQL executados |
| PostgreSQL isolado | Runner descartável; nenhum banco de trabalho usado |
| Comandos/limitações documentados | Tabela de execução e falhas gerais acima |
| Nenhuma branch criada | Trabalho iniciado e mantido na main local |
| Nenhum commit | Nenhuma operação de commit executada |
| Nenhum PR | Nenhuma operação de criação/publicação executada |
| Alterações na main local | Inspeção Git inicial/final; sem staging/publicação |
| Aceite humano / Keycloak real | Não executados; não inferidos de testes técnicos |

Limitações reais: sem filtro de Instances por Work, full-text, campos de perfis
2.0, reconciliação remota, editor RDF universal ou UI editorial completa. Dados
fora da projeção são preservados ou sua substituição é bloqueada; não se promete
round-trip de metadados operacionais que o próprio RDF v1 não conserva. A
descrição OpenAPI de `InstanceUpdate.instance_of` tem a limitação registrada no
documento funcional; referências locais são aceitas e testadas no backend real.
Auditoria WCAG integral, avaliação de catalogador e homologação institucional
permanecem decisões dos mantenedores. As duas falhas gerais do shell precisam
de tratamento em seu próprio escopo.


## Estado final do Git

```text
$ git branch --show-current
main
```

```text
$ git status --short
 M README.md
 M apps/api/src/libris/modules/bibliographic/infrastructure/serialization/adapters.py
 M apps/web/src/app/[locale]/admin/catalog/works/[id]/page.tsx
 M apps/web/src/components/catalog/WorkEditorFields.tsx
 M apps/web/src/i18n/messages.ts
 M apps/web/src/layout/admin/AppSidebar.tsx
 M apps/web/src/server/catalog/index.ts
 M apps/web/src/server/catalog/problems.ts
 M tests/api/test_dto_rdf_adapters.py
?? apps/web/src/app/[locale]/admin/catalog/instances/
?? apps/web/src/catalog/instance-editor.ts
?? apps/web/src/components/catalog/InstanceEditorFields.tsx
?? apps/web/src/components/catalog/InstanceEditorForm.tsx
?? apps/web/src/components/catalog/InstanceViews.tsx
?? apps/web/src/components/catalog/WorkSelector.tsx
?? apps/web/src/server/catalog/instance-actions.ts
?? apps/web/src/server/catalog/instance-page.tsx
?? apps/web/src/server/catalog/instances.ts
?? apps/web/tests/fixtures/instance-editor-browser.tsx
?? apps/web/tests/instance-bff.test.ts
?? apps/web/tests/instance-editor-fixture.ts
?? apps/web/tests/instance-editor-http.e2e.ts
?? apps/web/tests/instance-editor.e2e.ts
?? apps/web/tests/instance-editor.test.ts
?? docs/architecture/instance-editor-m0702-verification-2026-10-18.md
?? docs/architecture/instance-editor.md
?? scripts/test-instance-editor.sh
?? tests/integration/test_instance_editor_postgres.py
```

```text
$ git diff --stat
 README.md                                          |  7 ++
 .../infrastructure/serialization/adapters.py       |  4 +
 .../app/[locale]/admin/catalog/works/[id]/page.tsx |  3 +
 .../src/components/catalog/WorkEditorFields.tsx    | 44 ++++++----
 apps/web/src/i18n/messages.ts                      | 98 ++++++++++++++++++++++
 apps/web/src/layout/admin/AppSidebar.tsx           |  1 +
 apps/web/src/server/catalog/index.ts               | 10 +--
 apps/web/src/server/catalog/problems.ts            | 29 +++++++
 tests/api/test_dto_rdf_adapters.py                 | 37 ++++++++
 9 files changed, 211 insertions(+), 22 deletions(-)
```

O diff stat não inclui arquivos novos não rastreados; o inventário acima os inclui.
`git diff --cached --stat` vazio; `git diff --check` aprovado. O typecheck foi
repetido após restaurar o arquivo Next gerado incidentalmente e passou.
Todas as alterações permanecem não staged na main local. Nenhuma branch, commit,
PR, sincronização, publicação ou alteração remota de issue foi realizada.
