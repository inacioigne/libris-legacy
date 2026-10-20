# LC Languages operacional — M08.03 / #69

**Status:** implementação local para revisão em 2026-10-19. A homologação institucional
das traduções e dos direitos de redistribuição continua pendente.

A exposição autenticada do catálogo carregado foi acrescentada depois pela M08.04;
consulte [API de vocabulários](vocabulary-api-m0804.md). As limitações abaixo se
referem à entrega inicial de ingestão M08.03.

## Objetivo e arquitetura

A implementação materializa a MARC Code List for Languages no catálogo PostgreSQL da
M08.02. Ela reutiliza `ControlledConcept`, as releases imutáveis, a identidade por URI,
as transações de importação e a separação entre evidência e snapshot bibliográfico.
Não cria registry paralelo, não altera DTOs v1 e nunca consulta a LC no startup,
validação, edição ou ciclo editorial.

Identidade do vocabulário: `http://id.loc.gov/vocabulary/languages/`. Identidades de
conceito, por exemplo, são `.../por`, `.../eng` e `.../spa`. A URI permanece a identidade;
os códigos são identificadores pesquisáveis, não substitutos da URI.

## Fonte, snapshot e integridade

A fonte primária é o XML estruturado que a própria página da
[MARC Code List for Languages](https://www.loc.gov/marc/languages/) identifica como
versão para aplicações:
`https://www.loc.gov/standards/codelists/languages.xml`. O XML declara o namespace
`info:lc/xmlns/codelist-v1`, `codelistId=iso639-2b`, título, conceitos, nomes e códigos.
O importador converte somente registros presentes no artefato para as URIs HTTP
canônicas do namespace id.loc; não infere pertença por prefixo ou rótulo.

O parser:

- exige UTF-8, raiz/namespace e metadados esperados;
- recusa DTD/entidades, XML malformado, código/rótulo ausente e código/URI duplicado;
- limita o arquivo a 10 MiB no comando operacional;
- calcula SHA-256 sobre os bytes originais;
- exige ao menos 400 conceitos quando a cobertura é declarada completa;
- aceita conjuntos menores somente com `--partial-scope` explícito.

Como a LC informa atualização contínua, mas não uma versão de release no documento
consultado, `source_version` permanece nulo salvo se o operador comprovar e informar
uma versão. `release_key`, data de coleta e SHA-256 são metadados internos e não são
apresentados como versão externa.

## MARC, ISO e rótulos

O código do elemento XML é preservado simultaneamente como `notation` histórica e
identificador `marc`. Identificadores `iso639-1`, `iso639-2-b`, `iso639-2-t` e
`iso639-3` só entram por sidecar JSON governado; cada valor exige esquema e URI de
evidência. Similaridade de nome nunca cria equivalência. A fixture comprova:

| URI | MARC | ISO 639-1 | ISO 639-2 B/T | ISO 639-3 |
| --- | --- | --- | --- | --- |
| `.../por` | `por` | `pt` | `por` / `por` | `por` |
| `.../eng` | `eng` | `en` | `eng` / `eng` | `eng` |
| `.../spa` | `spa` | `es` | `spa` / `spa` | `spa` |

O inglês recebido no XML é `provider_official`. Traduções aprovadas entram por outro
sidecar como `institutional_translation`, com sua própria `source_uri`. A fixture pt
contém Português, Inglês e Espanhol; ela é evidência de teste, não homologação
curatorial. Sem tradução aprovada, a busca/exibição conserva o rótulo oficial inglês,
depois notação/URI conforme o consumidor; o importador não inventa texto.

## Carga inicial, atualização e operação offline

Pré-condições: PostgreSQL alvo explicitamente configurado em `DATABASE_URL` e schema
atualizado por Alembic. Os comandos são executados em `apps/api`:

```bash
uv run alembic upgrade head
uv run python -m libris.modules.bibliographic.lc_languages_cli prepare \
  /dados/languages.xml --release-key lc-languages-2026-10-19 \
  --retrieved-at 2026-10-19T18:00:00-04:00 \
  --identifiers /dados/lc-language-identifiers.json \
  --translations /dados/lc-language-translations.json
```

`prepare` valida e persiste uma candidata, devolvendo checksum, quantidade e relatório
contra a release ativa. Reexecutar os mesmos bytes/chave é idempotente. Um arquivo
parcial exige `--partial-scope`; sua ausência no relatório não prova remoção.

A ativação é separada e usa CAS:

```bash
uv run python -m libris.modules.bibliographic.lc_languages_cli activate \
  --release-key lc-languages-2026-10-19 \
  --expected-active-release-id 11111111-1111-1111-1111-111111111111
```

Na primeira carga, omita `--expected-active-release-id`. Em atualização, informe o
UUID ativo observado. A transação supersede a release anterior e ativa a candidata;
um índice parcial garante no máximo uma ativa. Conflito preserva a ativa anterior.
Releases e ocorrências antigas não são atualizadas ou apagadas.

Consultas são exclusivamente locais:

```bash
uv run python -m libris.modules.bibliographic.lc_languages_cli search por
uv run python -m libris.modules.bibliographic.lc_languages_cli search Português
uv run python -m libris.modules.bibliographic.lc_languages_cli search es
```

A busca usa URI exata, notação MARC, identificadores evidenciados e rótulo da release
ativa. Desligar a rede não muda esse caminho. Work já armazena `language` como
`ResourceReference` e os perfis v1.1/2.0 apontam para o namespace LC; selecionar um
resultado conserva a URI e os mecanismos existentes de revisão/ETag. Não foi criada
uma API/UI administrativa: M08.02 não fornece esse caminho e a autorização atual não
define operação específica de vocabulários. O editor ainda recebe URI manualmente;
um seletor BFF fica bloqueado até contrato HTTP/autorização próprios, sem justificar
uma API pública improvisada.

## Atualização, falhas e recuperação

O relatório distingue inclusões, mudanças de rótulos, mudanças de identificadores,
ausências e obsolescência explícita. `absence_proves_removal` só é verdadeiro para
snapshot completo; ainda assim a ausência é reportada separadamente, não convertida
silenciosamente em `deprecated`. Código marcado como descontinuado na fonte é
persistido como `deprecated`.

Falha de parse/importação reverte toda a transação. Falha de ativação não altera a
release ativa. Recuperação consiste em corrigir artefato/sidecar, preparar outra
candidata e ativá-la com o UUID ativo atual. Rollback usa a mesma ativação explícita
de uma release `superseded`, com CAS; nenhum histórico é apagado. O downgrade da
migration recusa remover identificadores existentes.

## Direitos, atribuição e limites

Atribuição registrada: Library of Congress, Network Development and MARC Standards
Office, MARC Code List for Languages. A orientação legal geral da LC informa que
obras do governo dos EUA no site podem ser reutilizadas e disponibilizadas em CC0,
mas também exige avaliação de direitos por item. Não foi encontrada declaração
específica no artefato LC Languages durante esta execução. Por isso o registry grava
`rights_status=pending-institutional-review`; esta entrega não escolhe licença para o
Libris nem afirma autorização jurídica de redistribuição.

O download oficial retornou HTTP 403 neste ambiente. O comando, portanto, trabalha
com artefato obtido e preservado pelo operador e não implementa fallback por scraping
HTML. Aquisição remota allowlisted com política operacional poderá ser acrescentada
quando o endpoint puder ser ensaiado; não é necessária para carga ou uso offline.

## Testes

Fixtures pequenas cobrem `por`, `eng` e `spa`, Unicode, sidecars, dados ausentes,
duplicações, XML inseguro/malformado, fonte parcial, comparação e obsolescência.
Integração PostgreSQL cobre importação atômica/idempotente, identificadores, busca
offline, ativação/CAS, supersessão e preservação de releases. Execute:

```bash
cd apps/api
uv run pytest -q ../../tests/api/test_lc_languages.py
uv run ruff check . ../../tests
uv run mypy src
cd ../..
bash scripts/test-postgres.sh
```

Resultados reais desta sessão estão em
[lc-languages-m0803-verification-2026-10-19.md](lc-languages-m0803-verification-2026-10-19.md).

## Evolução: ingestão SKOS/RDF Turtle — 2026-10-19

Esta evolução acrescenta o parser offline de Turtle sem substituir o parser XML nem
alterar a identidade do vocabulário ou os serviços de importação/ativação. O snapshot
local analisado foi `apps/api/data/vocabularies/lc/languages.skosrdf.ttl`, com 464.803
bytes e SHA-256
`ff5ed169c0cf3aafb88e16288d15e1037b9845094e30a907322fe8cb5db93d39`.

### Diagnóstico e mapeamento

O grafo tem 9.343 triplas, 485 recursos `skos:Concept` e 994 `cs:ChangeSet`; não
declara nenhum recurso tipado `skos:ConceptScheme`. Todos os 485 conceitos pertencem
ao único esquema `http://id.loc.gov/vocabulary/languages` e suas URIs são exatamente
`http://id.loc.gov/vocabulary/languages/{notation}`. O recurso
`http://id.loc.gov/vocabulary/languages/MARC_Language` é rotulado “MARC Language”,
confirmando a identidade MARC Languages. O artefato não é tratado como lista ISO.

| Propriedade nos conceitos | Quantidade | Tratamento |
| --- | ---: | --- |
| `skos:inScheme` | 485 | Exigida e validada contra o esquema único |
| `skos:notation` | 485 | Exigida; código MARC de três letras, único e igual ao segmento da URI |
| `skos:prefLabel` | 962 | Preservada como rótulos preferenciais oficiais com idioma |
| `skos:altLabel` | 0 | Suportada pelo parser; ausente no snapshot |
| `owl:deprecated` | 0 | Só mudará status com valor booleano explícito |
| `skos:exactMatch` | 357 | Não convertida em identificadores ISO nem persistida pelo DTO atual |
| `skos:changeNote` | 963 | Contabilizada na proveniência; fora do modelo ImportedConcept |
| `skos:editorial` | 485 | Contabilizada na proveniência; fora do modelo ImportedConcept |
| `skos:historyNote` | 4 | Contabilizada na proveniência; não implica obsolescência |

Todos os conceitos têm rótulo em inglês; 477 também têm rótulo em alemão, sem
conceitos sem rótulo. As 485 notações são distintas e coincidem com os segmentos
finais das URIs. `por`, `eng` e `spa` existem e retêm suas URIs canônicas. Não são
derivadas notações, traduções ou equivalências ISO. Os vínculos `exactMatch` do RDF
apontam a autoridades de assunto e não são evidência de códigos ISO.

O contrato `ImportedConcept` acomoda a identidade, notação, estado, rótulos
preferenciais/alternativos, idiomas e identificadores MARC. Anotações RDF sem campo
equivalente são enumeradas na proveniência da release e não mudam o estado do
conceito. Propriedades não reconhecidas no conceito bloqueiam a importação para
revisão, em vez de serem descartadas silenciosamente. O hash do artefato original
permanece associado à release.

### Integridade e proveniência por release

RDFLib já era dependência bloqueada do projeto. O parser recebe somente bytes locais,
não carrega contextos/recursos remotos, exige UTF-8, Turtle válido, limite de 10 MiB,
marcador MARC, pertença única, URI/notação canônica, rótulos e códigos únicos.
Para uma release Turtle completa, `--expected-concepts` é obrigatório; o parser também
valida a contagem exata e a presença de `eng`, `por` e `spa`. Arquivos parciais
continuam exigindo `--partial-scope`. O limiar histórico XML de 400 conceitos foi
mantido para compatibilidade e não é usado para validar a contagem Turtle.

A fonte oficial registrada é Library of Congress, com direitos em
`pending-institutional-review`. O `source_uri` da release Turtle é o IRI canônico do
esquema (`http://id.loc.gov/vocabulary/languages`). O snapshot local não informa o
URL exato de download nem a data em que foi obtido; nenhum endpoint `.ttl` ou data
foram inferidos. O operador deve informar a data/hora real de obtenção em
`--retrieved-at`. A release armazena URI da fonte, formato, nome local do artefato,
SHA-256, data informada, versão do importador, quantidade de conceitos, status de
direitos e contagens de propriedades RDF não mapeadas. Essa proveniência usa a nova
coluna JSONB `vocabulary_releases.provenance` (migration incremental 0008); downgrade
recusa apagar proveniência não vazia.

### Comandos

Executar em `apps/api`, com `DATABASE_URL` apontando explicitamente ao PostgreSQL
autorizado e migrations aplicadas:

```bash
uv run alembic upgrade head
uv run python -m libris.modules.bibliographic.lc_languages_cli prepare \
  data/vocabularies/lc/languages.skosrdf.ttl --format turtle \
  --release-key lc-languages-skos-2026-10 \
  --retrieved-at DATA_REAL_COM_TIMEZONE --expected-concepts 485
```

O formato também é inferido para `.xml`, `.ttl` e `.turtle`; `--format` pode
sobrescrever a inferência. `--max-bytes` configura o limite local. Sidecars de
identificadores/traduções continuam opcionais e exigem evidência própria; não usar
fixtures de teste como traduções homologadas. `prepare` cria uma release `candidate`,
em uma transação, calcula o relatório de diferenças e não ativa automaticamente.
Repetir a mesma chave e os mesmos dados é idempotente.

A ativação permanece separada e usa CAS:

```bash
uv run python -m libris.modules.bibliographic.lc_languages_cli activate \
  --release-key lc-languages-skos-2026-10 \
  --expected-active-release-id UUID_ATIVO_OBSERVADO
```

Na carga inicial, quando não há release ativa, omitir `--expected-active-release-id`.
Em atualização, observar o UUID ativo e passá-lo explicitamente. Para rollback, ativar
uma release histórica `superseded` com o UUID ativo atual. Consultas locais continuam
disponíveis por URI, código ou rótulo:

```bash
uv run python -m libris.modules.bibliographic.lc_languages_cli search \
  http://id.loc.gov/vocabulary/languages/por
uv run python -m libris.modules.bibliographic.lc_languages_cli search por
uv run python -m libris.modules.bibliographic.lc_languages_cli search Portuguese
```

### Verificação e limites desta evolução

Testes unitários usam fixture Turtle pequena, incluindo `por`/`eng`/`spa`, idioma,
`altLabel`, tradução e identificador sidecar, obsolescência explícita, dados ausentes,
notações/URIs inconsistentes, Turtle inválido, tamanho, ordenação e digest.
Os testes PostgreSQL foram estendidos para verificar releases XML e Turtle, busca
offline, idempotência, proveniência, concorrência de ativação e histórico. Resultados
da execução atual devem ser registrados em
[lc-languages-m0803-rdf-verification-2026-10-19.md](lc-languages-m0803-rdf-verification-2026-10-19.md),
sem substituir o relatório histórico da entrega original.

Não há confirmação de importação/ativação em banco de destino nesta evolução. A
integração real requer PostgreSQL descartável para os testes e, para uma carga fora
desse ambiente, confirmação do operador sobre destino, data real de obtenção e
autorização. A implementação não modifica dados bibliográficos, não consulta a rede e
não afirma homologação de direitos ou tradução institucional.
