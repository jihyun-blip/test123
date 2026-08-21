@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 주소 이미지 판독 테스트
set /a EMPTY=0

rem 폴더를 이 파일 위에 끌어다 놓으면 그 폴더를 쓴다.
rem 그냥 더블클릭하면 옆에 있는 "이미지넣기" 폴더를 쓴다.
set "IMGDIR=%~1"
if "%IMGDIR%"=="" set "IMGDIR=%~dp0이미지넣기"

rem 이 PC 에는 파이썬이 여러 개 있고 google-genai 는 그중 하나에만 있다.
rem 아무 python 이나 부르면 "No module named 'google'" 로 죽는다.
set "PY="
call :pick python
if not defined PY call :pick "py -3.12"
if not defined PY call :pick "py -3"
if not defined PY call :pick py
if not defined PY (
  echo.
  where python >nul 2>nul
  if errorlevel 1 (
    echo   파이썬을 찾지 못했습니다.
    echo   python.org 에서 설치할 때 "Add Python to PATH" 를 체크하세요.
  ) else (
    echo   google-genai 패키지가 없습니다. 아래를 실행하세요:
    echo       python -m pip install google-genai
  )
  echo.
  pause
  exit /b 1
)

if not exist "%IMGDIR%" mkdir "%IMGDIR%"

:MENU
cls
echo ============================================================
echo    주소 이미지 판독 테스트
echo ============================================================
echo.
echo  이미지 폴더 : %IMGDIR%
set /a CNT=0
for /f %%n in ('dir /b /s /a-d "%IMGDIR%\*.jpg" "%IMGDIR%\*.jpeg" "%IMGDIR%\*.png" "%IMGDIR%\*.webp" "%IMGDIR%\*.bmp" "%IMGDIR%\*.gif" 2^>nul ^| find /c /v ""') do set /a CNT=%%n
echo  찾은 이미지 : %CNT% 장
set /a MINS=%CNT%*7/24/60
if %MINS% LSS 1 set /a MINS=1
echo  전체 예상    : 약 %MINS%분 ^(병렬 24개 기준^)
echo.

if %CNT%==0 (
  echo  ------------------------------------------------------------
  echo   이미지가 없습니다. 아래 폴더에 사진을 복사해 넣으세요.
  echo   날짜별 폴더로 나눠 넣어도 됩니다. 알아서 전부 훑습니다.
  echo   폴더를 열어 두겠습니다. 다 넣으신 뒤 아무 키나 누르세요.
  echo  ------------------------------------------------------------
  start "" "%IMGDIR%"
  pause >nul
  goto MENU
)

echo   1   먼저 300장만 시험   ^(몇 분. 속도와 정확도 감을 잡는다^)
echo   2   전체 돌리기         ^(%CNT%장 · 약 %MINS%분^)
echo   3   재현성 확인         ^(300장을 2회 돌려 답이 갈리는지 본다^)
echo   4   결과 폴더 열기
echo   5   지난 기록 지우고 처음부터
echo   0   종료
echo.
echo   * 중간에 창을 닫아도 됩니다. 다시 실행하면 남은 것부터 이어갑니다.
echo.
set "SEL="
set /p "SEL=번호를 누르고 Enter: "
if not defined SEL (
  set /a EMPTY+=1
  if !EMPTY! GEQ 3 (
    echo.
    echo  입력이 없어 종료합니다.
    exit /b 0
  )
  goto MENU
)
set "EMPTY=0"

if "%SEL%"=="1" (set "ARGS=--sample 300 --workers 24" & goto RUN)
if "%SEL%"=="2" (set "ARGS=--workers 24" & goto RUN)
if "%SEL%"=="3" (set "ARGS=--sample 300 --repeat 2 --workers 24" & goto RUN)
if "%SEL%"=="4" (
  if not exist "%~dp0out" mkdir "%~dp0out"
  start "" "%~dp0out"
  goto MENU
)
if "%SEL%"=="5" goto RESET
if "%SEL%"=="0" exit /b 0
goto MENU

:RUN
cls
echo ============================================================
echo  실행 중입니다. 창을 닫아도 되고, 다른 일 하셔도 됩니다.
echo  처리한 것은 그때그때 저장되고, 다시 실행하면 이어서 진행합니다.
echo ============================================================
echo.
%PY% batch_address.py "%IMGDIR%" %ARGS%
echo.
echo ============================================================
if exist "%~dp0out\결과.xlsx" (
  echo  결과 폴더를 엽니다.
  echo   - 결과.xlsx  전체 표. 원본파일경로를 누르면 그 사진이 열립니다
  echo   - 실패 폴더  어떤 사진이 안 되는지 눈으로 훑어보세요
  start "" "%~dp0out"
)
echo ============================================================
echo.
pause
goto MENU

:RESET
echo.
echo  진행 기록^(out\진행.jsonl^)과 분류 폴더를 지웁니다.
echo  다음 실행은 처음부터 다시 돌리며, 그만큼 시간과 비용이 다시 듭니다.
echo  원본 이미지는 건드리지 않습니다.
set "YN="
set /p "YN=정말 지울까요? (y/n): "
if /i not "%YN%"=="y" goto MENU
if exist "%~dp0out\진행.jsonl" del /q "%~dp0out\진행.jsonl"
for %%d in (성공 불완전 실패 원본불량 오류) do (
  if exist "%~dp0out\%%d" rmdir /s /q "%~dp0out\%%d"
)
echo  지웠습니다.
timeout /t 2 >nul
goto MENU

:pick
if defined PY exit /b
%~1 -c "from google import genai" >nul 2>nul
if errorlevel 1 exit /b
set "PY=%~1"
exit /b
