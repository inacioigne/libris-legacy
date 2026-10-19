# Catálogo operacional persistente de vocabulários — M08.02 / #68

**Status:** implementação local para revisão, 2026-10-19. Esta entrega não homologa
fontes externas, direitos, conectores, administração ou novas APIs públicas. Ela
materializa a fase persistente prevista na
[especificação M08.01](vocabulary-operations-m0801.md) e mantém o
[ADR 0041](../decisions/0041-vocabulary-operations-governance.md) como decisão
proposta, sem ampliar silenciosamente sua aprovação.

A API de leitura adicionada posteriormente pela M08.04 está documentada em
[vocabulary-api-m0804.md](vocabulary-api-m0804.md); as declarações abaixo descrevem
o escopo original da persistência M08.02.

## Escopo e fronteiras

O PostgreSQL passa a armazenar o catálogo operacional, releases, conceitos,
rótulos, importações e evidências. O catálogo é auxiliar: não altera
`experimental_semantic_*`, revisões, snapshots RDF, perfis, ETags ou DTOs v1. A URI
continua sendo a identidade Linked Data; Elasticsearch não participa desta entrega.

Não há endpoint novo, consulta HTTP a provedor, job em background, ativação
administrativa ou sincronização automática. O serviço é reutilizável internamente
por consumidores futuros e não executa I/O externo no startup, em migrations ou na
validação síncrona.

## Modelo físico

| Tabela | Responsabilidade |
| --- | --- |
| `vocabulary_sources` | fonte/provedor governado e proveniência documental |
| `vocabularies` | identidade exata, origem, tipo externo/local e estado administrativo |
| `vocabulary_releases` | release interna, versão externa opcional, digest, temporalidade, cobertura completa/parcial e escopo |
| `vocabulary_concepts` | identidade global estável do conceito por URI; UUID local somente para conceito institucional |
| `vocabulary_release_concepts` | ocorrência histórica do conceito na release, com notação, estado, fonte e substituição |
| `vocabulary_labels` | transcrição multilíngue preferencial/alternativa e categoria de proveniência |
| `vocabulary_imports` | execução e chave de idempotência da importação |
| `vocabulary_resolution_attempts` | tentativas append-only, sucesso confiável, falhas, TTL, stale e contexto de cache |

A mesma URI em releases diferentes referencia uma linha em `vocabulary_concepts` e
ocorrências distintas em `vocabulary_release_concepts`. Assim, identidade não é
regenerada por label/notação e mudanças históricas não sobrescrevem releases
anteriores. Uma URI não pode pertencer simultaneamente a duas identidades de
vocabulário. Conceitos locais exigem UUID; conceitos externos o proíbem.

As chaves estrangeiras compostas garantem que ocorrência, URI, conceito, release e
vocabulário tenham o mesmo escopo. Uma release parcial exige descrição de escopo.
Estados confirmados exigem fonte. Substituição exige `deprecated` e URI distinta.
Há no máximo um label preferencial por idioma e ocorrência; labels alternativos
podem repetir idioma, mas não a mesma transcrição. Idioma vazio preserva literal sem
tag e tags são armazenadas em minúsculas após validação do contrato existente.

Índices atendem URI exata (a unicidade já é índice), releases por vocabulário,
ocorrências por release/URI, notação, label/idioma e seleção temporal de tentativa e
evidência. O índice parcial de evidência inclui apenas sucessos confiáveis.

## Transação e idempotência

`VocabularyCatalogRepository` oferece primitivas sem `commit`. O
`VocabularyCatalogService` abre uma única transação para cadastro, importação ou
registro de resolução. Uma exceção no meio da importação reverte execução, release,
conceitos, ocorrências e labels.

A chave `(vocabulary_id, idempotency_key)` identifica a execução e conserva um
digest determinístico do pedido. Repetir a mesma chave e conteúdo concluído retorna
a release original sem duplicar linhas. Reutilizar a chave com conteúdo divergente,
reusar uma identidade de release com outro digest ou associar uma URI global a
outra identidade falha explicitamente. `ON CONFLICT DO NOTHING` não é usado.
Constraints permanecem a última barreira para corridas concorrentes.

Releases são inseridas como `candidate`; ativação/rollback e imutabilidade após
ativação continuam fora desta issue porque exigem a governança e autorização ainda
pendentes no ADR 0041. Nenhuma release é adotada automaticamente por perfil.

## Catálogo, evidência e tentativa

Os três estados não se confundem:

1. o valor catalogado permanece no snapshot/revisão bibliográfica existente e não
   tem FK para este catálogo;
2. a última evidência confiável é o sucesso mais recente para URI, provedor, versão
   de configuração e contexto;
3. a tentativa mais recente pode ser sucesso ou falha e é consultada separadamente.

`record_resolution` aplica `ResolutionCachePolicy`: persiste expiração do resultado,
janela stale de sucesso e retry de falha. Falhas nunca recebem `reliable`, não
apagam sucesso anterior e não atualizam status/labels catalogados. Evidência expirada
permanece auditável, mas o consumidor deve comparar `expires_at`; durante falha
transitória, `stale_until` limita o uso informativo anterior. Resultado negativo é
escopado por provedor/configuração/contexto e não prova invalidade global.

## Releases completas e parciais

`completeness` é obrigatório. `partial` exige `scope`; ausência nela significa
desconhecido. O serviço não cria evidência negativa por ausência. A projeção para o
`VocabularyRegistry` existente deverá ser feita por um consumidor que selecione uma
release e use `complete=True` apenas para release comprovadamente completa. Pertença
continua por URI exata, nunca por prefixo, label, notação ou texto do fornecedor.

## Uso interno

O fluxo mínimo é:

1. validar `VocabularySourceInput` e `VocabularyInput`, então `register`;
2. construir `VocabularyImport` com `VocabularyReleaseInput` e os contratos
   `ControlledConcept`/`LocalConcept` existentes;
3. chamar `import_release`; a aplicação controla a transação;
4. consultar releases e evidências pelo `VocabularyCatalogRepository`;
5. registrar resultados tipados `ResolutionSuccess`/`ResolutionFailure` com
   `record_resolution` e uma `ResolutionCachePolicy` explícita.

A migration é aplicada somente por `cd apps/api && uv run alembic upgrade head`.
Nunca usar `Base.metadata.create_all()`. O downgrade vazio retorna a `0005`; se houver
qualquer vocabulário, ele recusa perda de dados. Exportação/remoção controlada em
banco descartável é pré-condição para downgrade destrutivo.

## Testes PostgreSQL

Na raiz, `bash scripts/test-postgres.sh` cria container PostgreSQL 17 com `tmpfs`,
bancos novos e sem volumes. Ele migra o banco principal e reserva banco exclusivo
para upgrade/downgrade M08.02. A suíte cobre importação/reimportação, divergência,
rollback, duas releases com a mesma URI, labels/notações repetíveis, release parcial,
separação de evidência/tentativa, constraints, downgrade protegido e reupgrade.

## Limitações e decisões futuras

- ADR 0041 continua proposto; ativação, CAS, RBAC e auditoria administrativa precisam
  de decisão aprovada e issue própria.
- Direitos, formatos, completude e releases reais das fontes LC não foram homologados.
- Não há importador remoto, busca textual normalizada, API/UI ou reconciliação.
- A política de retenção, quotas, RPO/RTO e observabilidade segue pendente.
- A concorrência é protegida por transação/constraints; uma API futura deverá mapear
  violações concorrentes para diagnóstico/409 sem ocultar divergências.
