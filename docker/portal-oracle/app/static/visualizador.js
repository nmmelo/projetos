/* Visualizador de logs (Clientes -> cliente -> Logs -> Abrir): numeros de linha, realce conforme o tipo de log
   (detetado), niveis (erro/aviso/info/debug), pesquisa com navegacao, filtros, quebra de linha e ir para a linha. */
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const T = s => (window.t ? window.t(s) : s);

  const TIPOS = {
    oracle: {nome: 'Oracle alert log', teste: /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d$|^(ORA|TNS)-\d{5}|^(Thread \d+ advanced|Completed:|Errors in file)/},
    listener: {nome: 'Oracle listener', teste: /^\d\d-[A-Z]{3}-\d{4} \d\d:\d\d:\d\d \* /},
    journal: {nome: 'journald (short-iso)', teste: /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} \S+ \S+?(\[\d+\])?: /},
    syslog: {nome: 'syslog', teste: /^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \S+ \S+?(\[\d+\])?:? /},
    acesso: {nome: 'Acesso web (Apache/Nginx)', teste: /^\S+ \S+ \S+ \[\d\d\/\w{3}\/\d{4}:\d\d:\d\d:\d\d [+-]\d{4}\] "/},
    audit: {nome: 'auditd', teste: /^type=\S+ msg=audit\(/},
    json: {nome: 'JSON (uma entrada por linha)', teste: /^\s*\{.*\}\s*$/},
    dnf: {nome: 'dnf / yum', teste: /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(Z|[+-]\d{4}) (DDEBUG|DEBUG|INFO|WARNING|ERROR|SUBDEBUG)/},
    iso: {nome: 'Data ISO + mensagem', teste: /^\[?\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d/},
    texto: {nome: 'Texto', teste: /./},
  };

  function detetar(linhas) {
    const conta = {};
    const amostra = linhas.filter(l => l.trim()).slice(-400);
    for (const l of amostra) for (const [k, t] of Object.entries(TIPOS)) if (k !== 'texto' && t.teste.test(l)) { conta[k] = (conta[k] || 0) + 1; break; }
    const melhor = Object.entries(conta).sort((a, b) => b[1] - a[1])[0];
    return melhor && melhor[1] >= Math.max(3, amostra.length * 0.15) ? melhor[0] : 'texto';
  }

  const NIVEL = [
    ['err', /\b(error|err|fail(ed|ure)?|fatal|crit(ical)?|emerg|alert|panic|exception|denied|segfault|refused|unable|invalid|corrupt)\b|\b(ORA|TNS|PLS|RMAN)-\d{3,5}\b|\boom-kill/i],
    ['warn', /\b(warn(ing)?|deprecated|timeout|timed out|retry|retrying|not responding)\b/i],
    ['debug', /\b(debug|ddebug|trace|subdebug)\b/i],
    ['info', /\b(info|notice|started|starting|completed|success(ful)?|accepted)\b/i],
  ];
  const nivel = l => { for (const [k, r] of NIVEL) if (r.test(l)) return k; return ''; };

  /* tokens: [regex, classe]; aplicados por ordem, sem sobreposicao */
  const TOK = [
    [/\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?/g, 't-ts'],
    [/^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d/g, 't-ts'],
    [/\[\d\d\/\w{3}\/\d{4}:\d\d:\d\d:\d\d [+-]\d{4}\]/g, 't-ts'],
    [/\b\d\d-[A-Z]{3}-\d{4} \d\d:\d\d:\d\d\b/g, 't-ts'],
    [/\b(?:ORA|TNS|PLS|RMAN|SP2)-\d{3,5}\b/g, 't-ora'],
    [/\b(?:ERROR|FATAL|CRITICAL|CRIT|EMERG|ALERT|SEVERE|Error|error|failed|Failed|FAILED)\b/g, 't-err'],
    [/\b(?:WARNING|WARN|Warning|warning)\b/g, 't-warn'],
    [/\b(?:INFO|NOTICE|DEBUG|DDEBUG|TRACE)\b/g, 't-lvl'],
    [/"(?:GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS) [^"]*"/g, 't-str'],
    [/(?<=" )[1-5]\d\d(?= )/g, 't-http'],
    [/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, 't-str'],
    [/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b/g, 't-ip'],
    [/\b[\w.-]+\[\d+\](?=:)/g, 't-proc'],
    [/\b[a-zA-Z_][\w-]*(?==)/g, 't-key'],
    [/(?:\/[\w.@+-]+){2,}/g, 't-path'],
  ];

  function segmentos(l) {
    const marcas = [];
    for (const [re, cls] of TOK) {
      re.lastIndex = 0; let m;
      while ((m = re.exec(l)) !== null) {
        if (!m[0]) { re.lastIndex++; continue; }
        const a = m.index, b = a + m[0].length;
        if (!marcas.some(x => a < x[1] && b > x[0])) marcas.push([a, b, cls]);
      }
    }
    marcas.sort((x, y) => x[0] - y[0]);
    const out = []; let i = 0;
    for (const [a, b, cls] of marcas) { if (a > i) out.push([l.slice(i, a), '']); out.push([l.slice(a, b), cls]); i = b; }
    if (i < l.length) out.push([l.slice(i), '']);
    return out;
  }

  function jsonBonito(l) {
    try { const o = JSON.parse(l); return Object.entries(o).map(([k, v]) => [[k, 't-key'], ['=', ''], [typeof v === 'string' ? v : JSON.stringify(v), typeof v === 'string' ? 't-str' : 't-num'], ['  ', '']]).flat(); }
    catch (e) { return null; }
  }

  function htmlLinha(l, tipo, q) {
    const segs = (tipo === 'json' && jsonBonito(l)) || segmentos(l);
    let h = '';
    for (const [txt, cls] of segs) {
      let s = esc(txt);
      if (q) {
        const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'), 'gi');
        s = s.replace(re, m => `<mark>${m}</mark>`);
      }
      h += cls ? `<span class="${cls}">${s}</span>` : s;
    }
    return h || '&nbsp;';
  }

  function criar(raiz, texto, opcoes) {
    const linhas = texto.replace(/\r/g, '').split('\n');
    if (linhas.length && linhas[linhas.length - 1] === '') linhas.pop();
    const niveis = linhas.map(nivel);
    let tipo = detetar(linhas), q = '', soQ = false, ocultos = new Set(), atual = -1, hits = [];
    const primeiro = (opcoes && opcoes.primeiraLinha) || 1;
    const contagem = {err: 0, warn: 0, info: 0, debug: 0};
    niveis.forEach(n => { if (n) contagem[n]++; });
    raiz.innerHTML = `
      <div class="lv-bar">
        <label class="lv-tipo">${esc(T('Tipo'))} <select>${Object.entries(TIPOS).map(([k, t]) => `<option value="${k}">${esc(T(t.nome))}</option>`).join('')}</select></label>
        <div class="lv-proc"><input type="search" placeholder="${esc(T('Procurar (Enter = seguinte)'))}"><span class="lv-n0"></span>
          <button type="button" class="btn small" data-a="ant" title="${esc(T('Anterior'))}">↑</button><button type="button" class="btn small" data-a="seg" title="${esc(T('Seguinte'))}">↓</button>
          <label class="inline small"><input type="checkbox" data-a="so"> ${esc(T('só as linhas encontradas'))}</label></div>
        <div class="lv-niveis">${[['err', 'Erros'], ['warn', 'Avisos'], ['info', 'Info'], ['debug', 'Debug']].map(([k, n]) =>
          `<button type="button" class="lv-chip on c-${k}" data-nivel="${k}">${esc(T(n))} <b>${contagem[k]}</b></button>`).join('')}</div>
        <div class="lv-dir"><label class="inline small"><input type="checkbox" data-a="wrap" checked> ${esc(T('quebrar linhas'))}</label>
          <input type="number" min="1" class="lv-ir" placeholder="${esc(T('linha'))}" title="${esc(T('Ir para a linha'))}"></div>
      </div>
      <div class="lv-ed wrap" tabindex="0"></div>
      <div class="lv-rod"><span class="lv-info"></span></div>`;
    const ed = raiz.querySelector('.lv-ed'), sel = raiz.querySelector('.lv-tipo select'), inp = raiz.querySelector('.lv-proc input');
    sel.value = tipo;
    function desenhar() {
      hits = []; const partes = [];
      for (let i = 0; i < linhas.length; i++) {
        const l = linhas[i], n = niveis[i];
        const tem = q && l.toLowerCase().includes(q.toLowerCase());
        if (tem) hits.push(i);
        if ((n && ocultos.has(n)) || (soQ && q && !tem)) continue;
        partes.push(`<div class="lv-l ${n ? 'n-' + n : ''}${tem ? ' hit' : ''}" data-i="${i}"><span class="lv-num">${i + primeiro}</span><span class="lv-txt">${htmlLinha(l, tipo, q)}</span></div>`);
      }
      ed.innerHTML = partes.join('') || `<div class="lv-vazio">${esc(T('Sem linhas para mostrar.'))}</div>`;
      raiz.querySelector('.lv-n0').textContent = q ? `${hits.length} ${T('linha(s)')}` : '';
      raiz.querySelector('.lv-info').textContent = `${linhas.length} ${T('linha(s)')} · ${T(TIPOS[tipo].nome)}${partes.length !== linhas.length ? ' · ' + partes.length + ' ' + T('visíveis') : ''}`;
      atual = -1;
    }
    function ir(i) {
      const el = ed.querySelector(`.lv-l[data-i="${i}"]`); if (!el) return;
      ed.querySelectorAll('.lv-l.cur').forEach(x => x.classList.remove('cur')); el.classList.add('cur');
      el.scrollIntoView({block: 'center'});
    }
    let tmr; inp.addEventListener('input', () => { clearTimeout(tmr); tmr = setTimeout(() => { q = inp.value.trim(); desenhar(); if (hits.length) { atual = 0; ir(hits[0]); } }, 250); });
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); passo(e.shiftKey ? -1 : 1); } });
    function passo(d) { if (!hits.length) return; atual = (atual + d + hits.length) % hits.length; ir(hits[atual]); raiz.querySelector('.lv-n0').textContent = `${atual + 1}/${hits.length}`; }
    raiz.querySelector('[data-a=ant]').onclick = () => passo(-1);
    raiz.querySelector('[data-a=seg]').onclick = () => passo(1);
    raiz.querySelector('[data-a=so]').onchange = e => { soQ = e.target.checked; desenhar(); };
    raiz.querySelector('[data-a=wrap]').onchange = e => ed.classList.toggle('wrap', e.target.checked);
    raiz.querySelectorAll('.lv-chip').forEach(b => b.onclick = () => { b.classList.toggle('on');
      b.classList.contains('on') ? ocultos.delete(b.dataset.nivel) : ocultos.add(b.dataset.nivel); desenhar(); });
    sel.onchange = () => { tipo = sel.value; desenhar(); };
    raiz.querySelector('.lv-ir').addEventListener('keydown', e => { if (e.key === 'Enter') { ir(Math.max(0, +e.target.value - primeiro)); } });
    desenhar();
    ed.scrollTop = ed.scrollHeight;          /* logs: o mais recente esta no fim */
  }
  window.PortalLog = {criar, detetar};
})();
