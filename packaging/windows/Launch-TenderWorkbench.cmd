@echo off
setlocal
cd /d "%~dp0"
if not defined LOCALAPPDATA (
  echo LOCALAPPDATA is required for the local environment and workspace.
  goto failed
)
set "TENDERWORKBENCH_VENV=%LOCALAPPDATA%\TenderWorkbench\python"
if not exist "%TENDERWORKBENCH_VENV%\Scripts\python.exe" (
  py -3.11 -m venv "%TENDERWORKBENCH_VENV%"
  if errorlevel 1 goto failed
)
if exist "%TENDERWORKBENCH_VENV%\requirements-installed.txt" (
  fc /b requirements.txt "%TENDERWORKBENCH_VENV%\requirements-installed.txt" >nul
  if not errorlevel 1 goto launch
)
"%TENDERWORKBENCH_VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
copy /y requirements.txt "%TENDERWORKBENCH_VENV%\requirements-installed.txt" >nul
if errorlevel 1 goto failed
:launch
"%TENDERWORKBENCH_VENV%\Scripts\python.exe" -m mesakumo %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo TenderWorkbench could not start. Check that Python 3.11 and package access are available.
pause
exit /b 1
