# ADR 0042 — Workspace simplificado de catalogação

Data: 2026-10-21. Status: aceito para implementação técnica. A aceitação
biblioteconômica e a homologação institucional continuam separadas.

## Contexto

O contrato editorial v1 exige submit, approve e publish, além de return,
withdraw, republish e archive. Esse fluxo separa catalogador, revisor e
publicador, mas impõe etapas que não pertencem ao fluxo operacional desejado:
o catalogador precisa poder interromper o trabalho, retomar o mesmo rascunho,
validá-lo e publicá-lo sem encaminhamento intermediário.

## Decisão

Work, Instance e Item passam a oferecer um workspace com três ações visíveis:

1. **Salvar rascunho** usa POST/PATCH já existentes, cria revisões imutáveis e
   mantém o estado efetivo `draft`.
2. **Validar catalogação** usa `POST /api/v1/catalog/{collection}/{id}/workspace/validate`.
   Valida o snapshot persistido, perfil, SHACL, vocabulários e referências sob
   o ETag atual, sem alterar estado nem afirmar publicação futura.
3. **Publicar** usa `POST /api/v1/catalog/{collection}/{id}/workspace/publish`.
   Repete toda a validação e promove atomicamente a revisão corrente de `draft`
   para `published`, atualizando os ponteiros de aprovação/publicação apenas
   como metadados internos de compatibilidade.

Ambas as ações exigem `bibliographic:update`, contexto humano OIDC e `If-Match`.
A API, não o frontend, decide autorização, conformidade e concorrência. Publicar
não confia em uma validação anterior: a revisão é revalidada na mesma transação.

## Compatibilidade

As rotas e estados editoriais v1 são preservados para registros históricos,
autoridades e consumidores existentes, mas deixam de ser o fluxo da interface
bibliográfica. Registros legados em `in_review` ou `approved` devem concluir ou
retornar pelo contrato legado; o workspace publica somente `draft`.

O snapshot OpenAPI v1 permanece imutável. O contrato corrente está em
`catalogographic-openapi-v2-workspace.json`. A migração 0009 amplia somente a
restrição da auditoria para admitir o evento `record.published` de `draft` para
`published`; o downgrade recusa execução se tais evidências já existirem.

## Consequências

- O catalogador pode salvar e retomar rascunhos indefinidamente.
- Validar é uma consulta forte do estado persistido e não um novo estado.
- Publicar continua protegido por CAS, transação, validação e auditoria.
- A revisão pública anterior permanece visível enquanto uma nova revisão está
  em rascunho; a publicação troca o ponteiro público de forma explícita.
- Agent e Subject mantêm temporariamente o fluxo editorial legado, pois a nova
  autorização foi limitada ao workspace bibliográfico.
