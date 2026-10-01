// Roteador único (a Vercel Hobby limita 12 funções): despacha para api/_handlers/<rota>.js via ?rota=, definido nos rewrites do vercel.json.
const ROTAS = ['config', 'me', 'health'];

module.exports = async (req, res) => {
  const rota = String((req.query && req.query.rota) || '');
  if (!ROTAS.includes(rota)) return res.status(404).json({ error: 'Rota não encontrada' });
  return require(`./_handlers/${rota}`)(req, res);
};
