"""Relatório de inconsistências de estoque no Elabore (somente leitura).

Compara ExpenseEntry x FeedingExpenseEntry (CONSUMIR) e lotes com saldo negativo.
Saída: data/output/inconsistencia/<timestamp>_elabore_anexo_inconsistencias_estoque.xlsx
Credenciais PG_* vêm do .env do projeto lr-indicadores-elabore (PROD).
Uso: python relatorio_inconsistencias_estoque_elabore.py [caminho_do_.env] [sufixo]
  Ex.: ... "<.../conversor-bd-elabore/.env>" dev  -> consulta DEV e acrescenta _dev ao nome do arquivo.
"""
import os, sys, datetime as dt, pandas as pd, psycopg2, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from dotenv import load_dotenv
RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
from functions.function import exportar_varias_abas_xlsx
ENV_PADRAO = r"C:/Users/Guilherme/LABOR RURAL/Analytics - Departamento Analytics/FERRAMENTAS/ELABORE/ELABORE_NOVO_SISTEMA/ANALISE_INDICADORES/lr-indicadores-elabore/.env"
ENV = sys.argv[1] if len(sys.argv) > 1 else ENV_PADRAO
SUFIXO = f"_{sys.argv[2]}" if len(sys.argv) > 2 else ""
load_dotenv(ENV, override=True)
print("Banco:", os.getenv("PG_HOST"))
c = psycopg2.connect(host=os.getenv("PG_HOST"), port=os.getenv("PG_PORT"), dbname=os.getenv("PG_DATABASE"), user=os.getenv("PG_USER"), password=os.getenv("PG_PASSWORD"), client_encoding="UTF8")
c.set_session(readonly=True)

# Cadastro direto da tabela Property (inclui inativas, que a vw_dim_property filtra).
# Consultor: vínculo atual; se não houver (ex.: propriedade inativa), o último vínculo aprovado.
PROP = """
prop as (
  select p.id_property, p.labor_rural_code, p.property_name, e.name as entrepreneur_name, a.name as agroindustry_name,
         cons.name as consultant_name,
         case when p."isActive" and p."isApproved" then 'ATIVA'
              when p."isActive" then 'ATIVA (APROVAÇÃO PENDENTE)'
              else 'INATIVA' end as situacao_propriedade,
         cons.vinculo_encerrado
  from "Property" p
  left join "Entrepreneur" e on e.id_entrepreneur = p.id_entrepreneur
  left join "Agroindustry" a on a.id_agroindustry = p.id_agroindustry
  left join lateral (
    select u.name, pu.return_date is not null as vinculo_encerrado
    from "PropertyUsers" pu join "User" u on u.id_user = pu.id_user join "UserType" ut on ut.id_type = u.id_type
    where pu.id_property = p.id_property and ut.name = 'Consultor(a)' and pu."isAssociationApproved"
    order by (pu.return_date is null) desc, coalesce(pu."approvedAt", pu.created_at) desc, pu.created_at desc
    limit 1) cons on true
)"""
COLS_PROP = '''p.labor_rural_code as "Código LR", p.property_name as "Fazenda", p.entrepreneur_name as "Produtor",
       p.agroindustry_name as "Agroindústria",
       p.consultant_name || case when p.vinculo_encerrado then ' (vínculo encerrado)' else '' end as "Consultor",
       p.situacao_propriedade as "Situação da propriedade",'''

SQL_LANC = f"""with {PROP}
select {COLS_PROP}
       e.reference_month::date as "Mês referência", f.item_code as "Item", e.unit as "Unidade",
       e.quantity as "Qtd cabeçalho (ExpenseEntry)", f.consumed_quantity as "Qtd consumida detalhe (FeedingExpenseEntry)",
       f.consumed_quantity - e.quantity as "Diferença (detalhe - cabeçalho)",
       f.unit_price as "Preço unitário", e.amount_total as "Valor total gravado",
       round((e.quantity*f.unit_price)::numeric,2) as "Valor pela qtd cabeçalho",
       e.created_at as "Criado em", e.updated_at as "Editado em", e.id_property as "id_property", e.id_expense_entry as "id_expense_entry"
from "ExpenseEntry" e join "FeedingExpenseEntry" f using (id_expense_entry)
left join prop p on p.id_property = e.id_property
where e.is_active and f.operation='CONSUMIR' and abs(e.quantity - f.consumed_quantity) > 0.01
order by p.property_name, e.reference_month"""

SQL_LOTES = f"""with {PROP}, mov as (
  select m.id_stock_control_item,
         sum(case when m.movement_type='ENTRY' then m.quantity else 0 end) entradas,
         sum(case when m.movement_type='EXIT' then m.quantity else 0 end) saidas,
         count(*) filter (where m.movement_type='EXIT') n_saidas
  from "StockControlMovement" m where m.is_active group by 1)
select {COLS_PROP}
       i.item_name as "Item", i.category as "Categoria", i.purchase_date::date as "Data compra", i.unit as "Unidade",
       i.purchased_quantity as "Qtd comprada (lote)", mov.entradas as "Entradas (movimentos)", mov.saidas as "Saídas (movimentos)",
       mov.n_saidas as "Nº saídas", mov.entradas - mov.saidas as "Saldo pelos movimentos",
       i.in_stock_quantity as "Saldo gravado no lote", i.situation::text as "Situação do lote",
       i.id_property as "id_property", i.id_stock_control_item as "id_stock_control_item"
from "StockControlItem" i join mov using (id_stock_control_item)
left join prop p on p.id_property = i.id_property
where i.is_active and mov.entradas - mov.saidas < -0.01
order by mov.entradas - mov.saidas"""

lanc = pd.read_sql(SQL_LANC, c); lotes = pd.read_sql(SQL_LOTES, c); c.close()

def resumo(df, nome_id, rotulo):
    g = df.assign(**{"Agroindústria": df["Agroindústria"].fillna("SEM AGROINDÚSTRIA")})
    return (g.groupby(["Agroindústria", "Situação da propriedade"], dropna=False)
             .agg(**{rotulo: (nome_id, "count"), "Fazendas": ("id_property", "nunique")}).reset_index())

abas = {
    "Lancamentos_divergentes": lanc,
    "Lotes_saldo_negativo": lotes,
    "Resumo_agroindustria_lanc": resumo(lanc, "id_expense_entry", "Lançamentos"),
    "Resumo_agroindustria_lotes": resumo(lotes, "id_stock_control_item", "Lotes"),
}
ts = dt.datetime.now().strftime("%Y_%m_%d_%H%M%S")
out = RAIZ / "data" / "output" / "inconsistencia" / f"{ts}_elabore_anexo_inconsistencias_estoque{SUFIXO}.xlsx"
exportar_varias_abas_xlsx(abas, out)
print(out)
for n, df in (("lanc", lanc), ("lotes", lotes)):
    print(n, len(df), "fazendas", df.id_property.nunique(), "| sem nome:", int(df["Fazenda"].isna().sum()), "| sem codigo LR:", int(df["Código LR"].isna().sum()))
    print(ascii(df.groupby("Situação da propriedade").id_property.agg(["count", "nunique"]).to_dict()))
