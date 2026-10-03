@echo off
REM PHI PDF Studio - double-click to start on Windows. The first start installs everything into a .venv folder here.
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 is needed. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and start this file again.
  pause
  exit /b 1
)
REM A .venv made in another folder (this folder was moved) cannot run from here: install again.
if exist ".venv\Scripts\phi-pdf.exe" (
  ".venv\Scripts\phi-pdf.exe" --help >nul 2>nul || (
    echo This folder was moved: installing again...
    rmdir /s /q .venv
  )
)
if not exist ".venv\Scripts\phi-pdf.exe" (
  echo First start: installing ^(a few minutes, once^)...
  py -3 -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :failed
  ".venv\Scripts\python.exe" -m pip install --quiet ".[ai]" || goto :failed
  where tesseract >nul 2>nul || echo Tip: for OCR install Tesseract:  winget install UB-Mannheim.TesseractOCR
)
call :icon
".venv\Scripts\phi-pdf.exe"
exit /b 0

:icon
REM The Desktop icon, made again when it was deleted or was made for an older .venv (icon.txt goes with the .venv).
set "desktop="
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "desktop=%%d"
if exist ".venv\icon.txt" if exist "%desktop%\PHI PDF Studio.lnk" exit /b 0
".venv\Scripts\phi-pdf.exe" shortcut && (type nul > ".venv\icon.txt") || echo ^(The Desktop icon could not be made; run: .venv\Scripts\phi-pdf.exe shortcut^)
exit /b 0

:failed
echo Installation failed ^(see above^).
pause
exit /b 1
