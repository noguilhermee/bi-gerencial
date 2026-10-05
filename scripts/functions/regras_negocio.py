# -*- coding: utf-8 -*-
"""
Regras de negócio oficiais do BI Labor Rural (fonte única da verdade).

Tudo que classifica, filtra ou padroniza dados para o dashboard mora aqui:
cadeia de leite, whitelist de visitas técnicas, consultores de campo,
agroindústria, região e situação de consistência. A API e o frontend apenas
leem os valores já resolvidos pelo ETL.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# ─── Consultores ──────────────────────────────────────────────────────────────

ALIAS_CONSULTORES = {
    "MARIO BARBOSA FILHO": "MARIO BARBOSA ROSA FILHO",
    "MARIO BARBOSA": "MARIO BARBOSA ROSA FILHO",
}

# Consultores da LAC Consultoria aparecem individualmente no SmartQuestion
CONSULTORES_LAC = {"CELIO ROBERTO OLIVEIRA", "SUELY DE JESUS OLIVEIRA"}
NOME_LAC = "LAC CONSULTORIA"

NAO_ATRIBUIDO = "NÃO ATRIBUÍDO"

# Perfis de coordenação/supervisão e contas genéricas que não fazem visita de campo
CONSULTORES_NAO_CAMPO = {
    "TALITA FONTES",
    "CONSULTOR LABOR RURAL (GENERICO)",
    "CONSULTOR GENERICO",
    "USUARIO TESTE (PRODUCAO)",
    "USUARIO TESTE",
    "CONTA DE SUPERVISAO",
    "LABOR RURAL (GERAL)",
    "SUPERVISAO",
    "SUPERVISAO AGRICULTURA",
    "SUPERVISAO PECUARIA",
    "COORDENACAO",
}

# Consultores removidos do BI: o nome sai dos grupos e as fazendas/visitas/movimentações
# em que ele é o único consultor de campo são descartadas
CONSULTORES_EXCLUIDOS_BI = {
    "MATEUS CARNIELLI",
    "AUROKE",
}

# Consultores que NÃO lançam dados no Elabore (exceção de negócio). Continuam na carteira e nas visitas,
# mas as fazendas deles ficam fora dos indicadores de consistência (ex.: "Número de fazendas consistentes
# no mês"). Inclui a grafia curta usada nas planilhas do Procampo (config: procampo.aliases_consultor).
CONSULTORES_SEM_LANCAMENTO_ELABORE = {
    "MARCOS PAULO DE PINHO FLECHA",
    "MARCOS PAULO FLECHA",
}

GRUPOS_CFT_EXCLUSIVOS = {
    "DAYANNE UCHOA VEIGA / DEBORA LIMA DE OLIVEIRA / MARIO BARBOSA ROSA FILHO / MATEUS CARNIELLI / TALITA FONTES / THAYNAN FERREIRA DE ARAUJO",
    "HUGO LOPES / MATEUS CARNIELLI / ROMARCIO PAULO DE OLIVEIRA / THAYNAN FERREIRA DE ARAUJO",
    "BRUNO ANTONIO FERRONI RODRIGUES / HUGO LOPES / MATEUS CARNIELLI / THAYNAN FERREIRA DE ARAUJO",
    "MATHEUS GOMIDES GONCALVES",
    "TALITA FONTES",
}

# ─── Cadeia de leite e projetos ───────────────────────────────────────────────

PROJETOS_OFICIAIS_LEITE = ["ALVOAR", "CCPR", "LPA", "REGENERA", "SEMEAR", "COPRIL", "CAMPILEITE", "NESTLE", "EDUCAMPO"]

TERMOS_FORA_LEITE = [
    "MAIS GRAOS", "GRAOS", "MIMC", "M&E", "CAFE&GESTAO", "CAFE & GESTAO",
    "CAFE", "CACAU", "CARGILL", "NCP", "OFI", "AGRICULTURA",
]

PROJETOS_CANONICOS = ["ALVOAR ASSIST", "ALVOAR ECO", "ATEG_CCPR", "LPA", "REGENERA", "SEMEAR"]

# ─── Visitas ──────────────────────────────────────────────────────────────────

WHITELIST_TIPOS_VISITA_LEITE = [
    "ALVOAR ASSIST", "ALVOAR ASSIST - V2",
    "ALVOAR ECO", "ALVOAR ECO - V2",
    "RELATORIO DE VISITA ATEG/CCPR_GOIAS", "RELATORIO DE VISITA ATEG/CCPR_GOIAS - V2",
    "RELATÓRIO DE VISITA AUROKE",
    "RELATORIO DE VISITA CAMPILEITE+",
    "RELATÓRIO DE VISITA COPRIL", "RELATÓRIO DE VISITA COPRIL - V2",
    "RELATÓRIO DE VISITA FLORA",
    "RELATÓRIO DE VISITA LABOR RURAL - LEITE",
    "RELATÓRIO DE VISITA LABOR RURAL (PADRÃO)",
    "RELATORIO DE VISITA LPA", "RELATORIO DE VISITA LPA - V1", "RELATORIO DE VISITA LPA - V2",
    "RELATÓRIO DE VISITA NATA",
    "RELATORIO DE VISITA REGENERA", "RELATORIO DE VISITA REGENERA - V2",
    "RELATÓRIO DE VISITA SEMEAR - V2", "RELATÓRIO DE VISITA SEMEAR - V3",
]

# ─── Consistência ─────────────────────────────────────────────────────────────

SITUACAO_CONSISTENTE = "CONSISTENTE"
SITUACAO_INCONSISTENTE = "INCONSISTENTE"
SITUACAO_DIVERGENTE = "DIVERGENTE"
SITUACAO_OUTLIER = "OUTLIER"
SITUACAO_SEM_DADOS = "SEM DADOS"

BLOCOS_ELABORE = ["receita", "qualidade", "alimentacao", "area", "rebanho", "mdo", "energia", "despesas"]


def chave(texto) -> str:
    """Normaliza texto para comparação: sem acentos, maiúsculo e sem espaços laterais."""
    if texto is None:
        return ""
    s = str(texto)
    if s.lower() in ("nan", "none", "nat"):
        return ""
    s = unicodedata.normalize("NFKD", s.replace("\xa0", " "))
    return "".join(c for c in s if not unicodedata.combining(c)).strip().upper()


def limpar_codigo_lr(valor) -> str:
    return chave(valor).replace(" ", "")


def limpar_id_atendimento(valor) -> int | None:
    """Converte o número do atendimento para inteiro (aceita '123.0', 123.0, ' 123 ')."""
    s = str(valor).strip().replace("\xa0", "") if valor is not None else ""
    if not s or s.lower() in ("nan", "none", "<na>"):
        return None
    try:
        n = int(float(s))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# ─── Consultores ──────────────────────────────────────────────────────────────

def eh_consultor_nao_campo(nome) -> bool:
    k = chave(nome)
    if not k:
        return False
    if k in CONSULTORES_NAO_CAMPO or k.startswith("TALITA FONTES"):
        return True
    return any(t in k for t in ("_CONSULTOR", "CONSULTOR_", "SUPERVISAO", "COORDENACAO"))


def eh_consultor_excluido(nome) -> bool:
    return chave(nome) in CONSULTORES_EXCLUIDOS_BI


def sanitizar_consultores(grupo, manter_excluidos: bool = False) -> list[str]:
    """Quebra um grupo 'A / B (PROJETO)' em consultores de campo individuais e padronizados."""
    if grupo is None:
        return []
    resultado: list[str] = []
    for parte in str(grupo).split("/"):
        parte = parte.strip()
        if not parte or parte.lower() == "nan":
            continue
        nome = re.sub(r"\s*\([^)]*\)\s*$", "", parte).strip().upper() or parte.upper()
        nome = ALIAS_CONSULTORES.get(nome, nome)
        if chave(nome) in CONSULTORES_LAC:
            nome = NOME_LAC
        if eh_consultor_nao_campo(nome):
            continue
        if not manter_excluidos and eh_consultor_excluido(nome):
            continue
        if nome not in resultado:
            resultado.append(nome)
    return resultado


def eh_grupo_excluido(grupo) -> bool:
    """Grupo cujos consultores de campo são todos removidos do BI (o registro inteiro sai)."""
    de_campo = sanitizar_consultores(grupo, manter_excluidos=True)
    return bool(de_campo) and all(eh_consultor_excluido(c) for c in de_campo)


def eh_consultor_sem_lancamento_elabore(nome) -> bool:
    return chave(nome) in CONSULTORES_SEM_LANCAMENTO_ELABORE


def eh_grupo_sem_lancamento_elabore(grupo) -> bool:
    """Fazenda fora dos indicadores de consistência: todos os consultores de campo do grupo são isentos.
    Se houver um co-consultor que lança no Elabore, a fazenda continua contando."""
    de_campo = sanitizar_consultores(grupo)
    return bool(de_campo) and all(eh_consultor_sem_lancamento_elabore(c) for c in de_campo)


def eh_codigo_conta_consultor(codigo_lr) -> bool:
    k = chave(codigo_lr)
    return "_CONSULTOR" in k or "CONSULTOR_" in k


# ─── Cadeia de leite, projeto e agroindústria ─────────────────────────────────

def eh_cadeia_leite(projeto) -> bool:
    """Projeto pertence à cadeia de leite (vazio conta como leite, como no dashboard)."""
    p = chave(projeto)
    if not p:
        return True
    if "CFT" in p and not any(proj in p for proj in PROJETOS_OFICIAIS_LEITE):
        return False
    return not any(termo in p for termo in TERMOS_FORA_LEITE)


def eh_tipo_visita_tecnica_leite(tipo_visita) -> bool:
    return chave(tipo_visita) in _WHITELIST_NORMALIZADA


_WHITELIST_NORMALIZADA = {chave(t) for t in WHITELIST_TIPOS_VISITA_LEITE}


AGRO_NAO_INFORMADA = "NÃO INFORMADA"
REGIAO_NAO_INFORMADA = "NÃO INFORMADA"


def mapear_agroindustria(projeto_ou_agro) -> str:
    """Nome canônico da agroindústria exibido no dashboard (= tab_dim_agroindustria.nome_bi)."""
    p = chave(projeto_ou_agro)
    if not p or p in ("LEITE", "GERAL", "NAO INFORMADA"):
        return AGRO_NAO_INFORMADA
    regras = [
        ("ALVOAR", "Alvoar"),
        ("CCPR", "CCPR"),
        ("LPA", "Laticínios Porto Alegre (LPA)"),
        ("PORTO ALEGRE", "Laticínios Porto Alegre (LPA)"),
        ("REGENERA", "Nestlé"),
        ("NESTLE", "Nestlé"),
        ("SEMEAR", "Danone"),
        ("DANONE", "Danone"),
        ("COPRIL", "Copril"),
        ("CAMPILEITE", "CAMPILEITE"),
        ("QUILLAYES", "Quillayes"),
        ("PIRACANJUBA", "Piracanjuba"),
        ("PROCAMPO", "Piracanjuba"),   # o Procampo é a operação da Piracanjuba fora do SmartQuestion
        ("INDEPENDENTE", "Independente"),
    ]
    for termo, nome in regras:
        if termo in p:
            return nome
    return str(projeto_ou_agro).strip()


# ─── Região ───────────────────────────────────────────────────────────────────

_REGIAO_EXPLICITA = {
    "BA": "Bahia", "CE": "Ceará", "AL": "Alagoas", "SE": "Sergipe", "PE": "Pernambuco",
    "MT": "Campinápolis", "GO": "Goiânia", "SP": "Araçatuba",
    "ALAGOAS": "Alagoas", "ARACATUBA": "Araçatuba", "BAHIA": "Bahia", "BATALHA/AL": "Alagoas",
    "BATALHA": "Alagoas", "CEARA": "Ceará", "GOIANIA": "Goiânia", "IBIA": "Ibiá",
    "INDEPENDENTE": "Independente", "ITAMBACURI": "Itambacuri", "ITUIUTABA": "Ituiutaba",
    "MINAS GERAIS": "Minas Gerais", "MONTES CLAROS": "Montes Claros", "PATOS DE MINAS": "Patos de Minas",
    "PEDRA DO FORTE": "Bahia", "PERNAMBUCO": "Pernambuco", "PONTE NOVA": "Ponte Nova",
    "QUIXERAMOBIM": "Ceará", "SERGIPE": "Sergipe", "SERTAO NORTE": "Sertão Norte",
    "SUL DE MINAS": "Sul de Minas", "TRIANGULO MINEIRO": "Triângulo Mineiro",
}
_SIGLAS_UF = {"AL", "MG", "SP", "GO", "CE", "BA", "SE", "PE", "RJ", "PR", "SC", "RS", "ES", "MT", "MS",
              "RO", "AC", "AM", "PA", "MA", "PI", "RN", "PB", "TO", "DF"}
_REGIOES_INVALIDAS = {"", "0", "1", "TESTE", "TEST", "LABOR RURAL", "UNIDADE GENERICA", "NAO INFORMADA", "NONE", "NAN"}


def _regiao_nestle(k: str) -> str | None:
    if k == "GO" or "GOIANIA" in k or "9655" in k or "GOIAS" in k:
        return "Goiânia"
    if k == "MG" or "PATOS" in k or "IBIA" in k or "9188" in k or "1215" in k:
        return "Patos de Minas e Ibiá"
    if "ITUIUTABA" in k or "1217" in k or "TRIANGULO" in k:
        return "Ituiutaba"
    if "MONTES CLAROS" in k or "9264" in k or "SERTAO NORTE" in k:
        return "Montes Claros"
    if "ARACATUBA" in k or "0460" in k or k == "SP":
        return "Araçatuba"
    return None


def _formatar_nome_regiao(texto: str) -> str | None:
    texto = texto.strip()
    if not texto:
        return None
    sufixo = ""
    m = re.search(r"\s*-\s*(\d+)\s*$", texto)
    if m:
        sufixo = f" - {m.group(1)}"
        texto = texto[: m.start()].strip()
    k = chave(texto)
    if k in _REGIAO_EXPLICITA:
        return _REGIAO_EXPLICITA[k] + sufixo
    palavras = []
    for i, w in enumerate(texto.split()):
        wk = chave(w)
        if wk in _REGIAO_EXPLICITA and len(wk) == 2:
            palavras.append(_REGIAO_EXPLICITA[wk])
        elif wk in _SIGLAS_UF:
            palavras.append(wk)
        elif i > 0 and w.lower() in ("de", "da", "do", "das", "dos", "e"):
            palavras.append(w.lower())
        else:
            palavras.append(w[:1].upper() + w[1:].lower())
    return " ".join(palavras) + sufixo


def sanitizar_regiao(regiao_bruta, contexto=None) -> str | None:
    """Padroniza uma região livre (Azure/SmartQuestion) sem consultar a dimensão."""
    if regiao_bruta is None:
        return None
    texto = str(regiao_bruta).strip()
    k = chave(texto)
    if k in _REGIOES_INVALIDAS or k.isdigit():
        return None
    ctx = chave(contexto)
    if "NESTLE" in ctx or "REGENERA" in ctx or re.search(r"\b(1215|9188|1217|9655|9264)\b", k):
        reg = _regiao_nestle(k)
        if reg:
            return reg
    if k.startswith("BATALHA/"):
        return "Alagoas"
    if "/" in texto:
        partes = sorted({p for p in (_formatar_nome_regiao(x) for x in texto.split("/")) if p})
        return "/".join(partes) if partes else None
    return _formatar_nome_regiao(texto)


class ResolvedorRegiao:
    """Resolve a região canônica e garante que ela pertence à agroindústria.

    Fontes (Supabase): `tab_dim_agroindustria` (nome_bi), `tab_dim_regiao` (1 linha por agroindústria ×
    região canônica) e `tab_dim_regiao_alias` (nome bruto do SmartQuestion/Elabore/Procampo -> região canônica).
    """

    def __init__(self, dim_regiao: Iterable[dict], alias: Iterable[dict] = (), dim_agroindustria: Iterable[dict] = ()):
        agro_por_id = {a["id"]: chave(a.get("nome_bi") or a.get("nome_agroindustria"))
                       for a in dim_agroindustria if a.get("id") is not None}
        self.pares: set[tuple[str, str]] = set()          # (agroindústria, região) canônicos
        self.donos: dict[str, set[str]] = {}              # região canônica -> agroindústrias que a têm
        self.canonica: dict[tuple[str, str], str] = {}    # (agroindústria, chave da região) -> nome canônico
        for r in dim_regiao:
            if chave(r.get("status")) != "ATIVO":
                continue
            nome = str(r.get("nome_regiao") or "").strip()
            agro = agro_por_id.get(r.get("id_agroindustria"))
            if not nome or not agro:
                continue
            self.pares.add((agro, chave(nome)))
            self.donos.setdefault(chave(nome), set()).add(agro)
            self.canonica[(agro, chave(nome))] = nome
        self.de_para: dict[str, str] = {}
        # aliases sem agroindústria primeiro: são eles que definem o fallback genérico (sem "agro|") de forma estável
        # (o pandas devolve NULL como NaN, que não é None: `v == v` é falso só para NaN)
        def _tem_agro(x):
            v = x.get("id_agroindustria")
            return v is not None and v == v
        for a in sorted(alias, key=lambda x: (_tem_agro(x), x.get("id") or 0)):
            if chave(a.get("status")) != "ATIVO":
                continue
            bruto, formatada = chave(a.get("nome_bruto")), str(a.get("nome_regiao") or "").strip()
            if not bruto or not formatada:
                continue
            agro = agro_por_id.get(a.get("id_agroindustria"))
            if agro:
                self.de_para[f"{agro}|{bruto}"] = formatada
            self.de_para.setdefault(bruto, formatada)
        for (agro, k), nome in self.canonica.items():     # o próprio nome canônico também resolve
            self.de_para.setdefault(f"{agro}|{k}", nome)

    def valida(self, agroindustria, regiao) -> bool:
        """True se a região canônica existe em `tab_dim_regiao` para essa agroindústria."""
        return (chave(agroindustria), chave(regiao)) in self.pares

    def agroindustrias_da(self, regiao) -> set[str]:
        """Agroindústrias (chave) que possuem essa região canônica."""
        return self.donos.get(chave(regiao), set())

    def resolver(self, regiao_bruta, agroindustria=None, projeto=None) -> str:
        """Nome canônico da região (sem validar contra a agroindústria; use `valida`)."""
        if regiao_bruta is None or not str(regiao_bruta).strip():
            return REGIAO_NAO_INFORMADA
        bruto = chave(regiao_bruta)
        agro = chave(agroindustria or mapear_agroindustria(projeto))
        for k in (f"{agro}|{bruto}", bruto):
            if k in self.de_para:
                return self.de_para[k]
        return sanitizar_regiao(str(regiao_bruta).strip(), projeto or agroindustria) or REGIAO_NAO_INFORMADA


# ─── Procampo / Piracanjuba ───────────────────────────────────────────────────

def eh_fazenda_smartquestion(codigo_lr) -> bool:
    """Código LR###### = fazenda gerenciada pelo SmartQuestion (visitas, carteira e movimentação), mesmo
    quando é da Piracanjuba. PRO#### e o provisório MAT... são gerenciados pelo Procampo/Esteio."""
    return re.fullmatch(r"LR\d+", limpar_codigo_lr(codigo_lr)) is not None


def classificar_movimentacao_procampo(status_movimentacao) -> str | None:
    """'ENTRADA', 'SAIDA' ou None. Legado (já estava na base) e trocas de consultor/titularidade
    não são entrada nem saída de produtor."""
    k = chave(status_movimentacao)
    if k.startswith("INCLUSAO"):
        return "ENTRADA"
    if k.startswith("SAIDA") or k.startswith("INATIVACAO"):
        return "SAIDA"
    return None


def consultor_procampo(nome, aliases: dict | None = None) -> str:
    """Nome do consultor do Procampo no BI: maiúsculas, sem acento e com a grafia canônica (config:
    procampo.aliases_consultor), porque base mestra e planilhas de visitas divergem para o mesmo consultor."""
    aliases = aliases or {}
    partes = [chave(p) for p in ("" if nome is None else str(nome)).split("/")]   # grupo "A / B" = co-consultores
    return " / ".join(aliases.get(p, p) for p in partes if p)


def ids_atendimento_procampo(chaves: Iterable[str]) -> dict[str, int]:
    """Número de atendimento estável para visitas do Procampo (a planilha não tem um). Faixa de 10 dígitos
    (1.000.000.000+), acima de qualquer id do SmartQuestion (9 dígitos); colisão resolvida em ordem determinística."""
    import hashlib
    usados: set[int] = set()
    resultado: dict[str, int] = {}
    for k in sorted(set(chaves)):
        n = 1_000_000_000 + int(hashlib.md5(k.encode("utf-8")).hexdigest()[:10], 16) % 9_000_000_000
        while n in usados:
            n += 1
        usados.add(n)
        resultado[k] = n
    return resultado


# ─── Consistência Elabore ─────────────────────────────────────────────────────

def classificar_consistencia(valor) -> str:
    k = chave(valor)
    if not k or "SEM DADOS" in k or "SEM_DADOS" in k or "NAO CALCULADO" in k or "PENDENTE" in k:
        return SITUACAO_SEM_DADOS
    if "INCONSISTENTE" in k:
        return SITUACAO_INCONSISTENTE
    if "OUTLIER" in k:
        return SITUACAO_OUTLIER
    if "DIVERG" in k:
        return SITUACAO_DIVERGENTE
    if "CONSISTENTE" in k:
        return SITUACAO_CONSISTENTE
    return str(valor).strip()


def tem_dados_consistencia(situacao: str) -> bool:
    return situacao != SITUACAO_SEM_DADOS


def rotulo_dados_elabore(pct: int) -> str:
    return f"SIM ({pct}%)" if pct > 0 else "NÃO (0%)"


# ─── Status de visita (tabela "Produtores sem visita") ───────────────────────

def classificar_status_sem_visita(inativacao_pendente: bool, dias_sem_visita: int | None,
                                  tem_visita_anterior: bool, dias_desde_associacao: int | None) -> tuple[str, str]:
    """Retorna (rótulo, classe CSS do badge) para um produtor da carteira sem visita no mês."""
    if inativacao_pendente:
        return "Inativação Pendente", "badge-danger"
    if not tem_visita_anterior:
        if dias_desde_associacao is not None and dias_desde_associacao <= 45:
            return "Vínculo Recente", "badge-positive"
        return "Nunca visitado", "badge-danger"
    if dias_sem_visita is None:
        return "Sem visita no período", "badge-warning"
    if dias_sem_visita >= 60:
        return "Sem visita > 60 dias", "badge-danger"
    if dias_sem_visita >= 45:
        return "Sem visita > 45 dias", "badge-warning"
    if dias_sem_visita >= 30:
        return "Sem visita > 30 dias", "badge-warning"
    if dias_sem_visita <= 0:
        return "Vínculo Recente", "badge-positive"
    return f"Sem visita ({dias_sem_visita}d)", "badge-warning"


# ─── Produção e qualidade do leite (tab_fato_producao_mensal) ────────────────

def produtividade_l_vaca_dia(producao_l_dia, vacas_lactacao) -> float | None:
    """Litros por vaca em lactação por dia; None quando faltar volume ou vacas."""
    try:
        p, v = float(producao_l_dia), float(vacas_lactacao)
    except (TypeError, ValueError):
        return None
    if p != p or v != v or v <= 0:
        return None
    return p / v


def producao_suspeita(producao_l_dia, vacas_lactacao, mediana_do_produtor,
                      produtividade_min: float, produtividade_max: float, fator_salto: float) -> bool:
    """Registro de produção suspeito: produtividade fora da faixa plausível ou volume que salta
    `fator_salto` vezes acima/abaixo da mediana do próprio produtor. Limites em config.yaml (producao:)."""
    try:
        p = float(producao_l_dia)
    except (TypeError, ValueError):
        return False
    if p != p or p <= 0:
        return False
    prod = produtividade_l_vaca_dia(p, vacas_lactacao)
    if prod is not None and not (produtividade_min <= prod <= produtividade_max):
        return True
    if mediana_do_produtor and mediana_do_produtor > 0 and fator_salto > 1:
        razao = p / mediana_do_produtor
        if razao >= fator_salto or razao <= 1 / fator_salto:
            return True
    return False


# ─── Localização das fazendas (tab_dim_fazenda_localizacao) ──────────────────

# Caixa aproximada (lat_min, lat_max, lon_min, lon_max) de cada UF. Serve só para descartar coordenadas
# que caem fora do estado informado no cadastro (lat/long invertidas, sinal perdido, zero etc.).
_CAIXA_UF = {
    "AC": (-11.15, -7.11, -73.99, -66.62), "AL": (-10.50, -8.81, -38.24, -35.15), "AM": (-9.82, 2.25, -73.80, -56.10),
    "AP": (-1.24, 4.44, -54.88, -49.87), "BA": (-18.35, -8.53, -46.62, -37.34), "CE": (-7.86, -2.78, -41.42, -37.25),
    "DF": (-16.05, -15.50, -48.29, -47.31), "ES": (-21.30, -17.89, -41.88, -39.68), "GO": (-19.50, -12.39, -53.25, -45.91),
    "MA": (-10.26, -1.04, -48.76, -41.79), "MG": (-22.92, -14.23, -51.05, -39.86), "MS": (-24.07, -17.17, -58.17, -50.92),
    "MT": (-18.04, -7.35, -61.63, -50.22), "PA": (-9.84, 2.59, -58.90, -46.06), "PB": (-8.30, -6.02, -38.77, -34.79),
    "PE": (-9.48, -7.27, -41.36, -34.81), "PI": (-10.93, -2.74, -45.99, -40.37), "PR": (-26.72, -22.52, -54.62, -48.02),
    "RJ": (-23.37, -20.76, -44.89, -40.96), "RN": (-6.98, -4.83, -38.58, -34.97), "RO": (-13.69, -7.97, -66.81, -59.77),
    "RR": (-1.58, 5.27, -64.83, -58.89), "RS": (-33.75, -27.08, -57.65, -49.69), "SC": (-29.35, -25.96, -53.84, -48.55),
    "SE": (-11.57, -9.51, -38.25, -36.39), "SP": (-25.31, -19.78, -53.11, -44.16), "TO": (-13.47, -5.17, -50.74, -45.70),
}
_CAIXA_BRASIL = (-34.0, 5.5, -74.0, -33.5)
_TOLERANCIA_UF = 0.3   # graus (~33 km): cadastro de município na divisa e caixa aproximada


_UF_POR_NOME = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA", "CEARA": "CE", "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA", "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS",
    "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE", "PIAUI": "PI",
    "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR",
    "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE", "TOCANTINS": "TO",
}


def sigla_uf(valor) -> str | None:
    """Sigla de 2 letras da UF ('GO', ' mg ' -> 'MG', 'Minas Gerais' -> 'MG'); None quando não for uma UF conhecida."""
    s = "" if valor is None else str(valor).strip().upper()
    if s in _CAIXA_UF:
        return s
    return _UF_POR_NOME.get(chave(s))


def _dentro(lat: float, lon: float, caixa: tuple, tol: float = 0.0) -> bool:
    return caixa[0] - tol <= lat <= caixa[1] + tol and caixa[2] - tol <= lon <= caixa[3] + tol


def sanear_coordenada(latitude, longitude, uf=None) -> tuple[float, float, bool] | None:
    """Coordenada utilizável (lat, lon, foi_corrigida) ou None.

    Tenta, nesta ordem: como veio; latitude/longitude trocadas; sinal perdido (o Brasil inteiro tem longitude
    negativa; o sinal da latitude é decidido pela caixa da UF); trocadas + sinal. A primeira que cair dentro da
    UF informada (ou do Brasil, se a UF for desconhecida) vale. Zero, vazio e fora de faixa viram None.
    """
    try:
        a, b = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if a != a or b != b or (abs(a) < 0.01 and abs(b) < 0.01) or abs(a) > 180 or abs(b) > 180:
        return None
    sigla = sigla_uf(uf)
    caixa, tol = (_CAIXA_UF[sigla], _TOLERANCIA_UF) if sigla else (_CAIXA_BRASIL, 0.0)
    candidatas: list[tuple[float, float, bool]] = [(a, b, False), (b, a, True)]
    for x, y in ((a, b), (b, a)):
        for sinal_lat in (-1, 1):
            candidatas.append((sinal_lat * abs(x), -abs(y), True))
    for lat, lon, corrigida in candidatas:
        if _dentro(lat, lon, caixa, tol):
            return round(lat, 6), round(lon, 6), corrigida
    return None
