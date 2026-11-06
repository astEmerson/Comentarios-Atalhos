import importlib


def _sanitize_token(raw: str, default: str) -> str:
    val = (raw or "").strip().lower().replace("-", "_")
    if not val:
        return default
    out = []
    for ch in val:
        if ch.isalnum() or ch == "_":
            out.append(ch)
    cleaned = "".join(out).strip("_")
    return cleaned or default


def carregar_overrides_modo(config_module):
    modo = _sanitize_token(getattr(config_module, "MODO_AUTOMACAO", "dm_com_comentario"), "dm_com_comentario")
    preset = _sanitize_token(getattr(config_module, "PRESET_AUTOMACAO", "default"), "default")

    merged = {}
    source = {"modo": modo, "preset": preset, "base_loaded": False, "preset_loaded": False}

    try:
        mod_base = importlib.import_module(f"modos.{modo}.settings")
        importlib.reload(mod_base)
        merged.update(dict(getattr(mod_base, "CONFIG_BASE", {}) or {}))
        source["base_loaded"] = True
    except Exception:
        return {}, source

    try:
        mod_preset = importlib.import_module(f"modos.{modo}.presets.{preset}")
        importlib.reload(mod_preset)
        merged.update(dict(getattr(mod_preset, "CONFIG", {}) or {}))
        source["preset_loaded"] = True
    except Exception:
        pass

    return merged, source


def aplicar_overrides_config(config_module, overrides: dict) -> None:
    for k, v in (overrides or {}).items():
        if not isinstance(k, str):
            continue
        if not k:
            continue
        setattr(config_module, k, v)
