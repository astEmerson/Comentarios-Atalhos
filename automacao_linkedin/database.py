import os
from typing import Dict, Any

# Credenciais só por variável de ambiente. Não grave chave neste arquivo.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def fetch_user_config(user_code: str):
    """
    Busca as configurações das campanhas associadas ao 'user_code' (PIN).
    Retorna uma lista de dicionários com os campos de cada campanha.
    """
    try:
        from supabase import create_client, Client
    except ImportError:
        print("[ERRO] Biblioteca 'supabase' não está instalada.")
        return []

    if SUPABASE_URL == "YOUR_SUPABASE_URL":
        print("[ERRO] Supabase URL não configurada no código-fonte.")
        return []

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Faz uma consulta na tabela "campanhas_bot"
        response = supabase.table("campanhas_bot").select("*").eq("pin", user_code.strip()).execute()
        
        if response.data and len(response.data) > 0:
            campanhas = []
            for row in response.data:
                campanhas.append({
                    "id": row.get("id"),
                    "NOME_USUARIO": row.get("nome_usuario", "Vendedor"),
                    "NOME_CAMPANHA": row.get("nome_campanha", f"Campanha {row.get('id', 'Desconhecida')}"),
                    "OPENAI_API_KEY": row.get("openai_api_key", ""),
                    "URL_POST": row.get("url_post", ""),
                    "IA_TOM_RESPOSTA": row.get("tom_ia", "Automatico"),
                    "PALAVRAS_CHAVE": row.get("palavras_chave", ""),
                })
            return campanhas
        else:
            print("[AVISO] Nenhum perfil encontrado para este usuário.")
            return []
            
    except Exception as e:
        print(f"[ERRO] Falha ao conectar no Supabase: {e}")
        return []

def carregar_metricas_nuvem(pin: str, nome_campanha: str):
    """
    Busca o JSON de métricas associado a este PIN e Campanha.
    """
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        response = supabase.table("metricas_bot").select("metricas_json").eq("pin", pin).eq("nome_campanha", nome_campanha).execute()
        if response.data and len(response.data) > 0:
            return response.data[0].get("metricas_json")
        return None
    except Exception as e:
        print(f"[ERRO] Falha ao buscar métricas: {e}")
        return None

def salvar_metricas_nuvem(pin: str, nome_campanha: str, metricas_json: dict):
    """
    Atualiza (ou insere) o JSON de métricas na nuvem.
    """
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Verifica se já existe
        response = supabase.table("metricas_bot").select("id").eq("pin", pin).eq("nome_campanha", nome_campanha).execute()
        
        if response.data and len(response.data) > 0:
            # Update
            supabase.table("metricas_bot").update({"metricas_json": metricas_json}).eq("pin", pin).eq("nome_campanha", nome_campanha).execute()
        else:
            # Insert
            supabase.table("metricas_bot").insert({
                "pin": pin,
                "nome_campanha": nome_campanha,
                "metricas_json": metricas_json
            }).execute()
    except Exception as e:
        print(f"[ERRO] Falha ao salvar métricas na nuvem: {e}")

