# --- CONFIGURAÇÕES DO USUÁRIO ---
import os

_sm = None
try:
    from automacao_linkedin import settings_manager as _sm
except ImportError:
    try:
        import settings_manager as _sm  # quando automacao_linkedin está no sys.path
    except ImportError:
        _sm = None

_settings = _sm.load_settings() if _sm is not None else {}
# Conta ativa deste processo (vazio = instalação de conta única / legado).
CONTA_ID = _sm.conta_ativa_id() if _sm is not None else ""

# ==========================================
# SELEÇÃO DE MODO/PRESET DA AUTOMAÇÃO
# ==========================================
# Modos disponíveis: "dm_com_comentario" | "comentario_simples" | "comentario_ia"
MODO_AUTOMACAO = _settings.get("MODO_AUTOMACAO", "comentario_ia")
# Preset do modo (arquivo em modos/<modo>/presets/<preset>.py)
PRESET_AUTOMACAO = _settings.get("PRESET_AUTOMACAO", "default")

# Lista de URLs adicionais para processar em sequência (um post por linha)
# Quando vazio, usa apenas URL_POST. Quando preenchido, rotaciona entre os posts.
URLS_POST_LISTA = _settings.get("URLS_EXTRAS", "") or ""


# ==========================================
# TOM DE RESPOSTA DA IA (modo comentario_ia)
# ==========================================
IA_TOM_RESPOSTA = _settings.get("IA_TOM_RESPOSTA", "Automatico")

# ==========================================
# CONFIGURAÇÃO DA API OPENAI (modo comentario_ia)
# ==========================================
# Chave única, no .env. Vale para todos os perfis.
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or _settings.get("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = _settings.get("OPENAI_MODEL", "gpt-4o-mini")
# Timeout (segundos) por tentativa de chamada à API.
OPENAI_TIMEOUT_SEGUNDOS = 30
# Número de novas tentativas em caso de falha (além da 1ª).
OPENAI_MAX_RETRIES = 2

# ==========================================
# SELEÇÃO DE NAVEGADOR (só altere NAVEGADOR)
# ==========================================
NAVEGADOR = _settings.get("NAVEGADOR", "edge")

# IMPORTANTE (Edge/Chrome atuais): por segurança, o navegador BLOQUEIA automação
# apontando para a pasta User Data original (perfil que você usa no dia a dia) e também
# bloqueia depuração remota nela. Por isso o recomendado é USAR_PERFIL_PRINCIPAL = False:
# a automação usa um perfil dedicado onde você faz login no LinkedIn UMA vez e a sessão
# fica salva para sempre. Deixe True apenas se souber o que está fazendo.
USAR_PERFIL_PRINCIPAL = _settings.get("USAR_PERFIL_PRINCIPAL", False)
# True = cria cópia sincronizada do perfil principal para contornar bloqueio do Edge/Chrome.
# False = tenta abrir diretamente a pasta User Data original do navegador.
SINCRONIZAR_PERFIL_PRINCIPAL = _settings.get("SINCRONIZAR_PERFIL_PRINCIPAL", True)
# True = se o launch_persistent do Edge falhar, tenta conectar via CDP.
EDGE_CDP_FALLBACK_ATIVO = _settings.get("EDGE_CDP_FALLBACK_ATIVO", True)
# True = tenta iniciar o Edge automaticamente com porta CDP quando necessário.
EDGE_CDP_AUTO_START = _settings.get("EDGE_CDP_AUTO_START", True)
# Porta local usada para conexão CDP no fallback do Edge.
# Cada conta recebe uma porta própria para poderem rodar ao mesmo tempo.
EDGE_CDP_PORTA = int(_settings.get("EDGE_CDP_PORTA", 9222) or 9222)
if CONTA_ID and _sm is not None:
    EDGE_CDP_PORTA = _sm.porta_cdp_conta(CONTA_ID, EDGE_CDP_PORTA)
# Tentativas de inicialização do navegador antes de entrar em fallback.
NAVEGADOR_INIT_MAX_TENTATIVAS = int(_settings.get("NAVEGADOR_INIT_MAX_TENTATIVAS", 2) or 2)
# Tempo (segundos) que a automação espera você fazer login no LinkedIn na 1ª vez.
LOGIN_TIMEOUT_SEGUNDOS = int(_settings.get("LOGIN_TIMEOUT_SEGUNDOS", 600) or 600)

# Dentro de User Data do Chrome/Edge/Brave, qual perfil usar. O mais comum é "Default".
PERFIL_NAVEGADOR_SUBPASTA = _settings.get("PERFIL_NAVEGADOR_SUBPASTA", "Default")

# ==========================================
# SESSÃO DO LINKEDIN VIA COOKIE (login sem senha/2FA)
# ==========================================
# Cole aqui (ou pela tela de Configurações) o valor do cookie "li_at" da sua conta
# do LinkedIn. Assim a automação entra JÁ LOGADA no perfil dedicado, sem pedir login
# nem código. Para pegar: abra o LinkedIn logado no Edge > F12 > aba Aplicativo >
# Cookies > https://www.linkedin.com > copie o VALOR de "li_at".
# O "JSESSIONID" é opcional e ajuda em algumas ações (copie do mesmo lugar).
# Se o cookie expirar (logout/troca de senha), a automação avisa para colar um novo.
LINKEDIN_LI_AT = (_settings.get("LINKEDIN_LI_AT", "") or "").strip()
LINKEDIN_JSESSIONID = (_settings.get("LINKEDIN_JSESSIONID", "") or "").strip()

# Só Firefox: caminho do firefox.exe se não for o padrão em Arquivos de Programas.
# FIREFOX_EXECUTABLE = r"C:\Program Files\Mozilla Firefox\firefox.exe"

# Pasta base dos perfis dedicados (um subdiretório por navegador).
# Cada conta usa uma pasta própria: duas contas podem rodar simultaneamente sem
# disputar o mesmo user-data-dir (o que travava/reiniciava o navegador).
_LOCAL = os.environ.get("LOCALAPPDATA", "")
if CONTA_ID and _sm is not None:
    DIR_PERFIL_NAVEGADOR = _sm.caminho_perfil_navegador_conta(CONTA_ID)
else:
    DIR_PERFIL_NAVEGADOR = os.path.join(_LOCAL, "LinkedInBotPerfil")

# Salve este arquivo (Ctrl+S) após mudar. O main.py relê o config a cada rodada.
# URL/PALAVRAS/FRASES/INTERVALOS ficam nos presets em modos/<modo>/presets/<preset>.py.

# Dashboard de acompanhamento
# True = abre o painel de métricas automaticamente no início da execução.
ABRIR_DASHBOARD_AO_INICIAR = False
# True = reabre/atualiza o dashboard ao encerrar a execução.
ABRIR_DASHBOARD_NO_FIM = False
# True = no HTML do dashboard, ativa atualização automática da tela.
DASHBOARD_AUTO_REFRESH_ATIVO = True
# Intervalo (segundos) da atualização automática do dashboard.
DASHBOARD_AUTO_REFRESH_SEGUNDOS = 5
# Porta local para servir dashboard+JSON em tempo real (evita bloqueio de fetch no file://).
DASHBOARD_SERVER_PORT = 8765
# Contas do dashboard: cadastre um id fixo e o nome exibido no painel. Troque DASHBOARD_CONTA_ATIVA
# conforme a conta LinkedIn desta máquina/execução (eventos gravados usam esse id).
DASHBOARD_CONTAS = [
    {"id": "ederson", "nome": "Ederson"},
    {"id": "paulo", "nome": "Paulo"},
]
DASHBOARD_CONTA_ATIVA = "ederson"

# True = ao abrir o post, tenta trocar filtro de comentários para "Mais recentes".
# A troca acontece só ao carregar o post (repetir a cada rodada fazia a lista
# recarregar sem parar e reprocessar os mesmos comentários).
FORCAR_ORDENACAO_MAIS_RECENTES = True

# True = nunca responde duas vezes ao mesmo perfil no mesmo post, mesmo após reiniciar.
NAO_REPETIR_MESMO_PERFIL = True

# Trecho após https://www.linkedin.com/in/  (ex: joão-silva-123abc). Usado para saber se você já respondeu no fio.
# Deixe vazio: o robô descobre sozinho o perfil logado e guarda em MEU_PERFIL_IN_SLUG_DETECTADO.
MEU_PERFIL_IN_SLUG = (_settings.get("MEU_PERFIL_IN_SLUG", "") or "").strip()
MEU_PERFIL_IN_SLUG_DETECTADO = (_settings.get("MEU_PERFIL_IN_SLUG_DETECTADO", "") or "").strip()

# Último recurso, quando o campo está vazio e a detecção automática falhou:
# deriva do link do post (.../posts/paulo-nome_titulo-activity-... → "paulo-nome").
TENTAR_SLUG_A_PARTIR_DA_URL_POST = True

# Se no HTML do fio já aparece texto igual a alguma frase longa de RESPOSTAS, pula (backup).
DETECTAR_RESPOSTAS_REPETIDAS_PELO_TEXTO = True

# True = cola o texto de uma vez (bem mais rápido). False = digita letra a letra.
DIGITACAO_INSTANTANEA = False
# Só se DIGITACAO_INSTANTANEA = False: atraso entre teclas (ms)
DIGITACAO_DELAY_MIN_MS = 10
DIGITACAO_DELAY_MAX_MS = 35

# True = imprime exceções ao processar cada bloco de comentário (para depuração).
LOG_ERROS_COMENTARIOS = False

# Ritmo fixo no mínimo: a tela não configura mais espera nem limite por rodada.
RODADAS_VAZIAS_PARA_ENCERRAR = 3

# ==========================================
# AJUSTES DA CONTA QUE VENCEM O PRESET
# ==========================================
# Aplicados por main.py depois de carregar o modo/preset, para que o que o usuário
# configurou na tela da conta prevaleça sobre os valores padrão do preset.
OVERRIDES_DA_CONTA = {
    "INTERVALO_MIN_SEGUNDOS": 0,
    "INTERVALO_MAX_SEGUNDOS": 0,
    "MAX_RESPOSTAS_POR_RODADA": 0,
    "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
    "FORCAR_ORDENACAO_MAIS_RECENTES": True,
    "NAO_REPETIR_MESMO_PERFIL": True,
    "MODO_AUTOMACAO": "comentario_simples",
}
if MEU_PERFIL_IN_SLUG:
    OVERRIDES_DA_CONTA["MEU_PERFIL_IN_SLUG"] = MEU_PERFIL_IN_SLUG
