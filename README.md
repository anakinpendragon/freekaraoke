# FreeKaraoke

Software de karaoke open source.

## Download

Baixe a versão mais recente em [Releases](https://github.com/anakinpendragon/freekaraoke/releases):

- **Linux:** `freekaraoke-x86_64.AppImage`. Dê permissão de execução e abra.
- **Windows 10 ou 11 (64 bits):** `FreeKaraoke-windows-x64.zip`. Extraia a pasta inteira e abra `FreeKaraoke.exe`. O programa não tem assinatura digital, então o Windows pode avisar: clique em **Mais informações → Executar assim mesmo**.
- **Conversor (Linux):** `conversor-x86_64.AppImage`.

Player de karaokê para Linux que toca arquivos `.st3`, `.kar`, `.mid` e MusicXML, com letra colorida sílaba por sílaba, nota por ritmo, fila de músicas e fundo de imagens ou vídeos.

## Sistemas compatíveis

O AppImage do player precisa de um Linux x86-64 com **glibc 2.35 ou mais nova**:

| Distribuição | Versão mínima | Situação |
|---|---|---|
| Ubuntu | 22.04 | Testado no 22.04 e no 24.04 |
| Debian | 12 | Testado no 12 |
| Fedora | 41 | Testado no 41 |
| Arch Linux | Atual | Testado |

Distribuições mais antigas não são suportadas; o Debian 11, por exemplo, não funciona. Os testes foram feitos em contêineres, sem tela e sem som reais.

## Formatos

| Formato | Letra colorida | Observações |
|---|---|---|
| `.st3` | Sim | Formato do RealOrch; traz a duração de cada sílaba |
| `.kar` | Sim | MIDI com letra |
| `.musicxml`, `.mxl`, `.xml` | Se a partitura tiver letra | Toca direto, sem converter |
| `.mid` | Se tiver eventos de letra | |

Uma partitura MusicXML funciona como um `.st3` quando tem letra. Ela toca os instrumentos escritos nela, e só eles: se a partitura for só voz e piano, é o que se ouve. Sem letra, a música toca sem a linha de letra e sem nota. A primeira abertura de uma partitura pode levar alguns segundos, com a janela parada; o resultado fica em `~/.cache/freekaraoke/`, e as seguintes são imediatas. Arquivos `.xml` só entram na lista se forem mesmo partituras.

## Instalação para desenvolvimento

Requer Python 3.13 e a biblioteca FluidSynth instalada no sistema.

```bash
virtualenv .venv
.venv/bin/pip install -r requirements.txt
```

## Player

```bash
.venv/bin/python player.py
```

Em **Opções → Pasta das músicas**, escolha a pasta da coleção. As músicas dela e das subpastas recebem um número fixo: novas músicas ganham o próximo número livre, um arquivo renomeado ou movido dentro da pasta mantém o seu (reconhecido por ter o mesmo tamanho e data), e o número de uma música removida nunca vai para outra. A lista continua em ordem alfabética.

Em **Músicas** (ou `F2`), abra a janela para buscar músicas e montar a fila. Dá para digitar o número e teclar Enter, selecionar a música e clicar em **Adicionar à fila**, ou arrastá-la com o mouse até a posição desejada na fila. Arrastar dentro da fila muda a ordem.

Também dá para digitar o número direto na tela principal, inclusive em tela cheia, sem abrir a janela de músicas: os dígitos aparecem na tela, `Enter` coloca a música na fila, `Backspace` corrige e `Esc` cancela.

Colocar na fila não inicia a música: use **Tocar** ou `Espaço` na janela principal, ou **Tocar a fila** na janela de músicas. Ao terminar uma música, a próxima da fila começa sozinha.

A lista pode ser filtrada por cantor e por nome. O cantor vem dos dados de cada arquivo: créditos como `Karaoke by...` são ignorados, e grafias como `The Beatles` e `Beatles` são agrupadas. Músicas sem essa informação ficam em **Others**.

Uma música que está tocando ou já está na fila não entra de novo. Ao terminar, a música para, a menos que haja outra na fila.

A lista em PDF, com número, música e cantor, é gerada sozinha no diretório de configuração e só é refeita quando os arquivos da pasta das músicas mudam. Abra pelo menu **Arquivo → Abrir lista de músicas em PDF**. A pasta é verificada ao abrir o player e ao usar `F5` (**Opções → Atualizar lista de músicas**).

### Atalhos

| Tecla | Ação |
|---|---|
| Dígitos e `Enter` | Coloca na fila a música com esse número |
| `F2` | Abre a janela de músicas e fila |
| `F5` | Verifica se a pasta das músicas mudou |
| `F11` | Entra e sai da tela cheia |
| `Esc` | Cancela o número digitado, sai da tela cheia ou fecha a janela de músicas |
| `Espaço` | Toca ou pausa |
| `←` e `→` | Volta ou avança 5 segundos |
| `↑` e `↓` | Aumenta ou diminui o volume |
| `-` e `+` | Diminui ou aumenta o tempo da música |
| `Ctrl -` e `Ctrl +` | Diminui ou aumenta a fonte da letra |
| `Ctrl+O` | Abre um arquivo avulso |
| `Delete` | Remove da fila a música selecionada (na janela de músicas) |

### Nota

Ao terminar a música, a nota de 0 a 100 fica 8 segundos na tela, e depois começa a próxima da fila. `Espaço` ou **Tocar** encerra a espera antes. A nota mede só o ritmo: sobe quando a voz soa nos trechos de cantar e cai com sílabas sem voz ou voz fora de hora. A afinação não conta.

O player usa o microfone padrão do sistema e só mede o volume; nada é gravado. Música parada ou com a maior parte pulada não recebe nota, e **Opções → Dar nota pelo microfone** desliga o recurso. A música que as caixas devolvem ao microfone é descontada: o player calcula o volume de cada trecho da música e só conta como voz o que passar disso. Para a nota sair justa, a voz precisa soar mais alta que as caixas no microfone.

### Volume e tema

O controle de volume vai de 0 a 100%. Em volumes altos, algumas músicas podem distorcer nos trechos mais fortes; se isso acontecer, baixe o volume do player e aumente o do sistema. O botão **Modo claro** / **Modo escuro** alterna o tema, com as cores do Bootstrap 5. A área da letra continua escura nos dois temas.

### Tamanho da letra dos menus

Menus, botões e listas seguem o tamanho de letra do sistema. Para escolher outro, use **Exibir → Tamanho da letra dos menus**; **Usar o tamanho de letra do sistema** volta ao padrão. A letra da música é separada: use **Letra → Fonte e tamanho** ou `Ctrl +` e `Ctrl -`.

### Fundo

Em **Opções → Pasta de imagens e vídeos**, escolha a pasta do fundo. As imagens trocam a cada 15 segundos, e os vídeos tocam até o fim, sem som.

## Configurações

No Linux, tudo fica em `~/.config/freekaraoke/` (ou em `$XDG_CONFIG_HOME/freekaraoke/`, se a variável estiver definida). No Windows, em `%APPDATA%\\freekaraoke\\`:

| Arquivo | Conteúdo |
|---|---|
| `freekaraoke.ini` | Configurações |
| `freekaraoke.sqlite` | Número de cada música; apagá-lo faz a numeração recomeçar |
| `lista-de-musicas.pdf` | Lista de músicas, refeita quando os arquivos da pasta mudam |

Chaves do `freekaraoke.ini`:

| Chave | Conteúdo |
|---|---|
| `song_folder` | Pasta das músicas |
| `background_folder` | Pasta de imagens e vídeos |
| `lyric_font` | Fonte e tamanho da letra |
| `volume` | Volume, de 0 a 100 |
| `interface_font_size` | Tamanho da letra dos menus; sem essa chave, segue o sistema |
| `theme` | Tema: `dark` ou `light` |
| `score_enabled` | Se a nota pelo microfone está ligada |
| `soundfont` | SoundFont escolhido, quando o padrão não é encontrado |
| `last_dir` | Última pasta usada para abrir um arquivo avulso |

Para voltar às configurações padrão, feche o player e apague o `freekaraoke.ini`. Os números ficam guardados no `freekaraoke.sqlite`; se você imprimiu a lista, não apague esse arquivo.

## SoundFont e fonte

O player procura primeiro `soundfonts/FluidR3_GM.sf2`, na pasta do projeto, e depois os caminhos comuns do sistema. Esse arquivo não fica no Git por causa do tamanho; ele vem do pacote `fluid-soundfont-gm`. Para usar outro SoundFont:

```bash
KARAOKE_SOUNDFONT=/caminho/do/arquivo.sf2 .venv/bin/python player.py
```

A fonte padrão da letra é a Liberation Sans, com as mesmas medidas da Arial, na pasta `fonts/`.

O ícone `icons/freekaraoke.png` foi desenhado a partir do símbolo ♫ (U+266B) por `packaging/make_icon.py`.

## Conversor

O player abre MusicXML direto. O conversor serve para usar a música em outro programa, como o RealOrch.

```bash
.venv/bin/python conversor.py partitura.mxl                 # cria .st3
.venv/bin/python conversor.py partitura.mxl --formato kar   # cria .kar
```

A partitura precisa ter letra. O conversor não substitui um arquivo existente, a menos que você use `--sobrescrever`.

Para gerar `dist/conversor-x86_64.AppImage` (baixa o Python empacotado e o `appimagetool` na primeira vez):

```bash
bash packaging/build_conversor.sh
```

Há também uma página web para a conversão, em `http://127.0.0.1:5000`:

```bash
.venv/bin/python app.py
```

## AppImage do player

```bash
bash packaging/build_player.sh
```

Gera `dist/freekaraoke-x86_64.AppImage` com Python, Qt, FluidSynth, SoundFont e fontes. A construção precisa de internet e de Docker: a FluidSynth vem do Ubuntu 22.04 para o pacote funcionar em sistemas com glibc 2.35 ou mais nova. Não funciona no Debian 11 nem em sistemas mais antigos que ele.

O AppImage roda com o FUSE instalado. Sem o FUSE, use `APPIMAGE_EXTRACT_AND_RUN=1 ./freekaraoke-x86_64.AppImage`.

`packaging/smoke_test.py` testa o conteúdo do pacote.

## Versões automáticas

O GitHub Actions (`.github/workflows/build.yml`) roda os testes e gera os pacotes a cada envio para a `main`. Eles ficam nos artefatos de cada execução. Os pacotes passam pelo autoteste do programa: o AppImage em cinco distribuições limpas, e o pacote do Windows numa máquina Windows.

Para publicar uma versão, crie uma etiqueta; o Actions cria a Release com os arquivos:

```bash
git tag v0.1.0
git push origin v0.1.0
```

O SoundFont não fica no repositório por causa do tamanho: o Actions o obtém do pacote `fluid-soundfont-gm`. Para testar um pacote já pronto, rode o programa com `--self-test resultado.txt tests/data/video-teste.mp4`. No Windows, se o programa fechar logo ao abrir, o motivo fica em `%TEMP%\\freekaraoke-erro.txt`.

## Testes

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests --timeout=120
```

## Licenças

- `soundfonts/FluidR3_GM.sf2`: MIT, veja `soundfonts/FluidR3_GM.copyright.txt`.
- `fonts/`: SIL Open Font License 1.1, veja `fonts/LICENSE.txt`.
