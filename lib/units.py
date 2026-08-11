# -*- coding: utf-8 -*-
"""
수량 표현을 포장 개수로 환산한다.

고객은 "2키로", "한 근", "3 ขีด" 처럼 무게로 말하고 상품은 "1kg" 같은 포장 단위로 판다.
숫자만 그대로 개수로 쓰면 포장단위가 500g 인 상품에서 절반만 보내게 된다.
설계서가 unit_expr 을 기록하라고 한 이유가 이것이고, 실제 대화에서 "2kg" 가 나왔다.

환산이 딱 떨어지지 않으면 올리지 않는다. 개수를 확정하지 않고 되물어 고객이 고르게 한다.
올려서 청구하면 고객이 요청한 양보다 많은 금액이 조용히 나간다.

단위 낱말은 코드가 아니라 units 탭(lang | expr | type | grams)에 있다.
파이썬 상수로 두면 언어를 늘릴 수 없다. 태국 고객이 자주 쓰는 ขีด(100g)를
한국어 표에서는 알 수 없어 "5개"로 읽히고, 환산이 아예 일어나지 않아
요청과 전혀 다른 양이 조용히 나간다.
"""
import math
import re
import unicodedata

# 시트를 못 읽었을 때의 폴백. 지금까지 쓰던 한국어 값 그대로다.
DEFAULT_WEIGHT_G = {
    "kg": 1000, "킬로": 1000, "키로": 1000, "킬로그램": 1000,
    "g": 1, "그램": 1, "그람": 1,
    "근": 600, "관": 3750,
}
DEFAULT_COUNT_WORDS = {"개", "팩", "봉", "봉지", "박스", "통", "마리", "줄", "세트",
                       "판", "장", "포"}

# master_products 의 unit 은 "1kg", "500g" 같은 라틴 표기만 온다고 본다.
# 고객 발화가 아니라 우리가 관리하는 값이므로 언어 축을 타지 않는다.
PACK_G = {"kg": 1000, "g": 1}

_PACK = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z가-힣]+)")


def norm(word):
    """단위 낱말 비교용. 태국어는 같은 글자가 다른 코드포인트 조합으로 들어온다."""
    return unicodedata.normalize("NFC", str(word or "")).strip().lower()


class Units:
    """한 언어의 단위표. Catalog 가 lang 에 맞춰 하나 만들어 들고 다닌다."""

    def __init__(self, rows=None, lang="ko"):
        self.lang = lang
        # 한국어는 지금까지 코드가 알던 낱말을 바탕에 깔고 시트가 덮어쓴다.
        # 시트에 근(600g)이 빠져 있다고 "한 근"이 갑자기 1개로 읽히면
        # 지금 되던 것이 조용히 망가진다. 다른 언어는 시트가 전부다.
        base = norm(lang).startswith("ko")
        self.weight = dict(DEFAULT_WEIGHT_G) if base else {}
        self.count = set(DEFAULT_COUNT_WORDS) if base else set()

        for r in rows or []:
            if norm(r.get("lang")) != norm(lang):
                continue
            expr = norm(r.get("expr"))
            if not expr:
                continue
            kind = str(r.get("type") or "").strip().lower()
            if kind.startswith("w"):   # weight
                try:
                    self.weight[expr] = float(str(r.get("grams") or "").replace(",", ""))
                except (TypeError, ValueError):
                    continue
            elif kind.startswith("c"):  # count
                self.count.add(expr)

        # 시트에 해당 언어 행이 하나도 없으면 지금까지의 한국어 값으로 돈다.
        # 매칭이 통째로 죽는 것보다 낫다
        if not self.weight and not self.count:
            self.weight = dict(DEFAULT_WEIGHT_G)
            self.count = set(DEFAULT_COUNT_WORDS)

        # 라틴 무게 단위는 어느 언어에서나 통한다. 시트에 빠져도 kg/g 는 읽혀야 한다
        for k, v in PACK_G.items():
            self.weight.setdefault(k, v)

    # ------------------------------------------------------------------ 조회
    def grams(self, word):
        """단위 낱말 하나를 그램으로. 무게 단위가 아니면 None."""
        return self.weight.get(norm(word))

    def is_weight(self, expr):
        return self.grams(expr) is not None

    def is_count(self, expr):
        return norm(expr) in self.count

    def words(self):
        """수량만 있는 발화를 알아보기 위한 단위 낱말 전체. 긴 것부터."""
        return sorted(set(self.weight) | self.count, key=len, reverse=True)

    # ------------------------------------------------------------------ 환산
    def parse_pack(self, unit):
        """'1kg' → 1000. '500g' → 500. '1팩' 처럼 개수 단위면 None."""
        m = _PACK.search(str(unit or ""))
        if not m:
            return None
        g = self.grams(m.group(2))
        return float(m.group(1)) * g if g else None

    def resolve(self, quantity, unit_expr, unit):
        """반환값은 (포장 개수, 요청 설명, 선택지).

        고객이 무게로 말했고 상품도 무게로 포장돼 있을 때만 환산한다.
        둘 중 하나라도 개수 단위면 손대지 않는다.

        포장단위로 딱 떨어지면 개수를 돌려준다. 안 떨어지면 개수를 돌려주지 않고
        선택지만 돌려준다. 예전에는 여기서 조용히 올렸는데, 1kg 포장 24,000원짜리를
        "2.5키로" 로 주문하면 3개(72,000원)가 잡혀 요청한 60,000원보다 12,000원을
        더 청구하게 된다. 거래명세서에 근거를 적어둬도 고객이 안 읽으면 그대로 나간다.
        애초에 1kg 단위로만 파는 상품이라 2.5kg 라는 주문은 성립하지 않는다.
        올려서 더 청구할 게 아니라 되물어야 한다."""
        if quantity is None:
            return None, None, []

        grams_per_unit = self.grams(unit_expr)
        pack_g = self.parse_pack(unit)

        if grams_per_unit is None or not pack_g:
            # "3개" 처럼 개수로 말했거나, 포장단위가 무게가 아니다.
            # 다만 반 포장은 보낼 수 없으므로 소수 개수도 되물어야 한다
            q = float(quantity)
            if abs(q - round(q)) < 1e-9:
                return quantity, None, []
            return None, None, _around(q)

        want_g = quantity * grams_per_unit
        exact = want_g / pack_g
        asked = "요청 %s" % _fmt_g(want_g)
        if abs(exact - round(exact)) < 1e-9 and round(exact) >= 1:
            return int(round(exact)), asked, []
        return None, asked, _around(exact)

    def convert(self, quantity, unit_expr, unit):
        """예전 호출부를 위한 얇은 껍데기. 포장 개수와 설명만 본다."""
        packs, note, _ = self.resolve(quantity, unit_expr, unit)
        return packs, note

    def pack_label(self, packs, unit):
        """'2개(2kg)' 처럼 되물음에 쓸 표기. 무게 포장이 아니면 개수만 적는다."""
        pack_g = self.parse_pack(unit)
        return _fmt_g(pack_g * packs) if pack_g else ""

    def rounded_up(self, quantity, unit_expr, unit):
        """올림이 실제로 일어났는지. 플래그 판정에 쓴다."""
        if quantity is None or not self.is_weight(unit_expr):
            return False
        pack_g = self.parse_pack(unit)
        if not pack_g:
            return False
        exact = (quantity * self.grams(unit_expr)) / pack_g
        return abs(exact - math.ceil(exact - 1e-9)) > 1e-9


def _around(x):
    """딱 떨어지지 않는 개수의 위아래 정수. 0개는 팔 수 없으므로 뺀다."""
    lo = int(math.floor(float(x) + 1e-9))
    hi = int(math.ceil(float(x) - 1e-9))
    return [n for n in dict.fromkeys((lo, hi)) if n >= 1]


def _fmt_g(g):
    if g >= 1000 and abs(g / 1000 - round(g / 1000, 2)) < 1e-9:
        v = g / 1000
        return ("%gkg" % v)
    return "%gg" % g


# 시트를 못 읽는 자리(테스트·목 모드)에서 쓰는 기본 표.
DEFAULT = Units()


def convert(quantity, unit_expr, unit, units=None):
    return (units or DEFAULT).convert(quantity, unit_expr, unit)


def rounded_up(quantity, unit_expr, unit, units=None):
    return (units or DEFAULT).rounded_up(quantity, unit_expr, unit)


def is_weight(expr, units=None):
    return (units or DEFAULT).is_weight(expr)


def parse_pack(unit, units=None):
    return (units or DEFAULT).parse_pack(unit)
