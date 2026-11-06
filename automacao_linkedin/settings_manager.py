"""
Gerenciamento de configurações da automação.

Suporta múltiplas contas: cada conta tem seu próprio arquivo em `contas/<id>.json`,
com histórico, métricas e perfil de navegador isolados. O processo do robô descobre
qual conta usar pelas variáveis de ambiente AUTOMACAO_CONTA_ID / AUTOMACAO_SETTINGS_FILE,
o que permite rodar várias contas (ou o mesmo perfil em posts diferentes) ao mesmo tempo.
"""

import json
import os
import re
import shutil
import unicodedata
from typing import Any, Dict, List, Optional

_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Arquivo legado (instalações antigas de conta única). Continua sendo lido na migração.
SETTINGS_FILE = os.path.join(_PROJ_DIR, "settings.json")

CONTAS_DIR = os.path.join(_PROJ_DIR, "contas")
INDICE_CONTAS = os.path.join(CONTAS_DIR, "_contas.json")

ENV_SETTINGS_FILE = "AUTOMACAO_SETTINGS_FILE"
ENV_CONTA_ID = "AUTOMACAO_CONTA_ID"

DEFAULT_SETTINGS: Dict[str, Any] = {
    # Identificação da conta
    "NOME_CONTA": "Conta 1",
    # IA
    "OPENAI_API_KEY": "",
    "OPENAI_MODEL": "gpt-4o-mini",
    "IA_TOM_RESPOSTA": "Automatico",
    # Navegador
    "NAVEGADOR": "edge",
    "USAR_PERFIL_PRINCIPAL": False,
    "SINCRONIZAR_PERFIL_PRINCIPAL": False,
    "EDGE_CDP_FALLBACK_ATIVO": True,
    "EDGE_CDP_AUTO_START": True,
    "EDGE_CDP_PORTA": 9222,
    "NAVEGADOR_INIT_MAX_TENTATIVAS": 2,
    "LOGIN_TIMEOUT_SEGUNDOS": 600,
    "PERFIL_NAVEGADOR_SUBPASTA": "Default",
    # Campanha
    "MODO_AUTOMACAO": "comentario_simples",
    "PRESET_AUTOMACAO": "default",
    "URL_POST": "",
    "URLS_EXTRAS": "",
    "PALAVRAS_CHAVE": "",
    # Sessão LinkedIn
    "LINKEDIN_LI_AT": "",
    "LINKEDIN_JSESSIONID": "",
    "MEU_PERFIL_IN_SLUG": "",
    # Preenchido pelo robô ao identificar o perfil logado; o campo acima manda nele.
    "MEU_PERFIL_IN_SLUG_DETECTADO": "",
    # Ritmo e limites (0 = sem espera / sem limite)
    "INTERVALO_MIN_SEGUNDOS": 0,
    "INTERVALO_MAX_SEGUNDOS": 0,
    "MAX_RESPOSTAS_POR_RODADA": 0,
    "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
    # Regras anti-repetição
    "NAO_REPETIR_MESMO_PERFIL": True,
    "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
    "FORCAR_ORDENACAO_MAIS_RECENTES": True,
    # Sincronização em nuvem (Supabase). Desligada: métricas ficam locais e instantâneas.
    "SINCRONIZAR_NUVEM": False,
    "PIN_ATUAL": "",
    "NOME_CAMPANHA_ATUAL": "",
    # Textos — use {palavra-chave} para a palavra reconhecida (ou digitada)
    "RESPOSTAS_SIMPLES": (
        "me envia uma msg no inbox com essa palavra? Eu já te mando na hora aqui\n"
        "manda no inbox essa palavra que eu já te passo na hora aqui.\n"
        'Oii! Verifica se já recebeu? Se não, me manda "{palavra-chave}" no inbox, que assim eu consigo te mandar.\n'
        'Oii, me confirma se recebeu? Se não, preciso que me envie "{palavra-chave}" no inbox, que eu te mando'
    ),
    "MENSAGEM_PERFIL_TEMPLATE": (
        "Ooi, vi o seu comentário sobre {palavra-chave}. Me envia o seu Whatsapp? "
        "Vamos conversar melhor sobre isso."
    ),
    "RESPOSTA_PUBLICA_DM_SUCESSO": "te enviei uma mensagem no inbox.",
    "RESPOSTA_PUBLICA_DM_FALHA": "não consegui te chamar no inbox, me chama para continuarmos a conversa?",
}

# Ao duplicar, tudo é copiado (inclusive o cookie): o caso comum é rodar a MESMA
# conta em outro post, bastando trocar a URL na cópia.


# ============================================================
# Utilidades de caminho
# ============================================================

def _slug(texto: str, padrao: str = "conta") -> str:
    base = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    return base or padrao


def caminho_conta(conta_id: str) -> str:
    return os.path.join(CONTAS_DIR, f"{_slug(conta_id)}.json")


def caminho_settings_ativo() -> str:
    """Arquivo de settings que este processo deve usar."""
    via_env = (os.environ.get(ENV_SETTINGS_FILE) or "").strip()
    if via_env:
        return via_env
    conta = (os.environ.get(ENV_CONTA_ID) or "").strip()
    if conta:
        return caminho_conta(conta)
    return SETTINGS_FILE


def conta_ativa_id() -> str:
    """Id da conta deste processo (vazio quando roda no modo legado)."""
    return _slug(os.environ.get(ENV_CONTA_ID) or "", padrao="")


def _garantir_dir() -> None:
    os.makedirs(CONTAS_DIR, exist_ok=True)


def _ler_json(caminho: str) -> Optional[dict]:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else None
    except Exception:
        return None


def _escrever_json(caminho: str, dados: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        temporario = f"{caminho}.tmp"
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        os.replace(temporario, caminho)
        return True
    except Exception as e:
        print(f"[ERRO] Falha ao salvar {os.path.basename(caminho)}: {e}")
        return False


# ============================================================
# API de conta única (compatibilidade com o código existente)
# ============================================================

def _chave_openai() -> str:
    """Uma chave só, no .env, para todos os perfis."""
    try:
        from ambiente import carregar_env

        carregar_env()
    except Exception:
        pass
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def _banco_disponivel() -> bool:
    try:
        from ambiente import carregar_env, database_url

        carregar_env()
        database_url()
        return True
    except Exception:
        return False


def _montar_settings_banco(campanha_id: str) -> Optional[Dict[str, Any]]:
    import repositorio

    campanha = repositorio.obter_campanha(campanha_id)
    if not campanha:
        return None
    sessao = repositorio.ler_sessao(str(campanha["perfil_id"]))
    settings = DEFAULT_SETTINGS.copy()
    settings.update(
        {
            "NOME_CONTA": campanha.get("nome") or campanha_id,
            "NOME_CAMPANHA_ATUAL": campanha.get("nome") or campanha_id,
            "PERFIL_ID": campanha.get("perfil_id") or "",
            "URL_POST": campanha.get("url_post") or "",
            "PALAVRAS_CHAVE": campanha.get("palavras_chave") or "",
            "URLS_EXTRAS": campanha.get("urls_extras") or "",
            "RESPOSTAS_SIMPLES": campanha.get("respostas_simples") or "",
            "MENSAGEM_PERFIL_TEMPLATE": campanha.get("mensagem_perfil_template") or "",
            "RESPOSTA_PUBLICA_DM_SUCESSO": campanha.get("resposta_publica_dm_sucesso") or "",
            "RESPOSTA_PUBLICA_DM_FALHA": campanha.get("resposta_publica_dm_falha") or "",
            "NAVEGADOR": campanha.get("navegador") or "edge",
            "PERFIL_NAVEGADOR_SUBPASTA": campanha.get("perfil_navegador_subpasta") or "Default",
            "LINKEDIN_LI_AT": sessao.get("li_at") or "",
            "LINKEDIN_JSESSIONID": sessao.get("jsessionid") or "",
            "MEU_PERFIL_IN_SLUG": campanha.get("slug_manual") or "",
            "MEU_PERFIL_IN_SLUG_DETECTADO": campanha.get("slug_linkedin") or "",
            "OPENAI_API_KEY": _chave_openai(),
            "MODO_AUTOMACAO": "comentario_simples",
            "INTERVALO_MIN_SEGUNDOS": 0,
            "INTERVALO_MAX_SEGUNDOS": 0,
            "MAX_RESPOSTAS_POR_RODADA": 0,
            "RODADAS_VAZIAS_PARA_ENCERRAR": 3,
            "NAO_REPETIR_MESMO_PERFIL": True,
            "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO": False,
            "FORCAR_ORDENACAO_MAIS_RECENTES": True,
        }
    )
    return settings


def _atualizar_banco(campanha_id: str, campos: Dict[str, Any]) -> None:
    import repositorio

    campanha = repositorio.obter_campanha(campanha_id)
    if not campanha:
        return
    perfil_id = str(campanha["perfil_id"])
    if "MEU_PERFIL_IN_SLUG_DETECTADO" in campos:
        repositorio.definir_slug_detectado(perfil_id, str(campos.get("MEU_PERFIL_IN_SLUG_DETECTADO") or ""))
    if any(chave in campos for chave in ("LINKEDIN_LI_AT", "LINKEDIN_JSESSIONID")):
        sessao = repositorio.ler_sessao(perfil_id)
        repositorio.salvar_sessao(
            perfil_id,
            str(campos.get("LINKEDIN_LI_AT", sessao.get("li_at") or "")),
            str(campos.get("LINKEDIN_JSESSIONID", sessao.get("jsessionid") or "")),
        )
    campos_campanha = {}
    for origem, destino in (
        ("NOME_CONTA", "nome"),
        ("URL_POST", "url_post"),
        ("PALAVRAS_CHAVE", "palavras_chave"),
        ("URLS_EXTRAS", "urls_extras"),
    ):
        if origem in campos:
            campos_campanha[destino] = campos[origem]
    if campos_campanha:
        atual = {
            "nome": campanha.get("nome") or campanha_id,
            "url_post": campanha.get("url_post") or "",
            "palavras_chave": campanha.get("palavras_chave") or "",
            "urls_extras": campanha.get("urls_extras") or "",
        }
        atual.update(campos_campanha)
        repositorio.salvar_campanha(campanha_id, atual)


def load_settings() -> Dict[str, Any]:
    if _banco_disponivel():
        campanha_id = conta_ativa_id()
        if campanha_id:
            try:
                montado = _montar_settings_banco(campanha_id)
            except Exception as e:
                print(f"[ERRO] Não foi possível ler a campanha no banco: {e}")
                montado = None
            if montado:
                return montado
    settings = DEFAULT_SETTINGS.copy()
    dados = _ler_json(caminho_settings_ativo())
    if dados:
        settings.update(dados)
    chave = _chave_openai()
    if chave:
        settings["OPENAI_API_KEY"] = chave
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    if _banco_disponivel() and conta_ativa_id():
        _atualizar_banco(conta_ativa_id(), settings)
        return
    _escrever_json(caminho_settings_ativo(), settings)


def atualizar_settings(campos: Dict[str, Any]) -> None:
    """Grava só as chaves informadas, preservando o resto da campanha."""
    if _banco_disponivel() and conta_ativa_id():
        _atualizar_banco(conta_ativa_id(), campos)
        return
    caminho = caminho_settings_ativo()
    dados = _ler_json(caminho) or {}
    dados.update(campos)
    _escrever_json(caminho, dados)


# ============================================================
# API multi-conta
# ============================================================

def _ler_indice() -> Dict[str, Any]:
    dados = _ler_json(INDICE_CONTAS) or {}
    ordem = [str(x) for x in (dados.get("ordem") or []) if str(x).strip()]
    return {"ordem": ordem, "ativa": str(dados.get("ativa") or "")}


def _escrever_indice(ordem: List[str], ativa: str = "") -> None:
    _garantir_dir()
    _escrever_json(INDICE_CONTAS, {"ordem": ordem, "ativa": ativa})


def _ids_em_disco() -> List[str]:
    _garantir_dir()
    ids = []
    try:
        for nome in sorted(os.listdir(CONTAS_DIR)):
            if nome.startswith("_") or not nome.endswith(".json"):
                continue
            ids.append(nome[: -len(".json")])
    except Exception:
        pass
    return ids


def migrar_legado_se_preciso() -> None:
    """Cria as contas iniciais a partir de settings.json / settings_N.json antigos."""
    _garantir_dir()
    if _ids_em_disco():
        return

    legados: List[tuple[str, str]] = []
    if os.path.exists(SETTINGS_FILE):
        legados.append(("Conta 1", SETTINGS_FILE))
    indice = 2
    while True:
        caminho = os.path.join(_PROJ_DIR, f"settings_{indice}.json")
        if not os.path.exists(caminho):
            break
        legados.append((f"Conta {indice}", caminho))
        indice += 1

    if not legados:
        criar_conta("Conta 1")
        return

    ordem: List[str] = []
    for nome, caminho in legados:
        dados = _ler_json(caminho) or {}
        conta_id = _novo_id(dados.get("NOME_CONTA") or nome)
        settings = DEFAULT_SETTINGS.copy()
        settings.update(dados)
        settings["NOME_CONTA"] = str(dados.get("NOME_CONTA") or nome)
        settings["NOME_CAMPANHA_ATUAL"] = settings["NOME_CONTA"]
        _escrever_json(caminho_conta(conta_id), settings)
        ordem.append(conta_id)

    if ordem:
        # A primeira conta herda o histórico e as métricas da versão de conta única.
        adotar_arquivos_legados(ordem[0])
    _escrever_indice(ordem, ordem[0] if ordem else "")


def adotar_arquivos_legados(conta_id: str) -> None:
    """Copia histórico/métricas da instalação antiga para os arquivos desta conta."""
    dir_modulo = os.path.dirname(os.path.abspath(__file__))
    pares = (
        (os.path.join(dir_modulo, "respondidos_history.json"), caminho_historico_conta(conta_id)),
        (os.path.join(dir_modulo, "historico_metricas.json"), caminho_metricas_conta(conta_id)),
    )
    for origem, destino in pares:
        if not os.path.exists(origem) or os.path.exists(destino) or origem == destino:
            continue
        try:
            shutil.copy2(origem, destino)
        except Exception:
            pass


def _novo_id(nome_base: str) -> str:
    existentes = set(_ids_em_disco())
    base = _slug(nome_base)
    if base not in existentes:
        return base
    n = 2
    while f"{base}-{n}" in existentes:
        n += 1
    return f"{base}-{n}"


def listar_contas() -> List[Dict[str, str]]:
    """Contas cadastradas, na ordem definida pelo usuário."""
    migrar_legado_se_preciso()
    em_disco = _ids_em_disco()
    indice = _ler_indice()
    ordem = [i for i in indice["ordem"] if i in em_disco]
    ordem += [i for i in em_disco if i not in ordem]

    contas = []
    for conta_id in ordem:
        dados = _ler_json(caminho_conta(conta_id)) or {}
        contas.append({
            "id": conta_id,
            "nome": str(dados.get("NOME_CONTA") or conta_id),
        })
    return contas


def carregar_conta(conta_id: str) -> Dict[str, Any]:
    settings = DEFAULT_SETTINGS.copy()
    dados = _ler_json(caminho_conta(conta_id)) or {}
    settings.update(dados)
    settings.setdefault("NOME_CONTA", conta_id)
    return settings


def salvar_conta(conta_id: str, settings: Dict[str, Any]) -> bool:
    return _escrever_json(caminho_conta(conta_id), settings)


def criar_conta(nome: str, base_id: str = "") -> str:
    """Cria uma conta nova (opcionalmente copiando as configurações de outra)."""
    _garantir_dir()
    conta_id = _novo_id(nome)

    settings = DEFAULT_SETTINGS.copy()
    if base_id:
        settings.update(_ler_json(caminho_conta(base_id)) or {})
    settings["NOME_CONTA"] = str(nome or conta_id).strip() or conta_id
    # Campanha própria: se a nuvem for ligada, uma conta não sobrescreve a outra.
    settings["NOME_CAMPANHA_ATUAL"] = settings["NOME_CONTA"]

    _escrever_json(caminho_conta(conta_id), settings)
    indice = _ler_indice()
    ordem = [i for i in indice["ordem"] if i != conta_id] + [conta_id]
    _escrever_indice(ordem, indice["ativa"] or conta_id)
    return conta_id


def excluir_conta(conta_id: str) -> bool:
    caminho = caminho_conta(conta_id)
    try:
        if os.path.exists(caminho):
            os.remove(caminho)
    except Exception as e:
        print(f"[ERRO] Não foi possível excluir a conta: {e}")
        return False

    indice = _ler_indice()
    ordem = [i for i in indice["ordem"] if i != conta_id]
    ativa = indice["ativa"] if indice["ativa"] != conta_id else (ordem[0] if ordem else "")
    _escrever_indice(ordem, ativa)

    for caminho_extra in (caminho_historico_conta(conta_id), caminho_metricas_conta(conta_id)):
        try:
            if os.path.exists(caminho_extra):
                os.remove(caminho_extra)
        except Exception:
            pass
    return True


def definir_conta_ativa(conta_id: str) -> None:
    indice = _ler_indice()
    _escrever_indice(indice["ordem"], conta_id)


def obter_conta_ativa() -> str:
    indice = _ler_indice()
    if indice["ativa"]:
        return indice["ativa"]
    contas = listar_contas()
    return contas[0]["id"] if contas else ""


# ============================================================
# Caminhos isolados por conta (histórico, métricas, perfil)
# ============================================================

def caminho_historico_conta(conta_id: str) -> str:
    sufixo = _slug(conta_id, padrao="")
    nome = f"respondidos_history_{sufixo}.json" if sufixo else "respondidos_history.json"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), nome)


def caminho_metricas_conta(conta_id: str) -> str:
    sufixo = _slug(conta_id, padrao="")
    nome = f"historico_metricas_{sufixo}.json" if sufixo else "historico_metricas.json"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), nome)


def caminho_perfil_navegador_conta(conta_id: str) -> str:
    base = os.path.join(os.environ.get("LOCALAPPDATA", _PROJ_DIR), "LinkedInBotPerfil")
    sufixo = _slug(conta_id, padrao="")
    return os.path.join(base, sufixo) if sufixo else base


# Pastas de cache e travas do navegador: não fazem falta na cópia (são
# recriadas) e as travas impediriam a cópia de abrir.
_PERFIL_NAO_COPIAR = {
    "Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache",
    "GraphiteDawnCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "GPUPersistentCache", "Service Worker", "Crashpad", "BrowserMetrics",
    "BrowserMetrics-spare.pma", "component_crx_cache", "extensions_crx_cache",
    "optimization_guide_model_store", "lockfile", "SingletonLock",
    "SingletonCookie", "SingletonSocket",
}


def copiar_perfil_navegador(origem_id: str, destino_id: str) -> str:
    """
    Copia a pasta do navegador de uma conta para outra (usado no Duplicar).

    É o que faz a cópia entrar já logada: além do cookie de sessão, a pasta
    leva os cookies que identificam o navegador para o LinkedIn. Uma pasta
    vazia parece um aparelho novo, e o LinkedIn pede autorização de novo.

    Retorna "ok", "parcial" (algum arquivo em uso ficou de fora — acontece
    com a conta original rodando), "sem_origem" ou "destino_existe".
    """
    origem = caminho_perfil_navegador_conta(origem_id)
    destino = caminho_perfil_navegador_conta(destino_id)
    if not origem or not os.path.isdir(origem):
        return "sem_origem"
    if os.path.exists(destino):
        # Sobra de uma conta excluída com o mesmo id (excluir não apaga a pasta
        # do navegador). Guarda de lado em vez de apagar e copia por cima.
        import time

        try:
            os.rename(destino, f"{destino}.antigo-{int(time.time())}")
        except Exception:
            return "destino_existe"

    def _ignorar(_pasta, nomes):
        return [n for n in nomes if n in _PERFIL_NAO_COPIAR]

    try:
        shutil.copytree(origem, destino, ignore=_ignorar, symlinks=False)
        return "ok"
    except shutil.Error:
        # copytree copia o que conseguir e só reclama no fim (arquivos em uso).
        return "parcial"
    except Exception as e:
        print(f"[AVISO] Não foi possível copiar o perfil do navegador: {e}")
        return "parcial" if os.path.isdir(destino) else "sem_origem"


def origem_navegador_perfil(perfil_id: str, ignorar: str = "") -> str:
    """Pasta de navegador já logada deste perfil, para copiar numa campanha nova."""
    master = f"perfil-{_slug(perfil_id)}"
    if os.path.isdir(caminho_perfil_navegador_conta(master)):
        return master
    try:
        import repositorio

        campanhas = repositorio.listar_campanhas(perfil_id)
    except Exception:
        return ""
    melhor = ""
    melhor_mtime = -1.0
    for campanha in campanhas:
        if campanha["id"] == ignorar:
            continue
        pasta = caminho_perfil_navegador_conta(campanha["id"])
        if not os.path.isdir(pasta):
            continue
        try:
            modificado = os.path.getmtime(pasta)
        except Exception:
            continue
        if modificado > melhor_mtime:
            melhor = campanha["id"]
            melhor_mtime = modificado
    return melhor


def remover_pasta_navegador(conta_id: str) -> None:
    pasta = caminho_perfil_navegador_conta(conta_id)
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "LinkedInBotPerfil")
    if not pasta or not base or not os.path.isdir(pasta):
        return
    try:
        if os.path.commonpath([os.path.abspath(pasta), os.path.abspath(base)]) != os.path.abspath(base):
            return
    except ValueError:
        return
    if os.path.basename(os.path.abspath(pasta)) != _slug(conta_id):
        return
    shutil.rmtree(pasta, ignore_errors=True)


def salvar_cookies_sessao(conta_id: str, li_at: str, jsessionid: str) -> bool:
    """
    Guarda no arquivo da conta a sessão do LinkedIn que o robô leu do navegador
    depois do login. Assim o Duplicar leva a sessão mesmo quando o login foi
    feito à mão (sem colar li_at/JSESSIONID).
    """
    if _banco_disponivel():
        try:
            import repositorio

            campanha = repositorio.obter_campanha(conta_id)
            if not campanha:
                return False
            repositorio.salvar_sessao(str(campanha["perfil_id"]), li_at or "", jsessionid or "")
            return True
        except Exception as e:
            print(f"[AVISO] Não foi possível guardar a sessão no perfil: {e}")
            return False
    caminho = caminho_conta(conta_id)
    dados = _ler_json(caminho)
    if dados is None:
        return False
    dados["LINKEDIN_LI_AT"] = li_at or ""
    dados["LINKEDIN_JSESSIONID"] = jsessionid or ""
    return _escrever_json(caminho, dados)


def porta_cdp_conta(conta_id: str, porta_base: int = 9222) -> int:
    """Porta CDP estável por campanha, para várias rodarem ao mesmo tempo."""
    import zlib

    return int(porta_base) + (zlib.crc32(_slug(conta_id).encode("utf-8")) % 500)


def ambiente_para_conta(conta_id: str) -> Dict[str, str]:
    """Variáveis de ambiente que isolam o subprocesso desta conta."""
    env = os.environ.copy()
    env[ENV_CONTA_ID] = _slug(conta_id)
    env[ENV_SETTINGS_FILE] = caminho_conta(conta_id)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env
