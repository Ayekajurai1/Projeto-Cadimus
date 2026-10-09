"""
Login (SSO) com Google e Microsoft (Outlook / Microsoft 365).

Além de identificar o usuário, o login dá ao organizador acesso de LEITURA aos arquivos
da própria conta, para baixar links do Google Drive e do OneDrive/SharePoint que não são
públicos (ex.: compartilhados só dentro da empresa).

Configuração (arquivo .env na pasta do projeto ou variáveis de ambiente; veja .env.exemplo):

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET          -> botão "Entrar com Google"
    MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID    -> botão "Entrar com Microsoft"
                                                       (MS_TENANT_ID: "common" aceita contas
                                                        pessoais Outlook e corporativas)
    ORGANIZADOR_DOMINIOS   (opcional) ex.: "fpf.br" -> só e-mails desses domínios entram
    ORGANIZADOR_URL_BASE   (opcional) endereço público, ex.: https://meu-servidor:5000
                           (no GitHub Codespaces é detectado sozinho)

A tela de login aparece sempre, com a opção "Entrar sem login" (desative com
ORGANIZADOR_EXIGIR_LOGIN=1 para obrigar o login com Google/Microsoft).
"""

import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, jsonify, redirect, request, session, url_for

# Pasta de dados graváveis (.env e chave das sessões); no Windows instalado: %LOCALAPPDATA%\OrganizadorFiscal
RAIZ = Path(os.environ.get("ORGANIZADOR_DADOS") or Path(__file__).parent)
bp = Blueprint("auth", __name__)

GOOGLE_ESCOPOS = "openid email profile https://www.googleapis.com/auth/drive.readonly"
GOOGLE_AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USUARIO = "https://openidconnect.googleapis.com/v1/userinfo"

MS_ESCOPOS = "User.Read Files.Read.All offline_access"
MS_USUARIO = "https://graph.microsoft.com/v1.0/me"

# Sessões no servidor (os tokens não vão para o cookie): {sid: {usuario, tokens}}
SESSOES: dict[str, dict] = {}


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

def carregar_env(caminho: Path = RAIZ / ".env") -> None:
    """Lê CHAVE=valor do arquivo .env (sem sobrescrever variáveis já definidas)."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def provedores() -> dict[str, bool]:
    return {
        "google": bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")),
        "microsoft": bool(os.environ.get("MS_CLIENT_ID") and os.environ.get("MS_CLIENT_SECRET")),
    }


def login_ativo() -> bool:
    """A tela de login é sempre mostrada antes do organizador."""
    return True


def sem_login_permitido() -> bool:
    """Botão "Entrar sem login" (padrão: disponível). ORGANIZADOR_EXIGIR_LOGIN=1 o remove."""
    return os.environ.get("ORGANIZADOR_EXIGIR_LOGIN") != "1"


def chave_secreta() -> str:
    """Chave das sessões: ORGANIZADOR_SECRET ou uma gerada e guardada em _web/.chave_sessao."""
    if os.environ.get("ORGANIZADOR_SECRET"):
        return os.environ["ORGANIZADOR_SECRET"]
    arquivo = RAIZ / "_web" / ".chave_sessao"
    arquivo.parent.mkdir(exist_ok=True)
    if not arquivo.exists():
        arquivo.write_text(secrets.token_hex(32))
        arquivo.chmod(0o600)
    return arquivo.read_text().strip()


def url_base() -> str:
    if os.environ.get("ORGANIZADOR_URL_BASE"):
        return os.environ["ORGANIZADOR_URL_BASE"].rstrip("/")
    if os.environ.get("CODESPACE_NAME"):  # GitHub Codespaces: endereço público da porta
        porta = request.host.rsplit(":", 1)[-1] if ":" in request.host else "5000"
        dominio = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
        return f"https://{os.environ['CODESPACE_NAME']}-{porta}.{dominio}"
    return request.host_url.rstrip("/")


def _ms_tenant() -> str:
    return os.environ.get("MS_TENANT_ID") or "common"


def _ms_url(caminho: str) -> str:
    return f"https://login.microsoftonline.com/{_ms_tenant()}/oauth2/v2.0/{caminho}"


def _cliente(provedor: str) -> tuple[str, str]:
    if provedor == "google":
        return os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"]
    return os.environ["MS_CLIENT_ID"], os.environ["MS_CLIENT_SECRET"]


# --------------------------------------------------------------------------
# Sessão do usuário
# --------------------------------------------------------------------------

def sessao_atual() -> dict | None:
    return SESSOES.get(session.get("sid", ""))


def usuario_logado() -> bool:
    s = sessao_atual()
    return bool(s and s.get("usuario"))


def dominio_permitido(email: str) -> bool:
    dominios = [d.strip().lower().lstrip("@") for d in os.environ.get("ORGANIZADOR_DOMINIOS", "").split(",") if d.strip()]
    return not dominios or email.lower().rsplit("@", 1)[-1] in dominios


def tokens_para_download() -> dict[str, str]:
    """Tokens de acesso válidos da sessão atual ({"google": ..., "microsoft": ...}),
    renovando os que estão para vencer. Usado para baixar links privados."""
    s = sessao_atual()
    if not s:
        return {}
    validos = {}
    for provedor, token in s["tokens"].items():
        if token.get("expira_em", 0) - time.time() < 300 and token.get("refresh_token"):
            _renovar(provedor, token)
        if token.get("expira_em", 0) > time.time():
            validos[provedor] = token["access_token"]
    return validos


def _renovar(provedor: str, token: dict) -> None:
    cliente_id, segredo = _cliente(provedor)
    dados = {"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
             "client_id": cliente_id, "client_secret": segredo}
    if provedor == "microsoft":
        dados["scope"] = MS_ESCOPOS
    resp = requests.post(GOOGLE_TOKEN if provedor == "google" else _ms_url("token"), data=dados, timeout=30)
    if resp.ok:
        _guardar_token(token, resp.json())


def _guardar_token(destino: dict, resposta: dict) -> None:
    destino["access_token"] = resposta["access_token"]
    destino["expira_em"] = time.time() + int(resposta.get("expires_in", 3600))
    if resposta.get("refresh_token"):
        destino["refresh_token"] = resposta["refresh_token"]


# --------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------

@bp.get("/login")
def pagina_login():
    if usuario_logado():
        return redirect("/")
    return current_app.send_static_file("login.html")


@bp.get("/api/sessao")
def info_sessao():
    s = sessao_atual() or {}
    return jsonify(
        login_ativo=login_ativo(),
        sem_login=sem_login_permitido(),
        provedores=provedores(),
        usuario=s.get("usuario"),
        conectado={p: p in s.get("tokens", {}) for p in ("google", "microsoft")},
    )


@bp.get("/auth/<provedor>")
def entrar(provedor: str):
    """Inicia o login (ou conecta uma segunda conta, ex.: logado no Google, conecta o OneDrive)."""
    if provedor == "sem-login":
        if not sem_login_permitido():
            return redirect("/login")
        return _iniciar_sessao({"nome": "Sem login", "email": "", "provedor": "nenhum"}, {})
    if provedor not in ("google", "microsoft"):
        return redirect("/login")
    if not provedores().get(provedor):
        nome = "Google" if provedor == "google" else "Microsoft"
        return redirect("/login?" + urlencode({"erro": f"O login com {nome} ainda não foi configurado. "
                                                         "Preencha as credenciais no arquivo .env (veja o README)."}))
    estado = secrets.token_urlsafe(24)
    session["oauth_estado"] = estado
    cliente_id, _ = _cliente(provedor)
    parametros = {"client_id": cliente_id, "response_type": "code", "state": estado,
                  "redirect_uri": f"{url_base()}/auth/{provedor}/retorno"}
    if provedor == "google":
        parametros.update(scope=GOOGLE_ESCOPOS, access_type="offline", prompt="select_account consent",
                          include_granted_scopes="true")
        return redirect(f"{GOOGLE_AUTORIZAR}?{urlencode(parametros)}")
    parametros.update(scope=MS_ESCOPOS, prompt="select_account", response_mode="query")
    return redirect(f"{_ms_url('authorize')}?{urlencode(parametros)}")


@bp.get("/auth/<provedor>/retorno")
def retorno(provedor: str):
    if provedor not in ("google", "microsoft") or not provedores().get(provedor):
        return redirect("/login")
    if request.args.get("error"):
        motivo = request.args.get("error_description") or request.args.get("error")
        return redirect("/login?" + urlencode({"erro": f"Login cancelado ou recusado: {motivo[:200]}"}))
    if not request.args.get("state") or request.args.get("state") != session.pop("oauth_estado", None):
        return redirect("/login?" + urlencode({"erro": "Sessão de login expirada. Tente de novo."}))

    cliente_id, segredo = _cliente(provedor)
    dados = {"grant_type": "authorization_code", "code": request.args.get("code", ""),
             "redirect_uri": f"{url_base()}/auth/{provedor}/retorno",
             "client_id": cliente_id, "client_secret": segredo}
    if provedor == "microsoft":
        dados["scope"] = MS_ESCOPOS
    resp = requests.post(GOOGLE_TOKEN if provedor == "google" else _ms_url("token"), data=dados, timeout=30)
    if not resp.ok:
        detalhe = resp.json().get("error_description", resp.text)[:200] if resp.headers.get(
            "Content-Type", "").startswith("application/json") else resp.text[:200]
        return redirect("/login?" + urlencode({"erro": f"Falha no login: {detalhe}"}))
    token: dict = {}
    _guardar_token(token, resp.json())

    # Quem é o usuário
    cab = {"Authorization": f"Bearer {token['access_token']}"}
    perfil = requests.get(GOOGLE_USUARIO if provedor == "google" else MS_USUARIO, headers=cab, timeout=30).json()
    if provedor == "google":
        usuario = {"nome": perfil.get("name") or perfil.get("email"), "email": perfil.get("email", ""), "provedor": "google"}
    else:
        email = perfil.get("mail") or perfil.get("userPrincipalName") or ""
        usuario = {"nome": perfil.get("displayName") or email, "email": email, "provedor": "microsoft"}

    s = sessao_atual()
    if s and s.get("usuario"):  # já logado: só conecta mais uma conta para os downloads
        s["tokens"][provedor] = token
        return redirect("/")

    if not dominio_permitido(usuario["email"]):
        return redirect("/login?" + urlencode({"erro": f"A conta {usuario['email']} não tem acesso a este organizador."}))
    return _iniciar_sessao(usuario, {provedor: token})


def _iniciar_sessao(usuario: dict, tokens: dict):
    sid = uuid.uuid4().hex
    SESSOES[sid] = {"usuario": usuario, "tokens": tokens}
    session.clear()
    session["sid"] = sid
    session.permanent = True
    return redirect("/")


@bp.get("/sair")
def sair():
    SESSOES.pop(session.get("sid", ""), None)
    session.clear()
    return redirect("/login")


# --------------------------------------------------------------------------
# Proteção das rotas
# --------------------------------------------------------------------------

LIVRES = ("/login", "/login.html", "/login.js", "/auth/", "/api/sessao", "/sair",
          "/estilo.css", "/fontes.css", "/fonts/", "/img/")


def exigir_login():
    """before_request: com login ativo, só usuários autenticados acessam o organizador."""
    if not login_ativo() or usuario_logado() or request.path.startswith(LIVRES):
        return None
    if request.path.startswith("/api/"):
        return jsonify(erro="Sua sessão expirou. Entre novamente.", login=True), 401
    return redirect(url_for("auth.pagina_login"))
