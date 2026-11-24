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
        <small class="muted">#${d.id} · ${d.cliente ? esc(d.cliente) + ' · ' : ''}${esc(d.pedido_por || '')} · ${esc((d.criado || '').slice(11, 19))}${d.terminado ? ' → ' + esc(d.terminado.slice(11, 19)) : ''}</small></div>
      <pre class="cmd-out-p" translate="no">${esc(d.output || '')}</pre>`;
    const p = el.querySelector('pre'); p.scrollTop = p.scrollHeight;
  }
  async function seguir(id, el, aoTerminar, titulo) {
    el.classList.add('cmd-out'); el.hidden = false;
    let ultimo = '';
    for (let i = 0; i < 1800; i++) {
      try {
        const r = await fetch(`/clientes/api/comando/${id}`, {cache: 'no-store'});
        if (r.ok) {
          const d = await r.json(); const k = d.estado + d.output;
          if (k !== ultimo) { desenhar(el, d, titulo); ultimo = k; }
          if (d.estado === 'OK' || d.estado === 'ERRO') { if (aoTerminar) aoTerminar(d); return d; }
        }
      } catch (e) { /* rede: tenta de novo */ }
      await new Promise(ok => setTimeout(ok, i < 120 ? 1000 : 3000));
    }
  }
  window.PortalComando = {seguir};
})();
