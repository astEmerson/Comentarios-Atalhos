"""Acesso ao Postgres (Neon). Toda leitura e gravação do app passa por aqui."""

from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from ambiente import database_url, dir_dados

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def _statements(sql: str) -> list[str]:
    partes: list[str] = []
    buffer: list[str] = []
    for linha in sql.splitlines():
        if linha.strip().startswith("--"):
            continue
        buffer.append(linha)
        if linha.rstrip().endswith(";"):
            texto = "\n".join(buffer).strip().rstrip(";").strip()
            buffer = []
            if texto:
                partes.append(texto)
    resto = "\n".join(buffer).strip().rstrip(";").strip()
    if resto:
        partes.append(resto)
    return partes


@contextmanager
def conexao(direta: bool = False) -> Iterator[psycopg.Connection]:
    url = database_url(direta=direta)
    conn = psycopg.connect(
        url,
        connect_timeout=20,
        prepare_threshold=None,
        row_factory=dict_row,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def garantir_schema() -> None:
    sql = _SCHEMA.read_text(encoding="utf-8")
    with conexao(direta=True) as conn:
        with conn.cursor() as cur:
            for comando in _statements(sql):
                cur.execute(comando)


def caminho_sessao(perfil_id: str) -> Path:
    pasta = dir_dados() / "sessoes"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / f"{perfil_id}.json"


def ler_sessao(perfil_id: str) -> dict[str, str]:
    caminho = caminho_sessao(perfil_id)
    if not caminho.is_file():
        return {"li_at": "", "jsessionid": ""}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return {"li_at": "", "jsessionid": ""}
    return {
        "li_at": str(dados.get("li_at") or ""),
        "jsessionid": str(dados.get("jsessionid") or ""),
    }


def salvar_sessao(perfil_id: str, li_at: str, jsessionid: str) -> None:
    caminho = caminho_sessao(perfil_id)
    temporario = caminho.with_suffix(".json.tmp")
    temporario.write_text(
        json.dumps(
            {"li_at": li_at or "", "jsessionid": jsessionid or ""},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporario.replace(caminho)


def caminho_fila() -> Path:
    return dir_dados() / "fila.jsonl"


def enfileirar(item: dict[str, Any]) -> None:
    caminho = caminho_fila()
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(item, ensure_ascii=False) + "\n")


def _aplicar_item_fila(cur: psycopg.Cursor, item: dict[str, Any]) -> None:
    op = item.get("op")
    if op == "historico":
        cur.execute(
            """
            INSERT INTO historico_respondido
                (campanha_id, url_chave, url_post, tipo, chave, criado_em)
            VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
            ON CONFLICT DO NOTHING
            """,
            (
                item["campanha_id"],
                item["url_chave"],
                item.get("url_post") or "",
                item["tipo"],
                item["chave"],
                item.get("criado_em"),
            ),
        )
    elif op == "evento":
        cur.execute(
            """
            INSERT INTO evento_metrica
                (campanha_id, ts, dia, tipo, amount, texto, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item["campanha_id"],
                item["ts"],
                item["dia"],
                item["tipo"],
                int(item.get("amount") or 1),
                item.get("texto") or "",
                item.get("duration_ms"),
            ),
        )
    else:
        raise ValueError(f"operação de fila desconhecida: {op}")


def esvaziar_fila(conn: psycopg.Connection) -> int:
    caminho = caminho_fila()
    if not caminho.is_file():
        return 0
    linhas = [ln for ln in caminho.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not linhas:
        caminho.unlink(missing_ok=True)
        return 0
    restantes: list[str] = []
    enviados = 0
    with conn.cursor() as cur:
        for linha in linhas:
            try:
                _aplicar_item_fila(cur, json.loads(linha))
                enviados += 1
            except Exception:
                restantes.append(linha)
    if restantes:
        caminho.write_text("\n".join(restantes) + "\n", encoding="utf-8")
    else:
        caminho.unlink(missing_ok=True)
    return enviados


def upsert_perfil(cur: psycopg.Cursor, perfil: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO perfil (
            id, nome, slug_linkedin, respostas_simples, mensagem_perfil_template,
            resposta_publica_dm_sucesso, resposta_publica_dm_falha,
            navegador, perfil_navegador_subpasta, criado_em, atualizado_em
        ) VALUES (
            %(id)s, %(nome)s, %(slug_linkedin)s, %(respostas_simples)s,
            %(mensagem_perfil_template)s, %(resposta_publica_dm_sucesso)s,
            %(resposta_publica_dm_falha)s, %(navegador)s, %(perfil_navegador_subpasta)s,
            %(criado_em)s, now()
        )
        ON CONFLICT (id) DO UPDATE SET
            nome = EXCLUDED.nome,
            slug_linkedin = EXCLUDED.slug_linkedin,
            respostas_simples = EXCLUDED.respostas_simples,
            mensagem_perfil_template = EXCLUDED.mensagem_perfil_template,
            resposta_publica_dm_sucesso = EXCLUDED.resposta_publica_dm_sucesso,
            resposta_publica_dm_falha = EXCLUDED.resposta_publica_dm_falha,
            navegador = EXCLUDED.navegador,
            perfil_navegador_subpasta = EXCLUDED.perfil_navegador_subpasta,
            atualizado_em = now()
        """,
        perfil,
    )


def upsert_campanha(cur: psycopg.Cursor, campanha: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO campanha (
            id, perfil_id, nome, url_post, palavras_chave, urls_extras,
            arquivada, criado_em, atualizado_em
        ) VALUES (
            %(id)s, %(perfil_id)s, %(nome)s, %(url_post)s, %(palavras_chave)s,
            %(urls_extras)s, false, %(criado_em)s, now()
        )
        ON CONFLICT (id) DO UPDATE SET
            perfil_id = EXCLUDED.perfil_id,
            nome = EXCLUDED.nome,
            url_post = EXCLUDED.url_post,
            palavras_chave = EXCLUDED.palavras_chave,
            urls_extras = EXCLUDED.urls_extras,
            atualizado_em = now()
        """,
        campanha,
    )


def definir_config(cur: psycopg.Cursor, chave: str, valor: str) -> None:
    cur.execute(
        """
        INSERT INTO config_global (chave, valor, atualizado_em)
        VALUES (%s, %s, now())
        ON CONFLICT (chave) DO UPDATE SET
            valor = EXCLUDED.valor,
            atualizado_em = now()
        """,
        (chave, valor),
    )


def contar() -> dict[str, int]:
    with conexao() as conn:
        with conn.cursor() as cur:
            totais = {}
            for tabela in (
                "perfil",
                "campanha",
                "historico_respondido",
                "evento_metrica",
                "execucao",
                "config_global",
            ):
                cur.execute(f"SELECT count(*) AS n FROM {tabela}")
                totais[tabela] = int(cur.fetchone()["n"])
        return totais


def resumo_do_dia(campanha_id: str, dia: date | None = None) -> dict[str, int]:
    dia = dia or date.today()
    chaves = {
        "recognized": "reconhecidos",
        "responded": "respondidos",
        "edited": "editados",
        "skipped": "pulados",
    }
    with conexao() as conn:
        esvaziar_fila(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tipo, COALESCE(sum(amount), 0) AS n
                FROM evento_metrica
                WHERE campanha_id = %s AND dia = %s
                GROUP BY tipo
                """,
                (campanha_id, dia),
            )
            bruto = {linha["tipo"]: int(linha["n"]) for linha in cur.fetchall()}
    return {nome: bruto.get(tipo, 0) for tipo, nome in chaves.items()}


def registrar_evento(
    campanha_id: str,
    tipo: str,
    amount: int = 1,
    texto: str = "",
    duration_ms: int | None = None,
    quando: datetime | None = None,
) -> None:
    quando = quando or datetime.now().astimezone()
    item = {
        "op": "evento",
        "campanha_id": campanha_id,
        "ts": quando.isoformat(),
        "dia": quando.date().isoformat(),
        "tipo": tipo,
        "amount": amount,
        "texto": texto or "",
        "duration_ms": duration_ms,
    }
    try:
        with conexao() as conn:
            esvaziar_fila(conn)
            with conn.cursor() as cur:
                _aplicar_item_fila(cur, item)
    except psycopg.OperationalError:
        enfileirar(item)


def _tentar(acao, vezes: int = 3):
    erro = None
    for tentativa in range(vezes):
        try:
            return acao()
        except psycopg.OperationalError as e:
            erro = e
            time.sleep(1.2 * (tentativa + 1))
    raise erro


def slug(texto: str, padrao: str = "item") -> str:
    base = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    return base or padrao


def _novo_id(cur: psycopg.Cursor, tabela: str, nome: str) -> str:
    cur.execute(f"SELECT id FROM {tabela}")
    existentes = {linha["id"] for linha in cur.fetchall()}
    base = slug(nome, tabela)
    if base not in existentes:
        return base
    n = 2
    while f"{base}-{n}" in existentes:
        n += 1
    return f"{base}-{n}"


def listar_perfis() -> list[dict[str, Any]]:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*,
                       (SELECT count(*) FROM campanha c
                        WHERE c.perfil_id = p.id AND NOT c.arquivada) AS campanhas
                FROM perfil p
                ORDER BY CASE p.id
                    WHEN 'paulo' THEN 1
                    WHEN 'vaz' THEN 2
                    WHEN 'duarte' THEN 3
                    ELSE 4
                END, p.nome
                """
            )
            return list(cur.fetchall())


def obter_perfil(perfil_id: str) -> dict[str, Any] | None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM perfil WHERE id = %s", (perfil_id,))
            return cur.fetchone()


def criar_perfil(nome: str, base: dict[str, Any] | None = None) -> str:
    base = base or {}
    with conexao() as conn:
        with conn.cursor() as cur:
            perfil_id = _novo_id(cur, "perfil", nome)
            cur.execute(
                """
                INSERT INTO perfil (
                    id, nome, slug_linkedin, slug_manual, respostas_simples,
                    mensagem_perfil_template, resposta_publica_dm_sucesso,
                    resposta_publica_dm_falha, navegador, perfil_navegador_subpasta
                ) VALUES (%s, %s, '', '', %s, %s, %s, %s, %s, %s)
                """,
                (
                    perfil_id,
                    nome.strip() or perfil_id,
                    base.get("respostas_simples") or "",
                    base.get("mensagem_perfil_template") or "",
                    base.get("resposta_publica_dm_sucesso") or "",
                    base.get("resposta_publica_dm_falha") or "",
                    base.get("navegador") or "edge",
                    base.get("perfil_navegador_subpasta") or "Default",
                ),
            )
    return perfil_id


def salvar_perfil(perfil_id: str, campos: dict[str, Any]) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE perfil SET
                    nome = %(nome)s,
                    slug_manual = %(slug_manual)s,
                    respostas_simples = %(respostas_simples)s,
                    mensagem_perfil_template = %(mensagem_perfil_template)s,
                    resposta_publica_dm_sucesso = %(resposta_publica_dm_sucesso)s,
                    resposta_publica_dm_falha = %(resposta_publica_dm_falha)s,
                    navegador = %(navegador)s,
                    perfil_navegador_subpasta = %(perfil_navegador_subpasta)s,
                    atualizado_em = now()
                WHERE id = %(id)s
                """,
                {"id": perfil_id, **campos},
            )


def definir_slug_detectado(perfil_id: str, slug_linkedin: str) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE perfil
                SET slug_linkedin = %s, atualizado_em = now()
                WHERE id = %s
                """,
                (slug_linkedin or "", perfil_id),
            )


def listar_campanhas(perfil_id: str = "", busca: str = "") -> list[dict[str, Any]]:
    filtros = ["NOT c.arquivada"]
    params: list[Any] = []
    if perfil_id:
        filtros.append("c.perfil_id = %s")
        params.append(perfil_id)
    if busca.strip():
        filtros.append(
            "(c.nome ILIKE %s OR c.palavras_chave ILIKE %s OR c.url_post ILIKE %s)"
        )
        termo = f"%{busca.strip()}%"
        params.extend([termo, termo, termo])
    where = " AND ".join(filtros)
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.perfil_id, c.nome, c.url_post, c.palavras_chave,
                       c.urls_extras, c.criado_em, p.nome AS perfil_nome
                FROM campanha c
                JOIN perfil p ON p.id = c.perfil_id
                WHERE {where}
                ORDER BY c.criado_em DESC, c.nome
                """,
                params,
            )
            return list(cur.fetchall())


def obter_campanha(campanha_id: str) -> dict[str, Any] | None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.*, p.nome AS perfil_nome, p.slug_linkedin, p.slug_manual,
                       p.respostas_simples, p.mensagem_perfil_template,
                       p.resposta_publica_dm_sucesso, p.resposta_publica_dm_falha,
                       p.navegador, p.perfil_navegador_subpasta
                FROM campanha c
                JOIN perfil p ON p.id = c.perfil_id
                WHERE c.id = %s
                """,
                (campanha_id,),
            )
            return cur.fetchone()


def criar_campanha(perfil_id: str, nome: str, url_post: str = "", palavras_chave: str = "") -> str:
    with conexao() as conn:
        with conn.cursor() as cur:
            campanha_id = _novo_id(cur, "campanha", nome)
            cur.execute(
                """
                INSERT INTO campanha (
                    id, perfil_id, nome, url_post, palavras_chave, urls_extras, criado_em
                ) VALUES (%s, %s, %s, %s, %s, '', now())
                """,
                (campanha_id, perfil_id, nome.strip() or campanha_id, url_post.strip(), palavras_chave.strip()),
            )
    return campanha_id


def salvar_campanha(campanha_id: str, campos: dict[str, Any]) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campanha SET
                    nome = %(nome)s,
                    url_post = %(url_post)s,
                    palavras_chave = %(palavras_chave)s,
                    urls_extras = %(urls_extras)s,
                    atualizado_em = now()
                WHERE id = %(id)s
                """,
                {"id": campanha_id, **campos},
            )


def excluir_campanha(campanha_id: str) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM campanha WHERE id = %s", (campanha_id,))


def ler_config(chave: str) -> str:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT valor FROM config_global WHERE chave = %s", (chave,))
            linha = cur.fetchone()
    return str(linha["valor"]) if linha else ""


def gravar_config(chave: str, valor: str) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            definir_config(cur, chave, valor)


def _chave_url(url_post: str) -> str:
    import hashlib

    return hashlib.sha256((url_post or "").strip().encode("utf-8")).hexdigest()[:16]


def _pendentes_historico(campanha_id: str, url_chave: str) -> tuple[set[str], set[str]]:
    caminho = caminho_fila()
    comentarios: set[str] = set()
    perfis: set[str] = set()
    if not caminho.is_file():
        return comentarios, perfis
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        try:
            item = json.loads(linha)
        except Exception:
            continue
        if item.get("op") != "historico" or item.get("campanha_id") != campanha_id:
            continue
        if item.get("url_chave") != url_chave:
            continue
        if item.get("tipo") == "comentario":
            comentarios.add(str(item.get("chave") or ""))
        elif item.get("tipo") == "perfil":
            perfis.add(str(item.get("chave") or ""))
    comentarios.discard("")
    perfis.discard("")
    return comentarios, perfis


def historico_do_post(campanha_id: str, url_post: str) -> tuple[set[str], set[str]]:
    chave = _chave_url(url_post)

    def ler():
        with conexao() as conn:
            esvaziar_fila(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tipo, chave FROM historico_respondido
                    WHERE campanha_id = %s AND url_chave = %s
                    """,
                    (campanha_id, chave),
                )
                return list(cur.fetchall())

    linhas = _tentar(ler)
    comentarios = {linha["chave"] for linha in linhas if linha["tipo"] == "comentario"}
    perfis = {linha["chave"] for linha in linhas if linha["tipo"] == "perfil"}
    extra_c, extra_p = _pendentes_historico(campanha_id, chave)
    return comentarios | extra_c, perfis | extra_p


def registrar_historico(campanha_id: str, url_post: str, chave_comentario: str = "", perfil_key: str = "") -> None:
    if not url_post or not (chave_comentario or perfil_key):
        return
    agora = datetime.now(timezone.utc).isoformat()
    itens = []
    for tipo, valor in (("comentario", chave_comentario), ("perfil", perfil_key)):
        if not valor:
            continue
        itens.append(
            {
                "op": "historico",
                "campanha_id": campanha_id,
                "url_chave": _chave_url(url_post),
                "url_post": url_post,
                "tipo": tipo,
                "chave": valor,
                "criado_em": agora,
            }
        )
    try:
        with conexao() as conn:
            esvaziar_fila(conn)
            with conn.cursor() as cur:
                for item in itens:
                    _aplicar_item_fila(cur, item)
    except psycopg.OperationalError:
        for item in itens:
            enfileirar(item)


def limpar_historico(campanha_id: str, url_post: str) -> bool:
    chave = _chave_url(url_post)
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM historico_respondido
                WHERE campanha_id = %s AND url_chave = %s
                """,
                (campanha_id, chave),
            )
            apagou = cur.rowcount > 0
    caminho = caminho_fila()
    if caminho.is_file():
        novas = []
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(linha) if linha.strip() else None
            except Exception:
                novas.append(linha)
                continue
            if (
                item
                and item.get("op") == "historico"
                and item.get("campanha_id") == campanha_id
                and item.get("url_chave") == chave
            ):
                continue
            if linha.strip():
                novas.append(linha)
        if novas:
            caminho.write_text("\n".join(novas) + "\n", encoding="utf-8")
        else:
            caminho.unlink(missing_ok=True)
    return apagou


def iniciar_execucao(campanha_id: str, mode: str, url_post: str) -> str:
    run_id = uuid.uuid4().hex[:12]

    def gravar():
        with conexao() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO execucao (run_id, campanha_id, started_at, mode, url_post)
                    VALUES (%s, %s, now(), %s, %s)
                    """,
                    (run_id, campanha_id, mode or "", url_post or ""),
                )

    try:
        _tentar(gravar)
    except psycopg.OperationalError:
        pass
    return run_id


def finalizar_execucao(run_id: str, mode: str = "", url_post: str = "") -> bool:
    def gravar():
        with conexao() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT campanha_id, started_at FROM execucao WHERE run_id = %s", (run_id,))
                linha = cur.fetchone()
                if not linha:
                    return False
                cur.execute(
                    """
                    SELECT tipo, COALESCE(sum(amount), 0) AS n
                    FROM evento_metrica
                    WHERE campanha_id = %s AND ts >= %s
                    GROUP BY tipo
                    """,
                    (linha["campanha_id"], linha["started_at"]),
                )
                totais = {item["tipo"]: int(item["n"]) for item in cur.fetchall()}
                cur.execute(
                    """
                    UPDATE execucao SET
                        ended_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::bigint),
                        mode = CASE WHEN %s <> '' THEN %s ELSE mode END,
                        url_post = CASE WHEN %s <> '' THEN %s ELSE url_post END,
                        recognized = %s,
                        responded = %s,
                        edited = %s,
                        reposted_after_delete = %s,
                        skipped = %s
                    WHERE run_id = %s
                    """,
                    (
                        mode or "",
                        mode or "",
                        url_post or "",
                        url_post or "",
                        totais.get("recognized", 0),
                        totais.get("responded", 0),
                        totais.get("edited", 0),
                        totais.get("reposted_after_delete", 0),
                        totais.get("skipped", 0),
                        run_id,
                    ),
                )
                return True

    try:
        return bool(_tentar(gravar))
    except psycopg.OperationalError:
        return False
