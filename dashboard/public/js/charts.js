/**
 * Fábrica de gráficos do dashboard Labor Rural.
 */
class DashboardCharts {
  constructor() {
    this.instances = {};
    Chart.defaults.font.family = 'Open Sans, Open Sans Fallback, Arial, sans-serif';
    Chart.defaults.font.size = 11.2;
    Chart.defaults.animation.duration = 420;
    Chart.defaults.responsive = true;
    Chart.defaults.maintainAspectRatio = false;
  }

  colors() {
    const styles = getComputedStyle(document.documentElement);
    const get = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    return {
      dark: get('--green-950', '#004d49'),
      green: get('--green-700', '#11775d'),
      medium: '#29927d',
      visits: '#014C49',
      light: '#a9d1c5',
      mint: get('--green-400', '#4fc9b0'),
      danger: get('--danger', '#e23e3b'),
      warning: get('--warning', '#e28a10'),
      info: get('--info', '#2385c7'),
      ink: get('--ink', '#162640'),
      muted: get('--muted', '#65707c'),
      grid: get('--line-soft', '#e9edef'),
      surface: get('--surface', '#ffffff')
    };
  }

  destroy(id) {
    if (this.instances[id]) this.instances[id].destroy();
  }

  tooltip() {
    return {
      backgroundColor: 'rgba(0, 77, 73, 0.94)',
      titleColor: '#ffffff',
      bodyColor: '#ffffff',
      padding: 10,
      displayColors: true,
      cornerRadius: 7,
      titleFont: { size: 11.2, weight: '700' },
      bodyFont: { size: 11.2 }
    };
  }

  formatValue(value, suffix = '') {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return `${value ?? '—'}${suffix}`;
    return `${numeric.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}${suffix}`;
  }

  valueLabels(formatter, options = {}) {
    const color = this.colors();
    return {
      id: 'dashboardValueLabels',
      afterDatasetsDraw: (chart) => {
        const context = chart.ctx;
        const isHorizontal = chart.options.indexAxis === 'y';
        const isDoughnut = chart.config.type === 'doughnut';
        const total = isDoughnut
          ? (chart.data.datasets[0]?.data || []).reduce((sum, value) => sum + Number(value || 0), 0)
          : 0;

        context.save();
        context.font = `700 ${options.fontSize || 11.2}px Open Sans, Arial, sans-serif`;
        context.textBaseline = 'middle';

        chart.data.datasets.forEach((dataset, datasetIndex) => {
          const metadata = chart.getDatasetMeta(datasetIndex);
          if (metadata.hidden) return;

          metadata.data.forEach((element, dataIndex) => {
            const rawValue = dataset.data[dataIndex];
            if (rawValue === null || rawValue === undefined || rawValue === '') return;
            const percentage = total > 0 ? (Number(rawValue) / total) * 100 : 0;
            if (isDoughnut && percentage < (options.minimumPercentage || 0)) return;

            const label = formatter(rawValue, { chart, dataset, datasetIndex, dataIndex, percentage });
            if (!label) return;

            let x = element.x;
            let y = element.y;
            let align = 'center';
            const requestedPosition = typeof options.position === 'function'
              ? options.position({ chart, dataset, datasetIndex, dataIndex, element })
              : options.position;
            const requestedColor = typeof options.color === 'function'
              ? options.color({ chart, dataset, datasetIndex, dataIndex, element })
              : options.color;
            let fill = requestedColor || color.ink;

            if (isDoughnut) {
              const angle = (element.startAngle + element.endAngle) / 2;
              const radius = (element.innerRadius + element.outerRadius) / 2;
              x = element.x + Math.cos(angle) * radius;
              y = element.y + Math.sin(angle) * radius;
              fill = requestedColor || '#ffffff';
            } else if (isHorizontal) {
              if (requestedPosition === 'inside') {
                const isPositive = element.x >= element.base;
                const barLength = Math.abs(element.x - element.base);
                if (barLength >= (options.fitLabel ? context.measureText(label).width + 20 : 45)) {
                  x = isPositive ? element.x - 10 : element.x + 10;
                  align = isPositive ? 'right' : 'left';
                  fill = requestedColor || '#ffffff';
                } else {
                  x = isPositive ? element.x + 7 : element.x - 7;
                  align = isPositive ? 'left' : 'right';
                  fill = options.outsideColor || color.ink;
                }
              } else {
                x += 7;
                align = 'left';
              }
            } else if (requestedPosition === 'insideBase' && Number.isFinite(element.base)) {
              y = element.base - 11;
            } else {
              y -= dataset.type === 'line' || chart.config.type === 'line' ? 11 : 8;
            }

            context.textAlign = align;
            context.lineWidth = options.strokeColor ? 4 : 3;
            context.lineJoin = 'round';
            context.strokeStyle = options.strokeColor ? options.strokeColor : requestedPosition === 'inside'
              ? 'rgba(0, 0, 0, 0.45)'
              : (requestedPosition === 'insideBase'
                ? String(dataset.backgroundColor || color.dark)
                : (isDoughnut ? 'rgba(0, 40, 35, 0.55)' : color.surface));
            if (options.pill) {
              const w = context.measureText(label).width + 10;
              const h = (options.fontSize || 11.2) + 8;
              const left = align === 'left' ? x - 5 : (align === 'right' ? x - w + 5 : x - w / 2);
              context.save();
              context.shadowColor = 'rgba(0, 0, 0, 0.18)';
              context.shadowBlur = 4;
              context.fillStyle = '#ffffff';
              context.beginPath();
              context.roundRect(left, y - h / 2, w, h, 5);
              context.fill();
              context.restore();
            }
            if (!options.noStroke) context.strokeText(label, x, y);
            context.fillStyle = fill;
            context.fillText(label, x, y);
          });
        });

        context.restore();
      }
    };
  }

  centerText(text, options = {}) {
    const color = this.colors();
    return {
      id: 'dashboardCenterText',
      afterDatasetsDraw: (chart) => {
        if (chart.config.type !== 'doughnut') return;
        const meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data || !meta.data[0]) return;
        const x = meta.data[0].x;
        const y = meta.data[0].y;
        const ctx = chart.ctx;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = `800 ${options.fontSize || 12}px Open Sans, Arial, sans-serif`;
        ctx.fillStyle = options.color || color.ink;
        ctx.fillText(text, x, y);
        ctx.restore();
      }
    };
  }

  baseScales(extra = {}) {
    const color = this.colors();
    return {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: color.muted, font: { size: 11.2, weight: '600' }, maxRotation: 0 }
      },
      y: {
        beginAtZero: true,
        grid: { color: color.grid },
        border: { display: false },
        ticks: { color: color.muted, font: { size: 11.2 } }
      },
      ...extra
    };
  }

  renderCoverage(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    this.instances[id] = new Chart(ctx, {
      plugins: [this.valueLabels(
        (value, context) => this.formatValue(value, context.datasetIndex === 2 ? '%' : ''),
        {
          position: ({ datasetIndex }) => datasetIndex < 2 ? 'insideBase' : 'above',
          color: ({ datasetIndex }) => datasetIndex === 0 ? '#ffffff' : color.dark
        }
      )],
      data: {
        labels: data.labels || [],
        datasets: [
          { type: 'bar', label: 'Fazendas ativas', data: data.fazendasAtivas || [], backgroundColor: color.dark, borderRadius: 2, barPercentage: 0.72, categoryPercentage: 0.72 },
          { type: 'bar', label: 'Fazendas visitadas', data: data.fazendasVisitadas || [], backgroundColor: color.light, borderRadius: 2, barPercentage: 0.72, categoryPercentage: 0.72 },
          { type: 'line', label: '% Visitadas', data: data.percCobertura || [], yAxisID: 'percentage', borderColor: color.dark, backgroundColor: color.dark, borderWidth: 2, pointRadius: 2.5, pointHoverRadius: 5, tension: 0.28 }
        ]
      },
      options: {
        layout: { padding: { top: 17 } },
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: this.tooltip() },
        scales: this.baseScales({
          percentage: {
            position: 'right', min: 0, max: 100,
            grid: { drawOnChartArea: false }, border: { display: false },
            ticks: { color: color.muted, font: { size: 11.2 }, callback: (value) => `${value}%` }
          }
        })
      }
    });
  }

  renderTurnover(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    const lineColor = '#4472c5'; // Azul clássico #4472c5
    const textColor = '#203864'; // Azul escuro legível

    const entradas = data.entradas || [];
    const saidas = data.saidas || [];
    const porcentagens = data.porcentagens || saidas.map((s, idx) => {
      const e = entradas[idx] || 0;
      const total = Number(e || 0) + Number(s || 0);
      return total > 0 ? Number(((s / total) * 100).toFixed(1)) : 0;
    });

    const turnoverCustomLabels = {
      id: 'turnoverCustomLabels',
      afterDatasetsDraw: (chart) => {
        const context = chart.ctx;
        context.save();

        chart.data.datasets.forEach((dataset, datasetIndex) => {
          const meta = chart.getDatasetMeta(datasetIndex);
          if (meta.hidden) return;

          meta.data.forEach((element, dataIndex) => {
            const rawValue = dataset.data[dataIndex];
            if (rawValue === null || rawValue === undefined) return;

            const x = element.x;

            if (datasetIndex === 0 || datasetIndex === 1) {
              // Barras (Entradas e Saídas): Rótulo na BASE INTERNA com texto branco
              const valNum = Number(rawValue);
              const label = String(valNum);

              context.font = '700 11px Open Sans, Arial, sans-serif';
              context.textAlign = 'center';
              context.textBaseline = 'middle';

              const baseY = element.base;
              const topY = element.y;
              const barHeight = Math.abs(baseY - topY);

              if (valNum > 0 && barHeight >= 14) {
                const y = baseY - 9;
                context.fillStyle = '#ffffff';
                context.fillText(label, x, y);
              } else if (valNum === 0) {
                const y = baseY - 8;
                context.fillStyle = color.muted;
                context.fillText(label, x, y);
              } else {
                const y = topY - 7;
                context.fillStyle = color.ink;
                context.fillText(label, x, y);
              }
            } else if (datasetIndex === 2) {
              // Linha (Inativações/total de pedidos): Texto em tom âmbar escuro com contorno branco e sem fundo
              const valNum = Number(rawValue);
              const label = `${valNum.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`;

              context.font = '700 11px Open Sans, Arial, sans-serif';
              context.textAlign = 'center';
              context.textBaseline = 'middle';

              const y = element.y - 12;

              // Contorno branco para máxima legibilidade sobre barras/linhas
              context.lineWidth = 3.5;
              context.strokeStyle = color.surface || '#ffffff';
              context.strokeText(label, x, y);

              // Texto em âmbar escuro
              context.fillStyle = textColor;
              context.fillText(label, x, y);
            }
          });
        });

        context.restore();
      }
    };

    this.instances[id] = new Chart(ctx, {
      data: {
        labels: data.labels || [],
        datasets: [
          {
            type: 'bar',
            label: 'Entradas',
            data: entradas,
            backgroundColor: color.green,
            borderRadius: 2,
            barPercentage: 0.72,
            categoryPercentage: 0.7,
            order: 2,
            yAxisID: 'y'
          },
          {
            type: 'bar',
            label: 'Saídas',
            data: saidas,
            backgroundColor: color.danger,
            borderRadius: 2,
            barPercentage: 0.72,
            categoryPercentage: 0.7,
            order: 3,
            yAxisID: 'y'
          },
          {
            type: 'line',
            label: 'Inativações/total de pedidos',
            data: porcentagens,
            borderColor: lineColor,
            backgroundColor: lineColor,
            borderWidth: 2.2,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: lineColor,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 1.5,
            tension: 0.28,
            fill: false,
            order: 1,
            yAxisID: 'y1'
          }
        ]
      },
      plugins: [turnoverCustomLabels],
      options: {
        layout: { padding: { top: 25, right: 8 } },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              pointStyle: 'circle',
              font: { size: 10.5, weight: '600' },
              color: color.muted,
              padding: 10
            }
          },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: (item) => {
                if (item.datasetIndex === 2) {
                  return ` Inativações/total de pedidos: ${item.raw}%`;
                }
                return ` ${item.dataset.label}: ${item.raw}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: color.muted, font: { size: 11.2, weight: '600' } }
          },
          y: {
            type: 'linear',
            position: 'left',
            beginAtZero: true,
            grid: { color: color.grid },
            border: { display: false },
            ticks: { color: color.muted, font: { size: 11.2 }, precision: 0 }
          },
          y1: {
            type: 'linear',
            position: 'right',
            beginAtZero: true,
            suggestedMax: 100,
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: color.muted,
              font: { size: 11.2 },
              callback: (val) => `${val}%`
            }
          }
        }
      }
    });
  }

  renderVisits(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    this.instances[id] = new Chart(ctx, {
      type: 'bar',
      plugins: [this.valueLabels((value) => this.formatValue(value))],
      data: { labels: data.labels || [], datasets: [{ label: 'Visitas', data: data.values || [], backgroundColor: color.green, borderRadius: 2, barPercentage: 0.68 }] },
      options: { layout: { padding: { top: 17 } }, plugins: { legend: { display: false }, tooltip: this.tooltip() }, scales: this.baseScales() }
    });
  }

  /**
   * Barras verticais com o total de visitas por mês e, acima de cada barra, o valor e a
   * variação % em relação ao mês anterior (seta verde ▲ / vermelha ▼; laranja ► quando
   * não há mês anterior para comparar). `data.variations` pode vir pronto (calculado
   * sobre a série completa, antes do corte do horizonte); senão é derivado de `values`.
   */
  renderVisitsEvolution(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    const isDark = document.documentElement.dataset.theme === 'dark';
    const positive = isDark ? '#6fdc8c' : '#2e9e44';
    const values = (data.values || []).map((value) => Number(value) || 0);
    const variations = Array.isArray(data.variations) && data.variations.length === values.length
      ? data.variations
      : values.map((value, idx) => (idx > 0 && values[idx - 1] > 0 ? ((value - values[idx - 1]) / values[idx - 1]) * 100 : null));
    const formatVariation = (variation) => `${variation > 0 ? '+' : ''}${Number(variation).toFixed(1).replace('.', ',')}%`;
    const toneOf = (variation) => (!Number.isFinite(variation) || variation === 0 ? color.warning : (variation > 0 ? positive : color.danger));

    const variationLabels = {
      id: 'visitsVariationLabels',
      afterDatasetsDraw: (chart) => {
        const meta = chart.getDatasetMeta(0);
        if (!meta || meta.hidden) return;
        const context = chart.ctx;
        context.save();
        context.textBaseline = 'middle';
        meta.data.forEach((bar, idx) => {
          const variation = variations[idx];
          const hasVariation = Number.isFinite(variation);
          const tone = toneOf(variation);
          const variationY = bar.y - 10;
          const barHeight = Math.abs(bar.base - bar.y);
          const valueInside = barHeight >= 24;
          const valueY = valueInside ? bar.y + 12 : bar.y - 27;

          context.font = '700 11px Open Sans, Arial, sans-serif';
          const arrowWidth = 7;
          let text = hasVariation ? formatVariation(variation) : '';
          if (text && context.measureText(text).width + arrowWidth + 4 > Math.max(bar.width + 16, 0)) text = '';
          const textWidth = text ? context.measureText(text).width : 0;
          const x = bar.x - (arrowWidth + (text ? 4 + textWidth : 0)) / 2;

          context.strokeStyle = tone;
          context.lineWidth = 2;
          context.lineCap = 'round';
          context.lineJoin = 'round';
          context.beginPath();
          if (!hasVariation || variation === 0) {
            context.moveTo(x + 1.5, variationY - 3.5);
            context.lineTo(x + 5.5, variationY);
            context.lineTo(x + 1.5, variationY + 3.5);
          } else if (variation > 0) {
            context.moveTo(x, variationY + 2);
            context.lineTo(x + 3.5, variationY - 2);
            context.lineTo(x + 7, variationY + 2);
          } else {
            context.moveTo(x, variationY - 2);
            context.lineTo(x + 3.5, variationY + 2);
            context.lineTo(x + 7, variationY - 2);
          }
          context.stroke();

          if (text) {
            context.fillStyle = tone;
            context.textAlign = 'left';
            context.fillText(text, x + arrowWidth + 4, variationY);
          }

          // Rótulo do valor: branco e negrito no topo da barra (mesmo estilo da cobertura);
          // em barras baixas demais para caber, sobe para acima da variação.
          context.font = '700 11.2px Open Sans, Arial, sans-serif';
          context.textAlign = 'center';
          const valueText = this.formatValue(values[idx]);
          if (valueInside) {
            context.lineWidth = 3;
            context.lineJoin = 'round';
            context.strokeStyle = color.visits;
            context.strokeText(valueText, bar.x, valueY);
          }
          context.fillStyle = valueInside ? '#ffffff' : color.ink;
          context.fillText(valueText, bar.x, valueY);
        });
        context.restore();
      }
    };

    this.instances[id] = new Chart(ctx, {
      type: 'bar',
      plugins: [variationLabels],
      data: {
        labels: data.labels || [],
        datasets: [{ label: 'Visitas', data: values, variations, backgroundColor: color.visits, borderRadius: 2, barPercentage: 0.78, categoryPercentage: 0.86 }]
      },
      options: {
        layout: { padding: { top: 26 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: (context) => ` Visitas: ${this.formatValue(context.raw)}`,
              afterLabel: (context) => {
                const variation = variations[context.dataIndex];
                return Number.isFinite(variation) ? ` Variação: ${formatVariation(variation)} vs mês anterior` : ' Variação: sem mês anterior';
              }
            }
          }
        },
        scales: this.baseScales({
          y: { display: false, beginAtZero: true }
        })
      }
    });
  }

  renderRanking(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    this.instances[id] = new Chart(ctx, {
      type: 'bar',
      plugins: [this.valueLabels((value) => this.formatValue(value), { fontSize: 11.2 })],
      data: { labels: data.labels || [], datasets: [{ label: 'Visitas', data: data.values || [], backgroundColor: color.visits, borderRadius: 2, barPercentage: 0.58 }] },
      options: {
        indexAxis: 'y',
        layout: { padding: { right: 30 } },
        plugins: { legend: { display: false }, tooltip: this.tooltip() },
        scales: {
          x: { beginAtZero: true, grid: { display: false }, border: { display: false }, ticks: { color: color.muted, font: { size: 11.2 }, precision: 0 } },
          y: { grid: { display: false }, border: { display: false }, ticks: { color: color.muted, font: { size: 11.2 }, autoSkip: false } }
        }
      }
    });
  }

  renderPortfolio(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    this.instances[id] = new Chart(ctx, {
      type: 'line',
      plugins: [this.valueLabels((value) => this.formatValue(value))],
      data: {
        labels: data.labels || [],
        datasets: [{ label: 'Produtores ativos', data: data.values || [], borderColor: color.dark, backgroundColor: 'rgba(79, 201, 176, 0.14)', fill: true, borderWidth: 2, pointRadius: 3, pointBackgroundColor: color.dark, tension: 0.3 }]
      },
      options: { layout: { padding: { top: 17 } }, plugins: { legend: { display: false }, tooltip: this.tooltip() }, scales: this.baseScales() }
    });
  }

  renderConsistencyHistory(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    this.instances[id] = new Chart(ctx, {
      type: 'line',
      plugins: [this.valueLabels((value) => this.formatValue(value, '%'), { fontSize: 11.2 })],
      data: {
        labels: data.labels || [],
        datasets: [
          { label: 'Consistência mensal', data: data.mensal || [], borderColor: color.dark, backgroundColor: color.dark, borderWidth: 2, pointRadius: 3, tension: 0.25 },
          { label: 'Consistência anual', data: data.anual || [], borderColor: color.mint, backgroundColor: color.mint, borderWidth: 2, pointRadius: 3, tension: 0.25 }
        ]
      },
      options: {
        layout: { padding: { top: 22, right: 14, left: 6, bottom: 4 } },
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: this.tooltip() },
        scales: this.baseScales({
          y: {
            min: 0,
            max: 108,
            grid: { color: color.grid },
            border: { display: false },
            ticks: {
              color: color.muted,
              font: { size: 11.2 },
              callback: (value) => value <= 100 ? `${value}%` : ''
            }
          }
        })
      }
    });

    const chart = this.instances[id];
    this.consistencyHidden = this.consistencyHidden || new Set();
    const toggles = ctx.closest('.panel-card')?.querySelectorAll('.legend-toggle[data-series]') || [];
    const sync = () => toggles.forEach(el => el.classList.toggle('is-off', this.consistencyHidden.has(Number(el.dataset.series))));
    this.consistencyHidden.forEach(i => chart.setDatasetVisibility(i, false));
    chart.update('none');
    sync();
    toggles.forEach(el => {
      const toggle = () => {
        const i = Number(el.dataset.series);
        const visible = this.consistencyHidden.has(i);
        if (visible) this.consistencyHidden.delete(i); else this.consistencyHidden.add(i);
        chart.setDatasetVisibility(i, visible);
        chart.update();
        sync();
      };
      el.onclick = toggle;
      el.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } };
    });
  }

  renderQuality(id, data = {}) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    this.destroy(id);
    const color = this.colors();
    const values = data.values || [];
    const baseLabels = ['Registros aptos', 'Registros incompletos', 'Registros divergentes'];
    const centerTitle = id.toLowerCase().includes('annual') ? 'Anual' : 'Mensal';
    this.instances[id] = new Chart(ctx, {
      type: 'doughnut',
      plugins: [
        this.centerText(centerTitle, { fontSize: 13, color: color.ink }),
        this.valueLabels((value, context) => {
          const val = Number(value || 0);
          if (val === 0) return '';
          const pct = this.formatValue(context.percentage, '%');
          return `${this.formatValue(val)} (${pct})`;
        }, { minimumPercentage: 2, fontSize: 9.5 })
      ],
      data: {
        labels: baseLabels,
        datasets: [{
          data: values,
          backgroundColor: [color.dark, color.warning, color.danger],
          borderColor: color.surface,
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '48%',
        radius: '90%',
        layout: { padding: { top: 4, bottom: 4, left: 4, right: 4 } },
        plugins: {
          legend: {
            display: false
          },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: (context) => {
                const val = context.raw || 0;
                const total = (context.dataset.data || []).reduce((a, b) => a + Number(b || 0), 0);
                const pct = total > 0 ? ((val / total) * 100).toFixed(1).replace('.', ',') : '0,0';
                return ` ${context.label}: ${val} (${pct}%)`;
              }
            }
          }
        }
      }
    });
  }

  renderTop5CostsPie(id, data = []) {
    this.destroy(id);
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const color = this.colors();
    const labels = data.map(d => d.item || 'Outro');
    const values = data.map(d => Number(d.valor || 0));
    const palette = [color.dark, color.green, color.medium, color.mint, color.light];

    this.instances[id] = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      plugins: [this.valueLabels(
        (value, { percentage }) => percentage >= 4 ? `${percentage.toFixed(1).replace('.', ',')}%` : '',
        { fontSize: 12 }
      )],
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: palette,
          borderColor: color.surface,
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '55%',
        plugins: {
          legend: { display: true, position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: (context) => {
                const val = context.raw || 0;
                const total = (context.dataset.data || []).reduce((a, b) => a + Number(b || 0), 0);
                const pct = total > 0 ? ((val / total) * 100).toFixed(1).replace('.', ',') : '0,0';
                return ` ${context.label}: R$ ${val.toLocaleString('pt-BR')} por fazenda (${pct}%)`;
              }
            }
          }
        }
      }
    });
  }

  renderVolumeEvolution(id, data = []) {
    this.destroy(id);
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const color = this.colors();
    const labels = data.map(d => d.mes_label || d.mes);
    const volumes = data.map(d => Number(d.volume || 0));
    const variacoes = data.map(d => Number(d.variacao_percentual || 0));

    this.instances[id] = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      plugins: [this.valueLabels(
        (value, { dataset }) => {
          if (dataset.type !== 'line' && !dataset.label?.includes('Variação')) return null;
          const num = Number(value || 0);
          const prefix = num > 0 ? '+' : '';
          return `${prefix}${num.toFixed(1).replace('.', ',')}%`;
        },
        { fontSize: 11, color: '#e6a800', strokeColor: '#ffffff' }
      )],
      data: {
        labels,
        datasets: [
          {
            type: 'line',
            label: 'Variação %',
            data: variacoes,
            borderColor: color.warning,
            backgroundColor: color.warning,
            borderWidth: 3,
            pointRadius: 5,
            pointHoverRadius: 7,
            pointBackgroundColor: color.warning,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 1.5,
            yAxisID: 'y1',
            order: 1
          },
          {
            type: 'bar',
            label: 'Volume (L/mês)',
            data: volumes,
            backgroundColor: color.green,
            borderRadius: 4,
            yAxisID: 'y',
            order: 2
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: { display: true, position: 'top', labels: { boxWidth: 12 } },
          tooltip: {
            ...this.tooltip(),
            mode: 'index',
            intersect: false,
            callbacks: {
              label: (context) => {
                const val = Number(context.raw || 0);
                if (context.dataset.type === 'line' || context.dataset.label.includes('Variação')) {
                  const prefix = val > 0 ? '+' : '';
                  return ` Variação %: ${prefix}${val.toFixed(2).replace('.', ',')}%`;
                }
                return ` Volume: ${val.toLocaleString('pt-BR')} L/mês`;
              }
            }
          }
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            type: 'linear',
            position: 'left',
            grid: { color: color.grid },
            ticks: { callback: v => `${(v / 1000).toFixed(0)}k L` }
          },
          y1: {
            type: 'linear',
            position: 'right',
            grid: { display: false },
            ticks: { callback: v => `${v}%` }
          }
        }
      }
    });
  }

  renderCadastroDonut(id, data = []) {
    this.destroy(id);
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const color = this.colors();
    const labels = data.map(d => d.categoria);
    const values = data.map(d => Number(d.count || 0));

    this.instances[id] = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      plugins: [this.valueLabels(
        (value) => `${value}`,
        { fontSize: 11.5, color: '#ffffff', minimumPercentage: 2 }
      )],
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: [color.light, color.medium, color.dark],
          borderColor: color.surface,
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '55%',
        plugins: {
          legend: { display: true, position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: (context) => {
                const val = context.raw || 0;
                const total = (context.dataset.data || []).reduce((a, b) => a + Number(b || 0), 0);
                const pct = total > 0 ? ((val / total) * 100).toFixed(1).replace('.', ',') : '0,0';
                return ` ${context.label}: ${val} fazendas (${pct}%)`;
              }
            }
          }
        }
      }
    });
  }

  renderMbRankingHorizontal(id, data = []) {
    this.destroy(id);
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const color = this.colors();
    const labels = data.map(d => d.produtor || d.codigo_lr);
    const values = data.map(d => Number(d.margem_bruta_por_litro || 0));
    const bgColors = values.map(v => v >= 0 ? color.green : color.danger);

    this.instances[id] = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      plugins: [this.valueLabels(
        (value) => `R$ ${Number(value || 0).toFixed(2)}`,
        { position: 'inside', color: '#ffffff', fontSize: 11, noStroke: true, fitLabel: true, outsideColor: color.dark }
      )],
      data: {
        labels,
        datasets: [{
          label: 'Margem Bruta (R$/L)',
          data: values,
          backgroundColor: bgColors,
          borderRadius: 4
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { right: 46 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...this.tooltip(),
            callbacks: {
              label: context => ` MB/L: R$ ${Number(context.raw || 0).toFixed(2)} / Litro`
            }
          }
        },
        scales: {
          x: { grid: { color: color.grid }, ticks: { callback: v => `R$ ${v}` } },
          y: { grid: { display: false } }
        }
      }
    });
  }

  applyTheme() {
    Object.entries(this.instances).forEach(([id, chart]) => {
      if (!chart) return;
      const type = chart.config.type;
      const data = JSON.parse(JSON.stringify(chart.data));
      if (id.includes('Coverage')) this.renderCoverage(id, {
        labels: data.labels,
        fazendasAtivas: data.datasets[0]?.data,
        fazendasVisitadas: data.datasets[1]?.data,
        percCobertura: data.datasets[2]?.data
      });
      else if (id.includes('Turnover')) this.renderTurnover(id, {
        labels: data.labels,
        entradas: data.datasets[0]?.data,
        saidas: data.datasets[1]?.data,
        porcentagens: data.datasets[2]?.data
      });
      else if (id === 'chartVisitsEvolution') this.renderVisitsEvolution(id, { labels: data.labels, values: data.datasets[0]?.data, variations: chart.data.datasets[0]?.variations });
      else if (id.includes('Ranking')) this.renderRanking(id, { labels: data.labels, values: data.datasets[0]?.data });
      else if (id.includes('Portfolio')) this.renderPortfolio(id, { labels: data.labels, values: data.datasets[0]?.data });
      else if (id.includes('ConsistencyHistory')) this.renderConsistencyHistory(id, { labels: data.labels, mensal: data.datasets[0]?.data, anual: data.datasets[1]?.data });
      else if (id.includes('DataQuality')) this.renderQuality(id, { labels: data.labels, values: data.datasets[0]?.data });
      else if (type === 'bar') this.renderVisits(id, { labels: data.labels, values: data.datasets[0]?.data });
    });
  }
}

window.DashboardCharts = DashboardCharts;

