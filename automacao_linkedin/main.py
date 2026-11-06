import asyncio
import hashlib
import importlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from time import perf_counter
from typing import Optional

from playwright.async_api import async_playwright

import config
import historico
from modos._shared.settings_loader import (
    aplicar_overrides_config,
    carregar_overrides_modo,
)

try:
    import metricas
except ImportError:
    metricas = None  # type: ignore

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

#region agent log
_DEBUG_LOG_PATH = os.path.join(os.getcwd(), "debug-4f8db9.log")
_DEBUG_SESSION_ID = "4f8db9"
_AGENT_DEBUG_LOG_PATH = os.path.join(os.getcwd(), "debug-dea245.log")
_AGENT_DEBUG_SESSION_ID = "dea245"


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


def _agent_debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    return
    try:
        safe_data = {}
        for k, v in (data or {}).items():
            try:
                json.dumps(v, ensure_ascii=True)
                safe_data[k] = v
            except Exception:
                safe_data[k] = repr(v)
        payload = {
            "sessionId": _AGENT_DEBUG_SESSION_ID,
            "id": f"log_{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:8]}",
            "timestamp": int(datetime.now().timestamp() * 1000),
            "location": location,
            "message": message,
            "data": safe_data,
            "runId": run_id or "none",
            "hypothesisId": hypothesis_id,
        }
        with open(_AGENT_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        fallback = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug-dea245.log")
        if fallback != _AGENT_DEBUG_LOG_PATH:
            with open(fallback, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass
#endregion


def _erro_target_fechado(exc: Exception) -> bool:
    return "Target page, context or browser has been closed" in str(exc)


def _pagina_fechada(page) -> bool:
    try:
        return page.is_closed()
    except Exception:
        return True


# ============================================================
# PERSISTÊNCIA DE HISTÓRICO DE RESPONDIDOS (ver historico.py)
# ============================================================

def _resolver_historico_path() -> str:
    """Arquivo de histórico desta conta (cada conta tem o seu)."""
    try:
        from settings_manager import caminho_historico_conta, conta_ativa_id

        return caminho_historico_conta(conta_ativa_id())
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "respondidos_history.json")


_HISTORICO_PATH = _resolver_historico_path()


def _carregar_historico_post(url_post: str) -> tuple[set, set]:
    """(comentários respondidos, perfis respondidos) já salvos para este post."""
    try:
        comentarios, perfis = historico.carregar_post(_HISTORICO_PATH, url_post)
    except Exception as e:
        print(f"[ERRO] Não foi possível carregar o histórico. A automação para para não responder de novo quem já foi atendido: {e}")
        raise
    if comentarios or perfis:
        print(
            f"[HISTÓRICO] {len(comentarios)} comentário(s) e {len(perfis)} perfil(is) "
            "já respondidos neste post — não serão repetidos."
        )
    return comentarios, perfis


def _carregar_historico_respondidos(url_post: str) -> set:
    """Compatibilidade: só as chaves de comentário."""
    comentarios, _ = _carregar_historico_post(url_post)
    return comentarios


def _salvar_historico_respondidos(
    url_post: str, chave_comentario: str, perfil_key: str = ""
) -> None:
    """Registra no disco, na hora, que este comentário/perfil já foi respondido."""
    try:
        historico.registrar(_HISTORICO_PATH, url_post, chave_comentario, perfil_key)
    except Exception as e:
        print(f"[AVISO] Não foi possível salvar histórico de respondidos: {e}")


def _limpar_historico_url(url_post: str) -> None:
    """Remove o histórico de um post específico (permite responder todos de novo)."""
    try:
        historico.limpar_post(_HISTORICO_PATH, url_post)
    except Exception:
        pass


def _salvar_dashboard_execucao(tag: str) -> None:
    if metricas is None:
        return
    try:
        path = metricas.salvar_dashboard_html()
        print(f"[Info] Dashboard salvo ({tag}): {path}")
    except OSError as e:
        print(f"[AVISO] Falha ao salvar dashboard ({tag}): {e}")


def _aplicar_config_modo_em_runtime() -> tuple[str, str]:
    dm_on = bool(getattr(config, "ATIVAR_AUTOMACAO_DM_COM_COMENTARIO", False))
    comentario_on = bool(getattr(config, "ATIVAR_AUTOMACAO_COMENTARIO_SIMPLES", False))

    if dm_on and not comentario_on:
        setattr(config, "MODO_AUTOMACAO", "dm_com_comentario")
    elif comentario_on and not dm_on:
        setattr(config, "MODO_AUTOMACAO", "comentario_simples")
    elif dm_on and comentario_on:
        # Ambas ativas: prioridade operacional para DM neste processo.
        # Comentário simples pode rodar em outra execução/aba dedicada.
        setattr(config, "MODO_AUTOMACAO", "dm_com_comentario")

    overrides, source = carregar_overrides_modo(config)
    if overrides:
        aplicar_overrides_config(config, overrides)

    # O que o usuário definiu na tela da conta vence os valores padrão do preset.
    overrides_conta = dict(getattr(config, "OVERRIDES_DA_CONTA", {}) or {})
    if overrides_conta:
        aplicar_overrides_config(config, overrides_conta)

    modo = str(source.get("modo") or getattr(config, "MODO_AUTOMACAO", "dm_com_comentario"))
    preset = str(source.get("preset") or getattr(config, "PRESET_AUTOMACAO", "default"))
    return modo, preset


def _validar_config_campanha(modo: str, preset: str) -> bool:
    obrigatorias_base = ["URL_POST", "PALAVRAS_CHAVE", "MAX_RESPOSTAS_POR_RODADA"]
    obrigatorias_dm = [
        "RESPOSTA_PUBLICA_DM_SUCESSO",
        "RESPOSTA_PUBLICA_DM_FALHA",
        "MENSAGEM_PERFIL_TEMPLATE",
        "MAX_ENVIO_PERFIL_POR_RODADA",
    ]
    obrigatorias_comentario = ["RESPOSTAS"]

    faltando = [k for k in obrigatorias_base if not hasattr(config, k)]
    if modo == "dm_com_comentario":
        faltando.extend([k for k in obrigatorias_dm if not hasattr(config, k)])
    if modo == "comentario_simples":
        faltando.extend([k for k in obrigatorias_comentario if not hasattr(config, k)])

    obrigatorias_ia = ["IA_SYSTEM_PROMPT", "IA_USER_TEMPLATE"]
    if modo == "comentario_ia":
        faltando.extend([k for k in obrigatorias_ia if not hasattr(config, k)])

    tem_intervalos = hasattr(config, "INTERVALOS")
    tem_min_max = hasattr(config, "INTERVALO_MIN_SEGUNDOS") and hasattr(
        config, "INTERVALO_MAX_SEGUNDOS"
    )
    if not (tem_intervalos or tem_min_max):
        faltando.append("INTERVALO_MIN_SEGUNDOS/INTERVALO_MAX_SEGUNDOS")

    if faltando:
        lista = ", ".join(sorted(set(faltando)))
        print(
            f"[ERRO] Preset inválido ({modo}/{preset}). Faltam chaves obrigatórias: {lista}."
        )
        return False
    return True


def _sortear_intervalo_segundos() -> int:
    """
    Prioriza intervalo por faixa (min/max). Mantém compatibilidade com lista INTERVALOS.
    Aceita 0 = sem espera (vai direto para o próximo comentário).
    """
    if hasattr(config, "INTERVALO_MIN_SEGUNDOS") and hasattr(config, "INTERVALO_MAX_SEGUNDOS"):
        try:
            nmin = int(getattr(config, "INTERVALO_MIN_SEGUNDOS", 5))
            nmax = int(getattr(config, "INTERVALO_MAX_SEGUNDOS", nmin))
            if nmax < nmin:
                nmin, nmax = nmax, nmin
            nmin = max(0, nmin)
            nmax = max(nmin, nmax)
            return random.randint(nmin, nmax)
        except Exception:
            pass

    intervalos = list(getattr(config, "INTERVALOS", []) or [])
    if intervalos:
        try:
            return max(0, int(random.choice(intervalos)))
        except Exception:
            pass
    return 5


async def _texto_do_editor_contem(elemento, esperado: str) -> bool:
    """
    O texto da resposta está no editor? A menção que o LinkedIn já coloca no
    cartão (o nome da pessoa) não conta como digitação bem-sucedida.
    """
    try:
        inner = await elemento.evaluate("(el) => (el.innerText || el.textContent || '')")
    except Exception:
        return False
    atual = _norm_texto_simples(inner)
    alvo = _norm_texto_simples(esperado)
    if not alvo:
        return len(atual) > 0
    return alvo[:40] in atual


async def digitar_humano(
    elemento, texto: str, *, preservar_mencao: bool = False
) -> None:
    """
    Digita texto no editor de comentário/resposta do LinkedIn.
    Usa múltiplas estratégias porque o editor TipTap/ProseMirror do LinkedIn
    frequentemente rejeita fill(), press_sequentially() e type() do Playwright.

    A ordem é: clique + cursor depois da menção + insertText → keyboard.type →
    type() do elemento → injeção no DOM → fill(). Cada tentativa só é aceita
    quando o texto da resposta aparece no editor.
    """
    dmin = max(1, int(getattr(config, "DIGITACAO_DELAY_MIN_MS", 10)))
    dmax = max(dmin, int(getattr(config, "DIGITACAO_DELAY_MAX_MS", 35)))
    instantanea = getattr(config, "DIGITACAO_INSTANTANEA", True)

    corpo = texto if not preservar_mencao or texto.startswith((" ", "\n")) else f" {texto}"

    # Obtém o objeto page a partir do elemento (necessário para page.keyboard)
    try:
        page = elemento.page
    except Exception:
        page = None

    async def _cursor_depois_da_mencao() -> bool:
        """Foca o editor e leva o cursor para o fim, preservando a menção."""
        try:
            await elemento.evaluate("""(el) => {
                el.focus();
                const range = document.createRange();
                range.selectNodeContents(el);
                range.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }""")
            return True
        except Exception:
            return False

    # ===== ESTRATÉGIA 1: Clique no editor + insertText =====
    # O clique real é o que faz o LinkedIn assumir o campo do cartão de resposta;
    # insertText entra no editor mesmo quando ele ignora eventos de tecla.
    try:
        if page:
            await _clique_seguro(page, elemento, timeout_ms=3000)
        await _cursor_depois_da_mencao()
        await asyncio.sleep(0.2)

        if page:
            await page.keyboard.insert_text(corpo)
            await asyncio.sleep(0.25)
            if await _texto_do_editor_contem(elemento, corpo):
                print("[DIGITAÇÃO] ✅ Estratégia 1 (clique + insertText) funcionou.")
                return
            print(
                "[DIGITAÇÃO] ⚠️ Estratégia 1 não deixou o texto no editor. "
                "Tentando próxima..."
            )
        else:
            print("[DIGITAÇÃO] ⚠️ Não consegui obter page do elemento. Tentando próxima...")
    except Exception as e:
        print(f"[DIGITAÇÃO] ⚠️ Estratégia 1 falhou: {e}")

    # ===== ESTRATÉGIA 2: Focus via JS + page.keyboard.type() =====
    # Teclas de verdade: alguns editores só habilitam o botão de publicar assim.
    try:
        await _cursor_depois_da_mencao()
        await asyncio.sleep(0.25)

        if page:
            if instantanea:
                await page.keyboard.type(corpo, delay=random.randint(2, 8))
            else:
                for letra in corpo:
                    await page.keyboard.type(letra, delay=random.randint(dmin, dmax))
                    await asyncio.sleep(random.uniform(0.003, 0.015))
            await asyncio.sleep(0.2)

            if await _texto_do_editor_contem(elemento, corpo):
                print("[DIGITAÇÃO] ✅ Estratégia 2 (JS focus + keyboard.type) funcionou.")
                return
            print(
                "[DIGITAÇÃO] ⚠️ Estratégia 2 digitou mas o texto não apareceu no DOM. "
                "Tentando próxima..."
            )
    except Exception as e:
        print(f"[DIGITAÇÃO] ⚠️ Estratégia 2 falhou: {e}")

    # ===== ESTRATÉGIA 3: Click + element.type() =====
    try:
        if page:
            await _clique_seguro(page, elemento, timeout_ms=3000)
        await asyncio.sleep(0.3)
        await elemento.type(corpo, delay=random.randint(2, 8))
        await asyncio.sleep(0.2)

        if await _texto_do_editor_contem(elemento, corpo):
            print("[DIGITAÇÃO] ✅ Estratégia 3 (click + element.type) funcionou.")
            return
        print("[DIGITAÇÃO] ⚠️ Estratégia 3 não produziu o texto esperado. Tentando próxima...")
    except Exception as e:
        print(f"[DIGITAÇÃO] ⚠️ Estratégia 3 falhou: {e}")

    # ===== ESTRATÉGIA 4: Injeção direta no DOM via JavaScript =====
    # Insere o texto diretamente no HTML e dispara eventos de input para
    # que o framework (React/TipTap) reconheça a mudança.
    try:
        await elemento.evaluate("""(el, text) => {
            el.focus();
            // Limpa placeholder se existir
            if (el.querySelector('p.is-editor-empty, [data-placeholder]')) {
                el.innerHTML = '';
            }
            // Cria um parágrafo com o texto
            const p = document.createElement('p');
            p.textContent = text;
            el.appendChild(p);
            // Dispara eventos para que o framework detecte a mudança
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ' ' }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ' ' }));
            // Reposiciona cursor no final
            const range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }""", corpo.strip())
        await asyncio.sleep(0.3)

        if await _texto_do_editor_contem(elemento, corpo):
            print("[DIGITAÇÃO] ✅ Estratégia 4 (injeção JS direta) funcionou.")
            return
        print("[DIGITAÇÃO] ⚠️ Estratégia 4 inseriu no DOM mas o texto não ficou no editor.")
    except Exception as e:
        print(f"[DIGITAÇÃO] ⚠️ Estratégia 4 falhou: {e}")

    # ===== ESTRATÉGIA 5: fill() como último recurso =====
    try:
        await elemento.fill(corpo)
        await asyncio.sleep(0.2)
        if await _texto_do_editor_contem(elemento, corpo):
            print("[DIGITAÇÃO] ✅ Estratégia 5 (fill) funcionou.")
            return
        print("[DIGITAÇÃO] ⚠️ Estratégia 5 (fill) não deixou o texto no editor.")
    except Exception as e:
        print(f"[DIGITAÇÃO] ❌ Estratégia 5 (fill) também falhou: {e}")

    print(f"[DIGITAÇÃO] ❌ TODAS as estratégias falharam para digitar: '{corpo[:50]}...'")


def _hash_curto(texto: str) -> str:
    return hashlib.sha256(texto.strip().encode("utf-8", errors="ignore")).hexdigest()[:32]


def _normalizar_texto_chave(texto: str) -> str:
    base = unicodedata.normalize("NFKC", (texto or "").strip().lower())
    base = re.sub(r"\s+", " ", base)
    return base.strip()


async def _chave_estavel_comentario(item) -> str:
    """
    Gera chave estável priorizando URN/ID do DOM.

    No fallback, usa o link do perfil do autor + o texto do próprio comentário,
    ignorando as respostas aninhadas. Sem isso, a chave mudava assim que o robô
    publicava a resposta (o texto dela entrava no bloco) e o mesmo comentário
    voltava a parecer inédito na rodada seguinte.
    """
    payload = {}
    try:
        payload = await item.evaluate(
            """(el) => {
              function pickCommentNode(node) {
                const ownLi = node.closest("li.comments-comment-item");
                if (ownLi) return ownLi;
                const ownEntity = node.closest("[data-test-comment-entity-urn]");
                if (ownEntity) return ownEntity;
                return node;
              }
              function textoSemRespostas(node) {
                let base = node;
                try {
                  base = node.cloneNode(true);
                  base.querySelectorAll(
                    "li.comments-comment-item, article.comments-comment-entity, " +
                    ".comments-comment-item__replies-list, .comments-replies-list, " +
                    "[data-view-name='comment-replies'], [componentkey*='replies']"
                  ).forEach((n) => n.remove());
                } catch (e) {
                  base = node;
                }
                return (base.textContent || "").trim();
              }
              const commentNode = pickCommentNode(el);
              const urnNode = commentNode.matches?.("[data-test-comment-entity-urn]")
                ? commentNode
                : commentNode.querySelector?.("[data-test-comment-entity-urn]");
              const dataIdNode = commentNode.matches?.("[data-id*='urn:li:comment']")
                ? commentNode
                : commentNode.querySelector?.("[data-id*='urn:li:comment']");
              let urn = (urnNode?.getAttribute("data-test-comment-entity-urn") || "").trim();
              if (!urn) urn = (commentNode.getAttribute("data-urn") || "").trim();
              if (!urn) {
                const compKey = (commentNode.getAttribute("componentkey") || commentNode.closest("[componentkey]")?.getAttribute("componentkey") || "").trim();
                if (compKey.includes("urn:li:comment")) urn = compKey;
              }
              const dataId = (dataIdNode?.getAttribute("data-id") || "").trim();
              const domId = (commentNode.getAttribute?.("id") || "").trim();
              const authorEl = commentNode.querySelector?.(
                ".comments-post-meta__name-text, .comments-comment-meta__description-title, a[href*='/in/']"
              );
              const authorLink = commentNode.querySelector?.("a[href*='/in/']");
              const textEl = commentNode.querySelector?.(
                ".comments-comment-item__main-content, .update-components-text"
              );
              return {
                stableId: urn ? `urn:${urn}` : (dataId ? `dataid:${dataId}` : (domId ? `domid:${domId}` : "")),
                author: (authorEl?.textContent || "").trim(),
                authorHref: (authorLink?.getAttribute("href") || "").split("?")[0].trim(),
                text: (textEl ? textoSemRespostas(textEl) : textoSemRespostas(commentNode)),
              };
            }"""
        ) or {}
    except Exception:
        payload = {}

    stable_id = str(payload.get("stableId") or "").strip().lower()
    if stable_id:
        return stable_id

    autor = _normalizar_texto_chave(
        str(payload.get("authorHref") or "") or str(payload.get("author") or "")
    )
    corpo = _normalizar_texto_chave(str(payload.get("text") or ""))
    if not corpo:
        try:
            corpo = _normalizar_texto_chave(await item.inner_text())
        except Exception:
            corpo = ""
    base = f"{autor}|{corpo[:200]}"
    return f"fallback:{_hash_curto(base or str(uuid.uuid4()))}"


def _log_skip_comentario(
    *,
    run_id: str,
    modo: str,
    chave: str,
    motivo: str,
    extra: Optional[dict] = None,
) -> None:
    payload = {"mode": modo, "comment_key": chave, "reason": motivo}
    if extra:
        payload.update(extra)
    _debug_log(
        run_id=run_id or "none",
        hypothesis_id="H8",
        location="main.py:comentario:skip",
        message="comment_skipped",
        data=payload,
    )


async def rolar_comentarios(page, passos: int = 8) -> None:
    """Força o LinkedIn a carregar mais comentários (lazy load)."""
    for _ in range(passos):
        await page.mouse.wheel(0, 700)
        await asyncio.sleep(1.2)


async def preparar_secao_comentarios(page, *, completo: bool = True) -> None:
    """
    Rola até a área de comentários e abre 'mostrar mais' (o LinkedIn esconde muito no lazy load).

    `completo=False` faz uma passada curta, usada nas rodadas seguintes à primeira:
    repetir a varredura inteira a cada rodada deixava a página em rolagem constante
    e reprocessando os mesmos comentários.
    """
    if not completo:
        try:
            await page.mouse.wheel(0, 1400)
            await asyncio.sleep(0.6)
            await page.mouse.wheel(0, 1400)
            await asyncio.sleep(0.6)
        except Exception:
            pass
        return

    await asyncio.sleep(2)

    for sel in (
        ".comments-comments-list",
        ".comments-comment-list",
        ".social-details-social-activity",
        ".feed-shared-update-v2__comments-container",
    ):
        box = page.locator(sel).first
        if await box.count() > 0:
            try:
                await box.scroll_into_view_if_needed(timeout=8000)
                await asyncio.sleep(1)
            except Exception:
                pass
            break

    # Botões comuns para expandir / carregar thread de comentários (PT/EN)
    padroes_btn = [
        re.compile(r"^(mostrar|ver)\s+(mais\s+)?coment", re.I),
        re.compile(r"^(see|view)\s+(more\s+)?comment", re.I),
        re.compile(r"^carregar\s+mais", re.I),
        re.compile(r"^load\s+more", re.I),
        re.compile(r"^comentários\s+anteriores", re.I),
        re.compile(r"^previous\s+comment", re.I),
    ]
    for pat in padroes_btn:
        btn = page.get_by_role("button", name=pat)
        try:
            n = await btn.count()
            for i in range(min(n, 5)):
                try:
                    if await btn.nth(i).is_visible():
                        await btn.nth(i).click(timeout=4000)
                        await asyncio.sleep(1.2)
                except Exception:
                    pass
        except Exception:
            pass

    # Rolar a página inteira várias vezes (comentários ficam abaixo do post)
    for _ in range(18):
        await page.keyboard.press("PageDown")
        await asyncio.sleep(0.35)
    await rolar_comentarios(page, passos=14)


async def forcar_ordenacao_mais_recentes(page) -> bool:
    """
    Tenta trocar ordenação de comentários para "Mais recentes" / "Most recent".
    """
    if not bool(getattr(config, "FORCAR_ORDENACAO_MAIS_RECENTES", True)):
        return False

    await asyncio.sleep(0.5)
    gatilhos = [
        page.get_by_role("button", name=re.compile(r"Mais relevantes|Most relevant", re.I)).first,
        page.get_by_role("button", name=re.compile(r"Ordenar|Sort", re.I)).first,
        page.locator("button[aria-label*='relev']", has_text=re.compile(r"Mais relevantes|Most relevant", re.I)).first,
        page.locator("button").filter(has_text=re.compile(r"Mais relevantes|Most relevant", re.I)).first,
    ]

    aberto = False
    for botao in gatilhos:
        try:
            if await botao.count() > 0 and await botao.is_visible():
                await botao.scroll_into_view_if_needed()
                await botao.click(timeout=4000)
                await asyncio.sleep(0.3)
                aberto = True
                break
        except Exception:
            continue

    if not aberto:
        return False

    opcoes = [
        page.get_by_role("menuitem", name=re.compile(r"Mais recentes|Most recent", re.I)).first,
        page.get_by_role("option", name=re.compile(r"Mais recentes|Most recent", re.I)).first,
        page.locator("[role='menuitem']").filter(has_text=re.compile(r"Mais recentes|Most recent", re.I)).first,
        page.locator("button").filter(has_text=re.compile(r"Mais recentes|Most recent", re.I)).first,
        page.locator("li").filter(has_text=re.compile(r"Mais recentes|Most recent", re.I)).first,
    ]
    for opcao in opcoes:
        try:
            if await opcao.count() > 0 and await opcao.is_visible():
                await opcao.click(timeout=4000)
                await asyncio.sleep(0.4)
                return True
        except Exception:
            continue
    return False


async def melhor_locator_comentarios(page):
    """
    Tenta vários seletores (o LinkedIn muda classes). Retorna (locator, total) ou (None, 0).
    Prefere containers de comentário “completos” (com barra social) em vez do maior
    contador bruto — nós SDUI aninhados inflavam o total e escondiam o botão Responder.
    """
    # (seletor, peso) — peso maior = preferir mesmo com menos matches.
    candidatos = [
        ("li.comments-comment-item", 3.0),
        ("article.comments-comment-entity", 3.0),
        (".comments-comment-entity", 2.6),
        (".comments-comment-item", 2.6),
        ("article.comments-comment-item", 2.6),
        ("[data-test-comment-entity-urn]", 2.4),
        ("div[data-id*='urn:li:comment']", 2.2),
        ("[data-view-name='comment-container']", 1.4),
        # replaceableComment costuma vir 2–3x aninhado por comentário lógico
        ("div[componentkey*='replaceableComment']", 0.7),
    ]
    melhor_loc = None
    melhor_n = 0
    melhor_score = -1.0
    for sel, peso in candidatos:
        loc = page.locator(sel)
        try:
            n = await loc.count()
        except Exception:
            continue
        if n <= 0:
            continue
        score = float(n) * float(peso)
        if score > melhor_score:
            melhor_score = score
            melhor_n = n
            melhor_loc = loc
    return melhor_loc, melhor_n


async def aviso_se_parece_login(page) -> None:
    try:
        u = page.url
        if "login" in u or "uas/login" in u:
            print(
                "[AVISO] A URL parece tela de login. Abra o LinkedIn logado na mesma janela do Edge e rode de novo."
            )
        t = await page.title()
        if t and "Entrar" in t and "LinkedIn" in t:
            print("[AVISO] Página pode estar pedindo login nesta aba.")
    except Exception:
        pass


def _normalizar_valor_cookie(valor: str) -> str:
    """Remove aspas, prefixo 'nome=' e espaços que às vezes vêm ao copiar o cookie."""
    if not valor:
        return ""
    v = valor.strip()
    # Se o usuário colou algo como 'li_at=AQED...' ou 'JSESSIONID="ajax:123"'
    for prefixo in ("li_at=", "LINKEDIN_LI_AT=", "JSESSIONID=", "jsessionid="):
        if v.lower().startswith(prefixo.lower()):
            v = v[len(prefixo):].strip()
    # Remove aspas nas pontas (JSESSIONID normalmente é guardado entre aspas)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


async def injetar_cookies_linkedin(context) -> bool:
    """
    Injeta os cookies li_at (e JSESSIONID, se houver) no contexto do navegador,
    permitindo que a automação entre já logada no LinkedIn sem senha nem 2FA.
    Retorna True se pelo menos o li_at foi injetado.
    """
    li_at = _normalizar_valor_cookie(getattr(config, "LINKEDIN_LI_AT", "") or "")
    jsessionid = _normalizar_valor_cookie(getattr(config, "LINKEDIN_JSESSIONID", "") or "")

    if not li_at:
        return False

    cookies = [
        {
            "name": "li_at",
            "value": li_at,
            "domain": ".www.linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }
    ]
    if jsessionid:
        # O LinkedIn guarda o JSESSIONID entre aspas; mantemos esse formato ao enviar.
        valor_js = jsessionid if jsessionid.startswith('"') else f'"{jsessionid}"'
        cookies.append(
            {
                "name": "JSESSIONID",
                "value": valor_js,
                "domain": ".www.linkedin.com",
                "path": "/",
                "secure": True,
                "sameSite": "None",
            }
        )

    try:
        await context.add_cookies(cookies)
        print("[Info] Cookie de sessão do LinkedIn injetado (login automático).")
        return True
    except Exception as e:
        print(f"[AVISO] Não foi possível injetar o cookie do LinkedIn: {e}")
        return False


async def guardar_sessao_da_conta(context) -> None:
    """
    Depois do login (manual ou por cookie), grava no arquivo da conta o li_at e
    o JSESSIONID que estão no navegador. Sem isso, quem loga à mão fica com os
    campos vazios e o "Duplicar" cria uma cópia deslogada, que o LinkedIn trata
    como aparelho novo e pede autorização de novo.
    """
    sm = getattr(config, "_sm", None)
    conta = getattr(config, "CONTA_ID", "") or ""
    if sm is None or not conta:
        return
    try:
        cookies = await context.cookies("https://www.linkedin.com")
    except Exception:
        return
    valores = {c.get("name"): c.get("value") or "" for c in cookies}
    li_at = _normalizar_valor_cookie(valores.get("li_at", ""))
    jsessionid = _normalizar_valor_cookie(valores.get("JSESSIONID", ""))
    if not li_at:
        return
    atual_li = _normalizar_valor_cookie(getattr(config, "LINKEDIN_LI_AT", "") or "")
    atual_js = _normalizar_valor_cookie(getattr(config, "LINKEDIN_JSESSIONID", "") or "")
    if li_at == atual_li and jsessionid == atual_js:
        return
    if sm.salvar_cookies_sessao(conta, li_at, f'"{jsessionid}"' if jsessionid else ""):
        config.LINKEDIN_LI_AT = li_at
        config.LINKEDIN_JSESSIONID = jsessionid
        print(
            "[Info] Sessão do LinkedIn guardada nesta conta (li_at/JSESSIONID). "
            "Contas duplicadas a partir dela já entram logadas."
        )


async def _linkedin_esta_logado(page) -> bool:
    """Heurística: logado quando não estamos em telas de login/checkpoint e há UI de sessão."""
    try:
        u = (page.url or "").lower()
    except Exception:
        return False
    if any(x in u for x in ("/login", "uas/login", "/checkpoint", "/authwall", "signup")):
        return False
    try:
        marcador = await page.evaluate(
            """() => {
              const sel = [
                "img.global-nav__me-photo",
                ".global-nav__me",
                "a[href*='/feed/']",
                "[data-control-name='nav.settings']",
                "input.search-global-typeahead__input",
              ];
              return sel.some((s) => document.querySelector(s));
            }"""
        )
        return bool(marcador)
    except Exception:
        return False


async def garantir_login_linkedin(page, timeout_login_s: Optional[int] = None) -> bool:
    """
    Garante sessão LinkedIn antes de operar. No perfil dedicado, na 1ª vez,
    aguarda o usuário logar manualmente na janela aberta. Depois disso a sessão
    fica salva no perfil e o login não é mais pedido.
    """
    if timeout_login_s is None:
        timeout_login_s = int(getattr(config, "LOGIN_TIMEOUT_SEGUNDOS", 600) or 600)

    try:
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
    except Exception:
        pass
    await asyncio.sleep(2)

    if await _linkedin_esta_logado(page):
        print("[Info] Sessão do LinkedIn detectada (já logado). Continuando.\n")
        return True

    # Não está logado: leva direto para a tela de login e aguarda o usuário.
    try:
        await page.goto(
            "https://www.linkedin.com/login",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
    except Exception:
        pass

    tem_cookie = bool((getattr(config, "LINKEDIN_LI_AT", "") or "").strip())
    minutos = max(1, int(round(timeout_login_s / 60)))
    if tem_cookie:
        print(
            "\n============== COOKIE DO LINKEDIN EXPIRADO/INVÁLIDO ==============\n"
            "O cookie 'li_at' salvo não logou (pode ter expirado ou deslogado).\n"
            "Você pode: (a) fazer login manual nesta janela agora, OU\n"
            "(b) fechar, pegar um novo 'li_at' no Edge (F12 > Aplicativo > Cookies)\n"
            "    e colar em Configurações > Cookie li_at.\n"
            f"Aguardando login por ~{minutos} min...\n"
            "=================================================================\n"
        )
    else:
        print(
            "\n============== FAÇA LOGIN NO LINKEDIN (só desta vez) ==============\n"
            "Uma janela do Edge foi aberta pela automação.\n"
            "1) Entre na SUA conta do LinkedIn nessa janela.\n"
            "2) NÃO feche a janela — assim que logar, a automação segue sozinha.\n"
            f"3) Você tem cerca de {minutos} min. O login fica salvo para as próximas vezes.\n"
            "==================================================================\n"
        )

    inicio = perf_counter()
    proximo_aviso = 30.0
    while perf_counter() - inicio <= timeout_login_s:
        if _pagina_fechada(page):
            print(
                "[AVISO] A janela foi fechada antes de concluir o login. "
                "Rode novamente e apenas faça login (não feche a janela)."
            )
            return False
        if await _linkedin_esta_logado(page):
            print("[Info] Login detectado com sucesso! Continuando a automação.\n")
            return True
        decorrido = perf_counter() - inicio
        if decorrido >= proximo_aviso:
            restante = int(timeout_login_s - decorrido)
            print(f"[Info] Aguardando login... (~{restante}s restantes)")
            proximo_aviso += 30.0
        await asyncio.sleep(3)

    print("[ERRO] Tempo de login esgotado. Rode novamente e faça o login na janela.")
    return False


async def _limpar_marcadores_reply(page) -> None:
    try:
        await page.evaluate(
            """() => {
              document.querySelectorAll("[data-bot-reply-editor]").forEach((el) => {
                el.removeAttribute("data-bot-reply-editor");
              });
              document.querySelectorAll("[data-bot-reply-box]").forEach((el) => {
                el.removeAttribute("data-bot-reply-box");
              });
            }"""
        )
    except Exception:
        pass


_JS_SELETOR_OVERLAY_MSG = (
    ".msg-overlay-list-bubble, "
    ".msg-overlay-conversation-bubble, "
    "aside.msg-overlay-list-bubble, "
    ".msg-overlay-container, "
    "#msg-overlay, "
    "[class*='msg-overlay-conversation-bubble'], "
    "[data-view-name='messaging-overlay']"
)


async def _fechar_overlays_messaging(page, *, silencioso: bool = False) -> int:
    """
    Tira do caminho TODAS as abas de chat/DM do rodapé. Enquanto um chat fica
    por cima do post, o robô perde a caixa de resposta ou o clique cai na aba
    (e abre o perfil da pessoa).

    Não clica em nada: clicar no X/minimizar das abas novas (classes com hash)
    acertava o nome da pessoa. Só esconde os blocos nesta aba do navegador —
    as conversas continuam intactas no LinkedIn.
    """
    try:
        escondeu = await page.evaluate(_JS_ESCONDER_DOCK_MENSAGENS, _JS_SELETOR_OVERLAY_MSG)
        if escondeu and escondeu > 0:
            if not silencioso:
                print(f"[PROTEÇÃO] Escondeu {escondeu} aba(s) de chat do rodapé (sem clicar).")
            await asyncio.sleep(0.1)
        return int(escondeu or 0)
    except Exception:
        return 0


async def _overlay_messaging_visivel(page) -> bool:
    """True quando ainda há chat/DM por cima do post depois de tentar fechar."""
    try:
        return bool(
            await page.evaluate(
                """(seletorOverlay) => {
              for (const el of document.querySelectorAll(seletorOverlay)) {
                const st = window.getComputedStyle(el);
                if (st.display === "none" || st.visibility === "hidden") continue;
                const r = el.getBoundingClientRect();
                if (r.width > 40 && r.height > 40) return true;
              }
              return false;
            }""",
                _JS_SELETOR_OVERLAY_MSG,
            )
        )
    except Exception:
        return False


# Esconde (sem clicar em nada) as abas de chat que o LinkedIn prende no rodapé.
# As classes antigas (msg-overlay-…) viraram hash no layout novo, então além
# delas vale qualquer bloco fixo na metade de baixo da tela com link de perfil
# ou de mensagens. Só muda o estilo nesta aba: nenhuma conversa é aberta/fechada.
_JS_ESCONDER_DOCK_MENSAGENS = r"""(seletorOverlay) => {
  let escondidos = 0;
  const esconde = (el) => {
    if (!el || el.getAttribute("data-bot-oculto") === "1") return;
    el.setAttribute("data-bot-oculto", "1");
    el.style.setProperty("display", "none", "important");
    escondidos++;
  };
  try {
    for (const el of document.querySelectorAll(seletorOverlay)) esconde(el);
  } catch (e) {}

  const alturaTela = window.innerHeight || document.documentElement.clientHeight || 0;
  const pareceChat = (el) => {
    try {
      if (el.querySelector("a[href*='/messaging/'], a[href*='/in/'], img")) return true;
    } catch (e) {}
    const aria = (el.getAttribute("aria-label") || "").toLowerCase();
    const txt = (el.textContent || "").slice(0, 400).toLowerCase();
    return /mensage|messag|conversa|chat/.test(aria + " " + txt);
  };
  for (const el of document.querySelectorAll("body *")) {
    if (el.getAttribute("data-bot-oculto") === "1") continue;
    if (el.closest("[data-bot-reply-box='1']")) continue;
    let st;
    try {
      st = window.getComputedStyle(el);
    } catch (e) {
      continue;
    }
    if (st.position !== "fixed") continue;
    if (st.display === "none" || st.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 20) continue;
    // Barra do topo (fixa em cima) fica: só o que mora na metade de baixo.
    if (r.top < alturaTela * 0.45) continue;
    if (!pareceChat(el)) continue;
    esconde(el);
  }
  return escondidos;
}"""


async def _esconder_dock_mensagens(page) -> int:
    try:
        return int(
            await page.evaluate(_JS_ESCONDER_DOCK_MENSAGENS, _JS_SELETOR_OVERLAY_MSG) or 0
        )
    except Exception:
        return 0


async def _clique_seguro(page, alvo, *, timeout_ms: int = 6000) -> bool:
    """
    Clica SÓ no elemento pedido. O clique com force=True do Playwright acerta
    o que estiver por cima nas coordenadas — era assim que a aba de chat do
    rodapé recebia o clique e o robô ia parar no perfil da pessoa.

    Centraliza o alvo na tela, esconde o dock de mensagens e confere com
    elementFromPoint que o ponto do clique é do próprio alvo. Se algo ainda
    cobrir, dispara o click direto no elemento (sem coordenadas).
    """
    try:
        await alvo.evaluate(
            "(el) => el.scrollIntoView({block: 'center', inline: 'nearest'})"
        )
    except Exception:
        try:
            await alvo.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
    # Alvo que mora num bloco fixo (ex.: o composer de DM do fluxo de perfil)
    # não pode ter o próprio bloco escondido.
    try:
        alvo_em_bloco_fixo = await alvo.evaluate(
            """(el) => {
              for (let n = el; n && n !== document.body; n = n.parentElement) {
                if (window.getComputedStyle(n).position === "fixed") return true;
              }
              return false;
            }"""
        )
    except Exception:
        alvo_em_bloco_fixo = False
    if not alvo_em_bloco_fixo:
        await _esconder_dock_mensagens(page)
    try:
        livre = await alvo.evaluate(
            """(el) => {
              const r = el.getBoundingClientRect();
              if (r.width < 1 || r.height < 1) return false;
              const x = r.left + r.width / 2;
              const y = r.top + r.height / 2;
              const topo = document.elementFromPoint(x, y);
              return !!topo && (topo === el || el.contains(topo));
            }"""
        )
    except Exception:
        return False
    if livre:
        try:
            await alvo.click(timeout=timeout_ms)
            return True
        except Exception:
            pass
    try:
        await alvo.evaluate("(el) => el.click()")
        return True
    except Exception:
        return False


async def _saiu_do_post(page, url_post: str, url_carregada: str) -> bool:
    """A aba deixou o post (perfil, mensagens, feed…) por causa de algum clique?"""
    try:
        atual = page.url or ""
    except Exception:
        return False

    def _caminho(u: str) -> str:
        return (u or "").split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()

    agora = _caminho(atual)
    if not agora:
        return False
    return agora not in {c for c in (_caminho(url_post), _caminho(url_carregada)) if c}


async def _fechar_popovers_e_emojis(page) -> None:
    """
    Fecha qualquer popover de reações/emojis e menus abertos após publicar resposta.
    Previne que o picker de emojis interfira com a próxima ação.
    """
    try:
        await page.evaluate("""() => {
          // Fecha popovers de reações (emoji picker que aparece após responder)
          const reactionPopovers = document.querySelectorAll(
            ".reactions-menu, " +
            "[data-view-name='reactions-menu'], " +
            ".artdeco-hoverable-content--visible, " +
            ".reactions-react-button--active-flyout, " +
            ".social-actions-button__reaction-picker, " +
            "[class*='reaction'][class*='popover'], " +
            "[class*='reaction'][class*='flyout'], " +
            ".artdeco-dropdown__content--is-open"
          );
          for (const el of reactionPopovers) {
            try { el.remove(); } catch(e) {
              try { el.style.display = 'none'; } catch(e2) {}
            }
          }
          // Remove active state from reaction buttons
          const activeReactionBtns = document.querySelectorAll(
            "button.reactions-react-button--active, " +
            "[class*='reaction'][class*='active']"
          );
          for (const btn of activeReactionBtns) {
            try { btn.blur(); } catch(e) {}
          }
        }""")
    except Exception:
        pass

    # Pressionar Escape múltiplas vezes para fechar qualquer menu/popover
    for _ in range(3):
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.15)
        except Exception:
            break
    # Sem clique "em área neutra": no canto da tela ficam o logo e links do
    # LinkedIn, e um clique ali tira a aba do post.


async def encontrar_botao_responder(item):
    """
    Acha o botão/link Responder do comentário no DOM atual do LinkedIn (SDUI + legado).
    Retorna um Locator Playwright ou None.
    """
    # Hover ajuda a revelar a barra de ações em alguns layouts.
    try:
        await item.scroll_into_view_if_needed(timeout=4000)
        await item.hover(timeout=2500)
        await asyncio.sleep(0.2)
    except Exception:
        pass

    seletores = (
        "button[data-view-name='comment-reply']",
        "a[data-view-name='comment-reply']",
        "[data-view-name='comment-reply']",
        "button.comments-comment-social-bar__reply-action-button",
        "button[class*='reply-action']",
        "a[class*='reply-action']",
        "[data-control-name*='reply' i]",
        "button[aria-label*='Responder' i]",
        "button[aria-label*='Reply' i]",
        "a[aria-label*='Responder' i]",
        "a[aria-label*='Reply' i]",
        "[role='button'][aria-label*='Responder' i]",
        "[role='button'][aria-label*='Reply' i]",
    )

    # Amplia o escopo: o item às vezes é um nó interno sem a barra social.
    escopos = [item]
    for xp in (
        "xpath=ancestor-or-self::li[contains(@class,'comments-comment-item')][1]",
        "xpath=ancestor-or-self::article[contains(@class,'comments-comment')][1]",
        "xpath=ancestor-or-self::div[@data-view-name='comment-container'][1]",
        "xpath=ancestor-or-self::div[contains(@componentkey,'replaceableComment')][1]",
        "xpath=ancestor-or-self::*[contains(@data-id,'urn:li:comment')][1]",
        "xpath=ancestor-or-self::*[@data-test-comment-entity-urn][1]",
    ):
        try:
            anc = item.locator(xp)
            if await anc.count() > 0:
                escopos.append(anc.first)
        except Exception:
            continue

    for escopo in escopos:
        for sel in seletores:
            try:
                loc = escopo.locator(sel)
                n = await loc.count()
            except Exception:
                continue
            for i in range(min(n, 6)):
                cand = loc.nth(i)
                try:
                    if await cand.is_visible():
                        return cand
                except Exception:
                    continue

        # Texto visível "Responder" / "Reply" (evita pegar "X respostas").
        try:
            por_texto = escopo.locator("button, a, [role='button']").filter(
                has_text=re.compile(r"^\s*(Responder|Reply)\s*$", re.I)
            )
            n_txt = await por_texto.count()
            for i in range(min(n_txt, 6)):
                cand = por_texto.nth(i)
                try:
                    if await cand.is_visible():
                        return cand
                except Exception:
                    continue
        except Exception:
            pass

        # SVG do balãozinho de comentário (legado).
        try:
            loc_svg = escopo.locator(
                "svg#comment-small, [id='comment-small'], "
                "svg[data-test-icon='comment-small'], "
                "li-icon[type='comment-icon'], "
                "svg[aria-label*='comment' i], svg[aria-label*='responder' i]"
            )
            if await loc_svg.count() > 0:
                parent_btn = loc_svg.first.locator(
                    "xpath=ancestor::button[1] | ancestor::a[1] | ancestor::*[@role='button'][1]"
                )
                if await parent_btn.count() > 0:
                    cand = parent_btn.first
                    try:
                        if await cand.is_visible():
                            return cand
                    except Exception:
                        return cand
        except Exception:
            pass

    # Fallback JS: marca o controle no DOM e retorna via atributo.
    try:
        marcado = await item.evaluate(
            """(el) => {
              document.querySelectorAll('[data-bot-reply-btn="1"]').forEach((n) => {
                n.removeAttribute('data-bot-reply-btn');
              });

              function pickRoot(node) {
                let best = node;
                const chain = [];
                let cur = node;
                for (let i = 0; i < 12 && cur; i++) {
                  chain.push(cur);
                  cur = cur.parentElement;
                }
                for (const n of chain) {
                  const ck = (n.getAttribute && n.getAttribute('componentkey')) || '';
                  const did = (n.getAttribute && n.getAttribute('data-id')) || '';
                  const dv = (n.getAttribute && n.getAttribute('data-view-name')) || '';
                  if (
                    ck.includes('replaceableComment') ||
                    did.includes('urn:li:comment') ||
                    dv === 'comment-container' ||
                    (n.classList && (
                      n.classList.contains('comments-comment-item') ||
                      n.classList.contains('comments-comment-entity')
                    ))
                  ) {
                    best = n;
                  }
                }
                return best;
              }

              const root = pickRoot(el);
              // A barra social às vezes fica no pai/irmão, fora do nó interno do texto.
              const searchRoots = [root];
              if (root.parentElement) searchRoots.push(root.parentElement);
              if (root.parentElement && root.parentElement.parentElement) {
                searchRoots.push(root.parentElement.parentElement);
              }

              function belongsToOtherComment(node, selfRoot) {
                const other = node.closest(
                  "li.comments-comment-item, article.comments-comment-entity, " +
                  "[data-view-name='comment-container'], " +
                  "div[componentkey*='replaceableComment'], " +
                  "[data-id*='urn:li:comment']"
                );
                if (!other) return false;
                return other !== selfRoot && !selfRoot.contains(other) && !other.contains(selfRoot);
              }

              const nodes = [];
              for (const sr of searchRoots) {
                for (const n of sr.querySelectorAll('button, a, [role="button"], span[tabindex]')) {
                  if (belongsToOtherComment(n, root)) continue;
                  nodes.push(n);
                }
              }
              const scoreOf = (c) => {
                const aria = (c.getAttribute('aria-label') || '').toLowerCase();
                const text = (c.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const dv = (c.getAttribute('data-view-name') || '').toLowerCase();
                const cls = String(c.className || '').toLowerCase();
                const control = (c.getAttribute('data-control-name') || '').toLowerCase();
                let s = 0;
                if (dv === 'comment-reply') s += 100;
                if (control.includes('reply')) s += 80;
                if (cls.includes('reply-action')) s += 80;
                if (/responder|reply/.test(aria) && !/replies|respostas/.test(aria)) s += 70;
                if (/^(responder|reply)$/.test(text)) s += 60;
                if (/ver\\s+\\d+\\s+respostas|see\\s+\\d+\\s+repl/i.test(text)) s -= 100;
                if (/gostei|like|curtir|seguir|follow|opções|options|reag/i.test(aria + ' ' + text)) {
                  s -= 50;
                }
                return s;
              };

              let best = null;
              let bestScore = 0;
              for (const c of nodes) {
                const sc = scoreOf(c);
                if (sc > bestScore) {
                  bestScore = sc;
                  best = c;
                }
              }
              if (best && bestScore >= 50) {
                best.setAttribute('data-bot-reply-btn', '1');
                try { best.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                return { found: true, score: bestScore };
              }
              return { found: false, score: bestScore || 0, probed: nodes.length };
            }"""
        )
    except Exception:
        marcado = {"found": False}

    if marcado and marcado.get("found"):
        try:
            page_ref = item.page
            loc = page_ref.locator("[data-bot-reply-btn='1']").last
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
        try:
            loc = item.locator("[data-bot-reply-btn='1']").first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass

    return None


# Trecho de JS reaproveitado (colado DENTRO da função avaliada): separa a caixa
# de resposta de um comentário do campo "Adicionar comentário…" do post e dos
# widgets de mensagem (inbox). Sem isso o robô comenta no post como autor.
_JS_CAIXA_REPLY = r"""
  function botEhMessaging(node) {
    if (!node || !node.closest) return false;
    if (node.classList && node.classList.contains("msg-form__contenteditable")) return true;
    return !!node.closest(
      ".msg-form, .msg-overlay, #msg-overlay, [class*='msg-form']," +
      "[data-view-name='messaging'], aside.msg-overlay-list-bubble," +
      ".msg-overlay-list-bubble, .msg-overlay-conversation-bubble, .msg-convo-wrapper"
    );
  }

  function botEhReplyExplicito(node) {
    if (!node || !node.closest) return false;
    return !!node.closest(
      ".comments-comment-box--reply," +
      ".comments-comment-item-reply-comment-box," +
      "[class*='reply-comment-box']," +
      "[class*='comment-box--reply']"
    );
  }

  function botDentroDeComentario(node) {
    if (!node || !node.closest) return false;
    return !!node.closest(
      "li.comments-comment-item," +
      "article.comments-comment-entity," +
      "[data-view-name='comment-container']," +
      "div[componentkey*='replaceableComment']," +
      "[data-test-comment-entity-urn]"
    );
  }

  // Sinais estruturais que SÓ o campo geral do post tem. O placeholder não
  // serve: no layout novo a caixa de resposta usa o mesmo "Adicionar comentário".
  function botEhComposerDoPost(node) {
    if (!node || !node.closest) return false;
    if (botEhReplyExplicito(node)) return false;
    if (botDentroDeComentario(node)) return false;
    if (node.closest(
      ".comments-comment-texteditor," +
      ".feed-shared-update-v2__comment-box," +
      "[data-view-name='feed-comment-box']," +
      ".comments-comment-box:not(.comments-comment-box--reply)"
    )) return true;
    const container = node.closest(
      ".feed-shared-update-v2__comments-container," +
      "[class*='comments-comments-list']"
    );
    const caixa = node.closest("[data-view-name='comment-box'], [class*='comment-box']");
    if (container && caixa && caixa.parentElement === container) return true;
    return false;
  }

  function botRaizDoComentario(alvo) {
    if (!alvo || !alvo.closest) return alvo;
    return (
      alvo.closest("li.comments-comment-item") ||
      alvo.closest("[data-view-name='comment-container']") ||
      alvo.closest("[componentkey*='replaceableComment']") ||
      alvo.closest("article.comments-comment-entity") ||
      alvo.closest("[data-test-comment-entity-urn]") ||
      alvo
    );
  }

  const BOT_SEL_COMENTARIO =
    "li.comments-comment-item," +
    "article.comments-comment-entity," +
    ".comments-comment-entity," +
    ".comments-comment-item," +
    "[data-view-name='comment-container']," +
    "[componentkey*='replaceableComment']," +
    "[data-test-comment-entity-urn]," +
    "[data-id*='urn:li:comment']";

  // O embrulho é só deste comentário (não agrupa outros)? É o que autoriza
  // procurar o cartão de resposta um nível acima da raiz.
  function botEmbrulhoDoMesmoComentario(raiz, embrulho) {
    if (!embrulho || !embrulho.querySelectorAll) return false;
    let cands = [];
    try {
      cands = Array.from(embrulho.querySelectorAll(BOT_SEL_COMENTARIO));
    } catch (e) {
      return false;
    }
    for (const c of cands) {
      if (c === raiz || c.contains(raiz) || raiz.contains(c)) continue;
      return false;
    }
    return true;
  }

  // Sobe pelos embrulhos do MESMO comentário (replaceableComment aninha 2–3x).
  function botRaizLargaDoComentario(alvo) {
    let raiz = botRaizDoComentario(alvo);
    let pai = raiz.parentElement;
    for (let i = 0; i < 3 && pai; i++) {
      if (!pai.matches || !pai.matches(BOT_SEL_COMENTARIO)) break;
      if (!botEmbrulhoDoMesmoComentario(raiz, pai)) break;
      raiz = pai;
      pai = pai.parentElement;
    }
    return raiz;
  }

  // Blocos onde o cartão de resposta DESTE comentário pode estar pendurado.
  // No layout de classes com hash o comentário fica dentro de vários <div>
  // vazios (display: contents) e o cartão nasce como irmão de um deles, mais
  // acima: sobe enquanto o embrulho só tem este comentário e, no primeiro
  // nível que agrupa outros, pega os irmãos seguintes até o próximo comentário.
  function botRaizesDoBloco(alvo) {
    const raiz = botRaizDoComentario(alvo);
    const raizes = [];
    let alturaBase = 0;
    try {
      alturaBase = raiz.getBoundingClientRect().height;
    } catch (e) {}
    let nivel = raiz;
    for (let i = 0; i < 8 && nivel; i++) {
      const pai = nivel.parentElement;
      if (!pai || pai === document.body || pai === document.documentElement) break;
      let alturaPai = 0;
      try {
        alturaPai = pai.getBoundingClientRect().height;
      } catch (e) {}
      if (botEmbrulhoDoMesmoComentario(raiz, pai) && alturaPai <= alturaBase + 450) {
        raizes.push(pai);
        nivel = pai;
        continue;
      }
      let sib = nivel.nextElementSibling;
      for (let j = 0; j < 4 && sib; j++) {
        let temComentario = false;
        try {
          temComentario = sib.matches(BOT_SEL_COMENTARIO) || !!sib.querySelector(BOT_SEL_COMENTARIO);
        } catch (e) {}
        if (temComentario) break;
        raizes.push(sib);
        sib = sib.nextElementSibling;
      }
      break;
    }
    return raizes;
  }

  // Existe outro comentário entre o alvo e a caixa? Então a caixa é de outra
  // pessoa e responder ali seria responder o comentário errado. A busca parte
  // do ancestral comum: o cartão pode estar vários níveis acima da raiz.
  function botComentarioEntre(raiz, node) {
    let pai = raiz.parentElement;
    while (pai && !pai.contains(node)) pai = pai.parentElement;
    if (!pai) return false;
    const cands = pai.querySelectorAll(
      "li.comments-comment-item, article.comments-comment-entity," +
      "[data-view-name='comment-container'], div[componentkey*='replaceableComment']"
    );
    for (const c of cands) {
      if (c === raiz || c.contains(raiz) || raiz.contains(c) || c.contains(node)) continue;
      const depoisDaRaiz = !!(raiz.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING);
      const antesDaCaixa = !!(node.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_PRECEDING);
      if (depoisDaRaiz && antesDaCaixa) return true;
    }
    return false;
  }

  // A caixa tem de pertencer a ESTE comentário: dentro dele ou logo depois.
  function botCaixaLigadaAoComentario(alvo, node) {
    const raiz = botRaizDoComentario(alvo);
    if (raiz.contains(node)) return true;
    if (botComentarioEntre(raiz, node)) return false;
    // O cartão pode estar pendurado num embrulho de fora do mesmo comentário.
    const raizLarga = botRaizLargaDoComentario(alvo);
    if (raizLarga !== raiz && raizLarga.contains(node)) return true;
    let sib = raiz.nextElementSibling;
    for (let i = 0; i < 4 && sib; i++) {
      if (sib === node || sib.contains(node)) return true;
      sib = sib.nextElementSibling;
    }
    for (const bloco of botRaizesDoBloco(alvo)) {
      if (bloco === node || bloco.contains(node)) return true;
    }
    const pai = raiz.parentElement;
    if (pai && pai.contains(node)) {
      try {
        const ra = raiz.getBoundingClientRect();
        const rb = node.getBoundingClientRect();
        if (rb.top >= ra.top - 10 && rb.top <= ra.bottom + 300) return true;
      } catch (e) {}
    }
    return false;
  }

  function botDiagnosticoCaixa(alvo, node) {
    if (!alvo || !node) return "sem_no";
    if (botEhMessaging(node)) return "messaging";
    // O campo do post fica ANTES de todos os comentários no documento; a caixa
    // de resposta nasce dentro/depois do comentário alvo e coladinha nele.
    const pos = alvo.compareDocumentPosition(node);
    if (!(pos & Node.DOCUMENT_POSITION_FOLLOWING)) return "composer_do_post";
    if (!botEhReplyExplicito(node) && botEhComposerDoPost(node)) return "composer_do_post";
    if (!botCaixaLigadaAoComentario(alvo, node)) return "de_outro_comentario";
    try {
      const ra = alvo.getBoundingClientRect();
      const rb = node.getBoundingClientRect();
      if (rb.width < 20 || rb.height < 8) return "invisivel";
      if (rb.top < ra.top - 60) return "acima_do_comentario";
      if (rb.top > ra.bottom + 900) return "longe_do_comentario";
    } catch (e) {}
    return "ok";
  }

  function botCaixaRespondeAoComentario(alvo, node) {
    return botDiagnosticoCaixa(alvo, node) === "ok";
  }

  // Só botões que publicam a resposta deste comentário. O "Comentar" do post e
  // os botões do chat ficam de fora.
  function botBotaoDeReply(alvo, btn) {
    if (!alvo || !btn || !btn.closest) return false;
    if (botEhMessaging(btn)) return false;
    if (btn.closest(
      ".feed-shared-social-action-bar," +
      ".social-details-social-actions," +
      "[data-view-name='social-actions']," +
      "[class*='social-actions-button']"
    )) return false;
    const caixa = btn.closest(
      "[data-bot-reply-box='1']," +
      ".comments-comment-box--reply," +
      "[class*='reply-comment-box']," +
      "[data-view-name='comment-box']," +
      "[class*='comment-box']"
    );
    return botCaixaRespondeAoComentario(alvo, caixa || btn);
  }
"""


# Trecho de JS reaproveitado (colado DENTRO da função avaliada, sempre depois de
# _JS_CAIXA_REPLY): acha a "casca" do cartão de resposta — o bloco que o
# LinkedIn abre com a menção, a barra de emoji/GIF e o botão de publicar — e o
# campo de texto dentro dela. O layout novo monta o campo depois da casca e às
# vezes com altura zero, então aqui nada é descartado por tamanho.
_JS_CASCA_REPLY = r"""
  const BOT_SEL_EDITOR =
    "[contenteditable='true']," +
    "[contenteditable='plaintext-only']," +
    "[contenteditable='']," +
    "[role='textbox']," +
    ".ql-editor," +
    ".tiptap," +
    ".ProseMirror," +
    "textarea";

  const BOT_SEL_COMPOSER =
    BOT_SEL_EDITOR + ",[data-placeholder],[aria-placeholder],[data-artdeco-is-focused]";

  function botNoVisivel(node) {
    if (!node) return false;
    if (node.offsetParent !== null) return true;
    try {
      return node.getClientRects().length > 0;
    } catch (e) {
      return false;
    }
  }

  function botEhRaizDeComentario(node) {
    if (!node || !node.matches) return false;
    return node.matches(
      "li.comments-comment-item," +
      "article.comments-comment-entity," +
      "[data-view-name='comment-container']," +
      "div[componentkey*='replaceableComment']"
    );
  }

  // O rótulo pode vir com complemento ("Responder a Fulano"), então casa pelo
  // começo. "3 respostas" e afins ficam de fora.
  function botNomeDeSubmit(valor) {
    const t = String(valor || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!t) return false;
    if (/\d+\s*(respostas?|replies?|coment[aá]rios?|comments?)/.test(t)) return false;
    return /^(responder|reply|publicar|publish|post|comentar|comment|enviar|send|submit)\b/.test(t);
  }

  // Botão que PUBLICA a resposta. O ícone "Responder" do comentário fica fora:
  // ele carrega data-view-name='comment-reply'.
  function botEhBotaoSubmitResposta(btn) {
    if (!btn || btn.getAttribute("data-view-name") === "comment-reply") return false;
    if (btn.getAttribute("data-bot-reply-btn") === "1") return false;
    if (botEhMessaging(btn)) return false;
    if (!botNoVisivel(btn)) return false;
    return botNomeDeSubmit(btn.textContent) || botNomeDeSubmit(btn.getAttribute("aria-label"));
  }

  // A casca precisa parecer um composer, não a barra de ações do comentário:
  // ou tem campo de texto, ou tem as ferramentas do cartão (emoji, GIF, imagem).
  function botCascaPareceComposer(casca, btnSubmit) {
    if (!casca) return false;
    try {
      if (casca.querySelector(BOT_SEL_COMPOSER)) return true;
    } catch (e) {}
    let btns = [];
    try {
      btns = Array.from(casca.querySelectorAll("button, [role='button']"));
    } catch (e) {}
    for (const b of btns) {
      if (b === btnSubmit) continue;
      const sinal = (
        (b.getAttribute("aria-label") || "") + " " +
        (b.getAttribute("data-view-name") || "") + " " +
        String(b.className || "")
      ).toLowerCase();
      if (/emoji|gif|imagem|image|foto|photo|anexo|attach|m[ií]dia|media|sticker|figurinha/.test(sinal)) {
        return true;
      }
    }
    return false;
  }

  function botCascaDoBotao(btn) {
    // A busca começa no pai: a classe do próprio botão de publicar costuma ser
    // "comments-comment-box__submit-button", que casaria com [class*='comment-box'].
    const base = btn.parentElement || btn;
    const conhecida = base.closest(
      "[data-view-name='comment-box']," +
      ".comments-comment-box--reply," +
      ".comments-comment-item-reply-comment-box," +
      "[class*='reply-comment-box']," +
      "[class*='comment-box']," +
      "form"
    );
    if (conhecida) return conhecida;
    let melhor = base;
    let node = melhor;
    for (let i = 0; i < 6 && node; i++) {
      if (botEhRaizDeComentario(node)) break;
      // Subir até um embrulho que já contém o comentário faria a casca "começar
      // antes" dele e ser recusada como campo do post.
      let temComentario = false;
      try {
        temComentario = !!node.querySelector(BOT_SEL_COMENTARIO);
      } catch (e) {}
      if (temComentario) break;
      melhor = node;
      node = node.parentElement;
    }
    return melhor;
  }

  function botEditorDaCasca(casca) {
    if (!casca) return null;
    const vistos = [];
    const varrer = (raiz, nivel) => {
      if (!raiz || nivel > 1) return;
      let achados = [];
      try {
        achados = Array.from(raiz.querySelectorAll(BOT_SEL_EDITOR));
      } catch (e) {}
      for (const n of achados) {
        if (vistos.indexOf(n) < 0) vistos.push(n);
      }
      let hosts = [];
      try {
        hosts = Array.from(raiz.querySelectorAll("*")).filter((n) => n.shadowRoot);
      } catch (e) {}
      for (const h of hosts) varrer(h.shadowRoot, nivel + 1);
    };
    varrer(casca, 0);

    let melhor = null;
    let melhorNota = -1;
    for (const ed of vistos) {
      if (ed.closest("button, [role='button']")) continue;
      if (botEhMessaging(ed)) continue;
      let nota = 0;
      const ce = (ed.getAttribute("contenteditable") || "").toLowerCase();
      if (ce === "true" || ce === "plaintext-only" || ce === "") nota += 30;
      if ((ed.getAttribute("role") || "").toLowerCase() === "textbox") nota += 20;
      if (ed.matches(".ql-editor, .tiptap, .ProseMirror")) nota += 15;
      const aria = (ed.getAttribute("aria-label") || "").toLowerCase();
      if (/editor|coment|comment|resposta|reply|texto|text/.test(aria)) nota += 10;
      if (botNoVisivel(ed)) nota += 5;
      if (nota > melhorNota) {
        melhorNota = nota;
        melhor = ed;
      }
    }
    return melhorNota >= 15 ? melhor : null;
  }

  function botAncoraDoComentario(alvo) {
    if (!alvo || !alvo.closest) return alvo;
    return alvo.closest(BOT_SEL_COMENTARIO) || alvo;
  }

  function botRaizesDeBuscaCasca(alvo) {
    const ancora = botAncoraDoComentario(alvo);
    const raizes = [ancora];
    const junta = (node) => {
      if (node && raizes.indexOf(node) < 0) raizes.push(node);
    };

    let sib = ancora.nextElementSibling;
    for (let i = 0; i < 3 && sib; i++) {
      junta(sib);
      sib = sib.nextElementSibling;
    }

    // O mesmo comentário aparece embrulhado várias vezes (replaceableComment
    // aninha 2–3x): o cartão pode estar pendurado num embrulho de fora.
    let pai = ancora.parentElement;
    for (let i = 0; i < 2 && pai; i++) {
      if (!pai.matches || !pai.matches(BOT_SEL_COMENTARIO)) break;
      junta(pai);
      let psib = pai.nextElementSibling;
      for (let j = 0; j < 2 && psib; j++) {
        junta(psib);
        psib = psib.nextElementSibling;
      }
      pai = pai.parentElement;
    }

    junta(ancora.parentElement);
    for (const bloco of botRaizesDoBloco(alvo)) junta(bloco);
    return raizes;
  }

  // Cartão de resposta DESTE comentário: tem botão de publicar e passa pelo
  // diagnóstico de pertencer ao comentário (não é o campo geral do post).
  function botAcharCascaResposta(alvo) {
    for (const raiz of botRaizesDeBuscaCasca(alvo)) {
      let btns = [];
      try {
        btns = Array.from(raiz.querySelectorAll("button, [role='button']"));
      } catch (e) {}
      for (const btn of btns) {
        if (!botEhBotaoSubmitResposta(btn)) continue;
        const casca = botCascaDoBotao(btn);
        if (!casca) continue;
        if (!botCascaPareceComposer(casca, btn)) continue;
        if (!botCaixaRespondeAoComentario(alvo, casca)) continue;
        return { casca: casca, btn: btn };
      }
    }
    return null;
  }
"""


async def _diagnostico_caixa_reply(item, caixa) -> str:
    """
    "ok" quando a caixa pertence a ESTE comentário. Caso contrário devolve o
    motivo ("composer_do_post", "messaging", …) para log e para pular o item.
    """
    try:
        handle = await caixa.element_handle(timeout=3000)
    except Exception:
        handle = None
    if handle is None:
        return "sem_no"
    try:
        motivo = await item.evaluate(
            "(el, box) => {"
            + _JS_CAIXA_REPLY
            + r"""
              return botDiagnosticoCaixa(el, box);
            }""",
            handle,
        )
        return str(motivo or "sem_no")
    except Exception:
        return "erro_diagnostico"
    finally:
        try:
            await handle.dispose()
        except Exception:
            pass


async def _caixa_eh_resposta_do_comentario(item, caixa) -> bool:
    """True só quando a caixa pertence a ESTE comentário (nunca o campo do post)."""
    return (await _diagnostico_caixa_reply(item, caixa)) == "ok"


async def _botao_publica_resposta(item, botao) -> bool:
    """Impede clicar no "Comentar" do post (ou no chat) achando que é a resposta."""
    try:
        handle = await botao.element_handle(timeout=2000)
    except Exception:
        handle = None
    if handle is None:
        return False
    try:
        return bool(
            await item.evaluate(
                "(el, btn) => {"
                + _JS_CAIXA_REPLY
                + _JS_CASCA_REPLY
                + r"""
                  // O botão tem de morar num cartão com campo de texto/ferramentas:
                  // o ícone "Responder" do comentário só abre/fecha o cartão.
                  return (
                    botBotaoDeReply(el, btn) &&
                    botCascaPareceComposer(botCascaDoBotao(btn), btn)
                  );
                }""",
                handle,
            )
        )
    except Exception:
        return False
    finally:
        try:
            await handle.dispose()
        except Exception:
            pass


def _norm_texto_simples(valor: str) -> str:
    return " ".join(_remover_acentos((valor or "").lower()).split())


async def _texto_esta_na_caixa_reply(caixa, texto: str) -> bool:
    """Confere que o texto digitado está mesmo na caixa de resposta."""
    esperado = _norm_texto_simples(texto)
    try:
        atual = await caixa.evaluate("(el) => (el.innerText || el.textContent || '')")
    except Exception:
        return False
    atual_norm = _norm_texto_simples(atual)
    if len(atual_norm) < 2:
        return False
    if not esperado:
        return True
    alvo = esperado[:60]
    return alvo in atual_norm or atual_norm[: len(alvo)] == alvo


async def _marcar_editor_resposta_no_dom(item) -> dict:
    """
    Localiza o editor de resposta inline aberto após clicar em Responder.
    Ignora widgets de mensagem (msg-form) e o campo geral do post.

    Primeiro acha a casca do cartão pelo botão de publicar: no layout novo o
    campo de texto nasce depois da casca e às vezes com altura zero, então
    procurar só por contenteditable visível deixava o cartão aberto e vazio.
    A varredura antiga continua como segunda tentativa.
    """
    try:
        return await item.evaluate(
            "(el) => {"
            + _JS_CAIXA_REPLY
            + _JS_CASCA_REPLY
            + """
              function commentAnchor(node) {
                return node.closest(
                  "li.comments-comment-item," +
                  "[data-view-name='comment-container']," +
                  "div[componentkey*='replaceableComment']," +
                  "[data-test-comment-entity-urn]"
                ) || node;
              }

              function isMessagingWidget(ed) {
                if (botEhMessaging(ed)) return true;
                const aria = (ed.getAttribute("aria-label") || "").toLowerCase();
                if (/mensagem|message|inbox/.test(aria) && !/coment|comment|resposta|reply/.test(aria)) {
                  return true;
                }
                return false;
              }

              function isPostTopEditor(ed) {
                return !botCaixaRespondeAoComentario(commentAnchor(el), ed);
              }

              function hasSubmitResponderButton(container) {
                if (!container) return false;
                const btns = container.querySelectorAll("button, [role='button']");
                for (const btn of btns) {
                  if (btn.getAttribute("data-view-name") === "comment-reply") continue;
                  const aria = (btn.getAttribute("aria-label") || "").toLowerCase().trim();
                  const txt = (btn.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  if (aria === "responder" || aria === "reply" || txt === "responder" || txt === "reply") {
                    if (btn.offsetParent !== null || btn.getClientRects().length > 0) return true;
                  }
                  if (/^(publicar|post|comentar|comment|enviar|submit)$/i.test(txt)) {
                    if (btn.offsetParent !== null || btn.getClientRects().length > 0) return true;
                  }
                }
                return false;
              }

              function scoreEditor(ed) {
                if (!ed || isMessagingWidget(ed) || isPostTopEditor(ed)) return -1;
                const rect = ed.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 8) return -1;

                let score = 0;
                const box =
                  ed.closest("[data-view-name='comment-box']") ||
                  ed.closest(".comments-comment-box--reply") ||
                  ed.closest("[class*='reply-comment-box']") ||
                  ed.closest("[class*='comment-box']") ||
                  ed.parentElement;

                if (ed.closest("[data-view-name='comment-box']")) score += 50;
                if (ed.closest("[class*='reply-comment']")) score += 40;
                if (ed.closest(".tiptap, .ProseMirror, .ql-editor")) score += 10;
                if (hasSubmitResponderButton(box)) score += 35;

                const aria = (ed.getAttribute("aria-label") || "").toLowerCase();
                if (/coment|comment|resposta|reply/.test(aria)) score += 20;

                const anchor = commentAnchor(el);
                if (anchor.contains(ed) || (box && anchor.contains(box))) score += 25;

                let sib = anchor;
                for (let i = 0; i < 4 && sib; i++) {
                  if (sib.contains(ed)) { score += 15; break; }
                  sib = sib.nextElementSibling;
                  if (sib && sib.contains(ed)) { score += 12; break; }
                }

                return score;
              }

              function collectSearchRoots(node) {
                const anchor = commentAnchor(node);
                const roots = [anchor];
                let sib = anchor.nextElementSibling;
                for (let i = 0; i < 3 && sib; i++) {
                  roots.push(sib);
                  sib = sib.nextElementSibling;
                }
                const parent = anchor.parentElement;
                if (parent && !roots.includes(parent)) roots.push(parent);
                for (const bloco of botRaizesDoBloco(node)) {
                  if (!roots.includes(bloco)) roots.push(bloco);
                }
                return roots;
              }

              function limparMarcadores() {
                document.querySelectorAll("[data-bot-reply-editor]").forEach((n) => {
                  n.removeAttribute("data-bot-reply-editor");
                });
                document.querySelectorAll("[data-bot-reply-box]").forEach((n) => {
                  n.removeAttribute("data-bot-reply-box");
                });
              }

              function marcar(ed, box, via, score) {
                limparMarcadores();
                ed.setAttribute("data-bot-reply-editor", "1");
                if (box) box.setAttribute("data-bot-reply-box", "1");
                return {
                  found: true,
                  shell: !!box,
                  via: via,
                  score: score,
                  aria: (ed.getAttribute("aria-label") || "").slice(0, 80),
                };
              }

              const achadoCasca = botAcharCascaResposta(el);
              if (achadoCasca) {
                const edCasca = botEditorDaCasca(achadoCasca.casca);
                if (edCasca) return marcar(edCasca, achadoCasca.casca, "casca", 100);
                // Cartão aberto sem campo montado: deixa a casca marcada para o
                // robô clicar nela e tentar de novo.
                limparMarcadores();
                achadoCasca.casca.setAttribute("data-bot-reply-box", "1");
              }

              const candidates = [];
              for (const root of collectSearchRoots(el)) {
                const eds = root.querySelectorAll(
                  "[contenteditable='true'], [role='textbox'][contenteditable='true']"
                );
                for (const ed of eds) {
                  const score = scoreEditor(ed);
                  if (score >= 0) candidates.push({ ed, score });
                }
              }

              candidates.sort((a, b) => b.score - a.score);
              const best = candidates.find((c) => c.score >= 12);
              if (!best) {
                return {
                  found: false,
                  shell: !!achadoCasca,
                  reason: achadoCasca ? "casca_sem_editor" : "no_candidate",
                  tried: candidates.length,
                };
              }

              const ed = best.ed;
              const box =
                ed.closest("[data-view-name='comment-box']") ||
                ed.closest(".comments-comment-box--reply") ||
                ed.closest("[class*='reply-comment-box']") ||
                ed.closest("[class*='comment-box']") ||
                ed.parentElement;

              return marcar(ed, box, "score", best.score);
            }"""
        ) or {"found": False}
    except Exception as exc:
        return {"found": False, "reason": repr(exc)}


_DIAG_REPLY_EMITIDOS = 0
_DIAG_REPLY_MAX = 3


async def _dump_estrutura_reply(item) -> dict:
    """
    Retrato do DOM em volta do comentário quando o campo de resposta não é
    achado. Os seletores do LinkedIn mudam sem aviso; sem esse retrato só dá
    para adivinhar qual atributo sumiu.
    """
    try:
        return await item.evaluate(
            "(el) => {"
            + _JS_CAIXA_REPLY
            + _JS_CASCA_REPLY
            + r"""
              const corta = (s, n) =>
                String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n);

              function descreve(node) {
                if (!node) return null;
                let w = 0;
                let h = 0;
                try {
                  const b = node.getBoundingClientRect();
                  w = Math.round(b.width);
                  h = Math.round(b.height);
                } catch (e) {}
                const attr = (nome) =>
                  node.getAttribute ? corta(node.getAttribute(nome), 40) : "";
                return {
                  tag: node.tagName,
                  dv: attr("data-view-name"),
                  ck: attr("componentkey"),
                  role: attr("role"),
                  ce: attr("contenteditable"),
                  aria: attr("aria-label"),
                  ph: attr("data-placeholder") || attr("aria-placeholder"),
                  cls: corta(node.className, 60),
                  wh: [w, h],
                };
              }

              const ancora = botAncoraDoComentario(el);

              const botoes = [];
              const editores = [];
              const vistosBtn = [];
              const vistosEd = [];
              for (const raiz of botRaizesDeBuscaCasca(el)) {
                let bs = [];
                try {
                  bs = Array.from(raiz.querySelectorAll("button, [role='button']"));
                } catch (e) {}
                for (const b of bs) {
                  if (botoes.length >= 24 || vistosBtn.indexOf(b) >= 0) continue;
                  vistosBtn.push(b);
                  const submit = botEhBotaoSubmitResposta(b);
                  const casca = submit ? botCascaDoBotao(b) : null;
                  const info = descreve(b);
                  info.txt = corta(b.textContent, 40);
                  info.submit = submit;
                  info.casca = casca ? descreve(casca) : null;
                  info.composer = casca ? botCascaPareceComposer(casca, b) : null;
                  info.diag = casca ? botDiagnosticoCaixa(el, casca) : null;
                  info.editor = casca ? !!botEditorDaCasca(casca) : null;
                  botoes.push(info);
                }

                let es = [];
                try {
                  es = Array.from(raiz.querySelectorAll(BOT_SEL_COMPOSER));
                } catch (e) {}
                for (const ed of es) {
                  if (editores.length >= 14 || vistosEd.indexOf(ed) >= 0) continue;
                  vistosEd.push(ed);
                  const info = descreve(ed);
                  info.diag = botDiagnosticoCaixa(el, ed);
                  info.dentro = ancora.contains(ed);
                  editores.push(info);
                }
              }

              const esqueleto = [];
              (function anda(node, nivel) {
                if (!node || nivel > 12 || esqueleto.length >= 160) return;
                for (const filho of Array.from(node.children || [])) {
                  if (esqueleto.length >= 160) return;
                  const d = descreve(filho);
                  esqueleto.push(
                    nivel + "|" + d.tag +
                    "|dv=" + d.dv + "|ck=" + d.ck + "|role=" + d.role +
                    "|ce=" + d.ce + "|aria=" + d.aria + "|ph=" + d.ph +
                    "|cls=" + d.cls + "|wh=" + d.wh.join("x") +
                    "|txt=" + corta(filho.textContent, 25)
                  );
                  anda(filho, nivel + 1);
                }
              })(ancora, 0);

              return {
                el: descreve(el),
                ancora: descreve(ancora),
                casca_achada: !!botAcharCascaResposta(el),
                botoes: botoes,
                editores: editores,
                esqueleto: esqueleto,
              };
            }"""
        ) or {}
    except Exception as exc:
        return {"erro": repr(exc)}


async def _registrar_diag_reply(item, *, run_id: str, modo: str, chave: str) -> None:
    """Grava o retrato do DOM no log de depuração e resume na tela."""
    global _DIAG_REPLY_EMITIDOS
    if _DIAG_REPLY_EMITIDOS >= _DIAG_REPLY_MAX:
        return
    _DIAG_REPLY_EMITIDOS += 1

    diag = await _dump_estrutura_reply(item)
    _debug_log(
        run_id=run_id or "none",
        hypothesis_id="H10",
        location="main.py:reply:editor_diag",
        message="reply_editor_diag",
        data={"mode": modo, "comment_key": chave, "diag": diag},
    )

    if diag.get("erro"):
        print(f"[DIAG] Não consegui ler o DOM do comentário: {diag['erro']}")
        return

    candidatos = [b for b in diag.get("botoes") or [] if b.get("submit")]
    print(
        f"[DIAG] Cartão de resposta: casca_achada={diag.get('casca_achada')}, "
        f"botões de publicar={len(candidatos)}, campos de texto={len(diag.get('editores') or [])}."
    )
    for b in candidatos[:4]:
        print(
            f"[DIAG]   botão '{b.get('txt') or b.get('aria')}' → casca={b.get('casca', {}).get('tag')}"
            f" composer={b.get('composer')} diag={b.get('diag')} editor={b.get('editor')}"
        )
    for ed in (diag.get("editores") or [])[:4]:
        print(
            f"[DIAG]   campo {ed.get('tag')} ce={ed.get('ce')!r} role={ed.get('role')!r} "
            f"aria={ed.get('aria')!r} {ed.get('wh')} diag={ed.get('diag')} dentro={ed.get('dentro')}"
        )
    print(
        f"[DIAG] Estrutura completa gravada em {os.path.basename(_DEBUG_LOG_PATH)} "
        "(mensagem reply_editor_diag)."
    )


async def _acordar_editor_da_casca(page) -> bool:
    """
    Cartão de resposta aberto sem campo de texto montado: um clique no corpo do
    cartão faz o LinkedIn montar o campo. O ponto é escolhido longe de links,
    botões e da barra de emoji/GIF, para não navegar nem publicar sem texto.
    """
    try:
        ponto = await page.evaluate(
            """() => {
              const casca = document.querySelector("[data-bot-reply-box='1']");
              if (!casca) return null;
              const r = casca.getBoundingClientRect();
              if (r.width < 40 || r.height < 20) return null;
              const proibido = (node) =>
                !node ||
                !node.closest ||
                !!node.closest("a, button, [role='button'], img, svg, input");
              const fxs = [0.5, 0.65, 0.35, 0.8];
              const fys = [0.3, 0.45, 0.2, 0.6];
              for (const fy of fys) {
                for (const fx of fxs) {
                  const x = r.left + r.width * fx;
                  const y = r.top + r.height * fy;
                  const node = document.elementFromPoint(x, y);
                  if (!node || !casca.contains(node)) continue;
                  if (proibido(node)) continue;
                  return { x: x, y: y };
                }
              }
              return null;
            }"""
        )
    except Exception:
        ponto = None
    if not ponto:
        return False
    try:
        await page.mouse.click(float(ponto["x"]), float(ponto["y"]))
        await asyncio.sleep(0.3)
        return True
    except Exception:
        return False


async def _editor_utilizavel(box, editor, timeout_ms: int = 3000) -> bool:
    """
    Editor pronto para receber texto. O campo do layout novo pode estar no DOM
    com altura zero enquanto o cartão já está visível — descartar por tamanho
    era o que fazia o robô desistir com a caixa aberta.
    """
    try:
        await editor.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        pass
    try:
        await editor.wait_for(state="attached", timeout=800)
    except Exception:
        return False
    try:
        return await box.is_visible()
    except Exception:
        return False


async def _cartao_resposta_aberto(item) -> bool:
    """
    Existe cartão de resposta aberto neste comentário? Vale tanto o cartão em si
    (barra de ferramentas + botão de publicar) quanto um campo de texto solto —
    o cartão pode aparecer antes de o campo ser montado.
    """
    try:
        return bool(
            await item.evaluate(
                "(el) => {"
                + _JS_CAIXA_REPLY
                + _JS_CASCA_REPLY
                + r"""
                  if (botAcharCascaResposta(el)) return true;
                  let nos = [];
                  for (const raiz of [botRaizLargaDoComentario(el)].concat(botRaizesDoBloco(el))) {
                    try {
                      nos = nos.concat(Array.from(raiz.querySelectorAll(BOT_SEL_COMPOSER)));
                    } catch (e) {}
                  }
                  for (const no of nos) {
                    if (botEhMessaging(no)) continue;
                    if (no.closest("button, [role='button']")) continue;
                    if (botNoVisivel(no)) return true;
                    const caixa = no.parentElement;
                    if (caixa && botNoVisivel(caixa)) return true;
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


async def _descartar_casca_resposta(page, item) -> None:
    """
    Fecha o cartão de resposta aberto para este comentário. Sem isso, cada falha
    deixava uma caixa vazia na página e o comentário seguinte abria outra.
    """
    aberto = await _cartao_resposta_aberto(item)

    if aberto:
        try:
            botao = await encontrar_botao_responder(item)
            if botao is not None:
                await _clique_seguro(page, botao, timeout_ms=4000)
                await asyncio.sleep(0.35)
        except Exception:
            pass
        if await _cartao_resposta_aberto(item):
            for _ in range(2):
                try:
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                except Exception:
                    break

    await _limpar_marcadores_reply(page)


async def obter_caixa_resposta_ao_comentario(page, item):
    """
    Após clicar em Responder: retorna (container_do_reply, editor).
    Não usar page.locator('.ql-editor').first — isso é o comentário novo no post.
    """
    seletores_editor = (
        ".tiptap.ProseMirror, .tiptap[contenteditable='true'], "
        "[contenteditable='true'].ql-editor, div[contenteditable='true'][role='textbox'], "
        "div.ql-editor[contenteditable='true'], [contenteditable='true'], "
        "[contenteditable='plaintext-only'], [role='textbox'], .ql-editor, .tiptap, .ProseMirror"
    )
    seletores_caixa = (
        "[data-view-name='comment-box']",
        ".comments-comment-box--reply",
        ".comments-comment-item-reply-comment-box",
        "div[class*='reply-comment-box']",
    )
    # Fora do escopo do comentário só valem caixas que são reply por definição:
    # "[data-view-name='comment-box']" solto é o campo "Adicionar comentário…".
    seletores_caixa_reply = (
        ".comments-comment-box--reply",
        ".comments-comment-item-reply-comment-box",
        "div[class*='reply-comment-box']",
    )

    scope = item
    for xp in (
        "xpath=ancestor-or-self::li[contains(@class,'comments-comment-item')][1]",
        "xpath=ancestor-or-self::div[@data-view-name='comment-container'][1]",
        "xpath=ancestor-or-self::div[contains(@componentkey,'replaceableComment')][1]",
    ):
        ri = item.locator(xp)
        try:
            if await ri.count() > 0:
                scope = ri.first
                break
        except Exception:
            continue

    await _limpar_marcadores_reply(page)

    resultado = {"found": False}
    avisou_casca = False
    for tentativa in range(10):
        await asyncio.sleep(0.35 if tentativa else 0.45)
        resultado = await _marcar_editor_resposta_no_dom(item)
        if resultado.get("found"):
            print(
                f"[CAIXA] ✅ Editor de resposta localizado via DOM "
                f"(via={resultado.get('via')}, score={resultado.get('score')}, "
                f"tentativa={tentativa + 1})."
            )
            break
        # Cartão aberto mas sem campo de texto: clicar no corpo dele costuma
        # montar o campo (o LinkedIn monta o editor de forma preguiçosa).
        if resultado.get("shell") and tentativa % 2 == 0:
            if not avisou_casca:
                print(
                    "[CAIXA] Cartão de resposta aberto sem campo de texto. "
                    "Clicando no cartão para montar o editor…"
                )
                avisou_casca = True
            try:
                await page.locator("[data-bot-reply-box='1']").last.scroll_into_view_if_needed(
                    timeout=3000
                )
            except Exception:
                pass
            await _acordar_editor_da_casca(page)

    if resultado.get("found"):
        ed = page.locator("[data-bot-reply-editor='1']").last
        box = page.locator("[data-bot-reply-box='1']").last
        try:
            await ed.wait_for(state="attached", timeout=5000)
            # O cartão é o alvo do scroll: o campo em si pode estar sem altura.
            for alvo_scroll in (box, ed):
                try:
                    await alvo_scroll.scroll_into_view_if_needed(timeout=4000)
                    break
                except Exception:
                    continue
            if await _caixa_eh_resposta_do_comentario(item, box):
                return box, ed
            print("[CAIXA] ⛔ Marcador caiu no campo do post/messaging. Descartando.")
            await _limpar_marcadores_reply(page)
        except Exception:
            pass

    xpath_tentativas = (
        "xpath=following::div[@data-view-name='comment-box'][1]",
        "xpath=./following-sibling::div[@data-view-name='comment-box'][1]",
        "xpath=following::div[contains(@class,'comments-comment-box--reply')][1]",
        "xpath=./following-sibling::div[contains(@class,'comments-comment-box--reply')][1]",
    )
    for xp in xpath_tentativas:
        box = scope.locator(xp).first
        if await box.count() == 0:
            continue
        ed = box.locator(seletores_editor).first
        if not await _editor_utilizavel(box, ed):
            continue
        if not await _caixa_eh_resposta_do_comentario(item, box):
            print("[CAIXA] ⛔ Caixa via xpath é o campo do post/messaging. Ignorando.")
            continue
        print("[CAIXA] ✅ Encontrou editor via xpath no scope do comentário.")
        return box, ed

    for sel in seletores_caixa:
        loc = scope.locator(sel)
        if await loc.count() == 0:
            continue
        box = loc.first
        ed = box.locator(seletores_editor).first
        if not await _editor_utilizavel(box, ed):
            continue
        if not await _caixa_eh_resposta_do_comentario(item, box):
            print(f"[CAIXA] ⛔ Caixa via '{sel}' é o campo do post/messaging. Ignorando.")
            continue
        print(f"[CAIXA] ✅ Encontrou editor via seletor '{sel}' no scope.")
        return box, ed

    print("[CAIXA] ⚠️ Não encontrou no scope. Buscando caixas de *reply* na página...")
    for sel in seletores_caixa_reply:
        loc = page.locator(sel)
        try:
            n = await loc.count()
        except Exception:
            n = 0
        for idx in range(n):
            box = loc.nth(idx)
            try:
                if not await box.is_visible():
                    continue
            except Exception:
                continue
            ed = box.locator(seletores_editor).first
            if not await _editor_utilizavel(box, ed, timeout_ms=2000):
                continue
            if not await _caixa_eh_resposta_do_comentario(item, box):
                continue
            print(f"[CAIXA] ✅ Encontrou editor na page via '{sel}' (índice {idx}).")
            return box, ed

    raise LookupError("caixa de resposta ao comentário")


def _texto_preview_match_envio(norm: str) -> str:
    n = _remover_acentos((norm or "").lower()).strip()
    n = " ".join(n.split())
    if len(n) < 12:
        return ""
    return n[:88]


async def _resposta_aparece_no_fio(item, trecho: str, caixa=None) -> Optional[dict]:
    """
    Procura o trecho da resposta no fio do comentário. Devolve
    {"achou": bool, "erro": bool} — "erro" quando o LinkedIn mostra
    "Erro ao publicar comentário" no fio. None quando não foi possível ler o
    DOM (fio recarregado, item desanexado).

    No layout com classes hash a resposta publicada nasce FORA do comentário,
    entre ele e o cartão de resposta. Por isso o trecho principal da busca é o
    texto do documento entre o comentário e o campo de texto do cartão — sem
    depender de classe nenhuma e sem contar o que ainda está no campo.
    """
    if not trecho or len(trecho) < 10:
        return {"achou": False, "erro": False}
    handle = None
    if caixa is not None:
        try:
            handle = await caixa.element_handle(timeout=1500)
        except Exception:
            handle = None
    try:
        return await item.evaluate(
            "(el, args) => {"
            + _JS_CAIXA_REPLY
            + r"""
              const snippet = args.snippet;
              const box = args.box;
              function norm(x) {
                return String(x || "")
                  .normalize("NFD")
                  .replace(/[̀-ͯ]/g, "")
                  .toLowerCase()
                  .replace(/[\t\n\r ]+/g, " ")
                  .trim();
              }
              // Sem espaços: <br> e quebras viram nada no Range.toString().
              const compacto = (x) => norm(x).replace(/\s+/g, "");
              const tgt = compacto(snippet || "").slice(0, 60);
              if (tgt.length < 10) return { achou: false, erro: false };
              const RE_ERRO = /erro ao publicar|error posting|nao foi possivel publicar/;

              const SEL_EDITORES =
                "[contenteditable='true'], [contenteditable='plaintext-only'], " +
                "[role='textbox'], textarea";

              // Texto SEM o que está dentro de campos de texto: o cartão ainda
              // pode ter a mensagem digitada, e isso não prova publicação.
              function textoForaDeEditores(raiz) {
                let texto = compacto(raiz.innerText || "");
                let eds = [];
                try {
                  eds = Array.from(raiz.querySelectorAll(SEL_EDITORES));
                } catch (e) {}
                for (const ed of eds) {
                  const t = compacto(ed.innerText || ed.value || "");
                  if (t) texto = texto.split(t).join("|");
                }
                return texto;
              }

              const raiz = botRaizLargaDoComentario(el);

              // 1) Layout antigo: as respostas ficam aninhadas no próprio comentário.
              const proprias = [
                el.closest("li.comments-comment-item"),
                el.closest("[data-view-name='comment-container']"),
                raiz,
              ];
              for (const r of proprias) {
                if (r && textoForaDeEditores(r).includes(tgt)) return { achou: true, erro: false };
              }

              // 2) Layout novo: tudo entre o fim do comentário e o campo do cartão.
              if (box && box.isConnected && !raiz.contains(box)) {
                const pos = raiz.compareDocumentPosition(box);
                if (pos & Node.DOCUMENT_POSITION_FOLLOWING) {
                  const range = document.createRange();
                  range.setStartAfter(raiz);
                  range.setEndBefore(box);
                  const bruto = range.toString();
                  if (compacto(bruto).includes(tgt)) return { achou: true, erro: false };
                  return { achou: false, erro: RE_ERRO.test(norm(bruto)) };
                }
              }

              // 3) Cartão já fechado: só os primeiros comentários logo abaixo
              // (respostas deste fio), nunca a lista inteira — a mesma frase
              // costuma estar em respostas antigas de outros fios.
              const rr = raiz.getBoundingClientRect();
              let seguintes = [];
              try {
                seguintes = Array.from(document.querySelectorAll(BOT_SEL_COMENTARIO)).filter(
                  (c) =>
                    c !== raiz &&
                    !c.contains(raiz) &&
                    !raiz.contains(c) &&
                    !!(raiz.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)
                );
              } catch (e) {}
              let vistos = 0;
              for (const c of seguintes) {
                const r = c.getBoundingClientRect();
                if (r.top > rr.bottom + 450) break;
                if (textoForaDeEditores(c).includes(tgt)) return { achou: true, erro: false };
                if (++vistos >= 4) break;
              }
              return { achou: false, erro: false };
            }""",
            {"snippet": trecho, "box": handle},
        )
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                await handle.dispose()
            except Exception:
                pass


# O LinkedIn recusou a última publicação ("Erro ao publicar comentário")?
# Recusas seguidas costumam ser a conta limitada/restrita: insistir piora.
_ERRO_LINKEDIN_NO_ULTIMO_ENVIO = False
_MAX_RECUSAS_SEGUIDAS_LINKEDIN = 3


async def _caixa_ainda_tem_resposta(caixa, texto_resposta: str) -> Optional[bool]:
    """
    A mensagem digitada ainda está no campo? Depois de publicar, o LinkedIn
    recoloca a menção (nome da pessoa) no cartão — então "campo vazio" não
    serve de sinal; o que conta é a MENSAGEM ter saído do campo.
    None quando o campo sumiu ou não dá para ler.
    """
    try:
        if await caixa.count() == 0:
            return None
        inner = await caixa.evaluate("(el) => (el.innerText || el.textContent || '')")
    except Exception:
        return None
    alvo = _norm_texto_simples(texto_resposta)[:40]
    if not alvo:
        return len(_norm_texto_simples(inner)) > 4
    return alvo in _norm_texto_simples(inner)


async def _confirmar_envio_resposta_thread(item, caixa, texto_resposta: str) -> bool:
    """
    Só confirma envio quando a resposta aparece no fio. A mensagem sair do
    campo não é prova sozinha: era assim que um "Comentar" no post vazio
    contava como resposta enviada.
    """
    global _ERRO_LINKEDIN_NO_ULTIMO_ENVIO
    trecho_py = _texto_preview_match_envio(texto_resposta or "")
    tem_trecho = bool(trecho_py) and len(trecho_py) >= 12
    # A resposta aparece primeiro "carregando" e só depois com o texto:
    # quando a mensagem já saiu do campo, espera mais antes de desistir.
    t_end = perf_counter() + 5.5
    t_limite = perf_counter() + 12.0
    first = True
    while perf_counter() < t_end:
        await asyncio.sleep(0.14 if first else 0.55)
        first = False

        estado = (
            await _resposta_aparece_no_fio(item, trecho_py, caixa) if tem_trecho else None
        )
        if estado and estado.get("achou"):
            return True
        if estado and estado.get("erro"):
            _ERRO_LINKEDIN_NO_ULTIMO_ENVIO = True
            print("[AVISO] O LinkedIn mostrou 'Erro ao publicar comentário' nesta resposta.")
            return False

        ainda = await _caixa_ainda_tem_resposta(caixa, texto_resposta)
        if ainda is True:
            continue
        # Mensagem saiu do campo (ou o campo sumiu): o envio foi disparado.
        if not tem_trecho or estado is None:
            return True
        t_end = t_limite
    return False


async def publicar_resposta_thread(
    page, item, bloco_reply, caixa, msg: Optional[str] = None
) -> bool:
    """
    LinkedIn às vezes usa botão só com ícone, aria-label, ou aceita Ctrl+Enter no editor.
    O botão de enviar pode estar num ancestral (li/article), não só dentro do bloco_reply.
    """
    await asyncio.sleep(0.25)
    nome_btn = re.compile(
        r"Publicar|Post|Comentar|Comment|Enviar|Submit|Done|Concluir|Publish|Responder|Reply|Send",
        re.I,
    )

    async def _click_one(el, *, force: bool = True) -> bool:
        try:
            if await el.get_attribute("disabled") is not None:
                return False
            ada = await el.get_attribute("aria-disabled")
            if ada is not None and ada.lower().strip() in ("true", "1"):
                return False
            if not await _botao_publica_resposta(item, el):
                return False
            return await _clique_seguro(page, el)
        except Exception:
            return False

    # Depois que a caixa esvazia a resposta já foi (ou o texto se perdeu):
    # continuar clicando é o que gerava resposta duplicada.
    estado = {"abortar": False}

    async def _caixa_vazia() -> bool:
        # "Vazia" = a mensagem saiu do campo. Depois de publicar o LinkedIn
        # recoloca a menção (nome da pessoa), então medir o tamanho do texto
        # dava "não vazia" e o robô clicava de novo — o 2º envio no meio do 1º
        # é o que gerava "Erro ao publicar comentário".
        return (await _caixa_ainda_tem_resposta(caixa, msg or "")) is not True

    async def try_click_batch(batch) -> bool:
        if estado["abortar"]:
            return False
        try:
            n = await batch.count()
        except Exception:
            n = 0
        for i in range(n):
            if not await _click_one(batch.nth(i)):
                continue
            if await _confirmar_envio_resposta_thread(item, caixa, msg or ""):
                return True
            if await _caixa_vazia():
                estado["abortar"] = True
                return False
        return False

    # O cartão marcado é o que está aberto para ESTE comentário, e o botão dele
    # é o alvo mais confiável: o ícone "Responder" do comentário (que só reabre
    # o cartão) fica de fora por causa do data-view-name.
    nome_btn_exato = re.compile(
        r"^\s*(Responder|Reply|Publicar|Publish|Post|Comentar|Comment|Enviar|Send|Submit)\s*$",
        re.I,
    )
    try:
        casca_marcada = page.locator("[data-bot-reply-box='1']").last
        if await casca_marcada.count() > 0:
            botoes_casca = casca_marcada.locator(
                "button:not([data-view-name='comment-reply'])"
            ).filter(has_text=nome_btn_exato)
            if await try_click_batch(botoes_casca):
                return True
            for frag in ("Responder", "Reply", "Publicar", "Comentar", "Post"):
                alvo_aria = casca_marcada.locator(
                    f'button:not([data-view-name="comment-reply"])[aria-label="{frag}"]'
                )
                if await try_click_batch(alvo_aria):
                    return True
    except Exception:
        pass

    seletores_submit_reply = (
        ".comments-comment-box__submit-button",
        "button[data-control-name='comment_submit']",
        "button[type='submit']",
        "button.artdeco-button--primary",
        "button[class*='artdeco-button--primary']",
    )
    # Depois o submit da própria caixa de resposta: é o único alvo garantido.
    for sel in seletores_submit_reply:
        if await try_click_batch(bloco_reply.locator(sel)):
            return True

    escopos = [bloco_reply]
    for xp in (
        "xpath=ancestor::li[contains(@class,'comment')][1]",
        "xpath=ancestor::article[contains(@class,'comment')][1]",
        "xpath=ancestor::div[@data-view-name='comment-container'][1]",
        "xpath=ancestor::div[contains(@componentkey,'replaceableComment')][1]",
    ):
        anc = bloco_reply.locator(xp)
        if await anc.count() > 0:
            escopos.append(anc.first)

    for escopo in escopos:
        b2 = escopo.locator("button:not([data-view-name='comment-reply'])").filter(has_text=nome_btn)
        if await try_click_batch(b2):
            return True

        b = escopo.get_by_role("button", name=nome_btn)
        if await try_click_batch(b):
            return True

        for sel in (
            "button.artdeco-button--primary",
            "button[class*='artdeco-button--primary']",
            "button[type='submit']",
            ".comments-comment-box__submit-button",
            "button[data-control-name='comment_submit']",
        ):
            loc = escopo.locator(sel)
            if await try_click_batch(loc):
                return True

        for frag in (
            "Publicar", "publicar", "Comentar", "comentar", "Comment", "comment",
            "Post", "Submit", "Enviar", "enviar", "Responder", "Reply", "Send",
        ):
            loc = escopo.locator(f'button:not([data-view-name="comment-reply"])[aria-label*="{frag}"]')
            if await try_click_batch(loc):
                return True

    if estado["abortar"]:
        print("[AVISO] A caixa de resposta esvaziou sem confirmação. Não vou clicar de novo.")
        return False

    try:
        handle_caixa = await caixa.element_handle(timeout=3000)
    except Exception:
        handle_caixa = None
    if handle_caixa is not None:
        try:
            js_clicked = await item.evaluate(
                "(el, box) => {"
                + _JS_CAIXA_REPLY
                + _JS_CASCA_REPLY
                + """
                  const limite = el.closest(
                    "li.comments-comment-item," +
                    "article.comments-comment-entity," +
                    "[data-view-name='comment-container']," +
                    "div[componentkey*='replaceableComment']"
                  );
                  const nomes = /(publicar|post|comentar|comment|enviar|submit|done|concluir|publish|responder|reply|send)/i;
                  let node = box.parentElement;
                  let depth = 0;
                  while (node && depth < 8) {
                    const btns = Array.from(node.querySelectorAll("button:not([data-view-name='comment-reply'])"));
                    for (const btn of btns) {
                      if (btn.disabled) continue;
                      if (String(btn.getAttribute("aria-disabled") || "") === "true") continue;
                      const t = (btn.textContent || "").toLowerCase().replace(/\\s+/g, " ");
                      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
                      if (!nomes.test(t) && !nomes.test(aria)) continue;
                      if (!botBotaoDeReply(el, btn)) continue;
                      if (!botCascaPareceComposer(botCascaDoBotao(btn), btn)) continue;
                      btn.click();
                      return { clicked: true, depth: depth, text: t.slice(0, 40) };
                    }
                    if (limite && node === limite) break;
                    node = node.parentElement;
                    depth++;
                  }
                  return { clicked: false, depth: depth, text: "" };
                }""",
                handle_caixa,
            )
            if js_clicked and js_clicked.get("clicked") and await _confirmar_envio_resposta_thread(
                item, caixa, msg or ""
            ):
                return True
        except Exception:
            pass
        finally:
            try:
                await handle_caixa.dispose()
            except Exception:
                pass

    if await _caixa_vazia():
        print("[AVISO] Caixa de resposta vazia: não vou tentar publicar de novo.")
        return False

    try:
        await _clique_seguro(page, caixa, timeout_ms=3000)
        await asyncio.sleep(0.12)
        try:
            await caixa.press("Control+Enter")
        except Exception:
            await page.keyboard.press("Control+Enter")
        if await _confirmar_envio_resposta_thread(item, caixa, msg or ""):
            return True
    except Exception:
        pass

    return False


def _frases_biblioteca(cfg_lista) -> list[str]:
    """
    Compatível com formatos:
    - ["texto 1", "texto 2"]
    - [{"texto":"..."}, ...]
    A chave "ativo" é ignorada; toda frase não vazia entra.
    """
    out: list[str] = []
    for item in list(cfg_lista or []):
        if isinstance(item, str):
            txt = item.strip()
            if txt:
                out.append(txt)
            continue
        if isinstance(item, dict):
            txt = str(item.get("texto", "")).strip()
            if txt:
                out.append(txt)
    return out


def _modelos_texto(cfg_valor, fallback: str = "") -> list[str]:
    """
    Normaliza configuração de mensagens/modelos para lista de strings.
    Aceita: str, dict{"texto"}, list/tuple contendo str ou dict.
    """
    out: list[str] = []
    if isinstance(cfg_valor, str):
        txt = cfg_valor.strip()
        if txt:
            out.append(txt)
    elif isinstance(cfg_valor, dict):
        txt = str(cfg_valor.get("texto", "")).strip()
        if txt:
            out.append(txt)
    else:
        for item in list(cfg_valor or []):
            if isinstance(item, str):
                txt = item.strip()
                if txt:
                    out.append(txt)
            elif isinstance(item, dict):
                txt = str(item.get("texto", "")).strip()
                if txt:
                    out.append(txt)
    if not out and fallback.strip():
        out.append(fallback.strip())
    return out


def _openai_api_key_resolve() -> str:
    env_key = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if env_key:
        return env_key
    cfg_key = getattr(config, "OPENAI_API_KEY", "")
    return str(cfg_key or "").strip()


_TOM_LINHA_GUION: dict[str, str] = {
    "automatico": (
        "Tom adicional em ‘Automático’: faça parecer mais curioso se o tom for bem receptivo; "
        "mais crítico construtivo se houver frustração; mais didático se houver perguntas; "
        "leve bem-humorado se houver sarcasmo ou meme."
    ),
    "calmo": "Tom adicional: mantenha a resposta tranquila, sem provocação.",
    "bem humorado": (
        "Tom adicional: seja leve e com senso de humor contextual (sem soar forçado)."
    ),
    "critico": (
        "Tom adicional: seja analítico e firme ao questionar premissas quando fizer sentido."
    ),
    "didático": (
        "Tom adicional: seja objetivo ao explicar; evite ficar prolixo."
    ),
    "neutro": "Tom adicional: seja objetivo e direto, sem tom marcado.",
    "espelhamento": (
        "Tom adicional: reproduza a cadência energética do comentário (curto/long, formal/informal)."
    ),
    "curioso": (
        "Tom adicional: amplie ou faça uma pergunta pertinente, com curiosidade genuína."
    ),
}


def _ia_normalize_tom_chave(label: str) -> str:
    nf = unicodedata.normalize(
        "NFD", str(label or "").strip().casefold()
    )
    sem_acento = "".join(c for c in nf if unicodedata.category(c) != "Mn")
    return "".join(sem_acento.split())


def _ia_system_prompt_com_tom() -> str:
    base = str(getattr(config, "IA_SYSTEM_PROMPT", "") or "").strip()
    if not base:
        return ""
    k = _ia_normalize_tom_chave(str(getattr(config, "IA_TOM_RESPOSTA", "Automatico") or ""))
    map_gui = {
        "automatico": "automatico",
        "calmo": "calmo",
        "bemhumorado": "bem humorado",
        "critico": "critico",
        "didatico": "didático",
        "neutro": "neutro",
        "espelhamento": "espelhamento",
        "curioso": "curioso",
    }
    suf_key = map_gui.get(k, "automatico")
    suffix = _TOM_LINHA_GUION.get(suf_key, _TOM_LINHA_GUION["automatico"])
    return base + "\n\n" + suffix


async def _extrair_texto_post_na_pagina(page) -> str:
    seletores = (
        '.feed-shared-update-v2__description span[dir="ltr"]',
        '.feed-shared-update-v2__description',
        'article[class*="feed-shared-update-v2"] span[dir="ltr"]',
        'div[data-highlighted-comment-urn] span[dir="ltr"]',
    )
    melhor = ""
    for sel in seletores:
        loc = page.locator(sel).first
        try:
            vis = await loc.is_visible(timeout=2500)
        except Exception:
            vis = False
        if not vis:
            continue
        try:
            txt = (await loc.inner_text()).strip()
        except Exception:
            continue
        if len(txt) > len(melhor):
            melhor = txt
    return melhor.strip()


def _montar_post_para_ia_user_prompt(texto_dom: str) -> str:
    ctx = str(getattr(config, "CONTEXTO_POST", "") or "").strip()
    usar_dom = bool(getattr(config, "IA_USAR_TEXTO_POST_DA_PAGINA", True))
    dom = texto_dom.strip()
    if usar_dom and dom:
        if ctx:
            return ctx + "\n--- Texto do post na pagina ---\n" + dom
        return dom
    if ctx:
        return ctx
    if dom:
        return dom
    return "(texto do post não disponível — use apenas o comentário.)"


def _pos_processamento_resposta_ia(texto_bruto: str) -> str:
    s = (texto_bruto or "").strip()
    if getattr(config, "IA_QUEBRA_LINHA_APOS_PONTO", False):
        s = re.sub(r"([\.!?])(\s+)(?=\S)", r"\1\n", s)
        s = re.sub(r"\.{4,}", "...", s)
    max_c = int(getattr(config, "IA_MAX_CARACTERES", 500) or 500)
    if max_c > 0 and len(s) > max_c:
        s = s[:max_c].rstrip()
    return s.strip()


async def _gerar_resposta_openai_ia(
    *,
    post_prompt: str,
    nome_interno: str,
    texto_comentario: str,
) -> tuple[Optional[str], str]:
    key = _openai_api_key_resolve()
    modelo = str(getattr(config, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()
    retries = max(0, int(getattr(config, "OPENAI_MAX_RETRIES", 2)))
    timeout_sec = float(getattr(config, "OPENAI_TIMEOUT_SEGUNDOS", 30))
    limite_car = str(int(getattr(config, "IA_MAX_CARACTERES", 280) or 280))
    tmpl = str(getattr(config, "IA_USER_TEMPLATE", "") or "").strip()

    if AsyncOpenAI is None:
        return (
            None,
            "Pacote OpenAI não instalado; instale com: pip install openai",
        )
    if not key:
        return None, "OPENAI_API_KEY ausente (config ou variável de ambiente)"

    sistem = _ia_system_prompt_com_tom()
    if not sistem or not tmpl:
        return None, "IA_SYSTEM_PROMPT ou IA_USER_TEMPLATE vazio"

    usuario = tmpl.format(
        post=post_prompt,
        nome=(nome_interno or "(nome omitido para publicação)").strip(),
        comentario=texto_comentario.strip(),
        limite=limite_car,
    )
    temp_ia = getattr(config, "IA_TEMPERATURA", None)
    try:
        temperature = (
            float(temp_ia)
            if temp_ia is not None and str(temp_ia).strip() != ""
            else 0.7
        )
    except (TypeError, ValueError):
        temperature = 0.7

    client = AsyncOpenAI(api_key=key, timeout=timeout_sec)

    tentativas = 1 + retries
    ultimo_erro = ""
    for _ in range(tentativas):
        try:
            resp = await client.chat.completions.create(
                model=modelo,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": sistem},
                    {"role": "user", "content": usuario},
                ],
            )
            choice0 = resp.choices[0] if resp.choices else None
            texto = str((choice0.message.content if choice0 else "") or "").strip()
            texto = _pos_processamento_resposta_ia(texto)
            if texto:
                return texto, ""
            ultimo_erro = "modelo_devolveu_resposta_vazia"
        except Exception as exc:
            ultimo_erro = str(exc)

    erro_acumulado = ultimo_erro or "falha_desconhecida"
    fb = str(getattr(config, "IA_FALLBACK_RESPOSTA", "") or "").strip()
    if fb:
        return _pos_processamento_resposta_ia(fb), erro_acumulado
    return None, erro_acumulado


def _remover_acentos(txt: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def palavra_chave_casada(texto: str, palavras: list[str]) -> Optional[str]:
    """Retorna a palavra-chave configurada que casou com o texto, ou None."""
    if not palavras:
        return None

    tl = _remover_acentos(texto.lower())
    inteira = getattr(config, "PALAVRA_CHAVE_SO_PALAVRA_INTEIRA", True)
    
    for raw in palavras:
        # Removemos os acentos da palavra-chave cadastrada (pra garantir)
        p = _remover_acentos((raw or "").lower().strip())
        if not p:
            continue
            
        if inteira:
            if " " in p:
                if p in tl:
                    return str(raw).strip()
                continue
            try:
                pat = rf"(?<!\w){re.escape(p)}(?!\w)"
                if re.search(pat, tl, flags=re.UNICODE):
                    return str(raw).strip()
            except re.error:
                if p in tl:
                    return str(raw).strip()
        else:
            if p in tl:
                return str(raw).strip()
    return None


def texto_bate_palavra_chave(texto: str, palavras: list[str]) -> bool:
    """Só True se o comentário contiver a palavra-chave conforme regras do config."""
    return palavra_chave_casada(texto, palavras) is not None


_STOPWORDS_KW_POST = frozenset(
    {
        "a", "o", "os", "as", "um", "uma", "uns", "umas", "de", "da", "do", "das",
        "dos", "e", "em", "no", "na", "nos", "nas", "por", "para", "pra", "com",
        "sem", "seu", "sua", "seus", "suas", "meu", "minha", "que", "se", "ao",
        "aos", "ou", "ja", "já", "eu", "voce", "você", "voce", "aqui", "agora",
        "isso", "este", "esta", "esse", "essa", "isto", "aqui", "la", "lá",
        "post", "comentario", "comentário", "comentarios", "comentários",
        "palavra", "chave", "inbox", "direct", "linkedin", "vaga", "emprego",
        "trabalho", "home", "office", "clique", "clica", "link", "abaixo",
        "acima", "mais", "menos", "muito", "pouco", "tudo", "toda", "todo",
        "the", "and", "for", "with", "your", "this", "that", "from", "comment",
        "comments", "keyword", "message", "dm",
    }
)


def _candidato_kw_parece_valido(candidato: str) -> bool:
    c = " ".join((candidato or "").strip().split())
    if len(c) < 2 or len(c) > 40:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]", c):
        return False
    toks = [_remover_acentos(t.lower()) for t in re.split(r"\s+", c) if t]
    if not toks:
        return False
    if all(t in _STOPWORDS_KW_POST for t in toks):
        return False
    if len(toks) == 1 and toks[0] in _STOPWORDS_KW_POST:
        return False
    return True


def _limpar_candidato_kw(candidato: str) -> str:
    """Corta o candidato na primeira stopword (evita 'JAVA nos comentários')."""
    toks = []
    for t in (candidato or "").strip().split():
        limpo = _remover_acentos(t.lower()).strip(".,!?:;\"'“”‘’")
        if limpo in _STOPWORDS_KW_POST:
            break
        toks.append(t.strip(".,!?:;\"'“”‘’"))
    return " ".join(toks).strip()


def detectar_palavra_chave_no_post(texto_post: str) -> Optional[str]:
    """
    Lê o texto do post e tenta achar a palavra-chave pedida nos comentários.
    Só deve ser usada quando PALAVRAS_CHAVE estiver vazio.
    """
    texto = " ".join((texto_post or "").split())
    if not texto:
        return None

    padroes = (
        (
            r"""(?i)(?:palavra[\s\-]*chave|keyword)\s*[:=\-–—]?\s*[\"'“”‘’]?([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\s\-]{0,40})[\"'“”‘’]?""",
            re.UNICODE,
        ),
        (
            r"""(?i)(?:coment[ea]|escrev[ae]|mand[ea]|envi[ea]|digite|digita|deixe|deixa)\s+(?:a\s+)?(?:palavra(?:[\s\-]*chave)?\s+)?[\"'“”‘’]([^\"'“”‘’]{1,40})[\"'“”‘’]""",
            re.UNICODE,
        ),
        # Verbo case-insensitive; token com maiúscula no começo (JAVA / Gerente Road).
        (
            r"""(?:coment[ea]|escrev[ae]|mand[ea]|envi[ea]|digite|digita)\s+(?:a\s+)?(?:palavra(?:[\s\-]*chave)?\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÜ0-9][A-Za-zÀ-ÿ0-9\-]{1,30}(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÜ0-9][A-Za-zÀ-ÿ0-9\-]{0,20}){0,3})""",
            re.IGNORECASE | re.UNICODE,
        ),
        (
            r"""(?i)(?:coment[ea]|escrev[ae]|mand[ea]|envi[ea])\s+[\"'“”‘’]?([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\-]{1,30})[\"'“”‘’]?\s+(?:nos?\s+coment|no\s+coment|aqui|abaixo)""",
            re.UNICODE,
        ),
    )
    for pat, flags in padroes:
        # IGNORECASE no padrão 3 tornaria [A-Z] aceitar minúscula; aplica só no verbo.
        if flags & re.IGNORECASE:
            m = re.search(pat, texto, flags=re.IGNORECASE)
            if m:
                # Revalida o grupo capturado com case-sensitive: 1º char maiúsculo/dígito.
                cand_raw = m.group(1) or ""
                if not re.match(
                    r"^[A-ZÁÉÍÓÚÂÊÔÃÕÜ0-9]",
                    cand_raw,
                ):
                    m = None
        else:
            m = re.search(pat, texto, flags=flags)
        if not m:
            continue
        candidato = _limpar_candidato_kw(m.group(1) or "")
        if _candidato_kw_parece_valido(candidato):
            return candidato

    # Aspas próximas de verbos de "comente/manda".
    for m in re.finditer(r"""[\"'“”‘’]([^\"'“”‘’]{1,40})[\"'“”‘’]""", texto):
        candidato = _limpar_candidato_kw(m.group(1) or "")
        if not _candidato_kw_parece_valido(candidato):
            continue
        janela = texto[max(0, m.start() - 50) : m.end() + 30].lower()
        if re.search(
            r"coment|escrev|mand|envi|palavra|keyword|inbox|direct",
            _remover_acentos(janela),
        ):
            return candidato
    return None


def variantes_palavra_chave(palavra: str) -> list[str]:
    """Gera variações simples (original / lower / UPPER / Title) para o filtro."""
    base = " ".join((palavra or "").strip().split())
    if not base:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in (base, base.lower(), base.upper(), base.title()):
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def aplicar_placeholders(
    texto: str,
    *,
    nome: str = "",
    palavra_chave: str = "",
) -> str:
    """
    Troca {palavra-chave}/{palavra_chave}/{nome} no texto.
    Sem palavra reconhecida, usa \"essa palavra\".
    """
    out = texto or ""
    kw = (palavra_chave or "").strip() or "essa palavra"
    nome_v = (nome or "").strip()
    for chave, valor in (
        ("{palavra-chave}", kw),
        ("{palavra_chave}", kw),
        ("{nome}", nome_v),
    ):
        out = out.replace(chave, valor)
    return out


async def texto_corpo_comentario_apenas(item) -> str:
    """
    Texto do comentário sem chrome da UI (Gostei, Responder, rótulo Palavras, etc.).
    Usado só para filtro de PALAVRAS_CHAVE.
    """
    try:
        t = await item.evaluate(r"""(el) => {
          function pickCommentNode(node) {
            const ownLi = node.closest("li.comments-comment-item");
            if (ownLi) return ownLi;
            const ownEntity = node.closest("[data-test-comment-entity-urn]");
            if (ownEntity) return ownEntity;
            const container = node.closest("[data-view-name='comment-container']") || node.closest("div[componentkey*='replaceableComment']");
            if (container) return container;
            return node;
          }
          function isUiNoise(line) {
            const l = (line || "").trim();
            if (!l) return true;
            // Contadores de reação/resposta (ex.: "0", "13") NÃO são corpo do comentário.
            if (/^\d+$/.test(l)) return true;
            return /^(Gostei|Like|Curtir|Responder|Reply|Seguir|Follow|Autor|Author|Você|You|Exibir tradução|Show translation|\d+\s+respostas?|\d+\s+replies?)$/i.test(l)
              || /^\d+\s*(?:min|mins|h|d|sem|sem\.|hour|hours|day|days)(?:\s|$)/i.test(l)
              || /^(?:Perfil|Premium)\s+\d/i.test(l)
              || /^Usuário verificado$/i.test(l)
              || /^Buscando emprego$/i.test(l);
          }
          function extrairCorpoDoInnerText(raw) {
            const lines = String(raw || "").split(/\n+/).map((l) => l.trim()).filter(Boolean);
            if (!lines.length) return "";
            const candidatas = lines.filter((l) =>
              !isUiNoise(l) &&
              !/ • /.test(l) &&
              !/Perfil\s+\d/i.test(l) &&
              !/Buscando emprego/i.test(l)
            );
            if (candidatas.length) return candidatas.join("\n");
            return "";
          }
          const cn = pickCommentNode(el);
          const sels = [
            ".comments-comment-item__main-content",
            ".comments-comment-item__inline-show-more-text",
            ".update-components-text",
            "[class*='comment-item__main-content']",
            "[class*='comment__text']",
            ".feed-shared-inline-show-more-text"
          ];
          for (const s of sels) {
            const nodes = cn.querySelectorAll?.(s) || [];
            for (const n of nodes) {
              const tx = (n.textContent || "").trim();
              if (tx && !isUiNoise(tx) && tx.length >= 2) return tx;
            }
          }
          const extraido = extrairCorpoDoInnerText(cn.innerText || "");
          if (extraido) return extraido;
          // Último recurso: innerText completo (permite casar palavra-chave no bloco).
          return (cn.innerText || "").trim();
        }""")
        return (t or "").strip()
    except Exception:
        return ""


def _chave_perfil(perfil_url: str) -> str:
    base = (perfil_url or "").strip()
    if not base:
        return ""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", base.lower())
    if m:
        return m.group(1).strip("/")
    return base.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()


async def ja_respondi_esta_pessoa_no_post(page, perfil_url: str) -> bool:
    """
    Procura na página inteira uma resposta MINHA que mencione este perfil.

    Ao responder, o LinkedIn insere a menção da pessoa no início da resposta.
    Então, se existe um comentário meu que aponta para o perfil dela, já
    respondi — não importa onde essa resposta esteja no DOM nem qual texto foi
    usado (frases antigas, editadas ou escritas à mão também contam).
    """
    alvo = _chave_perfil(perfil_url)
    slug = _slug_efetivo_config().lower()
    if not alvo or not slug:
        return False
    try:
        return await page.evaluate(
            """
            ([alvo, slug, souAutor]) => {
              const meus = [slug];
              if (slug.includes("_")) meus.push(slug.replace(/_/g, "-"));
              if (slug.includes("-")) meus.push(slug.replace(/-/g, "_"));
              const ehMeuLink = (h) =>
                meus.some((v) => v && (h || "").toLowerCase().includes("/in/" + v));

              const NOS =
                "[data-view-name='comment-container'], " +
                "div[componentkey*='replaceableComment'], " +
                "li.comments-comment-item, " +
                "article.comments-comment-entity, " +
                "[data-test-comment-entity-urn]";

              for (const no of document.querySelectorAll(NOS)) {
                const links = Array.from(no.querySelectorAll("a[href*='/in/']"));
                if (links.length < 2) continue;

                // O primeiro link /in/ de um comentário é sempre o autor dele.
                const autor = links[0].getAttribute("href") || "";
                if (!ehMeuLink(autor)) {
                  if (!souAutor) continue;
                  const selo = Array.from(no.querySelectorAll("span, div")).some((b) =>
                    /^(Autor|Author|Você|You)$/i.test((b.textContent || "").trim())
                  );
                  if (!selo) continue;
                }

                for (const a of links.slice(1)) {
                  const h = (a.getAttribute("href") || "").toLowerCase();
                  if (h.includes("/in/" + alvo)) return true;
                }
              }
              return false;
            }
            """,
            [alvo, slug, bool(getattr(config, "SOU_AUTOR_DO_POST", False))],
        )
    except Exception:
        return False


async def dados_autor_comentario(item) -> dict:
    """Extrai nome e URL de perfil do autor no bloco de comentário."""
    try:
        data = await item.evaluate(
            """(el) => {
              function pickCommentNode(node) {
                const ownLi = node.closest("li.comments-comment-item");
                if (ownLi) return ownLi;
                const ownEntity = node.closest("[data-test-comment-entity-urn]");
                if (ownEntity) return ownEntity;
                return node;
              }
              const cn = pickCommentNode(el);
              const links = Array.from(cn.querySelectorAll("a[href*='/in/']"));
              let profileHref = "";
              for (const a of links) {
                const h = (a.getAttribute("href") || "").trim();
                if (!h) continue;
                profileHref = h;
                const txt = (a.textContent || "").trim();
                if (txt) {
                  return { nome: txt, perfil_url: profileHref };
                }
              }
              const nomeEl = cn.querySelector(
                ".comments-post-meta__name-text, .comments-comment-meta__description-title, [class*='name-text']"
              );
              return {
                nome: (nomeEl?.textContent || "").trim(),
                perfil_url: profileHref
              };
            }"""
        ) or {}
    except Exception:
        data = {}

    nome = str(data.get("nome") or "").strip()
    perfil_url = str(data.get("perfil_url") or "").strip()
    if perfil_url.startswith("/"):
        perfil_url = f"https://www.linkedin.com{perfil_url}"
    return {"nome": nome, "perfil_url": perfil_url}


def montar_mensagem_perfil(templates_cfg, *, nome: str, palavra_chave: str) -> str:
    modelos = _modelos_texto(
        templates_cfg,
        "Ooi, vi o seu comentário sobre {palavra-chave}, me envia o seu Whatsapp? "
        "Vamos trabalhar e resolver isso!",
    )
    base = random.choice(modelos)
    return aplicar_placeholders(base, nome=nome or "", palavra_chave=palavra_chave or "")


async def _clicar_primeiro_visivel(locator, limite: int = 8) -> bool:
    try:
        n = await locator.count()
    except Exception:
        return False
    for i in range(min(n, limite)):
        el = locator.nth(i)
        try:
            if await el.is_visible():
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def enviar_mensagem_no_perfil(context, perfil_url: str, texto: str) -> tuple[bool, str]:
    """
    Retorna (ok, status):
    - ok=True: dm_sent | connect_note_sent
    - ok=False: dm_unreachable
    """
    page_perfil = None
    tentar_conectar = bool(getattr(config, "TENTAR_CONECTAR_COM_NOTA_SEM_MENSAGEM", True))
    try:
        page_perfil = await context.new_page()
        await page_perfil.goto(perfil_url, wait_until="domcontentloaded", timeout=90_000)
        try:
            await page_perfil.wait_for_load_state("load", timeout=20_000)
        except Exception:
            pass
        await asyncio.sleep(1.2)
    except Exception:
        if page_perfil is not None:
            try:
                await page_perfil.close()
            except Exception:
                pass
        return False, "dm_unreachable"

    try:
        usar_inmail_premium = bool(getattr(config, "USAR_INMAIL_PREMIUM", True))
        inmail_assunto = str(getattr(config, "INMAIL_ASSUNTO", "Incômodo") or "Incômodo").strip()

        async def _enviar_em_composer(selectors_editor: tuple[str, ...], status_ok: str) -> tuple[bool, str]:
            editor = None
            for sel in selectors_editor:
                loc = page_perfil.locator(sel)
                try:
                    if await loc.count() > 0:
                        await loc.first.wait_for(state="visible", timeout=3000)
                        editor = loc.first
                        break
                except Exception:
                    continue
            if editor is None:
                return False, "dm_unreachable"

            await editor.click()
            await digitar_humano(editor, texto, preservar_mencao=False)
            btn_send = page_perfil.get_by_role("button", name=re.compile(r"Enviar|Send|Enviar mensagem|Send message", re.I))
            btn_send_alt = page_perfil.locator(
                "button.msg-form__send-button, button[aria-label*='Send'], button[aria-label*='Enviar'], button[type='submit']"
            )
            if await _clicar_primeiro_visivel(btn_send) or await _clicar_primeiro_visivel(btn_send_alt):
                return True, status_ok
            try:
                await editor.press("Control+Enter")
                await asyncio.sleep(0.5)
                return True, status_ok
            except Exception:
                return False, "dm_unreachable"

        async def _aceitar_conexao_se_disponivel() -> bool:
            for nome in (
                re.compile(r"^Aceitar$", re.I),
                re.compile(r"^Accept$", re.I),
                re.compile(r"Aceitar convite|Accept invitation", re.I),
            ):
                btn = page_perfil.get_by_role("button", name=nome)
                if await _clicar_primeiro_visivel(btn):
                    await asyncio.sleep(0.8)
                    return True
            return False

        async def _abrir_composer_mensagem() -> bool:
            botao_msg = page_perfil.get_by_role("button", name=re.compile(r"Mensagem|Message", re.I))
            botao_msg_alt = page_perfil.locator("a,button").filter(
                has_text=re.compile(r"Mensagem|Message|Enviar mensagem|Send message", re.I)
            )
            if await _clicar_primeiro_visivel(botao_msg) or await _clicar_primeiro_visivel(botao_msg_alt):
                return True
            botao_mais = page_perfil.get_by_role("button", name=re.compile(r"Mais|More", re.I))
            if not await _clicar_primeiro_visivel(botao_mais):
                return False
            await asyncio.sleep(0.4)
            menu_msg = page_perfil.get_by_role("menuitem", name=re.compile(r"Mensagem|Message", re.I))
            menu_msg_alt = page_perfil.locator("[role='menuitem'],button,a").filter(
                has_text=re.compile(r"Mensagem|Message", re.I)
            )
            return await _clicar_primeiro_visivel(menu_msg) or await _clicar_primeiro_visivel(menu_msg_alt)

        async def _abrir_composer_inmail() -> bool:
            inmail_btn = page_perfil.get_by_role(
                "button",
                name=re.compile(r"InMail|Mensagem InMail|Enviar InMail|Message via InMail|Send InMail", re.I),
            )
            inmail_btn_alt = page_perfil.locator("a,button").filter(
                has_text=re.compile(r"InMail|Mensagem InMail|Enviar InMail|Message via InMail|Send InMail", re.I)
            )
            if await _clicar_primeiro_visivel(inmail_btn) or await _clicar_primeiro_visivel(inmail_btn_alt):
                return True

            botao_mais = page_perfil.get_by_role("button", name=re.compile(r"Mais|More", re.I))
            if not await _clicar_primeiro_visivel(botao_mais):
                return False
            await asyncio.sleep(0.4)
            menu_inmail = page_perfil.get_by_role(
                "menuitem",
                name=re.compile(r"InMail|Mensagem InMail|Enviar InMail|Message via InMail|Send InMail", re.I),
            )
            menu_inmail_alt = page_perfil.locator("[role='menuitem'],button,a").filter(
                has_text=re.compile(r"InMail|Mensagem InMail|Enviar InMail|Message via InMail|Send InMail", re.I)
            )
            return await _clicar_primeiro_visivel(menu_inmail) or await _clicar_primeiro_visivel(menu_inmail_alt)

        async def _enviar_inmail_modal() -> tuple[bool, str]:
            assunto_loc = None
            for sel in (
                "input[placeholder*='Assunto']",
                "input[aria-label*='Assunto']",
                "input[placeholder*='Subject']",
                "input[aria-label*='Subject']",
                "input[name*='subject']",
            ):
                loc = page_perfil.locator(sel)
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        assunto_loc = loc.first
                        break
                except Exception:
                    continue

            body_loc = None
            for sel in (
                "textarea[placeholder*='Escreva uma mensagem']",
                "textarea[placeholder*='mensagem']",
                "textarea[placeholder*='message']",
                "textarea[name='message']",
                "div[role='textbox'][contenteditable='true']",
            ):
                loc = page_perfil.locator(sel)
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        body_loc = loc.first
                        break
                except Exception:
                    continue

            if body_loc is None:
                return False, "dm_unreachable"

            try:
                if assunto_loc is not None:
                    await assunto_loc.click()
                    await assunto_loc.fill(inmail_assunto)
            except Exception:
                pass

            try:
                await body_loc.click()
                try:
                    await body_loc.fill(texto)
                except Exception:
                    await body_loc.press_sequentially(texto, delay=4)
            except Exception:
                return False, "dm_unreachable"

            btn_send = page_perfil.get_by_role("button", name=re.compile(r"Enviar|Send", re.I))
            btn_send_alt = page_perfil.locator(
                "button[aria-label*='Enviar'], button[aria-label*='Send'], button[data-control-name*='send']"
            )
            if await _clicar_primeiro_visivel(btn_send) or await _clicar_primeiro_visivel(btn_send_alt):
                return True, "inmail_sent"

            try:
                await body_loc.press("Control+Enter")
                await asyncio.sleep(0.4)
                return True, "inmail_sent"
            except Exception:
                return False, "dm_unreachable"

        abriu_msg = await _abrir_composer_mensagem()
        if not abriu_msg:
            aceitou = await _aceitar_conexao_se_disponivel()
            if aceitou:
                abriu_msg = await _abrir_composer_mensagem()
        if abriu_msg:
            await asyncio.sleep(1.2)
            ok_send, status_send = await _enviar_em_composer(
                (
                ".msg-form__contenteditable[contenteditable='true']",
                ".msg-form__msg-content-container [contenteditable='true']",
                "div[role='textbox'][contenteditable='true']",
                "div.msg-form__contenteditable",
                "textarea[name='message']",
                "textarea",
                ),
                "dm_sent",
            )
            if ok_send:
                return ok_send, status_send

            if status_send == "dm_unreachable":
                # Tentativa extra: clica novamente no botão Mensagem e revarre editor.
                await _abrir_composer_mensagem()
                await asyncio.sleep(0.8)
                ok_send_retry, status_send_retry = await _enviar_em_composer(
                    (
                    ".msg-form__contenteditable[contenteditable='true']",
                    "div[role='textbox'][contenteditable='true']",
                    "div.msg-form__contenteditable",
                    "textarea[name='message']",
                    "textarea",
                    ),
                    "dm_sent",
                )
                if ok_send_retry:
                    return ok_send_retry, status_send_retry

        if usar_inmail_premium:
            abriu_inmail = await _abrir_composer_inmail()
            if abriu_inmail:
                await asyncio.sleep(1.0)
                ok_inmail, status_inmail = await _enviar_inmail_modal()
                if ok_inmail:
                    return ok_inmail, status_inmail

        if not tentar_conectar:
            return False, "dm_unreachable"

        botao_connect = page_perfil.get_by_role("button", name=re.compile(r"Conectar|Connect", re.I))
        clicou_connect = await _clicar_primeiro_visivel(botao_connect)
        if not clicou_connect:
            botao_mais = page_perfil.get_by_role("button", name=re.compile(r"Mais|More", re.I))
            if await _clicar_primeiro_visivel(botao_mais):
                await asyncio.sleep(0.4)
                menu_connect = page_perfil.get_by_role("menuitem", name=re.compile(r"Conectar|Connect", re.I))
                menu_connect_btn = page_perfil.locator("[role='menuitem'],button").filter(
                    has_text=re.compile(r"Conectar|Connect", re.I)
                )
                clicou_connect = await _clicar_primeiro_visivel(menu_connect) or await _clicar_primeiro_visivel(
                    menu_connect_btn
                )
        if not clicou_connect:
            return False, "dm_unreachable"

        await asyncio.sleep(0.7)
        add_note = page_perfil.get_by_role("button", name=re.compile(r"Adicionar nota|Add a note", re.I))
        add_note_alt = page_perfil.locator("button").filter(has_text=re.compile(r"Adicionar nota|Add a note", re.I))
        if not (await _clicar_primeiro_visivel(add_note) or await _clicar_primeiro_visivel(add_note_alt)):
            return False, "dm_unreachable"

        await asyncio.sleep(0.4)
        nota = None
        for sel in ("textarea#custom-message", "textarea[name='message']", "textarea"):
            loc = page_perfil.locator(sel)
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    nota = loc.first
                    break
            except Exception:
                continue
        if nota is None:
            return False, "dm_unreachable"

        await nota.click()
        await digitar_humano(nota, texto, preservar_mencao=False)
        btn_enviar_convite = page_perfil.get_by_role(
            "button", name=re.compile(r"Enviar|Send|Enviar convite|Send now|Done", re.I)
        )
        btn_enviar_convite_alt = page_perfil.locator("button").filter(
            has_text=re.compile(r"Enviar|Send", re.I)
        )
        if await _clicar_primeiro_visivel(btn_enviar_convite) or await _clicar_primeiro_visivel(
            btn_enviar_convite_alt
        ):
            return True, "connect_note_sent"
        return False, "dm_unreachable"
    finally:
        if page_perfil is not None:
            try:
                await page_perfil.close()
            except Exception:
                pass


async def fio_ja_tem_qualquer_resposta(item) -> bool:
    """True = neste fio já existe pelo menos uma resposta aninhada (thread não está ‘zerada’)."""
    try:
        return await item.evaluate(r"""(el) => {
          function threadRoot(node) {
            let lazyColumn = node.closest("[data-component-type='LazyColumn']");
            if (lazyColumn) {
              let current = node;
              while (current && current.parentElement !== lazyColumn) {
                current = current.parentElement;
              }
              if (current) return current;
            }
            const lis = [];
            for (let n = node; n; n = n.parentElement) {
              if (n.matches?.("li.comments-comment-item")) lis.push(n);
              if (n.matches?.("ul.comments-comments-list") ||
                  n.matches?.("[class*='comments-comments-list']")) break;
            }
            if (lis.length) return lis[lis.length - 1];
            let baseNode = node.closest("div[componentkey*='replaceableComment']");
            if (!baseNode) baseNode = node.closest("[data-view-name='comment-container']");
            if (baseNode && baseNode.parentElement) return baseNode.parentElement;
            return node.closest("[data-test-comment-entity-urn]") || node;
          }
          const root = threadRoot(el);
          if (!root || !root.querySelectorAll) return false;

          const ownContainer = el.closest("[data-view-name='comment-container']") ||
            el.closest("div[componentkey*='replaceableComment']") ||
            el.closest("li.comments-comment-item");
          if (ownContainer) {
            const nestedInThread = ownContainer.querySelectorAll(
              "[data-view-name='comment-container'], div[componentkey*='replaceableComment'], li.comments-comment-item"
            );
            for (const c of nestedInThread) {
              if (c !== ownContainer) return true;
            }
          }

          const nested = root.querySelectorAll(":scope li.comments-comment-item");
          if (nested.length > 0) return true;
          const alt = root.querySelector(
            "[class*='comments-comment-entity--reply']," +
            " .comments-replies-list li," +
            " [class*='reply-comment']"
          );
          if (alt !== null) return true;
          const buttons = root.querySelectorAll("button");
          for (const b of buttons) {
            const aria = (b.getAttribute("aria-label") || "").toLowerCase();
            const txtBtn = (b.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
            if (/\d+\s+(resposta|respostas|reply|replies)\b/.test(aria)) return true;
            if (/^\d+\s+(resposta|respostas|reply|replies)\b/.test(txtBtn)) return true;
            if (/\d+\s+(resposta|respostas|reply|replies)\b/.test(txtBtn)) return true;
          }
          const txt = (root.innerText || "").toLowerCase();
          if (/\b\d+\s+(resposta|respostas|reply|replies)\b/.test(txt)) return true;
          return false;
        }""")
    except Exception:
        return False


async def _diagnostico_fio_respostas(item) -> dict:
    try:
        return await item.evaluate(r"""(el) => {
          function threadRoot(node) {
             let lazyColumn = node.closest("[data-component-type='LazyColumn']");
             if (lazyColumn) {
                 let current = node;
                 while (current && current.parentElement !== lazyColumn) {
                     current = current.parentElement;
                 }
                 if (current) return current;
             }
             const lis = [];
             for (let n = node; n; n = n.parentElement) {
               if (n.matches?.("li.comments-comment-item")) lis.push(n);
               if (n.matches?.("ul.comments-comments-list") ||
                   n.matches?.("[class*='comments-comments-list']")) break;
             }
             if (lis.length) return lis[lis.length - 1];
             let baseNode = node.closest("div[componentkey*='replaceableComment']");
             if (!baseNode) baseNode = node.closest("[data-view-name='comment-container']");
             if (baseNode && baseNode.parentElement) return baseNode.parentElement;
             return node.closest("[data-test-comment-entity-urn]") || node;
          }
          const root = threadRoot(el);
          if (!root) return {rootFound: false};
          const nestedCount = root.querySelectorAll(":scope li.comments-comment-item").length;
          const hasAltReply = !!root.querySelector(
            "[class*='comments-comment-entity--reply']," +
            " .comments-replies-list li," +
            " [class*='reply-comment']"
          );
          let hasReplyHintButton = false;
          const btns = root.querySelectorAll("button");
          for (const b of btns) {
            const aria = (b.getAttribute("aria-label") || "").toLowerCase();
            const txtBtn = (b.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
            if (/\d+\s+(resposta|respostas|reply|replies)\b/.test(aria) ||
                /^\d+\s+(resposta|respostas|reply|replies)\b/.test(txtBtn) ||
                /\d+\s+(resposta|respostas|reply|replies)\b/.test(txtBtn)) {
              hasReplyHintButton = true;
              break;
            }
          }
          const txt = (root.innerText || "").toLowerCase();
          const hasReplyCounterText = /\b\d+\s+(resposta|respostas|reply|replies)\b/.test(txt);
          const ownLi = el.closest("li.comments-comment-item");
          return {
            rootFound: true,
            ownLiClass: ownLi?.className || "",
            nestedCount,
            hasAltReply,
            hasReplyHintButton,
            hasReplyCounterText
          };
        }""")
    except Exception:
        return {"rootFound": False}


# Perfil da conta logada, descoberto no início da execução.
_SLUG_DETECTADO_SESSAO = ""


async def detectar_slug_da_conta(page) -> str:
    """
    Descobre o /in/<perfil> da conta que está logada nesta sessão.

    É o que permite ao robô reconhecer os próprios comentários e respostas.
    Sem isso ele responde às próprias respostas e reprocessa fios já atendidos.
    """
    global _SLUG_DETECTADO_SESSAO

    slug = ""
    try:
        await page.goto(
            "https://www.linkedin.com/in/me/", wait_until="domcontentloaded", timeout=45_000
        )
        await asyncio.sleep(1.5)
        m = re.search(r"/in/([^/?#]+)", page.url or "")
        if m:
            slug = m.group(1)
    except Exception:
        pass

    if not slug or slug.lower() in ("me", "unavailable"):
        try:
            href = await page.evaluate(
                """() => {
                  const sels = [
                    ".global-nav__me a[href*='/in/']",
                    ".global-nav__me-content a[href*='/in/']",
                    ".feed-identity-module a[href*='/in/']",
                    ".profile-rail-card a[href*='/in/']",
                    "a[data-control-name='identity_profile_photo'][href*='/in/']",
                    "a[href*='/in/'][data-view-name='profile-card']",
                  ];
                  for (const s of sels) {
                    const a = document.querySelector(s);
                    const h = a && (a.getAttribute("href") || "");
                    if (h && h.includes("/in/")) return h;
                  }
                  return "";
                }"""
            )
            m = re.search(r"/in/([^/?#]+)", str(href or ""))
            if m:
                slug = m.group(1)
        except Exception:
            pass

    slug = (slug or "").strip().strip("/").lower()
    if not slug or slug in ("me", "unavailable"):
        print(
            "[AVISO] Não consegui identificar o perfil desta conta automaticamente. "
            "Preencha 'Meu perfil (/in/)' na aba Sessão e navegador."
        )
        return ""

    _SLUG_DETECTADO_SESSAO = slug
    setattr(config, "MEU_PERFIL_IN_SLUG_DETECTADO", slug)
    print(f"[Info] Perfil desta conta: /in/{slug}")

    try:
        from settings_manager import atualizar_settings

        atualizar_settings({"MEU_PERFIL_IN_SLUG_DETECTADO": slug})
    except Exception:
        pass
    return slug


def _slug_da_url_post() -> str:
    """
    Extrai o perfil de uma URL do tipo /posts/<perfil>_<titulo>-activity-<id>-<hash>.

    O corte no primeiro "_" é essencial: sem ele sobrava o título inteiro
    ("perfil_vaga-home-office-...") e nenhum link /in/ batia.
    """
    url = (getattr(config, "URL_POST", "") or "").lower()
    m = re.search(r"linkedin\.com/posts/([^/?#]+)", url)
    if not m:
        return ""
    chunk = re.split(r"_", m.group(1), maxsplit=1)[0]
    chunk = re.sub(r"-activity.*$", "", chunk, flags=re.I)
    return chunk.strip("_-/")


def _slug_efetivo_config() -> str:
    """
    Perfil desta conta, na ordem: campo manual, detectado na sessão, detectado
    em execução anterior e, por último, derivado da URL do post.
    """
    manual = (getattr(config, "MEU_PERFIL_IN_SLUG", "") or "").strip().strip("/")
    if manual:
        return manual

    if _SLUG_DETECTADO_SESSAO:
        return _SLUG_DETECTADO_SESSAO

    salvo = (getattr(config, "MEU_PERFIL_IN_SLUG_DETECTADO", "") or "").strip().strip("/")
    if salvo:
        return salvo

    if not getattr(config, "TENTAR_SLUG_A_PARTIR_DA_URL_POST", True):
        return ""
    return _slug_da_url_post()


async def comentario_e_resposta_aninhada(item) -> bool:
    """True = resposta dentro do fio (não o comentário raiz). Corrigido para item ser article dentro do li."""
    try:
        return await item.evaluate("""(el) => {
          function threadRootLi(node) {
            const lis = [];
            for (let n = node; n; n = n.parentElement) {
              if (n.matches?.("li.comments-comment-item")) lis.push(n);
              if (n.matches?.("ul.comments-comments-list") ||
                  n.matches?.("[class*='comments-comments-list']")) break;
            }
            return lis.length ? lis[lis.length - 1] : null;
          }
          const rootLi = threadRootLi(el);
          if (!rootLi) return false;
          const ownLi = el.closest("li.comments-comment-item");
          if (!ownLi) return false;
          return ownLi !== rootLi;
        }""")
    except Exception:
        pass
    try:
        return await item.evaluate("""(el) => {
          const ownEntity = el.closest("[data-test-comment-entity-urn]");
          if (!ownEntity) return false;
          const parentEntity = ownEntity.parentElement?.closest?.("[data-test-comment-entity-urn]");
          if (parentEntity) return true;
          const inRepliesContainer = !!ownEntity.closest(
            ".comments-replies-list," +
            " [class*='comments-repl']," +
            " [class*='reply-comment']," +
            " [class*='comment-entity--reply']"
          );
          return inRepliesContainer;
        }""")
    except Exception:
        pass
    try:
        return await item.evaluate("""(el) => {
          const own = el.closest("[data-view-name='comment-container']") ||
            el.closest("div[componentkey*='replaceableComment']");
          if (!own) return false;
          let lazyColumn = own.closest("[data-component-type='LazyColumn']");
          if (lazyColumn) {
            let current = own;
            while (current && current.parentElement !== lazyColumn) {
              current = current.parentElement;
            }
            if (current && current !== own) return true;
          }
          const parentContainer = own.parentElement?.closest?.(
            "[data-view-name='comment-container'], div[componentkey*='replaceableComment']"
          );
          if (parentContainer && parentContainer !== own) return true;
          return false;
        }""")
    except Exception:
        return False


# Trecho de JS reaproveitado (colado DENTRO da função avaliada): acha a raiz do
# fio e o bloco deste comentário.
_JS_RAIZ_DO_FIO = r"""
  function raizEBloco(el) {
    let raiz = null;
    const lazy = el.closest("[data-component-type='LazyColumn']");
    if (lazy) {
      let atual = el;
      while (atual && atual.parentElement !== lazy) atual = atual.parentElement;
      raiz = atual;
    } else {
      for (let n = el; n; n = n.parentElement) {
        if (n.matches?.("li.comments-comment-item")) raiz = n;
        if (n.matches?.("ul.comments-comments-list") ||
            n.matches?.("[class*='comments-comments-list']")) break;
      }
    }
    if (!raiz || !raiz.contains(el)) return null;

    const own =
      el.closest("[data-view-name='comment-container']") ||
      el.closest("div[componentkey*='replaceableComment']") ||
      el.closest("li.comments-comment-item") ||
      el;
    let bloco = own;
    while (bloco.parentElement && bloco.parentElement !== raiz &&
           raiz.contains(bloco.parentElement)) {
      bloco = bloco.parentElement;
    }
    if (bloco === raiz) {
      bloco =
        raiz.querySelector(
          "article.comments-comment-entity, [data-view-name='comment-container'], " +
          "div[componentkey*='replaceableComment']"
        ) ||
        raiz.querySelector(".comments-comment-item__main-content, .update-components-text");
      if (!bloco) return null;
    }
    return { raiz, bloco };
  }
"""


async def respostas_do_fio_estao_ocultas(item) -> bool:
    """
    True quando o fio anuncia respostas ("1 resposta") mas nenhuma está no DOM.

    Nesse estado é impossível saber de quem é a resposta: o robô acabava
    respondendo de novo um comentário que já tinha resposta minha.
    """
    try:
        return await item.evaluate(
            "(el) => {"
            + _JS_RAIZ_DO_FIO
            + r"""
              const par = raizEBloco(el);
              if (!par) return false;
              const { raiz, bloco } = par;

              const fora = (sel) =>
                Array.from(raiz.querySelectorAll(sel)).filter(
                  (n) => n !== bloco && !bloco.contains(n)
                );

              const anuncia =
                fora("button, span, a").some((n) => {
                  const t = ((n.getAttribute?.("aria-label") || "") + " " + (n.textContent || ""))
                    .replace(/\s+/g, " ")
                    .toLowerCase();
                  return /\d+\s+(resposta|respostas|reply|replies)\b/.test(t) ||
                    /(ver|carregar|mostrar|load|show|previous)\b.*\b(resposta|respostas|reply|replies)\b/.test(t);
                });
              if (!anuncia) return false;

              // Se já há autor de resposta carregado, não está oculto.
              return fora("a[href*='/in/']").length === 0;
            }
            """
        )
    except Exception:
        return False


async def expandir_respostas_do_fio(item) -> bool:
    """Clica em 'N respostas' / 'Ver respostas anteriores' deste fio."""
    try:
        clicou = await item.evaluate(
            "(el) => {"
            + _JS_RAIZ_DO_FIO
            + r"""
              const par = raizEBloco(el);
              if (!par) return false;
              const { raiz, bloco } = par;
              const alvos = Array.from(raiz.querySelectorAll("button, [role='button']")).filter(
                (n) => n !== bloco && !bloco.contains(n)
              );
              for (const b of alvos) {
                const t = ((b.getAttribute("aria-label") || "") + " " + (b.textContent || ""))
                  .replace(/\s+/g, " ")
                  .toLowerCase();
                const ehResposta =
                  /\d+\s+(resposta|respostas|reply|replies)\b/.test(t) ||
                  /(ver|carregar|mostrar|load|show|previous)\b.*\b(resposta|respostas|reply|replies)\b/.test(t);
                if (ehResposta && !/responder|reply to|comentar/.test(t)) {
                  b.click();
                  return true;
                }
              }
              return false;
            }
            """
        )
    except Exception:
        return False
    if clicou:
        await asyncio.sleep(1.2)
    return bool(clicou)


async def fio_ja_tem_minha_resposta(item) -> bool:
    """
    Varre o fio inteiro para detectar se EU já respondi a este comentário.
    Busca por: (1) meu slug em links /in/, (2) badge 'Autor'/'Author',
    (3) texto de frase da biblioteca de respostas.
    Usa múltiplas fontes DOM para encontrar containers de resposta,
    incluindo scan de irmãos (para layout plano do LinkedIn novo).
    """
    slug = _slug_efetivo_config().lower()
    sou_autor = getattr(config, "SOU_AUTOR_DO_POST", False)
    frases = _frases_biblioteca(getattr(config, "RESPOSTAS", []) or [])
    detect_txt = getattr(config, "DETECTAR_RESPOSTAS_REPETIDAS_PELO_TEXTO", True)
    try:
        result = await item.evaluate(
            r"""
            (el, opts) => {
              const slug = (opts.slug || "").toLowerCase();
              const souAutor = opts.souAutor;
              const frases = opts.frases || [];
              const detectTxt = opts.detectTxt;

              const COMMENT_SELS =
                "[data-view-name='comment-container'], " +
                "div[componentkey*='replaceableComment'], " +
                "li.comments-comment-item, " +
                "[data-test-comment-entity-urn]";

              // Comentário longo aparece cortado por "…ver mais": além da frase
              // inteira, aceita o começo dela como prova de que a resposta é minha.
              function contemAlgumaFrase(texto, lista) {
                if (!texto) return false;
                for (const f of lista) {
                  const x = String(f || "").toLowerCase().trim().replace(/\s+/g, " ");
                  if (x.length >= 15 && texto.includes(x)) return true;
                  const inicio = x.slice(0, 40);
                  if (inicio.length >= 25 && texto.includes(inicio)) return true;
                }
                return false;
              }

              // ---- Localizar o container do comentário original ----
              const ownContainer =
                el.closest("[data-view-name='comment-container']") ||
                el.closest("div[componentkey*='replaceableComment']") ||
                el.closest("li.comments-comment-item") ||
                el.closest("[data-test-comment-entity-urn]") ||
                el;

              // ---- Coletar containers de RESPOSTA de múltiplas fontes ----
              const seen = new Set();
              seen.add(ownContainer);
              const replies = [];

              function addCandidate(node) {
                if (node && !seen.has(node)) {
                  seen.add(node);
                  replies.push(node);
                }
              }

              // FONTE A: Filhos aninhados dentro do próprio container
              for (const c of ownContainer.querySelectorAll(COMMENT_SELS)) {
                addCandidate(c);
              }

              // FONTE B: Wrapper entre LazyColumn e o comentário
              const lazyColumn = ownContainer.closest("[data-component-type='LazyColumn']");
              if (lazyColumn) {
                let wrapper = ownContainer;
                while (wrapper && wrapper.parentElement !== lazyColumn) {
                  wrapper = wrapper.parentElement;
                }
                if (wrapper && wrapper !== ownContainer) {
                  for (const c of wrapper.querySelectorAll(COMMENT_SELS)) {
                    addCandidate(c);
                  }
                }
              }

              // FONTE C: Filhos do pai (quando o pai agrupa a thread)
              const parent = ownContainer.parentElement;
              if (parent && parent !== lazyColumn) {
                for (const c of parent.querySelectorAll(COMMENT_SELS)) {
                  addCandidate(c);
                }
              }

              // FONTE D: Li hierarquia (layout antigo com ul/li)
              const lis = [];
              for (let n = el; n; n = n.parentElement) {
                if (n.matches?.("li.comments-comment-item")) lis.push(n);
                if (n.matches?.("ul.comments-comments-list") ||
                    n.matches?.("[class*='comments-comments-list']")) break;
              }
              if (lis.length) {
                const rootLi = lis[lis.length - 1];
                for (const c of rootLi.querySelectorAll(COMMENT_SELS)) {
                  addCandidate(c);
                }
              }

              // FONTE E: IRMÃOS SEGUINTES (layout plano do LinkedIn novo)
              // No layout plano, respostas são siblings do comentário original
              let sib = ownContainer.nextElementSibling;
              for (let i = 0; i < 8 && sib; i++) {
                const isComment = sib.matches?.("[data-view-name='comment-container']") ||
                  sib.matches?.("div[componentkey*='replaceableComment']") ||
                  sib.matches?.("li.comments-comment-item") ||
                  sib.matches?.("[data-test-comment-entity-urn]");
                if (isComment) {
                  addCandidate(sib);
                }
                sib = sib.nextElementSibling;
              }

              // FONTE F: O FIO INTEIRO, MENOS ESTE COMENTÁRIO
              // As fontes acima só acham respostas embrulhadas nos containers
              // conhecidos. Quando o LinkedIn renderiza a resposta como
              // <article class="comments-comment-entity--reply"> solto, nada era
              // coletado e o robô respondia de novo. Aqui não dependemos de
              // seletor de resposta: olhamos tudo que está no fio fora do
              // comentário em análise.
              let restoDoFio = null;
              (() => {
                // Raiz confiável = um fio só. Nunca a lista inteira de
                // comentários, senão uma resposta minha bloquearia o post todo.
                let raiz = null;
                const lazy = el.closest("[data-component-type='LazyColumn']");
                if (lazy) {
                  let atual = el;
                  while (atual && atual.parentElement !== lazy) {
                    atual = atual.parentElement;
                  }
                  raiz = atual;
                } else if (lis.length) {
                  raiz = lis[lis.length - 1];
                }
                if (!raiz || !raiz.contains(el)) return;

                // Meu bloco = o filho direto da raiz que contém este comentário.
                let meuBloco = ownContainer;
                while (
                  meuBloco.parentElement &&
                  meuBloco.parentElement !== raiz &&
                  raiz.contains(meuBloco.parentElement)
                ) {
                  meuBloco = meuBloco.parentElement;
                }
                if (meuBloco === raiz) {
                  // A raiz é o próprio comentário (layout antigo): exclui apenas
                  // o corpo dele, o resto da subárvore são as respostas.
                  meuBloco =
                    raiz.querySelector(
                      "article.comments-comment-entity, [data-view-name='comment-container'], " +
                      "div[componentkey*='replaceableComment']"
                    ) ||
                    raiz.querySelector(
                      ".comments-comment-item__main-content, .update-components-text"
                    );
                  if (!meuBloco) return;
                }

                // Rede de segurança: se a "raiz" na verdade agrupar muitos
                // comentários, uma resposta minha em qualquer lugar bloquearia o
                // post inteiro. Nesse caso é melhor não usar esta fonte.
                const outros = Array.from(
                  raiz.querySelectorAll(
                    "[data-view-name='comment-container'], li.comments-comment-item"
                  )
                ).filter((n) => n !== meuBloco && !meuBloco.contains(n));
                if (outros.length > 12) return;

                restoDoFio = { raiz, excluido: meuBloco };
              })();

              // Regiões onde uma resposta pode estar, sem depender de seletor:
              // (a) o que está no fio fora deste comentário;
              // (b) o que está DENTRO dele depois do corpo — é ali que o LinkedIn
              //     pendura a resposta, e até agora só o texto era conferido, por
              //     isso respostas antigas (com frase que saiu da biblioteca)
              //     passavam batido.
              const regioes = [];
              if (restoDoFio) {
                for (const filho of restoDoFio.raiz.children) {
                  if (filho !== restoDoFio.excluido && !filho.contains(restoDoFio.excluido)) {
                    regioes.push(filho);
                  }
                }
              }
              (() => {
                const corpo = ownContainer.querySelector(
                  ".comments-comment-item__main-content, " +
                  ".comments-comment-item__inline-show-more-text, " +
                  ".update-components-text, " +
                  "[class*='comment-item__main-content'], " +
                  "[class*='comment__text']"
                );
                if (!corpo) return;
                for (let n = corpo; n && n !== ownContainer; n = n.parentElement) {
                  for (let s = n.nextElementSibling; s; s = s.nextElementSibling) {
                    regioes.push(s);
                  }
                }
              })();

              // ---- CHECAGEM 0: regiões de resposta (fonte F) ----
              if (regioes.length) {
                const dentro = (sel) => {
                  const achados = [];
                  for (const r of regioes) {
                    if (r.matches?.(sel)) achados.push(r);
                    for (const n of r.querySelectorAll(sel)) achados.push(n);
                  }
                  return achados;
                };

                if (slug) {
                  const vars0 = [slug];
                  if (slug.includes("_")) vars0.push(slug.replace(/_/g, "-"));
                  if (slug.includes("-")) vars0.push(slug.replace(/-/g, "_"));
                  for (const a of dentro("a[href*='/in/']")) {
                    const h = (a.getAttribute("href") || "").toLowerCase();
                    if (vars0.some((v) => v && h.includes("/in/" + v))) return true;
                  }
                }

                if (souAutor) {
                  for (const b of dentro("span, div")) {
                    const t = (b.textContent || "").trim();
                    if (/^(Autor|Author|Você|You)$/i.test(t)) return true;
                  }
                }

                if (detectTxt && frases.length) {
                  const texto = regioes
                    .map((r) => r.textContent || "")
                    .join(" ")
                    .toLowerCase()
                    .replace(/\s+/g, " ");
                  if (contemAlgumaFrase(texto, frases)) return true;
                }
              }

              // ---- CHECAGEM 1: Slug no link /in/ ----
              if (slug) {
                const variants = [slug];
                if (slug.includes("_")) variants.push(slug.replace(/_/g, "-"));
                if (slug.includes("-")) variants.push(slug.replace(/-/g, "_"));
                for (const c of replies) {
                  for (const a of c.querySelectorAll("a[href]")) {
                    const h = (a.getAttribute("href") || "").toLowerCase();
                    for (const v of variants) {
                      if (v && h.includes("/in/" + v)) return true;
                    }
                  }
                }
              }

              // ---- CHECAGEM 2: Badge "Autor"/"Author" ----
              if (souAutor) {
                for (const c of replies) {
                  // 2a: Seletores CSS conhecidos para o badge
                  for (const badge of c.querySelectorAll(
                    ".comments-post-meta__text, " +
                    ".comments-comment-item__post-meta-item-text, " +
                    "[class*='post-meta'], " +
                    "span.artdeco-entity-lockup__badge"
                  )) {
                    const t = (badge.textContent || "").toLowerCase().trim();
                    if (t.includes("autor") || t.includes("author")) return true;
                  }

                  // 2b: Qualquer span/div com texto exato "Autor"
                  for (const b of c.querySelectorAll("span, div")) {
                    const t = (b.textContent || "").trim();
                    if (/^(Autor|Author|Você|You)$/i.test(t)) return true;
                  }

                  // 2c: FALLBACK AMPLO — busca a palavra "Autor" no texto inteiro do reply
                  // Isso pega inclusive layouts novos onde o badge mudou de classe CSS
                  const fullText = (c.innerText || "");
                  if (/\bAutor\b/.test(fullText) || /\bAuthor\b/.test(fullText)) return true;
                }
              }

              // ---- CHECAGEM 3: Texto de frase da biblioteca ----
              if (detectTxt && frases.length) {
                // Além dos containers coletados, olha o que existe DENTRO do próprio
                // comentário fora do corpo dele: é onde a resposta aninhada aparece
                // quando o container dela não bate com nenhum seletor conhecido.
                let restoDoProprio = "";
                try {
                  const clone = ownContainer.cloneNode(true);
                  const corpo = clone.querySelector(
                    ".comments-comment-item__main-content, " +
                    ".comments-comment-item__inline-show-more-text, " +
                    ".update-components-text, " +
                    "[class*='comment-item__main-content'], " +
                    "[class*='comment__text']"
                  );
                  if (corpo) corpo.remove();
                  restoDoProprio = clone.textContent || "";
                } catch (e) {
                  restoDoProprio = "";
                }

                const allText = (
                  replies
                    .map((c) => (c.innerText || "") + " " + (c.textContent || ""))
                    .join(" ") + " " + restoDoProprio
                )
                  .toLowerCase()
                  .replace(/\s+/g, " ");
                if (contemAlgumaFrase(allText, frases)) return true;
              }

              return false;
            }
        """,
            {
                "slug": slug,
                "souAutor": sou_autor,
                "frases": frases,
                "detectTxt": detect_txt,
            },
        )
        return bool(result)
    except Exception:
        return False


async def comentario_eh_meu(item) -> bool:
    """
    True quando o comentário (ou resposta) é da própria conta.

    Além do link /in/, reconhece o selo "Autor" e o texto das frases da biblioteca.
    Essas duas camadas não dependem do perfil estar configurado e são o que impede
    o robô de responder às próprias respostas.
    """
    slug = _slug_efetivo_config().lower()
    sou_autor = bool(getattr(config, "SOU_AUTOR_DO_POST", False))
    frases = [
        f
        for f in (
            _normalizar_texto_chave(x)
            for x in _frases_biblioteca(getattr(config, "RESPOSTAS", []) or [])
        )
        if len(f) >= 15
    ]
    try:
        return await item.evaluate(
            """
            (el, opts) => {
              const slug = (opts.slug || "").toLowerCase();
              const souAutor = !!opts.souAutor;
              const frases = opts.frases || [];

              // Cópia do comentário sem as respostas aninhadas: sem isso, um
              // comentário de outra pessoa que já tem resposta minha embaixo
              // seria lido como "meu".
              function escopoProprio(node) {
                const CORPO_SELS =
                  ".comments-comment-item__main-content, " +
                  ".comments-comment-item__inline-show-more-text, " +
                  ".update-components-text, " +
                  "[class*='comment-item__main-content'], " +
                  "[class*='comment__text']";
                const CONTAINER_SELS =
                  "li.comments-comment-item, article.comments-comment-entity, " +
                  "[data-view-name='comment-container'], " +
                  "div[componentkey*='replaceableComment'], " +
                  "[data-test-comment-entity-urn]";

                let base;
                try {
                  base = node.cloneNode(true);
                  base.querySelectorAll(
                    ".comments-comment-item__replies-list, .comments-replies-list, " +
                    "[class*='replies-list'], [data-view-name='comment-replies'], " +
                    "[componentkey*='replies'], [componentkey*='Replies']"
                  ).forEach((n) => n.remove());

                  // O primeiro corpo é o deste comentário; containers que não o
                  // envolvem são respostas aninhadas e saem da conta. Assim o
                  // recorte funciona mesmo com wrappers repetidos do layout novo.
                  const corpo = base.querySelector(CORPO_SELS);
                  if (corpo) {
                    base.querySelectorAll(CONTAINER_SELS).forEach((n) => {
                      if (!n.contains(corpo)) n.remove();
                    });
                    // Tudo que vem depois do corpo é resposta/rodapé, mesmo que o
                    // container dela não tenha classe conhecida.
                    for (let n = corpo; n && n !== base; n = n.parentNode) {
                      for (let s = n.nextSibling; s; ) {
                        const prox = s.nextSibling;
                        s.remove();
                        s = prox;
                      }
                    }
                  }
                } catch (e) {
                  return node;
                }
                // Se sobrou nada, o layout não é o esperado: melhor olhar tudo.
                return (base.textContent || "").trim() ? base : node;
              }

              const escopo = escopoProprio(el);

              function slugNoHref(href) {
                if (!slug) return false;
                const h = (href || "").toLowerCase();
                const variants = [slug];
                if (slug.includes("_")) variants.push(slug.replace(/_/g, "-"));
                if (slug.includes("-")) variants.push(slug.replace(/-/g, "_"));
                return variants.some((v) =>
                  h.includes(`/in/${v}/`) ||
                  h.includes(`/in/${v}?`) ||
                  h.endsWith(`/in/${v}`));
              }

              for (const a of escopo.querySelectorAll("a[href*='/in/']")) {
                if (slugNoHref(a.getAttribute("href") || "")) return true;
              }

              if (souAutor) {
                for (const badge of escopo.querySelectorAll(
                  ".comments-post-meta__text, " +
                  ".comments-comment-item__post-meta-item-text, " +
                  "[class*='post-meta'], " +
                  "span.artdeco-entity-lockup__badge"
                )) {
                  const t = (badge.textContent || "").toLowerCase();
                  if (t.includes("autor") || t.includes("author")) return true;
                }
                // Selo isolado, para layouts em que a classe do badge mudou.
                for (const b of escopo.querySelectorAll("span, div")) {
                  const t = (b.textContent || "").trim();
                  if (/^(Autor|Author|Você|You)$/i.test(t)) return true;
                }
              }

              // Texto igual a uma frase da minha biblioteca = resposta minha.
              if (frases.length) {
                const corpo = (escopo.textContent || "")
                  .normalize("NFKC")
                  .toLowerCase()
                  .replace(/\\s+/g, " ")
                  .trim();
                if (corpo) {
                  for (const f of frases) {
                    if (corpo.includes(f)) return true;
                    const inicio = f.slice(0, 40);
                    if (inicio.length >= 20 && corpo.includes(inicio)) return true;
                  }
                }
              }

              return false;
            }
            """,
            {"slug": slug, "souAutor": sou_autor, "frases": frases},
        )
    except Exception:
        return False


_NAVEGADOR_PROCESSO = {
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "brave": "brave.exe",
}

_ARQUIVOS_PERFIL_CRITICOS = (
    "Cookies",
    "Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "Preferences",
    "Secure Preferences",
    "Web Data",
    "Web Data-journal",
    "Network Persistent State",
    "History",
    "History-journal",
    "Favicons",
    "Favicons-journal",
    "Sessions",
    "Session Storage",
    "TransportSecurity",
    "Trust Tokens",
    "Trust Tokens-journal",
)

_DIRETORIOS_PERFIL_CRITICOS = (
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "Service Worker",
    "shared_proto_db",
    "Sync Data",
    "Storage",
)


def _navegador_esta_aberto(nav: str) -> bool:
    exe = _NAVEGADOR_PROCESSO.get((nav or "").lower().strip())
    if not exe:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return exe.lower() in (out.stdout or "").lower()
    except Exception:
        return False


def _caminho_perfil_sincronizado(nav: str) -> str:
    base = getattr(
        config,
        "DIR_PERFIL_NAVEGADOR",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "LinkedInBotPerfil"),
    )
    return os.path.join(base, f"{nav}-sync")


def _copiar_arquivo_perfil(origem: str, destino: str) -> None:
    if not os.path.isfile(origem):
        return
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copy2(origem, destino)


def _copiar_diretorio_perfil(origem: str, destino: str) -> None:
    if not os.path.isdir(origem):
        return
    if os.path.isdir(destino):
        shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(origem, destino, dirs_exist_ok=True)


def _precisa_atualizar_perfil_sincronizado(origem_perfil: str, destino_perfil: str) -> bool:
    if not os.path.isdir(destino_perfil):
        return True
    origem_cookies = os.path.join(origem_perfil, "Cookies")
    destino_cookies = os.path.join(destino_perfil, "Cookies")
    if not os.path.isfile(origem_cookies):
        return not os.path.isdir(destino_perfil)
    if not os.path.isfile(destino_cookies):
        return True
    try:
        return os.path.getmtime(origem_cookies) > os.path.getmtime(destino_cookies)
    except OSError:
        return True


def _sincronizar_perfil_principal(
    nav: str, user_data_origem: str, subpasta: str, *, force: bool = False
) -> str:
    """
    Edge/Chrome bloqueiam remote debugging na pasta User Data original.
    Copia a sessão para uma pasta dedicada (não-padrão) antes de abrir com Playwright.
    """
    sub = (subpasta or "Default").strip() or "Default"
    origem_perfil = os.path.join(user_data_origem, sub)
    if not os.path.isdir(origem_perfil):
        raise FileNotFoundError(
            f"Perfil '{sub}' não encontrado em:\n{user_data_origem}\n"
            "Abra o navegador uma vez ou ajuste PERFIL_NAVEGADOR_SUBPASTA."
        )

    destino_root = _caminho_perfil_sincronizado(nav)
    destino_perfil = os.path.join(destino_root, sub)
    primeira_vez = not os.path.isdir(destino_perfil)
    atualizar = force or primeira_vez or _precisa_atualizar_perfil_sincronizado(
        origem_perfil, destino_perfil
    )

    if atualizar:
        print(
            f"[Info] Sincronizando sessão do perfil '{sub}' para automação "
            f"({destino_root})..."
        )
        os.makedirs(destino_root, exist_ok=True)
        _copiar_arquivo_perfil(
            os.path.join(user_data_origem, "Local State"),
            os.path.join(destino_root, "Local State"),
        )
        if primeira_vez or force:
            if os.path.isdir(destino_perfil):
                shutil.rmtree(destino_perfil, ignore_errors=True)
            shutil.copytree(origem_perfil, destino_perfil, dirs_exist_ok=True)
        else:
            os.makedirs(destino_perfil, exist_ok=True)
            for nome in _ARQUIVOS_PERFIL_CRITICOS:
                _copiar_arquivo_perfil(
                    os.path.join(origem_perfil, nome),
                    os.path.join(destino_perfil, nome),
                )
            for nome in _DIRETORIOS_PERFIL_CRITICOS:
                _copiar_diretorio_perfil(
                    os.path.join(origem_perfil, nome),
                    os.path.join(destino_perfil, nome),
                )
        print("[Info] Sessão sincronizada com sucesso.")
    else:
        print("[Info] Usando sessão já sincronizada (perfil do navegador não mudou).")

    return destino_root


def _erro_perfil_principal_recusado(exc: Exception) -> bool:
    msg = str(exc).lower()
    marcadores = (
        "devtools remote debugging requires a non-default data directory",
        "remote debugging",
        "non-default data directory",
        "target page, context or browser has been closed",
        "targetclosederror",
    )
    return any(m in msg for m in marcadores)


def _erro_inicializacao_transiente(exc: Exception) -> bool:
    msg = str(exc).lower()
    marcadores = (
        "target page, context or browser has been closed",
        "targetclosederror",
        "timeout",
        "econnrefused",
        "browser has been closed",
    )
    return any(m in msg for m in marcadores)


def _resolver_executavel_chromium(nav: str) -> str:
    nav = (nav or "").lower().strip()
    if nav == "edge":
        for p in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ):
            if os.path.isfile(p):
                return p
        raise FileNotFoundError("Edge não encontrado no caminho padrão.")
    if nav == "chrome":
        for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            if os.path.isfile(p):
                return p
        raise FileNotFoundError("Chrome não encontrado no caminho padrão.")
    if nav == "brave":
        brave = _resolver_caminho_brave_exe()
        if brave:
            return brave
        raise FileNotFoundError("Brave não encontrado no caminho padrão.")
    raise ValueError(f"Navegador sem executável chromium conhecido: {nav}")


def _porta_cdp_ativa(porta: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(porta)), timeout=0.8):
            return True
    except OSError:
        return False


async def _esperar_endpoint_cdp(porta: int, timeout_s: float = 8.0) -> bool:
    inicio = perf_counter()
    url = f"http://127.0.0.1:{int(porta)}/json/version"
    while perf_counter() - inicio <= timeout_s:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            await asyncio.sleep(0.35)
            continue
        await asyncio.sleep(0.25)
    return False


def _iniciar_chromium_modo_cdp(nav: str, subpasta: str, porta: int) -> subprocess.Popen:
    exe = _resolver_executavel_chromium(nav)
    args = [
        exe,
        f"--remote-debugging-port={int(porta)}",
        f"--profile-directory={(subpasta or 'Default').strip() or 'Default'}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def _conectar_chromium_cdp(playwright, porta: int):
    browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{int(porta)}")
    if browser.contexts:
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        return browser, context, page
    page = await browser.new_page()
    return browser, page.context, page


def _args_chromium_com_perfil_principal() -> list[str]:
    """Chrome/Edge/Brave: apontar para User Data exige --profile-directory."""
    sub = (getattr(config, "PERFIL_NAVEGADOR_SUBPASTA", None) or "Default").strip()
    if not sub:
        sub = "Default"
    return [
        "--disable-blink-features=AutomationControlled",
        f"--profile-directory={sub}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def _args_chromium_perfil_robo() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
    ]


def _resolver_caminho_brave_exe():
    import os

    env = os.environ.get("BRAVE_EXECUTABLE")
    if env and os.path.isfile(env):
        return env
    for p in (
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ):
        if os.path.isfile(p):
            return p
    return None


async def _obter_page_navegador(playwright):
    """Abre só config.NAVEGADOR; sem fallback. Chrome/Edge/Brave com perfil principal usam --profile-directory."""
    import os
    import glob
    nav = getattr(config, "NAVEGADOR", "chrome").lower().strip()
    usar_perfil_principal = bool(getattr(config, "USAR_PERFIL_PRINCIPAL", False))

    if nav not in ("chrome", "edge", "brave", "firefox"):
        raise ValueError(
            f'NAVEGADOR="{nav}" inválido. Em config.py use: chrome, edge, brave ou firefox.'
        )

    _LOCAL = os.environ.get("LOCALAPPDATA", "")
    _ROAMING = os.environ.get("APPDATA", "")
    subpasta_perfil = (getattr(config, "PERFIL_NAVEGADOR_SUBPASTA", None) or "Default").strip() or "Default"
    caminho_perfil_original = ""

    if usar_perfil_principal:
        print("Modo: perfil principal (mesma conta que você usa no PC).")
        print(
            "Feche totalmente este navegador antes de rodar (incl. ícone na bandeja).\n"
        )
        if nav == "chrome":
            caminho_perfil_original = os.path.join(_LOCAL, "Google", "Chrome", "User Data")
        elif nav == "edge":
            caminho_perfil_original = os.path.join(_LOCAL, "Microsoft", "Edge", "User Data")
        elif nav == "brave":
            caminho_perfil_original = os.path.join(
                _LOCAL, "BraveSoftware", "Brave-Browser", "User Data"
            )
        else:
            base_ff = os.path.join(_ROAMING, "Mozilla", "Firefox", "Profiles")
            perfis = glob.glob(os.path.join(base_ff, "*.default-release*"))
            if perfis:
                caminho_perfil_original = perfis[0]
            else:
                perfis_alt = glob.glob(os.path.join(base_ff, "*.default*"))
                if not perfis_alt:
                    raise FileNotFoundError(
                        f"Nenhum perfil Firefox em {base_ff}. Abra o Firefox uma vez."
                    )
                caminho_perfil_original = perfis_alt[0]
        caminho_perfil = caminho_perfil_original
    else:
        caminho_base = getattr(
            config, "DIR_PERFIL_NAVEGADOR", os.path.join(_LOCAL, "LinkedInBotPerfil")
        )
        caminho_perfil = os.path.join(caminho_base, nav)

    sincronizar_perfil_principal = bool(
        getattr(config, "SINCRONIZAR_PERFIL_PRINCIPAL", True)
    )

    if usar_perfil_principal and nav in ("chrome", "edge", "brave"):
        if _navegador_esta_aberto(nav):
            raise RuntimeError(
                f"{nav} ainda está aberto. Feche todas as janelas e o ícone na bandeja, "
                "depois inicie a automação novamente."
            )
        if sincronizar_perfil_principal:
            caminho_perfil = _sincronizar_perfil_principal(
                nav, caminho_perfil_original, subpasta_perfil
            )

    print("==========================================")
    print(f"Navegador: {nav}")
    if usar_perfil_principal and nav in ("chrome", "edge", "brave"):
        print(f"Perfil original: {caminho_perfil_original}")
        if sincronizar_perfil_principal:
            print(f"Pasta usada pela automação: {caminho_perfil}")
        else:
            print("[Info] Sincronização desativada: usando User Data original.")
        print(f"PERFIL_NAVEGADOR_SUBPASTA: {subpasta_perfil}")
    else:
        print(f"Pasta do perfil: {caminho_perfil}")
    print("==========================================\n")

    if nav == "firefox":
        kwargs_ff: dict = {"user_data_dir": caminho_perfil, "headless": False}
        if usar_perfil_principal:
            ff_exe = getattr(config, "FIREFOX_EXECUTABLE", None) or (
                r"C:\Program Files\Mozilla Firefox\firefox.exe"
            )
            if os.path.isfile(ff_exe):
                kwargs_ff["executable_path"] = ff_exe
        try:
            context = await playwright.firefox.launch_persistent_context(**kwargs_ff)
        except Exception as e:
            raise RuntimeError(
                "Falha ao abrir Firefox. Feche o Firefox ou defina FIREFOX_EXECUTABLE em config. "
                f"Detalhe: {e}"
            ) from e
    else:
        args = (
            _args_chromium_com_perfil_principal()
            if usar_perfil_principal
            else _args_chromium_perfil_robo()
        )
        kwargs: dict = {
            "user_data_dir": caminho_perfil,
            "headless": False,
            "args": args,
            "timeout": 60_000,
        }
        if nav == "edge":
            kwargs["channel"] = "msedge"
        elif nav == "chrome":
            kwargs["channel"] = "chrome"
        elif nav == "brave":
            brave = _resolver_caminho_brave_exe()
            if not brave:
                raise FileNotFoundError(
                    "Brave não encontrado. Instale-o ou defina BRAVE_EXECUTABLE no sistema."
                )
            kwargs["executable_path"] = brave

        tentativas_init = max(
            1, int(getattr(config, "NAVEGADOR_INIT_MAX_TENTATIVAS", 2))
        )
        ultimo_erro = None
        context = None
        for tentativa in range(1, tentativas_init + 1):
            try:
                context = await playwright.chromium.launch_persistent_context(**kwargs)
                break
            except Exception as e:
                ultimo_erro = e
                if (
                    usar_perfil_principal
                    and nav in ("chrome", "edge", "brave")
                    and sincronizar_perfil_principal
                    and _erro_perfil_principal_recusado(e)
                ):
                    print(
                        f"[AVISO] {nav} recusou abrir com a pasta sincronizada. "
                        "Sincronizando de novo e repetindo tentativa..."
                    )
                    caminho_perfil = _sincronizar_perfil_principal(
                        nav, caminho_perfil_original, subpasta_perfil, force=True
                    )
                    kwargs["user_data_dir"] = caminho_perfil
                if tentativa < tentativas_init and _erro_inicializacao_transiente(e):
                    await asyncio.sleep(0.8 * tentativa)
                    continue
                break

        if context is None and nav == "edge" and usar_perfil_principal:
            cdp_fallback = bool(getattr(config, "EDGE_CDP_FALLBACK_ATIVO", True))
            cdp_porta = int(getattr(config, "EDGE_CDP_PORTA", 9222))
            cdp_autostart = bool(getattr(config, "EDGE_CDP_AUTO_START", True))
            if cdp_fallback:
                print(
                    "[AVISO] launch_persistent falhou no Edge. "
                    "Tentando fallback via CDP..."
                )
                cdp_proc = None
                if cdp_autostart and not _porta_cdp_ativa(cdp_porta):
                    try:
                        cdp_proc = _iniciar_chromium_modo_cdp(
                            nav, subpasta_perfil, cdp_porta
                        )
                        await asyncio.sleep(0.6)
                    except Exception as e:
                        print(f"[AVISO] Não consegui iniciar Edge em modo CDP: {e}")
                if await _esperar_endpoint_cdp(cdp_porta, timeout_s=10.0):
                    try:
                        _browser_cdp, context_cdp, page_cdp = await _conectar_chromium_cdp(
                            playwright, cdp_porta
                        )
                        print(
                            f"[Info] Conectado ao Edge via CDP na porta {cdp_porta}."
                        )
                        return context_cdp, page_cdp, False
                    except Exception as e:
                        ultimo_erro = e
                        if cdp_proc is not None:
                            try:
                                cdp_proc.kill()
                            except Exception:
                                pass
                else:
                    print(
                        "[AVISO] Endpoint CDP não respondeu a tempo. "
                        "Verifique se a porta está livre e se o Edge abriu corretamente."
                    )

        if context is None:
            detalhes = str(ultimo_erro)
            if usar_perfil_principal and (
                "DevTools remote debugging requires a non-default data directory" in detalhes
                or _erro_perfil_principal_recusado(ultimo_erro)
            ):
                raise RuntimeError(
                    f"{nav} recusou abrir com o perfil PRINCIPAL. "
                    "Os navegadores atuais bloqueiam automação na pasta de perfil original por segurança. "
                    "Solução recomendada: no settings.json use USAR_PERFIL_PRINCIPAL=false "
                    "(perfil dedicado) e faça login no LinkedIn UMA vez na janela que abrir."
                ) from ultimo_erro
            raise RuntimeError(
                f"Falha ao abrir {nav}. Feche todas as janelas dele e tente de novo. "
                f"Erro: {ultimo_erro}"
            ) from ultimo_erro

    page = context.pages[0] if context.pages else await context.new_page()
    print(
        f"Aba inicial: {page.url or '(vazio)'} — about:blank é normal; "
        "o script abre o post (URL_POST) logo em seguida.\n"
    )
    return context, page, True


async def iniciar_bot() -> None:
    global _ERRO_LINKEDIN_NO_ULTIMO_ENVIO
    _debug_log(
        run_id="bootstrap",
        hypothesis_id="H0",
        location="main.py:iniciar_bot",
        message="debug_logger_bootstrap",
        data={"cwd": os.getcwd(), "log_path": _DEBUG_LOG_PATH},
    )
    async with async_playwright() as p:
        context_persistent = None
        fechar_context = False
        run_id: Optional[str] = None
        run_mode = ""
        run_url = ""
        dashboard_no_fim = False

        try:
            # Recarrega config para garantir que NAVEGADOR e demais
            # configurações salvas pela GUI estejam atualizadas.
            importlib.reload(config)
            context_persistent, _pagina_inicial, fechar_context = await _obter_page_navegador(p)
        except Exception as e:
            print("\n[ERRO] O navegador não manteve a janela aberta ou falhou na inicialização.")
            print(f"Detalhe: {e}\n")
            return

        try:
            page = await context_persistent.new_page()
            print(
                "Aba dedicada criada para automação. "
                "Outras abas do perfil permanecerão abertas sem interferir.\n"
            )
            try:
                # Se o browser abriu uma aba inicial vazia, fechamos para evitar duas abas inúteis.
                if (
                    _pagina_inicial
                    and not _pagina_inicial.is_closed()
                    and (_pagina_inicial.url or "").strip().lower() in ("", "about:blank")
                    and len(context_persistent.pages) > 1
                ):
                    await _pagina_inicial.close()
            except Exception:
                pass
        except Exception as e:
            print(f"[ERRO] Não foi possível criar aba dedicada de automação: {e}")
            return

        try:
            await injetar_cookies_linkedin(context_persistent)
        except Exception as e:
            print(f"[AVISO] Falha ao injetar cookie do LinkedIn: {e!r}")

        try:
            if not await garantir_login_linkedin(page):
                print("[ERRO] Sem sessão do LinkedIn. Encerrando com segurança.")
                if fechar_context and context_persistent is not None:
                    try:
                        await context_persistent.close()
                    except Exception:
                        pass
                return
        except Exception as e:
            if _erro_target_fechado(e):
                print("[AVISO] Janela fechada durante verificação de login. Encerrando.")
                return
            print(f"[AVISO] Falha ao verificar login: {e!r}. Seguindo mesmo assim.")

        try:
            await guardar_sessao_da_conta(context_persistent)
        except Exception as e:
            print(f"[AVISO] Não foi possível guardar a sessão do LinkedIn na conta: {e!r}")

        try:
            await detectar_slug_da_conta(page)
        except Exception as e:
            if _erro_target_fechado(e):
                print("[AVISO] Janela fechada ao identificar o perfil da conta. Encerrando.")
                return
            print(f"[AVISO] Não foi possível identificar o perfil da conta: {e!r}")

        try:
            importlib.reload(config)
            modo_ativo, preset_ativo = _aplicar_config_modo_em_runtime()
            if not _validar_config_campanha(modo_ativo, preset_ativo):
                return
            run_mode = modo_ativo
            run_url = (getattr(config, "URL_POST", "") or "").strip()
            dashboard_inicio = bool(getattr(config, "ABRIR_DASHBOARD_AO_INICIAR", True))
            dashboard_no_fim = bool(getattr(config, "ABRIR_DASHBOARD_NO_FIM", False))
            print(f"[Info] Modo ativo: {modo_ativo} | preset: {preset_ativo}")
            if metricas is not None:
                try:
                    run_id = metricas.iniciar_execucao(run_mode, run_url)
                    _debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H5",
                        location="main.py:iniciar_bot:start",
                        message="run_started",
                        data={"mode": run_mode, "url_set": bool(run_url)},
                    )
                    if dashboard_inicio:
                        path_dash = metricas.abrir_dashboard()
                        print(f"[Info] Dashboard aberto em: {path_dash}")
                    _salvar_dashboard_execucao("inicio_execucao")
                except OSError as e:
                    print(f"[AVISO] Não foi possível iniciar registro de execução: {e}")
        except Exception:
            pass

        _url_inicial = (getattr(config, "URL_POST", "") or "").strip()
        respondidos_na_execucao, _perfis_iniciais = _carregar_historico_post(_url_inicial)
        perfis_contactados_execucao: set[str] = set()
        perfis_respondidos_execucao: set[str] = set(_perfis_iniciais)
        ultima_url_carregada: Optional[str] = None
        # Endereço real do post depois de carregar (o LinkedIn pode redirecionar):
        # é a referência para perceber que um clique tirou a aba do post.
        url_post_carregada: str = ""
        retornos_ao_post = 0
        recusas_linkedin_seguidas = 0
        palavra_chave_auto: str = ""
        primeira_varredura_do_post = True
        idx_url_atual = 0
        botao_responder = re.compile(r"Responder|Reply", re.I)
        consecutive_idle = 0

        print(
            "[Info] O config.py é relido a cada rodada. "
            "Troque MODO_AUTOMACAO/PRESET_AUTOMACAO para mudar campanha.\n"
        )

        while True:
            importlib.reload(config)
            modo_ativo, preset_ativo = _aplicar_config_modo_em_runtime()
            if not _validar_config_campanha(modo_ativo, preset_ativo):
                break
            if _pagina_fechada(page):
                print(
                    "[AVISO] A aba de automação foi fechada. "
                    "Encerrando de forma segura."
                )
                break

            run_mode = modo_ativo
            modo = modo_ativo
            dashboard_no_fim = bool(getattr(config, "ABRIR_DASHBOARD_NO_FIM", False))

            url_principal = (getattr(config, "URL_POST", "") or "").strip()
            if not url_principal:
                print("[ERRO] URL_POST está vazio no preset selecionado.")
                break
            if not (url_principal.startswith("http://") or url_principal.startswith("https://")):
                print(f"[ERRO] URL_POST inválida (precisa começar com http): {url_principal[:80]}…")
                break

            # Fila de posts: a URL principal + as extras (uma por linha), sem duplicatas.
            _urls_lista_raw = getattr(config, "URLS_POST_LISTA", "") or ""
            if isinstance(_urls_lista_raw, str):
                _urls_lista = [u.strip() for u in _urls_lista_raw.splitlines() if u.strip().startswith("http")]
            elif isinstance(_urls_lista_raw, list):
                _urls_lista = [u.strip() for u in _urls_lista_raw if str(u).strip().startswith("http")]
            else:
                _urls_lista = []
            _todas_urls = [url_principal] + [u for u in _urls_lista if u != url_principal]

            # O post da vez vem do índice, não de config.URL_POST: o config é relido a
            # cada rodada e desfazia a troca, deixando a automação recarregando o mesmo post.
            if idx_url_atual >= len(_todas_urls):
                idx_url_atual = 0
            url = _todas_urls[idx_url_atual]
            run_url = url

            respostas_cfg = _frases_biblioteca(getattr(config, "RESPOSTAS", []) or [])
            if modo != "comentario_ia" and not respostas_cfg:
                print("[ERRO] RESPOSTAS não possui frases válidas em config.py.")
                break

            max_idle = max(1, int(getattr(config, "RODADAS_VAZIAS_PARA_ENCERRAR", 3)))
            max_respostas_rodada = int(getattr(config, "MAX_RESPOSTAS_POR_RODADA", 0))
            if max_respostas_rodada < 0:
                max_respostas_rodada = 0
            enviar_mensagem_perfil_ativo = bool(
                getattr(config, "ENVIAR_MENSAGEM_PERFIL_ATIVO", False)
            )
            respostas_publicas_dm_sucesso = _modelos_texto(
                getattr(config, "RESPOSTA_PUBLICA_DM_SUCESSO", ""),
                "te enviei uma mensagem no inbox.",
            )
            respostas_publicas_dm_falha = _modelos_texto(
                getattr(config, "RESPOSTA_PUBLICA_DM_FALHA", ""),
                "não consegui te chamar no inbox, me chama para continuarmos a conversa.",
            )
            mensagem_perfil_templates = _modelos_texto(
                getattr(config, "MENSAGEM_PERFIL_TEMPLATE", ""),
                "Ooi, vi o seu comentário sobre {palavra-chave}, me envia o seu Whatsapp? "
                "Vamos trabalhar e resolver isso!",
            )
            max_envio_perfil_rodada = int(
                getattr(config, "MAX_ENVIO_PERFIL_POR_RODADA", 0)
            )
            if max_envio_perfil_rodada < 0:
                max_envio_perfil_rodada = 0

            if consecutive_idle >= max_idle:
                proximo_idx = idx_url_atual + 1
                if proximo_idx >= len(_todas_urls):
                    if len(_todas_urls) > 1:
                        print(
                            f"[Info] Ciclo completo de {len(_todas_urls)} post(s) "
                            "sem novos comentários. Encerrando."
                        )
                    break
                idx_url_atual = proximo_idx
                print(
                    f"[Info] Post atual esgotado. Indo para o próximo "
                    f"({idx_url_atual + 1}/{len(_todas_urls)}):"
                )
                print(f"  {_todas_urls[idx_url_atual]}\n")
                consecutive_idle = 0
                continue

            palavras_cfg = [
                p.strip() for p in config.PALAVRAS_CHAVE if p and str(p).strip()
            ]
            # Só reconhece a palavra no post quando o campo está vazio.
            if palavras_cfg:
                palavras = palavras_cfg
                palavra_chave_auto = ""
            else:
                palavras = []

            pulados_temporarios: set[str] = set()
            em_processamento: set[str] = set()
            acabou_de_carregar_post = False

            if url != ultima_url_carregada:
                print(f"Carregando post (lido do config.py):\n{url}\n")
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=90_000
                    )
                    try:
                        await page.wait_for_load_state("load", timeout=30_000)
                    except Exception:
                        pass
                    await asyncio.sleep(6)
                except Exception as e:
                    if _erro_target_fechado(e):
                        print("[AVISO] Navegador/aba fechou durante o page.goto(). Encerrando.")
                        break
                    print(f"Aviso: carregamento lento ou timeout: {e}")

                try:
                    await aviso_se_parece_login(page)
                except Exception as e:
                    if _erro_target_fechado(e):
                        print("[AVISO] Navegador/aba fechou antes da validação de login.")
                        break
                    raise
                print(f"URL aberta no navegador: {page.url}\n")
                try:
                    ordenou = await forcar_ordenacao_mais_recentes(page)
                    _debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H1",
                        location="main.py:forcar_ordenacao_mais_recentes",
                        message="order_switch_attempt",
                        data={"ordered_to_recent": bool(ordenou), "url_changed": True},
                    )
                    if ordenou:
                        print("[Info] Ordenação de comentários ajustada para: Mais recentes.")
                    else:
                        print(
                            "[Info] Não consegui confirmar 'Mais recentes' automaticamente; "
                            "seguindo com a ordenação atual."
                        )
                except Exception as e:
                    if getattr(config, "LOG_ERROS_COMENTARIOS", False):
                        print(f"[AVISO] Falha ao tentar ordenar comentários: {e!r}")
                ultima_url_carregada = url
                try:
                    url_post_carregada = page.url or url
                except Exception:
                    url_post_carregada = url
                primeira_varredura_do_post = True
                palavra_chave_auto = ""
                acabou_de_carregar_post = True
                # Carrega histórico persistido para a nova URL (não limpa — evita re-responder)
                respondidos_na_execucao, perfis_ja_respondidos = _carregar_historico_post(url)
                perfis_respondidos_execucao = set(perfis_ja_respondidos)
                perfis_contactados_execucao.clear()
                consecutive_idle = 0

            # Campo vazio → tenta ler a palavra-chave no texto do post.
            # Campo preenchido → usa só o que o usuário digitou (nunca sobrescreve).
            if palavras_cfg:
                palavras = list(palavras_cfg)
            else:
                if not palavra_chave_auto:
                    try:
                        texto_post_kw = await _extrair_texto_post_na_pagina(page)
                    except Exception:
                        texto_post_kw = ""
                    palavra_chave_auto = detectar_palavra_chave_no_post(texto_post_kw) or ""
                    if palavra_chave_auto:
                        print(
                            f"[Info] Palavra-chave reconhecida no post: "
                            f"«{palavra_chave_auto}» (campo Palavras-chave vazio)."
                        )
                if palavra_chave_auto:
                    palavras = variantes_palavra_chave(palavra_chave_auto)
                elif modo != "comentario_ia":
                    print(
                        "[ERRO] PALAVRAS_CHAVE está vazio e não encontrei a "
                        "palavra-chave no texto do post. Preencha o campo ou "
                        "deixe a palavra bem clara no post (ex.: Comente JAVA)."
                    )
                    break
                else:
                    palavras = []
                    if acabou_de_carregar_post:
                        print(
                            "[Info] Modo IA: PALAVRAS_CHAVE vazio e sem palavra no post — "
                            "considerando qualquer comentário raiz elegível pelo fio.\n"
                        )

            if acabou_de_carregar_post:
                if modo == "comentario_ia":
                    if palavras:
                        print(
                            "[Info] Modo IA: comentários raiz com palavra-chave no corpo e demais regras do fio.\n"
                        )
                    else:
                        print(
                            "[Info] Modo IA: comentários raiz elegíveis pelo fio (sem exigir palavra-chave).\n"
                        )
                else:
                    print(
                        "[Info] Responder comentários: apenas raiz e com palavra-chave no corpo.\n"
                    )
                    print(
                        f"[Info] Palavras-chave ativas: {', '.join(palavras)}\n"
                    )
                if bool(getattr(config, "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO", False)):
                    print("[Info] Regra do fio: sem nenhuma resposta no fio.")
                else:
                    print("[Info] Regra do fio: sem resposta sua no fio.")
                if bool(getattr(config, "NAO_REPETIR_MESMO_PERFIL", True)):
                    print(
                        "[Info] Regra extra: nunca responde duas vezes ao mesmo perfil neste post "
                        "(vale também após reiniciar)."
                    )
                if enviar_mensagem_perfil_ativo:
                    print("[Info] Fluxo extra ativo: enviar mensagem no perfil e depois responder no comentário.")
                print("")
                print(f"[Info] Encerra após {max_idle} rodada(s) vazias.\n")

            try:
                # A varredura pesada (e a troca de ordenação) só na 1ª rodada do post.
                # Repetir a cada rodada fazia a lista de comentários recarregar sem parar.
                await preparar_secao_comentarios(
                    page, completo=primeira_varredura_do_post
                )
                primeira_varredura_do_post = False
                comentarios, total = await melhor_locator_comentarios(page)
            except Exception as e:
                if _erro_target_fechado(e):
                    print("[AVISO] Navegador/aba fechou durante a varredura de comentários.")
                    break
                raise

            if total == 0:
                consecutive_idle += 1
                # Sem nada na tela, a próxima rodada volta a fazer a varredura completa.
                primeira_varredura_do_post = True
                print(
                    f"Nenhum bloco de comentário visível ({consecutive_idle}/{max_idle}). "
                    "Tentando de novo…"
                )
                await asyncio.sleep(2)
                continue

            post_context_ia = ""
            if modo == "comentario_ia":
                dom_post = ""
                if bool(getattr(config, "IA_USAR_TEXTO_POST_DA_PAGINA", True)):
                    try:
                        dom_post = await _extrair_texto_post_na_pagina(page)
                    except Exception:
                        dom_post = ""
                post_context_ia = _montar_post_para_ia_user_prompt(dom_post)

            print(f"--- Rodada: {total} bloco(s) de comentário na tela ---")
            respostas_nesta_rodada = 0
            envios_perfil_nesta_rodada = 0
            pulados_nesta_rodada = 0
            skip_motivos_rodada: dict[str, int] = {}
            encerrar_por_fechamento = False
            voltar_ao_post = False
            snapshot_itens: list[tuple[str, object]] = []
            snapshot_chaves: set[str] = set()
            for i in range(total):
                item = comentarios.nth(i)
                try:
                    try:
                        await item.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    chave = await _chave_estavel_comentario(item)
                    if chave in snapshot_chaves:
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="snapshot_duplicate",
                        )
                        continue
                    if chave in respondidos_na_execucao:
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="already_replied",
                        )
                        continue
                    snapshot_chaves.add(chave)
                    snapshot_itens.append((chave, item))
                except Exception:
                    continue

            for i, (chave, item) in enumerate(snapshot_itens):
                if await _saiu_do_post(page, url, url_post_carregada):
                    voltar_ao_post = True
                    break
                try:
                    if chave in respondidos_na_execucao:
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="already_replied",
                        )
                        continue
                    if chave in em_processamento:
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="already_claimed",
                        )
                        continue
                    em_processamento.add(chave)

                    try:
                        await item.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass

                    texto_corpo = await texto_corpo_comentario_apenas(item)
                    if not texto_corpo.strip():
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="empty_comment_body",
                        )
                        continue

                    motivo_skip = ""
                    is_nested_reply = await comentario_e_resposta_aninhada(item)
                    is_my_comment = await comentario_eh_meu(item)
                    if modo == "comentario_ia" and not palavras:
                        palavra_chave_match = None
                        has_keyword = True
                    else:
                        palavra_chave_match = palavra_chave_casada(texto_corpo, palavras)
                        has_keyword = bool(palavra_chave_match)
                    thread_diag = {}
                    has_reply_block = False
                    reply_block_reason = "thread_has_my_reply"
                    reply_rule = "my_reply"
                    if not is_nested_reply and (not is_my_comment) and has_keyword:
                        thread_diag = await _diagnostico_fio_respostas(item)
                        only_no_reply_thread = bool(
                            getattr(config, "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO", False)
                        )
                        if only_no_reply_thread:
                            has_reply_block = await fio_ja_tem_qualquer_resposta(item)
                            reply_block_reason = "thread_has_reply"
                            reply_rule = "any_reply"
                        else:
                            has_reply_block = await fio_ja_tem_minha_resposta(item)
                            reply_block_reason = "thread_has_my_reply"
                            reply_rule = "my_reply"
                            # Fio que anuncia resposta sem mostrá-la: abre para ver
                            # de quem é, senão o robô responderia de novo.
                            if not has_reply_block and await respostas_do_fio_estao_ocultas(item):
                                if await expandir_respostas_do_fio(item):
                                    has_reply_block = await fio_ja_tem_minha_resposta(item)
                                    thread_diag["respostas_expandidas"] = True
                                    thread_diag["bloqueou_apos_expandir"] = bool(has_reply_block)
                    #region agent log
                    _agent_debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H1",
                        location="main.py:responder:filtro_elegibilidade",
                        message="eligibility_evaluated",
                        data={
                            "comment_key": chave,
                            "body_prefix": texto_corpo.strip()[:120],
                            "is_nested_reply": bool(is_nested_reply),
                            "is_my_comment": bool(is_my_comment),
                            "has_keyword": bool(has_keyword),
                            "keyword_match": palavra_chave_match or "",
                            "has_reply_block": bool(has_reply_block),
                            "reply_rule": reply_rule,
                            "thread_diag": thread_diag,
                        },
                    )
                    #endregion
                    if is_my_comment:
                        motivo_skip = "own_comment"
                    elif is_nested_reply:
                        motivo_skip = "nested_reply"
                    elif not has_keyword:
                        motivo_skip = "keyword_miss"
                    elif has_reply_block:
                        motivo_skip = reply_block_reason

                    if motivo_skip:
                        skip_motivos_rodada[motivo_skip] = (
                            skip_motivos_rodada.get(motivo_skip, 0) + 1
                        )
                        if chave not in pulados_temporarios:
                            pulados_temporarios.add(chave)
                            pulados_nesta_rodada += 1
                            if motivo_skip in ("thread_has_my_reply", "thread_has_reply"):
                                print(
                                    f"[PULADO] Comentário já tem resposta do autor no fio. "
                                    f"Corpo: \"{texto_corpo.strip()[:60]}…\""
                                )
                            elif motivo_skip == "own_comment":
                                print(
                                    "[PULADO] Este comentário/resposta é meu. "
                                    f"Corpo: \"{texto_corpo.strip()[:60]}…\""
                                )
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo=motivo_skip,
                        )
                        continue

                    if metricas is not None:
                        try:
                            metricas.registrar_reconhecido(1)
                        except OSError as e:
                            print(f"[AVISO] Não foi possível salvar métricas (reconhecidos): {e}")

                    acao_inicio = perf_counter()
                    #region agent log
                    _agent_debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H2",
                        location="main.py:responder:attempt_start",
                        message="reply_attempt_start",
                        data={
                            "comment_key": chave,
                            "snapshot_index": i,
                            "body_prefix": texto_corpo.strip()[:120],
                            "keyword_match": palavra_chave_match or "",
                        },
                    )
                    #endregion

                    if (
                        enviar_mensagem_perfil_ativo
                        and max_envio_perfil_rodada > 0
                        and envios_perfil_nesta_rodada >= max_envio_perfil_rodada
                    ):
                        print(
                            f"[Info] Limite de envio por perfil na rodada atingido ({max_envio_perfil_rodada})."
                        )
                        break

                    dm_ok = False
                    dm_status = "not_enabled"
                    profile_url_log = ""
                    perfil_url = ""
                    nome_autor = ""
                    perfil_key = ""
                    dados_autor = await dados_autor_comentario(item)
                    perfil_url = str(dados_autor.get("perfil_url") or "").strip()
                    profile_url_log = perfil_url
                    nome_autor = str(dados_autor.get("nome") or "").strip()
                    perfil_key = _chave_perfil(perfil_url)
                    if (
                        perfil_key
                        and bool(getattr(config, "NAO_REPETIR_MESMO_PERFIL", True))
                        and perfil_key in perfis_respondidos_execucao
                    ):
                        print(
                            f"[PULADO] Já respondi {nome_autor or 'este perfil'} neste post."
                        )
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="profile_already_replied",
                            extra={"profile_key": perfil_key},
                        )
                        continue

                    # Última barreira: minha resposta pode estar em qualquer lugar
                    # da página (fio recolhido, layout diferente, texto antigo).
                    # A menção que o LinkedIn insere na resposta entrega quem já
                    # foi atendido, sem depender da estrutura do fio.
                    if (
                        perfil_url
                        and bool(getattr(config, "NAO_REPETIR_MESMO_PERFIL", True))
                        and await ja_respondi_esta_pessoa_no_post(page, perfil_url)
                    ):
                        if perfil_key:
                            perfis_respondidos_execucao.add(perfil_key)
                        print(
                            f"[PULADO] Já existe resposta minha para "
                            f"{nome_autor or 'este perfil'} neste post."
                        )
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="profile_reply_found_on_page",
                            extra={"profile_key": perfil_key},
                        )
                        continue
                    if enviar_mensagem_perfil_ativo:
                        if not perfil_url:
                            dm_ok = False
                            dm_status = "dm_unreachable"
                        perfil_key = _chave_perfil(perfil_url)
                        if perfil_key and perfil_key in perfis_contactados_execucao:
                            _log_skip_comentario(
                                run_id=run_id or "none",
                                modo=modo,
                                chave=chave,
                                motivo="profile_already_contacted",
                                extra={"profile_key": perfil_key},
                            )
                            continue
                        if perfil_url:
                            mensagem_perfil = montar_mensagem_perfil(
                                mensagem_perfil_templates,
                                nome=nome_autor,
                                palavra_chave=palavra_chave_match or "",
                            )
                            dm_ok, dm_status = await enviar_mensagem_no_perfil(
                                context_persistent,
                                perfil_url,
                                mensagem_perfil,
                            )
                        if dm_ok and perfil_key:
                            perfis_contactados_execucao.add(perfil_key)
                        if dm_ok:
                            envios_perfil_nesta_rodada += 1
                        _agent_debug_log(
                            run_id=run_id or "none",
                            hypothesis_id="H9",
                            location="main.py:perfil:send_result",
                            message="profile_contact_result",
                            data={
                                "comment_key": chave,
                                "profile_url": perfil_url,
                                "profile_status": dm_status,
                                "profile_ok": bool(dm_ok),
                                "keyword_match": palavra_chave_match or "",
                            },
                        )
                        if not dm_ok:
                            _log_skip_comentario(
                                run_id=run_id or "none",
                                modo=modo,
                                chave=chave,
                                motivo=dm_status,
                                extra={"profile_url": perfil_url},
                            )

                    resp = await encontrar_botao_responder(item)
                    if resp is None:
                        print(
                            "[AVISO] Botão Responder não encontrado neste comentário "
                            f"(corpo: \"{texto_corpo.strip()[:50]}\"). Pulando."
                        )
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="reply_button_missing",
                        )
                        continue

                    if modo == "comentario_ia":
                        _kw = palavra_chave_match or ""
                        _det = " (palavra-chave casou)" if _kw else ""
                        print(
                            f"Comentário elegível no modo IA{_det} e pela regra de fio — respondendo."
                        )
                    else:
                        print(
                            "Comentário elegível por palavra-chave e regra de resposta — respondendo."
                        )

                    if await comentario_e_resposta_aninhada(item):
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="preclick_nested_reply",
                        )
                        continue
                    only_no_reply_thread = bool(
                        getattr(config, "SOMENTE_COMENTARIOS_SEM_RESPOSTA_NO_FIO", False)
                    )
                    if only_no_reply_thread and await fio_ja_tem_qualquer_resposta(item):
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="preclick_thread_has_reply",
                        )
                        continue
                    if (not only_no_reply_thread) and await fio_ja_tem_minha_resposta(item):
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="preclick_thread_has_my_reply",
                        )
                        continue
                    #region agent log
                    _agent_debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H3",
                        location="main.py:responder:preclick_ok",
                        message="preclick_thread_clear",
                        data={"comment_key": chave},
                    )
                    #endregion

                    # PROTEÇÃO BUG 2: Fecha overlays de messaging/DM antes de interagir
                    await _fechar_overlays_messaging(page)
                    if await _overlay_messaging_visivel(page):
                        await _fechar_overlays_messaging(page, silencioso=True)
                    if await _overlay_messaging_visivel(page):
                        print(
                            "[PROTEÇÃO] Chat do LinkedIn continua aberto sobre o post. "
                            "Pulando este item em vez de forçar o clique."
                        )
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="messaging_overlay_aberto",
                        )
                        continue

                    # Clique só no Responder: com force=True o clique caía na aba
                    # de chat do rodapé quando ela cobria o botão.
                    if not await _clique_seguro(page, resp):
                        print("[AVISO] Não consegui clicar em Responder com segurança. Pulando.")
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="reply_click_failed",
                        )
                        continue
                    await asyncio.sleep(0.35)
                    if await _saiu_do_post(page, url, url_post_carregada):
                        voltar_ao_post = True
                        break

                    try:
                        bloco_reply, caixa = await obter_caixa_resposta_ao_comentario(
                            page, item
                        )
                    except LookupError:
                        print(
                            "[AVISO] Não achei o editor de *resposta ao comentário* "
                            "(só o campo geral do post). Pulando este item."
                        )
                        await _registrar_diag_reply(
                            item, run_id=run_id or "none", modo=modo, chave=chave
                        )
                        await _descartar_casca_resposta(page, item)
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="reply_editor_missing",
                        )
                        continue

                    # O cartão é o alvo do scroll: o campo de texto pode estar
                    # no DOM sem altura até receber o primeiro caractere.
                    for _alvo_scroll in (bloco_reply, caixa):
                        try:
                            await _alvo_scroll.scroll_into_view_if_needed(timeout=8000)
                            break
                        except Exception:
                            continue
                    try:
                        await caixa.wait_for(state="attached", timeout=10_000)
                    except Exception:
                        pass

                    # Uma DM nova pode ter reaberto o chat entre o Responder e a
                    # digitação: fecha de novo antes de olhar a caixa.
                    await _fechar_overlays_messaging(page, silencioso=True)

                    # PROTEÇÃO BUG 2: a caixa tem de ser a resposta DESTE
                    # comentário. Se for o campo "Adicionar comentário…" do post
                    # ou o inbox, pular — senão o autor comenta no próprio post.
                    motivo_caixa = await _diagnostico_caixa_reply(item, caixa)
                    if motivo_caixa != "ok":
                        if motivo_caixa == "messaging":
                            print(
                                "[PROTEÇÃO] Editor encontrado é de messaging/DM! "
                                "Fechando overlay e pulando este item."
                            )
                        elif motivo_caixa == "composer_do_post":
                            print(
                                "[PROTEÇÃO] Editor é o campo Comentar do post. "
                                "Não vou comentar como autor: pulando este item."
                            )
                        else:
                            print(
                                "[PROTEÇÃO] Caixa não é a resposta deste comentário "
                                f"({motivo_caixa}). Pulando este item."
                            )
                        await _fechar_overlays_messaging(page)
                        await _descartar_casca_resposta(page, item)
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="editor_nao_eh_reply",
                            extra={"detail": motivo_caixa},
                        )
                        continue
                    if enviar_mensagem_perfil_ativo:
                        if dm_ok:
                            msg = random.choice(respostas_publicas_dm_sucesso)
                        else:
                            msg = random.choice(respostas_publicas_dm_falha)
                    elif modo == "comentario_ia":
                        gerada, ia_err = await _gerar_resposta_openai_ia(
                            post_prompt=post_context_ia,
                            nome_interno=nome_autor,
                            texto_comentario=texto_corpo,
                        )
                        if not gerada:
                            print(f"[ERRO] Geração IA indisponível: {ia_err}. Pulando item.")
                            _log_skip_comentario(
                                run_id=run_id or "none",
                                modo=modo,
                                chave=chave,
                                motivo="ia_generate_failed",
                                extra={"detail": ia_err[:500]},
                            )
                            continue
                        msg = gerada
                        if ia_err:
                            print(
                                f"[AVISO] Resposta usa fallback após falha na API: "
                                f"{ia_err[:240]}{'…' if len(ia_err) > 240 else ''}"
                            )
                    else:
                        msg = random.choice(respostas_cfg)
                    msg = aplicar_placeholders(
                        msg,
                        nome=nome_autor or "",
                        palavra_chave=palavra_chave_match or palavra_chave_auto or "",
                    )
                    print(f"Respondendo a um comentário: {msg[:60]}...")
                    await digitar_humano(caixa, msg, preservar_mencao=False)

                    # Publicar sem conferir o texto é o que gerava comentário
                    # vazio do autor no post.
                    await _fechar_overlays_messaging(page, silencioso=True)
                    if not await _texto_esta_na_caixa_reply(caixa, msg):
                        print(
                            "[PROTEÇÃO] O texto não ficou na caixa de resposta. "
                            "Abortando para não publicar comentário vazio/errado."
                        )
                        await _descartar_casca_resposta(page, item)
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="texto_fora_da_caixa_reply",
                        )
                        continue
                    if (await _diagnostico_caixa_reply(item, caixa)) != "ok":
                        print(
                            "[PROTEÇÃO] A caixa deixou de ser a resposta do comentário "
                            "antes de publicar. Abortando."
                        )
                        await _descartar_casca_resposta(page, item)
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="caixa_mudou_antes_de_publicar",
                        )
                        continue

                    _ERRO_LINKEDIN_NO_ULTIMO_ENVIO = False
                    if not await publicar_resposta_thread(
                        page, item, bloco_reply, caixa, msg
                    ):
                        recusado = _ERRO_LINKEDIN_NO_ULTIMO_ENVIO
                        if recusado:
                            recusas_linkedin_seguidas += 1
                        else:
                            print(
                                "[ERRO] Não consegui clicar em Publicar/Comentar. "
                                "Veja se o botão mudou de idioma ou layout."
                            )
                        await _descartar_casca_resposta(page, item)
                        _log_skip_comentario(
                            run_id=run_id or "none",
                            modo=modo,
                            chave=chave,
                            motivo="linkedin_recusou" if recusado else "publish_failed",
                        )
                        if recusas_linkedin_seguidas >= _MAX_RECUSAS_SEGUIDAS_LINKEDIN:
                            print(
                                f"[ERRO] O LinkedIn recusou {recusas_linkedin_seguidas} respostas "
                                "seguidas ('Erro ao publicar comentário'). Isso costuma ser a "
                                "conta limitada pelo LinkedIn por excesso de atividade. "
                                "Encerrando para não piorar a restrição. Teste responder um "
                                "comentário à mão com esta conta e aumente o intervalo entre "
                                "respostas antes de rodar de novo."
                            )
                            encerrar_por_fechamento = True
                            break
                        continue
                    recusas_linkedin_seguidas = 0

                    respondidos_na_execucao.add(chave)
                    if perfil_key:
                        perfis_respondidos_execucao.add(perfil_key)
                    # Grava na hora: se o robô cair agora, não responde de novo ao reabrir.
                    _salvar_historico_respondidos(url, chave, perfil_key)
                    try:
                        await _limpar_marcadores_reply(page)
                    except Exception:
                        pass
                    # PROTEÇÃO BUG 3: Fecha emoji picker e popovers de reação
                    await _fechar_popovers_e_emojis(page)
                    #region agent log
                    _agent_debug_log(
                        run_id=run_id or "none",
                        hypothesis_id="H4",
                        location="main.py:responder:publish_result",
                        message="reply_published_success",
                        data={
                            "comment_key": chave,
                            "body_prefix": texto_corpo.strip()[:120],
                            "reply_prefix": msg[:120],
                            "profile_status": dm_status,
                            "profile_url": profile_url_log,
                        },
                    )
                    #endregion
                    respostas_nesta_rodada += 1
                    if metricas is not None:
                        try:
                            dur_ms = int((perf_counter() - acao_inicio) * 1000)
                            metricas.registrar_resposta(msg, dur_ms)
                        except OSError as e:
                            print(f"[AVISO] Não foi possível salvar métricas: {e}")

                    espera = _sortear_intervalo_segundos()
                    if espera > 0:
                        print(f"Resposta enviada. Próxima daqui a ~{espera}s…")
                        await asyncio.sleep(espera)
                    else:
                        print("Resposta enviada. Indo para o próximo…")
                    if (
                        max_respostas_rodada > 0
                        and respostas_nesta_rodada >= max_respostas_rodada
                    ):
                        break
                except Exception as e:
                    if _erro_target_fechado(e):
                        print("[AVISO] Navegador/aba fechou durante processamento de comentário.")
                        encerrar_por_fechamento = True
                        break
                    if await _saiu_do_post(page, url, url_post_carregada):
                        voltar_ao_post = True
                        break
                    print(f"[ERRO] Comentário índice {i}: {e!r}")
                    continue
                finally:
                    em_processamento.discard(chave)

            if encerrar_por_fechamento:
                break

            if not voltar_ao_post and await _saiu_do_post(page, url, url_post_carregada):
                voltar_ao_post = True
            if voltar_ao_post:
                retornos_ao_post += 1
                try:
                    saiu_para = page.url
                except Exception:
                    saiu_para = "?"
                print(
                    f"[PROTEÇÃO] A aba saiu do post ({saiu_para}). "
                    "Voltando ao post e continuando a responder."
                )
                _debug_log(
                    run_id=run_id or "none",
                    hypothesis_id="H11",
                    location="main.py:iniciar_bot:saiu_do_post",
                    message="left_post_page",
                    data={"url_atual": saiu_para, "retornos": retornos_ao_post},
                )
                if retornos_ao_post > 5:
                    print(
                        "[ERRO] A aba saiu do post várias vezes seguidas. "
                        "Encerrando para não ficar navegando sozinha."
                    )
                    break
                # Força o recarregamento completo do post na próxima rodada.
                ultima_url_carregada = None
                continue
            retornos_ao_post = 0

            if pulados_nesta_rodada > 0 and metricas is not None:
                try:
                    metricas.registrar_pulado(pulados_nesta_rodada)
                except OSError as e:
                    print(f"[AVISO] Não foi possível salvar métricas (pulados): {e}")
            _salvar_dashboard_execucao("fim_rodada")

            if respostas_nesta_rodada == 0:
                consecutive_idle += 1
                print(
                    f"Nenhum comentário elegível nesta rodada ({consecutive_idle}/{max_idle})."
                )
                if skip_motivos_rodada and modo != "comentario_ia":
                    resumo = ", ".join(
                        f"{k}={v}" for k, v in sorted(skip_motivos_rodada.items())
                    )
                    print(f"[Info] Motivos de exclusão nesta rodada: {resumo}")
                    if skip_motivos_rodada.get("keyword_miss", 0) > 0 and palavras:
                        print(
                            "[Dica] Nenhum comentário continha as palavras-chave configuradas. "
                            f"Ativas agora: {', '.join(palavras)}"
                        )
            else:
                consecutive_idle = 0
                print(
                    f"Rodada OK: {respostas_nesta_rodada} resposta(s). "
                    "Nova varredura após rolar o post…"
                )

        print(
            "Encerrado: nenhum comentário elegível nas últimas rodadas "
            "(ou a página não mostra mais blocos de comentário)."
        )

        if metricas is not None and run_id:
            try:
                metricas.finalizar_execucao(run_id, mode=run_mode, url_post=run_url)
                _debug_log(
                    run_id=run_id,
                    hypothesis_id="H5",
                    location="main.py:iniciar_bot:finish",
                    message="run_finalized_call",
                    data={"mode": run_mode, "url_set": bool(run_url)},
                )
                if dashboard_no_fim:
                    path_dash = metricas.abrir_dashboard()
                    print(f"[Info] Dashboard reaberto em: {path_dash}")
            except OSError as e:
                print(f"[AVISO] Não foi possível finalizar registro de execução: {e}")

        if fechar_context and context_persistent is not None:
            try:
                await context_persistent.close()
            except Exception as e:
                if not _erro_target_fechado(e):
                    raise


if __name__ == "__main__":
    asyncio.run(iniciar_bot())
