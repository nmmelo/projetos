/* Barra "A atualizar" (em baixo): tarefas em segundo plano do utilizador (cache de dados lentos, ex. Oracle).
   Continua noutras paginas (o estado esta no servidor). Quando uma tarefa acaba, dispara o evento
   'portal:tarefa' (detail = tarefa) para as paginas atualizarem os dados sozinhas. */
(() => {
  const API = document.documentElement.dataset.apiTarefas; if (!API) return;
  const T = s => (window.t ? window.t(s) : s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let bar, timer = null, conhecidas = {}, vistas = new Set(), acabou = {};
  const MOSTRAR_OK = 6000, MOSTRAR_ERRO = 15000, MAX = 4;
  const ini = Date.now();
  function caixa() {
    if (bar) return bar;
    bar = document.createElement('div'); bar.id = 'tarefas-bar'; bar.setAttribute('role', 'status'); bar.setAttribute('aria-live', 'polite');
    document.body.appendChild(bar); return bar;
  }
  function desenhar(lista) {
    const b = caixa();
    const pai = document.querySelector('dialog[open]') || document.body;   // por cima de janelas modais
    if (b.parentNode !== pai) pai.appendChild(b);
    const agora = Date.now();
    const ativas = lista.filter(t => t.estado === 'a_atualizar');
    const feitas = lista.filter(t => {
      if (t.estado === 'a_atualizar' || !vistas.has(t.chave)) return false;
      acabou[t.chave] = acabou[t.chave] || agora;
      return agora - acabou[t.chave] < (t.estado === 'ok' ? MOSTRAR_OK : MOSTRAR_ERRO);
    });
    const todos = [...ativas, ...feitas], itens = todos.slice(0, MAX);
    b.hidden = !itens.length;
    const resto = todos.length - itens.length;
    b.innerHTML = itens.map(t => {
      const ok = t.estado === 'ok', a = t.estado === 'a_atualizar';
      const ic = a ? '<span class="tb-spin"></span>' : ok ? '<span class="tb-ok">✓</span>' : '<span class="tb-err">✕</span>';
      const det = a ? `${T('a atualizar')}${t.segundos != null ? ' · ' + t.segundos + ' s' : ''}`
        : ok ? `${T('atualizado')}${t.duracao != null ? ' · ' + String(t.duracao).replace('.', ',') + ' s' : ''}` : esc(t.erro || T('erro'));
      return `<a class="tb-item ${a ? 'a' : ok ? 'ok' : 'err'}" ${t.link ? `href="${esc(t.link)}"` : ''} title="${esc(t.erro || '')}">${ic}
        <span class="tb-t" translate="no">${esc(t.titulo)}</span><small>${det}</small>${a ? '<i class="tb-prog"></i>' : ''}</a>`;
    }).join('') + (resto > 0 ? `<span class="tb-item tb-mais">+${resto} ${T('tarefa(s)')}</span>` : '');
  }
  async function ver() {
    let lista = [];
    try { lista = (await (await fetch(API, {cache: 'no-store'})).json()).tarefas || []; } catch (e) { agendar(5000); return; }
    for (const t of lista) {
      const antes = conhecidas[t.chave];
      if (t.estado === 'a_atualizar') { vistas.add(t.chave); delete acabou[t.chave]; }
      if (antes === 'a_atualizar' && t.estado !== 'a_atualizar') window.dispatchEvent(new CustomEvent('portal:tarefa', {detail: t}));
      conhecidas[t.chave] = t.estado;
    }
    // acabadas antes de esta pagina abrir mas ha pouco tempo: mostra-as tambem (vindas de outra pagina)
    if (Date.now() - ini < 3000) lista.forEach(t => { if (t.estado !== 'a_atualizar') vistas.add(t.chave); });
    desenhar(lista);
    const visiveis = !caixa().hidden;
    agendar(lista.some(t => t.estado === 'a_atualizar') ? 1500 : (visiveis ? 1000 : 0));
  }
  function agendar(ms) { clearTimeout(timer); timer = ms ? setTimeout(ver, ms) : null; }
  window.PortalTarefas = {
    vigiar(chave) { if (chave) { vistas.add(chave); conhecidas[chave] = 'a_atualizar'; } agendar(400); },
    ver,
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) ver(); });
  ver();
})();
