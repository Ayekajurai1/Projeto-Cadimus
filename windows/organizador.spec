# PyInstaller: empacota o organizador (Python + bibliotecas + página) numa pasta
# "OrganizadorFiscal" com OrganizadorFiscal.exe. O Tesseract (OCR) não entra aqui: o
# instalador o coloca em {app}\tesseract (senão o PyInstaller duplica as DLLs dele).
# Usado por windows/construir.sh.
import os
from PyInstaller.utils.hooks import collect_all

RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(RAIZ, "static"), "static"),
    (os.path.join(RAIZ, "exemplo"), "exemplo"),
    (os.path.join(SPECPATH, "organizador.ico"), "."),  # ícone da bandeja do sistema
]
binaries, hiddenimports = [], ["waitress", "pystray._win32", "app", "auth", "links", "organizador_documentos"]
for pacote in ("pypdfium2", "pypdfium2_raw", "pdfplumber", "pdfminer"):
    d, b, h = collect_all(pacote)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "organizador_windows.py")],
    pathex=[RAIZ],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "numpy.tests"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="OrganizadorFiscal",
    icon=os.path.join(SPECPATH, "organizador.ico"),
    console=False,  # sem janela de comando: roda na bandeja do sistema
)
coll = COLLECT(exe, a.binaries, a.datas, name="OrganizadorFiscal")
