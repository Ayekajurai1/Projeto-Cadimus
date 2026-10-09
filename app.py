#!/usr/bin/env python3
"""
Interface web do Organizador de Documentos Fiscais.

A página fica em static/ (index.html, estilo.css, app.js); este arquivo expõe a API
que ela usa:

    POST /api/arquivos              envia arquivos para análise (cria o lote se preciso)
    POST /api/fontes                lê uma pasta local ou link (Google Drive, OneDrive, SharePoint)
    GET  /api/tarefas/<id>          andamento da análise iniciada pelas duas rotas acima
    POST /api/organizar             aglutina e renomeia (respeitando o tipo escolhido)
    GET  /api/lotes/<lote>/zip      baixa tudo organizado
    GET  /api/lotes/<lote>/arquivo/<caminho>   baixa um arquivo gerado
    GET  /api/exemplo               lista os arquivos de exemplo (botão "Carregar exemplo")

Baixar e analisar (OCR) pode levar minutos; por isso as duas primeiras rotas só iniciam
uma tarefa em segundo plano e a página acompanha o andamento por /api/tarefas/<id>.

Uso:
    python app.py              # abre em http://localhost:5000
    python app.py --porta 8080
"""

import argparse
import os
import re
import sys
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import auth
from links import ErroLink, baixar_link, eh_link, provedor
from organizador_documentos import (EXTENSOES, Documento, analisar, definir_tipo, executar,
                                   formatar_valor, listar_arquivos, organizar, resumir)

# Arquivos do programa (página, exemplos): ao lado deste arquivo ou dentro do executável (PyInstaller)
RECURSOS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
# Onde gravar documentos enviados/organizados (no instalador do Windows: %LOCALAPPDATA%\OrganizadorFiscal)
DADOS = Path(os.environ.get("ORGANIZADOR_DADOS") or Path(__file__).parent)
PASTA_TRABALHO = DADOS / "_web"
PASTA_EXEMPLO = RECURSOS / "exemplo"

# tipo na interface <-> tipo no organizador
TIPO_WEB = {"nf": "nota", "cce": "cce", "comprovante": "comprovante", "indefinido": "indefinido"}
TIPO_ORG = {v: k for k, v in TIPO_WEB.items()}

auth.carregar_env()  # credenciais do login (arquivo .env)

app = Flask(__name__, static_folder=str(RECURSOS / "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB por envio
app.config.update(SECRET_KEY=auth.chave_secreta(), SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=12 * 3600)
app.register_blueprint(auth.bp)
app.before_request(auth.exigir_login)

# Documentos já analisados de cada lote: {lote: {id: Documento}}
LOTES: dict[str, dict[str, Documento]] = {}
# Tarefas de análise em andamento/concluídas: {id: {estado, feitos, total, arquivos, fonte, erro}}
TAREFAS: dict[str, dict] = {}
TRAVA = threading.Lock()

# Análises em paralelo (uma por núcleo); cada Tesseract usa 1 thread para não disputar CPU
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
ANALISADOR = ThreadPoolExecutor(max_workers=os.cpu_count() or 2)


def erro(mensagem: str, status: int = 400):
    return jsonify(erro=mensagem), status


def lote_valido(lote: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", lote or ""))


def novo_lote() -> str:
    lote = uuid.uuid4().hex
    with TRAVA:
        LOTES[lote] = {}
    return lote


def nome_original(nome: str) -> str:
    """Mantém acentos e espaços do nome enviado, só descartando caminhos."""
    nome = Path((nome or "").replace("\\", "/")).name.strip()
    return "arquivo" if not nome or nome.startswith(".") else nome


def descrever(doc: Documento) -> dict:
    return {
        "tipo": TIPO_WEB.get(doc.tipo, "indefinido"),
        "numero": doc.numero_nf,
        "valor": formatar_valor(doc.valor) if doc.valor is not None else None,
    }


def plural(n: int, singular: str, plural_: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_ or singular + 's')}"


# --------------------------------------------------------------------------
# Página e exemplos
# --------------------------------------------------------------------------

@app.get("/")
def inicio():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/exemplo")
def exemplo():
    arquivos = listar_arquivos(PASTA_EXEMPLO, PASTA_EXEMPLO / "_organizado") if PASTA_EXEMPLO.is_dir() else []
    return jsonify([{"nome": p.name, "url": f"/api/exemplo/{quote(p.name)}"} for p in arquivos])


@app.get("/api/exemplo/<nome>")
def arquivo_exemplo(nome: str):
    return send_from_directory(PASTA_EXEMPLO, nome)


# --------------------------------------------------------------------------
# Envio e análise
# --------------------------------------------------------------------------

@app.post("/api/arquivos")
def enviar_arquivos():
    """Salva os arquivos e inicia a análise. Responde {lote, tarefa}; o resultado
    ({id, tipo, numero, valor} ou {erro}, na ordem do envio) vem em /api/tarefas/<tarefa>."""
    lote = request.form.get("lote") or novo_lote()
    if not lote_valido(lote) or lote not in LOTES:
        return erro("Lote inválido ou expirado. Clique em “Começar de novo”.", 404)

    itens: list[Path | dict] = []
    for f in request.files.getlist("arquivos"):
        nome = nome_original(f.filename)
        if Path(nome).suffix.lower() not in EXTENSOES:
            itens.append({"nome": nome, "erro": "formato não suportado"})
            continue
        destino = PASTA_TRABALHO / lote / "entrada" / uuid.uuid4().hex[:12] / nome
        destino.parent.mkdir(parents=True)
        f.save(destino)
        itens.append(destino)

    def trabalho(tarefa):
        tarefa["arquivos"] = analisar_todos(tarefa, lote, itens)

    return jsonify(lote=lote, tarefa=iniciar_tarefa(trabalho))


@app.post("/api/fontes")
def adicionar_fonte():
    """Lê uma pasta local ou um link compartilhado e inicia a análise dos documentos.
    Corpo: {lote?, caminho}. Responde {lote, tarefa}; o resultado
    ({fonte: {caminho, tipo, quantidade}, arquivos: [...]}) vem em /api/tarefas/<tarefa>."""
    dados = request.get_json(silent=True) or {}
    caminho = (dados.get("caminho") or "").strip()
    lote = dados.get("lote") or novo_lote()
    if not lote_valido(lote) or lote not in LOTES:
        return erro("Lote inválido ou expirado. Clique em “Começar de novo”.", 404)
    if not caminho:
        return erro("Informe uma pasta ou um link.")

    if eh_link(caminho):
        if provedor(caminho) is None:
            return erro("Link não reconhecido. Use um link do Google Drive, OneDrive ou SharePoint.")
        tipo = {"google": "DRIVE", "onedrive": "ONEDRIVE", "sharepoint": "SHAREPOINT"}[provedor(caminho)]

        tokens = auth.tokens_para_download()  # contas conectadas: permitem ler links privados

        def obter(tarefa):
            tarefa["estado"] = "baixando"
            destino = PASTA_TRABALHO / lote / "links" / uuid.uuid4().hex[:12]
            try:
                return baixar_link(caminho, destino, tokens)
            except Exception:
                shutil.rmtree(destino, ignore_errors=True)
                raise
    else:
        pasta = Path(caminho).expanduser()
        if not pasta.is_dir():
            return erro("Pasta não encontrada no computador onde o organizador está rodando. "
                        "Para pastas na nuvem, cole o link compartilhado.")
        if not listar_arquivos(pasta, pasta / "_organizado"):
            return erro("Nenhum documento (PDF, imagem ou XML) nessa pasta.")
        caminho, tipo = str(pasta.resolve()), "PASTA"

        def obter(tarefa):
            return listar_arquivos(pasta, pasta / "_organizado")

    def trabalho(tarefa):
        arquivos = obter(tarefa)
        tarefa["fonte"] = {"caminho": caminho, "tipo": tipo, "quantidade": len(arquivos)}
        tarefa["arquivos"] = analisar_todos(tarefa, lote, arquivos)

    return jsonify(lote=lote, tarefa=iniciar_tarefa(trabalho))


@app.get("/api/tarefas/<tarefa_id>")
def consultar_tarefa(tarefa_id: str):
    tarefa = TAREFAS.get(tarefa_id)
    if tarefa is None:
        return erro("Tarefa não encontrada (o servidor pode ter sido reiniciado).", 404)
    resposta = {k: tarefa[k] for k in ("estado", "feitos", "total", "erro")}
    if tarefa["estado"] == "concluido":
        resposta.update(arquivos=tarefa["arquivos"], fonte=tarefa["fonte"])
    return jsonify(resposta)


def iniciar_tarefa(trabalho) -> str:
    """Roda `trabalho(tarefa)` em segundo plano e devolve o id para acompanhar."""
    tarefa_id = uuid.uuid4().hex
    tarefa = {"estado": "analisando", "feitos": 0, "total": 0, "arquivos": None, "fonte": None, "erro": None}
    TAREFAS[tarefa_id] = tarefa

    def rodar():
        try:
            trabalho(tarefa)
            tarefa["estado"] = "concluido"
        except ErroLink as e:
            tarefa.update(estado="erro", erro=str(e))
        except Exception as e:
            app.logger.exception("Falha na tarefa %s", tarefa_id)
            tarefa.update(estado="erro", erro=f"Falha inesperada: {e}")

    threading.Thread(target=rodar, daemon=True).start()
    return tarefa_id


def analisar_todos(tarefa: dict, lote: str, itens: list) -> list[dict]:
    """Analisa os arquivos em paralelo, mantendo a ordem e atualizando o progresso.
    Itens que já são dict (ex.: formato recusado) passam direto."""
    tarefa["estado"] = "analisando"
    tarefa["total"] = sum(isinstance(i, Path) for i in itens)

    def um(item):
        if not isinstance(item, Path):
            return item
        resultado = registrar(lote, item)
        with TRAVA:
            tarefa["feitos"] += 1
        return resultado

    return list(ANALISADOR.map(um, itens))


def registrar(lote: str, caminho: Path) -> dict:
    """Analisa um arquivo e guarda no lote. Devolve o que a tela precisa mostrar."""
    try:
        doc = analisar(caminho)
    except Exception as e:  # arquivo corrompido, PDF protegido etc.
        app.logger.warning("Falha ao analisar %s: %s", caminho, e)
        return {"nome": caminho.name, "erro": "não foi possível ler o arquivo"}
    doc_id = uuid.uuid4().hex[:12]
    with TRAVA:
        LOTES[lote][doc_id] = doc
    return {"id": doc_id, "nome": caminho.name, "tamanho": caminho.stat().st_size, **descrever(doc)}


# --------------------------------------------------------------------------
# Organização e download
# --------------------------------------------------------------------------

def montar_resultados(docs: list[Documento], saida: Path, lote: str) -> list[dict]:
    """Um item por arquivo gerado, com o resumo do que entrou nele."""
    grupos: dict[Path, list[Documento]] = {}
    for d in docs:
        grupos.setdefault(d.destino, []).append(d)

    resultados = []
    for destino, grupo in grupos.items():
        grupo.sort(key=lambda d: d.ordem)
        notas = [d for d in grupo if d.tipo == "nf"]
        cces = [d for d in grupo if d.tipo == "cce"]
        comps = [d for d in grupo if d.tipo == "comprovante"]
        anexos = cces + comps
        nomes = " · " + ", ".join(d.origem.name for d in anexos) if anexos else ""
        if notas:
            categoria = "nota"
            partes = [plural(len(notas), "via da nota", "vias da nota") if len(notas) > 1 else "1 nota"]
            if cces:
                partes.append(plural(len(cces), "carta de correção", "cartas de correção"))
            partes.append(plural(len(comps), "comprovante"))
            resumo = " + ".join(partes) + nomes
            if not anexos and notas[0].numero_nf and any(d.nf_vinculada == notas[0].numero_nf or (d.tipo == "cce" and d.numero_nf == notas[0].numero_nf)
                                  for d in docs if d.tipo in ("comprovante", "cce") and d.destino != destino):
                resumo = "1 nota (XML) · anexos no PDF de mesmo nome"
            if notas[0].observacao:
                resumo += " — ⚠ " + "; ".join(notas[0].observacao)
        elif comps and comps[0].nf_vinculada or cces and cces[0].destino.parent.name != "Revisar":
            categoria = "nota"
            resumo = f"Anexos da NF {(comps or cces)[0].nf_vinculada or cces[0].numero_nf}{nomes}"
        elif cces:
            categoria = "revisar"
            resumo = f"Carta de correção da NF {cces[0].numero_nf or '?'}, sem a nota correspondente · {cces[0].origem.name}"
        elif comps:
            categoria = "comprovante"
            motivo = "sem nota correspondente" if comps[0].valor is not None else "sem valor identificado — revisar"
            resumo = f"Comprovante {motivo} · {comps[0].origem.name}"
        elif grupo[0].tipo == "duplicado":
            categoria = "duplicado"
            resumo = f"Duplicado ({grupo[0].observacao[0]}) · {grupo[0].origem.name}"
        else:
            categoria = "revisar"
            resumo = f"Outro documento (não é nota nem comprovante) — revisar · {grupo[0].origem.name}"

        relativo = destino.relative_to(saida).as_posix()
        resultados.append({
            "arquivo": destino.name,
            "pasta": destino.parent.name,
            "resumo": resumo,
            "categoria": categoria,
            "extensao": destino.suffix.lstrip(".").upper(),
            "url": f"/api/lotes/{lote}/arquivo/{quote(relativo)}",
        })

    ordem = {"nota": 0, "comprovante": 1, "revisar": 2, "duplicado": 3}
    resultados.sort(key=lambda r: (ordem[r["categoria"]], r["arquivo"]))
    return resultados


@app.post("/api/organizar")
def organizar_lote():
    """Corpo: {lote, arquivos: [{id, tipo}]} (arquivos enviados ou lidos de pastas/links)."""
    dados = request.get_json(silent=True) or {}
    lote = dados.get("lote") or novo_lote()
    if not lote_valido(lote) or lote not in LOTES:
        return erro("Lote inválido ou expirado. Clique em “Começar de novo”.", 404)

    docs = []
    for item in dados.get("arquivos", []):
        doc = LOTES[lote].get(item.get("id"))
        if doc is None or item.get("tipo") not in TIPO_ORG:
            continue
        definir_tipo(doc, TIPO_ORG[item["tipo"]])  # respeita a correção feita na tela
        docs.append(doc)

    saida = PASTA_TRABALHO / lote / "organizado"
    if not docs:
        return erro("Nenhum documento para organizar.")

    shutil.rmtree(saida, ignore_errors=True)
    organizar(docs, saida)
    executar(docs, saida)  # sempre copia: os arquivos de pastas locais não são alterados
    shutil.make_archive(str(saida), "zip", saida)

    return jsonify(
        lote=lote,
        resultados=montar_resultados(docs, saida, lote),
        resumo=resumir(docs),
        zip=f"/api/lotes/{lote}/zip",
    )


@app.get("/api/lotes/<lote>/zip")
def baixar_zip(lote: str):
    zip_ = PASTA_TRABALHO / lote / "organizado.zip"
    if not lote_valido(lote) or not zip_.exists():
        abort(404)
    return send_file(zip_, as_attachment=True, download_name="documentos_organizados.zip")


@app.get("/api/lotes/<lote>/arquivo/<path:caminho>")
def baixar_arquivo(lote: str, caminho: str):
    if not lote_valido(lote):
        abort(404)
    return send_from_directory(PASTA_TRABALHO / lote / "organizado", caminho, as_attachment=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Interface web do organizador de documentos.")
    ap.add_argument("--porta", type=int, default=5000)
    args = ap.parse_args()
    app.run(host="127.0.0.1", port=args.porta)
