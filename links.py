"""
Download de documentos a partir de links compartilhados:
Google Drive, OneDrive (pessoal) e SharePoint / OneDrive for Business.

    baixar_link(url, destino) -> list[Path]   # arquivos baixados (só PDF, imagem e XML)

Ordem de tentativa para cada link:
  1. conta do usuário conectada pelo login (auth.py) -> lê o que ELE pode ver (links privados)
  2. (Microsoft) permissão de aplicativo, se MS_PERMISSAO_APLICATIVO=1 -> lê toda a organização
     (exige MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET com Files.Read.All e Sites.Read.All
      de APLICATIVO, com consentimento do administrador)
  3. link público ("Qualquer pessoa com o link"), sem login
"""

import base64
import html
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from organizador_documentos import EXTENSOES

MAX_ARQUIVOS = 300
MAX_BYTES = 500 * 1024 * 1024   # 500 MB por link
TIMEOUT = 60
CABECALHOS = {"User-Agent": "Mozilla/5.0 (OrganizadorFiscal)"}


class ErroLink(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


def eh_link(texto: str) -> bool:
    return bool(re.match(r"https?://", texto.strip(), re.I))


def provedor(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.endswith(("drive.google.com", "docs.google.com")):
        return "google"
    if host.endswith(("1drv.ms", "onedrive.live.com")):
        return "onedrive"
    if host.endswith(".sharepoint.com") or "-my.sharepoint" in host:
        return "sharepoint"
    return None


def baixar_link(url: str, destino: Path, tokens: dict[str, str] | None = None) -> list[Path]:
    """Baixa os documentos do link para `destino` e devolve os caminhos.
    `tokens`: acessos das contas conectadas pelo login ({"google": ..., "microsoft": ...})."""
    url = url.strip()
    tipo = provedor(url)
    if tipo is None:
        raise ErroLink("Link não reconhecido. Use um link do Google Drive, OneDrive ou SharePoint.")
    destino.mkdir(parents=True, exist_ok=True)
    tokens = tokens or {}

    if tipo == "google":
        tentativas = ([lambda: _google_api(url, destino, tokens["google"])] if "google" in tokens else []) \
            + [lambda: _google(url, destino)]
    else:
        tentativas = ([lambda: _graph(url, destino, tokens["microsoft"])] if "microsoft" in tokens else []) \
            + ([lambda: _graph(url, destino, _token_graph())] if credenciais_microsoft() else []) \
            + [lambda: _microsoft_publico(url, destino, tipo)]

    primeiro_erro = None
    for tentar in tentativas:  # conta do usuário primeiro; se não tiver acesso, tenta as outras formas
        try:
            arquivos = tentar()
            break
        except ErroLink as e:
            primeiro_erro = primeiro_erro or e
    else:
        raise primeiro_erro

    arquivos = [p for p in arquivos if p.suffix.lower() in EXTENSOES]
    if not arquivos:
        raise ErroLink("Nenhum documento (PDF, imagem ou XML) encontrado no link.")
    return sorted(arquivos)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

class _Cota:
    """Limita quantidade e tamanho total do que é baixado de um link."""

    def __init__(self):
        self.arquivos = 0
        self.bytes = 0

    def conferir(self, tamanho: int = 0):
        self.arquivos += 1
        self.bytes += tamanho
        if self.arquivos > MAX_ARQUIVOS:
            raise ErroLink(f"O link tem mais de {MAX_ARQUIVOS} arquivos. Divida em pastas menores.")
        if self.bytes > MAX_BYTES:
            raise ErroLink("O conteúdo do link passa de 500 MB. Divida em pastas menores.")


def _nome_seguro(nome: str) -> str:
    nome = Path(unquote(nome).replace("\\", "/")).name.strip()
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", nome) or "arquivo"


def _salvar(resp: requests.Response, pasta: Path, nome: str, cota: _Cota) -> Path:
    caminho = pasta / _nome_seguro(nome)
    n = 2
    while caminho.exists():
        caminho = caminho.with_name(f"{Path(nome).stem}_{n}{Path(nome).suffix}")
        n += 1
    total = 0
    with open(caminho, "wb") as f:
        for bloco in resp.iter_content(1 << 16):
            total += len(bloco)
            if total > MAX_BYTES:
                raise ErroLink("Arquivo grande demais no link (mais de 500 MB).")
            f.write(bloco)
    cota.conferir(total)
    return caminho


def _nome_da_resposta(resp: requests.Response, padrao: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    return unquote(m.group(1)) if m else padrao


def _eh_pagina(resp: requests.Response) -> bool:
    return "text/html" in resp.headers.get("Content-Type", "")


# --------------------------------------------------------------------------
# Google Drive
# --------------------------------------------------------------------------

def _google(url: str, destino: Path) -> list[Path]:
    """Link público do Google Drive ("Qualquer pessoa com o link"), sem login."""
    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)
    cota = _Cota()

    # Documentos/planilhas/apresentações do Google: exporta como PDF
    m = re.search(r"docs\.google\.com/(document|spreadsheets|presentation)/d/([\w-]+)", url)
    if m:
        return [_google_exportar_publico(sessao, m.group(1), m.group(2), destino, cota)]

    id_ = _id_google(url)
    if not id_:
        raise ErroLink("Não reconheci o arquivo/pasta no link do Google Drive.")
    if "/folders/" in url or "folderview" in url:
        return _google_pasta_publica(sessao, id_, destino, cota)
    return [_google_arquivo_publico(sessao, id_, destino / "000", cota)]


_SEM_ACESSO_GOOGLE = ("Sem permissão para acessar o link do Google Drive. Entre com a conta Google que tem "
                      "acesso ou compartilhe como “Qualquer pessoa com o link” (leitor).")


def _google_pasta_publica(sessao, id_: str, destino: Path, cota: _Cota, nivel: int = 0) -> list[Path]:
    """Lista a pasta pela visualização incorporada do Drive e baixa só os documentos."""
    resp = sessao.get("https://drive.google.com/embeddedfolderview", params={"id": id_}, timeout=TIMEOUT)
    if resp.status_code != 200 or "flip-entry" not in resp.text and "flip-view" not in resp.text:
        raise ErroLink(_SEM_ACESSO_GOOGLE)
    arquivos = []
    for trecho in resp.text.split('<div class="flip-entry" id="entry-')[1:]:
        m_id = re.match(r"([\w-]+)", trecho)
        m_link = re.search(r'href="([^"]+)"', trecho)
        m_nome = re.search(r'<div class="flip-entry-title">([^<]*)</div>', trecho)
        if not (m_id and m_link and m_nome):
            continue
        fid, link, nome = m_id.group(1), html.unescape(m_link.group(1)), html.unescape(m_nome.group(1)).strip()
        if "/folders/" in link:
            if nivel < 3:
                arquivos += _google_pasta_publica(sessao, fid, destino, cota, nivel + 1)
            continue
        m_doc = re.search(r"docs\.google\.com/(document|spreadsheets|presentation)/d/", link)
        if m_doc:
            arquivos.append(_google_exportar_publico(sessao, m_doc.group(1), fid, destino, cota, nome))
        elif Path(nome).suffix.lower() in EXTENSOES:
            arquivos.append(_google_arquivo_publico(sessao, fid, destino / f"{cota.arquivos:03d}", cota, nome))
    return arquivos


def _google_arquivo_publico(sessao, fid: str, pasta: Path, cota: _Cota, nome: str | None = None) -> Path:
    resp = sessao.get("https://drive.usercontent.google.com/download",
                      params={"id": fid, "export": "download", "confirm": "t"}, stream=True, timeout=TIMEOUT)
    if resp.status_code == 200 and _eh_pagina(resp):
        # Página de aviso (arquivo grande sem verificação de vírus): reenvia o formulário de confirmação
        pagina = resp.text
        form = re.search(r'<form[^>]+action="([^"]+)"', pagina)
        campos = dict(re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', pagina))
        if form and campos:
            resp = sessao.get(html.unescape(form.group(1)), params=campos, stream=True, timeout=TIMEOUT)
    if resp.status_code != 200 or _eh_pagina(resp):
        raise ErroLink(_SEM_ACESSO_GOOGLE)
    pasta.mkdir(parents=True, exist_ok=True)
    return _salvar(resp, pasta, _nome_da_resposta(resp, nome or f"{fid}.pdf"), cota)


def _google_exportar_publico(sessao, tipo: str, id_: str, destino: Path, cota: _Cota, nome: str = "") -> Path:
    resp = sessao.get(f"https://docs.google.com/{tipo}/d/{id_}/export", params={"format": "pdf"},
                      stream=True, timeout=TIMEOUT)
    if resp.status_code != 200 or _eh_pagina(resp):
        raise ErroLink("Não foi possível exportar o documento do Google. Confira o compartilhamento.")
    pasta = destino / f"{cota.arquivos:03d}"
    pasta.mkdir(parents=True, exist_ok=True)
    base = Path(nome).stem if Path(nome).suffix else nome  # "Planilha.xlsx" -> "Planilha.pdf"
    return _salvar(resp, pasta, (base or f"documento_{id_[:8]}") + ".pdf", cota)


DRIVE_API = "https://www.googleapis.com/drive/v3/files"
PASTA_GOOGLE = "application/vnd.google-apps.folder"


def _id_google(url: str) -> str | None:
    m = (re.search(r"/(?:file|document|spreadsheets|presentation)/d/([\w-]+)", url)
         or re.search(r"/folders/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url))
    return m.group(1) if m else None


def _google_api(url: str, destino: Path, token: str) -> list[Path]:
    """Google Drive API v3 com a conta do usuário: lê arquivos e pastas privados dele."""
    id_ = _id_google(url)
    if not id_:
        raise ErroLink("Não reconheci o arquivo/pasta no link do Google Drive.")
    sessao = requests.Session()
    sessao.headers["Authorization"] = f"Bearer {token}"
    comuns = {"supportsAllDrives": "true"}
    resp = sessao.get(f"{DRIVE_API}/{id_}", params={"fields": "id,name,mimeType", **comuns}, timeout=TIMEOUT)
    if resp.status_code == 401:
        raise ErroLink("O acesso ao Google Drive expirou. Clique em “Sair” e entre novamente.")
    if resp.status_code in (403, 404):
        raise ErroLink("A conta Google conectada não tem acesso a este link. Peça para compartilharem "
                       "com o seu e-mail ou como “Qualquer pessoa com o link”.")
    resp.raise_for_status()
    cota = _Cota()

    def baixar(item: dict, nivel: int = 0) -> list[Path]:
        if item["mimeType"] == PASTA_GOOGLE:
            if nivel > 3:
                return []
            arquivos, pagina = [], None
            while True:
                params = {"q": f"'{item['id']}' in parents and trashed=false", "pageSize": 1000,
                          "fields": "nextPageToken,files(id,name,mimeType)",
                          "includeItemsFromAllDrives": "true", **comuns}
                if pagina:
                    params["pageToken"] = pagina
                lista = sessao.get(DRIVE_API, params=params, timeout=TIMEOUT).json()
                for filho in lista.get("files", []):
                    arquivos += baixar(filho, nivel + 1)
                pagina = lista.get("nextPageToken")
                if not pagina:
                    return arquivos
        if item["mimeType"].startswith("application/vnd.google-apps."):  # Docs/Planilhas: exporta PDF
            conteudo = sessao.get(f"{DRIVE_API}/{item['id']}/export", params={"mimeType": "application/pdf"},
                                  stream=True, timeout=TIMEOUT)
            nome = item["name"] + ".pdf"
        else:
            if Path(item["name"]).suffix.lower() not in EXTENSOES:
                return []
            conteudo = sessao.get(f"{DRIVE_API}/{item['id']}", params={"alt": "media", **comuns},
                                  stream=True, timeout=TIMEOUT)
            nome = item["name"]
        if conteudo.status_code != 200:
            return []
        pasta = destino / f"{cota.arquivos:03d}"
        pasta.mkdir(parents=True, exist_ok=True)
        return [_salvar(conteudo, pasta, nome, cota)]

    return baixar(resp.json())


# --------------------------------------------------------------------------
# OneDrive / SharePoint — links públicos ("Qualquer pessoa com o link")
# --------------------------------------------------------------------------

def _token_compartilhamento(url: str) -> str:
    """Formato aceito pelas APIs /shares/{token} da Microsoft."""
    return "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _microsoft_publico(url: str, destino: Path, tipo: str) -> list[Path]:
    cota = _Cota()
    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)

    if tipo == "onedrive":
        arquivos = _onedrive_api_publica(sessao, url, destino, cota)
        if arquivos is not None:
            return arquivos

    # Abre o link como um navegador anônimo: o SharePoint devolve um cookie de convidado
    # e redireciona para a página do arquivo/pasta.
    try:
        resp = sessao.get(url, allow_redirects=True, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ErroLink(f"Não foi possível abrir o link: {e}") from e
    final = urlparse(resp.url)
    if "login.microsoftonline.com" in final.netloc or "login.live.com" in final.netloc or resp.status_code in (401, 403):
        raise ErroLink(_MSG_LOGIN)

    # Pasta: a URL final traz ?id=/sites/.../Pasta (ou /personal/...)
    caminho_pasta = parse_qs(final.query).get("id", [None])[0]
    if caminho_pasta and ("/:f:/" in url or not Path(caminho_pasta).suffix):
        return _sharepoint_pasta(sessao, f"{final.scheme}://{final.netloc}", caminho_pasta, destino, cota)

    # Arquivo: "download=1" entrega o conteúdo em vez da página de visualização
    link = url + ("&" if "?" in url else "?") + "download=1"
    resp = sessao.get(link, allow_redirects=True, stream=True, timeout=TIMEOUT)
    if resp.status_code == 200 and not _eh_pagina(resp):
        nome = _nome_da_resposta(resp, Path(unquote(urlparse(resp.url).path)).name or "documento.pdf")
        return [_salvar(resp, destino, nome, cota)]
    raise ErroLink(_MSG_LOGIN)


def _onedrive_api_publica(sessao, url: str, destino: Path, cota: _Cota) -> list[Path] | None:
    """API pública do OneDrive pessoal. Devolve None se não funcionar para este link."""
    base = f"https://api.onedrive.com/v1.0/shares/{_token_compartilhamento(url)}"
    try:
        resp = sessao.get(f"{base}/root", params={"expand": "children"}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    item = resp.json()
    itens = item.get("children", []) if "folder" in item else [item]
    arquivos = []
    for filho in itens:
        link = filho.get("@content.downloadUrl")
        if link and Path(filho.get("name", "")).suffix.lower() in EXTENSOES:
            arquivos.append(_salvar(sessao.get(link, stream=True, timeout=TIMEOUT), destino, filho["name"], cota))
    return arquivos


def _site_do_caminho(caminho: str) -> str:
    """/sites/Financeiro/Shared Documents/Notas -> /sites/Financeiro"""
    partes = caminho.strip("/").split("/")
    if len(partes) >= 2 and partes[0] in ("sites", "teams", "personal"):
        return "/" + "/".join(partes[:2])
    return ""


def _sharepoint_pasta(sessao, raiz: str, caminho: str, destino: Path, cota: _Cota, nivel: int = 0) -> list[Path]:
    """Lista e baixa os arquivos da pasta (e subpastas, até 3 níveis) pela API REST do SharePoint."""
    site = raiz + _site_do_caminho(caminho)
    api = f"{site}/_api/web/GetFolderByServerRelativePath(decodedurl='{quote(caminho.replace(chr(39), chr(39) * 2))}')"
    cab = {"Accept": "application/json;odata=nometadata"}
    resp = sessao.get(f"{api}/Files", headers=cab, timeout=TIMEOUT)
    if resp.status_code in (401, 403) or resp.status_code != 200:
        raise ErroLink(_MSG_LOGIN)

    arquivos = []
    for f in resp.json().get("value", []):
        if Path(f["Name"]).suffix.lower() not in EXTENSOES:
            continue
        rel = f["ServerRelativeUrl"].replace("'", "''")
        conteudo = sessao.get(f"{site}/_api/web/GetFileByServerRelativePath(decodedurl='{quote(rel)}')/$value",
                              stream=True, timeout=TIMEOUT)
        if conteudo.status_code == 200:
            arquivos.append(_salvar(conteudo, destino, f["Name"], cota))

    if nivel < 3:
        resp = sessao.get(f"{api}/Folders", headers=cab, timeout=TIMEOUT)
        if resp.status_code == 200:
            for sub in resp.json().get("value", []):
                if sub["Name"] != "Forms":
                    arquivos += _sharepoint_pasta(sessao, raiz, sub["ServerRelativeUrl"], destino, cota, nivel + 1)
    return arquivos


_MSG_LOGIN = ("Este link do OneDrive/SharePoint exige login. Entre com sua conta Microsoft "
              "(ou clique em “Conectar OneDrive/SharePoint” no topo) ou peça um link “Qualquer pessoa com o link”.")


# --------------------------------------------------------------------------
# OneDrive / SharePoint — Microsoft Graph (links restritos à organização)
# --------------------------------------------------------------------------

def credenciais_microsoft() -> bool:
    """Permissão de APLICATIVO (lê toda a organização, sem login do usuário)."""
    return os.environ.get("MS_PERMISSAO_APLICATIVO") == "1" and \
        all(os.environ.get(v) for v in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET"))


def _token_graph() -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token",
        data={"client_id": os.environ["MS_CLIENT_ID"], "client_secret": os.environ["MS_CLIENT_SECRET"],
              "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
        timeout=TIMEOUT)
    if resp.status_code != 200:
        raise ErroLink("Credenciais do Microsoft 365 inválidas (MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET).")
    return resp.json()["access_token"]


def _graph(url: str, destino: Path, token: str) -> list[Path]:
    """Microsoft Graph: com o token do usuário (login) ou do aplicativo."""
    sessao = requests.Session()
    sessao.headers["Authorization"] = f"Bearer {token}"
    graph = "https://graph.microsoft.com/v1.0"
    resp = sessao.get(f"{graph}/shares/{_token_compartilhamento(url)}/driveItem", timeout=TIMEOUT)
    if resp.status_code == 401:
        raise ErroLink("O acesso ao Microsoft 365 expirou. Clique em “Sair” e entre novamente.")
    if resp.status_code != 200:
        raise ErroLink("A conta Microsoft conectada não tem acesso a este link. Peça para compartilharem "
                       "com o seu e-mail ou gere um link “Qualquer pessoa com o link”.")
    item = resp.json()
    cota = _Cota()

    def baixar(item: dict, nivel: int = 0) -> list[Path]:
        if "folder" not in item:
            if Path(item["name"]).suffix.lower() not in EXTENSOES:
                return []
            conteudo = requests.get(item["@microsoft.graph.downloadUrl"], stream=True, timeout=TIMEOUT)
            return [_salvar(conteudo, destino, item["name"], cota)]
        if nivel > 3:
            return []
        drive = item["parentReference"]["driveId"]
        proximo = f"{graph}/drives/{drive}/items/{item['id']}/children"
        arquivos = []
        while proximo:
            pagina = sessao.get(proximo, timeout=TIMEOUT).json()
            for filho in pagina.get("value", []):
                arquivos += baixar(filho, nivel + 1)
            proximo = pagina.get("@odata.nextLink")
        return arquivos

    return baixar(item)
