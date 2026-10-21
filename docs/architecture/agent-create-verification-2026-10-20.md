# Cadastro administrativo de Agent — 2026-10-20

As páginas `/pt/admin/authority/new` e `/en/admin/authority/new` apresentam um
formulário de criação ligado a `POST /api/v1/catalog/agents`. O BFF usa o cliente
OpenAPI gerado e a API existente, que mantém persistência, revisão, auditoria e
estado editorial na transação já implementada. Não há nova tabela ou contrato.

## Campos e comportamento

- Tipos `Agent`, `Person` e `Organization`.
- Nomes autorizados multilíngues, obrigatórios, um por idioma, incluindo o sem
  idioma; variantes repetíveis no mesmo idioma.
- Identificadores `Identifier`, `Local` e `Isbn`, com fontes por URI.
- Referências externas, proveniência e autoridades relacionadas por URI.
  O formulário de criação limita autoridades relacionadas à representação
  `ResourceReference` v1; não infere localidade, não converte referências
  estruturadas existentes e não realiza reconciliação remota.

Página, server action e BFF verificam `authority:manage`. A ação verifica a
origem com a proteção existente; a sessão e o Bearer permanecem no servidor,
e o FastAPI continua responsável pela autorização e validade definitivas.
O parser do formulário não aceita UUID, URI própria, ator ou permissões no
payload. UUID e URI própria são retornados pela API. Criação não usa `If-Match`;
o contrato o exige nas atualizações, que não fazem parte deste incremento.

Depois do sucesso, o formulário apresenta confirmação, UUID e URI retornados e
desabilita uma nova submissão do mesmo formulário. O botão de novo cadastro abre
um formulário vazio. Falhas preservam o estado local, incluindo idioma, ordem,
identificadores e referências, com diagnóstico e orientações para 401/403.
Não há repetição automática de POST nem publicação automática.

A listagem mostra o botão de cadastro para sessões com `authority:manage`.
Se uma sessão pode cadastrar, mas não possui ambas as permissões de leitura
editorial (`authority:read` e `metadata_revision:read`), a entrada de autoridades
encaminha ao cadastro. A confirmação não oferece link de consulta nesse caso;
o formulário também pode ser acessado diretamente.

## Verificação e limites

- Node 24.16.0.
- `npm test`: 269 testes aprovados, incluindo o check de drift OpenAPI.
- `npm run typecheck`: aprovado.
- `npm run build`: aprovado, com `/[locale]/admin/authority/new` como rota dinâmica.
- `git diff --check`: aprovado.
- Suíte web e build executados fora do sandbox para evitar as limitações
  de subprocessos e servidores locais observadas na verificação da listagem.

Os testes novos exercitam o parser, nomes/idiomas, valores inválidos, identidade
gerada pela API, POST autenticado com payload completo, permissão de gestor
sem leitura editorial, bloqueio de catalogador, sessão ausente e preservação de
401/403/409/422/503 sem retry. Os testes BFF usam respostas HTTP simuladas.

`bash scripts/check.sh` foi executado e interrompido no primeiro `uv` por erro
de `snap-confine`/AppArmor. Isso não comprova execução da suíte API, Ruff, mypy
ou validação Docker Compose. Não houve execução de navegador ou integração
real desta nova tela com Keycloak e PostgreSQL. Os relatórios anteriores
permanecem evidências históricas independentes.
