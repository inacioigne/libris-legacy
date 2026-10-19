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
