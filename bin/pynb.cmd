@echo off
rem pynb FILE.py -- open a Python file with # %%%% cells as a notebook in the browser.
python "%~dp0..\pynb\server.py" %*
