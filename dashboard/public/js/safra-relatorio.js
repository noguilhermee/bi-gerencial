/**
 * Relatório em PDF da tela 7 (Safra de Forrageiras).
 *
 * Em vez de imprimir o slide (feito para a TV), monta um documento próprio em A4 paisagem: cabeçalho com a marca e os
 * filtros, indicadores-chave, gráficos desenhados em alta resolução (viram imagem, então ficam nítidos no PDF), tabelas
 * resumo por sistema de plantio e o detalhe completo por fazenda. Só reorganiza o que /api/safra já devolveu: nenhuma
 * regra de negócio nem correção de dado aqui (regra 12 do AGENTS.md); os totais gerais são somas das próprias linhas.
 *
 * Uso: window.LRSafraRelatorio.gerar({ dados, selecao, filtros, cores }) — chamado pelo botão "Gerar PDF" (producao.js).
 */
(function () {
  'use strict';

  const ESCALA = 3; // pixels por ponto do gráfico: imagem nítida mesmo com zoom no PDF
  const FONTE = '"Open Sans", Arial, sans-serif';
  const TINTA = '#16302c';
  const SUAVE = '#5d6f6b';
  const LINHA = '#dfe7e4';
  const VERDE = '#0f7a55';
  const AZUL = '#4f93d6';

  const escapeHtml = (v) => String(v ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  const num = (v, casas = 0) => (v === null || v === undefined || !Number.isFinite(Number(v))
    ? '—' : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas }));
  const pct = (v, casas = 0) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : `${num(v, casas)}%`);
  const reais = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : `R$ ${num(v)}`);
  const div = (a, b) => (b > 0 ? a / b : null);
  const titulo = (v) => { const t = String(v ?? '').toLowerCase(); return t ? t.charAt(0).toUpperCase() + t.slice(1) : '—'; };
  const fazendas = (n) => `${num(n)} ${n === 1 ? 'fazenda' : 'fazendas'}`;

  // ── Gráficos: Chart.js num canvas fora da tela, exportado como PNG ───────────────────────────────────
  async function imagemGrafico(config, largura, altura) {
    if (typeof Chart === 'undefined') return '';
    const caixa = document.createElement('div');
    caixa.style.cssText = `position:fixed;left:-20000px;top:0;width:${largura}px;height:${altura}px;`;
    const canvas = document.createElement('canvas');
    canvas.width = largura;
    canvas.height = altura;
    canvas.style.width = `${largura}px`;
    canvas.style.height = `${altura}px`;
    caixa.appendChild(canvas);
    document.body.appendChild(caixa);
    const grafico = new Chart(canvas, {
      ...config,
      options: {
        ...config.options,
        responsive: false,
        animation: false,
        devicePixelRatio: ESCALA,
        plugins: { legend: { display: false }, tooltip: { enabled: false }, ...(config.options?.plugins || {}) }
      }
    });
    const png = grafico.toBase64Image('image/png', 1);
    grafico.destroy();
    caixa.remove();
    return png;
  }

  /** Valor escrito no topo (barra vertical) ou na ponta (barra horizontal) de cada barra. */
  function rotulosBarras(formato, { horizontal = false, dataset = null } = {}) {
    return {
      id: 'relRotulos',
      afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = `800 12px ${FONTE}`;
        ctx.fillStyle = TINTA;
        chart.data.datasets.forEach((ds, di) => {
          if (dataset !== null && di !== dataset) return;
          chart.getDatasetMeta(di).data.forEach((barra, i) => {
            const texto = formato(ds.data[i], i);
            if (!texto) return;
            if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(texto, barra.x + 6, barra.y); }
            else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(texto, barra.x, barra.y - 5); }
          });
        });
        ctx.restore();
      }
    };
  }

  /** Percentual em branco no centro de cada segmento (barras empilhadas e rosca), só quando cabe. */
  function rotulosSegmentos(formato, minimo = 16) {
    return {
      id: 'relSegmentos',
      afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = `800 11.5px ${FONTE}`;
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        chart.data.datasets.forEach((ds, di) => {
          ctx.fillStyle = ds._texto || '#ffffff'; // segmentos claros (ex.: pré-plantio) pedem texto escuro
          chart.getDatasetMeta(di).data.forEach((el, i) => {
            const texto = formato(ds.data[i], i, di);
            if (!texto) return;
            if (chart.config.type === 'doughnut') {
              const { x, y } = el.tooltipPosition();
              ctx.fillText(texto, x, y);
              return;
            }
            const tamanho = chart.options.indexAxis === 'y' ? Math.abs(el.x - el.base) : Math.abs(el.base - el.y);
            if (tamanho < minimo) return;
            const x = chart.options.indexAxis === 'y' ? (el.x + el.base) / 2 : el.x;
            const y = chart.options.indexAxis === 'y' ? el.y : (el.y + el.base) / 2;
            ctx.fillText(texto, x, y);
          });
        });
        ctx.restore();
      }
    };
  }

  const eixoX = (extra = {}) => ({ grid: { display: false }, border: { color: LINHA }, ticks: { color: TINTA, font: { family: FONTE, size: 11.5, weight: '700' } }, ...extra });
  const eixoY = (extra = {}) => ({ beginAtZero: true, grid: { color: LINHA }, border: { display: false }, ticks: { color: SUAVE, font: { family: FONTE, size: 10.5 } }, ...extra });

  // ── Montagem do documento ─────────────────────────────────────────────────────────────────────────
  async function gerar({ dados, selecao = {}, filtros = '', cores = {} }) {
    if (!dados || !dados.porSistema) return;
    const SIS_COR = cores.sistema || {};
    const SIS_ROT = cores.rotulo || {};
    const ETAPAS = cores.etapas || [];
    const nomeSistema = (s) => SIS_ROT[s] || titulo(s);
    const chaveSistema = Object.fromEntries(Object.entries(SIS_ROT).map(([k, v]) => [v, k]));

    const sis = dados.sistema || {};
    const cob = dados.cobertura || {};
    const porSis = dados.porSistema || [];
    const detalhe = dados.detalhe || [];
    const rod = dados.rodape || {};
    const itens = sis.itens || [];
    const hist = cob.historico || [];
    const deflacionado = dados.custoBase === 'DEFLACIONADO';
    const baseCusto = deflacionado ? 'Custo deflacionado (IGP-DI)' : 'Custo nominal';

    // Totais gerais = somas das linhas por fazenda (mesma regra soma/soma da API)
    const soma = (campo, filtro = () => true) => detalhe.filter(filtro).reduce((a, r) => a + (Number(r[campo]) || 0), 0);
    const haColhida = soma('area_colhida_ha');
    const toneladas = soma('toneladas');
    const custoTotal = soma('custo_total');
    const tHaGeral = div(toneladas, haColhida);
    const rsHaGeral = div(custoTotal, haColhida);
    const rsTGeral = div(custoTotal, toneladas);

    const sel = {
      safra: (selecao.safra || []).join(', ') || '—',
      cultura: (selecao.cultura || []).map(titulo).join(', ') || '—',
      produto: (selecao.produto || []).map(titulo).join(', ') || '—'
    };
    const agora = new Date();
    const geradoEm = `${agora.toLocaleDateString('pt-BR')} às ${agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
    const ciclo = String(dados.ciclo || '').replace('_', 'ª ').toLowerCase();

    // ── Gráficos (em paralelo; cada um vira imagem) ──────────────────────────────────────────────
    const [imgSistema, imgCobertura, imgProd, imgComp, imgRsT] = await Promise.all([
      imagemGrafico({
        type: 'doughnut',
        plugins: [rotulosSegmentos((v, i) => (itens[i] && itens[i].pct >= 6 ? `${itens[i].pct}%` : '')), {
          id: 'relCentro',
          afterDraw(chart) {
            const { ctx, chartArea: a } = chart;
            const x = (a.left + a.right) / 2; const y = (a.top + a.bottom) / 2;
            ctx.save(); ctx.textAlign = 'center'; ctx.fillStyle = TINTA;
            ctx.font = `800 22px ${FONTE}`; ctx.textBaseline = 'bottom'; ctx.fillText(num(sis.areaTotal), x, y + 4);
            ctx.font = `600 11px ${FONTE}`; ctx.fillStyle = SUAVE; ctx.textBaseline = 'top'; ctx.fillText('hectares', x, y + 6);
            ctx.restore();
          }
        }],
        data: { labels: itens.map((i) => nomeSistema(i.sistema)), datasets: [{ data: itens.map((i) => i.area), backgroundColor: itens.map((i) => SIS_COR[i.sistema]), borderColor: '#ffffff', borderWidth: 2 }] },
        options: { cutout: '58%', layout: { padding: 4 } }
      }, 230, 230),
      hist.length > 1 ? imagemGrafico({
        type: 'bar',
        plugins: [rotulosSegmentos((v) => (v ? `${v}%` : ''), 15)],
        data: {
          labels: hist.map((h) => h.safra),
          datasets: [
            { label: 'Com cobertura', data: hist.map((h) => h.pctCom), backgroundColor: VERDE, stack: 'c', maxBarThickness: 70 },
            { label: 'Sem cobertura', data: hist.map((h) => h.pctSem), backgroundColor: AZUL, stack: 'c', maxBarThickness: 70 }
          ]
        },
        options: { scales: { x: eixoX({ stacked: true }), y: eixoY({ stacked: true, max: 100, ticks: { color: SUAVE, font: { family: FONTE, size: 10.5 }, callback: (v) => `${v}%` } }) } }
      }, 420, 250) : Promise.resolve(''),
      imagemGrafico({
        type: 'bar',
        plugins: [rotulosBarras((v) => (v === null ? '' : num(v, 1)))],
        data: { labels: porSis.map((r) => nomeSistema(r.sistema)), datasets: [{ data: porSis.map((r) => r.tHa), backgroundColor: porSis.map((r) => SIS_COR[r.sistema]), borderRadius: 4, maxBarThickness: 70 }] },
        options: { layout: { padding: { top: 20 } }, scales: { x: eixoX(), y: eixoY({ title: { display: true, text: 't/ha', color: SUAVE, font: { family: FONTE, size: 10.5 } } }) } }
      }, 420, 250),
      (() => {
        const linhas = porSis.filter((r) => r.rsHa);
        const etapas = ETAPAS.filter((e) => linhas.some((r) => Number(r.etapas?.[e.chave]) > 0));
        return imagemGrafico({
          type: 'bar',
          plugins: [
            rotulosSegmentos((v, i) => { const t = Number(linhas[i].rsHa) || 0; const p = t ? Math.round((Number(v) / t) * 100) : 0; return p >= 8 ? `${p}%` : ''; }, 30),
            {
              id: 'relTotais',
              afterDatasetsDraw(chart) {
                const ctx = chart.ctx; const ultimo = chart.getDatasetMeta(chart.data.datasets.length - 1);
                ctx.save(); ctx.font = `800 12px ${FONTE}`; ctx.fillStyle = TINTA; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
                linhas.forEach((r, i) => { const b = ultimo.data[i]; if (b) ctx.fillText(reais(r.rsHa), chart.scales.x.getPixelForValue(r.rsHa) + 6, b.y); });
                ctx.restore();
              }
            }
          ],
          data: {
            labels: linhas.map((r) => nomeSistema(r.sistema)),
            datasets: etapas.map((e) => ({
              label: e.rotulo, data: linhas.map((r) => Number(r.etapas?.[e.chave]) || 0), backgroundColor: e.cor, stack: 'e', barThickness: 34,
              // texto escuro nos segmentos claros
              _texto: e.texto
            }))
          },
          options: {
            indexAxis: 'y',
            layout: { padding: { right: 86 } },
            scales: {
              x: eixoY({ stacked: true, ticks: { color: SUAVE, font: { family: FONTE, size: 10.5 }, callback: (v) => `R$ ${Number(v).toLocaleString('pt-BR')}` } }),
              y: { stacked: true, grid: { display: false }, border: { color: LINHA }, ticks: { color: TINTA, font: { family: FONTE, size: 11.5, weight: '700' } } }
            }
          }
        }, 640, 240);
      })(),
      imagemGrafico({
        type: 'bar',
        plugins: [rotulosBarras((v) => (v === null ? '' : reais(v)))],
        data: { labels: porSis.map((r) => nomeSistema(r.sistema)), datasets: [{ data: porSis.map((r) => r.rsT), backgroundColor: porSis.map((r) => SIS_COR[r.sistema]), borderRadius: 4, maxBarThickness: 64 }] },
        options: { layout: { padding: { top: 20 } }, scales: { x: eixoX(), y: eixoY({ ticks: { color: SUAVE, font: { family: FONTE, size: 10.5 }, callback: (v) => `R$ ${Number(v).toLocaleString('pt-BR')}` } }) } }
      }, 360, 240)
    ]);

    // ── Blocos HTML ──────────────────────────────────────────────────────────────────────────────
    const cabecalho = (pagina) => `
      <header class="rel-cab">
        <img class="rel-logo" src="./img/logo.png" alt="Labor Rural">
        <div class="rel-cab-texto">
          <h1>Safra de Forrageiras</h1>
          <p>${pagina}</p>
        </div>
        <div class="rel-cab-meta"><span>Gerado em ${escapeHtml(geradoEm)}</span><span>${escapeHtml(baseCusto)} · ${escapeHtml(ciclo)}</span></div>
      </header>`;

    const chips = `
      <div class="rel-chips">
        <span><b>Safra</b>${escapeHtml(sel.safra)}</span>
        <span><b>Cultura</b>${escapeHtml(sel.cultura)}</span>
        <span><b>Produto</b>${escapeHtml(sel.produto)}</span>
        <span class="rel-chip-largo"><b>Recorte</b>${escapeHtml(filtros)}</span>
      </div>`;

    const kpi = (rotulo, valor, detalheTxt, destaque = '') => `<div class="rel-kpi ${destaque}"><span>${rotulo}</span><strong>${valor}</strong><small>${detalheTxt}</small></div>`;
    const kpis = `
      <section class="rel-kpis">
        ${kpi('Fazendas', num(sis.fazendas), `${num(rod.fazendasComCusto)} com custo lançado`)}
        ${kpi('Área plantada', `${num(sis.areaTotal)} <em>ha</em>`, `${num(haColhida)} ha colhidos`)}
        ${kpi('Conservacionista', pct(sis.conservacionistaPct), 'Plantio direto + cultivo mínimo', 'verde')}
        ${kpi('Com plantas de cobertura', pct(cob.pctCom), `${num(cob.areaCom)} ha · ${fazendas(cob.fazendasCom)}`, 'verde')}
        ${kpi('Produtividade', `${num(tHaGeral, 1)} <em>t/ha</em>`, `${num(toneladas)} t colhidas`)}
        ${kpi('Custo de produção', `${reais(rsHaGeral)}<em>/ha</em>`, `${reais(rsTGeral)} por tonelada`)}
      </section>`;

    const legendaSistema = `<table class="rel-tab rel-tab-compacta">
        <thead><tr><th>Sistema</th><th class="n">Área (ha)</th><th class="n">%</th><th class="n">Faz.</th></tr></thead>
        <tbody>${itens.map((i) => `<tr><td><i class="rel-dot" style="background:${SIS_COR[i.sistema]}"></i>${escapeHtml(nomeSistema(i.sistema))}</td><td class="n">${num(i.area, 1)}</td><td class="n"><b>${pct(i.pct)}</b></td><td class="n">${num(i.fazendas)}</td></tr>`).join('')}</tbody>
      </table>`;

    const cobSimples = `
      <div class="rel-cob">
        <div class="rel-cob-num"><strong class="verde">${pct(cob.pctCom)}</strong><span>com plantas de cobertura<small>${num(cob.areaCom, 1)} ha · ${fazendas(cob.fazendasCom)}</small></span></div>
        <div class="rel-cob-num"><strong class="azul">${pct(cob.pctSem)}</strong><span>sem plantas de cobertura<small>${num(cob.areaSem, 1)} ha</small></span></div>
        <div class="rel-barra"><i style="width:${cob.pctCom || 0}%;background:${VERDE}"></i><i style="width:${cob.pctSem || 0}%;background:${AZUL}"></i></div>
        <p class="rel-nota">Base: ${num(cob.areaTotal, 1)} ha plantados em ${fazendas(cob.fazendas)}.</p>
      </div>`;
    const cobHistorico = `
      <img class="rel-graf" src="${imgCobertura}" alt="Cobertura do solo por safra">
      <div class="rel-leg"><span><i style="background:${VERDE}"></i>Com cobertura</span><span><i style="background:${AZUL}"></i>Sem cobertura</span></div>`;

    // Resumo por sistema: área (sistema), cobertura (somas do detalhe por sistema) e produtividade/custo (porSistema)
    const linhasResumo = porSis.map((r) => {
      const item = itens.find((i) => i.sistema === r.sistema) || {};
      const doSistema = (x) => chaveSistema[x.sistema] === r.sistema;
      const plantada = soma('area_plantada_ha', doSistema);
      const coberta = soma('area_com_cobertura_ha', doSistema);
      return `<tr>
        <td><i class="rel-dot" style="background:${SIS_COR[r.sistema]}"></i><b>${escapeHtml(nomeSistema(r.sistema))}</b></td>
        <td class="n">${num(item.area, 1)}</td><td class="n">${pct(item.pct)}</td><td class="n">${pct(div(coberta * 100, plantada), 0)}</td>
        <td class="n">${num(r.fazendas)}</td><td class="n">${num(r.areaColhida, 1)}</td><td class="n">${num(r.toneladas)}</td>
        <td class="n"><b>${num(r.tHa, 1)}</b></td><td class="n"><b>${reais(r.rsHa)}</b></td><td class="n"><b>${reais(r.rsT)}</b></td>
      </tr>`;
    }).join('');
    const tabelaResumo = `
      <table class="rel-tab">
        <thead><tr><th>Sistema de plantio</th><th class="n">Área plantada (ha)</th><th class="n">% da área</th><th class="n">% com cobertura</th><th class="n">Fazendas c/ custo</th><th class="n">Área colhida (ha)</th><th class="n">Toneladas</th><th class="n">t/ha</th><th class="n">R$/ha</th><th class="n">R$/t</th></tr></thead>
        <tbody>${linhasResumo}</tbody>
        <tfoot><tr><td>Total</td><td class="n">${num(sis.areaTotal, 1)}</td><td class="n">100%</td><td class="n">${pct(cob.pctCom)}</td><td class="n">${num(rod.fazendasComCusto)}</td><td class="n">${num(haColhida, 1)}</td><td class="n">${num(toneladas)}</td><td class="n">${num(tHaGeral, 1)}</td><td class="n">${reais(rsHaGeral)}</td><td class="n">${reais(rsTGeral)}</td></tr></tfoot>
      </table>`;

    const etapasUsadas = ETAPAS.filter((e) => porSis.some((r) => Number(r.etapas?.[e.chave]) > 0));
    const tabelaEtapas = `
      <table class="rel-tab">
        <thead><tr><th>Sistema de plantio</th>${etapasUsadas.map((e) => `<th class="n"><i class="rel-dot" style="background:${e.cor}"></i>${escapeHtml(e.rotulo)}</th>`).join('')}<th class="n">Total R$/ha</th><th class="n">R$/t</th></tr></thead>
        <tbody>${porSis.filter((r) => r.rsHa).map((r) => {
          const total = etapasUsadas.reduce((a, e) => a + (Number(r.etapas?.[e.chave]) || 0), 0);
          return `<tr><td><i class="rel-dot" style="background:${SIS_COR[r.sistema]}"></i><b>${escapeHtml(nomeSistema(r.sistema))}</b></td>${etapasUsadas.map((e) => {
            const v = Number(r.etapas?.[e.chave]) || 0;
            return `<td class="n">${reais(v)}<small>${total ? `${Math.round((v / total) * 100)}%` : ''}</small></td>`;
          }).join('')}<td class="n"><b>${reais(r.rsHa)}</b></td><td class="n"><b>${reais(r.rsT)}</b></td></tr>`;
        }).join('')}</tbody>
      </table>`;
    const legendaEtapas = `<div class="rel-leg">${etapasUsadas.map((e) => `<span><i style="background:${e.cor}"></i>${escapeHtml(e.rotulo)}</span>`).join('')}</div>`;

    const tabelaHistorico = hist.length > 1 ? `
      <div class="rel-bloco">
        <h3>Cobertura do solo por safra</h3>
        <table class="rel-tab">
          <thead><tr><th>Safra</th><th class="n">Fazendas</th><th class="n">Área plantada (ha)</th><th class="n">Com cobertura (ha)</th><th class="n">Sem cobertura (ha)</th><th class="n">% com cobertura</th><th class="n">Fazendas com cobertura</th></tr></thead>
          <tbody>${hist.map((h) => `<tr><td><b>${escapeHtml(h.safra)}</b></td><td class="n">${num(h.fazendas)}</td><td class="n">${num(h.areaTotal, 1)}</td><td class="n">${num(h.areaCom, 1)}</td><td class="n">${num(h.areaSem, 1)}</td><td class="n"><b>${pct(h.pctCom)}</b></td><td class="n">${num(h.fazendasCom)}</td></tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    const notas = [
      ...(dados.avisos || []),
      'Cálculo soma/soma: produtividade = Σ toneladas ÷ Σ área colhida; R$/ha = Σ custo ÷ Σ área colhida; R$/t = Σ custo ÷ Σ toneladas.',
      'Produtor com áreas em dois sistemas conta nos dois: os números de fazendas por sistema não somam o total.',
      sis.areaNaoInformada > 0 ? `${num(sis.areaNaoInformada, 1)} ha sem sistema de plantio informado (fora dos percentuais).` : '',
      rod.producoesInconsistentes > 0 ? `${num(rod.producoesInconsistentes)} produções descartadas por inconsistência na origem.` : '',
      rod.fazendasSemPrePlantio > 0 ? `${num(rod.fazendasSemPrePlantio)} de ${num(rod.fazendasComCusto)} fazendas sem custo de pré-plantio lançado separadamente.` : '',
      rod.fontes && rod.fontes.length ? `Fonte: ${rod.fontes.map(titulo).join(', ')}.` : ''
    ].filter(Boolean);

    const linhasDetalhe = [...detalhe]
      .filter((r) => r.area_plantada_ha > 0 || r.area_colhida_ha > 0)
      .sort((a, b) => String(a.consultor).localeCompare(String(b.consultor), 'pt-BR') || String(a.nome_fazenda).localeCompare(String(b.nome_fazenda), 'pt-BR'))
      .map((r) => `<tr>
        <td><b>${escapeHtml(r.nome_fazenda)}</b></td><td>${escapeHtml(r.produtor)}</td><td>${escapeHtml(r.consultor)}</td>
        <td><i class="rel-dot" style="background:${SIS_COR[chaveSistema[r.sistema]] || '#9AAEAB'}"></i>${escapeHtml(r.sistema)}</td>
        <td class="n">${num(r.area_plantada_ha, 1)}</td><td class="n">${pct(r.pct_cobertura)}</td><td class="n">${num(r.area_colhida_ha, 1)}</td>
        <td class="n">${num(r.toneladas, 1)}</td><td class="n">${num(r.t_ha, 1)}</td><td class="n">${reais(r.rs_ha)}</td><td class="n">${reais(r.rs_t)}</td>
      </tr>`).join('');

    const html = `
      <section class="rel-pagina">
        ${cabecalho('Resumo executivo · sistema de plantio, cobertura do solo, produtividade e custo de produção')}
        ${chips}
        ${kpis}
        <section class="rel-grade3">
          <article class="rel-card">
            <h3>Sistema de plantio</h3><p class="rel-sub">Participação na área plantada</p>
            <div class="rel-sistema"><img class="rel-donut" src="${imgSistema}" alt="Participação dos sistemas de plantio">${legendaSistema}</div>
          </article>
          <article class="rel-card">
            <h3>Cobertura do solo</h3><p class="rel-sub">${hist.length > 1 ? `Área com plantas de cobertura após a colheita · ${hist.length} safras` : 'Área com plantas de cobertura após a colheita'}</p>
            ${hist.length > 1 ? cobHistorico : cobSimples}
          </article>
          <article class="rel-card">
            <h3>Produtividade</h3><p class="rel-sub">Toneladas colhidas por hectare (t/ha)</p>
            <img class="rel-graf" src="${imgProd}" alt="Produtividade por sistema">
          </article>
        </section>
        <div class="rel-bloco"><h3>Resumo por sistema de plantio</h3>${tabelaResumo}</div>
      </section>

      <section class="rel-pagina">
        ${cabecalho(`Custos de produção · ${escapeHtml(deflacionado ? "valores deflacionados pelo IGP-DI" : "valores nominais")}`)}
        <section class="rel-grade2">
          <article class="rel-card">
            <h3>Composição do custo por hectare</h3><p class="rel-sub">R$/ha por etapa e sistema de plantio (% do custo do sistema)</p>
            <img class="rel-graf" src="${imgComp}" alt="Composição do custo por etapa">
            ${legendaEtapas}
          </article>
          <article class="rel-card">
            <h3>Custo por tonelada</h3><p class="rel-sub">R$ por tonelada produzida</p>
            <img class="rel-graf" src="${imgRsT}" alt="Custo por tonelada">
          </article>
        </section>
        <div class="rel-bloco"><h3>Custo por etapa (R$/ha)</h3>${tabelaEtapas}</div>
        ${tabelaHistorico}
        <div class="rel-notas"><h4>Notas metodológicas</h4><ul>${notas.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul></div>
      </section>

      <section class="rel-pagina rel-pagina--lista">
        ${cabecalho('Detalhe por fazenda e sistema de plantio')}
        <table class="rel-tab rel-tab-lista">
          <thead><tr><th>Fazenda</th><th>Produtor(a)</th><th>Consultor(a)</th><th>Sistema</th><th class="n">Área plantada (ha)</th><th class="n">% cobertura</th><th class="n">Área colhida (ha)</th><th class="n">Toneladas</th><th class="n">t/ha</th><th class="n">R$/ha</th><th class="n">R$/t</th></tr></thead>
          <tbody>${linhasDetalhe || '<tr><td colspan="11" class="vazio">Sem fazendas para os filtros selecionados.</td></tr>'}</tbody>
        </table>
      </section>`;

    // ── Impressão ────────────────────────────────────────────────────────────────────────────────
    document.getElementById('safraRelatorio')?.remove();
    const raiz = document.createElement('div');
    raiz.id = 'safraRelatorio';
    raiz.className = 'rel';
    raiz.innerHTML = html;
    document.body.appendChild(raiz);
    await Promise.all([...raiz.querySelectorAll('img')].map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve())));
    if (document.fonts && document.fonts.ready) await document.fonts.ready;

    const tituloOriginal = document.title;
    document.title = ['Safra_Forrageiras', (selecao.safra || []).join('_').replace(/\//g, '-'), (selecao.cultura || []).map(titulo).join('_'), (selecao.produto || []).map(titulo).join('_')]
      .filter(Boolean).join('_').replace(/[^\w-]+/g, '_');
    const limpar = () => {
      document.body.classList.remove('imprimindo-relatorio');
      document.title = tituloOriginal;
      raiz.remove();
      window.removeEventListener('afterprint', limpar);
    };
    window.addEventListener('afterprint', limpar);
    document.body.classList.add('imprimindo-relatorio');
    await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 60)));
    window.print();
  }

  window.LRSafraRelatorio = { gerar };
})();
