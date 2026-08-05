# -*- coding: utf-8 -*-
"""업로드 이미지를 모델에 보내기 좋은 형태로 다듬는다.

휴대폰 사진은 4000×3000 에 3~5MB 다. 그대로 올리면 업로드 시간, 판독 시간,
토큰이 모두 커지는데 정확도는 그만큼 좋아지지 않는다. 라벨 글씨와 손글씨는
긴 변 1568px 이면 충분히 읽힌다.

EXIF 회전 보정도 여기서 한다. 휴대폰은 센서 방향 그대로 저장하고 회전값을
따로 적어두는데, 그 값을 무시하면 모델은 눕거나 뒤집힌 사진을 보게 된다.
"""
import io

MAX_SIDE = 1568   # 긴 변 상한. 라벨·손글씨 판독에 필요한 해상도는 남긴다
QUALITY = 85


def prepare(raw, mime="image/jpeg"):
    """(bytes, mime, note) 를 돌려준다. 실패하면 원본을 그대로 쓴다."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return raw, mime, ""

    try:
        img = Image.open(io.BytesIO(raw))
        # 촬영 방향을 화소에 실제로 반영한다. 이걸 빼면 모델이 누운 사진을 본다
        img = ImageOps.exif_transpose(img)
        w, h = img.size

        if max(w, h) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=QUALITY, optimize=True)
        out = buf.getvalue()

        # 줄여봤자 커지는 경우가 있다. 그럴 땐 원본이 낫다
        if len(out) >= len(raw) and max(w, h) <= MAX_SIDE:
            return raw, mime, ""

        note = "%dx%d %.1fMB → %dx%d %.1fMB" % (
            w, h, len(raw) / 1e6, img.size[0], img.size[1], len(out) / 1e6)
        return out, "image/jpeg", note
    except Exception:
        return raw, mime, ""
