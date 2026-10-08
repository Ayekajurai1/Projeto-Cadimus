#!/usr/bin/env python3
"""
Organizador de Documentos Fiscais
=================================
Varre uma pasta, identifica NOTAS FISCAIS e COMPROVANTES DE PAGAMENTO,
renomeia com número e valor e separa em subpastas.

Formatos aceitos: PDF (com texto ou escaneado), imagens (JPG/PNG) e XML de NF-e.

Uso:
    python organizador_documentos.py PASTA_ENTRADA                # simulação (não mexe em nada)
    python organizador_documentos.py PASTA_ENTRADA --aplicar      # copia e renomeia de verdade
    python organizador_documentos.py PASTA_ENTRADA --aplicar --mover

Saída (dentro de --saida, padrão: PASTA_ENTRADA/_organizado):
    Notas_Fiscais/   NF_<numero>_R$<valor>.pdf  (NF + comprovantes vinculados, num só PDF)
    Comprovantes/    COMP_<data>_R$<valor>.pdf  (comprovantes sem NF correspondente)
    Revisar/         o que não foi possível identificar com segurança
    Duplicados/      arquivos idênticos (mesmo conteúdo)
    relatorio.csv    log de tudo que foi feito
"""

import argparse
import csv
import hashlib
import logging
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Configuração: palavras-chave e pesos (ajuste conforme seus documentos)
# --------------------------------------------------------------------------

# (expressão regular, peso)
PISTAS_NF = [
    (r"\bdanfe\b", 6),
    (r"documento auxiliar da nota fiscal", 6),
    (r"\bnf-?e\b", 3),
    (r"\bnfs-?e\b", 4),
    (r"nota fiscal", 4),
    (r"chave de acesso", 4),
    (r"\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b", 5),  # chave 44 dígitos
    (r"natureza da opera", 3),
    (r"discrimina[cç][aã]o dos servi", 3),
    (r"c[oó]digo de verifica[cç][aã]o", 3),
    (r"prestador de servi", 2),
    (r"\btomador\b", 2),
    (r"inscri[cç][aã]o municipal", 1),
    (r"valor total da nota", 3),
    (r"\bicms\b|\biss\b|\bissqn\b", 1),
]

PISTAS_COMPROVANTE = [
    (r"comprovante de (pagamento|transfer[eê]ncia|transa[cç][aã]o|pix|dep[oó]sito)", 7),
    (r"comprovante", 3),
    (r"\bpix\b", 3),
    (r"\bted\b|\bdoc\b", 2),
    (r"pagamento (efetuado|realizado)|pago com sucesso", 4),
    (r"transfer[eê]ncia (realizada|efetuada)", 4),
    (r"autentica[cç][aã]o( banc[aá]ria)?", 3),
    (r"id (da )?transa[cç][aã]o|\be2e\b|n[uú]mero de controle", 3),
    (r"\bfavorecido\b|\bpagador\b|\bbenefici[aá]rio\b|\bdestinat[aá]rio\b", 2),
    (r"data (do )?pagamento|data da transfer", 2),
    (r"linha digit[aá]vel|c[oó]digo de barras", 1),
    (r"ag[eê]ncia.{0,20}conta", 1),
]

# Rótulos de valor, em ordem de prioridade
ROTULOS_VALOR_NF = [
    r"valor\s+total\s+da\s+nota(?:\s+fiscal)?",
    r"valor\s+total\s+da\s+nfs-?e",
    r"total\s+da\s+nota",
    r"valor\s*l[ií]quido(?:\s+da\s+nota)?",
    r"valor\s+total\s+dos\s+servi[cç]os",
    r"valor\s+dos\s+servi[cç]os",
    r"valor\s+total",
    r"total\s+geral",
    r"vl\.?\s*total",
]

ROTULOS_VALOR_COMP = [
    r"valor\s+(?:pago|do\s+pagamento|da\s+transfer[eê]ncia|da\s+transa[cç][aã]o|total|debitado)",
    r"valor\s+(?:r\$)?",
    r"total\s+pago",
    r"quantia",
]

PASTA_NF = "Notas_Fiscais"
PASTA_COMP = "Comprovantes"
PASTA_REVISAR = "Revisar"
PASTA_DUP = "Duplicados"

LIMIAR_MINIMO = 5       # pontuação mínima para considerar um tipo
MARGEM_MINIMA = 2       # diferença mínima entre NF e comprovante para não ser ambíguo

EXTENSOES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xml"}

RE_VALOR = r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
RE_DATA = r"\b(\d{2})[/\-.](\d{2})[/\-.](\d{4})\b"


# --------------------------------------------------------------------------
# Extração de texto
# --------------------------------------------------------------------------

def texto_pdf(caminho: Path) -> str:
    """Tenta camada de texto; se vier vazia, faz OCR."""
    texto = ""
    try:
        import pdfplumber
        with pdfplumber.open(caminho) as pdf:
            for pagina in pdf.pages[:5]:  # primeiras 5 páginas bastam
                texto += (pagina.extract_text() or "") + "\n"
    except Exception:
        try:
            from pypdf import PdfReader
            for pagina in PdfReader(str(caminho)).pages[:5]:
                texto += (pagina.extract_text() or "") + "\n"
        except Exception:
            pass

    if len(texto.strip()) < 40:  # provavelmente escaneado
        texto = ocr_pdf(caminho)
    return texto


def ocr_pdf(caminho: Path) -> str:
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        return ""
    texto = ""
    try:
        pdf = pdfium.PdfDocument(str(caminho))
        for i in range(min(len(pdf), 3)):
            imagem = pdf[i].render(scale=2.5).to_pil()
            texto += pytesseract.image_to_string(imagem, lang=idioma_ocr()) + "\n"
    except Exception as e:
        print(f"  [aviso] OCR falhou em {caminho.name}: {e}")
    return texto


def texto_imagem(caminho: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(caminho), lang=idioma_ocr())
    except Exception as e:
        print(f"  [aviso] OCR falhou em {caminho.name}: {e}")
        return ""


def idioma_ocr() -> str:
    """Usa português se o pacote 'por' do Tesseract estiver instalado."""
    try:
        import pytesseract
        return "por+eng" if "por" in pytesseract.get_languages() else "eng"
    except Exception:
        return "eng"


def extrair_texto(caminho: Path) -> str:
    ext = caminho.suffix.lower()
    if ext == ".pdf":
        return texto_pdf(caminho)
    if ext == ".xml":
        return caminho.read_text(encoding="utf-8", errors="ignore")
    return texto_imagem(caminho)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Minúsculas e espaços normalizados (mantém acentos; as regex tratam as variações)."""
    return re.sub(r"[ \t]+", " ", texto.lower())


def valor_para_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def formatar_valor(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")  # 1500,00


def nome_seguro(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\-.$,]+", "_", s).strip("_")


def pontuar(texto: str, pistas) -> int:
    return sum(peso for padrao, peso in pistas if re.search(padrao, texto))


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Extração de campos
# --------------------------------------------------------------------------

def valor_mesma_linha(texto: str, pos: int) -> str | None:
    """Valor logo após o rótulo, na mesma linha ("Valor total: R$ 1.500,00")."""
    fim = texto.find("\n", pos)
    m = re.match(r"[^\d\n]{0,25}" + RE_VALOR, texto[pos:fim if fim != -1 else None])
    return m.group(1) if m else None


def valor_coluna(texto: str, m: re.Match) -> str | None:
    """Layout de tabela (ex.: DANFE): o rótulo é título de coluna e os valores estão
    na linha de baixo. Se o rótulo é a última coluna, pega o último valor; se é a
    primeira, o primeiro. No meio da linha a coluna é ambígua, então desiste."""
    inicio_linha = texto.rfind("\n", 0, m.start()) + 1
    fim_linha = texto.find("\n", m.end())
    if fim_linha == -1:
        return None
    fim_prox = texto.find("\n", fim_linha + 1)
    prox = texto[fim_linha + 1:fim_prox if fim_prox != -1 else None]
    valores = re.findall(r"(?<![\d.,])" + RE_VALOR + r"(?![\d,])", prox)
    if not valores:
        return None
    if not texto[m.end():fim_linha].strip():
        return valores[-1]
    if not texto[inicio_linha:m.start()].strip():
        return valores[0]
    return None


def achar_valor(texto: str, rotulos) -> float | None:
    """Procura um valor monetário associado ao rótulo (mesma linha ou coluna da tabela)."""
    for rotulo in rotulos:
        for m in re.finditer(rotulo, texto):
            bruto = valor_mesma_linha(texto, m.end()) or valor_coluna(texto, m)
            if bruto and valor_para_float(bruto) > 0:
                return valor_para_float(bruto)
    return None


def extrair_nf(texto_original: str) -> tuple[str | None, float | None, str | None]:
    """Retorna (numero, valor, cnpj_emitente)."""
    texto = normalizar(texto_original)
    numero = valor = cnpj = None

    # 1) XML de NF-e
    m = re.search(r"<nnf>(\d+)</nnf>", texto)
    if m:
        numero = str(int(m.group(1)))
        mv = re.search(r"<vnf>([\d.]+)</vnf>", texto)
        if mv:
            valor = float(mv.group(1))
        mc = re.search(r"<emit>.*?<cnpj>(\d{14})</cnpj>", texto, re.S)
        if mc:
            cnpj = mc.group(1)
        return numero, valor, cnpj

    # 2) Chave de acesso (44 dígitos): posições 26-34 = número da NF
    sem_espacos = re.sub(r"(?<=\d)\s(?=\d)", "", texto)
    m = re.search(r"\b(\d{44})\b", sem_espacos)
    if m:
        chave = m.group(1)
        numero = str(int(chave[25:34]))
        cnpj = chave[6:20]

    # 3) Rótulos textuais
    if not numero:
        padroes = [
            r"(?:nf-?e|nfs-?e|nota\s+fiscal(?:\s+de\s+servi[cç]os?)?(?:\s+eletr[oô]nica)?)\s*(?:n[ºo°\.]*|n[uú]mero)?\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,9})\b",
            r"n[uú]mero\s+da\s+(?:nota|nfs-?e|nf-?e)\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,9})\b",
            r"\bn[ºo°]\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,9})\b",
        ]
        for p in padroes:
            m = re.search(p, texto)
            if m:
                numero = str(int(m.group(1).replace(".", "")))
                break

    valor = achar_valor(texto, ROTULOS_VALOR_NF)

    if not cnpj:
        m = re.search(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b", texto)
        if m:
            cnpj = re.sub(r"\D", "", m.group(1))
    return numero, valor, cnpj


def extrair_comprovante(texto_original: str) -> tuple[float | None, str | None, str | None]:
    """Retorna (valor, data 'AAAA-MM-DD', numero_nf_citado)."""
    texto = normalizar(texto_original)
    valor = achar_valor(texto, ROTULOS_VALOR_COMP)

    # Plano B: se só existir um "R$ x" no documento, usa-o
    if valor is None:
        achados = {valor_para_float(v) for v in re.findall(r"r\$\s*" + RE_VALOR, texto)}
        if len(achados) == 1:
            valor = achados.pop()

    data = None
    m = re.search(RE_DATA, texto)
    if m:
        d, mth, a = m.groups()
        if 1 <= int(mth) <= 12 and 1 <= int(d) <= 31:
            data = f"{a}-{mth}-{d}"

    nf = None
    m = re.search(r"(?:\bnf-?e?|nota\s+fiscal)\s*(?:n[ºo°\.]*)?\s*[:\-]?\s*(\d{1,9})\b", texto)
    if m:
        nf = str(int(m.group(1)))
    return valor, data, nf


# --------------------------------------------------------------------------
# Modelo e pipeline
# --------------------------------------------------------------------------

@dataclass
class Documento:
    origem: Path
    tipo: str = "indefinido"          # nf | comprovante | indefinido
    pontos_nf: int = 0
    pontos_comp: int = 0
    numero_nf: str | None = None
    valor: float | None = None
    data: str | None = None
    cnpj: str | None = None
    nf_vinculada: str | None = None
    hash: str = ""
    destino: Path | None = None
    observacao: list[str] = field(default_factory=list)


def classificar(texto: str) -> tuple[str, int, int]:
    t = normalizar(texto)
    pn, pc = pontuar(t, PISTAS_NF), pontuar(t, PISTAS_COMPROVANTE)
    if re.search(r"<nnf>", t):  # XML de NF-e é inequívoco
        return "nf", 99, 0
    if pn >= LIMIAR_MINIMO and pn - pc >= MARGEM_MINIMA:
        return "nf", pn, pc
    if pc >= LIMIAR_MINIMO and pc - pn >= MARGEM_MINIMA:
        return "comprovante", pn, pc
    return "indefinido", pn, pc


def analisar(caminho: Path) -> Documento:
    doc = Documento(origem=caminho, hash=sha256(caminho))
    texto = extrair_texto(caminho)
    if not texto.strip():
        doc.observacao.append("sem texto legível")
        return doc

    doc.tipo, doc.pontos_nf, doc.pontos_comp = classificar(texto)

    if doc.tipo == "nf":
        doc.numero_nf, doc.valor, doc.cnpj = extrair_nf(texto)
        if not doc.numero_nf:
            doc.observacao.append("número da NF não encontrado")
        if doc.valor is None:
            doc.observacao.append("valor da NF não encontrado")
    elif doc.tipo == "comprovante":
        doc.valor, doc.data, doc.nf_vinculada = extrair_comprovante(texto)
        if doc.valor is None:
            doc.observacao.append("valor do comprovante não encontrado")
    else:
        doc.observacao.append("tipo não identificado")
    return doc


def vincular_comprovantes(docs: list[Documento]) -> None:
    """Liga comprovantes a notas: pelo número citado ou, se único, pelo valor igual."""
    nfs = [d for d in docs if d.tipo == "nf" and d.numero_nf]
    por_numero = {d.numero_nf: d for d in nfs}
    for c in (d for d in docs if d.tipo == "comprovante"):
        if c.nf_vinculada and c.nf_vinculada in por_numero:
            continue
        c.nf_vinculada = None
        if c.valor is None:
            continue
        candidatas = [n for n in nfs if n.valor is not None and abs(n.valor - c.valor) < 0.005]
        if len(candidatas) == 1:
            c.nf_vinculada = candidatas[0].numero_nf
            c.observacao.append("vinculado à NF pelo valor")
        elif len(candidatas) > 1:
            c.observacao.append("valor bate com mais de uma NF; vínculo não feito")


def montar_nome(doc: Documento) -> tuple[str, str]:
    """Retorna (pasta, nome_base_sem_extensao)."""
    if doc.tipo == "nf":
        if doc.numero_nf and doc.valor is not None:
            return PASTA_NF, f"NF_{doc.numero_nf}_R${formatar_valor(doc.valor)}"
        return PASTA_REVISAR, f"NF_{doc.numero_nf or 'SEMNUMERO'}_R${formatar_valor(doc.valor) if doc.valor else 'SEMVALOR'}__revisar"
    if doc.tipo == "comprovante":
        if doc.valor is not None:
            ref = f"NF{doc.nf_vinculada}" if doc.nf_vinculada else (doc.data or "SEMDATA")
            return PASTA_COMP, f"COMP_{ref}_R${formatar_valor(doc.valor)}"
        return PASTA_REVISAR, f"COMP_{doc.data or 'SEMDATA'}_SEMVALOR__revisar"
    return PASTA_REVISAR, nome_seguro(doc.origem.stem)


def nome_livre(pasta: Path, base: str, ext: str, reservados: set[Path]) -> Path:
    candidato = pasta / f"{nome_seguro(base)}{ext}"
    n = 2
    while candidato.exists() or candidato in reservados:
        candidato = pasta / f"{nome_seguro(base)}_{n}{ext}"
        n += 1
    return candidato


def listar_arquivos(entrada: Path, saida: Path, recursivo: bool = False) -> list[Path]:
    padrao = "**/*" if recursivo else "*"
    return sorted(
        p for p in entrada.glob(padrao)
        if p.is_file() and p.suffix.lower() in EXTENSOES and saida not in p.parents
    )


def planejar(arquivos: list[Path], saida: Path, progresso=print) -> list[Documento]:
    """Analisa os arquivos e define o destino de cada um (sem mexer em nada)."""
    docs, vistos = [], {}
    for i, arq in enumerate(arquivos, 1):
        progresso(f"[{i}/{len(arquivos)}] {arq.name}")
        d = analisar(arq)
        if d.hash in vistos:
            d.tipo = "duplicado"
            d.observacao = [f"idêntico a {vistos[d.hash].name}"]
        else:
            vistos[d.hash] = arq
        docs.append(d)

    vincular_comprovantes([d for d in docs if d.tipo != "duplicado"])

    # Comprovantes vinculados vão para o mesmo PDF da NF (aglutinação)
    nfs = {d.numero_nf: d for d in docs if d.tipo == "nf" and d.numero_nf}
    anexos: dict[str, list[Documento]] = {}
    for d in docs:
        if d.tipo == "comprovante" and d.nf_vinculada in nfs:
            anexos.setdefault(d.nf_vinculada, []).append(d)

    reservados: set[Path] = set()
    for d in docs:
        if d.tipo == "comprovante" and d.nf_vinculada in nfs:
            continue  # destino definido junto com a NF
        if d.tipo == "duplicado":
            pasta, base = PASTA_DUP, nome_seguro(d.origem.stem)
        else:
            pasta, base = montar_nome(d)
        grupo = anexos.get(d.numero_nf, []) if d.tipo == "nf" else []
        if not grupo:
            d.destino = nome_livre(saida / pasta, base, d.origem.suffix.lower(), reservados)
            reservados.add(d.destino)
            continue

        # NF + comprovantes viram um só PDF; XML não cabe no PDF e fica ao lado, com o mesmo nome
        pdf = nome_livre(saida / pasta, base, ".pdf", reservados)
        reservados.add(pdf)
        for membro in [d, *grupo]:
            if mesclavel(membro.origem):
                membro.destino = pdf
            else:
                membro.destino = nome_livre(saida / pasta, base, membro.origem.suffix.lower(), reservados)
                reservados.add(membro.destino)
        for c in grupo:
            c.observacao.append(f"aglutinado ao PDF da NF {d.numero_nf}")
    return docs


def mesclavel(caminho: Path) -> bool:
    return caminho.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def paginas_pdf(caminho: Path):
    """Lê um PDF ou converte uma imagem em PDF (em memória) para aglutinar."""
    import io
    from pypdf import PdfReader
    if caminho.suffix.lower() == ".pdf":
        return PdfReader(str(caminho))
    from PIL import Image, ImageSequence
    with Image.open(caminho) as img:
        quadros = [q.convert("RGB") for q in ImageSequence.Iterator(img)]
    buf = io.BytesIO()
    quadros[0].save(buf, "PDF", save_all=True, append_images=quadros[1:], resolution=150)
    buf.seek(0)
    return PdfReader(buf)


def executar(docs: list[Documento], saida: Path, mover: bool = False) -> Path:
    """Copia/move os arquivos (aglutinando NF + comprovantes) e grava o relatório.
    Retorna o caminho do CSV."""
    from pypdf import PdfWriter
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # PDFs de bancos costumam ter pequenos defeitos

    grupos: dict[Path, list[Documento]] = {}
    for d in docs:
        grupos.setdefault(d.destino, []).append(d)

    for destino, grupo in grupos.items():
        destino.parent.mkdir(parents=True, exist_ok=True)
        if len(grupo) == 1 and grupo[0].origem.suffix.lower() == destino.suffix:
            (shutil.move if mover else shutil.copy2)(grupo[0].origem, destino)
            continue
        grupo.sort(key=lambda d: d.tipo != "nf")  # NF primeiro, depois comprovantes
        escritor = PdfWriter()
        for d in grupo:
            escritor.append(paginas_pdf(d.origem))
        with open(destino, "wb") as f:
            escritor.write(f)
        if mover:
            for d in grupo:
                d.origem.unlink()

    saida.mkdir(parents=True, exist_ok=True)
    relatorio = saida / "relatorio.csv"
    with open(relatorio, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["arquivo_original", "tipo", "numero_nf", "valor", "data", "nf_vinculada",
                    "pontos_nf", "pontos_comprovante", "novo_caminho", "observacoes"])
        for d in docs:
            w.writerow([d.origem.name, d.tipo, d.numero_nf or "",
                        formatar_valor(d.valor) if d.valor is not None else "",
                        d.data or "", d.nf_vinculada or "", d.pontos_nf, d.pontos_comp,
                        d.destino.relative_to(saida), "; ".join(d.observacao)])
    return relatorio


def resumir(docs: list[Documento]) -> dict[str, int]:
    return {t: sum(1 for d in docs if d.tipo == t) for t in ("nf", "comprovante", "indefinido", "duplicado")}


def main() -> int:
    ap = argparse.ArgumentParser(description="Organiza notas fiscais e comprovantes de pagamento.")
    ap.add_argument("entrada", type=Path, help="pasta com os documentos")
    ap.add_argument("--saida", type=Path, help="pasta de destino (padrão: ENTRADA/_organizado)")
    ap.add_argument("--aplicar", action="store_true", help="executa de verdade (sem isso, só simula)")
    ap.add_argument("--mover", action="store_true", help="move os arquivos em vez de copiar")
    ap.add_argument("--recursivo", action="store_true", help="inclui subpastas")
    args = ap.parse_args()

    entrada = args.entrada.resolve()
    if not entrada.is_dir():
        print(f"Pasta não encontrada: {entrada}")
        return 1
    saida = (args.saida or entrada / "_organizado").resolve()

    arquivos = listar_arquivos(entrada, saida, args.recursivo)
    if not arquivos:
        print("Nenhum documento encontrado.")
        return 0

    print(f"{len(arquivos)} arquivo(s) encontrado(s). {'APLICANDO' if args.aplicar else 'SIMULAÇÃO'}\n")
    docs = planejar(arquivos, saida)

    if args.aplicar:
        relatorio = executar(docs, saida, args.mover)

    print("\n" + "=" * 78)
    for d in docs:
        obs = f"  ⚠ {'; '.join(d.observacao)}" if d.observacao else ""
        print(f"{d.tipo.upper():<12} {d.origem.name}\n   → {d.destino.relative_to(saida)}{obs}")

    if args.aplicar:
        print(f"\nRelatório salvo em: {relatorio}")
    else:
        print("\nSimulação concluída. Rode novamente com --aplicar para executar.")

    resumo = resumir(docs)
    print(f"Resumo: {resumo['nf']} NF | {resumo['comprovante']} comprovantes | "
          f"{resumo['indefinido']} p/ revisar | {resumo['duplicado']} duplicados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
