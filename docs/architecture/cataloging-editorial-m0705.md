# M07.05 — Governança editorial na interface

Data: 2026-10-18. A interface administrativa de Work, Instance e Item passa a usar os contratos editoriais HTTP v1 existentes, sem alterar estados, autorização, snapshots, perfis ou persistência.

## Uso

O painel editorial mostra estado, revisão atual/candidata, revisões aprovada, publicada e pública exata. Salvar ou aprovar não altera a revisão pública.

Com `metadata_revision:read`, a sessão vê revisões e compara uma anterior com a atual pelo `SemanticDiff` retornado pela API; a interface não compara Turtle ou JSON-LD como texto. Com `metadata_audit:read`, vê eventos catalográficos autorizados, inclusive atores históricos nulos.

Os comandos aparecem apenas se a sessão tiver a permissão e o estado permitir: `editorial:submit`, `editorial:return`, `editorial:approve`, `editorial:publish`, `editorial:withdraw`, `editorial:republish` e `editorial:archive`. Retornar, retirar e arquivar exigem justificativa. Os comandos são confirmados, passam pelo BFF com origem verificada e usam o ETag editorial; FastAPI continua a decidir RBAC, transições, publicação e CAS.

`401`, `403`, `409`, `412` e `422` recebem mensagens específicas. Em `412`, a atualização da governança é explícita, sem sobrescrita concorrente. A pré-validação de proposta está disponível nas telas de criação de Work, Instance e Item e reutiliza os diagnósticos de Problem Details/SHACL já mostrados pelos formulários.

## Limites conhecidos

- A API consulta snapshot individual, mas esta UI ainda não o exibe; a lista mostra número, perfil, data e ator da revisão.
- A pré-validação cobre propostas `Create`; ela não declara válido um PATCH parcial cuja forma não representa o snapshot inteiro.
- Não há restauração visual. Permissões locais apenas ocultam controles inviáveis; a API continua rejeitando tentativas indevidas.
