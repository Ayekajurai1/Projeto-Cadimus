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
import functools
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
    (r"transfer[eê]ncia\s+entre\s+contas", 5),
    (r"\bsisbb\b|autoatendimento", 3),
]

# Documentos de apoio (não são NF nem comprovante): propostas, contratos, boletos...
PISTAS_OUTRO = [
    (r"proposta\s+(?:comercial|de\s+venda|t[eé]cnica|n)|segue\s+or[cç]amento|\bor[cç]amento\b", 6),
    (r"\bcota[cç][aã]o\b", 4),
    (r"contrato\s+de\s+compra|cl[aá]usula|pelo\s+presente\s+instrumento", 6),
    (r"of[ií]cio|presta[cç][aã]o\s+de\s+contas|justificativa", 5),
    (r"n[aã]o\s+comprova\s+pagamento|n[aã]o\s+[eé]\s+documento\s+fiscal", 8),
    (r"recibo\s+do\s+pagador|ficha\s+de\s+compensa[cç][aã]o|aqui\s+est[aá]\s+seu\s+boleto", 6),
    (r"local\s*de\s*pagamento|nosso\s*n[uú]mero|pague\s+agora", 4),
    (r"gerir/titulo|confirmar\s*instru|altera[cç][aã]o\s+de\s+vencimento", 6),
]

# Palavras no NOME do arquivo (pista extra, somada à do conteúdo)
NOME_COMPROVANTE = r"comprovante|\bpix\b|\bted\b|transfer[eê]ncia|pagamento"
NOME_NF = r"\bnf-?e?\b|\bnf\s*\d|nota\s+fiscal|danfe|\bnfs-?e\b"
NOME_OUTRO = (r"boleto|proposta|or[cç]amento|cota[cç][aã]o|contrato|justificativa|formul[aá]rio|"
              r"of[ií]cio|pedido|ordem\s+de\s+servi[cç]o|altera[cç][aã]o")

# Rótulos de valor, em ordem de prioridade
ROTULOS_VALOR_NF = [
    r"valor\s+total\s+da\s+nota(?:\s+fiscal)?",
    r"valor\s+total\s+da\s+nfs-?e",
    r"v(?:alor|l)?\.?\s*total\s+(?:da\s+)?(?:nota|nf-?e?)\b",
    r"valor\s+(?:da\s+)?nota\b(?!\s+fiscal\s+(?:indicada|eletr))",
    r"total\s+da\s+nota",
    r"valor\s*l[ií]quido(?:\s+da\s+(?:nota|nfs-?e))?",
    r"valor\s+total\s+d[oa]s?\s+servi[cç]os?",
    r"valor\s+d[oa]s?\s+servi[cç]os?",
    r"valor\s+total(?!\s+d[oa]s?\s+(?:produtos|tributos|impostos|icms|ipi|frete|seguro|desconto))",
    r"total\s+geral",
    r"vl\.?\s*total",
]

# Rótulos que, num comprovante, identificam o valor pago sem ambiguidade
ROTULOS_VALOR_COMP = [
    r"valor\s+(?:pago|do\s+pagamento|da\s+transfer[eê]ncia|da\s+transa[cç][aã]o|total|debitado|do\s+pix|enviado)",
    r"total\s+pago",
    r"quantia",
]
# Rótulo genérico, usado só se os específicos falharem
ROTULOS_VALOR_COMP_GENERICO = [r"\bvalor\b"]
# Linhas com essas palavras falam de tarifas/limites, não do valor pago
RE_LINHA_NAO_VALOR = r"tarif|m[aá]xim|m[ií]nim|limite|taxa|juros|multa|encargo|desconto|saldo"

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
    texto = camada_texto_pdf(caminho)
    if len(texto.strip()) < 40:  # provavelmente escaneado
        texto = ocr_pdf(caminho)
    return texto


def camada_texto_pdf(caminho: Path) -> str:
    """Texto embutido no PDF (vazio em documentos escaneados)."""
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
            texto += pytesseract.image_to_string(preparar_ocr(imagem), lang=idioma_ocr()) + "\n"
    except Exception as e:
        print(f"  [aviso] OCR falhou em {caminho.name}: {e}")
    return texto


def preparar_ocr(img):
    """Tons de cinza e, se a imagem for pequena (foto/print), amplia para o OCR ler melhor."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1600:
        fator = 1600 / img.width
        img = img.resize((1600, round(img.height * fator)))
    return img


def texto_imagem(caminho: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
        with Image.open(caminho) as img:
            return pytesseract.image_to_string(preparar_ocr(img), lang=idioma_ocr())
    except Exception as e:
        print(f"  [aviso] OCR falhou em {caminho.name}: {e}")
        return ""


@functools.cache
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
    # próxima linha com conteúdo (o OCR às vezes deixa linhas em branco no meio)
    seguintes = [l for l in texto[fim_linha + 1:].split("\n", 3)[:3] if l.strip()]
    if not seguintes:
        return None
    valores = re.findall(r"(?<![\d.,])" + RE_VALOR + r"(?![\d,])", seguintes[0])
    if not valores:
        return None
    borda = " \t|[](){}_-:.;"  # bordas de tabela lidas pelo OCR
    if not texto[m.end():fim_linha].strip(borda):
        return valores[-1]
    if not texto[inicio_linha:m.start()].strip(borda):
        return valores[0]
    return None


def linha_de(texto: str, pos: int) -> str:
    inicio = texto.rfind("\n", 0, pos) + 1
    fim = texto.find("\n", pos)
    return texto[inicio:fim if fim != -1 else None]


def achar_valor(texto: str, rotulos, ignorar: str | None = None) -> float | None:
    """Procura um valor monetário associado ao rótulo (mesma linha ou coluna da tabela).
    `ignorar`: regex; rótulos em linhas que casam com ela são pulados."""
    for rotulo in rotulos:
        for m in re.finditer(rotulo, texto):
            if ignorar and re.search(ignorar, linha_de(texto, m.start())):
                continue
            bruto = valor_mesma_linha(texto, m.end()) or valor_coluna(texto, m)
            if bruto and valor_para_float(bruto) > 0:
                return valor_para_float(bruto)
    return None


def chave_valida(chave: str) -> bool:
    """Confere o dígito verificador (módulo 11) e o modelo (55 = NF-e, 65 = NFC-e)."""
    if len(chave) != 44 or chave[20:22] not in ("55", "65"):
        return False
    soma = sum(int(d) * p for d, p in zip(reversed(chave[:43]), [2, 3, 4, 5, 6, 7, 8, 9] * 6))
    dv = 11 - soma % 11
    return int(chave[43]) == (0 if dv >= 10 else dv)


def achar_chave_acesso(texto: str) -> str | None:
    """Acha a chave de 44 dígitos, mesmo quebrada em grupos de 4 (DANFE) ou lida por OCR."""
    sem_espacos = re.sub(r"(?<=\d)[ \t.]+(?=\d)", "", texto)
    for m in re.finditer(r"(?<!\d)(\d{44})(?!\d)", sem_espacos):
        antes = sem_espacos[max(0, m.start() - 25):m.start()]
        if re.search(r"ref|referenciad", antes):  # chave de OUTRA nota citada nesta
            continue
        if chave_valida(m.group(1)):
            return m.group(1)
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
    chave = achar_chave_acesso(texto)
    if chave:
        numero = str(int(chave[25:34]))
        cnpj = chave[6:20]

    # 3) Rótulos textuais
    if not numero:
        padroes = [
            r"n[uú]mero\s+da\s+(?:nota(?:\s+fiscal)?|nfs-?e|nf-?e)\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,15})\b",
            r"\bn[ºo°]\s*[:\-]?\s*([\do]{3}\.[\do]{3}\.[\do]{3})\b",  # DANFE: Nº 000.171.660
            r"\bn[ºo°]\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,15})\b",
            r"(?:nf-?e|nfs-?e|nota\s+fiscal(?:\s+de\s+servi[cç]os?)?(?:\s+eletr[oô]nica)?)[ \t]*(?:n[ºo°\.]*|n[uú]mero)?[ \t]*[:\-]?[ \t]*(\d{1,3}(?:\.\d{3})+|\d{1,15})\b",
        ]
        for p in padroes:
            m = re.search(p, texto)
            if m:
                numero = str(int(m.group(1).replace(".", "").replace("o", "0")))
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
    valor = achar_valor(texto, ROTULOS_VALOR_COMP, ignorar=RE_LINHA_NAO_VALOR)

    # Valor em destaque, sozinho na linha ("R$ 6.870,00"), comum em Pix e apps de banco
    if valor is None:
        m = re.search(r"^[^\w\n]*r\$\s*" + RE_VALOR + r"[^\w\n]*$", texto, re.M)
        if m:
            valor = valor_para_float(m.group(1))

    if valor is None:
        valor = achar_valor(texto, ROTULOS_VALOR_COMP_GENERICO, ignorar=RE_LINHA_NAO_VALOR)

    # Pagamento de boleto: o valor está nos 10 últimos dígitos da linha digitável
    if valor is None:
        for linha in texto.splitlines():
            if len(re.findall(r"\d{5,}", linha)) >= 3:
                m = re.search(r"(?<!\d)\d{4}(\d{10})\s*$", linha.strip())
                if m and int(m.group(1)) > 0:
                    valor = int(m.group(1)) / 100
                    break

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
    m = re.search(r"(?:\bnf-?e?|nota\s+fiscal)\s*(?:n[ºo°\.]*)?\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,9})\b", texto)
    if m:
        nf = str(int(m.group(1).replace(".", "")))
    return valor, data, nf


def valores_citados(texto: str) -> set[float]:
    """Todos os valores monetários do texto (ex.: para achar a NF paga em parcelas/tarifas)."""
    return {valor_para_float(v) for v in re.findall(r"(?<![\d.,])" + RE_VALOR + r"(?![\d,])", texto)}


def cnpjs_citados(texto: str) -> set[str]:
    """CNPJs do texto, sem zeros à esquerda (bancos às vezes omitem o zero inicial)."""
    achados = re.findall(r"(?<![\d.])(\d{1,2}[.,]\s?\d{3}[.,]\s?\d{3}\s?/\s?\d{4}\s?-\s?\d{2}|\d{14})(?!\d)", texto)
    return {re.sub(r"\D", "", c).lstrip("0") for c in achados}


# --------------------------------------------------------------------------
# Modelo e pipeline
# --------------------------------------------------------------------------

@dataclass
class Documento:
    origem: Path
    tipo: str = "indefinido"          # nf | cce (carta de correção) | comprovante | indefinido | duplicado
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
    texto: str = field(default="", repr=False)
    valores: set[float] = field(default_factory=set, repr=False)   # todos os valores citados (comprovante)
    cnpjs: set[str] = field(default_factory=set, repr=False)       # todos os CNPJs citados (comprovante)
    ordem: int = 0                                                  # posição dentro do PDF aglutinado
    ocr: bool = False                                               # texto veio de OCR (escaneado/foto)


def classificar(texto: str, nome: str = "") -> tuple[str, int, int]:
    """Classifica pelo conteúdo, somando pistas do nome do arquivo (se houver)."""
    t = normalizar(texto)
    pn, pc, po = pontuar(t, PISTAS_NF), pontuar(t, PISTAS_COMPROVANTE), pontuar(t, PISTAS_OUTRO)
    if re.search(r"<nnf>", t):  # XML de NF-e é inequívoco
        return "nf", 99, 0
    if re.search(r"carta\s+de\s+corre[cç][aã]o", t) and achar_chave_acesso(t):
        return "cce", pn, pc  # evento vinculado a uma NF-e: vai junto com ela

    n = normalizar_nome(nome)
    if re.search(NOME_COMPROVANTE, n):
        pc += 6
    elif re.search(NOME_OUTRO, n):
        po += 8
    elif re.search(NOME_NF, n):
        pn += 4

    # proposta/contrato/boleto etc. citam "pix", "pagador"... mas não são comprovante
    if po >= LIMIAR_MINIMO and po >= max(pn, pc) - MARGEM_MINIMA:
        return "indefinido", pn, pc
    if pn >= LIMIAR_MINIMO and pn - pc >= MARGEM_MINIMA:
        return "nf", pn, pc
    if pc >= LIMIAR_MINIMO and pc - pn >= MARGEM_MINIMA:
        return "comprovante", pn, pc
    return "indefinido", pn, pc


def normalizar_nome(nome: str) -> str:
    """Nome do arquivo sem extensão, minúsculo, com _ e - viram espaço."""
    return re.sub(r"[_]+", " ", Path(nome).stem.lower())


def pistas_do_nome(nome: str) -> tuple[str | None, float | None]:
    """(número da NF, valor) citados no nome do arquivo, ex.: "NF 4111 R$818,00 - Fornecedor.pdf"."""
    n = normalizar_nome(nome)
    numero = valor = None
    m = re.search(r"(?:\bnf-?e?|nota\s+fiscal|\bnfs-?e)\s*[nº°.:\-]*\s*(\d{1,3}(?:\.\d{3})+|\d{1,15})(?![\d,])", n)
    if m:
        numero = str(int(m.group(1).replace(".", "")))
    m = re.search(r"r\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)", n)
    if m:
        valor = float(m.group(1).replace(".", "").replace(",", "."))
    return numero, valor


def completude(doc: Documento) -> int:
    """Quanto da informação esperada foi encontrada (para comparar camada de texto x OCR)."""
    if doc.tipo == "indefinido":
        return 0
    if doc.tipo in ("nf", "cce"):
        return 1 + bool(doc.numero_nf) + (doc.valor is not None or doc.tipo == "cce")
    return 1 + (doc.valor is not None)


def analisar(caminho: Path) -> Documento:
    doc = Documento(origem=caminho, hash=sha256(caminho), texto=extrair_texto(caminho))
    ext = caminho.suffix.lower()
    doc.ocr = ext != ".xml" and (ext != ".pdf" or len(camada_texto_pdf(caminho).strip()) < 40)
    if doc.texto.strip():
        doc.tipo, doc.pontos_nf, doc.pontos_comp = classificar(doc.texto, caminho.name)
    extrair_campos(doc)

    # PDF com camada de texto incompleta ou ilegível (fontes sem mapeamento, nota "impressa"
    # como imagem com pouco texto por cima...): tenta OCR e fica com o resultado mais completo.
    if caminho.suffix.lower() == ".pdf" and completude(doc) < 3:
        texto_ocr = ocr_pdf(caminho)
        if texto_ocr.strip() and texto_ocr != doc.texto:
            alt = Documento(origem=caminho, hash=doc.hash, texto=texto_ocr, ocr=True)
            alt.tipo, alt.pontos_nf, alt.pontos_comp = classificar(texto_ocr, caminho.name)
            extrair_campos(alt)
            if completude(alt) > completude(doc):
                doc = alt
    return doc


def definir_tipo(doc: Documento, tipo: str) -> None:
    """Corrige a classificação automática (ex.: escolha do usuário) e reextrai os campos."""
    doc.tipo = tipo
    extrair_campos(doc)


def extrair_campos(doc: Documento) -> None:
    """Preenche número, valor, data etc. de acordo com o tipo do documento."""
    texto = doc.texto
    doc.numero_nf = doc.valor = doc.data = doc.cnpj = doc.nf_vinculada = None
    doc.valores, doc.cnpjs = set(), set()
    doc.destino = None
    doc.ordem = 0
    doc.observacao = [] if texto.strip() else ["sem texto legível"]

    num_nome, valor_nome = pistas_do_nome(doc.origem.name)
    if doc.tipo == "nf":
        doc.numero_nf, doc.valor, doc.cnpj = extrair_nf(texto)
        doc.valores = valores_citados(normalizar(texto))  # parcelas/fatura citadas na nota
        if not doc.numero_nf and num_nome:
            doc.numero_nf = num_nome
            doc.observacao.append("número lido do nome do arquivo — confira")
        if doc.valor is None and valor_nome is not None:
            doc.valor = valor_nome
            doc.observacao.append("valor lido do nome do arquivo — confira")
        if not doc.numero_nf:
            doc.observacao.append("número da NF não encontrado")
        if doc.valor is None:
            doc.observacao.append("valor da NF não encontrado")
    elif doc.tipo == "cce":
        doc.numero_nf, _, doc.cnpj = extrair_nf(texto)
        if not doc.numero_nf:
            doc.observacao.append("número da NF não encontrado")
    elif doc.tipo == "comprovante":
        doc.valor, doc.data, doc.nf_vinculada = extrair_comprovante(texto)
        doc.nf_vinculada = doc.nf_vinculada or num_nome  # "Comprovante NF4111.pdf"
        if doc.valor is None and valor_nome is not None:
            doc.valor = valor_nome
            doc.observacao.append("valor lido do nome do arquivo — confira")
        t = normalizar(texto)
        doc.valores, doc.cnpjs = valores_citados(t), cnpjs_citados(t)
        if doc.valor is None:
            doc.observacao.append("valor do comprovante não encontrado")
    elif texto.strip():
        doc.observacao.append("não é nota fiscal nem comprovante (ou não foi possível identificar)")


def iguais(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.005


def cita_numero(texto: str, numero: str) -> bool:
    """O número da NF aparece solto no texto (com ou sem pontos de milhar)?"""
    if len(numero) < 4:  # números curtos casam com agência, conta etc.
        return False
    sem_pontos = re.sub(r"(?<=\d)\.(?=\d{3})", "", texto)
    return re.search(rf"(?<!\d)0*{numero}(?!\d)", sem_pontos) is not None


def vincular_comprovantes(docs: list[Documento], nfs: dict[str, Documento]) -> None:
    """Liga cada comprovante a uma NF (`nfs`: número -> NF principal), da pista mais
    forte para a mais fraca. Só vincula quando a pista aponta para uma única NF."""
    for c in (d for d in docs if d.tipo == "comprovante"):
        if c.nf_vinculada in nfs:
            c.observacao.append("vinculado pela NF citada no comprovante")
            continue
        c.nf_vinculada = None
        texto = normalizar(c.texto)
        cnpj_ok = lambda n: bool(n.cnpj) and n.cnpj.lstrip("0") in c.cnpjs
        pistas = [
            ("pelo valor e CNPJ", lambda n: iguais(n.valor, c.valor) and cnpj_ok(n)),
            ("pelo valor", lambda n: iguais(n.valor, c.valor)),
            ("pelo valor de parcela/fatura citado na NF — confira",
             lambda n: (c.valor or 0) >= 1 and any(iguais(c.valor, v) for v in n.valores)),
            ("pelo número da NF citado", lambda n: cita_numero(texto, n.numero_nf)),
            ("pelo CNPJ do emitente e valor citado", lambda n: cnpj_ok(n) and any(iguais(n.valor, v) for v in c.valores)),
            ("pelo CNPJ do emitente — confira", cnpj_ok),
            ("pelo valor citado no comprovante — confira", lambda n: any(iguais(n.valor, v) for v in c.valores)),
        ]
        for descricao, pista in pistas:
            candidatas = [n for n in nfs.values() if pista(n)]
            if len(candidatas) == 1:
                c.nf_vinculada = candidatas[0].numero_nf
                c.observacao.append(f"vinculado à NF {descricao}")
                break
        else:
            if sum(iguais(n.valor, c.valor) for n in nfs.values()) > 1:
                c.observacao.append("valor bate com mais de uma NF; vínculo não feito")

    vincular_parcelas([d for d in docs if d.tipo == "comprovante"], nfs)


def vincular_parcelas(comps: list[Documento], nfs: dict[str, Documento]) -> None:
    """Pagamento parcelado: comprovantes ainda soltos cuja soma (2 a 4 parcelas) fecha o
    valor que falta pagar de uma NF. Só vincula quando a combinação é única."""
    from itertools import combinations
    soltos = [c for c in comps if c.nf_vinculada not in nfs and c.valor]
    achados: dict[str, list[tuple]] = {}
    for n in nfs.values():
        if n.valor is None:
            continue
        falta = n.valor - sum(c.valor or 0 for c in comps if c.nf_vinculada == n.numero_nf)
        for k in range(2, min(4, len(soltos)) + 1):
            for combo in combinations(soltos, k):
                if iguais(sum(c.valor for c in combo), falta):
                    achados.setdefault(n.numero_nf, []).append(combo)
    usados = [id(c) for combos in achados.values() for combo in combos for c in combo]
    for numero, combos in achados.items():
        combo = combos[0]
        if len(combos) == 1 and all(usados.count(id(c)) == 1 for c in combo):
            for c in combo:
                c.nf_vinculada = numero
                c.observacao.append(f"vinculado à NF pela soma de {len(combo)} parcelas — confira")


def montar_nome(doc: Documento) -> tuple[str, str]:
    """Retorna (pasta, nome_base_sem_extensao)."""
    if doc.tipo == "nf":
        if doc.numero_nf and doc.valor is not None:
            return PASTA_NF, f"NF_{doc.numero_nf}_R${formatar_valor(doc.valor)}"
        return PASTA_REVISAR, f"NF_{doc.numero_nf or 'SEMNUMERO'}_R${formatar_valor(doc.valor) if doc.valor else 'SEMVALOR'}__revisar"
    if doc.tipo == "cce":
        return PASTA_REVISAR, f"CCE_NF{doc.numero_nf or 'SEMNUMERO'}__revisar"
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
    docs = []
    for i, arq in enumerate(arquivos, 1):
        progresso(f"[{i}/{len(arquivos)}] {arq.name}")
        docs.append(analisar(arq))
    return organizar(docs, saida)


def organizar(docs: list[Documento], saida: Path) -> list[Documento]:
    """Marca duplicados, agrupa cada NF com seus anexos e define o destino de cada documento.

    Grupo de uma NF (aglutinado num único PDF, nesta ordem):
      1. a NF principal (a que dá número e valor ao nome)
      2. outras vias da mesma NF (ex.: DANFE escaneado; XML fica ao lado, com o mesmo nome)
      3. cartas de correção da NF
      4. comprovantes vinculados
    """
    vistos: dict[str, Path] = {}
    for d in docs:
        if d.hash in vistos:
            d.tipo = "duplicado"
            d.observacao = [f"idêntico a {vistos[d.hash].name}"]
        else:
            vistos[d.hash] = d.origem
    ativos = [d for d in docs if d.tipo != "duplicado"]

    vias: dict[str, list[Documento]] = {}
    for d in ativos:
        if d.tipo == "nf" and d.numero_nf:
            vias.setdefault(d.numero_nf, []).append(d)
    principais: dict[str, Documento] = {}
    for numero, lista in vias.items():
        # prefere a via com valor, que entra no PDF (DANFE, não XML) e digital (não escaneada)
        principal = min(lista, key=lambda d: (d.valor is None, not mesclavel(d.origem), d.ocr))
        if principal.valor is None:  # empresta o valor de outra via (ex.: do XML)
            principal.valor = next((d.valor for d in lista if d.valor is not None), None)
            if principal.valor is not None:
                principal.observacao.remove("valor da NF não encontrado")
        principais[numero] = principal

    vincular_comprovantes(ativos, principais)

    grupos: dict[str, list[Documento]] = {n: [] for n in principais}
    for d in ativos:
        numero = d.nf_vinculada if d.tipo == "comprovante" else d.numero_nf
        if numero not in principais or d is principais[numero] or d.tipo not in ("nf", "cce", "comprovante"):
            continue
        d.ordem = {"nf": 1, "cce": 2, "comprovante": 3}[d.tipo]
        grupos[numero].append(d)
    anexados = {id(d) for g in grupos.values() for d in g}

    reservados: set[Path] = set()
    for d in docs:
        if id(d) in anexados:
            continue  # destino definido junto com a NF principal
        if d.tipo == "duplicado":
            pasta, base = PASTA_DUP, nome_seguro(d.origem.stem)
        else:
            pasta, base = montar_nome(d)
            if d.tipo == "cce":
                d.observacao.append("carta de correção sem a NF correspondente")
        grupo = grupos.get(d.numero_nf, []) if d is principais.get(d.numero_nf) else []
        if not grupo:
            d.destino = nome_livre(saida / pasta, base, d.origem.suffix.lower(), reservados)
            reservados.add(d.destino)
            continue

        # NF + anexos viram um só PDF; XML não cabe no PDF e fica ao lado, com o mesmo nome
        grupo.sort(key=lambda m: m.ordem)
        pdf = nome_livre(saida / pasta, base, ".pdf", reservados)
        reservados.add(pdf)
        for membro in [d, *grupo]:
            if mesclavel(membro.origem):
                membro.destino = pdf
            else:
                membro.destino = nome_livre(saida / pasta, base, membro.origem.suffix.lower(), reservados)
                reservados.add(membro.destino)
        for m in grupo:
            m.observacao.append(f"aglutinado ao PDF da NF {d.numero_nf}")
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
            (shutil.move if mover else shutil.copyfile)(grupo[0].origem, destino)
            continue
        grupo.sort(key=lambda d: d.ordem)  # NF, outras vias, cartas de correção, comprovantes
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
    return {t: sum(1 for d in docs if d.tipo == t) for t in ("nf", "cce", "comprovante", "indefinido", "duplicado")}


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
    print(f"Resumo: {resumo['nf']} NF | {resumo['cce']} cartas de correção | {resumo['comprovante']} comprovantes | "
          f"{resumo['indefinido']} p/ revisar | {resumo['duplicado']} duplicados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
