/* Pagina de login: aspeto "Boas-vindas" (o formulario abre com o botao Entrar) e o fator humano
   ("Nao sou um robo"): procura um nonce tal que sha256("<desafio>:<nonce>") comece por N bits a zero. */
(() => {
  const card = document.getElementById('login-card');
  const entrar = document.getElementById('login-entrar');
  if (entrar && card) {
    entrar.addEventListener('click', () => {
      card.classList.remove('fechado');
      card.classList.add('aberto');
      setTimeout(() => { const u = document.getElementById('login-user'); if (u) u.focus(); }, 60);
    });
  }

  const caixa = document.getElementById('humano');
  if (!caixa) return;
  const btn = document.getElementById('humano-btn'), campo = document.getElementById('pow');
  const form = caixa.closest('form');
  const desafio = caixa.dataset.c, bits = parseInt(caixa.dataset.b, 10) || 16;

  // SHA-256 (sincrono; o crypto.subtle so existe em HTTPS e e assincrono por cada tentativa)
  const K = new Uint32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb,
    0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f,
    0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  const W = new Uint32Array(64);
  function sha256Primeiro(txt) {           // devolve a primeira palavra (32 bits) do hash
    const b = new TextEncoder().encode(txt);
    const n = ((b.length + 9 + 63) >> 6) << 6, m = new Uint8Array(n);
    m.set(b); m[b.length] = 0x80;
    const dv = new DataView(m.buffer); dv.setUint32(n - 4, b.length * 8);
    let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a, h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    const rr = (x, k) => (x >>> k) | (x << (32 - k));
    for (let o = 0; o < n; o += 64) {
      for (let i = 0; i < 16; i++) W[i] = dv.getUint32(o + i * 4);
      for (let i = 16; i < 64; i++) {
        const s0 = rr(W[i - 15], 7) ^ rr(W[i - 15], 18) ^ (W[i - 15] >>> 3), s1 = rr(W[i - 2], 17) ^ rr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
      }
      let a = h0, bb = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (let i = 0; i < 64; i++) {
        const t1 = (h + (rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        const t2 = ((rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + bb) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }
    return h0 >>> 0;
  }

  let estado = 'novo', depois = null;
  function resolver() {
    if (estado !== 'novo') return;
    estado = 'a-verificar';
    caixa.classList.add('a-verificar'); btn.setAttribute('aria-busy', 'true');
    const limite = 2 ** (32 - bits);
    let nonce = 0;
    const lote = () => {
      const fim = nonce + 4000;
      for (; nonce < fim; nonce++) {
        if (sha256Primeiro(desafio + ':' + nonce) < limite) {
          campo.value = String(nonce); estado = 'ok';
          caixa.classList.remove('a-verificar'); caixa.classList.add('ok');
          btn.setAttribute('aria-pressed', 'true'); btn.removeAttribute('aria-busy');
          if (depois) { const f = depois; depois = null; f(); }
          return;
        }
      }
      setTimeout(lote, 0);
    };
    setTimeout(lote, 30);
  }
  btn.addEventListener('click', resolver);
  form.addEventListener('submit', ev => {
    if (estado === 'ok') return;
    ev.preventDefault();
    depois = () => form.requestSubmit ? form.requestSubmit() : form.submit();
    resolver();
  });
})();
