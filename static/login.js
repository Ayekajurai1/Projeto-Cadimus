// Tela de login: mostra erros e o botão "Entrar sem login" (se permitido no servidor).
(async () => {
  const erro = new URLSearchParams(location.search).get('erro');
  if (erro) {
    document.getElementById('erro').textContent = erro;
    document.getElementById('erro').hidden = false;
  }
  const sessao = await fetch('/api/sessao').then(r => r.json()).catch(() => null);
  if (!sessao) return;
  if (sessao.usuario) return location.replace('/');
  document.getElementById('btn-sem-login').hidden = !sessao.sem_login;
})();
