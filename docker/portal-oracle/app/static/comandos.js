/* Output de um comando enviado a um agente cliente (atualizar, ...), em direto.
   PortalComando.seguir(id, elemento[, aoTerminar]) -> mostra estado + linhas e vai buscando a cada segundo. */
(function () {
  const T = s => (window.t ? window.t(s) : s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const COR = {'PENDENTE': 'warn', 'EM CURSO': 'accent', 'OK': 'ok', 'ERRO': 'bad'};
  const TXT = {'PENDENTE': 'à espera do agente', 'EM CURSO': 'em curso', 'OK': 'concluído', 'ERRO': 'falhou'};
  function desenhar(el, d, titulo) {
    const fim = d.estado === 'OK' || d.estado === 'ERRO';
    el.innerHTML = `<div class="cmd-out-h">${titulo ? `<b>${esc(titulo)}</b>` : ''}${d.descricao ? `<span>${esc(T(d.descricao))}</span>` : ''}
        <span class="tag ${COR[d.estado] || ''}">${fim ? '' : '<span class="spin"></span> '}${esc(T(TXT[d.estado] || d.estado))}</span>
        <small class="muted">#${d.id} · ${d.cliente ? esc(d.cliente) + ' · ' : ''}${esc(d.pedido_por || '')} · ${esc((d.criado || '').slice(11, 19))}${d.terminado ? ' → ' + esc(d.terminado.slice(11, 19)) : ''}</small>
        ${d.estado === 'ERRO' ? `<button type="button" class="btn small cmd-out-rep" title="${esc(T('Fazer de novo o mesmo pedido'))}">↻ ${esc(T('Repetir'))}</button>` : ''}
        <button type="button" class="cmd-out-x" title="${esc(T('Fechar'))}" aria-label="${esc(T('Fechar'))}">✕</button></div>
      <pre class="cmd-out-p" translate="no">${esc(d.output || '')}</pre>`;
    const p = el.querySelector('pre'); p.scrollTop = p.scrollHeight;
    const rep = el.querySelector('.cmd-out-rep');
    if (rep) rep.onclick = async () => {
      rep.disabled = true;
      const novo = await repetir(d.id);
      if (novo) seguir(novo, el, el._aoTerminar, titulo); else rep.disabled = false;
    };
    el.querySelector('.cmd-out-x').onclick = () => { el._parar = true; el.dataset.fechado = d.id; el.hidden = true; el.innerHTML = '';
      if (el.parentElement && el.parentElement.id === 'saidas') el.remove();
      document.querySelectorAll('.cmd-hist tr.on').forEach(x => x.classList.remove('on')); };
  }
  /* Repete um pedido que falhou (mesmos argumentos): devolve o id do novo pedido ou null */
  async function repetir(id) {
    const csrf = (document.querySelector('meta[name=csrf]') || {}).content || '';
    try {
      const r = await fetch(`/clientes/comando/${id}/repetir`, {method: 'POST', headers: {'X-CSRF': csrf, 'X-Requested-With': 'fetch'}});
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) { (window.PortalDialogo ? PortalDialogo.aviso(j.erro || String(r.status)) : alert(j.erro || r.status)); return null; }
      window.dispatchEvent(new CustomEvent('portal:comando', {detail: {id: j.id, de: id}}));
      return j.id;
    } catch (e) { return null; }
  }
  async function seguir(id, el, aoTerminar, titulo) {
    el.classList.add('cmd-out'); el.hidden = false; el._aoTerminar = aoTerminar;
    const meu = Symbol(); el._seguir = meu; el._parar = false;      /* um novo seguir() substitui o anterior */
    let ultimo = '';
    for (let i = 0; i < 1800; i++) {
      if (el._parar || el._seguir !== meu) return;
      try {
        const r = await fetch(`/clientes/api/comando/${id}`, {cache: 'no-store'});
        if (r.ok) {
          const d = await r.json(); const k = d.estado + d.output;
          if (el._parar || el._seguir !== meu) return;
          if (k !== ultimo) { desenhar(el, d, titulo); ultimo = k; }
          if (d.estado === 'OK' || d.estado === 'ERRO') { if (aoTerminar) aoTerminar(d); return d; }
        }
      } catch (e) { /* rede: tenta de novo */ }
      await new Promise(ok => setTimeout(ok, i < 120 ? 1000 : 3000));
    }
  }
  window.PortalComando = {seguir, repetir};
})();
