@echo off
rem Publish a built version to the public site (fo.mobimml.com). THIS IS THE ONLY STEP USERS SEE.
rem Building does not publish. Run this when you decide a build should go out.
rem   scripts\publish.cmd            -> publishes the version in app\package.json
rem   scripts\publish.cmd 0.2.5      -> publishes that version from release\r
chcp 65001 >nul
cd /d "%~dp0.."
set "VER=%~1"
if not defined VER for /f "tokens=2 delims=:, " %%V in ('findstr /c:"\"version\"" app\package.json') do set "VER=%%~V"
if not defined MF_UPDATE_BASE set "MF_UPDATE_BASE=https://fo.mobimml.com"
if not defined MF_PROMO_DIR set "MF_PROMO_DIR=%~dp0..\..\MobiFolio_promo"
if not exist "release\MobiFolioLite-%VER%.exe" ( echo NOT BUILT: release\MobiFolioLite-%VER%.exe & goto :fail )
echo.
echo   %VER% 를 %MF_UPDATE_BASE% 에 배포합니다.
echo   설치된 앱들이 이 판으로 자동 업데이트됩니다.
echo.
choice /c YN /n /m "진행할까요? (Y/N) "
if errorlevel 2 ( echo 취소했습니다. & goto :end )
set PYTHONUTF8=1
python scripts\publish_promo.py --version %VER% --base %MF_UPDATE_BASE% --promo "%MF_PROMO_DIR%" --publish
if errorlevel 1 goto :fail
:end
pause
exit /b 0
:fail
echo PUBLISH FAILED
pause
exit /b 1
