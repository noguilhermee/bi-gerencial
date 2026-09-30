/**
 * Slides 5 (Produção e Qualidade) e 6 (Carteira e Território).
 *
 * Só desenha o que /api/production e /api/territory devolvem — nenhuma regra de negócio nem correção de dado
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
    indicador: 'volume_total',
    base: 'comparavel',
    charts: {},
    regioes: [],          // nomes em ordem alfabética — cor estável entre gráficos e mapa
    mapa: null,
    contorno: null,
    contornoLayer: null,
    grupos: new Map(),          // pontos (localização) por região
    gruposBolha: new Map(),     // bolhas de produção por região (abaixo dos pontos)
    mostrarBolhas: true,
    ocultas: new Set(),
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
  function rotulos(formatter, { horizontal = false } = {}) {
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
            if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.strokeText(texto, ponto.x + 6, ponto.y); ctx.fillText(texto, ponto.x + 6, ponto.y); }
            else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.strokeText(texto, ponto.x, ponto.y - 8); ctx.fillText(texto, ponto.x, ponto.y - 8); }
          });
        });
        ctx.restore();
      }
    };
  }

  const tooltipBase = () => ({
    backgroundColor: 'rgba(0, 77, 73, 0.94)', titleColor: '#fff', bodyColor: '#fff', padding: 10, cornerRadius: 7,
    titleFont: { size: 11.2, weight: '700' }, bodyFont: { size: 11.2 }
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
    const aviso = el('prodAviso');
    if (aviso) { aviso.textContent = mensagem; aviso.hidden = false; }
  }

  function renderProducao(dados) {
    est.prod = dados || null;
    const aviso = el('prodAviso');
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

    const partes = [];
    if (dados.parcial) {
      partes.push(`${dados.mesReferenciaLabel} em andamento: ${num(k.fazendas)} fazendas medidas (${dados.mesFimLabel}: ${num(dados.medidasMesAnterior)}). `
        + `Evolução e trajetória vão até ${dados.mesFimLabel}; as variações comparam as mesmas ${num(v.base)} fazendas.`);
    }
    const q = dados.qualidadeDados || {};
    if (q.suspeitas || q.qualidadeDescartada) {
      partes.push(`Qualidade dos dados no mês: ${num(q.suspeitas)} volume(s) suspeito(s) (${num(q.imputadas)} trocado(s) pela média do produtor) · ${num(q.qualidadeDescartada)} linha(s) com valor fora da faixa plausível descartado.`);
    }
    if (aviso) { aviso.textContent = partes.join('  ·  '); aviso.hidden = partes.length === 0; }

    renderEvolucao();
    renderTrajetoria();
    renderAtencao(dados);
  }

  function serieAtual() {
    const evo = est.prod && est.prod.evolucao;
    if (!evo) return null;
    if (est.base === 'comparavel' && evo.comparavel) return { ...evo.comparavel, tipo: 'comparavel' };
    return evo.todas ? { ...evo.todas, tipo: 'todas' } : null;
  }

  function renderEvolucao() {
    const evo = est.prod && est.prod.evolucao;
    if (!evo) return;
    // sem base comparável suficiente: cai para "todas" e desabilita o botão
    const btnComp = document.querySelector('#prodBase button[data-base="comparavel"]');
    if (btnComp) {
      btnComp.disabled = !evo.comparavel;
      btnComp.title = evo.comparavel ? 'Mesmas fazendas medidas em todos os meses' : 'Poucas fazendas medidas em todos os meses recentes';
    }
    if (!evo.comparavel && est.base === 'comparavel') est.base = 'todas';
    document.querySelectorAll('#prodBase button').forEach((b) => {
      const ativo = b.dataset.base === est.base;
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
      sub.textContent = serie.tipo === 'comparavel'
        ? `${num(serie.base)} produtores medidos em todos os ${serie.labels.length} meses · ${cfg.unidade}`
        : `todas as fazendas medidas em cada mês · ${cfg.unidade}`;
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
          x: { grid: { display: false }, border: { display: false }, ticks: { color: eixo, font: { size: 11.2, weight: '600' } } },
          y: { beginAtZero: cfg.zero, grid: { color: cssVar('--line-soft', '#e9edef') }, border: { display: false }, ticks: { color: eixo, font: { size: 11.2 } } }
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
    if (sub) sub.textContent = `volume diário · ${rotRec} contra ${rotAnt}`;
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
    if (el('prodCcsSub')) el('prodCcsSub').textContent = `CCS acima de 500 mil céls./mL · ${dados.mesReferenciaLabel}`;
    if (el('prodCppSub')) el('prodCppSub').textContent = `CPP acima de 300 mil UFC/mL · ${dados.mesReferenciaLabel}`;
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

  function renderTerritorio(dados) {
    est.terr = dados || null;
    if (!dados) return limparTerritorio('Não foi possível carregar os dados de território agora.');
    if (dados.disponivel === false) return limparTerritorio('Dados de território ainda não disponíveis: falta criar/carregar tab_fato_producao_mensal e as colunas de coordenada de tab_dim_fazendas_ativas.');
    if (dados.semDados || !dados.kpis) return limparTerritorio('Sem propriedades ativas para os filtros selecionados.');

    const k = dados.kpis;
    est.regioes = [...new Set([...(dados.regioes || []).map((r) => r.regiao), ...(dados.pontos || []).map((p) => p.regiao)])].sort((a, b) => a.localeCompare(b, 'pt-BR'));
    const set = (id, v, sub, txt) => { if (el(id)) el(id).textContent = v; if (el(sub) && txt) el(sub).textContent = txt; };
    set('kpiTerrPropriedades', num(k.propriedades), 'kpiTerrPropriedadesSub', `Carteira de ${dados.mesReferenciaLabel}`);
    set('kpiTerrRegioes', num(k.regioes));
    set('kpiTerrMunicipios', num(k.municipios));
    set('kpiTerrLitros', num(k.litrosTotal), 'kpiTerrLitrosSub', `${num(k.fazendasMedidas)} medidas · ${dados.janelaMedicao}`);
    set('kpiTerrMapa', num(k.comLocalizacao), 'kpiTerrMapaSub', k.semLocalizacao ? `${num(k.semLocalizacao)} sem localização` : 'Todas localizadas');
    set('kpiTerrAprox', num(k.aproximadas));
    const aviso = el('terrAviso');
    if (aviso) aviso.hidden = true;
    if (el('terrLitrosSub')) el('terrLitrosSub').textContent = `Última medição · ${dados.janelaMedicao} · L/dia`;

    renderBarrasRegiao(dados);
    renderMapa(dados);
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
          x: { beginAtZero: true, grid: { color: cssVar('--line-soft', '#e9edef') }, border: { display: false }, ticks: { color: eixo, font: { size: 10.5 } } },
          y: {
            grid: { display: false }, border: { display: false },
            ticks: { color: eixo, font: { size: 11, weight: '600' }, autoSkip: false, callback(v) { const t = this.getLabelForValue(v); return t.length > 24 ? `${t.slice(0, 23)}…` : t; } }
          }
        }
      }
    });
  }

  function renderBarrasRegiao(dados) {
    const regioes = dados.regioes || [];
    barrasHorizontais('chartTerrPropriedades', topComOutras(regioes, 'propriedades'), 'propriedades', 'propriedades');
    barrasHorizontais('chartTerrLitros', topComOutras(regioes, 'litros'), 'litros', 'L/dia');
  }

  // ── Mapa ───────────────────────────────────────────────────────────────────────────────────────────
  function estiloContorno() {
    return { color: cssVar('--green-300', '#8be4d2'), weight: 1.2, fillColor: cssVar('--green-100', '#eaf6f3'), fillOpacity: 1, interactive: false };
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
    fetch('/data/brasil.json').then((r) => r.json()).then((geo) => {
      est.contorno = geo;
      est.contornoLayer = L.geoJSON(geo, { style: estiloContorno }).addTo(est.mapa);
      est.contornoLayer.bringToBack();
    }).catch(() => { /* o mapa funciona sem o contorno */ });
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
    const cor = corRegiao(p.regiao);
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
        <div class="fc-item"><span>Região</span><b>${escapeHtml(p.regiao)}</b></div>
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
    est.contornoLayer?.bringToBack();
    if (est.selecionado) est.selecionado.bringToFront();
  }

  function renderMapa(dados) {
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
        if (!est.gruposBolha.has(p.regiao)) est.gruposBolha.set(p.regiao, L.layerGroup());
        const cor = corRegiao(p.regiao);
        L.circleMarker([p.lat, p.lng], { radius: raioBolha(p.litros, teto), color: cor, weight: 1, opacity: 0.6, fillColor: cor, fillOpacity: 0.2, interactive: false })
          .addTo(est.gruposBolha.get(p.regiao));
      });
    }
    // 2) pontos de localização por cima, clicáveis
    pontos.forEach((p) => {
      if (!est.grupos.has(p.regiao)) est.grupos.set(p.regiao, L.layerGroup());
      const m = L.circleMarker([p.lat, p.lng], { ...estiloBase(p), bubblingMouseEvents: false });
      m._p = p;
      m.bindTooltip(`${escapeHtml(p.produtor)}${p.litros ? ` · ${num(p.litros)} L/dia` : ''}`, { direction: 'top', offset: [0, -4], className: 'map-tip' });
      m.on('click', () => mostrarFicha(p, m));
      m.addTo(est.grupos.get(p.regiao));
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
    est.ajustado = false;
    mapa.invalidateSize();
    enquadrar();
    [200, 600].forEach((ms) => setTimeout(() => { if (!est.ajustado) enquadrar(); else mapa.invalidateSize(); }, ms));
  }

  // ── Interações e tema ─────────────────────────────────────────────────────────────────────────────
  function ligarControles() {
    document.querySelectorAll('#prodIndicador button').forEach((b) => b.addEventListener('click', () => { est.indicador = b.dataset.ind; renderEvolucao(); }));
    document.querySelectorAll('#prodBase button').forEach((b) => b.addEventListener('click', () => { if (b.disabled) return; est.base = b.dataset.base; renderEvolucao(); }));
    new MutationObserver(() => {
      if (est.prod) { renderEvolucao(); }
      if (est.terr && est.terr.regioes) renderBarrasRegiao(est.terr);
      est.contornoLayer?.setStyle(estiloContorno());
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ligarControles);
  else ligarControles();

  window.LRProducao = { renderProducao, renderTerritorio };
})();
