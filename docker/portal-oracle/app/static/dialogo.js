/* Caixas de confirmacao e aviso do portal (substituem o confirm()/alert() do browser).
 *
 * PortalDialogo.confirmar(texto, {titulo, perigo, ok, cancelar}) -> Promise<boolean>
 * PortalDialogo.aviso(texto, {titulo})                            -> Promise<void>
 *
 * O confirm() nativo bloqueia; esta caixa nao. Para manter o codigo existente
 * (onsubmit="return confirm(...)", if (!confirm(...)) return;) sem alteracoes, window.confirm passa a:
 * mostrar a caixa e devolver false (cancela o evento); ao confirmar, repete o mesmo evento
 * (submete o formulario / volta a clicar) e o confirm() com o mesmo texto devolve true dessa vez.
 * Fora de um evento (ex. depois de um await) usa o confirm() do browser.
 */
(function () {
  'use strict';
  const T = s => (window.t ? window.t(s) : s);
  const nativoConfirm = window.confirm.bind(window);
  const PERIGO = /^\s*(apagar|remover|eliminar|desativar|desligar|esquecer|cancelar|parar|stop|kill|retirar|desassociar|repor|limpar|importar|mudar|gerar um token|delete|remove|disable|forget|cancel|reset|clear)/i;

  let dlg = null;
  function criar() {
    if (dlg) return dlg;
    dlg = document.createElement('dialog');
    dlg.className = 'pdlg';
    dlg.setAttribute('aria-modal', 'true');
    dlg.innerHTML = `
      <form method="dialog" class="pdlg-caixa">
        <div class="pdlg-icone" aria-hidden="true"></div>
        <div class="pdlg-texto">
          <h2 class="pdlg-titulo" id="pdlg-titulo"></h2>
          <div class="pdlg-corpo" id="pdlg-corpo"></div>
        </div>
        <div class="pdlg-botoes">
          <button type="submit" value="nao" class="btn pdlg-nao"></button>
          <button type="submit" value="sim" class="btn primary pdlg-sim"></button>
        </div>
      </form>`;
    dlg.setAttribute('aria-labelledby', 'pdlg-titulo');
    dlg.setAttribute('aria-describedby', 'pdlg-corpo');
    dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close('nao'); });   /* clicar fora fecha */
    document.body.appendChild(dlg);
    return dlg;
  }

  /* "Atualizar o agente? Ele descarrega..." -> titulo "Atualizar o agente?" + texto "Ele descarrega..." */
  function partir(texto) {
    texto = String(texto ?? '').trim();
    const m = texto.match(/^([^\n]{1,140}?[?!:])(\s+[\s\S]+)?$/);
    if (m && m[2]) return [m[1], m[2].trim()];
    if (texto.length <= 90 && !texto.includes('\n')) return [texto, ''];
    return ['', texto];
  }

  function abrir(texto, o = {}) {
    const d = criar();
    if (d.open) d.close('nao');
    const [tit, corpo] = o.titulo ? [o.titulo, String(texto ?? '')] : partir(texto);
    const tipo = o.tipo || (o.perigo ?? PERIGO.test(texto) ? 'perigo' : 'pergunta');
    d.dataset.tipo = tipo;
    d.querySelector('.pdlg-titulo').textContent = tit || (tipo === 'aviso' ? T('Aviso') : T('Confirmar'));
    const c = d.querySelector('.pdlg-corpo');
    c.textContent = corpo; c.hidden = !corpo;
    const sim = d.querySelector('.pdlg-sim'), nao = d.querySelector('.pdlg-nao');
    sim.textContent = o.ok || (tipo === 'aviso' ? T('OK') : T('Confirmar'));
    sim.classList.toggle('danger', tipo === 'perigo');
    nao.textContent = o.cancelar || T('Voltar');
    nao.hidden = tipo === 'aviso';
    d.returnValue = '';
    const foco = document.activeElement;
    return new Promise(res => {
      d.addEventListener('close', () => {
        try { foco && foco.focus && foco.focus({preventScroll: true}); } catch (e) { /* */ }
        res(d.returnValue === 'sim');
      }, {once: true});
      d.showModal();
      (tipo === 'perigo' ? nao : sim).focus();   /* em acoes destrutivas o foco fica em Voltar */
    });
  }

  const PortalDialogo = {
    confirmar: (texto, o) => abrir(texto, o),
    aviso: (texto, o = {}) => abrir(texto, {...o, tipo: 'aviso'}).then(() => undefined),
  };
  window.PortalDialogo = PortalDialogo;

  /* ---- compatibilidade com confirm()/alert() existentes ---- */
  let aprovado = null;
  window.confirm = function (texto) {
    texto = String(texto ?? '');
    if (aprovado !== null && aprovado === texto) { aprovado = null; return true; }
    const ev = window.event;
    if (!ev || !document.body || !['submit', 'click', 'change'].includes(ev.type)) return nativoConfirm(texto);
    const alvo = ev.target, submitter = ev.submitter || null;
    if (ev.type === 'change' && !(alvo instanceof HTMLInputElement && /checkbox|radio/.test(alvo.type))) return nativoConfirm(texto);
    abrir(texto).then(ok => {
      if (!ok) return;
      aprovado = texto;
      try {
        if (ev.type === 'submit' && alvo instanceof HTMLFormElement) {
          if (alvo.requestSubmit) alvo.requestSubmit(submitter && submitter.form === alvo ? submitter : undefined);
          else if (!alvo.onsubmit || alvo.onsubmit() !== false) alvo.submit();
        } else {
          alvo.click();
        }
      } finally { aprovado = null; }
    });
    return false;
  };
  window.alert = function (texto) { PortalDialogo.aviso(texto); };
})();
