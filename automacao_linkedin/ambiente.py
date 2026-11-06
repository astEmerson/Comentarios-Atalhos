"""Carrega o .env e resolve a conexão do Neon sem espalhar a senha pelo código."""

from __future__ import annotations

import os
from pathlib import Path

_DIR_MODULO = Path(__file__).resolve().parent
_DIR_PROJETO = _DIR_MODULO.parent
_DIR_ACIMA = _DIR_PROJETO.parent

_carregado = False


def candidatos_env() -> list[Path]:
    explicito = (os.environ.get("AUTOMACAO_ENV_FILE") or "").strip()
    caminhos = []
    if explicito:
        caminhos.append(Path(explicito))
    caminhos.extend(
        [
            _DIR_PROJETO / ".env",
            _DIR_ACIMA / ".env",
            Path.cwd() / ".env",
        ]
    )
    vistos = set()
    unicos = []
    for caminho in caminhos:
        chave = str(caminho).lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(caminho)
    return unicos


def carregar_env() -> None:
    global _carregado
    if _carregado:
        return
    from dotenv import load_dotenv

    for caminho in candidatos_env():
        if caminho.is_file():
            load_dotenv(caminho, override=False)
    _carregado = True


def _sem_pooler(url: str) -> str:
    """Troca o host com -pooler pelo host direto, sem mexer na senha."""
    if "@" not in url or "-pooler" not in url:
        return url
    inicio, _, resto = url.rpartition("@")
    resto = resto.replace("-pooler.", ".", 1).replace("-pooler", "", 1)
    return f"{inicio}@{resto}"


def database_url(direta: bool = False) -> str:
    """String de conexão. direta=True serve para criar tabelas e migrar."""
    carregar_env()
    if direta:
        direta_cfg = (os.environ.get("DATABASE_URL_DIRECT") or "").strip()
        if direta_cfg:
            return direta_cfg
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL vazia. Cole a string postgresql:// do Neon no arquivo .env."
        )
    if not url.startswith("postgres"):
        raise RuntimeError("DATABASE_URL precisa começar com postgresql://.")
    if direta:
        return _sem_pooler(url)
    return url


def dir_dados() -> Path:
    """Pasta local fora do OneDrive: sessão, fila e logs."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    pasta = Path(base) / "RespostaIscas"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta
