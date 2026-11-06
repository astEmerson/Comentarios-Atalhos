"""
Executa o robô para UMA conta.

A conta é escolhida por --conta (ou pela variável AUTOMACAO_CONTA_ID). Como cada
processo tem seu próprio settings, histórico, métricas e pasta de perfil do navegador,
é possível rodar várias contas — ou a mesma conta em posts diferentes — ao mesmo tempo.
"""

import argparse
import asyncio
import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_AQUI)
for _p in (_RAIZ, _AQUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _preparar_ambiente() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conta", default="", help="Id da conta a executar")
    args, _ = parser.parse_known_args()

    conta_id = (args.conta or os.environ.get("AUTOMACAO_CONTA_ID") or "").strip()
    if not conta_id:
        return ""

    from settings_manager import ENV_CONTA_ID, ENV_SETTINGS_FILE, caminho_conta

    os.environ[ENV_CONTA_ID] = conta_id
    os.environ.setdefault(ENV_SETTINGS_FILE, caminho_conta(conta_id))
    return conta_id


if __name__ == "__main__":
    _conta = _preparar_ambiente()

    # Importado só depois do ambiente pronto: config e métricas resolvem
    # seus caminhos no momento do import.
    from main import iniciar_bot

    print(f"[run_bot] Iniciando robô da conta: {_conta or 'padrão'}", flush=True)
    try:
        asyncio.run(iniciar_bot())
    except KeyboardInterrupt:
        print("[run_bot] Encerrado pelo usuário.", flush=True)
    except Exception as e:
        print(f"[run_bot] Erro não tratado: {e}", flush=True)
    finally:
        print("[run_bot] Processo finalizado.", flush=True)
