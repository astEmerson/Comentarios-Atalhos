"""
Preset: default (modo dm_com_comentario)

Ordem da automacao:
1) tenta enviar mensagem no inbox/perfil;
2) comenta no post conforme o resultado (sucesso/falha);
3) vai para o proximo comentario elegivel.
"""

try:
    from automacao_linkedin.settings_manager import load_settings
    _settings = load_settings()
except ImportError:
    _settings = {}

_palavras_raw = _settings.get("PALAVRAS_CHAVE", "").strip()
_palavras = [p.strip() for p in _palavras_raw.split(",") if p.strip()] if _palavras_raw else []

CONFIG = {
    # -------------------------
    # CAMPANHA / POST ALVO
    # -------------------------
    "URL_POST": _settings.get("URL_POST", "") or (
        "https://www.linkedin.com/posts/paulobispo_se-voc%C3%AA-acha-que-a-gupy-n%C3%A3o-funciona-esse-activity-7446999284337684480-6ey8?utm_source=social_share_send&utm_medium=member_desktop_web&rcm=ACoAAAT8xlABG3uwKDT6XE1ZyzV1Jc8BE_tjrV8"
    ),
    "PALAVRAS_CHAVE": _palavras,
    "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA": True,
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
    "SOU_AUTOR_DO_POST": True,

    # -------------------------
    # FLUXO DM (INBOX)
    # -------------------------
    "USAR_INMAIL_PREMIUM": True,
    "INMAIL_ASSUNTO": "Incômodo",
    "MENSAGEM_PERFIL_TEMPLATE": _settings.get("MENSAGEM_PERFIL_TEMPLATE", "Ooi, vi o seu comentário no meu post. Me envia o seu Whatsapp? Vamos conversar melhor sobre isso."),

    # -----------------------------------------
    # COMENTARIO PUBLICO APOS TENTATIVA DE DM
    # -----------------------------------------
    "RESPOSTA_PUBLICA_DM_SUCESSO": _settings.get("RESPOSTA_PUBLICA_DM_SUCESSO", "te enviei uma mensagem no inbox."),
    "RESPOSTA_PUBLICA_DM_FALHA": _settings.get("RESPOSTA_PUBLICA_DM_FALHA", "não consegui te chamar no inbox, me chama para continuarmos a conversa?"),

    # ------------------------------------------------
    # BIBLIOTECA (usada só quando ENVIAR_MENSAGEM_PERFIL_ATIVO=False)
    # ------------------------------------------------
    "RESPOSTAS": [
        "oiiee, você pode me chamar no direct para eu enviar pra você?",
        'oii, pode me chamar no direct com "{palavra-chave}"??',
        'fico feliz pelo interesse! Me chama no inbox com "{palavra-chave}".',
        "hey, quer me chamar no direct pra eu liberar o acesso pra você?",
        "ooi, me envia uma mensagem no inbox?",
        'opa! Me chama no inbox com "{palavra-chave}"?',
    ],
}
