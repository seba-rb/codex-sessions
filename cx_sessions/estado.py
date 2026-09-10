import json
import os
import shutil
import sqlite3
import time

from .appserver import RpcError
import sys


def _base_estado(codex_home):
    """La base de sesiones del perfil, o None.

    El nombre lleva version (`state_5.sqlite` hoy) y ya cambio cinco veces: si
    la esperada no esta, se toma la mas nueva que haya.
    """
    db = os.path.join(codex_home, "state_5.sqlite")
    if os.path.exists(db):
        return db
    try:
        candidatos = sorted(f for f in os.listdir(codex_home)
                            if f.startswith("state_") and f.endswith(".sqlite"))
    except OSError:
        return None
    return os.path.join(codex_home, candidatos[-1]) if candidatos else None


def purgar_huerfanas(codex_home, ids, aplicar):
    """El unico caso que toca la base directamente.

    `thread/delete` exige que el rollout exista ("no rollout found for thread
    id"), asi que una fila cuyo archivo ya no esta no se puede borrar por la
    API. Es el bug openai/codex#36558: borrar quita el archivo y deja la fila,
    y desde ahi queda trabada para siempre.
    """
    db = _base_estado(codex_home)
    if db is None:
        print("  no encontre la base de sesiones; omito las huerfanas", file=sys.stderr)
        return 0
    if not aplicar:
        return len(ids)
    backup = f"{db}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db, backup)
    print(f"  backup de la base: {backup}")
    con = sqlite3.connect(db)
    with con:
        for tid in ids:
            con.execute("delete from threads where id = ?", (tid,))

# Eventos del rollout que marcan el ciclo de vida de una sesion. El resto
# (item_completed, token_count) es ruido de progreso y se ignora.
_CICLO = ("task_started", "task_complete", "turn_aborted")
# Nombres en ingles y pegados al vocabulario del propio Codex: su protocolo
# usa active / complete para el ciclo de vida de un turno, y su vista de
# agentes habla de working / ready. Traducirlos solo agregaria un dialecto mas.
_ETIQUETA = {"task_started": "active", "task_complete": "complete",
             "turn_aborted": "aborted"}
# El ultimo evento se busca leyendo el final del archivo. 64KB alcanzan casi
# siempre, pero un rollout largo puede tener megabytes de ruido despues del
# task_started, asi que la ventana crece hasta leerlo entero antes de rendirse.
_VENTANAS = (65536, 1048576, None)
_cache_estado = {}


def _ultimo_evento(path):
    for ventana in _VENTANAS:
        try:
            with open(path, "rb") as fh:
                if ventana is None:
                    fh.seek(0)
                else:
                    fh.seek(0, os.SEEK_END)
                    tam = fh.tell()
                    if tam <= ventana and ventana != _VENTANAS[0]:
                        return None  # ya se leyo entero en la pasada anterior
                    fh.seek(max(0, tam - ventana))
                    if tam > ventana:
                        fh.readline()  # descartar la linea partida al medio
                crudo = fh.read()
        except OSError:
            return None
        visto = None
        for linea in crudo.split(b"\n"):
            if b"task_" not in linea and b"turn_aborted" not in linea:
                continue  # descarte barato antes de parsear
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if d.get("type") != "event_msg":
                continue
            tipo = (d.get("payload") or {}).get("type")
            if tipo in _CICLO:
                visto = tipo
        if visto:
            return visto
    return None


def estado_sesion(sesion):
    """Etiqueta de estado, derivada del rollout en disco.

    Los estados en vivo solo los conoce el daemon de Codex, y su socket de
    control no responde el protocolo del app-server (contesta vacio incluso a
    metodos inventados). El rollout, en cambio, es informacion persistida.
    """
    path = sesion.get("path")
    if not path:
        return ""
    try:
        st = os.stat(path)
    except OSError:
        return "orphaned"
    clave = (path, st.st_mtime, st.st_size)
    if clave in _cache_estado:
        return _cache_estado[clave]
    evento = _ultimo_evento(path)
    etiqueta_ = _ETIQUETA.get(evento, "")
    # Un task_started sin cierre y sin escrituras recientes es casi siempre un
    # proceso que murio, no una sesion pensando.
    if evento == "task_started" and (time.time() - st.st_mtime) > 300:
        etiqueta_ = "stalled"
    _cache_estado[clave] = etiqueta_
    return etiqueta_


def resumen_estados(sesiones):
    """`3 trabajando · 12 listas`, omitiendo lo que este en cero."""
    from collections import Counter
    cuenta = Counter(e for e in (estado_sesion(s) for s in sesiones) if e)
    orden = ("active", "stalled", "complete", "aborted", "orphaned")
    partes = [f"{cuenta[e]} {e}" for e in orden if cuenta.get(e)]
    return " \u00b7 ".join(partes)


def archivar(srv, sesion, archivada=True):
    """Archiva o desarchiva una sesion."""
    metodo = "thread/archive" if archivada else "thread/unarchive"
    try:
        srv.llamar(metodo, {"threadId": sesion["id"]})
        return True, None
    except RpcError as e:
        return False, e.msg
