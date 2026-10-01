// Agroindústria e região já chegam resolvidas pelo ETL em tab_fato_economico (regra 12):
// a API apenas lê as colunas. As antigas importações sanitizeConsultorList/isTestData/
// ehCadeiaLeite/mapAgroindustria não existem mais em shared.js e faziam esta rota
// devolver HTTP 500 em toda requisição.
//
// 2026-09-23: correção da fórmula do COE/Margem Bruta no ETL (scripts/functions/
// carregar_fato_economico.py), validada 1:1 contra os indicadores ANUAIS do Elabore
// (98,75% de aderência em janelas de 12 meses). Esta API foi ajustada para:
//   1) usar apenas linhas com possui_dados_economicos=1 (exclui também o resíduo de
//      ~323k linhas da carga legada anterior, que ficam com possui_dados_economicos=0
//      por padrão e ainda não foram excluídas da tabela);
//   2) calcular preço/COE/margem por litro como médias PONDERADAS (Σvalor/Σlitros),
//      não média simples entre linhas de fazendas com portes muito diferentes.
const {
  COLUNAS,
  getSupabaseClient,
  fetchAll,
  fetchWithCache,
  monthLabel,
  formatDate,
  parseList, obterEscopo } = require('./shared');
const { comAuth } = require('./_auth');

module.exports = comAuth(async (req, res) => {
  try {

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);

    // Leitura das tabelas econômicas e de vínculos
    const [rawEconData, rawVinculosData] = await Promise.all([
      fetchWithCache('FATO_ECONOMICO', () =>
        fetchAll(() => supabase.from('tab_fato_economico').select(COLUNAS.economico).eq('possui_dados_economicos', 1), undefined, 'id_composto')
      ),
      fetchWithCache('RAW_VINCULOS_ECON', () =>
        fetchAll(() => supabase.from('sq_raw_vinculos').select(COLUNAS.vinculosEconomico), undefined, 'id_composto')
      )
    ]);

    // Filtros da requisição (mesmos nomes de query usados por overview/visits/turnover/consistency)
    // Multi-seleção: cada filtro é um Set em caixa alta (vazio = sem filtro)
    const listaMaiuscula = valor => new Set(parseList(valor).map(v => v.toUpperCase()));
    const filterAgro = listaMaiuscula(req.query.industry);
    const filterRegion = listaMaiuscula(req.query.region);
    const filterProject = listaMaiuscula(req.query.project);
    const filterStatus = (req.query.status || '').trim().toUpperCase();
    const filterConsultant = listaMaiuscula(req.query.consultant);
    const filterProducer = listaMaiuscula(req.query.producer);
    const filterMonth = (req.query.month || '').trim();

    // Mapeamento de cadastro de vínculos
    const vinculoMap = new Map();
    (rawVinculosData || []).forEach(v => {
      const cod = String(v.codigo_lr || '').trim().toUpperCase();
      if (cod) vinculoMap.set(cod, v);
    });

    // Filtrar dados econômicos
    let econList = (rawEconData || []).map(row => {
      const cod = String(row.codigo_lr || '').trim().toUpperCase();
      const vinculo = vinculoMap.get(cod) || {};
      const agro = String(row.agroindustria || vinculo.agroindustria || '').trim();
      const reg = String(row.regiao || vinculo.regiao || '').trim() || 'NAO INFORMADA';

      return {
        ...row,
        codigo_lr: cod,
        agroindustria: agro,
        regiao: reg,
        projeto: String(row.projeto || vinculo.projeto || '').trim(),
        consultor: String(row.nome_consultor || vinculo.consultor_campo || '').trim(),
        produtor: String(row.nome_produtor || vinculo.nome_produtor || '').trim(),
        nome_fazenda: String(vinculo.nome_propriedade || '').trim(),
        cidade: String(vinculo.cidade_produtor || '').trim(),
        estado: String(vinculo.estado_produtor || '').trim(),
        status: String(vinculo.status_cadastro || 'ATIVO').trim(),
        data_associacao: row.data_associacao || vinculo.data_associacao
      };
    });

    // Escopo do perfil logado, antes de qualquer filtro vindo da tela
    econList = escopo.fatos(econList);

    // Aplicação dos filtros de dimensão (agro/região/projeto/status/consultor/produtor).
    // O filtro de mês é aplicado separadamente logo abaixo: ele deve valer para os KPIs
    // pontuais (foto do mês selecionado), mas NUNCA para séries históricas (evolução mensal),
    // senão o gráfico de tendência fica com um único ponto — mesmo padrão de overview.js
    // (carteiraTodos vs. carteiraMes).
    if (filterAgro.size) econList = econList.filter(r => filterAgro.has(r.agroindustria.toUpperCase()));
    if (filterRegion.size) econList = econList.filter(r => filterRegion.has(r.regiao.toUpperCase()));
    if (filterProject.size) econList = econList.filter(r => filterProject.has(r.projeto.toUpperCase()));
    if (filterStatus) econList = econList.filter(r => r.status.toUpperCase() === filterStatus);
    if (filterConsultant.size) econList = econList.filter(r => filterConsultant.has(r.consultor.toUpperCase()));
    if (filterProducer.size) econList = econList.filter(r => filterProducer.has(r.produtor.toUpperCase()) || filterProducer.has(r.codigo_lr));

    // Descarta linhas sem lançamento econômico real no mês (inclui, por ora, o resíduo de
    // carga legada anterior à correção de 2026-09-23, que ficou com possui_dados_economicos=0
    // por padrão e não foi excluído da tabela — ver ETL scripts/functions/carregar_fato_economico.py).
    // Regra 12 (AGENTS.md): a exclusão definitiva do legado deve ocorrer na origem (Supabase),
    // este filtro é apenas para não diluir os KPIs enquanto o DELETE não é confirmado.
    // econSemFiltroMes mantém todos os meses (para séries históricas); econComDados aplica
    // também o filtro de mês (para KPIs pontuais e ranking do mês selecionado).
    const econSemFiltroMes = econList.filter(r => Number(r.possui_dados_economicos) === 1);
    // Sem mês escolhido ("Todos"), KPIs e gráficos pontuais mostram o ÚLTIMO mês com dado consistente
    // do recorte. Somar todos os meses empilharia volumes diários de meses diferentes (ex.: 268
    // fazenda-mês somando L/dia) e não bateria com o gráfico de evolução, que é mês a mês.
    const ehConsistenteMes = r => r.status_consistencia_mensal === 'CONSISTENTE';
    const mesesConsistentesRecorte = Array.from(new Set(
      econSemFiltroMes.filter(ehConsistenteMes).map(r => String(r.mes_referencia).substring(0, 7))
    )).sort();
    const mesEfetivo = filterMonth || mesesConsistentesRecorte[mesesConsistentesRecorte.length - 1] || '';
    const econTabela = mesEfetivo
      ? econSemFiltroMes.filter(r => String(r.mes_referencia).startsWith(mesEfetivo))
      : econSemFiltroMes;

    // Regra de consistência da tela 4: KPIs e gráficos usam apenas fazenda-mês com
    // status_consistencia_mensal = 'CONSISTENTE' (gravado pelo ETL a partir do Elabore).
    // As tabelas continuam listando todos os registros, sinalizando os inconsistentes.
    const ehConsistente = r => r.status_consistencia_mensal === 'CONSISTENTE';
    const econHistorico = econSemFiltroMes.filter(ehConsistente);
    const econComDados = econTabela.filter(ehConsistente);

    // Cálculos de KPIs do Slide 4 (Econômico e Produção) — médias PONDERADAS pelo volume/receita
    // de cada linha, nunca média simples entre fazendas de porte muito diferente.
    const totalRegistros = econComDados.length;
    const volDiarioTotal = econComDados.reduce((acc, r) => acc + Number(r.volume_diario_litros || 0), 0);
    const volumeTotalLitros = econComDados.reduce((acc, r) => acc + Number(r.volume_leite_mes || 0), 0);
    const receitaTotalBruta = econComDados.reduce((acc, r) => acc + Number(r.receita_bruta_atividade || 0), 0);
    const somaProdutividadePonderada = econComDados.reduce((acc, r) => acc + Number(r.produtividade_l_vl_dia || 0) * Number(r.vacas_lactacao || 0), 0);
    const somaVacasLactacao = econComDados.reduce((acc, r) => acc + Number(r.vacas_lactacao || 0), 0);

    const prodMedia = somaVacasLactacao > 0 ? (somaProdutividadePonderada / somaVacasLactacao) : 0;
    const precoMedio = volumeTotalLitros > 0 ? (receitaTotalBruta / volumeTotalLitros) : 0;
    // COE e Margem Bruta (R$/L): média dos R$/L de cada fazenda consistente PONDERADA pelo
    // Volume Diário (L) — Σ(R$/L × volume diário) ÷ Σ(volume diário).
    const mediaPonderadaPorVolDiario = campo => (volDiarioTotal > 0
      ? econComDados.reduce((acc, r) => acc + Number(r[campo] || 0) * Number(r.volume_diario_litros || 0), 0) / volDiarioTotal
      : 0);
    const coeMedio = mediaPonderadaPorVolDiario('coe_por_litro');
    const mbMedia = mediaPonderadaPorVolDiario('margem_bruta_por_litro');

    const mbPositivas = econComDados.filter(r => Number(r.margem_bruta_total || 0) > 0);
    const percMbPositiva = totalRegistros > 0 ? ((mbPositivas.length / totalRegistros) * 100) : 0;

    // Breakdown Top 5 Itens COE
    const coeConcentrado = econComDados.reduce((acc, r) => acc + Number(r.coe_concentrado || 0), 0);
    const coeVolumoso = econComDados.reduce((acc, r) => acc + Number(r.coe_volumoso || 0), 0);
    const coeMaoDeObra = econComDados.reduce((acc, r) => acc + Number(r.coe_mao_de_obra || 0), 0);
    const coeSanidade = econComDados.reduce((acc, r) => acc + Number(r.coe_sanidade || 0), 0);
    const coeOutros = econComDados.reduce((acc, r) => acc + Number(r.coe_outros || 0), 0);

    // Média por fazenda (fazenda-mês consistente): Σ do bloco ÷ nº de registros
    const mediaFaz = total => (totalRegistros > 0 ? Number((total / totalRegistros).toFixed(2)) : 0);
    const top5Coe = [
      { item: 'Concentrado', valor: mediaFaz(coeConcentrado) },
      { item: 'Volumoso / Forragem', valor: mediaFaz(coeVolumoso) },
      { item: 'Mão de Obra', valor: mediaFaz(coeMaoDeObra) },
      { item: 'Sanidade / Hormônios', valor: mediaFaz(coeSanidade) },
      { item: 'Outras Despesas', valor: mediaFaz(coeOutros) }
    ];

    // Série temporal de variação de volume mensal — usa econSemFiltroMes (todos os meses)
    // para preservar o histórico independentemente do mês selecionado no filtro principal.
    const volPorMesMap = new Map();
    econHistorico.forEach(r => {
      const mesKey = String(r.mes_referencia || '').substring(0, 7);
      if (mesKey) {
        volPorMesMap.set(mesKey, (volPorMesMap.get(mesKey) || 0) + Number(r.volume_leite_mes || (r.volume_diario_litros * 30) || 0));
      }
    });

    const mesKeysOrdenados = Array.from(volPorMesMap.keys()).sort();
    const volumeEvolution = mesKeysOrdenados.map((mes, idx) => {
      const volAtual = volPorMesMap.get(mes);
      const volAnterior = idx > 0 ? volPorMesMap.get(mesKeysOrdenados[idx - 1]) : null;
      let varPerc = 0;
      if (volAnterior && volAnterior > 0) {
        varPerc = Number((((volAtual - volAnterior) / volAnterior) * 100).toFixed(2));
      }
      return {
        mes,
        mes_label: monthLabel(mes),
        volume: volAtual,
        variacao_percentual: varPerc
      };
    });

    // Cálculos do Slide 5 (Perfil da Base e Tempo de Cadastro)
    const hoje = new Date();
    let cad1Ano = 0;
    let cad2Anos = 0;
    let cad3PlusAnos = 0;

    // Lista detalhada de produtores/fazendas para a tabela do painel "Tempo de cadastro dos
    // produtores" (exibida ao expandir o gráfico em tela cheia).
    const cadastroDetalhe = [];

    econTabela.forEach(r => {
      let categoria;
      let tempoLabel = '—';
      let diffAnos = null;
      let tempoMeses = null;
      if (r.data_associacao) {
        const dtAssoc = new Date(r.data_associacao);
        const diffMesesTotal = Math.max(0, Math.floor((hoje - dtAssoc) / (1000 * 60 * 60 * 24 * 30.4375)));
        tempoMeses = diffMesesTotal;
        diffAnos = Math.floor((hoje - dtAssoc) / (1000 * 60 * 60 * 24 * 365.25));
        const anos = Math.floor(diffMesesTotal / 12);
        const meses = diffMesesTotal % 12;
        tempoLabel = anos > 0
          ? `${anos} ano${anos !== 1 ? 's' : ''}${meses > 0 ? ` e ${meses} m${meses !== 1 ? 'eses' : 'ês'}` : ''}`
          : `${meses} m${meses !== 1 ? 'eses' : 'ês'}`;

        if (diffAnos <= 1) categoria = '1 ano de cadastro';
        else if (diffAnos === 2) categoria = '2 anos de cadastro';
        else categoria = '3+ anos de cadastro';
      } else {
        categoria = '1 ano de cadastro'; // Fallback padrão
      }
      // Exceção à regra de consistência: o donut de tempo de cadastro conta toda a amostra
      // (mesmo recorte da tabela), pois o tempo de cadastro não depende dos dados do Elabore.
      if (categoria === '1 ano de cadastro') cad1Ano++;
      else if (categoria === '2 anos de cadastro') cad2Anos++;
      else cad3PlusAnos++;

      cadastroDetalhe.push({
        codigo_lr: r.codigo_lr,
        produtor: r.produtor || r.codigo_lr,
        nome_fazenda: r.nome_fazenda || '—',
        agroindustria: r.agroindustria || '—',
        regiao: r.regiao || '—',
        consultor: r.consultor || '—',
        status: r.status || '—',
        cidade: r.cidade || '',
        estado: r.estado || '',
        cidade_uf: [r.cidade, r.estado].filter(Boolean).join(' / ') || '—',
        data_associacao: r.data_associacao ? formatDate(r.data_associacao) : '—',
        data_associacao_ts: r.data_associacao ? new Date(r.data_associacao).getTime() : null,
        tempo_cadastro: tempoLabel,
        tempo_meses: tempoMeses,
        categoria_cadastro: categoria,
        consistencia_mensal: r.status_consistencia_mensal || 'SEM DADOS',
        no_grafico: true
      });
    });

    cadastroDetalhe.sort((a, b) => {
      const da = a.data_associacao_ts ?? Infinity;
      const db = b.data_associacao_ts ?? Infinity;
      return da - db; // cadastro mais antigo primeiro
    });

    const totalFazendas = econComDados.length;
    const totalCadastro = econTabela.length;
    const mbNegativasCount = totalFazendas - mbPositivas.length;

    // Top 10 Fazendas por Margem Bruta
    // econComDados tem uma linha por fazenda POR MÊS (id_composto = codigo_lr + mes_referencia).
    // Sem agrupar por fazenda antes de rankear, a mesma fazenda pode ocupar várias posições do
    // top 10 (uma para cada mês em que ela pontuou alto) sempre que o recorte filtrado abranger
    // mais de um mês. Aqui mantemos apenas o registro do mês mais recente por codigo_lr.
    const ultimoPorFazenda = linhas => {
      const mapa = new Map();
      linhas.forEach(r => {
        const atual = mapa.get(r.codigo_lr);
        if (!atual || String(r.mes_referencia) > String(atual.mes_referencia)) mapa.set(r.codigo_lr, r);
      });
      return Array.from(mapa.values());
    };
    const porMargem = (a, b) => Number(b.margem_bruta_por_litro || 0) - Number(a.margem_bruta_por_litro || 0);
    const linhaRanking = (r, posicao) => ({
        posicao,
        codigo_lr: r.codigo_lr,
        produtor: r.produtor || r.codigo_lr,
        nome_fazenda: r.nome_fazenda || '—',
        agroindustria: r.agroindustria || '—',
        regiao: r.regiao || '—',
        consultor: r.consultor || '—',
        mes_referencia: r.mes_referencia,
        mes_label: monthLabel(String(r.mes_referencia || '').substring(0, 7)),
        volume_diario_litros: Number(r.volume_diario_litros || 0),
        preco_medio_litro: Number(r.preco_medio_litro || 0),
        coe_por_litro: Number(r.coe_por_litro || 0),
        margem_bruta_por_litro: Number(r.margem_bruta_por_litro || 0),
        flag_positiva: Number(r.margem_bruta_por_litro || 0) > 0,
        consistencia_mensal: r.status_consistencia_mensal || 'SEM DADOS',
        no_grafico: ehConsistente(r)
      });

    // Gráfico: top 10 entre os consistentes
    const top10MbRanking = ultimoPorFazenda(econComDados).sort(porMargem).slice(0, 10).map((r, idx) => linhaRanking(r, idx + 1));

    // Tabela: o mesmo top 10 + os inconsistentes que entrariam nele (margem ≥ 10º colocado),
    // sem posição, para evidenciar quem ficou fora do gráfico
    const corteMargem = top10MbRanking.length === 10 ? top10MbRanking[9].margem_bruta_por_litro : -Infinity;
    const noTop = new Set(top10MbRanking.map(r => r.codigo_lr));
    const inconsistentesNoTop = ultimoPorFazenda(econTabela.filter(r => !ehConsistente(r)))
      .filter(r => !noTop.has(r.codigo_lr) && Number(r.margem_bruta_por_litro || 0) >= corteMargem)
      .map(r => linhaRanking(r, null));
    const top10MbRankingTabela = [...top10MbRanking, ...inconsistentesNoTop].sort(porMargem);

    // Tabela do painel "5 principais itens de custo (COE)": 1 linha por fazenda (mês mais recente
    // do recorte), com a composição do COE de cada uma. Lista também as inconsistentes (sinalizadas).
    const NOMES_BLOCO = { conc: 'Concentrado', vol: 'Volumoso', mo: 'Mão de obra', san: 'Sanidade', out: 'Outras despesas' };
    const coeDetalhe = ultimoPorFazenda(econTabela).map(r => {
      const blocos = {
        conc: Number(r.coe_concentrado || 0), vol: Number(r.coe_volumoso || 0), mo: Number(r.coe_mao_de_obra || 0),
        san: Number(r.coe_sanidade || 0), out: Number(r.coe_outros || 0)
      };
      const total = Object.values(blocos).reduce((a, b) => a + b, 0);
      const perc = v => (total > 0 ? Number(((v / total) * 100).toFixed(1)) : 0);
      const maior = total > 0 ? Object.entries(blocos).sort((a, b) => b[1] - a[1])[0][0] : null;
      return {
        codigo_lr: r.codigo_lr,
        produtor: r.produtor || r.codigo_lr,
        nome_fazenda: r.nome_fazenda || '—',
        consultor: r.consultor || '—',
        mes_referencia: r.mes_referencia,
        mes_label: monthLabel(String(r.mes_referencia || '').substring(0, 7)),
        volume_leite_mes: Number(r.volume_leite_mes || 0),
        coe_total_reais: Number(total.toFixed(2)),
        coe_por_litro: Number(r.coe_por_litro || 0),
        perc_concentrado: perc(blocos.conc),
        perc_volumoso: perc(blocos.vol),
        perc_mao_de_obra: perc(blocos.mo),
        perc_sanidade: perc(blocos.san),
        perc_outros: perc(blocos.out),
        maior_item: maior ? NOMES_BLOCO[maior] : '—',
        consistencia_mensal: r.status_consistencia_mensal || 'SEM DADOS',
        no_grafico: ehConsistente(r)
      };
    }).sort((a, b) => b.coe_total_reais - a.coe_total_reais);

    const cadastroBreakdown = [
      { categoria: '1 ano de cadastro', count: cad1Ano, perc: totalCadastro > 0 ? Number(((cad1Ano / totalCadastro) * 100).toFixed(1)) : 0 },
      { categoria: '2 anos de cadastro', count: cad2Anos, perc: totalCadastro > 0 ? Number(((cad2Anos / totalCadastro) * 100).toFixed(1)) : 0 },
      { categoria: '3+ anos de cadastro', count: cad3PlusAnos, perc: totalCadastro > 0 ? Number(((cad3PlusAnos / totalCadastro) * 100).toFixed(1)) : 0 }
    ];

    const kpisConsolidados = {
      volume_diario_total: Math.round(volDiarioTotal),
      produtividade_l_vl_dia: Number(prodMedia.toFixed(2)),
      preco_medio_litro: Number(precoMedio.toFixed(2)),
      coe_medio_litro: Number(coeMedio.toFixed(2)),
      margem_bruta_litro: Number(mbMedia.toFixed(2)),
      perc_mb_positiva: Number(percMbPositiva.toFixed(1)),
      total_fazendas_ativas: totalFazendas,
      // Amostra dos KPIs: fazendas consistentes (usadas) de todas as fazendas com dados no recorte
      n_fazendas_consistentes: new Set(econComDados.map(r => r.codigo_lr)).size,
      mes_referencia_label: mesEfetivo ? monthLabel(mesEfetivo.substring(0, 7)) : null,
      n_fazendas_total: new Set(econTabela.map(r => r.codigo_lr)).size,
      fazendas_mb_positiva: mbPositivas.length,
      perc_mb_positiva_base: totalFazendas > 0 ? Number(((mbPositivas.length / totalFazendas) * 100).toFixed(1)) : 0,
      fazendas_mb_negativa: mbNegativasCount,
      perc_mb_negativa: totalFazendas > 0 ? Number(((mbNegativasCount / totalFazendas) * 100).toFixed(1)) : 0,
      cad_1_ano: cad1Ano,
      perc_cad_1_ano: totalCadastro > 0 ? Number(((cad1Ano / totalCadastro) * 100).toFixed(1)) : 0,
      cad_2_anos: cad2Anos,
      perc_cad_2_anos: totalCadastro > 0 ? Number(((cad2Anos / totalCadastro) * 100).toFixed(1)) : 0,
      cad_3_plus_anos: cad3PlusAnos,
      perc_cad_3_plus_anos: totalCadastro > 0 ? Number(((cad3PlusAnos / totalCadastro) * 100).toFixed(1)) : 0
    };

    // Aviso da tela 4: mês filtrado sem nenhuma fazenda-mês consistente (ex.: mês corrente ainda
    // não fechado no Elabore). Informa o último mês com dado consistente dentro do mesmo recorte.
    const mesesConsistentes = mesKeysOrdenados;
    const ultimoMesConsistente = mesesConsistentes.length ? mesesConsistentes[mesesConsistentes.length - 1] : null;
    const semDadosConsistentes = Boolean(filterMonth) && econComDados.length === 0;
    const aviso = semDadosConsistentes ? {
      sem_dados_consistentes: true,
      mes_selecionado_label: monthLabel(filterMonth.substring(0, 7)),
      ultimo_mes_consistente_label: ultimoMesConsistente ? monthLabel(ultimoMesConsistente) : null,
      linhas_no_mes: econTabela.length
    } : { sem_dados_consistentes: false };

    return res.status(200).json({
      aviso,
      kpis: kpisConsolidados,
      top5_coe: top5Coe,
      volume_evolution: volumeEvolution,
      cadastro_breakdown: cadastroBreakdown,
      cadastro_detalhe: cadastroDetalhe,
      top10_mb_ranking: top10MbRanking,
      top10_mb_ranking_tabela: top10MbRankingTabela,
      coe_detalhe: coeDetalhe,
      slide4: {
        kpis: kpisConsolidados,
        top5_coe: top5Coe,
        volume_evolution: volumeEvolution
      },
      slide5: {
        kpis: kpisConsolidados,
        cadastro_breakdown: cadastroBreakdown,
        cadastro_detalhe: cadastroDetalhe,
        top10_mb_ranking: top10MbRanking
      }
    });

  } catch (error) {
    console.error('💥 Erro na API /api/economics:', error);
    return res.status(500).json({ error: 'Erro interno no servidor ao processar indicadores econômicos.' });
  }
});
