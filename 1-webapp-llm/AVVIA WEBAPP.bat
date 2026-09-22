@echo off
title Webapp Electronics - Assistente
cd /d "%~dp0chatbotLLM"
echo ============================================================
echo   Avvio webapp Electronics + Assistente
echo   Attendi qualche secondo, poi si apre il browser.
echo   Per SPEGNERE: chiudi questa finestra.
echo ============================================================
echo.
start "" http://localhost:8000
python server.py
pause
