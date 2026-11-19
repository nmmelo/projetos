/* Paineis de cartoes (Home e Sistema): ordenar, esconder e mostrar cartoes, em modo de edicao.
 *
 *   const p = new Painel(document.getElementById('dash'), {
 *     chave: 'home', layout: {ordem: [...], ocultos: [...]}, url: '/painel/layout/home', csrf: '...',
 *     editar: botao, tray: elemento});
 *
 * Os cartoes sao elementos .dcard com data-id e data-t (titulo). A disposicao e guardada no servidor,
 * por utilizador (e tambem no browser, para o caso de o servidor nao responder).
 */
class Painel {
  constructor(el, o) {
    this.el = el; this.o = o;
    const loc = this._ls();
    this.layout = Object.assign({ordem: [], ocultos: null}, o.layout && (o.layout.ordem || o.layout.ocultos) ? o.layout : loc || {});
    if (!Array.isArray(this.layout.ocultos)) this.layout.ocultos = o.ocultosPadrao || [];
    this.editando = false;
    if (o.editar) o.editar.addEventListener('click', () => this.alternar());
    if (o.tray) o.tray.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.add) this.mostrar(b.dataset.add);
      else if (b.dataset.acao === 'repor') this.repor();
      else if (b.dataset.acao === 'fim') this.alternar(false);
    });
    el.addEventListener('click', e => {
      const x = e.target.closest('.dcard-x');
      if (!x) { if (this.editando && e.target.closest('a, button')) e.preventDefault(); return; }
      e.preventDefault(); this.esconder(x.closest('.dcard').dataset.id);
    });
    el.addEventListener('dragstart', e => {
      const c = e.target.closest('.dcard'); if (!this.editando || !c) return e.preventDefault();
      c.classList.add('drag'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', c.dataset.id);
    });
    el.addEventListener('dragend', e => { const c = e.target.closest('.dcard'); if (c) c.classList.remove('drag'); this.guardar(); });
    el.addEventListener('dragover', e => {
      if (!this.editando) return;
      e.preventDefault();
      const arr = el.querySelector('.dcard.drag'), alvo = e.target.closest('.dcard');
      if (!arr || !alvo || alvo === arr) return;
      const r = alvo.getBoundingClientRect();
      const depois = (e.clientX - r.left) / r.width > .5;
      el.insertBefore(arr, depois ? alvo.nextSibling : alvo);
    });
    this.aplicar();
  }
  cartoes() { return [...this.el.querySelectorAll(':scope > .dcard')]; }
  aplicar() {
    const ordem = this.layout.ordem, base = this.cartoes();
    const pos = c => { const i = ordem.indexOf(c.dataset.id); return i < 0 ? 1000 + base.indexOf(c) : i; };
    base.sort((a, b) => pos(a) - pos(b)).forEach(c => this.el.appendChild(c));
    base.forEach(c => { c.hidden = this.layout.ocultos.includes(c.dataset.id); c.draggable = this.editando; });
    this.tray();
    if (this.o.aoMudar) this.o.aoMudar();
  }
  tray() {
    const t = this.o.tray; if (!t) return;
    t.hidden = !this.editando;
    const ocultos = this.cartoes().filter(c => c.hidden);
    const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    t.innerHTML = `<div class="tray-t"><b>Editar painel</b><span class="muted small">Arraste os cartões para os reordenar;
        ✕ esconde um cartão.</span></div>
      <div class="tray-add">${ocultos.length ? '<span class="muted small">Adicionar:</span>' + ocultos.map(c =>
        `<button type="button" class="small tray-chip" data-add="${esc(c.dataset.id)}">+ ${esc(c.dataset.i || '')} ${esc(c.dataset.t)}</button>`).join('')
        : '<span class="muted small">Todos os cartões estão visíveis.</span>'}</div>
      <div class="tray-b"><button type="button" class="small" data-acao="repor">Repor</button>
        <button type="button" class="small primary" data-acao="fim">Concluir</button></div>`;
  }
  alternar(v) {
    this.editando = v === undefined ? !this.editando : v;
    this.el.classList.toggle('editing', this.editando);
    if (this.o.editar) { this.o.editar.classList.toggle('on', this.editando); this.o.editar.setAttribute('aria-pressed', this.editando); }
    this.aplicar();
  }
  esconder(id) { if (!this.layout.ocultos.includes(id)) this.layout.ocultos.push(id); this.aplicar(); this.guardar(); }
  mostrar(id) {
    this.layout.ocultos = this.layout.ocultos.filter(x => x !== id);
    const c = this.cartoes().find(x => x.dataset.id === id);
    if (c) this.el.appendChild(c);               // aparece no fim
    this.guardarOrdem(); this.aplicar(); this.guardar();
  }
  repor() { this.layout = {ordem: [], ocultos: [...(this.o.ocultosPadrao || [])]}; this.aplicar(); this.guardar(); }
  guardarOrdem() { this.layout.ordem = this.cartoes().map(c => c.dataset.id); }
  _ls(v) {
    const k = 'painel.' + this.o.chave;
    try { if (v === undefined) return JSON.parse(localStorage.getItem(k)); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return null; }
  }
  guardar() {
    this.guardarOrdem(); this._ls(this.layout);
    if (this.o.url) fetch(this.o.url, {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json', 'X-CSRF': this.o.csrf},
                                        body: JSON.stringify(this.layout)}).catch(() => {});
  }
}

/* Barra de atualizacao automatica: <select> com o intervalo (guardado no browser) e botao "atualizar agora". */
function autoAtualizar(chave, sel, botao, info, fn, padrao) {
  const k = 'refresh.' + chave;
  let t = null, a_correr = false;
  try { const v = localStorage.getItem(k); if (v !== null && [...sel.options].some(o => o.value === v)) sel.value = v; else sel.value = padrao; } catch (e) { sel.value = padrao; }
  async function corre() {
    if (a_correr) return; a_correr = true; botao && botao.classList.add('girar');
    try { await fn(); if (info) info.textContent = 'atualizado ' + new Date().toLocaleTimeString('pt-PT'); }
    catch (e) { if (info) info.textContent = 'sem ligação ao portal'; }
    finally { a_correr = false; botao && setTimeout(() => botao.classList.remove('girar'), 400); }
  }
  function programar() {
    clearInterval(t); const s = +sel.value;
    if (s > 0) t = setInterval(() => { if (!document.hidden) corre(); }, s * 1000);
  }
  sel.addEventListener('change', () => { try { localStorage.setItem(k, sel.value); } catch (e) {} programar(); corre(); });
  botao && botao.addEventListener('click', corre);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && +sel.value > 0) corre(); });
  programar();
  return {corre};
}
