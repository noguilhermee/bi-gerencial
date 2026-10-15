const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, monthLabel, formatDate, parseFilters, rowMatchesFilters, obterEscopo } = require('../_shared');
const { comAuth } = require('../_auth');

module.exports = comAuth(async (req, res) => {
  try {

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);
    const filters = parseFilters(req.query);
    // Mês: um só = foto do mês; vários = período (agrega como "Todos", restrito aos meses escolhidos)
    const refMonth = filters.month || null;
    const corteSerie = filters.monthRef || null; // séries históricas vão até o mês mais recente escolhido

    // Carteira mensal (1 linha por fazenda × mês × consultor), já resolvida pelo ETL,
    // e visitas técnicas válidas (1 linha por atendimento) — buscadas em paralelo
    const [carteiraTodosBruta, visitasTodasBruta] = await Promise.all([
      fetchWithCache('CARTEIRA_MENSAL_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto')),
      fetchWithCache('FATO_VISITAS_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_visitas').select(COLUNAS.visitas).order('data_visita', { ascending: false }), undefined, 'id_atendimento'))
    ]);
    const carteiraTodos = escopo.carteira(carteiraTodosBruta);
    const visitasTodas = escopo.fatos(visitasTodasBruta);

    // Relacionamento oficial dos filtros (consultor × agroindústria × região × regional × mês), gravado pelo ETL em
    // tab_dim_consultor_atuacao. Falha (ex.: migration ainda não aplicada) não é cacheada e não derruba a rota:
    // o front segue só com as opções derivadas das linhas.
    const atuacao = await fetchWithCache('DIM_CONSULTOR_ATUACAO', () =>
      fetchAll(() => supabase.from('tab_dim_consultor_atuacao').select('mes_referencia,agroindustria,regiao,consultor,regional'), undefined, 'id_composto')
    ).catch((error) => {
      console.warn('tab_dim_consultor_atuacao indisponível:', error.message);
      return null;
    });
    // Perfis restritos só enxergam combinações de agroindústria, região, regional e consultor presentes na sua carteira
    const noEscopo = escopo.admin ? () => true : (() => {
      const agros = new Set(carteiraTodos.map(r => r.agroindustria));
      const regioes = new Set(carteiraTodos.map(r => r.regiao));
      const regionais = new Set(carteiraTodos.map(r => r.regional));
      const consultores = new Set(carteiraTodos.map(r => r.consultor));
      return a => agros.has(a.agroindustria) && regioes.has(a.regiao) && regionais.has(a.regional) && consultores.has(a.consultor);
    })();
    const relacoes = atuacao
      ? [...new Map(atuacao.filter(noEscopo).map(a => {
          // [mês, agroindústria, região (UF), consultor, regional]: a regional vai no fim para não deslocar as posições antigas
          const linha = [String(a.mes_referencia).slice(0, 7), a.agroindustria, a.regiao, a.consultor, a.regional || null];
          return [linha.join('\u0001'), linha];
        })).values()]
      : null;

    // Consultores com status INATIVO (gravado pelo ETL em tab_dim_consultor): o filtro mostra "(INATIVO)" ao lado do nome
    const dimConsultor = await fetchWithCache('DIM_CONSULTOR_STATUS', () =>
      fetchAll(() => supabase.from('tab_dim_consultor').select('chave_consultor,status'), 1000, 'id')
    ).catch(() => []);
    const consultoresInativos = [...new Set(
      (dimConsultor || []).filter(c => String(c.status || '').toUpperCase() === 'INATIVO').map(c => c.chave_consultor).filter(Boolean)
    )];

    const carteiraMes = carteiraTodos.filter(r => filters.noPeriodo(r.mes_referencia));
    const carteiraFiltrada = carteiraMes.filter(r => rowMatchesFilters({ ...r, status: r.status_visita }, filters));

    const visitasMes = visitasTodas.filter(v => filters.noPeriodo(v.mes_referencia));
    const visitasFiltradas = visitasMes.filter(v => rowMatchesFilters({ ...v, status: v.status_produtor }, filters));

    // ── KPIs do mês selecionado ────────────────────────────────────────────
    const codigosAtivos = new Set(carteiraFiltrada.map(r => r.codigo_lr));
    const codigosVisitados = new Set(carteiraFiltrada.filter(r => r.visitado_mes).map(r => r.codigo_lr));
    const totalAtivos = codigosAtivos.size;
    const totalVisitas = visitasFiltradas.length;
    const consultoresAtivos = new Set(carteiraFiltrada.map(r => r.consultor).filter(Boolean)).size;
    const percVisitados = totalAtivos > 0 ? ((codigosVisitados.size / totalAtivos) * 100).toFixed(1) : '0.0';
    const visitasPorProdutor = totalAtivos > 0 ? (totalVisitas / totalAtivos).toFixed(1) : '0.0';

    // Regra 13 (AGENTS.md): fazendas de consultor que não lança no Elabore não entram nos indicadores de consistência
    const fazendasMes = [...new Map(carteiraFiltrada.filter(r => !r.sem_lancamento_elabore).map(r => [r.codigo_lr, r])).values()];
    const comDados = fazendasMes.filter(r => r.consistencia_mensal !== 'SEM DADOS');
    const consistentes = comDados.filter(r => r.consistencia_mensal === 'CONSISTENTE');
    const percConsistente = comDados.length > 0 ? ((consistentes.length / comDados.length) * 100).toFixed(1) : '0.0';

    // ── Séries históricas (todos os meses da carteira) ─────────────────────
    const mesesUnicos = [...new Set(carteiraTodos.map(r => String(r.mes_referencia).slice(0, 10)))].sort();
    const carteiraFiltradaHistorica = carteiraTodos.filter(r => rowMatchesFilters({ ...r, status: r.status_visita }, { ...filters, month: '' }));
    const referencias = mesesUnicos.filter(m => !corteSerie || m <= corteSerie);

    const porMes = new Map();
    carteiraFiltradaHistorica.forEach(r => {
      const key = String(r.mes_referencia).slice(0, 10);
      if (!porMes.has(key)) porMes.set(key, { ativos: new Set(), visitados: new Set() });
      const item = porMes.get(key);
      item.ativos.add(r.codigo_lr);
      if (r.visitado_mes) item.visitados.add(r.codigo_lr);
    });

    const evolucaoMensal = {
      labels: referencias.map(monthLabel),
      fazendasAtivas: referencias.map(ref => porMes.get(ref)?.ativos.size || 0),
      fazendasVisitadas: referencias.map(ref => porMes.get(ref)?.visitados.size || 0),
      percCobertura: referencias.map(ref => {
        const item = porMes.get(ref);
        if (!item || item.ativos.size === 0) return 0;
        return Number(((item.visitados.size / item.ativos.size) * 100).toFixed(1));
      })
    };

    const visitasHistoricas = visitasTodas.filter(v => rowMatchesFilters({ ...v, status: v.status_produtor }, { ...filters, month: '' }));
    const visitasPorMes = new Map();
    visitasHistoricas.forEach(v => {
      const key = String(v.mes_referencia).slice(0, 10);
      visitasPorMes.set(key, (visitasPorMes.get(key) || 0) + 1);
    });
    const valoresVisitas = referencias.map(ref => visitasPorMes.get(ref) || 0);
    // Variação % sobre o mês anterior da série completa (null quando não há base de comparação)
    const variacoesVisitas = valoresVisitas.map((valor, idx) => {
      const anterior = idx > 0 ? valoresVisitas[idx - 1] : 0;
      return anterior > 0 ? Number((((valor - anterior) / anterior) * 100).toFixed(1)) : null;
    });
    const evolucaoVisitas = { labels: referencias.map(monthLabel), values: valoresVisitas, variacoes: variacoesVisitas };

    const rankingMap = new Map();
    visitasFiltradas.forEach(v => {
      const nome = v.nome_produtor || v.codigo_lr || 'Produtor não identificado';
      rankingMap.set(nome, (rankingMap.get(nome) || 0) + 1);
    });
    const ranking = [...rankingMap.entries()].sort((a, b) => b[1] - a[1]);

    // ── Tabelas ──────────────────────────────────────────────────────────
    const semVisita = carteiraFiltrada.filter(r => !r.visitado_mes).map(r => ({
      consultor: r.consultor || 'NAO ATRIBUIDO',
      codigo_lr: r.codigo_lr,
      produtor: r.nome_produtor,
      propriedade: r.nome_propriedade,
      agroindustria: r.agroindustria,
      regiao: r.regiao,
      regional: r.regional,
      projeto: r.projeto,
      status: r.status_visita,
      status_class: r.status_visita_classe,
      cadastro_elabore: Boolean(r.cadastro_elabore),
      cadastro_elabore_label: r.cadastro_elabore ? 'SIM' : 'NÃO',
      mes_referencia: r.mes_referencia,
      data_associacao: r.data_associacao ? formatDate(r.data_associacao) : '—',
      data_ultima_visita: r.data_ultima_visita ? formatDate(r.data_ultima_visita) : '—',
      data_visita_mes_anterior: r.data_visita_mes_anterior ? formatDate(r.data_visita_mes_anterior) : '—',
      dias_sem_visita: r.dias_sem_visita
    }));

    const visitados = visitasFiltradas.map(v => {
      return {
        consultor: v.nome_consultor,
        codigo_lr: v.codigo_lr,
        produtor: v.nome_produtor,
        propriedade: v.nome_propriedade,
        agroindustria: v.agroindustria,
        regiao: v.regiao,
        regional: v.regional,
        projeto: v.projeto,
        status: v.status_produtor,
        mes_referencia: v.mes_referencia,
        profissao: v.profissao_consultor || '-',
        atendimento: `AT-${v.id_atendimento}`,
        data_visita: formatDate(v.data_visita),
        elabore_ok: v.cadastro_elabore,
        cadastro_elabore: v.status_produtor === 'INATIVO' ? 'INATIVO' : v.cadastro_elabore,
        cadastro_elabore_label: v.status_produtor === 'INATIVO' ? 'INATIVO' : (v.cadastro_elabore ? 'SIM' : 'NÃO'),
        dados_elabore_status: v.dados_elabore_status,
        dados_elabore_pct: v.dados_elabore_pct,
        dados_elabore_tem_dado: v.dados_elabore_pct > 0,
        detalhes_blocos: v.blocos_elabore,
        tipo_visita: v.tipo_visita,
        valor_pago_produtor: Number(v.valor_pago_produtor || 0),
        valor_pago_agroindustria: Number(v.valor_pago_agroindustria || 0)
      };
    });

    let dataProvenance = null;
    try {
      const fs = require('fs');
      const path = require('path');
      const metaPath = path.join(__dirname, '..', '..', 'public', 'data', 'fontes_metadados.json');
      if (fs.existsSync(metaPath)) dataProvenance = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
    } catch (e) {
      console.warn('Metadados de proveniência não carregados:', e.message);
    }

    let ultimaExecucaoEtl = null;
    try {
      const { data: ultProc } = await supabase
        .from('tab_fato_visitas')
        .select('data_processamento')
        .order('data_processamento', { ascending: false })
        .limit(1);
      if (ultProc && ultProc.length > 0 && ultProc[0].data_processamento) {
        const d = new Date(ultProc[0].data_processamento);
        ultimaExecucaoEtl = `${d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' })} às ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })}`;
      }
    } catch (e) {
      console.warn('Não foi possível obter data_processamento de tab_fato_visitas:', e.message);
    }
    if (!ultimaExecucaoEtl && dataProvenance?.ultima_execucao_etl_formatada) {
      ultimaExecucaoEtl = dataProvenance.ultima_execucao_etl_formatada;
    }

    const dimFazendas = fazendasMes.map(r => ({
        codigo_lr: r.codigo_lr,
        produtor: r.nome_produtor,
        propriedade: r.nome_propriedade,
        consultor: r.consultores_grupo || r.consultor,
        projeto: r.projeto,
        agroindustria: r.agroindustria,
        regiao: r.regiao,
        regional: r.regional,
        mes_referencia: r.mes_referencia,
        status: 'ATIVO'
      }));

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      ultimaExecucaoEtl,
      refMonth,
      meses: filters.months,
      visitasMonth: refMonth,
      consistencyMonth: refMonth,
      dataProvenance,
      kpis: {
        total_visitas: totalVisitas,
        consultores_ativos: consultoresAtivos,
        produtores_ativos: totalAtivos,
        produtores_visitados: codigosVisitados.size,
        visitas_por_produtor: visitasPorProdutor,
        perc_visitados: percVisitados,
        produtores_com_dados: comDados.length,
        perc_consistente: percConsistente,
        visitas_nao_realizadas: null
      },
      evolucaoMensal,
      evolucaoVisitas,
      rankingProdutores: { labels: ranking.map(i => i[0]), values: ranking.map(i => i[1]) },
      filterOptions: {
        agroindustrias: [...new Set(carteiraTodos.map(r => r.agroindustria))].filter(Boolean).sort((a, b) => a.localeCompare(b, 'pt-BR')),
        regioes: [...new Set(carteiraTodos.map(r => r.regiao))].filter(Boolean).sort((a, b) => a.localeCompare(b, 'pt-BR')),
        regionais: [...new Set(carteiraTodos.map(r => r.regional))].filter(Boolean).sort((a, b) => a.localeCompare(b, 'pt-BR')),
        projetos: [...new Set(carteiraTodos.map(r => r.projeto))].filter(Boolean).sort(),
        consultores: [...new Set(carteiraTodos.map(r => r.consultor))].filter(Boolean).sort(),
        status: ['ATIVO', 'INATIVO'],
        meses: mesesUnicos,
        consultoresInativos,
        relacoes
      },
      dim_fazendas: dimFazendas,
      tabelas: { sem_visita: semVisita, visitados }
    });
  } catch (error) {
    console.error('Erro em /api/overview:', error);
    return res.status(500).json({ error: error.message });
  }
});
