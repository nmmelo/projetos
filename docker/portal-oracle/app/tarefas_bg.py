"""Cache de dados lentos (ex. consultas as bases de dados Oracle) com atualizacao em segundo plano.

pedir(chave, titulo, funcao, ...) devolve logo o que esta em cache (mesmo antigo) e, se os dados tiverem mais de
max_idade segundos (ou se for pedido), arranca a atualizacao numa thread. O estado fica na tabela cache_dados,
por isso qualquer worker (e qualquer pagina) ve as tarefas em curso: a barra "A atualizar" em baixo usa
/api/tarefas e, quando uma tarefa acaba, as paginas que mostram esses dados atualizam-se sozinhas."""
import datetime
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import store

log = logging.getLogger("portal")
_EXEC = ThreadPoolExecutor(max_workers=6, thread_name_prefix="cache")
_LOCAL: set = set()                 # chaves a correr neste processo
_TRAVA = threading.Lock()
MORTA = 900                         # s: uma tarefa "a atualizar" ha mais tempo do que isto morreu (worker reiniciado)


def _agora() -> str:
    return store.now()


def _seg(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return (datetime.datetime.now() - datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None


def obter(chave: str) -> dict | None:
    with store.tx() as t:
        return t.one("SELECT * FROM cache_dados WHERE chave = ?", (chave,))


def _resposta(r: dict | None) -> dict:
    if not r:
        return {"dados": None, "atualizado": None, "idade": None, "a_atualizar": False, "erro": None}
    dados = None
    if r.get("dados"):
        try:
            dados = json.loads(r["dados"])
        except ValueError:
            dados = None
    idade = _seg(r.get("atualizado"))
    return {"dados": dados, "atualizado": r.get("atualizado"), "idade": int(idade) if idade is not None else None,
            "a_atualizar": r.get("estado") == "a_atualizar" and (_seg(r.get("inicio")) or 0) < MORTA,
            "erro": r.get("erro") if r.get("estado") == "erro" else None, "duracao": r.get("duracao")}


def _reservar(chave: str, titulo: str, utilizador: str, link: str) -> bool:
    """Marca a chave como 'a atualizar' (so um pedido ganha). True se esta chamada deve correr a tarefa."""
    agora, limite = _agora(), (datetime.datetime.now() - datetime.timedelta(seconds=MORTA)).strftime("%Y-%m-%d %H:%M:%S")
    with store.tx() as t:
        if not t.one("SELECT chave FROM cache_dados WHERE chave = ?", (chave,)):
            try:
                t.insert("cache_dados", {"chave": chave, "titulo": titulo[:200], "estado": "a_atualizar", "inicio": agora,
                                         "utilizador": utilizador[:64], "link": link[:255]})
                return True
            except Exception:  # noqa: BLE001 - outro worker inseriu ao mesmo tempo
                pass
    with store.tx() as t:
        n = t.run("UPDATE cache_dados SET estado = 'a_atualizar', inicio = ?, fim = NULL, titulo = ?, utilizador = ?, link = ? "
                  "WHERE chave = ? AND (estado IS NULL OR estado <> 'a_atualizar' OR inicio < ?)",
                  (agora, titulo[:200], utilizador[:64], link[:255], chave, limite))
    return n > 0


def _correr(chave: str, funcao) -> None:
    t0 = time.time()
    try:
        dados = funcao()
        with store.tx() as t:
            t.run("UPDATE cache_dados SET dados = ?, atualizado = ?, estado = 'ok', erro = NULL, fim = ?, duracao = ? "
                  "WHERE chave = ?", (json.dumps(dados, default=str), _agora(), _agora(), round(time.time() - t0, 1), chave))
    except Exception as exc:  # noqa: BLE001 - guarda o erro e mantem os dados antigos
        msg = str(exc).splitlines()[0][:500] if str(exc) else exc.__class__.__name__
        with store.tx() as t:
            t.run("UPDATE cache_dados SET estado = 'erro', erro = ?, fim = ?, duracao = ? WHERE chave = ?",
                  (msg, _agora(), round(time.time() - t0, 1), chave))
    finally:
        with _TRAVA:
            _LOCAL.discard(chave)


def pedir(chave: str, titulo: str, funcao, utilizador: str = "", max_idade: int = 30, forcar: bool = False,
          link: str = "") -> dict:
    """Dados em cache (podem ser None ou antigos) e arranca a atualizacao em segundo plano se for preciso."""
    r = obter(chave)
    resp = _resposta(r)
    velho = forcar or resp["dados"] is None or resp["idade"] is None or resp["idade"] > max_idade
    if velho and not resp["a_atualizar"] and _reservar(chave, titulo, utilizador, link):
        with _TRAVA:
            _LOCAL.add(chave)
        _EXEC.submit(_correr, chave, funcao)
        resp["a_atualizar"] = True
    resp["chave"] = chave
    return resp


def esperar(chave: str, segundos: float) -> dict:
    """Espera um pouco (ex. 1 s) para que um pedido rapido venha logo com os dados."""
    fim = time.time() + segundos
    while time.time() < fim:
        r = _resposta(obter(chave))
        if not r["a_atualizar"]:
            return r
        time.sleep(0.15)
    return _resposta(obter(chave))


def tarefas(utilizador: str | None = None, recentes: int = 20) -> list[dict]:
    """Tarefas a correr e as que acabaram ha menos de 'recentes' segundos (para a barra em baixo)."""
    limite = (datetime.datetime.now() - datetime.timedelta(seconds=recentes)).strftime("%Y-%m-%d %H:%M:%S")
    sql = ("SELECT chave, titulo, estado, inicio, fim, erro, duracao, link, utilizador FROM cache_dados "
           "WHERE (estado = 'a_atualizar' OR fim >= ?)")
    par = [limite]
    if utilizador:
        sql += " AND utilizador = ?"
        par.append(utilizador)
    with store.tx() as t:
        rows = t.all(sql + " ORDER BY inicio DESC", tuple(par))
    out = []
    for r in rows:
        dur = _seg(r["inicio"])
        if r["estado"] == "a_atualizar" and (dur or 0) >= MORTA:
            continue
        out.append({"chave": r["chave"], "titulo": r["titulo"], "estado": r["estado"], "erro": r["erro"],
                    "segundos": int(dur) if r["estado"] == "a_atualizar" and dur is not None else None,
                    "duracao": r["duracao"], "link": r["link"], "fim": r["fim"]})
    return out


def limpar(dias: int = 7) -> int:
    limite = (datetime.datetime.now() - datetime.timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    with store.tx() as t:
        return t.run("DELETE FROM cache_dados WHERE (atualizado IS NULL OR atualizado < ?) AND estado <> 'a_atualizar'",
                     (limite,))


def apagar_prefixo(prefixo: str) -> None:
    with store.tx() as t:
        t.run("DELETE FROM cache_dados WHERE chave LIKE ?", (prefixo + "%",))
