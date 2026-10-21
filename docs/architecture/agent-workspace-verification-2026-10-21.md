# Verificação do workspace simplificado de Agent — 2026-10-21

Esta execução verifica a substituição do ciclo editorial visual e HTTP de Agent
pelo mesmo fluxo operacional da catalogação: salvar rascunho, validar e publicar.
O [ADR 0043](../decisions/0043-agent-workspace.md) registra a decisão. Relatórios
anteriores permanecem evidências históricas do comportamento que existia antes
desta alteração.

## Escopo comprovado

- POST/PATCH de Agent continuam salvando revisões em `draft`, com ETag/If-Match;
- `workspace/validate` e `workspace/publish` aceitam `agents` e exigem
  `authority:manage` tanto no BFF quanto na API;
- publicar revalida a revisão corrente e usa a transação/auditoria já existente;
- os sete comandos `editorial/{command}` não aceitam mais `agents`;
- a tela de detalhe não apresenta submissão, aprovação, devolução, retirada,
  republicação ou arquivamento;
- Subject e a compatibilidade editorial dos demais tipos não foram alterados;
- a baseline OpenAPI v2 foi preservada e a v3 passou a ser a fonte do cliente
  TypeScript gerado.

## Execuções

| Comando | Resultado |
| --- | --- |
| `uv run python ../../scripts/generate_catalogographic_contract.py --check` | 2 artefatos verificados |
| `uv run python ../../scripts/generate_catalogographic_openapi.py --check` | baseline v3 sem drift |
| `uv run pytest -q` | 2170 aprovados; 514 avisos de depreciação; 532,80 s |
| `uv run mypy src` | 114 arquivos sem erros |
| Ruff nos arquivos Python alterados | aprovado |
| `npm test` em imagem local Node 24.22.1 | 281 aprovados; OpenAPI client sem drift |
| `npm run typecheck` na mesma imagem | aprovado |
| `npm run build` na mesma imagem | aprovado; 32 páginas e rotas dinâmicas geradas |
| `docker compose --env-file .env.example config --quiet` com credenciais efêmeras | aprovado; nenhum serviço iniciado |
| `git diff --check` | aprovado |

O `ruff check` global usado por `scripts/check.sh` encontrou 61 violações I001 de
ordenação de imports já presentes em testes fora deste escopo. Elas não foram
corrigidas mecanicamente para evitar misturar uma limpeza ampla com esta mudança;
a seleção de arquivos alterados passou. O script integral também não pôde usar
`npm` diretamente no host, pois Node/npm não estavam no PATH; as três verificações
web foram executadas na imagem local `libris-web:latest`, com Node 24.22.1.

## Limites

Não houve navegador real, Keycloak real nem PostgreSQL descartável configurado
especificamente para esta execução. A suíte padrão não comprova integração externa.
Agents históricos em `in_review` ou `approved` continuam preservados e precisam
de retorno deliberado a `draft` antes do workspace; não houve migração ou reescrita
de estados. A consulta ao estado vigente de issues/milestones do GitHub foi tentada,
mas o conector não retornou conteúdo e o CLI `gh` não está instalado; portanto esta
evidência não afirma atualização ou fechamento de issue.
