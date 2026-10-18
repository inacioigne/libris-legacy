# Verificação M07.05 — 2026-10-18

## Ambiente

- Checkout local em `main`, commit inicial `0ae5136 M07.04`.
- Não havia alterações antes da execução (`git status --short` e `git diff --stat` vazios).
- Ambiente atual sem `node`, `npm`, `npx` ou `uv` no `PATH`.

## Implementação inspecionada

| Funcionalidade | Contrato real | Integração |
| --- | --- | --- |
| Estado e ETag | `GET B/editorial` | painel Work/Instance/Item |
| Revisões | `GET B/revisions` | `metadata_revision:read` |
| Diferença | `GET B/revisions/compare` | `SemanticDiff`, sem comparação textual RDF |
| Auditoria | `GET B/audit` | `metadata_audit:read` |
| Transições | sete `POST B/editorial/*` | BFF, confirmação, justificativa e If-Match |
| Pré-validação | `POST B/validation` | criação de Work, Instance e Item |

## Comandos executados

| Comando | Resultado |
| --- | --- |
| `git branch --show-current` | `main` |
| `git status --short` inicial | sem saída |
| `git diff --stat` inicial | sem saída |
| `git log -1 --oneline` | `0ae5136 M07.04` |
| `git diff --check` | aprovado |
| `npm run typecheck` em `apps/web` | bloqueado: `npm: command not found` |
| `bash scripts/check.sh` | bloqueado no primeiro passo: `uv: command not found` |

`npm run build`, testes web e testes API não foram executados por ausência das ferramentas. Nenhum teste PostgreSQL, migração, volume ou banco foi modificado. Repetir compilação TypeScript, cenários de navegador, BFF e PostgreSQL em ambiente com Node 24/npm e uv. Esta evidência não equivale a homologação OIDC/Keycloak nem aceite biblioteconômico.
