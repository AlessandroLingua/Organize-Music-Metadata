@echo off
rem Avvia Music Organizer senza finestra di terminale
set "DIR=%~dp0"
if not exist "%DIR%.venv\Scripts\pythonw.exe" (
    echo Ambiente non trovato. Esegui: uv sync
    pause
    exit /b 1
)
start "" "%DIR%.venv\Scripts\pythonw.exe" "%DIR%main.py"
