# M07.01 — Editor Work: integridade, comparação e verificação — 2026-10-18

## Objetivo e estado inicial

Implementação local da [issue #61](https://github.com/inacioigne/libris/issues/61).
As issues [#58](https://github.com/inacioigne/libris/issues/58) e
[#59](https://github.com/inacioigne/libris/issues/59) foram consultadas em leitura:
ambas fechadas, mas isso não comprova todos os seus critérios. #61 estava aberta,
associada à milestone 7. Nenhuma issue foi alterada.

Antes de editar: `git branch --show-current` retornou `main`;
`git status --short` retornou vazio. Todas as mudanças desta execução ficam locais,
sem staging, commit, branch nova, pull, push, merge, rebase ou PR.

A auditoria seguiu DTOs Python → adaptadores RDF → serviços/CAS → OpenAPI gerado →
BFF → ações → formulário e rotas localizadas. Referências: [contrato v1](catalogographic-contract.md),
[HTTP](catalogographic-api-testing.md), [adaptadores](catalogographic-dto-rdf-adapters.md),
[perfis](catalog-profiles.md), [editorial](catalogographic-editorial-http.md),
[autorização](operation-authorization.md), [plano M07](m07-cataloging-editor.md)
e ADRs 0017, 0023, 0036 e [0040](../decisions/0040-catalogographic-persistence-stabilization.md).

Evidências M06 localizadas: [BFF M06.04](catalog-bff-integration-m0604-verification-2026-10-17.md),
[cliente M06.03](catalogographic-openapi-client-m0603-verification-2026-10-17.md),
[persistência M06.02](catalogographic-persistence-m0602-verification-2026-10-17.md)
e [correção 422](work-form-422-verification-2026-10-17.md).
Não foi localizado relatório dedicado à M06.07 no checkout. A issue fechada não
foi utilizada como substituto de evidência de navegador/persistência.
Nenhum relatório histórico foi sobrescrito.

Problemas observados no código inicial:

- Apenas primeiro título/main_title/subtitle; notas apenas com primeiro label;
  contribuição apenas com primeiro papel. Idiomas dos literais eram descartados.
- Arrays de agentes/papéis filtrados separadamente, desalinhando pares; contribuições
  incompletas eram omitidas silenciosamente.
- PATCH reconstruía todos os campos preenchidos; remover todos os valores não
  enviava `[]`. Campos complexos exibidos podiam ser substituídos por formas reduzidas.
- 412 não tinha comparação nem recuperação; mensagens genéricas misturavam os status.
- Inputs de idioma/assunto/nota sem nomes acessíveis; placeholders e mensagens
  de ação fixos em inglês; ausência de indicador de alterações locais.
- Log da ação de criação imprimia o registro editorial. Foi removido.
- BFF tentava `response.clone()` após consumo pelo cliente OpenAPI: exceção
  impedia transformar erros 401/403/409/412/422 em `CatalogAccessError`.
  Os novos testes reproduziram as cinco falhas antes da correção.

## Inventário técnico Work

Fontes: [WorkCreate/Read/Update](../../apps/api/src/libris/modules/bibliographic/api/schemas/work.py),
[auxiliares](../../apps/api/src/libris/modules/bibliographic/api/schemas/common.py),
[cliente gerado](../../apps/web/src/generated/catalogographic-openapi.ts),
[inventário contratual](catalogographic-contract-field-inventory.md).

A escrita HTTP de Work usa **`urn:libris:profile:monograph:v1:1`**, candidato
institucional 1.1; PATCH conserva a release armazenada. Perfis declarativos 2.0
não foram adotados automaticamente. O formulário não escolhe nem troca perfil.
Dados históricos não representáveis continuam sujeitos à recusa segura da projeção/API.

Todos os nove campos bibliográficos são listas. Create exige `title`; os outros
possuem default vazio. Read acrescenta UUID/URI, sem perfil/revisão/proveniência.
Update admite omissão em todos: omissão preserva; lista substitui o campo inteiro;
`null` limpa apenas os oito campos opcionais. `title: null` e `title: []` são inválidos.

| Informação / campo exato | Tipo / cardinalidade | Semântica | Estado inicial → atual | Risco e tratamento | Evidência de teste |
| --- | --- | --- | --- | --- | --- |
| Títulos `title` | Title[], 1..*; ao menos um `kind=Title` | bf:title; Title/VariantTitle | Primeiro valor → todos editáveis | Não reconstruir apenas o primeiro título; PATCH só quando alterado | mapper, Chromium, PostgreSQL |
| `title[].kind` | Title ou VariantTitle, obrigatório no cliente gerado | Classe auxiliar | Implícito Title → seletor localizado | Preservar classe de cada título; não trocar variante automaticamente | mapper/Chromium |
| `title[].main_title` | LocalizedText[], 1..* por título; um valor por idioma | bf:mainTitle | Primeiro literal → repetível | Copiar valor e language completos; backend valida unicidade | mapper/Chromium/PostgreSQL |
| `title[].subtitle` | LocalizedText[], 0..* | bf:subtitle | Primeiro literal → repetível | Remoção explícita; restantes preservados | mapper/Chromium |
| `LocalizedText.value/language` | string não vazia; language opcional/null | Léxico e tag de idioma; sem datatype arbitrário no DTO | language ausente → input de tag | Sem trim/coerção do conteúdo; ausência/null mantidos quando não alterados | mapper e persistência multilíngue |
| Idiomas `language[].uri` | ResourceReference[], 0..* | bf:language, URI de vocabulário; distinta de tag de literal | Repetível sem label → repetível com label | Não converter URI em código/label; `[]` permite limpar | mapper/BFF/Chromium |
| Conteúdo `content[].uri` | ResourceReference[], 0..* | bf:content | Ausente → consulta somente leitura | Nunca incluído pelo editor no PATCH | mapper/PostgreSQL |
| Contribuições `contribution` | Contribution[], 0..* | Nó bf:Contribution | Simplificado → estrutura preservada | Preservar agrupamento agente/papéis; nenhuma filtragem independente | mapper/Chromium/PostgreSQL |
| `contribution[].agent` | Uma referência de Agent | URI ou local/external/unreconciled, com discriminadores/identidade | URI reduzida → URI editável ou estrutura somente leitura | Estruturada não vira texto/URI-only; remover vínculo exige ação explícita | fixture com namespace/external_id/UUID |
| `contribution[].role[].uri` | ResourceReference[], 1..* por contribuição | bf:role | Primeiro papel → todos repetíveis | Contribuição incompleta chega à validação, não é descartada | mapper/Chromium/PostgreSQL |
| Assuntos `subject` | Referências de Subject[], 0..* | bf:subject; identidade da autoridade | URI reduzida → URI ou referência estruturada preservada | Não confundir autoridade com identidade OIDC; sem reconciliação remota | mapper/PostgreSQL |
| Classificação `classification[].classification_portion/source` | strings 1..*; fontes URI 0..*; classificações 0..* | bf:classification/ classificationPortion/source | Ausente → consulta somente leitura | Copiar no servidor por omissão; não substituir nó parcialmente | PostgreSQL |
| Relação `related_to` | Referências de Work[], 0..* | bf:relatedTo | Ausente → consulta somente leitura | Preservada por omissão; sem inferir hierarquia | PostgreSQL |
| Tradução `translation_of` | Referências de Work[], 0..* | bf:translationOf | Ausente → consulta somente leitura | Preservada por omissão; não criar Work duplicado | PostgreSQL |
| Notas `note[].label` | Note[] 0..*; LocalizedText[] 1..* por nota | bf:note/rdfs:label | Primeiro label → todos os labels com idioma | Preservar agrupamento/idiomas; remoção explícita | mapper/Chromium/PostgreSQL |
| `note[].note_type` | string[], 0..* | bf:noteType | Descartado → repetível | Manter todos os tipos e valores da nota | mapper/Chromium/PostgreSQL |
| `internal_id`, `uri` | UUID/ResourceURI, 1..1 em Read | Identidade persistente | Identificador oculto → leitura explícita | Nunca no payload de criação/PATCH; ID apenas seleciona rota | boundary/BFF/PostgreSQL |
| Perfil, revisão, estados | Fora de WorkRead; consultas editorial/revisions | Release fixa; revisão de conteúdo e geração editorial distintas | Não expostos → leitura quando autorizada/coerente | Estado só anexado se ETag editorial = ETag do conteúdo; sem ator/proveniência no browser | teste BFF de geração correspondente |
| Proveniência e snapshots | Fora do DTO Work | Histórico imutável e autoria confiável | Não editáveis → preservados no servidor | Não enviar actor_id/revisão; mesma transação do domínio | testes API existentes e histórico PostgreSQL |
| Extensões, datatypes, IRIs de auxiliares | Fora de WorkRead quando não projetáveis | RDF completo; perfil governa permissão | Invisíveis → preservados por PATCH ou recusa | Nunca usar Read como snapshot; auxiliar com anotação não projetada bloqueia substituição no backend | suíte adaptadores/extensões + novo teste PostgreSQL |
| `bf:hasInstance` | Relação derivada, 0..* no perfil | Inversa de instanceOf | Ausente → permanece fora da edição | Não é campo WorkCreate/Update; sem tela Instance nesta entrega | contratos existentes inalterados |

### Limites semânticos importantes

O mapper mantém ordem e multiplicidade do DTO em memória e no JSON enviado.
RDF usa conjuntos: o backend ordena a projeção canonicamente e pode colapsar
triplas idênticas; não foi inventada ordenação persistente. Comparações de snapshots
usam isomorfismo. Referências estruturadas são mantidas na interface, mas o contrato
RDF não recupera namespace/external_id/UUID-only como metadados operacionais:
a projeção canônica devolve URI. Isso é limite existente, não promessa de round-trip
operacional de metadados ausentes do grafo. Unreconciled é recusado pela escrita atual.

LocalizedText não oferece datatype arbitrário. Campos alterados são reconstruídos
sob os tipos v1; a distinção sintática entre literal simples e xsd:string explícito
não é editável. Datatypes/IRIs/extensões fora do campo alterado permanecem no snapshot.
Uma propriedade que não possa ser substituída sem perda é recusada pelo backend,
com 422 e entrada local mantida. Não há tentativa de “simplificar” o grafo para salvar.

## Implementação e decisões

[work-editor.ts](../../apps/web/src/catalog/work-editor.ts) mantém o mapeamento
puro e tipado a partir do OpenAPI: cópia completa dos cinco grupos editáveis,
comparação de objetos independente da ordem das chaves e preservação da ordem das
listas, cálculo de PATCH e comparação explícita. Campos não editáveis são omitidos.
Remover o último valor opcional envia `[]`; `null` é aceito na fronteira da ação
apenas para grupos opcionais e encaminhado sem transformá-lo em omissão.
A UI utiliza `[]` como sua ação de limpeza; não precisa expor dois controles equivalentes.

O parser das ações limita tamanho, chaves permitidas e estrutura de nível superior.
Validação aninhada, referências, perfis, SHACL, estados e permissões permanecem na API.
Não há outro motor de validade no frontend nem alteração do OpenAPI gerado.
Um PATCH vazio não cria uma revisão desnecessária pela ação do formulário.

[WorkEditorFields](../../apps/web/src/components/catalog/WorkEditorFields.tsx)
organiza controles específicos para títulos, idiomas, contribuições, assuntos e
notas. O auxiliar Rows é apenas apresentação repetível; não interpreta perfis/RDF.
Referências estruturadas são mostradas integralmente e só podem ser substituídas
por remoção/adição explícitas. Conteúdo/classificações/relações têm consulta somente
leitura. Comparação avançada usa JSON dos grupos para mostrar toda a estrutura,
com rótulos humanos ao redor; não é editor JSON/RDF.

Acessibilidade: labels associados, useId, ajuda e erros por aria-describedby,
aria-invalid (inclusive required nativo), fieldset/legend, nomes de remoção com
contexto, foco após adição/remoção e primeiro erro identificável, resumo focável
para erros globais, role=status/alert e bloqueio durante processamento.
Todos os textos de interface novos vêm de messages pt/en. Mensagens públicas da
API mantêm seu idioma original; não são traduzidas por heurística.
WCAG 2.2 AA orientou os ajustes; não se declara auditoria integral de conformidade.

Ações de escrita reutilizam sessão BFF, cliente gerado e autorização FastAPI.
`assertSameOrigin` existente passou a ser aplicado explicitamente às ações: Origin,
Host e Sec-Fetch-Site, além da proteção nativa Next.js. Tokens permanecem server-side.
Os erros usam `result.error` já interpretado pelo cliente; não se relê Response consumida.
O sucesso de PATCH retorna WorkRead e ETag da **mesma resposta**, sem GET implícito
que substitua a edição por outra geração. Metadados editoriais opcionais exigem
consulta autorizada e ETag correspondente; indisponibilidade não transforma
salvamento confirmado em falha nem inventa estado/release.

## Erros e comparação 412

| Status | Comportamento |
| --- | --- |
| 401 | Explicação de sessão; abrir autenticação em outra aba; preservar entrada para retry explícito |
| 403 | Permissão insuficiente; orientar responsável pelo catálogo; não tratar como expiração |
| 409 | Conflito de domínio/estado; diagnósticos públicos e consulta do registro em outra aba; sem retry automático |
| 412 | Travar nova escrita; manter entrada e ETag antigo; carregar versão atual separadamente para comparar |
| 422 | Diagnósticos raiz e validation.issues; deduplicação; UUID de requisição; associação só por caminho inequívoco; erros globais permanecem no resumo |
| 428/indisponibilidade | Orientação de recarga ou tentativa posterior; entrada mantida; sem divulgar exceção bruta |

Na comparação, cada grupo local alterado exige escolha local/remoto explícita,
mesmo quando não houve sobreposição. Os dois grupos são mostrados integralmente;
não há união automática de arrays ou subgrafos. Campos localmente não alterados
adotam a versão remota. Aceitar grupo remoto que descarta entrada local exige
confirmação nativa. Cancelar a confirmação conserva formulário e comparação.
Só aplicar as escolhas altera base/ETag. Isso não salva: o usuário revisa e aciona
Salvar. Nova concorrência nesse intervalo é novamente 412, sem sobrescrita.
Rejeições 422 por estruturas não representadas também continuam bloqueando escrita.

Após sucesso, formulário/base vêm do Read confirmado e ficam limpos. Há aviso de
alterações locais e confirmação para navegação por links na mesma aba, além do
beforeunload. Rascunhos ficam em memória; não são gravados em localStorage. Não se
promete recuperação após encerramento do processo/aba ou todas as navegações de
histórico do navegador. A nova aba de autenticação permite preservar a aba de edição.

## Testes e evidências desta execução

Node 24.16.0 foi localizado em `/home/inacio/.nvm/versions/node/v24.16.0/bin`;
Python do ambiente do projeto: 3.13.15. `node`, `npm`, `uv` e o interpretador do
venv não estavam acessíveis no sandbox. As execuções foram autorizadas fora dele,
com `PATH=/home/inacio/.nvm/versions/node/v24.16.0/bin:$PATH` para comandos web/gate.
Nenhuma dependência ou lock foi alterado. O harness Chromium usa esbuild já presente
pelo lock/tsx, sem instalação adicional.

| Comando efetivamente executado | Resultado | Observações |
| --- | --- | --- |
| `git branch --show-current`; `git status --short` | main; limpo inicialmente | Sem alterações preexistentes |
| `npm run typecheck` inicial | Falhou; corrigido | Inferência genérica dos controles Rows; execução posterior passou |
| `npm test` inicial | 181 passaram, 5 falharam | Testes novos revelaram Response consumida no BFF; corrigido |
| `node --conditions=react-server --import tsx --test tests/catalog-bff.test.ts tests/work-editor.test.ts tests/catalog-problems.test.ts` | 18 passaram, 0 skips | Após correção do BFF; teste editorial acrescido depois |
| `bash scripts/check.sh` | Exit 0 | Contratos/OpenAPI sem drift; API 2084 passed, 0 skips, 513 warnings; Ruff/mypy; web 186 passed, 0 skips; build/typecheck; Compose estático |
| `npm test` final | 187 passaram, 0 skips | Inclui teste adicional de metadados coerentes com ETag |
| `npm run typecheck` após metadados | Passou | Gerados intactos |
| `TEST_WORK_EDITOR_BROWSER=1 node --conditions=react-server --import tsx --test tests/work-editor.e2e.ts` | 1 passou, 0 skips, duas execuções | Chromium real; React real; ações/persistência simuladas e declaradas |
| `bash scripts/test-postgres.sh` | 168 passaram, 0 skips, 2 deselected | PostgreSQL real novo em tmpfs; Alembic explícito; 2967 warnings; dois Keycloak excluídos |
| `uv run ruff format ../../tests/integration/test_work_editor_postgres.py` | 1 arquivo formatado | Somente o teste novo |
| `apps/api/.venv/bin/python scripts/verify-m04-integration.py --isolated --browser` | Exit 1 | API integração e web-store passaram; HTTP: 1 passou (editor), 2 falharam (shell administrativo preexistente), 0 skips; container próprio limpo |
| `git diff --check` | Passou na revisão intermediária | Repetido na inspeção final |

O gate executou `uv run pytest`, `uv run ruff check . ../../tests` (com scripts
adicionais definidos pelo gate) e `uv run mypy src`: 106 arquivos sem problemas.
Os warnings são deprecações Starlette/RDFLib, sem falhas mascaradas.

### O que os testes novos demonstram

- Mapper: títulos/variantes multilíngues, ausência/null/[], grupos não renderizados,
  papéis múltiplos, referências estruturadas, notas aninhadas e ausência de mutação
  da base. Criação não inclui UUID/URI. Comparação exige escolhas completas.
- BFF: cliente real gerado com fetch simulado; Bearer server-side, nenhum Cookie
  encaminhado, If-Match exato, ETag da resposta, cinco status, deny-by-default,
  proteção de origem e ausência de ator/proveniência no envelope editorial.
- Chromium: renderização pt/en; labels/IDs/descrições e árvore de acessibilidade;
  Tab; foco após adicionar; remoção; required; 401/403/409/422 preservam valores;
  422 foca campo; 412 conserva entrada e ETag; comparação não salva; nota remota
  permanece; retry usa ETag correto; salvar/recarregar; confirmação de descarte.
- PostgreSQL novo: POST/GET/PATCH reais via ASGI, ator de teste explicitamente
  substituído; primeira edição persistida, segunda 412, consulta/reconciliação,
  revisão +2 sem revisão da falha, UUID/URI/perfil estáveis, histórico isomórfico,
  extensão xsd:string preservada, limpeza null/[], 422 sem alteração do grafo.
- Regressões existentes de adaptadores, extensões, autorização, snapshots,
  migrações, publicação/candidata, CAS e anonimato executadas nas suítes completas.

## Critérios de aceite e limites

| Critério | Situação / evidência |
| --- | --- |
| Inventário DTO/perfil/editável | Atendido: tabela acima e release exata |
| Editor efetivamente revisado | Atendido: componentes, mapper, ações e rotas locais |
| Criar/editar/recarregar sem perda representável | Atendido nas camadas testadas; mapper, Chromium e PostgreSQL |
| Campos não exibidos preservados | Atendido: PATCH apenas de grupos alterados e comparação PostgreSQL |
| Estruturas complexas preservadas ou bloqueadas | Atendido: cópia integral, referências estruturadas somente leitura; proteção de auxiliares no backend e regressões |
| Idiomas, URIs, papéis e repetições | Atendido para DTO/RDF v1; limites de conjuntos/projeção explicitados |
| 401/403/409/412/422 acionáveis | Atendido: mensagens, ações e testes BFF/Chromium |
| 412 sem apagar entrada / sem sobrescrita automática | Atendido: comparação explícita, confirmação, novo ETag somente após conciliação |
| Segurança, sessão, CSRF e autorização | Atendido nas verificações executadas; sem mudança de permissões FastAPI/OIDC |
| Testes de formulário/BFF/acessibilidade/concorrência | Atendido com níveis de substituição declarados; não é auditoria WCAG completa |
| Testes pertinentes e evidências | Parcialmente atendido: gate, editor e PostgreSQL passam; regressão HTTP geral tem duas falhas preexistentes descritas abaixo |
| Documentação corrente e relatório datado | Atendido; relatórios anteriores preservados |
| Escopo e contratos | Atendido: sem API v2, perfis/SHACL/migrações/cliente gerado alterados; sem Instance/Item/circulação |
| Git local main, não staged, sem publicação | Atendido; inspeção final registrada no retorno da execução |
| Aceite por catalogador / homologação institucional | Não atendido nesta execução: exige avaliação humana independente |
| Work com navegador + ações Next + API + PostgreSQL | Atendido: teste dedicado real abaixo, JWT assinado e sessão de teste |
| Login Keycloak real e homologação institucional | Não executados; não inferir da sessão de teste |

Riscos residuais: escolhas de comparação substituem grupos inteiros; o usuário
precisa conferir todos os valores. Extensões não projetadas podem impedir alteração
de um grupo e exigem tratamento fora do formulário v1, sem contorno automático.
Classificação/conteúdo/relações permanecem somente leitura. Não há pesquisa remota
de autoridades. Sem validação institucional de usabilidade, carga ou produção.


### Regressão HTTP ampliada: falhas preexistentes

O harness isolado completou sua seleção de integração API e `npm run test:integration`
(web-store: 1 passed, 0 skips). Em `npm run test:http`, o teste Chromium do editor
passou; dois testes antigos falharam antes de alcançar suas etapas finais:

- `admin-api.e2e.ts:112`: esperava “Acesso não autorizado” na página administrativa.
- `http.e2e.ts:161`: esperava “Autenticação necessária” na mesma página.

A inspeção de `git show HEAD:apps/web/src/app/[locale]/admin/page.tsx` confirmou
que a versão anterior desta execução já retornava um cartão estático e mantinha
`guardAdminContent` comentado. O arquivo não foi modificado nesta issue.
Isso é lacuna preexistente do shell e impede declarar a regressão HTTP geral
aprovada. As verificações após essas asserções, inclusive o restante do roteiro
OIDC/navegador geral, não foram executadas. Não se alteraram as expectativas para
produzir aprovação artificial. A guarda específica das rotas Work e a autorização
BFF/FastAPI permanecem aplicadas.

O link de autenticação do editor abre `/[locale]/admin` em outra aba. Embora o shell
inicial tenha essa lacuna, `UserArea` existente renderiza ali o formulário POST
`/oidc/login` para sessões não autenticadas. Nenhuma autenticação alternativa foi
introduzida. A entrada local permanece na aba original.

O diretório privado temporário desta execução do harness foi
`/tmp/vscode-agent-codex-sandbox-mcUC6U/libris-m0409-01f3d37793282e2f-v187jjli`.
Logs privados não foram copiados para o repositório. O harness confirmou limpeza
do container que criou; nenhuma base/volume de desenvolvimento foi usada.


### Fluxo Work dedicado, com ações reais e persistência

Após identificar o bloqueio do shell geral, foi acrescentado
[work-editor-http.e2e.ts](../../apps/web/tests/work-editor-http.e2e.ts).
Ele não passa pela página inicial administrativa: navega diretamente para as
rotas Work, que continuam protegidas. O teste exige `TEST_WEB_ISOLATED=1`,
`TEST_WEB_BROWSER_ENABLED=1` e `TEST_ADMIN_API_DATABASE_URL` do harness descartável.
Cria um banco adicional com nome aleatório no cluster isolado e remove apenas
esse banco ao terminar, preservando os bancos usados pelos outros testes.

Comando executado: `bash /tmp/vscode-agent-codex-sandbox-mcUC6U/libris-m0701-web-integration.sh`.
Esse runner temporário (não altera scripts do projeto) cria um container novo com
`postgres:17-bookworm`, `--pull=never`, tmpfs e porta dinâmica loopback; fornece as
variáveis acima, executa
`node --conditions=react-server --import tsx --test tests/work-editor-http.e2e.ts`
e remove exclusivamente seu container por ID no trap.
Para reproduzir com o runner persistente já existente, usar
`PATH=/home/inacio/.nvm/versions/node/v24.16.0/bin:$PATH apps/api/.venv/bin/python scripts/verify-m04-integration.py --isolated --browser`:
`test:http` descobre também esse teste; as duas falhas gerais já documentadas
continuarão independentes até correção do shell.

Resultado final: **1 passed, 0 failed, 0 skipped**, 5,50 s. Uma primeira tentativa
do teste falhou por escape de expressão JavaScript no próprio harness, corrigido;
nenhum defeito de produto foi mascarado. A tentativa inicial de gravar o runner
em `/tmp` encontrou filesystem somente leitura, resolvida usando o diretório
temporário autorizado. Cada tentativa efetiva usou container/banco novo.

Cadeia efetivamente exercitada:

1. Chromium com perfil temporário e sessão opaca HttpOnly; sessão armazenada pelo
   PostgresAuthStore com criptografia e JWT sintético assinado de catalogador.
2. Next.js compilado, Server Actions reais, proteção de origem, BFF e cliente
   OpenAPI real; FastAPI real valida assinatura, issuer/audience e permissões.
   Apenas discovery/JWKS são servidos por provedor de teste; não é login Keycloak.
3. Criar Work pela UI; carregar detalhe; consultar API; rascunho público 404,
   leitura editorial anônima 401 e página de edição anônima sem o título privado.
4. Carregar editor, alterar título local, persistir nota concorrente pela API,
   salvar título com ETag antigo → 412 no formulário sem perda da entrada.
5. Carregar versão atual, escolher grupo local explicitamente, aplicar e salvar;
   título local e nota concorrente persistidos; revisão corrente = 3.
6. Recarregar página e conferir ambos os valores; sem JWT em saída/logs públicos.

Esse ensaio comprova a integração nova além dos testes de componentes com ações
simuladas. Não inclui emissão/login Keycloak real, aceite humano ou conformidade
WCAG integral. O build usado foi o produzido pelo gate desta execução.


## Inspeção final do working tree

O build alterou automaticamente `apps/web/next-env.d.ts` entre caminhos dev/build.
Essa alteração incidental foi restaurada ao conteúdo inicial; o typecheck final
passou depois da restauração. Nenhum cliente OpenAPI foi editado manualmente.
`git diff --check` passou e `git diff --cached --stat` ficou vazio.

Saídas da inspeção (o diff stat não inclui arquivos novos não rastreados):

```text
$ git branch --show-current
main
```

```text
$ git status --short
 M README.md
 M apps/web/src/app/[locale]/admin/catalog/works/[id]/edit/page.tsx
 M apps/web/src/components/catalog/WorkEditorForm.tsx
 M apps/web/src/i18n/messages.ts
 M apps/web/src/server/catalog/actions.ts
 M apps/web/src/server/catalog/index.ts
 M apps/web/src/server/catalog/problems.ts
 M apps/web/tests/browser.ts
?? apps/web/src/catalog/
?? apps/web/src/components/catalog/WorkEditorFields.tsx
?? apps/web/tests/catalog-bff.test.ts
?? apps/web/tests/fixtures/
?? apps/web/tests/work-editor-fixture.ts
?? apps/web/tests/work-editor-http.e2e.ts
?? apps/web/tests/work-editor.e2e.ts
?? apps/web/tests/work-editor.test.ts
?? docs/architecture/work-editor-m0701-verification-2026-10-18.md
?? tests/integration/test_work_editor_postgres.py
```

```text
$ git diff --stat
 README.md                                          |   7 +
 .../admin/catalog/works/[id]/edit/page.tsx         |  10 +-
 apps/web/src/components/catalog/WorkEditorForm.tsx | 497 ++++++---------------
 apps/web/src/i18n/messages.ts                      |  86 ++++
 apps/web/src/server/catalog/actions.ts             | 266 ++---------
 apps/web/src/server/catalog/index.ts               |  61 ++-
 apps/web/src/server/catalog/problems.ts            |  29 +-
 apps/web/tests/browser.ts                          |   6 +-
 8 files changed, 364 insertions(+), 598 deletions(-)
```

```text
$ git ls-files --others --exclude-standard
apps/web/src/catalog/work-editor.ts
apps/web/src/components/catalog/WorkEditorFields.tsx
apps/web/tests/catalog-bff.test.ts
apps/web/tests/fixtures/work-editor-browser.tsx
apps/web/tests/work-editor-fixture.ts
apps/web/tests/work-editor-http.e2e.ts
apps/web/tests/work-editor.e2e.ts
apps/web/tests/work-editor.test.ts
docs/architecture/work-editor-m0701-verification-2026-10-18.md
tests/integration/test_work_editor_postgres.py
```

Todos os arquivos permanecem não staged na main. Não houve commits, criação de branches, push, pull, PRs ou alterações remotas.
