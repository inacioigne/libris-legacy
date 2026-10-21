# Administração e fluxo editorial de Subject — 2026-10-20

As rotas `/{locale}/admin/authority/subjects`, `/new`, `/{id}` e `/{id}/edit`
oferecem listagem, criação, detalhe, edição e governança editorial bilíngue para
Subject. A implementação reutiliza os DTOs v1, o cliente OpenAPI gerado, o BFF
OIDC e a persistência catalográfica existentes.

O formulário preserva rótulos preferidos e alternativos com idioma, esquemas e
referências URI de conceitos mais amplos, relacionados, externos e de
proveniência. Rótulo preferido e esquema são obrigatórios. UUID e URI própria
são gerados pela API e permanecem imutáveis.

A edição usa `If-Match`; em 412 mantém a entrada local e exige comparação e
escolha explícita antes de adotar o novo ETag. Referências estruturadas
`broader`/`related` sem URI bloqueiam a edição para evitar achatamento ou perda.
O detalhe conserva a representação completa dessas referências.

O painel editorial usa a coleção real `subjects` e oferece somente as ações
compatíveis com estado e permissões efetivas: submissão, aprovação, devolução,
publicação, retirada, republicação e arquivamento. Salvar não publica. Leitura
editorial exige `authority:read` e `metadata_revision:read`; criação/edição exige
`authority:manage`; as transições continuam autorizadas individualmente pelo
FastAPI.

## Verificação desta execução

- `git diff --check`: aprovado.
- Node/npm e uv não estavam disponíveis no `PATH`; typecheck, testes, build e
  `scripts/check.sh` não puderam produzir nova evidência executada.
- Foram acrescentados testes unitários do parser/round-trip e testes BFF de
  POST/GET/PATCH com ETag, mas eles ainda precisam ser executados.
- Não houve ensaio de navegador, PostgreSQL descartável ou Keycloak real.

Os relatórios históricos permanecem inalterados; este documento não afirma
homologação institucional ou operacional.
