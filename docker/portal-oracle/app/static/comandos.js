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
        <button type="button" class="cmd-out-x" title="${esc(T('Fechar'))}" aria-label="${esc(T('Fechar'))}">✕</button></div>
      <pre class="cmd-out-p" translate="no">${esc(d.output || '')}</pre>`;
    const p = el.querySelector('pre'); p.scrollTop = p.scrollHeight;
    el.querySelector('.cmd-out-x').onclick = () => { el._parar = true; el.dataset.fechado = d.id; el.hidden = true; el.innerHTML = '';
      if (el.parentElement && el.parentElement.id === 'saidas') el.remove();
      document.querySelectorAll('.cmd-hist tr.on').forEach(x => x.classList.remove('on')); };
  }
  async function seguir(id, el, aoTerminar, titulo) {
    el.classList.add('cmd-out'); el.hidden = false;
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
  window.PortalComando = {seguir};
})();
