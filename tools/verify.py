# -*- coding: utf-8 -*-
"""
구조 전환 검증. API 키도 시트도 없이 돈다.

    python tools/verify.py

각 항목은 "이번 전환에서 깨질 수 있는 지점"을 하나씩 짚는다.
실패하면 무엇이 기대와 달랐는지 그 자리에서 보여준다.
"""
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 윈도 콘솔은 기본이 cp949 라 태국어를 출력하다 죽는다. 검증이 멈추면 안 된다
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lib import flags as FL          # noqa: E402
from lib import handoff as HO        # noqa: E402
from lib import llm as LLM           # noqa: E402
from lib import matching as M        # noqa: E402
from lib import policies as pol      # noqa: E402
from lib import reply as RP          # noqa: E402
from lib import units as U           # noqa: E402
from lib.order import OrderState     # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("%s %s%s" % ("  ok  " if cond else "  FAIL", name,
                       "" if cond else "\n         → %s" % detail))


# ------------------------------------------------------------------ 공통 도구
def policies(extra=None, lang="ko"):
    rows = [
        {"구분": "배송정책", "키": "FREE_SHIPPING_THRESHOLD", "값": "50000", "값유형": "숫자", "lang": ""},
        {"구분": "배송정책", "키": "SHIPPING_FEE", "값": "3000", "값유형": "숫자", "lang": ""},
        {"구분": "배송정책", "키": "SHIPPING_MIX_RULE", "값": "합산", "값유형": "제어값", "lang": ""},
        {"구분": "응대규칙", "키": "EXACT_NAME_PRIORITY", "값": "Y", "값유형": "제어값", "lang": ""},
        {"구분": "응대규칙", "키": "ASK_RETRY_LIMIT", "값": "2", "값유형": "숫자", "lang": ""},
        {"구분": "응대규칙", "키": "AMBIGUOUS_MAX_OPTIONS", "값": "5", "값유형": "숫자", "lang": ""},
        {"구분": "응대규칙", "키": "AMBIGUOUS_ATTR_THRESHOLD", "값": "20", "값유형": "숫자", "lang": ""},
        {"구분": "응대규칙", "키": "REQUIRED_FIELDS", "값": "수령인,전화,주소", "값유형": "제어값", "lang": ""},
        {"구분": "응대규칙", "키": "ASK_ADDRESS_DETAIL", "값": "권장", "값유형": "제어값", "lang": ""},
        {"구분": "응대규칙", "키": "AMOUNT_MISMATCH_ENFORCE", "값": "Y", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "AMBIGUOUS_ALIAS", "값": "되물음", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "PRODUCT_NOT_FOUND", "값": "되물음", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "SOLDOUT", "값": "되물음", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "MISSING_PRICE", "값": "차단", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "AMOUNT_MISMATCH", "값": "차단", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "PHONE_INVALID", "값": "미완료", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "RECEIVER_MISSING", "값": "미완료", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "ADDRESS_MISSING", "값": "미완료", "값유형": "제어값", "lang": ""},
        {"구분": "플래그", "키": "ADDRESS_DETAIL_MISSING", "값": "미완료", "값유형": "제어값", "lang": ""},
    ]
    rows += extra or []
    return pol.Policies(rows, lang=lang)


def read_csv(path):
    """검증은 판다스 없이도 돌아야 한다. 시트 폴백 CSV 를 직접 읽는다."""
    import csv
    full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
    with open(full, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class Field:
    def __init__(self, value, source="text"):
        self.value, self.source, self.source_ref, self.turn = value, source, "", 1

    def __bool__(self):
        return bool(self.value)


# ------------------------------------------------------------------ A2
def a2_label_and_channel():
    master = [
        {"item_code": "P001", "label_code": "L900", "canonical_name": "삼겹살",
         "species": "돼지", "part": "삼겹", "unit": "1kg", "cost": "7000",
         "product_code": "SKU-1", "erp_code": "ERP-1", "ship_type": "냉동",
         "soldout": "N", "is_active": "Y"},
        {"item_code": "P002", "label_code": "", "canonical_name": "소꼬리",
         "species": "소", "part": "꼬리", "unit": "1kg", "is_active": "Y"},
    ]
    prices = [
        {"country_code": "KR", "channel": "facebook", "item_code": "P001",
         "price": "10000", "rank": "1"},
        {"country_code": "KR", "channel": "platform", "item_code": "P001",
         "price": "12000", "rank": ""},
        {"country_code": "KR", "channel": "facebook", "item_code": "P002",
         "price": "24000", "rank": "2"},
        {"country_code": "TH", "channel": "facebook", "item_code": "P001",
         "price": "99999", "rank": "9"},
    ]
    names = [{"lang": "ko", "item_code": "P001", "display_name": "삼겹살"},
             {"lang": "th", "item_code": "P001", "display_name": "หมูสามชั้น"}]
    syn = [{"lang": "ko", "item_code": "P001", "synonym": "삼겹"},
           {"lang": "th", "item_code": "P001", "synonym": "สามชั้น"}]

    fb = M.Catalog(master, prices, names, syn, lang="ko", country_code="KR", channel="facebook")
    pf = M.Catalog(master, prices, names, syn, lang="ko", country_code="KR", channel="platform")
    P = policies()

    check("A2 라벨코드(L900)로 품목코드(P001) 를 찾는다",
          M.match({"label_code": "L900"}, fb, P).code == "P001",
          repr(M.match({"label_code": "L900"}, fb, P)))
    check("A2 품목코드를 라벨로 쓰지 않는다 (P001 은 라벨이 아니다)",
          M.match({"label_code": "P001"}, fb, P).status == M.NOT_FOUND,
          repr(M.match({"label_code": "P001"}, fb, P)))
    check("A2 label_code 가 비면 품목코드를 라벨로 폴백한다",
          M.match({"label_code": "P002"}, fb, P).code == "P002")
    check("A2 채팅에 적은 라벨코드도 같은 매핑을 쓴다",
          M.match({"name_hint": "L900 주세요"}, fb, P).code == "P001")
    check("A2 채널별로 다른 판매가",
          (fb.price("P001"), pf.price("P001")) == (10000, 12000),
          "%s / %s" % (fb.price("P001"), pf.price("P001")))
    check("A2 다른 나라 가격이 섞여 들어오지 않는다", fb.price("P001") == 10000)
    check("A2 rank 가 빈 채널은 다른 채널 rank 로 폴백",
          pf.rank("P001") == 1, pf.rank("P001"))
    check("A2 표시명은 언어 축", M.Catalog(master, prices, names, syn, lang="th").display("P001")
          == "หมูสามชั้น")
    check("A2 그 언어 표시명이 없으면 정식명으로 폴백",
          M.Catalog(master, prices, names, syn, lang="th").display("P002") == "소꼬리")
    check("A2 유사어는 선택한 언어만",
          M.Catalog(master, prices, names, syn, lang="th").by_synonym.get("สามชั้น") == ["P001"])


# ------------------------------------------------------------------ A3
def a3_whitelist():
    master = [{"item_code": "P001", "label_code": "L900", "canonical_name": "삼겹살",
               "species": "돼지", "part": "삼겹", "unit": "1kg",
               "cost": "7350", "product_code": "SKU-777", "erp_code": "ERP-888",
               "ship_type": "냉동", "soldout": "N", "is_active": "Y",
               "note": "내부 메모"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": "P001",
               "price": "10000", "rank": "1"}]
    cat = M.Catalog(master, prices, [], [])
    st = OrderState()
    user = LLM.build_user("삼겹살 주세요", st, cat, ["P001"], "full")

    for bad in ("7350", "SKU-777", "ERP-888", "내부 메모"):
        check("A3 프롬프트에 '%s' 가 없다" % bad, bad not in user,
              user)
    check("A3 표시명·가격은 그대로 실린다",
          "삼겹살" in user and "10000" in user, user)


# ------------------------------------------------------------------ A4
def a4_active_soldout():
    master = [
        {"item_code": "P001", "canonical_name": "삼겹살", "species": "돼지", "part": "삼겹",
         "unit": "1kg", "is_active": "Y", "soldout": "N", "ship_type": "냉동"},
        {"item_code": "P002", "canonical_name": "목살", "species": "돼지", "part": "삼겹",
         "unit": "1kg", "is_active": "Y", "soldout": "Y", "ship_type": "냉동"},
        {"item_code": "P003", "canonical_name": "항정살", "species": "돼지", "part": "삼겹",
         "unit": "1kg", "is_active": "N", "soldout": "N", "ship_type": "냉동"},
    ]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": c,
               "price": "10000", "rank": str(i + 1)}
              for i, c in enumerate(["P001", "P002", "P003"])]
    names = [{"lang": "ko", "item_code": c, "display_name": n}
             for c, n in (("P001", "삼겹살"), ("P002", "목살"), ("P003", "항정살"))]
    cat = M.Catalog(master, prices, names, [])
    P = policies()

    check("A4 is_active=N 은 카탈로그에서 빠진다", "P003" not in cat.items, list(cat.items))
    check("A4 is_active=N 은 매칭 후보로도 안 뜬다",
          M.match({"name_hint": "항정살"}, cat, P).status == M.NOT_FOUND)

    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "목살", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)
    check("A4 soldout=Y 는 주문 확정을 막는다", q["blocked"] and q["total"] is None, q)
    text, stage = RP.build(st, q, cat, P, [])
    check("A4 품절이면 대체 후보를 제시한다",
          stage == "soldout_ask" and "삼겹살" in text, "%s / %s" % (stage, text))
    fl = FL.evaluate(st, q, cat, P, {}, "full")
    check("A4 SOLDOUT 플래그가 뜬다", any(f.key == "SOLDOUT" for f in fl),
          [f.key for f in fl])
    check("A4 품절은 MISSING_PRICE 로 잘못 보고되지 않는다",
          not any(f.key == "MISSING_PRICE" for f in fl), [f.key for f in fl])


# ------------------------------------------------------------------ A5
def a5_shipping():
    master = [
        {"item_code": "F1", "canonical_name": "냉동상품", "unit": "1kg",
         "ship_type": "냉동", "is_active": "Y"},
        {"item_code": "R1", "canonical_name": "상온상품", "unit": "1kg",
         "ship_type": "상온", "is_active": "Y"},
    ]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": "F1",
               "price": "10000", "rank": "1"},
              {"country_code": "KR", "channel": "facebook", "item_code": "R1",
               "price": "5000", "rank": "2"}]
    ship = [{"country_code": "KR", "channel": "", "ship_type": "냉동",
             "fee": "3000", "free_threshold": "50000"},
            {"country_code": "KR", "channel": "", "ship_type": "상온",
             "fee": "2500", "free_threshold": "40000"}]
    names = [{"lang": "ko", "item_code": "F1", "display_name": "냉동상품"},
             {"lang": "ko", "item_code": "R1", "display_name": "상온상품"}]

    def order(cat, P):
        st = OrderState()
        st.apply({"item_ops": [{"op": "add", "name_hint": "냉동상품", "quantity": 1},
                               {"op": "add", "name_hint": "상온상품", "quantity": 1}]},
                 1, cat, P)
        st.rematch(cat, P, "full")
        return st, st.quote(cat, P)

    cat = M.Catalog(master, prices, names, [], shipping=ship)
    _, q = order(cat, policies())
    check("A5 합산 = 유형별로 각각 부과 (3000+2500)", q["shipping"] == 5500, q["shipping"])

    Pmax = policies([{"구분": "배송정책", "키": "SHIPPING_MIX_RULE", "값": "최대",
                      "값유형": "제어값", "lang": ""}])
    # 같은 키가 두 번이면 앞 행이 이긴다. 최대 규칙만 담은 표를 따로 만든다
    Pmax = pol.Policies([r for r in Pmax.rows if not (r["키"] == "SHIPPING_MIX_RULE"
                                                      and r["값"] == "합산")])
    _, q2 = order(cat, Pmax)
    check("A5 최대 = 비싼 쪽 하나만 (3000)", q2["shipping"] == 3000, q2["shipping"])

    st3, q3 = order(cat, policies())
    text = RP._invoice_text(q3, policies())
    check("A5 거래명세서에 유형별 배송비를 나눠 적는다",
          "냉동 배송비" in text and "상온 배송비" in text, text)

    # shipping 탭이 없으면 지금까지의 계산으로 폴백
    cat0 = M.Catalog(master, prices, names, [])
    _, q4 = order(cat0, policies())
    check("A5 shipping 탭이 없으면 SHIPPING_FEE 로 폴백", q4["shipping"] == 3000, q4["shipping"])

    # 유형별 무료배송 기준
    st5 = OrderState()
    st5.apply({"item_ops": [{"op": "add", "name_hint": "상온상품", "quantity": 9}]},
              1, cat, policies())
    st5.rematch(cat, policies(), "full")
    q5 = st5.quote(cat, policies())
    check("A5 유형별 무료배송 기준이 적용된다 (상온 45000 ≥ 40000)",
          q5["shipping"] == 0, q5["shipping"])


# ------------------------------------------------------------------ A6
def a6_near_index():
    """유사어 10만 행 규모에서 응답 시간과 결과 동일성."""
    import difflib

    def slow_near(expr, cat, top=3, floor=M.NEAR_FLOOR):
        best = {}

        def look(table):
            for word, codes in table.items():
                if not word:
                    continue
                r = difflib.SequenceMatcher(None, expr, word).ratio()
                for c in codes:
                    if r > best.get(c, 0):
                        best[c] = r

        look(cat.by_canonical)
        look(cat.by_synonym)
        for c in cat.items:
            name = cat.display(c)
            if name:
                r = difflib.SequenceMatcher(None, expr, name).ratio()
                if r > best.get(c, 0):
                    best[c] = r
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        return [c for c, r in ranked[:top] if r >= floor]

    rnd = random.Random(20260810)
    syllables = "가나다라마바사아자차카타파하고노도로모보소오조초코토포호"
    master, prices, names, syn = [], [], [], []
    for i in range(1000):
        code = "X%04d" % i
        base = "".join(rnd.choice(syllables) for _ in range(3))
        master.append({"item_code": code, "canonical_name": base + "살",
                       "species": "돼지", "part": "부위", "unit": "1kg", "is_active": "Y"})
        prices.append({"country_code": "KR", "channel": "facebook", "item_code": code,
                       "price": "10000", "rank": str(i + 1)})
        names.append({"lang": "ko", "item_code": code, "display_name": base + "살"})
        for j in range(100):
            syn.append({"lang": "ko", "item_code": code,
                        "synonym": base + "".join(rnd.choice(syllables) for _ in range(2))
                        + str(j)})

    t0 = time.time()
    cat = M.Catalog(master, prices, names, syn)
    cat_ms = (time.time() - t0) * 1000
    rows = len(syn) + len(names)

    probes = ["삼겹살", master[7]["canonical_name"], master[500]["canonical_name"][:2] + "살",
              "메기", syn[300]["synonym"]]

    # 색인은 카탈로그 하나당 한 번만 만든다. 조회 시간과 섞어 재면 안 된다
    t0 = time.time()
    cat.near_index()
    build_ms = (time.time() - t0) * 1000

    t0 = time.time()
    fast = [M.near_candidates(p, cat) for p in probes]
    fast_ms = (time.time() - t0) * 1000 / len(probes)

    t0 = time.time()
    slow = [slow_near(p, cat) for p in probes]
    slow_ms = (time.time() - t0) * 1000 / len(probes)

    check("A6 %d행 색인 결과가 전수 비교와 같다" % rows, fast == slow,
          "%s\n         vs %s" % (fast, slow))
    check("A6 색인이 전수 비교보다 빠르다 (조회 %.0fms → %.1fms, "
          "카탈로그 %.0fms + 색인 %.0fms 는 1회)"
          % (slow_ms, fast_ms, cat_ms, build_ms), fast_ms < slow_ms / 5)


# ------------------------------------------------------------------ B1
def b1_units():
    rows = read_csv("sheets/momo_country_products/units.csv")
    ko, th = U.Units(rows, "ko"), U.Units(rows, "th")

    check("B1 한국어 단위표가 시트에서 온다", ko.grams("키로") == 1000, ko.grams("키로"))
    check("B1 태국어 ขีด 는 100g", th.grams("ขีด") == 100, th.grams("ขีด"))
    check("B1 태국 고객의 '5 ขีด' 는 500g. 1kg 포장이라 확정하지 않고 되묻는다",
          th.resolve(5, "ขีด", "1kg") == (None, "요청 500g", [1]),
          th.resolve(5, "ขีด", "1kg"))
    check("B1 10 ขีด 는 1kg 이라 딱 떨어진다",
          th.resolve(10, "ขีด", "1kg") == (1, "요청 1kg", []), th.resolve(10, "ขีด", "1kg"))
    check("B1 올림이 필요한지 판정은 그대로 선다", th.rounded_up(5, "ขีด", "1kg"))
    check("B1 시트를 못 읽으면 한국어 값으로 폴백",
          U.Units([], "th").grams("근") == 600)
    check("B1 unit 파싱은 라틴 표기 그대로", ko.parse_pack("500g") == 500)
    check("B1 개수 단위는 환산하지 않는다", ko.convert(3, "개", "1kg") == (3, None))
    check("B1 시트에 없는 단위로 온 소수 수량도 올리지 않고 되묻는다",
          ko.resolve(0.5, "ขีด", "1kg") == (None, None, [1]),
          ko.resolve(0.5, "ขีด", "1kg"))


# ------------------------------------------------------------------ B2
def b2_policy_lang():
    rows = [
        {"구분": "응대규칙", "키": "PERSONA", "값": "친절한 담당자", "값유형": "문장", "lang": ""},
        {"구분": "응대규칙", "키": "PERSONA", "값": "ใจดี", "값유형": "문장", "lang": "th"},
        {"구분": "응대규칙", "키": "ASK_ADDRESS_DETAIL", "값": "권장", "값유형": "제어값", "lang": ""},
        {"구분": "응대규칙", "키": "NO_REPEAT_QUESTION", "값": "반드시", "값유형": "제어값", "lang": ""},
        {"구분": "배송정책", "키": "FREE_SHIPPING_THRESHOLD", "값": "50000", "값유형": "숫자", "lang": ""},
        {"구분": "배송정책", "키": "SHIPPING_FEE", "값": "3000", "값유형": "숫자", "lang": ""},
    ]
    ko, th = pol.Policies(rows, "ko"), pol.Policies(rows, "th")
    check("B2 lang 이 비면 전 언어 공통", ko.get("PERSONA") == "친절한 담당자")
    check("B2 언어 행이 공통 행을 덮어쓴다", th.get("PERSONA") == "ใจดี", th.get("PERSONA"))
    check("B2 언어만 다른 중복 키는 경고가 아니다",
          not any("중복" in w for w in ko.validate()), ko.validate())
    check("B2 제어값을 번역하면 경고한다",
          any("반드시" in w for w in ko.validate()), ko.validate())
    check("B2 컬럼이 없는 예전 시트도 돈다",
          pol.Policies([{"구분": "응대규칙", "키": "PERSONA", "값": "x"}]).get("PERSONA") == "x")


# ------------------------------------------------------------------ B3
def b3_reject_evidence():
    master = [{"item_code": "P001", "canonical_name": "삼겹살", "unit": "1kg", "is_active": "Y"},
              {"item_code": "P002", "canonical_name": "생삼겹", "unit": "1kg", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": c,
               "price": "10000", "rank": "1"} for c in ("P001", "P002")]
    syn = [{"lang": "ko", "item_code": "P001", "synonym": "삼겹"},
           {"lang": "ko", "item_code": "P002", "synonym": "삼겹"},
           {"lang": "th", "item_code": "P001", "synonym": "สามชั้น"}]
    P = policies()

    def run(lang, text, op):
        cat = M.Catalog(master, prices, [], syn, lang=lang)
        st = OrderState()
        st.apply({"item_ops": [{"op": "add", "name_hint": op["name_hint"], "quantity": 1}]},
                 1, cat, P)
        st.rematch(cat, P, "full")
        st.apply({"item_ops": [op]}, 2, cat, P, user_text=text)
        st.rematch(cat, P, "full")
        return st

    st = run("ko", "그거 말고 다른 거요",
             {"op": "reject", "name_hint": "삼겹살", "reject_evidence": None})
    check("B3 한국어 거부는 예전 정규식 폴백으로도 통과", st.lines[0].rejected)

    st = run("th", "ไม่ใช่อันนี้ครับ ขอเป็นอย่างอื่น",
             {"op": "reject", "name_hint": "สามชั้น", "reject_evidence": "ไม่ใช่อันนี้"})
    check("B3 태국어 거부가 근거와 함께 오면 받아들인다", st.lines[0].rejected,
          "rejected=%s" % st.lines[0].rejected)

    st = run("th", "อันนี้ขายยังไงครับ",
             {"op": "reject", "name_hint": "สามชั้น", "reject_evidence": "ไม่ใช่"})
    check("B3 발화에 없는 근거는 버린다 (모델의 reject 남발 차단)",
          not st.lines[0].rejected)

    # 지시대명사
    cat = M.Catalog(master, prices, [], syn)
    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "อันนี้", "is_reference": True,
                            "reference_evidence": "อันนี้", "quantity": 1}]},
             1, cat, P, user_text="อันนี้ครับ")
    check("B3 지시대명사는 근거가 맞으면 상품명이 아니다", not st.lines, st.lines)


# ------------------------------------------------------------------ B4
def b4_stage_detect():
    master = [{"item_code": "P001", "canonical_name": "삼겹살", "unit": "1kg", "is_active": "Y"},
              {"item_code": "P002", "canonical_name": "생삼겹", "unit": "1kg", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": c,
               "price": "10000", "rank": "1"} for c in ("P001", "P002")]
    syn = [{"lang": "ko", "item_code": c, "synonym": "삼겹"} for c in ("P001", "P002")]
    P = policies()
    cat = M.Catalog(master, prices, [], syn)
    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "삼겹", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)

    thai_ask = "อันไหนดีครับ ไหม"      # 물음표 없이 묻는 태국어
    old = FL.detect(thai_ask, st, q, P, {}, False)
    new = FL.detect(thai_ask, st, q, P, {}, False, catalog=cat, asking=True)
    check("B4 물음표가 없다고 되물음 누락으로 잡히던 것 (예전 방식)",
          any(h["감지"] == "되물음 누락" for h in old))
    check("B4 stage 로 판정하면 오탐이 사라진다",
          not any(h["감지"] == "되물음 누락" for h in new), new)
    check("B4 실제로 안 되물으면 그대로 잡는다",
          any(h["감지"] == "되물음 누락"
              for h in FL.detect("네 알겠습니다", st, q, P, {}, False,
                                 catalog=cat, asking=False)))


# ------------------------------------------------------------------ B5
def b5_nfc():
    """맥·아이폰에서 온 한글은 자모가 분해된 형태(NFD)로 들어온다.
    결합 부호를 쓰는 로마자도 마찬가지다. 정규화하지 않으면 눈에 같아 보이는데
    매칭이 통째로 실패하고, 왜 실패했는지 화면에서 알 방법이 없다."""
    import unicodedata as ud

    master = [{"item_code": "P001", "canonical_name": "삼겹살", "unit": "1kg", "is_active": "Y"},
              {"item_code": "P002", "canonical_name": "Café", "unit": "1kg", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": c,
               "price": "1000", "rank": "1"} for c in ("P001", "P002")]
    cat = M.Catalog(master, prices, [], [])
    P = policies()

    nfd_ko = ud.normalize("NFD", "삼겹살")
    check("B5 자모가 분해된 한글(맥 입력)도 같은 상품으로 확정",
          nfd_ko != "삼겹살" and M.match({"name_hint": nfd_ko}, cat, P).code == "P001",
          repr(M.match({"name_hint": nfd_ko}, cat, P)))
    check("B5 결합 부호가 분해된 로마자도 마찬가지",
          M.match({"name_hint": ud.normalize("NFD", "Café")}, cat, P).code == "P002")
    check("B5 색인 쪽 표기가 분해형이어도 찾는다",
          M.Catalog([{"item_code": "P003", "canonical_name": ud.normalize("NFD", "삼겹살"),
                      "unit": "1kg", "is_active": "Y"}], [], [], []).by_canonical.get("삼겹살")
          == ["P003"])


# ------------------------------------------------------------------ C1
def c1_narrowing():
    """샴푸 20종. 고객이 후보를 좁히는 말을 하면 새 줄을 만들지 않는다."""
    scents = ["라벤더", "로즈", "민트", "레몬", "코코넛"]
    kinds = ["일반", "대용량", "여행용", "리필"]
    master, prices, names, syn = [], [], [], []
    i = 0
    for s in scents:
        for k in kinds:
            i += 1
            code = "S%03d" % i
            master.append({"item_code": code, "canonical_name": "%s%s샴푸" % (s, k),
                           "species": "샴푸", "part": s, "unit": "500g", "is_active": "Y"})
            prices.append({"country_code": "KR", "channel": "facebook", "item_code": code,
                           "price": "9000", "rank": str(i)})
            names.append({"lang": "ko", "item_code": code,
                          "display_name": "%s%s샴푸" % (s, k)})
            syn.append({"lang": "ko", "item_code": code, "synonym": "샴푸"})
            syn.append({"lang": "ko", "item_code": code, "synonym": "%s향" % s})
    cat = M.Catalog(master, prices, names, syn)
    P = policies()

    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "샴푸", "quantity": None}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    check("C1 1턴: 샴푸 한 줄, 후보 20개",
          len(st.lines) == 1 and len(st.lines[0].match.candidates) == 20,
          "%d줄 / %s" % (len(st.lines), len(st.lines[0].match.candidates)))

    diff = st.apply({"item_ops": [{"op": "add", "name_hint": "라벤더향"}]}, 2, cat, P)
    st.rematch(cat, P, "full")
    check("C1 2턴: 줄이 늘지 않는다", len(st.lines) == 1, [l.key for l in st.lines])
    check("C1 2턴: 후보가 4개로 좁혀진다",
          len(st.lines[0].match.candidates) == 4, st.lines[0].match.candidates)
    check("C1 좁힌 사실이 상태 변화에 남는다",
          any("후보 좁힘" in d for d in diff), diff)

    st.apply({"item_ops": [{"op": "add", "name_hint": "여행용"}]}, 3, cat, P)
    st.rematch(cat, P, "full")
    check("C1 3턴: 하나로 확정된다",
          st.lines[0].match.status == M.CONFIRMED
          and cat.display(st.lines[0].match.code) == "라벤더여행용샴푸",
          repr(st.lines[0].match))

    # 후보 안에서 안 걸리면 반드시 새 줄
    st2 = OrderState()
    st2.apply({"item_ops": [{"op": "add", "name_hint": "샴푸"}]}, 1, cat, P)
    st2.rematch(cat, P, "full")
    st2.apply({"item_ops": [{"op": "add", "name_hint": "칫솔", "quantity": 1}]}, 2, cat, P)
    check("C1 정말 다른 상품은 새 줄로 간다", len(st2.lines) == 2,
          [l.key for l in st2.lines])


# ------------------------------------------------------------------ C2
def c2_options():
    scents = ["라벤더", "로즈", "민트", "레몬", "코코넛"]
    kinds = ["일반", "대용량", "여행용", "리필"]
    master, prices, names, syn = [], [], [], []
    i = 0
    for s in scents:
        for k in kinds:
            i += 1
            code = "S%03d" % i
            # rank 는 platform 에만 넣어 채널 폴백까지 함께 본다
            master.append({"item_code": code, "canonical_name": "%s%s샴푸" % (s, k),
                           "species": "샴푸", "part": s, "unit": "500g", "is_active": "Y"})
            prices.append({"country_code": "KR", "channel": "facebook", "item_code": code,
                           "price": "9000", "rank": ""})
            prices.append({"country_code": "KR", "channel": "platform", "item_code": code,
                           "price": "9500", "rank": str(21 - i)})
            names.append({"lang": "ko", "item_code": code,
                          "display_name": "%s%s샴푸" % (s, k)})
            syn.append({"lang": "ko", "item_code": code, "synonym": "샴푸"})
    cat = M.Catalog(master, prices, names, syn, channel="facebook")
    P = policies()

    check("C2 rank 채널 폴백이 돈다 (facebook 은 비었고 platform 에만 있음)",
          cat.rank("S020") == 1, cat.rank("S020"))

    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "샴푸", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)
    text, stage = RP.build(st, q, cat, P, [])
    check("C2 후보 20개여도 되물음이 짧다 (%d자)" % len(text), len(text) < 120, text)
    check("C2 1위 상품 하나를 제안한다", "코코넛리필샴푸" in text, text)

    st.lines[0].top_offer_declined = True
    text2, _ = RP.build(st, q, cat, P, [])
    check("C2 아니라고 하면 상위 5개를 보여준다",
          text2.count("/") == 4 and "코코넛리필샴푸" in text2, text2)

    check("C2 alternatives 도 같은 상한을 쓴다",
          len(cat.alternatives("S001", 5)) <= 5, cat.alternatives("S001", 5))

    # 후보가 아주 많으면 종류·부위로 먼저 좁힌다
    P2 = pol.Policies([r for r in policies().rows
                       if r["키"] != "AMBIGUOUS_ATTR_THRESHOLD"]
                      + [{"구분": "응대규칙", "키": "AMBIGUOUS_ATTR_THRESHOLD", "값": "10",
                          "값유형": "숫자", "lang": ""}])
    text3, _ = RP.build(st, q, cat, P2, [])
    check("C2 후보가 아주 많으면 부위로 먼저 좁힌다", "라벤더" in text3 and "중에서" in text3,
          text3)


# ------------------------------------------------------------------ C3 / D1 / D2
def c3_d1_d2():
    pend = {"missing": [], "detail": True, "detail_rule": "권장", "keys": []}
    ask = RP.fallback_ask(pend, policies())
    check("C3 동·호를 전제하지 않는 문구", "동·호" not in ask and "건물" in ask, ask)

    master = [{"item_code": "P001", "canonical_name": "삼겹살", "unit": "1kg",
               "ship_type": "냉동", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": "P001",
               "price": "10000", "rank": "1"}]
    cat = M.Catalog(master, prices, [], [])
    P = policies()
    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "삼겹살", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    st.address_base = Field("서울시 강남구 테헤란로 1")
    q = st.quote(cat, P)

    fl = FL.evaluate(st, q, cat, P, {}, "full")
    ev = [f.evidence for f in fl if f.key == "ADDRESS_DETAIL_MISSING"]
    check("C3 플래그 근거에서도 (동·호) 를 뺀다", ev and "동·호" not in ev[0], ev)

    for value, why in (("1084770874.0", "소수점"), ("1084770874", "0 으로 시작"),
                       ("010-1234", "10~11자리")):
        st.phone = Field(value)
        fl = FL.evaluate(st, q, cat, P, {}, "full")
        got = [f.evidence for f in fl if f.key == "PHONE_INVALID"]
        check("D1 '%s' 는 PHONE_INVALID (%s)" % (value, why),
              got and why in got[0], got)
    st.phone = Field("010-8477-0874")
    fl = FL.evaluate(st, q, cat, P, {}, "full")
    check("D1 정상 번호는 통과", not any(f.key == "PHONE_INVALID" for f in fl))

    # 총액이 아직 확정되지 않은 턴에서도 대조해야 한다. 모델이 금액을 지어내는 것은
    # 오히려 코드가 총액을 못 낸 순간이다
    st_open = OrderState()
    st_open.apply({"item_ops": [{"op": "add", "name_hint": "삼겹살"}]}, 1, cat, P)
    st_open.rematch(cat, P, "full")
    q_open = st_open.quote(cat, P)
    made_up = "배송비 4,000원을 더해서 총 35,000원입니다."
    check("D2 총액 미확정 턴에서도 지어낸 금액을 잡는다",
          q_open["total"] is None and FL.amount_mismatch(made_up, q_open) == [4000, 35000],
          (q_open["total"], FL.amount_mismatch(made_up, q_open)))
    check("D2 되물음 중에는 검사하지 않는다 (코드가 후보 가격을 직접 쓴다)",
          not any(f.key == "AMOUNT_MISMATCH"
                  for f in FL.evaluate(st_open, q_open, cat, P, {}, "full",
                                       bot_text=made_up, asking=True)),
          [f.key for f in FL.evaluate(st_open, q_open, cat, P, {}, "full",
                                      bot_text=made_up, asking=True)])
    check("D2 되물음이 아니면 확정 전에도 플래그를 올린다",
          any(f.key == "AMOUNT_MISMATCH"
              for f in FL.evaluate(st_open, q_open, cat, P, {}, "full",
                                   bot_text=made_up, asking=False)))

    bot = "총 99999원 입금해주세요"
    fl = FL.evaluate(st, q, cat, P, {}, "full", bot_text=bot)
    check("D2 금액 불일치가 실제 플래그로 뜬다",
          any(f.key == "AMOUNT_MISMATCH" for f in fl), [f.key for f in fl])
    Poff = pol.Policies([r for r in policies().rows
                         if r["키"] != "AMOUNT_MISMATCH_ENFORCE"]
                        + [{"구분": "응대규칙", "키": "AMOUNT_MISMATCH_ENFORCE", "값": "N",
                            "값유형": "제어값", "lang": ""}])
    fl = FL.evaluate(st, q, cat, Poff, {}, "full", bot_text=bot)
    check("D2 지침으로 끌 수 있다", not any(f.key == "AMOUNT_MISMATCH" for f in fl))
    check("D2 정상 금액은 안 뜬다",
          not any(f.key == "AMOUNT_MISMATCH"
                  for f in FL.evaluate(st, q, cat, P, {}, "full",
                                       bot_text="총 13,000원 입금해주세요")))

    # C4
    hits = FL.detect("네 알겠습니다", st, q, P, {"customer_question": "원산지가 어디예요?",
                                            "question_answered": False},
                     False, catalog=cat, asking=True)
    check("C4 고객 질문을 흘려보내면 잡는다",
          any(h["감지"] == "고객 질문 무시" for h in hits), hits)
    hits = FL.detect("삼겹살은 취급하지 않아요", st, q, P,
                     {"unavailable_claim": "삼겹살"}, False, catalog=cat, asking=True)
    check("C4 파는 상품을 없다고 하면 잡는다",
          any(h["감지"] == "취급 여부 오안내" for h in hits), hits)


# ------------------------------------------------------------------ 한국어 회귀
def ko_regression():
    master = read_csv("sheets/momo_master_products/master_products.csv")
    prices = read_csv("sheets/momo_country_products/prices.csv")
    names = read_csv("sheets/momo_country_products/product_names.csv")
    syn = read_csv("sheets/momo_country_products/synonyms.csv")
    ship = read_csv("sheets/momo_country_products/shipping.csv")
    unit_rows = read_csv("sheets/momo_country_products/units.csv")
    pol_rows = read_csv("sheets/momo_bot_policies/bot_policies.csv")

    cat = M.Catalog(master, prices, names, syn, lang="ko", country_code="KR",
                    channel="facebook", shipping=ship, units=unit_rows)
    P = pol.Policies(pol_rows, lang="ko")

    check("회귀 지침 경고 없음", not P.validate(), P.validate())
    check("회귀 6품목이 모두 살아 있다", len(cat.items) == 6, list(cat.items))
    check("회귀 가격이 예전과 같다",
          [cat.price(c) for c in ("A0013", "B0023", "A0022", "A0031", "A0024", "A0026")]
          == [4000, 10000, 5000, 7000, 8000, 24000],
          [cat.price(c) for c in cat.items])

    ko_syn = [r for r in syn if r["lang"] == "ko"]
    bad = []
    for r in ko_syn:
        res = M.match({"name_hint": r["synonym"]}, cat, P)
        if res.status == M.NOT_FOUND:
            bad.append(r["synonym"])
        elif res.status == M.CONFIRMED and res.code != r["item_code"]:
            # 유사어가 여러 상품에 걸리면 모호가 정상이고, 정식명 우선도 정상이다
            if r["synonym"] not in [x["synonym"] for x in ko_syn
                                    if x["item_code"] != r["item_code"]]:
                if cat.by_canonical.get(r["synonym"]) is None:
                    bad.append("%s→%s" % (r["synonym"], res.code))
    check("회귀 한국어 유사어 %d개가 전부 매칭된다" % len(ko_syn), not bad, bad)

    check("회귀 오타는 여전히 오타로 (섬겹살)",
          M.near_candidates("섬겹살", cat) == ["B0023"], M.near_candidates("섬겹살", cat))
    check("회귀 취급하지 않는 상품은 여전히 미취급 (메기)",
          M.near_candidates("메기", cat) == [], M.near_candidates("메기", cat))

    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "삼겹살", "quantity": 2},
                           {"op": "add", "name_hint": "소꼬리", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)
    check("회귀 합계가 예전과 같다 (20000+24000+배송 3000)", q["total"] == 47000, q)
    inv = RP._invoice_text(q, P)
    check("회귀 조사가 겹치지 않는다 (원을을 · 를를 등)",
          not re.search(r"(을을|를를|은은|는는|이이|가가)", inv), inv)
    check("회귀 총액 문장이 그대로",
          "총 47,000원을 아래 계좌로 입금주시면 감사하겠습니다." in inv, inv)

    check("회귀 배송비 한 줄로만 적는다 (유형이 하나)",
          RP._invoice_text(q, P).count("배송비") == 1, RP._invoice_text(q, P))

    st_free = OrderState()
    st_free.apply({"item_ops": [{"op": "add", "name_hint": "소꼬리", "quantity": 3}]},
                  1, cat, P)
    st_free.rematch(cat, P, "full")
    qf = st_free.quote(cat, P)
    check("회귀 무료배송 기준을 넘기면 0원 (72000 ≥ 50000)",
          qf["shipping"] == 0 and "무료배송" in RP._invoice_text(qf, P),
          RP._invoice_text(qf, P))

    st2 = OrderState()
    st2.apply({"item_ops": [{"op": "add", "name_hint": "돼지꼬리", "quantity": 2,
                             "unit_expr": "키로"}]}, 1, cat, P)
    st2.rematch(cat, P, "full")
    q2 = st2.quote(cat, P)
    check("회귀 무게 환산이 그대로", q2["rows"][0]["수량"] == 2, q2["rows"][0])
    check("회귀 배송비 3000 그대로", q2["shipping"] == 3000, q2["shipping"])

    st3 = OrderState()
    st3.apply({"item_ops": [{"op": "add", "name_hint": "꼬리", "quantity": 1}]}, 1, cat, P)
    st3.rematch(cat, P, "full")
    check("회귀 '꼬리' 는 여전히 모호 (돼지꼬리·소꼬리)",
          st3.lines[0].match.status == M.AMBIGUOUS, repr(st3.lines[0].match))
    text, stage = RP.build(st3, st3.quote(cat, P), cat, P, [])
    check("회귀 모호 되물음 문장이 후보를 그대로 나열한다",
          stage == "ambiguous_ask" and "돼지꼬리" in text and "소꼬리" in text, text)


# ------------------------------------------------------------------ 태국어 화면·문장
def th_ui_and_replies():
    """태국 직원이 이 도구를 직접 쓰고, 태국 고객 대화를 재현한다.
    화면도 고객 문장도 한국어가 남아 있으면 안 된다."""
    from lib import messages as MSG

    ko, th = MSG.KO, MSG.TH
    check("UI 기본 언어가 태국어", MSG.DEFAULT_LANG == "th", MSG.DEFAULT_LANG)

    # 문구표에 빠진 키가 있으면 그 자리만 한국어로 튀어나온다
    missing = [k for k in ko if k not in th]
    check("문구표에 태국어가 빠진 키가 없다", not missing, missing)

    holes = lambda v: len(re.findall(r"%[sd]", str(v)))
    mism = [k for k, v in ko.items() if holes(v) != holes(th.get(k, ""))]
    check("문구표의 자리표시자 개수가 언어마다 같다", not mism, mism)
    star = [k for k, v in th.items() if "$" in str(v) and "%" in str(v).split("$")[0][-3:]]
    check("파이썬이 못 읽는 위치 지정(%1$s)이 없다", not star, star)

    hangul = re.compile(r"[가-힣]")
    leaked = [k for k, v in th.items() if hangul.search(str(v))]
    check("태국어 문구에 한글이 남아 있지 않다", not leaked, leaked)

    master = [{"item_code": "P001", "canonical_name": "삼겹살", "unit": "1kg",
               "ship_type": "냉동", "is_active": "Y"},
              {"item_code": "P002", "canonical_name": "생삼겹", "unit": "1kg",
               "ship_type": "냉동", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": c,
               "price": "10000", "rank": str(i + 1)} for i, c in enumerate(("P001", "P002"))]
    names = [{"lang": "th", "item_code": "P001", "display_name": "หมูสามชั้น"},
             {"lang": "th", "item_code": "P002", "display_name": "หมูสามชั้นสด"}]
    syn = [{"lang": "th", "item_code": c, "synonym": "สามชั้น"} for c in ("P001", "P002")]
    P = policies(lang="th")
    cat = M.Catalog(master, prices, names, syn, lang="th")

    st = OrderState()
    st.apply({"item_ops": [{"op": "add", "name_hint": "สามชั้น", "quantity": 1}]}, 1, cat, P)
    st.rematch(cat, P, "full")
    text, stage = RP.build(st, st.quote(cat, P), cat, P, [])
    check("태국어 되물음에 한글이 없다", stage == "ambiguous_ask" and not hangul.search(text),
          text)

    st.apply({"item_ops": [{"op": "choose", "name_hint": "สามชั้น",
                            "chosen_code": "P001"}]}, 2, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)
    inv = RP._invoice_text(q, P)
    check("태국어 거래명세서에 한글이 없다", not hangul.search(inv), inv)
    check("태국어 거래명세서에 금액이 그대로 들어 있다", "10,000" in inv, inv)

    st.done_shown = False
    st.receiver = Field("Pen")
    st.phone = Field("010-8477-0874")
    st.address_base = Field("경기도 안산시")
    st.address_detail = Field("บ้านพักคนงาน")
    done, stage = RP.build(st, q, cat, P, [])
    check("태국어 마무리 인사에 한글이 없다", stage == "complete" and not hangul.search(done),
          done)

    # 인계 메모와 플래그 근거는 개발자가 읽고 로그에 쌓이므로 언어를 고정한다.
    # 언어별로 다른 문장이 같은 컬럼에 섞이면 한국어·태국어 대화를 나란히 비교할 수 없다.
    hand = HO.build(st, q, cat, P, [])
    check("인계 메모는 태국어 세션에서도 개발자 언어로 남는다",
          hand and all(hangul.search(t) for _, t in hand), hand)
    st2 = OrderState()
    st2.apply({"item_ops": [{"op": "add", "name_hint": "สามชั้น", "quantity": 1}]},
              1, cat, P)
    st2.rematch(cat, P, "full")
    fl = FL.evaluate(st2, st2.quote(cat, P), cat, P, {}, "full")
    check("플래그 근거도 개발자 언어로 남는다",
          fl and all(hangul.search(f.evidence) for f in fl), [f.evidence for f in fl])

    sysmsg = LLM.build_system(P, "full")
    check("시스템 지시문이 태국어로 답하라고 못박는다", "태국어" in sysmsg,
          sysmsg[:200])
    check("한국어를 고르면 예전 문구 그대로",
          RP.build(OrderState(), {"rows": [], "subtotal": 0, "shipping": 0,
                                  "total": 0, "blocked": False,
                                  "shipping_rows": []}, cat, policies())[0]
          == "어떤 상품 찾으세요? 상품명을 말씀해주시거나 사진을 보내주시면 담아드릴게요.")


def weight_order():
    """포장단위에 안 맞는 무게 주문. 올리지 않고 되묻는다."""
    master = [{"item_code": "P001", "canonical_name": "소꼬리", "unit": "1kg",
               "ship_type": "냉동", "is_active": "Y"},
              {"item_code": "P002", "canonical_name": "삼겹살", "unit": "500g",
               "ship_type": "냉동", "is_active": "Y"}]
    prices = [{"country_code": "KR", "channel": "facebook", "item_code": "P001",
               "price": "24000", "rank": "1"},
              {"country_code": "KR", "channel": "facebook", "item_code": "P002",
               "price": "5000", "rank": "2"}]
    P = policies()
    cat = M.Catalog(master, prices, [], [])

    def order(name, qty, unit_expr):
        st = OrderState()
        st.apply({"item_ops": [{"op": "add", "name_hint": name, "quantity": qty,
                                "unit_expr": unit_expr}]}, 1, cat, P)
        st.rematch(cat, P, "full")
        return st, st.quote(cat, P)

    st, q = order("소꼬리", 2, "키로")
    check("무게 2키로는 1kg 2개로 그냥 확정된다 (되묻지 않음)",
          q["rows"][0]["수량"] == 2 and q["total"] == 51000
          and RP.build(st, q, cat, P, [])[1] == "invoice",
          (q["rows"][0]["수량"], q["total"], RP.build(st, q, cat, P, [])[1]))

    st, q = order("소꼬리", 2.5, "키로")
    text, stage = RP.build(st, q, cat, P, [])
    check("2.5키로는 올리지 않는다 (예전엔 3개 72,000원이 청구됐다)",
          q["rows"][0]["수량"] is None and q["total"] is None, q["rows"][0])
    check("2.5키로는 기존 수량 되물음 자리로 간다", stage == "quantity_ask", stage)
    check("문구가 포장단위와 선택지를 알려준다",
          "1kg 단위로만" in text and "2개(2kg)" in text and "3개(3kg)" in text, text)
    check("총액 확정을 막는다 (MISSING_PRICE 로 오인하지 않는다)",
          q["blocked"] and not any(f.key == "MISSING_PRICE"
                                   for f in FL.evaluate(st, q, cat, P, {}, "full")),
          [f.key for f in FL.evaluate(st, q, cat, P, {}, "full")])

    # 고객이 "3개" 라고 답하면 그대로 확정된다
    st.apply({"item_ops": [{"op": "update", "name_hint": "소꼬리", "quantity": 3}]},
             2, cat, P)
    st.rematch(cat, P, "full")
    q = st.quote(cat, P)
    check("고른 뒤에는 거래명세서가 나온다",
          q["rows"][0]["수량"] == 3 and q["total"] == 72000
          and RP.build(st, q, cat, P, [])[1] == "invoice", (q["rows"][0], q["total"]))

    # 숫자만 답해도 새 품목이 만들어지면 안 된다
    st2, _ = order("소꼬리", 2.5, "키로")
    st2.apply({"item_ops": [{"op": "add", "name_hint": "2개", "quantity": 2}]}, 2, cat, P)
    st2.rematch(cat, P, "full")
    q2 = st2.quote(cat, P)
    check("숫자만 답해도 새 줄이 생기지 않는다",
          len(st2.lines) == 1 and q2["rows"][0]["수량"] == 2, [l.key for l in st2.lines])

    st, q = order("소꼬리", 1, "근")
    text, stage = RP.build(st, q, cat, P, [])
    check("한 근(600g)도 1kg 로 올리지 않는다",
          stage == "quantity_ask" and "1개(1kg)" in text, text)

    st, q = order("삼겹살", 1.2, "키로")
    text, _ = RP.build(st, q, cat, P, [])
    check("500g 포장은 500g 단위로 되묻는다",
          "500g 단위로만" in text and "2개(1kg)" in text and "3개(1.5kg)" in text, text)

    st, q = order("삼겹살", 1, "키로")
    check("500g 포장에 1키로는 2개로 확정", q["rows"][0]["수량"] == 2, q["rows"][0])


def flag_judging():
    """테스터가 찍은 플래그 판정이 로그 행으로 제대로 옮겨지는지."""
    from lib import logs as LOG

    class F:
        def __init__(self, key, value, ev):
            self.key, self.value, self.evidence = key, value, ev

    history = [{"turn": 2, "user": "u", "bot": "b", "at": "", "usage": {},
                "flags": [F("AMBIGUOUS_ALIAS", "되물음", "'꼬리' 가 2개 상품에 걸림"),
                          F("RECEIVER_MISSING", "미완료", "수령인 이름 미확보")],
                "out": {}, "diff": [], "detect": []}]

    class S:
        receiver = phone = address_base = address_detail = type("F", (), {"value": ""})()
        zipno = road_addr = ""
        lines = []
    quote = {"rows": [], "subtotal": 0, "shipping": 0, "total": 0, "blocked": False,
             "shipping_rows": []}
    bundle = LOG.build_rows(
        "c1", "A", "전체", "m", S(), quote, history, {}, {}, "",
        policy_version="v", started_at="", ended_at="",
        flag_settings={"AMBIGUOUS_ALIAS": "되물음", "RECEIVER_MISSING": "미완료",
                       "SOLDOUT": "되물음"},
        flag_verdicts={"AMBIGUOUS_ALIAS": "정탐", "RECEIVER_MISSING": "오탐"},
        missed_flags=["SOLDOUT"])
    rows = {r["flag_key"]: r for r in bundle["flag_verdicts"]}

    check("정탐은 expected=Y raised=Y 로 남는다",
          (rows["AMBIGUOUS_ALIAS"]["verdict"], rows["AMBIGUOUS_ALIAS"]["expected"],
           rows["AMBIGUOUS_ALIAS"]["raised"]) == ("정탐", "Y", "Y"),
          rows["AMBIGUOUS_ALIAS"])
    check("오탐은 expected=N 으로 남는다",
          (rows["RECEIVER_MISSING"]["verdict"], rows["RECEIVER_MISSING"]["expected"])
          == ("오탐", "N"), rows["RECEIVER_MISSING"])
    check("미탐은 뜨지도 않은 플래그로 한 줄이 생긴다",
          (rows["SOLDOUT"]["verdict"], rows["SOLDOUT"]["raised"],
           rows["SOLDOUT"]["expected"], rows["SOLDOUT"]["flag_value"])
          == ("미탐", "N", "Y", "되물음"), rows["SOLDOUT"])
    check("근거와 뜬 턴은 그대로 남는다",
          rows["AMBIGUOUS_ALIAS"]["raised_turn"] == 2
          and "꼬리" in rows["AMBIGUOUS_ALIAS"]["evidence"], rows["AMBIGUOUS_ALIAS"])

    bundle = LOG.build_rows("c1", "A", "전체", "m", S(), quote, history, {}, {}, "",
                            policy_version="v", started_at="", ended_at="",
                            flag_settings={})
    check("아무것도 안 찍으면 판정 칸이 빈 채로 남는다",
          all(r["verdict"] == "" for r in bundle["flag_verdicts"])
          and len(bundle["flag_verdicts"]) == 2, bundle["flag_verdicts"])


def flag_coverage():
    """시트에서 플래그 행을 지우면 add() 가 조용히 넘어가 아무 일도 안 일어난다.
    에러도 경고도 없다. 그래서 코드가 부르는 키 목록을 시트와 대조한다.
    그 목록 자체가 낡으면 점검이 무의미하므로 소스와도 대조한다."""
    import re as _re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "flags.py"), encoding="utf-8").read()
    called = set(_re.findall(r'add\("([A-Z_]+)"', src))
    check("점검 목록이 코드가 실제로 부르는 플래그와 같다",
          called == set(FL.CODE_FLAGS),
          "코드에만: %s / 목록에만: %s" % (sorted(called - set(FL.CODE_FLAGS)),
                                     sorted(set(FL.CODE_FLAGS) - called)))

    rows = read_csv("sheets/momo_bot_policies/bot_policies.csv")
    warn = pol.Policies(rows).validate()
    check("시트 폴백에 빠진 플래그가 없다",
          not any("행이 없습니다" in w for w in warn), warn)

    short = [r for r in rows if r["키"] != "PRODUCT_NOT_FOUND"]
    warn = pol.Policies(short).validate()
    check("행을 지우면 경고로 드러난다",
          any("PRODUCT_NOT_FOUND" in w and "행이 없습니다" in w for w in warn), warn)


def main():
    print("=" * 70)
    for fn in (a2_label_and_channel, a3_whitelist, a4_active_soldout, a5_shipping,
               a6_near_index, b1_units, b2_policy_lang, b3_reject_evidence,
               b4_stage_detect, b5_nfc, c1_narrowing, c2_options, c3_d1_d2,
               th_ui_and_replies, weight_order, flag_coverage, flag_judging, ko_regression):
        print("\n[%s]" % fn.__name__)
        fn()
    print("\n" + "=" * 70)
    print("통과 %d · 실패 %d" % (len(OK), len(BAD)))
    for b in BAD:
        print("  실패: %s" % b)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
