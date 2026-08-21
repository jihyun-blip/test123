# -*- coding: utf-8 -*-
"""GEMINI_API_KEY 를 secrets.toml 에 넣고, 실제로 되는지 바로 확인한다.

키가 채팅이나 로그에 남지 않게 하려고 만들었다.

입력을 감추기만 하면 안 된다. cmd 창에서는 Ctrl+V 가 붙여넣기로 동작하지 않고
문자 하나(0x16)로 들어오는데, 화면에 아무것도 안 찍히니 붙여넣기가 됐는지조차
알 수 없다. 그래서 클립보드를 직접 읽는 길을 먼저 준다.

넣기만 하고 끝내지 않는다. 키를 바꾸는 이유가 "한도에 걸려서" 이므로,
바꾼 키가 진짜로 한도가 열려 있는지 그 자리에서 확인해야 의미가 있다.
"""
import io
import os
import re
import shutil
import subprocess
import sys

if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
SEC = os.path.join(BASE, ".streamlit", "secrets.toml")

CTRL_V = chr(0x16)
CTRL_C = chr(0x03)
BACKSPACE = (chr(0x08), chr(0x7F))
ENTER = (chr(0x0D), chr(0x0A))
PREFIX_KEYS = (chr(0x00), chr(0xE0))     # 방향키 등은 두 번에 나눠 들어온다


def mask(v):
    v = (v or "").strip()
    if len(v) <= 12:
        return "(너무 짧음)"
    return "%s...%s  (%d자)" % (v[:6], v[-3:], len(v))


def clipboard():
    """윈도우 클립보드 내용. 브라우저에서 키를 복사한 직후일 테니 이게 가장 확실하다."""
    # text=True 를 쓰면 콘솔 코드페이지(한국어 윈도우는 cp949)로 디코딩하는데,
    # 클립보드에 한글이 들어 있으면 그대로 UnicodeDecodeError 로 죽는다.
    # 실제로 그것 때문에 클립보드 경로가 통째로 건너뛰어졌다.
    # 바이트로 받아서 우리가 직접 푼다.
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard"],
            capture_output=True, timeout=15)
        raw = r.stdout or b""
    except Exception:
        return ""

    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return " ".join(raw.decode(enc).split()).strip()
        except Exception:
            continue
    return ""


def typed_input(prompt):
    """한 글자씩 받아 * 로 표시한다. 값은 감추되 몇 글자가 들어갔는지는 보인다."""
    try:
        import msvcrt
    except Exception:
        return input(prompt).strip()

    sys.stdout.write(prompt)
    sys.stdout.flush()
    buf = []
    while True:
        ch = msvcrt.getwch()
        if ch in ENTER:
            print()
            break
        if ch == CTRL_C:
            print()
            raise KeyboardInterrupt
        if ch == CTRL_V:
            pasted = clipboard()
            if pasted:
                buf.extend(pasted)
                sys.stdout.write("*" * len(pasted))
                sys.stdout.flush()
            continue
        if ch in BACKSPACE:
            if buf:
                buf.pop()
                sys.stdout.write(chr(0x08) + " " + chr(0x08))
                sys.stdout.flush()
            continue
        if ch in PREFIX_KEYS:
            msvcrt.getwch()
            continue
        buf.append(ch)
        sys.stdout.write("*")
        sys.stdout.flush()
    return "".join(buf).strip()


def ask_key():
    """클립보드를 먼저 권하고, 아니면 직접 입력받는다."""
    clip = clipboard()
    if clip and 20 <= len(clip) <= 200 and " " not in clip:
        print("  클립보드에 이런 값이 들어 있습니다:")
        print("      %s" % mask(clip))
        print()
        yn = input("  이 값을 쓸까요?  (Enter 또는 y = 사용 / n = 직접 입력) : ")
        # 거절만 명시적으로 받는다. 'ㅇ' 이나 'Y ' 처럼 뭘 눌러도 되게 한다.
        # 여기서 못 알아들으면 직접 입력 단계로 떨어지는데, 그게 지금 막힌 그 길이다.
        if yn.strip().strip("﻿").lower() not in ("n", "no", "ㅜ"):
            return clip
        print()
    elif clip:
        print("  클립보드에 뭔가 있지만 키 형식으로 보이지 않아 건너뜁니다.")
        print()

    print("  키를 붙여넣고 Enter 를 누르세요.")
    print("    - 마우스 오른쪽 클릭 = 붙여넣기 (cmd 창 기본 동작)")
    print("    - Ctrl+V 도 됩니다")
    print("    - 들어간 만큼 * 가 찍힙니다. 안 찍히면 안 들어간 것입니다")
    print("    - 그냥 Enter = 취소")
    print()
    return typed_input("  키 : ")


def check(key):
    """인증과 생성 호출을 각각 본다.

    인증만 보면 '키는 살아 있는데 하루 한도가 다 찬' 상태를 못 걸러낸다.
    지금 문제가 정확히 그 상태였으므로 생성까지 한 번 불러본다."""
    try:
        from google import genai
    except Exception as e:
        msg = ["google-genai 패키지가 없습니다 (%s)" % e,
               "     지금 파이썬: %s" % sys.executable,
               '     해결:  "%s" -m pip install google-genai' % sys.executable]
        return False, chr(10).join(msg)

    try:
        client = genai.Client(api_key=key)
        list(client.models.list())
    except Exception as e:
        return False, "인증 실패 - %s" % str(e)[:200]

    try:
        client.models.generate_content(model="gemini-3.5-flash", contents="ok")
    except Exception as e:
        m = str(e)
        if "RESOURCE_EXHAUSTED" in m or "quota" in m.lower():
            lim = re.search(r"limit:\s*(\d+)", m)
            return False, ("인증은 되는데 하루 한도가 이미 찼습니다%s. "
                           "결제가 붙은 프로젝트의 키인지 확인하세요."
                           % (" (한도 %s건)" % lim.group(1) if lim else ""))
        return False, "생성 호출 실패 - %s" % m[:200]

    return True, "정상입니다. 인증도 되고 생성 호출도 통과했습니다."


def main():
    if not os.path.exists(SEC):
        print("secrets.toml 이 없습니다: %s" % SEC)
        return 1

    print("=" * 58)
    print("  Gemini API 키 설정")
    print("=" * 58)
    print()

    try:
        key = ask_key()
    except (KeyboardInterrupt, EOFError):
        print("\n  취소했습니다.")
        return 0

    if not key:
        print("\n  취소했습니다. 아무것도 바꾸지 않았습니다.")
        return 0

    print("\n  입력받음: %s" % mask(key))
    if not key.startswith("AIza"):
        print("  참고: 보통의 Gemini API 키는 'AIza' 로 시작합니다.")
        print("        다른 형식이어도 동작할 수 있으니 아래 결과를 보세요.")

    # 되는 것만 파일에 쓴다. 안 되는 키로 덮어쓰면 원래 키까지 잃는다.
    print("\n  확인 중...")
    ok, msg = check(key)
    print("  %s" % msg)
    if not ok:
        print("\n  동작하지 않는 키라서 파일을 바꾸지 않았습니다.")
        return 1

    shutil.copy2(SEC, SEC + ".bak")
    text = io.open(SEC, encoding="utf-8").read()
    new, n = re.subn(r"(?m)^\s*GEMINI_API_KEY\s*=.*$",
                     'GEMINI_API_KEY = "%s"' % key, text, count=1)
    if not n:
        new = text.rstrip() + '\nGEMINI_API_KEY = "%s"\n' % key
    io.open(SEC, "w", encoding="utf-8").write(new)

    print("\n  저장했습니다: %s" % SEC)
    print("  이전 파일은 secrets.toml.bak 에 백업했습니다.")
    print("\n  이제 주소판독_실행.bat 을 돌리시면 새 키로 동작합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
