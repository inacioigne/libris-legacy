# Diagnóstico do 422 no formulário Work — 2026-10-17

## Evidência e limites

O log fornecido registra `POST /api/v1/catalog/works` com status 422, sem o corpo
da resposta. Durante a investigação, o usuário confirmou ter preenchido título
e idioma como código/nome. O formulário envia o idioma como `language[].uri`;
códigos/nomes não são IRIs absolutas e são rejeitados pelo contrato da API.

A inspeção identificou perda de informação no frontend: `CatalogAccessError`
preserva Problem Details em `detail`, mas as ações retornavam apenas
`error.message`, cuja mensagem para 422 é `Catalog request failed`.

Uma execução local com `apps/api/.venv/bin/python`, sem banco ou requisições
de escrita, exercitou `WorkCreate.model_validate`, `create_to_graph` e
`prepare_document` com o perfil institucional utilizado pelo endpoint:

- Work com apenas título: passou DTO, RDF e SHACL.
- Idioma com URI `http://id.loc.gov/vocabulary/languages/por`: passou.
- Idioma com `uri: "pt"`: rejeitado pelo DTO em `language/0/uri`.
- Agente com nome no campo URI: rejeitado pelo DTO.

O caso de idioma inválido reproduz a entrada confirmada pelo usuário, embora
não tenha sido recuperado o corpo da resposta original. Essas execuções não
constituem teste de persistência ou homologação do fluxo no navegador.

## Ajuste

As ações de criação e edição agora extraem exclusivamente mensagens públicas,
identificadores de campo e o UUID de correlação do Problem Details no 422.
O formulário exibe esses diagnósticos e orienta o preenchimento das referências
com URIs absolutas, com textos de interface em pt/en. As mensagens autorais
da API são preservadas. Nenhum payload bruto de erro, token ou valor de entrada
é acrescentado ao estado do formulário.

O escopo corresponde ao feedback Problem Details/SHACL previsto nas issues
[M06 #52](https://github.com/inacioigne/libris/issues/52) e
[BFF #56](https://github.com/inacioigne/libris/issues/56), consultadas durante
a investigação. Não houve alteração do contrato v1, perfis ou SHACL.

## Verificações

- `npm test` com Node 24.16.0 fora do sandbox: **171 testes passaram**, sem skips.
  Inclui três testes novos para diagnóstico por campo, diagnóstico global,
  fallback localizado e exclusão de campos privados do estado retornado.
- `npm run build` fora do sandbox: **passou**.
- `npm run typecheck`: **passou**.
- `git diff --check`: **passou**.
- Etapas API de `bash scripts/check.sh` fora do sandbox: artefatos contratuais
  e OpenAPI sem drift; **2084 testes passaram**, sem skips; Ruff e mypy passaram.
  A suíte emitiu 513 warnings, incluindo deprecações de Starlette/RDFLib.
- `bash scripts/check.sh` completo fora do sandbox: **passou**, incluindo nova
  execução dos 171 testes web, build, typecheck e validação estática do Compose.
  Não foram executados testes de integração PostgreSQL ou navegador.

As primeiras tentativas dentro do sandbox tiveram falhas de ambiente:
`npm test` falhou no arquivo de autenticação, o build não conseguiu interpretar
a saída de `tsc --showConfig`, e `scripts/check.sh` não iniciou o uv/Snap.
Os testes web e o build acima foram reexecutados fora dessas restrições.
