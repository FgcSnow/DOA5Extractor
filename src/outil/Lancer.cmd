@echo off
cd /d "%~dp0"
python interface.py
if errorlevel 1 pause
