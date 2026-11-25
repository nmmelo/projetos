/* Caixa de processos (canto inferior direito): numa so caixa, tudo o que esta a correr —
   atualizacoes de dados em segundo plano do utilizador (cache de dados lentos, ex. Oracle),
   execucoes Oracle (shrink) e pedidos aos agentes dos clientes.
   Fechada mostra so o numero em execucao; ao clicar estende-se para o lado com a lista.
   Continua noutras paginas (o estado esta no servidor). Quando uma atualizacao de dados acaba,
   dispara o evento 'portal:tarefa' (detail = tarefa) para as paginas atualizarem os dados sozinhas. */
(() => {
  const API = document.documentElement.dataset.apiTarefas; if (!API) return;
  const T = s => (window.t ? window.t(s) : s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const GRUPOS = {dados: 'Atualização de dados', oracle: 'Execuções Oracle', clientes: 'Pedidos aos agentes'};
  const MOSTRAR_OK = 6000, MOSTRAR_ERRO = 15000, PARADO = 8000;
  const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } };
  let box, timer = null, conhecidas = {}, vistas = new Set(), acabou = {}, aberto = ls('portal.processos.aberto') === '1';
  const ini = Date.now();

  function caixa() {
    if (box) return box;
    box = document.createElement('div'); box.id = 'tarefas-bar'; box.hidden = true;
    box.innerHTML = `
      <div class="tb-painel" role="region" aria-label="${esc(T('Processos em execução'))}">
        <div class="tb-cab"><b class="tb-cab-t"></b>
          <button type="button" class="tb-fechar" title="${esc(T('Recolher'))}" aria-label="${esc(T('Recolher'))}">⟩</button></div>
        <div class="tb-lista" role="status" aria-live="polite"></div>
      </div>
      <button type="button" class="tb-pill" aria-expanded="false"><span class="tb-ic"></span><b class="tb-n"></b><span class="tb-txt"></span></button>`;
    box.querySelector('.tb-pill').onclick = () => abrir(!aberto);
    box.querySelector('.tb-fechar').onclick = () => abrir(false);
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && aberto && !document.querySelector('dialog[open]')) abrir(false); });
    document.body.appendChild(box); return box;
  }
  function abrir(v) {
    aberto = !!v; ls('portal.processos.aberto', aberto ? '1' : '0');
    const b = caixa(); b.classList.toggle('aberto', aberto);
    b.querySelector('.tb-pill').setAttribute('aria-expanded', String(aberto));
  }
  const tempo = s => s == null ? '' : s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60 ? (s % 60) + ' s' : ''}`.trim()
    : `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`;

  function item(t) {
    const a = t.estado === 'a_atualizar', ok = t.estado === 'ok';
    const ic = a ? '<span class="tb-spin"></span>' : ok ? '<span class="tb-ok">✓</span>' : '<span class="tb-err">✕</span>';
    const partes = [];
    if (t.detalhe) partes.push(esc(T(t.detalhe)));
    if (a) partes.push(t.segundos != null ? esc(tempo(t.segundos)) : esc(T('a atualizar')));
    else if (ok) partes.push(esc(T('concluído')) + (t.duracao != null ? ' · ' + esc(String(t.duracao).replace('.', ',')) + ' s' : ''));
    else partes.push(esc(T(t.erro || 'erro')));
    if (t.por) partes.push(esc(t.por));
    const pct = a && t.progresso != null ? Math.max(0, Math.min(100, +t.progresso)) : null;
    const barra = a ? (pct != null ? `<i class="tb-barra"><i style="width:${pct}%"></i></i>` : '<i class="tb-barra ind"><i></i></i>') : '';
    return `<a class="tb-item ${a ? 'a' : ok ? 'ok' : 'err'}" ${t.link ? `href="${esc(t.link)}"` : ''} title="${esc(t.titulo)}">${ic}
      <span class="tb-corpo"><span class="tb-t" translate="no">${esc(t.titulo)}</span><small>${partes.join(' · ')}</small></span>${barra}</a>`;
  }

  function desenhar(lista) {
    const b = caixa();
    const pai = document.querySelector('dialog[open]:not(.pdlg)') || document.body;   // por cima de janelas modais
    if (b.parentNode !== pai) pai.appendChild(b);
    const agora = Date.now();
    const visiveis = lista.filter(t => {
      if (t.estado === 'a_atualizar') return true;
      if (!vistas.has(t.chave)) return false;
      acabou[t.chave] = acabou[t.chave] || agora;
      return agora - acabou[t.chave] < (t.estado === 'ok' ? MOSTRAR_OK : MOSTRAR_ERRO);
    });
    b.hidden = !visiveis.length;
    if (!visiveis.length) return;
    const ativos = visiveis.filter(t => t.estado === 'a_atualizar').length;
    const erros = visiveis.filter(t => t.estado !== 'a_atualizar' && t.estado !== 'ok').length;
    b.dataset.estado = ativos ? 'a' : erros ? 'err' : 'ok';
    b.querySelector('.tb-ic').innerHTML = ativos ? '<span class="tb-spin"></span>' : erros ? '✕' : '✓';
    b.querySelector('.tb-n').textContent = ativos || (erros || visiveis.length);
    b.querySelector('.tb-txt').textContent = ativos ? T('em execução') : erros ? T('com erro') : T('concluído(s)');
    b.querySelector('.tb-cab-t').textContent = ativos ? `${T('Em execução')} (${ativos})` : T('Concluídos');
    let html = '';
    for (const g of Object.keys(GRUPOS)) {
      const do_g = visiveis.filter(t => t.grupo === g);
      if (!do_g.length) continue;
      do_g.sort((x, y) => (x.estado === 'a_atualizar' ? 0 : 1) - (y.estado === 'a_atualizar' ? 0 : 1));
      html += `<div class="tb-grupo"><h4>${esc(T(GRUPOS[g]))} <span>${do_g.length}</span></h4>${do_g.map(item).join('')}</div>`;
    }
    b.querySelector('.tb-lista').innerHTML = html;
    b.classList.toggle('aberto', aberto);
  }

  async function ver() {
    let d;
    try { d = await (await fetch(API, {cache: 'no-store'})).json(); } catch (e) { agendar(5000); return; }
    const tarefas = (d.tarefas || []).map(t => ({...t, grupo: 'dados', chave: 't:' + t.chave, _chave: t.chave}));
    const lista = [...tarefas, ...(d.processos || [])];
    for (const t of lista) {
      const antes = conhecidas[t.chave];
      if (t.estado === 'a_atualizar') { vistas.add(t.chave); delete acabou[t.chave]; }
      if (t._chave && antes === 'a_atualizar' && t.estado !== 'a_atualizar')
        window.dispatchEvent(new CustomEvent('portal:tarefa', {detail: {...t, chave: t._chave}}));
      conhecidas[t.chave] = t.estado;
    }
    // acabados antes de esta pagina abrir mas ha pouco tempo: mostra-os tambem (vindos de outra pagina)
    if (Date.now() - ini < 3000) lista.forEach(t => { if (t.estado !== 'a_atualizar') vistas.add(t.chave); });
    desenhar(lista);
    agendar(lista.some(t => t.estado === 'a_atualizar') ? 1500 : (!caixa().hidden ? 1000 : PARADO));
  }
  function agendar(ms) { clearTimeout(timer); timer = ms ? setTimeout(ver, ms) : null; }
  window.PortalTarefas = {
    vigiar(chave) { if (chave) { vistas.add('t:' + chave); conhecidas['t:' + chave] = 'a_atualizar'; } agendar(400); },
    ver, abrir,
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) ver(); else agendar(0); });
  ver();
})();
