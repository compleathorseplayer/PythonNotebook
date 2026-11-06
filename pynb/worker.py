"""
The pynb worker: runs notebook cells in its own Python process, so the server stays free to
stream output, and a cell can be interrupted (SIGINT) or the whole session restarted.

Protocol, one cell at a time:
  server -> worker   stdin:  one JSON line  {"code": ..., "line": firstline, "path": ...}
  worker -> server   stdout: whatever the cell prints, as it prints it, interleaved with
                             framed messages ending in a newline:  FRAME + json(msg)
                             msg = {"kind": "display", "item": ...}
                                 | {"kind": "done", "value": ..., "error": ..., "seconds": ...}
Both travel on stdout (stderr is the same pipe), so a cell's printed text always arrives
before its result.

Only the standard library is used, so this runs under whichever Python the notebook uses,
and needs Python 3.8 or later.
"""
import ast
import base64
import builtins
import importlib.util
import io
import json
import linecache
import os
import signal
import sys
import time
import tokenize
import traceback
import types

FRAME = "\x1ePYNB:"
MAX_TEXT = 50_000           # characters of a value's repr to show
BACKEND = "module://pynb_inline"
HERE = os.path.abspath(__file__)


def send(msg):
    sys.__stdout__.write(FRAME + json.dumps(msg) + "\n")
    sys.__stdout__.flush()


# ---------- rendering a value ----------
def is_figure(x):
    return type(x).__module__.startswith("matplotlib") and hasattr(x, "savefig")


def call_repr(x, name):
    if isinstance(x, type):                 # a class, e.g. pd.DataFrame itself
        return None
    method = getattr(x, name, None)
    if not callable(method):
        return None
    try:
        data = method()
    except NotImplementedError:
        return None
    return data[0] if isinstance(data, tuple) else data   # some return (data, metadata)


def render(x):
    try:
        if is_figure(x):
            buf = io.BytesIO()
            x.savefig(buf, format="png", bbox_inches="tight")
            return {"kind": "png", "data": base64.b64encode(buf.getvalue()).decode()}
        png = call_repr(x, "_repr_png_")
        if png:
            data = png if isinstance(png, str) else base64.b64encode(png).decode()
            return {"kind": "png", "data": data}
        html = call_repr(x, "_repr_svg_") or call_repr(x, "_repr_html_")
        if html:
            return {"kind": "html", "data": html}
        text = repr(x)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + f"\n… ({len(text) - MAX_TEXT} more characters)"
        return {"kind": "text", "data": text}
    except Exception as e:
        return {"kind": "error", "data": f"error while displaying value: {e!r}"}


def display(*objs):
    """Show each object below the cell: a plot as an image, a table as HTML, else as text."""
    for x in objs:
        send({"kind": "display", "item": render(x)})


# ---------- matplotlib: plots appear below the cell ----------
# A backend module matplotlib can import by name: drawing is matplotlib's own Agg backend;
# show() sends the open figures to the page. Figures still open when a cell ends are sent
# too, so a cell that only calls plt.plot(...) shows its plot.
def flush_figures():
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is None or plt.get_backend() != BACKEND:
        return
    for num in plt.get_fignums():
        display(plt.figure(num))
    plt.close("all")


class BackendLoader:
    """Import hook for the module `pynb_inline`, built when matplotlib asks for it."""

    def find_spec(self, name, path=None, target=None):
        if name == "pynb_inline":
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, mod):
        from matplotlib.backends import backend_agg
        mod.FigureCanvas = backend_agg.FigureCanvasAgg
        mod.FigureManager = backend_agg.FigureManager
        mod.show = lambda *args, **kwargs: flush_figures()


def install_backend():
    sys.meta_path.append(BackendLoader())
    os.environ["MPLBACKEND"] = BACKEND


# ---------- running a cell ----------
in_cell = False


def on_sigint(signum, frame):
    # Ctrl-C (SIGINT from the server's Stop button) may only land inside the user's code;
    # anywhere else it could cut a protocol message in half.
    if in_cell:
        raise KeyboardInterrupt


def ends_with_semicolon(code):
    last = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type not in (tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT,
                                tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
                last = tok
    except (tokenize.TokenError, SyntaxError):
        return False
    return last is not None and last.string == ";"


def format_error(e):
    if isinstance(e, KeyboardInterrupt):
        return "Interrupted."
    if isinstance(e, SyntaxError):         # the cell didn't parse: just say where
        return "".join(traceback.format_exception_only(type(e), e)).rstrip()
    tb = e.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename == HERE:
        tb = tb.tb_next                     # drop this worker's own frames
    return "".join(traceback.format_exception(type(e), e, tb)).rstrip()


def run_code(code, firstline, path, ns):
    # Blank lines in front, so line numbers in errors are the real lines in the file.
    tree = ast.parse("\n" * (firstline - 1) + code, filename=path)
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    exec(compile(tree, path, "exec"), ns)
    return eval(compile(last, path, "eval"), ns) if last is not None else None


def run_cell(code, firstline, path, ns):
    global in_cell
    t0 = time.time()
    value = err = None
    linecache.checkcache()                  # tracebacks quote the file as saved now
    signal.signal(signal.SIGINT, on_sigint)  # in case a cell replaced it
    ns["__file__"] = path
    try:
        try:
            in_cell = True
            result = run_code(code, firstline, path, ns)
        finally:
            in_cell = False
        if result is not None and not ends_with_semicolon(code):
            value = render(result)
            ns["_"] = result
            if is_figure(result):           # shown already; don't show it again below
                sys.modules["matplotlib.pyplot"].close(result)
    except BaseException as e:              # SystemExit too: exit() ends the cell, not Python
        in_cell = False
        err = format_error(e)
    try:
        flush_figures()
    except Exception as e:
        err = (err + "\n" if err else "") + f"error while showing plots: {e!r}"
    send({"kind": "done", "value": value, "error": err,
          "seconds": round(time.time() - t0, 2)})


def main():
    nbdir = sys.argv[1]
    sys.path[0] = nbdir                     # import modules next to the notebook, not next to us
    # Commands arrive on stdin. Keep a private copy of it, and give cells an empty stdin,
    # so input() fails cleanly instead of reading the next command.
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8")
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    sys.stdin = open(os.devnull, "r")

    # Cells run in a fresh __main__, so they cannot overwrite this worker's own names.
    main_mod = types.ModuleType("__main__")
    main_mod.__builtins__ = builtins
    sys.modules["__main__"] = main_mod
    builtins.display = display
    install_backend()
    signal.signal(signal.SIGINT, on_sigint)

    while True:
        try:
            line = commands.readline()
            if not line:
                return                      # the server has gone: stop with it
            cmd = json.loads(line)
            run_cell(cmd["code"], cmd["line"], cmd["path"], main_mod.__dict__)
        except KeyboardInterrupt:           # a Stop that arrived just after a cell finished
            continue


if __name__ == "__main__":
    main()
