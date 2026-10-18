import base64
import getpass
import hashlib
import secrets
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ISSUER = "http://keycloak.localhost:8081/realms/libris"
CALLBACK = "http://127.0.0.1:8765/callback"
CLIENT_ID = "libris-web"

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode("ascii")).digest()
).rstrip(b"=").decode("ascii")
state = secrets.token_urlsafe(32)

authorization_url = (
    f"{ISSUER}/protocol/openid-connect/auth?"
    + urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
)

print("Faça login no navegador que será aberto.")
webbrowser.open(authorization_url)
print(authorization_url)

# Não há servidor nessa porta. Após o login, o navegador mostrará
# uma falha de conexão. Copie a URL completa da barra de endereços.
redirect = getpass.getpass(
    "Cole a URL completa após o redirecionamento (entrada oculta): "
).strip()

parsed = urlsplit(redirect)
if f"{parsed.scheme}://{parsed.netloc}{parsed.path}" != CALLBACK:
    raise SystemExit("Callback inesperado.")

query = parse_qs(parsed.query)
states = query.get("state", [])
codes = query.get("code", [])

if len(states) != 1 or not secrets.compare_digest(states[0], state):
    raise SystemExit("State inválido.")
if "error" in query or len(codes) != 1:
    raise SystemExit("Login não retornou um código válido.")

with httpx.Client(timeout=15, trust_env=False) as client:
    response = client.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": CALLBACK,
            "code": codes[0],
            "code_verifier": verifier,
        },
    )
    if response.status_code != 200:
        raise SystemExit(f"Troca do código falhou: HTTP {response.status_code}")

    token = response.json()["access_token"]

    me = client.get(
        "http://localhost:8000/api/v1/identity/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    print("GET /identity/me:", me.status_code)
    if me.is_success:
        print(me.json())

    input("Pressione Enter para exibir o token e copiá-lo para o Swagger.")
    print(token)