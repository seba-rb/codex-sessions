import fcntl
import os
import signal
import subprocess
import time

from .appserver import RpcError
from .estado import borrar_fila

# Cuanto se le da al proceso para cerrar por las buenas antes del SIGKILL, y
# cuanto se espera despues de matarlo a que el kernel suelte el lock.
_ESPERA_TERM = 5.0
_ESPERA_KILL = 2.0
# Techo por si el lock se vuelve a tomar solo: cortar antes de matar el sistema.
_MAX_WRITERS = 5


def ruta_lock(codex_home, thread_id):
    """El lock por sesion que Codex toma mientras la tiene abierta."""
    if not (codex_home and thread_id):
        return None
    return os.path.join(codex_home, "thread-writer-locks", f"{thread_id}.lock")


def hay_writer(codex_home, thread_id):
    """Si alguien tiene tomada la sesion. Sin depender de nada instalado.

    El .lock queda en disco aunque el proceso muera, asi que su existencia no
    dice nada; lo que dice es si se puede tomar. Pedir el mismo flock que pide
    Codex responde eso mismo, con el kernel de arbitro y sin salir a buscar un
    binario que puede no estar (xps, por ejemplo, no trae lsof).
    """
    lock = ruta_lock(codex_home, thread_id)
    if not lock or not os.path.exists(lock):
        return False
    try:
        # O_RDWR y no "w": abrir en modo escritura truncaria el archivo de
        # alguien que lo esta usando.
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True  # tomado por otro
    finally:
        os.close(fd)  # cerrar el fd suelta el flock que acabamos de pedir
    return False


def pid_con_writer(codex_home, thread_id):
    """Quien tiene tomado el writer, si se puede averiguar.

    Saber *que* esta tomado alcanza para avisar; para matarlo hace falta el
    pid, y eso solo lo da lsof. Donde no este instalado devuelve None aunque
    el lock este tomado: usar `hay_writer` para lo primero y esto para lo
    segundo, no al reves.
    """
    lock = ruta_lock(codex_home, thread_id)
    if not lock or not os.path.exists(lock):
        return None
    try:
        r = subprocess.run(["lsof", "-t", lock], capture_output=True, text=True)
    except OSError:  # incluye FileNotFoundError: no hay lsof
        return None
    pids = [int(x) for x in r.stdout.split() if x.isdigit()]
    return pids[0] if pids else None


def _cerrar(pid, codex_home, thread_id):
    """SIGTERM y, si a los segundos sigue con el lock, SIGKILL."""
    for sig, espera in ((signal.SIGTERM, _ESPERA_TERM), (signal.SIGKILL, _ESPERA_KILL)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True, None
        except PermissionError:
            return False, f"no puedo matar al proceso {pid}"
        limite = time.time() + espera
        while time.time() < limite:
            if pid_con_writer(codex_home, thread_id) != pid:
                return True, None
            time.sleep(0.1)
    return False, f"el proceso {pid} no solto el lock ni con SIGKILL"


def terminar_writer(codex_home, thread_id):
    """Deja el lock de la sesion libre, matando a quien lo tenga.

    SIGTERM primero, para que Codex cierre el rollout y salga ordenado. Se
    repite mientras quede alguien tomandolo: el lock es exclusivo, pero nada
    garantiza que un solo proceso lo pida, y matar al primero de la lista y
    darlo por hecho dejaria el borrado a medias. Devuelve los pids que hubo
    que cerrar.
    """
    muertos = []
    while len(muertos) < _MAX_WRITERS:
        pid = pid_con_writer(codex_home, thread_id)
        if pid is None:
            if hay_writer(codex_home, thread_id):
                # Tomado por alguien que no sabemos nombrar. Seguir de largo
                # seria peor que parar: borrariamos la fila por abajo y el
                # proceso vivo la volveria a escribir en su proximo turno.
                return False, ("la tiene tomada un proceso que no puedo identificar: "
                               "instala lsof, o cerrala donde este abierta"), muertos
            return True, None, muertos
        ok, err = _cerrar(pid, codex_home, thread_id)
        muertos.append(pid)
        if not ok:
            return False, err, muertos
    return False, f"el lock sigue tomado tras cerrar {len(muertos)} procesos", muertos


def borrar_forzado(srv, sesion):
    """Borra la sesion aunque este abierta en otro proceso.

    No alcanza con sacar la fila por abajo: Codex la persiste con un
    `INSERT ... ON CONFLICT(id) DO UPDATE`, asi que un proceso vivo la vuelve
    a insertar en el turno siguiente. Por eso el orden es matar primero y
    borrar despues, y por eso se reintenta la API: una vez libre el lock,
    `thread/delete` hace la limpieza completa mejor que nosotros.
    """
    ok, err, _ = terminar_writer(srv.home, sesion["id"])
    if not ok:
        return False, err
    try:
        srv.llamar("thread/delete", {"threadId": sesion["id"]})
        return True, None
    except RpcError as e:
        detalle = e.msg
    # La API todavia se niega (fila huerfana, rollout movido, version vieja):
    # ultimo recurso, a mano.
    ok, err = borrar_fila(srv.home, sesion["id"])
    if not ok:
        return False, f"{detalle}; {err}"
    for ruta in (sesion.get("path"), ruta_lock(srv.home, sesion["id"])):
        try:
            if ruta:
                os.remove(ruta)
        except OSError:
            pass
    return True, None
