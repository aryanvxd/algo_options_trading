@echo off
rem Runs the platform CLI with the project's virtualenv, without the pip-generated algo.exe
rem (Windows Application Control can block freshly generated unsigned launchers).
"%~dp0.venv\Scripts\python.exe" -m trading_core %*
