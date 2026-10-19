@echo off
rem Double-clic : ouvre le dashboard (voir lancer.ps1). Les options passees ici vont au script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lancer.ps1" %*
if errorlevel 1 pause
