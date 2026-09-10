import json
import os

import subprocess
import threading
import time
from cx_sessions.comun import explicar_error

TIMEOUT = 30


def codex_bin():
    local = os.path.expanduser("~/.local/bin/codex")
    return local if os.path.exists(local) else "codex"


class AppServer:
    """Cliente JSON-RPC sobre un `codex app-server` de vida corta."""

    def __init__(self, codex_home):
        self.home = codex_home
        self.proc = None
        self._respuestas = {}
        self._siguiente_id = 100
        self._lock = threading.Lock()

    def __enter__(self):
        self.proc = subprocess.Popen(
            [codex_bin(), "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=dict(os.environ, CODEX_HOME=self.home), text=True, bufsize=1,
        )
        threading.Thread(target=self._leer, daemon=True).start()
        self.llamar("initialize", {"clientInfo": {
            "name": "cx-sessions", "title": "cx-sessions", "version": "1"}})
        return self

    def __exit__(self, *_):
        if self.proc:
            self.proc.kill()

    def _leer(self):
        for linea in self.proc.stdout:
            try:
                msg = json.loads(linea)
            except ValueError:
                continue
            if isinstance(msg.get("id"), int):
                with self._lock:
                    self._respuestas[msg["id"]] = msg

    def llamar(self, metodo, params=None):
        with self._lock:
            self._siguiente_id += 1
            rid = self._siguiente_id
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": metodo, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        limite = time.time() + TIMEOUT
        while time.time() < limite:
            with self._lock:
                if rid in self._respuestas:
                    msg = self._respuestas.pop(rid)
                    break
            if self.proc.poll() is not None:
                raise RuntimeError("el app-server se cerro antes de responder")
            time.sleep(0.02)
        else:
            raise RuntimeError(f"{metodo}: sin respuesta en {TIMEOUT}s")
        if "error" in msg:
            raise RpcError(metodo, msg["error"])
        return msg.get("result", {})

    def sesiones(self, archivadas=False):
        """Todas las paginas de thread/list, ya aplanadas."""
        out, cursor = [], None
        while True:
            params = {"limit": 100, "archived": archivadas}
            if cursor:
                params["cursor"] = cursor
            r = self.llamar("thread/list", params)
            out.extend(r.get("data", []))
            cursor = r.get("nextCursor")
            if not cursor:
                return out


class RpcError(Exception):
    def __init__(self, metodo, err):
        self.metodo, self.code = metodo, err.get("code")
        self.msg = err.get("message", "")
        super().__init__(f"{metodo}: [{self.code}] {self.msg}")

    @property
    def metodo_desconocido(self):
        return self.code == -32601 or "unknown variant" in self.msg


def borrar(srv, sesion):
    """thread/delete, con la CLI publica como red de seguridad."""
    try:
        srv.llamar("thread/delete", {"threadId": sesion["id"]})
        return True, None
    except RpcError as e:
        if not e.metodo_desconocido:
            return False, explicar_error(e.msg, srv.home)
        r = subprocess.run([codex_bin(), "delete", "--force", sesion["id"]],
                           capture_output=True, text=True,
                           env=dict(os.environ, CODEX_HOME=srv.home))
        if r.returncode == 0:
            return True, None
        return False, (r.stderr or r.stdout).strip()[:120]
