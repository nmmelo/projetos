"""Codigo QR em SVG, sem bibliotecas externas (modo byte, correcao de erros M, versoes 1-40).

Usado para o codigo da Autenticacao de Dois Fatores (otpauth://...), lido pelas aplicacoes de
autenticacao (Microsoft Authenticator, Google Authenticator, FreeOTP, ...). Segue a norma ISO/IEC 18004.
"""

# Correcao de erros M: codewords de correcao por bloco e numero de blocos, por versao (indice 0 nao usado)
_ECC = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28,
        28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
_BLOCOS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25,
           26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
_FORMATO_M = 0


def _modulos_dados(v: int) -> int:
    r = (16 * v + 128) * v + 64
    if v >= 2:
        n = v // 7 + 2
        r -= (25 * n - 10) * n - 55
        if v >= 7:
            r -= 36
    return r


def _cw_dados(v: int) -> int:
    return _modulos_dados(v) // 8 - _ECC[v] * _BLOCOS[v]


def _mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _divisor(grau: int) -> list[int]:
    r = [0] * (grau - 1) + [1]
    raiz = 1
    for _ in range(grau):
        for j in range(grau):
            r[j] = _mul(r[j], raiz)
            if j + 1 < grau:
                r[j] ^= r[j + 1]
        raiz = _mul(raiz, 2)
    return r


def _resto(dados: list[int], div: list[int]) -> list[int]:
    r = [0] * len(div)
    for b in dados:
        f = b ^ r.pop(0)
        r.append(0)
        for i, c in enumerate(div):
            r[i] ^= _mul(c, f)
    return r


def _bit(x: int, i: int) -> bool:
    return (x >> i) & 1 != 0


class _QR:
    def __init__(self, dados: bytes):
        for v in range(1, 41):
            cc = 8 if v <= 9 else 16
            if 4 + cc + 8 * len(dados) <= _cw_dados(v) * 8:
                break
        else:
            raise ValueError("Texto demasiado longo para um código QR.")
        self.v, self.n = v, v * 4 + 17
        bits: list[int] = []

        def poe(val, n):
            bits.extend((val >> i) & 1 for i in reversed(range(n)))
        poe(4, 4)
        poe(len(dados), cc)
        for b in dados:
            poe(b, 8)
        cap = _cw_dados(v) * 8
        poe(0, min(4, cap - len(bits)))
        poe(0, -len(bits) % 8)
        pad = 0xEC
        while len(bits) < cap:
            poe(pad, 8)
            pad ^= 0xEC ^ 0x11
        cws = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        self.m = [[False] * self.n for _ in range(self.n)]
        self.f = [[False] * self.n for _ in range(self.n)]
        self._padroes()
        self._desenhar(self._intercalar(cws))
        melhor, pen = 0, None
        for mk in range(8):
            self._mascara(mk)
            self._formato(mk)
            p = self._penalizacao()
            if pen is None or p < pen:
                melhor, pen = mk, p
            self._mascara(mk)
        self._mascara(melhor)
        self._formato(melhor)

    def _set(self, x, y, escuro):
        self.m[y][x] = escuro
        self.f[y][x] = True

    def _padroes(self):
        n = self.n
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self._alinhamento()
        k = len(pos)
        for i in range(k):
            for j in range(k):
                if (i == 0 and j == 0) or (i == 0 and j == k - 1) or (i == k - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self._formato(0)
        if self.v >= 7:
            r = self.v
            for _ in range(12):
                r = (r << 1) ^ ((r >> 11) * 0x1F25)
            b = self.v << 12 | r
            for i in range(18):
                a, c = n - 11 + i % 3, i // 3
                self._set(a, c, _bit(b, i))
                self._set(c, a, _bit(b, i))

    def _alinhamento(self) -> list[int]:
        if self.v == 1:
            return []
        k = self.v // 7 + 2
        passo = 26 if self.v == 32 else (self.v * 4 + k * 2 + 1) // (k * 2 - 2) * 2
        return list(reversed([self.n - 7 - i * passo for i in range(k - 1)] + [6]))

    def _formato(self, mk):
        d = _FORMATO_M << 3 | mk
        r = d
        for _ in range(10):
            r = (r << 1) ^ ((r >> 9) * 0x537)
        b = (d << 10 | r) ^ 0x5412
        n = self.n
        for i in range(6):
            self._set(8, i, _bit(b, i))
        self._set(8, 7, _bit(b, 6))
        self._set(8, 8, _bit(b, 7))
        self._set(7, 8, _bit(b, 8))
        for i in range(9, 15):
            self._set(14 - i, 8, _bit(b, i))
        for i in range(8):
            self._set(n - 1 - i, 8, _bit(b, i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, _bit(b, i))
        self._set(8, n - 8, True)

    def _intercalar(self, dados: list[int]) -> list[int]:
        nb, ecl = _BLOCOS[self.v], _ECC[self.v]
        brutos = _modulos_dados(self.v) // 8
        curtos = nb - brutos % nb
        tam = brutos // nb
        div = _divisor(ecl)
        blocos, k = [], 0
        for i in range(nb):
            d = dados[k:k + tam - ecl + (0 if i < curtos else 1)]
            k += len(d)
            ecc = _resto(d, div)
            if i < curtos:
                d = d + [0]
            blocos.append(d + ecc)
        out = []
        for i in range(len(blocos[0])):
            for j, b in enumerate(blocos):
                if i != tam - ecl or j >= curtos:
                    out.append(b[i])
        return out

    def _desenhar(self, dados: list[int]):
        n, i = self.n, 0
        dir_ = n - 1
        while dir_ >= 1:
            if dir_ == 6:
                dir_ = 5
            for vert in range(n):
                for j in range(2):
                    x = dir_ - j
                    sobe = (dir_ + 1) & 2 == 0
                    y = n - 1 - vert if sobe else vert
                    if not self.f[y][x] and i < len(dados) * 8:
                        self.m[y][x] = _bit(dados[i >> 3], 7 - (i & 7))
                        i += 1
            dir_ -= 2

    def _mascara(self, mk):
        fs = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
              lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
              lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
              lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mk]
        for y in range(self.n):
            for x in range(self.n):
                if not self.f[y][x] and fs(x, y):
                    self.m[y][x] = not self.m[y][x]

    def _penalizacao(self) -> int:
        n, m, p = self.n, self.m, 0
        for linhas in (m, [list(c) for c in zip(*m)]):
            for ln in linhas:
                corr = 1
                for a, b in zip(ln, ln[1:]):
                    if a == b:
                        corr += 1
                    else:
                        if corr >= 5:
                            p += corr - 2
                        corr = 1
                if corr >= 5:
                    p += corr - 2
        for y in range(n - 1):
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    p += 3
        escuros = sum(sum(r) for r in m)
        p += abs(escuros * 20 - n * n * 10) // (n * n) * 10
        return p


def matriz(texto: str) -> list[list[bool]]:
    return _QR(texto.encode("utf-8")).m


def svg(texto: str, modulo: int = 5, margem: int = 4) -> str:
    """SVG (fundo branco, modulos pretos) com o texto codificado."""
    m = matriz(texto)
    n = len(m) + 2 * margem
    caminho = "".join(f"M{x + margem},{y + margem}h1v1h-1z" for y, ln in enumerate(m) for x, v in enumerate(ln) if v)
    lado = n * modulo
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{lado}" height="{lado}" '
            f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{caminho}" fill="#000"/></svg>')
