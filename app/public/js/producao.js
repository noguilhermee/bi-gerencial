/**
 * Slides 5 (Produção e Qualidade), 6 (Carteira e Território) e 7 (Safra de Forrageiras).
 *
 * Só desenha o que /api/production, /api/territory e /api/safra devolvem — nenhuma regra de negócio nem correção de dado
 * aqui (regra 12 do AGENTS.md). Cada gráfico é criado e guardado neste módulo (não em DashboardCharts) para não
 * entrar no applyTheme() genérico; o tema é reaplicado por um observador de data-theme.
 */
(function () {
  'use strict';

  const el = (id) => document.getElementById(id);
  const cssVar = (nome, padrao) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim() || padrao;
  const escapeHtml = (v) => String(v ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  const num = (v, casas = 0) => (v === null || v === undefined || !Number.isFinite(Number(v))
    ? '—' : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas }));
  const seta = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? '' : `${v > 0 ? '▲' : v < 0 ? '▼' : '■'} ${num(Math.abs(v), 1)}%`);

  const INDICADORES = {
    volume_total: { rotulo: 'Volume total', unidade: 'L/dia', casas: 0, cor: '#0f6e56', zero: true },
    produtividade: { rotulo: 'Produtividade', unidade: 'L/vaca/dia', casas: 1, cor: '#2385c7', zero: false },
    ccs: { rotulo: 'CCS', unidade: '×1.000 céls./mL', casas: 0, cor: '#e23e3b', zero: false },
    cpp: { rotulo: 'CPP', unidade: '×1.000 UFC/mL', casas: 0, cor: '#e28a10', zero: false },
    gordura: { rotulo: 'Gordura', unidade: '%', casas: 2, cor: '#b58a00', zero: false },
    proteina: { rotulo: 'Proteína', unidade: '%', casas: 2, cor: '#7c5cbf', zero: false }
  };

  const PALETA_REGIOES = ['#014945', '#2385c7', '#e28a10', '#7c5cbf', '#0E968B', '#c2456b', '#5b8c2a', '#8a6d3b',
    '#d9614c', '#3f6fb5', '#a3a300', '#00838f', '#9c5b9e', '#6d8a96', '#b5651d', '#1b9e77', '#e6ab02', '#66a61e',
    '#a6761d', '#e7298a', '#1f78b4', '#b15928', '#4d4d4d', '#33a02c'];

  const est = {
    prod: null,
    terr: null,
    safra: null,
    safraComposicao: false,
    safraSel: { safra: [], cultura: [], produto: [] },
    aoMudarSafra: null,
    indicador: 'volume_total',
    periodo: 6,
    charts: {},
    regioes: [],          // nomes em ordem alfabética — cor estável entre gráficos e mapa
    mapa: null,
    contorno: null,
    contornoLayer: null,
    estadosLayer: null,         // divisa dos estados (malha IBGE)
    grupos: new Map(),          // pontos (localização) por região
    gruposBolha: new Map(),     // bolhas de produção por região (abaixo dos pontos)
    mostrarBolhas: true,
    ocultas: new Set(),
    agrupamento: (() => { try { return localStorage.getItem('lr.terr.agrupamento') === 'regiao' ? 'regiao' : 'estado'; } catch (e) { return 'estado'; } })(),
    selecionado: null,
    ajustado: false
  };

  const corRegiao = (nome) => PALETA_REGIOES[Math.max(0, est.regioes.indexOf(nome)) % PALETA_REGIOES.length];

  // ── Gráficos ───────────────────────────────────────────────────────────────────────────────────────
  function criarGrafico(id, config) {
    const canvas = el(id);
    if (!canvas || typeof Chart === 'undefined') return null;
    if (est.charts[id]) est.charts[id].destroy();
    est.charts[id] = new Chart(canvas, config);
    return est.charts[id];
  }

  function destruirGrafico(id) {
    if (est.charts[id]) { est.charts[id].destroy(); delete est.charts[id]; }
  }

  /** Rótulos de valor (formatter em closure: funções dentro de options viram "scriptable" no Chart.js v4). */
  function rotulos(formatter, { horizontal = false, dentro = false } = {}) {
    return {
      id: 'lrRotulos',
      afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = '700 11px "Open Sans", Arial, sans-serif';
        ctx.fillStyle = cssVar('--ink', '#162640');
        ctx.strokeStyle = cssVar('--surface', '#ffffff');
        ctx.lineWidth = 3;
        ctx.lineJoin = 'round';
        chart.data.datasets.forEach((ds, di) => {
          const meta = chart.getDatasetMeta(di);
          if (meta.hidden) return;
          meta.data.forEach((ponto, i) => {
            const v = ds.data[i];
            if (v === null || v === undefined) return;
            const texto = formatter(v, i);
            if (!texto) return;
            // dentro: rótulo branco em negrito na parte superior interna da barra (barra baixa demais: volta para fora)
            if (dentro && !horizontal && Math.abs(ponto.base - ponto.y) >= 24) {
              ctx.save();
              ctx.fillStyle = '#ffffff';
              ctx.font = '800 12px "Open Sans", Arial, sans-serif';
              ctx.textAlign = 'center'; ctx.textBaseline = 'top';
              ctx.fillText(texto, ponto.x, Math.min(ponto.y, ponto.base) + 6);
              ctx.restore();
              return;
            }
            if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.strokeText(texto, ponto.x + 6, ponto.y); ctx.fillText(texto, ponto.x + 6, ponto.y); }
            else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.strokeText(texto, ponto.x, ponto.y - 8); ctx.fillText(texto, ponto.x, ponto.y - 8); }
          });
        });
        ctx.restore();
      }
    };
  }

  /** Telas 5 e 6: a API devolve os dados de M-1 para o mês selecionado M. Ex.: "dados de Ago/26 (mês anterior a Set/26)". */
  const anteriorA = (dados) => (dados && dados.mesSelecionadoLabel ? ` (mês anterior a ${dados.mesSelecionadoLabel})` : '');
  const dadosDe = (dados) => `dados de ${dados.mesReferenciaLabel}${anteriorA(dados)}`;

  const tooltipBase = () => ({
    backgroundColor: 'rgba(0, 77, 73, 0.94)', titleColor: '#fff', bodyColor: '#fff', padding: 10, cornerRadius: 7,
    titleFont: { size: LR_FS.ax, weight: '700' }, bodyFont: { size: LR_FS.ax }
  });

  // ── Tela 5: Produção e Qualidade ──────────────────────────────────────────────────────────────────
  function setKpi(idValor, valor, idSub, unidade, variacao, rotuloAnterior) {
    const v = el(idValor);
    if (v) v.textContent = valor;
    const s = el(idSub);
    if (s) s.textContent = variacao !== null && variacao !== undefined ? `${unidade} · ${seta(variacao)} vs ${rotuloAnterior}` : unidade;
  }

  function limparProducao(mensagem) {
    ['kpiProdVolume', 'kpiProdProdutividade', 'kpiProdCcs', 'kpiProdCpp', 'kpiProdGordura', 'kpiProdProteina'].forEach((id) => { if (el(id)) el(id).textContent = '—'; });
    destruirGrafico('chartProdEvolucao');
    const t = el('prodTrajetoria');
    if (t) t.innerHTML = `<div class="traj-empty">${escapeHtml(mensagem)}</div>`;
  }

  function renderProducao(dados) {
    est.prod = dados || null;
    if (!dados) return limparProducao('Não foi possível carregar os dados de produção agora.');
    if (dados.disponivel === false) return limparProducao('Dados de produção ainda não disponíveis: falta criar/carregar a tabela tab_fato_producao_mensal.');
    if (dados.semDados) return limparProducao('Sem medições de produção para os filtros selecionados.');

    const k = dados.kpis || {};
    const v = dados.variacao || {};
    const ant = v.mesAnteriorLabel || 'mês anterior';
    setKpi('kpiProdVolume', num(k.volume_total), 'kpiProdVolumeSub', 'L/dia', v.volume_total, ant);
    setKpi('kpiProdProdutividade', num(k.produtividade, 1), 'kpiProdProdutividadeSub', 'L/vaca/dia', v.produtividade, ant);
    setKpi('kpiProdCcs', num(k.ccs), 'kpiProdCcsSub', 'mil céls./mL', v.ccs, ant);
    setKpi('kpiProdCpp', num(k.cpp), 'kpiProdCppSub', 'mil UFC/mL', v.cpp, ant);
    setKpi('kpiProdGordura', k.gordura === null || k.gordura === undefined ? '—' : `${num(k.gordura, 2)}%`, 'kpiProdGorduraSub', 'média pond.', v.gordura, ant);
    setKpi('kpiProdProteina', k.proteina === null || k.proteina === undefined ? '—' : `${num(k.proteina, 2)}%`, 'kpiProdProteinaSub', 'média pond.', v.proteina, ant);

    renderEvolucao();
    renderTrajetoria();
    renderAtencao(dados);
  }

  function serieAtual() {
    const todas = est.prod && est.prod.evolucao && est.prod.evolucao.todas;
    if (!todas) return null;
    if (!est.periodo || est.periodo >= todas.labels.length) return todas;
    const recorte = {};
    Object.keys(todas).forEach((k) => { recorte[k] = Array.isArray(todas[k]) ? todas[k].slice(-est.periodo) : todas[k]; });
    return recorte;
  }

  function renderEvolucao() {
    const evo = est.prod && est.prod.evolucao;
    if (!evo) return;
    document.querySelectorAll('#prodBase button').forEach((b) => {
      const ativo = Number(b.dataset.periodo) === est.periodo;
      b.classList.toggle('active', ativo);
      b.setAttribute('aria-pressed', String(ativo));
    });
    document.querySelectorAll('#prodIndicador button').forEach((b) => {
      const ativo = b.dataset.ind === est.indicador;
      b.classList.toggle('active', ativo);
      b.setAttribute('aria-pressed', String(ativo));
    });

    const serie = serieAtual();
    if (!serie) return;
    const cfg = INDICADORES[est.indicador];
    const sub = el('prodEvolucaoSub');
    if (sub) {
      const ate = est.prod && est.prod.mesFimLabel ? ` · até ${est.prod.mesFimLabel}${anteriorA(est.prod)}` : '';
      sub.textContent = `todas as fazendas medidas em cada mês · ${cfg.unidade}${ate}`;
    }
    const valores = serie[est.indicador] || [];
    const fmt = (x) => num(x, cfg.casas);
    const eixo = cssVar('--muted', '#65707c');
    criarGrafico('chartProdEvolucao', {
      type: 'line',
      plugins: [rotulos(fmt)],
      data: {
        labels: serie.labels,
        datasets: [{
          label: cfg.rotulo, data: valores, borderColor: cfg.cor, backgroundColor: `${cfg.cor}22`, fill: true,
          tension: 0.3, borderWidth: 2.5, pointRadius: 3.5, pointBackgroundColor: cfg.cor, spanGaps: true
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: 24, right: 40, left: 8 } },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tooltipBase(),
            callbacks: {
              label: (c) => ` ${cfg.rotulo}: ${fmt(c.parsed.y)} ${cfg.unidade}`,
              afterLabel: (c) => ` ${num(serie.fazendas[c.dataIndex])} fazendas medidas`
            }
          }
        },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { color: eixo, font: { size: LR_FS.ax, weight: '600' } } },
          y: { beginAtZero: cfg.zero, grid: { color: cssVar('--line-soft', '#e9edef') }, border: { display: false }, ticks: { color: eixo, font: { size: LR_FS.ax } } }
        }
      }
    });
  }

  function renderTrajetoria() {
    const alvo = el('prodTrajetoria');
    const t = est.prod && est.prod.trajetoria;
    const sub = el('prodTrajetoriaSub');
    if (!alvo) return;
    if (!t) {
      if (sub) sub.textContent = 'volume diário';
      alvo.innerHTML = '<div class="traj-empty">Poucos meses medidos para comparar períodos.</div>';
      return;
    }
    const rot = (lista) => `${lista[0]}–${lista[lista.length - 1]}`;
    const rotRec = rot(t.janelaRecente);
    const rotAnt = rot(t.janelaAnterior);
    if (sub) sub.textContent = `volume diário · ${rotRec} contra ${rotAnt}${anteriorA(est.prod)}`;
    const linhas = [
      ['evoluiu', 'up', 'Evoluíram', `acima de +${t.banda}%`],
      ['estavel', 'flat', 'Estáveis', `entre −${t.banda}% e +${t.banda}%`],
      ['regrediu', 'down', 'Regrediram', `abaixo de −${t.banda}%`]
    ];
    const total = linhas.reduce((s, [k]) => s + (t.contagem[k] || 0), 0) || 1;
    alvo.innerHTML = linhas.map(([k, cls, titulo, dica]) => `
      <button class="traj-row traj-row--${cls}" type="button" data-traj="${k}"${t.contagem[k] ? '' : ' disabled'}>
        <strong>${num(t.contagem[k])}</strong>
        <span>${titulo}<i>${dica}</i></span>
        <em title="Ver produtores">${num(100 * (t.contagem[k] || 0) / total, 0)}% ›</em>
      </button>`).join('');
    alvo.querySelectorAll('[data-traj]').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.traj;
      const titulos = { evoluiu: 'Produtores que evoluíram', estavel: 'Produtores estáveis', regrediu: 'Produtores que regrediram' };
      // a lista (filtro por coluna, ordenação, paginação, exportação) é montada por main.js
      if (typeof window.openTrajectoryList === 'function') {
        window.openTrajectoryList(k, titulos[k], `Média de ${rotRec} contra ${rotAnt} · ${num(t[k].length)} produtores`, rotAnt, rotRec);
      }
    }));
  }

  /** As tabelas de CCS/CPP acima do limite são montadas por main.js; aqui só os subtítulos. */
  function renderAtencao(dados) {
    if (el('prodCcsSub')) el('prodCcsSub').textContent = `CCS acima de 500 mil céls./mL · ${dadosDe(dados)}`;
    if (el('prodCppSub')) el('prodCppSub').textContent = `CPP acima de 300 mil UFC/mL · ${dadosDe(dados)}`;
  }

  // ── Tela 6: Carteira e Território ─────────────────────────────────────────────────────────────────
  function limparTerritorio(mensagem) {
    ['kpiTerrPropriedades', 'kpiTerrRegioes', 'kpiTerrMunicipios', 'kpiTerrLitros', 'kpiTerrMapa', 'kpiTerrAprox'].forEach((id) => { if (el(id)) el(id).textContent = '—'; });
    destruirGrafico('chartTerrPropriedades');
    destruirGrafico('chartTerrLitros');
    limparMapa();
    const vazio = el('mapaVazio');
    if (vazio) { vazio.textContent = mensagem; vazio.hidden = false; }
    const aviso = el('terrAviso');
    if (aviso) { aviso.textContent = mensagem; aviso.hidden = false; }
    if (el('countTerrMapa')) el('countTerrMapa').textContent = '0 propriedades';
    if (el('mapaLegenda')) el('mapaLegenda').innerHTML = '';
  }

  function renderTerritorio(dados, opcoes = {}) {
    est.terr = dados || null;
    if (!dados) return limparTerritorio('Não foi possível carregar os dados de território agora.');
    if (dados.disponivel === false) return limparTerritorio('Dados de território ainda não disponíveis: falta criar/carregar tab_fato_producao_mensal e as colunas de coordenada de tab_dim_fazendas_ativas.');
    if (dados.semDados || !dados.kpis) return limparTerritorio('Sem propriedades ativas para os filtros selecionados.');

    const k = dados.kpis;
    definirPaleta(dados);
    rotularAgrupamento();
    const set = (id, v, sub, txt) => { if (el(id)) el(id).textContent = v; if (el(sub) && txt) el(sub).textContent = txt; };
    set('kpiTerrPropriedades', num(k.propriedades), 'kpiTerrPropriedadesSub', `Carteira de ${dados.mesReferenciaLabel}${anteriorA(dados)}`);
    set('kpiTerrRegioes', num(est.agrupamento === 'regiao' ? k.regionais : k.regioes));
    set('kpiTerrMunicipios', num(k.municipios));
    set('kpiTerrLitros', num(k.litrosTotal), 'kpiTerrLitrosSub', `${num(k.fazendasMedidas)} medidas · ${dados.janelaMedicao}`);
    set('kpiTerrMapa', num(k.comLocalizacao), 'kpiTerrMapaSub', k.semLocalizacao ? `${num(k.semLocalizacao)} sem localização` : 'Todas localizadas');
    set('kpiTerrAprox', num(k.aproximadas));
    const aviso = el('terrAviso');
    if (aviso) aviso.hidden = true;
    if (el('terrLitrosSub')) el('terrLitrosSub').textContent = `Última medição · ${dados.janelaMedicao} · L/dia${anteriorA(dados)}`;
    if (el('terrPropSub')) el('terrPropSub').textContent = `Carteira ativa · nº de propriedades · ${dadosDe(dados)}`;
    if (el('terrMapaSub')) el('terrMapaSub').textContent = `Cores por ${est.agrupamento === 'regiao' ? 'região' : 'estado'} · clique em uma propriedade para ver os dados · ${dadosDe(dados)}`;

    renderBarrasRegiao(dados);
    renderMapa(dados, opcoes);
  }

  /** Top 10 regiões + "Outras" (o painel é baixo; 20 barras não cabem). */
  function topComOutras(lista, campo, limite = 10) {
    const ordenada = [...lista].sort((a, b) => b[campo] - a[campo]).filter((r) => r[campo] > 0);
    const topo = ordenada.slice(0, limite);
    const resto = ordenada.slice(limite);
    if (resto.length) topo.push({ regiao: `Outras (${resto.length})`, [campo]: resto.reduce((s, r) => s + r[campo], 0), outras: true });
    return topo;
  }

  function barrasHorizontais(id, itens, campo, sufixoTooltip) {
    const total = itens.reduce((s, r) => s + r[campo], 0) || 1;
    const eixo = cssVar('--muted', '#65707c');
    const fmt = (v) => `${num(v)}  (${num(100 * v / total, 1)}%)`;
    criarGrafico(id, {
      type: 'bar',
      plugins: [rotulos((v) => fmt(v), { horizontal: true })],
      data: {
        labels: itens.map((r) => r.regiao),
        datasets: [{ data: itens.map((r) => r[campo]), backgroundColor: itens.map((r) => (r.outras ? '#9AAEAB' : corRegiao(r.regiao))), borderRadius: 4, maxBarThickness: 20 }]
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        layout: { padding: { right: 96 } },
        plugins: { legend: { display: false }, tooltip: { ...tooltipBase(), callbacks: { label: (c) => ` ${num(c.parsed.x)} ${sufixoTooltip}` } } },
        scales: {
          x: { beginAtZero: true, grid: { color: cssVar('--line-soft', '#e9edef') }, border: { display: false }, ticks: { color: eixo, font: { size: LR_FS.lbl } } },
          y: {
            grid: { display: false }, border: { display: false },
            ticks: { color: eixo, font: { size: LR_FS.lg, weight: '600' }, autoSkip: false, callback(v) { const t = this.getLabelForValue(v); return t.length > 24 ? `${t.slice(0, 23)}…` : t; } }
          }
        }
      }
    });
  }

  // Agrupamento da tela de território, escolhido no botão "Estado | Região" do mapa:
  //   'estado' = UF da fazenda (coluna regiao) · 'regiao' = região definida pela agroindústria (coluna regional)
  const porRegiaoAgro = () => est.agrupamento === 'regiao';
  const grupoDe = (p) => (porRegiaoAgro() ? p.regional : p.regiao) || 'NAO INFORMADA';
  const gruposDe = (dados) => (porRegiaoAgro() ? dados.regionais : dados.regioes) || [];

  // Cor estável por nome dentro do agrupamento atual (mesma cor nas barras e no mapa)
  function definirPaleta(dados) {
    est.regioes = [...new Set([...gruposDe(dados).map((r) => r.regiao), ...(dados.pontos || []).map(grupoDe)])]
      .sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }

  function rotularAgrupamento() {
    const regiao = porRegiaoAgro();
    const set = (id, txt) => { if (el(id)) el(id).textContent = txt; };
    set('terrPropTitulo', regiao ? 'Propriedades ativas por região' : 'Propriedades ativas por estado');
    set('terrLitrosTitulo', regiao ? 'Produção de leite por região' : 'Produção de leite por estado');
    set('kpiTerrRegioesLabel', regiao ? 'Regiões' : 'Estados');
    document.querySelectorAll('#terrAgrupamento button').forEach((b) => {
      const ativo = b.dataset.agrupamento === est.agrupamento;
      b.classList.toggle('active', ativo);
      b.setAttribute('aria-pressed', String(ativo));
    });
  }

  // Troca Estado ⇄ Região sem nova consulta: a API já devolve os dois agrupamentos
  function mudarAgrupamento(agrupamento) {
    if (agrupamento === est.agrupamento) return;
    est.agrupamento = agrupamento;
    try { localStorage.setItem('lr.terr.agrupamento', agrupamento); } catch (e) { /* preferência só desta sessão */ }
    est.ocultas.clear();   // os nomes da legenda mudam de um agrupamento para o outro
    if (est.terr && est.terr.kpis) renderTerritorio(est.terr, { manterVista: true });
    else rotularAgrupamento();
  }

  function renderBarrasRegiao(dados) {
    const regioes = gruposDe(dados);
    barrasHorizontais('chartTerrPropriedades', topComOutras(regioes, 'propriedades'), 'propriedades', 'propriedades');
    barrasHorizontais('chartTerrLitros', topComOutras(regioes, 'litros'), 'litros', 'L/dia');
  }

  // ── Mapa ───────────────────────────────────────────────────────────────────────────────────────────
  // Fundo do mapa: estados (malha do IBGE, /data/brasil_uf.json) preenchidos com divisa fina; contorno do país por cima
  function estiloEstados() {
    return { color: cssVar('--green-300', '#8be4d2'), weight: 0.7, fillColor: cssVar('--green-100', '#eaf6f3'), fillOpacity: 1, interactive: false };
  }

  function estiloContorno() {
    return { color: cssVar('--green-300', '#8be4d2'), weight: 1.4, fill: false, interactive: false };
  }

  function fundoParaTras() {
    est.contornoLayer?.bringToBack();
    est.estadosLayer?.bringToBack();
  }

  // Sigla da UF no centro do estado, só a partir do zoom 5 (no Brasil inteiro as siglas do Nordeste se sobrepõem)
  const ZOOM_ROTULO_UF = 5;
  function atualizarRotulosUf() {
    if (!est.mapa) return;
    est.mapa.getContainer().classList.toggle('sem-rotulo-uf', est.mapa.getZoom() < ZOOM_ROTULO_UF);
  }

  function limparMapa() {
    est.grupos.forEach((g) => g.remove());
    est.grupos.clear();
    est.gruposBolha.forEach((g) => g.remove());
    est.gruposBolha.clear();
    if (el('mapaEscala')) el('mapaEscala').hidden = true;
    est.selecionado = null;
    const ficha = el('mapaFicha');
    if (ficha) ficha.hidden = true;
  }

  function garantirMapa() {
    if (est.mapa) return est.mapa;
    const alvo = el('mapaTerritorio');
    if (!alvo || typeof L === 'undefined') return null;
    est.mapa = L.map(alvo, { scrollWheelZoom: false, attributionControl: false, zoomSnap: 0.25, minZoom: 3, maxZoom: 13, preferCanvas: true });
    est.mapa.setView([-15.5, -50], 4);
    fetch('/data/brasil_uf.json').then((r) => r.json()).then((geo) => {
      est.estadosLayer = L.geoJSON(geo, {
        style: estiloEstados,
        onEachFeature: (f, camada) => camada.bindTooltip(f.properties.uf,
          { permanent: true, direction: 'center', className: 'uf-rotulo', interactive: false })
      }).addTo(est.mapa);
      fundoParaTras();
      atualizarRotulosUf();
    }).catch(() => { /* o mapa funciona sem a divisa dos estados */ });
    fetch('/data/brasil.json').then((r) => r.json()).then((geo) => {
      est.contorno = geo;
      est.contornoLayer = L.geoJSON(geo, { style: estiloContorno }).addTo(est.mapa);
      fundoParaTras();
    }).catch(() => { /* o mapa funciona sem o contorno */ });
    est.mapa.on('zoomend', atualizarRotulosUf);
    // zoom por rolagem só com o mouse sobre o mapa (evita brigar com a rolagem da página no celular)
    alvo.addEventListener('mouseenter', () => { if (window.innerWidth > 1024) est.mapa.scrollWheelZoom.enable(); });
    alvo.addEventListener('mouseleave', () => est.mapa.scrollWheelZoom.disable());
    est.mapa.on('click', () => { const f = el('mapaFicha'); if (f) f.hidden = true; desmarcar(); });
    // slide fora da tela / painel em tela cheia mudam o tamanho: refaz o layout e enquadra na 1ª vez visível
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => { if (!est.mapa) return; est.mapa.invalidateSize(); if (!est.ajustado) enquadrar(); }).observe(alvo);
    }
    return est.mapa;
  }

  function enquadrar() {
    if (!est.mapa || !est.limites) return;
    const caixa = el('mapaTerritorio');
    if (!caixa || caixa.clientWidth < 50 || caixa.clientHeight < 50) return;
    est.mapa.fitBounds(est.limites, { padding: [16, 16], maxZoom: 9 });
    est.ajustado = true;
  }

  // Área da bolha proporcional aos litros/dia (raio ∝ √litros), com teto no percentil 95 para um outlier não engolir o mapa
  const RAIO_MAX_BOLHA = 24;
  const RAIO_MIN_BOLHA = 3;
  const raioBolha = (litros, teto) => Math.max(RAIO_MIN_BOLHA, RAIO_MAX_BOLHA * Math.sqrt(Math.min(litros, teto) / teto));

  function tetoProducao(pontos) {
    const v = pontos.map((p) => p.litros).filter((l) => l > 0).sort((a, b) => a - b);
    return v.length ? v[Math.min(v.length - 1, Math.floor(v.length * 0.95))] : 0;
  }

  function arredondarBonito(v) {
    if (v >= 10000) return Math.round(v / 1000) * 1000;
    if (v >= 1000) return Math.round(v / 500) * 500;
    return Math.max(50, Math.round(v / 50) * 50);
  }

  function renderEscala(teto) {
    const alvo = el('mapaEscala');
    if (!alvo) return;
    if (!teto || !est.mostrarBolhas) { alvo.hidden = true; return; }
    const amostras = [0.1, 0.35, 1].map((f) => arredondarBonito(teto * f));
    alvo.innerHTML = `<div class="me-titulo">Produção (L/dia)</div><div class="me-linha">${amostras.map((v) => {
      const d = Math.round(2 * raioBolha(v, teto));
      return `<div class="me-item"><i style="width:${d}px;height:${d}px"></i><span>${num(v)}${v === amostras[2] ? '+' : ''}</span></div>`;
    }).join('')}</div><div class="me-nota">área ∝ litros/dia</div>`;
    alvo.hidden = false;
  }

  function estiloBase(p) {
    const cor = corRegiao(grupoDe(p));
    return p.aproximada
      ? { radius: 5, weight: 2, color: cor, fillColor: cor, fillOpacity: 0.05, opacity: 0.95 }
      : { radius: 4.5, weight: 0.8, color: '#ffffff', fillColor: cor, fillOpacity: 0.85, opacity: 1 };
  }
  const estiloSelecionado = { radius: 9, weight: 3, color: '#014945', fillColor: '#ffd23f', fillOpacity: 1, opacity: 1 };

  function desmarcar() {
    if (est.selecionado) { est.selecionado.setStyle(estiloBase(est.selecionado._p)); est.selecionado.setRadius(estiloBase(est.selecionado._p).radius); est.selecionado = null; }
  }

  function mostrarFicha(p, marcador) {
    desmarcar();
    est.selecionado = marcador;
    marcador.setStyle(estiloSelecionado);
    marcador.setRadius(estiloSelecionado.radius);
    marcador.bringToFront();
    const ficha = el('mapaFicha');
    if (!ficha) return;
    ficha.innerHTML = `
      <button class="fc-fechar" type="button" aria-label="Fechar">&times;</button>
      <div class="fc-nome">${escapeHtml(p.produtor)}</div>
      <div class="fc-loc">${escapeHtml(p.propriedade)}${p.cidade ? ` · ${escapeHtml(p.cidade)}${p.estado ? ` / ${escapeHtml(p.estado)}` : ''}` : ''}</div>
      <div class="fc-lista">
        <div class="fc-item"><span>Agroindústria</span><b>${escapeHtml(p.agroindustria)}</b></div>
        <div class="fc-item"><span>Estado</span><b>${escapeHtml(p.regiao)}</b></div>
        ${p.regional && p.regional !== 'NAO INFORMADA' ? `<div class="fc-item"><span>Região</span><b>${escapeHtml(p.regional)}</b></div>` : ''}
        <div class="fc-item"><span>Consultor(a)</span><b>${p.consultores.length ? p.consultores.map(escapeHtml).join('<br>') : '—'}</b></div>
        <div class="fc-item"><span>Projeto</span><b>${escapeHtml(p.projeto)}</b></div>
        <div class="fc-item"><span>Código</span><b>${escapeHtml(p.codigo_lr)}</b></div>
      </div>
      <div class="fc-prod"><span>Produção (última medição)</span>${p.litros ? `<b>${num(p.litros)}</b> <small>L/dia</small>` : '<small>sem medição nos últimos 3 meses</small>'}</div>
      ${p.aproximada ? '<div class="fc-aprox">Posição aproximada: centro do município (cadastro sem coordenada válida)</div>' : ''}`;
    ficha.hidden = false;
    ficha.querySelector('.fc-fechar').addEventListener('click', () => { ficha.hidden = true; desmarcar(); });
  }

  /** Bolhas primeiro e pontos depois (ficam por cima), respeitando regiões ocultas e o botão de produção. */
  function aplicarCamadas() {
    const mapa = est.mapa;
    if (!mapa) return;
    est.gruposBolha.forEach((g) => g.remove());
    est.grupos.forEach((g) => g.remove());
    if (est.mostrarBolhas) est.gruposBolha.forEach((g, regiao) => { if (!est.ocultas.has(regiao)) g.addTo(mapa); });
    est.grupos.forEach((g, regiao) => { if (!est.ocultas.has(regiao)) g.addTo(mapa); });
    fundoParaTras();
    if (est.selecionado) est.selecionado.bringToFront();
  }

  function renderMapa(dados, { manterVista = false } = {}) {
    const mapa = garantirMapa();
    const vazio = el('mapaVazio');
    const pontos = dados.pontos || [];
    if (el('countTerrMapa')) el('countTerrMapa').textContent = `${num(pontos.length)} propriedades`;
    if (!mapa) { if (vazio) { vazio.textContent = 'Mapa indisponível (biblioteca não carregou).'; vazio.hidden = false; } return; }
    limparMapa();
    if (!pontos.length) { if (vazio) { vazio.textContent = 'Sem localização para exibir.'; vazio.hidden = false; } if (el('mapaLegenda')) el('mapaLegenda').innerHTML = ''; return; }
    if (vazio) vazio.hidden = true;

    // 1) bolhas de produção (maiores primeiro, para as pequenas não ficarem escondidas); não recebem clique
    const teto = tetoProducao(pontos);
    if (teto) {
      pontos.filter((p) => p.litros > 0).sort((a, b) => b.litros - a.litros).forEach((p) => {
        if (!est.gruposBolha.has(grupoDe(p))) est.gruposBolha.set(grupoDe(p), L.layerGroup());
        const cor = corRegiao(grupoDe(p));
        L.circleMarker([p.lat, p.lng], { radius: raioBolha(p.litros, teto), color: cor, weight: 1, opacity: 0.6, fillColor: cor, fillOpacity: 0.2, interactive: false })
          .addTo(est.gruposBolha.get(grupoDe(p)));
      });
    }
    // 2) pontos de localização por cima, clicáveis
    pontos.forEach((p) => {
      if (!est.grupos.has(grupoDe(p))) est.grupos.set(grupoDe(p), L.layerGroup());
      const m = L.circleMarker([p.lat, p.lng], { ...estiloBase(p), bubblingMouseEvents: false });
      m._p = p;
      m.bindTooltip(`${escapeHtml(p.produtor)}${p.litros ? ` · ${num(p.litros)} L/dia` : ''}`, { direction: 'top', offset: [0, -4], className: 'map-tip' });
      m.on('click', () => mostrarFicha(p, m));
      m.addTo(est.grupos.get(grupoDe(p)));
    });
    est.tetoProducao = teto;
    aplicarCamadas();
    renderEscala(teto);

    const usadas = [...est.grupos.keys()].sort((a, b) => a.localeCompare(b, 'pt-BR'));
    const legenda = el('mapaLegenda');
    if (legenda) {
      legenda.innerHTML = `<span class="lg lg-toggle${est.mostrarBolhas ? '' : ' off'}" id="lgBolhas" title="Mostrar/ocultar a área proporcional à produção"><i class="dot dot-anel"></i>Área = produção</span>`
        + usadas.map((r) => `<span class="lg${est.ocultas.has(r) ? ' off' : ''}" data-reg="${escapeHtml(r)}" title="Clique para mostrar/ocultar"><i class="dot" style="background:${corRegiao(r)}"></i>${escapeHtml(r)}</span>`).join('')
        + `<span class="lg lg-nota">${num(pontos.length)} localizadas · ${num(pontos.filter((p) => p.aproximada).length)} aproximadas (marcador vazado)</span>`;
      legenda.querySelectorAll('.lg[data-reg]').forEach((s) => s.addEventListener('click', () => {
        const reg = s.dataset.reg;
        if (!est.grupos.has(reg)) return;
        if (est.ocultas.has(reg)) est.ocultas.delete(reg); else est.ocultas.add(reg);
        s.classList.toggle('off', est.ocultas.has(reg));
        aplicarCamadas();
      }));
      el('lgBolhas')?.addEventListener('click', () => {
        est.mostrarBolhas = !est.mostrarBolhas;
        el('lgBolhas').classList.toggle('off', !est.mostrarBolhas);
        aplicarCamadas();
        renderEscala(est.tetoProducao);
      });
    }

    est.limites = L.latLngBounds(pontos.map((p) => [p.lat, p.lng])).pad(0.08);
    if (manterVista) return;   // só trocou Estado ⇄ Região: mantém o zoom e a posição de quem está olhando
    est.ajustado = false;
    mapa.invalidateSize();
    enquadrar();
    [200, 600].forEach((ms) => setTimeout(() => { if (!est.ajustado) enquadrar(); else mapa.invalidateSize(); }, ms));
  }

  // ── Tela 7: Safra de Forrageiras ──────────────────────────────────────────────────────────────────
  const SISTEMA_ROTULO = { 'PLANTIO DIRETO': 'Plantio direto', 'CULTIVO MINIMO': 'Cultivo mínimo', CONVENCIONAL: 'Convencional' };
  const SISTEMA_COR = { 'PLANTIO DIRETO': '#0f7a55', 'CULTIVO MINIMO': '#72b35a', CONVENCIONAL: '#4f93d6' };
  const ETAPAS_CUSTO = [
    { chave: 'pre_plantio', rotulo: 'Pré-plantio', cor: '#cfe8da', texto: '#0f2f2a' },
    { chave: 'plantio', rotulo: 'Plantio', cor: '#85c4a2', texto: '#0f2f2a' },
    { chave: 'tratos', rotulo: 'Tratos culturais', cor: '#2f9168', texto: '#ffffff' },
    { chave: 'colheita', rotulo: 'Colheita e ensilagem', cor: '#0e5a42', texto: '#ffffff' },
    { chave: 'etapa_nao_informada', rotulo: 'Etapa não informada', cor: '#9AAEAB', texto: '#0f2f2a' }
  ];
  const titulo = (v) => { const t = String(v ?? '').toLowerCase(); return t ? t.charAt(0).toUpperCase() + t.slice(1) : '—'; };
  const pct = (v) => (v === null || v === undefined ? '—' : `${num(v)}%`);
  const GRAFICOS_SAFRA = ['chartSafraSistema', 'chartSafraCobHist', 'chartSafraProdutividade', 'chartSafraCustoHa', 'chartSafraCustoT'];

  function limparSafra(mensagem) {
    if (el('safraNFazendas')) el('safraNFazendas').textContent = '–';
    GRAFICOS_SAFRA.forEach(destruirGrafico);
    ['safraSistemaSub', 'safraCoberturaSub', 'safraProdSub', 'safraCustoSub', 'safraDonutTotal', 'safraCobPctCom', 'safraCobPctSem'].forEach((id) => { if (el(id)) el(id).textContent = '—'; });
    ['safraSistemaLegenda', 'safraConservacionista', 'safraCobDestaque', 'safraCobHaCom', 'safraCobHaSem'].forEach((id) => { if (el(id)) el(id).innerHTML = ''; });
    ['safraCobBarraCom', 'safraCobBarraSem'].forEach((id) => { if (el(id)) el(id).style.width = '0'; });
    renderHistoricoCobertura([]);
    if (el('safraRodape')) el('safraRodape').innerHTML = '';
    const aviso = el('safraAviso');
    if (aviso) { aviso.textContent = mensagem; aviso.hidden = false; }
  }

  function preencherSelect(id, opcoes, selecionados, formato = (v) => v) {
    const sel = el(id);
    if (!sel) return;
    const marcados = new Set(selecionados || []);
    sel.innerHTML = opcoes.map((o) => `<option value="${escapeHtml(o)}"${marcados.has(o) ? ' selected' : ''}>${escapeHtml(formato(o))}</option>`).join('');
    sel.disabled = !opcoes.length;
    // O seletor é desenhado pelo dropdown padrão dos filtros (main.js): o 'change' faz o texto exibido acompanhar as
    // opções; a guarda impede que esse evento sintético recarregue a tela.
    est.sincronizando = true;
    sel.dispatchEvent(new Event('change'));
    est.sincronizando = false;
  }

  /** "Milho", "Milho e Sorgo", "3 culturas" — rótulo curto de uma seleção múltipla. */
  function rotuloSelecao(lista, formato = titulo) {
    const itens = (lista || []).map(formato);
    return itens.length <= 2 ? itens.join(' e ') : `${itens.length} selecionados`;
  }

  function renderSafra(dados) {
    est.safra = dados || null;
    if (!dados) return limparSafra('Não foi possível carregar os dados de safra agora.');
    if (dados.disponivel === false) return limparSafra('Dados de safra ainda não disponíveis: falta criar/carregar tab_fato_safra_forrageira.');
    if (dados.semDados) return limparSafra('Sem dados de safra para os filtros selecionados.');

    // A API devolve a seleção efetiva (padrão quando nada foi pedido): o painel passa a refleti-la
    const selecao = dados.selecao || { safra: [], cultura: [], produto: [] };
    est.safraSel = { safra: [...selecao.safra], cultura: [...selecao.cultura], produto: [...selecao.produto] };
    preencherSelect('safraSelSafra', dados.safras || [], selecao.safra);
    preencherSelect('safraSelCultura', dados.culturas || [], selecao.cultura, titulo);
    preencherSelect('safraSelProduto', dados.produtos || [], selecao.produto, titulo);
    const baseCusto = dados.custoBase === 'DEFLACIONADO' ? 'custo deflacionado (IGP-DI)' : 'custo nominal';
    if (el('safraNota')) { el('safraNota').textContent = `${String(dados.ciclo || '').replace('_', 'ª ').toLowerCase()} · soma/soma por sistema · ${baseCusto}`; el('safraNota').title = el('safraNota').textContent; }
    const aviso = el('safraAviso');
    if (aviso) {
      // Avisos metodológicos (mais de um produto/cultura misturam bases): informativos, não bloqueiam a tela
      aviso.textContent = (dados.avisos || []).join(' ');
      aviso.hidden = !(dados.avisos || []).length;
    }

    const sis = dados.sistema || {};
    const cob = dados.cobertura || {};
    const porSis = dados.porSistema || [];
    const rod = dados.rodape || {};
    if (el('safraNFazendas')) el('safraNFazendas').textContent = num(sis.fazendas);

    renderGraficosSafra(dados);

    const rodape = el('safraRodape');
    if (rodape) {
      const partes = [`Produtor com áreas em dois sistemas conta nos dois: os nº de fazendas por sistema não somam o total.`];
      if (sis.areaNaoInformada > 0) partes.push(`${num(sis.areaNaoInformada, 1)} ha sem sistema de plantio informado (fora dos percentuais).`);
      if (rod.producoesInconsistentes > 0) partes.push(`${num(rod.producoesInconsistentes)} produções descartadas por inconsistência na origem (sem colheita, ciclo divergente ou custo/ha acima do teto).`);
      if (rod.fazendasSemPrePlantio > 0) partes.push(`${num(rod.fazendasSemPrePlantio)} de ${num(rod.fazendasComCusto)} fazendas sem custo de pré-plantio lançado.`);
      if (rod.fontes && rod.fontes.length) partes.push(`Fonte: ${rod.fontes.map(titulo).join(', ')}.`);
      rodape.textContent = partes.join(' ');
    }
  }

  /** Rótulos do eixo X em duas linhas: a 1ª (nome do sistema) em negrito, a 2ª (fazendas/área) normal, cor #004D48.
   * Os ticks nativos ficam transparentes só para reservar o espaço; o Chart.js aplica uma única fonte a todas as linhas. */
  function eixoXDuasLinhas(tamanho) {
    return {
      id: 'lrEixoXDuasLinhas',
      afterDraw(chart) {
        const escala = chart.scales.x;
        if (!escala) return;
        const ctx = chart.ctx;
        const cor = document.documentElement.dataset.theme === 'dark' ? cssVar('--ink', '#E8F2EF') : '#004D48';
        const passo = tamanho * 1.25;
        ctx.save();
        ctx.fillStyle = cor;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        chart.data.labels.forEach((rotulo, i) => {
          const linhas = Array.isArray(rotulo) ? rotulo : [rotulo];
          const x = escala.getPixelForTick(i);
          linhas.forEach((linha, j) => {
            ctx.font = `${j === 0 ? 800 : 400} ${tamanho}px "Open Sans", Arial, sans-serif`;
            ctx.fillText(String(linha), x, escala.top + 5 + j * passo);
          });
        });
        ctx.restore();
      }
    };
  }

  /** Percentual dentro de cada fatia da rosca (só fatias com espaço). */
  function rotulosFatias(valores) {
    return {
      id: 'lrFatias',
      afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = '800 12px "Open Sans", Arial, sans-serif';
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        chart.getDatasetMeta(0).data.forEach((arco, i) => {
          if (!valores[i] || valores[i] < 6) return;
          const { x, y } = arco.tooltipPosition();
          ctx.fillText(`${valores[i]}%`, x, y);
        });
        ctx.restore();
      }
    };
  }

  function renderGraficosSafra(dados) {
    const eixo = cssVar('--muted', '#65707c');
    const grade = cssVar('--line-soft', '#e9edef');
    const sis = dados.sistema || {};
    const cob = dados.cobertura || {};
    const porSis = dados.porSistema || [];
    const nome = (s) => SISTEMA_ROTULO[s] || titulo(s);
    const produto = rotuloSelecao((dados.selecao || {}).produto);
    const ciclo = `${String(dados.ciclo || '').replace('_', 'ª ').toLowerCase()}`;
    const fazLabel = (n) => `${num(n)} ${n === 1 ? 'fazenda' : 'fazendas'}`;

    // 1) Sistema de plantio: rosca + legenda
    const itens = sis.itens || [];
    if (el('safraSistemaSub')) el('safraSistemaSub').textContent = `Participação na área de ${produto} (${ciclo}) · ${fazLabel(sis.fazendas)}`;
    if (el('safraDonutTotal')) el('safraDonutTotal').textContent = num(sis.areaTotal);
    criarGrafico('chartSafraSistema', {
      type: 'doughnut',
      plugins: [rotulosFatias(itens.map((i) => i.pct))],
      data: { labels: itens.map((i) => nome(i.sistema)), datasets: [{ data: itens.map((i) => i.area), backgroundColor: itens.map((i) => SISTEMA_COR[i.sistema]), borderColor: cssVar('--surface', '#ffffff'), borderWidth: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '56%',
        plugins: { legend: { display: false }, tooltip: { ...tooltipBase(), callbacks: { label: (c) => ` ${num(c.parsed, 1)} ha (${itens[c.dataIndex].pct}%) · ${fazLabel(itens[c.dataIndex].fazendas)}` } } }
      }
    });
    if (el('safraSistemaLegenda')) {
      el('safraSistemaLegenda').innerHTML = itens.map((i) => `<li><i style="background:${SISTEMA_COR[i.sistema]}"></i><span><b>${escapeHtml(nome(i.sistema))}</b><small>${num(i.area, 1)} ha · ${fazLabel(i.fazendas)}</small></span><strong>${i.pct}%</strong></li>`).join('');
    }
    if (el('safraConservacionista')) {
      el('safraConservacionista').innerHTML = `<span class="material-symbols-rounded" aria-hidden="true">eco</span><strong>${pct(sis.conservacionistaPct)}</strong><span>da área em sistemas conservacionistas<small>Plantio direto + Cultivo mínimo</small></span>`;
    }

    // 2) Cobertura do solo: percentuais grandes + barra 100 %
    if (el('safraCoberturaSub')) el('safraCoberturaSub').textContent = `Área com plantas de cobertura após a colheita · ${fazLabel(cob.fazendas)}`;
    if (el('safraCobPctCom')) el('safraCobPctCom').textContent = pct(cob.pctCom);
    if (el('safraCobPctSem')) el('safraCobPctSem').textContent = pct(cob.pctSem);
    if (el('safraCobHaCom')) el('safraCobHaCom').textContent = `${num(cob.areaCom, 1)} ha`;
    if (el('safraCobHaSem')) el('safraCobHaSem').textContent = `${num(cob.areaSem, 1)} ha`;
    const barraCom = el('safraCobBarraCom');
    const barraSem = el('safraCobBarraSem');
    // Rótulo "35% (139 fazendas)"; sem espaço na fatia, só o percentual. Sem cobertura = fazendas sem nenhuma área coberta.
    const fazSem = Math.max(0, (cob.fazendas || 0) - (cob.fazendasCom || 0));
    const rotuloFatia = (p, f) => (!p ? '' : p >= 22 ? `${p}% (${fazLabel(f)})` : `${p}%`);
    if (barraCom) { barraCom.style.width = `${cob.pctCom || 0}%`; barraCom.textContent = rotuloFatia(cob.pctCom, cob.fazendasCom); }
    if (barraSem) { barraSem.style.width = `${cob.pctSem || 0}%`; barraSem.textContent = rotuloFatia(cob.pctSem, fazSem); }
    renderHistoricoCobertura(cob.historico || [], fazLabel);

    // 3) Produtividade por sistema (t/ha)
    if (el('safraProdSub')) el('safraProdSub').textContent = `${produto} · toneladas colhidas por hectare · ${fazLabel(porSis.reduce((a, r) => a + (r.fazendas || 0), 0))}`;
    const rotuloX = (r) => [nome(r.sistema), `${fazLabel(r.fazendas)} · ${num(r.areaColhida)} ha`];
    criarGrafico('chartSafraProdutividade', {
      type: 'bar',
      plugins: [rotulos((v) => (v === null ? '' : num(v, 1)), { dentro: true }), eixoXDuasLinhas(11)],
      data: { labels: porSis.map(rotuloX), datasets: [{ data: porSis.map((r) => r.tHa), backgroundColor: porSis.map((r) => SISTEMA_COR[r.sistema]), borderRadius: 5, maxBarThickness: 96 }] },
      options: {
        responsive: true, maintainAspectRatio: false, layout: { padding: { top: 22 } },
        plugins: { legend: { display: false }, tooltip: { ...tooltipBase(), callbacks: { label: (c) => { const r = porSis[c.dataIndex]; return ` ${num(r.tHa, 1)} t/ha · ${num(r.toneladas)} t em ${num(r.areaColhida, 1)} ha`; } } } },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { color: 'transparent', font: { size: LR_FS.lg } } },
          y: { beginAtZero: true, grid: { color: grade }, border: { display: false }, title: { display: true, text: 't/ha', color: eixo, font: { size: LR_FS.lbl } }, ticks: { color: eixo, font: { size: LR_FS.lbl } } }
        }
      }
    });

    // 4) Custos: R$/ha e R$/t lado a lado (ou composição por etapa)
    aplicarVistaCusto();
  }

  /** Cobertura do solo com mais de uma safra: colunas 100 % empilhadas (com × sem cobertura), uma por safra.
   * Com uma safra só, o painel mantém os percentuais grandes e a barra. */
  function renderHistoricoCobertura(historico, fazLabel) {
    const multiplas = historico.length > 1;
    if (el('safraCobResumo')) el('safraCobResumo').hidden = multiplas;
    if (el('safraCobBarra')) el('safraCobBarra').hidden = multiplas;
    if (el('safraCobHist')) el('safraCobHist').hidden = !multiplas;
    if (!multiplas) { destruirGrafico('chartSafraCobHist'); return; }
    if (el('safraCoberturaSub')) el('safraCoberturaSub').textContent = `Área com plantas de cobertura após a colheita · histórico de ${historico.length} safras`;
    const eixo = cssVar('--muted', '#65707c');
    // Rótulo branco centralizado no segmento (só quando cabe)
    const rotulosSegmento = {
      id: 'lrSegmentos',
      afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = '800 12px "Open Sans", Arial, sans-serif';
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        chart.data.datasets.forEach((ds, di) => {
          const meta = chart.getDatasetMeta(di);
          if (meta.hidden) return;
          meta.data.forEach((barra, i) => {
            const v = ds.data[i];
            if (!v || Math.abs(barra.base - barra.y) < 18) return;
            ctx.fillText(`${v}%`, barra.x, (barra.y + barra.base) / 2);
          });
        });
        ctx.restore();
      }
    };
    criarGrafico('chartSafraCobHist', {
      type: 'bar',
      plugins: [rotulosSegmento, eixoXDuasLinhas(11)],
      data: {
        labels: historico.map((h) => [h.safra, `${fazLabel(h.fazendas)} · ${num(h.areaTotal)} ha`]),
        datasets: [
          { label: 'Com cobertura', data: historico.map((h) => h.pctCom), backgroundColor: '#0f7a55', borderRadius: { bottomLeft: 4, bottomRight: 4 }, borderSkipped: false, maxBarThickness: 84, stack: 'cob' },
          { label: 'Sem cobertura', data: historico.map((h) => h.pctSem), backgroundColor: '#4f93d6', borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: false, maxBarThickness: 84, stack: 'cob' }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tooltipBase(),
            callbacks: {
              title: (itens) => `Safra ${historico[itens[0].dataIndex].safra}`,
              label: (c) => {
                const h = historico[c.dataIndex];
                return c.datasetIndex === 0
                  ? ` Com cobertura: ${pct(h.pctCom)} · ${num(h.areaCom, 1)} ha · ${fazLabel(h.fazendasCom)}`
                  : ` Sem cobertura: ${pct(h.pctSem)} · ${num(h.areaSem, 1)} ha`;
              }
            }
          }
        },
        scales: {
          x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: 'transparent', font: { size: LR_FS.lg } } },
          y: { stacked: true, display: false, beginAtZero: true, max: 100 }
        }
      }
    });
  }

  function barrasCusto(id, campo, dados) {
    const porSis = dados.porSistema || [];
    const nome = (s) => SISTEMA_ROTULO[s] || titulo(s);
    const eixo = cssVar('--muted', '#65707c');
    criarGrafico(id, {
      type: 'bar',
      plugins: [rotulos((v) => (v === null ? '' : `R$ ${num(v)}`), { dentro: true }), eixoXDuasLinhas(10.5)],
      data: {
        labels: porSis.map((r) => [nome(r.sistema), `${num(r.fazendas)} faz.`]),
        datasets: [{ data: porSis.map((r) => r[campo]), backgroundColor: porSis.map((r) => SISTEMA_COR[r.sistema]), borderRadius: 5, maxBarThickness: 84, barPercentage: 0.92, categoryPercentage: 0.96 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, layout: { padding: { top: 22 } },
        plugins: { legend: { display: false }, tooltip: { ...tooltipBase(), callbacks: { label: (c) => ` R$ ${num(c.parsed.y)}${campo === 'rsHa' ? '/ha' : '/t'}` } } },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { color: 'transparent', font: { size: LR_FS.lbl } } },
          y: { display: false, beginAtZero: true }
        }
      }
    });
  }

  /** Percentuais inteiros que somam 100 (maior resto), como no resto da tela. */
  function pctsSoma100(valores) {
    const total = valores.reduce((x, y) => x + y, 0);
    if (!total) return valores.map(() => 0);
    const brutos = valores.map((v) => (v / total) * 100);
    const base = brutos.map(Math.floor);
    let resto = 100 - base.reduce((x, y) => x + y, 0);
    brutos.map((v, i) => [v - base[i], i]).sort((x, y) => y[0] - x[0]).forEach(([, i]) => { if (resto > 0 && valores[i] > 0) { base[i] += 1; resto -= 1; } });
    return base;
  }

  /** Composição do custo por etapa (R$/ha): uma barra horizontal empilhada por sistema de plantio. */
  function composicaoCusto(dados) {
    const caixa = el('safraCustoCompBox');
    if (!caixa) return;
    const porSis = (dados.porSistema || []).filter((r) => r.rsHa);
    const rod = dados.rodape || {};
    const nome = (s) => SISTEMA_ROTULO[s] || titulo(s);
    const fazLabel = (n) => `${num(n)} ${n === 1 ? 'fazenda' : 'fazendas'}`;
    if (!porSis.length) { caixa.innerHTML = '<div class="traj-empty">Sem custo de produção para os filtros selecionados.</div>'; return; }
    const etapas = ETAPAS_CUSTO.filter((e) => porSis.some((r) => Number((r.etapas && r.etapas[e.chave]) || 0) > 0));
    const maior = Math.max(...porSis.map((r) => r.rsHa));
    const linhas = porSis.map((r) => {
      const vals = etapas.map((e) => Number((r.etapas && r.etapas[e.chave]) || 0));
      const pcts = pctsSoma100(vals);
      const total = vals.reduce((x, y) => x + y, 0);
      const segmentos = etapas.map((e, i) => (vals[i] > 0
        ? `<div class="safra-comp-seg" style="flex:${vals[i]} 1 0;background:${e.cor};color:${e.texto}" title="${escapeHtml(nome(r.sistema))} · ${escapeHtml(e.rotulo)}: R$ ${num(vals[i])}/ha · ${pcts[i]}% do custo">${pcts[i] >= 7 ? `<span class="${e.texto === '#ffffff' ? 'claro' : ''}">${pcts[i]}%</span>` : ''}</div>`
        : '')).join('');
      return `<div class="safra-comp-linha">
        <div class="safra-comp-nome"><i style="background:${SISTEMA_COR[r.sistema]}"></i><span><b>${escapeHtml(nome(r.sistema))}</b><small>${fazLabel(r.fazendas)}</small></span></div>
        <div class="safra-comp-trilho"><div class="safra-comp-barra" style="width:${(total / maior) * 100}%">${segmentos}</div></div>
        <strong class="safra-comp-total">R$ ${num(r.rsHa)}</strong>
      </div>`;
    }).join('');
    const legenda = etapas.map((e) => `<span><i style="background:${e.cor}"></i>${escapeHtml(e.rotulo)}</span>`).join('');
    const nota = rod.fazendasSemPrePlantio > 0
      ? `Pré-plantio não lançado separadamente por ${num(rod.fazendasSemPrePlantio)} de ${num(rod.fazendasComCusto)} fazendas. Valores em R$/ha.`
      : 'Valores em R$/ha.';
    caixa.innerHTML = `<div class="safra-comp-legenda">${legenda}</div><div class="safra-comp-linhas">${linhas}</div><p class="safra-comp-nota">${escapeHtml(nota)}</p>`;
  }

  /** Alterna entre "R$/ha e R$/t" e a composição por etapa; só o gráfico visível é (re)desenhado. */
  function aplicarVistaCusto() {
    const dados = est.safra;
    if (!dados || !dados.porSistema) return;
    const comp = est.safraComposicao;
    if (el('safraCustoDuplo')) el('safraCustoDuplo').hidden = comp;
    if (el('safraCustoCompBox')) el('safraCustoCompBox').hidden = !comp;
    const btn = el('safraBtnComposicao');
    if (btn) { btn.textContent = comp ? 'Ver custo por ha e t' : 'Ver composição do custo'; btn.setAttribute('aria-pressed', String(comp)); }
    const produtoNome = rotuloSelecao((dados.selecao || {}).produto);
    const sufixoBase = dados.custoBase === 'DEFLACIONADO' ? ' · deflacionado (IGP-DI)' : '';
    const nFaz = (dados.rodape && dados.rodape.fazendasComCusto) || 0;
    if (el('safraCustoSub')) el('safraCustoSub').textContent = comp
      ? `${produtoNome} · composição do custo por etapa · ${num(nFaz)} ${nFaz === 1 ? 'fazenda' : 'fazendas'}${sufixoBase}`
      : `${produtoNome} · custo por área × custo por tonelada${sufixoBase}`;
    if (comp) { destruirGrafico('chartSafraCustoHa'); destruirGrafico('chartSafraCustoT'); composicaoCusto(dados); }
    else { barrasCusto('chartSafraCustoHa', 'rsHa', dados); barrasCusto('chartSafraCustoT', 'rsT', dados); }
  }

  /** Gerar PDF da tela 7: imprime só o slide 7 em A4 paisagem (destino "Salvar como PDF"). */
  function textoFiltrosGlobais() {
    const rotulos = [['filterIndustry', 'Agroindústria'], ['filterRegion', 'Estado'], ['filterRegional', 'Região'], ['filterProject', 'Projeto'], ['filterConsultant', 'Consultor'], ['filterProducer', 'Produtor']];
    const partes = rotulos.map(([id, rotulo]) => {
      const sel = el(id);
      const escolhidos = sel ? [...sel.selectedOptions].map((o) => o.textContent.trim()).filter(Boolean) : [];
      return escolhidos.length ? `${rotulo}: ${escolhidos.join(', ')}` : '';
    }).filter(Boolean);
    return partes.length ? partes.join(' · ') : 'Todas as agroindústrias, regiões e consultores';
  }

  /** Gerar PDF: monta o relatório de impressão (safra-relatorio.js) com os dados já exibidos na tela. */
  async function gerarPdfSafra() {
    const btn = el('safraBtnPdf');
    if (!window.LRSafraRelatorio || !est.safra || !est.safra.porSistema || (btn && btn.disabled)) return;
    if (btn) { btn.disabled = true; btn.classList.add('gerando'); }
    try {
      await window.LRSafraRelatorio.gerar({
        dados: est.safra,
        selecao: est.safraSel || {},
        filtros: textoFiltrosGlobais(),
        cores: { sistema: SISTEMA_COR, rotulo: SISTEMA_ROTULO, etapas: ETAPAS_CUSTO }
      });
    } catch (erro) {
      console.error('Falha ao gerar o PDF da safra:', erro);
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('gerando'); }
    }
  }

  // ── Interações e tema ─────────────────────────────────────────────────────────────────────────────
  function ligarControles() {
    document.querySelectorAll('#prodIndicador button').forEach((b) => b.addEventListener('click', () => { est.indicador = b.dataset.ind; renderEvolucao(); }));
    document.querySelectorAll('#terrAgrupamento button').forEach((b) => b.addEventListener('click', () => mudarAgrupamento(b.dataset.agrupamento)));
    rotularAgrupamento();
    document.querySelectorAll('#prodBase button').forEach((b) => b.addEventListener('click', () => { est.periodo = Number(b.dataset.periodo); renderEvolucao(); }));
    el('safraBtnPdf')?.addEventListener('click', gerarPdfSafra);
    // O dropdown dos filtros da tela 7 vive dentro do carrossel (overflow oculto, deslocado por transform): abrir o popup faz
    // o navegador rolar o viewport na horizontal e o slide some. O carrossel só se move por transform, então zera a rolagem.
    const viewport = document.querySelector('.carousel-viewport');
    viewport?.addEventListener('scroll', () => { if (viewport.scrollLeft) viewport.scrollLeft = 0; }, { passive: true });
    // Nem todo navegador dispara 'scroll' a tempo (foco, scrollIntoView e a transição do popup rolam em momentos diferentes):
    // também zera depois de cada interação com a barra de filtros.
    const zerarRolagem = () => [0, 60, 200, 450].forEach((ms) => setTimeout(() => { if (viewport && viewport.scrollLeft) viewport.scrollLeft = 0; }, ms));
    ['click', 'keydown', 'focusin'].forEach((evento) => document.querySelector('.safra-toolbar')?.addEventListener(evento, zerarRolagem, true));
    el('safraBtnComposicao')?.addEventListener('click', () => { est.safraComposicao = !est.safraComposicao; aplicarVistaCusto(); });
    const valores = (id) => [...(el(id)?.selectedOptions || [])].map((o) => o.value);
    [['safraSelSafra', 'safra'], ['safraSelCultura', 'cultura'], ['safraSelProduto', 'produto']].forEach(([id, campo]) => {
      el(id)?.addEventListener('change', () => {
        if (est.sincronizando) return;
        const nova = { safra: valores('safraSelSafra'), cultura: valores('safraSelCultura'), produto: valores('safraSelProduto') };
        if (!nova[campo].length) {
          // Nada marcado não é uma consulta válida: volta a mostrar a seleção que está na tela
          const d = est.safra;
          if (d && d.selecao) { preencherSelect('safraSelSafra', d.safras || [], d.selecao.safra); preencherSelect('safraSelCultura', d.culturas || [], d.selecao.cultura, titulo); preencherSelect('safraSelProduto', d.produtos || [], d.selecao.produto, titulo); }
          return;
        }
        // Trocar a safra/cultura invalida as escolhas que dependem dela: a API devolve o padrão válido
        if (campo === 'safra') { nova.cultura = []; nova.produto = []; }
        if (campo === 'cultura') nova.produto = [];
        est.safraSel = nova;
        if (est.aoMudarSafra) est.aoMudarSafra({ ...nova });
      });
    });
    new MutationObserver(() => {
      if (est.prod) { renderEvolucao(); }
      if (est.safra && est.safra.porSistema) renderGraficosSafra(est.safra);
      if (est.terr && est.terr.regioes) renderBarrasRegiao(est.terr);
      est.contornoLayer?.setStyle(estiloContorno());
      est.estadosLayer?.setStyle(estiloEstados());
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ligarControles);
  else ligarControles();

  window.LRProducao = { renderProducao, renderTerritorio, renderSafra, aoMudarSafra: (fn) => { est.aoMudarSafra = fn; }, selecaoSafra: () => ({ ...est.safraSel }) };
})();
