#!/usr/bin/env python3
"""
Interface web do Organizador de Documentos Fiscais.

Uso:
    python app.py              # abre em http://localhost:5000
    python app.py --porta 8080
"""

import argparse
import shutil
import uuid
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_file
from werkzeug.utils import secure_filename

from organizador_documentos import (EXTENSOES, formatar_valor, executar, planejar, resumir)

PASTA_TRABALHO = Path(__file__).parent / "_web"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB por envio


PAGINA = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Organizador de Documentos Fiscais</title>
<style>
  :root { --azul: #2563eb; --cinza: #6b7280; --borda: #e5e7eb; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #f9fafb; color: #111827; }
  main { max-width: 1000px; margin: 0 auto; padding: 32px 20px; }
  h1 { margin: 0 0 4px; font-size: 1.6rem; }
  .sub { color: var(--cinza); margin: 0 0 24px; }
  .card { background: #fff; border: 1px solid var(--borda); border-radius: 12px; padding: 24px; margin-bottom: 20px; }
  .zona { border: 2px dashed #cbd5e1; border-radius: 10px; padding: 36px; text-align: center;
          color: var(--cinza); cursor: pointer; transition: .15s; }
  .zona.ativa, .zona:hover { border-color: var(--azul); background: #eff6ff; color: var(--azul); }
  .zona input { display: none; }
  #lista { margin: 12px 0 0; font-size: .9rem; color: #374151; }
  button, .botao { background: var(--azul); color: #fff; border: 0; border-radius: 8px; padding: 10px 18px;
           font-size: 1rem; cursor: pointer; text-decoration: none; display: inline-block; }
  button:disabled { background: #93c5fd; cursor: wait; }
  .acoes { margin-top: 16px; display: flex; gap: 12px; align-items: center; }
  .resumo { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .resumo div { background: #f3f4f6; border-radius: 8px; padding: 10px 16px; }
  .resumo b { font-size: 1.3rem; display: block; }
  table { width: 100%; border-collapse: collapse; font-size: .9rem; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--borda); vertical-align: top; }
  th { color: var(--cinza); font-weight: 600; }
  .tag { border-radius: 999px; padding: 2px 10px; font-size: .8rem; font-weight: 600; white-space: nowrap; }
  .nf { background: #dcfce7; color: #166534; }
  .comprovante { background: #dbeafe; color: #1e40af; }
  .indefinido { background: #fef3c7; color: #92400e; }
  .duplicado { background: #f3f4f6; color: #4b5563; }
  .obs { color: #b45309; font-size: .82rem; }
  code { font-size: .85rem; }
  .erro { color: #b91c1c; }
</style>
</head>
<body>
<main>
  <h1>Organizador de Documentos Fiscais</h1>
  <p class="sub">Envie notas fiscais e comprovantes (PDF, imagem ou XML). Cada nota fiscal é aglutinada com seus comprovantes num único PDF, nomeado com o número e o valor da nota.</p>

  <form class="card" method="post" action="{{ url_for('organizar') }}" enctype="multipart/form-data" id="form">
    <label class="zona" id="zona">
      <input type="file" name="arquivos" id="arquivos" multiple accept="{{ aceitos }}">
      <strong>Clique para escolher</strong> ou arraste os arquivos aqui
      <div id="lista"></div>
    </label>
    <div class="acoes">
      <button type="submit" id="enviar" disabled>Organizar</button>
      <span id="status" class="sub" style="margin:0"></span>
    </div>
    {% if erro %}<p class="erro">{{ erro }}</p>{% endif %}
  </form>

  {% if docs %}
  <section class="card">
    <div class="resumo">
      <div><b>{{ resumo.nf }}</b>Notas fiscais</div>
      <div><b>{{ resumo.comprovante }}</b>Comprovantes</div>
      <div><b>{{ resumo.indefinido }}</b>Para revisar</div>
      <div><b>{{ resumo.duplicado }}</b>Duplicados</div>
    </div>
    <table>
      <tr><th>Tipo</th><th>Arquivo enviado</th><th>Novo nome</th><th>Valor</th></tr>
      {% for d in docs %}
      <tr>
        <td><span class="tag {{ d.tipo }}">{{ rotulos[d.tipo] }}</span></td>
        <td>{{ d.origem.name }}</td>
        <td><code>{{ d.destino.relative_to(saida) }}</code>
          {% if d.observacao %}<div class="obs">⚠ {{ d.observacao | join('; ') }}</div>{% endif %}</td>
        <td>{{ 'R$ ' ~ formatar_valor(d.valor) if d.valor is not none else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    <div class="acoes">
      <a class="botao" href="{{ url_for('baixar', lote=lote) }}">Baixar tudo organizado (.zip)</a>
    </div>
  </section>
  {% endif %}
</main>
<script>
  const zona = document.getElementById('zona'), input = document.getElementById('arquivos'),
        lista = document.getElementById('lista'), enviar = document.getElementById('enviar');
  function atualizar() {
    const n = input.files.length;
    lista.textContent = n ? `${n} arquivo(s): ` + [...input.files].map(f => f.name).join(', ') : '';
    enviar.disabled = !n;
  }
  input.addEventListener('change', atualizar);
  ['dragenter', 'dragover'].forEach(e => zona.addEventListener(e, ev => { ev.preventDefault(); zona.classList.add('ativa'); }));
  ['dragleave', 'drop'].forEach(e => zona.addEventListener(e, () => zona.classList.remove('ativa')));
  zona.addEventListener('drop', ev => { ev.preventDefault(); input.files = ev.dataTransfer.files; atualizar(); });
  document.getElementById('form').addEventListener('submit', () => {
    enviar.disabled = true;
    document.getElementById('status').textContent = 'Processando... (documentos escaneados passam por OCR e podem demorar)';
  });
</script>
</body>
</html>
"""

ROTULOS = {"nf": "Nota fiscal", "comprovante": "Comprovante", "indefinido": "Revisar", "duplicado": "Duplicado"}


def renderizar(**contexto):
    return render_template_string(PAGINA, aceitos=",".join(sorted(EXTENSOES)), rotulos=ROTULOS,
                                  formatar_valor=formatar_valor, **contexto)


@app.get("/")
def inicio():
    return renderizar()


@app.post("/organizar")
def organizar():
    lote = uuid.uuid4().hex
    entrada = PASTA_TRABALHO / lote / "entrada"
    saida = PASTA_TRABALHO / lote / "organizado"
    entrada.mkdir(parents=True)

    arquivos = []
    for f in request.files.getlist("arquivos"):
        nome = secure_filename(f.filename or "")
        if not nome or Path(nome).suffix.lower() not in EXTENSOES:
            continue
        destino = entrada / nome
        n = 2
        while destino.exists():
            destino = entrada / f"{Path(nome).stem}_{n}{Path(nome).suffix}"
            n += 1
        f.save(destino)
        arquivos.append(destino)

    if not arquivos:
        shutil.rmtree(PASTA_TRABALHO / lote)
        return renderizar(erro="Nenhum arquivo aceito. Formatos válidos: " + ", ".join(sorted(EXTENSOES)))

    docs = planejar(sorted(arquivos), saida)
    executar(docs, saida)
    shutil.make_archive(str(PASTA_TRABALHO / lote / "organizado"), "zip", saida)
    return renderizar(docs=docs, resumo=resumir(docs), saida=saida, lote=lote)


@app.get("/baixar/<lote>")
def baixar(lote: str):
    if not (len(lote) == 32 and all(c in "0123456789abcdef" for c in lote)):
        abort(404)
    zip_ = PASTA_TRABALHO / lote / "organizado.zip"
    if not zip_.exists():
        abort(404)
    return send_file(zip_, as_attachment=True, download_name="documentos_organizados.zip")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Interface web do organizador de documentos.")
    ap.add_argument("--porta", type=int, default=5000)
    args = ap.parse_args()
    app.run(host="127.0.0.1", port=args.porta)
