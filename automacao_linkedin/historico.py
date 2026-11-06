"""
Histórico de quem já foi respondido, por conta e por post.

É o que impede a automação de responder a mesma pessoa duas vezes — inclusive
depois de reiniciar o robô ou quando o LinkedIn recarrega a lista e devolve o
comentário com outro identificador. Fica em um módulo separado para a interface
poder consultar/limpar sem carregar o motor (Playwright, IA etc.).
"""

import hashlib
import json
import os
from datetime import datetime
from typing import Dict, Set, Tuple

MAX_POR_POST = 5000


def chave_url_post(url: str) -> str:
    """Índice curto e estável para a URL dentro do JSON."""
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:16]


def _ler(caminho: str) -> Dict:
    if not caminho or not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def _gravar(caminho: str, dados: Dict) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def _entrada(dados: Dict, chave: str) -> Dict:
    """Normaliza a entrada (o formato antigo guardava só uma lista de comentários)."""
    bruto = dados.get(chave)
    if isinstance(bruto, list):
        return {"comentarios": list(bruto), "perfis": []}
    if isinstance(bruto, dict):
        return {
            "comentarios": list(bruto.get("comentarios") or []),
            "perfis": list(bruto.get("perfis") or []),
        }
    return {"comentarios": [], "perfis": []}


def _campanha_ativa() -> str:
    try:
        from settings_manager import conta_ativa_id

        return conta_ativa_id()
    except Exception:
        return ""


def carregar_post(caminho: str, url_post: str) -> Tuple[Set[str], Set[str]]:
    """(comentários respondidos, perfis respondidos) já salvos para este post."""
    if not url_post:
        return set(), set()
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        return repositorio.historico_do_post(campanha_id, url_post)
    entrada = _entrada(_ler(caminho), chave_url_post(url_post))
    return set(entrada["comentarios"]), set(entrada["perfis"])


def registrar(caminho: str, url_post: str, chave_comentario: str, perfil_key: str = "") -> None:
    """Grava na hora que este comentário/perfil já foi respondido."""
    if not url_post or not (chave_comentario or perfil_key):
        return
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_historico(campanha_id, url_post, chave_comentario, perfil_key)
        return
    dados = _ler(caminho)
    chave = chave_url_post(url_post)
    entrada = _entrada(dados, chave)

    for campo, valor in (("comentarios", chave_comentario), ("perfis", perfil_key)):
        if not valor:
            continue
        if valor not in entrada[campo]:
            entrada[campo].append(valor)
        if len(entrada[campo]) > MAX_POR_POST:
            entrada[campo] = entrada[campo][-MAX_POR_POST:]

    entrada["url"] = url_post
    entrada["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
    dados[chave] = entrada
    _gravar(caminho, dados)


def limpar_post(caminho: str, url_post: str) -> bool:
    """Apaga o histórico de um post (permite responder de novo quem já respondeu)."""
    if not url_post:
        return False
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        return repositorio.limpar_historico(campanha_id, url_post)
    dados = _ler(caminho)
    chave = chave_url_post(url_post)
    if chave not in dados:
        return False
    del dados[chave]
    _gravar(caminho, dados)
    return True


def contar_post(caminho: str, url_post: str) -> Tuple[int, int]:
    comentarios, perfis = carregar_post(caminho, url_post)
    return len(comentarios), len(perfis)
