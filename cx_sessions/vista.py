import argparse
import curses
import locale
import os
import shutil
import subprocess

from .comun import edad_dias, etiqueta, corto, filtrar
from .estado import estado_sesion, resumen_estados, archivar

from .appserver import RpcError
from cx_sessions.comun import _ruta_corta
from cx_sessions.comun import _texto_sesion
from cx_sessions.appserver import borrar
from cx_sessions.appserver import codex_bin
from cx_sessions.comun import _ancho, _recortar, _limpiar





def _preview_ui(stdscr, srv, sesion):
    try:
        # thread/items/list responde "not supported yet" en sesiones viejas
        # (formato de rollout anterior); thread/read sirve para todas, solo que
        # trae los items envueltos en turnos.
        try:
            entradas = srv.llamar(
                "thread/items/list", {"threadId": sesion["id"], "limit": 20}).get("data", [])
        except RpcError:
            hilo = srv.llamar(
                "thread/read", {"threadId": sesion["id"], "includeTurns": True}).get("thread", {})
            entradas = []
            for turno in hilo.get("turns") or []:
                entradas.extend(turno.get("items") or [])
            entradas = entradas[:20]
        lineas = [etiqueta(sesion), "", f"cwd: {_ruta_corta(sesion)}", ""]
        for entrada in entradas:
            item = entrada.get("item") if isinstance(entrada.get("item"), dict) else entrada
            valor = item.get("text") or item.get("content") or item.get("message") or item.get("summary")
            if isinstance(valor, list):
                valor = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in valor)
            valor = valor or f"[{item.get('type') or item.get('itemType') or 'item'}]"
            tipo = item.get("type") or item.get("itemType") or ""
            lineas.append(f"--- {tipo} ---" if tipo else "---")
            lineas.extend(str(valor).splitlines() or [""])
    except Exception as e:
        lineas = [f"Error al cargar preview: {e}"]
    alto, ancho = stdscr.getmaxyx()
    stdscr.erase()
    for y, linea in enumerate(lineas[:max(1, alto - 2)]):
        stdscr.addnstr(y, 0, linea, max(1, ancho - 1))
    stdscr.addnstr(alto - 1, 0, "Cualquier tecla vuelve", max(1, ancho - 1), curses.A_DIM)
    stdscr.refresh()
    curses.flushinp()
    stdscr.getch()


def _armar_filas(vistas, cwd_actual):
    """Filas a dibujar y elementos navegables, en el mismo orden.

    El cursor se mueve sobre `navegables`, que ahora incluye los encabezados de
    grupo: pararse en uno y pulsar Enter abre una sesion nueva en ese
    directorio. Antes los encabezados se saltaban; ahora son un destino.

    `cwd_actual` siempre aparece como grupo aunque no tenga ninguna sesion, que
    es lo que permite arrancar una desde un repo donde todavia no hubo ninguna.
    """
    grupos = {}
    for i, s in enumerate(vistas):
        grupos.setdefault(_ruta_corta(s), []).append(i)
    if cwd_actual and cwd_actual not in grupos:
        grupos[cwd_actual] = []

    def recencia(indices):
        if not indices:
            return -1.0  # un grupo vacio es el directorio actual: va primero
        d = edad_dias(vistas[indices[0]])
        return d if d is not None else 1e9

    filas, navegables, vacios = [], [], set()
    for pos, (ruta, indices) in enumerate(
            sorted(grupos.items(), key=lambda kv: recencia(kv[1]))):
        if pos:
            filas.append(("blanco", None))
        navegables.append(len(filas))
        filas.append(("grupo", ruta))
        if not indices:
            vacios.add(ruta)
        for i in indices:
            navegables.append(len(filas))
            filas.append(("sesion", i))
    return filas, navegables, vacios


def _dibujar_ui(stdscr, vistas, filas, navegables, cursor, marcadas, filtro,
                archivadas, estado, todos, cwd_actual, vacios):
    alto, ancho = stdscr.getmaxyx()
    stdscr.erase()
    home = "todos los perfiles" if todos else (
        os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    titulo = f" CODEX_HOME: {home}  {len(vistas)} sesiones"
    resumen = resumen_estados(vistas)
    if resumen:
        titulo += f"  \u2014 {resumen}"
    if marcadas:
        titulo += f" \u00b7 {len(marcadas)} marcadas"
    stdscr.addnstr(0, 0, _recortar(titulo, ancho - 1), max(1, ancho - 1), curses.A_BOLD)
    stdscr.addnstr(1, 0, "-" * max(1, ancho - 1), max(1, ancho - 1))
    cuerpo = max(1, alto - 5)
    fila_cursor = navegables[cursor] if navegables else 0
    inicio = max(0, min(fila_cursor - cuerpo + 1, max(0, len(filas) - cuerpo)))
    y = 2
    for idx, (tipo, valor) in enumerate(filas[inicio:inicio + cuerpo], inicio):
        if y >= alto - 3:
            break
        elegido = idx == fila_cursor
        if tipo == "blanco":
            pass
        elif tipo == "grupo":
            sufijo = "  (aqui)" if valor == cwd_actual else ""
            if valor in vacios:
                sufijo += "  sin sesiones \u2014 Enter para crear una"
            stdscr.addnstr(y, 0,
                           _recortar(f"{'>' if elegido else ' '} {valor}{sufijo}", ancho - 1),
                           max(1, ancho - 1),
                           curses.A_BOLD | (curses.A_REVERSE if elegido else 0))
        else:
            ses = vistas[valor]
            dias = edad_dias(ses)
            edad = f"{dias:.0f}d" if dias is not None else "?d"
            marca = "*" if ses.get("id") in marcadas else " "
            perfil = (str(ses.get("_perfil", ""))[:9] + " ") if todos else ""
            # El aviso se reserva ANTES de recortar el titulo: agregarlo despues
            # hace que el addnstr posterior lo corte y no se vea nunca.
            aviso = ("  d otra vez para borrar"
                     if elegido and estado.startswith("d otra") else "")
            hueco = max(10, ancho - 1 - _ancho(aviso))
            est = estado_sesion(ses)
            texto = (f"{'>' if elegido else ' '}{marca} {corto(ses):8} {perfil}"
                     f"{edad:>4}  {est:10} {_limpiar(_texto_sesion(ses))}")
            # Recorte por columnas, no por caracteres: un emoji ocupa dos y
            # contarlo como uno corre toda la fila hacia la derecha.
            texto = _recortar(texto, hueco)
            if aviso:
                texto += " " * max(0, hueco - _ancho(texto)) + aviso
            stdscr.addnstr(y, 0, texto, max(1, ancho - 1),
                           curses.A_REVERSE if elegido else 0)
        y += 1
    stdscr.addnstr(alto - 3, 0, "-" * max(1, ancho - 1), max(1, ancho - 1))
    modo = "archivadas" if archivadas else "activas"
    stdscr.addnstr(alto - 2, 0, f"filtro ({modo}): {filtro}_", max(1, ancho - 1))
    en_grupo = bool(navegables) and filas[fila_cursor][0] == "grupo"
    ayuda = ("Enter sesion nueva aqui \u00b7 j/k mover \u00b7 / filtrar \u00b7 q salir"
             if en_grupo else
             "Enter abrir \u00b7 p preview \u00b7 d borrar \u00b7 a archivar \u00b7 / filtrar")
    stdscr.addnstr(alto - 1, 0, ayuda, max(1, ancho - 1), curses.A_DIM)
    if estado and not estado.startswith("d otra"):
        stdscr.addnstr(alto - 4, 0, estado, max(1, ancho - 1), curses.A_BOLD)
    stdscr.refresh()


def _ui(stdscr, servidores, todos):
    curses.curs_set(0)
    stdscr.keypad(True)
    # Nombrar la ventana propia: en la barra de tmux queda "0:sesiones" en vez
    # de "0:python3.12", asi se ve —y con el mouse se clickea— para volver aca
    # desde la ventana donde corre Codex.
    if os.environ.get("TMUX"):
        try:
            subprocess.run(["tmux", "rename-window", "sesiones"],
                           capture_output=True, check=False)
        except OSError:
            pass
    archivadas, filtro, marcadas, estado, cursor = False, "", set(), "", 0
    def cargar():
        out = []
        for nombre, srv in servidores.items():
            try:
                for s in srv.sesiones(archivadas=archivadas):
                    s["_perfil"], s["_codex_home"], s["_srv"] = nombre, srv.home, srv
                    out.append(s)
            except Exception as e:
                nonlocal estado
                estado = f"{nombre}: no se pudo cargar"
        return out
    sesiones = cargar()
    # Directorio desde donde se abrio la vista: aparece como grupo aunque no
    # tenga sesiones, para poder arrancar una ahi.
    try:
        cwd_actual = _ruta_corta({"cwd": os.getcwd()})
    except OSError:
        cwd_actual = None
    while True:
        vistas = filtrar(sesiones, argparse.Namespace(
            grep=filtro, cwd=None, days=None, model=None))
        vistas.sort(key=lambda x: edad_dias(x) if edad_dias(x) is not None else 1e9)
        filas, navegables, vacios = _armar_filas(vistas, cwd_actual)
        cursor = min(cursor, max(0, len(navegables) - 1))
        _dibujar_ui(stdscr, vistas, filas, navegables, cursor, marcadas, filtro,
                    archivadas, estado, todos, cwd_actual, vacios)
        tecla = stdscr.getch(); previo = estado; estado = ""

        # Que hay bajo el cursor: un encabezado de directorio, o una sesion.
        tipo, valor = filas[navegables[cursor]] if navegables else (None, None)
        actual = vistas[valor] if tipo == "sesion" else None

        if tecla in (ord("q"), ord("Q"), 3): return
        if tecla == curses.KEY_RESIZE: continue
        if tecla in (curses.KEY_DOWN, ord("j")):
            cursor = min(cursor + 1, max(0, len(navegables) - 1))
        elif tecla in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif tecla == curses.KEY_NPAGE:
            cursor = min(cursor + max(1, stdscr.getmaxyx()[0] - 5),
                         max(0, len(navegables) - 1))
        elif tecla == curses.KEY_PPAGE:
            cursor = max(0, cursor - max(1, stdscr.getmaxyx()[0] - 5))
        elif tecla == ord("g"): cursor = 0
        elif tecla == ord("G"): cursor = max(0, len(navegables) - 1)
        elif tecla == ord(" "):
            if actual is None:
                estado = "marcar no aplica a un directorio"
            else:
                marcadas.symmetric_difference_update({actual["id"]})
        elif tecla == ord("r") or tecla == ord("t"):
            if tecla == ord("t"): archivadas = not archivadas; cursor = 0
            sesiones = cargar(); marcadas.clear()
        elif tecla in (10, 13, curses.KEY_ENTER) and navegables:
            # Sobre un directorio, Enter arranca una sesion nueva ahi; sobre una
            # sesion, la reanuda. En ambos casos se sale de curses, corre Codex,
            # y al volver se recarga la lista.
            if actual is None:
                destino = os.path.expanduser(valor)
                if not os.path.isdir(destino):
                    estado = f"{valor} ya no existe"
                    continue
                cmd = [codex_bin()]
                entorno = dict(os.environ)
                if todos:
                    # Con varios perfiles no hay uno "actual": se usa el primero,
                    # que es el que la cabecera lista por defecto.
                    entorno["CODEX_HOME"] = next(iter(servidores.values())).home
                nombre = f"codex:{os.path.basename(destino.rstrip('/')) or 'nuevo'}"
            else:
                destino = actual.get("cwd") or None
                cmd = [codex_bin(), "resume", actual["id"]]
                entorno = dict(os.environ, CODEX_HOME=actual["_codex_home"])
                # Nombre corto y legible: en la barra de tmux se lee mejor
                # "codex:Saludo inicial" que el UUID.
                titulo_corto = " ".join(_texto_sesion(actual).split()[:3])[:22]
                nombre = f"codex:{titulo_corto or corto(actual)}"
            if os.environ.get("TMUX"):
                # La vista no se toca: Codex abre en otra ventana de tmux.
                _, aviso = lanzar_codex(cmd, destino, entorno, nombre,
                                        actual["id"] if actual else None)
                estado = aviso
                sesiones = cargar(); marcadas.clear()
            else:
                stdscr.clear(); curses.endwin()
                try:
                    _, aviso = lanzar_codex(cmd, destino, entorno, nombre,
                                            actual["id"] if actual else None)
                finally:
                    stdscr.refresh()
                estado = aviso
                sesiones = cargar(); marcadas.clear()
        elif tecla == ord("p"):
            if actual is None:
                estado = "no hay preview de un directorio"
            else:
                _preview_ui(stdscr, actual["_srv"], actual)
        elif tecla == ord("/"):
            buffer = filtro
            while True:
                previas = filtrar(sesiones, argparse.Namespace(
                    grep=buffer, cwd=None, days=None, model=None))
                previas.sort(key=lambda x: edad_dias(x) if edad_dias(x) is not None else 1e9)
                f2, n2, v2 = _armar_filas(previas, cwd_actual)
                _dibujar_ui(stdscr, previas, f2, n2, 0, marcadas, buffer,
                            archivadas, "filtrando \u2014 Enter fija, Esc cancela",
                            todos, cwd_actual, v2)
                k = stdscr.getch()
                if k in (10, 13, curses.KEY_ENTER): filtro = buffer; break
                if k == 27: break
                if k in (curses.KEY_BACKSPACE, 127, 8): buffer = buffer[:-1]
                elif k == curses.KEY_RESIZE: continue
                elif 32 <= k < 127: buffer += chr(k)
            cursor = 0
        elif tecla == 27: filtro = ""
        elif tecla == ord("a"):
            elegidas = [s for s in vistas if s["id"] in marcadas]
            if not elegidas and actual is not None:
                elegidas = [actual]
            if not elegidas:
                estado = "archivar no aplica a un directorio"
                continue
            for s in elegidas:
                ok, err = archivar(s["_srv"], s, not archivadas)
                if ok: sesiones.remove(s); marcadas.discard(s["id"])
                else: estado = err or "fallo al archivar"
        elif tecla == ord("d"):
            elegidas = [s for s in vistas if s["id"] in marcadas]
            if not elegidas and actual is not None:
                elegidas = [actual]
            if not elegidas:
                estado = "borrar no aplica a un directorio"
                continue
            armado = previo.startswith("d otra")
            if armado:
                for s in elegidas:
                    ok, err = borrar(s["_srv"], s)
                    if ok: sesiones.remove(s); marcadas.discard(s["id"])
                    else: estado = err or "fallo al borrar"
            else:
                estado = f"d otra vez para borrar {len(elegidas)} sesiones" if len(elegidas) > 1 else "d otra vez para borrar"


def _hay_tmux():
    return shutil.which("tmux") is not None


def _sesion_tmux_actual():
    try:
        return subprocess.run(["tmux", "display-message", "-p", "#{session_name}"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def _pid_con_writer(codex_home, thread_id):
    """El proceso que tiene tomado el writer de esa sesion, si hay alguno.

    Codex mantiene un lock por sesion en <CODEX_HOME>/thread-writer-locks/.
    Mirarlo cubre casos que la marca de tmux no puede: una sesion **creada**
    desde la vista (Enter sobre un directorio) todavia no tenia id cuando se
    abrio su ventana, asi que quedo sin marcar.
    """
    if not (codex_home and thread_id):
        return None
    lock = os.path.join(codex_home, "thread-writer-locks", f"{thread_id}.lock")
    if not os.path.exists(lock):
        return None
    try:
        r = subprocess.run(["lsof", "-t", lock], capture_output=True, text=True)
    except OSError:
        return None
    pids = [int(x) for x in r.stdout.split() if x.isdigit()]
    return pids[0] if pids else None


def _ventana_del_pid(pid):
    """La ventana de tmux en cuyo arbol de procesos vive ese pid."""
    if not pid:
        return None
    try:
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid}\t#{window_id}\t#{session_name}"],
            capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    panes = {}
    for linea in r.stdout.splitlines():
        partes = linea.split("\t")
        if len(partes) >= 3 and partes[0].isdigit():
            panes[int(partes[0])] = (partes[1], partes[2])
    # subir por los padres hasta dar con el proceso de algun pane
    visto, actual = set(), pid
    while actual and actual > 1 and actual not in visto:
        visto.add(actual)
        if actual in panes:
            return panes[actual]
        try:
            salida = subprocess.run(["ps", "-o", "ppid=", "-p", str(actual)],
                                    capture_output=True, text=True).stdout.strip()
            actual = int(salida) if salida.isdigit() else None
        except (OSError, ValueError):
            return None
    return None


def _ventana_de(thread_id):
    """La ventana de tmux donde ya corre esa sesion, si existe.

    Al crearla se le pone una opcion `@cx_session` con el id: buscar por nombre
    no sirve porque el nombre es el titulo, que puede repetirse o cambiar.
    """
    if not (thread_id and os.environ.get("TMUX")):
        return None
    # -a: todas las sesiones de tmux, no solo esta. La misma sesion de Codex
    # puede estar abierta desde otra sesion de tmux (otra terminal, o una
    # adjunta por ssh), y ahi tampoco se puede abrir dos veces.
    try:
        r = subprocess.run(
            ["tmux", "list-windows", "-a", "-F",
             "#{window_id}\t#{session_name}\t#{@cx_session}"],
            capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    actual = _sesion_tmux_actual()
    for linea in r.stdout.splitlines():
        partes = linea.split("\t")
        if len(partes) < 3:
            continue
        wid, sesion, sid = partes[0], partes[1], partes[2].strip()
        if sid == thread_id:
            return (wid, sesion == actual, sesion)
    return None


def lanzar_codex(cmd, cwd, entorno, nombre, thread_id=None):
    """Corre Codex de forma que volver a la vista no termine la sesion.

    En primer plano (subprocess.call) el proceso *es* la sesion: salir con
    Ctrl+D la mata. Dentro de tmux se abre una ventana aparte, asi que volver
    es cambiar de ventana y el agente sigue trabajando.

    Devuelve (volvio_solo, aviso): volvio_solo indica si hay que redibujar la
    vista porque Codex corrio en primer plano y ya termino.
    """
    env_flags = []
    for clave, valor in entorno.items():
        if clave == "CODEX_HOME":
            env_flags += ["-e", f"{clave}={valor}"]
    if not _hay_tmux():
        # Sin tmux no hay a donde desatacharse: se corre en primer plano, como
        # antes, y se avisa que salir de Codex termina la sesion.
        subprocess.call(cmd, cwd=cwd, env=entorno)
        return True, "sin tmux: al salir de Codex la sesion termina"
    if os.environ.get("TMUX"):
        # Si la sesion ya esta abierta, saltar a su ventana. Abrirla de nuevo
        # no funciona —Codex rechaza una sesion con writer activo— y la ventana
        # se cierra al instante, con lo que parece que Enter no hizo nada.
        # Primero la marca @cx_session; si no esta —tipico de una sesion creada
        # desde la vista, que no tenia id cuando se abrio su ventana— se busca
        # por el proceso que tiene tomado el writer.
        ya = _ventana_de(thread_id)
        if not ya:
            porproceso = _ventana_del_pid(
                _pid_con_writer(entorno.get("CODEX_HOME"), thread_id))
            if porproceso:
                wid, sesion_tmux = porproceso
                actual = _sesion_tmux_actual()
                ya = (wid, sesion_tmux == actual, sesion_tmux)
                # marcarla ahora, para no tener que rastrear el proceso de nuevo
                subprocess.run(["tmux", "set-window-option", "-t", wid,
                                "@cx_session", thread_id],
                               capture_output=True, check=False)
        if ya:
            wid, aqui, sesion_tmux = ya
            if aqui:
                subprocess.run(["tmux", "select-window", "-t", wid],
                               capture_output=True, check=False)
                return False, "esa sesion ya estaba abierta: te lleve a su ventana"
            return False, (f"ya esta abierta en la sesion tmux '{sesion_tmux}'; "
                           "Codex no permite abrirla dos veces")
        # Sin ventana pero con writer tomado: un Codex fuera de tmux.
        pid = _pid_con_writer(entorno.get("CODEX_HOME"), thread_id)
        if pid:
            return False, (f"la tiene abierta el proceso {pid}, fuera de tmux; "
                           "cerralo o usa otra sesion")
        # Ventana nueva en la misma sesion de tmux.
        tmux_cmd = ["tmux", "new-window", "-P", "-F", "#{window_id}", "-n", nombre]
        if cwd:
            tmux_cmd += ["-c", cwd]
        tmux_cmd += env_flags + ["--"] + cmd
        try:
            r = subprocess.run(tmux_cmd, check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, OSError) as e:
            return True, f"no pude abrir la ventana tmux: {e}"
        wid = r.stdout.strip()
        if wid and thread_id:
            # Marcar la ventana para poder volver a ella la proxima vez.
            subprocess.run(["tmux", "set-window-option", "-t", wid,
                            "@cx_session", thread_id],
                           capture_output=True, check=False)
        if wid:
            # Sin esto, un Codex que falla al arrancar cierra la ventana antes
            # de que se alcance a leer el error.
            subprocess.run(["tmux", "set-window-option", "-t", wid,
                            "remain-on-exit", "on"],
                           capture_output=True, check=False)
        return False, "Codex abrio en otra ventana \u2014 Ctrl-b p (o Ctrl-b 0) vuelve aca"
    # Fuera de tmux: sesion propia, attach; al desatachar se vuelve a la vista.
    sesion = f"codex-{nombre}"
    crear = ["tmux", "new-session", "-d", "-s", sesion]
    if cwd:
        crear += ["-c", cwd]
    crear += env_flags + ["--"] + cmd
    try:
        subprocess.run(crear, check=True, capture_output=True)
        subprocess.call(["tmux", "attach", "-t", sesion])
    except (subprocess.CalledProcessError, OSError) as e:
        return True, f"no pude crear la sesion tmux: {e}"
    return True, "Ctrl-b d vuelve aca sin cortar el trabajo"


def cmd_ui(servidores, todos=False):
    # Requisito de curses en Python para UTF-8: sin esto los acentos salen
    # partidos y el ancho de las filas se descuadra. Va antes de wrapper().
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    curses.wrapper(lambda stdscr: _ui(stdscr, servidores, todos))
