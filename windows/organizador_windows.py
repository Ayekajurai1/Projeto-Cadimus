"""
Inicializador do Organizador de Documentos Fiscais para Windows.

Roda em segundo plano (sem janela de comando) e abre a página no navegador padrão.
Fica um ícone na bandeja do sistema (perto do relógio) com "Abrir organizador" e "Sair".
Se o organizador já estiver aberto, abrir o programa de novo só abre o navegador.

Dados (documentos enviados, configuração .env do login) e o log ficam em
%LOCALAPPDATA%\\OrganizadorFiscal; o programa em si fica na pasta de instalação.
"""

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

PORTA = 8765
URL = f"http://127.0.0.1:{PORTA}/"
NOME = "Organizador de Documentos Fiscais"

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DADOS = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OrganizadorFiscal"


def ja_aberto() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORTA)) == 0


def abrir_navegador(*_) -> None:
    webbrowser.open(URL)


def registrar_log() -> None:
    """Sem janela de comando não há onde imprimir: mensagens e erros vão para o arquivo de log."""
    log = open(DADOS / "organizador.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def configurar_ocr() -> None:
    """Usa o Tesseract (com português) que vem junto no instalador."""
    pasta_programa = Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE / "windows" / "build"
    tesseract = pasta_programa / "tesseract" / "tesseract.exe"
    if tesseract.exists():
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = str(tesseract)
        os.environ["TESSDATA_PREFIX"] = str(tesseract.parent / "tessdata")


def icone_bandeja(servidor) -> None:
    """Ícone perto do relógio; bloqueia até o usuário escolher "Sair"."""
    import pystray
    from PIL import Image

    def sair(icone, _item):
        icone.stop()
        servidor.close()
        os._exit(0)

    def ao_iniciar(icone):
        icone.visible = True
        try:
            icone.notify("Aberto no navegador. Para encerrar, clique com o botão direito "
                         "neste ícone e escolha “Sair”.", NOME)
        except Exception:
            pass

    icone_arquivo = BASE / "organizador.ico"  # no executável; rodando como script: windows/organizador.ico
    imagem = Image.open(icone_arquivo if icone_arquivo.exists() else BASE / "windows" / "organizador.ico")
    menu = pystray.Menu(pystray.MenuItem("Abrir organizador", abrir_navegador, default=True),
                        pystray.MenuItem("Sair", sair))
    pystray.Icon("OrganizadorFiscal", imagem, NOME, menu).run(setup=ao_iniciar)


def main() -> None:
    if ja_aberto():
        abrir_navegador()
        return

    DADOS.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or getattr(sys, "frozen", False):
        registrar_log()
    os.environ.setdefault("ORGANIZADOR_DADOS", str(DADOS))
    configurar_ocr()

    sys.path.insert(0, str(BASE))  # rodando como script: app.py fica uma pasta acima
    import app  # depois de definir ORGANIZADOR_DADOS
    from waitress import create_server

    servidor = create_server(app.app, host="127.0.0.1", port=PORTA, threads=8, channel_timeout=600)
    threading.Thread(target=servidor.run, daemon=True).start()
    print(f"{NOME} aberto em {URL} (dados em {DADOS})")
    threading.Timer(1.0, abrir_navegador).start()
    icone_bandeja(servidor)


if __name__ == "__main__":
    main()
