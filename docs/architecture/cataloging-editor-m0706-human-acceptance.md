# Roteiro de homologação humana M07.06

Issue: [#66 — M07.06](https://github.com/inacioigne/libris/issues/66).

Este formulário deve ser preenchido por profissional de biblioteconomia em ambiente
isolado, com dados sintéticos e contas de teste. Testes automatizados não substituem
esta avaliação. Não registrar senhas, tokens, dados pessoais ou conteúdo sigiloso.

## Identificação

- Avaliador(a): ______________________________________________
- Formação/função: __________________________________________
- Instituição/unidade: ______________________________________
- Data e horário: ____________________________________________
- Versão/hash avaliado: ______________________________________
- Ambiente: _________________________________________________

## Pré-condições

- [ ] Ambiente isolado contém FastAPI, BFF, PostgreSQL descartável e OIDC de teste.
- [ ] Há contas separadas de cataloger, authority-manager, reviewer e publisher.
- [ ] Há Agent e Subject sintéticos com UUID, URI e rótulos verificáveis.
- [ ] O avaliador foi informado de que bf:Item não representa circulação/inventário.
- [ ] Não há credenciais ou dados reais na ficha/evidências.

## Cenários

Em cada linha, marcar APROVADO, REPROVADO ou NÃO EXECUTADO e anotar a evidência.

| Cenário | Resultado | Observações/evidência |
| --- | --- | --- |
| Entrar como cataloger e localizar o editor Work |  |  |
| Criar Work com títulos pt/en, título variante, idioma, notas repetidas, Agent com dois papéis e Subject |  |  |
| Salvar, recarregar e conferir UUID, URI, perfil, revisão e ausência na leitura pública |  |  |
| Criar Instance vinculada explicitamente à Work; conferir `instanceOf` e navegação inversa |  |  |
| Criar bf:Item vinculado explicitamente à Instance; conferir `itemOf` e ausência de funções de circulação |  |  |
| Consultar/vincular Agent e Subject por rótulo mais URI; recarregar sem achatamento |  |  |
| Pré-validar e compreender diagnósticos de campo/registro |  |  |
| Submeter como cataloger; tentar e confirmar barreiras de permissão |  |  |
| Aprovar/devolver como reviewer |  |  |
| Publicar como publisher e conferir a revisão pública exata |  |  |
| Editar após publicar e confirmar que a candidata não substitui a publicação anterior |  |  |
| Produzir conflito 412 em duas sessões; preservar entrada e reconciliar explicitamente |  |  |
| Conferir mensagens 401/403/409/422 e request ID quando presente |  |  |
| Usar todos os controles apenas por teclado e conferir foco/nomes acessíveis |  |  |
| Repetir navegação principal em português e inglês |  |  |

## Avaliação profissional

Use escala 1 (inadequado) a 5 (excelente), seguida de comentário obrigatório para
notas 1 ou 2.

| Critério | Nota | Comentário |
| --- | --- | --- |
| Clareza dos formulários e agrupamentos |  |  |
| Adequação da terminologia biblioteconômica |  |  |
| Usabilidade e previsibilidade |  |  |
| Aderência ao fluxo de catalogação |  |  |
| Correção perceptível dos vínculos Work–Instance–bf:Item |  |  |
| Clareza dos vínculos de Agent/Subject e papéis |  |  |
| Compreensão de rascunho, revisão, aprovação e publicação |  |  |
| Utilidade dos diagnósticos e recuperação de conflito |  |  |
| Acessibilidade por teclado e foco |  |  |
| Consistência pt/en |  |  |

## Problemas e recomendações

Para cada problema, registrar cenário, passos, resultado esperado/obtido, impacto
catalográfico e severidade (crítica, alta, média ou baixa). Não incluir dados
sensíveis.

1. ____________________________________________________________________
2. ____________________________________________________________________
3. ____________________________________________________________________

Recomendações:

________________________________________________________________________

## Decisão

- [ ] APROVADA
- [ ] APROVADA COM RESSALVAS
- [ ] REPROVADA
- [ ] INCONCLUSIVA

Justificativa: __________________________________________________________

Nome/assinatura ou registro institucional: ______________________________

Data: __________________________________________________________________

Estado na emissão deste roteiro:

**Homologação institucional pendente — avaliação humana não realizada.**
