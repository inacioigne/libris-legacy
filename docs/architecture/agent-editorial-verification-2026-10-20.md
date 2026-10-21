# Edição e fluxo editorial de Agent — 2026-10-20

As rotas administrativas bilíngues `/{locale}/admin/authority/{id}` e
`/{locale}/admin/authority/{id}/edit` completam a navegação iniciada pela
listagem de Agents. Elas reutilizam os DTOs v1, o cliente OpenAPI gerado, o BFF
OIDC e as rotas catalográficas existentes; não introduzem contrato, tabela ou
estado editorial paralelo.

## Comportamento implementado

- O nome autorizado na listagem conduz ao detalhe do Agent.
- O detalhe apresenta identidade persistente, tipo, nomes e referências, além
  do painel editorial compartilhado com Work, Instance e Item.
- O painel usa a coleção `agents` nas operações reais de estado, revisões,
  auditoria, comparação, submissão, aprovação, devolução, publicação, retirada,
  republicação e arquivamento. Os botões dependem das permissões efetivas e do
  estado atual; salvar uma edição não publica o registro.
- A edição exige `authority:manage`, `authority:read` e
  `metadata_revision:read`, mantém tipo, UUID e URI imutáveis e envia
  `If-Match` com o ETag observado.
- Em 412, a entrada local permanece no navegador. A pessoa deve carregar a
  versão corrente, comparar os payloads e escolher explicitamente entre manter
  a entrada local com o novo ETag ou adotar a versão remota.
- Agents com referências relacionadas estruturadas que não possuam URI não são
  reduzidos a texto: o formulário bloqueia a edição para evitar perda silenciosa.
  Propriedades RDF fora da projeção DTO continuam preservadas pelo PATCH da API.

Leitura de detalhe/estado de Agent segue `authority:read` mais
`metadata_revision:read`. Edição segue `authority:manage`; cada transição
editorial continua autorizada separadamente (`editorial:submit`,
`editorial:approve`, `editorial:publish` etc.) pelo FastAPI. O Bearer e a sessão
permanecem no servidor e a origem das mutações usa a proteção BFF existente.

## Verificação desta execução

- `git diff --check`: aprovado.
- `npm run typecheck`: bloqueado porque `npm` e `node` não estavam disponíveis
  no `PATH` do ambiente desta sessão.
- `bash scripts/check.sh`: bloqueado no primeiro comando porque `uv` não estava
  disponível no `PATH`; portanto não comprova API, Ruff, mypy ou Compose.
- Testes e build web não foram executados pela mesma ausência do runtime Node.
- Não houve execução de navegador, PostgreSQL descartável ou Keycloak real.

Foram acrescentados casos unitários para projeção editável, identidade imutável,
bloqueio de referência sem round-trip e GET/PATCH BFF com ETag exato. Eles são
evidência preparada, não resultado aprovado, enquanto a suíte não for executada.
Os relatórios anteriores de criação e listagem permanecem evidências históricas
independentes e não foram sobrescritos.
