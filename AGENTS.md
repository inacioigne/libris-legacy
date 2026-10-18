# Convenções do Libris

## Contexto e fontes de verdade
- Leia `README.md`, `docs/architecture/overview.md`, `docs/architecture/domains.md`, `docs/architecture/catalogographic-contract.md` e os ADRs pertinentes antes de alterar contratos ou arquitetura.
- Consulte as issues/milestones vigentes do GitHub. O estado de cada entrega é comprovado pelo código, testes e evidências, não apenas por uma issue fechada.
- M01/M02 estabeleceram a infraestrutura e o núcleo semântico. M03 já contém contratos catalográficos, perfis, SHACL, identificadores, serialização, relações, auditoria, revisões e persistência editorial **internos**, com migrações experimentais. M04 iniciou identidade OIDC; integração real com Keycloak e login Next.js ainda não estão homologados. Não confunda contrato/serviço interno com API pública ou funcionalidade operacional disponível.
- Mantenha os relatórios históricos de verificação como evidência datada. Documente novas execuções em separado; não sobrescreva resultados anteriores nem anuncie aprovação sem rodar os comandos.

## Arquitetura e limites
- Monólito modular FastAPI em `apps/api/src/libris/modules/<domain>`; Next.js App Router em `apps/web`; PostgreSQL como fonte de verdade, Elasticsearch como futura projeção reconstruível.
- Use `api`, `application`, `domain`, `infrastructure` onde houver código concreto: domínio independente de FastAPI/HTTP; serviços de aplicação coordenam invariantes e transações. Um módulo não acessa diretamente tabelas/repositórios de outro; use contratos explícitos.
- Não confunda BIBFRAME Work/Instance/Item com inventário físico, empréstimo ou disponibilidade. O modelo bibliográfico não administra circulação.
- Evite microsserviços, triplestore, filas, novos frameworks internos e abstrações vazias sem necessidade demonstrada e ADR. A introdução de indexação assíncrona exigirá decisão de consistência/outbox antes da primeira integração real.
- Não declare esquema bibliográfico definitivo: `experimental_semantic_*` e migrações 0001–0004 são experimentais. Não usar `create_all` ou executar migrações no startup. Migrações Alembic novas devem ser incrementais, preservar legado e incluir testes upgrade/downgrade seguros.

## Semântica e integridade
- BIBFRAME 2.0 e RDF/Linked Data são centrais; outras ontologias entram pelo mecanismo governado de extensões e releases, sem alterar unilateralmente perfis/SHACL históricos.
- RDFLib Graph: compare grafos por isomorfismo, não texto serializado. JSON-LD/Turtle passam pelo parser limitado, sem importar contextos remotos.
- Perfis declarativos JSON e shapes SHACL são artefatos versionados independentes. Preserve URIs armazenadas, identidade UUID, snapshots imutáveis, proveniência e revisões independentes.
- Escritas usam concorrência otimista/CAS e revisão esperada; publicação seleciona a revisão aprovada, sem tornar candidata visível automaticamente. Edições, estados e auditoria devem compartilhar transação quando exigido pelos contratos M03; não reimplemente garantias já existentes.
- Contratos DTO, diagnósticos e Problem Details devem permanecer compatíveis com a especificação catalográfica v1 e fixtures existentes. Mudanças incompatíveis requerem versão, ADR e testes.

## Identidade e segurança (M04)
- FastAPI é Resource Server OIDC: valida access token assinado, issuer/audience, JWKS, permissões e ator; o provedor externo gerencia credenciais. O módulo não implementa login, refresh ou senhas. `/api/v1/identity/me` é protegido; `/health` é liveness público.
- Identidade/autorização não se confundem com autoridades bibliográficas. Papéis no frontend não substituem autorização no backend. Reforce deny-by-default e a política efetiva por operação; não confie em actor_id, roles ou permissões vindos de payload/header do usuário.
- Se implementar login Next.js, use Authorization Code + PKCE e sessão do lado servidor/cookies apropriados, sem tokens em localStorage, URLs públicas, logs ou `NEXT_PUBLIC_*`. Trate CSRF, callback, state, redirecionamentos e expiração conforme a issue M04 pertinente.
- Não afirme integração Keycloak real, segurança ponta a ponta ou homologação sem execução documentada. Preserve `actor_id` histórico nulo e não invente identidades retroativas; diferencie auditoria bibliográfica de eventos de segurança.
- Configure segredos somente por ambiente; nunca versionar `.env`, chaves, tokens, credenciais, dados pessoais ou caches. Docker Compose atual é apenas de desenvolvimento local, não produção.

## Código, frontend e qualidade
- Python `>=3.13,<3.15` (referência 3.13), SQLAlchemy 2 async, Pydantic 2, tipagem obrigatória e mypy strict; TypeScript strict e Node.js 24. Código/identificadores em inglês, documentação em português. Não use `Any` para mascarar erros.
- Respeite a internacionalização existente em `apps/web/src/i18n/messages.ts`, rotas `/pt` e `/en`, HTML semântico e acessibilidade. Prefira primitivas Radix; shadcn/ui apenas quando justificado pelo controle real.
- Dependências exigem atualização concomitante de `apps/api/uv.lock` ou `apps/web/package-lock.json`; imagens Docker usam locks.
- Após alterar código, execute `bash scripts/check.sh` quando as ferramentas estiverem disponíveis. Na API: `uv run pytest`, `uv run ruff check . ../../tests`, `uv run mypy src`. No frontend: `npm run build` e `npm run typecheck`. Registre quais rodaram e quais ficaram bloqueados.
- A suíte API padrão não exige serviços externos. Testes de integração de persistência usam PostgreSQL real **descartável e isolado**, não SQLite; variáveis de teste ausentes podem causar skips e não comprovam integração. Não rode downgrade, remoção de volumes ou mutações em bases de trabalho.
- Preserve arquivos e contratos existentes. Para mudanças estruturais, crie ADR; para mudanças de escopo, atualize documentação, testes e a issue correspondente. Não escolha licença unilateralmente.


## Diretrizes para M07 — Editor de catalogação (2026-10-18)
- M06 (#52–#59) entregou cliente OpenAPI gerado, BFF catalográfico com OIDC, listagem/detalhe e formulário inicial de Work. Antes de iniciar uma issue M07, conferir código, testes e evidências datadas da M06.07; não tratar o formulário Work como editor completo. A milestone M07 ainda precisa de desdobramento e validação de escopo no GitHub.
- Leia [o plano da M07](docs/architecture/m07-cataloging-editor.md), [contrato HTTP](docs/architecture/catalogographic-api-testing.md), [perfis](docs/architecture/catalog-profiles.md), [ciclo editorial](docs/architecture/catalogographic-editorial-http.md), [autorização](docs/architecture/operation-authorization.md) e [decisões de persistência](docs/decisions/0040-catalogographic-persistence-stabilization.md).
- Construir incrementalmente sobre `apps/web/src/components/catalog/WorkEditorForm.tsx`, `apps/web/src/server/catalog/` e `apps/web/src/generated/`. Os arquivos gerados são artefatos: modificar a fonte OpenAPI e regenerar por comandos existentes; não editar clientes gerados manualmente.
- Separar Work (obra intelectual), Instance (manifestação/edição), bf:Item (recurso semântico da instância) e as futuras operações de inventário/circulação. Criar/vincular instâncias e itens por APIs reais e referências explícitas; não duplicar Work nem inferir uma hierarquia automaticamente de uma tela.
- Preferir formulários por tarefa e tipo de recurso, orientados por perfis versionados já registrados e pelos DTOs reais. Não criar um editor RDF universal, engine de formulários ou camada nova de persistência como pré-requisito; perfis 2.0 não estão automaticamente adotados pela escrita. Se um perfil não puder ser representado nos DTOs v1, documentar o impedimento e exigir decisão/versão antes de ampliar o contrato.
- Repetições e elementos aninhados devem preservar idioma, datatype, URIs, identificadores, papéis, propriedades estendidas e distinção entre omitido/null/[] no PATCH. Não destruir dados não exibidos pelo formulário; quando não for possível round-trip sem perdas, bloquear a operação ou limitar explicitamente o escopo editável.
- Não converter indiscriminadamente links de Agent/Subject em texto simples. Usar seleção/vinculação de autoridades existentes quando a API suportar; não afirmar reconciliação remota automática. Identidades de autoridades são independentes do OIDC.
- Em toda escrita, manter sessão BFF server-side, autorização efetiva FastAPI, proteção CSRF, `If-Match`/ETag e tratamento acionável de 401/403/409/412/422; em conflito, preservar entrada local e oferecer recarga/conciliação explícita. Pré-validação SHACL ajuda a UX, mas o backend decide validade e transições.
- Distinguir salvar rascunho, submeter, aprovar e publicar segundo as permissões e comandos existentes; não expor versões não publicadas no OPAC. Testar barreiras entre perfis catalogador/revisor/publicador e leitura pública anônima.
- Referências de UX: MARVA e Sinopia para editor Linked Data; Koha/FOLIO para operações bibliotecárias; Open Library para páginas de obras/edições, autores, capas e navegação. Não importar o modelo de dados ou o código visual do Open Library como contrato BIBFRAME, nem prometer integração com sua API.
- Critérios por incrementos M07: testes de componentes, i18n pt/en e acessibilidade; contrato BFF/API e diagnósticos; PostgreSQL descartável com integridade/revisões; fluxo navegador real quando possível. Registrar comandos/limites sem reescrever relatórios históricos. Não antecipar MARC21 (M08), Elasticsearch/OPAC (M09), circulação, triplestore ou dependências sem issue/ADR.
