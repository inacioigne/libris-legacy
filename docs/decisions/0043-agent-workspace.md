# ADR 0043 — Workspace simplificado de Agent

Data: 2026-10-21. Status: aceito para implementação técnica. A aceitação
biblioteconômica e a homologação institucional continuam separadas.

## Contexto

Agent já reutilizava snapshots, revisões, auditoria e o ciclo editorial v1 do
catálogo. A interface, porém, expunha submissão, devolução, aprovação, retirada,
republicação e arquivamento, enquanto o fluxo operacional requerido é o mesmo
workspace direto adotado por Work, Instance e Item no ADR 0042.

Agent é uma autoridade bibliográfica independente da identidade OIDC. A
simplificação não transforma `authority:manage` em gestão de usuários nem altera
o modelo de Subject.

## Decisão

Agent passa a ter somente três ações operacionais:

1. salvar rascunho pelos POST/PATCH catalográficos existentes;
2. validar a revisão persistida por `POST .../agents/{id}/workspace/validate`;
3. publicar com revalidação transacional por
   `POST .../agents/{id}/workspace/publish`.

Validar e publicar exigem `authority:manage`, contexto humano OIDC e `If-Match`.
Publicar aceita somente `draft`, revalida perfil, SHACL e referências dentro da
transação e promove a revisão corrente diretamente para `published`. Os ponteiros
de aprovação permanecem metadados internos de compatibilidade da persistência.

Os sete endpoints genéricos `editorial/{command}` deixam de aceitar a coleção
`agents`. Work, Instance, Item e Subject preservam sua compatibilidade existente.
O GET de estado editorial, revisões e auditoria é mantido porque fornece ETag,
concorrência, rastreabilidade e compatibilidade de dados, não etapas de trabalho.

## Contrato e compatibilidade

O snapshot OpenAPI v2 é preservado como evidência histórica. O contrato corrente
passa a ser `catalogographic-openapi-v3-agent-workspace.json`; clientes são
regenerados a partir dele, sem edição manual.

Agents históricos em `in_review` ou `approved` não são publicados pelo workspace:
eles precisam ser devolvidos a `draft` por uma migração ou procedimento de
compatibilidade deliberado antes de usar o novo fluxo. Esta decisão não reescreve
estados, eventos ou snapshots existentes e não cria migração de banco.

## Consequências

- o formulário continua sem publicar automaticamente;
- falhas de validação ou concorrência preservam o rascunho e a entrada local;
- a publicação pública anterior continua visível durante uma nova revisão;
- permissões editoriais legadas não autorizam o workspace de Agent;
- Subject permanece no ciclo anterior até decisão própria.
