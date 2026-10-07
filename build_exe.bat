@echo off
chcp 65001 >nul
rem Сборка AutoPrintCode.exe и файлов для релиза на GitHub. Результат — в папке dist\
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Нет окружения .venv. Создайте его и поставьте зависимости:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\build_exe.py %*
set "CODE=%ERRORLEVEL%"
rem запущен двойным щелчком — не закрывать окно, пока не прочитают итог
echo %CMDCMDLINE% | "%SystemRoot%\System32\find.exe" /i "%~nx0" >nul && pause
exit /b %CODE%
