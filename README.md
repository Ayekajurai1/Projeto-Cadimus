# Projeto-Cadimus

Organizador de documentos fiscais: varre uma pasta, identifica **notas fiscais** e **comprovantes de pagamento**, renomeia cada arquivo com número e valor e separa tudo em subpastas.

Aceita PDF (com texto ou escaneado), imagens (JPG, PNG, TIFF) e XML de NF-e.

## Instalação

1. Instale o [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) com o pacote de português (necessário para PDFs escaneados e imagens):

   ```bash
   sudo apt install tesseract-ocr tesseract-ocr-por
   ```

2. Instale as dependências Python:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

## Uso

```bash
python organizador_documentos.py PASTA_ENTRADA                     # simulação (não altera nada)
python organizador_documentos.py PASTA_ENTRADA --aplicar           # copia e renomeia de verdade
python organizador_documentos.py PASTA_ENTRADA --aplicar --mover   # move em vez de copiar
```

Opções:

| Opção         | Descrição                                               |
|---------------|---------------------------------------------------------|
| `--aplicar`   | executa de verdade (sem isso, só mostra o que faria)    |
| `--mover`     | move os arquivos em vez de copiar                       |
| `--recursivo` | inclui subpastas                                        |
| `--saida DIR` | pasta de destino (padrão: `PASTA_ENTRADA/_organizado`)  |

## Interface web

```bash
python app.py              # abre em http://localhost:5000
python app.py --porta 8080
```

A página segue o visual FPFtech e funciona em três etapas:

1. **Envie** — arraste os arquivos ou informe uma pasta do computador onde o organizador está rodando. Cada arquivo é analisado assim que chega.
2. **Confira** — cada arquivo aparece como *Nota fiscal*, *Comprovante*, *Carta de correção* ou *Outro documento*; clique no tipo para corrigir.
3. **Baixe** — um PDF por nota (com os comprovantes aglutinados), individualmente ou tudo em `.zip`.

O botão **Carregar exemplo** usa os arquivos fictícios da pasta `exemplo/`.

### Links do Google Drive, OneDrive e SharePoint

No campo **Ou informe uma pasta**, cole o link compartilhado de um arquivo ou de uma pasta. Os documentos (PDF, imagem e XML) são baixados, analisados e aparecem na lista para conferência. São lidas até 3 subpastas, com limite de 300 arquivos e 500 MB por link.

| Serviço | Sem login | Com login (conta conectada) |
|---|---|---|
| Google Drive (arquivo, pasta; Docs/Planilhas viram PDF) | link “Qualquer pessoa com o link” | tudo que a conta Google pode ver |
| OneDrive pessoal (Outlook/Hotmail) | link “Qualquer pessoa com o link” | tudo que a conta pode ver |
| SharePoint / OneDrive da empresa | link “Qualquer pessoa com o link” | links restritos à organização |

### Login com Google e Microsoft (SSO)

Com o login configurado, a página pede **Entrar com Google** ou **Entrar com Microsoft / Outlook** antes de liberar o organizador. A conta usada no login (e outras conectadas pelo topo da página, em “Conectar Google Drive” / “Conectar OneDrive/SharePoint”) dá acesso **somente de leitura** aos arquivos dela, para baixar links privados. A tela de login aparece sempre e tem também **Entrar sem login**, que libera o organizador normalmente (só os links privados do Drive/OneDrive precisam de uma conta conectada). Para obrigar o login com Google/Microsoft, defina `ORGANIZADOR_EXIGIR_LOGIN=1`.

1. Copie `.env.exemplo` para `.env` (o `.env` não vai para o Git) e preencha as credenciais abaixo.
2. Reinicie o `app.py`.

O endereço de retorno (*redirect URI*) é `<endereço do organizador>/auth/google/retorno` e `/auth/microsoft/retorno`. No Codespaces, por exemplo: `https://<nome-do-codespace>-5000.app.github.dev/auth/google/retorno`.

**Google** — [Google Cloud Console](https://console.cloud.google.com/):
1. Crie um projeto e ative a **Google Drive API** (APIs e serviços › Biblioteca).
2. Em **Tela de consentimento OAuth**, escolha *Interno* (só contas da organização Google Workspace) ou *Externo* (adicione os e-mails de teste) e inclua o escopo `drive.readonly`.
3. Em **Credenciais › Criar credenciais › ID do cliente OAuth › Aplicativo da Web**, adicione o endereço de retorno acima.
4. Copie o ID e a chave secreta para `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`.

**Microsoft / Outlook** — [Portal do Azure › Microsoft Entra ID › Registros de aplicativo](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade):
1. **Novo registro**. Em *Tipos de conta*, escolha só a organização (e use o ID do diretório em `MS_TENANT_ID`) ou “qualquer organização e contas pessoais” (`MS_TENANT_ID=common`).
2. Em *URI de redirecionamento*, escolha **Web** e informe o endereço de retorno acima.
3. Em **Permissões de API › Microsoft Graph › Permissões delegadas**, adicione `User.Read`, `Files.Read.All` e `offline_access`.
4. Em **Certificados e segredos**, crie um segredo do cliente.
5. Copie o *ID do aplicativo (cliente)* e o segredo para `MS_CLIENT_ID` e `MS_CLIENT_SECRET`.

Para restringir o acesso a e-mails da empresa, use `ORGANIZADOR_DOMINIOS=fpf.br`.

O código da página fica em `static/` (`index.html`, `estilo.css`, `app.js`) e a API em `app.py`. No GitHub Codespaces, abra a porta pela aba **Portas** do VS Code.

## Instalador para Windows

`windows/dist/OrganizadorFiscal-Setup.exe` instala o organizador como um programa comum, com Python, bibliotecas e o OCR (Tesseract com português) embutidos. Não precisa de permissão de administrador.

- Abra **Organizador de Documentos Fiscais** pelo Menu Iniciar ou pela Área de Trabalho: o navegador mostra a página (`http://127.0.0.1:8765`) e o organizador fica rodando em segundo plano, com um ícone na bandeja do sistema (perto do relógio). Clique no ícone para abrir a página de novo; para encerrar, clique com o botão direito e escolha **Sair**.
- Os documentos enviados, a configuração (`.env` do login) e o log (`organizador.log`) ficam em `%LOCALAPPDATA%\OrganizadorFiscal`.
- Como o instalador não é assinado digitalmente, o Windows pode mostrar “O Windows protegeu o computador”: clique em **Mais informações › Executar assim mesmo**.

Para gerar o instalador de novo (no Linux/Codespaces, via Wine): `bash windows/construir.sh`.

## Resultado

Dentro da pasta de saída:

```
Notas_Fiscais/   NF_<numero>_R$<valor>.pdf   (NF + comprovantes vinculados, aglutinados num só PDF)
Comprovantes/    COMP_<data>_R$<valor>.pdf   (comprovantes sem NF correspondente)
Revisar/         documentos que não puderam ser identificados com segurança
Duplicados/      arquivos com conteúdo idêntico a outro
relatorio.csv    registro de tudo o que foi feito
```

Comprovantes são vinculados a uma NF pelo número citado no documento ou, se houver uma única nota com o mesmo valor, pelo valor. Cada NF vinculada é aglutinada com seus comprovantes (a nota primeiro) num único PDF; imagens são convertidas para PDF. NF-e em XML fica ao lado do PDF, com o mesmo nome.

## Ajustes

As palavras-chave e pesos usados na classificação ficam no início de `organizador_documentos.py` (`PISTAS_NF`, `PISTAS_COMPROVANTE`, `ROTULOS_VALOR_*`) e podem ser ajustados conforme os seus documentos.
