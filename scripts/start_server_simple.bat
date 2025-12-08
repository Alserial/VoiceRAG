@echo off
echo Starting VoiceRAG Server...
echo.
cd /d "%~dp0..\app\backend"
echo Current directory: %CD%
echo.
echo Starting server on http://localhost:8765
echo Press Ctrl+C to stop the server
echo.
python app.py
pause

