# Editor de Instance — M07.02 / #62

Interface administrativa pt/en implementada sobre o DTO v1 e a persistência
M03–M05. Ver [evidências técnicas desta entrega](instance-editor-m0702-verification-2026-10-18.md).
Instance descreve manifestação/edição bibliográfica; não administra inventário,
circulação nem disponibilidade física.

## Rotas e navegação

- `/[locale]/admin/catalog/instances`: listagem real, filtros exatos por UUID,
  URI, perfil e estado editorial; limite 1–100 e cursor retornado pela API.
- `/[locale]/admin/catalog/instances/new`: seleção de Work existente e criação.
- `/[locale]/admin/catalog/instances/[id]`: descrição estruturada, vínculo,
  identidade imutável e metadados editoriais da geração carregada.
- `/[locale]/admin/catalog/instances/[id]/edit`: edição parcial e comparação
  explícita em caso de conflito.

O menu administrativo inclui a listagem. No detalhe de Work, a ação autorizada
“Nova instância” abre o formulário com a Work selecionada por UUID; o servidor
consulta a identidade antes de renderizar. A seleção também pode ser feita no
formulário, com páginas de dez Works e filtros por UUID/URI exatos. Cada opção
mostra título, UUID e URI e permite consultar a obra em outra aba.

Não há busca textual nem filtro de Instances por Work no contrato de listagem.
O link do contexto de Work abre a lista geral e informa essa limitação. A lista
de Instances contém título próprio (ou ausência explícita), UUID, edição,
identificadores e referência à Work. O estado editorial serve como filtro;
não é inventado nas linhas, pois não integra o DTO Read da listagem. O detalhe
consulta o estado separadamente e resolve a Work por identidade exata.

## Campos e perfil

O POST existente fixa `urn:libris:profile:monograph:v1:1` (release 1.1). A edição
preserva o perfil persistido; não oferece migração ou seletor fictício de perfil.
Perfis declarativos 2.0 não estão habilitados para escrita nessa rota.

| Campo v1 | Controle / preservação |
| --- | --- |
| `instance_of` | Seleção explícita de Work existente; UUID + URI + tipo local |
| `title` | Opcional; Title/VariantTitle, títulos/subtítulos repetíveis com idioma |
| `edition_statement` | Literais repetíveis com idioma |
| `provision_activity` | Publicações repetíveis; agentes, lugares e datas aninhados |
| `identified_by` | Identifier/Local/Isbn, valor e fontes por URI |
| `extent` | Estruturas repetíveis de rótulos com idioma |
| `carrier` | Referências URI repetíveis |
| `note` | Rótulos com idioma e tipos de nota repetíveis |

Datas preservam `xsd:string`, `xsd:gYear` e `xsd:date`; o backend valida os
lexicais. Referências estruturadas de agentes permanecem íntegras e somente
leitura dentro da linha; sua remoção é explícita. Novas referências de agente
usam URI, sem seleção/reconciliação remota antecipada da M07.04. Não há campos
arbitrários de RDF nem novos datatypes nos literais do DTO. Propriedades de
perfis 2.0 como `schema:encodingFormat` e `schema:dateCreated` não podem ser
criadas por este DTO; exigiriam decisão/versão própria.

Títulos/notas reutilizam os controles do editor Work. Datas, fontes, idiomas e
valores existentes são copiados estruturalmente, sem transformação em strings
resumidas para edição. Resumos de leitura não são usados como payloads.

## Relação, transação e atualização parcial

`InstanceCreate.instance_of` aceita referência local. O BFF consulta a Work
selecionada para conferir acesso e URI; o backend revalida UUID/URI/classe sob o
protocolo transacional existente. Um único POST cria identidade, snapshot,
vínculo, estado draft e evento. Não se cria uma Work nem se usa uma segunda
operação de relacionamento; portanto não há uma gravação parcial a compensar.
Uma troca explícita de Work usa o mesmo campo no PATCH da Instance, sem revisar
a Work. `bf:hasInstance` não é gravado como inversa na Work.

O rascunho conserva os oito grupos v1 completos. O PATCH inclui somente grupos
cujo valor mudou. Campo omitido preserva o grafo; `null` limpa somente campos
opcionais; `[]` substitui explicitamente por uma lista vazia. A UI usa `[]` ao
remover todas as linhas. `instance_of=null` é recusado. UUID, URI, perfil,
estado, ator e revisão nunca são campos editáveis ou argumentos autorizadores.

O backend aplica o PATCH a uma cópia do grafo integral. Extensões independentes
permanecem no servidor. Se substituir um auxiliar apagaria RDF fora da projeção,
o adaptador rejeita a operação; a interface conserva a entrada e os diagnósticos.
Esta entrega também recusa substituir um auxiliar exclusivo com URI própria:
o DTO não transporta essa identidade e sua substituição apagaria a URI. Auxiliares
não alterados permanecem intactos. O bloqueio é no adaptador já existente, sem
nova persistência nem alteração de schemas/rotas.
Não há garantia falsa de representar toda extensão no formulário v1.

Limite já existente no OpenAPI: `InstanceUpdate.instance_of` é descrito como
referência URI, embora o validador Python aceite também referências discriminadas.
A fronteira de parsing do editor conserva a referência local validada e o teste
PostgreSQL exercita esse payload real. Nenhum arquivo gerado foi editado; a
correção geral dessa descrição OpenAPI exige revisão das baselines, separadamente.

## Concorrência, erros e segurança

O GET carrega o ETag de edição; o PATCH envia exatamente esse valor em If-Match.
O sucesso usa o ETag retornado pelo PATCH. Estado/revisão/perfil são acrescentados
apenas quando a resposta editorial possui o mesmo ETag, evitando combinar
gerações diferentes. Metadados indisponíveis são indicados explicitamente.

412 conserva o formulário e bloqueia novo envio até carregar a versão corrente e
escolher, por grupo alterado, os valores locais ou remotos. Grupos não alterados
adotam a versão remota. A comparação não salva nem faz retry automático.
Descartar um grupo local pede confirmação. Navegação com alterações pendentes
também avisa. 409 permite consultar o estado em outra aba sem apagar a entrada.

400/401/403/404/409/412/422/428 e indisponibilidade recebem orientação localizada.
Códigos, caminhos públicos e request ID são conservados; correspondências exatas
de campo aparecem junto ao controle. Diagnósticos sem correspondência ficam no
formulário. Identidade, tokens e detalhes internos não são apresentados em erros
de autenticação/autorização. Pré-validação de criação usa o endpoint existente,
sem persistir; gravações sempre passam pela validação efetiva da API.

Server Actions reutilizam a sessão OIDC server-side, o cliente OpenAPI e a
proteção de origem/CSRF existente. O navegador não chama diretamente FastAPI.
Guardas das páginas, permissões no BFF e autorização efetiva no FastAPI continuam
independentes; esconder ações não concede autorização. Catalogadores não recebem
permissões de aprovação/publicação. Salvar não publica; candidatas permanecem
separadas da revisão pública. O detalhe expõe proveniência autorizada da revisão,
sem implementar uma nova interface de auditoria/transições da M07.05.

## Verificação local

Com Node 24, Python/uv do projeto, dependências dos locks, Docker e Chromium:

```bash
bash scripts/check.sh
bash scripts/test-postgres.sh
# Após npm run build (também executado pelo gate):
bash scripts/test-instance-editor.sh
```

O último runner cria um cluster PostgreSQL novo em tmpfs, porta dinâmica loopback,
sem ler URLs de bancos existentes. O teste cria seu próprio banco, executa
Alembic e inicia Next.js/FastAPI reais. Ao terminar, remove apenas os recursos
que criou. Usa JWT assinado e discovery/JWKS de teste; não é login Keycloak real.
O teste de componente usa React/Chromium reais com ações simuladas, separado do
fluxo HTTP com persistência real. Testes opt-in ignorados não provam integração.

Aceite por catalogador, auditoria WCAG completa e homologação institucional
permanecem avaliações humanas separadas.
