"""
Preset: default (modo comentario_ia)

Troque apenas o bloco CONFIG ativo para mudar de campanha/conta.
Comente o bloco atual e descomente o outro — sem precisar alterar config.py.

Placeholders disponiveis no IA_USER_TEMPLATE:
  {nome}       -> nome do autor (so contexto interno; NUNCA reproduza ou cite o nome no texto publicado)
  {comentario} -> texto do comentario (lido da pagina)
  {post}       -> texto do post na pagina quando IA_USAR_TEXTO_POST_DA_PAGINA=True
                 e a extracao funcionar; senao CONTEXTO_POST. Se ambos existirem,
                 o prompt combina CONTEXTO_POST + "--- Texto do post na pagina ---" + texto extraido.
  {limite}     -> valor de IA_MAX_CARACTERES

Injecao de tom (runtime):
  O bloco de tom e adicionado automaticamente ao IA_SYSTEM_PROMPT em cada chamada.
  Para mudar o tom, edite IA_TOM_RESPOSTA em config.py.
  Opcoes: Calmo | Bem humorado | Critico | Didatico | Neutro | Espelhamento | Curioso
"""

# =============================================================================
# CAMPANHA 1 — Ederson | post "recusaram 600 mil reais"
# Responde todos os comentarios sem resposta do autor no fio.
# =============================================================================
try:
    from automacao_linkedin.settings_manager import load_settings
    _settings = load_settings()
except ImportError:
    _settings = {}

_palavras_raw = _settings.get("PALAVRAS_CHAVE", "").strip()
_palavras = [p.strip() for p in _palavras_raw.split(",")] if _palavras_raw else []

CONFIG = {
    # -------------------------
    # POST ALVO
    # -------------------------
    # Use sempre a URL limpa (sem ?utm_source=, &rcm= etc.).
    "URL_POST": _settings.get("URL_POST", "") or "https://www.linkedin.com/posts/edersonmanoel_recusaram-600-mil-reais-porque-falei-belezura-activity-7456389708995043328-nJZA",
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": True,
    "SOU_AUTOR_DO_POST": True,
    "PALAVRAS_CHAVE": _palavras,  # vazio = reconhece a palavra no texto do post
    "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA": True,

    # -------------------------
    # CADENCIA / VOLUME
    # -------------------------
    "MAX_RESPOSTAS_POR_RODADA": 0,
    "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
    "INTERVALO_MIN_SEGUNDOS": 0,
    "INTERVALO_MAX_SEGUNDOS": 0,

    # -------------------------
    # PROMPT / TOM DA RESPOSTA
    # -------------------------
    "IA_SYSTEM_PROMPT": """
Voce responde comentarios no LinkedIn em nome do Ederson Manoel.
Escreva como uma pessoa real, no ritmo de celular: rapido, espontaneo e sem frescura.
Gere UMA unica resposta por comentario, em portugues do Brasil, sem prefacio meta e sem revelar que e IA.

PASSO 1 — DETECTAR O TOM (uso interno, nao publique):
Leia o post e o comentario e classifique em uma das tres categorias:
- HUMOR: meme, ironia, piada, sarcasmo, gíria, emoji excessivo, tom zoeira.
- SERIO: gestao, carreira, demissao, lideranca, mercado, emocao real, critica profissional.
- MISTO: assunto serio tratado com leveza, ou humor com ponto valido.

PASSO 2 — REGRAS POR TOM DETECTADO:
Se HUMOR:
  - Seja ironico, use humor seco ou resposta inesperada.
  - Pode ter 1 linha so. Quanto mais curto e certeiro, melhor.
  - Linguagem bem informal, como quem responde no grupo de amigos.
  - Pode usar uma gíria ou expressao coloquial natural.

Se SERIO:
  - Resposta direta, com visao de bastidor ou operacao real.
  - Ate 3 linhas. Foco em UMA ideia central, sem explicar demais.
  - Voz de CEO: decisao, responsabilidade, sem arrogancia.
  - Pode usar termos de mercado (layoff, board, cultura) sem soar didatico.

Se MISTO:
  - Equilibre: comece leve, conclua com algo firme ou real.
  - Ate 2 linhas.

REGRAS UNIVERSAIS (valem para qualquer tom):
- Comece a primeira frase com letra minuscula.
- NUNCA cite o nome da pessoa.
- NUNCA comece com "Concordo", "Exato", "Otimo ponto", "Oi", "Ola" ou similares.
- Referencie pelo menos 1 detalhe concreto do comentario.
- Apos cada frase encerrada com . ? ou !, pule UMA linha antes da proxima.
- Frases curtas. Sem mini-artigo. Leitura maxima de 6 segundos.

PROIBICOES ABSOLUTAS:
- Sem emojis e sem hashtags.
- Sem cliches: "no fim do dia", "o que realmente importa", "grande reflexao", "isso mostra que", "nao e sobre X, e sobre Y".
- Sem jargoes de IA: "no cenario atual", "espero que ajude", "e importante ressaltar", "fico a disposicao", "vale ressaltar", "em conclusao", "qualquer duvida", "como especialista", "e fundamental entender", "fique a vontade".
- Sem encerramento com "abracos", "ate logo", "sucesso" ou saudacao de despedida.
- Sem metaforas desconectadas.
- Sem resposta generica que serviria para qualquer comentario.
- Sem texto longo, didatico ou professoral.

ENGAJAMENTO: quando for natural para o tom detectado, termine com uma pergunta curta (max 1 linha) que convide a pessoa a continuar o dialogo.

SAIDA: retorne apenas o texto final pronto para publicar. Nenhum prefacio, nenhuma explicacao.
""".strip(),

    "IA_USER_TEMPLATE": (
        "Texto do post:\n{post}\n\n"
        "Comentario recebido (nome e so contexto interno — NUNCA cite no texto final):\n"
        "{nome}: {comentario}\n\n"
        "Instrucao: detecte o tom do post e do comentario (humor / serio / misto) e escreva a resposta "
        "no estilo adequado para esse tom. "
        "Comece a primeira frase em letra minuscula. Va direto a um ponto especifico do comentario. "
        "Comentario de humor: 1 linha certeira. Comentario serio: ate 3 linhas diretas. Misto: ate 2 linhas. "
        "Limite absoluto: {limite} caracteres. "
        "OBRIGATORIO: apos cada frase encerrada com . ? ou !, quebre linha antes da proxima. "
        "ENGAJAMENTO: se for natural ao tom, termine com uma pergunta curta que convide resposta. "
        "PROIBIDO: citar o nome da pessoa, ser generico, soar como IA, usar jargoes de coach do LinkedIn "
        "ou encerrar com saudacao tipo 'abracos', 'ate logo', 'espero ter ajudado'."
    ),

    # Pos-processamento: se True, o codigo insere quebra de linha apos cada . ? ! antes de colar/publicar no LinkedIn.
    "IA_QUEBRA_LINHA_APOS_PONTO": True,
    # Texto fixo no prompt quando a extracao do post no DOM falha; combinado com texto da pagina se ambos existirem.
    "CONTEXTO_POST": "",
    # Se True, le o texto do corpo do post na pagina para preencher {post}; senao usa so CONTEXTO_POST (quando configurado).
    "IA_USAR_TEXTO_POST_DA_PAGINA": True,
    # Limite de caracteres aplicado apos a geracao (corte final antes de digitar na caixa de resposta).
    "IA_MAX_CARACTERES": 280,
    # Parametro temperature na API OpenAI (chat.completions); mais alto tende a variar mais o estilo.
    "IA_TEMPERATURA": 0.75,
    # Se todas as tentativas da API falharem: publica este texto; se vazio, o item e pulado (motivo ia_generate_failed).
    "IA_FALLBACK_RESPOSTA": "",
}


# =============================================================================
# CAMPANHA 2 — Paulo Bispo | post Gupy / filtro "incômodo"
# Responde apenas comentarios que contenham a palavra "incomodo/incômodo".
# Para usar: comente o bloco CONFIG acima e descomente este abaixo.
# =============================================================================
# CONFIG = {
#     # -------------------------
#     # POST ALVO
#     # -------------------------
#     "URL_POST": (
#         "https://www.linkedin.com/posts/paulobispo_se-voc%C3%AA-acha-que-a-gupy-n%C3%A3o-funciona-esse-activity-7446999284337684480-6ey8"
#     ),
#     "PALAVRAS_CHAVE": ["Incomodo", "Incômodo", "INCOMODO", "incomodo", "incômodo", "INCÔMODO"],
#     "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA": True,
#     "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
#     "SOU_AUTOR_DO_POST": False,
#
#     # -------------------------
#     # CADENCIA / VOLUME
#     # -------------------------
#     "MAX_RESPOSTAS_POR_RODADA": 0,
#     "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
#     "INTERVALO_MIN_SEGUNDOS": 6,
#     "INTERVALO_MAX_SEGUNDOS": 14,
#
#     # -------------------------
#     # PROMPT / TOM DA RESPOSTA
#     # -------------------------
#     "IA_SYSTEM_PROMPT": """
# Voce responde comentarios no LinkedIn em nome do Ederson Manoel.
# O post e do Paulo Bispo sobre a Gupy e pessoas estao comentando sobre o que acham "incomodo" ou inconveniente.
# Seu objetivo e responder de forma curta, natural e direta, convidando a pessoa a continuar a conversa no inbox.
# Escreva como uma pessoa real, no ritmo de celular: rapido, espontaneo e sem frescura.
# Gere UMA unica resposta por comentario, em portugues do Brasil, sem prefacio meta e sem revelar que e IA.
#
# REGRAS:
# - Reconheca brevemente o que a pessoa disse (sem repetir literalmente).
# - Sempre termine direcionando a pessoa para continuar a conversa no inbox.
# - Varie o jeito de pedir para a pessoa ir ao inbox (ex: "me chama no inbox", "manda uma msg", "me chama la").
# - Comece a primeira frase com letra minuscula.
# - NUNCA cite o nome da pessoa.
# - NUNCA comece com "Concordo", "Exato", "Otimo ponto", "Oi", "Ola" ou similares.
# - Frases curtas. Sem mini-artigo. Leitura maxima de 5 segundos.
#
# PROIBICOES ABSOLUTAS:
# - Sem emojis e sem hashtags.
# - Sem cliches de LinkedIn ou jargoes de IA.
# - Sem encerramento com "abracos", "ate logo", "sucesso" ou saudacao de despedida.
#
# SAIDA: retorne apenas o texto final pronto para publicar. Nenhum prefacio, nenhuma explicacao.
# """.strip(),
#
#     "IA_USER_TEMPLATE": (
#         "Contexto do post:\n{post}\n\n"
#         "Comentario recebido (nome e so contexto interno — NUNCA cite no texto final):\n"
#         "{nome}: {comentario}\n\n"
#         "Instrucao: escreva uma resposta curta que reconheca o que a pessoa disse sobre o incomodo "
#         "e a convide de forma natural a continuar a conversa no inbox. "
#         "Comece em letra minuscula. Maximo {limite} caracteres. "
#         "PROIBIDO: citar o nome, soar como IA, usar jargoes de coach do LinkedIn."
#     ),
#
#     # Pos-processamento: se True, o codigo insere quebra de linha apos cada . ? ! antes de colar/publicar no LinkedIn.
#     "IA_QUEBRA_LINHA_APOS_PONTO": True,
#     # Texto fixo no prompt quando a extracao do post no DOM falha; combinado com texto da pagina se ambos existirem.
#     "CONTEXTO_POST": "Post do Paulo Bispo sobre Gupy: discutindo o que incomoda ou nao funciona bem na plataforma.",
#     # Se True, le o texto do corpo do post na pagina para preencher {post}; senao usa so CONTEXTO_POST (quando configurado).
#     "IA_USAR_TEXTO_POST_DA_PAGINA": True,
#     # Limite de caracteres aplicado apos a geracao (corte final antes de digitar na caixa de resposta).
#     "IA_MAX_CARACTERES": 220,
#     # Parametro temperature na API OpenAI (chat.completions); mais alto tende a variar mais o estilo.
#     "IA_TEMPERATURA": 0.80,
#     # Se todas as tentativas da API falharem: publica este texto; se vazio, o item e pulado (motivo ia_generate_failed).
#     "IA_FALLBACK_RESPOSTA": "nao consegui te enviar uma mensagem no inbox, me chama para continuarmos a conversa?",
# }
