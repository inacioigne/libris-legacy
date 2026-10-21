# Consulta administrativa de Agents — 2026-10-20

A página `/pt/admin/authority` e sua equivalente `/en/admin/authority` consultam
`GET /api/v1/catalog/agents` pelo BFF e cliente OpenAPI existentes. Apresentam os
nomes autorizados com seus idiomas, tipo traduzido, UUID e URI armazenada.
Incluem filtros exatos de UUID/URI, paginação de 20 registros com cursor opaco,
retorno à primeira página, estado vazio e erro com nova tentativa.

O acesso exige `authority:read` e `metadata_revision:read`; o BFF e o FastAPI
mantêm suas verificações. Sessões ausentes/expiradas e acesso negado encaminham
às páginas existentes. O link Agentes na entrada administrativa aponta à listagem.

## Verificação executada

- Node 24.16.0.
- `npm test`: 242 testes aprovados; cliente OpenAPI atualizado.
- `npm run typecheck`: aprovado.
- `npm run build`: aprovado fora do sandbox, incluindo a rota dinâmica
  `/[locale]/admin/authority`.
- `git diff --check`: aprovado.
- `bash scripts/check.sh`: interrompido no primeiro comando `uv` pelo erro de
  confinamento `snap-confine`/AppArmor. Não comprova execução da suíte API,
  Ruff, mypy ou validação Docker Compose.

A primeira tentativa de `npm test` no sandbox teve falha no arquivo de
autenticação; a execução isolada fora do sandbox passou em seus 71 testes e a
suíte completa fora do sandbox passou em seus 242 testes. A primeira tentativa
de build no sandbox falhou ao interpretar a saída de `tsc --showConfig`.

Não houve execução de navegador ou homologação com Keycloak/PostgreSQL reais.
As verificações BFF existentes usam fixtures. A entrega permite consulta; não
adiciona criação/edição de autoridades, busca textual global ou reconciliação.
