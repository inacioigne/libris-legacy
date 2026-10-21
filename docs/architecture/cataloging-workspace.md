# Workspace de catalogação

Implementação técnica de 2026-10-21 para Work, Instance e Item, estendida na
mesma data para Agent. Consulte o [ADR 0042](../decisions/0042-cataloging-workspace.md)
e o [ADR 0043](../decisions/0043-agent-workspace.md).

```text
criar/editar ──salvar──> draft ──validar──> draft ──publicar──> published
                           ▲                    │
                           └──── retomar ───────┘
```

Salvar usa os POST/PATCH catalográficos existentes. Cada salvamento bem-sucedido
gera uma revisão imutável; ETag/If-Match impede sobrescrita concorrente. Um
registro já publicado que recebe nova revisão conserva a revisão pública anterior
e abre uma candidata interna em `draft`.

`POST .../workspace/validate` valida a revisão corrente completa e retorna
`{"ready": true, "revision": N}`. Violações usam Problem Details e diagnósticos
catalográficos existentes. A chamada não grava aprovação nem estado intermediário.

`POST .../workspace/publish` executa a mesma validação completa e, somente se ela
for concluída, publica a revisão corrente na mesma transação do estado e do evento
de auditoria. Falhas deixam o rascunho inalterado. As duas rotas aceitam `works`,
`instances`, `items` e `agents`, requerem sessão OIDC e o ETag editorial atual.
Recursos bibliográficos exigem `bibliographic:update`; Agent exige
`authority:manage`.

As rotas editoriais v1 permanecem disponíveis por compatibilidade para Work,
Instance, Item e Subject, mas não são exibidas como etapas do workspace
bibliográfico. Agent deixa de ser aceito nos sete comandos legados; seu fluxo
operacional é somente salvar rascunho, validar e publicar. Histórico, auditoria e
projeção pública continuam preservados.
