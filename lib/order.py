# -*- coding: utf-8 -*-
"""
주문 상태 누적과 견적 계산.

누적은 코드가 담당한다. LLM 은 매 턴 "이번 턴에서 파악한 변경"만 돌려주고,
이전까지 쌓인 상태는 여기가 보관한다. LLM 응답으로 상태 전체를 덮어쓰지 않는다.
대화가 길어질수록 모델이 앞 항목을 빠뜨릴 확률이 올라가고,
그때마다 주문이 조용히 훼손되기 때문이다.

빈 값과 미언급을 구분한다. LLM 출력의 null 은 "이번 턴에 언급 없음"이며
코드는 기존 값을 유지한다. 빈 문자열을 미언급의 표현으로 쓰지 않는다.
"""
import copy
import re

from . import matching as M
from . import units as U


# '1개씩', '2', '3kg', '1개요', '1개씩이요' 처럼 수량만 있는 발화.
# 상품명으로 오인하면 없는 품목을 되묻게 된다.
# 말끝의 '요/이요/입니다' 같은 군더더기까지 받아줘야 실제 대화에서 걸린다.
_QTY_ONLY = re.compile(
    r"^\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(개|kg|g|키로|킬로|그램|근|박스|팩|봉|봉지|마리|줄|세트|통)?\s*"
    r"(씩)?\s*(?:이?요|입니다|이에요|예요|이요)?\s*[.!~]*\s*$")


class Field:
    """값 하나와 그 근거를 함께 들고 다닌다."""

    def __init__(self, value=None, source=None, source_ref=None, turn=None):
        self.value = value
        self.source = source
        self.source_ref = source_ref
        self.turn = turn

    def apply(self, payload, turn):
        """payload 가 None 이면 미언급이므로 기존 값을 유지한다."""
        if payload is None:
            return
        if isinstance(payload, dict):
            v = payload.get("value")
            if v is None:
                return
            self.value = v
            self.source = payload.get("source")
            self.source_ref = payload.get("source_ref")
        else:
            self.value = payload
        self.turn = turn

    @property
    def origin(self):
        if self.value is None:
            return ""
        if self.source == "image":
            return "%s턴 · %s" % (self.turn, self.source_ref or "이미지")
        return "%s턴 · 텍스트" % self.turn

    def __bool__(self):
        return bool(self.value)


# "이거 주세요" 처럼 사진이나 앞말을 가리키는 표현. 상품명이 아니다.
_DEMONSTRATIVE = {"이거", "이것", "요거", "요것", "저거", "저것", "그거", "그것",
                  "이상품", "그상품", "저상품", "이제품", "그제품", "사진", "이사진",
                  "위에것", "방금것", "동일상품", "같은거", "같은것"}


def _is_demonstrative(text):
    t = re.sub(r"[\s의]", "", str(text or "")).strip()
    return t in _DEMONSTRATIVE


class Line:
    """주문 품목 한 줄. 고객 표현과 매칭 결과를 분리해서 들고 있는다.
    표현을 정확히 뽑았는데 엉뚱한 상품에 붙는 경우와,
    표현을 잘못 읽었는데 우연히 맞는 상품으로 가는 경우는 대응이 다르다."""

    def __init__(self, raw_text, name_hint, quantity, unit_expr, source, source_ref, turn,
                 label_code=""):
        self.raw_text = raw_text
        self.name_hint = name_hint
        # 사진에서 읽은 라벨코드. 이름보다 확실한 신호이므로 버리지 않고 들고 간다.
        self.label_code = label_code
        self.quantity = quantity   # None 이면 고객이 수량을 말하지 않은 것
        self.unit_expr = unit_expr or ""
        self.source = source
        self.source_ref = source_ref
        self.turn = turn
        self.match = None       # MatchResult
        self.rejected = False   # 고객이 이 품목이 아니라고 함
        self.chosen = None      # 후보 중 고객이 고른 item_code
        self.alternatives = []  # 되물을 대체 후보
        self.unavailable = False   # DB 에 없어 취급하지 않는다고 판정된 줄
        self.notice_shown = False  # 미취급 사실을 고객에게 이미 알렸는가
        self.notfound_turns = 0    # 몇 턴째 못 찾고 있는가
        self.reject_turns = 0      # 아니라고 한 뒤 몇 턴째 대안을 못 고르고 있는가
        self.drop_reason = ""      # 주문에서 뺀 이유. 고객에게 뭐라고 알릴지가 달라진다
        self.packs = None       # 무게 표현을 환산한 포장 개수
        self.unit_note = None   # '요청 2kg' 처럼 근거를 남긴다

    @property
    def key(self):
        return (self.name_hint or self.raw_text or "").strip()

    @property
    def origin(self):
        if self.source == "image":
            return "%s턴 · %s" % (self.turn, self.source_ref or "이미지")
        return "%s턴 · 텍스트" % self.turn


class OrderState:
    def __init__(self):
        self.lines = []
        self.receiver = Field()
        self.phone = Field()
        self.address_base = Field()
        self.address_detail = Field()
        self.zipno = None
        self.road_addr = None
        self.addr_api = {}       # 정제 전/후, 응답 요약. 관찰 패널이 그대로 보여준다
        self.phone_second = None # 이미지 전화번호 2차 판독값. 1차와 다르면 PHONE_MISMATCH
        self.images = []         # [{ref, kind, read}] LLM 이 판별한 이미지 종류
        self.payment_proof = None # 입금증 이미지 ref. 받았어도 입금 확인은 사람이 한다
        self.invoice_sig = None  # 마지막으로 보여준 거래명세서의 지문
        self.done_shown = False  # 마무리 인사를 이미 했는가
        self.ask_rounds = {}     # 필수 항목별로 몇 턴째 물었는데도 못 받았는가
        self.upsell_shown = 0    # 추가 구매를 권한 횟수. 반복해서 조르지 않기 위한 것

    def count_unanswered(self, asked_fields):
        """지난 턴에 물었던 항목이 이번에도 비어 있으면 횟수를 올린다.

        고객이 '없어요' 라고 답해도 값은 여전히 비어 있으므로, 코드는 계속 물어야 할
        것으로 본다. 그러면 같은 질문이 무한히 반복된다. 몇 번까지 묻고 포기할지는
        세어 두어야 판단할 수 있다."""
        for attr in asked_fields or []:
            if not getattr(self, attr, None):
                self.ask_rounds[attr] = self.ask_rounds.get(attr, 0) + 1

    # ---------------------------------------------------------------- 누적
    def apply(self, out, turn, catalog=None, policies=None, each_hint=False, user_text=""):
        """LLM 출력 한 턴치를 누적 상태에 반영한다. 반환값은 변화 요약."""
        before = self.snapshot()

        for op in out.get("item_ops") or []:
            self._apply_op(op, turn, catalog, policies, each_hint)

        # "각각 몇 개씩" 이라고 물었는데 고객이 "1개요" 라고만 답한 경우.
        # LLM 은 이걸 한 품목의 update 로만 보내는 일이 잦아, 나머지 줄이 계속 빈 채로 남는다.
        # 고객이 실제로 친 문장을 직접 보고 남은 줄을 채운다. LLM 출력 모양에 기대지 않는다.
        if each_hint:
            m = _QTY_ONLY.match(str(user_text or "").strip())
            if m:
                qty = float(m.group(1))
                qty = int(qty) if qty == int(qty) else qty
                self._fill_quantities(qty, m.group(2) or "", True)

        self.receiver.apply(out.get("receiver"), turn)
        self.phone.apply(out.get("phone"), turn)

        addr = out.get("address")
        if addr:
            # base 와 detail 은 반드시 분리해서 다룬다.
            # 주소 검색 API 는 상세주소가 붙은 문자열을 통째로 던지면 실패율이 올라간다.
            if addr.get("base"):
                self.address_base.apply(
                    {"value": addr["base"], "source": addr.get("source"),
                     "source_ref": addr.get("source_ref")}, turn)
            if addr.get("detail"):
                self.address_detail.apply(
                    {"value": addr["detail"], "source": addr.get("source"),
                     "source_ref": addr.get("source_ref")}, turn)

        return self.diff(before)

    def _find(self, hint, catalog, policies):
        """같은 품목을 가리키는 줄을 찾는다.

        고객 표현이 턴마다 달라진다. 사진에서 'A0026' 으로 담겼다가 다음 턴에
        '소꼬리' 로 불리는 식이다. 표현만 비교하면 같은 상품이 두 줄로 쌓이고,
        먼저 담긴 줄은 수량이 채워지지 않아 같은 질문을 무한히 반복하게 된다."""
        for l in self.lines:
            if l.key == hint:
                return l

        if catalog is None or not hint:
            return None

        m = M.match({"name_hint": hint}, catalog, policies, "full")
        if m.status != M.CONFIRMED:
            return None
        for l in self.lines:
            if l.match and l.match.status == M.CONFIRMED and l.match.code == m.code:
                return l
        return None

    def _fill_quantities(self, qty, unit, each, each_hint=False):
        """수량 표현만 온 경우 비어 있는 줄에 채운다. 반환값은 처리했는지 여부.

        '1개씩' 같은 답을 상품명으로 잡으면 없는 품목을 되묻는 무한 루프가 된다.
        each_hint 는 직전에 "각각 몇 개씩" 이라고 물었다는 뜻이다.
        그 질문에 "1개요" 라고 답했으면 각 품목에 1개로 보는 것이 자연스럽다."""
        blanks = [l for l in self.lines if l.quantity is None]
        if not blanks:
            return False
        targets = blanks if (each or each_hint or len(blanks) == 1) else []
        if not targets:
            # 여러 줄이 비었는데 '씩' 도 없으면 어느 쪽인지 알 수 없다. 되물어야 한다.
            return True
        for l in targets:
            l.quantity = qty
            if unit:
                l.unit_expr = unit
        return True

    def _apply_op(self, op, turn, catalog=None, policies=None, each_hint=False):
        act = (op.get("op") or "add").lower()
        hint = (op.get("name_hint") or op.get("raw_text") or "").strip()
        label = (op.get("label_code") or "").strip()

        # "이거 구매하고싶어요" 처럼 사진을 가리키는 말은 상품명이 아니다.
        # 그대로 두면 '이거' 라는 품목이 만들어지고, DB 에 없으니 취급하지 않는 상품으로
        # 판정되어 정작 사진에서 읽은 상품이 묻힌다.
        if _is_demonstrative(hint):
            hint = ""

        # 무엇을 가리키는지 전혀 없는 항목은 어떤 op 든 버린다. 빈 줄이 만들어지면
        # "'말씀하신 상품'이 어떤 상품인지…" 처럼 실체 없는 되물음이 나간다.
        # add 만 막으면 op="update" 같은 빈 항목이 아래로 흘러 새 줄을 만든다.
        if not hint and not label:
            return
        if not hint:
            # 라벨코드만 온 경우. 이름 자리가 비면 화면과 되물음에 빈 문자열이 남는다.
            hint = label

        # 수량 표현만 있는 발화는 상품명이 아니다
        m = _QTY_ONLY.match(hint)
        if m and not (op.get("label_code") or "").strip():
            qty = float(m.group(1))
            qty = int(qty) if qty == int(qty) else qty
            if self._fill_quantities(qty, m.group(2) or op.get("unit_expr") or "",
                                     bool(m.group(3)), each_hint):
                return

        if act == "remove":
            target = self._find(hint, catalog, policies)
            self.lines = [l for l in self.lines if l is not target]
            return

        existing = self._find(hint, catalog, policies)

        # 고객이 "그 상품이 아니다" — 확정을 풀고 대체 후보를 받을 상태로 둔다
        if act == "reject" and existing:
            existing.rejected = True
            existing.chosen = None
            return

        # 제시한 후보 중 고객이 하나를 고름 — 그 상품으로 확정 교체
        if act == "choose" and existing:
            code = (op.get("chosen_code") or "").strip()
            if code:
                existing.chosen = code
                existing.rejected = False
            return

        if act == "update" and existing:
            if op.get("quantity") is not None:
                existing.quantity = op["quantity"]
            if op.get("unit_expr"):
                existing.unit_expr = op["unit_expr"]
            return

        if existing:
            if label and not existing.label_code:
                existing.label_code = label
            # 같은 표현이 다시 나오면 수량을 더한다
            q = op.get("quantity")
            if q is not None:
                existing.quantity = (existing.quantity or 0) + q
            return

        self.lines.append(Line(
            raw_text=op.get("raw_text", ""),
            name_hint=hint,
            quantity=op.get("quantity"),
            unit_expr=op.get("unit_expr"),
            source=op.get("source", "text"),
            source_ref=op.get("source_ref"),
            turn=turn,
            label_code=label,
        ))

    # ---------------------------------------------------------------- 매칭
    def rematch(self, catalog, policies, mode):
        for line in self.lines:
            # 고객이 직접 고른 상품이 있으면 매칭 로직보다 우선한다
            if line.chosen:
                line.match = M.MatchResult(M.CONFIRMED, line.chosen, rule="고객 선택")
                line.alternatives = []
                continue

            line.match = M.match(
                {"name_hint": line.name_hint, "raw_text": line.raw_text,
                 "label_code": line.label_code}, catalog, policies, mode)

            # 확정됐지만 고객이 아니라고 한 건은, 같은 표현을 공유하는 상품을 후보로 제시한다
            if line.rejected and line.match.code:
                line.alternatives = catalog.alternatives(line.match.code)
            else:
                line.alternatives = []

        self._merge_same_product()
        self._mark_unavailable(catalog, policies.get_int("ASK_RETRY_LIMIT", 2))

    def _mark_unavailable(self, catalog, limit=2):
        """되물음을 언제까지 계속할지 정한다.

        코드가 묻는 것은 거래명세서를 완성하기 위해서다. 몇 번을 물어도 안 풀리면
        그 품목은 빼고 나머지를 진행해야 한다. 끝이 없는 되물음은 고객이 무슨 말을
        해도 같은 문장만 돌려주는 벽이 된다.

        빼는 이유는 두 가지이고 고객에게 할 말이 서로 다르다.
            not_found  DB 에 없다 → 취급하지 않는 상품
            rejected   고객이 아니라고 했는데 대안도 고르지 않았다 → 주문에서 뺌
        """
        for line in self.lines:
            if line.unavailable:
                continue

            # 고객이 아니라고 한 품목 — 대안을 고를 때까지만 되묻는다
            if line.rejected and line.alternatives:
                line.reject_turns += 1
                if line.reject_turns > limit:
                    line.unavailable = True
                    line.drop_reason = "rejected"
                continue
            line.reject_turns = 0

            if not (line.match and line.match.status == M.NOT_FOUND):
                line.notfound_turns = 0
                continue

            line.notfound_turns += 1
            near = M.near_candidates(line.key, catalog)
            if not near or line.notfound_turns >= limit:
                line.unavailable = True
                line.drop_reason = "not_found"

    def take_unavailable_notice(self):
        """아직 알리지 않은, 주문에서 뺀 품목을 (표현, 이유) 로 돌려준다."""
        out = []
        for line in self.lines:
            if line.unavailable and not line.notice_shown:
                line.notice_shown = True
                out.append((line.key, line.drop_reason or "not_found"))
        return out

    def _merge_same_product(self):
        """같은 상품을 가리키는 줄이 둘 이상이면 하나로 합친다.
        수량이 있는 쪽을 살리고, 둘 다 있으면 더한다."""
        seen, kept = {}, []
        for line in self.lines:
            code = line.match.code if (line.match and line.match.status == M.CONFIRMED) else None
            if code is None or code not in seen:
                if code is not None:
                    seen[code] = line
                kept.append(line)
                continue
            first = seen[code]
            if first.quantity is None:
                first.quantity = line.quantity
                first.unit_expr = line.unit_expr or first.unit_expr
            elif line.quantity is not None:
                first.quantity += line.quantity
        self.lines = kept

    # ---------------------------------------------------------------- 견적
    def quote(self, catalog, policies):
        """수량 × 단가로 소계, 무료배송 기준 초과 시 배송비 0원,
        단가 없는 항목이 있으면 합계 확정을 차단한다."""
        rows, subtotal, blocked = [], 0, False

        for line in self.lines:
            if line.unavailable:
                continue
            confirmed = line.match and line.match.status == M.CONFIRMED and not line.rejected
            code = line.match.code if confirmed else None
            pack = catalog.items.get(code, {}).get("pack_unit", "") if code else ""

            # 고객이 무게로 말했으면 포장 개수로 환산한다. "2키로"를 2개로 쓰면
            # 포장단위가 500g 인 상품에서 절반만 나간다.
            qty, note = U.convert(line.quantity, line.unit_expr, pack)
            line.packs = qty
            line.unit_note = note

            unit = catalog.price(code) if code else None
            amount = unit * qty if (unit is not None and qty) else None
            if amount is None:
                blocked = True
            else:
                subtotal += amount
            rows.append({
                "표현": line.key,
                "포장단위": pack,
                "매칭": catalog.display(code) if code else (line.match.status if line.match else "-"),
                "수량": qty,
                "요청": note or "",
                "단가": unit,
                "소계": amount,
                "근거": line.origin,
            })

        threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
        fee = policies.get_int("SHIPPING_FEE", 0)
        shipping = 0 if (threshold and subtotal >= threshold) else fee

        return {
            "rows": rows,
            "subtotal": subtotal,
            "shipping": shipping,
            "total": None if blocked else subtotal + shipping,
            "blocked": blocked,
        }

    # ---------------------------------------------------------------- 관찰
    def snapshot(self):
        return copy.deepcopy({
            "lines": [(l.key, l.quantity) for l in self.lines],
            "receiver": self.receiver.value,
            "phone": self.phone.value,
            "address_base": self.address_base.value,
            "address_detail": self.address_detail.value,
        })

    def diff(self, before):
        after = self.snapshot()
        changes = []

        b, a = dict(before["lines"]), dict(after["lines"])
        for k in a:
            if k not in b:
                changes.append("+ 품목 %s ×%s" % (k, a[k]))
            elif a[k] != b[k]:
                changes.append("~ 품목 %s ×%s → ×%s" % (k, b[k], a[k]))
        for k in b:
            if k not in a:
                changes.append("- 품목 %s" % k)

        for f in ("receiver", "phone", "address_base", "address_detail"):
            if before[f] != after[f]:
                changes.append("~ %s: %s → %s" % (f, before[f] or "(없음)", after[f]))

        return changes
