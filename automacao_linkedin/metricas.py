import json
import os
import threading
import uuid
import webbrowser
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from typing import Any

_DIR_BASE = os.path.dirname(os.path.abspath(__file__))


def _sufixo_conta() -> str:
    """Sufixo do arquivo quando o processo roda no contexto de uma conta."""
    bruto = (os.environ.get("AUTOMACAO_CONTA_ID") or "").strip().lower()
    limpo = "".join(c if (c.isalnum() or c == "-") else "-" for c in bruto).strip("-")
    return f"_{limpo}" if limpo else ""


_SUFIXO = _sufixo_conta()

# Cada conta grava suas próprias métricas: rodar várias ao mesmo tempo não mistura números.
ARQUIVO_METRICAS = os.path.join(_DIR_BASE, f"historico_metricas{_SUFIXO}.json")
ARQUIVO_DASHBOARD = os.path.join(_DIR_BASE, f"metricas_dashboard{_SUFIXO}.html")
ARQUIVO_DASHBOARD_DATA = os.path.join(_DIR_BASE, f"metricas_dashboard_data{_SUFIXO}.json")
MAX_RUNS_HISTORICO = 300
_DASHBOARD_HTTP_SERVER: ThreadingHTTPServer | None = None
_DASHBOARD_HTTP_PORT: int | None = None

#region agent log
_DEBUG_LOG_PATH = os.path.join(os.getcwd(), "debug-4f8db9.log")
_DEBUG_SESSION_ID = "4f8db9"


def _debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    return
    try:
        payload = {
            "sessionId": _DEBUG_SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        fallback = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug-4f8db9.log")
        if fallback != _DEBUG_LOG_PATH:
            with open(fallback, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
#endregion


def _today_key() -> str:
    return date.today().isoformat()


def _base_data() -> dict[str, Any]:
    return {
        "version": 2,
        "events": [],
        "daily": {},
        "runs": [],
        "totals": {
            "recognized": 0,
            "responded": 0,
            "skipped": 0,
            "edited": 0,
            "reposted_after_delete": 0,
        },
    }


def _base_daily() -> dict[str, Any]:
    return {
        "recognized": 0,
        "responded": 0,
        "skipped": 0,
        "edited": 0,
        "reposted_after_delete": 0,
        "durations_ms": [],
        "messages": {},
    }


def _ensure_schema(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _base_data()

    # Migração do formato antigo
    if "daily" not in data and "respostas" in data:
        migrated = _base_data()
        for r in data.get("respostas", []):
            ts = r.get("data")
            txt = str(r.get("texto", ""))
            dkey = _today_key()
            try:
                dkey = datetime.fromisoformat(ts).date().isoformat()
            except Exception:
                pass
            day = migrated["daily"].setdefault(dkey, _base_daily())
            day["responded"] += 1
            if txt:
                day["messages"][txt] = day["messages"].get(txt, 0) + 1
            migrated["totals"]["responded"] += 1
        migrated["totals"]["skipped"] = int(data.get("pulados", 0))
        if migrated["daily"]:
            last_key = sorted(migrated["daily"].keys())[-1]
            migrated["daily"][last_key]["skipped"] += migrated["totals"]["skipped"]
        return migrated

    out = _base_data()
    out.update(data)

    if not isinstance(out.get("events"), list):
        out["events"] = []
    if not isinstance(out.get("daily"), dict):
        out["daily"] = {}
    if not isinstance(out.get("runs"), list):
        out["runs"] = []
    if not isinstance(out.get("totals"), dict):
        out["totals"] = _base_data()["totals"]

    for key in ("recognized", "responded", "skipped", "edited", "reposted_after_delete"):
        out["totals"][key] = int(out["totals"].get(key, 0))

    for day_key, day_val in list(out["daily"].items()):
        if not isinstance(day_val, dict):
            out["daily"][day_key] = _base_daily()
            continue
        d = _base_daily()
        d.update(day_val)
        d["recognized"] = int(d.get("recognized", 0))
        d["responded"] = int(d.get("responded", 0))
        d["skipped"] = int(d.get("skipped", 0))
        d["edited"] = int(d.get("edited", 0))
        d["reposted_after_delete"] = int(d.get("reposted_after_delete", 0))
        if not isinstance(d.get("durations_ms"), list):
            d["durations_ms"] = []
        d["durations_ms"] = [
            int(x)
            for x in d["durations_ms"]
            if isinstance(x, (int, float)) and x >= 0
        ]
        if not isinstance(d.get("messages"), dict):
            d["messages"] = {}
        out["daily"][day_key] = d

    sane_runs: list[dict[str, Any]] = []
    for run in out.get("runs", []):
        if not isinstance(run, dict):
            continue
        item = {
            "run_id": str(run.get("run_id", "")).strip() or uuid.uuid4().hex[:12],
            "started_at": str(run.get("started_at", "")),
            "ended_at": str(run.get("ended_at", "")),
            "duration_ms": int(run.get("duration_ms", 0) or 0),
            "mode": str(run.get("mode", "") or ""),
            "url_post": str(run.get("url_post", "") or ""),
            "recognized": int(run.get("recognized", 0) or 0),
            "responded": int(run.get("responded", 0) or 0),
            "edited": int(run.get("edited", 0) or 0),
            "reposted_after_delete": int(run.get("reposted_after_delete", 0) or 0),
            "skipped": int(run.get("skipped", 0) or 0),
            "_start_totals": run.get("_start_totals", {}) if isinstance(run.get("_start_totals"), dict) else {},
        }
        sane_runs.append(item)
    out["runs"] = sane_runs[-MAX_RUNS_HISTORICO:]

    return out


def get_credenciais_nuvem():
    """
    Credenciais da sincronização em nuvem, quando ligada na conta.

    Fica desligada por padrão: cada evento faria uma ida ao Supabase (lento) e
    contas diferentes com a mesma campanha sobrescreveriam as métricas umas das outras.
    """
    try:
        from settings_manager import load_settings

        settings = load_settings()
        if not settings.get("SINCRONIZAR_NUVEM", False):
            return None, None
        pin = settings.get("PIN_ATUAL", "")
        # Sem nome próprio, usa o nome da conta para não misturar com outra campanha.
        nome = settings.get("NOME_CAMPANHA_ATUAL", "") or settings.get("NOME_CONTA", "")
        if pin and nome:
            return pin, nome
    except Exception:
        pass
    return None, None

def carregar_metricas() -> dict[str, Any]:
    pin, nome = get_credenciais_nuvem()
    if pin and nome:
        from database import carregar_metricas_nuvem
        nuvem = carregar_metricas_nuvem(pin, nome)
        if nuvem:
            # Mantém backup local atualizado
            salvar_metricas_local(nuvem)
            return _ensure_schema(nuvem)
        else:
            # Se não tem nada na nuvem para esse usuário/campanha, zera as métricas
            base = _base_data()
            salvar_metricas_local(base)
            return base

    # Fallback apenas se estiver rodando sem autenticação (testes rápidos)
    if not os.path.exists(ARQUIVO_METRICAS):
        return _base_data()
    try:
        with open(ARQUIVO_METRICAS, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return _ensure_schema(raw)
    except Exception:
        return _base_data()

def carregar_metricas_arquivo(caminho: str) -> dict[str, Any]:
    """Lê métricas de um arquivo específico (a GUI usa um por conta)."""
    if not caminho or not os.path.exists(caminho):
        return _base_data()
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return _ensure_schema(json.load(f))
    except Exception:
        return _base_data()


def _campanha_ativa() -> str:
    try:
        from settings_manager import conta_ativa_id

        return conta_ativa_id()
    except Exception:
        return ""


def resumo_do_dia(dados: dict[str, Any] | None = None, caminho: str = "") -> dict[str, int]:
    """Contadores do dia atual, prontos para exibir na interface."""
    campanha_id = _campanha_ativa()
    if campanha_id and dados is None:
        import repositorio

        return repositorio.resumo_do_dia(campanha_id)
    if dados is None:
        dados = carregar_metricas_arquivo(caminho) if caminho else carregar_metricas()
    dia = (dados.get("daily") or {}).get(_today_key(), {})
    return {
        "respondidos": int(dia.get("responded", 0) or 0),
        "reconhecidos": int(dia.get("recognized", 0) or 0),
        "editados": int(dia.get("edited", 0) or 0),
        "pulados": int(dia.get("skipped", 0) or 0),
    }


def salvar_metricas_local(dados: dict[str, Any]) -> None:
    with open(ARQUIVO_METRICAS, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

def salvar_metricas(dados: dict[str, Any]) -> None:
    salvar_metricas_local(dados)
    pin, nome = get_credenciais_nuvem()
    if pin and nome:
        # Roda em thread para não travar o bot
        from database import salvar_metricas_nuvem
        threading.Thread(target=salvar_metricas_nuvem, args=(pin, nome, dados), daemon=True).start()


def _snapshot_totais(dados: dict[str, Any]) -> dict[str, int]:
    totals = dados.get("totals", {})
    return {
        "recognized": int(totals.get("recognized", 0) or 0),
        "responded": int(totals.get("responded", 0) or 0),
        "edited": int(totals.get("edited", 0) or 0),
        "reposted_after_delete": int(totals.get("reposted_after_delete", 0) or 0),
        "skipped": int(totals.get("skipped", 0) or 0),
    }


def _dashboard_http_port() -> int:
    try:
        import config  # type: ignore

        return max(1024, int(getattr(config, "DASHBOARD_SERVER_PORT", 8765)))
    except Exception:
        return 8765


def _garantir_dashboard_http_server() -> int:
    global _DASHBOARD_HTTP_SERVER, _DASHBOARD_HTTP_PORT
    if _DASHBOARD_HTTP_SERVER is not None and _DASHBOARD_HTTP_PORT is not None:
        return _DASHBOARD_HTTP_PORT

    base_dir = os.path.dirname(__file__)
    porta_base = _dashboard_http_port()
    for porta in (porta_base, porta_base + 1, porta_base + 2):
        try:
            class QuietHandler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=base_dir, **kwargs)

                def log_message(self, format, *args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", porta), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _DASHBOARD_HTTP_SERVER = server
            _DASHBOARD_HTTP_PORT = porta
            return porta
        except OSError:
            continue
    raise RuntimeError("Não foi possível iniciar servidor HTTP do dashboard.")


def abrir_dashboard() -> str:
    if _campanha_ativa():
        return ""
    path = salvar_dashboard_html()
    uri = Path(path).resolve().as_uri()
    try:
        porta = _garantir_dashboard_http_server()
        url = f"http://127.0.0.1:{porta}/metricas_dashboard.html"
        opened = bool(webbrowser.open(url, new=2))
        _debug_log(
            run_id="repro",
            hypothesis_id="H4",
            location="metricas.py:abrir_dashboard",
            message="webbrowser_open_result",
            data={"opened": opened, "url": url},
        )
        if opened:
            return path
    except Exception:
        pass
    try:
        # Fallback mais confiável no Windows para abrir arquivo local.
        os.startfile(Path(path).resolve())  # type: ignore[attr-defined]
        _debug_log(
            run_id="repro",
            hypothesis_id="H4",
            location="metricas.py:abrir_dashboard",
            message="startfile_open_result",
            data={"opened": True},
        )
    except Exception:
        _debug_log(
            run_id="repro",
            hypothesis_id="H4",
            location="metricas.py:abrir_dashboard",
            message="startfile_open_result",
            data={"opened": False},
        )
    return path


def iniciar_execucao(mode: str, url_post: str) -> str:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        return repositorio.iniciar_execucao(campanha_id, mode, url_post)
    dados = carregar_metricas()
    run_id = uuid.uuid4().hex[:12]
    dados["runs"].append(
        {
            "run_id": run_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "ended_at": "",
            "duration_ms": 0,
            "mode": str(mode or ""),
            "url_post": str(url_post or ""),
            "recognized": 0,
            "responded": 0,
            "edited": 0,
            "reposted_after_delete": 0,
            "skipped": 0,
            "_start_totals": _snapshot_totais(dados),
        }
    )
    dados["runs"] = dados["runs"][-MAX_RUNS_HISTORICO:]
    salvar_metricas(dados)
    salvar_dashboard_html(dados)
    return run_id


def finalizar_execucao(run_id: str, mode: str = "", url_post: str = "") -> bool:
    if _campanha_ativa():
        import repositorio

        return repositorio.finalizar_execucao(run_id, mode, url_post)
    dados = carregar_metricas()
    run = None
    for item in reversed(dados.get("runs", [])):
        if str(item.get("run_id")) == str(run_id):
            run = item
            break
    if run is None:
        return False

    ended = datetime.now()
    started_raw = str(run.get("started_at", "") or "")
    try:
        started = datetime.fromisoformat(started_raw)
    except Exception:
        started = ended
    duration_ms = max(0, int((ended - started).total_seconds() * 1000))

    snap = _snapshot_totais(dados)
    start_totals = run.get("_start_totals", {}) if isinstance(run.get("_start_totals"), dict) else {}
    run["recognized"] = max(0, snap["recognized"] - int(start_totals.get("recognized", 0) or 0))
    run["responded"] = max(0, snap["responded"] - int(start_totals.get("responded", 0) or 0))
    run["edited"] = max(0, snap["edited"] - int(start_totals.get("edited", 0) or 0))
    run["reposted_after_delete"] = max(
        0, snap["reposted_after_delete"] - int(start_totals.get("reposted_after_delete", 0) or 0)
    )
    run["skipped"] = max(0, snap["skipped"] - int(start_totals.get("skipped", 0) or 0))
    run["ended_at"] = ended.isoformat(timespec="seconds")
    run["duration_ms"] = duration_ms
    if mode:
        run["mode"] = str(mode)
    if url_post:
        run["url_post"] = str(url_post)
    run.pop("_start_totals", None)

    salvar_metricas(dados)
    salvar_dashboard_html(dados)
    _debug_log(
        run_id=run_id,
        hypothesis_id="H5",
        location="metricas.py:finalizar_execucao",
        message="run_snapshot_saved",
        data={"duration_ms": duration_ms, "recognized": run["recognized"], "responded": run["responded"]},
    )
    return True


def _append_event(
    dados: dict[str, Any],
    event_type: str,
    amount: int = 1,
    text: str = "",
    duration_ms: int | None = None,
) -> None:
    amount = max(1, int(amount))
    dkey = _today_key()
    day = dados["daily"].setdefault(dkey, _base_daily())

    if event_type == "recognized":
        day["recognized"] += amount
        dados["totals"]["recognized"] += amount
    elif event_type == "responded":
        day["responded"] += amount
        dados["totals"]["responded"] += amount
        if text:
            day["messages"][text] = day["messages"].get(text, 0) + amount
        if duration_ms is not None:
            day["durations_ms"].append(max(0, int(duration_ms)))
    elif event_type == "edited":
        day["edited"] += amount
        dados["totals"]["edited"] += amount
        if text:
            day["messages"][text] = day["messages"].get(text, 0) + amount
        if duration_ms is not None:
            day["durations_ms"].append(max(0, int(duration_ms)))
    elif event_type == "reposted_after_delete":
        day["reposted_after_delete"] += amount
        dados["totals"]["reposted_after_delete"] += amount
        if text:
            day["messages"][text] = day["messages"].get(text, 0) + amount
        if duration_ms is not None:
            day["durations_ms"].append(max(0, int(duration_ms)))
    elif event_type == "skipped":
        day["skipped"] += amount
        dados["totals"]["skipped"] += amount

    dados["events"].append(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            "amount": amount,
            "text": text,
            "duration_ms": None if duration_ms is None else int(duration_ms),
        }
    )


def registrar_reconhecido(quantidade: int = 1) -> None:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_evento(campanha_id, "recognized", amount=quantidade)
        return
    dados = carregar_metricas()
    _append_event(dados, "recognized", amount=quantidade)
    salvar_metricas(dados)
    salvar_dashboard_html(dados)


def registrar_resposta(texto_resposta: str, duration_ms: int | None = None) -> None:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_evento(
            campanha_id, "responded", texto=texto_resposta, duration_ms=duration_ms
        )
        return
    dados = carregar_metricas()
    _append_event(
        dados,
        "responded",
        amount=1,
        text=texto_resposta,
        duration_ms=duration_ms,
    )
    salvar_metricas(dados)
    salvar_dashboard_html(dados)


def registrar_edicao(texto_novo: str, duration_ms: int | None = None) -> None:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_evento(campanha_id, "edited", texto=texto_novo, duration_ms=duration_ms)
        return
    dados = carregar_metricas()
    _append_event(dados, "edited", amount=1, text=texto_novo, duration_ms=duration_ms)
    salvar_metricas(dados)
    salvar_dashboard_html(dados)


def registrar_repost_apos_exclusao(
    texto_novo: str, duration_ms: int | None = None
) -> None:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_evento(
            campanha_id, "reposted_after_delete", texto=texto_novo, duration_ms=duration_ms
        )
        return
    dados = carregar_metricas()
    _append_event(
        dados,
        "reposted_after_delete",
        amount=1,
        text=texto_novo,
        duration_ms=duration_ms,
    )
    salvar_metricas(dados)
    salvar_dashboard_html(dados)


def registrar_pulado(quantidade: int = 1) -> None:
    campanha_id = _campanha_ativa()
    if campanha_id:
        import repositorio

        repositorio.registrar_evento(campanha_id, "skipped", amount=quantidade)
        return
    dados = carregar_metricas()
    _append_event(dados, "skipped", amount=quantidade)
    salvar_metricas(dados)
    salvar_dashboard_html(dados)


def _top_message(daily: dict[str, Any]) -> tuple[str, int]:
    merged: dict[str, int] = {}
    for day in daily.values():
        msgs = day.get("messages", {})
        for msg, cnt in msgs.items():
            merged[msg] = merged.get(msg, 0) + int(cnt)
    if not merged:
        return ("-", 0)
    msg, cnt = max(merged.items(), key=lambda x: x[1])
    return (msg, cnt)


def obter_estatisticas() -> dict[str, Any]:
    dados = carregar_metricas()
    daily = dados.get("daily", {})
    all_durations: list[int] = []
    for d in daily.values():
        all_durations.extend(d.get("durations_ms", []))

    top_msg, top_cnt = _top_message(daily)
    return {
        "recognized_total": int(dados["totals"].get("recognized", 0)),
        "responded_total": int(dados["totals"].get("responded", 0)),
        "edited_total": int(dados["totals"].get("edited", 0)),
        "reposted_after_delete_total": int(
            dados["totals"].get("reposted_after_delete", 0)
        ),
        "skipped_total": int(dados["totals"].get("skipped", 0)),
        "avg_time_ms": int(mean(all_durations)) if all_durations else 0,
        "top_message": top_msg,
        "top_message_count": top_cnt,
        "days_count": len(daily),
    }


def _build_dashboard_payload(dados: dict[str, Any]) -> dict[str, Any]:
    daily_rows = []
    for day_key in sorted(dados.get("daily", {}).keys()):
        d = dados["daily"][day_key]
        durations = d.get("durations_ms", [])
        avg_ms = int(mean(durations)) if durations else 0
        daily_rows.append(
            {
                "date": day_key,
                "recognized": int(d.get("recognized", 0)),
                "responded": int(d.get("responded", 0)),
                "edited": int(d.get("edited", 0)),
                "reposted_after_delete": int(d.get("reposted_after_delete", 0)),
                "skipped": int(d.get("skipped", 0)),
                "avg_ms": avg_ms,
            }
        )
    top_msg, top_cnt = _top_message(dados.get("daily", {}))
    recent_runs = []
    for run in dados.get("runs", [])[-10:]:
        if not isinstance(run, dict):
            continue
        recent_runs.append(
            {
                "run_id": str(run.get("run_id", "")),
                "started_at": str(run.get("started_at", "")),
                "ended_at": str(run.get("ended_at", "")),
                "duration_ms": int(run.get("duration_ms", 0) or 0),
                "mode": str(run.get("mode", "") or ""),
                "url_post": str(run.get("url_post", "") or ""),
                "recognized": int(run.get("recognized", 0) or 0),
                "responded": int(run.get("responded", 0) or 0),
                "edited": int(run.get("edited", 0) or 0),
                "reposted_after_delete": int(run.get("reposted_after_delete", 0) or 0),
                "skipped": int(run.get("skipped", 0) or 0),
            }
        )
    return {
        "totals": dados.get("totals", {}),
        "top_message": {"text": top_msg, "count": top_cnt},
        "daily": daily_rows,
        "runs": recent_runs,
    }


def _ui_config() -> dict[str, Any]:
    # Import local para evitar dependência obrigatória de config em usos isolados.
    try:
        import config  # type: ignore

        refresh_enabled = bool(getattr(config, "DASHBOARD_AUTO_REFRESH_ATIVO", True))
        refresh_seconds = max(3, int(getattr(config, "DASHBOARD_AUTO_REFRESH_SEGUNDOS", 5)))
    except Exception:
        refresh_enabled = True
        refresh_seconds = 5
    return {
        "refresh_enabled": refresh_enabled,
        "refresh_seconds": refresh_seconds,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def salvar_dashboard_data_json(dados: dict[str, Any] | None = None) -> str:
    dados = _ensure_schema(dados or carregar_metricas())
    payload = _build_dashboard_payload(dados)
    payload["ui"] = _ui_config()
    with open(ARQUIVO_DASHBOARD_DATA, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return ARQUIVO_DASHBOARD_DATA


def gerar_dashboard_html(dados: dict[str, Any] | None = None) -> str:
    ui = _ui_config()
    refresh_enabled = "true" if ui["refresh_enabled"] else "false"
    refresh_ms = max(3000, int(ui["refresh_seconds"]) * 1000)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Métricas da Automação LinkedIn</title>
  <style>
    :root {{
      --bg:#0b1020; --card:#11182e; --muted:#9fb0d3; --text:#eef3ff;
      --ok:#36c98f; --warn:#ffb84d; --danger:#ff6b6b; --info:#58a6ff;
      --border:#213056;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif; background:var(--bg); color:var(--text); }}
    .wrap {{ max-width:1100px; margin:24px auto; padding:0 16px; }}
    h1 {{ margin:0 0 16px; font-size:24px; }}
    .grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }}
    .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px; }}
    .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
    .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
    .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .danger {{ color:var(--danger); }} .info {{ color:var(--info); }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; background:var(--card); border:1px solid var(--border); border-radius:12px; overflow:hidden; }}
    th,td {{ padding:10px 12px; border-bottom:1px solid var(--border); text-align:left; font-size:14px; }}
    th {{ color:var(--muted); font-weight:600; background:#101733; }}
    tr:last-child td {{ border-bottom:none; }}
    .top {{ margin-top:12px; color:var(--muted); }}
    .empty {{ color:var(--muted); padding:12px; }}
    .chart-wrap {{ margin-top:12px; background:var(--card); border:1px solid var(--border); border-radius:12px; padding:12px; }}
    .chart-title {{ margin:0 0 8px; color:var(--muted); font-size:13px; }}
    canvas {{ width:100%; max-width:100%; height:250px; display:block; }}
    .toolbar {{ margin-top:8px; display:flex; gap:10px; align-items:center; color:var(--muted); font-size:12px; }}
    .toolbar button {{ background:#101733; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:6px 10px; cursor:pointer; }}
    .error {{ margin-top:10px; color:#ffd2d2; background:#4a1f1f; border:1px solid #7a2c2c; border-radius:8px; padding:8px 10px; display:none; white-space:pre-wrap; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Métricas da Automação</h1>
    <div id="cards" class="grid"></div>
    <div class="top" id="topMsg"></div>
    <div class="toolbar">
      <span id="generatedAt"></span>
      <button id="btnRefresh" type="button">Atualizar agora</button>
    </div>
    <div id="errorBox" class="error"></div>
    <div class="chart-wrap">
      <div class="chart-title">Evolução diária (respondidos, editados, repostados, pulados)</div>
      <canvas id="dailyChart" width="1080" height="250"></canvas>
    </div>
    <table>
      <thead>
        <tr>
          <th>Data</th>
          <th>Reconhecidos</th>
          <th>Respondidos</th>
          <th>Editados</th>
          <th>Repostados</th>
          <th>Pulados</th>
          <th>Tempo médio (ms)</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>

    <h1 style="margin-top:20px;">Histórico de execuções (últimas 10)</h1>
    <table>
      <thead>
        <tr>
          <th>Início</th>
          <th>Fim</th>
          <th>Duração (s)</th>
          <th>Modo</th>
          <th>Reconh.</th>
          <th>Resp.</th>
          <th>Edit.</th>
          <th>Repost.</th>
          <th>Pul.</th>
        </tr>
      </thead>
      <tbody id="runsTbody"></tbody>
    </table>
  </div>
  <script>
    const DATA_URL = "metricas_dashboard_data.json";
    const AUTO_REFRESH = {refresh_enabled};
    const REFRESH_MS = {refresh_ms};

    function showError(msg) {{
      const box = document.getElementById("errorBox");
      if (!box) return;
      box.style.display = "block";
      box.textContent = String(msg || "Erro desconhecido no dashboard.");
    }}
    function renderCards(data) {{
      const cards = [
        ["Reconhecidos", data?.totals?.recognized || 0, "info"],
        ["Respondidos", data?.totals?.responded || 0, "ok"],
        ["Editados", data?.totals?.edited || 0, "warn"],
        ["Repostados (após excluir)", data?.totals?.reposted_after_delete || 0, "warn"],
        ["Pulados", data?.totals?.skipped || 0, "danger"],
      ];
      const cardsEl = document.getElementById("cards");
      if (!cardsEl) return;
      cardsEl.innerHTML = "";
      for (const [label, value, klass] of cards) {{
        const div = document.createElement("div");
        div.className = "card";
        div.innerHTML = `<div class="label">${{label}}</div><div class="value ${{klass}}">${{value}}</div>`;
        cardsEl.appendChild(div);
      }}
    }}

    function renderTopMessage(data) {{
      const ui = data?.ui || {{}};
      const generatedAt = document.getElementById("generatedAt");
      if (generatedAt) generatedAt.textContent = `Atualizado em: ${{ui.generated_at || "-"}}`;
      const top = data?.top_message || {{ text: "-", count: 0 }};
      const topEl = document.getElementById("topMsg");
      if (topEl) topEl.textContent = `Mensagem mais usada: "${{top.text || "-"}}" (${{top.count || 0}}x)`;
    }}

    function drawDailyChart(rows) {{
      try {{
        const canvas = document.getElementById("dailyChart");
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) {{
          showError("Não foi possível inicializar contexto 2D do gráfico.");
          return;
        }}
        const w = canvas.width;
        const h = canvas.height;
        const padL = 42, padR = 18, padT = 16, padB = 28;
        const plotW = w - padL - padR;
        const plotH = h - padT - padB;
        ctx.clearRect(0, 0, w, h);
        ctx.strokeStyle = "#2b3a62";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, padT);
        ctx.lineTo(padL, h - padB);
        ctx.lineTo(w - padR, h - padB);
        ctx.stroke();
        if (!rows || rows.length === 0) {{
          ctx.fillStyle = "#9fb0d3";
          ctx.font = "13px Segoe UI, Arial";
          ctx.fillText("Sem dados para o gráfico ainda.", padL + 8, padT + 20);
          return;
        }}
        const points = rows.slice(-14);
        const maxVal = Math.max(
          1,
          ...points.map(r => Number(r.responded || 0)),
          ...points.map(r => Number(r.edited || 0)),
          ...points.map(r => Number(r.reposted_after_delete || 0)),
          ...points.map(r => Number(r.skipped || 0))
        );
        function xAt(i) {{
          return padL + (i * (plotW / Math.max(1, points.length - 1)));
        }}
        function yAt(v) {{
          return padT + (plotH - (Number(v || 0) / maxVal) * plotH);
        }}
        function drawSerie(key, color) {{
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.beginPath();
          for (let i = 0; i < points.length; i++) {{
            const x = xAt(i);
            const y = yAt(points[i][key]);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          }}
          ctx.stroke();
        }}
        drawSerie("responded", "#36c98f");
        drawSerie("edited", "#ffb84d");
        drawSerie("reposted_after_delete", "#58a6ff");
        drawSerie("skipped", "#ff6b6b");
      }} catch (err) {{
        showError(`Falha no gráfico: ${{String(err)}}`);
      }}
    }}

    function renderDailyTable(data) {{
      const tbody = document.getElementById("tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      const rows = data?.daily || [];
      if (!rows.length) {{
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="7" class="empty">Sem dados ainda.</td>';
        tbody.appendChild(tr);
        return;
      }}
      for (const row of [...rows].reverse()) {{
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${{row.date}}</td>
          <td>${{row.recognized}}</td>
          <td>${{row.responded}}</td>
          <td>${{row.edited}}</td>
          <td>${{row.reposted_after_delete}}</td>
          <td>${{row.skipped}}</td>
          <td>${{row.avg_ms}}</td>
        `;
        tbody.appendChild(tr);
      }}
    }}

    function renderRunsTable(data) {{
      const runsTbody = document.getElementById("runsTbody");
      if (!runsTbody) return;
      runsTbody.innerHTML = "";
      const runs = data?.runs || [];
      if (!runs.length) {{
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="9" class="empty">Sem histórico de execução ainda.</td>';
        runsTbody.appendChild(tr);
        return;
      }}
      for (const run of [...runs].reverse()) {{
        const tr = document.createElement("tr");
        const durSec = Math.round((Number(run.duration_ms || 0) / 1000) * 10) / 10;
        tr.innerHTML = `
          <td>${{run.started_at || "-"}}</td>
          <td>${{run.ended_at || "-"}}</td>
          <td>${{durSec}}</td>
          <td>${{run.mode || "-"}}</td>
          <td>${{run.recognized || 0}}</td>
          <td>${{run.responded || 0}}</td>
          <td>${{run.edited || 0}}</td>
          <td>${{run.reposted_after_delete || 0}}</td>
          <td>${{run.skipped || 0}}</td>
        `;
        runsTbody.appendChild(tr);
      }}
    }}

    function renderAll(data) {{
      renderCards(data);
      renderTopMessage(data);
      drawDailyChart(data?.daily || []);
      renderDailyTable(data);
      renderRunsTable(data);
    }}

    async function refreshNow() {{
      try {{
        const res = await fetch(`${{DATA_URL}}?v=${{Date.now()}}`, {{ cache: "no-store" }});
        if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
        const data = await res.json();
        renderAll(data);
      }} catch (err) {{
        showError(`Falha ao atualizar dashboard: ${{String(err)}}`);
      }}
    }}

    const cardsEl = document.getElementById("cards");
    if (cardsEl) cardsEl.innerHTML = "";
    refreshNow();
    if (AUTO_REFRESH) {{
      window.setInterval(refreshNow, REFRESH_MS);
    }}
    const btnRefresh = document.getElementById("btnRefresh");
    if (btnRefresh) btnRefresh.addEventListener("click", refreshNow);
  </script>
</body>
</html>
"""


def salvar_dashboard_html(dados: dict[str, Any] | None = None) -> str:
    if _campanha_ativa():
        return ""
    dados = _ensure_schema(dados or carregar_metricas())
    salvar_dashboard_data_json(dados)
    html = gerar_dashboard_html(dados)
    with open(ARQUIVO_DASHBOARD, "w", encoding="utf-8") as f:
        f.write(html)
    return ARQUIVO_DASHBOARD
