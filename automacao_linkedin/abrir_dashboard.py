"""Abre só o painel de métricas (HTML + JSON), sem iniciar o bot/Playwright."""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import metricas  # noqa: E402

if __name__ == "__main__":
    print(metricas.abrir_dashboard())
