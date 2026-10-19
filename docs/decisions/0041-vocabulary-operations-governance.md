# ADR 0041 — Operação e governança de vocabulários controlados

- **Status:** Proposto para revisão
- **Data:** 2026-10-19
- **Issue:** [#67](https://github.com/inacioigne/libris/issues/67)
- **Decisões relacionadas:** [ADR 0024](0024-controlled-vocabularies.md),
  [ADR 0021](0021-declarative-catalog-profiles.md),
  [ADR 0027](0027-provenance-audit-versioning.md),
  [ADR 0031](0031-operation-authorization.md) e
  [ADR 0040](0040-catalogographic-persistence-stabilization.md)

## Contexto e problema

A M03 definiu os contratos internos `ControlledConcept`, `LocalConcept`,
`VocabularyRegistry`, `VocabularyProvider` e `ResolutionCachePolicy`, mas não
implementou conectores, catálogo persistente, cache ou administração operacional.
Os DTOs bibliográficos públicos v1 continuam usando `ResourceReference` (somente
URI), e os perfis 2.0 conseguem declarar uma URI de vocabulário sem fixar uma
release operacional. A M07 edita essas URIs diretamente e não faz reconciliação
remota.

A M08 precisa tornar vocabulários externos utilizáveis sem transformar a rede em
dependência de validação, sem aceitar prefixo ou rótulo como prova de pertença e
sem alterar registros históricos quando uma fonte muda. Também precisa governar
proveniência, direitos, atualização, ativação e rollback.

## Forças e restrições

- BIBFRAME/RDF e a URI canônica são a identidade semântica.
- Contratos públicos v1, UUID/URI, ETag/If-Match, revisões, snapshots e releases
  históricas devem permanecer compatíveis.
- Uma indisponibilidade externa não equivale a invalidade semântica.
- Perfis, releases de vocabulário e revisões bibliográficas têm ciclos próprios.
- PostgreSQL continua fonte de verdade; cache é reconstruível.
- O provedor externo é autoritativo sobre sua publicação; o Libris é autoritativo
  apenas sobre seleção institucional, evidência capturada e releases internos.
- A fonte pode não oferecer releases formais, coleção completa, licença clara ou
  rótulos em português.

## Alternativas consideradas

1. **Resolver e validar online em cada escrita.** Rejeitada: acopla disponibilidade
   externa à catalogação, amplia SSRF/rate limiting e contradiz o ADR 0024.
2. **Aceitar namespace/notação como prova de pertença.** Rejeitada: prefixos não
   comprovam publicação de um conceito e conjuntos parciais não comprovam ausência.
3. **Copiar cada fonte e tratá-la como autoritativa.** Rejeitada: inventa autoridade
   local, perde proveniência e confunde catálogo operacional com fonte externa.
4. **Guardar apenas cache efêmero.** Rejeitada: não sustenta releases imutáveis,
   auditoria, rollback nem validação reproduzível.
5. **Registry governado + releases internos imutáveis + catálogo/evidência separados.**
   Escolhida. Reutiliza os contratos existentes e permite adoção incremental.

## Decisão arquitetural

Adotar um único serviço operacional dentro do módulo bibliográfico, composto por
responsabilidades separadas:

1. O **registry governado** amplia operacionalmente, sem substituir, o conceito de
   `VocabularyRegistry`. Ele cadastra fonte permitida, mantenedor, namespace,
   idiomas, direitos, estratégia de resolução, política de atualização, estado
   administrativo e uso em perfis.
2. Um **adapter `VocabularyProvider` por fonte/estratégia** busca somente hosts e
   caminhos allowlisted, normaliza respostas, conserva bytes/digest/proveniência e
   produz resultados tipados. Não é chamado no startup nem na validação síncrona.
3. Cada atualização explícita cria uma **release interna candidata e imutável**.
   Se a fonte declara uma release, o identificador declarado é conservado como
   dado externo. Sempre existe um identificador interno e um digest calculado sobre
   a representação normalizada; o Libris nunca inventa uma versão externa.
4. O **catálogo de conceitos** é uma projeção consultável de conceitos conhecidos.
   Não é cópia autoritativa da fonte e não substitui referências bibliográficas.
5. A **evidência de pertença** é um fato separado, positivo, negativo conclusivo ou
   inconclusivo, ligado à fonte e release. Prefixo, notação e rótulo não são prova.
6. O **cache** apenas acelera resolução/pesquisa. É limitado por provider, release,
   URI, representação e idioma; falhas transitórias podem usar último sucesso
   dentro de `stale-if-unavailable`, sem promover esse cache a nova evidência.
7. O **snapshot bibliográfico** preserva a URI usada, o rótulo efetivamente gravado
   quando o contrato o comportar, o perfil e a revisão. Atualizações externas nunca
   reescrevem snapshots. A projeção v1 URI-only continua válida; metadados de
   conceito só entram em uma revisão explícita e compatível futura.

Estados administrativos mínimos de vocabulário: `draft`, `active`, `suspended` e
`retired`. Estados de release: `importing`, `candidate`, `active`, `rejected` e
`superseded`. Ativação troca atomicamente o ponteiro ativo; rollback reativa uma
release anterior já validada, sem apagar releases nem revisar registros.

## Responsabilidades

O mantenedor externo publica e descreve a fonte. O administrador institucional
autoriza vocabulários, direitos e operadores. O curador define completude,
qualidade, idiomas e aceite. O operador inicia ingestão e rollback. O serviço
automatizado coleta e normaliza, mas não autoativa. O catalogador apenas pesquisa e
seleciona releases autorizadas pelo perfil. Separação de funções entre operação e
ativação é recomendada; ativação exige uma permissão própria futura.

## Modelo de dados

Nenhuma migração é criada na M08.01. A implementação futura deverá acrescentar,
incrementalmente, o mínimo equivalente a:

- configuração de registry, com identificador estável e revisão/CAS;
- release interna imutável, origem externa opcional, digest, cobertura e estado;
- conceito conhecido por `(release_id, concept_uri)`, com notação e estado;
- rótulo por conceito, idioma, valor, origem e categoria de proveniência;
- evidência de pertença por release, URI, tipo, resultado e instante;
- execução de atualização/auditoria, idempotency key e resultados.

Chaves estrangeiras devem impedir conceitos sem release; unicidade deve impedir
duas releases internas com o mesmo `(vocabulary_id, digest)` e duplicação de URI na
mesma release. Linhas de releases ativadas são imutáveis. O ponteiro ativo e o
evento de ativação/rollback são alterados na mesma transação. Dados bibliográficos
continuam nas tabelas `experimental_semantic_*`; não haverá FK de snapshots para o
catálogo reconstruível.

## Governança de releases, proveniência e licenciamento

Atualizações são sempre iniciadas explicitamente, idempotentes, validadas fora do
caminho de escrita e ativadas em transação. Importações parciais declaram escopo e
nunca transformam ausência em prova negativa. Releases anteriores são retidas.
Rótulos distinguem `provider_official`, `authorized_secondary`,
`institutional_translation` e `bibliographic_snapshot`; traduções institucionais
jamais são anunciadas como rótulos oficiais da fonte.

Cada registry registra página oficial, meio técnico de obtenção, condição de acesso,
atribuição, restrições e evidência documental. “Publicado na web” não significa
licença irrestrita. Direitos não verificados permanecem `pending`; nesse estado não
se aprova redistribuição do dataset, embora referências por URI possam continuar
conforme análise institucional.

## Integração com contratos existentes

- `ControlledConcept`/`LocalConcept` continuam contratos internos 1.0.
- `VocabularyProvider.resolve(uri)` permanece a fronteira; ingestão em lote será
  serviço de aplicação separado que reutiliza adapters, não um novo provider público.
- `VocabularyRegistry.membership()` continua consumindo um snapshot imutável de
  evidência selecionado pelo chamador.
- `ReferenceValue.vocabulary` continua válido. Uma extensão futura opcional pode
  adicionar política/release por campo em uma nova release de perfil 2.x; perfis
  2.0.0 existentes não mudam.
- DTOs v1 e `ResourceReference` não ganham campos obrigatórios. Uma API operacional
  futura terá contrato próprio e versionado.
- RDF/JSON-LD preservam a URI canônica. Cache e rótulos de apresentação não alteram
  o grafo bibliográfico sem nova revisão explícita.

## Segurança e autorização

Reutilizar OIDC/RBAC e BFF/CSRF. Consulta pública pode expor somente conceitos de
vocabulários e releases ativos cuja redistribuição seja permitida. Configuração,
ingestão, validação, ativação, rollback, permissões e auditoria exigem operações
específicas deny-by-default; `system:admin` isolado não deve conceder ações futuras
automaticamente.

Adapters usam HTTPS quando disponível, DNS/host/path allowlist, bloqueio de IPs
privados e redirecionamentos fora da allowlist, timeout, limites de resposta,
content type e parser limitados, sem contextos JSON-LD remotos. Devem aplicar rate
limit, backoff com jitter, circuit breaker, logs sem payload sensível, métricas e
correlação. Importações têm tamanho/contagem máximos e publicação atômica.

## Compatibilidade retroativa

Esta decisão não altera código, schema, OpenAPI nem dados. Conceitos já gravados
continuam válidos segundo a revisão e evidência então aplicáveis. Novas políticas
não são aplicadas retroativamente a snapshots históricos; publicação futura pode
exigir evidência conforme o perfil/release selecionado sem mudar leituras antigas.

## Consequências

Positivas: validação reproduzível, operação offline, rollback determinístico,
proveniência explícita e preservação histórica. Negativas: maior armazenamento,
curadoria e necessidade de distinguir muitos estados; dados exibidos podem ficar
deliberadamente diferentes da fonte atual até uma revisão explícita.

## Riscos

- Fontes sem release ou licença formal exigirão digest interno e decisão humana.
- Uma importação “completa” pode estar incompleta por falha do provedor.
- Rótulos multilíngues podem vir de origens com condições diferentes.
- Sem quotas, imports malformados podem causar exaustão de recursos.
- A API v1 URI-only não transporta toda a evidência; não se deve alegar o contrário.

## Migração e rollback

Fase 1 cria tabelas novas vazias e permissões sem tocar snapshots. Fase 2 cadastra
registries desativados. Fase 3 ingere releases candidatas. Fase 4 valida contagens,
digest, pertença, rótulos e direitos. Fase 5 ativa uma release atomicamente e só
então habilita perfis novos. Downgrade de schema deve recusar perda de releases ou
auditoria; recuperação restaura backup e reativa a release anterior. Não há backfill
automático de identidade, rótulo ou proveniência em revisões históricas.

## Questões abertas

- Quais condições jurídicas específicas regem a redistribuição de cada dataset LC?
- Qual formato oficial e estável será preferido após ensaio operacional do id.loc.gov?
- Quais permissões e separação de funções serão adicionadas ao enum vigente?
- Quais limites de volume/latência e política de retenção serão homologados?
- A extensão de perfil deve fixar release exata ou uma política governada resolvida
  e persistida no instante da revisão? A proposta inicial admite ambos, mas exige
  que a release efetiva seja registrada na evidência de validação.
