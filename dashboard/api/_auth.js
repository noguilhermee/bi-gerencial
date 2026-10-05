/**
 * Autenticação e escopo de acesso do dashboard (nome com "_" para a Vercel não expor como rota).
 *
 * O front manda o access_token do Supabase Auth em "Authorization: Bearer". Aqui o token é validado,
 * o perfil é lido de tab_dim_usuario_acesso e cada endpoint recebe `req.perfil`. O escopo (quais
 * fazendas o usuário enxerga) é montado em _shared.js (obterEscopo) e aplicado ANTES dos filtros da tela,
 * então um filtro manipulado na URL nunca devolve dado de fora do escopo.
 */
const { getSupabaseClient } = require('./_shared');

// Só estes e-mails (separados por vírgula em ADMIN_USUARIOS_EMAIL) criam, editam e excluem usuários.
const EMAILS_GESTORES = String(process.env.ADMIN_USUARIOS_EMAIL || 'admin@laborrural.com')
  .split(',').map((e) => e.trim().toLowerCase()).filter(Boolean);

function podeGerenciarUsuarios(perfil) {
  return perfil.perfil === 'ADMIN' && EMAILS_GESTORES.includes(String(perfil.email_login || '').toLowerCase());
}

const TTL_PERFIL_MS = 5 * 60 * 1000;
const cachePerfil = new Map();

async function carregarPerfil(supabase, token) {
  const { data, error } = await supabase.auth.getUser(token);
  if (error || !data?.user) return { status: 401, erro: 'Sessão inválida ou expirada' };
  const id = data.user.id;

  const emCache = cachePerfil.get(id);
  if (emCache && Date.now() < emCache.expiraEm) return emCache.resultado;

  const { data: linha, error: erroPerfil } = await supabase
    .from('tab_dim_usuario_acesso')
    .select('id_usuario,email,nome,perfil,chave_consultor,agroindustrias,ativo')
    .eq('id_usuario', id)
    .maybeSingle();
  if (erroPerfil) throw erroPerfil;

  let resultado;
  if (!linha || !linha.ativo) resultado = { status: 403, erro: 'Usuário sem permissão de acesso' };
  else resultado = { perfil: { ...linha, email_login: data.user.email } };
  cachePerfil.set(id, { resultado, expiraEm: Date.now() + TTL_PERFIL_MS });
  return resultado;
}

/** Valida o token e devolve { perfil } ou { status, erro }. */
async function autenticar(req) {
  const cabecalho = String(req.headers?.authorization || '');
  const token = cabecalho.startsWith('Bearer ') ? cabecalho.slice(7).trim() : '';
  if (!token) return { status: 401, erro: 'Login necessário' };
  return carregarPerfil(getSupabaseClient(), token);
}

/** Envolve um handler: resposta JSON privada, sem CORS aberto, exigindo login. */
function comAuth(handler, { admin = false } = {}) {
  return async (req, res) => {
    try {
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 'private, no-store');
      // req.perfil só existe aqui se outro handler do servidor (rota master) já validou o token; o cliente não consegue defini-lo.
      const resultado = req.perfil ? { perfil: req.perfil } : await autenticar(req);
      if (!resultado.perfil) return res.status(resultado.status).json({ error: resultado.erro });
      if (admin && !podeGerenciarUsuarios(resultado.perfil)) return res.status(403).json({ error: 'Apenas o administrador principal gerencia usuários' });
      req.perfil = resultado.perfil;
      return await handler(req, res);
    } catch (error) {
      console.error(`Erro em ${req.url}:`, error);
      return res.status(500).json({ error: error.message });
    }
  };
}

/** Descarta o perfil em cache (depois de editar o usuário). Vale só para esta instância; as demais expiram em 5 min. */
function limparCachePerfil(id) {
  if (id) cachePerfil.delete(id); else cachePerfil.clear();
}

module.exports = { autenticar, comAuth, limparCachePerfil, podeGerenciarUsuarios };
