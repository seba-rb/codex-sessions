import argparse
import glob
import json
import os
import sys

from .appserver import AppServer, RpcError
from .comun import edad_dias, etiqueta, corto, imprimir, filtrar
from .estado import purgar_huerfanas
from .nombres import texto_del_usuario, pedir_titulo, escribir_nombre
from .vista import cmd_ui
import subprocess
from cx_sessions.comun import _texto_sesion
from cx_sessions.appserver import borrar
from cx_sessions.comun import resolver



# ── comandos ─────────────────────────────────────────────────────────

def cmd_ls(srv, args):
    sesiones = srv.sesiones(archivadas=args.archived)
    sesiones = filtrar(sesiones, args)
    sesiones.sort(key=lambda s: edad_dias(s) if edad_dias(s) is not None else 1e9)
    if args.limit:
        sesiones = sesiones[:args.limit]
    if args.json:
        print(json.dumps(sesiones, indent=2, ensure_ascii=False))
        return 0
    imprimir(sesiones, args.verbose)
    return 0


def confirmar(sesiones, verbo="borrar"):
    print(f"\nSe van a {verbo} {len(sesiones)} sesion(es). No se puede deshacer.")
    if not sys.stdin.isatty():
        print("No hay terminal interactiva; usa --yes para confirmar.", file=sys.stderr)
        return False
    return input("Continuar? [y/N]: ").strip().lower() in ("y", "yes", "s", "si")


def cmd_rm(srv, args):
    sesiones = srv.sesiones() + srv.sesiones(archivadas=True)
    elegidas, faltan = resolver(sesiones, args.refs)
    for ref in faltan:
        print(f"No encontre una sesion para '{ref}'.", file=sys.stderr)
    if not elegidas:
        return 1
    imprimir(elegidas)
    if not args.yes and not confirmar(elegidas):
        print("Cancelado.")
        return 0
    fallos = 0
    for s in elegidas:
        ok, err = borrar(srv, s)
        if ok:
            print(f"  borrada  {corto(s)}  {etiqueta(s)}")
        else:
            fallos += 1
            print(f"  FALLO    {corto(s)}  {err}", file=sys.stderr)
    return 1 if fallos else 0


def cmd_prune(srv, args):
    if args.older_than is None and not args.orphans:
        print("Decime que purgar: --older-than N y/o --orphans.", file=sys.stderr)
        return 2

    sesiones = srv.sesiones() + srv.sesiones(archivadas=True)
    huerfanas = [s for s in sesiones if s.get("path") and not os.path.isfile(s["path"])]
    viejas = []
    if args.older_than is not None:
        for s in sesiones:
            d = edad_dias(s)
            if d is not None and d > args.older_than and s not in huerfanas:
                viejas.append(s)

    if args.orphans and huerfanas:
        print(f"Huerfanas (fila sin rollout en disco): {len(huerfanas)}")
        imprimir(huerfanas)
    if args.older_than is not None:
        print(f"\nMas viejas que {args.older_than} dias: {len(viejas)}")
        imprimir(viejas)

    total = len(viejas) + (len(huerfanas) if args.orphans else 0)
    if not total:
        print("\nNada que purgar.")
        return 0
    if not args.apply:
        print("\nSolo diagnostico. Agrega --apply para ejecutarlo.")
        return 0
    if not args.yes and not confirmar(list(viejas) + (huerfanas if args.orphans else []), "purgar"):
        print("Cancelado.")
        return 0

    fallos = 0
    for s in viejas:
        ok, err = borrar(srv, s)
        print(f"  borrada  {corto(s)}  {etiqueta(s)}" if ok else f"  FALLO    {corto(s)}  {err}")
        fallos += 0 if ok else 1
    if args.orphans and huerfanas:
        n = purgar_huerfanas(srv.home, [s["id"] for s in huerfanas], True)
        print(f"  {n} fila(s) huerfana(s) eliminada(s) de la base")
        print("  Cerra y volve a abrir Codex para que recargue el listado.")
    return 1 if fallos else 0


def cmd_prune(srv, args):
    if args.older_than is None and not args.orphans:
        print("Decime que purgar: --older-than N y/o --orphans.", file=sys.stderr)
        return 2

    sesiones = srv.sesiones() + srv.sesiones(archivadas=True)
    huerfanas = [s for s in sesiones if s.get("path") and not os.path.isfile(s["path"])]
    viejas = []
    if args.older_than is not None:
        for s in sesiones:
            d = edad_dias(s)
            if d is not None and d > args.older_than and s not in huerfanas:
                viejas.append(s)

    if args.orphans and huerfanas:
        print(f"Huerfanas (fila sin rollout en disco): {len(huerfanas)}")
        imprimir(huerfanas)
    if args.older_than is not None:
        print(f"\nMas viejas que {args.older_than} dias: {len(viejas)}")
        imprimir(viejas)

    total = len(viejas) + (len(huerfanas) if args.orphans else 0)
    if not total:
        print("\nNada que purgar.")
        return 0
    if not args.apply:
        print("\nSolo diagnostico. Agrega --apply para ejecutarlo.")
        return 0
    if not args.yes and not confirmar(list(viejas) + (huerfanas if args.orphans else []), "purgar"):
        print("Cancelado.")
        return 0

    fallos = 0
    for s in viejas:
        ok, err = borrar(srv, s)
        print(f"  borrada  {corto(s)}  {etiqueta(s)}" if ok else f"  FALLO    {corto(s)}  {err}")
        fallos += 0 if ok else 1
    if args.orphans and huerfanas:
        n = purgar_huerfanas(srv.home, [s["id"] for s in huerfanas], True)
        print(f"  {n} fila(s) huerfana(s) eliminada(s) de la base")
        print("  Cerra y volve a abrir Codex para que recargue el listado.")
    return 1 if fallos else 0


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


def main():
    ap = argparse.ArgumentParser(
        prog="cx-sessions", description="Listar, borrar y purgar sesiones de Codex.")
    sub = ap.add_subparsers(dest="cmd")
    ui_parser = sub.add_parser("ui", help="abrir la vista interactiva")
    ui_parser.add_argument("--all-profiles", action="store_true", help="usar todos los perfiles")
    ap.add_argument("--all-profiles", action="store_true", help="usar todos los perfiles")

    def filtros(p):
        p.add_argument("--cwd", help="solo sesiones bajo este directorio")
        p.add_argument("--days", type=float, metavar="N", help="solo las de los ultimos N dias")
        p.add_argument("--grep", "-g", metavar="TEXTO", help="filtra por titulo, preview o cwd")
        p.add_argument("--model", help="filtra por modelo")

    p = sub.add_parser("ls", help="listar sesiones")
    filtros(p)
    p.add_argument("--archived", action="store_true", help="las archivadas en vez de las activas")
    p.add_argument("--limit", type=int, help="cortar a las N mas recientes")
    p.add_argument("--verbose", "-v", action="store_true", help="mostrar UUID y ruta completos")
    p.add_argument("--json", action="store_true", help="salida cruda")

    p = sub.add_parser("rm", help="borrar sesiones por id o prefijo")
    p.add_argument("refs", nargs="+", metavar="ID", help="UUID completo o prefijo")
    p.add_argument("--yes", "-y", action="store_true", help="sin confirmacion")

    p = sub.add_parser("prune", help="purgar sesiones viejas y filas huerfanas")
    p.add_argument("--older-than", type=float, metavar="N", help="borrar las de mas de N dias")
    p.add_argument("--orphans", action="store_true",
                   help="borrar filas cuyo rollout ya no esta en disco")
    p.add_argument("--apply", action="store_true", help="ejecutar (por defecto solo informa)")
    p.add_argument("--yes", "-y", action="store_true", help="sin confirmacion")

    p = sub.add_parser("rename", help="poner nombre a una sesion, o generarlos")
    p.add_argument("id", nargs="?", help="UUID o prefijo; sin esto, usa --auto")
    p.add_argument("nombre", nargs="?", help="el nombre nuevo")
    p.add_argument("--auto", action="store_true",
                   help="generar nombre para las sesiones que no tienen")
    p.add_argument("--apply", action="store_true",
                   help="ejecutar (por defecto solo informa)")

    args = ap.parse_args()
    if args.cmd is None:
        args.cmd = "ui"
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    todos = getattr(args, "all_profiles", False)
    homes = {os.path.basename(home.rstrip(os.sep)) or "default": home}
    if args.cmd == "ui" and todos:
        # Donde buscar CODEX_HOMEs adicionales. Por defecto ~/.codex-profiles/*,
        # que es la convencion de quien mantiene varias cuentas; se puede
        # cambiar con CODEX_SESSIONS_PROFILE_GLOB.
        patron = os.environ.get("CODEX_SESSIONS_PROFILE_GLOB",
                                os.path.expanduser("~/.codex-profiles/*"))
        homes = {os.path.basename(p.rstrip(os.sep)) or "default": p
                 for p in glob.glob(os.path.expanduser(patron)) if os.path.isdir(p)}
        homes["default"] = os.path.expanduser("~/.codex")
    if args.cmd == "ui" and not sys.stdin.isatty():
        print("Sin TTY; abriendo listado plano.", file=sys.stderr)
        args.cmd = "ls"
        args.archived = False
        args.limit = None
        args.verbose = False
        args.json = False
        args.cwd = args.days = args.grep = args.model = None
    if not os.path.isdir(home):
        print(f"CODEX_HOME no existe: {home}", file=sys.stderr)
        return 1
    print(f"CODEX_HOME: {home}")

    try:
        if args.cmd == "ui":
            servidores = {}
            for nombre, ruta in homes.items():
                if os.path.isdir(ruta):
                    try:
                        servidores[nombre] = AppServer(ruta).__enter__()
                    except Exception as e:
                        print(f"{nombre}: no se pudo abrir", file=sys.stderr)
            try:
                return cmd_ui(servidores, todos)
            finally:
                for srv in servidores.values():
                    srv.__exit__(None, None, None)
        with AppServer(home) as srv:
            return {"ls": cmd_ls, "rm": cmd_rm, "prune": cmd_prune,
                    "rename": cmd_rename}[args.cmd](srv, args)
    except RpcError as e:
        if e.metodo_desconocido:
            print(f"\nEsta version de Codex ya no expone {e.metodo}.\n"
                  "El protocolo del app-server cambio; hay que actualizar cx-sessions.",
                  file=sys.stderr)
        else:
            print(f"\n{e}", file=sys.stderr)
        return 1
    except (RuntimeError, KeyboardInterrupt) as e:
        print(f"\n{e or 'cancelado'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
