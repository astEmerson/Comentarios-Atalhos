"""
Preset: default (modo comentario_simples)

Ordem da automacao:
1) identifica comentario elegivel pela palavra-chave;
2) escolhe uma frase da biblioteca;
3) responde no comentario;
4) vai para o proximo comentario elegivel.
"""

try:
    from automacao_linkedin.settings_manager import load_settings
    _settings = load_settings()
except ImportError:
    _settings = {}

_palavras_raw = _settings.get("PALAVRAS_CHAVE", "").strip()
_palavras = [p.strip() for p in _palavras_raw.split(",") if p.strip()] if _palavras_raw else []

_respostas_raw = _settings.get("RESPOSTAS_SIMPLES", "")
if _respostas_raw:
    _respostas_simples = [r.strip() for r in _respostas_raw.split("\n") if r.strip()]
else:
    _respostas_simples = [
        "me envia uma msg no inbox com essa palavra? Eu já te mando na hora aqui",
        "manda no inbox essa palavra que eu já te passo na hora aqui.",
        'Oii! Verifica se já recebeu? Se não, me manda "{palavra-chave}" no inbox, que assim eu consigo te mandar.',
        'Oii, me confirma se recebeu? Se não, preciso que me envie "{palavra-chave}" no inbox, que eu te mando',
    ]

CONFIG = {
    # -------------------------
    # CAMPANHA / POST ALVO
    # -------------------------
    "URL_POST": _settings.get("URL_POST", "") or (
        "https://www.linkedin.com/posts/paulobispo_sou-gupy-expert-e-vou-te-mostrar-como-passar-activity-7455665414250037248-o8qY?utm_source=social_share_send&utm_medium=member_desktop_web&rcm=ACoAAAWrL5sB33n9EVkdW9f6iBDhnRsUedJmuV4"
    ),
    "PALAVRAS_CHAVE": _palavras,
    "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA": True,
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
    "SOU_AUTOR_DO_POST": True,

    # -------------------------
    # CADENCIA / VOLUME
    # -------------------------
    "MAX_RESPOSTAS_POR_RODADA": 0,
    "RODADAS_VAZIAS_PARA_ENCERRAR": 3,

    # -------------------------
    # BIBLIOTECA DE RESPOSTAS
    # -------------------------
    "RESPOSTAS": _respostas_simples,
}
