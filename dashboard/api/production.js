// Produção e qualidade do leite (slide "Produção e Qualidade").
//
// Lê tab_fato_producao_mensal (1 linha por fazenda × mês, já saneada pelo ETL: produção suspeita marcada/imputada
// e limites de CCS/CPP resolvidos em camada_consumo.py). Aqui só há agregação de indicadores — nenhuma correção de
// dado (regra 12 do AGENTS.md).
//
// Convenções de cálculo (mesmas do dashboard Procampo/Piracanjuba):
//  • Volume total = Σ litros/dia das fazendas medidas no mês. Média por fazenda = total ÷ fazendas medidas.
//  • Produtividade do rebanho = Σ litros ÷ Σ vacas em lactação (só linhas com os dois).
//  • CCS, CPP, gordura e proteína: médias PONDERADAS pelo volume diário.
//  • Variação contra o mês anterior: só nas fazendas medidas nos DOIS meses (mês em andamento não distorce).
//  • Evolução "base comparável": mesmas fazendas medidas em todos os meses da janela.
//  • Trajetória: média dos 3 últimos meses contra a dos 3 anteriores, faixa de estabilidade ±5 %.
const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, monthLabel, parseFilters, rowMatchesFilters, obterEscopo } = require('./shared');
const { comAuth } = require('./_auth');

const BANDA_TRAJETORIA_PCT = 5;
const MESES_TRAJETORIA = 3;
const COBERTURA_MES_PARCIAL = 0.6;     // mês com menos de 60 % das fazendas do anterior = mês em andamento

const mesDe = (linha) => String(linha.mes_referencia).slice(0, 10);
const temNumero = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));
const num = (v) => Number(v);
const soma = (lista, fn) => lista.reduce((acc, x) => acc + fn(x), 0);
const arred = (v, casas = 1) => (v === null || v === undefined || !Number.isFinite(v) ? null : Number(v.toFixed(casas)));
const variacaoPct = (atual, anterior) => (temNumero(atual) && temNumero(anterior) && Number(anterior) !== 0
  ? ((Number(atual) / Number(anterior)) - 1) * 100 : null);

function mesAnterior(mes, passos = 1) {
  const d = new Date(`${mes}T12:00:00`);
  d.setMonth(d.getMonth() - passos);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
}

const comVolume = (linhas) => linhas.filter((r) => temNumero(r.producao_l_dia) && num(r.producao_l_dia) > 0);

/** Indicadores de um conjunto de linhas (fazenda × mês). */
function agregar(linhas) {
  const medidas = comVolume(linhas);
  const volume = soma(medidas, (r) => num(r.producao_l_dia));
  const rebanho = medidas.filter((r) => temNumero(r.vacas_lactacao) && num(r.vacas_lactacao) > 0);
  const ponderada = (campo) => {
    const validas = medidas.filter((r) => temNumero(r[campo]));
    const peso = soma(validas, (r) => num(r.producao_l_dia));
    return peso > 0 ? soma(validas, (r) => num(r[campo]) * num(r.producao_l_dia)) / peso : null;
  };
  return {
    fazendas: medidas.length,
    volume_total: medidas.length ? volume : null,
    volume_por_fazenda: medidas.length ? volume / medidas.length : null,
    vacas_lactacao: soma(rebanho, (r) => num(r.vacas_lactacao)),
    produtividade: rebanho.length ? soma(rebanho, (r) => num(r.producao_l_dia)) / soma(rebanho, (r) => num(r.vacas_lactacao)) : null,
    ccs: ponderada('ccs'),
    cpp: ponderada('cpp'),
    gordura: ponderada('gordura'),
    proteina: ponderada('proteina')
  };
}

const CAMPOS_INDICADOR = ['volume_total', 'volume_por_fazenda', 'produtividade', 'ccs', 'cpp', 'gordura', 'proteina'];

function arredondar(agregado) {
  const casas = { volume_total: 0, volume_por_fazenda: 0, produtividade: 1, ccs: 0, cpp: 0, gordura: 2, proteina: 2 };
  const saida = { fazendas: agregado.fazendas, vacas_lactacao: agregado.vacas_lactacao };
  CAMPOS_INDICADOR.forEach((c) => { saida[c] = arred(agregado[c], casas[c]); });
  return saida;
}

function infoFazenda(linha) {
  return {
    codigo_lr: linha.codigo_lr,
    produtor: linha.nome_produtor,
    propriedade: linha.nome_propriedade,
    agroindustria: linha.agroindustria,
    regiao: linha.regiao,
    consultor: linha.nome_consultor
  };
}

module.exports = comAuth(async (req, res) => {
  try {


    res.setHeader('Content-Type', 'application/json');

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);
    const filters = parseFilters(req.query);

    let todas;
    try {
      todas = await fetchWithCache('FATO_PRODUCAO_ALL', () =>
        fetchAll(() => supabase.from('tab_fato_producao_mensal').select(COLUNAS.producao), undefined, 'id_composto'));
    } catch (error) {
      // Tabela ainda não criada/carregada (migration + camada_consumo.py pendentes): a tela mostra o aviso.
      console.warn('tab_fato_producao_mensal indisponível:', error.message);
      return res.status(200).json({ disponivel: false, motivo: 'tab_fato_producao_mensal indisponível' });
    }

    const linhas = escopo.fatos(todas).filter((r) => rowMatchesFilters({ ...r, status: r.status_produtor }, { ...filters, month: '' }));
    const meses = [...new Set(linhas.map(mesDe))].sort();
    if (!meses.length) return res.status(200).json({ disponivel: true, semDados: true, mesReferencia: filters.month || null });

    const mesRef = /^\d{4}-\d{2}-\d{2}$/.test(filters.month) ? filters.month : meses[meses.length - 1];
    const porMes = new Map();
    linhas.forEach((r) => {
      const m = mesDe(r);
      if (!porMes.has(m)) porMes.set(m, []);
      porMes.get(m).push(r);
    });
    const doMes = (m) => porMes.get(m) || [];

    const mesAnt = mesAnterior(mesRef);
    const atualLinhas = doMes(mesRef);
    const anteriorLinhas = doMes(mesAnt);
    const medidasAtual = comVolume(atualLinhas).length;
    const medidasAnterior = comVolume(anteriorLinhas).length;
    const parcial = medidasAnterior > 0 && medidasAtual < medidasAnterior * COBERTURA_MES_PARCIAL;
    // Séries e trajetória terminam no último mês completo quando o mês de referência ainda está em andamento
    const mesFim = parcial ? mesAnt : mesRef;

    // ── KPIs do mês e variação contra o anterior (só fazendas medidas nos dois meses) ─────────────
    const codigosAnterior = new Set(comVolume(anteriorLinhas).map((r) => r.codigo_lr));
    const pares = comVolume(atualLinhas).filter((r) => codigosAnterior.has(r.codigo_lr));
    const codigosPares = new Set(pares.map((r) => r.codigo_lr));
    const aPar = agregar(pares);
    const bPar = agregar(anteriorLinhas.filter((r) => codigosPares.has(r.codigo_lr)));
    const variacao = {};
    CAMPOS_INDICADOR.forEach((c) => { variacao[c] = arred(variacaoPct(aPar[c], bPar[c]), 1); });

    // ── Evolução ─────────────────────────────────────────────────────────────────────────────────
    const mesesAteFim = meses.filter((m) => m <= mesFim);
    const serie = (mesesLista, filtro) => {
      const pontos = mesesLista.map((m) => agregar(filtro ? doMes(m).filter(filtro) : doMes(m)));
      const bloco = { labels: mesesLista.map(monthLabel), meses: mesesLista, fazendas: pontos.map((p) => p.fazendas) };
      CAMPOS_INDICADOR.forEach((c) => { bloco[c] = pontos.map((p) => arred(p[c], c === 'gordura' || c === 'proteina' ? 2 : c === 'produtividade' ? 1 : 0)); });
      return bloco;
    };
    // série completa: o recorte (1m/6m/12m/tudo) é escolhido no gráfico
    const evolucao = { todas: serie(mesesAteFim) };

    // ── Trajetória dos produtores (média 3 meses recentes × 3 anteriores) ───────────────────────────
    const janelaRec = mesesAteFim.slice(-MESES_TRAJETORIA);
    const janelaAnt = mesesAteFim.slice(-2 * MESES_TRAJETORIA, -MESES_TRAJETORIA);
    let trajetoria = null;
    if (janelaRec.length && janelaAnt.length) {
      const porFazenda = new Map();
      [...janelaAnt, ...janelaRec].forEach((m) => comVolume(doMes(m)).forEach((r) => {
        if (!porFazenda.has(r.codigo_lr)) porFazenda.set(r.codigo_lr, { rec: [], ant: [], base: r });
        const item = porFazenda.get(r.codigo_lr);
        (janelaRec.includes(m) ? item.rec : item.ant).push(num(r.producao_l_dia));
        if (janelaRec.includes(m)) item.base = r;
      }));
      const grupos = { evoluiu: [], estavel: [], regrediu: [] };
      porFazenda.forEach((item) => {
        if (!item.rec.length || !item.ant.length) return;
        const ant = soma(item.ant, (x) => x) / item.ant.length;
        const rec = soma(item.rec, (x) => x) / item.rec.length;
        if (!ant) return;
        const v = ((rec / ant) - 1) * 100;
        const grupo = v > BANDA_TRAJETORIA_PCT ? 'evoluiu' : v < -BANDA_TRAJETORIA_PCT ? 'regrediu' : 'estavel';
        grupos[grupo].push({ ...infoFazenda(item.base), anterior: arred(ant, 0), recente: arred(rec, 0), variacao: arred(v, 1) });
      });
      Object.values(grupos).forEach((lista) => lista.sort((a, b) => b.variacao - a.variacao));
      trajetoria = {
        banda: BANDA_TRAJETORIA_PCT,
        janelaRecente: janelaRec.map(monthLabel),
        janelaAnterior: janelaAnt.map(monthLabel),
        contagem: { evoluiu: grupos.evoluiu.length, estavel: grupos.estavel.length, regrediu: grupos.regrediu.length },
        ...grupos
      };
    }

    // ── Pontos de atenção do mês de referência ─────────────────────────────────────────────────────
    const lista = (campo, flag) => atualLinhas.filter((r) => r[flag] && temNumero(r[campo]))
      .map((r) => ({ ...infoFazenda(r), valor: arred(num(r[campo]), 0) })).sort((a, b) => b.valor - a.valor);
    const atencao = { ccs: lista('ccs', 'ccs_acima_limite'), cpp: lista('cpp', 'cpp_acima_limite') };

    return res.status(200).json({
      disponivel: true,
      mesReferencia: mesRef,
      mesReferenciaLabel: monthLabel(mesRef),
      mesFim,
      mesFimLabel: monthLabel(mesFim),
      parcial,
      medidasMesAnterior: medidasAnterior,
      kpis: arredondar(agregar(atualLinhas)),
      variacao: { ...variacao, base: codigosPares.size, mesAnteriorLabel: monthLabel(mesAnt) },
      qualidadeDados: {
        suspeitas: atualLinhas.filter((r) => r.producao_suspeita).length,
        imputadas: atualLinhas.filter((r) => r.producao_imputada).length,
        qualidadeDescartada: atualLinhas.filter((r) => r.qualidade_descartada).length
      },
      evolucao,
      trajetoria,
      atencao
    });
  } catch (error) {
    console.error('Erro em /api/production:', error);
    return res.status(500).json({ error: 'Falha ao consolidar produção e qualidade do leite', details: error.message });
  }
});
