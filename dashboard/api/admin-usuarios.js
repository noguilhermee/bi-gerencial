const { getSupabaseClient, fetchAll, chaveTexto } = require('./_shared');
const { comAuth, limparCachePerfil } = require('./_auth');

const PERFIS = ['ADMIN', 'CONSULTOR', 'AGROINDUSTRIA'];
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function erro(status, mensagem) {
  return Object.assign(new Error(mensagem), { status });
}

/** Valida o escopo e devolve os campos coerentes com o perfil (os demais ficam nulos). */
function escopoDoPerfil(perfil, corpo, opcoes) {
  if (perfil === 'ADMIN') return { chave_consultor: null, agroindustrias: null };
  if (perfil === 'CONSULTOR') {
    const chave = chaveTexto(corpo.chaveConsultor);
    if (!chave || !opcoes.consultores.includes(chave)) throw erro(400, 'Escolha um consultor da lista.');
    return { chave_consultor: chave, agroindustrias: null };
  }
  const agros = [...new Set([].concat(corpo.agroindustrias || []).map(String))].filter((a) => opcoes.agroindustrias.includes(a));
  if (!agros.length) throw erro(400, 'Escolha ao menos uma agroindústria da lista.');
  return { chave_consultor: null, agroindustrias: agros };
}

async function carregarOpcoes(supabase) {
  const [consultores, agros] = await Promise.all([
    fetchAll(() => supabase.from('tab_dim_consultor').select('chave_consultor,excluido'), 1000, 'id'),
    fetchAll(() => supabase.from('tab_dim_agroindustria').select('nome_bi,exibir_no_bi'), 1000, 'id')
  ]);
  const unicos = (lista) => [...new Set(lista.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  return {
    consultores: unicos(consultores.filter((c) => !c.excluido).map((c) => c.chave_consultor)),
    agroindustrias: unicos(agros.filter((a) => a.exibir_no_bi).map((a) => a.nome_bi))
  };
}

async function listarUsuarios(supabase) {
  const [{ data: linhas, error }, { data: lista, error: erroAuth }] = await Promise.all([
    supabase.from('tab_dim_usuario_acesso').select('id_usuario,email,nome,perfil,chave_consultor,agroindustrias,ativo'),
    supabase.auth.admin.listUsers({ page: 1, perPage: 1000 })
  ]);
  if (error) throw error;
  if (erroAuth) throw erroAuth;
  const ultimoAcesso = new Map(lista.users.map((u) => [u.id, u.last_sign_in_at]));
  return linhas
    .map((l) => ({ ...l, ultimo_acesso: ultimoAcesso.get(l.id_usuario) || null }))
    .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'));
}

function validarSenha(senha) {
  if (typeof senha !== 'string' || senha.length < 8) throw erro(400, 'A senha precisa ter pelo menos 8 caracteres.');
}

async function criar(supabase, corpo, opcoes) {
  const email = String(corpo.email || '').trim().toLowerCase();
  const nome = chaveTexto(corpo.nome);
  const perfil = String(corpo.perfil || '').toUpperCase();
  if (!EMAIL.test(email)) throw erro(400, 'E-mail inválido.');
  if (!nome) throw erro(400, 'Informe o nome.');
  if (!PERFIS.includes(perfil)) throw erro(400, 'Perfil inválido.');
  validarSenha(corpo.senha);
  const escopo = escopoDoPerfil(perfil, corpo, opcoes);

  const { data, error } = await supabase.auth.admin.createUser({ email, password: corpo.senha, email_confirm: true });
  if (error) throw erro(400, /already|registered/i.test(error.message) ? 'Já existe um usuário com esse e-mail.' : error.message);

  const { error: erroLinha } = await supabase.from('tab_dim_usuario_acesso')
    .insert({ id_usuario: data.user.id, email, nome, perfil, ativo: true, ...escopo });
  if (erroLinha) {
    await supabase.auth.admin.deleteUser(data.user.id); // não deixa login sem perfil
    throw erroLinha;
  }
  return { id_usuario: data.user.id };
}

async function atualizar(supabase, corpo, opcoes, eu) {
  const id = String(corpo.id || '');
  if (!id) throw erro(400, 'Usuário não informado.');
  const { data: atual, error } = await supabase.from('tab_dim_usuario_acesso').select('*').eq('id_usuario', id).maybeSingle();
  if (error) throw error;
  if (!atual) throw erro(404, 'Usuário não encontrado.');

  const perfil = corpo.perfil ? String(corpo.perfil).toUpperCase() : atual.perfil;
  const ativo = typeof corpo.ativo === 'boolean' ? corpo.ativo : atual.ativo;
  if (!PERFIS.includes(perfil)) throw erro(400, 'Perfil inválido.');
  if (id === eu.id_usuario && (perfil !== 'ADMIN' || !ativo)) throw erro(400, 'Você não pode remover o próprio acesso de administrador.');

  const mudaEscopo = Boolean(corpo.perfil) || 'chaveConsultor' in corpo || 'agroindustrias' in corpo;
  const escopo = mudaEscopo
    ? escopoDoPerfil(perfil, { chaveConsultor: atual.chave_consultor, agroindustrias: atual.agroindustrias, ...corpo }, opcoes)
    : {};
  const campos = { ...escopo, perfil, ativo, data_processamento: new Date().toISOString() };
  if (corpo.nome) campos.nome = chaveTexto(corpo.nome);

  // Login (Supabase Auth): e-mail e/ou senha, numa única chamada
  const loginNovo = {};
  const email = corpo.email ? String(corpo.email).trim().toLowerCase() : '';
  if (email && email !== atual.email) {
    if (!EMAIL.test(email)) throw erro(400, 'E-mail inválido.');
    loginNovo.email = email;
    loginNovo.email_confirm = true; // sem exigir novo e-mail de confirmação
    campos.email = email;
  }
  if (corpo.senha) {
    validarSenha(corpo.senha);
    loginNovo.password = corpo.senha;
  }
  if (Object.keys(loginNovo).length) {
    const { error: erroLogin } = await supabase.auth.admin.updateUserById(id, loginNovo);
    if (erroLogin) throw erro(400, /already|registered|exists/i.test(erroLogin.message) ? 'Já existe um usuário com esse e-mail.' : erroLogin.message);
  }
  const { error: erroUpdate } = await supabase.from('tab_dim_usuario_acesso').update(campos).eq('id_usuario', id);
  if (erroUpdate) throw erroUpdate;
  limparCachePerfil(id);
  return { ok: true };
}

async function excluir(supabase, id, eu) {
  if (!id) throw erro(400, 'Usuário não informado.');
  if (id === eu.id_usuario) throw erro(400, 'Você não pode excluir o próprio usuário.');
  // O projeto Supabase pode ter logins de outros apps: só mexe em quem tem perfil neste dashboard
  const { data: perfil } = await supabase.from('tab_dim_usuario_acesso').select('id_usuario').eq('id_usuario', id).maybeSingle();
  if (!perfil) throw erro(404, 'Usuário não encontrado neste dashboard.');
  const { error } = await supabase.auth.admin.deleteUser(id); // a linha de perfil cai junto (ON DELETE CASCADE)
  if (error) throw erro(400, error.message);
  limparCachePerfil(id);
  return { ok: true };
}

module.exports = comAuth(async (req, res) => {
  const supabase = getSupabaseClient();
  const corpo = req.body && typeof req.body === 'object' ? req.body : {};
  try {
    if (req.method === 'GET') {
      const [usuarios, opcoes] = await Promise.all([listarUsuarios(supabase), carregarOpcoes(supabase)]);
      return res.status(200).json({ usuarios, opcoes });
    }
    const opcoes = await carregarOpcoes(supabase);
    if (req.method === 'POST') return res.status(201).json(await criar(supabase, corpo, opcoes));
    if (req.method === 'PATCH') return res.status(200).json(await atualizar(supabase, corpo, opcoes, req.perfil));
    if (req.method === 'DELETE') return res.status(200).json(await excluir(supabase, String(req.query.id || ''), req.perfil));
    return res.status(405).json({ error: 'Método não permitido' });
  } catch (error) {
    if (error.status) return res.status(error.status).json({ error: error.message });
    throw error;
  }
}, { admin: true });
