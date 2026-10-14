import difflib
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Set
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yaml

FUSO_SP = ZoneInfo("America/Sao_Paulo")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

caminho_atual = Path(__file__).resolve()
for candidato in [caminho_atual, *caminho_atual.parents]:
    if (candidato / "scripts").is_dir() and ((candidato / "data").is_dir() or (candidato / "app").is_dir()):
        raiz_projeto = candidato
        break
    if (candidato / "SCRIPTS").is_dir() and ((candidato / "DATA").is_dir() or (candidato / "APP").is_dir()):
        raiz_projeto = candidato
        break
else:
    raiz_projeto = caminho_atual.parents[2]

for p in [
    raiz_projeto,
    raiz_projeto / "scripts",
    raiz_projeto / "scripts" / "functions",
    raiz_projeto / "SCRIPTS",
    raiz_projeto / "SCRIPTS" / "FUNCTIONS",
]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from functions.function import (
        carregar_config_referencia,
        carregar_env,
        obter_cliente_supabase,
        consultar_tabela_supabase,
    )
    from functions.metadata_tracker import obter_metadados_planilhas
    from functions import regras_negocio as rn
    from functions.carregar_status_consultor import ler_historico_status, consultores_inativos as consultores_inativos_sq
except ImportError:
    from FUNCTIONS.function import (
        carregar_config_referencia,
        carregar_env,
        obter_cliente_supabase,
        consultar_tabela_supabase,
    )
    from FUNCTIONS.metadata_tracker import obter_metadados_planilhas
    from FUNCTIONS.carregar_status_consultor import ler_historico_status, consultores_inativos as consultores_inativos_sq
    try:
        from FUNCTIONS import regras_negocio as rn
    except ImportError:
        import regras_negocio as rn


def _normalizar_nome(texto: str) -> str:
    """Remove acentos, colapsa espaços e coloca em maiúsculas para casar nomes entre fontes."""
    if not texto or not isinstance(texto, str):
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


MOTIVO_CADASTRO_GENERICO = "Cadastro de Produtor(a)"


def _eh_nome_placeholder(nome) -> bool:
    """Nome genérico do SmartQuestion ("PRODUTOR(A) PARA CADASTRO") ou fallback "Novo Produtor (...)"."""
    if not isinstance(nome, str) or not nome.strip() or nome.strip().lower() == "nan":
        return True
    upper = nome.strip().upper()
    return "CADASTRO" in upper or upper.startswith("NOVO PRODUTOR")


def _id_atendimento_str(valor) -> str | None:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return None
    s = str(valor).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s and s.lower() != "nan" else None


def extrair_consultor_individual(consultor_str: str, grupo_str: str = "") -> str:
    """Extrai o consultor individual responsável a partir do grupo ou do campo de consultor."""
    texto = grupo_str if (isinstance(grupo_str, str) and grupo_str.strip()) else consultor_str
    if not isinstance(texto, str) or not texto.strip():
        return "NÃO ATRIBUÍDO"
        
    texto_upper = texto.upper().strip()
    if "CELIO ROBERTO OLIVEIRA" in texto_upper or "SUELY DE JESUS OLIVEIRA" in texto_upper:
        return "LAC CONSULTORIA"
        
    limpo = re.sub(r"\(.*?\)", "", texto).strip()
    if limpo:
        partes = [p.strip().upper() for p in limpo.split("/") if p.strip()]
        if partes:
            ans = partes[0]
            if ans in ["MARIO BARBOSA FILHO", "MARIO BARBOSA"]:
                return "MARIO BARBOSA ROSA FILHO"
            return ans
        ans = limpo.upper()
        if ans in ["MARIO BARBOSA FILHO", "MARIO BARBOSA"]:
            return "MARIO BARBOSA ROSA FILHO"
        return ans
    return "NÃO ATRIBUÍDO"
        
    texto_upper = texto.upper().strip()
    if "CELIO ROBERTO OLIVEIRA" in texto_upper or "SUELY DE JESUS OLIVEIRA" in texto_upper:
        return "LAC CONSULTORIA"
        
    limpo = re.sub(r"\(.*?\)", "", texto).strip()
    if limpo:
        partes = [p.strip().upper() for p in limpo.split("/") if p.strip()]
        if partes:
            return partes[0]
        return limpo.upper()
    return "NÃO ATRIBUÍDO"


def executar_reconciliacao(reindex_completo: bool = False):
    print("=================================================================")
    print("   INICIANDO RECONCILIAÇÃO DE VÍNCULOS, INATIVAÇÕES E ATIVOS     ")
    print("=================================================================")

    config = carregar_config_referencia(raiz_projeto)
    supabase = obter_cliente_supabase(raiz_projeto)
    
    from functions.config_projeto import ler_config
    cfg_raw = ler_config(raiz_projeto)
        
    bd_path = Path(cfg_raw.get("caminhos", {}).get("bd_smartquestion", ""))
    
    # 1. Atualizar Metadados de Proveniência
    print("\n📦 1. Atualizando metadados de proveniência das planilhas...")
    metadados = obter_metadados_planilhas(raiz_projeto)
    print(f"   -> {metadados['total_arquivos']} arquivos rastreados com sucesso.")

    # 2. Status atual dos consultores: última alteração de cada usuário (originalOid) do histórico do
    # SmartQuestion, com todos os nomes que ele já usou (carregar_status_consultor.py). Nomes em chave()
    # (sem acento, maiúsculo), o mesmo formato dos grupos lidos do banco.
    print("\n👤 2. Verificando status dos consultores (BD_STATUS_USUARIO_SQ.xlsx)...")
    consultores_inativos: Set[str] = set()
    try:
        historico_status = ler_historico_status()
        consultores_inativos = consultores_inativos_sq(historico_status)
        print(f"   -> {historico_status['id_usuario'].nunique()} usuários avaliados "
              f"({len(historico_status)} alterações no histórico).")
        print(f"   -> {len(consultores_inativos)} nomes de consultores inativos identificados.")
    except Exception as e:
        print(f"   ⚠️ Aviso ao ler BD_STATUS_USUARIO_SQ.xlsx (nenhum consultor será tratado como inativo): {e}")

    # 3. Processar Inativações Recentes para sq_raw_inativacoes_produtor
    print("\n🚫 3. Processando solicitações de inativação (sq_raw_inativacoes_produtor)...")
    
    # Verificar se há inativações recentes na pasta para sincronizar no Supabase
    try:
        arquivos_inat = list(bd_path.glob("*_LISTA_INATIVACAO.xlsx")) + list((bd_path / "BACKUPS").glob("*_LISTA_INATIVACAO.xlsx"))
        if arquivos_inat:
            for arq_inat in arquivos_inat:
                if arq_inat.exists():
                    df_raw_i = pd.read_excel(arq_inat, header=None)
                    h_i = 0
                    for r in range(min(5, len(df_raw_i))):
                        vals = [str(x).strip().lower() for x in df_raw_i.iloc[r].dropna().tolist()]
                        if any("atendimento" in v for v in vals):
                            h_i = r
                            break
                    df_i = pd.read_excel(arq_inat, header=h_i).dropna(how="all", axis=1).dropna(how="all", axis=0)
                    col_id_i = [c for c in df_i.columns if "atendimento" in str(c).lower()]
                    if col_id_i:
                        df_i["id_atendimento"] = pd.to_numeric(df_i[col_id_i[0]], errors="coerce")
                        df_i = df_i.dropna(subset=["id_atendimento"])
                        df_i["id_atendimento"] = df_i["id_atendimento"].astype(int)
                        
                        mapa_cols = {
                            "Consultor(a):": "nome_consultor",
                            "Projeto": "projeto",
                            "Código do(a) produtor(a):": "codigo_lr",
                            "Produtor(a):": "nome_produtor",
                            "Propriedade:": "nome_propriedade",
                            "Grupo Ponto Atendimento": "grupo_ponto_atendimento",
                            "Data da solicitação:": "data_solicitacao",
                            "Data da inativação:": "data_inativacao",
                            "Motivo da inativação:": "motivo_inativacao",
                            "Se outro, qual motivo?": "outro_motivo",
                            "Status": "produtor_ativo",
                        }
                        df_prep_i = df_i.rename(columns={k: v for k, v in mapa_cols.items() if k in df_i.columns})
                        df_prep_i["data_processamento"] = datetime.now(FUSO_SP).isoformat()
                        
                        # Converter datas
                        if "data_solicitacao" in df_prep_i.columns:
                            df_prep_i["data_solicitacao"] = pd.to_datetime(df_prep_i["data_solicitacao"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                        if "data_inativacao" in df_prep_i.columns:
                            df_prep_i["data_inativacao"] = pd.to_datetime(df_prep_i["data_inativacao"], errors="coerce").dt.strftime("%Y-%m-%d")
                            
                        cols_finais = ["id_atendimento", "nome_consultor", "projeto", "codigo_lr", "nome_produtor", "nome_propriedade", "grupo_ponto_atendimento", "data_solicitacao", "data_inativacao", "motivo_inativacao", "outro_motivo", "produtor_ativo", "data_processamento"]
                        cols_presentes = [c for c in cols_finais if c in df_prep_i.columns]
                        registros_i = df_prep_i[cols_presentes].replace({np.nan: None}).to_dict(orient="records")
                        if registros_i:
                            supabase.table("sq_raw_inativacoes_produtor").upsert(registros_i, on_conflict="id_atendimento").execute()
    except Exception as e_inat:
        print(f"   ⚠️ Aviso ao sincronizar inativações recentes: {e_inat}")

    # Buscar inativações já consolidadas no Supabase com paginação transparente (P-09)
    df_inats_existentes = consultar_tabela_supabase("sq_raw_inativacoes_produtor", "*", raiz=raiz_projeto)
    print(f"   -> Total de inativações existentes no banco: {len(df_inats_existentes)}")

    # 4. Construir Movimentações Consolidadas (tab_fato_movimentacao)
    print("\n🔄 4. Consolidando tabela fato de movimentação (tab_fato_movimentacao)...")
    
    PROJETOS_OFICIAIS = ['ALVOAR ASSIST', 'ALVOAR ECO', 'ATEG_CCPR', 'LPA', 'REGENERA', 'SEMEAR']

    movimentacoes_lista = []

    # 4.1 Entradas Pré-2026 (a partir de sq_raw_vinculos com paginação transparente P-09)
    cols_vinc = "codigo_lr, consultor_grupo_atendimento, grupo_atendimento, data_associacao, projeto, nome_produtor, nome_propriedade, vinculo_ativo, unidade_atendimento, cidade_produtor, estado_produtor, codigo_agroindustria, codigo_fazenda"
    df_vinc_db = consultar_tabela_supabase("sq_raw_vinculos", cols_vinc, filtros_in={"projeto": PROJETOS_OFICIAIS}, raiz=raiz_projeto)
    
    if not df_vinc_db.empty:
        for _, row in df_vinc_db.iterrows():
            cod = str(row.get("codigo_lr") or "").strip()
            if not cod or cod.lower() == "nan":
                continue
            raw_cons = str(row.get("consultor_grupo_atendimento") or row.get("grupo_atendimento") or "").strip()
            cons = raw_cons if (raw_cons and raw_cons.lower() != "nan") else extrair_consultor_individual(row.get("consultor_grupo_atendimento"), row.get("grupo_atendimento"))
            proj = str(row.get("projeto") or "").strip().upper()
            if proj not in PROJETOS_OFICIAIS:
                continue
            if rn.eh_grupo_excluido(cons):
                continue
            dt_assoc = row.get("data_associacao")
            if dt_assoc:
                try:
                    dt_mov = pd.to_datetime(dt_assoc).strftime("%Y-%m-01")
                except Exception:
                    dt_mov = "2024-01-01"
            else:
                dt_mov = "2024-01-01"
                
            # Incluir todas as entradas válidas de vínculos (preservando o grupo completo)
            nome_prod_vinc = str(row.get("nome_produtor") or "").strip()
            dt_assoc_str = None
            if dt_assoc:
                try:
                    dt_assoc_str = pd.to_datetime(dt_assoc).strftime("%Y-%m-%d")
                except Exception:
                    dt_assoc_str = None
            id_comp = f"{cod}_{dt_mov}_Entrada"
            movimentacoes_lista.append({
                "id_composto": id_comp,
                "codigo_lr": cod,
                "nome_consultor": cons,
                "nome_produtor": nome_prod_vinc if (nome_prod_vinc and nome_prod_vinc.lower() != "nan") else None,
                "numero_atendimento": None,
                "data_movimentacao": dt_mov,
                "movimentacao": "Entrada",
                "motivo_inativacao": None,
                "outro_motivo": None,
                "data_solicitacao": dt_assoc_str,
                "origem_dado": "sq_raw_vinculos",
                "data_processamento": datetime.now(FUSO_SP).isoformat(),
            })

    # ─── Resolução de codigo_lr dos cadastros ──────────────────────────────────
    # Os cadastros (LISTA_CADASTRO.xlsx / sq_raw_visitas) trazem um "código do produtor"
    # provisório que não bate com nenhuma chave de sq_raw_vinculos nem de tab_dim_fazendas_ativas.
    # Resolvemos o codigo_lr real só pelo nome, em 3 critérios de prioridade decrescente; se
    # nenhum resolver de forma inequívoca, o cadastro NÃO entra em sq_raw_movimentacao — vai para
    # a planilha de auditoria em data/output/logs/ e é resolvido sozinho numa execução futura,
    # quando o vínculo existir.
    df_vinc_qualquer_projeto = consultar_tabela_supabase(
        "sq_raw_vinculos",
        "codigo_lr, nome_produtor, data_associacao, projeto, consultor_grupo_atendimento, cidade_produtor",
        raiz=raiz_projeto,
    )
    vinc_por_nome: Dict[str, List[dict]] = {}
    if not df_vinc_qualquer_projeto.empty:
        for _, row in df_vinc_qualquer_projeto.iterrows():
            n = _normalizar_nome(row.get("nome_produtor"))
            c = str(row.get("codigo_lr") or "").strip()
            if not n or not c:
                continue
            vinc_por_nome.setdefault(n, []).append({
                "codigo_lr": c,
                "data_associacao": row.get("data_associacao"),
                "consultor": str(row.get("consultor_grupo_atendimento") or ""),
                "cidade": _normalizar_nome(row.get("cidade_produtor")),
            })

    lg_por_nome: Dict[str, List[dict]] = {}
    arquivo_lg_resolver = bd_path / "LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx"
    if arquivo_lg_resolver.exists():
        try:
            df_lg_resolver = pd.read_excel(arquivo_lg_resolver)
            col_nome_lg = next((c for c in df_lg_resolver.columns if str(c).strip() == "Nome"), None)
            col_cod_lg = next((c for c in df_lg_resolver.columns if "código" in str(c).lower() or "codigo" in str(c).lower()), None)
            col_cid_lg = next((c for c in df_lg_resolver.columns if "cidade" in str(c).lower()), None)
            col_grp_lg = next((c for c in df_lg_resolver.columns if "grupo ponto" in str(c).lower()), None)
            if col_nome_lg is not None and col_cod_lg is not None:
                for _, row in df_lg_resolver.iterrows():
                    n = _normalizar_nome(row.get(col_nome_lg))
                    c = str(row.get(col_cod_lg) or "").strip()
                    if not n or not c or c.lower() == "nan":
                        continue
                    lg_por_nome.setdefault(n, []).append({
                        "codigo_lr": c,
                        "cidade": _normalizar_nome(row.get(col_cid_lg)) if col_cid_lg is not None else "",
                        "consultor": str(row.get(col_grp_lg) or "") if col_grp_lg is not None else "",
                    })
        except Exception as e_lg_resolver:
            print(f"   ⚠️ Aviso ao ler {arquivo_lg_resolver.name} para resolução de cadastros: {e_lg_resolver}")

    todos_nomes_conhecidos = sorted(set(vinc_por_nome) | set(lg_por_nome))
    cadastros_sem_resolucao: List[dict] = []

    def resolver_codigo_lr_cadastro(nome: str, mes: str, id_atend: str, consultor: str = "",
                                     cidade: str = "", tipo_cadastro: str = "") -> str | None:
        n = _normalizar_nome(nome)
        if not n:
            return None
        eh_troca = "troca" in (tipo_cadastro or "").lower()

        # (a) nome idêntico no vínculo, qualquer projeto, com data entre -1 e +2 meses da
        # solicitação (troca de titularidade aceita qualquer data, já que o vínculo é antigo).
        candidatos_a = vinc_por_nome.get(n, [])
        if candidatos_a:
            if eh_troca:
                escolhidos = {c["codigo_lr"] for c in candidatos_a}
            else:
                escolhidos = set()
                try:
                    mes_ts = pd.Timestamp(mes)
                    janela_ini, janela_fim = mes_ts - pd.DateOffset(months=1), mes_ts + pd.DateOffset(months=2)
                    for c in candidatos_a:
                        dt_a = pd.to_datetime(c["data_associacao"], errors="coerce")
                        if pd.notna(dt_a) and janela_ini <= dt_a <= janela_fim:
                            escolhidos.add(c["codigo_lr"])
                except Exception:
                    pass
            if len(escolhidos) == 1:
                return escolhidos.pop()

        # (b) nome idêntico na LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx (coluna "Código" = codigo_lr).
        codigos_b = {c["codigo_lr"] for c in lg_por_nome.get(n, [])}
        if len(codigos_b) == 1:
            return codigos_b.pop()

        # (c) nome parecido (similaridade >= 0,85), só aceito se sobrar 1 candidato do mesmo
        # consultor ou da mesma cidade — evita casar produtores homônimos diferentes.
        parecidos = difflib.get_close_matches(n, todos_nomes_conhecidos, n=5, cutoff=0.85)
        if parecidos:
            candidatos_c = []
            for p in parecidos:
                candidatos_c += vinc_por_nome.get(p, [])
                candidatos_c += lg_por_nome.get(p, [])
            cid_n, cons_n = _normalizar_nome(cidade), _normalizar_nome(consultor)
            filtrados = [
                c for c in candidatos_c
                if (cid_n and c.get("cidade") == cid_n) or (cons_n and cons_n in _normalizar_nome(c.get("consultor")))
            ]
            codigos_c = {c["codigo_lr"] for c in filtrados}
            if len(codigos_c) == 1:
                return codigos_c.pop()

        return None

    # Mapeamento dimensional para validação da cadeia produtiva com paginação transparente (P-09)
    df_all_vinc = consultar_tabela_supabase("sq_raw_vinculos", "codigo_lr, projeto, tipo_ponto_atendimento, nome_produtor", raiz=raiz_projeto)
    mapa_lr_tipo: Dict[str, str] = {}
    mapa_lr_proj: Dict[str, str] = {}
    mapa_lr_nome: Dict[str, str] = {}
    for _, v in df_all_vinc.iterrows():
        c_lr = str(v.get("codigo_lr") or "").strip()
        if c_lr:
            mapa_lr_tipo[c_lr] = str(v.get("tipo_ponto_atendimento") or "").strip().upper()
            mapa_lr_proj[c_lr] = str(v.get("projeto") or "").strip().upper()
            mapa_lr_nome[c_lr] = str(v.get("nome_produtor") or "").strip()

    def eh_cadeia_leite(projeto_str: str, codigo_lr: str = "") -> bool:
        proj_upper = (projeto_str or "").strip().upper()
        # 1. Se o projeto contém termos de outras cadeias (Cacau, Grãos, Café)
        for p_out in ['MAIS GRÃOS', 'MAIS GRAOS', 'MIMC', 'M&E', 'CAFE&GESTAO', 'CAFE & GESTAO', 'CARGILL', 'NCP', 'OFI', 'PV CARGILL']:
            if p_out in proj_upper:
                return False
        # 2. Se o projeto é reconhecido de Leite
        for p_lei in ['REGENERA', 'ALVOAR', 'SEMEAR', 'CCPR', 'ATEG_CCPR', 'LPA', 'CFT', 'CAMPILEITE', 'COPRIL', 'EDUCAMPO', 'QUILLAYES', 'NESTLE']:
            if p_lei in proj_upper:
                return True
        # 3. Cruzamento com código LR em sq_raw_vinculos
        if codigo_lr in mapa_lr_tipo:
            t = mapa_lr_tipo[codigo_lr]
            if "CACAU" in t or "CAFE" in t or "GRAOS" in t:
                return False
            if "LEITE" in t:
                return True
        if codigo_lr in mapa_lr_proj:
            p = mapa_lr_proj[codigo_lr]
            if any(p_out in p for p_out in ['MIMC', 'M&E', 'GRAOS', 'CAFE', 'CARGILL', 'NCP', 'OFI']):
                return False
            if any(p_lei in p for p_lei in ['REGENERA', 'ALVOAR', 'SEMEAR', 'CCPR', 'LPA', 'CFT', 'CAMPILEITE', 'COPRIL', 'EDUCAMPO', 'QUILLAYES', 'NESTLE']):
                return True
        return False

    # 4.2 Entradas de 2026 em diante (a partir de *_LISTA_CADASTRO.xlsx, com fallback em
    # sq_raw_visitas para atendimentos que a planilha não cobre)
    # A) LISTA_CADASTRO.xlsx é a fonte principal: já traz nome real, consultor e tipo de
    # cadastro, e filtra por cadeia de produção (só Leite segue adiante).
    atendimentos_planilha_cadastro: Set[str] = set()
    arquivos_cad = list(bd_path.glob("*_LISTA_CADASTRO.xlsx")) + list((bd_path / "BACKUPS").glob("*_LISTA_CADASTRO.xlsx"))
    if arquivos_cad:
        for arq_cad in arquivos_cad:
            if not arq_cad.exists():
                continue
            try:
                df_raw_c = pd.read_excel(arq_cad, header=None)
                h_c = 0
                for r in range(min(5, len(df_raw_c))):
                    vals = [str(x).strip().lower() for x in df_raw_c.iloc[r].dropna().tolist()]
                    if any("atendimento" in v for v in vals):
                        h_c = r
                        break
                df_c = pd.read_excel(arq_cad, header=h_c).dropna(how="all", axis=1).dropna(how="all", axis=0)
                col_id_c = [c for c in df_c.columns if "atendimento" in str(c).lower()][0]
                col_cons_c = [c for c in df_c.columns if "consultor" in str(c).lower()][0]
                col_dt_c = [c for c in df_c.columns if "solicita" in str(c).lower()][0]
                col_cod_c = [c for c in df_c.columns if "código" in str(c).lower() or "codigo" in str(c).lower()][0]
                col_tipo_c = [c for c in df_c.columns if "tipo de cadastro" in str(c).lower()]
                col_cadeia_c = [c for c in df_c.columns if "cadeia" in str(c).lower()]
                col_proj_c = [c for c in df_c.columns if "projeto" in str(c).lower()]
                col_cid_c = [c for c in df_c.columns if "cidade" in str(c).lower()]
                col_prod_c = [c for c in df_c.columns if ("produtor" in str(c).lower() and "novo" not in str(c).lower() and "código" not in str(c).lower() and "consultor" not in str(c).lower())]
                col_novo_prod_c = [c for c in df_c.columns if "novo(a) produtor(a)" in str(c).lower() and "nome" in str(c).lower()]

                for _, row in df_c.iterrows():
                    id_atend = str(row[col_id_c]).strip().replace(".0", "")
                    if not id_atend or id_atend.lower() == "nan":
                        continue
                    # Registrado como "coberto" pela planilha ANTES de qualquer filtro — mesmo um
                    # cadastro de outra cadeia ou sem data válida não deve ser reprocessado pelo
                    # fallback de sq_raw_visitas (bloco B), que não tem como saber por que a
                    # planilha o descartou.
                    atendimentos_planilha_cadastro.add(id_atend)

                    dt_solic = pd.to_datetime(row[col_dt_c], errors="coerce")
                    if pd.isna(dt_solic):
                        continue
                    dt_mov = dt_solic.strftime("%Y-%m-01")
                    if dt_mov < "2026-01-01":
                        continue

                    # Filtro exclusivo de LEITE
                    if col_cadeia_c:
                        cadeia_val = str(row.get(col_cadeia_c[0]) or "").strip().lower()
                        if cadeia_val and not ("leite" in cadeia_val):
                            continue
                    elif col_proj_c:
                        if not eh_cadeia_leite(str(row.get(col_proj_c[0]) or ""), str(row.get(col_cod_c) or "")):
                            continue

                    cons = extrair_consultor_individual(str(row[col_cons_c]))
                    tipo_cad = str(row[col_tipo_c[0]]) if col_tipo_c and pd.notna(row.get(col_tipo_c[0])) else "Inclusão de propriedade"
                    cidade_cad = str(row.get(col_cid_c[0]) or "") if col_cid_c else ""

                    # Nome do produtor direto do Excel (ou titular novo em caso de troca)
                    nome_prod_cad = ""
                    if col_novo_prod_c and pd.notna(row.get(col_novo_prod_c[0])) and str(row.get(col_novo_prod_c[0])).strip():
                        nome_prod_cad = str(row.get(col_novo_prod_c[0])).strip()
                    elif col_prod_c and pd.notna(row.get(col_prod_c[0])):
                        nome_prod_cad = str(row.get(col_prod_c[0])).strip()

                    cod = resolver_codigo_lr_cadastro(nome_prod_cad, dt_mov, id_atend, consultor=cons,
                                                       cidade=cidade_cad, tipo_cadastro=tipo_cad)
                    if not cod:
                        cadastros_sem_resolucao.append({
                            "fonte": arq_cad.name, "numero_atendimento": id_atend, "nome_produtor": nome_prod_cad,
                            "nome_consultor": cons, "data_solicitacao": dt_solic.strftime("%Y-%m-%d"),
                            "tipo_cadastro": tipo_cad, "motivo": "Nenhum codigo_lr resolvido por nome",
                        })
                        continue

                    id_atend_num = int(id_atend) if str(id_atend).isdigit() else id_atend
                    movimentacoes_lista.append({
                        "id_composto": f"{cod}_{dt_mov}_Entrada",
                        "codigo_lr": cod,
                        "nome_consultor": cons,
                        "nome_produtor": nome_prod_cad if (nome_prod_cad and nome_prod_cad.lower() != "nan") else None,
                        "numero_atendimento": id_atend_num,
                        "data_movimentacao": dt_mov,
                        "movimentacao": "Entrada",
                        "motivo_inativacao": None,
                        "outro_motivo": tipo_cad,
                        "data_solicitacao": dt_solic.strftime("%Y-%m-%d"),
                        "origem_dado": arq_cad.name,
                        "data_processamento": datetime.now(FUSO_SP).isoformat(),
                    })
            except Exception as e_cad:
                print(f"   ⚠️ Aviso ao processar {arq_cad.name}: {e_cad}")

    # B) Fallback: atendimentos de cadastro que existem em sq_raw_visitas mas NÃO estão em
    # nenhuma *_LISTA_CADASTRO.xlsx (planilha desatualizada ou exportada antes do cadastro).
    try:
        df_raw_visitas_cad = consultar_tabela_supabase(
            "sq_raw_visitas",
            "id_atendimento, nome_consultor, codigo_lr, nome_produtor, data_visita, tipo_visita, projeto",
            raiz=raiz_projeto
        )
        if not df_raw_visitas_cad.empty:
            # consultar_tabela_supabase só aceita filtros de igualdade; o corte de data é feito aqui.
            df_raw_visitas_cad = df_raw_visitas_cad[
                pd.to_datetime(df_raw_visitas_cad["data_visita"], errors="coerce") >= pd.Timestamp("2026-01-01")
            ]
        atendimentos_visitas_cadastro: Set[str] = set()
        if not df_raw_visitas_cad.empty:
            m_cad = (
                df_raw_visitas_cad["tipo_visita"].astype(str).str.upper().str.contains("CADASTRO") |
                df_raw_visitas_cad["codigo_lr"].astype(str).str.upper().str.contains("CADASTRO")
            )
            df_cads_vis = df_raw_visitas_cad[m_cad].copy()
            for _, r_cv in df_cads_vis.iterrows():
                id_atend = str(r_cv.get("id_atendimento") or "").strip().replace(".0", "")
                if not id_atend or id_atend.lower() == "nan":
                    continue
                atendimentos_visitas_cadastro.add(id_atend)
                if id_atend in atendimentos_planilha_cadastro:
                    continue  # já coberto pela LISTA_CADASTRO (bloco A)

                dt_vis = pd.to_datetime(r_cv.get("data_visita"), errors="coerce")
                if pd.isna(dt_vis):
                    continue
                dt_mov = dt_vis.strftime("%Y-%m-01")

                cod_raw = str(r_cv.get("codigo_lr") or "").strip()
                # A visita de cadastro vem com o nome genérico "PRODUTOR(A) PARA CADASTRO": tratar
                # como vazio para não atrapalhar a resolução por nome.
                nome_p = str(r_cv.get("nome_produtor") or "").strip()
                if _eh_nome_placeholder(nome_p):
                    nome_p = mapa_lr_nome.get(cod_raw) or ""
                    if _eh_nome_placeholder(nome_p):
                        nome_p = ""
                cons = extrair_consultor_individual(r_cv.get("nome_consultor"))
                cod = resolver_codigo_lr_cadastro(nome_p, dt_mov, id_atend, consultor=cons)
                if not cod:
                    cadastros_sem_resolucao.append({
                        "fonte": "sq_raw_visitas (fallback)", "numero_atendimento": id_atend, "nome_produtor": nome_p,
                        "nome_consultor": cons, "data_solicitacao": dt_vis.strftime("%Y-%m-%d"),
                        "tipo_cadastro": MOTIVO_CADASTRO_GENERICO, "motivo": "Nenhum codigo_lr resolvido por nome",
                    })
                    continue

                id_atend_num = int(id_atend) if str(id_atend).isdigit() else id_atend
                movimentacoes_lista.append({
                    "id_composto": f"{cod}_{dt_mov}_Entrada",
                    "codigo_lr": cod,
                    "nome_consultor": cons,
                    "nome_produtor": nome_p or None,
                    "numero_atendimento": id_atend_num,
                    "data_movimentacao": dt_mov,
                    "movimentacao": "Entrada",
                    "motivo_inativacao": None,
                    "outro_motivo": MOTIVO_CADASTRO_GENERICO,
                    "data_solicitacao": dt_vis.strftime("%Y-%m-%d"),
                    "origem_dado": "sq_raw_visitas",
                    "data_processamento": datetime.now(FUSO_SP).isoformat(),
                })

        # Conferência de completude: cadastro presente em sq_raw_visitas mas ausente de TODAS as
        # LISTA_CADASTRO.xlsx exportadas — pode indicar planilha desatualizada.
        faltando_na_planilha = sorted(atendimentos_visitas_cadastro - atendimentos_planilha_cadastro)
        if faltando_na_planilha and arquivos_cad:
            nomes_arqs = ", ".join(a.name for a in arquivos_cad)
            print(f"   ⚠️ {len(faltando_na_planilha)} cadastro(s) presentes em sq_raw_visitas mas ausentes de "
                  f"{nomes_arqs}: {faltando_na_planilha}")
    except Exception as e_vis_cad:
        print(f"   ⚠️ Aviso ao extrair atendimentos de cadastro em sq_raw_visitas: {e_vis_cad}")

    # 4.3 Saídas (Histórico Pré-2026 preservado + 2026 em diante por Data da Solicitação - Filtrado LEITE)
    codigos_oficiais_set = set(df_vinc_db["codigo_lr"].dropna().unique()) if not df_vinc_db.empty else set()
    
    if not df_inats_existentes.empty:
        for _, row in df_inats_existentes.iterrows():
            id_atend = str(row.get("id_atendimento") or "").strip().replace(".0", "")
            cod_raw = str(row.get("codigo_lr") or "").strip()
            cod = cod_raw if (cod_raw and cod_raw.lower() != "nan") else f"INAT_{id_atend}"
            cons = extrair_consultor_individual(row.get("nome_consultor"), row.get("grupo_ponto_atendimento"))
            proj_inat = str(row.get("projeto") or "").strip()
            nome_prod_inat = str(row.get("nome_produtor") or "").strip()
            if not nome_prod_inat and cod in mapa_lr_nome:
                nome_prod_inat = mapa_lr_nome[cod]
            
            dt_solic = row.get("data_solicitacao")
            dt_inat = row.get("data_inativacao")
            
            # Priorizar a Data da Inativação (quando o produtor efetivamente saiu) sobre a Data da Solicitação
            dt_inat_p = pd.to_datetime(dt_inat, errors="coerce")
            dt_solic_p = pd.to_datetime(dt_solic, errors="coerce")
            dt_efetiva = dt_inat_p if pd.notna(dt_inat_p) else dt_solic_p

            if pd.notna(dt_efetiva):
                if dt_efetiva >= pd.Timestamp("2026-01-01"):
                    if not eh_cadeia_leite(proj_inat, cod_raw):
                        continue
                dt_mov = dt_efetiva.strftime("%Y-%m-01")
                id_comp = f"{cod}_{dt_mov}_Saída"
            else:
                dt_mov = config.mes_referencia.strftime("%Y-%m-01")
                id_comp = f"{cod}_{dt_mov}_Saída"


            motivo = row.get("motivo_inativacao")
            outro = row.get("outro_motivo")
            id_atend_num = int(id_atend) if str(id_atend).isdigit() else (id_atend if id_atend else None)
            dt_solic_str = (dt_solic_p if pd.notna(dt_solic_p) else dt_efetiva).strftime("%Y-%m-%d") if pd.notna(dt_solic_p) or pd.notna(dt_efetiva) else None

            movimentacoes_lista.append({
                "id_composto": id_comp,
                "codigo_lr": cod,
                "nome_consultor": cons,
                "nome_produtor": nome_prod_inat if (nome_prod_inat and nome_prod_inat.lower() != "nan") else None,
                "numero_atendimento": id_atend_num,
                "data_movimentacao": dt_mov,
                "movimentacao": "Saída",
                "motivo_inativacao": motivo,
                "outro_motivo": outro,
                "data_solicitacao": dt_solic_str,
                "origem_dado": "sq_raw_inativacoes_produtor",
                "data_processamento": datetime.now(FUSO_SP).isoformat(),
            })
            
    # Consolidação em duas etapas, sempre complementando campos (groupby.first() pega o primeiro
    # valor não nulo de CADA coluna, na ordem de prioridade definida pelo sort):
    #   1) Por (numero_atendimento, movimentação): o mesmo atendimento de cadastro pode chegar
    #      tanto pela LISTA_CADASTRO quanto pelo fallback de sq_raw_visitas (bloco B) — em tese
    #      não deveriam coexistir (B pula quem já está coberto pela planilha), mas a linha com
    #      codigo_lr "LR<dígitos>" tem prioridade sobre qualquer outra por segurança.
    #   2) Por (codigo_lr, mês, movimentação): junta o resultado com a entrada "crua" do vínculo
    #      (sem atendimento), priorizando a linha que tem atendimento.
    # O motivo genérico da visita vira nulo durante a consolidação para não vencer o tipo de
    # cadastro da planilha ("Inclusão de propriedade" etc.) e é restaurado só onde faltar.
    colunas_mov = list(movimentacoes_lista[0].keys()) if movimentacoes_lista else []
    df_mov = pd.DataFrame(movimentacoes_lista)
    df_mov["_at"] = df_mov["numero_atendimento"].map(_id_atendimento_str)
    df_mov["_tem_at"] = df_mov["_at"].notna()
    df_mov["_eh_lr"] = df_mov["codigo_lr"].astype(str).str.upper().str.fullmatch(r"LR\d+")
    df_mov.loc[df_mov["nome_produtor"].map(_eh_nome_placeholder), "nome_produtor"] = None
    df_mov.loc[df_mov["outro_motivo"] == MOTIVO_CADASTRO_GENERICO, "outro_motivo"] = None
    df_mov = df_mov.sort_values(by=["_tem_at", "_eh_lr"], ascending=False, kind="stable")

    com_at = df_mov[df_mov["_tem_at"]].groupby(["_at", "movimentacao"], sort=False, as_index=False).first()
    df_mov = pd.concat([com_at, df_mov[~df_mov["_tem_at"]]], ignore_index=True)
    df_mov["_tem_at"] = df_mov["_at"].notna()
    df_mov = df_mov.sort_values(by=["_tem_at"], ascending=False, kind="stable")
    df_mov_final = df_mov.groupby(["codigo_lr", "data_movimentacao", "movimentacao"], sort=False, as_index=False).first()

    df_mov_final["_tem_at"] = df_mov_final["_at"].notna()
    cadastro_sem_motivo = df_mov_final["_tem_at"] & (df_mov_final["movimentacao"] == "Entrada") & df_mov_final["outro_motivo"].isna()
    df_mov_final.loc[cadastro_sem_motivo, "outro_motivo"] = MOTIVO_CADASTRO_GENERICO
    cad_sem_nome = df_mov_final["nome_produtor"].isna() & df_mov_final["_tem_at"]
    df_mov_final.loc[cad_sem_nome, "nome_produtor"] = "Novo Produtor (" + df_mov_final.loc[cad_sem_nome, "_at"] + ")"

    # id_composto = codigo_lr_numero_atendimento_movimentacao (ex.: LR02480_420000019_Saída).
    # Entradas de vínculo sem atendimento (histórico) usam a data no lugar do atendimento.
    # numero_atendimento vira sempre texto aqui: número do atendimento quando existe, ou a data
    # de movimentação como fallback — substitui a antiga coluna separada chave_atendimento.
    df_mov_final["numero_atendimento"] = [
        at if isinstance(at, str) else dt
        for at, dt in zip(df_mov_final["_at"], df_mov_final["data_movimentacao"])
    ]
    df_mov_final["id_composto"] = [
        f"{cod}_{chave}_{mov}"
        for cod, chave, mov in zip(df_mov_final["codigo_lr"], df_mov_final["numero_atendimento"],
                                    df_mov_final["movimentacao"])
    ]
    df_mov_final = df_mov_final.drop_duplicates(subset=["id_composto"], keep="first")[colunas_mov].copy()
    print(f"   -> Total de movimentações consolidadas: {len(df_mov_final)} (Entradas: {len(df_mov_final[df_mov_final['movimentacao'] == 'Entrada'])}, Saídas: {len(df_mov_final[df_mov_final['movimentacao'] == 'Saída'])})")

    # Rede de segurança: (codigo_lr, mês, movimentação) já é único por construção (dedup acima),
    # mas o mesmo produtor pode aparecer sob dois codigo_lr diferentes (ex.: cadastro não resolvido
    # ainda por resolver_codigo_cadastro, ou dois vínculos distintos para o mesmo nome). Isso não
    # bloqueia a publicação — só torna visível no log, já que hoje nada mais verifica isso.
    dup_nome_mes = (
        df_mov_final.assign(_nome_norm=df_mov_final["nome_produtor"].map(_normalizar_nome))
        .query("_nome_norm != ''")
        .groupby(["_nome_norm", "data_movimentacao", "movimentacao"])
        .filter(lambda g: len(g) > 1)
    )
    if not dup_nome_mes.empty:
        qtd_grupos = dup_nome_mes.groupby(["_nome_norm", "data_movimentacao", "movimentacao"]).ngroups
        print(f"   ⚠️ {qtd_grupos} produtor(es)/mês com mais de um codigo_lr para a mesma movimentação "
              f"(possível cadastro ainda não resolvido ao vínculo):")
        for (_nome_norm, mes_dup, mov_dup), grupo in dup_nome_mes.groupby(["_nome_norm", "data_movimentacao", "movimentacao"]):
            print(f"     - {grupo['nome_produtor'].iloc[0]} | {mes_dup} | {mov_dup} | codigos: {sorted(grupo['codigo_lr'].unique())}")

    # Cadastros sem codigo_lr resolvido (nenhum dos 3 critérios de resolver_codigo_lr_cadastro
    # bateu): não entram em sq_raw_movimentacao — ficam só nesta planilha de auditoria e são
    # resolvidos sozinhos numa execução futura, assim que o vínculo do produtor existir.
    if cadastros_sem_resolucao:
        pasta_logs = raiz_projeto / "data" / "output" / "logs"
        pasta_logs.mkdir(parents=True, exist_ok=True)
        caminho_auditoria = pasta_logs / f"{datetime.now(FUSO_SP).strftime('%Y_%m_%d_%H%M%S')}_cadastros_sem_codigo_lr.xlsx"
        pd.DataFrame(cadastros_sem_resolucao).to_excel(caminho_auditoria, index=False)
        print(f"   ⚠️ {len(cadastros_sem_resolucao)} cadastro(s) sem codigo_lr resolvido — não entraram em "
              f"sq_raw_movimentacao. Detalhes em {caminho_auditoria}")

    # 4.4 Sincronizar sq_raw_movimentacao (bruto): upsert de tudo + remoção de quem não existe
    # mais em df_mov_final (id_composto legado, cadastro duplicado, etc.). Esta é a ÚNICA escrita
    # de movimentação do pipeline — grava sempre no bruto; camada_consumo.py lê daqui e publica
    # a versão enriquecida (consultor/tipo/motivo/projeto/agroindustria/regiao) em
    # tab_fato_movimentacao. Antes, este script escrevia direto em tab_fato_movimentacao só com os
    # campos brutos, apagando o enriquecimento sempre que rodava sem a camada de consumo na
    # sequência (ex.: execução interrompida no meio) e deixando linhas duplicadas por
    # (codigo_lr, mês, movimentação) que nunca eram limpas fora da janela 2026+.
    print("\n💾 5. Sincronizando movimentações consolidadas em sq_raw_movimentacao no Supabase...")
    registros_mov = df_mov_final.replace({np.nan: None}).to_dict(orient="records")
    LOTE = 1000
    sucesso_mov = 0
    for i in range(0, len(registros_mov), LOTE):
        lote = registros_mov[i : i + LOTE]
        try:
            supabase.table("sq_raw_movimentacao").upsert(lote, on_conflict="id_composto").execute()
            sucesso_mov += len(lote)
        except Exception as e:
            print(f"   ❌ Erro ao enviar lote {i // LOTE + 1}: {e}")
        time.sleep(0.01)
    print(f"   ✅ {sucesso_mov} registros de movimentação atualizados em sq_raw_movimentacao.")

    try:
        ids_db, inicio_pag = set(), 0
        while True:
            pagina = supabase.table("sq_raw_movimentacao").select("id_composto").range(inicio_pag, inicio_pag + 999).execute().data or []
            ids_db.update(r["id_composto"] for r in pagina)
            if len(pagina) < 1000:
                break
            inicio_pag += 1000
        ids_atuais = set(df_mov_final["id_composto"])
        obsoletos = sorted(ids_db - ids_atuais)
        if obsoletos:
            print(f"   🧹 Removendo {len(obsoletos)} registros obsoletos de sq_raw_movimentacao (formato legado/duplicado)...")
            LOTE_DEL = 100
            for d_idx in range(0, len(obsoletos), LOTE_DEL):
                lote_ids = obsoletos[d_idx : d_idx + LOTE_DEL]
                supabase.table("sq_raw_movimentacao").delete().in_("id_composto", lote_ids).execute()
            print(f"   ✅ {len(obsoletos)} registros obsoletos removidos.")
    except Exception as e_clean:
        print(f"   ⚠️ Aviso ao remover registros obsoletos de sq_raw_movimentacao: {e_clean}")

    # 6. Reconciliar Tabelas de Fazendas:
    #    6.1 sq_raw_fazendas (e sq_raw_fazendas_grupo se existir): Espelho COMPLETO da LISTA_GERAL (todas as cadeias, ativos e inativos)
    #    6.2 sq_raw_fazendas_grupo_ativas (se existir): Espelho das ATIVAS (todas as cadeias, apenas status=Ativo)
    #    6.3 sq_fato_fazendas_ativas: Fato Analítica de LEITE com histórico mensal desde 2026-01-01 reconstruído

    tabela_raw = cfg_raw.get("supabase", {}).get("tabelas", {}).get("fazendas_raw", "sq_raw_fazendas")
    tabela_grupo_raw = cfg_raw.get("supabase", {}).get("tabelas", {}).get("fazendas_grupo_raw", "sq_raw_fazendas_grupo")
    tabela_grupo_ativas_raw = cfg_raw.get("supabase", {}).get("tabelas", {}).get("fazendas_grupo_ativas_raw", "sq_raw_fazendas_grupo_ativas")
    tabela_ativos = cfg_raw.get("supabase", {}).get("tabelas", {}).get("fazendas_ativas_dim", cfg_raw.get("supabase", {}).get("tabelas", {}).get("fazendas_ativas_fato", "tab_dim_fazendas_ativas"))

    print(f"\n🌱 6. Processando espelhos e base ativa mensal de fazendas...")

    # Coordenadas (latitude/longitude/origem_coordenada/coordenada_corrigida) são colunas novas em
    # sq_raw_fazendas_grupo e tab_dim_fazendas_ativas (data/input/2026_09_30_ddl_coordenadas_dim.sql). Sem elas o upsert
    # falharia DEPOIS de a etapa 6.3 apagar o mês da dim; por isso a presença é checada antes e as colunas saem do envio.
    CAMPOS_COORD = ("latitude", "longitude", "origem_coordenada", "coordenada_corrigida")

    def _tem_colunas_coord(tabela: str) -> bool:
        try:
            supabase.table(tabela).select(",".join(CAMPOS_COORD)).limit(1).execute()
            return True
        except Exception:
            print(f"   ⚠️ {tabela} ainda não tem as colunas de coordenada (rode data/input/2026_09_30_ddl_coordenadas_dim.sql); "
                  f"as coordenadas não serão gravadas nela.")
            return False

    def _sem_nan(registros: list, descartar: tuple = ()) -> list:
        """NaN não é JSON válido; colunas float com vazio viram NaN ao passar pelo DataFrame."""
        return [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items() if k not in descartar}
                for r in registros]

    # Identificar todas as inativações com data e código
    inativacoes_por_codigo: Dict[str, str] = {}
    if not df_inats_existentes.empty:
        for _, row in df_inats_existentes.iterrows():
            c = str(row.get("codigo_lr") or "").strip()
            dt_solic = row.get("data_solicitacao")
            dt_inat = row.get("data_inativacao")

            dt_inat_p = pd.to_datetime(dt_inat, errors="coerce")
            dt_solic_p = pd.to_datetime(dt_solic, errors="coerce")
            dt_efetiva = dt_inat_p if pd.notna(dt_inat_p) else dt_solic_p
            dt_str = dt_efetiva.strftime("%Y-%m-01") if pd.notna(dt_efetiva) else None

            if c and dt_str:
                # Usar a data de inativação mais recente (caso o produtor tenha inativação antiga e nova)
                if c not in inativacoes_por_codigo or dt_str > inativacoes_por_codigo[c]:
                    inativacoes_por_codigo[c] = dt_str

    print(f"   -> Mapeados {len(inativacoes_por_codigo)} produtores com inativação confirmada.")

    # Identificar cadastros novos de 2026
    cadastros_por_codigo: Dict[str, str] = {}
    if not df_mov_final.empty:
        df_ents_2026 = df_mov_final[
            (df_mov_final["movimentacao"] == "Entrada") & 
            (pd.to_datetime(df_mov_final["data_movimentacao"], errors="coerce") >= pd.Timestamp("2026-01-01"))
        ]
        for _, row in df_ents_2026.iterrows():
            c = str(row.get("codigo_lr") or "").strip()
            dt_ent = pd.to_datetime(row.get("data_movimentacao"), errors="coerce")
            if c and pd.notna(dt_ent):
                dt_str = dt_ent.strftime("%Y-%m-01")
                if c not in cadastros_por_codigo or dt_str < cadastros_por_codigo[c]:
                    cadastros_por_codigo[c] = dt_str

    print(f"   -> Mapeados {len(cadastros_por_codigo)} produtores com novo cadastro em 2026.")

    # Linha do tempo de Entradas/Saídas por produtor: permite saber o estado do vínculo em CADA
    # mês de referência, em vez de aplicar a última movimentação conhecida a todo o histórico.
    # Sem isso, uma saída em agosto removia o produtor também de janeiro a julho.
    movs_por_codigo: Dict[str, list] = {}
    if not df_mov_final.empty:
        for _, row in df_mov_final.iterrows():
            c = str(row.get("codigo_lr") or "").strip()
            dt_mv = pd.to_datetime(row.get("data_movimentacao"), errors="coerce")
            tipo_mv = str(row.get("movimentacao") or "").strip().upper()
            if not c or pd.isna(dt_mv) or not tipo_mv:
                continue
            movs_por_codigo.setdefault(c, []).append((dt_mv.strftime("%Y-%m-01"), tipo_mv))
    for c_mv in movs_por_codigo:
        # Ordem estável: por mês e, dentro do mesmo mês, Entrada antes de Saída
        movs_por_codigo[c_mv].sort(key=lambda t: (t[0], 0 if t[1].startswith("ENTRADA") else 1))

    def vinculo_encerrado_em(codigo: str, mes: str) -> bool:
        """True se a última movimentação até `mes` (inclusive) for uma Saída.

        Respeita reentradas: produtor que saiu em 2024 e voltou em 2025 continua ativo.
        Convenção de negócio confirmada com a carteira: entrada no mês M já conta em M e
        saída no mês M já não conta em M.
        """
        historico = movs_por_codigo.get(codigo)
        if not historico:
            return False
        ultimo = None
        for mes_mv, tipo_mv in historico:
            if mes_mv <= mes:
                ultimo = tipo_mv
            else:
                break
        return ultimo is not None and ultimo.startswith("SAÍDA")

    def vinculo_ainda_nao_iniciado_em(codigo: str, mes: str) -> bool:
        """True se a primeira Entrada registrada do produtor for posterior a `mes`."""
        historico = movs_por_codigo.get(codigo)
        if not historico:
            return False
        entradas = [m for m, t in historico if t.startswith("ENTRADA")]
        return bool(entradas) and min(entradas) > mes

    UF_MAP = {
        'MINAS GERAIS': 'MG', 'BAHIA': 'BA', 'GOIAS': 'GO', 'GOIÁS': 'GO',
        'SAO PAULO': 'SP', 'SÃO PAULO': 'SP', 'ESPIRITO SANTO': 'ES', 'ESPÍRITO SANTO': 'ES',
        'MATO GROSSO': 'MT', 'MATO GROSSO DO SUL': 'MS', 'PARANA': 'PR', 'PARANÁ': 'PR',
        'RIO DE JANEIRO': 'RJ', 'RONDÔNIA': 'RO', 'RONDONIA': 'RO', 'TOCANTINS': 'TO'
    }

    def limpar_uf(val):
        if not val or pd.isna(val):
            return None
        s = str(val).strip().upper()
        if len(s) == 2:
            return s
        return UF_MAP.get(s, s[:2] if len(s) >= 2 else None)

    # Leitura e parsing da LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx
    arquivo_grupo = bd_path / "LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx"
    if not arquivo_grupo.exists():
        print(f"   ❌ Arquivo não encontrado: {arquivo_grupo}")
        return

    print(f"   📖 Lendo base de status de grupos: {arquivo_grupo.name}...")
    df_g_raw = pd.read_excel(arquivo_grupo)

    status_cols = [c for c in df_g_raw.columns if 'status' in str(c).lower() and (df_g_raw[c].dtype == bool or str(c).strip() == 'Status ')]
    status_col_name = status_cols[0] if status_cols else 'Status '

    tipo_cols = [c for c in df_g_raw.columns if 'tipo ponto' in str(c).lower()]
    tipo_col_name = tipo_cols[0] if tipo_cols else 'Tipo ponto atendimento'

    cod_cols = [c for c in df_g_raw.columns if 'código' in str(c).lower() or 'codigo' in str(c).lower()]
    cod_col_name = cod_cols[0] if cod_cols else 'Código'

    nome_cols = [c for c in df_g_raw.columns if str(c).strip() == 'Nome']
    nome_col_name = nome_cols[0] if nome_cols else 'Nome'

    prop_cols = [c for c in df_g_raw.columns if 'propriedade' in str(c).lower()]
    prop_col_name = prop_cols[0] if prop_cols else 'Nome da propriedade'

    cid_cols = [c for c in df_g_raw.columns if 'cidade' in str(c).lower()]
    cid_col_name = cid_cols[0] if cid_cols else 'Cidade'

    uf_cols = [c for c in df_g_raw.columns if 'estado' in str(c).lower()]
    uf_col_name = uf_cols[0] if uf_cols else 'Estado'

    unid_cols = [c for c in df_g_raw.columns if 'unidade atendimento' in str(c).lower()]
    unid_col_name = unid_cols[0] if unid_cols else 'Unidade atendimento'

    grupo_cols = [c for c in df_g_raw.columns if 'grupo ponto' in str(c).lower()]
    grupo_col_name = grupo_cols[0] if grupo_cols else 'Grupo ponto atendimento'

    agro_cols = [c for c in df_g_raw.columns if 'agroindústria' in str(c).lower() or 'agroindustria' in str(c).lower()]
    agro_col_name = agro_cols[0] if agro_cols else 'Código Agroindústria - Campo personalizado'

    faz_cols = [c for c in df_g_raw.columns if 'fazenda - campo' in str(c).lower()]
    faz_col_name = faz_cols[0] if faz_cols else 'Código Fazenda - Campo personalizado'

    cfg_ref = carregar_config_referencia(raiz_projeto)
    mes_ref_dt = cfg_ref.mes_referencia
    mes_ref_str = mes_ref_dt.strftime("%Y-%m-01")
    proximo_mes_str = (mes_ref_dt + pd.DateOffset(months=1)).strftime("%Y-%m-01")

    # Montar DataFrame padronizado completo
    lista_todos = []
    agora_iso = datetime.now(FUSO_SP).isoformat()

    def extrair_projeto_especifico(texto):
        if not texto or str(texto).strip().lower() in ["nan", "none", "não atribuído", "nao atribuido"]:
            return None
        matches = re.findall(r'\((.*?)\)', str(texto))
        if not matches:
            return None
        projs = []
        for m in matches:
            p = m.strip().upper()
            # Se contiver 'CFT', desconsidera (é modalidade operacional, não projeto)
            if "CFT" in p:
                continue
            if p and p not in projs:
                projs.append(p)
        if not projs:
            return None
        projs.sort()
        return " / ".join(projs)

    def extrair_consultores_do_grupo(texto):
        if not texto or str(texto).strip().lower() in ["nan", "none", "não atribuído", "nao atribuido"]:
            return None
        partes = [p.strip() for p in str(texto).split("/") if p.strip()]
        partes_sem_cft = [p for p in partes if "CFT" not in p.upper()]
        if not partes_sem_cft:
            return None
        # Remove qualquer conteúdo entre parênteses e espaços extras para ficar apenas o nome limpo sem parênteses
        partes_limpas = [re.sub(r'\s*\(.*?\)', '', p).strip() for p in partes_sem_cft]
        partes_limpas = [p for p in partes_limpas if p]
        if not partes_limpas:
            return None
        return " / ".join(partes_limpas)

    PROJETOS_OFICIAIS_VALIDOS = [
        'ALVOAR ASSIST', 'ALVOAR ECO', 'ALVOAR ASSIST / ALVOAR ECO', 'ALVOAR ECO / MAIS GRAOS',
        'ATEG_CCPR', 'CCPR', 'LPA', 'REGENERA', 'SEMEAR', 'COPRIL', 'CAMPILEITE', 'NESTLE', 'EDUCAMPO'
    ]

    GRUPOS_CFT_EXCLUSIVOS = [
        "DAYANNE UCHOA VEIGA / DEBORA LIMA DE OLIVEIRA / MARIO BARBOSA ROSA FILHO / MATEUS CARNIELLI / TALITA FONTES / THAYNAN FERREIRA DE ARAUJO",
        "HUGO LOPES / MATEUS CARNIELLI / ROMARCIO PAULO DE OLIVEIRA / THAYNAN FERREIRA DE ARAUJO",
        "BRUNO ANTONIO FERRONI RODRIGUES / HUGO LOPES / MATEUS CARNIELLI / THAYNAN FERREIRA DE ARAUJO",
        "MATHEUS GOMIDES GONCALVES",
        "TALITA FONTES"
    ]

    def eh_puramente_cft(grupo_str, proj_str, consultor_limpo):
        g_up = str(grupo_str or "").strip().upper()
        p_up = str(proj_str or "").strip().upper()
        
        # Checagem de grupo exclusivo de CFT
        if any(gcft in g_up for gcft in GRUPOS_CFT_EXCLUSIVOS):
            if not any(p_val in p_up for p_val in PROJETOS_OFICIAIS_VALIDOS):
                return True

        # Se o projeto da fazenda contiver "CFT" (ex: CFT DANONE 2026, CFT LPA 2026, CFT PIRACANJUBA, QUILLAYES - CFT)
        if "CFT" in p_up:
            matches_g = re.findall(r'\((.*?)\)', g_up)
            projs_g = [m.strip().upper() for m in matches_g if "CFT" not in m.upper()]
            if not any(p_g in PROJETOS_OFICIAIS_VALIDOS for p_g in projs_g):
                return True
                
        # Se o projeto for nulo, vazio, "CFT" ou "NONE"
        if not p_up or p_up in ["NONE", "NAN", "CFT"]:
            matches_g = re.findall(r'\((.*?)\)', g_up)
            projs_g = [m.strip().upper() for m in matches_g if "CFT" not in m.upper()]
            if not any(p_g in PROJETOS_OFICIAIS_VALIDOS for p_g in projs_g):
                return True
                
        return False

    # Mapeamento oficial de Projeto -> Agroindústria e mapa de regiões
    MAP_PROJETO_AGRO = {
        "REGENERA": "Nestlé",
        "ALVOAR ECO": "Alvoar",
        "ALVOAR ASSIST": "Alvoar",
        "ALVOAR ECO / MAIS GRAOS": "Alvoar",
        "COPRIL": "Copril",
        "SEMEAR": "Danone",
        "CAMPILEITE": "CAMPILEITE",
        "ATEG_CCPR": "CCPR",
        "LPA": "Laticínios Porto Alegre"
    }

    regiao_map = {}
    try:
        res_reg = supabase.table("tab_dim_fazendas_ativas").select("codigo_produtor, regiao").not_.is_("regiao", "null").limit(5000).execute()
        for r_reg in (res_reg.data or []):
            c_lr = str(r_reg.get("codigo_produtor") or "").strip().upper()
            reg_val = r_reg.get("regiao")
            if c_lr and reg_val and str(reg_val).strip().lower() not in ["none", "nan", "teste"]:
                regiao_map[c_lr] = str(reg_val).strip()
    except Exception:
        pass

    # Carregar o de-para de tab_dim_regiao_alias (nome bruto -> região canônica de tab_dim_regiao) do Supabase
    dim_regiao_cache = {}
    try:
        # O banco grava texto maiúsculo e sem acento: status e chaves do cache comparados sem caixa/acento.
        res_dim = supabase.table("tab_dim_regiao_alias").select("nome_bruto, nome_regiao, agroindustria").ilike("status", "ativo").execute()
        for r_dim in (res_dim.data or []):
            raw_n = rn.chave(r_dim.get("nome_bruto"))
            fmt_n = str(r_dim.get("nome_regiao") or "").strip()
            agro_n = rn.chave(r_dim.get("agroindustria"))
            if raw_n and fmt_n:
                if agro_n:
                    dim_regiao_cache[f"{agro_n}|{raw_n}"] = fmt_n
                if raw_n not in dim_regiao_cache:
                    dim_regiao_cache[raw_n] = fmt_n
    except Exception:
        pass

    UF_TO_NOME = {
        "BA": "Bahia",
        "CE": "Ceará",
        "AL": "Alagoas",
        "SE": "Sergipe",
        "PE": "Pernambuco",
        "MT": "Campinápolis",
        "GO": "Goiânia",
        "SP": "Araçatuba",
        "MG": "Minas Gerais"
    }

    def formatar_regiao(agro, reg_raw, estado_val):
        target = reg_raw
        if not target or str(target).strip().lower() in ["none", "nan", "teste", "labor rural", "unidade generica"]:
            target = estado_val
        if not target or str(target).strip().lower() in ["none", "nan", "teste", "labor rural", "unidade generica"]:
            return "NÃO INFORMADA"

        t_str = str(target).strip()
        t_upper = t_str.upper()

        # 1. Consulta ao cache dinâmico da tab_dim_regiao
        t_chave, agro_chave = rn.chave(t_str), rn.chave(agro)
        if agro_chave and f"{agro_chave}|{t_chave}" in dim_regiao_cache:
            return dim_regiao_cache[f"{agro_chave}|{t_chave}"]
        if t_chave in dim_regiao_cache:
            return dim_regiao_cache[t_chave]

        # 2. Regras específicas por agroindústria
        if agro == "Nestlé":
            if t_str in ["Patos de Minas - 9188", "Ibiá - 1215", "9188", "1215", "MG"]:
                return "Patos de Minas e Ibiá"
            if "9655" in t_str or "Goiânia" in t_str or "Goiania" in t_str or t_upper == "GO":
                return "Goiânia"
            if "1217" in t_str or "Ituiutaba" in t_str or "TRIANGULO" in t_upper:
                return "Ituiutaba"
            if "9264" in t_str or "Montes Claros" in t_str:
                return "Montes Claros"
            if "0460" in t_str or "Araçatuba" in t_str or t_upper == "SP":
                return "Araçatuba"
        elif agro == "CAMPILEITE":
            if t_upper in ["MT", "CAMPINAPOLIS", "CAMPINÁPOLIS"]:
                return "Campinápolis"
        elif agro == "Copril":
            if t_upper in ["MG", "ITAMBACURI"]:
                return "Itambacuri"
        elif agro == "Danone":
            if t_upper in ["MG", "MINAS GERAIS", "SUL DE MINAS"]:
                return "Sul de Minas"
        elif agro in ["LPA", "Laticínios Porto Alegre"]:
            if t_upper in ["MG", "PONTE NOVA"]:
                return "Ponte Nova"
        elif agro == "CCPR":
            if t_upper in ["GO", "DF", "GOIANIA", "GOIÂNIA"]:
                return "Goiânia"

        # 3. Fallback de UFs e siglas
        if t_upper in UF_TO_NOME:
            return UF_TO_NOME[t_upper]

        return t_str

    print("\n📍 Lendo a coordenada do produtor nas planilhas de visita do SmartQuestion...")
    try:
        try:
            from functions.coordenadas_visitas import carregar_coordenadas_smartquestion
        except ImportError:
            from coordenadas_visitas import carregar_coordenadas_smartquestion
        coords_sq = carregar_coordenadas_smartquestion(bd_path)
    except Exception as e_coord:
        coords_sq = {}
        print(f"   ⚠️ Coordenadas do SmartQuestion não carregadas ({e_coord}); a reconciliação segue sem elas.")

    def _coord_sq(codigo: str) -> dict:
        c = coords_sq.get(str(codigo or "").strip().upper().replace(" ", "")) or {}
        return {
            "latitude": c.get("latitude"), "longitude": c.get("longitude"),
            "origem_coordenada": c.get("origem_coordenada"), "coordenada_corrigida": c.get("coordenada_corrigida"),
        }

    for _, r in df_g_raw.iterrows():
        c = str(r.get(cod_col_name) or "").strip()
        if not c or c.lower() == "nan":
            continue

        nome_p_raw = str(r.get(nome_col_name) or "PRODUTOR").strip()
        tem_inativo_flag = ("_INATIVO" in c.upper()) or ("(INATIVO)" in nome_p_raw.upper()) or ("_INATIVO" in nome_p_raw.upper())

        st_val = r.get(status_col_name)
        is_ativo = (st_val == True) or (str(st_val).strip().lower() in ["ativo", "true", "sim", "1"])
        if tem_inativo_flag:
            is_ativo = False

        status_str = "Ativo" if is_ativo else "Inativo"

        tipo_str = str(r.get(tipo_col_name) or "LEITE").strip().upper()
        if not tipo_str or tipo_str.lower() == "nan":
            tipo_str = "LEITE"

        g_raw = r.get(grupo_col_name) if grupo_col_name in r else None
        cons_extraido = None
        proj_extraido = None
        if pd.notna(g_raw) and str(g_raw).strip() and str(g_raw).strip().lower() != "nan":
            g_str = str(g_raw).strip()
            m_proj = re.search(r'\((.*?)\)', g_str)
            proj_extraido = m_proj.group(1).strip().upper() if m_proj else None
            cons_extraido = re.sub(r'\(.*?\)', '', g_str).strip()

        nome_c = cons_extraido or "NÃO ATRIBUÍDO"
        unid = str(r.get(unid_col_name) or "LABOR RURAL").strip()[:100]
        cid = str(r.get(cid_col_name) or "").strip()[:100] or None
        uf = limpar_uf(r.get(uf_col_name))
        
        cod_agro = str(r.get(agro_col_name) or "").strip()[:50] or None
        if cod_agro and cod_agro.lower() == "nan":
            cod_agro = None
        cod_faz = str(r.get(faz_col_name) or "").strip()[:50] or None
        if cod_faz and cod_faz.lower() == "nan":
            cod_faz = None

        grupo_final = str(g_raw).strip()[:250] if (pd.notna(g_raw) and str(g_raw).strip() and str(g_raw).strip().lower() != "nan") else str(nome_c).strip()[:250]
        proj_final = extrair_projeto_especifico(grupo_final)
        consultor_limpo = extrair_consultores_do_grupo(grupo_final)
        agro_calc = MAP_PROJETO_AGRO.get(proj_final)
        reg_calc = formatar_regiao(agro_calc, regiao_map.get(c.upper()), uf)

        id_raw = f"{c[:50]}_{mes_ref_str.replace('-', '_')}"
        lista_todos.append({
            "id": id_raw,
            "codigo_produtor": c[:50],
            "nome_produtor": nome_p_raw[:250],
            "nome_propriedade": str(r.get(prop_col_name) or "FAZENDA").strip()[:250],
            "estado": uf or "NÃO INFORMADO",
            "cidade": cid or "NÃO INFORMADA",
            "tipo_ponto_atendimento": tipo_str,
            "unidade_atendimento": unid,
            "grupo_ponto_atendimento": grupo_final,
            "consultor": consultor_limpo,
            "projeto": proj_final,
            "agroindustria": agro_calc,
            "regiao": reg_calc,
            **_coord_sq(c),
            "codigo_agroindustria": cod_agro,
            "codigo_fazenda": cod_faz,
            "status": status_str,
            "mes_referencia": mes_ref_str,
            "data_processamento": agora_iso
        })

    df_todos = pd.DataFrame(lista_todos).drop_duplicates(subset=["id"])
    df_ativas_todas = df_todos[df_todos["status"] == "Ativo"].copy()
    df_leite_todos = df_todos[df_todos["tipo_ponto_atendimento"].str.contains("LEITE", na=False)].copy()

    # Mapeamento de grupos completos da LISTA_GERAL (para garantir co-consultores em tab_dim_fazendas_ativas)
    mapa_grupos_lista_geral = {}
    for item_g in lista_todos:
        c_prod = str(item_g.get("codigo_produtor") or "").strip().upper()
        grp_comp = item_g.get("grupo_ponto_atendimento")
        cons_lg = item_g.get("consultor")
        if c_prod and grp_comp:
            mapa_grupos_lista_geral[c_prod] = (grp_comp, cons_lg)

    print(f"   -> {len(df_todos)} fazendas consolidadas no espelho completo ({len(mapa_grupos_lista_geral)} mapeadas com grupo).")
    
    # ─── 6.1 Enviar Espelho Completo para sq_raw_fazendas_grupo ────────────
    print(f"\n📤 6.1 Gravando espelho completo da LISTA_GERAL em {tabela_raw}...")
    
    tabela_destino_raw = tabela_raw
    try:
        supabase.table(tabela_destino_raw).select("id").limit(1).execute()
    except Exception:
        if tabela_destino_raw != "sq_raw_fazendas":
            print(f"   ℹ️ {tabela_destino_raw} não disponível no schema cache. Usando sq_raw_fazendas...")
            tabela_destino_raw = "sq_raw_fazendas"

    try:
        # Expurgar snapshot do mês de referência atual para atualizar com dados mais recentes
        supabase.table(tabela_destino_raw).delete().eq("mes_referencia", mes_ref_str).execute()
    except Exception as e_del_raw:
        print(f"   ℹ️ Aviso ao expurgar mês atual de {tabela_destino_raw}: {e_del_raw}")

    recs_todos = _sem_nan(df_todos.to_dict(orient="records"),
                          () if _tem_colunas_coord(tabela_destino_raw) else CAMPOS_COORD)
    LOTE = 1000
    sucesso_raw = 0
    erros_raw = 0

    for i in range(0, len(recs_todos), LOTE):
        lote = recs_todos[i : i + LOTE]
        try:
            supabase.table(tabela_destino_raw).upsert(lote, on_conflict="id").execute()
            sucesso_raw += len(lote)
        except Exception as e_raw:
            try:
                supabase.table(tabela_destino_raw).upsert(lote).execute()
                sucesso_raw += len(lote)
            except Exception as e_raw2:
                erros_raw += len(lote)
                print(f"     ❌ Erro ao enviar lote {i // LOTE + 1} para {tabela_destino_raw}: {e_raw2}")
        time.sleep(0.01)
    print(f"   ✅ {sucesso_raw} registros gravados em {tabela_destino_raw} (falhas: {erros_raw}).")

    # Tenta também sq_raw_fazendas_grupo se configurada com nome distinto e disponível
    if tabela_grupo_raw != tabela_destino_raw:
        try:
            for i in range(0, len(recs_todos), LOTE):
                supabase.table(tabela_grupo_raw).upsert(recs_todos[i : i + LOTE], on_conflict="id").execute()
            print(f"   ✅ Registros espelhados em {tabela_grupo_raw}.")
        except Exception as e_espelho:
            print(f"   ℹ️ Aviso ao espelhar em {tabela_grupo_raw}: {e_espelho}")

    # ─── 6.2 Enviar Ativas de Todas as Cadeias para sq_raw_fazendas_grupo_ativas ─
    print(f"\n📤 6.2 Gravando fazendas ativas (todas as cadeias) da LISTA_GERAL em {tabela_grupo_ativas_raw}...")

    # ─── 6.3 Reconstruindo histórico de FAZENDAS ATIVAS (todas as cadeias) ─────
    # Janela temporal deslizante: reprocessa mês anterior (fechamento), mês atual e próximo mês
    mes_ref_dt = pd.to_datetime(mes_ref_str)
    mes_anterior_str = (mes_ref_dt - pd.DateOffset(months=1)).strftime("%Y-%m-01")
    proximo_mes_str = (mes_ref_dt + pd.DateOffset(months=1)).strftime("%Y-%m-01")

    # Se reindex_completo for True no chamador, recalcula todos os meses a partir de 2026-01-01
    if reindex_completo:
        inicio_2026 = pd.Timestamp("2026-01-01")
        meses_reconciliacao = [m.strftime("%Y-%m-01") for m in pd.date_range(start=inicio_2026, end=pd.to_datetime(proximo_mes_str), freq="MS")]
    else:
        meses_reconciliacao = [mes_anterior_str, mes_ref_str, proximo_mes_str]

    print(f"\n📊 6.3 Reconstruindo FAZENDAS ATIVAS (todas as cadeias) em {tabela_ativos} para a janela ({meses_reconciliacao[0]} a {meses_reconciliacao[-1]})...")

    # Buscar base completa de vínculos no Supabase para isolar da LISTA_GERAL
    res_vinculos_dim = supabase.table("sq_raw_vinculos").select(
        "codigo_lr, consultor_grupo_atendimento, grupo_atendimento, data_associacao, projeto, "
        "nome_produtor, nome_propriedade, vinculo_ativo, unidade_atendimento, cidade_produtor, "
        "estado_produtor, codigo_agroindustria, codigo_fazenda, tipo_ponto_atendimento"
    ).execute()
    df_vinculos_base = pd.DataFrame(res_vinculos_dim.data) if res_vinculos_dim.data else pd.DataFrame()

    def normalizar_cadeia(tipo_val, proj_val):
        t = str(tipo_val or "").strip().upper()
        p = str(proj_val or "").strip().upper()
        if "LEITE" in t or any(x in p for x in ["REGENERA", "ALVOAR", "SEMEAR", "CCPR", "LPA", "COPRIL", "CAMPILEITE", "DANONE", "CFT"]):
            return "LEITE"
        if "CACAU" in t or any(x in p for x in ["CACAU", "OFI", "MIMC", "CARGILL"]):
            return "CACAU"
        if "CAFE" in t or "CAFÉ" in t or "CAFE" in p:
            return "CAFE"
        if "GRAOS" in t or "GRÃOS" in t or "GRAOS" in p:
            return "MAIS GRAOS"
        if t and t not in ["NONE", "NAN", ""]:
            return t
        return "OUTROS"

    if not df_vinculos_base.empty:
        print(f"   -> {len(df_vinculos_base)} vínculos carregados de sq_raw_vinculos ({df_vinculos_base['codigo_lr'].nunique()} produtores únicos).")
    else:
        print("   ⚠️ Nenhum vínculo encontrado em sq_raw_vinculos.")

    # ── FONTE DE VERDADE: Ler BD_BI_VINCULOS_COMPLETO.xlsx para saber quem está ATIVO oficialmente ──
    # O campo vinculo_ativo em sq_raw_vinculos pode estar obsoleto (ETL antigo não deletou registros).
    # Produtores inativados e removidos da planilha mestre devem ser excluídos de tab_dim_fazendas_ativas.
    codigos_ativos_excel: set = set()
    try:
        arq_vinc_excel = bd_path / "BD_BI_VINCULOS_COMPLETO.xlsx"
        if arq_vinc_excel.exists():
            try:
                from functions.atualizar_detalhamento_consistencia import ler_excel_seguro
            except ImportError:
                from atualizar_detalhamento_consistencia import ler_excel_seguro
            df_vinc_ex = ler_excel_seguro(arq_vinc_excel)
            if "Ativo" in df_vinc_ex.columns and "Código LR" in df_vinc_ex.columns:
                m_at_ex = df_vinc_ex["Ativo"].astype(str).str.strip().str.lower().isin(["true", "sim", "ativo", "1"])
                codigos_ativos_excel = set(df_vinc_ex[m_at_ex]["Código LR"].dropna().astype(str).str.strip().str.upper())
                print(f"   -> {len(codigos_ativos_excel)} vínculos ativos confirmados em BD_BI_VINCULOS_COMPLETO.xlsx (fonte de verdade).")
    except Exception as e_vinc_ex2:
        print(f"   ℹ️ Aviso ao ler BD_BI_VINCULOS_COMPLETO.xlsx para dim_fazendas_ativas: {e_vinc_ex2}")

    # Adicionar também todas as fazendas com Status = Ativo da LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx
    if not df_todos.empty and "status" in df_todos.columns and "codigo_produtor" in df_todos.columns:
        m_at_lg = df_todos["status"].astype(str).str.strip().str.lower().isin(["true", "sim", "ativo", "1"])
        codigos_lg_ativos = set(df_todos[m_at_lg]["codigo_produtor"].dropna().astype(str).str.strip().str.upper())
        codigos_ativos_excel.update(codigos_lg_ativos)
        print(f"   -> {len(codigos_lg_ativos)} fazendas ativas adicionadas a partir de LISTA_GERAL_RELATORIO_DE_GRUPO.xlsx (total ativos consolidados: {len(codigos_ativos_excel)}).")

    # Carregar lista de códigos de produtores com atendimento/visita em sq_raw_visitas
    codigos_com_visita = set()
    codigos_com_visita_por_mes = {}
    try:
        # ATENÇÃO: sq_raw_visitas NÃO possui coluna mes_referencia; o mês é derivado de data_visita.
        # Pedir mes_referencia fazia o PostgREST devolver 400 (42703) e o except abaixo zerava
        # silenciosamente codigos_com_visita/codigos_com_visita_por_mes em toda execução.
        df_vis_cods = consultar_tabela_supabase(
            "sq_raw_visitas",
            "codigo_lr, data_visita",
            raiz=raiz_projeto
        )
        if not df_vis_cods.empty:
            if "codigo_lr" in df_vis_cods.columns:
                codigos_com_visita = set(df_vis_cods["codigo_lr"].dropna().astype(str).str.strip().str.upper())
            for _, rv in df_vis_cods.iterrows():
                c_vis = str(rv.get("codigo_lr") or "").strip().upper()
                dt_vis = rv.get("data_visita")
                if c_vis and pd.notna(dt_vis):
                    m_vis = pd.to_datetime(dt_vis, errors="coerce")
                    if pd.notna(m_vis):
                        m_str = m_vis.strftime("%Y-%m-01")
                        if m_str not in codigos_com_visita_por_mes:
                            codigos_com_visita_por_mes[m_str] = set()
                        codigos_com_visita_por_mes[m_str].add(c_vis)
            print(f"   -> {len(codigos_com_visita)} produtores identificados com relatórios de visita em sq_raw_visitas ({len(codigos_com_visita_por_mes)} meses).")
    except Exception as e_v_cods:
        print(f"   ⚠️ Aviso ao carregar visitas para validação CFT: {e_v_cods}")

    # ── VÍNCULOS E GRUPOS UNIFICADOS: Combinar LISTA_GERAL (df_todos) + sq_raw_vinculos (df_vinculos_base)
    def _grupo_valido(valor) -> bool:
        """Um grupo só é aproveitável se não for vazio/NaN e não for o placeholder NÃO ATRIBUÍDO."""
        v = str(valor or "").strip().upper()
        if not v or v in ("NAN", "NONE"):
            return False
        return "NÃO ATRIBUÍDO" not in v and "NAO ATRIBUIDO" not in v

    # sq_raw_vinculos pode ter mais de uma linha por produtor (vínculo antigo encerrado + vínculo vigente).
    # Priorizar o vínculo ativo e, em empate, a data_associacao mais recente — pegar a última linha
    # iterada fazia o ETL herdar data_associacao de época (ex.: 1970-01-01) e grupo desatualizado.
    vinc_dict = {}
    if not df_vinculos_base.empty:
        for _, r_v in df_vinculos_base.iterrows():
            c_v = str(r_v.get("codigo_lr") or "").strip().upper()
            if not c_v:
                continue
            novo = r_v.to_dict()
            atual = vinc_dict.get(c_v)
            if atual is None:
                vinc_dict[c_v] = novo
                continue
            rank_novo = (
                1 if novo.get("vinculo_ativo") is True else 0,
                1 if _grupo_valido(novo.get("grupo_atendimento")) else 0,
                pd.to_datetime(novo.get("data_associacao"), errors="coerce"),
            )
            rank_atual = (
                1 if atual.get("vinculo_ativo") is True else 0,
                1 if _grupo_valido(atual.get("grupo_atendimento")) else 0,
                pd.to_datetime(atual.get("data_associacao"), errors="coerce"),
            )
            dt_novo = rank_novo[2] if pd.notna(rank_novo[2]) else pd.Timestamp.min
            dt_atual = rank_atual[2] if pd.notna(rank_atual[2]) else pd.Timestamp.min
            if (rank_novo[0], rank_novo[1], dt_novo) > (rank_atual[0], rank_atual[1], dt_atual):
                vinc_dict[c_v] = novo

    candidatos_list = []
    # 1. Fazendas ativas da LISTA_GERAL_RELATORIO_DE_GRUPO (fonte primária e oficial)
    for _, r_g in df_todos.iterrows():
        c_prod = str(r_g.get("codigo_produtor") or "").strip()
        if not c_prod or c_prod.lower() == "nan":
            continue
        # Incluir todos os produtores da LISTA_GERAL (mesmo inativos atuais), pois a inativação
        # é tratada temporante por ref_m via inativacoes_por_codigo no loop mensal abaixo.
        v_info = vinc_dict.get(c_prod.upper(), {})
        candidatos_list.append({
            "codigo_lr": c_prod,
            "nome_produtor": r_g.get("nome_produtor") or v_info.get("nome_produtor"),
            "nome_propriedade": r_g.get("nome_propriedade") or v_info.get("nome_propriedade"),
            "estado_produtor": r_g.get("estado") or v_info.get("estado_produtor"),
            "cidade_produtor": r_g.get("cidade") or v_info.get("cidade_produtor"),
            "tipo_ponto_atendimento": r_g.get("tipo_ponto_atendimento") or v_info.get("tipo_ponto_atendimento"),
            "unidade_atendimento": r_g.get("unidade_atendimento") or v_info.get("unidade_atendimento"),
            # "NÃO ATRIBUÍDO" é placeholder, não atribuição: quando o produtor sai da carteira ele
            # volta para esse estado na LISTA_GERAL atual e, sendo string truthy, vencia o `or` e
            # apagava o consultor em TODOS os meses do histórico (remoção retroativa da carteira).
            "grupo_atendimento": (
                r_g.get("grupo_ponto_atendimento")
                if _grupo_valido(r_g.get("grupo_ponto_atendimento"))
                else (v_info.get("grupo_atendimento") or r_g.get("grupo_ponto_atendimento"))
            ),
            "consultor_grupo_atendimento": (
                r_g.get("consultor")
                if _grupo_valido(r_g.get("consultor"))
                else (v_info.get("consultor_grupo_atendimento") or r_g.get("consultor"))
            ),
            "projeto": r_g.get("projeto") or v_info.get("projeto"),
            "codigo_agroindustria": r_g.get("codigo_agroindustria") or v_info.get("codigo_agroindustria"),
            "codigo_fazenda": r_g.get("codigo_fazenda") or v_info.get("codigo_fazenda"),
            "data_associacao": v_info.get("data_associacao"),
            "vinculo_ativo": True
        })

    # 2. Fazendas adicionais de sq_raw_vinculos que não constem na LISTA_GERAL
    codigos_g_set = {str(item.get("codigo_lr") or "").strip().upper() for item in candidatos_list}
    if not df_vinculos_base.empty:
        for _, r_v in df_vinculos_base.iterrows():
            c_v = str(r_v.get("codigo_lr") or "").strip()
            if c_v and c_v.upper() not in codigos_g_set:
                candidatos_list.append({
                    "codigo_lr": c_v,
                    "nome_produtor": r_v.get("nome_produtor"),
                    "nome_propriedade": r_v.get("nome_propriedade"),
                    "estado_produtor": r_v.get("estado_produtor"),
                    "cidade_produtor": r_v.get("cidade_produtor"),
                    "tipo_ponto_atendimento": r_v.get("tipo_ponto_atendimento"),
                    "unidade_atendimento": r_v.get("unidade_atendimento"),
                    "grupo_atendimento": r_v.get("grupo_atendimento"),
                    "consultor_grupo_atendimento": r_v.get("consultor_grupo_atendimento"),
                    "projeto": r_v.get("projeto"),
                    "codigo_agroindustria": r_v.get("codigo_agroindustria"),
                    "codigo_fazenda": r_v.get("codigo_fazenda"),
                    "data_associacao": r_v.get("data_associacao"),
                    "vinculo_ativo": r_v.get("vinculo_ativo")
                })

    df_base_candidatos = pd.DataFrame(candidatos_list).drop_duplicates(subset=["codigo_lr"])
    print(f"   -> {len(df_base_candidatos)} produtores ativas mapeados como candidatos a tab_dim_fazendas_ativas.")

    dim_tem_coord = _tem_colunas_coord(tabela_ativos)
    for ref_m in meses_reconciliacao:
        novos_ativos_m = []

        for _, r in df_base_candidatos.iterrows():
            c = str(r.get("codigo_lr") or "").strip()
            nome_p = str(r.get("nome_produtor") or "").strip()

            if not c or c.lower() == "nan":
                continue

            # 0. Se possui flag _INATIVO no código ou nome, NUNCA entra na dim de ativos
            if ("_INATIVO" in c.upper()) or ("(INATIVO)" in nome_p.upper()) or ("_INATIVO" in nome_p.upper()):
                continue

            # Priorizar grupo completo da LISTA_GERAL_RELATORIO_DE_GRUPO, exceto se estiver como NÃO ATRIBUÍDO
            grupo_val = str(r.get("grupo_atendimento") or "")
            grp_lg, cons_lg = mapa_grupos_lista_geral.get(c.upper(), (None, None))
            if _grupo_valido(grp_lg):
                grupo_efetivo = grp_lg
                consultor_efetivo = cons_lg if cons_lg else extrair_consultores_do_grupo(grupo_efetivo)
            elif _grupo_valido(grupo_val):
                grupo_efetivo = grupo_val
                consultor_efetivo = extrair_consultores_do_grupo(grupo_efetivo)
            else:
                # Último recurso: recuperar o vínculo histórico em sq_raw_vinculos para não perder
                # a atribuição de consultor nos meses em que o produtor ainda estava na carteira.
                grupo_hist = vinc_dict.get(c.upper(), {}).get("grupo_atendimento")
                grupo_efetivo = grupo_hist if _grupo_valido(grupo_hist) else grupo_val
                consultor_efetivo = extrair_consultores_do_grupo(grupo_efetivo)

            tem_visita_no_mes = c.upper() in codigos_com_visita_por_mes.get(ref_m, set())

            # 0.1 Se a fazenda for PURAMENTE CFT ou de grupo exclusivo CFT, NUNCA entra na dimensão de ativos
            proj_val = str(r.get("projeto") or "")
            if not tem_visita_no_mes and eh_puramente_cft(grupo_efetivo, proj_val, consultor_efetivo):
                continue

            # 0.2 Status do Consultor: Se todos os consultores do grupo estiverem inativos
            cons_resp = extrair_consultor_individual(r.get("consultor_grupo_atendimento"), grupo_efetivo)
            consultores_do_grupo = [p.strip().upper() for p in re.sub(r"\(.*?\)", "", grupo_efetivo).split("/") if p.strip()]
            if not tem_visita_no_mes:
                if consultores_do_grupo and all(rn.chave(cg) in consultores_inativos for cg in consultores_do_grupo):
                    continue
                elif not consultores_do_grupo and rn.chave(cons_resp) in consultores_inativos:
                    continue

            # 0.3 Movimentação oficial (tab_fato_movimentacao) manda no recorte temporal do vínculo.
            # Vale mesmo havendo visita no mês: Entrada e Saída são eventos datados e auditados,
            # e a convenção é que a saída do mês M já não compõe a carteira de M.
            if vinculo_encerrado_em(c, ref_m):
                continue
            if vinculo_ainda_nao_iniciado_em(c, ref_m):
                continue

            # 1. Se a data de associação for posterior ao mês avaliado, verificar se houve visita no mês avaliado
            dt_assoc = r.get("data_associacao")
            dt_assoc_p = pd.to_datetime(dt_assoc, errors="coerce")

            if pd.notna(dt_assoc_p) and dt_assoc_p.strftime("%Y-%m-01") > ref_m:
                if not tem_visita_no_mes:
                    continue

            # 2. Se o produtor possui visita técnica no mês, sua atividade no mês é incontestável.
            if not tem_visita_no_mes:
                # 2a. BD_BI_VINCULOS_COMPLETO.xlsx: Se inativo e com data de inativação <= ref_m
                is_ativo_excel = c.upper() in codigos_ativos_excel
                if not is_ativo_excel and c in inativacoes_por_codigo and inativacoes_por_codigo[c] <= ref_m:
                    continue

                # 3. Se vinculo_ativo é False no Supabase e o produtor já estava inativo em data <= ref_m
                v_ativo = r.get("vinculo_ativo")
                if v_ativo is False and c in inativacoes_por_codigo and inativacoes_por_codigo[c] <= ref_m:
                    continue

                # 4. Cadastro novo posterior ao mês avaliado — já coberto pela regra 0.3
                # (vinculo_ainda_nao_iniciado_em), mantido para produtores sem linha do tempo completa.
                if c in cadastros_por_codigo and cadastros_por_codigo[c] > ref_m:
                    continue

            # Tratamentos dimensionais de projeto, cadeia, agroindústria e região
            proj_final = proj_val.strip().upper() if proj_val else None
            cadeia_calc = normalizar_cadeia(r.get("tipo_ponto_atendimento"), proj_final)
            agro_calc = MAP_PROJETO_AGRO.get(proj_final) or (proj_final if proj_final in ["OFI", "PV CARGILL", "NCP", "MIMC", "CAFE & GESTAO", "COPRIL", "CAMPILEITE"] else None)
            uf_val = limpar_uf(r.get("estado_produtor"))
            reg_calc = formatar_regiao(agro_calc, regiao_map.get(c.upper()), uf_val)
            nome_prop = str(r.get("nome_propriedade") or "FAZENDA").strip()[:250]
            cid_val = str(r.get("cidade_produtor") or "").strip()[:100] or "NÃO INFORMADA"
            unid_val = str(r.get("unidade_atendimento") or "LABOR RURAL").strip()[:100]
            id_ativo = f"{c}_{ref_m.replace('-', '_')}"
            novos_ativos_m.append({
                "id": id_ativo,
                "codigo_produtor": c[:50],
                "nome_produtor": nome_p[:250] if nome_p else "PRODUTOR",
                "nome_propriedade": nome_prop,
                "estado": uf_val or "NÃO INFORMADO",
                "cidade": cid_val,
                "tipo_ponto_atendimento": cadeia_calc,
                "unidade_atendimento": unid_val,
                "grupo_ponto_atendimento": str(grupo_efetivo)[:250] if grupo_efetivo else cons_resp,
                "consultor": str(consultor_efetivo)[:250] if consultor_efetivo else None,
                "projeto": proj_final,
                "agroindustria": agro_calc,
                "regiao": reg_calc,
                **_coord_sq(c),
                "codigo_agroindustria": r.get("codigo_agroindustria"),
                "codigo_fazenda": r.get("codigo_fazenda"),
                "status": "Ativo",
                "mes_referencia": ref_m,
                "data_processamento": agora_iso
            })

        df_novos_ativos = pd.DataFrame(novos_ativos_m).drop_duplicates(subset=["id"])
        print(f"   Mês {ref_m}: {len(df_novos_ativos)} fazendas ativas consolidadas.")

        # Limpar apenas os registros do mês específico que estamos atualizando antes de inserir os novos
        try:
            supabase.table(tabela_ativos).delete().eq("mes_referencia", ref_m).execute()
        except Exception as e_del_m:
            print(f"   ℹ️ Aviso ao limpar {ref_m} em {tabela_ativos}: {e_del_m}")

        registros_ativos = _sem_nan(df_novos_ativos.to_dict(orient="records"),
                                    () if dim_tem_coord else CAMPOS_COORD)
        sucesso_ativos = 0
        erros_ativos = 0
        for i in range(0, len(registros_ativos), LOTE):
            lote_at = registros_ativos[i : i + LOTE]
            try:
                supabase.table(tabela_ativos).upsert(lote_at, on_conflict="id").execute()
                sucesso_ativos += len(lote_at)
            except Exception as e:
                try:
                    supabase.table(tabela_ativos).upsert(lote_at).execute()
                    sucesso_ativos += len(lote_at)
                except Exception as e2:
                    erros_ativos += len(lote_at)
                    print(f"     ❌ Erro ao enviar lote de ativos {i // LOTE + 1}: {e2}")
            time.sleep(0.01)

        print(f"   ✅ {sucesso_ativos} produtores ativos atualizados para {ref_m} em {tabela_ativos} (falhas: {erros_ativos}).")

    print("\n=================================================================")
    print("   RECONCILIAÇÃO CONCLUÍDA COM SUCESSO!                          ")
    print("=================================================================")


if __name__ == "__main__":
    executar_reconciliacao(reindex_completo=True)