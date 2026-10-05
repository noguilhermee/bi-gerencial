// Slides gerenciais numa única chamada: overview, visits, turnover e consistency compartilham as mesmas tabelas
// (carteira mensal etc.), então uma só instância lê cada tabela uma vez em vez de quatro. O token é validado uma vez.
const { comAuth } = require('../_auth');

const PARTES = ['overview', 'visits', 'turnover', 'consistency'];

function executar(nome, req) {
  return new Promise((resolve) => {
    const res = {
      statusCode: 200,
      setHeader() {},
      status(codigo) { this.statusCode = codigo; return this; },
      json(corpo) { resolve(this.statusCode < 400 ? corpo : null); return this; }
    };
    Promise.resolve(require(`./${nome}`)(req, res)).then(() => resolve(null), () => resolve(null));
  });
}

module.exports = comAuth(async (req, res) => {
  const resultados = await Promise.all(PARTES.map((nome) => executar(nome, req)));
  return res.status(200).json(Object.fromEntries(PARTES.map((nome, i) => [nome, resultados[i]])));
});
