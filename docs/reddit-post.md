I've been using a small tool of my own for exploratory work and have now put it on GitHub: **Python Notebook** (`pynb`).

https://github.com/compleathorseplayer/PythonNotebook

## What My Project Does

`pynb analysis.py` opens a notebook page in your browser for an ordinary Python script. Cells are marked with `# %%` comments, the convention that VS Code, Spyder and PyCharm already understand:

    # %% Load
    import pandas as pd
    df = pd.read_csv("data.csv")
    df.describe()

    # %% Plot
    import matplotlib.pyplot as plt
    df.plot()

You run cells one at a time or all at once, and output streams in live: prints, progress bars (tqdm), pandas tables as HTML, matplotlib figures inline. The last expression in a cell is displayed, as in Jupyter. Stop interrupts a running cell; Restart gives you a fresh Python.

The file on disk stays a plain script: there is no JSON, the diffs are clean, and `python analysis.py` still runs it top to bottom.

Some details:

- **No dependencies.** The server uses only the standard library and the editor (CodeMirror) is bundled. pynb doesn't need Jupyter or ipykernel.
- Cells run in a **separate Python process**, chosen automatically: the active venv/conda environment, or a `.venv` next to the notebook, or `--python /path/to/python`. So pynb itself doesn't have to be installed into each project's environment.
- Tracebacks point to the real line numbers in your file.
- Install: `pip install git+https://github.com/compleathorseplayer/PythonNotebook.git` (or `pipx install …`)

It also turns out to suit AI coding agents. Without a notebook, an agent either reruns the whole script after every change, reloading the data each time, or splits the work into separate files and stitches them back together at the end. With one `# %%` file, the agent edits a cell and you rerun just that cell, while the session keeps everything else in memory.

## Target Audience

People who like notebooks for exploration but dislike `.ipynb` files: data analysis, quick modelling and plotting, teaching. It's a personal tool that I use daily, but it is young (v0.1). I've tested it on macOS with Chrome, on Python 3.10–3.13. Linux and Windows *should* work but are untested, so reports are welcome.

## Comparison

- **Jupyter / JupyterLab:** a much bigger ecosystem (widgets, extensions, many languages). pynb keeps the file a plain `.py` and has no install footprint. If you need Jupyter's ecosystem, use Jupyter.
- **Jupytext:** pairs `.ipynb` with `.py` files but still runs on Jupyter. pynb has a single file and no Jupyter.
- **VS Code / PyCharm `# %%` cells:** same file format, but tied to the editor (VS Code's Interactive Window also needs ipykernel). pynb is a standalone browser page, so you can keep editing in any editor, or let an agent edit the file.
- **marimo:** reactive cells and a richer UI, but its own file structure (functions decorated with `@app.cell`). pynb is deliberately simpler: ordinary top-to-bottom scripts with comment markers.

It's a sibling of an earlier Julia version (Julia Notebook), which has had a good reception on Julia Discourse.

Longer write-up, including the agent workflow: [Python Notebook: a lightweight notebook for you and your AI agent](https://medium.com/@compleathorseplayer/python-notebook-a-lightweight-notebook-for-you-and-your-ai-agent-6dcf85888156)

Feedback welcome, especially from Linux and Windows users.
