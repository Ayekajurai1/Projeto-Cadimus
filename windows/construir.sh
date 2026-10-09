#!/usr/bin/env bash
# Gera o instalador do Windows (windows/dist/OrganizadorFiscal-Setup.exe) a partir do Linux,
# usando Wine. Testado no GitHub Codespaces (Ubuntu).
#
#   bash windows/construir.sh
#
# Etapas: instala Wine -> Python 3.12 para Windows -> bibliotecas + PyInstaller ->
# Tesseract (OCR) com português -> PyInstaller (OrganizadorFiscal.exe) -> Inno Setup (Setup.exe).
# Os downloads e o "Windows" do Wine ficam em ~/.cache/organizador-windows (reaproveitados).
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(dirname "$AQUI")"
CACHE="${CACHE:-$HOME/.cache/organizador-windows}"
export WINEPREFIX="$CACHE/wine" WINEARCH=win64 WINEDEBUG=-all
PY_URL="https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
TESS_URL="https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
TESSDATA_URL="https://github.com/tesseract-ocr/tessdata_fast/raw/main"
INNO_URL="https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"

passo() { echo; echo "==> $*"; }
baixar() { [ -s "$CACHE/$2" ] || curl -sSL --fail -o "$CACHE/$2" "$1"; }
mkdir -p "$CACHE"

passo "Wine"
if ! command -v wine >/dev/null; then
  sudo dpkg --add-architecture i386
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine64 wine32:i386 xvfb winbind >/dev/null
fi
[ -d "$WINEPREFIX" ] || xvfb-run -a wineboot --init >/dev/null 2>&1

passo "Python 3.12 para Windows"
PYW='C:\Python312\python.exe'
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  baixar "$PY_URL" python-amd64.exe
  xvfb-run -a wine "$CACHE/python-amd64.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 \
    Include_launcher=0 TargetDir='C:\Python312' >/dev/null 2>&1
fi
# (no Wine, a saída do Python precisa passar por um pipe)
wine "$PYW" -m pip install -q --disable-pip-version-check -r "$RAIZ/requirements.txt" pyinstaller 2>&1 | cat

passo "Tesseract (OCR) com português"
TESS_WIN="$WINEPREFIX/drive_c/Program Files/Tesseract-OCR"
if [ ! -f "$TESS_WIN/tesseract.exe" ]; then
  baixar "$TESS_URL" tesseract-setup.exe
  xvfb-run -a wine "$CACHE/tesseract-setup.exe" /S >/dev/null 2>&1
fi
for idioma in por eng; do baixar "$TESSDATA_URL/$idioma.traineddata" "$idioma.traineddata"; done
rm -rf "$AQUI/build/tesseract"
mkdir -p "$AQUI/build/tesseract/tessdata"
cp "$TESS_WIN"/tesseract.exe "$TESS_WIN"/*.dll "$AQUI/build/tesseract/"
cp "$TESS_WIN"/tessdata/osd.traineddata "$CACHE"/por.traineddata "$CACHE"/eng.traineddata "$AQUI/build/tesseract/tessdata/"

passo "PyInstaller (OrganizadorFiscal.exe)"
cd "$AQUI"
rm -rf build/dist build/work
wine "$PYW" -m PyInstaller --noconfirm --clean --distpath build/dist --workpath build/work organizador.spec 2>&1 \
  | cat > build/pyinstaller.log
test -f build/dist/OrganizadorFiscal/OrganizadorFiscal.exe || { tail -20 build/pyinstaller.log; exit 1; }

passo "Inno Setup (OrganizadorFiscal-Setup.exe)"
ISCC="$WINEPREFIX/drive_c/InnoSetup/ISCC.exe"
if [ ! -f "$ISCC" ]; then
  baixar "$INNO_URL" innosetup.exe
  xvfb-run -a wine "$CACHE/innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /DIR='C:\InnoSetup' >/dev/null 2>&1
fi
wine "$ISCC" /Qp instalador.iss 2>&1 | cat
ls -lh "$AQUI/dist/OrganizadorFiscal-Setup.exe"
