CONFIG_BASE = {
    # Funcoes ativas do modo:
    # 1) seleciona todo comentario raiz do post sem resposta do autor no fio;
    # 2) gera resposta com OpenAI a partir do prompt do preset;
    # 3) publica a resposta e segue para o proximo.
    "ENVIAR_MENSAGEM_PERFIL_ATIVO": False,
    "TENTAR_CONECTAR_COM_NOTA_SEM_MENSAGEM": False,

    # Regra de elegibilidade padrao do modo IA:
    # responder comentarios cujo fio ainda nao tem resposta sua (autor).
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": True,
    "SOU_AUTOR_DO_POST": True,

    # Palavras-chave e biblioteca de respostas NAO sao usadas neste modo;
    # ficam vazias para manter compatibilidade com validacoes existentes.
    "PALAVRAS_CHAVE": [],
    "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA": True,
    "RESPOSTAS": [],

    # Limites e tempos do modo:
    "MAX_ENVIO_PERFIL_POR_RODADA": 0,
    "MAX_RESPOSTAS_POR_RODADA": 0,
    "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
    "INTERVALO_MIN_SEGUNDOS": 0,
    "INTERVALO_MAX_SEGUNDOS": 0,
}
