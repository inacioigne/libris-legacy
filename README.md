# Libris

A M07.04 acrescenta [consulta e vínculos visuais de Agent/Subject](docs/architecture/authority-links.md)
nos detalhes administrativos de Work, Instance e Item, em pt/en, com seleção de
autoridades existentes, papéis de contribuição e concorrência relacional.
Preserva revisões independentes e a matriz de permissões, sem reconciliação externa
ou novo CRUD visual de autoridades. Consulte as
[evidências M07.04](docs/architecture/authority-links-m0704-verification-2026-10-18.md).

A M07.05 integra [governança editorial](docs/architecture/cataloging-editorial-m0705.md)
nas telas administrativas de Work, Instance e Item: estado, revisões, diff
semântico, auditoria autorizada, transições com ETag e pré-validação de criação.
Consulte as [evidências datadas](docs/architecture/cataloging-editorial-m0705-verification-2026-10-18.md)
para comandos, limites e itens ainda não verificados.

A M07.03 acrescenta o [editor de Item semântico](docs/architecture/item-editor.md)
pt/en e a navegação Work → Instance → Item por relações reais. Criação, consulta,
edição parcial, pré-validação e conciliação de ETags preservam os contratos v1;
Item não representa inventário operacional ou circulação. Consulte as
[evidências M07.03](docs/architecture/item-editor-m0703-verification-2026-10-18.md).

A M07.02 implementa o [editor administrativo de Instance](docs/architecture/instance-editor.md)
em português e inglês: listagem, detalhe, criação com seleção explícita de Work e
edição parcial com ETag/If-Match e comparação de conflitos. Reutiliza DTOs v1,
BFF OIDC e persistência existentes, sem publicação automática. Consulte as
[evidências técnicas datadas](docs/architecture/instance-editor-m0702-verification-2026-10-18.md)
para resultados, limites e distinção de homologação institucional.

Base inicial de um sistema integrado de gestão de bibliotecas com BIBFRAME 2.0,
RDF e Linked Data. Monólito modular FastAPI, frontend Next.js App Router,
PostgreSQL transacional e Elasticsearch para descoberta futura. A base visual usa
Tailwind e uma primitiva acessível Radix UI; a adoção de shadcn/ui para controles
complexos está registrada no ADR 0005.

**Implementado:** `/health`, página inicial `/pt/opac` e `/en/opac`, configuração validada,
engine/sessões SQLAlchemy assíncronas, Docker Compose e núcleo semântico experimental
BIBFRAME/RDF. A Etapa 2 inclui JSON-LD/Turtle, perfil monográfico, SHACL, UUID/URI,
snapshots JSONB, revisões e concorrência otimista. Há migrações Alembic
experimentais; elas não executam no startup. A M05.03 implementa
[POST/GET/PATCH editoriais persistentes](docs/architecture/catalogographic-http.md)
para Work, Instance, Item, Agent e Subject, com ETag/If-Match. A M05.05 publica
[ciclo editorial, histórico, auditoria e restauração](docs/architecture/catalogographic-editorial-http.md),
com sete comandos explícitos e leitura pública da revisão publicada exata.
[Evidências PostgreSQL e matriz de testes](docs/architecture/catalogographic-m0505-verification-2026-10-16.md).
A M05.06 implementa [listagens públicas/editoriais, filtros, paginação e Accept JSON-LD/Turtle](docs/architecture/catalogographic-listing.md), sem depender de Elasticsearch.
Editor, circulação,
reconciliação de autoridades e pesquisa continuam futuros. A Entrega A do Incremento 2
implementa [autenticação/autorização OIDC](docs/architecture/identity.md),
`/api/v1/identity/me` e ator opcional nas revisões, com [BFF de login web M04.03](docs/architecture/identity-web.md) implementado;
ensaio real isolado documentado em [M04.03](docs/architecture/identity-web-verification.md);
homologação de navegador/produção permanece separada.
A M04.04 implementa o [shell administrativo e guardas server-side](docs/architecture/admin-access.md)
em `/pt/admin` e `/en/admin`, com navegação por permissões efetivas e
[verificação própria](docs/architecture/admin-access-verification.md), sem CRUD ou editor.
A M04.02 fornece [roteiro Keycloak isolado](docs/architecture/identity-keycloak-local.md)
e [evidências reais e limites](docs/architecture/identity-m04-02-verification.md).

Consulte a [fundação semântica](docs/architecture/semantic-foundation.md) para
exemplos, serviços internos, limites e testes de integração PostgreSQL.

A M06.02 estabelece a [baseline operacional da persistência catalográfica](docs/architecture/catalogographic-persistence.md)
e sua política incremental de evolução. Preserva os nomes físicos históricos
`experimental_semantic_*`, sem nova migração ou mudança dos contratos M03–M05.
O [ADR 0040](docs/decisions/0040-catalogographic-persistence-stabilization.md)
distingue estabilidade operacional de esquema definitivo e homologação institucional.

A Etapa 3, Incremento 1 consolida o [contrato catalográfico](docs/architecture/catalogographic-contract.md):
release candidata monograph-v1.1, perfil complementar de autoridades, snapshots
proprietários e revisões independentes, preservando o perfil experimental legado.
Ver [diagnóstico](docs/architecture/catalogographic-contract-diagnosis.md),
[ADRs 0009–0015](docs/decisions/) e
[relatório de verificação](docs/architecture/catalogographic-contract-verification.md).
A homologação institucional permanece pendente; não há novos endpoints ou editor.
A M03.01.02 acrescenta [contratos públicos Pydantic v2](docs/architecture/catalogographic-schemas.md)
para Work, Instance, Item, Agent e Subject, com Create/Read/Update e testes OpenAPI
isolados; as APIs de catalogação continuam pendentes.
A M03.01.03 acrescenta o [contrato de relacionamentos](docs/architecture/relationships.md):
referências locais, externas e não reconciliadas, matriz semântica, operações
explícitas de vínculos e validação de integridade com resolvedor desacoplado.
Ver [evidências e critérios](docs/architecture/relationships-verification.md).
A M03.01.04 consolida a [estratégia de identificadores persistentes](docs/architecture/persistent-identifiers.md):
UUIDv4 e URI armazenada, referências compatíveis, ciclo de vida, importação e
resolução HTTP futura. Ver [ADR 0019](docs/decisions/0019-persistent-identifiers.md)
e [verificação](docs/architecture/persistent-identifiers-verification.md).

A M03.01.05 implementa a [representação semântica v1](docs/architecture/semantic-serialization.md):
mapeamento dos cinco Read para Graph, JSON-LD com contexto local versionado, Turtle,
exportação integral de snapshots e negociação Accept desacoplada. Ver
[ADR 0020](docs/decisions/0020-jsonld-rdf-serialization.md),
[exemplos pareados](docs/examples/serialization/) e
[verificação](docs/architecture/semantic-serialization-verification.md).
Não há novos endpoints ou homologação institucional.


A M03.02.01 acrescenta o [contrato de perfis declarativos 2.0](docs/architecture/catalog-profiles.md):
configurações Pydantic/JSON, herança por release, seleção determinística, registro
semântico local e associação SHACL versionada, preservando todos os perfis históricos.
Ver [ADR 0021](docs/decisions/0021-declarative-catalog-profiles.md),
[exemplos](docs/examples/profiles/) e [verificação](docs/architecture/catalog-profiles-verification.md).
O contrato prepara formulários futuros; não acrescenta CRUD, editor ou tabelas.

A M03.02.02 consolida a [validação SHACL](docs/architecture/shacl-validation.md):
registro local por release/contexto, diagnóstico RDF completo, distinção de falhas e
políticas suplementares para perfis 2.0, preservando as fontes históricas.
Ver [inventário](docs/architecture/shacl-inventory.md),
[ADR 0022](docs/decisions/0022-shacl-profile-policies.md) e
[evidências](docs/architecture/shacl-validation-verification.md).

A M03.02.03 implementa [diagnósticos e erros catalográficos 1.0](docs/architecture/catalogographic-errors.md):
contratos Pydantic, códigos estáveis, adaptação SHACL/perfis, correlação por request ID
e Problem Details na aplicação FastAPI. Rejeições persistentes carregam diagnósticos,
preservando transações e perfis históricos. Ver [ADR 0023](docs/decisions/0023-catalogographic-diagnostics.md)
e [verificação](docs/architecture/catalogographic-errors-verification.md).
Pré-validação HTTP, ETags de edição e editor continuam futuros.

A M03.03.01 acrescenta o [contrato de valores controlados e vocabulários](docs/architecture/controlled-vocabularies.md):
valores discriminados 1.0, conceitos locais/externos, evidência de pertença, estados,
provedores desacoplados e política de cache, com exemplos RDF/JSON-LD verificáveis.
Ver [ADR 0024](docs/decisions/0024-controlled-vocabularies.md) e
[verificação](docs/architecture/controlled-vocabularies-verification.md).
Não há conectores remotos, cache persistente ou adoção automática nos DTOs históricos.

A M03.03.02 implementa [extensibilidade semântica governada](docs/architecture/semantic-extensions.md):
registro semântico 2.0 fixado por release, autorização de classes/propriedades e
perfil candidato BIBFRAME + Dublin Core Terms, com exemplos RDF/JSON-LD sem perdas.
Ver [ADR 0025](docs/decisions/0025-semantic-extensions.md) e
[verificação](docs/architecture/semantic-extensions-verification.md).
Os registros/perfis históricos e os DTOs permanecem preservados; não há novas rotas.

A M03.04.01 implementa o [contrato de estados editoriais](docs/architecture/editorial-lifecycle.md):
matriz fechada, permissões explícitas, publicação validada, revisão candidata separada
da publicação anterior e ciclo independente da identidade. Ver
[ADR 0026](docs/decisions/0026-editorial-lifecycle.md).
O contrato original foi integrado à persistência PostgreSQL pela #43, descrita abaixo.


A M03.04.02 implementa [proveniência, auditoria e versionamento](docs/architecture/provenance-audit-versioning.md):
eventos imutáveis no mesmo commit das revisões, histórico interno autorizado,
restauração de conteúdo com CAS e comparação semântica RDF. A migração incremental
`0003_semantic_audit` preserva snapshots legados, sem backfill. Ver
[ADR 0027](docs/decisions/0027-provenance-audit-versioning.md) e
[evidências e pendências](docs/architecture/provenance-audit-versioning-verification.md).
A M03.04.03 / #43 implementa a [persistência editorial PostgreSQL](docs/architecture/editorial-persistence.md):
metadados separados, transições e auditoria no mesmo commit, CAS de conteúdo/estado,
publicação da revisão aprovada, candidata isolada e consultas internas autorizadas.
`0004_semantic_editorial` preserva legado sem backfill e amplia RETURN/ARCHIVE.
Ver [ADR 0028](docs/decisions/0028-editorial-transactions.md) e
[verificação](docs/architecture/editorial-persistence-verification.md).
Não há APIs CRUD públicas ou homologação institucional.


A M04.08 acrescenta [eventos estruturados de segurança](docs/architecture/security-audit.md)
na API/BFF, com ator confiável, correlação e privacidade, separados da auditoria M03.
Emissão por logs não garante armazenamento durável; retenção/acesso operacional
seguem contrato próprio. Ver [evidências locais](docs/architecture/security-audit-m04-08-verification.md)
e [ADR 0035](docs/decisions/0035-security-audit-emission.md).

## Estrutura

```text
apps/
  api/
    src/libris/{api,core,infrastructure,modules}/
    migrations/versions/
    pyproject.toml, uv.lock, alembic.ini
  web/
    src/app/[locale]/
    src/i18n/
    package.json, package-lock.json
packages/shared/
infrastructure/docker/
docs/architecture/
docs/decisions/
scripts/check.sh
tests/api/
docker-compose.yml
.env.example
AGENTS.md
```

Leia [visão arquitetural](docs/architecture/overview.md),
[domínios](docs/architecture/domains.md),
[modelo bibliográfico](docs/architecture/bibliographic-model.md) e
[ADRs](docs/decisions/) antes de implementar módulos.

A [baseline de segurança M04.01](docs/architecture/security-baseline.md) reúne
o inventário OIDC, a matriz de acesso, as ameaças e os critérios de saída M04,
distinguindo serviços internos, requisitos futuros e validação operacional pendente.

## Executar com Docker

Requisitos: Docker Engine com Compose, acesso ao daemon e conexão para baixar
imagens/dependências. Reserve inicialmente cerca de 4 GB de RAM disponíveis para
o conjunto; ajuste conforme o ambiente.

```bash
cp .env.example .env
# Edite .env: escolha uma senha local e atualize DATABASE_URL com a mesma senha.
# Defina também KEYCLOAK_ADMIN_USERNAME e KEYCLOAK_ADMIN_PASSWORD.
docker compose config --quiet
docker compose up --build -d
# Ação administrativa explícita: instala/atualiza tabelas da API e do BFF.
docker compose run --rm --no-deps api alembic upgrade head
docker compose ps
docker compose logs -f api web
```

Acesse:

- Frontend: http://localhost:3000 (redireciona para `/pt/opac`; inglês em `/en/opac`).
- API: http://localhost:8000/health
- OpenAPI: http://localhost:8000/docs
- Elasticsearch: http://localhost:9200
- PostgreSQL: localhost:5432
- Keycloak: http://keycloak.localhost:8081/admin

O `docker-compose.yml` reúne os cinco serviços, com OIDC habilitado por padrão.
Host/navegador precisam resolver `keycloak.localhost` em `127.0.0.1`; consulte o
[roteiro Keycloak](docs/architecture/identity-keycloak-local.md).

As portas de PostgreSQL, Elasticsearch, API e web podem ser alteradas no `.env`.
Ajuste `CORS_ORIGINS` se mudar a porta do frontend. Dentro do Compose use hosts `postgres`, `elasticsearch` e `api`,
independentemente das portas publicadas. `API_INTERNAL_URL` é usado pelo BFF para consultar /api/v1/identity/me
com Bearer server-side. Configure as variáveis WEB_* e mantenha DATABASE_URL e WEB_SESSION_DATABASE_URL
no mesmo banco. O Alembic prepara também o schema separado libris_web; veja [identidade web](docs/architecture/identity-web.md).

A senha do exemplo é descartável e pública. O Compose é exclusivamente local,
publica portas em `127.0.0.1` e desativa segurança do Elasticsearch. Não usar essa
configuração como implantação de produção. Configurar credenciais, TLS, backups e
rede apropriados antes de implantação real. `POSTGRES_PASSWORD` só inicializa um
volume novo; alterá-la no `.env` não troca a senha de um banco já existente.

Se Elasticsearch registrar erro de `vm.max_map_count`, consulte o requisito do
host em [documentação Elastic](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-prod).
Não altere configurações do kernel sem considerar o ambiente hospedeiro.

```bash
docker compose stop        # preserva volumes
docker compose down        # remove containers/rede, preserva dados
# docker compose down -v   # DESTRUTIVO: apaga dados locais; execute só se desejado
```

## Desenvolvimento com ferramentas locais

Instale [uv](https://docs.astral.sh/uv/getting-started/installation/) e Node.js 24 LTS
(com npm). Python de referência é 3.13; uv pode obtê-lo automaticamente. Mantenha os locks
sob controle de versão; não inclua a pasta `.venv` nem `node_modules`.

```bash
cp .env.example .env
# Para aplicações fora do Docker, altere no .env:
# DATABASE_URL=postgresql+asyncpg://libris:SUA_SENHA@localhost:5432/libris
# ELASTICSEARCH_URL=http://localhost:9200
# API_INTERNAL_URL=http://localhost:8000
docker compose up -d postgres elasticsearch
cd apps/api
uv sync --locked
uv run fastapi dev --host 0.0.0.0
```

Em outro terminal:

```bash
cd apps/web
npm ci
npm run dev
```

O backend lê `.env` da raiz quando executado em `apps/api`; variáveis do processo
prevalecem. A saúde pode funcionar sem PostgreSQL/Elasticsearch, pois não abre
conexão no startup; isso é intencional e não comprova readiness dos serviços.

## Testes e verificações

A M05.07 / #51 consolida o [guia de consumo e testes HTTP catalográficos](docs/architecture/catalogographic-api-testing.md),
com [matriz e evidências próprias](docs/architecture/catalogographic-m0507-verification-2026-10-16.md).
`bash scripts/test-postgres.sh` executa a integração em PostgreSQL 17 novo e
descartável, com quatro bancos separados, migrações explícitas e rejeição de skips.
JWT/JWKS assinados offline, overrides de ator e integração Keycloak real são
identificados separadamente; a suíte padrão permanece sem serviços externos.

Referência canônica da M03.05.02 / #31: [contrato catalográfico v1, especificação 1.0.0](docs/architecture/catalogographic-contract.md),
[inventário de campos](docs/architecture/catalogographic-contract-field-inventory.md),
[JSON Schema documental](docs/architecture/catalogographic-contract-v1.schema.json),
[rastreabilidade](docs/architecture/catalogographic-contract-traceability.md) e
[verificação](docs/architecture/catalogographic-contract-v1-verification.md).
A [política de evolução proposta](docs/decisions/0029-catalogographic-contract-versioning.md)
distingue contrato público, perfis, shapes e revisões; não cria versionamento HTTP
ou modifica releases históricas. Homologação institucional permanece pendente.

Consolidação da M03.05.01 / #30: [inventário de fixtures e testes](docs/architecture/catalogographic-v1-test-inventory.md),
[matriz de conformidade](docs/architecture/catalogographic-v1-conformance-matrix.md) e
[reprodução e resultados](docs/architecture/catalogographic-v1-testing.md).

```bash
cd apps/api
uv run pytest
uv run ruff check . ../../tests
uv run mypy src
uv run alembic heads
uv run alembic upgrade head --sql
```

Existem as revisões experimentais `0001_semantic`, `0002_revision_actor` e
`0003_semantic_audit` e `0004_semantic_editorial`. `alembic current` exige banco
acessível; `heads` e geração offline inspecionam o ambiente sem modificar o banco.
Execute `uv run alembic upgrade head` explicitamente para instalar as tabelas
experimentais e o schema `libris_web` (revisão 0005). O downgrade de 0005 preserva
as tabelas web; downgrades bibliográficos anteriores podem descartar dados.

```bash
cd apps/web
npm run build
npm run typecheck
```

Na raiz, `bash scripts/check.sh` reúne os testes, análise de tipos, build e
validação do Compose. Para testar dentro da imagem API, que não contém dependências
de desenvolvimento por padrão:

```bash
docker compose run --rm --user root -v "$PWD/tests:/tests:ro" api \
  sh -c 'uv sync --locked --group dev && uv run pytest /tests/api'
```

Esse comando instala ferramentas somente no container descartável e precisa de
rede. A suíte padrão cobre lifespan/saúde sem serviços externos, contrato OpenAPI,
CORS, configuração, RDF e SHACL. A suíte separada de integração verifica snapshots,
histórico e concorrência em PostgreSQL real; não há integração com índices. Evidências desta sessão estão em
[verification.md](docs/architecture/verification.md).

## Próxima etapa

Homologar os perfis candidatos com catalogadores e implementar a API de catalogação
no Incremento 2, conforme o contrato consolidado de entidades, URIs, referências
e concorrência. Autenticação/autorização OIDC está implementada na Entrega A;
auditoria de criação/revisão e histórico interno estão implementados na M03.04.02.
Persistência editorial e integração interna estão implementadas na #43; APIs completas
e demais entregas operacionais continuam pendentes.
As tabelas permanecem experimentais; não há esquema bibliográfico definitivo.
Ver [contrato](docs/architecture/catalogographic-contract.md) e ADRs 0009–0015.

A licença do Libris ainda precisa ser escolhida pelos mantenedores; não foi
adicionada uma licença arbitrária. Avaliar também os termos das distribuições
Elasticsearch antes de redistribuição. Para orientação FastAPI atualizada opcional,
`uvx library-skills` instala skills interativamente a partir das dependências;
escolha o agente usado no projeto quando solicitado.

A M04.05 / #38 consolida [RBAC por operação e guard de aplicação](docs/architecture/operation-authorization.md),
com contextos confiáveis explícitos, escopo técnico limitado e preservação dos contratos
M03; [evidências atuais](docs/architecture/identity-m04-05-verification.md). Sem novas APIs CRUD.

A M04.06 / #39 define [governança de identidades e contas de serviço](docs/architecture/identity-governance.md),
com cliente humano vinculado por azp assinado, revogação com validade residual de JWT,
eventos administrativos e contrato futuro M2M fechado. Ver
[evidências próprias](docs/architecture/identity-m04-06-verification.md); sem multi-tenancy.


## Atualização M04.07 — 2026-10-14

A [política HTTP M04.07](docs/architecture/http-security.md) acrescenta validação CORS por
ambiente, CSP com nonce no SSR Next.js, headers/cache, limites de entrada e quotas
compartilhadas PostgreSQL. Reutiliza PKCE, state/binding/consumo único, sessão cifrada,
CSRF e autorização existentes. Instalação exige `alembic upgrade head` explicitamente; a revisão **0005_web_auth**
cria/adota as tabelas em `libris_web`, inclusive quotas (ADR 0034). Nenhuma migração executa no startup. Em produção web,
WEB_HTTPS_TERMINATED=1 exige proxy TLS/rede confiáveis; Compose continua local.
Resultados, falhas corrigidas e limites no [relatório próprio](docs/architecture/http-security-m04-07-verification.md).
As evidências/tabelas históricas acima não foram reclassificadas como homologação
atual; carga, TLS público, operação institucional e pentest continuam pendentes.

A M05.03 / #47 conecta os quinze DTOs aos serviços PostgreSQL existentes, com
precondição editorial e de conteúdo, rollback e preservação da revisão publicada.
Não expõe histórico, transições, relações, endpoints públicos ou listagens HTTP.
Ver [contrato operacional](docs/architecture/catalogographic-http.md),
[ADR 0036](docs/decisions/0036-catalog-http-concurrency.md) e
[evidência datada](docs/architecture/catalogographic-m0503-verification-2026-10-16.md).

A M05.04 / #48 acrescenta [relações e pré-validação HTTP](docs/architecture/catalogographic-http.md#m0504--48--capacidades-implementadas), com consulta somente-leitura de perfis/releases, diagnósticos 1.0, CAS e rollback relacional. [Evidências e limites](docs/architecture/catalogographic-m0504-verification-2026-10-16.md); não inclui editor nem homologação institucional.

A M06.01 / #53 acrescenta a [baseline OpenAPI operacional](docs/architecture/catalogographic-openapi.md),
com geração exclusiva, check de drift, CI e [inventário das operações reais](docs/architecture/catalogographic-openapi-inventory.md).
A especificação documental 1.0.0 permanece preservada; esta entrega não implementa
cliente TypeScript, BFF catalográfico ou editor Work.


## M07 — Próximo incremento: editor de catalogação

A M07.01 (#61) revisa o editor Work com PATCH por grupo alterado, títulos/variantes
multilíngues, contribuições e notas completas, diagnósticos pt/en e comparação
explícita de versões em conflitos 412. Dados fora do formulário permanecem
preservados; a API bloqueia substituições sem representação segura. Consulte o
[inventário, testes executados e limites da M07.01](docs/architecture/work-editor-m0701-verification-2026-10-18.md).
Aceite por catalogador e homologação institucional permanecem independentes.

A M06 (#52–#59, issues encerradas) estabeleceu a integração OpenAPI → cliente TypeScript
gerado → BFF Next.js com sessão OIDC e as primeiras telas de Work: listagem, detalhe,
criação e edição. Isso constitui **editor inicial de Work**, não o editor catalográfico
completo. A capacidade operacional deve ser aferida pelos testes e evidências
disponíveis, sem confundi-la com homologação institucional. As seções históricas
deste README descrevem entregas no momento em que ocorreram; para planejar novos
trabalhos, utilizar o estado atual do código e os documentos abaixo.

A [proposta de execução M07](docs/architecture/m07-cataloging-editor.md) organiza
o desenvolvimento incremental de Work → Instance → Item, vínculos com Agent e
Subject, diagnóstico SHACL e ciclo editorial autorizado, reutilizando a API
FastAPI, a persistência PostgreSQL e as decisões BIBFRAME existentes. O escopo da
milestone deverá ser confirmado em issues específicas; documentação prospectiva
não significa funcionalidade já entregue.

**Referências funcionais:** MARVA (editor BIBFRAME), Sinopia (perfis declarativos),
Koha e FOLIO (fluxos bibliotecários) e [Open Library](https://openlibrary.org/)
(UX de obras, edições, autores, capas e navegação). Open Library é referência de
experiência e organização, não fonte do modelo canônico ou integração implementada.
BIBFRAME 2.0/RDF permanece a base do Libris, com extensões governadas.

Para contribuir na M07, consultar [AGENTS.md](AGENTS.md),
[contratos da API](docs/architecture/catalogographic-api-testing.md),
[perfis](docs/architecture/catalog-profiles.md) e
[plano do editor](docs/architecture/m07-cataloging-editor.md).
MARC21, reconciliação remota, inventário físico/circulação e busca Elasticsearch
permanecem entregas separadas, salvo decisão explícita registrada.
