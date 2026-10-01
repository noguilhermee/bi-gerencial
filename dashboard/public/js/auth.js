/**
 * Sessão do usuário (Supabase Auth). Expõe window.LRAuth:
 *   client()    cliente supabase-js (URL e chave anon vêm de /api/config)
 *   getToken()  access_token atual (renovado automaticamente) ou null
 *   exigirLogin() redireciona para /login se não houver sessão; devolve o perfil de /api/me
 *   logout()
 * A segurança dos dados está na API (escopo por perfil); aqui é só a sessão e a usabilidade.
 */
(() => {
  let clientePromise = null;

  function client() {
    if (!clientePromise) {
      clientePromise = fetch('/api/config').then((r) => {
        if (!r.ok) throw new Error('config indisponível');
        return r.json();
      }).then(({ url, anonKey }) => window.supabase.createClient(url, anonKey, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
      }));
    }
    return clientePromise;
  }

  async function getToken() {
    const { data } = await (await client()).auth.getSession();
    return data.session ? data.session.access_token : null;
  }

  function irParaLogin() {
    if (location.pathname !== '/login' && location.pathname !== '/login.html') location.replace('/login');
  }

  async function exigirLogin() {
    const token = await getToken();
    if (!token) { irParaLogin(); return new Promise(() => {}); }
    const resposta = await fetch('/api/me', { headers: { Authorization: `Bearer ${token}` } });
    if (resposta.status === 401) { irParaLogin(); return new Promise(() => {}); }
    if (resposta.status === 403) {
      await logout('Seu usuário não tem permissão de acesso. Fale com a Labor Rural.');
      return new Promise(() => {});
    }
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    return resposta.json();
  }

  async function logout(aviso) {
    try { await (await client()).auth.signOut(); } catch (_) { /* sessão já inválida */ }
    if (aviso) alert(aviso);
    irParaLogin();
  }

  window.LRAuth = { client, getToken, exigirLogin, logout, irParaLogin };
})();
