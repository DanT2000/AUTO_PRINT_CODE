@echo off
chcp 65001 >nul
rem AutoPrintCode в браузере: помощник печати (без окна) + страница http://127.0.0.1:8790
rem Помощник с окном консоли (видны сообщения): npm start
cd /d "%~dp0"
where node >nul 2>nul || (
  echo Нужен Node.js 22.18 или новее: https://nodejs.org/
  pause
  exit /b 1
)
if not exist node_modules (
  echo Первый запуск: установка библиотек...
  call npm ci || (pause & exit /b 1)
)
rem сборка страницы — меньше секунды; так после обновления (git pull) всегда свежая версия
call npm run build --silent >nul || (call npm run build & pause & exit /b 1)
rem помощник — без окна (conhost --headless): в панели задач только окно AutoPrintCode со своим значком.
rem Если помощник уже запущен, новый экземпляр просто откроет страницу и завершится.
start "" "%SystemRoot%\System32\conhost.exe" --headless node server/main.ts
