import json
import os
import shutil
import sqlite3
import time

from .estado import _base_estado
import sys
from cx_sessions.comun import corto
from cx_sessions.comun import _RELLENO
from cx_sessions.comun import _texto_sesion
from cx_sessions.comun import resolver



def texto_del_usuario(path, limite=3):
    """Los primeros mensajes que escribio la persona, sin el relleno.

    Ojo: NO estan en los event_msg de tipo user_message —ahi solo hay prompts
    que inyecta el sistema— sino en los response_item con role "user".
    """
    fuera = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for linea in fh:
                if '"user"' not in linea:
                    continue
                try:
                    d = json.loads(linea)
                except ValueError:
                    continue
                if d.get("type") != "response_item":
                    continue
                pay = d.get("payload") or {}
                if pay.get("type") != "message" or pay.get("role") != "user":
                    continue
                partes = []
                for c in pay.get("content") or []:
                    t = c.get("text") if isinstance(c, dict) else None
                    if t:
                        partes.append(t)
                txt = " ".join(partes).strip()
                if not txt or any(r in txt[:200] for r in _RELLENO):
                    continue
                fuera.append(" ".join(txt.split())[:600])
                if len(fuera) >= limite:
                    break
    except OSError:
        return []
    return fuera


def pedir_titulo(texto):
    """Un titulo corto, via endpoint compatible con la API de OpenAI.

    Se configura por entorno para no atar la herramienta a ningun proveedor:
    CX_SESSIONS_NAMER_URL, CX_SESSIONS_NAMER_TOKEN, CX_SESSIONS_NAMER_MODEL.
    """
    import urllib.request
    url = os.environ.get("CX_SESSIONS_NAMER_URL")
    token = os.environ.get("CX_SESSIONS_NAMER_TOKEN")
    modelo = os.environ.get("CX_SESSIONS_NAMER_MODEL")
    cuerpo = json.dumps({
        "model": modelo,
        "messages": [
            {"role": "system", "content":
             "Resumi la tarea en un titulo de 3 a 6 palabras. Sin comillas, "
             "sin punto final, en el idioma del texto. Responde solo el titulo."},
            {"role": "user", "content": texto[:2000]},
        ],
        # Holgado a proposito: un modelo de razonamiento gasta tokens pensando
        # antes de responder, y con un limite chico devuelve content vacio.
        "max_tokens": 300,
    }).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions", data=cuerpo,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read())
    try:
        msg = d["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"respuesta inesperada: {str(d)[:120]}")
    # Solo `content`. `reasoning_content` NO sirve como titulo: es el monologo
    # del modelo ("We need to summarize the user's text into a title...") y
    # usarlo llena la base de basura. Algunos gateways devuelven una lista de
    # bloques en vez de una cadena.
    t = msg.get("content")
    if isinstance(t, list):
        t = " ".join(b.get("text", "") for b in t if isinstance(b, dict))
    t = (t or "").strip().strip('"').strip()
    t = t.lstrip("#").strip()  # algunos modelos titulan en markdown
    if not t:
        raise RuntimeError("el modelo no devolvio un titulo (content vacio)")
    t = " ".join(t.split())
    # Una respuesta larga es razonamiento que se colo, no un titulo.
    if len(t.split()) > 12:
        raise RuntimeError(f"la respuesta no parece un titulo: {t[:60]}...")
    return t[:60]


def escribir_nombre(codex_home, thread_id, nombre, respaldar=True):
    """Escribe la columna `name` de la tabla threads.

    Es el unico camino: el app-server no expone metodo para renombrar
    (`thread/setName` y `thread/name` no existen, responde unknown variant).
    """
    db = _base_estado(codex_home)
    if db is None:
        return False, "no encontre la base de sesiones"
    if respaldar:
        copia = f"{db}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
        if not os.path.exists(copia):
            shutil.copy2(db, copia)
    con = sqlite3.connect(db)
    try:
        with con:
            cur = con.execute("update threads set name = ? where id = ?",
                              (nombre, thread_id))
        return (cur.rowcount > 0), ("" if cur.rowcount else "no existe esa sesion")
    finally:
        con.close()


def cmd_rename(srv_o_servidores, args):
    servidores = (srv_o_servidores if isinstance(srv_o_servidores, dict)
                  else {"": srv_o_servidores})
    sesiones = []
    for nombre, srv in servidores.items():
        for s in srv.sesiones() + srv.sesiones(archivadas=True):
            s["_codex_home"] = srv.home
            sesiones.append(s)

    if args.id:
        elegidas, faltan = resolver(sesiones, [args.id])
        if not elegidas:
            print(f"No encontre una sesion para '{args.id}'.", file=sys.stderr)
            return 1
        ok, err = escribir_nombre(elegidas[0]["_codex_home"], elegidas[0]["id"],
                                  args.nombre)
        print(f"  {corto(elegidas[0])} -> {args.nombre}" if ok
              else f"  fallo: {err}")
        print("  Cerra y volve a abrir Codex para que recargue el listado.")
        return 0 if ok else 1

    faltantes = [s for s in sesiones if not (s.get("name") or "").strip()]
    print(f"sesiones sin nombre: {len(faltantes)} de {len(sesiones)}")
    if not faltantes:
        return 0

    if not all(os.environ.get(v) for v in
               ("CX_SESSIONS_NAMER_URL", "CX_SESSIONS_NAMER_TOKEN",
                "CX_SESSIONS_NAMER_MODEL")):
        print("\nPara generar nombres hace falta un endpoint compatible con la "
              "API de OpenAI.\nConfiguralo con:")
        print("  export CX_SESSIONS_NAMER_URL=https://tu-endpoint/v1")
        print("  export CX_SESSIONS_NAMER_TOKEN=...")
        print("  export CX_SESSIONS_NAMER_MODEL=...")
        print("\nTambien podes renombrar a mano: cx-sessions rename <id> \"Un titulo\"")
        return 0

    if not args.apply:
        for s in faltantes[:10]:
            print(f"  {corto(s)}  {_texto_sesion(s)[:60]}")
        if len(faltantes) > 10:
            print(f"  ... y {len(faltantes) - 10} mas")
        print("\nSolo diagnostico. Agrega --apply para generar los nombres.")
        return 0

    fallos = 0
    total = len(faltantes)
    for i, s in enumerate(faltantes, 1):
        # flush en cada linea: es una llamada por sesion y sin esto no se ve
        # ningun avance hasta el final.
        textos = texto_del_usuario(s.get("path") or "")
        if not textos:
            print(f"  [{i}/{total}] {corto(s)}  sin texto util, la salteo", flush=True)
            continue
        try:
            titulo = pedir_titulo("\n".join(textos))
        except Exception as e:  # una sesion que falla no aborta el lote
            fallos += 1
            print(f"  [{i}/{total}] {corto(s)}  fallo al generar: {str(e)[:70]}",
                  file=sys.stderr, flush=True)
            continue
        ok, err = escribir_nombre(s["_codex_home"], s["id"], titulo)
        print(f"  [{i}/{total}] {corto(s)}  -> {titulo}" if ok
              else f"  [{i}/{total}] {corto(s)}  {err}", flush=True)
        fallos += 0 if ok else 1
    print("\nCerra y volve a abrir Codex para que recargue el listado.")
    return 1 if fallos else 0
