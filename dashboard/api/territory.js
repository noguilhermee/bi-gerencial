// Carteira e território (slide "Carteira e Território").
//
// Combina três tabelas já resolvidas pelo ETL (regra 12 do AGENTS.md — a API só lê e agrega):
//  • tab_fato_carteira_mensal  — propriedades ativas no mês, com agroindústria/região/consultor;
//  • tab_dim_fazendas_ativas   — coordenada saneada de cada fazenda (latitude/longitude/origem_coordenada, gravadas
//    pelo ETL a partir das visitas do SmartQuestion, da base do Procampo e do Elabore; origem_coordenada = MUNICIPIO
//    indica posição aproximada pela mediana das outras fazendas da cidade);
//  • tab_fato_producao_mensal — litros/dia. Como a medição não é mensal em todas as fazendas, cada fazenda
//    usa a ÚLTIMA medição dos últimos 3 meses (mês de referência incluído).
const { COLUNAS, getSupabaseClient, fetchAll, fetchWithCache, monthLabel, parseFilters, rowMatchesFilters, obterEscopo } = require('./shared');
const { comAuth } = require('./_auth');

const MESES_ULTIMA_MEDICAO = 3;

const mesDe = (linha) => String(linha.mes_referencia).slice(0, 10);
const temNumero = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));

function mesAnterior(mes, passos) {
  const d = new Date(`${mes}T12:00:00`);
  d.setMonth(d.getMonth() - passos);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
}

module.exports = comAuth(async (req, res) => {
  try {


    res.setHeader('Content-Type', 'application/json');

    const supabase = getSupabaseClient();
    const escopo = await obterEscopo(req, supabase);
    const filters = parseFilters(req.query);

    let carteira;
    let producao;
    try {
      [carteira, producao] = await Promise.all([
        fetchWithCache('CARTEIRA_MENSAL_ALL', () =>
          fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto')),
        fetchWithCache('FATO_PRODUCAO_ALL', () =>
          fetchAll(() => supabase.from('tab_fato_producao_mensal').select(COLUNAS.producao), undefined, 'id_composto'))
      ]);
    } catch (error) {
      // Tabela nova ainda não criada/carregada (migration + camada_consumo.py pendentes)
      console.warn('Território indisponível:', error.message);
      return res.status(200).json({ disponivel: false, motivo: 'tab_fato_producao_mensal indisponível' });
    }

    carteira = escopo.carteira(carteira);
    producao = escopo.fatos(producao);

    const meses = [...new Set(carteira.map(mesDe))].sort();
    const mesRef = /^\d{4}-\d{2}-\d{2}$/.test(filters.month) ? filters.month : meses[meses.length - 1];
    if (!mesRef) return res.status(200).json({ disponivel: true, semDados: true });

    let dimMes;
    try {
      dimMes = await fetchWithCache(`DIM_FAZENDAS_LOCALIZACAO_${mesRef}`, () =>
        fetchAll(() => supabase.from('tab_dim_fazendas_ativas').select(COLUNAS.dimLocalizacao).eq('mes_referencia', mesRef), undefined, 'id'));
    } catch (error) {
      // Colunas de coordenada ainda não criadas na dim (db/input/2026_09_30_ddl_coordenadas_dim.sql pendente)
      console.warn('Coordenadas indisponíveis em tab_dim_fazendas_ativas:', error.message);
      return res.status(200).json({ disponivel: false, motivo: 'colunas de coordenada indisponíveis em tab_dim_fazendas_ativas' });
    }

    dimMes = escopo.fatos(dimMes, 'codigo_produtor');

    // Propriedades ativas do mês, respeitando os filtros (1 linha por consultor na carteira → agrupa por fazenda)
    const linhasMes = carteira.filter((r) => mesDe(r) === mesRef && rowMatchesFilters({ ...r, status: r.status_visita }, filters));
    const fazendas = new Map();
    linhasMes.forEach((r) => {
      if (!fazendas.has(r.codigo_lr)) fazendas.set(r.codigo_lr, { ...r, consultores: new Set() });
      if (r.consultor) fazendas.get(r.codigo_lr).consultores.add(r.consultor);
    });

    // mesma limpeza do ETL (regras_negocio.limpar_codigo_lr): maiúsculo e sem espaços
    const loc = new Map(dimMes.map((l) => [String(l.codigo_produtor || '').toUpperCase().replace(/\s+/g, ''), l]));

    // Última medição de litros/dia de cada fazenda nos últimos 3 meses
    const inicioJanela = mesAnterior(mesRef, MESES_ULTIMA_MEDICAO - 1);
    const ultimaMedicao = new Map();
    producao.forEach((p) => {
      const m = mesDe(p);
      if (m < inicioJanela || m > mesRef || !temNumero(p.producao_l_dia) || Number(p.producao_l_dia) <= 0) return;
      const atual = ultimaMedicao.get(p.codigo_lr);
      if (!atual || m > atual.mes) ultimaMedicao.set(p.codigo_lr, { mes: m, litros: Number(p.producao_l_dia) });
    });

    const porRegiao = new Map();
    const pontos = [];
    const municipios = new Set();
    let litrosTotal = 0;
    let medidas = 0;
    let aproximadas = 0;
    let comLocalizacao = 0;

    fazendas.forEach((f, codigo) => {
      const l = loc.get(codigo) || {};
      const regiao = f.regiao || 'NAO INFORMADA';
      const medicao = ultimaMedicao.get(codigo);
      if (!porRegiao.has(regiao)) porRegiao.set(regiao, { regiao, propriedades: 0, litros: 0, medidas: 0 });
      const grupo = porRegiao.get(regiao);
      grupo.propriedades += 1;
      if (medicao) { grupo.litros += medicao.litros; grupo.medidas += 1; litrosTotal += medicao.litros; medidas += 1; }
      if (l.cidade) municipios.add(`${l.cidade}|${l.estado || ''}`);

      if (temNumero(l.latitude) && temNumero(l.longitude)) {
        comLocalizacao += 1;
        const aproximada = l.origem_coordenada === 'MUNICIPIO';
        if (aproximada) aproximadas += 1;
        pontos.push({
          codigo_lr: codigo,
          produtor: f.nome_produtor,
          propriedade: f.nome_propriedade,
          cidade: l.cidade || null,
          estado: l.estado || null,
          regiao,
          agroindustria: f.agroindustria,
          projeto: f.projeto,
          consultores: [...f.consultores],
          lat: Number(l.latitude),
          lng: Number(l.longitude),
          aproximada,
          litros: medicao ? Math.round(medicao.litros) : null
        });
      }
    });

    const regioes = [...porRegiao.values()].sort((a, b) => b.propriedades - a.propriedades)
      .map((g) => ({ ...g, litros: Math.round(g.litros) }));

    return res.status(200).json({
      disponivel: true,
      mesReferencia: mesRef,
      mesReferenciaLabel: monthLabel(mesRef),
      janelaMedicao: `${monthLabel(inicioJanela)} a ${monthLabel(mesRef)}`,
      kpis: {
        propriedades: fazendas.size,
        regioes: regioes.filter((g) => g.regiao !== 'NAO INFORMADA').length,
        municipios: municipios.size,
        comLocalizacao,
        aproximadas,
        semLocalizacao: fazendas.size - comLocalizacao,
        litrosTotal: Math.round(litrosTotal),
        fazendasMedidas: medidas
      },
      regioes,
      pontos
    });
  } catch (error) {
    console.error('Erro em /api/territory:', error);
    return res.status(500).json({ error: 'Falha ao consolidar carteira e território', details: error.message });
  }
});
