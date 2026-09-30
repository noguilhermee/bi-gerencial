// Indicadores de safra (slide 7): sistema de plantio, cobertura do solo, produtividade e custo de produção.
//
// Lê tab_fato_safra_forrageira (já resolvida pelo ETL em camada_consumo.py: sistema de plantio, cobertura, produções
// consistentes, custo nominal por etapa, agroindústria/região/consultor da carteira). Aqui só há agregação — nenhuma
// correção de dado (regra 12 do AGENTS.md).
//
// Convenções de cálculo (soma/soma, cada área no seu próprio sistema de plantio):
//  • Participação do sistema = Σ área plantada do sistema ÷ Σ área plantada (cultura e ciclo escolhidos).
//  • Cobertura = Σ área que recebeu cobertura depois ÷ Σ área plantada; produtores = fazendas com alguma área coberta.
//  • Produtividade = Σ toneladas ÷ Σ área colhida; R$/ha = Σ custo ÷ Σ área colhida; R$/t = Σ custo ÷ Σ toneladas.
//  • Produtor com áreas em dois sistemas conta nos dois: os "n produtores" por sistema não somam o total.
//  • Percentuais exibidos são arredondados para somar 100 % (maior resto).
const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, parseFilters, rowMatchesFilters } = require('./shared');

const SISTEMAS = ['PLANTIO DIRETO', 'CULTIVO MINIMO', 'CONVENCIONAL'];
const CICLO_PADRAO = '1_SAFRA';
const CULTURA_PADRAO = 'MILHO';
const COBERTURA_SAFRA_PADRAO = 0.5;
const ETAPAS = ['custo_pre_plantio', 'custo_plantio', 'custo_tratos', 'custo_colheita', 'custo_etapa_nao_informada'];

const n = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? 0 : Number(v));
const soma = (lista, campo) => lista.reduce((acc, r) => acc + n(r[campo]), 0);
const arred = (v, casas = 1) => (v === null || v === undefined || !Number.isFinite(v) ? null : Number(v.toFixed(casas)));
const div = (a, b) => (b > 0 ? a / b : null);
const fazendas = (lista) => new Set(lista.map((r) => r.codigo_lr)).size;

/** Percentuais inteiros que somam 100 (método do maior resto). */
function percentuais(valores) {
  const total = valores.reduce((a, b) => a + b, 0);
  if (!total) return valores.map(() => 0);
  const brutos = valores.map((v) => (v / total) * 100);
  const base = brutos.map(Math.floor);
  let resto = 100 - base.reduce((a, b) => a + b, 0);
  brutos.map((v, i) => [v - base[i], i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => {
    if (resto > 0 && valores[i] > 0) { base[i] += 1; resto -= 1; }
  });
  return base;
}

/** Safra "AA/AA" -> número para ordenar (24/25 < 25/26). */
const ordemSafra = (s) => Number(String(s).slice(0, 2)) || 0;

function escolher(opcoes, pedido, padrao) {
  if (pedido && opcoes.includes(pedido)) return pedido;
  if (padrao && opcoes.includes(padrao)) return padrao;
  return opcoes[0] || null;
}

module.exports = async (req, res) => {
  try {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept');
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'OPTIONS') return res.status(200).end();

    const supabase = getSupabaseClient();
    const filters = parseFilters(req.query);

    let todas;
    try {
      todas = await fetchWithCache('FATO_SAFRA_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_safra_forrageira').select(COLUNAS.safra), undefined, 'id_composto'));
    } catch (error) {
      // Tabela ainda não criada/carregada (migration + carga + camada_consumo.py pendentes): a tela mostra o aviso.
      console.warn('tab_fato_safra_forrageira indisponível:', error.message);
      return res.status(200).json({ disponivel: false, motivo: 'tab_fato_safra_forrageira indisponível' });
    }

    const linhas = todas.filter((r) => rowMatchesFilters(r, { ...filters, month: '', status: '' }) && r.ciclo === CICLO_PADRAO);
    if (!linhas.length) return res.status(200).json({ disponivel: true, semDados: true });

    // ── Recorte: safra, cultura e produto (opções derivadas do que existe para os filtros) ─────────────
    // Safra padrão: a mais recente com pelo menos metade das fazendas com custo da safra de maior cobertura
    // (a safra em andamento, com poucas colheitas lançadas, não abre a tela).
    const comCusto = linhas.filter((r) => r.bloco === 'CUSTO' && n(r.n_producoes) > 0);
    const safras = [...new Set(linhas.map((r) => r.safra))].sort((a, b) => ordemSafra(b) - ordemSafra(a));
    const fazendasPorSafra = new Map();
    comCusto.forEach((r) => {
      if (!fazendasPorSafra.has(r.safra)) fazendasPorSafra.set(r.safra, new Set());
      fazendasPorSafra.get(r.safra).add(r.codigo_lr);
    });
    const maiorCobertura = Math.max(0, ...[...fazendasPorSafra.values()].map((s) => s.size));
    const safraPadrao = safras.find((s) => (fazendasPorSafra.get(s)?.size || 0) >= maiorCobertura * COBERTURA_SAFRA_PADRAO && maiorCobertura > 0);
    const safra = escolher(safras, String(req.query?.safra || ''), safraPadrao);
    const daSafra = linhas.filter((r) => r.safra === safra);

    const areaPorCultura = new Map();
    daSafra.filter((r) => r.bloco === 'AREA').forEach((r) => areaPorCultura.set(r.cultura, (areaPorCultura.get(r.cultura) || 0) + n(r.area_plantada_ha)));
    const culturas = [...new Set(daSafra.map((r) => r.cultura))].sort((a, b) => (areaPorCultura.get(b) || 0) - (areaPorCultura.get(a) || 0));
    const cultura = escolher(culturas, String(req.query?.cultura || '').toUpperCase(), CULTURA_PADRAO);
    const area = daSafra.filter((r) => r.bloco === 'AREA' && r.cultura === cultura);
    const custoCultura = daSafra.filter((r) => r.bloco === 'CUSTO' && r.cultura === cultura);

    const areaPorProduto = new Map();
    custoCultura.forEach((r) => areaPorProduto.set(r.produto, (areaPorProduto.get(r.produto) || 0) + n(r.area_colhida_ha)));
    const produtos = [...areaPorProduto.keys()].filter(Boolean).sort((a, b) => areaPorProduto.get(b) - areaPorProduto.get(a));
    const produto = escolher(produtos, String(req.query?.produto || '').toUpperCase(), null);
    const custo = custoCultura.filter((r) => r.produto === produto);

    // ── Sistema de plantio (participação na área) ────────────────────────────────────────────────
    const areaTotal = soma(area, 'area_plantada_ha');
    const areasSistema = SISTEMAS.map((s) => soma(area.filter((r) => r.sistema === s), 'area_plantada_ha'));
    const pctSistema = percentuais(areasSistema);
    const areaNaoInformada = soma(area.filter((r) => !SISTEMAS.includes(r.sistema)), 'area_plantada_ha');
    const sistema = {
      areaTotal: arred(areaTotal, 1),
      areaNaoInformada: arred(areaNaoInformada, 1),
      fazendas: fazendas(area),
      itens: SISTEMAS.map((s, i) => ({
        sistema: s,
        area: arred(areasSistema[i], 1),
        pct: pctSistema[i],
        fazendas: fazendas(area.filter((r) => r.sistema === s))
      })),
      // % conservacionista = Plantio Direto + Cultivo Mínimo sobre a área com sistema informado
      conservacionistaPct: areasSistema.some((v) => v > 0) ? pctSistema[0] + pctSistema[1] : null
    };

    // ── Cobertura do solo ────────────────────────────────────────────────────────────────────────
    const areaCoberta = soma(area, 'area_com_cobertura_ha');
    const [pctCom, pctSem] = percentuais([areaCoberta, Math.max(0, areaTotal - areaCoberta)]);
    const cobertura = {
      areaTotal: arred(areaTotal, 1),
      areaCom: arred(areaCoberta, 1),
      areaSem: arred(Math.max(0, areaTotal - areaCoberta), 1),
      pctCom: areaTotal > 0 ? pctCom : null,
      pctSem: areaTotal > 0 ? pctSem : null,
      fazendas: fazendas(area),
      fazendasCom: fazendas(area.filter((r) => n(r.area_com_cobertura_ha) > 0))
    };

    // ── Produtividade e custo por sistema ──────────────────────────────────────────────────────────
    const porSistema = SISTEMAS.map((s) => {
      const doSistema = custo.filter((r) => r.sistema === s && n(r.n_producoes) > 0);
      const ha = soma(doSistema, 'area_colhida_ha');
      const t = soma(doSistema, 'toneladas');
      const rs = soma(doSistema, 'custo_total');
      const etapas = {};
      ETAPAS.forEach((e) => { etapas[e.replace('custo_', '')] = arred(div(soma(doSistema, e), ha), 0); });
      return {
        sistema: s,
        fazendas: fazendas(doSistema),
        areaColhida: arred(ha, 1),
        toneladas: arred(t, 0),
        tHa: arred(div(t, ha), 1),
        rsHa: arred(div(rs, ha), 0),
        rsT: arred(div(rs, t), 0),
        etapas
      };
    });
    const comCustoRecorte = custo.filter((r) => n(r.n_producoes) > 0);
    const porFazenda = new Map();
    comCustoRecorte.forEach((r) => porFazenda.set(r.codigo_lr, (porFazenda.get(r.codigo_lr) || 0) + n(r.custo_pre_plantio)));

    return res.status(200).json({
      disponivel: true,
      safra,
      safras,
      cultura,
      culturas,
      produto,
      produtos,
      ciclo: CICLO_PADRAO,
      sistema,
      cobertura,
      porSistema,
      rodape: {
        fazendasComCusto: fazendas(comCustoRecorte),
        fazendasSemPrePlantio: [...porFazenda.values()].filter((v) => v <= 0).length,
        producoesInconsistentes: soma(custo, 'producoes_inconsistentes'),
        fontes: [...new Set(area.map((r) => r.fonte).filter(Boolean))]
      }
    });
  } catch (error) {
    console.error('Erro em /api/safra:', error);
    return res.status(500).json({ error: 'Falha ao consolidar os indicadores de safra', details: error.message });
  }
};
