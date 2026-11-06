"""
Copia contas, histórico e métricas dos JSON locais para o Neon.

Não apaga os arquivos de origem. Cookies vão para um arquivo local fora do
projeto. A chave OpenAI vai para config_global.

Uso:
    python migrar_para_banco.py            # só mostra o que faria
    python migrar_para_banco.py --aplicar  # grava
    python migrar_para_banco.py --aplicar --forcar  # substitui uma migração anterior
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
_RAIZ = _AQUI.parent
for _p in (_RAIZ, _AQUI):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import historico  # noqa: E402
import repositorio  # noqa: E402

CONTAS = _RAIZ / "contas"
INDICE = CONTAS / "_contas.json"
BASE_TEMPO = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _ler_json(caminho: Path):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return None


def _perfil_de(conta_id: str) -> str:
    base = conta_id.lower()
    if base.startswith("paulo"):
        return "paulo"
    if base.startswith("vaz"):
        return "vaz"
    if base.startswith("duarte"):
        return "duarte"
    return ""


def _ordem_contas() -> list[str]:
    indice = _ler_json(INDICE) or {}
    ordem = [str(x) for x in (indice.get("ordem") or []) if str(x).strip()]
    em_disco = sorted(
        p.stem
        for p in CONTAS.glob("*.json")
        if not p.name.startswith("_")
    )
    return [i for i in ordem if i in em_disco] + [i for i in em_disco if i not in ordem]


def _mais_frequente(valores: list[str], padrao: str = "") -> str:
    limpos = [v for v in valores if str(v or "").strip()]
    if not limpos:
        return padrao
    return Counter(limpos).most_common(1)[0][0]


def montar() -> dict:
    ordem = _ordem_contas()
    contas = []
    for indice, conta_id in enumerate(ordem):
        caminho = CONTAS / f"{conta_id}.json"
        dados = _ler_json(caminho) or {}
        contas.append(
            {
                "id": conta_id,
                "perfil_id": _perfil_de(conta_id),
                "dados": dados,
                "mtime": caminho.stat().st_mtime,
                "criado_em": BASE_TEMPO + timedelta(minutes=indice),
            }
        )

    desconhecidas = [c["id"] for c in contas if not c["perfil_id"]]
    perfis: dict[str, dict] = {}
    for perfil_id in ("paulo", "vaz", "duarte"):
        grupo = [c for c in contas if c["perfil_id"] == perfil_id]
        if not grupo:
            continue
        recente = max(grupo, key=lambda c: c["mtime"])
        textos = [str(c["dados"].get("RESPOSTAS_SIMPLES") or "") for c in grupo]
        perfis[perfil_id] = {
            "id": perfil_id,
            "nome": perfil_id.capitalize(),
            "slug_linkedin": _mais_frequente(
                [c["dados"].get("MEU_PERFIL_IN_SLUG_DETECTADO") or c["dados"].get("MEU_PERFIL_IN_SLUG") or "" for c in grupo]
            ),
            "respostas_simples": str(recente["dados"].get("RESPOSTAS_SIMPLES") or ""),
            "mensagem_perfil_template": str(recente["dados"].get("MENSAGEM_PERFIL_TEMPLATE") or ""),
            "resposta_publica_dm_sucesso": str(recente["dados"].get("RESPOSTA_PUBLICA_DM_SUCESSO") or ""),
            "resposta_publica_dm_falha": str(recente["dados"].get("RESPOSTA_PUBLICA_DM_FALHA") or ""),
            "navegador": _mais_frequente([c["dados"].get("NAVEGADOR") or "" for c in grupo], "edge"),
            "perfil_navegador_subpasta": _mais_frequente(
                [c["dados"].get("PERFIL_NAVEGADOR_SUBPASTA") or "" for c in grupo],
                "Default",
            ),
            "criado_em": min(c["criado_em"] for c in grupo),
            "cookie_de": recente["id"],
            "li_at": str(recente["dados"].get("LINKEDIN_LI_AT") or ""),
            "jsessionid": str(recente["dados"].get("LINKEDIN_JSESSIONID") or ""),
            "cookies_distintos": len(
                {
                    hashlib.sha256(str(c["dados"].get("LINKEDIN_LI_AT") or "").encode()).hexdigest()
                    for c in grupo
                    if str(c["dados"].get("LINKEDIN_LI_AT") or "").strip()
                }
            ),
            "mensagens_distintas": len(set(textos)),
            "campanhas": len(grupo),
        }

    chaves = []
    for conta in contas:
        chave = str(conta["dados"].get("OPENAI_API_KEY") or "").strip()
        if chave:
            chaves.append((conta["mtime"], chave, conta["id"]))
    openai = ""
    openai_de = ""
    if chaves:
        chaves.sort(key=lambda item: item[0])
        openai_de = chaves[-1][2]
        openai = chaves[-1][1]

    return {
        "contas": contas,
        "perfis": perfis,
        "desconhecidas": desconhecidas,
        "openai": openai,
        "openai_de": openai_de,
        "openai_distintas": len({item[1] for item in chaves}),
    }


def _linhas_historico(conta_id: str) -> list[tuple]:
    caminho = _AQUI / f"respondidos_history_{conta_id}.json"
    dados = _ler_json(caminho)
    if not isinstance(dados, dict):
        return []
    linhas = []
    for chave_arquivo, entrada in dados.items():
        if isinstance(entrada, list):
            comentarios, perfis, url, quando = list(entrada), [], "", None
        elif isinstance(entrada, dict):
            comentarios = list(entrada.get("comentarios") or [])
            perfis = list(entrada.get("perfis") or [])
            url = str(entrada.get("url") or "")
            quando = entrada.get("atualizado_em") or None
        else:
            continue
        url_chave = historico.chave_url_post(url) if url else str(chave_arquivo)
        for tipo, valores in (("comentario", comentarios), ("perfil", perfis)):
            for valor in valores:
                valor = str(valor or "").strip()
                if not valor:
                    continue
                linhas.append((conta_id, url_chave, url, tipo, valor, quando))
    return linhas


def _linhas_metricas(conta_id: str) -> tuple[list[tuple], list[tuple]]:
    caminho = _AQUI / f"historico_metricas_{conta_id}.json"
    dados = _ler_json(caminho)
    if not isinstance(dados, dict):
        return [], []
    eventos = []
    for evento in dados.get("events") or []:
        if not isinstance(evento, dict):
            continue
        ts = str(evento.get("ts") or "")
        try:
            quando = datetime.fromisoformat(ts)
        except Exception:
            quando = None
        if quando is None:
            continue
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        eventos.append(
            (
                conta_id,
                quando,
                quando.date(),
                str(evento.get("type") or "desconhecido"),
                int(evento.get("amount") or 1),
                str(evento.get("text") or ""),
                evento.get("duration_ms"),
            )
        )
    execucoes = []
    for run in dados.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            continue
        execucoes.append(
            (
                f"{conta_id}:{run_id}",
                conta_id,
                run.get("started_at") or None,
                run.get("ended_at") or None,
                int(run.get("duration_ms") or 0),
                str(run.get("mode") or ""),
                str(run.get("url_post") or ""),
                int(run.get("recognized") or 0),
                int(run.get("responded") or 0),
                int(run.get("edited") or 0),
                int(run.get("reposted_after_delete") or 0),
                int(run.get("skipped") or 0),
            )
        )
    return eventos, execucoes


def relatorio(pacote: dict) -> str:
    linhas = [
        f"Campanhas: {len(pacote['contas'])}",
        f"Sem perfil reconhecido: {len(pacote['desconhecidas'])}",
    ]
    if pacote["desconhecidas"]:
        linhas.append("  " + ", ".join(pacote["desconhecidas"]))
    for perfil in pacote["perfis"].values():
        linhas.append(
            f"Perfil {perfil['nome']}: {perfil['campanhas']} campanhas, "
            f"slug {perfil['slug_linkedin'] or '-'}, "
            f"cookie da campanha {perfil['cookie_de']} "
            f"({perfil['cookies_distintos']} cookies diferentes no histórico), "
            f"{perfil['mensagens_distintas']} variação(ões) de mensagem"
        )
    linhas.append(
        f"OpenAI: {'salva a partir de ' + pacote['openai_de'] if pacote['openai'] else 'nenhuma chave encontrada'}"
        + (f" ({pacote['openai_distintas']} distintas)" if pacote["openai"] else "")
    )
    comentarios = perfis = eventos = execucoes = 0
    for conta in pacote["contas"]:
        hist = _linhas_historico(conta["id"])
        ev, runs = _linhas_metricas(conta["id"])
        comentarios += sum(1 for linha in hist if linha[3] == "comentario")
        perfis += sum(1 for linha in hist if linha[3] == "perfil")
        eventos += len(ev)
        execucoes += len(runs)
    linhas.append(
        f"Histórico: {comentarios} comentários, {perfis} perfis respondidos"
    )
    linhas.append(f"Métricas: {eventos} eventos, {execucoes} execuções")
    return "\n".join(linhas)


def aplicar(pacote: dict, forcar: bool) -> None:
    if pacote["desconhecidas"]:
        raise SystemExit("Há campanhas sem perfil. Nada foi gravado.")
    repositorio.garantir_schema()
    with repositorio.conexao(direta=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM campanha")
            existentes = int(cur.fetchone()["n"])
            if existentes and not forcar:
                raise SystemExit(
                    f"O banco já tem {existentes} campanhas. Rode de novo com --forcar para substituir."
                )
            if forcar and existentes:
                cur.execute("DELETE FROM campanha")

            for perfil in pacote["perfis"].values():
                repositorio.upsert_perfil(cur, perfil)
                repositorio.salvar_sessao(perfil["id"], perfil["li_at"], perfil["jsessionid"])

            for conta in pacote["contas"]:
                dados = conta["dados"]
                repositorio.upsert_campanha(
                    cur,
                    {
                        "id": conta["id"],
                        "perfil_id": conta["perfil_id"],
                        "nome": str(dados.get("NOME_CONTA") or conta["id"]),
                        "url_post": str(dados.get("URL_POST") or ""),
                        "palavras_chave": str(dados.get("PALAVRAS_CHAVE") or ""),
                        "urls_extras": str(dados.get("URLS_EXTRAS") or ""),
                        "criado_em": conta["criado_em"],
                    },
                )

            historico_linhas = []
            eventos = []
            execucoes = []
            for conta in pacote["contas"]:
                historico_linhas.extend(_linhas_historico(conta["id"]))
                ev, runs = _linhas_metricas(conta["id"])
                eventos.extend(ev)
                execucoes.extend(runs)

            if historico_linhas:
                cur.executemany(
                    """
                    INSERT INTO historico_respondido
                        (campanha_id, url_chave, url_post, tipo, chave, criado_em)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
                    ON CONFLICT DO NOTHING
                    """,
                    historico_linhas,
                )
            if eventos:
                cur.executemany(
                    """
                    INSERT INTO evento_metrica
                        (campanha_id, ts, dia, tipo, amount, texto, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    eventos,
                )
            if execucoes:
                cur.executemany(
                    """
                    INSERT INTO execucao (
                        run_id, campanha_id, started_at, ended_at, duration_ms, mode,
                        url_post, recognized, responded, edited, reposted_after_delete, skipped
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    execucoes,
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()
    pacote = montar()
    print(relatorio(pacote))
    if not args.aplicar:
        print("\nSimulação. Nenhum dado foi gravado. Use --aplicar para gravar.")
        return
    aplicar(pacote, args.forcar)
    print("\nGravado.")
    for tabela, total in repositorio.contar().items():
        print(f"  {tabela}: {total}")


if __name__ == "__main__":
    main()
