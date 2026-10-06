// Aquecimento do cache em memória, chamado pela tela de login (sem token). Não devolve dado nenhum: só lê as
// tabelas compartilhadas (que o cache guarda sem escopo) para que o /api/master pós-login encontre tudo pronto.
// O escopo por perfil continua sendo aplicado nos endpoints autenticados, depois do cache.
const { executarHandler } = require('../_executar');

const ROTAS_AQUECIDAS = ['overview', 'visits', 'turnover', 'consistency', 'economics', 'production', 'territory'];
const INTERVALO_MS = 60 * 1000;
let aquecimento = null;
let iniciadoEm = 0;

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'private, no-store');
  // Uma rodada por minuto por instância; chamadas simultâneas esperam a mesma (limita o custo de quem insistir)
  if (!aquecimento || Date.now() - iniciadoEm > INTERVALO_MS) {
    iniciadoEm = Date.now();
    const fake = { perfil: { perfil: 'ADMIN', id_usuario: 'aquecimento' }, query: {}, headers: {}, url: '/api/aquecer' };
    aquecimento = Promise.all(ROTAS_AQUECIDAS.map((nome) => executarHandler(nome, { ...fake })));
  }
  await aquecimento;
  return res.status(204).end();
};
