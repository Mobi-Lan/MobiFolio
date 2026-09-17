@echo off
rem Release: copy the packaged app into release\, zip it, build the NSIS installer, write SHA256 sums. (ASCII only)
rem Prerequisite: build.cmd has produced dist-electron\MobiFolio-win32-x64.  Usage: release.cmd [--nopause]
chcp 65001 >nul
cd /d "%~dp0"
set "NOPAUSE="
if /i "%~1"=="--nopause" set "NOPAUSE=1"
set "SRC=dist-electron\MobiFolio-win32-x64"
if not exist "%SRC%\MobiFolio.exe" ( echo RUN build.cmd FIRST: %SRC% not found & goto :fail )
for /f "tokens=2 delims=:, " %%V in ('findstr /c:"\"version\"" app\package.json') do set "VER=%%~V"
set "OUT=release"
if not exist "%OUT%" mkdir "%OUT%"
rem 1) portable copy + zip
if exist "%OUT%\MobiFolio-win32-x64" rmdir /s /q "%OUT%\MobiFolio-win32-x64"
xcopy /e /i /q /y "%SRC%" "%OUT%\MobiFolio-win32-x64" >nul || goto :fail
if exist "%OUT%\MobiFolio-%VER%-portable.zip" del /q "%OUT%\MobiFolio-%VER%-portable.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\MobiFolio-win32-x64\*' -DestinationPath '%OUT%\MobiFolio-%VER%-portable.zip' -CompressionLevel Optimal" || goto :fail
echo portable zip OK
rem 1b) lite edition: single exe (build.cmd produces dist\MobiFolioLite.exe)
if exist "dist\MobiFolioLite.exe" ( copy /y "dist\MobiFolioLite.exe" "%OUT%\MobiFolioLite-%VER%.exe" >nul && echo lite exe OK ) else ( echo lite exe missing - run build.cmd )
rem 2) installer (electron-builder wraps the prepackaged folder; fuses/icon/metadata are kept as built)
cd app
call npm run installer
if errorlevel 1 ( cd .. & echo INSTALLER FAILED & goto :fail )
cd ..
rem 3) checksums
powershell -NoProfile -Command "Get-ChildItem '%OUT%\*.exe','%OUT%\*.zip' | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash + '  ' + $_.Name } | Set-Content -Encoding ascii '%OUT%\SHA256SUMS.txt'"
type "%OUT%\SHA256SUMS.txt"
echo RELEASE OK: %OUT%
if not defined NOPAUSE pause
exit /b 0
:fail
if not defined NOPAUSE pause
exit /b 1
