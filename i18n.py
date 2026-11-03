"""Portuguese when the system is in Portuguese, English otherwise.

The Portuguese text itself is the key, so code reads naturally and a missing
translation shows Portuguese rather than nothing; a test keeps this table complete.
"""

from __future__ import annotations

import os
import sys

PORTUGUESE = "pt"
ENGLISH = "en"
WINDOWS_PORTUGUESE_LANGUAGE = 0x16

TRANSLATIONS = {
    "Músicas de karaokê (*.st3 *.ST3 *.St3 *.kar *.KAR *.mid *.MID *.midi *.musicxml *.mxl *.xml);;Todos os arquivos (*)": "Karaoke songs (*.st3 *.ST3 *.St3 *.kar *.KAR *.mid *.MID *.midi *.musicxml *.mxl *.xml);;All files (*)",
    "Todos os cantores": "All singers",
    "Sua nota": "Your score",
    "Videokê": "FreeKaraoke",
    "Digite o número da música e tecle Enter, ou clique em Músicas": "Type a song number and press Enter, or click Songs",
    "Parar": "Stop",
    "Próxima": "Next",
    "Tempo −": "Tempo −",
    "Tempo +": "Tempo +",
    "Músicas": "Songs",
    "Volume (↑ e ↓)": "Volume (↑ and ↓)",
    "Número da música": "Song number",
    "Adicionar": "Add",
    "Buscar pelo nome": "Search by name",
    "Adicionar à fila": "Add to queue",
    "Tocar a fila": "Play queue",
    "Remover da fila": "Remove from queue",
    "Abrir lista em PDF": "Open PDF list",
    "Músicas: escolha a pasta em Opções": "Songs: choose the folder in Options",
    "Fila (arraste para mudar a ordem)": "Queue (drag to reorder)",
    "Músicas e fila": "Songs and queue",
    "&Arquivo": "&File",
    "&Exibir": "&View",
    "&Letra": "&Lyrics",
    "&Opções": "&Options",
    "Abrir música...": "Open song...",
    "Abrir lista de músicas em PDF": "Open song list PDF",
    "Salvar uma cópia da lista em PDF...": "Save a copy of the PDF list...",
    "Sair": "Quit",
    "Gravar a sua voz com a música (R)": "Record your voice with the music (R)",
    "Parar a gravação (R)": "Stop recording (R)",
    "Salvar a gravação": "Save the recording",
    "Gravação Opus (*.opus)": "Opus recording (*.opus)",
    "Descartar a gravação?": "Discard the recording?",
    "Se não salvar, a gravação será perdida. Descartar mesmo assim?": "If you do not save it, the recording will be lost. Discard it anyway?",
    "A gravação começa quando a música tocar.": "Recording starts when the song plays.",
    "Salvando a gravação...": "Saving the recording...",
    "Não consegui salvar a gravação: {0}": "Could not save the recording: {0}",
    "Gravação salva: {0}": "Recording saved: {0}",
    "Tocar a voz-guia": "Play the guide voice",
    "Abrir pasta das gravações": "Open the recordings folder",
    "Esta música não tem uma trilha de voz identificável.": "This song has no identifiable voice track.",
    "Músicas e fila (F2)": "Songs and queue (F2)",
    "Tela cheia (F11; Esc para sair)": "Full screen (F11; Esc to leave)",
    "Alternar modo claro e escuro": "Switch between light and dark mode",
    "Tamanho da letra dos menus...": "Menu text size...",
    "Usar o tamanho de letra do sistema": "Use the system text size",
    "Fonte e tamanho...": "Font and size...",
    "Aumentar fonte (Ctrl +)": "Larger font (Ctrl +)",
    "Diminuir fonte (Ctrl -)": "Smaller font (Ctrl -)",
    "Restaurar fonte padrão": "Restore default font",
    "Pasta das músicas...": "Song folder...",
    "Atualizar lista de músicas (F5)": "Refresh song list (F5)",
    "Pasta de imagens e vídeos...": "Image and video folder...",
    "Tirar imagens e vídeos": "Remove images and videos",
    "Dar nota pelo microfone": "Score with the microphone",
    "Pasta das músicas": "Song folder",
    "Não consegui ler o banco de músicas: {0}": "Could not read the song database: {0}",
    "Não consegui gerar a lista em PDF: {0}": "Could not create the PDF list: {0}",
    "Lista de músicas em PDF atualizada.": "PDF song list updated.",
    "Músicas: {0}": "Songs: {0}",
    "Músicas: {0} de {1}": "Songs: {0} of {1}",
    "Música nº {0}": "Song no. {0}",
    "Não existe música com o número {0}.": "There is no song number {0}.",
    "Escolha antes a pasta das músicas em Opções.": "First choose the song folder in Options.",
    "Salvar uma cópia da lista": "Save a copy of the list",
    "Não consegui salvar a lista: {0}": "Could not save the list: {0}",
    "Lista salva: {0}": "List saved: {0}",
    "Selecione uma música na lista.": "Select a song in the list.",
    "Já está tocando: {0}": "Already playing: {0}",
    "Já está na fila: {0}": "Already in the queue: {0}",
    "Na fila: {0}": "In the queue: {0}",
    "A fila está vazia.": "The queue is empty.",
    "{0}: {1} Pulando.": "{0}: {1} Skipping.",
    "Abrir música": "Open song",
    "Preparando a partitura, só na primeira vez: {0}": "Preparing the score, only the first time: {0}",
    "{0} — Videokê": "{0} — FreeKaraoke",
    "Volume: {0}%": "Volume: {0}%",
    "Tamanho da letra dos menus": "Menu text size",
    "Tamanho da letra de menus, botões e listas:": "Text size of menus, buttons and lists:",
    "Modo claro": "Light mode",
    "Modo escuro": "Dark mode",
    "Escolha um SoundFont": "Choose a SoundFont",
    "SoundFont (*.sf2 *.sf3);;Todos os arquivos (*)": "SoundFont (*.sf2 *.sf3);;All files (*)",
    "Nenhum microfone disponível: a música tocará sem nota nem gravação.": "No microphone available: the song will play without a score or recording.",
    "Pausar": "Pause",
    "Tocar": "Play",
    "Fonte e tamanho da letra": "Lyric font and size",
    "Tamanho da letra: {0}": "Lyric size: {0}",
    "Pasta de imagens e vídeos": "Image and video folder",
    "Nenhuma imagem ou vídeo nessa pasta.": "No images or videos in that folder.",
    "sem extensão": "no extension",
    "Formato não suportado: {0}": "Unsupported format: {0}",
    "Formato não suportado: {0}.": "Unsupported format: {0}.",
    "Não consegui abrir o arquivo: {0}": "Could not open the file: {0}",
    "Arquivo MIDI inválido: {0}": "Invalid MIDI file: {0}",
    "O arquivo não tem notas para tocar.": "The file has no notes to play.",
    "A biblioteca FluidSynth não foi encontrada.": "The FluidSynth library was not found.",
    "Não consegui iniciar o sintetizador.": "Could not start the synthesizer.",
    "Não consegui carregar o SoundFont: {0}": "Could not load the SoundFont: {0}",
    "Não encontrei nenhuma saída de áudio disponível.": "No audio output is available.",
    "A FluidSynth recusou o arquivo MIDI.": "FluidSynth refused the MIDI file.",
    "Arquivo .st3 truncado ou corrompido: {0}": "Truncated or damaged .st3 file: {0}",
    "Não é um arquivo .st3 reconhecido.": "Not a recognised .st3 file.",
    "MIDI interno inválido: {0}": "Invalid embedded MIDI: {0}",
    "Bloco CEVENT não encontrado.": "CEVENT block not found.",
    "Trilha ultrapassa o bloco CEVENT em {0}.": "Track goes past the CEVENT block at {0}.",
    "Evento ultrapassa o fim da trilha em {0}.": "Event goes past the end of the track at {0}.",
    "Evento de canal inválido {0} em {1}.": "Invalid channel event {0} at {1}.",
    "O arquivo não tem letra. Baixe o MusicXML de uma partitura com letra ou use um MIDI que contenha eventos de letra.": "The file has no lyrics. Download the MusicXML of a score with lyrics, or use a MIDI file with lyric events.",
    "Não consegui ler o MusicXML: {0}": "Could not read the MusicXML: {0}",
    "O MusicXML não contém uma partitura completa.": "The MusicXML does not contain a complete score.",
    "Não consegui preparar a música dessa partitura: {0}": "Could not prepare the music of this score: {0}",
    "Não consegui ler o MIDI: {0}": "Could not read the MIDI: {0}",
    "Lista de músicas": "Song list",
    "Não consegui criar o arquivo: {0}": "Could not create the file: {0}",
    "{0} músicas — {1}": "{0} songs — {1}",
    "Cantor": "Singer",
    "Música": "Song",
    "Nº": "No.",
    "Página {0} de {1}": "Page {0} of {1}",
    "%d/%m/%Y": "%Y-%m-%d",
    "Cria um arquivo de karaokê .st3 ou .kar a partir de uma partitura MusicXML com letra.": "Creates a .st3 or .kar karaoke file from a MusicXML score with lyrics.",
    "entrada": "input",
    "saida": "output",
    "arquivo .musicxml, .mxl ou .xml": ".musicxml, .mxl or .xml file",
    "arquivo de saída (padrão: ao lado da entrada)": "output file (default: next to the input)",
    "formato de saída (padrão: {0}, ou o da extensão da saída)": "output format (default: {0}, or the output file's extension)",
    "título da música (padrão: o da partitura)": "song title (default: the score's)",
    "artista (padrão: o compositor da partitura)": "artist (default: the score's composer)",
    "substitui a saída se ela já existir": "replace the output if it already exists",
    "Arquivo não encontrado: {0}": "File not found: {0}",
    "A entrada precisa ser MusicXML (.musicxml, .mxl ou .xml): {0}": "The input must be MusicXML (.musicxml, .mxl or .xml): {0}",
    "A saída já existe: {0}. Use --sobrescrever para substituir.": "The output already exists: {0}. Use --overwrite to replace it.",
    "Não consegui gravar {0}: {1}": "Could not write {0}: {1}",
    "Criado: {0}": "Created: {0}",
    "Erro: {0}": "Error: {0}",
    "A letra é longa demais para o formato .st3.": "The lyrics are too long for the .st3 format.",
    "Cabeçalho MIDI não encontrado.": "MIDI header not found.",
    "Divisão de tempo MIDI não suportada.": "Unsupported MIDI time division.",
    "O arquivo MIDI não tem trilhas.": "The MIDI file has no tracks.",
    "A URL não parece ser de uma partitura do MuseScore.": "The URL does not look like a MuseScore score.",
    "Envie o arquivo MIDI ou MusicXML da partitura.": "Send the score's MIDI or MusicXML file.",
    "Formato de saída inválido.": "Invalid output format.",
    "Arquivo muito grande (máximo de 20 MB).": "File too large (20 MB at most).",
    "Gerador de karaokê": "Karaoke generator",
    "Baixe a partitura no MuseScore pela sua conta (de preferência em MusicXML, que preserva a letra) e envie o arquivo aqui.": "Download the score from MuseScore with your account (preferably as MusicXML, which keeps the lyrics) and send the file here.",
    "Arquivo da partitura": "Score file",
    ".mxl, .musicxml, .xml, .mid, .midi ou .kar (máximo de 20 MB)": ".mxl, .musicxml, .xml, .mid, .midi or .kar (20 MB at most)",
    "URL no MuseScore (opcional)": "MuseScore URL (optional)",
    "Não é baixada; só dá nome ao arquivo quando a partitura não tem título.": "It is not downloaded; it only names the file when the score has no title.",
    "Título (opcional)": "Title (optional)",
    "Artista (opcional)": "Artist (optional)",
    "Formato de saída": "Output format",
    ".st3 (letra mais fluida)": ".st3 (smoother lyrics)",
    ".kar (mais compatível)": ".kar (more compatible)",
    "Gerar arquivo": "Create file",
}


def system_language() -> str:
    override = os.environ.get("FREEKARAOKE_LANG")
    if override:
        return PORTUGUESE if override.lower().startswith(PORTUGUESE) else ENGLISH
    if sys.platform == "win32":
        import ctypes

        language = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return PORTUGUESE if language & 0x3FF == WINDOWS_PORTUGUESE_LANGUAGE else ENGLISH
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            return PORTUGUESE if value.lower().startswith(PORTUGUESE) else ENGLISH
    return ENGLISH


LANGUAGE = system_language()


def tr(text: str) -> str:
    return text if LANGUAGE == PORTUGUESE else TRANSLATIONS.get(text, text)
