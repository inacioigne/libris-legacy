# Recuperação do início de login no Compose — 2026-10-17

Diagnóstico do ambiente local após relato de exclusão de volumes do Keycloak.
Esta execução é independente do diagnóstico histórico de 2026-10-14.

## Evidências e causa

- `docker compose ps`: os cinco serviços estavam em execução; API, web,
  PostgreSQL e Elasticsearch saudáveis.
- Descoberta do realm `libris`, via IPv4 com hostname canônico: HTTP 200.
- `GET http://localhost:3000/oidc/login`: HTTP 405. A rota implementa somente
  POST e deve ser acionada pelo formulário “Entrar” da aplicação.
- POST pelo host com Origin e formulário válidos: HTTP 503 antes da recuperação.
- Consulta pela conexão efetiva do BFF: `libris_web.sessions`,
  `libris_web.auth_transactions` e `libris_web.auth_quotas` ausentes.
- `docker compose exec -T api alembic current`: nenhuma revisão aplicada.
  Consulta de tabelas em `public` e `libris_web`: nenhuma tabela presente.

O banco PostgreSQL também estava sem sua estrutura, independentemente do realm
Keycloak disponível. O admission check do login não conseguia consultar quotas.
O healthcheck da página pública não verifica a instalação das tabelas de autenticação.

## Recuperação executada

Aplicado o executor existente, conforme ADR 0034 e README:

```bash
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic current
```

Ambos concluíram com código zero; a revisão final foi `0005_web_auth (head)`.
Não houve downgrade, remoção de volumes ou alteração de código/configuração.

## Verificação posterior e limites

- POST `/oidc/login`, pelo host, com Origin `http://localhost:3000` e formulário
  `returnTo=%2Fpt%2Fadmin`: HTTP 303 para o endpoint de autorização do realm,
  com cookie de transação e sem indicador `auth=error`.
- Acesso ao destino preservando Host canônico e conectando por IPv4: HTTP 200,
  com formulário `kc-form-login` presente.
- O resolver do host retornou `::1` para `keycloak.localhost`; o Compose publica
  8081 somente em IPv4. Se o navegador não alcançar o provedor, conferir o
  mapeamento `127.0.0.1 keycloak.localhost` conforme o roteiro Keycloak local.

As verificações não imprimiram cookies, state, códigos, tokens ou credenciais.
Não foi executado login com usuário, callback, logout ou homologação de navegador.
Se contas anteriores desapareceram com o volume Keycloak, precisam ser restauradas
de backup ou recriadas com seus papéis no realm `libris`; a importação versionada
não inclui usuários. Migrações reinstalam estrutura, não recuperam dados apagados.
Suítes de código/build não foram executadas: a recuperação usou migrações existentes,
sem alteração de implementação.
