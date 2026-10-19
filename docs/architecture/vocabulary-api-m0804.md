# API de vocabulários — M08.04 / #70

## Escopo e fronteiras

A API lê o catálogo operacional PostgreSQL materializado pelas M08.02/M08.03. Ela
não altera snapshots RDF, referências catalogadas, perfis, revisões, releases
históricas ou ETags de edição. A URI semântica continua sendo a identidade; UUIDs do
catálogo identificam vocabulário e conceito para navegação HTTP. A API não consulta
provedores remotos, não acessa Elasticsearch e não executa resolução no startup.

O recurso disponível para consulta é a combinação de vocabulário `active` e release
`active`. Releases `candidate` e vocabulários em outros estados não são expostos. Uma
release `superseded` só pode ser consultada se seu UUID for selecionado explicitamente,
preservando a leitura histórica sem misturar releases.

O schema M08.02 não tem um estado de aprovação separado para fontes nem uma política
de visibilidade pública. Por isso esta superfície não é anônima: todas as operações
exigem token OIDC válido com `bibliographic:read` e `metadata_revision:read`, conforme
a política de leitura editorial existente. Os papéis atuais catalogador, revisor,
publicador e administrador possuem ambas as permissões; o papel leitor e o gestor de
autoridades não possuem a permissão de revisão exigida. A autorização é verificada no
FastAPI, não no BFF.

O estado `active` é o único sinal operacional disponível para publicação técnica no
catálogo; não equivale a uma homologação jurídica ou curatorial. Em particular, o
estado de direitos LC Languages continua pendente de revisão institucional conforme
a documentação M08.03. Nenhuma rota torna esses dados publicamente acessíveis ou
retorna o JSON de proveniência interno integral da fonte.

## Endpoints

| Método e rota | Operação |
| --- | --- |
| `GET /api/v1/vocabularies` | Lista vocabulários ativos com release ativa |
| `GET /api/v1/vocabularies/{vocabulary_id}/concepts` | Busca conceitos na release ativa ou em uma release histórica selecionada |
| `GET /api/v1/vocabularies/{vocabulary_id}/concepts/{concept_id}` | Detalha um conceito pela identidade UUID operacional |
| `POST /api/v1/vocabularies/{vocabulary_id}/validate` | Verifica localmente a pertença/estado da URI enviada no corpo |

`vocabulary_id` e `concept_id` são UUIDs, evitando que barras, fragmentos ou
caracteres reservados de uma URI se tornem identificadores ambíguos no path. A URI
canônica é retornada sem normalização e pode ser pesquisada exatamente pelo parâmetro
`uri` ou validada no corpo `{"uri":"..."}`. URI e label não são tratados como
identidades intercambiáveis.

## Busca, locale e paginação

`GET .../concepts` aceita:

| Parâmetro | Semântica |
| --- | --- |
| `q` | Busca substring em rótulos preferenciais/alternativos ou correspondência exata em notação e identificadores da release |
| `uri` | Filtro de URI canônica exata, sensível a maiúsculas/minúsculas |
| `locale` | Idioma preferido para o label de apresentação; padrão `en` |
| `label_language` | Filtro estrito de idioma do label, comparado após normalização em minúsculas |
| `status` | `resolved`, `unverified`, `invalid`, `deprecated` ou `unavailable` |
| `release_id` | UUID de release `active` ou `superseded` pertencente ao vocabulário |
| `limit`, `offset` | Limite padrão 20, máximo 100; offset de 0 a 100000 |

O texto de `q` tem no máximo 200 caracteres, deve conter algum caractere não branco,
é normalizado em Unicode NFC, espaços laterais removidos e sequências internas de
espaços reduzidas a um espaço. O PostgreSQL normaliza labels armazenados para NFC, reduz seus espaços
consecutivos e usa `LOWER()` para comparar sem distinção de caixa conforme a
collation, preservando acentos. Não há remoção
de diacríticos, transliteração nem alteração da URI. Notações e identificadores são
comparados exatamente após conversão para minúsculas; labels usam substring. `uri`
não admite prefix matching.

O locale é uma preferência de apresentação, não uma regra para ocultar um conceito:
o fallback determinístico é idioma exato, subt tag/base-language, inglês e então
outro label preferencial pela ordem idioma/valor. Se não houver label preferencial,
usa-se o primeiro alternativo pela mesma ordenação; se o conceito não tiver labels,
`label` e `language` ficam ausentes. Labels completos, idiomas, categorias de origem
e fontes são preservados no detalhe.

Resultados são ordenados por `uri` ascendente e UUID do conceito como desempate.
`pagination` contém o limite, offset e total calculado na mesma release. Uma página
além do fim retorna `items: []` e preserva o total. Uma busca sem release ativa
retorna página vazia; um `release_id` inexistente, candidato ou de outro vocabulário
retorna 404. As consultas de busca usam duas instruções SQL (contagem e página), com
label localizado por `LATERAL`, evitando fan-out e N+1; lista usa uma instrução e
detalhe carrega labels em uma consulta independente de quantidade.

## Validação offline e diagnósticos

A validação consulta apenas a release local selecionada. Sem parâmetro, usa a release
ativa; um `release_id` explícito seleciona histórico `active`/`superseded`.

| Evidência local | `status` / `belongs` / `valid` |
| --- | --- |
| Ocorrência confirmada | `resolved` / `true` / `true` |
| Ocorrência obsoleta | `deprecated` / `true` / `false`; a URI e eventual substituto são preservados |
| Ocorrência explicitamente inválida | `invalid` / `false` / `false` |
| URI ausente em release completa | `invalid` / `false` / `false`, código `reference.vocabulary_mismatch` |
| URI ausente em release parcial | `unverified` / `null` / `null`, código `reference.vocabulary_unverified` |
| Vocabulário sem release ativa | `unavailable` / `null` / `null`, código `reference.resolver_unavailable` |

Estados semânticos são resultados HTTP 200, não erros de transporte. A resposta
retorna `diagnostics` (lista, vazia para confirmação) usando `CatalogCode` e o schema
`Diagnostic` já existentes. A ausência só prova
invalidade em conjunto local declarado completo. Falha posterior de tentativa remota
não substitui uma ocorrência/importação confiável: este caso de uso não lê a tentativa
como se fosse a evidência anterior e não faz rede. A operação é read-only e não altera
o valor bibliográfico original nem a revisão catalográfica.

Vocabulário, release e URI malformada são distinguidos: recursos/release inexistentes
resultam em 404; URI, locale, status e paginação estruturalmente inválidos resultam
em 422 Problem Details. Falha real de conexão PostgreSQL resulta no 503 padronizado;
`deprecated`, `unverified` e `unavailable` semânticos não são 503.

## Cache e segurança HTTP

GETs de vocabulário/conceito retornam `Cache-Control: private, no-cache,
must-revalidate`, ETag forte SHA-256 da resposta JSON e `Vary: Authorization`.
`If-None-Match` aceita lista, tag fraca equivalente e `*`; a revalidação inalterada
retorna 304. Locale e release são parte da URI de consulta, e mudanças na release ou
no payload mudam o ETag. O middleware conserva esse cache privado somente nos GETs
dessas rotas e códigos 200/304; os demais endpoints continuam `no-store, private`.
A validação POST é sempre `private, no-store`. CORS permite `If-None-Match` e expõe
ETag sem habilitar credenciais compartilhadas.

401 indica credencial ausente/inválida; 403 indica ausência de qualquer permissão
exigida. Os handlers globais produzem `application/problem+json` e não revelam
tokens, SQL ou proveniência interna arbitrária.

## OpenAPI, cliente e execução

O snapshot real da fábrica FastAPI foi adicionado em
[catalogographic-openapi-v1-m0804.json](catalogographic-openapi-v1-m0804.json); o
snapshot original M06.01 continua preservado. O cliente TypeScript usa esse snapshot
como entrada do gerador oficial:

```bash
cd apps/api
uv run python ../../scripts/generate_catalogographic_openapi.py --check
cd ../web
npm run openapi:generate
npm run openapi:check
npm run typecheck
```

Os testes HTTP sem serviços externos:

```bash
cd apps/api
uv run pytest ../../tests/api/test_vocabulary_api.py \
  ../../tests/api/test_http_security.py ../../tests/api/test_catalogographic_openapi.py -q
```

O teste PostgreSQL exige a base descartável isolada pelo runner; não use banco de
desenvolvimento:

```bash
bash scripts/test-postgres.sh -k vocabulary_api
```

Limitações atuais: não há CRUD administrativo de fontes/releases, ativação automática,
visibilidade anônima, busca cross-vocabulary, filtro por versão externa distinta do
UUID de release, endpoint para tentativa atual ou integração visual no editor. A
superfície serve apenas os dados ativos já importados e ativados explicitamente.
Os comandos, resultados e limites desta execução estão no
[relatório datado](vocabulary-api-m0804-verification-2026-10-19.md).
