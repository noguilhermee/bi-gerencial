/**
 * Controlador da interface e integração somente leitura com as APIs do dashboard.
 */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const themeToggle = document.getElementById('themeToggle');
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const carousel = new DashboardCarousel({ slideDuration: 30000 });
  const charts = new DashboardCharts();
  const state = {
    overview: null,
    visits: null,
    turnover: null,
    consistency: null,
    rankingDimension: 'consultant',
    hasUserChangedMonth: false,
    chartHorizons: {
      coverage: 12,
      turnover: 6,
      portfolio: 6,
      consistency: 6,
      volume: 6,
      visits: 12
    }
  };

  function sliceTimeSeries(seriesObj, monthsCount) {
    if (!seriesObj) return seriesObj;
    if (Array.isArray(seriesObj)) {
      if (!monthsCount || monthsCount <= 0 || monthsCount >= seriesObj.length) return seriesObj;
      return seriesObj.slice(Math.max(0, seriesObj.length - monthsCount));
    }
    if (!seriesObj.labels || !seriesObj.labels.length) return seriesObj;
    if (!monthsCount || monthsCount <= 0 || monthsCount >= seriesObj.labels.length) return seriesObj;
    const startIdx = Math.max(0, seriesObj.labels.length - monthsCount);
    const sliced = {};
    for (const [k, v] of Object.entries(seriesObj)) {
      if (Array.isArray(v)) {
        sliced[k] = v.slice(startIdx);
      } else {
        sliced[k] = v;
      }
    }
    return sliced;
  }
  const projectOptions = [
    { value: 'ALVOAR ASSIST', label: 'Alvoar Assist' },
    { value: 'ALVOAR ECO', label: 'Alvoar Eco' },
    { value: 'ATEG_CCPR', label: 'Ateg_Ccpr' },
    { value: 'LPA', label: 'Lpa' },
    { value: 'REGENERA', label: 'Regenera' },
    { value: 'SEMEAR', label: 'Semear' }
  ];

  const emptyState = {
    overview: {
      refMonth: null,
      kpis: {},
      evolucaoMensal: { labels: [], fazendasAtivas: [], fazendasVisitadas: [], percCobertura: [] },
      evolucaoVisitas: { labels: [], values: [], variacoes: [] },
      rankingProdutores: { labels: [], values: [] },
      filterOptions: { agroindustrias: [], regioes: [], projetos: projectOptions.map((p) => p.value), status: ['ATIVO', 'INATIVO'], meses: [] },
      tabelas: { movimentacao: [], sem_visita: [], visitados: [] }
    },
    visits: {
      kpis: {},
      rankingConsultores: { labels: [], coberturas: [], visitas: [] },
      tabelaConsultores: []
    },
    turnover: {
      kpis: {},
      historicoMovimentacao: { labels: [], entradas: [], saidas: [] },
      historicoCarteira: { labels: [], values: [] },
      tabelaMovimentacao: []
    },
    consistency: {
      kpis: {},
      evolucaoConsistencia: { labels: [], mensal: [], anual: [] },
      distribuicaoDonut: { labels: ['Registros aptos', 'Registros incompletos', 'Registros divergentes'], values: [0, 0, 0] },
      distribuicaoDonutAnual: { labels: ['Registros aptos', 'Registros incompletos', 'Registros divergentes'], values: [0, 0, 0] },
      tabelaProdutoresComDados: [],
      tabelaInconsistentes: []
    }
  };

  const el = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '—').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const number = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('pt-BR') : '—';
  const percent = (value) => value === null || value === undefined || value === '' ? '—' : `${String(value).replace('.', ',')}%`;
  const date = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString('pt-BR');
  };

  function isStatusInconsistente(statusStr) {
    if (!statusStr) return false;
    const normalized = String(statusStr)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toUpperCase();
    if (normalized.includes('SEM DADOS') || normalized.includes('NAO CALCULADO') || normalized.includes('PENDENTE')) {
      return false;
    }
    return normalized.includes('INCONSIST') || normalized.includes('DIVERG') || normalized.includes('OUTLIER');
  }

  function isStatusConsistente(statusStr) {
    if (!statusStr) return false;
    const normalized = String(statusStr)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toUpperCase();
    return normalized.includes('CONSIST') && !normalized.includes('INCONSIST');
  }

  function updateValue(id, value) {
    const node = el(id);
    if (!node) return;
    const text = String(value ?? '—');
    if (node.textContent !== text) {
      node.textContent = text;
      node.classList.remove('pulse-update');
      void node.offsetWidth;
      node.classList.add('pulse-update');
    }
  }

  function setTheme(theme, persist = true) {
    root.dataset.theme = theme === 'dark' ? 'dark' : 'light';
    const isDark = root.dataset.theme === 'dark';
    const labelText = `Ativar modo ${isDark ? 'claro' : 'escuro'}`;
    themeToggle?.setAttribute('aria-pressed', String(isDark));
    themeToggle?.setAttribute('aria-label', labelText);
    themeToggle?.setAttribute('title', labelText);
    const themeIcon = themeToggle?.querySelector('.theme-icon');
    if (themeIcon) themeIcon.textContent = isDark ? 'light_mode' : 'dark_mode';
    const label = themeToggle?.querySelector('.theme-toggle-label');
    if (label) label.textContent = `Modo ${isDark ? 'claro' : 'escuro'}`;
    if (themeMeta) themeMeta.content = isDark ? '#060E0D' : '#ffffff';
    if (persist) {
      try { localStorage.setItem('lr-dashboard-theme', root.dataset.theme); } catch (_) { /* preferência opcional */ }
    }
    charts.applyTheme();
  }

  setTheme(root.dataset.theme, false);
  themeToggle?.addEventListener('click', () => setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'));

  async function getJson(url) {
    try {
      const response = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      console.error(`${url} indisponível.`, error);
      return null;
    }
  }

  function renderOverview(data) {
    if (!data) return;
    const kpi = data.kpis || {};
    const totalActive = Number(kpi.produtores_ativos) || 0;
    const totalVisited = Number(kpi.produtores_visitados) || Math.max(0, totalActive - Number(state.visits?.kpis?.fazendas_nao_visitadas || 0));
    updateValue('kpiVisitsTotal', number(kpi.total_visitas));
    updateValue('kpiVisitsActive', number(kpi.produtores_ativos));
    updateValue('kpiVisitsVisited', number(totalVisited));
    updateValue('kpiVisitsCoverage', percent(kpi.perc_visitados));
    updateValue('kpiVisitsPerProducer', String(kpi.visitas_por_produtor || '—').replace('.', ','));
    updateValue('kpiVisitsMissing', number(Math.max(0, totalActive - totalVisited)));

    updateValue('kpiTurnActive', number(kpi.produtores_ativos));
    updateValue('kpiDataProducers', number(kpi.produtores_com_dados));
    updateValue('kpiDataEligible', number(state.consistency?.kpis?.fazendas_aptas ?? kpi.produtores_com_dados));

    const coverageData = sliceTimeSeries(data.evolucaoMensal || emptyState.overview.evolucaoMensal, state.chartHorizons.coverage);
    charts.renderCoverage('chartVisitsCoverage', coverageData);
    renderVisitsEvolution();
    renderSelectedRanking();
  }

  function renderVisitsEvolution() {
    const series = sliceTimeSeries(state.overview?.evolucaoVisitas || emptyState.overview.evolucaoVisitas, state.chartHorizons.visits);
    charts.renderVisitsEvolution('chartVisitsEvolution', { labels: series.labels, values: series.values, variations: series.variacoes });
  }

  function renderSelectedRanking() {
    const isConsultant = state.rankingDimension === 'consultant';
    const producerRanking = state.overview?.rankingProdutores || emptyState.overview.rankingProdutores;
    const consultantSource = state.visits?.rankingConsultores || emptyState.visits.rankingConsultores;
    let rawRanking = isConsultant
      ? { labels: consultantSource.labels || [], values: consultantSource.visitas || [] }
      : { labels: producerRanking.labels || [], values: producerRanking.values || [] };

    // Ordenação estrita do MAIOR para o MENOR valor
    const paired = (rawRanking.labels || []).map((label, idx) => ({
      label,
      value: Number(rawRanking.values?.[idx]) || 0
    })).filter((item) => item.value > 0);
    paired.sort((a, b) => b.value - a.value);

    // Limita aos Top 30 para manter o Canvas em alta performance (60fps) e evitar travamento da GPU do navegador
    const maxRankingItems = isConsultant ? 40 : 30;
    const topPaired = paired.slice(0, maxRankingItems);

    const ranking = {
      labels: topPaired.map((item) => item.label),
      values: topPaired.map((item) => item.value)
    };

    const viewport = el('rankingChartViewport');
    const inner = el('rankingChartInner');
    if (viewport && inner) {
      const availableHeight = Math.max(viewport.clientHeight || 0, 170);
      const computedHeight = Math.min(Math.max(availableHeight, ranking.labels.length * 28 + 8), 900);
      inner.style.height = `${computedHeight}px`;
      viewport.scrollTop = 0;
    }
    charts.renderRanking('chartVisitsRanking', ranking);
    if (el('rankingSubtitle')) el('rankingSubtitle').textContent = `Ranking por ${isConsultant ? 'consultor' : 'produtor'} (Top ${topPaired.length})`;
    document.querySelectorAll('[data-ranking]').forEach((button) => {
      const active = button.dataset.ranking === state.rankingDimension;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-ranking-pane]').forEach((pane) => {
      pane.hidden = pane.dataset.rankingPane !== state.rankingDimension;
    });
    renderRankingTables();
  }

  function renderVisits(data) {
    if (!data) return;
    const kpi = data.kpis || {};
    updateValue('kpiVisitsCoverage', percent(kpi.perc_cobertura_geral || state.overview?.kpis?.perc_visitados));
    updateValue('kpiVisitsTotal', number(kpi.total_visitas || state.overview?.kpis?.total_visitas));
    updateValue('kpiVisitsMissing', number(kpi.fazendas_nao_visitadas));
    updateValue('kpiTurnConsultants', number(data.tabelaConsultores?.length || state.overview?.kpis?.consultores_ativos));
  }

  function renderTurnover(data) {
    if (!data) return;
    const kpi = data.kpis || {};
    updateValue('kpiTurnEntradas', number(kpi.entradas_mes));
    updateValue('kpiTurnSaidas', number(kpi.saidas_mes));
    updateValue('kpiTurnSaldo', Number(kpi.saldo) >= 0 ? `+${number(kpi.saldo)}` : number(kpi.saldo));
    updateValue('kpiTurnChurn', percent(kpi.taxa_churn));
    const turnoverData = sliceTimeSeries(data.historicoMovimentacao || emptyState.turnover.historicoMovimentacao, state.chartHorizons.turnover);
    charts.renderTurnover('chartTurnoverHistory', turnoverData);
    const portfolioData = sliceTimeSeries(data.historicoCarteira || emptyState.turnover.historicoCarteira, state.chartHorizons.portfolio);
    charts.renderPortfolio('chartPortfolioHistory', portfolioData);
  }

  function renderConsistency(data) {
    if (!data) return;
    const kpi = data.kpis || {};
    const values = data.distribuicaoDonut?.values || [];
    const base = Number(kpi.base_analisada) || values.reduce((sum, item) => sum + Number(item || 0), 0);
    const divergences = Number(kpi.registros_divergentes ?? values[2] ?? 0);
    updateValue('kpiDataProducers', number(kpi.produtores_com_dados ?? state.overview?.kpis?.produtores_com_dados));
    updateValue('kpiDataEligible', number(kpi.fazendas_aptas ?? kpi.produtores_com_dados ?? state.overview?.kpis?.produtores_com_dados));
    updateValue('kpiDataMonthly', percent(kpi.perc_consistente));
    updateValue('kpiDataAnnual', percent(kpi.perc_anual));
    updateValue('kpiDataDivergent', number(divergences));
    updateValue('kpiDataBase', number(base));
    const consistencyData = sliceTimeSeries(data.evolucaoConsistencia || emptyState.consistency.evolucaoConsistencia, state.chartHorizons.consistency);
    charts.renderConsistencyHistory('chartConsistencyHistory', consistencyData);
    charts.renderQuality('chartDataQualityMonthly', data.distribuicaoDonut || emptyState.consistency.distribuicaoDonut);
    charts.renderQuality('chartDataQualityAnnual', data.distribuicaoDonutAnual || emptyState.consistency.distribuicaoDonutAnual);
  }

  // Filtros multi-seleção (agroindústria, região, projeto, consultor, produtor): lista vazia = "Todos".
  // O <select multiple> é o armazenamento; a UI é o popup com checkboxes (setupCustomSelectDropdowns).
  function allOptionsSelected(select) {
    return select.options.length > 1 && [...select.options].every((opt) => opt.selected);
  }

  function selectedValues(selectId) {
    const select = el(selectId);
    if (!select || allOptionsSelected(select)) return []; // tudo marcado = "Todos" = sem restrição
    return [...select.selectedOptions].map((opt) => opt.value).filter(Boolean);
  }

  // Reconstrói as opções de um filtro multi-seleção preservando o que continua disponível.
  // items: [{ value, label }]. Seleções que deixaram de existir são descartadas.
  function fillMultiSelect(selectId, items) {
    const select = el(selectId);
    if (!select) return;
    const previous = new Set(selectedValues(selectId));
    select.innerHTML = items.map(({ value, label }) => (
      `<option value="${escapeHtml(value)}"${previous.has(value) ? ' selected' : ''}>${escapeHtml(label)}</option>`
    )).join('');
    syncCustomSelectDisplay(selectId);
  }

  function currentFilter() {
    return {
      industry: selectedValues('filterIndustry'),
      region: selectedValues('filterRegion'),
      project: selectedValues('filterProject'),
      status: '',
      consultant: selectedValues('filterConsultant'),
      producer: selectedValues('filterProducer'),
      month: el('filterMonth')?.value || ''
    };
  }

  function dimensionMatches(rowValue, filterValue) {
    const expected = [].concat(filterValue ?? []).filter(Boolean).map((v) => String(v).toLocaleLowerCase('pt-BR'));
    if (!expected.length) return true;
    if (rowValue === null || rowValue === undefined || rowValue === '' || rowValue === 'Todos') return true;
    const values = Array.isArray(rowValue) ? rowValue : [rowValue];
    return values.some((value) => expected.includes(String(value).toLocaleLowerCase('pt-BR')));
  }

  function normalizeStatus(value) {
    const normalized = String(value || '').trim().toLocaleUpperCase('pt-BR');
    if (normalized.startsWith('INATIV')) return 'INATIVO';
    if (normalized.startsWith('ATIV')) return 'ATIVO';
    return normalized;
  }

  const LAC_CONSULTORIA_RAW = new Set([
    'CELIO ROBERTO OLIVEIRA (REGENERA)',
    'SUELY DE JESUS OLIVEIRA (REGENERA)',
    'CELIO ROBERTO OLIVEIRA',
    'SUELY DE JESUS OLIVEIRA'
  ]);

  const NON_FIELD_CONSULTANTS = new Set([
    'TALITA FONTES',
    'TALITA FONTES (ALVOAR ECO)',
    'TALITA FONTES (LABOR RURAL)',
    'CONSULTOR LABOR RURAL (GENERICO)',
    'CONSULTOR GENERICO',
    'USUARIO TESTE (PRODUCAO)',
    'USUARIO TESTE',
    'CONTA DE SUPERVISÃO',
    'CONTA DE SUPERVISAO',
    'LABOR RURAL (GERAL)',
    'SUPERVISAO',
    'SUPERVISÃO',
    'SUPERVISAO AGRICULTURA',
    'SUPERVISAO PECUARIA',
    'SUPERVISÃO AGRICULTURA',
    'SUPERVISÃO PECUÁRIA',
    'COORDENACAO',
    'COORDENAÇÃO'
  ]);

  function isNonFieldConsultant(name) {
    if (!name) return true;
    const upper = String(name).trim().toUpperCase();
    if (NON_FIELD_CONSULTANTS.has(upper)) return true;
    if (upper.startsWith('TALITA FONTES')) return true;
    if (upper.includes('_CONSULTOR') || upper.includes('CONSULTOR_') || upper === 'CONTA DE SUPERVISÃO') return true;
    if (upper.includes('SUPERVISAO') || upper.includes('SUPERVISÃO')) return true;
    if (upper.includes('COORDENACAO') || upper.includes('COORDENAÇÃO')) return true;
    return false;
  }

  function sanitizeConsultorList(rawName) {
    if (!rawName) return [];
    return String(rawName)
      .split('/')
      .map((p) => p.trim())
      .filter(Boolean)
      .map((part) => {
        const upper = part.toUpperCase();
        if (LAC_CONSULTORIA_RAW.has(upper)) return 'LAC CONSULTORIA';
        return part.replace(/\s*\([^)]+\)\s*$/, '').trim() || part;
      })
      .filter((name) => !isNonFieldConsultant(name));
  }

  function matches(row, filter, ignoreMonth = false) {
    const codUpper = String(row.codigo_lr || row.codigo_produtor || '').toUpperCase();
    if (codUpper.includes('_CONSULTOR') || codUpper.includes('CONSULTOR_')) return false;

    const rawConsultant = String(row.consultor || row.nome_consultor || '');
    if (isNonFieldConsultant(rawConsultant)) return false;

    const consultores = sanitizeConsultorList(rawConsultant).map((c) => c.toLocaleLowerCase('pt-BR'));
    const filterConsult = (filter.consultant || []).map((c) => c.toLocaleLowerCase('pt-BR'));
    const rawConsultantLower = rawConsultant.toLocaleLowerCase('pt-BR');
    const consultantMatch = !filterConsult.length ||
      filterConsult.some((c) => consultores.includes(c) || rawConsultantLower.includes(c));

    const producer = String(row.produtor || row.nome_produtor || row.propriedade || '').trim().toLocaleLowerCase('pt-BR');
    const producerCode = String(row.codigo_lr || row.codigo_produtor || '').trim().toLocaleLowerCase('pt-BR');
    const filterProd = (filter.producer || []).map((p) => p.trim().toLocaleLowerCase('pt-BR'));
    const producerMatch = !filterProd.length || filterProd.includes(producer) || filterProd.includes(producerCode);

    // Agroindústria e região já chegam canônicas do ETL (tab_dim_*): comparação exata, sem reinterpretar
    const rowRegions = [].concat(row.regiao ?? row.regioes ?? []).filter(Boolean);
    const regionMatch = !filter.region.length || rowRegions.some((r) => filter.region.includes(r));
    const rowMonth = String(row.mes_referencia || row.data_referencia || '').slice(0, 10);
    const monthMatch = ignoreMonth || !filter.month || !rowMonth || dimensionMatches(rowMonth, filter.month);

    const rowAgro = row.agroindustria || row.agroindustrias || 'NAO INFORMADA';

    return consultantMatch &&
      producerMatch &&
      dimensionMatches(rowAgro, filter.industry) &&
      regionMatch &&
      dimensionMatches(row.projeto || row.projetos, filter.project) &&
      (!filter.status || normalizeStatus(row.status) === normalizeStatus(filter.status)) &&
      monthMatch;
  }

  // Estado de ordenação para cada tabela do dashboard
  const tableSort = {
    tbodySemVisita: { colKey: null, dir: 'asc' },
    tbodyVisitados: { colKey: null, dir: 'asc' },
    tbodyTurnover: { colKey: null, dir: 'asc' },
    tbodyConsultants: { colKey: null, dir: 'asc' },
    tbodyDataProducers: { colKey: 'possui_dados', dir: 'asc' },
    tbodyInconsistencies: { colKey: null, dir: 'asc' },
    tbodyCadastroDetalhe: { colKey: null, dir: 'asc' },
    tbodyMbRankingDetalhe: { colKey: null, dir: 'asc' },
    tbodyCoeDetalhe: { colKey: null, dir: 'asc' },
    tbodyRankingProdutores: { colKey: null, dir: 'asc' },
    tbodyRankingConsultores: { colKey: null, dir: 'asc' },
    tbodyProdCcs: { colKey: 'valor', dir: 'desc' },
    tbodyProdCpp: { colKey: 'valor', dir: 'desc' },
    tbodyTrajetoria: { colKey: 'variacao', dir: 'desc' }
  };

  // Estado de paginação para cada tabela do dashboard
  const tablePagination = {
    tableSemVisita: { page: 1, pageSize: 25 },
    tableVisitados: { page: 1, pageSize: 25 },
    tableTurnover: { page: 1, pageSize: 25 },
    tableConsultants: { page: 1, pageSize: 25 },
    tableDataProducers: { page: 1, pageSize: 25 },
    tableInconsistencies: { page: 1, pageSize: 25 },
    tableCadastroDetalhe: { page: 1, pageSize: 25 },
    tableMbRankingDetalhe: { page: 1, pageSize: 25 },
    tableCoeDetalhe: { page: 1, pageSize: 25 },
    tableRankingProdutores: { page: 1, pageSize: 25 },
    tableRankingConsultores: { page: 1, pageSize: 25 },
    tableProdCcs: { page: 1, pageSize: 25 },
    tableProdCpp: { page: 1, pageSize: 25 },
    tableTrajetoria: { page: 1, pageSize: 25 }
  };

  function getPaginatedSlice(tableId, rows) {
    if (!tablePagination[tableId]) {
      tablePagination[tableId] = { page: 1, pageSize: 25 };
    }
    const conf = tablePagination[tableId];
    const total = rows.length;
    if (total === 0) {
      conf.page = 1;
      return [];
    }

    if (conf.pageSize === 0) {
      // 0 = Exibir todos os registros
      conf.page = 1;
      return rows;
    }

    const totalPages = Math.max(1, Math.ceil(total / conf.pageSize));
    if (conf.page > totalPages) conf.page = totalPages;
    if (conf.page < 1) conf.page = 1;

    const start = (conf.page - 1) * conf.pageSize;
    const end = start + conf.pageSize;
    return rows.slice(start, end);
  }

  function getPaginationPagesList(currentPage, totalPages) {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages = [];
    if (currentPage <= 4) {
      for (let i = 1; i <= 5; i++) pages.push(i);
      pages.push('...');
      pages.push(totalPages);
    } else if (currentPage >= totalPages - 3) {
      pages.push(1);
      pages.push('...');
      for (let i = totalPages - 4; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      pages.push('...');
      pages.push(currentPage - 1);
      pages.push(currentPage);
      pages.push(currentPage + 1);
      pages.push('...');
      pages.push(totalPages);
    }
    return pages;
  }

  function renderTablePagination(paginationId, tableId, totalRows) {
    const container = el(paginationId);
    if (!container) return;
    if (!tablePagination[tableId]) {
      tablePagination[tableId] = { page: 1, pageSize: 25 };
    }
    const conf = tablePagination[tableId];
    const pageSize = conf.pageSize;

    if (totalRows === 0) {
      container.innerHTML = `
        <div class="pagination-info"><span>0 registros</span></div>
      `;
      return;
    }

    const isAll = pageSize === 0;
    const totalPages = isAll ? 1 : Math.max(1, Math.ceil(totalRows / pageSize));
    const currentPage = isAll ? 1 : Math.min(Math.max(1, conf.page), totalPages);
    conf.page = currentPage;

    const startIdx = isAll ? 0 : (currentPage - 1) * pageSize;
    const endIdx = isAll ? totalRows : Math.min(startIdx + pageSize, totalRows);

    const rangeText = isAll
      ? `Exibindo todos os <strong>${totalRows.toLocaleString('pt-BR')}</strong> registros`
      : `Exibindo <strong>${(startIdx + 1).toLocaleString('pt-BR')}–${endIdx.toLocaleString('pt-BR')}</strong> de <strong>${totalRows.toLocaleString('pt-BR')}</strong> registros`;

    const pageButtons = isAll || totalPages <= 1 ? '' : getPaginationPagesList(currentPage, totalPages).map((p) => {
      if (p === '...') {
        return `<span class="pagination-ellipsis">…</span>`;
      }
      const isActive = p === currentPage;
      return `<button type="button" class="pagination-btn ${isActive ? 'active' : ''}" data-table="${escapeHtml(tableId)}" data-page="${p}" ${isActive ? 'aria-current="page"' : ''} title="Página ${p}">${p}</button>`;
    }).join('');

    container.innerHTML = `
      <div class="pagination-info">
        <span>${rangeText}</span>
      </div>
      <div class="pagination-controls">
        <div class="pagination-size-wrap">
          <label for="pageSize_${escapeHtml(tableId)}">Exibir:</label>
          <select id="pageSize_${escapeHtml(tableId)}" class="pagination-size-select" data-table="${escapeHtml(tableId)}" aria-label="Registros por página">
            <option value="10" ${pageSize === 10 ? 'selected' : ''}>10</option>
            <option value="25" ${pageSize === 25 ? 'selected' : ''}>25</option>
            <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
            <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
            <option value="0" ${pageSize === 0 ? 'selected' : ''}>Todos</option>
          </select>
        </div>
        ${totalPages > 1 ? `
        <div class="pagination-nav" aria-label="Navegação de páginas">
          <button type="button" class="pagination-btn" data-table="${escapeHtml(tableId)}" data-page="1" ${currentPage <= 1 ? 'disabled' : ''} title="Primeira página" aria-label="Primeira página">«</button>
          <button type="button" class="pagination-btn" data-table="${escapeHtml(tableId)}" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''} title="Página anterior" aria-label="Página anterior">‹</button>
          ${pageButtons}
          <button type="button" class="pagination-btn" data-table="${escapeHtml(tableId)}" data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''} title="Próxima página" aria-label="Próxima página">›</button>
          <button type="button" class="pagination-btn" data-table="${escapeHtml(tableId)}" data-page="${totalPages}" ${currentPage >= totalPages ? 'disabled' : ''} title="Última página" aria-label="Última página">»</button>
        </div>
        ` : ''}
      </div>
    `;
  }

  function setupTablePagination() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.pagination-btn[data-page]');
      if (!btn || btn.disabled) return;
      const tableId = btn.dataset.table;
      const targetPage = Number(btn.dataset.page);
      if (!tableId || Number.isNaN(targetPage)) return;

      if (tablePagination[tableId]) {
        tablePagination[tableId].page = targetPage;
        renderTables();
        const table = el(tableId);
        const scroll = table?.closest('.table-scroll');
        if (scroll) scroll.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });

    document.addEventListener('change', (e) => {
      const select = e.target.closest('.pagination-size-select');
      if (!select) return;
      const tableId = select.dataset.table;
      const newSize = Number(select.value);
      if (!tableId || Number.isNaN(newSize)) return;

      if (tablePagination[tableId]) {
        tablePagination[tableId].pageSize = newSize;
        tablePagination[tableId].page = 1;
        renderTables();
        const table = el(tableId);
        const scroll = table?.closest('.table-scroll');
        if (scroll) scroll.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });
  }

  let loadingTimeout = null;

  function showLoading(message = 'Atualizando dashboard com filtros...') {
    const overlay = el('loadingOverlay');
    if (!overlay) return;
    const textEl = overlay.querySelector('.loading-text');
    if (textEl) textEl.textContent = message;
    overlay.classList.add('active');
    overlay.setAttribute('aria-hidden', 'false');
  }

  function hideLoading(delay = 0) {
    clearTimeout(loadingTimeout);
    if (delay <= 0) {
      const overlay = el('loadingOverlay');
      if (overlay) {
        overlay.classList.remove('active');
        overlay.setAttribute('aria-hidden', 'true');
      }
      return;
    }
    loadingTimeout = setTimeout(() => {
      const overlay = el('loadingOverlay');
      if (!overlay) return;
      overlay.classList.remove('active');
      overlay.setAttribute('aria-hidden', 'true');
    }, delay);
  }

  function parseSortValue(value) {
    if (value === null || value === undefined || value === '' || value === '—') return null;
    if (typeof value === 'boolean') return value ? 1 : 0;
    if (typeof value === 'number') return value;
    const str = String(value).trim();
    if (/^-?\d+([.,]\d+)?%?$/.test(str)) {
      return parseFloat(str.replace('%', '').replace(/\./g, '').replace(',', '.'));
    }
    const brDateMatch = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(str);
    if (brDateMatch) {
      return new Date(`${brDateMatch[3]}-${brDateMatch[2]}-${brDateMatch[1]}`).getTime();
    }
    return str.toLocaleLowerCase('pt-BR');
  }

  function sortRows(rows, sortConfig, keyGetter) {
    if (!sortConfig || !sortConfig.colKey) return rows;
    const { colKey, dir } = sortConfig;
    return [...rows].sort((a, b) => {
      const valA = parseSortValue(keyGetter(a, colKey));
      const valB = parseSortValue(keyGetter(b, colKey));
      if (valA === null && valB === null) return 0;
      if (valA === null) return 1;
      if (valB === null) return -1;
      let res = 0;
      if (typeof valA === 'number' && typeof valB === 'number') {
        res = valA - valB;
      } else {
        res = String(valA).localeCompare(String(valB), 'pt-BR');
      }
      return dir === 'asc' ? res : -res;
    });
  }

  function rowsOrEmpty(rows, columns, mapper) {
    if (!rows || !rows.length) {
      return `<tr><td colspan="${columns}" class="empty-cell">Nenhum registro para os filtros selecionados.</td></tr>`;
    }
    return rows.map(mapper).join('');
  }

  function updateCount(id, rows) {
    const node = el(id);
    if (!node) return;
    node.textContent = `${rows.length} registros`;
  }

  function updateTableHeadIcons(tbodyId, activeColKey, dir) {
    const tbody = el(tbodyId);
    if (!tbody) return;
    const table = tbody.closest('table');
    if (!table) return;
    table.querySelectorAll('th').forEach((th) => {
      const key = th.dataset.sortKey;
      th.classList.remove('sort-asc', 'sort-desc');
      if (key && key === activeColKey) {
        th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
      }
    });
  }

  let isTableResizing = false;
  let tableResizeEndTime = 0;

  function setupTableSorting() {
    const MAPPINGS = {
      tbodySemVisita: ['codigo_lr', 'consultor', 'produtor', 'propriedade', 'data_associacao', 'data_visita_mes_anterior', 'data_ultima_visita', 'dias_sem_visita', 'cadastro_elabore_label', 'status'],
      tbodyVisitados: ['codigo_lr', 'consultor', 'produtor', 'propriedade', 'atendimento', 'data_visita', 'dados_elabore_status'],
      tbodyTurnover: ['atendimento', 'produtor', 'tipo', 'data', 'grupo', 'motivo'],
      tbodyConsultants: ['consultor', 'total_fazendas', 'fazendas_visitadas', 'total_visitas', 'perc_cobertura', 'status'],
      tbodyDataProducers: ['codigo_lr', 'produtor', 'consultor', 'possui_dados', 'referencia', 'status'],
      tbodyInconsistencies: ['codigo_lr', 'produtor', 'consultor', 'meses_sequenciais', 'consistencia_mensal', 'consistencia_anual', 'acao'],
      tbodyCadastroDetalhe: ['nome_fazenda', 'produtor', 'cidade_uf', 'consultor', 'data_associacao_ts', 'tempo_meses', 'categoria_cadastro', 'consistencia_mensal', 'status'],
      tbodyMbRankingDetalhe: ['posicao', 'nome_fazenda', 'produtor', 'consultor', 'mes_referencia', 'volume_diario_litros', 'preco_medio_litro', 'coe_por_litro', 'margem_bruta_por_litro', 'consistencia_mensal'],
      tbodyCoeDetalhe: ['nome_fazenda', 'produtor', 'mes_referencia', 'volume_leite_mes', 'coe_total_reais', 'coe_por_litro', 'perc_concentrado', 'perc_volumoso', 'perc_mao_de_obra', 'perc_sanidade', 'perc_outros', 'maior_item', 'consistencia_mensal'],
      tbodyRankingProdutores: ['posicao', 'codigo_lr', 'produtor', 'consultor', 'propriedade', 'total_visitas', 'perc_total_label', 'data_ultima_visita'],
      tbodyRankingConsultores: ['posicao', 'consultor', 'total_visitas', 'fazendas_visitadas', 'total_fazendas', 'perc_cobertura_label', 'visitas_por_produtor_label'],
      tbodyProdCcs: ['codigo_lr', 'produtor', 'propriedade', 'consultor', 'regiao', 'valor'],
      tbodyProdCpp: ['codigo_lr', 'produtor', 'propriedade', 'consultor', 'regiao', 'valor'],
      tbodyTrajetoria: ['codigo_lr', 'produtor', 'consultor', 'regiao', 'anterior', 'recente', 'variacao']
    };

    Object.entries(MAPPINGS).forEach(([tbodyId, colKeys]) => {
      const tbody = el(tbodyId);
      if (!tbody) return;
      const table = tbody.closest('table');
      if (!table) return;
      const ths = table.querySelectorAll('thead tr:first-child th');
      ths.forEach((th, idx) => {
        const key = colKeys[idx];
        if (!key || key === 'acao') return;
        th.dataset.sortKey = key;
        th.classList.add('sortable');
        th.title = `Clique para ordenar por ${th.textContent.trim()}`;
        th.addEventListener('click', (e) => {
          // Bloqueia ordenação se o usuário estiver redimensionando colunas, clicando no botão info ou se acabou de soltar a divisória
          if (e.target.closest('.col-resizer') || e.target.closest('.kpi-info-btn') || isTableResizing || (Date.now() - tableResizeEndTime < 350)) {
            e.preventDefault();
            e.stopPropagation();
            return;
          }

          const current = tableSort[tbodyId] || { colKey: null, dir: 'asc' };
          if (current.colKey === key) {
            current.dir = current.dir === 'asc' ? 'desc' : 'asc';
          } else {
            current.colKey = key;
            current.dir = 'asc';
          }
          tableSort[tbodyId] = current;
          showLoading('Ordenando dados...');
          setTimeout(() => {
            renderTables();
            hideLoading(150);
          }, 60);
        });
      });
    });
  }

  function setupColumnResizers() {
    document.querySelectorAll('.data-table').forEach((table) => {
      const ths = table.querySelectorAll('thead tr:first-child th');
      const tableScroll = table.closest('.table-scroll');

      ths.forEach((th, idx) => {
        if (th.querySelector('.col-resizer')) return;

        const resizer = document.createElement('span');
        resizer.className = 'col-resizer';
        resizer.title = 'Arraste para redimensionar a coluna ou dê um duplo clique para auto-ajustar';
        th.appendChild(resizer);

        // Previne que cliques isolados na alça disparem ordenação
        resizer.addEventListener('click', (e) => {
          e.stopPropagation();
          e.preventDefault();
        });

        let startX = 0;
        let startWidth = 0;
        let rafId = null;

        const initExplicitWidths = () => {
          if (!table.dataset.hasExplicitWidths) {
            ths.forEach((colTh) => {
              const currentW = Math.round(colTh.getBoundingClientRect().width);
              colTh.style.width = `${currentW}px`;
              colTh.style.minWidth = `${currentW}px`;
            });
            table.dataset.hasExplicitWidths = 'true';
            table.style.width = 'max-content';
            table.style.minWidth = '100%';
            if (tableScroll) tableScroll.style.overflowX = 'auto';
          }
        };

        const onMouseMove = (e) => {
          const clientX = e.type.startsWith('touch') ? e.touches[0].clientX : e.clientX;
          const diff = clientX - startX;
          const newWidth = Math.max(50, startWidth + diff);

          if (rafId) cancelAnimationFrame(rafId);
          rafId = requestAnimationFrame(() => {
            th.style.width = `${newWidth}px`;
            th.style.minWidth = `${newWidth}px`;
          });
        };

        const onMouseUp = () => {
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);
          document.removeEventListener('touchmove', onMouseMove);
          document.removeEventListener('touchend', onMouseUp);
          document.body.classList.remove('table-resizing');
          resizer.classList.remove('is-active');

          isTableResizing = false;
          tableResizeEndTime = Date.now();
        };

        const startResize = (clientX) => {
          isTableResizing = true;
          initExplicitWidths();

          startX = clientX;
          startWidth = th.offsetWidth;
          resizer.classList.add('is-active');
          document.body.classList.add('table-resizing');

          document.addEventListener('mousemove', onMouseMove);
          document.addEventListener('mouseup', onMouseUp, { once: false });
        };

        resizer.addEventListener('mousedown', (e) => {
          e.stopPropagation();
          e.preventDefault();
          startResize(e.clientX);
        });

        resizer.addEventListener('touchstart', (e) => {
          e.stopPropagation();
          startResize(e.touches[0].clientX);
          document.addEventListener('touchmove', onMouseMove, { passive: false });
          document.addEventListener('touchend', onMouseUp, { once: false });
        }, { passive: false });

        resizer.addEventListener('dblclick', (e) => {
          e.stopPropagation();
          e.preventDefault();
          isTableResizing = true;
          initExplicitWidths();

          const tableRows = table.querySelectorAll('tbody tr');
          let maxWidth = th.textContent.trim().length * 8.5 + 24;

          const ruler = document.createElement('span');
          ruler.style.visibility = 'hidden';
          ruler.style.position = 'absolute';
          ruler.style.whiteSpace = 'nowrap';
          ruler.style.font = '600 11px "Open Sans", sans-serif';
          document.body.appendChild(ruler);

          tableRows.forEach((tr) => {
            const cell = tr.children[idx];
            if (cell) {
              ruler.textContent = cell.textContent.trim();
              const w = ruler.offsetWidth + 22;
              if (w > maxWidth) maxWidth = w;
            }
          });

          document.body.removeChild(ruler);
          const optimalWidth = Math.min(520, Math.max(65, Math.ceil(maxWidth)));

          th.style.width = `${optimalWidth}px`;
          th.style.minWidth = `${optimalWidth}px`;

          isTableResizing = false;
          tableResizeEndTime = Date.now();
        });
      });
    });
  }

  const tableColFilters = {
    tableSemVisita: {},
    tableVisitados: {},
    tableTurnover: {},
    tableConsultants: {},
    tableDataProducers: {},
    tableInconsistencies: {},
    tableRankingProdutores: {},
    tableRankingConsultores: {},
    tableProdCcs: {},
    tableProdCpp: {},
    tableTrajetoria: {}
  };

  function applyColumnFilters(rows, tableId) {
    const filters = tableColFilters[tableId];
    if (!filters || Object.keys(filters).length === 0) return rows;

    return rows.filter((row) => {
      for (const [colKey, rawVal] of Object.entries(filters)) {
        if (!rawVal) continue;
        const searchVal = String(rawVal).toLowerCase().trim();
        if (!searchVal) continue;

        let rowVal = '';
        if (colKey === 'dias_sem_visita') {
          rowVal = String(row.dias_sem_visita ?? '');
        } else if (colKey === 'status') {
          if (row.status && row.status !== 'ATIVO') {
            rowVal = String(row.status);
          } else if (row.dias_sem_visita !== undefined) {
            const hasDays = row.dias_sem_visita !== null && row.dias_sem_visita !== undefined && row.dias_sem_visita !== '';
            const days = hasDays ? Number(row.dias_sem_visita) : null;
            rowVal = !hasDays ? 'sem visita no período' :
                     days >= 60 ? 'sem visita > 60 dias' :
                     days >= 45 ? 'sem visita > 45 dias' :
                     days >= 30 ? 'sem visita > 30 dias' :
                     days > 0 ? `sem visita (${days}d)` :
                     'vínculo recente';
          } else {
            rowVal = String(row.status || 'ativo');
          }
        } else if (colKey === 'elabore') {
          rowVal = row.elabore_ok === false ? 'não nao' : 'sim';
        } else if (colKey === 'possui_dados') {
          rowVal = row.possui_dados === false ? 'não nao' : 'sim';
        } else if (colKey === 'data_associacao' || colKey === 'data_vinculo') {
          rowVal = String(row.data_associacao || row.data_vinculacao || row.data_referencia || '');
        } else if (colKey === 'data_visita_mes_anterior') {
          rowVal = String(row.data_visita_mes_anterior || '');
        } else if (colKey === 'data_ultima_visita') {
          rowVal = String(row.data_ultima_visita || '');
        } else if (colKey === 'grupo') {
          rowVal = String(row.grupo || row.consultor || '');
        } else if (colKey === 'tipo') {
          rowVal = String(row.tipo || row.movimentacao || '');
        } else if (colKey === 'referencia') {
          rowVal = String(row.referencia || row.mes_referencia || '');
        } else if (colKey === 'atendimento') {
          rowVal = String(row.atendimento || row.numero_atendimento || '');
        } else if (colKey === 'perc_cobertura') {
          rowVal = String(row.perc_cobertura ?? '') + '%';
        } else {
          rowVal = String(row[colKey] ?? '');
        }

        if (!rowVal.toLowerCase().includes(searchVal)) {
          return false;
        }
      }
      return true;
    });
  }

  function setupColumnFilters() {
    document.querySelectorAll('.table-col-filter').forEach((input) => {
      input.addEventListener('input', (e) => {
        const tableId = e.target.dataset.table;
        const col = e.target.dataset.col;
        if (!tableId || !col) return;
        if (!tableColFilters[tableId]) tableColFilters[tableId] = {};
        tableColFilters[tableId][col] = e.target.value;
        if (tablePagination[tableId]) tablePagination[tableId].page = 1;
        renderTables();
      });
      input.addEventListener('click', (e) => e.stopPropagation());
      input.addEventListener('keydown', (e) => e.stopPropagation());
    });
  }

  // Linhas das tabelas exibidas ao expandir "Quantidade de visitas": agregam as mesmas
  // visitas da tabela "Produtores visitados" (por produtor) e a carteira por consultor
  // de /api/visits. A posição é fixada pela ordem padrão (mais visitas primeiro), antes
  // dos filtros de coluna e da ordenação escolhida pelo usuário.
  function producerRankingRows() {
    const filter = currentFilter();
    const overview = state.overview || emptyState.overview;
    const visited = (overview.tabelas?.visitados || []).filter((row) => matches(row, filter, true));
    const groups = new Map();
    visited.forEach((row) => {
      const key = row.codigo_lr || row.produtor || '—';
      if (!groups.has(key)) {
        groups.set(key, { codigo_lr: row.codigo_lr || '—', produtor: row.produtor || '—', consultores: new Set(), fazendas: new Set(), total_visitas: 0, ultimaTs: null, data_ultima_visita: '—' });
      }
      const group = groups.get(key);
      group.total_visitas += 1;
      if (row.consultor) group.consultores.add(row.consultor);
      if (row.propriedade || row.fazenda) group.fazendas.add(row.propriedade || row.fazenda);
      const ts = parseSortValue(row.data_visita);
      if (typeof ts === 'number' && (group.ultimaTs === null || ts > group.ultimaTs)) {
        group.ultimaTs = ts;
        group.data_ultima_visita = row.data_visita;
      }
    });
    const total = visited.length;
    return [...groups.values()]
      .sort((a, b) => b.total_visitas - a.total_visitas || String(a.produtor).localeCompare(String(b.produtor), 'pt-BR'))
      .map((group, idx) => ({
        posicao: idx + 1,
        codigo_lr: group.codigo_lr,
        produtor: group.produtor,
        consultor: [...group.consultores].join(', ') || '—',
        propriedade: [...group.fazendas].join(', ') || '—',
        total_visitas: group.total_visitas,
        perc_total_label: `${(total > 0 ? (group.total_visitas / total) * 100 : 0).toFixed(1).replace('.', ',')}%`,
        data_ultima_visita: group.data_ultima_visita
      }));
  }

  function consultantRankingRows() {
    const filter = currentFilter();
    const visits = state.visits || emptyState.visits;
    return (visits.tabelaConsultores || [])
      .filter((row) => matches(row, filter, true))
      .map((row) => {
        const totalVisitas = Number(row.total_visitas) || 0;
        const carteira = Number(row.total_fazendas) || 0;
        return {
          consultor: row.consultor || '—',
          total_visitas: totalVisitas,
          fazendas_visitadas: Number(row.fazendas_visitadas) || 0,
          total_fazendas: carteira,
          perc_cobertura_label: `${Number(row.perc_cobertura || 0).toFixed(1).replace('.', ',')}%`,
          visitas_por_produtor_label: (carteira > 0 ? totalVisitas / carteira : 0).toFixed(1).replace('.', ',')
        };
      })
      .sort((a, b) => b.total_visitas - a.total_visitas || String(a.consultor).localeCompare(String(b.consultor), 'pt-BR'))
      .map((row, idx) => ({ posicao: idx + 1, ...row }));
  }

  function renderRankingTables() {
    const isConsultant = state.rankingDimension === 'consultant';

    let producers = applyColumnFilters(producerRankingRows(), 'tableRankingProdutores');
    if (!isConsultant) updateCount('countRankingVisitas', producers);
    producers = sortRows(producers, tableSort.tbodyRankingProdutores, (row, key) => row[key]);
    updateTableHeadIcons('tbodyRankingProdutores', tableSort.tbodyRankingProdutores.colKey, tableSort.tbodyRankingProdutores.dir);
    const pProducers = getPaginatedSlice('tableRankingProdutores', producers);
    if (el('tbodyRankingProdutores')) el('tbodyRankingProdutores').innerHTML = rowsOrEmpty(pProducers, 8, (row) => `<tr>
        <td class="col-center font-tabular">${row.posicao}</td>
        <td class="col-center"><strong>${escapeHtml(row.codigo_lr)}</strong></td>
        <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
        <td class="col-left" title="${escapeHtml(row.consultor)}">${escapeHtml(row.consultor)}</td>
        <td class="col-left col-fazenda" title="${escapeHtml(row.propriedade)}">${escapeHtml(row.propriedade)}</td>
        <td class="col-center font-tabular"><strong>${number(row.total_visitas)}</strong></td>
        <td class="col-center font-tabular">${escapeHtml(row.perc_total_label)}</td>
        <td class="col-center">${escapeHtml(row.data_ultima_visita)}</td>
      </tr>`);
    renderTablePagination('paginationRankingProdutores', 'tableRankingProdutores', producers.length);

    let consultants = applyColumnFilters(consultantRankingRows(), 'tableRankingConsultores');
    if (isConsultant) updateCount('countRankingVisitas', consultants);
    consultants = sortRows(consultants, tableSort.tbodyRankingConsultores, (row, key) => row[key]);
    updateTableHeadIcons('tbodyRankingConsultores', tableSort.tbodyRankingConsultores.colKey, tableSort.tbodyRankingConsultores.dir);
    const pConsultants = getPaginatedSlice('tableRankingConsultores', consultants);
    if (el('tbodyRankingConsultores')) el('tbodyRankingConsultores').innerHTML = rowsOrEmpty(pConsultants, 7, (row) => `<tr>
        <td class="col-center font-tabular">${row.posicao}</td>
        <td class="col-left" title="${escapeHtml(row.consultor)}"><strong>${escapeHtml(row.consultor)}</strong></td>
        <td class="col-center font-tabular"><strong>${number(row.total_visitas)}</strong></td>
        <td class="col-center font-tabular">${number(row.fazendas_visitadas)}</td>
        <td class="col-center font-tabular">${number(row.total_fazendas)}</td>
        <td class="col-center font-tabular">${escapeHtml(row.perc_cobertura_label)}</td>
        <td class="col-center font-tabular">${escapeHtml(row.visitas_por_produtor_label)}</td>
      </tr>`);
    renderTablePagination('paginationRankingConsultores', 'tableRankingConsultores', consultants.length);
  }

  // ─── Slide 5: tabelas de CCS/CPP acima do limite e lista da trajetória (mesmo sistema das demais tabelas) ───
  const PROD_TABLES = {
    tableProdCcs: { kind: 'ccs', tbody: 'tbodyProdCcs', pagination: 'paginationProdCcs', count: 'countProdCcs' },
    tableProdCpp: { kind: 'cpp', tbody: 'tbodyProdCpp', pagination: 'paginationProdCpp', count: 'countProdCpp' }
  };

  function productionAttentionRows(kind) {
    return (state.production?.atencao?.[kind] || []).map((r) => ({
      codigo_lr: r.codigo_lr || '—',
      produtor: r.produtor || '—',
      propriedade: r.propriedade || '—',
      consultor: r.consultor || '—',
      regiao: r.regiao || '—',
      valor: r.valor
    }));
  }

  function trajectoryRows() {
    return (state.production?.trajetoria?.[state.trajectoryGroup] || []).map((r) => ({
      codigo_lr: r.codigo_lr || '—',
      produtor: r.produtor || '—',
      propriedade: r.propriedade || '—',
      consultor: r.consultor || '—',
      regiao: r.regiao || '—',
      anterior: r.anterior,
      recente: r.recente,
      variacao: r.variacao
    }));
  }

  function renderProductionTables() {
    Object.entries(PROD_TABLES).forEach(([tableId, cfg]) => {
      let rows = applyColumnFilters(productionAttentionRows(cfg.kind), tableId);
      updateCount(cfg.count, rows);
      rows = sortRows(rows, tableSort[cfg.tbody], (row, key) => row[key]);
      updateTableHeadIcons(cfg.tbody, tableSort[cfg.tbody].colKey, tableSort[cfg.tbody].dir);
      const page = getPaginatedSlice(tableId, rows);
      if (el(cfg.tbody)) {
        el(cfg.tbody).innerHTML = rowsOrEmpty(page, 6, (row) => `<tr>
        <td class="col-center"><strong>${escapeHtml(row.codigo_lr)}</strong></td>
        <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
        <td class="col-left col-fazenda" title="${escapeHtml(row.propriedade)}">${escapeHtml(row.propriedade)}</td>
        <td class="col-left" title="${escapeHtml(row.consultor)}">${escapeHtml(row.consultor)}</td>
        <td class="col-left" title="${escapeHtml(row.regiao)}">${escapeHtml(row.regiao)}</td>
        <td class="col-center font-tabular"><strong>${number(row.valor)}</strong></td>
      </tr>`);
      }
      renderTablePagination(cfg.pagination, tableId, rows.length);
    });

    let traj = applyColumnFilters(trajectoryRows(), 'tableTrajetoria');
    updateCount('countTrajetoria', traj);
    traj = sortRows(traj, tableSort.tbodyTrajetoria, (row, key) => row[key]);
    updateTableHeadIcons('tbodyTrajetoria', tableSort.tbodyTrajetoria.colKey, tableSort.tbodyTrajetoria.dir);
    const pTraj = getPaginatedSlice('tableTrajetoria', traj);
    const badge = { evoluiu: 'badge-positive', estavel: 'badge-warning', regrediu: 'badge-danger' }[state.trajectoryGroup] || 'badge-neutral';
    if (el('tbodyTrajetoria')) {
      el('tbodyTrajetoria').innerHTML = rowsOrEmpty(pTraj, 7, (row) => `<tr>
        <td class="col-center"><strong>${escapeHtml(row.codigo_lr)}</strong></td>
        <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
        <td class="col-left" title="${escapeHtml(row.consultor)}">${escapeHtml(row.consultor)}</td>
        <td class="col-left" title="${escapeHtml(row.regiao)}">${escapeHtml(row.regiao)}</td>
        <td class="col-center font-tabular">${number(row.anterior)}</td>
        <td class="col-center font-tabular">${number(row.recente)}</td>
        <td class="col-center font-tabular"><span class="badge ${badge}">${row.variacao > 0 ? '+' : ''}${String(row.variacao).replace('.', ',')}%</span></td>
      </tr>`);
    }
    renderTablePagination('paginationTrajetoria', 'tableTrajetoria', traj.length);
  }

  // Modal da trajetória: aberto por producao.js ao clicar em "Evoluíram / Estáveis / Regrediram"
  function openTrajectoryList(group, title, subtitle, rotuloAnterior, rotuloRecente) {
    state.trajectoryGroup = group;
    tablePagination.tableTrajetoria = { page: 1, pageSize: 25 };
    tableColFilters.tableTrajetoria = {};
    tableSort.tbodyTrajetoria = { colKey: 'variacao', dir: group === 'regrediu' ? 'asc' : 'desc' };
    document.querySelectorAll('#tableTrajetoria .table-col-filter').forEach((input) => { input.value = ''; });
    const heads = document.querySelectorAll('#tableTrajetoria thead tr:first-child th');
    if (heads[4]?.firstChild) heads[4].firstChild.nodeValue = `Média ${rotuloAnterior} (L/dia)`;
    if (heads[5]?.firstChild) heads[5].firstChild.nodeValue = `Média ${rotuloRecente} (L/dia)`;
    el('listaProdTitulo').textContent = title;
    el('listaProdSub').textContent = subtitle;
    renderProductionTables();
    const overlay = el('modalListaProdutores');
    overlay.classList.add('active');
    overlay.setAttribute('aria-hidden', 'false');
    carousel?.pauseForModal?.(true);
  }

  function closeTrajectoryList() {
    const overlay = el('modalListaProdutores');
    if (!overlay || !overlay.classList.contains('active')) return;
    overlay.classList.remove('active');
    overlay.setAttribute('aria-hidden', 'true');
    carousel?.pauseForModal?.(false);
  }

  ['listaProdFechar', 'listaProdOk'].forEach((id) => el(id)?.addEventListener('click', closeTrajectoryList));
  el('modalListaProdutores')?.addEventListener('click', (e) => { if (e.target === el('modalListaProdutores')) closeTrajectoryList(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTrajectoryList(); });
  window.openTrajectoryList = openTrajectoryList;

  function renderTables() {
    const filter = currentFilter();
    const overview = state.overview || emptyState.overview;
    const visits = state.visits || emptyState.visits;
    const turnover = state.turnover || emptyState.turnover;
    const consistency = state.consistency || emptyState.consistency;

    // Tabela 1: Sem Visita
    let withoutVisit = (overview.tabelas?.sem_visita || []).filter((row) => matches(row, filter, true));
    withoutVisit = applyColumnFilters(withoutVisit, 'tableSemVisita');
    updateCount('countWithoutVisit', withoutVisit);
    withoutVisit = sortRows(withoutVisit, tableSort.tbodySemVisita, (row, key) => row[key] ?? row.data_associacao ?? row.data_referencia);
    updateTableHeadIcons('tbodySemVisita', tableSort.tbodySemVisita.colKey, tableSort.tbodySemVisita.dir);
    const pWithoutVisit = getPaginatedSlice('tableSemVisita', withoutVisit);
    if (el('tbodySemVisita')) el('tbodySemVisita').innerHTML = rowsOrEmpty(pWithoutVisit, 10, (row) => {
      const hasDays = row.dias_sem_visita !== null && row.dias_sem_visita !== undefined && row.dias_sem_visita !== '';
      const days = hasDays ? Number(row.dias_sem_visita) : null;
      
      let status = row.status && row.status !== 'ATIVO' ? row.status : '';
      let badgeClass = String(row.status_class || '').toLowerCase();

      if (!status) {
        const isGrave = hasDays && days >= 60;
        const isZero = hasDays && days <= 0;
        status = !hasDays ? 'Sem visita no período' :
                 isGrave ? 'Sem visita > 60 dias' :
                 days >= 45 ? 'Sem visita > 45 dias' :
                 days >= 30 ? 'Sem visita > 30 dias' :
                 days > 0 ? `Sem visita (${days}d)` :
                 'Vínculo Recente';
        badgeClass = isGrave ? 'badge-danger' : (isZero ? 'badge-positive' : 'badge-warning');
      }

      if (!badgeClass) {
        const statusKey = status.toUpperCase();
        if (statusKey === 'INATIVACAO PENDENTE' || statusKey === 'NUNCA VISITADO' || statusKey.includes('> 60')) {
          badgeClass = 'badge-danger';
        } else if (statusKey.includes('RECENTE')) {
          badgeClass = 'badge-positive';
        } else {
          badgeClass = 'badge-warning';
        }
      }

      const rowClass = badgeClass === 'badge-danger' ? 'table-row-grave' : (badgeClass === 'badge-positive' ? '' : 'table-row-pending');
      const dtAssoc = row.data_associacao || row.data_vinculacao || row.data_referencia || '—';
      const dtVisitaMesAnterior = row.data_visita_mes_anterior || '—';
      const dtUltimaVisita = row.data_ultima_visita || '—';
      const cadastroBadge = row.cadastro_elabore
        ? '<span class="badge badge-positive">SIM</span>'
        : '<span class="badge badge-neutral">NÃO</span>';
      return `<tr class="${rowClass}"><td class="col-center"><strong>${escapeHtml(row.codigo_lr || '—')}</strong></td><td class="col-left" title="${escapeHtml(row.consultor || '—')}">${escapeHtml(row.consultor || '—')}</td><td class="col-left" title="${escapeHtml(row.produtor || '—')}">${escapeHtml(row.produtor || '—')}</td><td class="col-left col-fazenda" title="${escapeHtml(row.propriedade || row.fazenda || '—')}">${escapeHtml(row.propriedade || row.fazenda || '—')}</td><td class="col-center col-data-associacao" title="${escapeHtml(dtAssoc)}">${escapeHtml(dtAssoc)}</td><td class="col-center col-visita-mes-anterior" title="${escapeHtml(dtVisitaMesAnterior)}">${escapeHtml(dtVisitaMesAnterior)}</td><td class="col-center" title="${escapeHtml(dtUltimaVisita)}">${escapeHtml(dtUltimaVisita)}</td><td class="col-center font-tabular">${hasDays ? days : '—'}</td><td class="col-center">${cadastroBadge}</td><td class="col-center"><span class="badge ${badgeClass}" title="${escapeHtml(status)}">${escapeHtml(status)}</span></td></tr>`;
    });
    renderTablePagination('paginationSemVisita', 'tableSemVisita', withoutVisit.length);

    // Tabela 2: Visitados
    let visited = (overview.tabelas?.visitados || []).filter((row) => matches(row, filter, true));
    visited = applyColumnFilters(visited, 'tableVisitados');
    updateCount('countVisited', visited);
    visited = sortRows(visited, tableSort.tbodyVisitados, (row, key) => row[key]);
    updateTableHeadIcons('tbodyVisitados', tableSort.tbodyVisitados.colKey, tableSort.tbodyVisitados.dir);
    const pVisited = getPaginatedSlice('tableVisitados', visited);
    if (el('tbodyVisitados')) el('tbodyVisitados').innerHTML = rowsOrEmpty(pVisited, 7, (row) => {
      const labelCad1 = String(row.cadastro_elabore_label || (row.cadastro_elabore === 'INATIVO' ? 'INATIVO' : (row.cadastro_elabore ? 'SIM' : 'NÃO'))).toUpperCase();
      const isInactive1 = labelCad1 === 'INATIVO' || String(row.status || '').toUpperCase().includes('INATIV');
      const isCad = !isInactive1 && row.cadastro_elabore !== false && row.cadastro_elabore !== 'NÃO' && labelCad1 === 'SIM' && Boolean(row.cadastro_elabore);
      const statusDados = row.dados_elabore_status || (row.elabore_ok ? 'SIM (100%)' : 'NÃO (0%)');
      const hasData = row.dados_elabore_tem_dado || row.elabore_ok;
      const pct = row.dados_elabore_pct ?? (hasData ? 100 : 0);

      let dataBadgeClass = 'badge-neutral';
      if (pct >= 80) {
        dataBadgeClass = 'badge-positive';
      } else if (pct > 0) {
        dataBadgeClass = 'badge-warning';
      } else {
        dataBadgeClass = isCad ? 'badge-danger' : 'badge-neutral';
      }

      const dataBadge = `<span class="badge ${dataBadgeClass}">${escapeHtml(statusDados)}</span>`;

      return `<tr>
        <td class="col-center"><strong>${escapeHtml(row.codigo_lr || '—')}</strong></td>
        <td class="col-left" title="${escapeHtml(row.consultor || '—')}">${escapeHtml(row.consultor || '—')}</td>
        <td class="col-left" title="${escapeHtml(row.produtor || '—')}">${escapeHtml(row.produtor || '—')}</td>
        <td class="col-left col-fazenda" title="${escapeHtml(row.propriedade || row.fazenda || '—')}">${escapeHtml(row.propriedade || row.fazenda || '—')}</td>
        <td class="col-center" title="${escapeHtml(row.atendimento || '—')}">${escapeHtml(row.atendimento || '—')}</td>
        <td class="col-center">${escapeHtml(row.data_visita || '—')}</td>
        <td class="col-center">${dataBadge}</td>
      </tr>`;
    });
    renderTablePagination('paginationVisitados', 'tableVisitados', visited.length);

    renderRankingTables();
    renderProductionTables();

    // Tabela 3: Turnover / Movimentação
    let movements = (turnover.tabelaMovimentacao || []).filter((row) => matches(row, filter, true));
    movements = applyColumnFilters(movements, 'tableTurnover');
    updateCount('countTurnover', movements);
    movements = sortRows(movements, tableSort.tbodyTurnover, (row, key) => key === 'grupo' ? (row.grupo || row.consultor) : row[key]);
    updateTableHeadIcons('tbodyTurnover', tableSort.tbodyTurnover.colKey, tableSort.tbodyTurnover.dir);
    const pMovements = getPaginatedSlice('tableTurnover', movements);
    if (el('tbodyTurnover')) el('tbodyTurnover').innerHTML = rowsOrEmpty(pMovements, 6, (row) => {
      const isSaida = row.tipo === 'SAIDA';
      const grp = row.grupo || row.consultor || '—';
      const numAtend = row.atendimento || row.numero_atendimento || '—';
      return `<tr class="${isSaida ? 'table-row-grave' : ''}"><td class="col-center font-tabular"><strong>${escapeHtml(String(numAtend))}</strong></td><td class="col-left" title="${escapeHtml(row.produtor || '—')}"><strong>${escapeHtml(row.produtor || '—')}</strong></td><td class="col-center"><span class="badge ${isSaida ? 'badge-danger' : 'badge-positive'}">${escapeHtml(row.tipo)}</span></td><td class="col-center">${escapeHtml(row.data || '—')}</td><td class="col-left" title="${escapeHtml(grp)}">${escapeHtml(grp)}</td><td class="col-left" title="${escapeHtml(row.motivo || '—')}">${escapeHtml(row.motivo || '—')}</td></tr>`;
    });
    renderTablePagination('paginationTurnover', 'tableTurnover', movements.length);

    // Tabela 4: Consultores
    let consultants = (visits.tabelaConsultores || []).filter((row) => matches(row, filter, true));
    consultants = applyColumnFilters(consultants, 'tableConsultants');
    updateCount('countConsultants', consultants);
    consultants = sortRows(consultants, tableSort.tbodyConsultants, (row, key) => row[key]);
    updateTableHeadIcons('tbodyConsultants', tableSort.tbodyConsultants.colKey, tableSort.tbodyConsultants.dir);
    const pConsultants = getPaginatedSlice('tableConsultants', consultants);
    if (el('tbodyConsultants')) el('tbodyConsultants').innerHTML = rowsOrEmpty(pConsultants, 6, (row) => `<tr><td class="col-left" title="${escapeHtml(row.consultor || '—')}"><strong>${escapeHtml(row.consultor || '—')}</strong></td><td class="col-center font-tabular">${number(row.total_fazendas)}</td><td class="col-center font-tabular">${number(row.fazendas_visitadas)}</td><td class="col-center font-tabular">${number(row.total_visitas)}</td><td class="col-center font-tabular">${percent(row.perc_cobertura)}</td><td class="col-center"><span class="badge badge-positive">ATIVO</span></td></tr>`);
    renderTablePagination('paginationConsultants', 'tableConsultants', consultants.length);

    // Tabela 5: Produtores com Dados
    let withData = (consistency.tabelaProdutoresComDados || []).filter((row) => matches(row, filter, true));
    withData = applyColumnFilters(withData, 'tableDataProducers');
    updateCount('countDataProducers', withData);
    withData = sortRows(withData, tableSort.tbodyDataProducers, (row, key) => key === 'produtor' ? (row.produtor || row.codigo_lr) : row[key]);
    updateTableHeadIcons('tbodyDataProducers', tableSort.tbodyDataProducers.colKey, tableSort.tbodyDataProducers.dir);
    const pWithData = getPaginatedSlice('tableDataProducers', withData);
    if (el('tbodyDataProducers')) el('tbodyDataProducers').innerHTML = rowsOrEmpty(pWithData, 6, (row) => {
      const labelCad2 = String(row.cadastro_elabore_label || (row.cadastro_elabore === 'INATIVO' ? 'INATIVO' : (row.cadastro_elabore ? 'SIM' : 'NÃO'))).toUpperCase();
      const isInactive2 = labelCad2 === 'INATIVO' || String(row.status || '').toUpperCase().includes('INATIV');
      const isCad = !isInactive2 && row.cadastro_elabore !== false && row.cadastro_elabore !== 'NÃO' && labelCad2 === 'SIM' && Boolean(row.cadastro_elabore ?? true);
      const cadBadge = isInactive2
        ? '<span class="badge badge-neutral">INATIVO</span>'
        : (isCad ? '<span class="badge badge-positive">SIM</span>' : '<span class="badge badge-neutral">NÃO</span>');
      
      const statusDados = row.dados_elabore_status || (row.possui_dados ? 'SIM (100%)' : 'NÃO (0%)');
      const hasData = row.dados_elabore_tem_dado ?? row.possui_dados;
      const pct = row.dados_elabore_pct ?? (hasData ? 100 : 0);

      let dataBadgeClass = 'badge-neutral';
      if (pct >= 80) {
        dataBadgeClass = 'badge-positive';
      } else if (pct > 0) {
        dataBadgeClass = 'badge-warning';
      } else {
        dataBadgeClass = isCad ? 'badge-danger' : 'badge-neutral';
      }

      const dataBadge = `<span class="badge ${dataBadgeClass}">${escapeHtml(statusDados)}</span>`;
      const prodName = row.produtor || row.codigo_lr || '—';

      return `<tr class="${!hasData && isCad ? 'table-row-grave' : ''}"><td class="col-center"><strong>${escapeHtml(row.codigo_lr || '—')}</strong></td><td class="col-left" title="${escapeHtml(prodName)}">${escapeHtml(prodName)}</td><td class="col-left" title="${escapeHtml(row.consultor || '—')}">${escapeHtml(row.consultor || '—')}</td><td class="col-center">${escapeHtml(row.referencia || '—')}</td><td class="col-center">${dataBadge}</td><td class="col-center"><button class="btn-elabore-detail" type="button" data-lr="${escapeHtml(row.codigo_lr || '')}">Ver detalhes ›</button></td></tr>`;
    });
    renderTablePagination('paginationDataProducers', 'tableDataProducers', withData.length);

    // Tabela 6: Consistência Mensal e Anual das Fazendas
    let inconsistencies = (consistency.tabelaInconsistentes || []).filter((row) => matches(row, filter, true));
    inconsistencies = applyColumnFilters(inconsistencies, 'tableInconsistencies');
    updateCount('countInconsistencies', inconsistencies);
    inconsistencies = sortRows(inconsistencies, tableSort.tbodyInconsistencies, (row, key) => key === 'produtor' ? (row.produtor || row.codigo_lr) : row[key]);
    updateTableHeadIcons('tbodyInconsistencies', tableSort.tbodyInconsistencies.colKey, tableSort.tbodyInconsistencies.dir);
    const pInconsistencies = getPaginatedSlice('tableInconsistencies', inconsistencies);
    if (el('tbodyInconsistencies')) el('tbodyInconsistencies').innerHTML = rowsOrEmpty(pInconsistencies, 8, (row, idx) => {
      const isGraveMensal = isStatusInconsistente(row.consistencia_mensal || row.consistencia);
      const isGraveAnual = isStatusInconsistente(row.consistencia_anual);
      const isConsistMensal = isStatusConsistente(row.consistencia_mensal || row.consistencia);
      const isConsistAnual = isStatusConsistente(row.consistencia_anual);

      let rowClass = '';
      if (isGraveMensal || isGraveAnual) {
        rowClass = 'table-row-grave';
      } else if (!isConsistMensal || !isConsistAnual) {
        rowClass = 'table-row-pending';
      }

      const getBadgeClass = (val) => {
        const str = String(val || '').toLowerCase();
        if (str.includes('consistente') && !str.includes('inconsistente')) return 'badge-positive';
        if (str.includes('inconsistente') || str.includes('divergente') || str.includes('outlier')) return 'badge-danger';
        return 'badge-warning';
      };

      const badgeClassMensal = getBadgeClass(row.consistencia_mensal || row.consistencia);
      const badgeClassAnual = getBadgeClass(row.consistencia_anual);
      const prodName = row.produtor || row.codigo_lr || '—';
      const seqText = row.meses_sequenciais != null ? number(row.meses_sequenciais) : '—';

      return `<tr class="${rowClass}"><td class="col-center"><strong>${escapeHtml(row.codigo_lr || '—')}</strong></td><td class="col-left" title="${escapeHtml(prodName)}"><strong>${escapeHtml(prodName)}</strong></td><td class="col-left col-fazenda" title="${escapeHtml(row.propriedade || '—')}">${escapeHtml(row.propriedade || '—')}</td><td class="col-left" title="${escapeHtml(row.consultor || '—')}">${escapeHtml(row.consultor || '—')}</td><td class="col-center font-tabular">${seqText}</td><td class="col-center"><span class="badge ${badgeClassMensal}" title="${escapeHtml(row.consistencia_mensal || 'SEM DADOS')}">${escapeHtml(row.consistencia_mensal || 'SEM DADOS')}</span></td><td class="col-center"><span class="badge ${badgeClassAnual}" title="${escapeHtml(row.consistencia_anual || 'SEM DADOS')}">${escapeHtml(row.consistencia_anual || 'SEM DADOS')}</span></td><td class="col-center"><button class="link-button btn-view-details" type="button" onclick="window.openInconsistencyDetail('${escapeHtml(row.codigo_lr || row.produtor)}')">Ver detalhes ›</button></td></tr>`;
    });
    renderTablePagination('paginationInconsistencies', 'tableInconsistencies', inconsistencies.length);

    // Registros inconsistentes aparecem nas tabelas da tela 4, mas ficam fora dos gráficos/KPIs
    const consistenciaBadge = (row) => `<span class="badge ${row.no_grafico ? 'badge-positive' : 'badge-danger'}" title="${row.no_grafico ? 'Considerado nos gráficos' : 'Fora dos gráficos e KPIs'}">${escapeHtml(row.consistencia_mensal)}</span>`;

    // Tabela 7: Tempo de Cadastro dos Produtores (detalhe exibido ao expandir o painel)
    const economics = state.economics || {};
    let cadDetalhe = economics.cadastro_detalhe || economics.slide5?.cadastro_detalhe || [];
    cadDetalhe = applyColumnFilters(cadDetalhe, 'tableCadastroDetalhe');
    updateCount('countCadastroDetalhe', cadDetalhe);
    cadDetalhe = sortRows(cadDetalhe, tableSort.tbodyCadastroDetalhe, (row, key) => row[key]);
    updateTableHeadIcons('tbodyCadastroDetalhe', tableSort.tbodyCadastroDetalhe.colKey, tableSort.tbodyCadastroDetalhe.dir);
    const pCadDetalhe = getPaginatedSlice('tableCadastroDetalhe', cadDetalhe);
    if (el('tbodyCadastroDetalhe')) el('tbodyCadastroDetalhe').innerHTML = rowsOrEmpty(pCadDetalhe, 9, (row) => `<tr>
      <td class="col-left" title="${escapeHtml(row.nome_fazenda)}"><strong>${escapeHtml(row.nome_fazenda)}</strong></td>
      <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
      <td class="col-left">${escapeHtml(row.cidade_uf)}</td>
      <td class="col-left" title="${escapeHtml(row.consultor)}">${escapeHtml(row.consultor)}</td>
      <td class="col-center">${escapeHtml(row.data_associacao)}</td>
      <td class="col-center">${escapeHtml(row.tempo_cadastro)}</td>
      <td class="col-center">${escapeHtml(row.categoria_cadastro)}</td>
      <td class="col-center">${consistenciaBadge(row)}</td>
      <td class="col-center"><span class="badge ${String(row.status).toUpperCase().includes('INATIV') ? 'badge-neutral' : 'badge-positive'}">${escapeHtml(row.status)}</span></td>
    </tr>`);
    renderTablePagination('paginationCadastroDetalhe', 'tableCadastroDetalhe', cadDetalhe.length);

    // Tabela 8: Top 10 Fazendas por Margem Bruta (detalhe exibido ao expandir o painel)
    let mbDetalhe = economics.top10_mb_ranking_tabela || economics.top10_mb_ranking || [];
    mbDetalhe = applyColumnFilters(mbDetalhe, 'tableMbRankingDetalhe');
    updateCount('countMbRankingDetalhe', mbDetalhe);
    mbDetalhe = sortRows(mbDetalhe, tableSort.tbodyMbRankingDetalhe, (row, key) => row[key]);
    updateTableHeadIcons('tbodyMbRankingDetalhe', tableSort.tbodyMbRankingDetalhe.colKey, tableSort.tbodyMbRankingDetalhe.dir);
    const pMbDetalhe = getPaginatedSlice('tableMbRankingDetalhe', mbDetalhe);
    if (el('tbodyMbRankingDetalhe')) el('tbodyMbRankingDetalhe').innerHTML = rowsOrEmpty(pMbDetalhe, 10, (row) => `<tr>
      <td class="col-right font-tabular">${row.posicao ?? '—'}</td>
      <td class="col-left" title="${escapeHtml(row.nome_fazenda)}"><strong>${escapeHtml(row.nome_fazenda)}</strong></td>
      <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
      <td class="col-left" title="${escapeHtml(row.consultor)}">${escapeHtml(row.consultor)}</td>
      <td class="col-center">${escapeHtml(row.mes_label)}</td>
      <td class="col-right font-tabular">${number(row.volume_diario_litros)}</td>
      <td class="col-right font-tabular">R$ ${number(row.preco_medio_litro)}</td>
      <td class="col-right font-tabular">R$ ${number(row.coe_por_litro)}</td>
      <td class="col-right font-tabular"><strong class="${row.flag_positiva ? 'text-positive' : 'text-negative'}">R$ ${number(row.margem_bruta_por_litro)}</strong></td>
      <td class="col-center">${consistenciaBadge(row)}</td>
    </tr>`);
    renderTablePagination('paginationMbRankingDetalhe', 'tableMbRankingDetalhe', mbDetalhe.length);

    // Tabela 9: Composição do COE por fazenda (detalhe do painel "5 principais itens de custo")
    let coeDetalhe = economics.coe_detalhe || [];
    coeDetalhe = applyColumnFilters(coeDetalhe, 'tableCoeDetalhe');
    updateCount('countCoeDetalhe', coeDetalhe);
    coeDetalhe = sortRows(coeDetalhe, tableSort.tbodyCoeDetalhe, (row, key) => row[key]);
    updateTableHeadIcons('tbodyCoeDetalhe', tableSort.tbodyCoeDetalhe.colKey, tableSort.tbodyCoeDetalhe.dir);
    const pCoeDetalhe = getPaginatedSlice('tableCoeDetalhe', coeDetalhe);
    const reais = (v) => `R$ ${Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (el('tbodyCoeDetalhe')) el('tbodyCoeDetalhe').innerHTML = rowsOrEmpty(pCoeDetalhe, 13, (row) => `<tr>
      <td class="col-left" title="${escapeHtml(row.nome_fazenda)}"><strong>${escapeHtml(row.nome_fazenda)}</strong></td>
      <td class="col-left" title="${escapeHtml(row.produtor)}">${escapeHtml(row.produtor)}</td>
      <td class="col-center">${escapeHtml(row.mes_label)}</td>
      <td class="col-right font-tabular">${number(row.volume_leite_mes)}</td>
      <td class="col-right font-tabular">${reais(row.coe_total_reais)}</td>
      <td class="col-right font-tabular">R$ ${number(row.coe_por_litro)}</td>
      <td class="col-right font-tabular">${percent(row.perc_concentrado)}</td>
      <td class="col-right font-tabular">${percent(row.perc_volumoso)}</td>
      <td class="col-right font-tabular">${percent(row.perc_mao_de_obra)}</td>
      <td class="col-right font-tabular">${percent(row.perc_sanidade)}</td>
      <td class="col-right font-tabular">${percent(row.perc_outros)}</td>
      <td class="col-center">${escapeHtml(row.maior_item)}</td>
      <td class="col-center">${consistenciaBadge(row)}</td>
    </tr>`);
    renderTablePagination('paginationCoeDetalhe', 'tableCoeDetalhe', coeDetalhe.length);
  }

  // ─── LÓGICA DE FILTRAGEM MULTIDIRECIONAL ESTILO POWER BI ──────────────

  function ehCadeiaLeite(projeto) {
    if (!projeto) return true;
    const p = String(projeto).trim().toUpperCase();
    const TERMOS_NAO_LEITE = [
      'MAIS GRAOS', 'MAIS GRÃOS', 'GRAOS', 'GRÃOS',
      'MIMC', 'M&E', 'CAFE&GESTAO', 'CAFE & GESTAO', 'CAFÉ & GESTÃO',
      'CAFÉ', 'CAFE', 'CACAU', 'CARGILL', 'NCP', 'OFI', 'PV CARGILL',
      'AGRICULTURA'
    ];
    for (const termo of TERMOS_NAO_LEITE) {
      if (p.includes(termo)) return false;
    }
    return true;
  }

  const PROJECT_LABEL_MAP = {
    'ALVOAR ASSIST': 'Alvoar Assist',
    'ALVOAR ECO': 'Alvoar Eco',
    'ATEG_CCPR': 'Ateg_Ccpr',
    'LPA': 'Lpa',
    'REGENERA': 'Regenera',
    'SEMEAR': 'Semear'
  };

  function formatProjectLabel(projectCode) {
    if (!projectCode) return '';
    const code = String(projectCode).trim().toUpperCase();
    if (PROJECT_LABEL_MAP[code]) return PROJECT_LABEL_MAP[code];
    return String(projectCode)
      .toLowerCase()
      .split(/\s+/)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }

  function extractMasterRows(overview, visits, turnover, consistency) {
    const o = overview || emptyState.overview;
    const v = visits || emptyState.visits;
    const t = turnover || emptyState.turnover;
    const c = consistency || emptyState.consistency;

    const rows = [];
    const seen = new Set();

    function addRow(r, defaultStatus = 'ATIVO') {
      if (!r) return;
      if (!ehCadeiaLeite(r.projeto || r.agroindustria)) return;
      const consultores = sanitizeConsultorList(r.consultor || r.nome_consultor);
      const produtor = String(r.produtor || r.nome_produtor || '').trim();
      const codigoLr = String(r.codigo_lr || '').trim();
      // Valores canônicos gravados pelo ETL (tab_dim_agroindustria / tab_dim_regiao): nada é reinterpretado aqui
      const agro = String(r.agroindustria || 'NAO INFORMADA').trim();
      const reg = String(r.regiao || '').trim() === 'NAO INFORMADA' ? '' : String(r.regiao || '').trim();
      const proj = String(r.projeto || '').trim();
      const stat = normalizeStatus(r.status || defaultStatus);
      const mes = String(r.mes_referencia || r.data_referencia || '').slice(0, 10);

      if (produtor.includes('_CONSULTOR') || produtor === 'CONTA DE SUPERVISÃO') return;

      if (consultores.length === 0) {
        const key = `${agro}|${reg}|${proj}|${stat}||${produtor}|${codigoLr}|${mes}`;
        if (!seen.has(key)) {
          seen.add(key);
          rows.push({
            agroindustria: agro || '',
            regiao: reg || '',
            projeto: proj || '',
            status: stat || 'ATIVO',
            consultor: '',
            produtor: produtor || '',
            codigo_lr: codigoLr || '',
            mes_referencia: mes || ''
          });
        }
      } else {
        consultores.forEach((consult) => {
          const cleanConsult = String(consult || '').trim();
          if (!cleanConsult) return;
          const key = `${agro}|${reg}|${proj}|${stat}|${cleanConsult}|${produtor}|${codigoLr}|${mes}`;
          if (!seen.has(key)) {
            seen.add(key);
            rows.push({
              agroindustria: agro || '',
              regiao: reg || '',
              projeto: proj || '',
              status: stat || 'ATIVO',
              consultor: cleanConsult || '',
              produtor: produtor || '',
              codigo_lr: codigoLr || '',
              mes_referencia: mes || ''
            });
          }
        });
      }
    }

    // 0. Base Mestre Direta da tab_dim_fazendas_ativas
    if (o.dim_fazendas && Array.isArray(o.dim_fazendas)) {
      o.dim_fazendas.forEach((r) => addRow(r, r.status || 'ATIVO'));
    }

    // 1. Tabela de consultores ativos da carteira oficial do mês
    (v.tabelaConsultores || []).forEach((tc) => {
      const consult = tc.consultor;
      const agros = tc.agroindustrias && tc.agroindustrias.length ? tc.agroindustrias : ['NAO INFORMADA'];
      const projs = tc.projetos && tc.projetos.length ? tc.projetos : [tc.projeto];
      const regs = tc.regioes && tc.regioes.length ? tc.regioes : [tc.regiao];
      agros.forEach((agro) => {
        projs.forEach((proj) => {
          regs.forEach((reg) => {
            addRow({
              consultor: consult,
              agroindustria: agro,
              projeto: proj,
              regiao: reg,
              status: 'ATIVO',
              mes_referencia: tc.mes_referencia
            }, 'ATIVO');
          });
        });
      });
    });

    (o.tabelas?.sem_visita || []).forEach((r) => addRow(r, 'ATIVO'));
    (o.tabelas?.visitados || []).forEach((r) => addRow(r, 'ATIVO'));
    (c.tabelaProdutoresComDados || []).forEach((r) => addRow(r, r.status || 'ATIVO'));
    (c.tabelaInconsistentes || []).forEach((r) => addRow(r, r.status || 'ATIVO'));
    (t.tabelaMovimentacao || []).forEach((r) => addRow(r, r.status || (r.tipo === 'SAIDA' ? 'INATIVO' : 'ATIVO')));

    return rows;
  }

  // Texto exibido no campo do filtro: "Todos" sem seleção, o nome com 1, "N selecionados" com várias (nomes no tooltip)
  function multiDisplayText(select) {
    const chosen = [...select.selectedOptions].filter((opt) => opt.value);
    if (!chosen.length || allOptionsSelected(select)) return select.dataset.allLabel || 'Todos';
    return chosen.length === 1 ? chosen[0].text : `${chosen.length} selecionados`;
  }

  function syncCustomSelectDisplay(selectId) {
    const select = el(selectId);
    if (!select) return;
    const control = select.closest('.filter-control');
    if (!control) return;
    const displayValue = control.querySelector('.select-display-value');
    if (!displayValue) return;
    if (select.multiple) {
      displayValue.textContent = multiDisplayText(select);
      displayValue.title = [...select.selectedOptions].map((opt) => opt.text).join(', ');
      return;
    }
    const selectedOpt = select.options[select.selectedIndex];
    displayValue.textContent = selectedOpt ? selectedOpt.text : (select.value || '');
  }

  function populateMonthSelect(values) {
    const select = el('filterMonth');
    if (!select) return;
    const current = select.value;
    const now = new Date();
    const currentCalendarMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
    const maxAllowedMonth = currentCalendarMonth;
    const unique = [...new Set((values || []).filter(Boolean).map((v) => String(v).slice(0, 10)).filter((v) => v <= maxAllowedMonth))].sort().reverse();
    const options = unique.map((value) => {
      const parsed = new Date(`${value}T12:00:00`);
      const label = Number.isNaN(parsed.getTime())
        ? value
        : parsed.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' }).replace(/^./, (letter) => letter.toUpperCase());
      return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
    }).join('');
    select.innerHTML = `<option value="">Todos</option>${options}`;
    if (state.hasUserChangedMonth) {
      if (unique.includes(current) || current === '') select.value = current;
    } else {
      // Padrão ao carregar o dashboard: selecionar o mês atual (ou o mês mais recente disponível <= mês atual), não TODOS
      if (unique.length > 0) {
        select.value = unique.includes(currentCalendarMonth) ? currentCalendarMonth : unique[0];
      }
    }
    syncCustomSelectDisplay('filterMonth');
  }

  function updateAllCrossFilters() {
    const current = currentFilter();
    const rows = state.masterRows || [];
    if (!rows.length) return;

    // Multi-seleção: cada filtro é uma lista (vazia = sem restrição); a linha passa se bate com QUALQUER valor da lista
    const curInd = current.industry;
    const curReg = current.region;
    const curProj = current.project;
    const curStat = current.status;
    const curCons = current.consultant.map((c) => c.toLowerCase());
    const curProd = current.producer.map((p) => p.toLowerCase());
    const curMonth = current.month ? String(current.month).slice(0, 7) : '';

    function matchesActiveExcept(row, fieldKey) {
      if (!ehCadeiaLeite(row.projeto || row.agroindustria)) return false;
      if (fieldKey !== 'month' && curMonth && row.mes_referencia && String(row.mes_referencia).slice(0, 7) !== curMonth) return false;
      if (fieldKey !== 'industry' && curInd.length && !curInd.includes(row.agroindustria)) return false;
      if (fieldKey !== 'region' && curReg.length && !curReg.includes(row.regiao)) return false;
      if (fieldKey !== 'project' && curProj.length && !curProj.includes(row.projeto)) return false;
      if (fieldKey !== 'status' && curStat && row.status !== curStat) return false;
      if (fieldKey !== 'consultant' && curCons.length && !curCons.includes((row.consultor || '').toLowerCase())) return false;
      if (fieldKey !== 'producer' && curProd.length && !curProd.includes((row.produtor || '').toLowerCase())) return false;
      return true;
    }

    // Relacionamento oficial (tab_dim_consultor_atuacao, gravada pelo ETL): [mês AAAA-MM, agroindústria, região, consultor].
    // Agroindústria, região e consultor só se oferecem entre si quando existe essa relação; sem a ponte publicada,
    // valem apenas as opções derivadas das linhas.
    const relacoes = (state.overview || state.masterOverview)?.filterOptions?.relacoes;
    function opcoesRelacionadas(fieldKey) {
      if (!Array.isArray(relacoes) || !relacoes.length) return null;
      const permitidos = new Set();
      relacoes.forEach(([mes, agro, regiao, consultor]) => {
        if (curMonth && mes !== curMonth) return;
        if (fieldKey !== 'industry' && curInd.length && !curInd.includes(agro)) return;
        if (fieldKey !== 'region' && curReg.length && !curReg.includes(regiao)) return;
        if (fieldKey !== 'consultant' && curCons.length && !curCons.includes(String(consultor).toLowerCase())) return;
        permitidos.add(String(fieldKey === 'industry' ? agro : fieldKey === 'region' ? regiao : consultor).toLowerCase());
      });
      return permitidos;
    }
    const restringirPelaRelacao = (lista, fieldKey) => {
      const permitidos = opcoesRelacionadas(fieldKey);
      return permitidos ? lista.filter((v) => permitidos.has(String(v).toLowerCase())) : lista;
    };
    const ordenar = (lista) => lista.sort((a, b) => a.localeCompare(b, 'pt-BR'));
    const simples = (valor) => ({ value: valor, label: valor });

    // 1. Agroindústria
    if (el('filterIndustry')) {
      const validRows = rows.filter((r) => matchesActiveExcept(r, 'industry'));
      const oficiais = state.overview?.filterOptions?.agroindustrias;
      let available = [...new Set(validRows.map((r) => r.agroindustria).filter(Boolean).filter(ehCadeiaLeite))];
      if (oficiais && Array.isArray(oficiais) && oficiais.length > 0) {
        available = available.filter((ind) => oficiais.includes(ind));
      }
      available = restringirPelaRelacao(available, 'industry');
      fillMultiSelect('filterIndustry', ordenar(available).map(simples));
    }

    // 2. Região
    if (el('filterRegion')) {
      const validRows = rows.filter((r) => matchesActiveExcept(r, 'region'));
      const available = restringirPelaRelacao([...new Set(validRows.map((r) => r.regiao).filter(Boolean))], 'region');
      fillMultiSelect('filterRegion', ordenar(available).map(simples));
    }

    // 3. Projeto
    if (el('filterProject')) {
      const validRows = rows.filter((r) => matchesActiveExcept(r, 'project'));
      const availableProjects = ordenar([...new Set(validRows.map((r) => r.projeto).filter(Boolean).filter(ehCadeiaLeite))]);
      fillMultiSelect('filterProject', availableProjects.map((p) => ({ value: p, label: formatProjectLabel(p) })));
    }

    // 4. Consultor
    if (el('filterConsultant')) {
      const validRows = rows.filter((r) => matchesActiveExcept(r, 'consultant'));
      const available = restringirPelaRelacao([...new Set(validRows.map((r) => r.consultor).filter(Boolean))], 'consultant');
      fillMultiSelect('filterConsultant', ordenar(available).map(simples));
    }

    // 5. Produtor
    if (el('filterProducer')) {
      const validRows = rows.filter((r) => matchesActiveExcept(r, 'producer'));
      const available = [...new Set(
        validRows.map((r) => r.produtor)
          .filter((p) => p && !String(p).includes('_CONSULTOR') && p !== 'CONTA DE SUPERVISÃO')
      )];
      fillMultiSelect('filterProducer', ordenar(available).map(simples));
    }
  }

  function getTableDataForExport(tableId) {
    const filter = currentFilter();
    const overview = state.overview || emptyState.overview;
    const visits = state.visits || emptyState.visits;
    const turnover = state.turnover || emptyState.turnover;
    const consistency = state.consistency || emptyState.consistency;

    if (tableId === 'tableSemVisita') {
      let data = (overview.tabelas?.sem_visita || []).filter((row) => matches(row, filter, true));
      data = applyColumnFilters(data, 'tableSemVisita');
      return {
        headers: ['Consultor(a)', 'ID (Código LR)', 'Produtor(a)', 'Propriedade', 'Agroindústria', 'Região', 'Projeto', 'Data Associação', 'Última Visita', 'Dias s/ Visita', 'Cadastro Elabore', 'Status'],
        rows: data.map((r) => [
          r.consultor || '—',
          r.codigo_lr || '—',
          r.produtor || '—',
          r.propriedade || '—',
          r.agroindustria || '—',
          r.regiao || '—',
          r.projeto || '—',
          r.data_associacao || r.data_vinculacao || '—',
          r.data_ultima_visita || '—',
          r.dias_sem_visita !== null && r.dias_sem_visita !== undefined ? r.dias_sem_visita : '—',
          r.cadastro_elabore_label || '—',
          r.status && r.status !== 'ATIVO' ? r.status : (r.dias_sem_visita >= 60 ? 'Sem visita > 60 dias' : (r.dias_sem_visita >= 45 ? 'Sem visita > 45 dias' : (r.dias_sem_visita >= 30 ? 'Sem visita > 30 dias' : 'Sem visita no período')))
        ])
      };
    }

    if (tableId === 'tableVisitados') {
      let data = (overview.tabelas?.visitados || []).filter((row) => matches(row, filter, true));
      data = applyColumnFilters(data, 'tableVisitados');
      return {
        headers: ['Consultor(a)', 'ID (Código LR)', 'Produtor(a)', 'Nº Atendimento', 'Data Visita', 'Mês Referência', 'Projeto', 'Agroindústria', 'Região', 'Dados Elabore'],
        rows: data.map((r) => [
          r.consultor || '—',
          r.codigo_lr || '—',
          r.produtor || '—',
          r.atendimento || '—',
          r.data_visita || '—',
          r.mes_referencia || '—',
          r.projeto || '—',
          r.agroindustria || '—',
          r.regiao || '—',
          r.dados_elabore_status || (r.elabore_ok === false ? 'NÃO (0%)' : 'SIM (100%)')
        ])
      };
    }

    if (tableId === 'tableTurnover') {
      let data = (turnover.tabelaMovimentacao || []).filter((row) => matches(row, filter, true));
      data = applyColumnFilters(data, 'tableTurnover');
      return {
        headers: ['Nº Atendimento', 'Produtor(a)', 'Movimentação', 'Data', 'Consultor / Grupo', 'Motivo da Inativação'],
        rows: data.map((r) => [
          r.atendimento || r.numero_atendimento || '—',
          r.produtor || '—',
          r.tipo || (String(r.movimentacao).toLowerCase().includes('sa') ? 'SAIDA' : 'ENTRADA'),
          r.data || '—',
          r.grupo || r.consultor || '—',
          r.motivo || '—'
        ])
      };
    }

    if (tableId === 'tableConsultants') {
      let data = (visits.tabelaConsultores || []).filter((row) => matches(row, filter, true));
      data = applyColumnFilters(data, 'tableConsultants');
      return {
        headers: ['Consultor(a)', 'Total Fazendas', 'Fazendas Visitadas', 'Total Visitas', '% Cobertura', 'Status'],
        rows: data.map((r) => [
          r.consultor || '—',
          r.total_fazendas ?? 0,
          r.fazendas_visitadas ?? 0,
          r.total_visitas ?? 0,
          percent(r.perc_cobertura),
          'ATIVO'
        ])
      };
    }

    if (tableId === 'tableDataProducers') {
      let data = (consistency.tabelaProdutoresComDados || []).filter((row) => matches(row, filter, true));
      data = applyColumnFilters(data, 'tableDataProducers');
      return {
        headers: ['ID (Código LR)', 'Produtor(a)', 'Consultor(a)', 'Possui Dados', 'Última Referência', 'Status Cadastral'],
        rows: data.map((r) => [
          r.codigo_lr || '—',
          r.produtor || r.codigo_lr || '—',
          r.consultor || '—',
          r.possui_dados ? 'SIM' : 'NÃO',
          r.referencia || '—',
          r.status || 'ATIVO'
        ])
      };
    }

    if (tableId === 'tableCadastroDetalhe') {
      const economics = state.economics || {};
      let data = economics.cadastro_detalhe || economics.slide5?.cadastro_detalhe || [];
      data = applyColumnFilters(data, 'tableCadastroDetalhe');
      return {
        headers: ['Fazenda', 'Produtor(a)', 'Cidade/UF', 'Consultor(a)', 'Data de Associação', 'Tempo de Cadastro', 'Categoria', 'Consistência', 'Status'],
        rows: data.map((r) => [
          r.nome_fazenda || '—',
          r.produtor || '—',
          r.cidade_uf || '—',
          r.consultor || '—',
          r.data_associacao || '—',
          r.tempo_cadastro || '—',
          r.categoria_cadastro || '—',
          r.consistencia_mensal || '—',
          r.status || '—'
        ])
      };
    }

    if (tableId === 'tableRankingProdutores') {
      const data = applyColumnFilters(producerRankingRows(), 'tableRankingProdutores');
      return {
        headers: ['#', 'ID (Código LR)', 'Produtor(a)', 'Consultor(a)', 'Fazenda', 'Nº Visitas', '% do Total', 'Última Visita'],
        rows: data.map((r) => [r.posicao, r.codigo_lr, r.produtor, r.consultor, r.propriedade, r.total_visitas, r.perc_total_label, r.data_ultima_visita])
      };
    }

    if (tableId === 'tableRankingConsultores') {
      const data = applyColumnFilters(consultantRankingRows(), 'tableRankingConsultores');
      return {
        headers: ['#', 'Consultor(a)', 'Nº Visitas', 'Produtores Visitados', 'Carteira Ativa', '% Cobertura', 'Visitas / Produtor'],
        rows: data.map((r) => [r.posicao, r.consultor, r.total_visitas, r.fazendas_visitadas, r.total_fazendas, r.perc_cobertura_label, r.visitas_por_produtor_label])
      };
    }

    if (tableId === 'tableCoeDetalhe') {
      let data = state.economics?.coe_detalhe || [];
      data = applyColumnFilters(data, 'tableCoeDetalhe');
      return {
        headers: ['Fazenda', 'Produtor(a)', 'Mês Referência', 'Volume (L/mês)', 'COE total (R$)', 'COE (R$/L)', 'Concentrado (%)', 'Volumoso (%)', 'Mão de obra (%)', 'Sanidade (%)', 'Outras despesas (%)', 'Maior item', 'Consistência'],
        rows: data.map((r) => [
          r.nome_fazenda || '—', r.produtor || '—', r.mes_label || '—', number(r.volume_leite_mes),
          number(r.coe_total_reais), number(r.coe_por_litro), r.perc_concentrado, r.perc_volumoso,
          r.perc_mao_de_obra, r.perc_sanidade, r.perc_outros, r.maior_item || '—', r.consistencia_mensal || '—'
        ])
      };
    }

    if (tableId === 'tableMbRankingDetalhe') {
      const economics = state.economics || {};
      let data = economics.top10_mb_ranking_tabela || economics.top10_mb_ranking || [];
      data = applyColumnFilters(data, 'tableMbRankingDetalhe');
      return {
        headers: ['#', 'Fazenda', 'Produtor(a)', 'Consultor(a)', 'Mês Referência', 'Volume Diário (L)', 'Preço Médio (R$/L)', 'COE (R$/L)', 'Margem Bruta (R$/L)', 'Consistência'],
        rows: data.map((r) => [
          r.posicao ?? '—',
          r.nome_fazenda || '—',
          r.produtor || '—',
          r.consultor || '—',
          r.mes_label || '—',
          number(r.volume_diario_litros),
          number(r.preco_medio_litro),
          number(r.coe_por_litro),
          number(r.margem_bruta_por_litro),
          r.consistencia_mensal || '—'
        ])
      };
    }

    if (tableId === 'tableProdCcs' || tableId === 'tableProdCpp') {
      const kind = tableId === 'tableProdCcs' ? 'ccs' : 'cpp';
      const data = sortRows(applyColumnFilters(productionAttentionRows(kind), tableId), tableSort[PROD_TABLES[tableId].tbody], (row, key) => row[key]);
      return {
        headers: ['ID (Código LR)', 'Produtor(a)', 'Fazenda', 'Consultor(a)', 'Região', kind === 'ccs' ? 'CCS (x1.000 céls./mL)' : 'CPP (x1.000 UFC/mL)'],
        rows: data.map((r) => [r.codigo_lr, r.produtor, r.propriedade, r.consultor, r.regiao, r.valor])
      };
    }

    if (tableId === 'tableTrajetoria') {
      const data = sortRows(applyColumnFilters(trajectoryRows(), 'tableTrajetoria'), tableSort.tbodyTrajetoria, (row, key) => row[key]);
      const t = state.production?.trajetoria;
      const rotulo = (lista) => (lista && lista.length ? `${lista[0]}–${lista[lista.length - 1]}` : '');
      return {
        headers: ['ID (Código LR)', 'Produtor(a)', 'Fazenda', 'Consultor(a)', 'Região', `Média ${rotulo(t?.janelaAnterior)} (L/dia)`, `Média ${rotulo(t?.janelaRecente)} (L/dia)`, 'Variação (%)'],
        rows: data.map((r) => [r.codigo_lr, r.produtor, r.propriedade, r.consultor, r.regiao, r.anterior, r.recente, r.variacao])
      };
    }

    return null;
  }

  function exportTableToCsv(tableId, defaultFilename) {
    const exportData = getTableDataForExport(tableId);
    if (!exportData || !exportData.rows.length) {
      alert('Nenhum dado disponível para exportação com os filtros atuais.');
      return;
    }

    const csvLines = [exportData.headers.map((h) => `"${h.replace(/"/g, '""')}"`).join(';')];
    exportData.rows.forEach((rowVals) => {
      csvLines.push(rowVals.map((v) => `"${String(v ?? '—').trim().replace(/\r?\n/g, ' | ').replace(/"/g, '""')}"`).join(';'));
    });

    const bom = '\uFEFF';
    const blob = new Blob([bom + csvLines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const d = new Date();
    const dateStr = `${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, '0')}_${String(d.getDate()).padStart(2, '0')}_${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`;
    link.href = url;
    link.download = `${dateStr}_${defaultFilename || 'export'}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function exportInconsistenciesToCsv() {
    const filter = currentFilter();
    let inconsistencies = (state.consistency?.tabelaInconsistentes || []).filter((row) => matches(row, filter));
    if (!inconsistencies.length) {
      alert('Nenhum dado disponível para exportação com os filtros atuais.');
      return;
    }
    inconsistencies = sortRows(inconsistencies, tableSort.tbodyInconsistencies, (row, key) => key === 'produtor' ? (row.produtor || row.codigo_lr) : row[key]);

    const headers = [
      'ID (Código LR)',
      'Produtor(a)',
      'Consultor(a)',
      'Projeto',
      'Agroindústria',
      'Região',
      'Mês Referência',
      'Meses Consecutivos',
      'Situação Mensal',
      'Situação Anual',
      'Detalhamento Mensal',
      'Detalhamento Anual'
    ];

    const csvLines = [headers.map((h) => `"${h.replace(/"/g, '""')}"`).join(';')];

    inconsistencies.forEach((row) => {
      const prodName = row.produtor || row.codigo_lr || '—';
      const detMensal = row.detalhamento || 'Nenhum detalhamento registrado na base mensal.';
      const detAnual = row.detalhamento_anual || 'Nenhum detalhamento registrado na base anual.';
      const values = [
        row.codigo_lr || '—',
        prodName,
        row.consultor || '—',
        row.projeto || '—',
        row.agroindustria || '—',
        row.regiao || '—',
        row.mes_referencia || '—',
        row.meses_sequenciais ?? '0',
        row.consistencia_mensal || 'SEM DADOS',
        row.consistencia_anual || 'SEM DADOS',
        detMensal,
        detAnual
      ];
      csvLines.push(values.map((v) => `"${String(v).trim().replace(/\r?\n/g, ' | ').replace(/"/g, '""')}"`).join(';'));
    });

    const bom = '\uFEFF';
    const blob = new Blob([bom + csvLines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const d = new Date();
    const dateStr = `${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, '0')}_${String(d.getDate()).padStart(2, '0')}_${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`;
    link.href = url;
    link.download = `${dateStr}_consistencia_fazendas.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function setupExportButtons() {
    el('btnExportSemVisita')?.addEventListener('click', () => exportTableToCsv('tableSemVisita', 'produtores_sem_visita'));
    el('btnExportVisitados')?.addEventListener('click', () => exportTableToCsv('tableVisitados', 'produtores_visitados'));
    el('btnExportRankingVisitas')?.addEventListener('click', () => (state.rankingDimension === 'consultant'
      ? exportTableToCsv('tableRankingConsultores', 'ranking_visitas_consultores')
      : exportTableToCsv('tableRankingProdutores', 'ranking_visitas_produtores')));
    el('btnExportTurnover')?.addEventListener('click', () => exportTableToCsv('tableTurnover', 'movimentacao_produtores'));
    el('btnExportConsultants')?.addEventListener('click', () => exportTableToCsv('tableConsultants', 'consultores_ativos'));
    el('btnExportDataProducers')?.addEventListener('click', () => exportTableToCsv('tableDataProducers', 'produtores_com_dados'));
    el('btnExportInconsistencies')?.addEventListener('click', exportInconsistenciesToCsv);
    el('btnExportCadastroDetalhe')?.addEventListener('click', () => exportTableToCsv('tableCadastroDetalhe', 'tempo_cadastro_produtores'));
    el('btnExportCoeDetalhe')?.addEventListener('click', () => exportTableToCsv('tableCoeDetalhe', 'composicao_coe_fazendas'));
    el('btnExportMbRankingDetalhe')?.addEventListener('click', () => exportTableToCsv('tableMbRankingDetalhe', 'top10_margem_bruta'));
    el('btnExportProdCcs')?.addEventListener('click', () => exportTableToCsv('tableProdCcs', 'ccs_acima_do_limite'));
    el('btnExportProdCpp')?.addEventListener('click', () => exportTableToCsv('tableProdCpp', 'cpp_acima_do_limite'));
    el('btnExportTrajetoria')?.addEventListener('click', () => exportTableToCsv('tableTrajetoria', `trajetoria_produtores_${state.trajectoryGroup || 'lista'}`));
  }

  function updateTimestamp() {
    const now = new Date();
    if (el('lastUpdateDate')) el('lastUpdateDate').textContent = `${now.toLocaleDateString('pt-BR')} ${now.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
  }

  async function fetchMonthMasterData() {
    let selectedMonth = el('filterMonth')?.value || '';
    if (!selectedMonth && !state.hasUserChangedMonth) {
      const now = new Date();
      selectedMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
      const select = el('filterMonth');
      if (select) select.value = selectedMonth;
    }

    const query = selectedMonth ? `?month=${encodeURIComponent(selectedMonth)}` : '';
    // Econômico já sai junto (sem await): se loadAllData pedir a mesma query, reaproveita.
    state.masterQuery = query;
    state.masterEconomicsRequest = getJson(`/api/economics${query}`);
    const [overview, visits, turnover, consistency] = await Promise.all([
      getJson(`/api/overview${query}`),
      getJson(`/api/visits${query}`),
      getJson(`/api/turnover${query}`),
      getJson(`/api/consistency${query}`)
    ]);

    state.masterOverview = overview;
    state.masterVisits = visits;
    state.masterTurnover = turnover;
    state.masterConsistency = consistency;
    state.masterRows = extractMasterRows(overview, visits, turnover, consistency);

    if (overview?.filterOptions?.meses) {
      if (!state.allAvailableMonths || state.allAvailableMonths.length === 0) {
        state.allAvailableMonths = overview.filterOptions.meses;
      } else {
        state.allAvailableMonths = [...new Set([...state.allAvailableMonths, ...overview.filterOptions.meses])];
      }
      populateMonthSelect(state.allAvailableMonths);
    }
  }

  function renderEconomics(data) {
    if (!data) return;
    const slide4 = data.slide4 || {};
    const slide5 = data.slide5 || {};
    const kpi = data.kpis || slide4.kpis || {};

    const notice = el('econEmptyNotice');
    if (notice) {
      const aviso = data.aviso || {};
      notice.hidden = !aviso.sem_dados_consistentes;
      if (aviso.sem_dados_consistentes) {
        const ultimo = aviso.ultimo_mes_consistente_label ? ` Último mês disponível: ${aviso.ultimo_mes_consistente_label}.` : '';
        const parcial = aviso.linhas_no_mes > 0 ? ` (${aviso.linhas_no_mes} lançamento(s) ainda inconsistente(s))` : '';
        const texto = el('econEmptyNoticeText');
        if (texto) texto.textContent = `Sem dados econômicos consistentes em ${aviso.mes_selecionado_label}${parcial}.${ultimo}`;
      }
    }

    updateValue('kpiEconVolTotal', number(kpi.volume_diario_total));
    updateValue('kpiEconProdutividade', number(kpi.produtividade_l_vl_dia));
    updateValue('kpiEconPrecoMedio', kpi.preco_medio_litro !== undefined ? `R$ ${kpi.preco_medio_litro}` : '—');
    updateValue('kpiEconCoeMedio', kpi.coe_medio_litro !== undefined ? `R$ ${kpi.coe_medio_litro}` : '—');
    updateValue('kpiEconMargemBruta', kpi.margem_bruta_litro !== undefined ? `R$ ${kpi.margem_bruta_litro}` : '—');
    updateValue('kpiEconPercMbPositiva', percent(kpi.perc_mb_positiva));

    const top5Coe = data.top5_coe || slide4.top5_coe;
    const volEvolutionRaw = data.volume_evolution || slide4.volume_evolution;
    const cadBreakdown = data.cadastro_breakdown || slide5.cadastro_breakdown;
    const mbRanking = data.top10_mb_ranking || slide5.top10_mb_ranking;

    if (top5Coe) charts.renderTop5CostsPie('chartTop5CostsPie', top5Coe);
    if (volEvolutionRaw) {
      const slicedVol = sliceTimeSeries(volEvolutionRaw, state.chartHorizons.volume || 6);
      charts.renderVolumeEvolution('chartVolumeEvolution', slicedVol);
    }
    if (cadBreakdown) charts.renderCadastroDonut('chartCadastroBreakdownDonut', cadBreakdown);
    if (mbRanking) charts.renderMbRankingHorizontal('chartMbRankingHorizontal', mbRanking);
    // As tabelas detalhadas (cadastro_detalhe / top10_mb_ranking) são renderizadas em
    // renderTables(), junto com as demais tabelas do dashboard (mesmo padrão de
    // ordenação/filtro/paginação/exportação das páginas 1–3).
  }

  let debounceFilterTimer = null;
  let loadSequence = 0;

  async function loadAllData(isFilterChange = false) {
    showLoading(isFilterChange ? 'Atualizando dashboard com filtros...' : 'Carregando dados...');
    const loadId = ++loadSequence;

    try {
      let masterJustFetched = false;
      if (!state.masterRows || state.masterRows.length === 0) {
        await fetchMonthMasterData();
        updateAllCrossFilters();
        masterJustFetched = true;
      }

      const filter = currentFilter();
      const params = new URLSearchParams();
      if (filter.month) params.set('month', filter.month);
      // Multi-seleção viaja em um único parâmetro, valores separados por "|" (ex.: consultant=Ana|Bruno)
      if (filter.industry.length) params.set('industry', filter.industry.join('|'));
      if (filter.region.length) params.set('region', filter.region.join('|'));
      if (filter.project.length) params.set('project', filter.project.join('|'));
      if (filter.consultant.length) params.set('consultant', filter.consultant.join('|'));
      if (filter.status) params.set('status', filter.status);
      if (filter.producer.length) params.set('producer', filter.producer.join('|'));

      const query = params.toString() ? `?${params.toString()}` : '';
      // Na carga inicial a busca "master" acabou de trazer exatamente estes dados:
      // reaproveita em vez de repetir as mesmas requisições.
      const reuseMaster = masterJustFetched && query === state.masterQuery;
      const economicsRequest = reuseMaster ? state.masterEconomicsRequest : getJson(`/api/economics${query}`);
      const [overview, visits, turnover, consistency] = reuseMaster
        ? [state.masterOverview, state.masterVisits, state.masterTurnover, state.masterConsistency]
        : await Promise.all([
          getJson(`/api/overview${query}`),
          getJson(`/api/visits${query}`),
          getJson(`/api/turnover${query}`),
          getJson(`/api/consistency${query}`)
        ]);
      if (loadId !== loadSequence) return; // um filtro mais novo já está carregando
      state.overview = overview;
      state.visits = visits;
      state.turnover = turnover;
      state.consistency = consistency;
      renderVisits(visits);
      renderConsistency(consistency);
      renderOverview(overview);
      renderTurnover(turnover);
      renderTables();
      updateTimestamp();
      hideLoading(0);

      // Produção/qualidade e território (slides 5 e 6) também chegam sem travar o restante
      if (window.LRProducao) {
        Promise.all([getJson(`/api/production${query}`), getJson(`/api/territory${query}`)]).then(([production, territory]) => {
          if (loadId !== loadSequence) return;
          state.production = production;
          window.LRProducao.renderProducao(production);
          renderProductionTables();
          window.LRProducao.renderTerritorio(territory);
        });
      }

      // Econômico chega depois sem travar o restante do dashboard
      const economics = await economicsRequest;
      if (loadId !== loadSequence) return;
      state.economics = economics;
      renderEconomics(economics);
      renderTables();
    } finally {
      if (loadId === loadSequence) hideLoading(0);
    }
  }

  function handleFilterSelectionChange() {
    updateAllCrossFilters();
    Object.keys(tablePagination).forEach((k) => { tablePagination[k].page = 1; });
    clearTimeout(debounceFilterTimer);
    debounceFilterTimer = setTimeout(() => {
      loadAllData(true);
    }, 50);
  }

  function closeAllPopups() {
    // Filtros multi-seleção só recarregam o dashboard ao fechar o popup (evita um reload por clique)
    document.querySelectorAll('.filter-control.active-popup').forEach((fc) => fc.dispatchEvent(new CustomEvent('multicommit')));
    document.querySelectorAll('.custom-select-popup').forEach((p) => p.classList.remove('open'));
    document.querySelectorAll('.filter-control').forEach((fc) => {
      fc.classList.remove('active-popup');
      fc.setAttribute('aria-expanded', 'false');
    });
  }

  function normalizeText(str) {
    return String(str || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .trim();
  }

  function setupCustomSelectDropdowns() {
    const filterControls = document.querySelectorAll('.filter-control');
    
    filterControls.forEach((control) => {
      const select = control.querySelector('select');
      if (!select) return;

      if (!control.hasAttribute('tabindex')) {
        control.setAttribute('tabindex', '0');
        control.setAttribute('role', 'combobox');
        control.setAttribute('aria-expanded', 'false');
        control.setAttribute('aria-haspopup', 'listbox');
      }

      let displayValue = control.querySelector('.select-display-value');
      if (!displayValue) {
        displayValue = document.createElement('div');
        displayValue.className = 'select-display-value';
        control.appendChild(displayValue);
      }

      const multi = select.multiple;
      let multiDirty = false;
      // Excel-like: após ↑/↓ o foco "está na lista" e a barra de espaço marca a linha; digitar volta a pesquisar
      let modoLista = false;

      function syncDisplayValue() {
        if (multi) {
          displayValue.textContent = multiDisplayText(select);
          displayValue.title = [...select.selectedOptions].map((opt) => opt.text).join(', ');
          return;
        }
        const selectedOpt = select.options[select.selectedIndex];
        displayValue.textContent = selectedOpt ? selectedOpt.text : (select.value || '');
      }

      syncDisplayValue();

      let popup = control.querySelector('.custom-select-popup');
      if (!popup) {
        popup = document.createElement('div');
        popup.className = 'custom-select-popup';
        control.appendChild(popup);
      }

      select.onchange = syncDisplayValue;

      if (control.dataset.customSelectInitialized === 'true') {
        return;
      }
      control.dataset.customSelectInitialized = 'true';

      let highlightedIndex = 0;

      function highlightOption(index, scroll = true) {
        const optionsList = popup.querySelector('.custom-select-options-list');
        if (!optionsList) return;
        const optEls = optionsList.querySelectorAll('.custom-select-option');
        if (optEls.length === 0) return;

        if (index < 0) index = 0;
        if (index >= optEls.length) index = optEls.length - 1;
        highlightedIndex = index;

        optEls.forEach((el, idx) => {
          if (idx === highlightedIndex) {
            el.classList.add('highlighted');
            if (scroll) {
              el.scrollIntoView({ block: 'nearest' });
            }
          } else {
            el.classList.remove('highlighted');
          }
        });
      }

      // Multi-seleção: alterna a opção (ou marca/desmarca tudo com o marcador "__all__") sem fechar o popup
      function toggleMultiOption(val) {
        if (val === '__all__') {
          // "Todos": marca todas as opções; se já estavam todas marcadas, desmarca
          const marcar = !allOptionsSelected(select);
          [...select.options].forEach((opt) => { opt.selected = marcar; });
        } else if (val === '__results__') {
          // Com uma pesquisa ativa: marca (ou desmarca) só as opções encontradas
          const consulta = normalizeText(popup.querySelector('.custom-select-search-input')?.value || '');
          const encontradas = [...select.options].filter((opt) => normalizeText(opt.text).includes(consulta));
          const marcar = !encontradas.every((opt) => opt.selected);
          encontradas.forEach((opt) => { opt.selected = marcar; });
        } else {
          const opt = [...select.options].find((o) => o.value === val);
          if (opt) opt.selected = !opt.selected;
        }
        multiDirty = true;
        syncDisplayValue();
        updatePopupOptions(popup.querySelector('.custom-select-search-input')?.value || '', true);
      }

      control.addEventListener('multicommit', () => {
        if (!multiDirty) return;
        multiDirty = false;
        showLoading('Atualizando dashboard com filtros...');
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });

      function selectOptionByValue(val) {
        if (multi) {
          toggleMultiOption(val);
          return;
        }
        select.value = val;
        syncDisplayValue();
        closeAllPopups();
        showLoading('Atualizando dashboard com filtros...');
        select.dispatchEvent(new Event('change', { bubbles: true }));
        control.focus();
      }

      function updatePopupOptions(searchQuery = '', keepHighlight = false) {
        const options = Array.from(select.options);
        const queryNorm = normalizeText(searchQuery);

        const currentFilteredOptions = options.filter(opt => {
          if (!queryNorm) return true;
          return normalizeText(opt.text).includes(queryNorm);
        });

        let searchWrap = popup.querySelector('.custom-select-search-wrap');
        let optionsList = popup.querySelector('.custom-select-options-list');

        if (!searchWrap || !optionsList) {
          popup.innerHTML = `
            <div class="custom-select-search-wrap">
              <input type="text" class="custom-select-search-input" placeholder="Pesquisar..." aria-label="Pesquisar opção">
            </div>
            <div class="custom-select-options-list"></div>
            ${multi ? '<div class="custom-select-footer"><button type="button" class="custom-select-apply">Aplicar</button></div>' : ''}
          `;
          popup.querySelector('.custom-select-apply')?.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();
            closeAllPopups(); // fecha e aplica a seleção (evento multicommit)
            control.focus();
          });
          searchWrap = popup.querySelector('.custom-select-search-wrap');
          optionsList = popup.querySelector('.custom-select-options-list');

          const inputEl = searchWrap.querySelector('.custom-select-search-input');
          inputEl.addEventListener('click', (e) => e.stopPropagation());
          inputEl.addEventListener('mousedown', (e) => e.stopPropagation());
          
          inputEl.addEventListener('keydown', (e) => {
            e.stopPropagation();
            const key = e.key;
            const isDown = key === 'ArrowDown' || key === 'Down' || e.keyCode === 40;
            const isUp = key === 'ArrowUp' || key === 'Up' || e.keyCode === 38;
            const isEnter = key === 'Enter' || e.keyCode === 13;
            const isEsc = key === 'Escape' || key === 'Esc' || e.keyCode === 27;
            const isTab = key === 'Tab' || e.keyCode === 9;

            const isSpace = key === ' ' || key === 'Spacebar' || e.keyCode === 32;
            const optsList = popup.querySelector('.custom-select-options-list');
            const optEls = optsList ? optsList.querySelectorAll('.custom-select-option') : [];

            if (isDown) {
              e.preventDefault();
              modoLista = true;
              highlightOption(highlightedIndex + 1, true);
            } else if (isUp) {
              e.preventDefault();
              modoLista = true;
              highlightOption(highlightedIndex - 1, true);
            } else if (isSpace && multi && (modoLista || !inputEl.value)) {
              // Espaço marca/desmarca a linha destacada (com texto digitado e sem navegar, o espaço é só um caractere)
              e.preventDefault();
              const alvo = optEls[highlightedIndex];
              if (alvo) toggleMultiOption(alvo.dataset.value);
            } else if (isEnter && multi) {
              // Enter aplica a seleção (como o botão Aplicar)
              e.preventDefault();
              closeAllPopups();
              control.focus();
            } else if (isEnter) {
              e.preventDefault();
              if (optEls.length > 0) {
                const targetIdx = Math.max(0, Math.min(highlightedIndex, optEls.length - 1));
                const targetEl = optEls[targetIdx];
                if (targetEl && targetEl.dataset.value !== undefined) {
                  selectOptionByValue(targetEl.dataset.value);
                }
              }
            } else if (isEsc) {
              e.preventDefault();
              closeAllPopups();
              control.focus();
            } else if (isTab) {
              closeAllPopups();
            }
          });

          inputEl.addEventListener('input', (e) => {
            modoLista = false;
            updatePopupOptions(e.target.value);
          });
        }

        // Multi-seleção: linha "Todos" (marca/desmarca tudo). Aparece sem pesquisa ou quando a pesquisa
        // combina com o rótulo ("todos"); com pesquisa, também há "Selecionar resultados" só para o que foi encontrado.
        const allLabel = select.dataset.allLabel || 'Todos';
        const linhaMulti = (valor, texto, marcada) => `<div class="custom-select-option is-multi is-all ${marcada ? 'selected' : ''}" data-value="${valor}" data-index="-1">
              <span class="multi-check" aria-hidden="true">${marcada ? '✓' : ''}</span>
              <span>${escapeHtml(texto)}</span>
            </div>`;
        let extras = '';
        if (multi && (!queryNorm || normalizeText(allLabel).includes(queryNorm))) {
          extras += linhaMulti('__all__', allLabel, allOptionsSelected(select));
        }
        if (multi && queryNorm && currentFilteredOptions.length > 0) {
          extras += linhaMulti('__results__', `Selecionar resultados (${currentFilteredOptions.length})`, currentFilteredOptions.every(opt => opt.selected));
        }

        if (currentFilteredOptions.length === 0) {
          optionsList.innerHTML = extras || `<div class="custom-select-no-results">Nenhum resultado encontrado</div>`;
          highlightedIndex = -1;
        } else {
          const previousHighlight = highlightedIndex;
          const selectedIdx = multi
            ? currentFilteredOptions.findIndex(opt => opt.selected)
            : currentFilteredOptions.findIndex(opt => opt.value === select.value);
          highlightedIndex = keepHighlight && previousHighlight >= 0 ? previousHighlight : (selectedIdx >= 0 ? selectedIdx : 0);
          const extrasCount = (extras.match(/is-all/g) || []).length;
          if (!(keepHighlight && previousHighlight >= 0)) highlightedIndex += extrasCount; // posição na lista (após as linhas extras)

          const rowsHtml = currentFilteredOptions.map((opt, idx) => {
            const isSelected = multi ? opt.selected : opt.value === select.value;
            const isHighlighted = idx + (extras.match(/is-all/g) || []).length === highlightedIndex;
            return `<div class="custom-select-option ${multi ? 'is-multi' : ''} ${isSelected ? 'selected' : ''} ${isHighlighted ? 'highlighted' : ''}" data-value="${escapeHtml(opt.value)}" data-index="${idx}">
              ${multi ? `<span class="multi-check" aria-hidden="true">${isSelected ? '✓' : ''}</span>` : ''}
              <span>${escapeHtml(opt.text)}</span>
              ${!multi && isSelected ? '<span style="font-size:10px;">✓</span>' : ''}
            </div>`;
          }).join('');
          optionsList.innerHTML = extras + rowsHtml;
          optionsList.querySelectorAll('.custom-select-option').forEach((row, pos) => { row.dataset.index = String(pos); });

          highlightOption(highlightedIndex, !keepHighlight);
        }

        optionsList.querySelectorAll('.custom-select-option').forEach((optEl) => {
          const idx = Number(optEl.dataset.index);
          optEl.addEventListener('mouseenter', () => {
            if (idx >= 0) highlightOption(idx, false);
          });

          optEl.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();
            const val = optEl.dataset.value;
            selectOptionByValue(val);
          });
        });
      }

      function togglePopup(e) {
        if (e) {
          e.preventDefault();
          e.stopPropagation();
        }
        const isOpen = popup.classList.contains('open');
        closeAllPopups();
        if (!isOpen) {
          modoLista = false;
          updatePopupOptions('');
          popup.classList.add('open');
          control.classList.add('active-popup');
          control.setAttribute('aria-expanded', 'true');
          const searchInput = popup.querySelector('.custom-select-search-input');
          if (searchInput) {
            searchInput.value = '';
            // visibility do popup só vira 'visible' após a transição: focar antes disso é ignorado pelo navegador.
            // preventScroll evita que a página pule; tenta de novo ao fim da transição.
            searchInput.focus({ preventScroll: true });
            setTimeout(() => { if (document.activeElement !== searchInput) searchInput.focus({ preventScroll: true }); }, 180);
          }
        }
      }

      control.addEventListener('click', (e) => {
        if (e.target.closest('.custom-select-popup')) return;
        togglePopup(e);
      });

      control.addEventListener('keydown', (e) => {
        if (e.target.closest('.custom-select-popup')) return;
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          e.stopPropagation();
          togglePopup(e);
        }
      });
    });

    if (!window._customSelectDocumentListenerAttached) {
      window._customSelectDocumentListenerAttached = true;
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.filter-control')) {
          closeAllPopups();
        }
      });
    }
  }

  const mobileFilterToggle = el('mobileFilterToggle');
  const filterShell = el('filterShell');
  if (mobileFilterToggle && filterShell) {
    mobileFilterToggle.addEventListener('click', () => {
      const isExpanded = filterShell.classList.toggle('is-expanded');
      mobileFilterToggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    });
  }

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (window.renderAllCharts && state.renderedData) {
        window.renderAllCharts(state.renderedData);
      }
    }, 250);
  });

  // Eventos de filtros com suporte a filtragem cruzada multidirecional estilo Power BI
  ['filterIndustry', 'filterRegion', 'filterProject', 'filterConsultant', 'filterProducer']
    .forEach((id) => el(id)?.addEventListener('change', handleFilterSelectionChange));
  el('filterMonth')?.addEventListener('change', async () => {
    state.hasUserChangedMonth = true;
    showLoading('Atualizando dashboard com filtros...');
    state.masterRows = null;
    await fetchMonthMasterData();
    updateAllCrossFilters();
    await loadAllData(true);
  });
  document.querySelectorAll('[data-ranking]').forEach((button) => button.addEventListener('click', () => {
    state.rankingDimension = button.dataset.ranking;
    renderSelectedRanking();
  }));

  function setupDetailsModal() {
    const detailsModal = el('detailsModal');
    const modalBody = el('modalBody');
    const modalCancelBtn = el('modalCancelBtn');

    function openDetailsModal(item) {
      if (!item || !detailsModal || !modalBody) return;

      const statusMensalStr = String(item.consistencia_mensal || item.consistencia || 'CONSISTENTE');
      const isMensalInconsistente = isStatusInconsistente(statusMensalStr);
      const isMensalConsistente = isStatusConsistente(statusMensalStr);
      const isMensalSemDados = statusMensalStr.toLowerCase().includes('sem dados') || statusMensalStr.toLowerCase().includes('não calculado');

      const badgeClassMensal = isMensalInconsistente ? 'badge-danger' : (isMensalConsistente ? 'badge-positive' : 'badge-warning');
      const highlightBoxClassMensal = isMensalInconsistente ? 'field-box--danger' : (isMensalSemDados ? 'field-box--warning' : '');

      const statusAnualStr = String(item.consistencia_anual || 'Não calculado');
      const isAnualInconsistente = isStatusInconsistente(statusAnualStr);
      const isAnualConsistente = isStatusConsistente(statusAnualStr);
      const isAnualSemDados = statusAnualStr.toLowerCase().includes('sem dados') || statusAnualStr.toLowerCase().includes('não calculado');

      const badgeClassAnual = isAnualInconsistente ? 'badge-danger' : (isAnualConsistente ? 'badge-positive' : 'badge-warning');
      const highlightBoxClassAnual = isAnualInconsistente ? 'field-box--danger' : (isAnualSemDados ? 'field-box--warning' : '');

      const statusBadge = String(item.status || 'ATIVO').toUpperCase() === 'INATIVO' ? 'badge-danger' : 'badge-positive';

      const refMonthText = item.mes_referencia ? String(item.mes_referencia).slice(0, 7).split('-').reverse().join('/') : '--/----';
      const refMonthEl = el('modalRefMonthText');
      if (refMonthEl) refMonthEl.textContent = refMonthText;

      const mensalDetailText = isMensalConsistente
        ? 'Registros mensais conformes e validados (sem inconsistências apuradas).'
        : (item.detalhamento || 'Nenhum detalhamento registrado na base de auditoria mensal.');

      const anualDetailText = isAnualConsistente
        ? 'Fechamento anual em conformidade (sem inconsistências apuradas).'
        : (item.detalhamento_anual || 'Nenhum detalhamento registrado na base de auditoria anual.');

      modalBody.innerHTML = `
        <fieldset class="modal-fieldset">
          <legend class="modal-legend">Informações do Produtor e Vínculo</legend>
          <div class="detail-grid">
            <div class="detail-field">
              <label class="field-label">Código LR</label>
              <div class="field-box">${escapeHtml(item.codigo_lr || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Produtor(a)</label>
              <div class="field-box field-box--bold">${escapeHtml(item.produtor || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Consultor(a) Técnico(a)</label>
              <div class="field-box">${escapeHtml(item.consultor || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Agroindústria</label>
              <div class="field-box">${escapeHtml(item.agroindustria || item.projeto || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Região Leiteira</label>
              <div class="field-box">${escapeHtml(item.regiao || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Projeto / Programa</label>
              <div class="field-box">${escapeHtml(item.projeto || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Status Cadastral</label>
              <div class="field-box"><span class="badge ${statusBadge}">${escapeHtml(item.status || 'ATIVO')}</span></div>
            </div>
          </div>
        </fieldset>

        <fieldset class="modal-fieldset">
          <legend class="modal-legend">Consistência Mensal</legend>
          <div class="detail-grid">
            <div class="detail-field">
              <label class="field-label">Mês Referência</label>
              <div class="field-box">${escapeHtml(item.mes_referencia || '—')}</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Meses Consecutivos</label>
              <div class="field-box">${number(item.meses_sequenciais)} mês(es)</div>
            </div>
            <div class="detail-field">
              <label class="field-label">Classificação Mensal</label>
              <div class="field-box"><span class="badge ${badgeClassMensal}">${escapeHtml(statusMensalStr)}</span></div>
            </div>
            <div class="detail-field field-full">
              <label class="field-label">Detalhamento da Consistência Mensal</label>
              <div class="field-box ${highlightBoxClassMensal}">${escapeHtml(mensalDetailText)}</div>
            </div>
          </div>
        </fieldset>

        <fieldset class="modal-fieldset">
          <legend class="modal-legend">Consistência Anual</legend>
          <div class="detail-grid">
            <div class="detail-field">
              <label class="field-label">Classificação Anual</label>
              <div class="field-box"><span class="badge ${badgeClassAnual}">${escapeHtml(statusAnualStr)}</span></div>
            </div>
            <div class="detail-field field-full">
              <label class="field-label">Detalhamento da Consistência Anual</label>
              <div class="field-box ${highlightBoxClassAnual}">${escapeHtml(anualDetailText)}</div>
            </div>
          </div>
        </fieldset>
      `;

      detailsModal.classList.add('active');
      detailsModal.setAttribute('aria-hidden', 'false');
    }

    function closeDetailsModal() {
      if (!detailsModal) return;
      detailsModal.classList.remove('active');
      detailsModal.setAttribute('aria-hidden', 'true');
    }

    modalCloseBtn?.addEventListener('click', closeDetailsModal);
    modalOkBtn?.addEventListener('click', closeDetailsModal);
    modalCancelBtn?.addEventListener('click', closeDetailsModal);
    detailsModal?.addEventListener('click', (e) => {
      if (e.target === detailsModal) closeDetailsModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && detailsModal?.classList.contains('active')) {
        closeDetailsModal();
      }
    });

    window.openInconsistencyDetail = function(codigoLr) {
      if (!codigoLr) return;
      const targetCode = String(codigoLr).trim().toLowerCase();
      const list = (state.consistency?.tabelaInconsistentes || []);
      const selectedItem = list.find(r => String(r.codigo_lr || '').trim().toLowerCase() === targetCode)
                        || list.find(r => String(r.produtor || '').trim().toLowerCase() === targetCode);
      if (selectedItem) {
        openDetailsModal(selectedItem);
      }
    };

    el('tbodyInconsistencies')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-view-details');
      if (!btn) return;
      const idx = Number(btn.dataset.idx);
      const filter = getFilterState();
      let inconsistencies = (state.consistency?.tabelaInconsistentes || []).filter((row) => matches(row, filter));
      inconsistencies = sortRows(inconsistencies, tableSort.tbodyInconsistencies, (row, key) => key === 'produtor' ? (row.produtor || row.codigo_lr) : row[key]);
      const selectedItem = inconsistencies[idx];
      if (selectedItem) openDetailsModal(selectedItem);
    });
  }

  function shiftMonthMinus1(monthStr) {
    if (!monthStr) return null;
    const d = new Date(`${String(monthStr).slice(0, 10)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return null;
    d.setMonth(d.getMonth() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
  }

  function getActiveReferenceMonthDisplay(isElabore = false) {
    const select = el('filterMonth');
    let monthValue = (select && select.value) ? select.value : (state.overview?.refMonth || state.visits?.refMonth || '2026-08-01');

    if (isElabore && monthValue) {
      const shifted = shiftMonthMinus1(monthValue);
      if (shifted) monthValue = shifted;
    }

    if (monthValue) {
      const parsed = new Date(`${String(monthValue).slice(0, 10)}T12:00:00`);
      if (!Number.isNaN(parsed.getTime())) {
        return parsed.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' }).replace(/^./, (c) => c.toUpperCase());
      }
      return String(monthValue).slice(0, 7);
    }
    return isElabore ? 'Julho de 2026' : 'Agosto de 2026';
  }

  function setupKpiInfoPopovers() {
    const popover = el('kpiInfoPopover');
    const popoverTitle = el('kpiPopoverTitle');
    const popoverBody = el('kpiPopoverBody');
    const popoverRefBox = el('kpiPopoverRefBox');
    const popoverRefLabel = el('kpiPopoverRefLabel');
    const popoverRefVal = el('kpiPopoverRefVal');
    if (!popover || !popoverTitle || !popoverBody) return;

    let activeBtn = null;

    function showPopover(btn) {
      const title = btn.dataset.infoTitle || 'Informação do Indicador';
      const body = btn.dataset.infoBody || '';
      const isElabore = btn.dataset.infoRefType === 'mes_elabore' || title.toLowerCase().includes('elabore');
      const refMonthDisplay = btn.dataset.infoRef || getActiveReferenceMonthDisplay(isElabore);

      popoverTitle.textContent = title;
      popoverBody.textContent = body;

      if (popoverRefBox && popoverRefVal) {
        if (refMonthDisplay) {
          if (popoverRefLabel) {
            popoverRefLabel.textContent = 'Mês de referência:';
          }
          popoverRefVal.textContent = refMonthDisplay;
          popoverRefBox.style.display = 'flex';
        } else {
          popoverRefBox.style.display = 'none';
        }
      }

      const rect = btn.getBoundingClientRect();
      const scrollX = window.scrollX || window.pageXOffset;
      const scrollY = window.scrollY || window.pageYOffset;

      popover.style.display = 'block';
      const popoverHeight = popover.offsetHeight || 110;
      const popoverWidth = popover.offsetWidth || 270;

      let top = rect.top + scrollY - popoverHeight - 8;
      let left = rect.left + scrollX + (rect.width / 2) - (popoverWidth / 2);

      if (rect.top - popoverHeight < 10) {
        top = rect.bottom + scrollY + 8;
      }
      if (left < 10) left = 10;
      if (left + popoverWidth > window.innerWidth - 10) {
        left = window.innerWidth - popoverWidth - 10;
      }

      popover.style.top = `${top}px`;
      popover.style.left = `${left}px`;
      popover.classList.add('active');
      popover.setAttribute('aria-hidden', 'false');
      activeBtn = btn;
    }

    function hidePopover() {
      popover.classList.remove('active');
      popover.setAttribute('aria-hidden', 'true');
      activeBtn = null;
    }

    document.addEventListener('mouseover', (e) => {
      const btn = e.target.closest('.kpi-info-btn');
      if (btn && activeBtn !== btn) {
        showPopover(btn);
      }
    });

    document.addEventListener('mouseout', (e) => {
      const btn = e.target.closest('.kpi-info-btn');
      if (btn && activeBtn === btn) {
        const related = e.relatedTarget;
        if (!popover.contains(related) && (!related || !related.closest('.kpi-info-btn'))) {
          hidePopover();
        }
      }
    });

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.kpi-info-btn');
      if (btn) {
        e.stopPropagation();
        if (activeBtn === btn) {
          hidePopover();
        } else {
          showPopover(btn);
        }
        return;
      }
      if (activeBtn && !popover.contains(e.target)) {
        hidePopover();
      }
    });

    window.addEventListener('scroll', hidePopover, { passive: true });
  }

  function setupProvenanceModal() {
    const modalOverlay = el('modalProvenanceOverlay');
    const btnProvenance = el('btnProvenance');
    const closeBtn = el('provCloseBtn');
    const okBtn = el('provOkBtn');
    const groupsContainer = el('provGroupsContainer');
    const totalBadge = el('provTotalFilesBadge');
    const summaryDir = el('provSummaryDir');
    const summaryInspection = el('provSummaryInspection');

    function openModal() {
      modalOverlay.classList.add('active');
      modalOverlay.setAttribute('aria-hidden', 'false');
      renderProvenanceData();
    }

    function closeModal() {
      modalOverlay.classList.remove('active');
      modalOverlay.setAttribute('aria-hidden', 'true');
    }

    btnProvenance?.addEventListener('click', openModal);
    closeBtn?.addEventListener('click', closeModal);
    okBtn?.addEventListener('click', closeModal);
    modalOverlay?.addEventListener('click', (e) => {
      if (e.target === modalOverlay) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modalOverlay?.classList.contains('active')) {
        closeModal();
      }
    });

    async function renderProvenanceData() {
      let data = state.overview?.dataProvenance;
      if (!data) {
        data = await getJson('/data/fontes_metadados.json');
      }
      if (!data || !data.arquivos || data.arquivos.length === 0) {
        if (groupsContainer) {
          groupsContainer.innerHTML = '<div class="provenance-loading-state">Nenhum metadado de planilha disponível no momento.</div>';
        }
        return;
      }

      if (totalBadge) totalBadge.textContent = String(data.total_arquivos || data.arquivos.length);
      if (summaryDir) summaryDir.textContent = data.diretorio_origem ? '.../' + data.diretorio_origem.split(/[\\/]/).pop() : 'BD_SMARTQUESTION';
      
      const summaryEtl = el('provSummaryEtl');
      if (summaryEtl) {
        if (data.ultima_execucao_etl_formatada) {
          summaryEtl.textContent = data.ultima_execucao_etl_formatada;
        } else {
          const etlTime = data.timestamp_etl || data.timestamp_inspecao;
          if (etlTime) {
            const d = new Date(etlTime);
            summaryEtl.textContent = `${d.toLocaleDateString('pt-BR')} às ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
          }
        }
      }

      if (summaryInspection) {
        if (data.ultima_leitura_planilhas_formatada) {
          summaryInspection.textContent = data.ultima_leitura_planilhas_formatada;
        } else if (data.timestamp_inspecao) {
          const d = new Date(data.timestamp_inspecao);
          summaryInspection.textContent = `${d.toLocaleDateString('pt-BR')} às ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
        }
      }

      const summaryRecentFile = el('provSummaryRecentFile');
      if (summaryRecentFile) {
        if (data.arquivo_mais_recente_nome) {
          const dataFmt = data.arquivo_mais_recente_data ? ` (${data.arquivo_mais_recente_data})` : '';
          summaryRecentFile.textContent = `${data.arquivo_mais_recente_nome}${dataFmt}`;
          summaryRecentFile.setAttribute('title', `Última modificação detectada: ${data.arquivo_mais_recente_nome}`);
        } else {
          summaryRecentFile.textContent = '--';
        }
      }

      // Preencher dados de Consistência Mensal
      const mensalUpdate = el('provMensalUpdate');
      const mensalRecords = el('provMensalRecords');
      const mensalStatus = el('provMensalStatus');
      if (data.consistencia_mensal) {
        if (mensalUpdate) mensalUpdate.textContent = data.consistencia_mensal.ultima_atualizacao_formatada || '--';
        if (mensalRecords) {
          const count = data.consistencia_mensal.total_registros;
          mensalRecords.textContent = count !== null && count !== undefined ? `${Number(count).toLocaleString('pt-BR')} registros` : '--';
        }
        if (mensalStatus && data.consistencia_mensal.status) mensalStatus.textContent = data.consistencia_mensal.status;
      }

      // Preencher dados de Consistência Anual
      const anualUpdate = el('provAnualUpdate');
      const anualRecords = el('provAnualRecords');
      const anualStatus = el('provAnualStatus');
      if (data.consistencia_anual) {
        if (anualUpdate) anualUpdate.textContent = data.consistencia_anual.ultima_atualizacao_formatada || '--';
        if (anualRecords) {
          const count = data.consistencia_anual.total_registros;
          anualRecords.textContent = count !== null && count !== undefined ? `${Number(count).toLocaleString('pt-BR')} registros` : '--';
        }
        if (anualStatus && data.consistencia_anual.status) anualStatus.textContent = data.consistencia_anual.status;
      }

      // Agrupar arquivos por categoria
      const groups = {};
      data.arquivos.forEach(file => {
        const cat = file.categoria || 'Outros Relatórios';
        if (!groups[cat]) {
          groups[cat] = {
            categoria: cat,
            icone: file.icone || 'description',
            descricao: file.descricao || '',
            files: []
          };
        }
        groups[cat].files.push(file);
      });

      let html = '';
      Object.values(groups).forEach(grp => {
        html += `
          <div class="prov-category-card">
            <div class="prov-category-header">
              <div class="prov-category-title-box">
                <span class="material-symbols-rounded prov-cat-icon" aria-hidden="true">${escapeHtml(grp.icone)}</span>
                <span class="prov-category-title">${escapeHtml(grp.categoria)}</span>
              </div>
              <span class="prov-category-count">${grp.files.length} ${grp.files.length === 1 ? 'arquivo' : 'arquivos'}</span>
            </div>
            <div class="prov-file-list">
        `;

        grp.files.forEach(f => {
          const recText = f.total_registros !== null && f.total_registros !== undefined
            ? `${Number(f.total_registros).toLocaleString('pt-BR')} registros`
            : 'Planilha bruta';
          html += `
            <div class="prov-file-item">
              <div class="prov-file-name-col">
                <span class="prov-file-name">${escapeHtml(f.nome)}</span>
                <span class="prov-file-desc">${escapeHtml(f.descricao)}</span>
              </div>
              <div class="prov-file-date">
                <span class="prov-date-label">Exportação SmartQuestion</span>
                <span class="prov-date-val">${escapeHtml(f.data_modificacao_formatada || f.data_modificacao)}</span>
              </div>
              <div>
                <span class="prov-badge-size">${escapeHtml(f.tamanho_formatado)}</span>
              </div>
              <div>
                <span class="prov-badge-records">${escapeHtml(recText)}</span>
              </div>
            </div>
          `;
        });

        html += `
            </div>
          </div>
        `;
      });

      if (groupsContainer) groupsContainer.innerHTML = html;
    }
  }

  function setupChartHorizonControls() {
    document.querySelectorAll('.chart-horizon-control button').forEach((btn) => {
      btn.addEventListener('click', () => {
        const container = btn.closest('.chart-horizon-control');
        if (!container) return;
        const horizonKey = container.dataset.chartHorizon;
        const months = Number(btn.dataset.months) || 0;

        container.querySelectorAll('button').forEach((b) => {
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');

        if (horizonKey && state.chartHorizons) {
          state.chartHorizons[horizonKey] = months;
        }

        if (horizonKey === 'coverage' && state.overview) {
          const coverageData = sliceTimeSeries(state.overview.evolucaoMensal || emptyState.overview.evolucaoMensal, months);
          charts.renderCoverage('chartVisitsCoverage', coverageData);
        } else if (horizonKey === 'visits' && state.overview) {
          renderVisitsEvolution();
        } else if (horizonKey === 'turnover' && state.turnover) {
          const turnoverData = sliceTimeSeries(state.turnover.historicoMovimentacao || emptyState.turnover.historicoMovimentacao, months);
          charts.renderTurnover('chartTurnoverHistory', turnoverData);
        } else if (horizonKey === 'portfolio' && state.turnover) {
          const portfolioData = sliceTimeSeries(state.turnover.historicoCarteira || emptyState.turnover.historicoCarteira, months);
          charts.renderPortfolio('chartPortfolioHistory', portfolioData);
        } else if (horizonKey === 'consistency' && state.consistency) {
          const consistencyData = sliceTimeSeries(state.consistency.evolucaoConsistencia || emptyState.consistency.evolucaoConsistencia, months);
          charts.renderConsistencyHistory('chartConsistencyHistory', consistencyData);
        } else if (horizonKey === 'volume' && state.economics) {
          const raw = state.economics.volume_evolution || state.economics.slide4?.volume_evolution;
          const volData = sliceTimeSeries(raw, months);
          charts.renderVolumeEvolution('chartVolumeEvolution', volData);
        }
      });
    });
  }

  function setupPanelFullscreen() {
    let activeFullscreenPanel = null;
    const fsBackdrop = el('panelFsBackdrop');

    function openPanelFullscreen(panel) {
      if (!panel) return;
      if (activeFullscreenPanel && activeFullscreenPanel !== panel) {
        closePanelFullscreen();
      }

      // 1. Criar marcador de posição no local original do DOM
      const placeholder = document.createElement('div');
      placeholder.className = 'panel-fs-placeholder';
      placeholder.style.display = 'none';
      panel.parentNode.insertBefore(placeholder, panel);
      panel._fsPlaceholder = placeholder;

      // 2. Mover o painel diretamente para document.body (escapa do transform do carrossel)
      document.body.appendChild(panel);

      panel.classList.add('is-fullscreen');
      if (fsBackdrop) {
        fsBackdrop.removeAttribute('hidden');
      }

      // 3. Adicionar botão explícito de fechar na parte superior
      let closeBtn = panel.querySelector('.panel-fullscreen-close-btn');
      if (!closeBtn) {
        closeBtn = document.createElement('button');
        closeBtn.className = 'panel-fullscreen-close-btn';
        closeBtn.type = 'button';
        closeBtn.title = 'Fechar tela cheia (Esc)';
        closeBtn.setAttribute('aria-label', 'Fechar tela cheia');
        closeBtn.innerHTML = '<span class="material-symbols-rounded">close</span><span>Fechar</span>';
        closeBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          closePanelFullscreen();
        });
        panel.appendChild(closeBtn);
      }

      const fsBtn = panel.querySelector('.btn-panel-fs');
      if (fsBtn) {
        fsBtn.setAttribute('title', 'Sair da tela cheia (Esc)');
        fsBtn.setAttribute('aria-label', 'Sair do modo tela cheia');
        const icon = fsBtn.querySelector('.material-symbols-rounded');
        if (icon) icon.textContent = 'fullscreen_exit';
      }

      // Pausa carrossel durante a inspeção em tela cheia (S-01)
      carousel?.pauseForModal?.(true);

      activeFullscreenPanel = panel;

      // Redimensionar gráficos instantaneamente
      setTimeout(() => {
        panel.querySelectorAll('canvas').forEach((canvas) => {
          const chartInstance = charts.instances[canvas.id];
          if (chartInstance && typeof chartInstance.resize === 'function') {
            chartInstance.resize();
            if (typeof chartInstance.update === 'function') {
              chartInstance.update('none');
            }
          }
        });
      }, 60);
    }

    function closePanelFullscreen() {
      if (!activeFullscreenPanel) return;
      const panel = activeFullscreenPanel;

      // Remover botão de fechar criado dinamicamente
      const closeBtn = panel.querySelector('.panel-fullscreen-close-btn');
      if (closeBtn) {
        closeBtn.remove();
      }

      panel.classList.remove('is-fullscreen');

      if (fsBackdrop) {
        fsBackdrop.setAttribute('hidden', '');
      }

      const fsBtn = panel.querySelector('.btn-panel-fs');
      if (fsBtn) {
        fsBtn.setAttribute('title', 'Tela cheia');
        fsBtn.setAttribute('aria-label', 'Expandir painel para tela cheia');
        const icon = fsBtn.querySelector('.material-symbols-rounded');
        if (icon) icon.textContent = 'fullscreen';
      }

      // Devolver o painel para a sua posição original no slide
      if (panel._fsPlaceholder && panel._fsPlaceholder.parentNode) {
        panel._fsPlaceholder.parentNode.insertBefore(panel, panel._fsPlaceholder);
        panel._fsPlaceholder.remove();
        delete panel._fsPlaceholder;
      }

      // Retoma o carrossel se não estiver pausado pelo usuário
      carousel?.pauseForModal?.(false);

      activeFullscreenPanel = null;

      // Restaurar dimensões dos gráficos
      setTimeout(() => {
        panel.querySelectorAll('canvas').forEach((canvas) => {
          const chartInstance = charts.instances[canvas.id];
          if (chartInstance && typeof chartInstance.resize === 'function') {
            chartInstance.resize();
            if (typeof chartInstance.update === 'function') {
              chartInstance.update('none');
            }
          }
        });
      }, 60);
    }

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-panel-fs');
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        const panel = btn.closest('.panel-card');
        if (!panel) return;
        if (panel.id === 'panelElabore') {
          closeElabore();
          return;
        }
        if (panel.querySelector('#tableDataProducers')) {
          openElabore();
          return;
        }
        if (panel.classList.contains('is-fullscreen')) {
          closePanelFullscreen();
        } else {
          openPanelFullscreen(panel);
        }
        return;
      }

      // Se clicou no backdrop de tela cheia ou no container exterior de tela cheia, fecha a tela cheia
      if (e.target === fsBackdrop || e.target.classList.contains('panel-fullscreen-backdrop')) {
        const modalDet = el('modalDet');
        const modalElabore = el('modalElaboreOverlay');
        if (modalDet?.classList.contains('active') || modalElabore?.classList.contains('active')) {
          return;
        }
        closeElabore();
        closePanelFullscreen();
      }
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && activeFullscreenPanel) {
        const detailsModal = el('detailsModal');
        const provModal = el('provenanceModal');
        if (detailsModal?.classList.contains('active') || provModal?.classList.contains('active')) {
          return;
        }
        e.preventDefault();
        closePanelFullscreen();
      }
    });

    window.openPanelFullscreen = openPanelFullscreen;
    window.closePanelFullscreen = closePanelFullscreen;
  }

  setupTableSorting();
  setupTablePagination();
  setupColumnResizers();
  setupColumnFilters();
  setupCustomSelectDropdowns();
  setupDetailsModal();
  setupKpiInfoPopovers();
  setupProvenanceModal();

  // ─── PAINEL DADOS ELABORE (TELA CHEIA DOS 8 BLOCOS) ─────────────────────

  const BLOCOS_ELABORE = [
    ['receita', 'Receita', 'Receita', 'Faturamento bruto e venda de leite/animais'],
    ['qualidade', 'Qualidade', 'Qualidade', 'CCS, CBT, gordura e proteína'],
    ['alimentacao', 'Alimentação', 'Alimentação', 'Volumoso, ração e concentrados'],
    ['area', 'Área', 'Área', 'Hectares e uso da terra'],
    ['rebanho', 'Rebanho', 'Rebanho', 'Inventário e categorias de animais'],
    ['mdo', 'MDO', 'Mão de Obra', 'Trabalho familiar e contratado'],
    ['energia_combustivel', 'Energia e Combustível', 'Energia e Combustível', 'Energia elétrica e diesel'],
    ['despesas', 'Outras Despesas', 'Outras Despesas', 'Medicamentos, fretes e manutenção']
  ];

  const GRUPOS_ELABORE = [
    ['todos', 'Todos os produtores'],
    ['completo', '100% dos blocos (8/8)'],
    ['parcial', 'Preenchimento parcial (1 a 7)'],
    ['semdados', 'Nenhum bloco (0/8)'],
    ['nocad', 'Sem cadastro no Elabore']
  ];

  let elaboreState = {
    grupo: 'todos',
    sort: { k: 'cod', dir: 1 },
    filtros: {},
    page: 1,
    size: 25
  };

  function getElaboreRows() {
    const list = state.consistency?.tabelaProdutoresComDados || [];
    return list.map((r) => {
      const cod = r.codigo_lr || '—';
      const produtor = r.produtor || cod;
      const consultor = r.consultor || 'NAO INFORMADO';
      const projeto = r.projeto || 'NAO INFORMADO';
      const data = r.referencia || '—';
      const labelCad3 = String(r.cadastro_elabore_label || (r.cadastro_elabore === 'INATIVO' ? 'INATIVO' : (r.cadastro_elabore ? 'SIM' : 'NÃO'))).toUpperCase();
      const isInactive3 = labelCad3 === 'INATIVO' || String(r.status || '').toUpperCase().includes('INATIV');
      const isCad = !isInactive3 && r.cadastro_elabore !== false && r.cadastro_elabore !== 'NÃO' && labelCad3 === 'SIM';
      const cad = isInactive3 ? 'INATIVO' : (isCad ? 'SIM' : 'NÃO');
      const b = r.detalhes_blocos || {
        receita: false, qualidade: false, alimentacao: false, area: false,
        rebanho: false, mdo: false, energia_combustivel: false, despesas: false
      };

      const nBlocks = Object.values(b).filter(Boolean).length;
      const pct = Math.round((nBlocks / 8) * 100);
      const status = nBlocks > 0 ? `SIM (${pct}%)` : 'NÃO (0%)';

      let grupo = 'semdados';
      if (!isCad) {
        grupo = 'nocad';
      } else if (nBlocks === 8) {
        grupo = 'completo';
      } else if (nBlocks > 0) {
        grupo = 'parcial';
      }

      return {
        cod,
        produtor,
        propriedade: r.propriedade || '—',
        consultor,
        projeto,
        data,
        cad,
        pct,
        status,
        grupo,
        n: nBlocks,
        receita: b.receita ? 'SIM' : 'NÃO',
        qualidade: b.qualidade ? 'SIM' : 'NÃO',
        alimentacao: b.alimentacao ? 'SIM' : 'NÃO',
        area: b.area ? 'SIM' : 'NÃO',
        rebanho: b.rebanho ? 'SIM' : 'NÃO',
        mdo: b.mdo ? 'SIM' : 'NÃO',
        energia_combustivel: (b.energia_combustivel || b.energia) ? 'SIM' : 'NÃO',
        despesas: b.despesas ? 'SIM' : 'NÃO',
        raw: r
      };
    });
  }

  function pctClassElabore(pct) {
    if (pct >= 80) return 'badge-positive';
    if (pct > 0) return 'badge-warning';
    return 'badge-danger';
  }

  function renderElaboreHead() {
    const elHead = el('elHead');
    const elFilters = el('elFilters');
    if (!elHead || !elFilters) return;

    const COLS = [
      { k: 'cod', t: 'ID', blk: false },
      { k: 'consultor', t: 'Consultor(a)', blk: false },
      { k: 'produtor', t: 'Produtor(a)', blk: false },
      { k: 'propriedade', t: 'Fazenda', blk: false },
      { k: 'projeto', t: 'Projeto', blk: false },
      { k: 'data', t: 'Últ. Ref.', blk: false },
      { k: 'cad', t: 'Cadastro Elabore', blk: false },
      { k: 'pct', t: 'Dados Elabore', blk: false },
      ...BLOCOS_ELABORE.map(([k, t]) => ({ k, t, blk: true }))
    ];

    elHead.innerHTML = `
      <th class="col-center" data-key="cod">ID</th>
      <th class="col-left" data-key="consultor">Consultor(a)</th>
      <th class="col-left" data-key="produtor">Produtor(a)</th>
      <th class="col-left" data-key="propriedade">Fazenda</th>
      <th class="col-left" data-key="projeto">Projeto</th>
      <th class="col-center" data-key="data">Últ. Ref.</th>
      <th class="col-center" data-key="cad">Cadastro Elabore</th>
      <th class="col-center" data-key="pct">Dados Elabore
        <button class="kpi-info-btn" type="button" aria-label="Informações sobre a coluna Dados Elabore" data-info-title="Dados Elabore" data-info-body="Indica a presença e o percentual de preenchimento dos 8 blocos gerenciais do Elabore no mês de referência. Ex: SIM (67%) ou NÃO (0%)."><span class="material-symbols-rounded" aria-hidden="true">info</span></button>
      </th>
      <th class="det-col">Ação</th>
      ${BLOCOS_ELABORE.map(([k, t], i) => `<th class="blk ${i === 0 ? 'first-blk' : ''}" data-key="${k}">${t}</th>`).join('')}
    `;

    const filterCell = (c) => `<th class="${c.blk ? 'blk' : c.k === 'cod' || c.k === 'data' || c.k === 'cad' || c.k === 'pct' ? 'col-center' : 'col-left'}">${
      c.k === 'cad' || c.blk
        ? `<select class="table-col-filter" data-col="${c.k}" aria-label="Filtrar ${c.t}"><option value="">${c.blk ? '–' : 'Todos'}</option><option>SIM</option><option>NÃO</option><option>INATIVO</option></select>`
        : `<input type="text" class="table-col-filter" data-col="${c.k}" placeholder="Filtrar..." aria-label="Filtrar ${c.t}">`
    }</th>`;

    elFilters.innerHTML = COLS.slice(0, 8).map(filterCell).join('') + '<th class="det-col"></th>' + COLS.slice(8).map(filterCell).join('');
  }

  function filteredElaboreRows(ignoreGrupo = false) {
    const rows = getElaboreRows();
    const f = Object.entries(elaboreState.filtros).filter(([, v]) => v);
    return rows.filter((r) => {
      if (!ignoreGrupo && elaboreState.grupo !== 'todos' && r.grupo !== elaboreState.grupo) return false;
      return f.every(([k, v]) => {
        const val = k === 'pct' ? r.status : String(r[k] || '');
        const filterInput = el('elFilters')?.querySelector(`[data-col="${k}"]`);
        if (filterInput && filterInput.tagName === 'SELECT') {
          return !v || val.toUpperCase() === v.toUpperCase();
        }
        return val.toLowerCase().includes(v.toLowerCase());
      });
    });
  }

  function renderElaborePanel() {
    const base = filteredElaboreRows(true);
    const chipsEl = el('elChips');
    if (chipsEl) {
      chipsEl.innerHTML = GRUPOS_ELABORE.map(([g, t]) => {
        const count = g === 'todos' ? base.length : base.filter((r) => r.grupo === g).length;
        return `<button type="button" class="el-chip ${elaboreState.grupo === g ? 'on' : ''}" data-g="${g}">${t} <b>${count}</b></button>`;
      }).join('');
    }

    const { k, dir } = elaboreState.sort;
    const list = filteredElaboreRows(false).sort((a, b) => {
      const x = a[k] ?? '', y = b[k] ?? '';
      const comp = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
      return comp * dir || String(a.produtor).localeCompare(String(b.produtor));
    });

    document.querySelectorAll('#elHead th[data-key]').forEach((th) => {
      th.classList.toggle('sort-asc', th.dataset.key === k && dir === 1);
      th.classList.toggle('sort-desc', th.dataset.key === k && dir === -1);
    });

    const pages = elaboreState.size ? Math.max(1, Math.ceil(list.length / elaboreState.size)) : 1;
    elaboreState.page = Math.min(elaboreState.page, pages);
    const start = elaboreState.size ? (elaboreState.page - 1) * elaboreState.size : 0;
    const slice = elaboreState.size ? list.slice(start, start + elaboreState.size) : list;

    if (el('elCount')) el('elCount').textContent = `${list.length.toLocaleString('pt-BR')} registros`;

    const icon = (v, nome) => v === 'SIM'
      ? `<span class="material-symbols-rounded ic ic-ok" title="${nome}: possui">check_circle</span>`
      : v === 'NÃO'
      ? `<span class="material-symbols-rounded ic ic-no" title="${nome}: não possui">cancel</span>`
      : `<span class="material-symbols-rounded ic ic-na" title="Sem cadastro no Elabore">remove</span>`;

    const bodyEl = el('elBody');
    if (bodyEl) {
      bodyEl.innerHTML = slice.length ? slice.map((r) => `<tr class="${r.cad === 'NÃO' ? 'row-nocad' : (r.cad === 'INATIVO' ? 'row-inativo' : '')}" data-cod="${escapeHtml(r.cod)}">
        <td class="col-center"><strong>${escapeHtml(r.cod)}</strong></td>
        <td class="col-left" title="${escapeHtml(r.consultor)}">${escapeHtml(r.consultor)}</td>
        <td class="col-left" title="${escapeHtml(r.produtor)}">${escapeHtml(r.produtor)}</td>
        <td class="col-left" title="${escapeHtml(r.propriedade)}">${escapeHtml(r.propriedade)}</td>
        <td class="col-left" title="${escapeHtml(r.projeto)}">${escapeHtml(r.projeto)}</td>
        <td class="col-center">${escapeHtml(r.data)}</td>
        <td class="col-center cell-trigger-detail" data-action="detail" title="Clique para ver os detalhes deste produtor"><span class="badge ${r.cad === 'SIM' ? 'badge-positive' : 'badge-neutral'}">${r.cad}</span></td>
        <td class="col-center cell-trigger-detail" data-action="detail" title="Clique para ver os detalhes deste produtor"><span class="badge ${r.cad === 'NÃO' ? 'badge-neutral' : pctClassElabore(r.pct)}">${r.status}</span></td>
        <td class="det-col cell-trigger-detail" data-action="detail"><button class="btn-elabore-detail btn-det" type="button" data-cod="${escapeHtml(r.cod)}" aria-label="Ver detalhes de ${escapeHtml(r.produtor)}">Ver detalhes ›</button></td>
        ${BLOCOS_ELABORE.map(([bk, , bt], i) => `<td class="blk ${i === 0 ? 'first-blk' : ''}">${icon(r[bk], bt)}</td>`).join('')}
      </tr>`).join('') : `<tr><td colspan="17" class="el-empty">Nenhum produtor com esses filtros.</td></tr>`;
    }

    const footEl = el('elFoot');
    if (footEl) {
      const cads = list.filter((r) => r.cad === 'SIM');
      const media = cads.length ? Math.round(cads.reduce((s, r) => s + r.pct, 0) / cads.length) : 0;
      footEl.innerHTML = `<tr>
        <td colspan="6" class="col-left">Preenchimento entre ${cads.length.toLocaleString('pt-BR')} cadastrados</td>
        <td class="col-center">${list.length ? Math.round(cads.length / list.length * 100) : 0}%</td>
        <td class="col-center"><div class="pct">média ${media}%</div></td>
        <td class="det-col"></td>
        ${BLOCOS_ELABORE.map(([bk], i) => {
          const p = cads.length ? Math.round(cads.filter((r) => r[bk] === 'SIM').length / cads.length * 100) : 0;
          return `<td class="blk ${i === 0 ? 'first-blk' : ''}"><div class="pct"><span>${p}%</span><span class="pct-bar"><i style="width:${p}%"></i></span></div></td>`;
        }).join('')}
      </tr>`;
    }

    const pagEl = el('elPagination');
    if (pagEl) {
      const nums = [];
      for (let p = 1; p <= pages; p++) if (p === 1 || p === pages || Math.abs(p - elaboreState.page) <= 1) nums.push(p); else if (nums.at(-1) !== '…') nums.push('…');
      const btn = (p, label, dis, on) => `<button type="button" class="pagination-btn ${on ? 'active' : ''}" data-page="${p}" ${dis ? 'disabled' : ''}>${label}</button>`;
      pagEl.innerHTML = `
        <div class="pagination-info"><span>${list.length ? `Exibindo <strong>${start + 1}–${Math.min(start + slice.length, list.length)}</strong> de <strong>${list.length.toLocaleString('pt-BR')}</strong> registros` : '0 registros'}</span></div>
        <div class="pagination-controls">
          <div class="pagination-size-wrap"><label for="elSize">Exibir:</label>
            <select id="elSize" class="pagination-size-select">${[10, 25, 50, 100, 0].map((n) => `<option value="${n}" ${n === elaboreState.size ? 'selected' : ''}>${n || 'Todos'}</option>`).join('')}</select></div>
          ${pages > 1 ? `<div class="pagination-nav">${btn(1, '«', elaboreState.page === 1)}${btn(elaboreState.page - 1, '‹', elaboreState.page === 1)}${nums.map((p) => p === '…' ? '<span class="pagination-ellipsis">…</span>' : btn(p, p, false, p === elaboreState.page)).join('')}${btn(elaboreState.page + 1, '›', elaboreState.page === pages)}${btn(pages, '»', elaboreState.page === pages)}</div>` : ''}
        </div>`;
    }
  }

  function openElabore(cod) {
    elaboreState.filtros = {}; elaboreState.grupo = 'todos'; elaboreState.page = 1;
    const filtersContainer = el('elFilters');
    if (filtersContainer) {
      filtersContainer.querySelectorAll('.table-col-filter').forEach((f) => { f.value = ''; });
    }
    if (cod) {
      elaboreState.filtros.cod = cod;
      const codFilter = filtersContainer?.querySelector('[data-col="cod"]');
      if (codFilter) codFilter.value = cod;
    }
    const panel = el('panelElabore');
    const backdrop = el('panelFsBackdrop');
    if (panel) panel.classList.add('is-fullscreen');
    if (backdrop) {
      backdrop.hidden = false;
      backdrop.removeAttribute('hidden');
    }
    const refMonth = state.consistency?.refMonth || state.overview?.refMonth || '';
    if (el('elRef')) el('elRef').textContent = refMonth ? String(refMonth).slice(0, 7).split('-').reverse().join('/') : '--/----';
    renderElaboreHead();
    renderElaborePanel();
  }

  function closeElabore() {
    const panel = el('panelElabore');
    const backdrop = el('panelFsBackdrop');
    if (panel) panel.classList.remove('is-fullscreen');
    if (backdrop) {
      backdrop.hidden = true;
      backdrop.setAttribute('hidden', '');
    }
  }

  function openDetailElaboreModal(cod) {
    const rows = getElaboreRows();
    const r = rows.find((x) => x.cod === cod);
    if (!r) return;
    const semCad = r.cad === 'NÃO' || r.cad === 'INATIVO';
    const dadosBadge = `<span class="badge ${semCad ? 'badge-neutral' : pctClassElabore(r.pct)}">${r.status}</span>`;
    const situacao = (v) => v === 'SIM'
      ? '<span class="material-symbols-rounded ic ic-ok">check_circle</span> Possui'
      : v === 'NÃO'
      ? '<span class="material-symbols-rounded ic ic-no">cancel</span> Não possui'
      : '<span class="material-symbols-rounded ic ic-na">remove</span> Sem cadastro';

    if (el('detTitle')) el('detTitle').textContent = `Detalhes Elabore · ${r.produtor}`;
    const refMonth = state.consistency?.refMonth || state.overview?.refMonth || '';
    if (el('detRef')) el('detRef').textContent = refMonth ? String(refMonth).slice(0, 7).split('-').reverse().join('/') : '--/----';

    if (el('detBody')) {
      el('detBody').innerHTML = `
        <p class="det-sec">Fazenda / Produtor</p>
        <table class="data-table det-ficha"><tbody>
          <tr><th>ID</th><td><strong>${escapeHtml(r.cod)}</strong></td><th>Projeto</th><td>${escapeHtml(r.projeto)}</td></tr>
          <tr><th>Produtor(a)</th><td colspan="3">${escapeHtml(r.produtor)}</td></tr>
          <tr><th>Consultor(a)</th><td colspan="3">${escapeHtml(r.consultor)}</td></tr>
          <tr><th>Última referência</th><td>${escapeHtml(r.data)}</td><th>Cadastro Elabore</th><td><span class="badge ${r.cad === 'SIM' ? 'badge-positive' : 'badge-neutral'}">${r.cad}</span></td></tr>
          <tr><th>Dados Elabore</th><td colspan="3">${dadosBadge}</td></tr>
        </tbody></table>
        <p class="det-sec">Blocos gerenciais do Elabore</p>
        <table class="data-table det-blocos">
          <thead><tr><th class="col-center">#</th><th>Bloco</th><th>O que cobre</th><th>Situação</th></tr></thead>
          <tbody>${BLOCOS_ELABORE.map(([k, , nome, desc], i) => `<tr class="det-${r[k] === 'SIM' ? 'ok' : r[k] === 'NÃO' ? 'no' : 'na'}">
            <td class="col-center">${i + 1}</td><td><strong>${nome}</strong></td><td>${desc}</td><td>${situacao(r[k])}</td></tr>`).join('')}</tbody>
          <tfoot><tr><td colspan="3">${r.cad === 'INATIVO' ? 'Produtor inativo' : (r.cad === 'NÃO' ? 'Produtor sem cadastro no Elabore' : 'Blocos preenchidos')}</td>
            <td>${semCad ? '—' : `${r.n} de 8 (${r.pct}%)`}</td></tr></tfoot>
        </table>`;
    }
    const modalDet = el('modalDet');
    if (modalDet) modalDet.classList.add('active');
  }

  function closeDetailElaboreModal() {
    const modalDet = el('modalDet');
    if (modalDet) modalDet.classList.remove('active');
  }

  el('elChips')?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-g]');
    if (b) {
      elaboreState.grupo = b.dataset.g;
      elaboreState.page = 1;
      renderElaborePanel();
    }
  });

  el('elHead')?.addEventListener('click', (e) => {
    const th = e.target.closest('th[data-key]');
    if (!th) return;
    const k = th.dataset.key;
    elaboreState.sort = { k, dir: elaboreState.sort.k === k ? -elaboreState.sort.dir : 1 };
    renderElaborePanel();
  });

  el('elFilters')?.addEventListener('input', (e) => {
    const col = e.target.dataset.col;
    if (col) {
      elaboreState.filtros[col] = e.target.value;
      elaboreState.page = 1;
      renderElaborePanel();
    }
  });
  el('elFilters')?.addEventListener('change', (e) => {
    const col = e.target.dataset.col;
    if (col && e.target.tagName === 'SELECT') {
      elaboreState.filtros[col] = e.target.value;
      elaboreState.page = 1;
      renderElaborePanel();
    }
  });

  el('elPagination')?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-page]');
    if (b && !b.disabled) {
      elaboreState.page = +b.dataset.page;
      renderElaborePanel();
    }
  });

  el('elPagination')?.addEventListener('change', (e) => {
    if (e.target.id === 'elSize') {
      elaboreState.size = +e.target.value;
      elaboreState.page = 1;
      renderElaborePanel();
    }
  });

  el('btnExportElabore')?.addEventListener('click', () => {
    const head = ['ID', 'Consultor', 'Produtor', 'Fazenda', 'Projeto', 'Ult. referencia', 'Cadastro Elabore', 'Dados Elabore', ...BLOCOS_ELABORE.map(([, , t]) => t)];
    const body = filteredElaboreRows(false).map((r) => [r.cod, r.consultor, r.produtor, r.propriedade, r.projeto, r.data, r.cad, r.status, ...BLOCOS_ELABORE.map(([k]) => r[k])]);
    const csv = '\ufeff' + [head, ...body].map((l) => l.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(';')).join('\r\n');
    const a = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' })),
      download: `dados_elabore_${state.consistency?.refMonth || 'export'}.csv`
    });
    a.click();
    URL.revokeObjectURL(a.href);
  });

  el('elBody')?.addEventListener('click', (e) => {
    const triggerCell = e.target.closest('[data-action="detail"]');
    if (triggerCell) {
      const tr = triggerCell.closest('tr[data-cod]');
      if (tr && tr.dataset.cod) {
        openDetailElaboreModal(tr.dataset.cod);
      }
    }
  });

  ['btnDetClose', 'btnDetOk'].forEach((id) => el(id)?.addEventListener('click', closeDetailElaboreModal));
  el('modalDet')?.addEventListener('click', (e) => {
    if (e.target === el('modalDet')) closeDetailElaboreModal();
  });
  ['btnCloseElabore', 'btnFsElabore'].forEach((id) => el(id)?.addEventListener('click', closeElabore));

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-elabore-detail');
    if (btn) {
      e.preventDefault();
      const lr = btn.dataset.lr || btn.dataset.cod;
      if (lr) openDetailElaboreModal(lr);
    }
  });

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modalDet = el('modalDet');
    if (modalDet && modalDet.classList.contains('active')) {
      closeDetailElaboreModal();
    } else {
      closeElabore();
    }
  });

  setupExportButtons();
  setupChartHorizonControls();
  setupPanelFullscreen();

  let chartResizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(chartResizeTimer);
    chartResizeTimer = setTimeout(() => {
      if (charts && charts.instances) {
        Object.values(charts.instances).forEach((instance) => {
          if (instance && typeof instance.resize === 'function') {
            instance.resize();
          }
        });
      }
    }, 100);
  });

  loadAllData();
  setInterval(loadAllData, 300000);

  window.dashboard = { carousel, charts, state, reload: loadAllData, openElabore };
});
