/**
 * Módulo compartilhado do backend do Dashboard.
 *
 * Contém exclusivamente utilitários técnicos: cliente Supabase, paginação,
 * cache em memória e formatação de datas/rótulos. Nenhuma regra de negócio
 * (cadeia de leite, whitelist de visitas, sanitização de consultores,
 * mapeamento de agroindústria/região etc.) vive aqui — tudo isso é resolvido
 * uma única vez no ETL (scripts/functions/regras_negocio.py e
 * camada_consumo.py) e gravado nas tabelas tab_fato_*. A API só lê.
 */

// Cache em memória no processo Node.js
const memoryCache = new Map();
const DEFAULT_TTL_MS = 30 * 60 * 1000; // 30 minutos: os fatos só mudam quando o ETL roda
// Passado o TTL o dado velho ainda é servido (e atualizado em segundo plano) até este limite; depois disso é descartado.
const STALE_MS = 6 * 60 * 60 * 1000;

function getCached(key) {
  const item = memoryCache.get(key);
  if (!item) return null;
  if (Date.now() > item.expiresAt + STALE_MS) {
    memoryCache.delete(key);
    return null;
  }
  return item.data;
}

function isFresh(key) {
  const item = memoryCache.get(key);
  return Boolean(item) && Date.now() <= item.expiresAt;
}

function setCached(key, data, ttlMs = DEFAULT_TTL_MS) {
  memoryCache.set(key, {
    data,
    expiresAt: Date.now() + ttlMs
  });
  if (memoryCache.size > 200) {
    const now = Date.now();
    for (const [k, v] of memoryCache.entries()) {
      if (now > v.expiresAt + STALE_MS) memoryCache.delete(k);
    }
  }
}

// Leituras em andamento por chave: com o cache frio, overview/visits/turnover/consistency pedem a
// mesma tabela (ex.: tab_fato_carteira_mensal) ao mesmo tempo. Sem isto cada uma varria a tabela
// inteira em paralelo, o Supabase estourava o timeout e a rota respondia HTTP 500.
const emAndamento = new Map();

async function fetchWithCache(cacheKey, fetcherFn, ttlMs = DEFAULT_TTL_MS) {
  const cached = getCached(cacheKey);
  const iniciarLeitura = () => {
    const leitura = fetcherFn()
      .then(result => {
        setCached(cacheKey, result, ttlMs);
        return result;
      })
      .finally(() => emAndamento.delete(cacheKey));
    emAndamento.set(cacheKey, leitura);
    return leitura;
  };
  if (cached !== null) {
    // Vencido: responde com o dado anterior e atualiza em segundo plano (falha não derruba; tenta de novo na próxima)
    if (!isFresh(cacheKey) && !emAndamento.has(cacheKey)) iniciarLeitura().catch((error) => console.warn(`Atualização de ${cacheKey} falhou:`, error.message));
    return cached;
  }
  if (emAndamento.has(cacheKey)) return emAndamento.get(cacheKey);
  return iniciarLeitura();
}

function getSupabaseClient() {
  const { createClient } = require('@supabase/supabase-js');
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error('Supabase credentials missing in environment variables');
  }
  return createClient(url, key);
}

/**
 * Colunas lidas de cada tabela fato — apenas o que as APIs efetivamente usam.
 * `select('*')` trazia ~40% de bytes inúteis (data_processamento, id, colunas de rebanho
 * etc.). Ao usar uma coluna nova em algum endpoint, adicioná-la aqui.
 */
const COLUNAS = {
  carteira: [
    'id_composto', 'mes_referencia', 'codigo_lr', 'consultor', 'consultores_grupo', 'nome_produtor',
    'nome_propriedade', 'projeto', 'agroindustria', 'regiao', 'regional', 'data_associacao', 'visitado_mes',
    'data_ultima_visita', 'data_visita_mes_anterior', 'dias_sem_visita', 'status_visita',
    'status_visita_classe', 'consistencia_mensal', 'detalhamento_mensal', 'consistencia_anual',
    'detalhamento_anual', 'meses_sequenciais', 'excecao', 'sem_lancamento_elabore', 'em_carencia', 'cadastro_elabore',
    'dados_elabore_pct', 'dados_elabore_status', 'blocos_elabore'
  ].join(','),
  visitas: [
    'id_atendimento', 'codigo_lr', 'nome_consultor', 'profissao_consultor', 'mes_referencia',
    'nome_produtor', 'nome_propriedade', 'projeto', 'agroindustria', 'regiao', 'regional', 'status_produtor',
    'data_visita', 'tipo_visita', 'valor_pago_produtor', 'valor_pago_agroindustria',
    'cadastro_elabore', 'dados_elabore_pct', 'dados_elabore_status', 'blocos_elabore'
  ].join(','),
  movimentacao: [
    'id_composto', 'codigo_lr', 'data_movimentacao', 'data_solicitacao', 'nome_produtor', 'numero_atendimento',
    'consultor', 'tipo', 'motivo', 'projeto', 'agroindustria', 'regiao', 'regional'
  ].join(','),
  consistencia: [
    'id_composto', 'codigo_lr', 'nome_consultor', 'projeto', 'agroindustria', 'regiao', 'regional', 'mes_referencia',
    'na_carteira', 'data_carencia_fim', 'consistencia_mensal', 'consistencia_anual',
    'detalhamento_inconsistencia', 'detalhamento_anual', 'meses_sequenciais', 'excecao', 'sem_lancamento_elabore',
    'cadastro_elabore', 'dados_elabore_pct', 'dados_elabore_status', 'blocos_elabore'
  ].join(','),
  economico: [
    'id_composto', 'codigo_lr', 'nome_produtor', 'nome_consultor', 'projeto', 'agroindustria', 'regiao', 'regional',
    'mes_referencia', 'data_associacao', 'possui_dados_economicos', 'volume_leite_mes',
    'volume_diario_litros', 'vacas_lactacao', 'produtividade_l_vl_dia', 'receita_bruta_atividade',
    'preco_medio_litro', 'coe_total_reais', 'coe_por_litro', 'margem_bruta_total',
    'margem_bruta_por_litro', 'coe_concentrado', 'coe_volumoso', 'coe_mao_de_obra', 'coe_sanidade',
    'coe_outros', 'status_consistencia_mensal'
  ].join(','),
  producao: [
    'id_composto', 'codigo_lr', 'mes_referencia', 'nome_produtor', 'nome_propriedade', 'nome_consultor',
    'projeto', 'agroindustria', 'regiao', 'regional', 'status_produtor', 'producao_l_dia', 'vacas_lactacao',
    'produtividade_l_vaca_dia', 'ccs', 'cpp', 'gordura', 'proteina', 'producao_suspeita', 'producao_imputada',
    'ccs_acima_limite', 'cpp_acima_limite', 'qualidade_descartada'
  ].join(','),
  safra: [
    'id_composto', 'bloco', 'codigo_lr', 'nome_produtor', 'nome_propriedade', 'consultor', 'projeto', 'agroindustria',
    'regiao', 'regional', 'safra', 'cultura', 'ciclo', 'produto', 'sistema', 'fonte', 'area_plantada_ha', 'area_com_cobertura_ha',
    'n_producoes', 'producoes_inconsistentes', 'area_colhida_ha', 'toneladas', 'custo_total', 'custo_pre_plantio',
    'custo_plantio', 'custo_tratos', 'custo_colheita', 'custo_etapa_nao_informada',
    'custo_total_defl', 'custo_pre_plantio_defl', 'custo_plantio_defl', 'custo_tratos_defl', 'custo_colheita_defl',
    'custo_etapa_nao_informada_defl'
  ].join(','),
  // Coordenada de cada fazenda vive em tab_dim_fazendas_ativas (1 linha por fazenda × mês; ver AGENTS.md, regra 10)
  dimLocalizacao: [
    'codigo_produtor', 'latitude', 'longitude', 'origem_coordenada', 'coordenada_corrigida', 'cidade', 'estado'
  ].join(','),
  vinculosEconomico: [
    'id_composto', 'codigo_lr', 'nome_produtor', 'nome_propriedade', 'projeto', 'cidade_produtor',
    'estado_produtor', 'data_associacao'
  ].join(',')
};

/**
 * Pagina uma query via .range() até esgotar os resultados, buscando `concurrency` páginas
 * em paralelo por rodada (antes era uma página de 1000 linhas por vez, em série).
 *
 * IMPORTANTE: sem uma ordenação explícita e estável, o Postgrest/Supabase não garante
 * que duas chamadas .range() sucessivas vejam o mesmo "snapshot" de ordenação em tabelas
 * grandes — o que causa linhas duplicadas e/ou linhas nunca retornadas entre páginas
 * (detectado em 2026-09-23 em tab_fato_economico: .range() sem order() devolvia 15.872
 * linhas onde a contagem real, via SQL direto, era 12.519). Por isso, sempre que a tabela
 * tiver uma coluna de chave natural, ela deve ser passada em `orderColumn`.
 */
async function fetchAll(createQuery, pageSize = 2500, orderColumn = null, concurrency = 4) {
  const rows = [];
  let from = 0;
  while (true) {
    const offsets = Array.from({ length: concurrency }, (_, i) => from + i * pageSize);
    const pages = await Promise.all(offsets.map(async (start) => {
      // Até 3 tentativas: timeouts pontuais do Supabase em páginas profundas não devem derrubar a rota
      for (let tentativa = 1; ; tentativa++) {
        let query = createQuery().range(start, start + pageSize - 1);
        if (orderColumn) query = query.order(orderColumn, { ascending: true });
        const { data, error } = await query;
        if (!error) return data || [];
        if (tentativa >= 3) throw error;
        await new Promise(resolve => setTimeout(resolve, 400 * tentativa));
      }
    }));

    const firstShort = pages.findIndex(page => page.length < pageSize);
    // Página curta seguida de página com dados = o "Max rows" da API do Supabase está
    // abaixo de pageSize; falhar é melhor que devolver o dataset truncado em silêncio.
    if (firstShort !== -1 && pages.slice(firstShort + 1).some(page => page.length > 0)) {
      throw new Error(`fetchAll: Supabase limitou a página a ${pages[firstShort].length} linhas (pageSize=${pageSize}). Reduza o pageSize ou aumente "Max rows" na API do Supabase.`);
    }
    pages.forEach(page => rows.push(...page));
    if (firstShort !== -1) break;
    from += concurrency * pageSize;
  }
  return rows;
}

function monthLabel(value) {
  if (!value) return '-';
  const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  const month = parsed.toLocaleDateString('pt-BR', { month: 'short' }).replace('.', '');
  return `${month.charAt(0).toUpperCase()}${month.slice(1)}/${String(parsed.getFullYear()).slice(-2)}`;
}

function formatDate(value) {
  if (!value) return '-';
  const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString('pt-BR');
}

function shiftMonthMinus1(monthStr) {
  if (!monthStr) return null;
  const d = new Date(`${String(monthStr).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
}

/**
 * Lê um filtro multi-valor da query: valores separados por "|" (ex.: consultant=Ana|Bruno).
 * Aceita também parâmetro repetido (array). Devolve sempre um array sem vazios nem repetições.
 */
function parseList(value) {
  const partes = [].concat(value ?? []).flatMap(v => String(v).split('|')).map(v => v.trim()).filter(Boolean);
  return [...new Set(partes)];
}

/**
 * Extrai e normaliza os filtros comuns compartilhados por todos os endpoints.
 * industry/region/regional/project/consultant/producer são listas (multi-seleção; lista vazia = sem filtro).
 * consultantKeys/producerKeys guardam as versões minúsculas em Set, para o match por linha ser O(1).
 *
 * Mês também é multi-seleção (month=2026-08-01|2026-09-01):
 *  • months   = meses escolhidos (AAAA-MM-DD, ordem crescente); vazio = "Todos".
 *  • month    = o mês quando há exatamente UM escolhido; com vários (ou nenhum) fica '' — as telas de fluxo
 *               (visitas, carteira, movimentação) tratam vários meses como período, igual a "Todos" restrito a eles.
 *  • monthRef = o mês mais recente escolhido: base das telas que são uma foto do mês (consistência, econômico,
 *               produção, território), onde somar meses empilharia estoques (L/dia, fazendas ativas).
 *  • noPeriodo(valor) = a data (mes_referencia) cai num dos meses escolhidos (sempre true sem seleção).
 */
function parseFilters(query) {
  const consultant = parseList(query?.consultant);
  const producer = parseList(query?.producer);
  const months = [...new Set(parseList(query?.month).map(m => m.slice(0, 10)).filter(m => /^\d{4}-\d{2}-\d{2}$/.test(m)))].sort();
  const mesesSet = new Set(months);
  return {
    month: months.length === 1 ? months[0] : '',
    months,
    monthRef: months.length ? months[months.length - 1] : '',
    noPeriodo: valor => !mesesSet.size || mesesSet.has(String(valor || '').slice(0, 10)),
    industry: parseList(query?.industry),
    region: parseList(query?.region),
    regional: parseList(query?.regional),
    project: parseList(query?.project),
    consultant,
    producer,
    consultantKeys: new Set(consultant.map(c => c.toLowerCase())),
    producerKeys: new Set(producer.map(p => p.toLowerCase())),
    status: String(query?.status || '').trim().toUpperCase()
  };
}

/** Aplica os filtros comuns a uma linha já resolvida pelo ETL (campos: agroindustria, regiao, regional, projeto, consultor/nome_consultor, nome_produtor/codigo_lr, status_*). */
function rowMatchesFilters(row, filters) {
  if (filters.industry.length && !filters.industry.includes(row.agroindustria)) return false;
  if (filters.region.length && !filters.region.includes(row.regiao)) return false;
  if (filters.regional.length && !filters.regional.includes(row.regional)) return false;
  if (filters.project.length && !filters.project.includes(String(row.projeto || ''))) return false;
  if (filters.consultantKeys.size) {
    const consultor = String(row.consultor || row.nome_consultor || '').toLowerCase();
    if (!filters.consultantKeys.has(consultor)) return false;
  }
  if (filters.producerKeys.size) {
    const nome = String(row.nome_produtor || row.produtor || '').trim().toLowerCase();
    const codigo = String(row.codigo_lr || '').trim().toLowerCase();
    if (!filters.producerKeys.has(nome) && !filters.producerKeys.has(codigo)) return false;
  }
  if (filters.status) {
    const status = String(row.status || row.status_produtor || 'ATIVO').toUpperCase();
    if (filters.status === 'ATIVO' && status.includes('INATIV')) return false;
    if (filters.status === 'INATIVO' && !status.includes('INATIV')) return false;
  }
  return true;
}


// ── Escopo de acesso por perfil (login) ──────────────────────────────────────
function chaveTexto(valor) {
  return String(valor || '').normalize('NFD').replace(/[̀-ͯ]/g, '').trim().toUpperCase();
}

const SEM_RESTRICAO = { admin: true, carteira: rows => rows, fatos: rows => rows, codigos: null };
const escoposPorCarteira = new WeakMap();

/**
 * Escopo do usuário logado. ADMIN não tem restrição; CONSULTOR vê as fazendas em que seu nome aparece na
 * carteira (consultor ou consultores_grupo, então co-consultoria vale para todos do grupo); AGROINDUSTRIA vê
 * as fazendas das suas agroindústrias. `carteira(rows)` filtra a carteira mensal; `fatos(rows, campoCodigo)`
 * filtra as demais fatos pelas fazendas permitidas (e, para AGROINDUSTRIA, pela coluna agroindustria da linha).
 */
function montarEscopo(perfil, carteiraTodos) {
  if (perfil.perfil === 'ADMIN') return SEM_RESTRICAO;

  let porCarteira;
  if (perfil.perfil === 'CONSULTOR') {
    const chave = chaveTexto(perfil.chave_consultor);
    porCarteira = r => chaveTexto(r.consultor) === chave
      || String(r.consultores_grupo || '').split('/').some(c => chaveTexto(c) === chave);
  } else if (perfil.perfil === 'AGROINDUSTRIA') {
    const agros = new Set((perfil.agroindustrias || []).map(chaveTexto));
    porCarteira = r => agros.has(chaveTexto(r.agroindustria));
  } else {
    return { admin: false, carteira: () => [], fatos: () => [], codigos: new Set() };
  }

  const carteira = carteiraTodos.filter(porCarteira);
  const codigos = new Set(carteira.map(r => String(r.codigo_lr || '').trim().toUpperCase()).filter(Boolean));
  const agros = perfil.perfil === 'AGROINDUSTRIA' ? new Set((perfil.agroindustrias || []).map(chaveTexto)) : null;
  return {
    admin: false,
    codigos,
    carteira: () => carteira,
    fatos: (rows, campoCodigo = 'codigo_lr') => rows.filter(r => {
      if (agros && r.agroindustria) return agros.has(chaveTexto(r.agroindustria));
      return codigos.has(String(r[campoCodigo] || '').trim().toUpperCase());
    })
  };
}

/** Carteira mensal completa (mesma chave de cache de overview/visits/...), sem escopo. */
function carregarCarteira(supabase) {
  return fetchWithCache('CARTEIRA_MENSAL_ALL', () =>
    fetchAll(() => supabase.from('tab_fato_carteira_mensal').select(COLUNAS.carteira).order('mes_referencia', { ascending: false }), undefined, 'id_composto'));
}

/** Escopo do usuário da requisição (req.perfil vem de comAuth), memoizado por carteira em cache. */
async function obterEscopo(req, supabase) {
  const perfil = req.perfil;
  if (perfil.perfil === 'ADMIN') return SEM_RESTRICAO;
  const carteiraTodos = await carregarCarteira(supabase);
  let porUsuario = escoposPorCarteira.get(carteiraTodos);
  if (!porUsuario) escoposPorCarteira.set(carteiraTodos, porUsuario = new Map());
  // A chave inclui o cadastro de acesso: se o administrador trocar as agroindústrias/consultor do usuário, o escopo antigo não é reaproveitado
  const chave = JSON.stringify([perfil.id_usuario, perfil.perfil, perfil.chave_consultor, perfil.agroindustrias]);
  if (!porUsuario.has(chave)) porUsuario.set(chave, montarEscopo(perfil, carteiraTodos));
  return porUsuario.get(chave);
}

module.exports = {
  chaveTexto,
  obterEscopo,
  montarEscopo,
  carregarCarteira,
  COLUNAS,
  fetchWithCache,
  getCached,
  setCached,
  getSupabaseClient,
  fetchAll,
  monthLabel,
  formatDate,
  shiftMonthMinus1,
  parseList,
  parseFilters,
  rowMatchesFilters
};
