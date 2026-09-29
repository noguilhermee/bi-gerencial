from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo
import pandas as pd
import yaml

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def categorizar_arquivo(nome_arquivo: str) -> Dict[str, str]:
    nome = nome_arquivo.upper()
    if "VINCULOS" in nome or "GRUPO" in nome or "RESGATE" in nome:
        return {
            "categoria": "Vínculos e Grupos",
            "icone": "link",
            "descricao": "Relação de fazendas, grupos de atendimento e vínculos técnicos",
        }
    if "INATIVACAO" in nome or "INATIVACOES" in nome:
        return {
            "categoria": "Inativações",
            "icone": "person_remove",
            "descricao": "Solicitações e histórico de inativação de produtores",
        }
    if "CADASTRO" in nome:
        return {
            "categoria": "Novos Cadastros",
            "icone": "person_add",
            "descricao": "Solicitações de novos cadastros e troca de titularidade",
        }
    if "STATUS_USUARIO" in nome or "CONSULTOR" in nome:
        return {
            "categoria": "Equipe e Usuários",
            "icone": "badge",
            "descricao": "Status cadastral e histórico de atividade dos consultores",
        }
    if "VISITA" in nome or "VISITAS" in nome:
        return {
            "categoria": "Visitas Técnicas",
            "icone": "calendar_month",
            "descricao": "Relatórios de atendimentos em campo e apontamentos",
        }
    return {
        "categoria": "Outros Relatórios",
        "icone": "description",
        "descricao": "Planilha auxiliar do SmartQuestion",
    }


def formatar_tamanho_bytes(tamanho_bytes: int) -> str:
    if tamanho_bytes < 1024:
        return f"{tamanho_bytes} B"
    elif tamanho_bytes < 1024 * 1024:
        return f"{tamanho_bytes / 1024:.1f} KB"
    else:
        return f"{tamanho_bytes / (1024 * 1024):.1f} MB"


def obter_info_consistencia_supabase(raiz: Path) -> Dict[str, Any]:
    """Consulta segura (somente leitura) no Supabase dos metadados das tabelas de consistência."""
    info = {
        "consistencia_mensal": {
            "tabela": "elabore_raw_consistencia_mensal",
            "ultima_atualizacao": None,
            "ultima_atualizacao_formatada": "Não disponível",
            "total_registros": None,
            "status": "Não sincronizado",
        },
        "consistencia_anual": {
            "tabela": "elabore_raw_consistencia_anual",
            "ultima_atualizacao": None,
            "ultima_atualizacao_formatada": "Não disponível",
            "total_registros": None,
            "status": "Não sincronizado",
        }
    }
    
    try:
        from dotenv import load_dotenv
        for env_path in [
            raiz / "scripts" / "config" / ".env",
            raiz / "dashboard" / ".env.local",
            raiz / "SCRIPTS" / "CONFIG" / ".env",
            raiz / "DASHBOARD" / ".env.local",
            raiz / ".env"
        ]:
            if env_path.is_file():
                load_dotenv(env_path)
                break
                
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        
        if supabase_url and supabase_key:
            from supabase import create_client
            supabase = create_client(supabase_url, supabase_key)
            
            # 1. Consistência Mensal
            try:
                res_m = supabase.table("elabore_raw_consistencia_mensal").select("data_processamento").order("data_processamento", desc=True).limit(1).execute()
                res_m_count = supabase.table("elabore_raw_consistencia_mensal").select("idfazenda", count="exact").limit(1).execute()
                
                dt_m = None
                if res_m.data and res_m.data[0].get("data_processamento"):
                    dt_m_str = res_m.data[0]["data_processamento"]
                    dt_m = datetime.fromisoformat(dt_m_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Sao_Paulo"))
                    dt_m_fmt = dt_m.strftime("%d/%m/%Y às %H:%M")
                else:
                    dt_m_fmt = "Sem registro"
                    
                info["consistencia_mensal"] = {
                    "tabela": "elabore_raw_consistencia_mensal",
                    "ultima_atualizacao": dt_m.isoformat() if dt_m else None,
                    "ultima_atualizacao_formatada": dt_m_fmt,
                    "total_registros": res_m_count.count if hasattr(res_m_count, "count") and res_m_count.count is not None else len(res_m.data),
                    "status": "✅ Sincronizado",
                }
            except Exception as e:
                print(f"⚠️ Aviso ao consultar elabore_raw_consistencia_mensal: {e}")

            # 2. Consistência Anual
            try:
                res_a = supabase.table("elabore_raw_consistencia_anual").select("data_processamento").order("data_processamento", desc=True).limit(1).execute()
                res_a_count = supabase.table("elabore_raw_consistencia_anual").select("idfazenda", count="exact").limit(1).execute()
                
                dt_a = None
                if res_a.data and res_a.data[0].get("data_processamento"):
                    dt_a_str = res_a.data[0]["data_processamento"]
                    dt_a = datetime.fromisoformat(dt_a_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Sao_Paulo"))
                    dt_a_fmt = dt_a.strftime("%d/%m/%Y às %H:%M")
                else:
                    dt_a_fmt = "Sem registro"
                    
                info["consistencia_anual"] = {
                    "tabela": "elabore_raw_consistencia_anual",
                    "ultima_atualizacao": dt_a.isoformat() if dt_a else None,
                    "ultima_atualizacao_formatada": dt_a_fmt,
                    "total_registros": res_a_count.count if hasattr(res_a_count, "count") and res_a_count.count is not None else len(res_a.data),
                    "status": "✅ Sincronizado",
                }
            except Exception as e:
                print(f"⚠️ Aviso ao consultar elabore_raw_consistencia_anual: {e}")
                
    except Exception as e:
        print(f"⚠️ Aviso ao inicializar Supabase para metadados: {e}")
        
    return info


def obter_metadados_planilhas(raiz_projeto: Path | str | None = None) -> Dict[str, Any]:
    caminho_base = Path(raiz_projeto or Path.cwd()).resolve()
    for candidato in [caminho_base, *caminho_base.parents]:
        if (candidato / "scripts").is_dir() and ((candidato / "db").is_dir() or (candidato / "dashboard").is_dir()):
            raiz = candidato
            break
        if (candidato / "SCRIPTS").is_dir() and ((candidato / "DB").is_dir() or (candidato / "DASHBOARD").is_dir()):
            raiz = candidato
            break
    else:
        raiz = caminho_base
        
    config_path = raiz / "scripts" / "config" / "config.yaml"
    if not config_path.exists():
        config_path = raiz / "SCRIPTS" / "CONFIG" / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo config.yaml não encontrado em {config_path}")
        
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
        
    bd_path = Path(cfg.get("caminhos", {}).get("bd_smartquestion", ""))
    
    arquivos_info: List[Dict[str, Any]] = []
    arquivo_mais_recente_nome = None
    arquivo_mais_recente_data = None
    max_mtime = None
    
    if bd_path.exists():
        for arquivo in sorted(bd_path.glob("*.xlsx")):
            stat = arquivo.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo("America/Sao_Paulo"))
            cat_info = categorizar_arquivo(arquivo.name)
            
            if max_mtime is None or mtime > max_mtime:
                max_mtime = mtime
                arquivo_mais_recente_nome = arquivo.name
                arquivo_mais_recente_data = mtime.strftime("%d/%m/%Y às %H:%M")
            
            # Tentar ler linhas de forma segura sem travar
            qtd_linhas = None
            try:
                # Leitura rápida de nomes de abas e dimensões básicas
                xl = pd.ExcelFile(arquivo)
                primeira_aba = xl.sheet_names[0]
                df_sample = xl.parse(primeira_aba)
                qtd_linhas = len(df_sample)
            except Exception:
                qtd_linhas = None
                
            arquivos_info.append({
                "nome": arquivo.name,
                "categoria": cat_info["categoria"],
                "icone": cat_info["icone"],
                "descricao": cat_info["descricao"],
                "data_modificacao": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                "data_modificacao_formatada": mtime.strftime("%d/%m/%Y às %H:%M"),
                "tamanho_bytes": stat.st_size,
                "tamanho_formatado": formatar_tamanho_bytes(stat.st_size),
                "total_registros": qtd_linhas,
                "caminho": str(arquivo),
            })
            
    # Consultar metadados de consistência no Supabase (somente leitura)
    info_consistencia = obter_info_consistencia_supabase(raiz)
    
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    payload = {
        "timestamp_inspecao": agora.isoformat(),
        "timestamp_etl": agora.isoformat(),
        "ultima_execucao_etl_formatada": agora.strftime("%d/%m/%Y às %H:%M:%S"),
        "ultima_leitura_planilhas_formatada": agora.strftime("%d/%m/%Y às %H:%M:%S"),
        "arquivo_mais_recente_nome": arquivo_mais_recente_nome,
        "arquivo_mais_recente_data": arquivo_mais_recente_data,
        "total_arquivos": len(arquivos_info),
        "diretorio_origem": str(bd_path),
        "consistencia_mensal": info_consistencia.get("consistencia_mensal"),
        "consistencia_anual": info_consistencia.get("consistencia_anual"),
        "arquivos": arquivos_info,
    }
    
    # Salvar cópia em db/output/processed/fontes_metadados.json
    pasta_saida = raiz / "db" / "output" / "processed"
    if not pasta_saida.exists() and (raiz / "DB" / "OUTPUT" / "PROCESSED").exists():
        pasta_saida = raiz / "DB" / "OUTPUT" / "PROCESSED"
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho_json = pasta_saida / "fontes_metadados.json"
    
    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    # Também salvar cópia no dashboard para fallback estático se necessário
    dashboard_data_dir = raiz / "dashboard" / "public" / "data"
    if not dashboard_data_dir.exists() and (raiz / "DASHBOARD" / "public" / "data").exists():
        dashboard_data_dir = raiz / "DASHBOARD" / "public" / "data"
    dashboard_data_dir.mkdir(parents=True, exist_ok=True)
    with open(dashboard_data_dir / "fontes_metadados.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    return payload


if __name__ == "__main__":
    resultado = obter_metadados_planilhas()
    print(f"Metadados gerados com sucesso para {resultado['total_arquivos']} planilhas.")

