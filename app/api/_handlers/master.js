// Slides gerenciais numa única chamada: overview, visits, turnover e consistency compartilham as mesmas tabelas
// (carteira mensal etc.), então uma só instância lê cada tabela uma vez em vez de quatro. O token é validado uma vez.
const { comAuth } = require('../_auth');
const { executarHandler } = require('../_executar');

const PARTES = ['overview', 'visits', 'turnover', 'consistency'];

module.exports = comAuth(async (req, res) => {
  const resultados = await Promise.all(PARTES.map((nome) => executarHandler(nome, req)));
  return res.status(200).json(Object.fromEntries(PARTES.map((nome, i) => [nome, resultados[i]])));
});
