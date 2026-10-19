# Evidências M08.03 — 2026-10-19

Registro datado; não substitui evidências M08.01/M08.02.

## Estado inicial

- branch: `main`;
- commit: `281d933 M08.02`;
- `git status --short`: vazio;
- `git diff --stat`: vazio.

## Fonte externa

Em 2026-10-19, `curl` com TLS, redirects, timeout e limite tentou baixar tanto
`https://www.loc.gov/standards/codelists/languages.xml` quanto
`https://id.loc.gov/vocabulary/languages.jsonld`. Ambos responderam HTTP 403. Nenhum
artefato foi fingido como snapshot oficial. A fixture local é deliberadamente parcial
e serve apenas a testes determinísticos.

As páginas públicas da LC confirmaram o XML para aplicações, o RDF em id.loc.gov, a
edição 2007 atualizada continuamente e a relação histórica com ISO 639-2. A página
legal geral foi consultada, mas não foi tratada como licença específica do dataset.

## Comandos executados até esta revisão

| Comando | Resultado |
| --- | --- |
| inspeção Git obrigatória | sucesso; estado inicial acima |
| `.venv/bin/ruff check` nos arquivos M08.03 | sucesso após correções |
| `python3 -m py_compile` nos arquivos M08.03 | sucesso com Python 3.14.4 |
| `uv run pytest ...` | não executado: `uv` ausente no PATH |
| `.venv/bin/pytest ...` | não executado: shebang aponta para Python 3.13 removido |
| integração PostgreSQL | ainda não executada nesta etapa; depende de ambiente Python válido e Docker |

`py_compile` não substitui pytest/mypy. Skips não são contabilizados como aprovação.
O relatório será complementado ao fim da auditoria desta implementação, sem alterar
evidências históricas.
