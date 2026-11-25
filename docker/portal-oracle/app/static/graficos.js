/* Graficos de linhas em SVG (sem bibliotecas): eixo do tempo, grelha, legenda e valores ao passar o rato.
   PortalGrafico.desenhar(el, {series: [{nome, cor, pontos: [[ts, valor], ...], tracejado}], unidade: '%'|'bytes',
                               desde, ate, max}) */
(() => {
  const T = s => (window.t ? window.t(s) : s);
  const NS = 'http://www.w3.org/2000/svg';
  const fmtBytes = v => v == null ? '—' : v >= 1073741824 ? (v / 1073741824).toFixed(2) + ' GB' : v >= 1048576 ? (v / 1048576).toFixed(0) + ' MB' : (v / 1024).toFixed(0) + ' KB';
  const fmt = (v, u) => v == null ? '—' : u === 'bytes' ? fmtBytes(v) : (Math.round(v * 10) / 10).toLocaleString('pt-PT') + (u || '');
  const pad = n => String(n).padStart(2, '0');
  function rotuloTempo(ts, span) {
    const d = new Date(ts * 1000);
    if (span <= 2 * 86400) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}${span <= 8 * 86400 ? ' ' + pad(d.getHours()) + 'h' : ''}`;
  }
  function el(tag, at, pai) { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); if (pai) pai.appendChild(e); return e; }
  function teto(v, u) {
    if (u === '%') return v <= 10 ? 10 : v <= 25 ? 25 : v <= 50 ? 50 : v <= 100 ? 100 : Math.ceil(v / 100) * 100;
    if (u === 'bytes') { const mb = 1048576; const p = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]; for (const x of p) if (v <= x * mb) return x * mb; return v * 1.1; }
    return v <= 0 ? 1 : v * 1.15;
  }
  function desenhar(caixa, o) {
    caixa.innerHTML = '';
    const W = Math.max(320, caixa.clientWidth || 600), H = o.altura || 220, m = {e: 58, d: 12, c: 14, b: 26};
    const series = (o.series || []).filter(s => s.pontos && s.pontos.length);
    const desde = o.desde, ate = o.ate, span = Math.max(60, ate - desde);
    let maxv = o.max || 0; series.forEach(s => s.pontos.forEach(p => { if (p[1] != null && p[1] > maxv) maxv = p[1]; }));
    const topo = teto(maxv || (o.unidade === '%' ? 10 : 1), o.unidade);
    const x = ts => m.e + (W - m.e - m.d) * (ts - desde) / span, y = v => H - m.b - (H - m.b - m.c) * (v / topo);
    const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%', height: H, class: 'pg-svg', role: 'img'}, caixa);
    for (let i = 0; i <= 4; i++) {
      const v = topo * i / 4, yy = y(v);
      el('line', {x1: m.e, x2: W - m.d, y1: yy, y2: yy, class: 'pg-grid'}, svg);
      el('text', {x: m.e - 6, y: yy + 4, class: 'pg-lbl', 'text-anchor': 'end'}, svg).textContent = fmt(v, o.unidade);
    }
    const nx = Math.max(2, Math.min(8, Math.floor((W - m.e) / 90)));
    for (let i = 0; i <= nx; i++) {
      const ts = desde + span * i / nx, xx = x(ts);
      el('line', {x1: xx, x2: xx, y1: m.c, y2: H - m.b, class: 'pg-grid v'}, svg);
      el('text', {x: xx, y: H - 8, class: 'pg-lbl', 'text-anchor': i === 0 ? 'start' : i === nx ? 'end' : 'middle'}, svg).textContent = rotuloTempo(ts, span);
    }
    if (!series.length) {
      el('text', {x: W / 2, y: H / 2, class: 'pg-vazio', 'text-anchor': 'middle'}, svg).textContent = T('Sem dados neste período');
    }
    const passo = o.passo || 60;
    series.forEach(s => {
      let d = '', ant = null;
      s.pontos.forEach(p => {
        if (p[1] == null) { ant = null; return; }
        const salto = ant != null && p[0] - ant > passo * 2.5;     // falha de recolha: corta a linha
        d += (d && !salto ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1);
        ant = p[0];
      });
      if (s.area) {
        const pts = s.pontos.filter(p => p[1] != null);
        if (pts.length > 1) el('path', {d: d + `L${x(pts[pts.length - 1][0]).toFixed(1)},${y(0)}L${x(pts[0][0]).toFixed(1)},${y(0)}Z`, fill: s.cor, opacity: .08, stroke: 'none'}, svg);
      }
      el('path', {d, fill: 'none', stroke: s.cor, 'stroke-width': s.largura || 1.8, 'stroke-dasharray': s.tracejado ? '4 3' : '', 'stroke-linejoin': 'round'}, svg);
    });
    // legenda
    const leg = document.createElement('div'); leg.className = 'pg-leg';
    leg.innerHTML = series.map(s => `<span><i style="background:${s.cor}${s.tracejado ? ';opacity:.6' : ''}"></i>${s.nome}</span>`).join('');
    caixa.appendChild(leg);
    // rato: linha vertical + valores
    const guia = el('line', {y1: m.c, y2: H - m.b, class: 'pg-guia', visibility: 'hidden'}, svg);
    const tip = document.createElement('div'); tip.className = 'pg-tip'; tip.hidden = true; caixa.appendChild(tip);
    const area = el('rect', {x: m.e, y: m.c, width: W - m.e - m.d, height: H - m.b - m.c, fill: 'transparent'}, svg);
    area.addEventListener('mousemove', ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
      const ts = desde + (px - m.e) / (W - m.e - m.d) * span;
      const linhas = series.map(s => {
        let best = null; for (const p of s.pontos) if (best == null || Math.abs(p[0] - ts) < Math.abs(best[0] - ts)) best = p;
        return best && Math.abs(best[0] - ts) <= passo * 1.5 ? {s, p: best} : null;
      }).filter(Boolean);
      if (!linhas.length) { tip.hidden = true; guia.setAttribute('visibility', 'hidden'); return; }
      const t0 = linhas[0].p[0], d = new Date(t0 * 1000);
      guia.setAttribute('x1', x(t0)); guia.setAttribute('x2', x(t0)); guia.setAttribute('visibility', 'visible');
      tip.innerHTML = `<b>${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}</b>` +
        linhas.map(l => `<div><i style="background:${l.s.cor}"></i>${l.s.nome}: <b>${fmt(l.p[1], o.unidade)}</b></div>`).join('');
      tip.hidden = false;
      const cx = (x(t0) / W) * r.width;
      tip.style.left = Math.min(r.width - tip.offsetWidth - 4, Math.max(4, cx + 12)) + 'px';
    });
    area.addEventListener('mouseleave', () => { tip.hidden = true; guia.setAttribute('visibility', 'hidden'); });
  }

  /* Grafico de fatias (donut): PortalGrafico.fatias(caixa, {itens: [{nome, valor, link}], unidade: 'MB', max: 8,
     titulo, centro}) — os 'max' maiores e o resto em "Outros"; legenda com valor e %; passar o rato realca a fatia. */
  const CORES = ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#475569'];
  const fmtMB = v => v >= 1048576 ? (v / 1048576).toFixed(2).replace('.', ',') + ' TB' : v >= 1024 ? (v / 1024).toFixed(1).replace('.', ',') + ' GB'
    : Math.round(v).toLocaleString('pt-PT') + ' MB';
  function fatias(caixa, o) {
    caixa.innerHTML = '';
    const max = o.max || 8;
    let itens = (o.itens || []).filter(i => i.valor > 0).sort((a, b) => b.valor - a.valor);
    if (itens.length > max) {
      const resto = itens.slice(max - 1);
      itens = [...itens.slice(0, max - 1), {nome: `${T('Outros')} (${resto.length})`, valor: resto.reduce((a, i) => a + i.valor, 0), outros: true}];
    }
    const total = itens.reduce((a, i) => a + i.valor, 0);
    const wrap = document.createElement('div'); wrap.className = 'pf-wrap'; caixa.appendChild(wrap);
    if (!total) { wrap.innerHTML = `<p class="muted">${T('Sem dados.')}</p>`; return; }
    const R = 80, r = 52, C = 100;
    const svg = el('svg', {viewBox: '0 0 200 200', class: 'pf-svg', role: 'img', 'aria-label': o.titulo || ''}, wrap);
    const fmtv = o.unidade === 'MB' ? fmtMB : v => fmt(v, o.unidade);
    const centroV = el('text', {x: C, y: C - 2, 'text-anchor': 'middle', class: 'pf-c1'}, null);
    const centroT = el('text', {x: C, y: C + 16, 'text-anchor': 'middle', class: 'pf-c2'}, null);
    const leg = document.createElement('ul'); leg.className = 'pf-leg'; wrap.appendChild(leg);
    const repor = () => { centroV.textContent = fmtv(total); centroT.textContent = o.centro || T('total'); };
    let ang = -Math.PI / 2;
    const pt = (a, rr) => [C + rr * Math.cos(a), C + rr * Math.sin(a)];
    itens.forEach((it, i) => {
      const cor = it.outros ? '#94a3b8' : CORES[i % CORES.length];
      const frac = it.valor / total, a2 = ang + frac * 2 * Math.PI, grande = frac > .5 ? 1 : 0;
      let fatia;
      if (frac >= .9999) {
        fatia = el('path', {d: `M ${C - R} ${C} a ${R} ${R} 0 1 0 ${2 * R} 0 a ${R} ${R} 0 1 0 ${-2 * R} 0 M ${C - r} ${C} a ${r} ${r} 0 1 1 ${2 * r} 0 a ${r} ${r} 0 1 1 ${-2 * r} 0`,
                            fill: cor, 'fill-rule': 'evenodd', class: 'pf-f'}, svg);
      } else {
        const [x1, y1] = pt(ang, R), [x2, y2] = pt(a2, R), [x3, y3] = pt(a2, r), [x4, y4] = pt(ang, r);
        fatia = el('path', {d: `M ${x1} ${y1} A ${R} ${R} 0 ${grande} 1 ${x2} ${y2} L ${x3} ${y3} A ${r} ${r} 0 ${grande} 0 ${x4} ${y4} Z`,
                            fill: cor, class: 'pf-f'}, svg);
      }
      const pct = (frac * 100).toFixed(frac < .1 ? 1 : 0).replace('.', ',') + '%';
      el('title', {}, fatia).textContent = `${it.nome}: ${fmtv(it.valor)} (${pct})`;
      const li = document.createElement('li');
      li.innerHTML = `<i style="background:${cor}"></i><span class="pf-n" translate="no"></span><span class="pf-v">${fmtv(it.valor)}</span><span class="pf-p">${pct}</span>`;
      li.querySelector('.pf-n').textContent = it.nome;
      if (it.link) { li.classList.add('pf-link'); li.onclick = () => { location.href = it.link; }; fatia.style.cursor = 'pointer'; fatia.onclick = li.onclick; }
      leg.appendChild(li);
      const on = v => { fatia.classList.toggle('on', v); li.classList.toggle('on', v); svg.classList.toggle('foco', v);
        if (v) { centroV.textContent = pct; centroT.textContent = it.nome.length > 16 ? it.nome.slice(0, 15) + '…' : it.nome; } else repor(); };
      fatia.addEventListener('mouseenter', () => on(true)); fatia.addEventListener('mouseleave', () => on(false));
      li.addEventListener('mouseenter', () => on(true)); li.addEventListener('mouseleave', () => on(false));
      ang = a2;
    });
    svg.appendChild(centroV); svg.appendChild(centroT); repor();
  }
  window.PortalGrafico = {desenhar, fmt, fmtBytes, fatias};
})();
