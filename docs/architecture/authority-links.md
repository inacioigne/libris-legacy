# M07.04 — Consulta e vínculos de autoridades

Implementação da [issue #64](https://github.com/inacioigne/libris/issues/64).
As telas de detalhe administrativo de Work, Instance e Item, em `/pt` e `/en`,
incluem o painel **Agentes e assuntos vinculados**. Os formulários anteriores de
conteúdo permanecem disponíveis. A nova operação é pontual sobre relações, sem
reconstruir o registro a partir do DTO de leitura.

## Consulta e seleção

O seletor consulta Agent ou Subject pelo BFF autenticado, apresenta todos os
rótulos preferidos, UUID e URI armazenada, e usa UUID como chave da lista. Dois
registros de mesmo nome continuam distinguíveis pela URI, inclusive no nome
acessível do botão de seleção. Os controles nativos permitem Tab/Enter, possuem
labels e estados de carregamento, vazio e erro. Após consultar, o foco vai aos
resultados; após selecionar, à identificação escolhida; após escrever, ao resumo.

A API atual não oferece pesquisa textual. O filtro de rótulo é explicitamente
restrito à página carregada, de 20 registros. UUID/URI são filtros exatos opcionais;
nenhum é necessário para navegar e selecionar. O próximo cursor conserva os
filtros originais. Consultar novamente retorna à primeira página. Não há busca
Elasticsearch, reconciliação ou acesso a VIAF/ORCID/ISNI/Wikidata.

## Operações e matriz semântica

| Origem | Seleção | Operação |
| --- | --- | --- |
| Work | Agent | Contribution com um ou mais papéis por URI |
| Work | Subject | vínculo direto bf:subject |
| Instance | Agent | publicador em atividade existente identificada na coleção, ou nova atividade |
| Item | Agent do tipo Organization | vínculo direto bf:heldBy, sem inventário/circulação |

O usuário seleciona uma autoridade local, escolhe os papéis quando pertinentes e
confirma o vínculo. Autor/editor/tradutor têm rótulos traduzidos; outros papéis
podem ser informados por URI. A remoção exige confirmação e envia o vínculo completo,
incluindo todos os papéis ou activity_key, sem criar/editar a autoridade.
Duplicação usa identidade e conjunto de papéis; mesmo agente com papéis distintos
não é tratado automaticamente como duplicado. A validação final pertence à API.

A tela identifica referências locais, externas e não reconciliadas sem inferir
localidade por prefixo. Somente referências explicitamente locais são consultadas
para obter rótulos; erro/permissão insuficiente mantém UUID/URI e aviso de rótulo
indisponível. Externas não são resolvidas. O componente apresenta referências
não reconciliadas, mas a política persistente atual as rejeita: não fabrica URI,
identidade nem entidade para torná-las graváveis. Não oferece criação de novas
referências externas/provisórias neste incremento.

Atividades usam chaves locais à representação RDF, nunca UUIDs de autoridades.
Após comparar/adotar outra revisão de Instance, a atividade deve ser escolhida
novamente. Atividades sem agentes não aparecem na coleção de vínculos; sua edição
de lugar/data continua no formulário Instance. A criação de uma atividade pelo
painel acrescenta somente o agente; dados descritivos são editados no formulário.

## Endpoints e segurança

| Endpoint existente | Uso |
| --- | --- |
| GET /api/v1/catalog/agents | seleção paginada Agent |
| GET /api/v1/catalog/subjects | seleção paginada Subject |
| GET /api/v1/catalog/agents/{id} | rótulos de referências locais |
| GET /api/v1/catalog/subjects/{id} | rótulos de referências locais |
| GET /api/v1/catalog/{collection}/{id}/relationships | coleção própria, sem inversas, e ETag relacional |
| PATCH na mesma rota | uma operação add/remove com o vínculo estruturado e If-Match relacional |

O cliente OpenAPI gerado, sessão cifrada no servidor, Bearer server-side,
no-store/private, correlação e proteção same-origin/CSRF existentes são reutilizados.
As server actions de escrita verificam origem antes do acesso. O FastAPI revalida
ator, permissões, tipos e operação; propriedades recebidas do browser não concedem
permissões. Nenhum contrato v1, schema OpenAPI, perfil, SHACL, migração, dependência
ou código de persistência foi alterado.

| Operação | Permissões efetivas |
| --- | --- |
| Consultar autoridades editoriais | authority:read + metadata_revision:read |
| Consultar relações bibliográficas | bibliographic:read + metadata_revision:read no FastAPI |
| Adicionar/remover vínculos bibliográficos | bibliographic:update, com estado editável e ETag |
| Criar/editar a própria autoridade pela API existente | authority:manage; não oferecido pelo painel de vínculos |

O papel cataloger tem leitura editorial e atualização bibliográfica, sem
authority:manage. O authority-manager isolado tem authority:manage e authority:read,
mas **não** metadata_revision:read nem bibliographic:update. Não ganha acesso ao
seletor editorial/vínculos por seu nome; combinações de papéis usam as permissões
efetivas existentes. Não foi acrescentado CRUD visual de autoridades nem alterada
a política para contornar essa distinção. Os testes verificam edição permitida ao
manager na API e edição negada ao cataloger.

## Integridade, concorrência e erros

O painel envia somente `operations: [{op, links: [link]}]`, jamais PATCH de listas
inteiras ou do snapshot da autoridade. Portanto não confunde campo omitido, null e
lista vazia dos DTOs de conteúdo. A API resolve UUID/URI/tipo, valida duplicações,
SHACL e estado sob o gate existente e cria revisão/auditoria do proprietário na
mesma transação. Remover contribuição anotada continua sendo recusado quando
perderia extensões; publicação remove somente a aresta de agente e conserva data,
lugar e demais propriedades. A autoridade conserva revisão, snapshot e metadados.

Usa-se exclusivamente o ETag de `relationships` sem inversas; o ETag de edição
do recurso ou de sua autoridade não serve. Após sucesso, a resposta atualiza a
coleção/ETag e o Next atualiza os demais dados da página. Botões ficam bloqueados
durante escrita e um lock local impede envio duplo.

400/401/403/404/409/412/422/428 mantêm status e diagnósticos seguros do BFF.
Problemas de autenticação/autorização não expõem identidade nem diagnósticos
privados. Seleção, papéis e vínculos continuam presentes em falhas. Não há retry
automático de escrita. Em 412, a escrita é bloqueada; **Carregar vínculos atuais
para comparação** não troca o ETag. **Usar esta versão e manter minha seleção**
adota explicitamente a versão carregada, sem escrever; o usuário confirma a
operação novamente. Remoções devem ser confirmadas sobre a coleção atual.

## Reprodução e limites

```bash
bash scripts/check.sh
bash scripts/test-postgres.sh
# Exige build web já produzido; cria cluster tmpfs novo e exclusivo.
bash scripts/test-authority-editor.sh
```

O último script segue os runners de M07.02/M07.03: executa Chromium de componentes
e fluxo real Next/BFF/FastAPI/PostgreSQL com JWT assinado por provedor de teste.
Não é ensaio Keycloak real nem homologação institucional. Evidências, falhas
intermediárias e pendências estão no [relatório próprio](authority-links-m0704-verification-2026-10-18.md).
