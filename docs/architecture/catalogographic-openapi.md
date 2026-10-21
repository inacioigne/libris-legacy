# Baseline OpenAPI operacional v1

O snapshot histórico v1 é [M08.04](catalogographic-openapi-v1-m0804.json). O contrato
operacional corrente é o [workspace v2](catalogographic-openapi-v2-workspace.json), com
as rotas reais de vocabulários, catálogo, identidade e health produzidas pela fábrica
FastAPI. O snapshot original [M06.01](catalogographic-openapi-v1.json) permanece
inalterado como evidência histórica.
Complementa, sem substituir, o [JSON Schema documental 1.0.0](catalogographic-contract-v1.schema.json).
`info.version=0.1.0` é versão do aplicativo; `/api/v1` é caminho existente;
`urn:libris:contract:catalogographic:v1` identifica a especificação documental.
A baseline registra operações existentes, sem criar rotas, autenticação ou
seleção de versão por si só. Os contratos e limites das rotas M08.04 estão em
[vocabulary-api-m0804.md](vocabulary-api-m0804.md).

## Gerar, verificar e revisar

Com dependências fixadas, a partir de `apps/api`:

```bash
uv sync --locked --python 3.13
uv run python ../../scripts/generate_catalogographic_contract.py --check
uv run python ../../scripts/generate_catalogographic_openapi.py --check
# Somente criação inicial em caminho ausente. Nunca sobrescreve, mesmo idêntico:
uv run python ../../scripts/generate_catalogographic_openapi.py --generate
# Candidato para revisão (usar caminho ainda inexistente):
uv run python ../../scripts/generate_catalogographic_openapi.py --generate --output /tmp/libris-openapi-candidate.json
uv run pytest ../../tests/api -q
```

O gerador isola ambiente e `.env`, inclusive na importação de `main` (que também
constrói a aplicação ASGI global). Usa Settings reais com ambiente test, não inicia
lifespan e não acessa PostgreSQL, Keycloak, Elasticsearch ou rede. Não substitui
routers/serviços nem filtra caminhos, componentes ou campos para facilitar igualdade.
JSON UTF-8, indentação de dois espaços, chaves ordenadas, newline final; listas
preservam ordem significativa. As dependências vêm de `uv.lock`.

`--check` não escreve: compara os bytes canônicos completos, retorna 1 para ausência,
JSON inválido, referência quebrada ou qualquer divergência e imprime unified diff
baseline/runtime. `--generate` usa abertura exclusiva; um arquivo existente é
preservado. Não existe atualização automática do esperado. A validação de integridade
resolve referências locais; o teste verifica também alcançabilidade dos componentes.
Os componentes automáticos FastAPI HTTPValidationError/ValidationError podem estar
inalcançáveis porque os handlers reais substituem 422 por ProblemDetails; são mantidos
como produzidos pela aplicação, sem limpeza artificial do snapshot.

O workflow `.github/workflows/catalogographic-contract.yml` prepara Python 3.13/uv,
sincroniza o lock, verifica as duas baselines e executa toda a suíte offline API,
Ruff e mypy. `scripts/check.sh` executa os mesmos checks antes da suíte global web
(Node 24). CI não substitui integração PostgreSQL: executar separadamente o runner
isolado documentado em [testes de API](catalogographic-api-testing.md).

## Compatibilidade e diferenças de representação

A [política técnica ADR 0029](../decisions/0029-catalogographic-contract-versioning.md)
exige evidências de interoperabilidade nas duas direções. Qualquer drift falha,
inclusive documental; sua aprovação é humana. Adicionar campo opcional ou valor de
enum pode romper leitores com `extra=forbid`/enum fechado. Restringir ou relaxar tipos,
null, defaults, cardinalidades, autenticação, status ou semântica exige análise de
payloads e HTTP, além do diff. Alterações incompatíveis bloqueiam a mesma superfície;
não se legitimam pela regeneração da baseline. Releases históricas são retidas.

FastAPI serializa OpenAPI com exclusão de None: `default: null` de Pydantic pode
estar ausente nos componentes, embora omissão continue resultando em None runtime.
O teste compara os quinze modelos aplicando a serialização **do próprio FastAPI**,
e testa fixtures e validação runtime. O JSON Schema documental preserva defaults
originais. Invariantes de IRI, idioma único, Title/Local, coerência de referências,
SHACL e estado editorial continuam no código/testes, sem alegar expressão completa
em JSON Schema. PATCH omite para preservar, null remove apenas opcional, lista
substitui integralmente; não usar exclude_none para montar PATCH.

`If-Match` é obrigatório no OpenAPI das escritas existentes: ausência continua
**428**, sintaxe inválida **400**, valor forte obsoleto **412**, conflito de estado
**409**. A dependência aceita ausência somente para produzir 428 em vez do 422
padrão; não houve mudança do parser ou CAS. Headers ETag, Location, Vary,
WWW-Authenticate e X-Request-ID descrevem emissões reais. Vary inclui Accept, podendo
incluir Origin. ETag público/RDF não autoriza edição e restauração retorna Revision
sem prometer ETag novo: reler GET editorial após restaurar.

Erros globais no OpenAPI descrevem handlers disponíveis, não alcance de cada status
em toda operação. 401/403 herdados do router também aparecem em rotas públicas,
que continuam sem security/Bearer obrigatório. 406 só é declarado nos GET negociados.
405 de método inexistente é tratado pelo router, não cria operação DELETE no contrato.
O [inventário](catalogographic-openapi-inventory.md) associa cada operação real à
implementação, modelos, parâmetros, segurança, headers e testes.

## Consumo mínimo do editor Work

| Necessidade | Contrato existente / encaminhamento |
| --- | --- |
| Criar/ler/editar | POST/GET/PATCH works, WorkCreate/Read/Update, Location e ETag; #55 gera tipos; #56 transporta Bearer server-side |
| Listar | GET works editorial, limit/cursor/filtros; #57 implementa página pt/en |
| Perfil/release | GET profiles e profiles/release; usar persistable e release exata; perfis 2.0 não autorizam escrita por si só |
| Vincular Agent/Subject | contribution/subject no Work ou PATCH relationships com ETag próprio; consultas editoriais exigem permissões efetivas |
| Diagnósticos | POST works/validation; 200 pode conter conforms=false ou state=failed; ProblemDetails em erros; #58 correlaciona campos e mensagens globais |
| Concorrência | If-Match forte da consulta JSON editorial; 400/412/428 exigem correção/releitura, sem retry cego |
| Revisão publicada/editorial | Read não contém revisão/perfil; GET editorial fornece current/public/candidate e ETag; GET public seleciona publicação exata |
| Permissões | GET identity/me + políticas backend por operação; não inferir autorização apenas de papéis; #56/#58 compõem capacidades |

Ao combinar GET JSON editorial e GET de estado editorial, comparar os ETags:
se diferirem, reler o conteúdo antes de editar. Não anexar ao conteúdo antigo um
ETag mais recente obtido por outra consulta. As duas requisições não formam um
snapshot transacional único; o CAS continua protegendo a escrita.

Não foi encontrada necessidade de alterar os DTOs v1 para o primeiro formulário.
Leitor TypeScript deve preservar distinção entre null e omissão e ler headers além
do JSON. Pré-validação `kind=dto` usa uma união tipada dos cinco Create em `resource`:
a coleção determina o tipo aplicável, e a validação runtime é obrigatória. GET público
usa união dos cinco Read sem discriminador universal: a coleção conhecida escolhe
o tipo; não inventar `kind` em Work. Essas limitações exigem adaptação explícita na
#55, não mudança silenciosa de contrato. Erros desconhecidos devem ter fallback,
sem alegar que Diagnostic.code Pydantic aceita valores novos.

Homologação institucional, carga/produção, retenção e prazos de depreciação continuam
pendentes. Estabilização persistente é #54; cliente #55, BFF #56, UI #57/#58 e E2E
#59 ficam fora da #53. Evidências atuais no [relatório datado](catalogographic-openapi-m0601-verification-2026-10-16.md).
