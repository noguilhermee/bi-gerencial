const { comAuth, podeGerenciarUsuarios } = require('../_auth');
const { getSupabaseClient, obterEscopo } = require('../_shared');

// Perfil do usuário logado (o front usa para mostrar nome/perfil e travar filtros; a segurança está nos endpoints).
// escopoFiltros: agroindústrias e projetos a que o usuário tem acesso, para o front já abrir com os filtros travados
// (só vale para CONSULTOR, cuja carteira define o escopo; o de AGROINDUSTRIA já está em `agroindustrias`).
module.exports = comAuth(async (req, res) => {
  const { nome, email, perfil, chave_consultor: chaveConsultor, agroindustrias } = req.perfil;
  let escopoFiltros = null;
  if (perfil === 'CONSULTOR') {
    try {
      const carteira = (await obterEscopo(req, getSupabaseClient())).carteira();
      const unicos = (campo) => [...new Set(carteira.map((r) => r[campo]).filter(Boolean))];
      escopoFiltros = { agroindustrias: unicos('agroindustria'), projetos: unicos('projeto') };
    } catch (error) {
      console.warn('Escopo dos filtros indisponível em /api/me:', error.message); // o front trava depois, quando os dados chegam
    }
  }
  return res.status(200).json({ nome, email, perfil, chaveConsultor, agroindustrias, escopoFiltros, gerenciaUsuarios: podeGerenciarUsuarios(req.perfil) });
});
