# Workspace de catalogação

Implementação técnica de 2026-10-21 para Work, Instance e Item. Consulte o
[ADR 0042](../decisions/0042-cataloging-workspace.md).

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
de auditoria. Falhas deixam o rascunho inalterado. As duas rotas aceitam apenas
`works`, `instances` e `items`, requerem sessão OIDC, `bibliographic:update` e
o ETag editorial atual.

As rotas editoriais v1 permanecem disponíveis por compatibilidade, mas não são
exibidas como etapas do workspace de Work, Instance e Item. Agent e Subject ainda
usam o ciclo anterior nesta entrega.
