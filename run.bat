@echo off
rem Запуск Cutter из исходников. Первый запуск создаст venv и поставит зависимости.
cd /d "%~dp0"
set "PIP_CACHE_DIR=%~dp0.pip-cache"
set "TMP=%~dp0.tmp"
set "TEMP=%~dp0.tmp"
if not exist "%~dp0.tmp" mkdir "%~dp0.tmp"
if not exist "%~dp0.pip-cache" mkdir "%~dp0.pip-cache"

if not exist ".venv\Scripts\python.exe" (
    echo [Cutter] создаю venv...
    python -m venv .venv || goto :fail
    .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
)

echo [Cutter] запускаю...
.venv\Scripts\python.exe -m app.main %*
goto :eof

:fail
echo [Cutter] не получилось подготовить окружение. Поставь Python 3.11+ и попробуй снова.
pause
exit /b 1
