/**
 * Tela de administração de usuários. Só ADMIN acessa (a API também recusa os demais com 403).
 * Cria o login no Supabase Auth e o perfil em tab_dim_usuario_acesso numa única chamada a /api/admin-usuarios.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const ROTULOS = { ADMIN: 'Administrador', CONSULTOR: 'Consultor', AGROINDUSTRIA: 'Agroindústria' };
  let usuarios = [];
  let opcoes = { consultores: [], agroindustrias: [] };
  let editando = null; // usuário em edição; null = novo

  function avisar(texto, ehErro) {
    const el = $('aviso');
    el.textContent = texto || '';
    el.classList.toggle('erro', !!ehErro);
  }

  async function chamar(metodo, corpo, query = '') {
    const token = await LRAuth.getToken();
    const resposta = await fetch(`/api/admin-usuarios${query}`, {
      method: metodo,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: corpo ? JSON.stringify(corpo) : undefined
    });
    if (resposta.status === 401) { LRAuth.irParaLogin(); return new Promise(() => {}); }
    const dados = await resposta.json().catch(() => ({}));
    if (!resposta.ok) throw new Error(dados.error || `HTTP ${resposta.status}`);
    return dados;
  }

  const escapar = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function escopoTexto(u) {
    if (u.perfil === 'ADMIN') return 'Tudo';
    if (u.perfil === 'CONSULTOR') return escapar(u.chave_consultor);
    return escapar((u.agroindustrias || []).join(', '));
  }

  function desenhar() {
    const corpo = $('corpo');
    if (!usuarios.length) { corpo.innerHTML = '<tr><td colspan="7" class="vazio">Nenhum usuário cadastrado.</td></tr>'; return; }
    corpo.innerHTML = usuarios.map((u) => `
      <tr>
        <td><strong>${escapar(u.nome)}</strong></td>
        <td>${escapar(u.email)}</td>
        <td><span class="chip">${ROTULOS[u.perfil] || u.perfil}</span></td>
        <td>${escopoTexto(u)}</td>
        <td>${u.ultimo_acesso ? new Date(u.ultimo_acesso).toLocaleString('pt-BR') : 'Nunca'}</td>
        <td><span class="chip ${u.ativo ? '' : 'off'}">${u.ativo ? 'Ativo' : 'Inativo'}</span></td>
        <td class="acoes">
          <button class="btn" data-acao="editar" data-id="${u.id_usuario}">Editar</button>
          <button class="btn perigo" data-acao="excluir" data-id="${u.id_usuario}">Excluir</button>
        </td>
      </tr>`).join('');
  }

  async function carregar() {
    try {
      const dados = await chamar('GET');
      usuarios = dados.usuarios;
      opcoes = dados.opcoes;
      desenhar();
    } catch (e) {
      avisar(`Não foi possível carregar os usuários: ${e.message}`, true);
    }
  }

  function atualizarCamposPerfil() {
    const perfil = $('fPerfil').value;
    $('lblConsultor').hidden = perfil !== 'CONSULTOR';
    $('lblAgros').hidden = perfil !== 'AGROINDUSTRIA';
  }

  // Sem caracteres ambíguos (0/O, 1/l/I) para facilitar repassar a senha ao usuário.
  function gerarSenha(tamanho = 8) {
    const grupos = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnpqrstuvwxyz', '23456789', '@#$%&*!?'];
    const todos = grupos.join('');
    const sorteio = (n) => crypto.getRandomValues(new Uint32Array(1))[0] % n;
    const chars = grupos.map((g) => g[sorteio(g.length)]);
    while (chars.length < tamanho) chars.push(todos[sorteio(todos.length)]);
    for (let i = chars.length - 1; i > 0; i--) {
      const j = sorteio(i + 1);
      [chars[i], chars[j]] = [chars[j], chars[i]];
    }
    return chars.join('');
  }

  function abrirFormulario(usuario) {
    editando = usuario;
    $('erro').textContent = '';
    $('dlgTitulo').textContent = usuario ? 'Editar usuário' : 'Novo usuário';
    $('fNome').value = usuario ? usuario.nome : '';
    $('fEmail').value = usuario ? usuario.email : '';
    $('fSenha').value = '';
    $('lblSenha').firstChild.textContent = usuario ? 'Nova senha (deixe em branco para manter) ' : 'Senha (mínimo 8 caracteres) ';
    $('fPerfil').value = usuario ? usuario.perfil : 'CONSULTOR';
    $('fConsultor').innerHTML = opcoes.consultores.map((c) => `<option value="${escapar(c)}">${escapar(c)}</option>`).join('');
    if (usuario && usuario.chave_consultor) $('fConsultor').value = usuario.chave_consultor;
    const marcadas = new Set(usuario ? usuario.agroindustrias || [] : []);
    $('fAgros').innerHTML = opcoes.agroindustrias.map((a) =>
      `<label><input type="checkbox" value="${escapar(a)}" ${marcadas.has(a) ? 'checked' : ''}> ${escapar(a)}</label>`).join('');
    $('fAtivo').checked = usuario ? usuario.ativo : true;
    $('lblAtivo').hidden = !usuario;
    atualizarCamposPerfil();
    $('dlg').showModal();
  }

  async function salvar(evento) {
    evento.preventDefault();
    const botao = $('btnSalvar');
    const perfil = $('fPerfil').value;
    const corpo = {
      nome: $('fNome').value.trim(),
      perfil,
      chaveConsultor: perfil === 'CONSULTOR' ? $('fConsultor').value : null,
      agroindustrias: perfil === 'AGROINDUSTRIA' ? [...$('fAgros').querySelectorAll('input:checked')].map((i) => i.value) : null
    };
    const senha = $('fSenha').value;
    if (senha) corpo.senha = senha;
    $('erro').textContent = '';
    botao.disabled = true;
    try {
      if (editando) {
        corpo.id = editando.id_usuario;
        corpo.email = $('fEmail').value.trim();
        corpo.ativo = $('fAtivo').checked;
        await chamar('PATCH', corpo);
        avisar('Usuário atualizado.');
      } else {
        corpo.email = $('fEmail').value.trim();
        await chamar('POST', corpo);
        avisar('Usuário criado. Ele já pode entrar com o e-mail e a senha informados.');
      }
      $('dlg').close();
      await carregar();
    } catch (e) {
      $('erro').textContent = e.message;
    } finally {
      botao.disabled = false;
    }
  }

  async function excluir(usuario) {
    if (!confirm(`Excluir ${usuario.nome} (${usuario.email})? O login será apagado e não dá para desfazer.`)) return;
    try {
      await chamar('DELETE', null, `?id=${encodeURIComponent(usuario.id_usuario)}`);
      avisar('Usuário excluído.');
      await carregar();
    } catch (e) {
      avisar(e.message, true);
    }
  }

  async function iniciar() {
    const eu = window.LRPerfil || await LRAuth.exigirLogin();
    if (!eu.gerenciaUsuarios) { location.replace('/'); return; }
    $('btnNovo').addEventListener('click', () => abrirFormulario(null));
    $('btnCancelar').addEventListener('click', () => $('dlg').close());
    $('btnGerarSenha').addEventListener('click', () => {
      $('fSenha').value = gerarSenha();
      $('fSenha').select();
    });
    $('fPerfil').addEventListener('change', atualizarCamposPerfil);
    $('form').addEventListener('submit', salvar);
    $('corpo').addEventListener('click', (e) => {
      const botao = e.target.closest('button[data-acao]');
      if (!botao) return;
      const usuario = usuarios.find((u) => u.id_usuario === botao.dataset.id);
      if (!usuario) return;
      if (botao.dataset.acao === 'editar') abrirFormulario(usuario); else excluir(usuario);
    });
    await carregar();
  }

  iniciar().catch((e) => avisar(`Falha ao iniciar: ${e.message}`, true));
})();
