const { comAuth, podeGerenciarUsuarios } = require('./_auth');

// Perfil do usuário logado (o front usa para mostrar nome/perfil e travar filtros; a segurança está nos endpoints).
module.exports = comAuth(async (req, res) => {
  const { nome, email, perfil, chave_consultor: chaveConsultor, agroindustrias } = req.perfil;
  return res.status(200).json({ nome, email, perfil, chaveConsultor, agroindustrias, gerenciaUsuarios: podeGerenciarUsuarios(req.perfil) });
});
