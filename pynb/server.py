"""
pynb: a minimal notebook for plain Python files with `# %%` cell markers.

The file on disk stays an ordinary .py file. The browser page shows one editor per cell.
Cells run in a separate Python worker process (worker.py): what a cell prints streams to
the page as it is printed, and its value is shown when it finishes (a plot as an image, a
table as HTML, anything else as text). Stop interrupts a cell; Restart starts a fresh worker.

Only the standard library is used. Start it with:   pynb analysis.py
"""
import argparse
import codecs
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
WORKER_SCRIPT = os.path.join(HERE, "worker.py")
MARKER = re.compile(r"^#\s?%%")
FRAME = b"\x1ePYNB:"


# ---------- file <-> cells ----------
# A new cell starts at every line beginning with `# %%` (or `#%%`); the marker line belongs
# to the cell it opens. Anything before the first marker is a cell of its own. Joining the
# cells with "\n" reproduces the file byte for byte.
def split_cells(src):
    cells, cur = [], []
    for line in src.split("\n"):
        if MARKER.match(line) and cur:
            cells.append("\n".join(cur))
            cur = []
        cur.append(line)
    cells.append("\n".join(cur))
    return cells


def join_cells(cells):
    return "\n".join(cells)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:     # newline="": keep \r\n as is
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ---------- which Python runs the cells ----------
def venv_python(root):
    for sub in ("bin/python", "Scripts/python.exe"):
        p = os.path.join(root, sub)
        if os.path.isfile(p):
            return p
    return None


def find_python(nbdir, requested=None):
    """The interpreter for the notebook's code: --python, else an active virtualenv or
    conda env, else a .venv or venv folder next to the notebook or above it, else python3."""
    if requested:
        return shutil.which(requested) or requested
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        p = os.environ.get(var) and (venv_python(os.environ[var]) or
                                     os.path.join(os.environ[var], "python.exe"))
        if p and os.path.isfile(p):
            return p
    d = nbdir
    while True:
        for name in (".venv", "venv"):
            p = venv_python(os.path.join(d, name))
            if p:
                return p
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return shutil.which("python3") or shutil.which("python") or sys.executable


def python_version(python):
    try:
        out = subprocess.run([python, "-c", "import sys; print(sys.version.split()[0])"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else None
    except OSError:
        return None


# ---------- the worker process ----------
def find_frame(buf):
    """Index of a frame start in buf, or of the start of one cut off at the end."""
    i = buf.find(FRAME[:1])
    while i >= 0:
        k = min(len(FRAME), len(buf) - i)
        if buf[i:i + k] == FRAME[:k]:
            return i
        i = buf.find(FRAME[:1], i + 1)
    return None


class Worker:
    def __init__(self, python, nbdir):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                   PYTHON_COLORS="0")
        self.proc = subprocess.Popen(
            [python, "-u", WORKER_SCRIPT, nbdir], cwd=nbdir, env=env, bufsize=0,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.events = queue.Queue()     # dicts for the page: out | display | done | died
        self.killed = False
        threading.Thread(target=self.pump, daemon=True).start()

    def alive(self):
        return self.proc.poll() is None

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def interrupt(self):
        if self.alive() and os.name != "nt":     # Windows has no SIGINT to send
            self.proc.send_signal(signal.SIGINT)

    def kill(self):
        if self.alive():
            self.killed = True
            self.proc.kill()

    # Read the worker's stdout, splitting it into printed text and framed messages.
    def pump(self):
        fd = self.proc.stdout.fileno()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")   # keeps split characters whole
        buf = b""

        def out(data, final=False):
            text = decoder.decode(data, final)
            if text:
                self.events.put({"kind": "out", "text": text})

        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            while buf:
                i = find_frame(buf)
                if i is None:                       # all plain text
                    out(buf)
                    buf = b""
                    break
                if i > 0:
                    out(buf[:i])
                    buf = buf[i:]
                j = buf.find(b"\n")
                if j < 0 or len(buf) < len(FRAME):  # rest not here yet
                    break
                self.events.put(json.loads(buf[len(FRAME):j]))
                buf = buf[j + 1:]
        out(buf, final=True)
        code = self.proc.wait()
        if self.killed:
            text = "Python was restarted before this cell finished."
        else:
            why = f"signal {-code}" if code < 0 else f"exit code {code}"
            text = f"The Python worker stopped ({why}). The next run starts a fresh one."
        self.events.put({"kind": "died", "text": text})


# ---------- session state ----------
class Session:
    def __init__(self, path, python):
        self.path = path
        self.python = python
        self.mtime = os.path.getmtime(path)   # when last loaded or saved, to catch edits made elsewhere
        self.token = secrets.token_urlsafe(18)
        self.generation = 0                   # bumped on every page load, so a reload cancels a pending shutdown
        self.runlock = threading.Lock()       # one cell at a time
        self.running = False
        self.interrupted = False              # at most one SIGINT per cell: a spare one would hit the next cell
        self.worker = self.new_worker()

    def new_worker(self):
        return Worker(self.python, os.path.dirname(self.path))

    def ensure_worker(self):
        if not self.worker.alive():
            self.worker = self.new_worker()
        return self.worker

    # Run one cell, calling `emit` with each event as it happens; returns when the cell is
    # done or the worker died (Stop then Restart, or a crash). If `emit` fails because the
    # page went away, keep reading until the cell finishes, so its output is not mistaken
    # for the next one's.
    def run_cell(self, emit, code, line):
        with self.runlock:
            w = self.ensure_worker()
            while not w.events.empty():         # stray output printed between cells
                w.events.get_nowait()
            self.running, self.interrupted = True, False
            listening = True
            try:
                try:
                    w.send({"code": code, "line": line, "path": self.path})
                except OSError:
                    pass                        # it just died; its "died" event is coming
                while True:
                    ev = w.events.get()
                    if listening:
                        try:
                            emit(ev)
                        except OSError:
                            listening = False
                    if ev["kind"] in ("done", "died"):
                        break
            finally:
                self.running = False

    def stop(self):
        if self.running and not self.interrupted and self.worker.alive():
            self.interrupted = True
            self.worker.interrupt()

    def restart(self):
        self.worker.kill()                      # a running cell ends with a "died" event
        with self.runlock:
            self.worker.kill()                  # a worker started meanwhile by a queued cell
            self.worker = self.new_worker()

    # Write the cells unless the file changed on disk since we last read or wrote it.
    def save(self, cells, force=False):
        if not force and os.path.isfile(self.path) and os.path.getmtime(self.path) != self.mtime:
            return False
        write_text(self.path, join_cells(cells))
        self.mtime = os.path.getmtime(self.path)
        return True

    def load(self):
        self.generation += 1
        self.mtime = os.path.getmtime(self.path)
        return {"path": self.path, "name": os.path.basename(self.path),
                "python": self.python, "cells": split_cells(read_text(self.path))}

    # The page was closed: stop, unless a page loads again soon (a reload sends bye, then load).
    def schedule_exit(self, grace=3):
        g = self.generation

        def check():
            if self.generation == g:
                print("pynb: browser window closed, stopping.", flush=True)
                self.worker.kill()
                os._exit(0)

        threading.Timer(grace, check).start()


# ---------- HTTP ----------
CONTENT_TYPES = {".js": "text/javascript", ".css": "text/css",
                 ".html": "text/html; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    server_version = "pynb"

    def log_message(self, *args):
        pass

    @property
    def session(self):
        return self.server.session

    def reply(self, status, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        elif not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s, url = self.session, urlsplit(self.path)
        if url.path.startswith("/assets/"):
            path = os.path.join(ASSETS, os.path.basename(url.path))   # basename: no path traversal
            if not os.path.isfile(path):
                return self.reply(404, "not found", "text/plain")
            ctype = CONTENT_TYPES.get(os.path.splitext(path)[1], "application/octet-stream")
            with open(path, "rb") as f:
                return self.reply(200, f.read(), ctype)
        if url.path == "/":
            if parse_qs(url.query).get("t", [""])[0] != s.token:
                return self.reply(403, "bad token", "text/plain")
            html = read_text(os.path.join(ASSETS, "index.html")).replace("__TOKEN__", s.token)
            return self.reply(200, html, "text/html; charset=utf-8")
        self.reply(404, "not found", "text/plain")

    def do_POST(self):
        s, p = self.session, urlsplit(self.path).path
        # Every API call must carry the token: the server runs arbitrary code, and any web
        # page open in the browser could otherwise post to localhost.
        if self.headers.get("X-Pynb-Token") != s.token:
            return self.reply(403, "bad token", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        body = json.loads(raw) if raw.strip() else {}

        if p == "/api/run":
            return self.stream_run(body)
        if p == "/api/load":
            return self.reply(200, s.load())
        if p == "/api/save":
            if not s.save(body["cells"], force=body.get("force", False)):
                return self.reply(409, {"ok": False, "reason": "changed"})
            return self.reply(200, {"ok": True})
        if p == "/api/stop":
            s.stop()
            return self.reply(200, {"ok": True})
        if p == "/api/restart":
            s.restart()
            return self.reply(200, {"ok": True})
        if p == "/api/bye":
            if body.get("cells") is not None:
                s.save(body["cells"])           # never overwrites a file changed elsewhere
            s.schedule_exit()
            return self.reply(200, {"ok": True})
        self.reply(404, "not found", "text/plain")

    # /api/run answers with newline-delimited JSON, one line per event, written as they happen.
    def stream_run(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(ev):
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()

        self.session.run_cell(emit, body["code"], int(body["line"]))


def bind(port):
    for p in range(port, port + 100):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue
    sys.exit(f"pynb: no free port between {port} and {port + 99}")


def serve(path, port=8765, python=None, open_browser=True):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        write_text(path, "")
    python = find_python(os.path.dirname(path), python)
    version = python_version(python)
    if version is None:
        sys.exit(f"pynb: could not run Python at {python!r}; name one with --python")
    s = Session(path, python)
    server = bind(port)
    server.session = s
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={s.token}"
    print(f"pynb: {os.path.basename(path)}  ->  {url}\n"
          f"      Python {version} ({python})\n"
          "(close the browser window, or Ctrl-C, to stop)", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            print("pynb: could not open a browser; open the URL above yourself.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        s.worker.kill()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pynb", description="Open a Python file with `# %%` cells as a notebook in the browser.")
    ap.add_argument("file", help="the .py file (created if it doesn't exist)")
    ap.add_argument("--python", help="the Python to run cells with (default: an active or "
                    "nearby virtualenv, else python3 on your PATH)")
    ap.add_argument("--port", type=int, default=8765, help="first port to try (default 8765)")
    args = ap.parse_args(argv)
    serve(args.file, args.port, args.python, open_browser=not os.environ.get("PYNB_NO_BROWSER"))


if __name__ == "__main__":
    main()
