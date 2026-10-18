# M07 — Editor de catalogação: proposta de desenvolvimento

Status: **planejamento técnico**, 2026-10-18. Não é um ADR nem aprova alterações
incompatíveis. A associação de issues à milestone M07 e a aceitação biblioteconômica
devem ser deliberadas pelos mantenedores.

## Objetivo e referência de estado

Entregar uma interface administrativa utilizável, acessível e bilíngue para o
catalogador criar, consultar, relacionar, validar e revisar registros BIBFRAME
sem precisar editar RDF ou conhecer URIs de propriedades. O editor visual é uma
camada sobre contratos e serviços **já existentes**, não outra fonte de verdade.

M03 definiu Work, Instance, Item semântico, Agent e Subject, perfis, RDF/SHACL,
URIs/UUID, relações e ciclo editorial. M05 expôs operações HTTP, pré-validação,
histórico, relações, listagens e ETag/If-Match. M06 (#52–#59, encerradas)
acrescentou cliente tipado derivado do OpenAPI, BFF Next.js e primeiro fluxo
administrativo de Work (lista, detalhe, criação e edição).
Isso **não comprova** editor completo, integração de autoridades externas,
fluxos visuais editoriais ou homologação institucional. Ver
[API e testes](catalogographic-api-testing.md),
[OpenAPI operacional](catalogographic-openapi.md),
[persistência](catalogographic-persistence.md),
[perfis](catalog-profiles.md) e
[governança](catalogographic-editorial-http.md).

## Referências de projeto

- **MARVA (Library of Congress)**: edição orientada por entidades BIBFRAME,
  recursos e propriedades; referência técnica principal para o editor.
- **Sinopia**: templates e perfis, repetibilidade, vocabulários controlados
  e catalogação Linked Data; não presumir que seu modelo de perfis é compatível
  diretamente com o contrato Libris.
- **FOLIO e Koha**: fluxos profissionais, identificação da edição, conferência
  de campos, vínculos e revisão; não reproduzir telas sem estudo de uso.
- **Open Library** ([site](https://openlibrary.org/),
  [código](https://github.com/internetarchive/openlibrary)): organização intuitiva
  de obra, edições, autores, capas, identificadores, navegação e ações de edição.
  Inspirar a experiência, não importar seu modelo bibliográfico, dependências,
  imagens, dados ou API como decisão implícita. Capas externas, se propostas,
  exigem examinar licenças, origem, privacidade, cache e política de atribuição.
- **Share-VDE**: relações entre entidades e exploração semântica. VuFind/Primo
  servem principalmente à descoberta pública, cujo escopo é posterior.

### Padrão de interação desejado

Na tela de Work, apresentar título principal e variantes, idiomas, contribuições,
assuntos e notas em grupos compreensíveis, com campos repetíveis, rótulos e ajuda
pt/en. Mostrar identificação persistente e estado editorial sem torná-los
editáveis. Navegar para instâncias da obra; na Instance, acessar seus bf:Item
relacionados. Autoridade e vocabulário devem aparecer com rótulo humano
**mais** identidade URI verificável, não somente uma caixa de texto opaca.
Oferecer prévia de validação e mensagens no campo *quando* houver correlação
exata do diagnóstico, sem esconder erros em nível de registro. Apresentar as
edições/capas como contexto, não como prova de disponibilidade física.

## Sequenciamento proposto (issues futuras, sem criar funcionalidades antecipadas)

| Passo | Entrega verificável | Dependência e limite |
| --- | --- | --- |
| M07.01 | Revisar o formulário Work M06; inventário de campos editáveis, preservação no round-trip, acessibilidade, conflito 412 e teste com catalogador | Reutilizar BFF/DTOs reais; corrigir perda de dados antes de ampliar |
| M07.02 | Telas e fluxo Instance: listar/vincular a Work, criar/editar/detalhar com validação | Respeitar `instanceOf`, ETags, publicações e perfis disponíveis |
| M07.03 | Telas de bf:Item associado à Instance | `itemOf` sem implementar inventário, localização física ou circulação |
| M07.04 | Componentes de vínculo/consulta Agent e Subject e edição de autoridades autorizada | Reaproveitar relações/DTOs/API; não prometer VIAF/ORCID/Wikidata automáticos |
| M07.05 | Visualização de diagnósticos, histórico, diferenças e transições editoriais | Matriz de permissões existente; manter revisão publicada exata |
| M07.06 | Integração e aceite com cenário Work → Instance → Item, perfis/estados/erros e evidências | PostgreSQL isolado, navegador quando disponível, aprovação de catalogador separada |

A numeração é proposta interna, não issue criada nem vínculo automático à milestone 7.

## Invariantes obrigatórias

1. **Semântica**: Work ≠ Instance ≠ bf:Item ≠ exemplar de inventário/circulação.
   Work/Instance/Item mantêm identidades e revisões independentes; vínculos são
   explícitos e usam contratos de relações. Agent/Subject são entidades
   compartilhadas com sua própria identidade e autorização.
2. **Integridade sem perdas**: não achatar objetos estruturados em rótulos, não
   descartar extensões semânticas, URI, idioma, datatype, papéis, valores repetidos
   ou propriedades presentes no snapshot que a tela não edita. Em PATCH preservar
   distinções entre campo omitido, `null` e `[]`; proibir escrita destrutiva
   quando a forma não representar o dado existente.
3. **Perfis e validação**: perfis declarativos, SHACL e contratos DTO são camadas
   distintas. Um perfil 2.0 descrito não implica que sua escrita já está adotada.
   Selecionar releases registradas e manter registros históricos fixados à release.
   A pré-validação ajuda a UX; backend revalida e conserva diagnósticos completos.
   Não criar uma engine universal de formulários antes de um caso validado.
4. **Concorrência e publicação**: usar ETag editorial atual e `If-Match`;
   412 requer reconciliar sem perder edição; 409/422 expõem informações seguras
   e acionáveis. Salvar rascunho não é publicar; publicar exige revisão aprovada,
   permissões e as transições HTTP existentes. A versão pública continua a revisão
   publicada até outra publicação autorizada.
5. **Segurança**: manter autenticação OIDC no BFF, CSRF e autorização por
   operação no FastAPI. Não enviar tokens ao browser nem confiar em papéis locais.
   Impedir exposição editorial a usuário anônimo e respeitar confidencialidade.
6. **Compatibilidade**: usar o cliente OpenAPI gerado, regenerando-o a partir
   da fonte quando estritamente necessário. Nenhuma alteração silenciosa nos
   contratos v1, perfis e shapes históricos, migrações ou snapshot; mudanças de
   modelo exigem ADR, versão apropriada e testes. Não criar triplestore/outbox
   sem caso de uso autorizado.

## Critérios de aceite e verificações

- Catalogador autenticado consegue Work → Instance → bf:Item via UI e verifica
  no PostgreSQL descartável UUID, URI, relações e revisões após recarregar.
- Editar um registro com campos complexos, repetidos e extensões existentes não
  causa perda silenciosa, incluindo após falha de validação e concorrência.
- Cada tipo usa perfis autorizados, rótulos adequados e erros SHACL/Problem Details
  com retorno por campo ou por registro, preservando request ID quando disponível.
- Papéis cataloger/authority-manager/reviewer/publisher/admin são exercitados,
  incluindo 401/403 e publicações inacessíveis a usuários anônimos.
- Interface localizada pt/en, foco/teclado, nomes acessíveis, estados de
  carregamento/erro e confirmação de ações sensíveis.
- Testes de frontend/BFF/API e, onde pertinente, `bash scripts/check.sh` e
  `bash scripts/test-postgres.sh` com banco descartável. Reportar comandos
  realmente executados, skips e bloqueios sem afirmar homologação institucional.
- Teste funcional com catalogador documenta ajustes, limites e aceite humano
  independentemente de CI e testes técnicos.

## Fora do escopo por padrão

Importação/exportação MARC21 (M08), reconciliação externa automática, OPAC de
descoberta e Elasticsearch (M09), mecanismo de circulação, exemplares físicos,
reservas, aquisições, periódicos, IA catalogadora, editor genérico de ontologias
e implantação de produção. Transformar qualquer item em escopo M07 exige issue,
justificativa técnica e deliberação explícita, não apenas inspiração em outro site.
