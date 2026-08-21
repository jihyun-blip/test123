@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Gemini API 키 설정

rem 이 PC 에는 파이썬이 여러 개 있고(3.12, 3.14, 스토어판),
rem google-genai 는 그중 하나에만 설치돼 있다. 아무 python 이나 부르면
rem "No module named 'google'" 로 죽는다. 패키지가 있는 것을 골라 쓴다.
set "PY="
call :pick python
if not defined PY call :pick "py -3.12"
if not defined PY call :pick "py -3"
if not defined PY call :pick py
if not defined PY goto NOPKG

%PY% 키설정.py
echo.
pause
exit /b 0

:pick
if defined PY exit /b
%~1 -c "from google import genai" >nul 2>nul
if errorlevel 1 exit /b
set "PY=%~1"
exit /b

:NOPKG
echo.
echo   google-genai 패키지가 설치된 파이썬을 찾지 못했습니다.
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo   파이썬 자체가 없습니다. python.org 에서 설치할 때
  echo   "Add Python to PATH" 를 체크하세요.
) else (
  echo   파이썬은 있는데 패키지가 없습니다. 아래를 실행하세요:
  echo.
  echo       python -m pip install google-genai
)
echo.
pause
exit /b 1
