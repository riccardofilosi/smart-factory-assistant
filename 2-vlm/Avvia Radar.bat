@echo off
rem Avvia Radar — doppio click e parte il terminale con radar_live.
rem Il menu del programma chiede fase/commessa (nessun argomento necessario).
cd /d "%~dp0"
python src\radar\radar_live.py %*
echo.
pause
