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

## Resultado

Dentro da pasta de saída:

```
Notas_Fiscais/   NF_<numero>_R$<valor>.pdf
Comprovantes/    COMP_<data>_R$<valor>.pdf   (ou COMP_NF<numero>_... quando vinculado a uma NF)
Revisar/         documentos que não puderam ser identificados com segurança
Duplicados/      arquivos com conteúdo idêntico a outro
relatorio.csv    registro de tudo o que foi feito
```

Comprovantes são vinculados a uma NF pelo número citado no documento ou, se houver uma única nota com o mesmo valor, pelo valor.

## Ajustes

As palavras-chave e pesos usados na classificação ficam no início de `organizador_documentos.py` (`PISTAS_NF`, `PISTAS_COMPROVANTE`, `ROTULOS_VALOR_*`) e podem ser ajustados conforme os seus documentos.
