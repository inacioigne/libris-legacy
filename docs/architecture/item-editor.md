# Editor de Item — M07.03 / #63

Interface administrativa pt/en sobre DTOs v1, cliente OpenAPI e persistência
M03–M05 existentes. [Evidências datadas](item-editor-m0703-verification-2026-10-18.md).

## Uso e navegação

1. Abra uma Work e consulte **Instâncias vinculadas à obra**.
2. Abra a Instance desejada. A seção **Itens semânticos vinculados** lista os
   vínculos persistidos; **Novo item semântico** aparece com permissão de criação.
3. Confira a Instance, informe pelo menos um identificador bibliográfico do tipo
   Local e, opcionalmente, notas e histórico de custódia. Use **Validar antes de
   criar** e depois **Salvar item**. O detalhe permite retornar à Instance/Work.
4. **Editar item semântico** carrega a geração corrente e seu ETag. Salve e
   consulte novamente. O vínculo com a Instance não é editável neste formulário.

Work é obra intelectual; Instance é manifestação/edição; **bf:Item é recurso
semântico individual associado à Instance**. Item não cadastra automaticamente
exemplar de inventário, barcode, localização física, disponibilidade ou empréstimo.
Essa distinção aparece na criação, edição, detalhe e seção de itens vinculados.
Marcas de estante históricas são dados semânticos preservados, não localização
operacional administrada por esta entrega.

Rotas: `/[locale]/admin/catalog/items`, `/items/new?instance=<UUID>`,
`/items/[id]` e `/items/[id]/edit`, sob o mesmo prefixo administrativo.
Criação sem Instance orienta a selecioná-la na listagem existente; não infere
vínculos de título, ISBN ou sufixo de URI e não duplica Work/Instance.

As seções contextuais usam GET `/{collection}/{id}/relationships` com
`include_inverses=true`, filtrando `bf:hasInstance`/`bf:hasItem` e referências
locais do tipo correto. Não gravam inversas nos pais. A lista geral de Items usa
GET `/items` e seu cursor opaco; ela é distinta da lista contextual de uma Instance.
A API de relações não pagina inversas e atualmente percorre snapshots: a UI
herda esse custo; não se declara escalabilidade de produção nem busca textual.
As linhas apresentam URI/UUID fornecidos pelo contrato, sem fabricar títulos.

## Superfície editável e preservação

| Campo | Comportamento |
| --- | --- |
| `item_of` | Referência local explícita na criação, conferida por UUID/URI no BFF e no backend; somente leitura na edição |
| `identified_by` | Tipo, valor e fontes repetíveis; pelo menos um Local exigido pelo backend |
| `custodial_history` | Textos repetíveis e idiomas |
| `note` | Rótulos/idiomas repetíveis e tipos de nota |
| `held_by`, `shelf_mark`, `provenance` | Consultáveis, somente leitura; omitidos do PATCH |
| UUID/URI, perfil, revisão, estado e proveniência técnica | Somente leitura; não são campos autorizadores do payload |
| Extensões RDF não projetadas | Permanecem no snapshot integral do servidor |

O formulário copia estruturas, não resumos textuais. PATCH envia apenas grupos
alterados, preservando omissão, `null` e `[]`; a UI usa `[]` para remoção explícita
de linhas opcionais. O BFF rejeita chaves fora da superfície editável e não aceita
troca de `item_of`, perfil, identidade ou ator por esse formulário. A API continua
oferecendo seu contrato v1 completo a outros consumidores autorizados.

O adaptador existente aplica o PATCH sobre o grafo integral. Além das barreiras
anteriores para auxiliares anotados/nomeados, Item compara por isomorfismo a
projeção dos grupos substituídos com seus subgrafos originais. Se o DTO não pode
representar datatypes explícitos, identidade de auxiliar ou extensões, rejeita a
substituição como `400 / request.malformed`, preservando o contrato de erro
existente. Grupos não alterados continuam editáveis independentemente dessa
limitação. Não há migração nem reconstrução do snapshot completo pelo navegador.

POST fixa `urn:libris:profile:monograph:v1:1`, release histórica 1.1. PATCH reutiliza
o perfil armazenado. Perfis 2.0 não são adotados para escrita. Pré-validação de
criação usa POST `/items/validation` com envelope DTO e a release suportada, sem
persistir. Na edição, validação efetiva e transacional ocorre ao salvar; não se
simula pré-validação completa reconstruindo RDF a partir do Read limitado.

## Concorrência, segurança e diagnósticos

O PATCH envia exatamente o ETag carregado em `If-Match`; usa o novo ETag da
resposta. Metadados editoriais só são combinados se o ETag de GET `/editorial`
corresponder ao conteúdo carregado. Ausência de metadados é mostrada explicitamente.

412 mantém a entrada, bloqueia reenvio e oferece carregar a versão atual. Cada
grupo alterado exige escolher valores locais ou remotos; grupos intactos adotam
a versão remota. Descartar alterações pede confirmação, aplicar escolhas não
salva e não há retry automático. Se outra sessão trocar `itemOf`, a comparação
fica bloqueada e orienta consultar o registro em outra aba antes de reiniciar,
preservando o formulário local. Navegação com alterações pendentes também avisa.

Server Actions mantêm sessão OIDC server-side, proteção de origem/CSRF e cliente
gerado. FastAPI decide autorização, validação e CAS. Permissões de leitura
editorial continuam conjuntas; catalogador não ganha aprovação/publicação.
Não são introduzidos papéis, endpoints, tabelas, migrações ou dependências.
Tokens não atravessam a fronteira cliente. 401/403 não apresentam identidade ou
diagnósticos privados; 400/404/409/412/422/428 e indisponibilidade têm orientação
pt/en, códigos e request ID. Correlação exata aponta para controles editáveis;
demais diagnósticos permanecem visíveis no registro.

Salvar cria/atualiza rascunho ou candidata segundo os serviços existentes; não
submete, aprova ou publica. Estado/revisão/perfil são exibidos, sem antecipar a
interface de transições da M07.05. A publicação anterior permanece selecionada
até o comando autorizado da revisão aprovada; rascunhos não vão ao OPAC.

## Verificação

```bash
bash scripts/check.sh
bash scripts/test-postgres.sh
# Usa o build anterior, banco novo em tmpfs e perfil Chromium temporário:
bash scripts/test-item-editor.sh
```

O runner de navegador inicia Next.js e FastAPI reais com JWT assinado e discovery/
JWKS sintéticos; não é login Keycloak real. Cria banco novo e remove somente seus
recursos. Testes do componente usam React/Chromium com ações simuladas. Aceite por
catalogador, auditoria WCAG completa e homologação institucional são separados.
