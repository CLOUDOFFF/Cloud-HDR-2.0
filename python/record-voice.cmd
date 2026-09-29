@echo off
chcp 65001 >nul
cd /d "%~dp0"
set COQUI_TOS_AGREED=1
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
powershell -NoProfile -Command "try { Invoke-RestMethod http://127.0.0.1:4482/voice/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 start "" "%~dp0voice-env\Scripts\pythonw.exe" "%~dp0cloudhdr_voice.py"
powershell -NoProfile -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-RestMethod http://127.0.0.1:4482/voice/health -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }"
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
if not exist "%EDGE%" set EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe
start "" "%EDGE%" --app=http://127.0.0.1:4482/record --window-size=860,940