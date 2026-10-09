// Organizador de Documentos Fiscais — interface web.
// Etapas: 1) Envie (arquivos são enviados e analisados no servidor)
//         2) Confira (o usuário corrige o tipo, se preciso)
//         3) Baixe (um PDF por nota, nomeado com número e valor)

const EXTENSOES = ['pdf', 'xml', 'jpg', 'jpeg', 'png', 'tif', 'tiff'];
const ROTULO_TIPO = {
  nota: 'Nota fiscal', comprovante: 'Comprovante', cce: 'Carta de correção', indefinido: 'Outro documento',
  analisando: 'Analisando…', erro: 'Erro na leitura'
};
// Ordem ao clicar na etiqueta de tipo
const PROXIMO_TIPO = { nota: 'comprovante', comprovante: 'cce', cce: 'nota', indefinido: 'nota' };
const PASSOS = [
  ['Envie', 'Notas fiscais e comprovantes, em qualquer ordem.'],
  ['Confira', 'Cada arquivo é classificado. Corrija se necessário.'],
  ['Baixe', 'Um PDF por nota, nomeado com número e valor.']
];

const estado = {
  lote: null,
  arquivos: [],      // {id, nome, tamanho, tipo, servidorId, erro, pastaId}
  pastas: [],        // pastas locais ou links: {id, caminho, tipo, quantidade, lendo}
  status: 'idle',    // idle | processando | concluido
  resultados: [],
  zip: null,
  erro: '',
  arrastando: false,
};
let uid = 0;
let filaInicio = Promise.resolve();  // inicia uma tarefa de cada vez, para todas usarem o mesmo "lote"
let geracao = 0;                      // muda em "Começar de novo": respostas antigas são ignoradas

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const plural = (n, s, p) => `${n} ${n === 1 ? s : (p || s + 's')}`;
const extensao = nome => (nome.split('.').pop() || '').toLowerCase();
const fmtTamanho = b => b > 1048576 ? (b / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';

const SERVIDOR_FORA = 'O organizador não está respondendo. Verifique se ele está rodando (python app.py) e recarregue a página (F5).';

async function api(url, opcoes = {}) {
  let resp;
  try {
    resp = await fetch(url, opcoes);
  } catch {
    throw new Error(SERVIDOR_FORA);
  }
  const dados = await resp.json().catch(() => ({}));
  if (resp.ok) return dados;
  if (dados.login) location.href = '/login';  // sessão de login expirou
  if (dados.erro) throw new Error(dados.erro);
  // Sem resposta do organizador: quem respondeu foi o encaminhamento de porta do Codespace
  if (resp.status === 401 || resp.status === 403) {
    throw new Error('A sessão do Codespace expirou ou o organizador foi reiniciado. Recarregue a página (F5) e tente de novo.');
  }
  if (resp.status >= 502) throw new Error(SERVIDOR_FORA);
  throw new Error(`Erro ${resp.status} no servidor.`);
}

// ---------------------------------------------------------------- tarefas

// Inicia a análise no servidor (em fila, para reaproveitar o lote) e acompanha o andamento.
// A análise roda em segundo plano no servidor; aqui só consultamos o progresso, então
// nenhuma requisição fica aberta por minutos (o que derrubava a conexão em pastas grandes).
async function executarTarefa(url, opcoes, aoProgredir) {
  const minhaGeracao = geracao;
  const inicio = filaInicio.then(() => {
    if (estado.lote && opcoes.body instanceof FormData) opcoes.body.set('lote', estado.lote);
    if (typeof opcoes.body === 'string') opcoes.body = JSON.stringify({ ...JSON.parse(opcoes.body), lote: estado.lote });
    return api(url, opcoes);
  });
  filaInicio = inicio.catch(() => {});
  const { lote, tarefa } = await inicio;
  if (minhaGeracao !== geracao) throw new Cancelado();
  estado.lote = lote;

  let falhas = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 1000));
    if (minhaGeracao !== geracao) throw new Cancelado();
    let t;
    try {
      t = await api(`/api/tarefas/${tarefa}`);
      falhas = 0;
    } catch (e) {
      if (++falhas >= 15 || /não encontrada/.test(e.message)) throw e;  // tolera quedas rápidas de rede
      continue;
    }
    if (t.estado === 'erro') throw new Error(t.erro);
    if (t.estado === 'concluido') return t;
    aoProgredir(t);
  }
}

class Cancelado extends Error {}

// ---------------------------------------------------------------- envio

function adicionar(lista) {
  const aceitos = [], recusados = [];
  for (const f of lista) (EXTENSOES.includes(extensao(f.name)) ? aceitos : recusados).push(f);
  estado.erro = recusados.length ? `Formato não suportado: ${recusados.map(f => f.name).join(', ')}` : '';
  if (!aceitos.length) return render();

  const itens = aceitos.map(f => ({ id: ++uid, nome: f.name, tamanho: f.size, tipo: 'analisando' }));
  estado.arquivos.push(...itens);
  render();

  (async () => {
    const form = new FormData();
    aceitos.forEach(f => form.append('arquivos', f, f.name));
    try {
      const dados = await executarTarefa('/api/arquivos', { method: 'POST', body: form }, t => {
        estado.progressoEnvio = t.total > 1 ? ` (${t.feitos} de ${t.total})` : '';
        render();
      });
      dados.arquivos.forEach((r, i) => {
        const item = itens[i];
        if (r.erro) Object.assign(item, { tipo: 'erro', erro: r.erro });
        else Object.assign(item, { tipo: r.tipo, servidorId: r.id, numero: r.numero, valor: r.valor });
      });
    } catch (e) {
      if (e instanceof Cancelado) return;
      itens.forEach(item => Object.assign(item, { tipo: 'erro', erro: e.message }));
    }
    estado.progressoEnvio = '';
    render();
  })();
}

async function carregarExemplo() {
  try {
    const lista = await api('/api/exemplo');
    if (!lista.length) throw new Error('Nenhum arquivo de exemplo disponível.');
    const arquivos = await Promise.all(lista.map(async a => {
      const blob = await (await fetch(a.url)).blob();
      return new File([blob], a.nome, { type: blob.type });
    }));
    adicionar(arquivos);
  } catch (e) {
    estado.erro = e.message;
    render();
  }
}

// ---------------------------------------------------------------- pastas

// Pasta local ou link (Google Drive, OneDrive, SharePoint): o servidor baixa/lê e analisa
// os documentos, que entram na lista de arquivos para conferência.
function adicionarPasta() {
  const caminho = $('pasta').value.trim();
  if (!caminho) return;
  const link = /^https?:\/\//i.test(caminho);
  const pasta = { id: ++uid, caminho, tipo: link ? 'LINK' : 'PASTA', quantidade: 0, lendo: true };
  estado.pastas.push(pasta);
  $('pasta').value = '';
  $('btn-pasta').disabled = true;
  $('pasta-erro').hidden = true;
  render();

  (async () => {
    try {
      const dados = await executarTarefa('/api/fontes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ caminho })
      }, t => {
        pasta.progresso = t.estado === 'baixando' ? 'Baixando arquivos do link…'
          : t.total ? `Analisando ${t.feitos} de ${plural(t.total, 'documento')}…` : 'Lendo documentos…';
        render();
      });
      if (!estado.pastas.includes(pasta)) return;  // removida enquanto lia
      Object.assign(pasta, dados.fonte, { lendo: false });
      estado.arquivos.push(...dados.arquivos.map(r => r.erro
        ? { id: ++uid, nome: r.nome, tamanho: 0, tipo: 'erro', erro: r.erro, pastaId: pasta.id }
        : { id: ++uid, nome: r.nome, tamanho: r.tamanho, tipo: r.tipo, servidorId: r.id, numero: r.numero, valor: r.valor, pastaId: pasta.id }));
    } catch (e) {
      if (e instanceof Cancelado) return;
      estado.pastas = estado.pastas.filter(p => p !== pasta);
      $('pasta-erro').textContent = `${caminho}: ${e.message}`;
      $('pasta-erro').hidden = false;
    }
    render();
  })();
}

// ---------------------------------------------------------------- organizar

async function organizar() {
  estado.status = 'processando';
  estado.erro = '';
  render();
  try {
    const dados = await api('/api/organizar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lote: estado.lote,
        arquivos: estado.arquivos.filter(a => a.servidorId).map(a => ({ id: a.servidorId, tipo: a.tipo })),
      })
    });
    Object.assign(estado, { lote: dados.lote, resultados: dados.resultados, zip: dados.zip, status: 'concluido' });
  } catch (e) {
    Object.assign(estado, { status: 'idle', erro: e.message });
  }
  render();
}

function reiniciar() {
  geracao++;
  Object.assign(estado, { lote: null, arquivos: [], pastas: [], status: 'idle', resultados: [], zip: null, erro: '' });
  $('pasta').value = '';
  $('pasta-erro').hidden = true;
  $('btn-pasta').disabled = true;
  render();
}

// ---------------------------------------------------------------- render

function render() {
  const { arquivos, pastas, status, resultados } = estado;
  const prontos = arquivos.filter(a => a.servidorId);
  const notas = prontos.filter(a => a.tipo === 'nota').length;
  const comps = prontos.filter(a => a.tipo === 'comprovante').length;
  const indefinidos = prontos.filter(a => a.tipo === 'indefinido').length;
  const cces = prontos.filter(a => a.tipo === 'cce').length;
  const analisando = arquivos.filter(a => a.tipo === 'analisando').length;
  const lendo = pastas.filter(p => p.lendo).length;
  const processando = status === 'processando';
  const concluido = status === 'concluido';
  const etapa = concluido ? 3 : arquivos.length || pastas.length ? 2 : 1;

  // Etapas
  $('passos').innerHTML = PASSOS.map(([titulo, desc], i) => {
    const n = i + 1;
    const classe = n === etapa ? 'passo--ativo' : n < etapa ? 'passo--feito' : 'passo--futuro';
    return `<div class="passo ${classe}"><span class="passo__num">${n}</span>
      <div class="passo__texto"><strong>${titulo}</strong><span>${desc}</span></div></div>`;
  }).join('');

  $('resultado').hidden = !concluido;
  $('entrada').hidden = concluido;

  if (concluido) {
    $('titulo-resultado').textContent = `${plural(resultados.length, 'arquivo')} gerado${resultados.length === 1 ? '' : 's'}`;
    $('btn-zip').href = estado.zip;
    $('lista-resultados').innerHTML = resultados.map(r => `
      <div class="linha colunas-resultado">
        <div class="celula-nome"><strong title="${esc(r.pasta + '/' + r.arquivo)}">${esc(r.arquivo)}</strong><span>${esc(r.resumo)}</span></div>
        <a class="btn btn--secundario" href="${esc(r.url)}" download>Baixar ${esc(r.extensao)}</a>
      </div>`).join('');
    return;
  }

  // Área de envio
  $('zona').classList.toggle('zona--compacta', arquivos.length > 0);
  $('zona').classList.toggle('zona--arrastando', estado.arrastando);
  $('zona-titulo').textContent = estado.arrastando ? 'Solte para adicionar' : 'Arraste os arquivos aqui';

  // Pastas
  $('tabela-pastas').hidden = !pastas.length;
  $('lista-pastas').innerHTML = pastas.map(p => `
    <div class="linha colunas-pasta">
      <span class="formato">${esc(p.tipo)}</span>
      <div class="celula-nome"><span class="nome" title="${esc(p.caminho)}">${esc(p.caminho)}</span><span>${p.lendo ? esc(p.progresso || (p.tipo === 'PASTA' ? 'Lendo documentos…' : 'Conectando ao link…')) : plural(p.quantidade, 'documento') + ' · listados abaixo'}</span></div>
      <button type="button" class="btn-icone" data-remover-pasta="${p.id}" aria-label="Remover pasta">${ICONE_X}</button>
    </div>`).join('');

  // Arquivos
  $('bloco-arquivos').hidden = !arquivos.length;
  $('titulo-arquivos').textContent = `Arquivo (${arquivos.length})`;
  $('lista-arquivos').innerHTML = arquivos.map(a => {
    const detalhe = a.erro ? a.erro
      : a.tipo === 'nota' && (a.numero || a.valor) ? `${fmtTamanho(a.tamanho)} · NF ${a.numero || '?'}${a.valor ? ' · R$ ' + a.valor : ''}`
      : a.tipo === 'comprovante' && a.valor ? `${fmtTamanho(a.tamanho)} · R$ ${a.valor}`
      : a.tipo === 'cce' && a.numero ? `${fmtTamanho(a.tamanho)} · NF ${a.numero}`
      : fmtTamanho(a.tamanho);
    const alteravel = !!a.servidorId;
    return `
    <div class="linha colunas-arquivo">
      <span class="formato">${esc(extensao(a.nome).toUpperCase())}</span>
      <div class="celula-nome"><span class="nome" title="${esc(a.nome)}">${esc(a.nome)}</span><span${a.erro ? ' class="erro"' : ''}>${esc(detalhe)}</span></div>
      <button type="button" class="tipo tipo--${a.tipo}" ${alteravel ? `data-alternar="${a.id}" title="Clique para alterar o tipo"` : 'disabled'}>${ROTULO_TIPO[a.tipo]}</button>
      <button type="button" class="btn-icone" data-remover="${a.id}" aria-label="Remover">${ICONE_X}</button>
    </div>`;
  }).join('');

  // Barra inferior
  const origem = [
    prontos.length ? `${plural(notas, 'nota')} · ${plural(comps, 'comprovante')}${cces ? ' · ' + plural(cces, 'carta de correção', 'cartas de correção') : ''}${indefinidos ? ' · ' + plural(indefinidos, 'outro documento', 'outros documentos') : ''}` : '',
    pastas.length ? plural(pastas.length, 'pasta/link', 'pastas/links') : ''
  ].filter(Boolean).join(' · ');
  const status_ = $('status');
  status_.classList.toggle('erro', !!estado.erro);
  status_.textContent = estado.erro ? estado.erro
    : processando ? 'Aglutinando documentos…'
    : lendo ? `Lendo ${plural(lendo, 'pasta/link', 'pastas/links')}…`
    : analisando ? `Analisando ${plural(analisando, 'arquivo')}${estado.progressoEnvio || ''}…`
    : !arquivos.length && !pastas.length ? 'Nenhum arquivo ou pasta informado.'
    : !notas ? 'Adicione ao menos uma nota fiscal.'
    : origem;

  $('btn-exemplo').hidden = arquivos.length > 0;
  $('btn-organizar').disabled = !notas || processando || analisando > 0 || lendo > 0;
  $('rotulo-organizar').textContent = processando ? 'Organizando…' : 'Organizar documentos';
}

const ICONE_X = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';

// ---------------------------------------------------------------- eventos

const zona = $('zona');
zona.addEventListener('click', () => $('seletor').click());
zona.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('seletor').click(); } });
zona.addEventListener('dragover', e => { e.preventDefault(); if (!estado.arrastando) { estado.arrastando = true; render(); } });
zona.addEventListener('dragleave', e => { if (zona.contains(e.relatedTarget)) return; estado.arrastando = false; render(); });
zona.addEventListener('drop', e => { e.preventDefault(); estado.arrastando = false; adicionar([...e.dataTransfer.files]); });
$('seletor').addEventListener('change', e => { adicionar([...e.target.files]); e.target.value = ''; });

$('pasta').addEventListener('input', e => { $('btn-pasta').disabled = !e.target.value.trim(); $('pasta-erro').hidden = true; });
$('pasta').addEventListener('keydown', e => { if (e.key === 'Enter') adicionarPasta(); });
$('btn-pasta').addEventListener('click', adicionarPasta);

$('lista-arquivos').addEventListener('click', e => {
  const alternar = e.target.closest('[data-alternar]');
  const remover = e.target.closest('[data-remover]');
  if (alternar) {
    const a = estado.arquivos.find(x => x.id === +alternar.dataset.alternar);
    a.tipo = PROXIMO_TIPO[a.tipo] || 'nota';
  } else if (remover) {
    estado.arquivos = estado.arquivos.filter(x => x.id !== +remover.dataset.remover);
  } else return;
  estado.erro = '';
  render();
});
$('lista-pastas').addEventListener('click', e => {
  const remover = e.target.closest('[data-remover-pasta]');
  if (!remover) return;
  const id = +remover.dataset.removerPasta;
  estado.pastas = estado.pastas.filter(p => p.id !== id);
  estado.arquivos = estado.arquivos.filter(a => a.pastaId !== id);  // tira junto os arquivos dela
  render();
});

$('btn-exemplo').addEventListener('click', carregarExemplo);

// Usuário logado e contas conectadas (para ler links privados do Drive/OneDrive)
(async () => {
  const s = await fetch('/api/sessao').then(r => r.json()).catch(() => null);
  if (!s || !s.login_ativo || !s.usuario) return;
  const contas = [['google', 'Google Drive'], ['microsoft', 'OneDrive/SharePoint']]
    .filter(([p]) => s.provedores[p])
    .map(([p, nome]) => s.conectado[p]
      ? `<span class="conta conta--ligada" title="Links privados desta conta podem ser lidos">✓ ${nome}</span>`
      : `<a class="conta" href="/auth/${p}" title="Conectar para ler links privados">Conectar ${nome}</a>`).join('');
  const semLogin = s.usuario.provedor === 'nenhum';
  $('usuario').innerHTML = `<span class="usuario__contas">${contas}</span>
    <span class="usuario__nome" title="${esc(s.usuario.email)}">${esc(s.usuario.nome)}</span>
    <a href="/sair">${semLogin ? 'Entrar com uma conta' : 'Sair'}</a>`;
  $('usuario').hidden = false;
})();
$('btn-organizar').addEventListener('click', organizar);
$('btn-reiniciar').addEventListener('click', reiniciar);

render();
