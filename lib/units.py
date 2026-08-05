# -*- coding: utf-8 -*-
"""
수량 표현을 포장 개수로 환산한다.

고객은 "2키로", "한 근" 처럼 무게로 말하고 상품은 "1kg" 같은 포장 단위로 판다.
숫자만 그대로 개수로 쓰면 포장단위가 500g 인 상품에서 절반만 보내게 된다.
설계서가 unit_expr 을 기록하라고 한 이유가 이것이고, 실제 대화에서 "2kg" 가 나왔다.

환산이 딱 떨어지지 않으면 임의로 버리지 않고 올린 뒤 플래그로 드러낸다.
고객이 요청한 무게와 실제 보낼 양이 다르면 사람이 봐야 한다.
"""
import math
import re

# 1단위당 그램. 근은 정육 기준 600g 이다.
WEIGHT_G = {
    "kg": 1000, "킬로": 1000, "키로": 1000, "킬로그램": 1000,
    "g": 1, "그램": 1, "그람": 1,
    "근": 600, "관": 3750,
}

# 개수로 세는 표현. 환산하지 않고 그대로 개수로 본다.
COUNT_WORDS = {"개", "팩", "봉", "봉지", "박스", "통", "마리", "줄", "세트", "판", "장", "포"}

_PACK = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z가-힣]+)")


def _unit_grams(word):
    """단위 낱말 하나를 그램으로. 무게 단위가 아니면 None."""
    w = str(word or "").strip().lower()
    return WEIGHT_G.get(w)


def parse_pack(pack_unit):
    """'1kg' → 1000. '500g' → 500. '1팩' 처럼 개수 단위면 None."""
    m = _PACK.search(str(pack_unit or ""))
    if not m:
        return None
    g = _unit_grams(m.group(2))
    return float(m.group(1)) * g if g else None


def is_weight(expr):
    return _unit_grams(expr) is not None


def convert(quantity, unit_expr, pack_unit):
    """반환값은 (포장 개수, 설명). 환산이 필요 없으면 (원래 수량, None).

    고객이 무게로 말했고 상품도 무게로 포장돼 있을 때만 환산한다.
    둘 중 하나라도 개수 단위면 손대지 않는다."""
    if quantity is None:
        return None, None

    grams_per_unit = _unit_grams(unit_expr)
    if grams_per_unit is None:
        return quantity, None          # "3개" 처럼 개수로 말함

    pack_g = parse_pack(pack_unit)
    if not pack_g:
        return quantity, None          # 포장단위가 무게가 아니거나 비어 있음

    want_g = quantity * grams_per_unit
    exact = want_g / pack_g
    packs = math.ceil(exact - 1e-9)
    packs = max(1, int(packs))

    asked = _fmt_g(want_g)
    if abs(exact - packs) < 1e-9:
        return packs, "요청 %s" % asked

    # 딱 떨어지지 않으면 올림한다. 얼마를 요청했고 얼마가 나가는지 문장에 남긴다.
    return packs, "요청 %s → %s %d개(%s)" % (
        asked, pack_unit, packs, _fmt_g(pack_g * packs))


def _fmt_g(g):
    if g >= 1000 and abs(g / 1000 - round(g / 1000, 2)) < 1e-9:
        v = g / 1000
        return ("%gkg" % v)
    return "%gg" % g


def rounded_up(quantity, unit_expr, pack_unit):
    """올림이 실제로 일어났는지. 플래그 판정에 쓴다."""
    if quantity is None or not is_weight(unit_expr):
        return False
    pack_g = parse_pack(pack_unit)
    if not pack_g:
        return False
    exact = (quantity * _unit_grams(unit_expr)) / pack_g
    return abs(exact - math.ceil(exact - 1e-9)) > 1e-9
