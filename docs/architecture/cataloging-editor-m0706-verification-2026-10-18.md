# Verificação M07.06 — editor catalográfico ponta a ponta — 2026-10-18

## Identificação, escopo e estado inicial

Verificação técnica local da [issue #66](https://github.com/inacioigne/libris/issues/66),
aberta e sem comentários no momento da consulta. Branch inicial `main`, commit
`1294174 M07.05`, diretório de trabalho limpo. Nenhuma branch, commit, preparação,
push, Pull Request ou alteração remota foi realizada.

Foram lidos o `AGENTS.md`, as fontes arquiteturais exigidas pela issue, os contratos
HTTP/perfis/editorial/autorização/persistência, o ADR 0040, os ADRs relacionados e
as evidências M07.01–M07.05. Código e testes locais foram tratados como fonte de
estado efetivo. Os relatórios históricos não foram alterados.

## Ambiente

| Componente | Evidência |
| --- | --- |
| Data/hora | 2026-10-18, America/Manaus |
| Python | 3.13.15 no gate oficial; 3.13.16 no ensaio focado em container |
| Node/npm | Node 24.16.0 e npm do ambiente NVM |
| PostgreSQL | imagem local `postgres:17-bookworm`, digest `sha256:3645570...`; banco novo em tmpfs pelo runner oficial |
| Docker | Engine 29.8.2 |
| API | FastAPI/ASGI, Alembic `head`, SQLAlchemy async/asyncpg |
| Identidade de integração | JWT RS256 efêmero, issuer/audience/azp e permissões efetivamente validados; descoberta/JWKS locais por MockTransport, sem alegação de Keycloak real |
| Navegador | Google Chrome headless real; componentes React reais; ações/persistência simuladas nos probes existentes |

O shell restrito inicialmente não expunha `uv`, `node` e `npm`. Os comandos oficiais
foram repetidos fora desse sandbox com os runtimes locais já instalados. Nenhuma
dependência ou lock foi alterado.

## Matriz de rastreabilidade e resultados

| Requisito | Implementação efetiva | Evidência | Estado | Lacuna/ação |
| --- | --- | --- | --- | --- |
| Criação de Work | API v1, BFF e formulário Work | novo `test_m0706_end_to_end_cataloging_chain_and_published_revision`; suites Work web/PostgreSQL | APROVADO | Work multilíngue, repetível, contribuição, assunto e nota no mesmo fluxo |
| Criação de Instance | `instanceOf` explícito e editor próprio | teste M07.06, `test_instance_editor_postgres.py`, Chrome Instance | APROVADO | Identidade e revisão independentes conferidas |
| Criação de bf:Item | `itemOf` explícito, sem circulação | teste M07.06, `test_item_editor_postgres.py`, Chrome Item | APROVADO | Identificador local e metadados sem comportamento operacional |
| Vínculos e autoridades | Agent/Subject locais e relações versionadas | teste M07.06, `test_authority_links_m0704_postgres.py`, Chrome Authority | APROVADO | URIs/UUIDs/papéis preservados; autoridade não é identidade OIDC |
| Revisões e publicação | sete comandos, ETag editorial, revisão pública fixada | teste M07.06 e `test_editorial_http_postgres.py` | APROVADO | Nova candidata não substitui revisão publicada |
| Segurança | Resource Server OIDC, políticas FastAPI, sessão/CSRF no BFF | `test_catalog_acceptance_postgres.py`, suites API/web | APROVADO | Keycloak e navegador autenticado integrados não executados |
| Navegador ponta a ponta autenticado | rotas e componentes existem | quatro probes Chrome reais | BLOQUEADO | probes simulam ações; não havia ambiente Keycloak+BFF+API isolado configurado para a cadeia completa |
| Aceite biblioteconômico | roteiro separado | `cataloging-editor-m0706-human-acceptance.md` | NÃO EXECUTADO | requer catalogador humano identificado |

## Correção e regressão acrescentada

A lacuna encontrada foi de evidência integrada: as entregas M07.01–M07.05 possuíam
testes fortes por incremento, mas nenhum cenário PostgreSQL unia autoridades,
Work, Instance e Item, inversas, ciclo editorial por papéis e revisão pública em
uma única execução.

Foi acrescentado `test_m0706_end_to_end_cataloging_chain_and_published_revision` em
`tests/integration/test_catalog_acceptance_postgres.py`. O teste:

- usa HTTP ASGI e PostgreSQL real, sem override de ator/autorização/serviço;
- valida JWT RS256 e alterna cataloger, reviewer e publisher por identidade assinada;
- cria Agent e Subject, Work multilíngue com papéis repetidos, Instance e bf:Item;
- verifica UUIDv4/URI únicos, persistência após recarga e revisões independentes;
- verifica `hasInstance` e `hasItem` derivados das relações explícitas;
- confirma ausência pública antes de publicar;
- submete, aprova e publica cada entidade;
- cria nova candidata de Work e confirma que a revisão pública permanece na revisão 1;
- compara o snapshot histórico e a representação pública por isomorfismo RDFLib.

Não foram alterados DTOs, OpenAPI, cliente gerado, perfis, SHACL, migrações ou
contratos v1.

## Segurança, concorrência e códigos HTTP

| Cenário | Evidência executada | Resultado |
| --- | --- | --- |
| 401 sem sessão/token inválido | matriz assinada e confidencialidade em `test_catalog_acceptance_postgres.py`; BFF web | APROVADO |
| 403 sem permissão | matriz dos cinco papéis e permissões conjuntivas | APROVADO |
| 409 estado/transição | transições editoriais inválidas e edição em estado aprovado | APROVADO |
| 412 ETag obsoleto | duas sessões concorrentes, relação obsoleta e reconciliação nos três editores/autoridades | APROVADO |
| 422 DTO/referência/SHACL | referências inexistentes, publicação inválida e diagnósticos por campo/registro | APROVADO |
| CSRF e segredo server-side | testes de origem BFF, sessão opaca, Bearer somente no servidor e logs seguros | APROVADO |
| anonimato e confidencialidade | todos os caminhos editoriais/formatos e listagens; draft/candidata ocultos | APROVADO |
| corrida entre sessões | CAS PostgreSQL com um vencedor; publicação concorrente com edição | APROVADO |

Papéis/identidade enviados em headers de teste não substituem o ator: a API obtém o
ator do token/dependência confiável e os testes verificam que headers forjados não
alteram a auditoria. `system:admin` isolado não é bypass.

## Integridade semântica e persistência

O runner oficial criou PostgreSQL 17 exclusivo, publicou porta somente em loopback,
usou tmpfs, migrou quatro bancos inicialmente vazios e removeu seu container por
trap. O JUnit `/tmp/libris-postgres-junit.JLmmos.xml` registrou 173 testes, 0 falhas
e 0 skips; a validação do script recusaria qualquer skip.

As suítes executadas cobrem UUID/URI armazenados, FKs/constraints/triggers,
snapshots imutáveis, revisões e atores, auditoria transacional, perfis históricos,
Work–Instance–Item, autoridades compartilhadas, propriedades não exibidas,
distinção omitido/null/[], extensões e datatypes. Comparações RDF usam isomorfismo,
nunca igualdade textual. Os dois casos Keycloak foram deliberadamente `deselected`
pela seleção oficial `not keycloak`; não são contados como skips nem como evidência
de Keycloak real.

## Comandos executados

| Data/hora local | Comando | Resultado |
| --- | --- | --- |
| 15:58:40 | `bash scripts/check.sh` no shell restrito | exit 127: `uv` fora do PATH; bloqueio ambiental registrado, depois repetido corretamente |
| 15:58:47 | `bash scripts/test-postgres.sh` | exit 0; 173 passed, 0 failed, 0 skipped, 2 deselected; 3880 warnings; 193,23 s |
| 16:02:29 | `PATH=... bash scripts/check.sh` | exit 0; contratos/OpenAPI sem drift; API 2088 passed, 0 skips, 513 warnings; Ruff; mypy 106 arquivos; web 236 passed, 0 skips; build/typecheck; Compose válido |
| 16:09:08 | `TEST_WEB_BROWSER_ENABLED=1 node ... --test` nos quatro editores | exit 0; 4 passed, 0 failed, 0 skipped; 9,41 s |
| durante o diagnóstico | cenário M07.06 isolado em PostgreSQL 17 migrado | 1 passed, 0 skipped; 109 warnings; 10,60 s |
| após implementação | `ruff check`/`ruff format --check` pelo gate oficial | aprovados; três linhas longas detectadas no ensaio inicial foram formatadas antes do gate final |

Warnings observados são deprecações conhecidas de Starlette TestClient e RDFLib
ConjunctiveGraph; não houve falha ou skip oculto.

## Navegador e limitações

Chrome real exercitou pt/en, árvore de acessibilidade, labels, teclado, repetidores,
validação, loading, mensagens 401/403/409/412/422, preservação de entrada,
reconciliação explícita e recarga em Work, Instance, Item e autoridades. Esses
probes usam React real, mas simulam ações/persistência e não autenticam contra um
provedor OIDC.

Não foi executada uma única cadeia navegador → Keycloak → BFF → FastAPI → PostgreSQL.
A integração HTTP/PostgreSQL usou tokens assinados e políticas reais, porém sem
Keycloak/rede. Portanto, esta evidência não autoriza afirmar homologação de login,
Keycloak ou experiência institucional ponta a ponta.

## Backlog residual

| Prioridade | Impacto | Pendência/dependência |
| --- | --- | --- |
| P0 para fechar #66 | impede homologação técnica completa | executar cadeia Work → Instance → Item em navegador autenticado, ambiente isolado Keycloak+BFF+API+PostgreSQL, retendo evidências sem segredos |
| P0 institucional | impede aceite institucional | aplicar o roteiro com catalogador e registrar decisão real |
| P1 | confiança operacional | avaliar warnings/deprecações Starlette/RDFLib em atualização separada, sem misturar com contratos M07 |
| P2 | rastreabilidade | considerar um harness futuro que una os probes de browser ao backend isolado; exige decisão de escopo, não nova dependência automática |

## Conclusão

Os contratos e componentes testados estão aprovados no escopo automatizado, e a
lacuna de regressão integrada foi corrigida. Contudo, um critério crítico da issue
permanece sem evidência: o fluxo completo em navegador autenticado contra toda a
pilha. Assim, a **homologação técnica é INCONCLUSIVA**, não reprovada por defeito
conhecido, mas bloqueada por ensaio integrado não executado.

**Homologação institucional pendente — avaliação humana não realizada.**

