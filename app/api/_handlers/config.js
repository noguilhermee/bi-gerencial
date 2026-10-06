// Configuração pública do login: URL do projeto e chave anon (pública por design; a tabela de perfis tem RLS sem policy).
module.exports = (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'public, max-age=300');
  const url = process.env.SUPABASE_URL;
  const anonKey = process.env.SUPABASE_ANON_KEY;
  if (!url || !anonKey) return res.status(500).json({ error: 'SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configuradas' });
  return res.status(200).json({ url, anonKey });
};
