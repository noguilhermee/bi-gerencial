const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, monthLabel, formatDate, parseFilters, rowMatchesFilters } = require('./shared');

const SEM_DADOS = 'Sem dados';

/** Linha única de consistência: fazenda ativa (carteira) ou fora da carteira com registro Elabore. */
function daCarteira(r) {
  return {
    codigo_lr: r.codigo_lr, nome_produtor: r.nome_produtor, propriedade: r.nome_propriedade, consultor: r.consultor, projeto: r.projeto,
    agroindustria: r.agroindustria, regiao: r.regiao, mes_referencia: r.mes_referencia, status: 'ATIVO',
    consistencia_mensal: r.consistencia_mensal, consistencia_anual: r.consistencia_anual,
    detalhamento_mensal: r.detalhamento_mensal, detalhamento_anual: r.detalhamento_anual,
    meses_sequenciais: r.meses_sequenciais, em_carencia: r.em_carencia, excecao: r.excecao, sem_lancamento_elabore: r.sem_lancamento_elabore,
    cadastro_elabore: r.cadastro_elabore, dados_elabore_pct: r.dados_elabore_pct,
    dados_elabore_status: r.dados_elabore_status, blocos_elabore: r.blocos_elabore
  };
}

function foraDaCarteira(c) {
  return {
    codigo_lr: c.codigo_lr, nome_produtor: c.nome_produtor, consultor: c.nome_consultor, projeto: c.projeto,
    agroindustria: c.agroindustria, regiao: c.regiao, mes_referencia: c.mes_referencia, status: 'INATIVO',
    consistencia_mensal: c.consistencia_mensal, consistencia_anual: c.consistencia_anual,
    detalhamento_mensal: c.detalhamento_inconsistencia, detalhamento_anual: c.detalhamento_anual,
    meses_sequenciais: c.meses_sequenciais, em_carencia: Boolean(c.data_carencia_fim && new Date(c.data_carencia_fim) > new Date()),
    excecao: Boolean(c.excecao), sem_lancamento_elabore: c.sem_lancamento_elabore, cadastro_elabore: c.cadastro_elabore, dados_elabore_pct: c.dados_elabore_pct,
    dados_elabore_status: c.dados_elabore_status, blocos_elabore: c.blocos_elabore
  };
}

function faixaSequencia(seq) {
  if (seq <= 3) return '0-3 meses';
  if (seq <= 6) return '4-6 meses';
  if (seq <= 9) return '7-9 meses';
  if (seq <= 12) return '10-12 meses';
  return '12+ meses';
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

    const [carteiraTodos, consistenciaTodos] = await Promise.all([
      fetchWithCache('CARTEIRA_MENSAL_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto')),
      fetchWithCache('FATO_CONSISTENCIA_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_consistencia').select(COLUNAS.consistencia).order('mes_referencia', { ascending: false }), undefined, 'id_composto'))
    ]);

    const ultimosMeses = [...new Set([...carteiraTodos, ...consistenciaTodos].map(c => String(c.mes_referencia).slice(0, 10)))].filter(Boolean).sort();
    const ultimoMesDisponivel = ultimosMeses.length > 0 ? ultimosMeses[ultimosMeses.length - 1] : null;
    const refMonth = /^\d{4}-\d{2}-\d{2}$/.test(filters.month) ? filters.month : ultimoMesDisponivel;
    const doMes = r => !refMonth || String(r.mes_referencia).slice(0, 10) === refMonth;

    // Base do mês: 1 linha por fazenda (a carteira tem 1 linha por consultor)
    const porFazenda = new Map();
    carteiraTodos.filter(doMes).forEach(r => {
      const chave = `${r.codigo_lr}|${String(r.mes_referencia).slice(0, 10)}`;
      if (!porFazenda.has(chave)) porFazenda.set(chave, daCarteira(r));
    });
    consistenciaTodos.filter(c => doMes(c) && !c.na_carteira).forEach(c => {
      porFazenda.set(`${c.codigo_lr}|${String(c.mes_referencia).slice(0, 10)}`, foraDaCarteira(c));
    });
    // Regra 13 (AGENTS.md): fazendas de consultor que não lança no Elabore ficam fora dos indicadores (flag gravada pelo ETL)
    // Nome da fazenda por código (fora da carteira o fato de consistência não traz a propriedade)
    const fazendaPorCodigo = new Map();
    carteiraTodos.forEach(r => { if (r.nome_propriedade && !fazendaPorCodigo.has(r.codigo_lr)) fazendaPorCodigo.set(r.codigo_lr, r.nome_propriedade); });
    const base = [...porFazenda.values()].filter(r => !r.sem_lancamento_elabore && rowMatchesFilters(r, filters));

    const conta = (campo, valor) => base.filter(r => r[campo] === valor).length;
    // Partição exclusiva da base: fazenda em carência conta como "incompleta" (ainda não é cobrada),
    // nunca também como apta/divergente. Assim aptas + incompletas + divergentes = base analisada.
    const semDados = conta('consistencia_mensal', SEM_DADOS);
    const incompletas = base.filter(r => r.em_carencia || r.consistencia_mensal === SEM_DADOS).length;
    const consistentes = base.filter(r => !r.em_carencia && r.consistencia_mensal === 'Consistente').length;
    const inconsistentes = base.length - consistentes - incompletas;
    const anualConsistentes = conta('consistencia_anual', 'Consistente');
    const anualSemDados = conta('consistencia_anual', SEM_DADOS);
    const anualInconsistentes = base.length - anualConsistentes - anualSemDados;
    const carencia = base.filter(r => r.em_carencia).length;
    const excecoes = base.filter(r => r.excecao).length;
    const pct = (n, d) => ((n / (d || 1)) * 100).toFixed(1);

    const histograma = { '0-3 meses': 0, '4-6 meses': 0, '7-9 meses': 0, '10-12 meses': 0, '12+ meses': 0 };
    base.forEach(r => { histograma[faixaSequencia(Number(r.meses_sequenciais) || 0)]++; });

    // Evolução: registros Elabore de cada mês (fato de consistência), respeitando os filtros
    const historico = consistenciaTodos.filter(c => !c.sem_lancamento_elabore && rowMatchesFilters({ ...c, status: c.na_carteira ? 'ATIVO' : 'INATIVO' }, filters));
    const referencias = [...new Set(historico.map(c => String(c.mes_referencia).slice(0, 10)))].filter(m => !refMonth || m <= refMonth).sort();
    // Taxa = Consistentes ÷ registros avaliados (exclui "Sem dados")
    const taxa = (linhas, campo) => {
      const avaliados = linhas.filter(c => c[campo] !== SEM_DADOS);
      return avaliados.length ? Number(pct(avaliados.filter(c => c[campo] === 'Consistente').length, avaliados.length)) : 0;
    };
    const evolucaoConsistencia = { labels: [], mensal: [], anual: [] };
    referencias.forEach(ref => {
      const doRef = historico.filter(c => String(c.mes_referencia).slice(0, 10) === ref);
      evolucaoConsistencia.labels.push(monthLabel(ref));
      evolucaoConsistencia.mensal.push(taxa(doRef, 'consistencia_mensal'));
      evolucaoConsistencia.anual.push(taxa(doRef, 'consistencia_anual'));
    });
    // Cards fixos no último mês da série (mesma regra e mesma base do gráfico)
    const historicoRef = historico.filter(doMes);
    const percMensalRef = taxa(historicoRef, 'consistencia_mensal').toFixed(1);
    const percAnualRef = taxa(historicoRef, 'consistencia_anual').toFixed(1);

    const tabelaInconsistentes = base.map(r => ({
      codigo_lr: r.codigo_lr,
      produtor: r.nome_produtor,
      propriedade: r.propriedade || fazendaPorCodigo.get(r.codigo_lr) || null,
      consultor: r.consultor,
      agroindustria: r.agroindustria,
      regiao: r.regiao,
      projeto: r.projeto,
      status: r.status,
      mes_referencia: r.mes_referencia,
      meses_sequenciais: r.meses_sequenciais,
      consistencia_mensal: r.consistencia_mensal,
      consistencia_anual: r.consistencia_anual,
      consistencia: r.consistencia_mensal,
      detalhamento: r.detalhamento_mensal,
      consistencia_anual_raw: r.consistencia_anual,
      detalhamento_anual: r.detalhamento_anual
    }));

    const tabelaProdutoresComDados = base.map(r => {
      const temDado = r.dados_elabore_pct > 0;
      const cadastrado = Boolean(r.cadastro_elabore) && !r.excecao;
      return {
        codigo_lr: r.codigo_lr,
        produtor: r.nome_produtor,
        propriedade: r.propriedade || fazendaPorCodigo.get(r.codigo_lr) || null,
        consultor: r.consultor,
        agroindustria: r.agroindustria,
        regiao: r.regiao,
        projeto: r.projeto,
        mes_referencia: r.mes_referencia,
        possui_dados: temDado,
        cadastro_elabore: r.status === 'INATIVO' ? 'INATIVO' : cadastrado,
        cadastro_elabore_label: r.status === 'INATIVO' ? 'INATIVO' : (cadastrado ? 'SIM' : 'NÃO'),
        dados_elabore_pct: r.dados_elabore_pct,
        dados_elabore_status: r.dados_elabore_status,
        dados_elabore_tem_dado: temDado,
        detalhes_blocos: r.blocos_elabore,
        referencia: formatDate(r.mes_referencia),
        status: r.status
      };
    });

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      refMonth,
      mesFiltro: filters.month,
      mesCompetencia: refMonth,
      kpis: {
        perc_consistente: percMensalRef,
        perc_anual: percAnualRef,
        perc_inconsistente: pct(inconsistentes, base.length),
        produtores_com_dados: base.length - semDados,
        fazendas_aptas: consistentes,
        base_analisada: base.length,
        registros_divergentes: inconsistentes,
        produtores_carencia: carencia,
        excecoes_ativas: excecoes
      },
      distribuicaoDonut: { labels: ['Registros aptos', 'Registros incompletos', 'Registros divergentes'], values: [consistentes, incompletas, inconsistentes] },
      distribuicaoDonutAnual: { labels: ['Registros aptos', 'Registros incompletos', 'Registros divergentes'], values: [anualConsistentes, anualSemDados, anualInconsistentes] },
      evolucaoConsistencia,
      histogramaMeses: { labels: Object.keys(histograma), values: Object.values(histograma) },
      tabelaProdutoresComDados,
      tabelaInconsistentes
    });
  } catch (error) {
    console.error('Erro em /api/consistency:', error);
    return res.status(500).json({ error: error.message });
  }
};
