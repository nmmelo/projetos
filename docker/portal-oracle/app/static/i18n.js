/* Traducao do portal no browser.
 *
 * O portal e escrito em portugues. Para outra lingua, o servidor envia window.I18N = {lingua, t: {pt: traducao}}
 * (rota /i18n/<lingua>.js) e este ficheiro troca os textos da pagina e dos atributos placeholder, title,
 * aria-label, alt e value (botoes), incluindo o que o JavaScript cria depois (MutationObserver).
 * Numeros dentro dos textos procuram-se como {n}: "ha 5 min" -> chave "ha {n} min".
 * Nao traduz <code>, <pre>, <textarea>, editores nem elementos com translate="no" ou class="notranslate".
 */
(() => {
  const D = (window.I18N && window.I18N.t) || {};
  const NUM = /\d+(?:[.,]\d+)*/g;
  const norm = s => s.replace(/\s+/g, ' ').trim();
  const ATRS = ['placeholder', 'title', 'aria-label', 'alt'];
  const SALTAR = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'TEXTAREA', 'KBD', 'SAMP', 'NOSCRIPT']);
  const feitos = new WeakMap();          // no -> texto que la pusemos (para ignorar as nossas mudancas)

  function t(s) {
    if (s == null) return s;
    const txt = String(s), k = norm(txt);
    if (!k) return txt;
    let tr = D[k];
    if (tr === undefined && NUM.test(k)) {
      NUM.lastIndex = 0;
      const nums = k.match(NUM);
      const modelo = D[k.replace(NUM, '{n}')];
      if (modelo !== undefined) { let i = 0; tr = modelo.replace(/\{n\}/g, () => nums[i++] ?? ''); }
    }
    NUM.lastIndex = 0;
    if (tr === undefined || tr === '') return txt;
    const ini = txt.match(/^\s*/)[0], fim = txt.match(/\s*$/)[0];
    return ini + tr + fim;
  }
  window.t = t;
  window.i18nLingua = (window.I18N && window.I18N.lingua) || 'pt';
  if (!window.I18N || !Object.keys(D).length) { document.documentElement.classList.remove('i18n'); return; }

  function saltar(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (SALTAR.has(e.tagName) || e.isContentEditable || e.getAttribute('translate') === 'no' || e.classList.contains('notranslate'))
        return true;
    }
    return false;
  }
  function texto(n) {
    const v = n.nodeValue;
    if (!v || !v.trim() || feitos.get(n) === v || saltar(n.parentElement)) return;
    const tr = t(v);
    if (tr !== v) { n.nodeValue = tr; }
    feitos.set(n, n.nodeValue);
  }
  function atributos(el) {
    if (saltar(el)) return;
    for (const a of ATRS) {
      if (!el.hasAttribute(a)) continue;
      const v = el.getAttribute(a), marca = 'i18n' + a;
      if (el[marca] === v) continue;
      const tr = t(v);
      if (tr !== v) el.setAttribute(a, tr);
      el[marca] = el.getAttribute(a);
    }
    if (el.tagName === 'INPUT' && /^(submit|button|reset)$/i.test(el.type) && el.value && el.i18nvalue !== el.value) {
      el.value = t(el.value); el.i18nvalue = el.value;
    }
  }
  function arvore(raiz) {
    if (raiz.nodeType === 3) { texto(raiz); return; }
    if (raiz.nodeType !== 1 && raiz.nodeType !== 9 && raiz.nodeType !== 11) return;
    if (raiz.nodeType === 1) { if (saltar(raiz)) return; atributos(raiz); }
    const w = document.createTreeWalker(raiz, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.nodeType === 1 && SALTAR.has(n.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
    for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? texto(n) : atributos(n);
  }

  // dialogos do browser
  for (const f of ['alert', 'confirm', 'prompt']) {
    const orig = window[f].bind(window);
    window[f] = (msg, ...r) => orig(t(msg), ...r);
  }

  function iniciar() {
    document.title = document.title.split(' · ').map(x => t(x)).join(' · ');
    arvore(document.body);
    document.documentElement.classList.remove('i18n');
    new MutationObserver(ms => {
      for (const m of ms) {
        if (m.type === 'characterData') texto(m.target);
        else if (m.type === 'attributes') atributos(m.target);
        else m.addedNodes.forEach(arvore);
      }
    }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATRS.concat('value')});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
  setTimeout(() => document.documentElement.classList.remove('i18n'), 2000);   // nunca deixar a pagina escondida
})();
