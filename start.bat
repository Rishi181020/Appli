@echo off
rem Appli: double-click to start. The first time it installs everything it needs (a few minutes).
cd /d "%~dp0"

if not exist .env if not exist shared.env (
  copy /y .env.example .env >nul
  echo.
  echo  First time: fill in the 2 Supabase lines at the top of .env ^(Notepad is opening it^),
  echo  save it, then double-click start.bat again.
  echo.
  start "" notepad .env
  pause
  exit /b
)

where python >nul 2>nul || (
  echo Python 3.11+ is required: https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  pause
  exit /b 1
)
where npm >nul 2>nul || (
  echo Node.js 18+ is required: https://nodejs.org/
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Setting up Python ^(first run only^)...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -r requirements.txt || (pause & exit /b 1)
  .venv\Scripts\python -m playwright install chromium || (pause & exit /b 1)
)

if not exist dashboard\node_modules (
  echo Installing dashboard dependencies ^(first run only^)...
  pushd dashboard
  call npm install --no-audit --no-fund
  popd
)
echo Building dashboard...
pushd dashboard
call npm run build
popd
.venv\Scripts\python -m runner serve
pause
