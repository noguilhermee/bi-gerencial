const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, parseFilters, rowMatchesFilters, obterEscopo } = require('../_shared');
const { comAuth } = require('../_auth');

module.exports = comAuth(async (req, res) => {
  try {

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);
    const filters = parseFilters(req.query);
    const isAllMonths = !/^\d{4}-\d{2}-\d{2}$/.test(filters.month);
    const refMonth = isAllMonths ? null : filters.month;

    const [carteiraTodosBruta, visitasTodasBruta] = await Promise.all([
      fetchWithCache('CARTEIRA_MENSAL_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto')),
      fetchWithCache('FATO_VISITAS_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_visitas').select(COLUNAS.visitas).order('data_visita', { ascending: false }), undefined, 'id_atendimento'))
    ]);
    const carteiraTodos = escopo.carteira(carteiraTodosBruta);
    const visitasTodas = escopo.fatos(visitasTodasBruta);
    const carteiraMes = refMonth ? carteiraTodos.filter(r => String(r.mes_referencia).slice(0, 10) === refMonth) : carteiraTodos;
    const carteiraFiltrada = carteiraMes.filter(r => rowMatchesFilters({ ...r, status: r.status_visita }, filters));

    const visitasFiltradas = visitasTodas
      .filter(v => !refMonth || String(v.mes_referencia).slice(0, 10) === refMonth)
      .filter(v => rowMatchesFilters({ ...v, status: v.status_produtor }, filters));
    const visitasPorConsultor = new Map();
    visitasFiltradas.forEach(v => visitasPorConsultor.set(v.nome_consultor, (visitasPorConsultor.get(v.nome_consultor) || 0) + 1));

    const consultoresMap = new Map();
    carteiraFiltrada.forEach(r => {
      const c = r.consultor || 'NAO ATRIBUIDO';
      if (!consultoresMap.has(c)) {
        consultoresMap.set(c, {
          consultor: c, totalFarms: new Set(), visitedFarms: new Set(),
          industries: new Set(), projects: new Set(), regions: new Set()
        });
      }
      const item = consultoresMap.get(c);
      item.totalFarms.add(r.codigo_lr);
      if (r.visitado_mes) item.visitedFarms.add(r.codigo_lr);
      if (r.projeto) item.projects.add(r.projeto);
      if (r.agroindustria) item.industries.add(r.agroindustria);
      if (r.regiao && r.regiao !== 'NAO INFORMADA') item.regions.add(r.regiao);
    });

    const consultoresList = [...consultoresMap.values()].map(c => {
      const total = c.totalFarms.size || 1;
      return {
        consultor: c.consultor,
        total_fazendas: c.totalFarms.size,
        fazendas_visitadas: c.visitedFarms.size,
        total_visitas: visitasPorConsultor.get(c.consultor) || 0,
        perc_cobertura: Number(((c.visitedFarms.size / total) * 100).toFixed(1)),
        agroindustrias: [...c.industries],
        regioes: [...c.regions],
        projetos: [...c.projects],
        status: 'ATIVO',
        mes_referencia: refMonth
      };
    }).sort((a, b) => b.perc_cobertura - a.perc_cobertura || b.total_visitas - a.total_visitas);

    const totalAtivos = new Set(carteiraFiltrada.map(r => r.codigo_lr)).size;
    const totalVisitas = visitasFiltradas.length;
    const visitadosUnicos = new Set(carteiraFiltrada.filter(r => r.visitado_mes).map(r => r.codigo_lr)).size;

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      refMonth,
      kpis: {
        perc_cobertura_geral: totalAtivos > 0 ? ((visitadosUnicos / totalAtivos) * 100).toFixed(1) : '0.0',
        total_visitas: totalVisitas,
        media_visitas_consultor: (totalVisitas / (consultoresList.length || 1)).toFixed(1),
        fazendas_nao_visitadas: totalAtivos - visitadosUnicos
      },
      rankingConsultores: {
        labels: consultoresList.map(c => c.consultor),
        coberturas: consultoresList.map(c => c.perc_cobertura),
        visitas: consultoresList.map(c => c.total_visitas)
      },
      tabelaConsultores: consultoresList
    });
  } catch (error) {
    console.error('Erro em /api/visits:', error);
    return res.status(500).json({ error: error.message });
  }
});
