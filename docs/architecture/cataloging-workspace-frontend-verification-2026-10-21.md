# Verificação do frontend do workspace de catalogação — 2026-10-21

Esta execução atualiza a interface administrativa de Work, Instance e Item para
o fluxo aceito no [ADR 0042](../decisions/0042-cataloging-workspace.md), sem mudar
o contrato HTTP, o cliente OpenAPI gerado, perfis, snapshots ou persistência.

## Superfície verificada no código

- Os formulários pt/en identificam POST/PATCH como salvamento de rascunho.
- O painel dos três recursos apresenta a sequência salvar, validar e publicar,
  distinguindo validação sem mudança de estado da publicação transacional.
- Validar e publicar continuam passando pelo BFF, proteção de origem, sessão OIDC,
  permissão `bibliographic:update` e ETag/If-Match.
- A publicação possui confirmação específica e a API repete a validação completa;
  a interface não trata uma validação anterior como autorização durável.
- Registros históricos em `in_review` ou `approved` mostram orientação de
  compatibilidade e somente as ações legadas permitidas pelo estado e pela sessão.
- O teste BFF cobre as rotas de validação e publicação para `works`, `instances`
  e `items`, incluindo Bearer server-side, método POST e ETag exato.
- Os testes de navegador existentes foram alinhados aos novos nomes acessíveis dos
  botões de salvamento, em português e inglês.

## Comandos executados

```text
git diff --check
```

Resultado: concluído sem erros.

```text
npm run typecheck
bash scripts/check.sh
```

Resultado: bloqueados neste ambiente antes da execução das suítes. `npm`/Node.js
não estão disponíveis e o gate encerrou em `scripts/check.sh: line 6: uv: command
not found`. Portanto, esta evidência não afirma build, testes automatizados,
PostgreSQL, navegador real, Keycloak ou homologação institucional.

Também não foi possível consultar issues/milestones remotos porque o executável
`gh` não está instalado. O escopo foi conferido contra o checkout local, o histórico
Git, o ADR 0042 e o contrato OpenAPI v2 já gerado.
