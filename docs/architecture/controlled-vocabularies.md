# Valores controlados e vocabulários — M03.03.01

Contrato operacional **1.0**, implementado para [#24](https://github.com/inacioigne/libris/issues/24),
parte da [#23](https://github.com/inacioigne/libris/issues/23). Decisões no
[ADR 0024](../decisions/0024-controlled-vocabularies.md). Reutilizável por catalogação
e autoridades; não implementa suas rotas ou persistência. O Graph RDFLib continua
canônico; metadados de consulta são auxiliares operacionais.

## Inventário e escopo

#15–#17 já forneciam ResourceReference, EntityReference, LocalizedText, UniqueTexts,
identidade persistida e invariantes de vínculos. #18 fornecia serialização segura,
contexto local e round-trip isomórfico. #20 fornecia ReferenceValue.vocabulary,
ProfileDefinition, ResolvedProfile, SemanticRegistry e ProfileRegistry; #21 validava
nodeKind/enums por SHACL suplementar, sem verificar pertença; #22 fornecia Diagnostic,
CatalogCode e Problem Details. Faltavam conceitos operacionais, estados e evidência
externa, fronteira de provedores, política de cache e pertença baseada em identidade.

Acrescentados: quatro valores discriminados, envelope versionado, registro tipado
com evidência de pertença, fronteira assíncrona de provedores, resultados e
proveniência separados do original, diagnósticos suplementares e mapeamentos RDF
explícitos. Não há dependências, tabelas, migrações, cache persistente, frontend,
HTTP remoto ou endpoints novos.

## Contrato e schemas

Importação pública: `bibliographic.api.schemas.controlled_values`; implementação
reutilizável: `bibliographic.application.controlled_values`. Reaproveita os tipos
comuns existentes, sem FastAPI nos modelos e sem importações de API no domínio.

[Schema gerado](../../apps/api/src/libris/modules/bibliographic/resources/controlled-values-v1/catalog-value-1.0.schema.json)
contém discriminator/oneOf. O mesmo diretório publica schemas de EnrichedConcept,
ResolutionCachePolicy e VocabularyRegistry. Validadores Pydantic complementam invariantes cruzadas
não expressas integralmente no JSON Schema.

| kind | Representação | Identidade |
| --- | --- | --- |
| literal | value lexical string, language ou datatype opcionais | Não identifica recurso |
| controlled_concept | uri, vocabulary, metadados | URI externa exata |
| local_concept | Mesmos campos, internal_id obrigatório e URI institucional | Par UUID/URI armazenado |
| entity_reference | reference: EntityReference (local/external/unreconciled) | Contrato #16, sem classificá-la como conceito |

`CatalogValueDocument` contém `format="libris.catalog-value"`,
`contract_version="1.0"` e `value`. `parse_catalog_value(payload)` valida um valor
isolado sem coerção de tipo. Campos extras/variantes ambíguas são rejeitados.
Uma entidade Subject pode ter descrição SKOS, mas uma referência à entidade e
um valor de vocabulário continuam operações distintas no contrato.

Literais usam **forma lexical RDF**, inclusive números/datas: `{"kind":"literal",
"value":"42","datatype":"http://www.w3.org/2001/XMLSchema#integer"}`.
Reutilizam os datatypes e validação lexical dos perfis 2.0 (string, integer, decimal,
double, boolean, date, dateTime, gYear). Isto evita coerção/perda de transcrição;
primitivos JSON numéricos não são aceitos implicitamente. Datas seguem o subconjunto
Gregorian do contrato existente. Datatypes adicionais exigem evolução deliberada.
Literais com idioma não admitem datatype explícito. Literais simples podem ser vazios;
um campo de perfil pode exigir texto não vazio. A conversão RDF continua sujeita aos
limites de normalização lexical da biblioteca e falha se perder isomorfismo.

Conceitos exigem URI absoluta e vocabulary como IRI, não nome de apresentação.
Não existe download obrigatório de um vocabulário para validação estrutural; um
vocabulário declarado ainda precisa de evidência confiável para verificar pertença.
`label` é lista LocalizedText, no máximo um preferencial por idioma, incluindo
sem idioma, comparando tags sem distinguir caixa. Lista vazia é permitida: URI
identifica o conceito mesmo sem rótulo disponível. `alternative_labels` admite
múltiplos rótulos por idioma, mas proíbe pares duplicados e interseção com preferenciais.
Rótulos em branco são rejeitados. `notation` é texto transcrito, não identidade.
`source` é uma IRI de fonte, não justificativa automática de confiança.
Não há `language` no topo: cada rótulo possui seu idioma, evitando ambiguidade.

Exemplo externo completo ([JSON executável](../examples/controlled-vocabularies/external.json)):

```json
{
  "format": "libris.catalog-value",
  "contract_version": "1.0",
  "value": {
    "kind": "controlled_concept",
    "uri": "http://id.loc.gov/vocabulary/languages/por",
    "vocabulary": "http://id.loc.gov/vocabulary/languages/",
    "label": [
      {"value": "Português", "language": "pt"},
      {"value": "Portuguese", "language": "en"}
    ],
    "alternative_labels": [{"value": "Língua portuguesa", "language": "pt"}],
    "notation": "por",
    "status": "unverified"
  }
}
```

As diferenças da proposta da issue são envelope versionado, discriminator, vocabulary
identificado por IRI e rótulos LocalizedText. O exemplo não alega consulta real à LC.

## Identidade local e externa

`LocalConcept` exige UUID e ResourceURI, respeitando ResourceIdentity/ADR 0019:
HTTP(S), sem query/fragmento/credenciais; identidade persistida reaproveitada nas
revisões, nunca gerada por label/notation. O exemplo usa `/resources/{uuid}`.
Validação estrutural não extrai UUID da URI nem prova correspondência no banco.
Uma futura criação deve usar ResourceIdentity.create uma vez e persistir o par;
correções de rótulo e troca da base não alteram identidades já atribuídas.

[Exemplo local completo](../examples/controlled-vocabularies/local.json) possui
rótulos pt/en, altLabel, notation AMZ e fonte institucional sintética. Conceitos
locais neste contrato são descrições SKOS próprias; conceitos externos não são
presumidos SKOS. Nenhuma interface de administração é implementada.
VIAF/ISNI/ORCID/Wikidata podem identificar entidades, registros ou conceitos:
o adaptador futuro deve escolher a categoria pela natureza do recurso, sem
usar o fornecedor como regra de classificação universal.

## Estados e tentativas de resolução

| status conhecido | Significado | Diagnóstico padrão |
| --- | --- | --- |
| resolved | Identidade confirmada por fonte confiável selecionada pela aplicação | Sem erro de existência |
| unverified | Sintaxe válida, sem confirmação de existência | reference.unverified / Warning |
| invalid | Evidência definitiva reconhece referência inválida | reference.invalid / Violation |
| deprecated | Conceito descontinuado; sucessor opcional | reference.deprecated / Warning |
| unavailable | Sem confirmação disponível devido a indisponibilidade | Warning, nunca invalidação |

resolved/invalid/deprecated exigem source. Sua presença não comprova a veracidade
da afirmação: estado recebido de cliente precisa da política de confiança da aplicação.
replacement só é permitido em deprecated e deve ser URI diferente; nunca troca uri.
`unavailable` existe para representar uma referência sem evidência disponível.
**Uma consulta falha não converte um resolved/deprecated catalogado em unavailable.**

`VocabularyProvider.resolve(uri)` é protocolo assíncrono tipado de aplicação.
Não recebe rótulos e não participa de validadores Pydantic. Não há conector HTTP.
Resultados discriminados por outcome:

- success: URI consultada, ControlledConcept com estado definitivo, provenance;
  fonte/identidade devem concordar. invalid é resultado definitivo com evidência.
- temporary_failure: reason unavailable, retry_after_seconds opcional.
- permanent_failure: not_found, unsupported ou access_denied, sem retry transitório.
  Permanente descreve a consulta nesse provedor/escopo; não prova invalidade universal.

ResolutionProvenance contém provider, source, retrieved_at com timezone e
source_version opcional. `resolve_concept` faz consulta somente por chamada explícita
e devolve EnrichedConcept com cataloged copiado, attempt e previous_success opcional.
Identidades devem concordar. Enriquecimento conserva rótulos originais, URI, fonte,
notação, idioma e estado catalogados. Seleção de provedor/confiabilidade e captura
de exceções de transporte são responsabilidade do futuro adaptador; erros de
programação não são convertidos silenciosamente em indisponibilidade.

A aplicação deve revisar novas evidências antes de corrigir estado/rótulo/sucessor.
Atualização aceita gera revisão nova com revisão esperada e proveniência, nunca
reescreve revisões/snapshots históricos. previous_success é fornecida explicitamente,
sem resolver, substituir URI ou revalidar registros automaticamente.

## Cache e atualização

ResolutionCachePolicy é configuração/teste de decisões, **sem armazenamento**.
Defaults técnicos: sucesso 24h; resposta negativa 5min; nova tentativa transitória
60s; última evidência durante indisponibilidade até 7 dias além do TTL de sucesso.
TTL zero desabilita reutilização. fresh usa relógio explícito com timezone e recusa
idade negativa; usable_stale exige URI igual e falha temporária, não negativa definitiva.
Stale significa apresentação de evidência antiga, não confirmação atual.

Cache futuro deve ser limitado e indexado por URI **exata**, provedor e versão da
configuração (e contexto/autorização quando relevante). Guardar fonte/instante/versão,
separar última resposta bem-sucedida de tentativas; nunca cachear outage como invalid.
Honrar retry_after quando presente. TTLs precisam de ajuste às regras do fornecedor;
a aplicação explicita freshness/stale ao consumidor. Expiração não invalida registro
aceito. Rótulos não se atualizam no snapshot por leitura/cache: label_update é
explicit_revision. Sem persistência de cache nem coleta massiva nesta etapa.

## Integração de perfis, SHACL e diagnósticos

Nenhuma alteração a ProfileDefinition, ResolvedProfile, SemanticRegistry,
ProfileRegistry, releases ou release-lock. Uma configuração confiável já pode
usar ReferenceValue(kind="vocabulary", vocabulary=IRI); iri genérico não impõe
pertença. SemanticRegistry continua governando propriedades/classes, enquanto
VocabularyRegistry governa evidência dos conceitos (instâncias), sem duplicar ontologia.

`validate_catalog_value(value, field, vocabularies=..., field_id=...)` recebe
DeclarativeField de ResolvedProfile confiável. Reutiliza check_value para literais,
tipos de entidades e policy.allowed_values por URI. StructuredValue continua
auxiliar, fora dos quatro escalares. O chamador conserva cardinalidades de coleções,
classes RDF/ownership e integridade de entidade nos validadores existentes.

VocabularyDefinition fixa uri/release/source, members por URI e complete.
O registry recebe snapshots confiáveis, seleciona uma release por vocabulário,
sem HTTP. URI presente confirma pertença; ausência em conjunto completo rejeita;
ausência em conjunto parcial/desconhecido gera Warning. Não compara label,
notation, prefixo da URI ou campo vocabulary recebido como prova. Nem uma resposta
resolved comprova pertença universal: adaptar evidência do provedor exige decisão
explícita do chamador. Conjuntos parciais não são cópias locais autoritativas da fonte.

| Camada | Verifica | Não verifica |
| --- | --- | --- |
| Pydantic | Sintaxe, discriminadores, coerência, idiomas | Existência/trust remotos |
| ProfileRegistry/SemanticRegistry | Configuração/release/propriedades reconhecidas | Dados do conceito |
| Aplicação + VocabularyRegistry | Tipo/enum/estado e pertença com evidência selecionada | Classes RDF universais/existência remota implícita |
| SHACL local + suplemento #21 | Grafo concreto: nodeKind, cardinalidade, datatype, enum/classes aplicáveis | Pertença por vocabulary ou disponibilidade remota |
| Provedor explícito | Evidência externa por URI e escopo | Alteração automática do catálogo |

validate_resolved_profile permanece intacto. A pertença é verificação suplementar
**da aplicação**, não resultado SHACL. Não traduz vocabulary em novas shapes históricas.

Diagnósticos usam o Diagnostic/CatalogCode estabelecido (#22), com field_ids,
Warning/Violation e mensagens fixas, sem valores de usuário ou detalhes técnicos.
Novos códigos aditivos: reference.unverified, reference.deprecated,
reference.vocabulary_mismatch, reference.vocabulary_unverified,
reference.resolver_unavailable. `resolution_diagnostics` converte falhas de consultas
em Warning; não são falhas do motor SHACL nem CatalogValidationResult(state="failed").
Reutilizar `adapt_pydantic_error` para URI inválida/identificação insuficiente;
`schema.value` distingue tipo incompatível; pertença negativa tem código próprio.
O suplemento integra agora os resultados catalográficos pelo contrato #22.

## JSON, JSON-LD e RDF

JSON operacional preserva metadados. `concept.as_reference()` projeta URI para
ResourceReference dos DTOs existentes explicitamente; **não conserva metadados nesse
DTO reduzido**. Manter o documento operacional separado quando forem necessários.
Os cinco DTOs públicos não passam a aceitar conceitos ricos silenciosamente.

`catalog_value_to_graph(subject, predicate, value)` emite só a declaração do
proprietário. Conceitos/entidades viram URIRef; literal vira RDF Literal com idioma
ou datatype. LocalReference reutiliza reference_to_uri do mapeador #18 e exige
mapa de identidades quando aplicável; UnreconciledReference não é publicável.
O chamador seleciona propriedade autorizada pelo perfil e a URI armazenada.
Não emite label/notation/inScheme/status sobre um alvo externo, não inventa tipo,
mesmo para LC Languages. O enriquecimento fica fora do snapshot proprietário.

O [Turtle externo](../examples/controlled-vocabularies/external.ttl) equivale a:

```turtle
<https://example.org/resources/00000000-0000-4000-8000-000000000001>
  <http://id.loc.gov/ontologies/bibframe/language>
  <http://id.loc.gov/vocabulary/languages/por> .
```

Em JSON-LD expandido, equivalente ao [exemplo gerado](../examples/controlled-vocabularies/external.jsonld):

```json
[{
  "@id": "https://example.org/resources/00000000-0000-4000-8000-000000000001",
  "http://id.loc.gov/ontologies/bibframe/language": [
    {"@id": "http://id.loc.gov/vocabulary/languages/por"}
  ]
}]
```

`local_concept_to_graph` exporta **descrição local própria**: skos:Concept,
skos:prefLabel/altLabel/inScheme/notation, idiomas conservados. Não junta esta
descrição ao link bibliográfico automaticamente. source/status/replacement de
resolução não viram fatos RDF sem mapeamento aprovado. Não gera owl:sameAs,
skos:exactMatch, equivalência de propriedade ou descrição externa.

JSON-LD/Turtle usam serialize_graph e parse_representation #18, contexto bibframe-v1
intacto, sem contextos/imports remotos ou expansão de alvos. Todos os round-trips e
exemplos são verificados por isomorfismo. JSON-LD RDF não é backup dos metadados
operacionais: estes ficam no envelope/enriquecimento, enquanto o Graph exportado
conserva todas as triplas explicitamente fornecidas.

## Evolução e limites

Schemas e exemplos novos têm gerador/check próprios. Artefatos históricos e locks
não mudaram. Mudança de significado/discriminadores exige nova major; atualização
de política institucional ou vocabulário exige release/evidência nova explícita.

A [#25](https://github.com/inacioigne/libris/issues/25) poderá reutilizar IRI,
ReferenceValue.vocabulary, registros e fronteiras existentes para ontologias
adicionais. Este trabalho não amplia namespaces/propriedades arbitrariamente,
não oferece plugins ontológicos ou altera o mapeador dos cinco Read. Graph exportado
continua preservando extensões existentes. #23 só estará completa com #25.

Pendentes: governança institucional de fontes/releases, política de retenção,
aprovação dos TTLs e conectores de rede. Homologação humana não é substituída pelos
schemas/testes. Não há reconciliação automática, MARC, CRUD de autoridades ou
migração bibliográfica definitiva.

## Integração de pertença na catalogação — M08.05

A release imutável
[`monograph-v1.2.json`](../../apps/api/src/libris/modules/bibliographic/resources/monograph-v1.2.json)
reutiliza os descritores e shapes institucionais 1.1 e ativa a política suplementar
de pertença nas novas criações e pré-validações. A release 1.1 e seus registros não
são reescritos nem reavaliados ao publicar. Em PATCH de registro 1.1, somente URIs
adicionadas/alteradas passam pela nova política; valores preservados e removidos não
são reinterpretados. Identidade, URI RDF e DTO `ResourceReference` v1 permanecem
inalterados.

Cada operação lê, na própria transação (ou na transação explicitamente somente leitura
da pré-validação), a release ativa persistida dos vocabulários IRI declarados pelo
perfil. Não há escolha de release pelo cliente nem consulta síncrona a provedor.
Uma ocorrência exata em release é pertença positiva. A ausência em release completa
gera `reference.vocabulary_mismatch`/Violation; a ausência em release parcial, a
release ausente e a evidência não conclusiva geram Warning. `deprecated` informa
sucessor quando disponível sem trocar URI. Falhas temporárias permanecem fora das
releases imutáveis e não apagam a evidência confiável anterior; estado
`unavailable` também é Warning, inclusive na publicação. A publicação valida o
snapshot selecionado com a evidência ativa no mesmo contexto transacional, sem
alterar revisões publicadas automaticamente.

Os diagnósticos suplementares incluem `field_ids` no formato BFF (por exemplo,
`/language`) e o caminho RDF IRI; o BFF os associa ao campo e localiza as mensagens
em pt/en. SHACL e as regras RDF continuam sendo avaliados separadamente. A política
não importa labels ou descrições de terceiros para o grafo proprietário e não
converte timeout em incompatibilidade comprovada.

Testes abrangem identidade exata, release completa/parcial, deprecated/sucessor,
indisponibilidade, PATCH sem revalidar dados preservados e a barreira de publicação.
O fluxo HTTP com PostgreSQL descartável comprova a integração operacional; a
verificação não homologa Keycloak nem fontes remotas.

## Verificação

```bash
cd apps/api
uv run python ../../scripts/generate_controlled_value_artifacts.py --check
uv run python ../../scripts/generate_profile_artifacts.py --check
uv run pytest ../../tests/api
uv run ruff check . ../../tests ../../scripts/generate_controlled_value_artifacts.py
uv run mypy src
cd ../..
bash scripts/check.sh
git diff --check
```

Se uv não executar, usar os binários de apps/api/.venv. Evidências e checklist
na [verificação desta entrega](controlled-vocabularies-verification.md).

Os mapeamentos locais e invariantes de rótulos seguem a
[referência normativa SKOS do W3C](https://www.w3.org/TR/skos-reference/):
preferenciais únicos por idioma e disjuntos dos alternativos; inScheme expressa
pertença explicitamente declarada. Nenhuma regra infere pertença por namespace.
