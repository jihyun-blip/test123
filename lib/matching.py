# -*- coding: utf-8 -*-
"""
상품 매칭.

결정적 신호부터 순서대로 시도하고, 확정되면 이후 단계를 건너뛴다.
LLM 은 이해만 하고 매칭 확정은 코드가 한다.

조회는 2단이다. 라벨코드와 품목코드는 서로 다른 값이고, 가격은 나라 × 채널,
이름·유사어는 언어 축에 있다.

    사진   → label_code → master_products 에서 item_code
    텍스트 → synonyms(lang) / product_names(lang) → item_code
    item_code → prices(country, channel) 판매가·rank
              → product_names(lang) 표시명
              → master_products 의 unit·ship_type·soldout·is_active
"""
import difflib
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter

from . import units as U

CONFIRMED = "확정"
AMBIGUOUS = "모호"
NOT_FOUND = "미발견"
CONFLICT = "충돌"

# rank 가 비어 있는 상품. 1이 1위이므로 큰 값이 최하위다.
NO_RANK = 10 ** 9


def nfc(s):
    """유니코드 정규화.

    같은 글자가 다른 코드포인트 조합으로 들어오면 눈에는 같아 보이는 문자열이
    서로 다른 값이 되고, 유사도가 이유 없이 떨어진다. 맥·아이폰에서 넘어온 한글은
    자모가 분리된 형태(NFD)로 오고, 결합 부호를 쓰는 문자도 마찬가지다.
    매칭 진입점과 색인을 만드는 자리에서 모두 같은 형태로 맞춰둔다."""
    return unicodedata.normalize("NFC", str(s or ""))


def normalize(s):
    """공백·대소문자·구분기호를 정리한다. 5단계 재시도에서만 쓴다."""
    return re.sub(r"[\s\-_·]", "", nfc(s)).lower()


def _records(table):
    """DataFrame 이든 dict 목록이든 같은 형태로 받는다.
    검증 스크립트가 판다스 없이도 카탈로그를 만들 수 있어야 한다."""
    if table is None:
        return []
    if hasattr(table, "to_dict"):
        if getattr(table, "empty", False):
            return []
        return table.to_dict("records")
    return list(table)


def _int(v, default=None):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _yes(v, default=True):
    """빈 칸은 default. 명시적으로 다른 값을 적었을 때만 아니라고 본다."""
    s = str(v if v is not None else "").strip().upper()
    if not s:
        return default
    return s in ("Y", "YES", "TRUE", "1", "O")


class _NearIndex:
    """근접 후보 탐색용 문자 색인.

    본버전 규모(1,000품목 × 유사어 20개 × 5개국 ≒ 10만~20만 행)에서 매번 전체를
    difflib 로 훑으면 1초가 넘는다. 고객이 그만큼 기다린다.

    difflib 의 quick_ratio(문자 다중집합 교집합 기반)는 ratio 의 상한이다.
    상한이 기준에 못 미치는 후보는 실제 ratio 를 볼 필요가 없다. 색인은 그 상한을
    싸게 계산하기 위한 것이라, 결과가 전수 비교와 정확히 같다.
    2-gram 만으로 좁히면 '삼겹살'↔'삼결살' 처럼 가운데 한 글자만 다른 오타가
    공유 2-gram 이 없어 통째로 누락된다. 그래서 낱글자로 색인한다."""

    def __init__(self, entries):
        self.words = []      # 표현 문자열
        self.codes = []      # 그 표현이 가리키는 item_code 목록
        self.lens = []
        self.post = {}       # 글자 -> [(표현 번호, 그 표현에서의 등장 횟수)]

        for word, codes in entries:
            i = len(self.words)
            self.words.append(word)
            self.codes.append(codes)
            self.lens.append(len(word))
            for ch, n in Counter(word).items():
                self.post.setdefault(ch, []).append((i, n))

        # 글자별 목록을 길이순으로 정렬해둔다. 길이만으로 이미 상한이 안 되는 표현은
        # 훑을 필요조차 없다. ratio <= 2*min(la,lb)/(la+lb) 이기 때문이다.
        self.plen = {}
        for ch, lst in self.post.items():
            lst.sort(key=lambda t: self.lens[t[0]])
            self.plen[ch] = [self.lens[i] for i, _ in lst]

    def candidates(self, expr, floor):
        """실제 ratio 가 floor 이상일 수 있는 표현 번호만 추린다."""
        la = len(expr)
        lo = la * floor / (2 - floor)
        hi = la * (2 - floor) / floor if floor else float("inf")
        inter = {}
        for ch, k in Counter(expr).items():
            lst = self.post.get(ch)
            if not lst:
                continue
            lens = self.plen[ch]
            a = bisect_left(lens, lo)
            b = bisect_right(lens, hi)
            for i, n in lst[a:b]:
                inter[i] = inter.get(i, 0) + (k if k < n else n)
        out = [i for i, m in inter.items() if 2.0 * m / (la + self.lens[i]) >= floor]
        # 색인 순서 = 표를 만든 순서. 동점일 때의 순서를 전수 비교와 맞추기 위해 정렬한다
        out.sort()
        return out


class Catalog:
    """상품·유사어를 매칭에 쓰기 좋은 형태로 펼쳐둔다.

    세 축을 모두 들고 있어야 한다. 가격은 나라 × 채널, 이름·유사어·단위는 언어다.
    한 축이라도 빠뜨리면 같은 item_code 의 뒤 행이 앞 행을 조용히 덮어쓴다."""

    def __init__(self, master, prices, product_names, synonyms,
                 lang="ko", country_code="KR", channel="facebook",
                 shipping=None, units=None):
        self.lang = lang
        self.country_code = country_code
        self.channel = channel
        self.units = units if isinstance(units, U.Units) else U.Units(_records(units), lang)

        # ------------------------------------------------------------ 마스터
        # is_active 는 취급 여부(영구), soldout 은 재고(일시)다.
        # 취급하지 않는 상품은 후보로도 뜨면 안 되므로 여기서 아예 뺀다.
        # 품절은 카탈로그에 남겨두고 주문 확정만 막는다. 대체 상품을 권해야 하기 때문이다.
        self.items = {}
        self.inactive = set()
        for r in _records(master):
            code = nfc(r.get("item_code")).strip()
            if not code:
                continue
            row = dict(r)
            if not _yes(row.get("is_active"), True):
                self.inactive.add(code)
                continue
            self.items[code] = row

        # ------------------------------------------------------------ 라벨코드
        # 라벨코드와 품목코드는 다른 값이다. 사진에서 읽은 것은 라벨코드다.
        # 두 컬럼이 분리된 뒤에도 예전처럼 품목코드를 라벨로 쓰면 "없는 코드"가 되어
        # 이름 매칭으로 조용히 폴백하고 결국 되물음이 나간다. 에러는 안 난다.
        self.by_label = {}
        for code, r in self.items.items():
            lab = nfc(r.get("label_code")).strip().upper()
            if lab:
                self.by_label.setdefault(lab, code)
        # label_code 가 비어 있는 상품만 품목코드를 라벨코드로 본다(지금 데이터가 그렇다).
        # 라벨코드를 채운 상품까지 품목코드로 찾히게 두면 두 값을 다시 뒤섞는 셈이 된다.
        for code, r in self.items.items():
            if not nfc(r.get("label_code")).strip():
                self.by_label.setdefault(code.upper(), code)

        # ------------------------------------------------------------ 가격(나라 × 채널)
        self._price, self._rank = {}, {}
        chan_p, common_p = {}, {}
        chan_r, common_r, other_r = {}, {}, {}
        want_ch = nfc(channel).strip().lower()
        for r in _records(prices):
            code = nfc(r.get("item_code")).strip()
            if code not in self.items:
                continue
            cc = nfc(r.get("country_code")).strip().upper()
            if cc and cc != nfc(country_code).strip().upper():
                continue
            ch = nfc(r.get("channel")).strip().lower()
            rank = _int(r.get("rank"))
            if ch and ch != want_ch:
                # 다른 채널 행. 가격은 쓰지 않지만 rank 폴백용으로 기억해둔다.
                # 운영자가 한쪽 채널의 rank 만 채우는 경우가 많다
                if rank is not None:
                    other_r.setdefault(code, rank)
                continue
            price = _int(r.get("price"))
            if ch:
                chan_p.setdefault(code, price)
                if rank is not None:
                    chan_r.setdefault(code, rank)
            else:
                common_p.setdefault(code, price)
                if rank is not None:
                    common_r.setdefault(code, rank)
            self.items[code].setdefault("currency", r.get("currency", ""))

        for code in self.items:
            # 채널 행이 공통 행을 덮어쓴다. 둘 다 없으면 다른 채널의 rank 로 폴백한다
            self._price[code] = chan_p.get(code, common_p.get(code))
            self._rank[code] = chan_r.get(code, common_r.get(code, other_r.get(code)))
            self.items[code]["price"] = self._price[code]
            self.items[code]["rank"] = self._rank[code]

        # ------------------------------------------------------------ 표시명(언어)
        self._display = {}
        for r in _records(product_names):
            if nfc(r.get("lang")).strip().lower() != nfc(lang).strip().lower():
                continue
            code = nfc(r.get("item_code")).strip()
            name = nfc(r.get("display_name")).strip()
            if code in self.items and name:
                self._display.setdefault(code, name)
        for code, r in self.items.items():
            # 해당 언어 행이 없으면 정식명으로 폴백한다. 태국어 행을 아직 안 채웠다고
            # 화면이 비면 안 된다
            r["display_name"] = self._display.get(code) or nfc(r.get("canonical_name")).strip() or code

        # ------------------------------------------------------------ 유사어(언어)
        syn_rows = [r for r in _records(synonyms)
                    if nfc(r.get("lang")).strip().lower() == nfc(lang).strip().lower()]
        if not syn_rows:
            # 그 언어의 유사어를 아직 안 채웠으면 한국어로 돈다. 매칭이 통째로 죽는 것보다 낫다
            syn_rows = [r for r in _records(synonyms)
                        if nfc(r.get("lang")).strip().lower() in ("ko", "")]
        self.by_synonym, self.syn_of = {}, {}
        for r in syn_rows:
            code = nfc(r.get("item_code")).strip()
            word = nfc(r.get("synonym")).strip()
            if not word or code not in self.items:
                continue
            self.by_synonym.setdefault(word, []).append(code)
            self.syn_of.setdefault(code, []).append(word)

        # 정식명·표시명 -> [item_code]
        self.by_canonical = {}
        for code, r in self.items.items():
            for name in (nfc(r.get("canonical_name")).strip(), r.get("display_name")):
                if name:
                    self.by_canonical.setdefault(name, [])
                    if code not in self.by_canonical[name]:
                        self.by_canonical[name].append(code)

        # 정규화 색인. 4단계까지 실패했을 때만 본다
        self.by_norm = {}
        for name, codes in list(self.by_canonical.items()) + list(self.by_synonym.items()):
            self.by_norm.setdefault(normalize(name), []).extend(codes)

        # ------------------------------------------------------------ 배송(나라 × 채널)
        self.shipping = {}
        common_s, chan_s = {}, {}
        for r in _records(shipping):
            cc = nfc(r.get("country_code")).strip().upper()
            if cc and cc != nfc(country_code).strip().upper():
                continue
            st = nfc(r.get("ship_type")).strip()
            if not st:
                continue
            ch = nfc(r.get("channel")).strip().lower()
            if ch and ch != want_ch:
                continue
            rule = {"fee": _int(r.get("fee"), 0) or 0,
                    "free_threshold": _int(r.get("free_threshold"), 0) or 0}
            (chan_s if ch else common_s).setdefault(st, rule)
        self.shipping = dict(common_s)
        self.shipping.update(chan_s)   # 채널 행이 공통 행을 덮어쓴다

        self._near = None   # 근접 탐색 색인. 실제로 필요할 때 한 번만 만든다

    # ---------------------------------------------------------------- 조회
    def display(self, code):
        r = self.items.get(code, {})
        return r.get("display_name") or r.get("canonical_name") or code

    def price(self, code):
        return self._price.get(code)

    def rank(self, code):
        r = self._rank.get(code)
        return NO_RANK if r is None else r

    def unit(self, code):
        return str(self.items.get(code, {}).get("unit") or "").strip()

    def ship_type(self, code):
        return str(self.items.get(code, {}).get("ship_type") or "").strip()

    def soldout(self, code):
        """재고가 없는 상태(일시). 카탈로그에는 있지만 주문 확정은 막는다."""
        return _yes(self.items.get(code, {}).get("soldout"), False)

    def label_codes(self):
        """유효한 라벨코드 목록. 품목코드와 같지 않을 수 있다."""
        return set(self.by_label)

    def item_of_label(self, label):
        return self.by_label.get(nfc(label).strip().upper())

    def by_rank(self, codes):
        """인기순. rank 1 이 1위이고 빈칸은 최하위다.
        품목코드 순으로 내밀면 고객에게는 아무 의미가 없는 순서가 된다."""
        return sorted(codes, key=lambda c: (self.rank(c), self.display(c)))

    def searchable(self, code):
        """이 상품을 가리킬 수 있는 표현 전부. 후보 좁히기의 부분일치에 쓴다."""
        r = self.items.get(code, {})
        out = [r.get("display_name") or "", nfc(r.get("canonical_name")),
               nfc(r.get("species")), nfc(r.get("part"))]
        out += self.syn_of.get(code, [])
        return [x for x in out if x]

    def substitutes(self, code, limit=3):
        """품절 상품의 대체 후보. 같은 종류·부위 중 인기순."""
        r = self.items.get(code, {})
        sp, part = nfc(r.get("species")).strip(), nfc(r.get("part")).strip()
        same = [c for c, x in self.items.items()
                if c != code and not self.soldout(c)
                and nfc(x.get("species")).strip() == sp
                and (not part or nfc(x.get("part")).strip() == part)]
        if not same and part:
            same = [c for c, x in self.items.items()
                    if c != code and not self.soldout(c)
                    and nfc(x.get("species")).strip() == sp]
        return self.by_rank(same)[:limit]

    def alternatives(self, code, limit=5):
        """이 상품과 정식명·유사어를 공유하는 다른 상품들.

        정식명이 다른 상품의 유사어이기도 한 경우가 많고, 그럴 때 정식명 쪽으로
        확정하는 것이 정상 동작이다(거래명세서에 정식명을 넣기 때문).
        다만 고객이 "그 상품이 아니다"라고 하면 같은 표현을 공유하는 상품들을
        후보로 제시해 선택받아야 하므로, 그 후보 목록을 여기서 만든다.

        상한과 인기순 정렬이 있어야 한다. 품목 1,000개에서는 상한 없는 나열이
        그대로 대화를 불가능하게 만든다."""
        r = self.items.get(code, {})
        exprs = {r.get("canonical_name"), r.get("display_name")}
        exprs |= set(self.syn_of.get(code, []))

        out = []
        for e in exprs:
            if not e:
                continue
            for c in self.by_canonical.get(e, []) + self.by_synonym.get(e, []):
                if c != code and c not in out:
                    out.append(c)
        out = self.by_rank(out)
        return out[:limit] if limit else out

    def shipping_rule(self, ship_type):
        return self.shipping.get(str(ship_type or "").strip())

    # ---------------------------------------------------------------- 근접 색인
    def near_index(self):
        """Catalog 하나당 한 번만 만든다. 20만 행 기준 약 0.6초라서,
        근접 탐색이 실제로 필요해질 때까지 미룬다."""
        if self._near is None:
            entries = [(w, list(cs)) for w, cs in self.by_canonical.items() if w]
            entries += [(w, list(cs)) for w, cs in self.by_synonym.items() if w]
            entries += [(self.display(c), [c]) for c in self.items if self.display(c)]
            self._near = _NearIndex(entries)
        return self._near


class MatchResult:
    def __init__(self, status, code=None, candidates=None, rule=None, note=""):
        self.status = status
        self.code = code
        self.candidates = candidates or []
        self.rule = rule          # 어느 단계에서 결정됐는지. 결과 화면의 근거가 된다
        self.note = note

    def __repr__(self):
        return "<%s %s via %s>" % (self.status, self.code, self.rule)


def match(op, catalog, policies, mode="full"):
    """op 는 LLM 이 돌려준 item_ops 원소 하나."""
    hint = nfc(op.get("name_hint") or op.get("raw_text") or "").strip()
    label = nfc(op.get("label_code") or "").strip()
    printed = nfc(op.get("printed_name") or "").strip()

    # 고객이 품목코드를 직접 적는 경우가 있다. LLM 이 그걸 name_hint 에 넣어 보내면
    # 유사어 사전에 없어 미발견이 되고, 되물음 문장에 코드가 그대로 노출된다.
    if not label:
        for token in re.findall(r"[A-Za-z]\d{3,}", hint):
            if catalog.item_of_label(token):
                label = token.upper()
                break

    # 1. 라벨코드 — 유효한 라벨이면 품목코드로 바꿔 확정
    item = catalog.item_of_label(label) if label else None
    if item:
        # 2. 인쇄 상품명이 함께 읽혔으면 대조한다
        if printed:
            printed_codes = catalog.by_canonical.get(printed) or catalog.by_synonym.get(printed) or []
            if printed_codes and item not in printed_codes:
                return MatchResult(
                    CONFLICT, item, printed_codes, "라벨코드-인쇄명 불일치",
                    "라벨 %s / 인쇄 '%s' → %s" % (label, printed, ", ".join(printed_codes)),
                )
        return MatchResult(CONFIRMED, item, rule="라벨코드")

    if not hint:
        return MatchResult(NOT_FOUND, rule="표현 없음")

    if mode == "reduced":
        return _match_reduced(hint, catalog)

    # 3. 정식 품목명 정확 일치
    canon = catalog.by_canonical.get(hint, [])
    if len(canon) == 1:
        if str(policies.get("EXACT_NAME_PRIORITY", "Y")).upper() == "Y":
            return MatchResult(CONFIRMED, canon[0], rule="정식명 정확일치")
        # N 이면 유사어까지 합쳐 모호 판단으로 넘긴다
        merged = sorted(set(canon + catalog.by_synonym.get(hint, [])))
        if len(merged) > 1:
            return MatchResult(AMBIGUOUS, candidates=merged, rule="정식명+유사어 중복")
        return MatchResult(CONFIRMED, canon[0], rule="정식명 정확일치")
    if len(canon) > 1:
        return MatchResult(AMBIGUOUS, candidates=sorted(canon), rule="정식명 중복")

    # 4. 유사어 정확 일치
    syn = catalog.by_synonym.get(hint, [])
    if len(syn) == 1:
        return MatchResult(CONFIRMED, syn[0], rule="유사어 정확일치")
    if len(syn) > 1:
        return MatchResult(AMBIGUOUS, candidates=sorted(set(syn)), rule="유사어 중복")

    # 5. 정규화 후 일치
    norm = catalog.by_norm.get(normalize(hint), [])
    norm = sorted(set(norm))
    if len(norm) == 1:
        return MatchResult(CONFIRMED, norm[0], rule="정규화 일치")
    if len(norm) > 1:
        return MatchResult(AMBIGUOUS, candidates=norm, rule="정규화 후 중복")

    # 6~7. 후보 없음
    return MatchResult(NOT_FOUND, rule="미발견")


def codes_for(expr, catalog):
    """이 표현이 가리키는 상품 전부. 확정·모호를 가르지 않고 모으기만 한다.
    후보 좁히기(모호한 줄을 더 좁힐지, 새 품목인지)를 판단할 때 쓴다."""
    e = nfc(expr).strip()
    if not e:
        return []
    out = list(catalog.by_canonical.get(e, [])) + list(catalog.by_synonym.get(e, []))
    if not out:
        out = list(catalog.by_norm.get(normalize(e), []))
    return list(dict.fromkeys(out))


def _match_reduced(hint, catalog, top_n=5):
    """축소 모드 — 외부 개발사가 실제로 갖게 될 수준의 재현.
    유사어 사전을 쓰지 않고 표시명 문자열 유사도 상위 N 개만 본다."""
    names = [(code, catalog.display(code)) for code in catalog.items]
    scored = sorted(
        ((difflib.SequenceMatcher(None, hint, name).ratio(), code) for code, name in names),
        reverse=True,
    )[:top_n]

    if not scored or scored[0][0] < 0.34:
        return MatchResult(NOT_FOUND, rule="유사도 미달(축소)",
                           note="최고 점수 %.2f" % (scored[0][0] if scored else 0))
    if scored[0][0] >= 0.9:
        return MatchResult(CONFIRMED, scored[0][1], rule="문자열 유사도(축소)")
    return MatchResult(AMBIGUOUS, candidates=[c for _, c in scored], rule="유사도 상위5(축소)",
                       note="최고 점수 %.2f" % scored[0][0])


# 오타와 미취급을 가르는 경계.
# 실측: 섬겹살→삼겹살 0.67, 삽겹살 0.67, 후지슬라이→후지슬라이스 0.91 (오타)
#       메기 0.29, 소고기 0.50 (DB 에 없는 상품)
# 0.55 를 넘으면 오타로 보고 후보를 내밀고, 못 넘으면 취급하지 않는 상품이다.
NEAR_FLOOR = 0.55


def near_candidates(expr, catalog, top=3, floor=NEAR_FLOOR):
    """DB 에 없는 표현과 가장 가까운 실제 상품을 고른다.

    표시명뿐 아니라 유사어까지 본다. 유사어가 더 가까운 경우가 많아서,
    표시명만 보면 오타를 미취급으로 잘못 판정한다.
    후보를 지어내지 않는다. 반드시 실제 DB 행에서만 뽑는다.

    색인으로 후보를 먼저 좁히고 그 안에서만 difflib 를 돌린다. 상한 판정이라
    전수 비교와 결과가 같다."""
    expr = nfc(expr).strip()
    if not expr:
        return []

    idx = catalog.near_index()
    best = {}
    for i in idx.candidates(expr, floor):
        r = difflib.SequenceMatcher(None, expr, idx.words[i]).ratio()
        for c in idx.codes[i]:
            if r > best.get(c, 0):
                best[c] = r

    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [c for c, r in ranked[:top] if r >= floor]
