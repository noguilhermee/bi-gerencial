const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, monthLabel, formatDate, parseFilters, rowMatchesFilters, obterEscopo } = require('./shared');
const { comAuth } = require('./_auth');

module.exports = comAuth(async (req, res) => {
  try {

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);
    const filters = parseFilters(req.query);
    const isAllMonths = !/^\d{4}-\d{2}-\d{2}$/.test(filters.month);
    const refMonth = isAllMonths ? null : filters.month;

    const [movimentacaoTodasBruta, carteiraTodosBruta] = await Promise.all([
      fetchWithCache('FATO_MOVIMENTACAO_ALL', () => fetchAll(() => supabase.from('tab_fato_movimentacao').select(COLUNAS.movimentacao).order('data_movimentacao', { ascending: false }), undefined, 'id_composto')),
      fetchWithCache('CARTEIRA_MENSAL_ALL', () => fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto'))
    ]);
    const movimentacaoTodas = escopo.fatos(movimentacaoTodasBruta);
    const carteiraTodos = escopo.carteira(carteiraTodosBruta);

    const movimentacaoFiltrada = movimentacaoTodas.filter(m => rowMatchesFilters(m, filters));
    const carteiraMes = refMonth ? carteiraTodos.filter(r => String(r.mes_referencia).slice(0, 10) === refMonth) : carteiraTodos;
    const carteiraFiltrada = carteiraMes.filter(r => rowMatchesFilters({ ...r, status: r.status_visita }, filters));

    const totalAtivos = new Set(carteiraFiltrada.map(r => r.codigo_lr)).size;
    const totalConsultores = new Set(carteiraFiltrada.map(r => r.consultor)).size;

    const currentMonthKey = refMonth ? refMonth.slice(0, 7) : null;
    const movimentacoesDoMes = currentMonthKey
      ? movimentacaoFiltrada.filter(m => String(m.data_movimentacao || '').slice(0, 7) === currentMonthKey)
      : movimentacaoFiltrada;

    let entradas = 0, saidas = 0;
    const motivosMap = {};
    movimentacoesDoMes.forEach(m => {
      if (m.tipo === 'ENTRADA') entradas++;
      else {
        saidas++;
        motivosMap[m.motivo] = (motivosMap[m.motivo] || 0) + 1;
      }
    });

    const tabelaMov = movimentacoesDoMes.map(m => ({
      numero_atendimento: m.numero_atendimento,
      atendimento: m.numero_atendimento ? String(m.numero_atendimento).replace(/\.0+$/, '') : '—',
      produtor: m.nome_produtor,
      consultor: m.consultor,
      grupo: m.consultor,
      agroindustria: m.agroindustria,
      regiao: m.regiao,
      projeto: m.projeto,
      status: m.tipo === 'SAIDA' ? 'INATIVO' : 'ATIVO',
      mes_referencia: `${String(m.data_movimentacao).slice(0, 7)}-01`,
      tipo: m.tipo,
      data: formatDate(m.data_solicitacao),
      motivo: m.motivo
    }));

    const referencias = [...new Set(carteiraTodos.map(r => String(r.mes_referencia).slice(0, 10)))].filter(ref => !refMonth || ref <= refMonth).sort();
    const movimentosPorMes = new Map();
    movimentacaoFiltrada.forEach(m => {
      const key = String(m.data_movimentacao || '').slice(0, 7);
      if (!key) return;
      if (!movimentosPorMes.has(key)) movimentosPorMes.set(key, { entradas: 0, saidas: 0 });
      const item = movimentosPorMes.get(key);
      if (m.tipo === 'SAIDA') item.saidas += 1; else item.entradas += 1;
    });
    const historicoMovimentacao = {
      labels: referencias.map(monthLabel),
      entradas: referencias.map(ref => movimentosPorMes.get(ref.slice(0, 7))?.entradas || 0),
      saidas: referencias.map(ref => movimentosPorMes.get(ref.slice(0, 7))?.saidas || 0),
      porcentagens: referencias.map(ref => {
        const item = movimentosPorMes.get(ref.slice(0, 7)) || { entradas: 0, saidas: 0 };
        const total = item.entradas + item.saidas;
        return total > 0 ? Number(((item.saidas / total) * 100).toFixed(1)) : 0;
      })
    };

    const carteiraPorMes = new Map();
    carteiraTodos.filter(r => rowMatchesFilters({ ...r, status: r.status_visita }, { ...filters, month: '' })).forEach(r => {
      const key = String(r.mes_referencia).slice(0, 10);
      if (!carteiraPorMes.has(key)) carteiraPorMes.set(key, new Set());
      carteiraPorMes.get(key).add(r.codigo_lr);
    });

    const topMotivos = Object.entries(motivosMap).sort((a, b) => b[1] - a[1]).slice(0, 5);

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      refMonth,
      kpis: {
        entradas_mes: entradas,
        saidas_mes: saidas,
        saldo: entradas - saidas,
        taxa_churn: ((saidas / (totalAtivos || 1)) * 100).toFixed(1),
        produtores_ativos: totalAtivos,
        consultores_ativos: totalConsultores
      },
      historicoMovimentacao,
      historicoCarteira: { labels: referencias.map(monthLabel), values: referencias.map(ref => carteiraPorMes.get(ref)?.size || 0) },
      motivosInativacao: { labels: topMotivos.map(m => m[0]), values: topMotivos.map(m => m[1]) },
      tabelaMovimentacao: tabelaMov
    });
  } catch (error) {
    console.error('Erro em /api/turnover:', error);
    return res.status(500).json({ error: error.message });
  }
});
