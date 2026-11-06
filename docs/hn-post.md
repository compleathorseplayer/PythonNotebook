Title: Show HN: Python Notebook – a notebook for plain .py files with # %% cells
URL:   https://github.com/compleathorseplayer/PythonNotebook

First comment:

I wanted notebook-style work (run a cell, look at the output, change it, rerun) without .ipynb files, Jupyter, or a particular editor. pynb opens an ordinary Python script in the browser, splits it into cells at # %% lines, and runs them in a separate Python process. The file stays a normal script: clean diffs, and python analysis.py still runs it top to bottom.

  pip install git+https://github.com/compleathorseplayer/PythonNotebook.git
  pynb analysis.py

Some design choices:

- No dependencies. The server is http.server from the standard library and the editor (CodeMirror) is bundled.

- The worker is whichever Python fits the notebook: the active venv/conda environment, a .venv next to the file, or --python. pynb doesn't need installing into each environment.

- Output streams live, including tqdm progress bars, pandas tables and matplotlib figures (via a small inline backend). Stop sends SIGINT to the worker; Restart replaces it.

- The page saves before running, so tracebacks quote the real lines of the file.

It also suits coding agents. Without a notebook an agent tends to rerun the whole script after every edit, or split the work into stage files and assemble them later. With one # %% file the agent edits a cell, you rerun only that cell, and the loaded data stays in memory.

It is a port of a Julia version I wrote earlier (JuliaNotebook). Tested on macOS with Chrome, Python 3.10–3.13; Linux and Windows are untested, so reports are welcome. Write-up: https://medium.com/@compleathorseplayer/python-notebook-a-lightweight-notebook-for-you-and-your-ai-agent-6dcf85888156
