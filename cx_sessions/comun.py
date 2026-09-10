import unicodedata
import sys
import os
import time
import json


def edad_dias(sesion):
    ts = sesion.get("updatedAt") or sesion.get("recencyAt") or sesion.get("createdAt")
    if not ts:
        return None
    try:  # ISO-8601, con o sin Z
        t = time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        try:
            t = float(ts) / (1000 if float(ts) > 1e11 else 1)
        except (TypeError, ValueError):
            return None
    return (time.time() - t) / 86400


def etiqueta(sesion):
    txt = sesion.get("name") or sesion.get("preview") or ""
    return " ".join(str(txt).split())[:60] or "(sin titulo)"


def corto(sesion):
    return str(sesion.get("id", ""))[:8]


def imprimir(sesiones, mostrar_ruta=False):
    if not sesiones:
        print("No hay sesiones que coincidan.")
        return
    home = os.path.expanduser("~")
    for s in sesiones:
        dias = edad_dias(s)
        edad = f"{dias:4.0f}d" if dias is not None else "   ?"
        cwd = str(s.get("cwd") or "")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        huerfana = " [huerfana]" if s.get("path") and not os.path.isfile(s["path"]) else ""
        print(f"  {corto(s)}  {edad}  {etiqueta(s):<60}  {cwd}{huerfana}")
        if mostrar_ruta:
            print(f"            {s.get('id')}  {s.get('path')}")
    print(f"\n{len(sesiones)} sesion(es).")


def filtrar(sesiones, args):
    home = os.path.expanduser("~")
    out = []
    for s in sesiones:
        if getattr(args, "cwd", None):
            objetivo = os.path.abspath(os.path.expanduser(args.cwd))
            if not str(s.get("cwd") or "").startswith(objetivo):
                continue
        if getattr(args, "days", None) is not None:
            d = edad_dias(s)
            if d is None or d > args.days:
                continue
        if getattr(args, "grep", None):
            aguja = args.grep.lower()
            heno = f"{s.get('name') or ''} {s.get('preview') or ''} {s.get('cwd') or ''}".lower()
            if aguja not in heno:
                continue
        if getattr(args, "model", None) and args.model not in str(s.get("model") or ""):
            continue
        _ = home
        out.append(s)
    return out


def _ruta_corta(sesion):
    cwd = str(sesion.get("cwd") or "")
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def resolver(sesiones, refs):
    """Prefijos de UUID -> sesiones. Un prefijo ambiguo es un error, no una apuesta."""
    elegidas, faltan = [], []
    for ref in refs:
        coinciden = [s for s in sesiones if str(s.get("id", "")).startswith(ref)]
        if not coinciden:
            faltan.append(ref)
        elif len(coinciden) > 1:
            print(f"'{ref}' coincide con {len(coinciden)} sesiones:", file=sys.stderr)
            for s in coinciden:
                print(f"    {s['id']}  {etiqueta(s)}", file=sys.stderr)
            faltan.append(ref)
        else:
            elegidas.append(coinciden[0])
    return elegidas, faltan


def explicar_error(msg, codex_home):
    """Traduce los errores crudos del app-server a algo accionable."""
    if "active writer" in msg:
        return ("la sesion esta abierta en otro proceso. Cerrala donde la tengas "
                "abierta, o reinicia el daemon: pkill -f "
                f"'{codex_home}/packages/standalone/current/codex app-server'")
    if "no rollout found" in msg:
        return ("fila huerfana: el .jsonl ya no esta. Limpiala con "
                "cx-sessions prune --orphans --apply")
    return msg


def _texto_sesion(sesion):
    # El nombre gana sobre el preview: si la sesion tiene titulo —puesto por
    # Codex o por `rename`— es mas util que el primer mensaje truncado.
    return " ".join(str(sesion.get("name") or sesion.get("preview") or "").split())

# Boilerplate que Codex inyecta alrededor del texto del usuario. Un titulo
# generado a partir de esto no dice nada, asi que esos mensajes se descartan.
_RELLENO = ("# AGENTS.md instructions", "<environment_context>",
            "<permissions instructions>", ">>> TRANSCRIPT START",
            "The following is the Codex agent history")


def _ancho(texto):
    """Columnas que ocupa en pantalla, no cantidad de caracteres.

    Un emoji o un ideograma ocupan dos columnas: contarlos como uno descuadra
    la fila entera hacia la derecha.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in texto)


def _recortar(texto, columnas):
    """Recorta a lo que entra en esas columnas, midiendo por ancho visual."""
    if columnas <= 0:
        return ""
    total, salida = 0, []
    for c in texto:
        w = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if total + w > columnas:
            break
        salida.append(c); total += w
    return "".join(salida)


def _limpiar(texto):
    """Una linea sin caracteres de control: un \n o un \t rompen el dibujo."""
    return " ".join(str(texto).split())
