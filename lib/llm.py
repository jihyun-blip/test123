# -*- coding: utf-8 -*-
"""
LLM 입출력 계약.

LLM 은 이해만 하고 판단과 계산은 코드가 한다.
자연어·이미지를 구조화된 데이터로 바꾸는 것과, 확정된 값을 문장으로 옮기는 것만 맡는다.

매 턴 LLM 이 돌려주는 것은 "이번 턴에서 파악한 변경"이지 누적 목록 전체가 아니다.
null 은 "이번 턴에 언급 없음"이며, 코드가 기존 값을 유지한다.
"""
import difflib
import json
import re

from . import matching as M

# 100만 토큰당 달러. 비용 추정용이며 정확한 청구액이 아니다.
PRICING = {
    "gemini-3.5-flash":     (0.15, 1.25),
    "gemini-3.1-pro":       (2.00, 12.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash":     (0.15, 1.25),
    "gemini-2.5-pro":       (1.25, 10.00),
}
DEFAULT_PRICE = (0.15, 1.25)

# 모델별로 사고 끄기 인자를 받는지 한 번만 확인하고 기억한다.
_NO_THINKING = {}

# 호출마다 새로 만들면 연결을 매번 다시 맺는다.
_CLIENTS = {}

OUTPUT_CONTRACT = """\
반드시 아래 JSON 하나만 출력한다. 설명 문장을 덧붙이지 않는다.

{
  "reply": "고객에게 보낼 답변 문장",
  "item_ops": [
    {"op":"add|update|remove|reject|choose","raw_text":"뒷다리 3개","name_hint":"뒷다리",
     "quantity":3,"unit_expr":"개","label_code":null,"printed_name":null,
     "chosen_code":null,"source":"text|image","source_ref":"turn_3|img_2"}
  ],
  "receiver": {"value":"홍길동","source":"text","source_ref":"turn_2"},
  "phone": null,
  "address": {"base":"서울시 강남구 테헤란로 123","detail":"101동 1002호",
              "source":"image","source_ref":"img_2"},
  "intent": "order|question|payment_claim|info_provide|complaint|smalltalk|other",
  "handoff_request": false,
  "angry": false,
  "missing_info": [{"asked":"원산지가 어디예요?","needed":"상품별 원산지","found":false}],
  "used_refs": {"products":["A0022"],"synonyms":["뒷다리"],"policies":["TONE"]},
  "images": [{"ref":"img_1","kind":"product|address|payment|other","read":"이미지에서 읽은 것 요약"}]
}

규칙:
- item_ops 는 이번 턴의 변경만 담는다. 누적 목록 전체를 다시 보내지 않는다.
  "삼겹살은 빼주세요" → remove, "3개로 바꿔주세요" → update.
  고객이 확정된 품목이 아니라고 하면 → reject.
  제시한 후보 중 하나를 고르면 → choose 와 chosen_code.
- 이번 턴에 언급이 없는 항목은 null 로 둔다. 빈 문자열을 쓰지 않는다.
- address 는 base(도로명/지번까지)와 detail(동·호)로 반드시 분리한다.
- unit_expr 는 고객이 실제로 쓴 표현("개","키로","박스")을 그대로 담는다.
- reply 에 금액·품목 목록·계좌번호를 쓰지 않는다. 거래명세서와 되물음 문장은 코드가 이미
  조립해 고객에게 전달했다. reply 에는 그 뒤에 덧붙일 말만 쓴다.
  덧붙일 말이 없으면 reply 를 빈 문자열로 둔다. 같은 내용을 반복하지 않는다.
- 코드가 되묻고 있는 중이면 "담아드렸어요", "주문이 완료되었어요" 처럼 끝난 것으로 말하지 않는다.
  아직 확정되지 않은 상태이므로 모순된 안내가 된다.
- 한 번에 한 가지만 묻는다. 어느 상품인지 되묻는 중에 성함·연락처·주소를 함께 묻지 않는다.
  고객이 무엇에 답해야 할지 모르게 된다.
- 직전 턴에 챗봇이 무언가를 물었다면, 고객의 짧은 답은 그 질문에 대한 값이다.
  "성함을 알려주세요" 뒤의 "모모" 는 receiver 값이고, "몇 개 필요하신가요" 뒤의 "3" 은 수량이다.
  단답이라고 해서 흘려보내지 않는다.
- 입금증을 먼저 요구하지 않는다. 고객이 보내오면 받았다고만 하고, 요청하는 말은 하지 않는다.
- 고객이 A0013 같은 품목코드를 적으면 label_code 에 넣는다. name_hint 에 코드를 넣지 않는다.
- reply 에 품목코드를 쓰지 않는다. 고객은 코드가 무엇인지 모른다. 항상 상품명으로 말한다.
- 수령인·연락처·주소는 글로 받는 편이 정확하다. 먼저 사진으로 보내라고 권하지 않는다.
  고객이 사진을 보내오면 읽으면 된다.
- DB 에 없는 사실(원산지·성분·유통기한·보관법)은 추측하지 않고 missing_info 에 기록한다.

이미지 규칙:
- 고객은 이미지의 종류를 알려주지 않는다. 각 이미지가 무엇인지 스스로 판별해 images 에 적는다.
    product  카탈로그 게시물 캡처 등 상품 사진
    address  손글씨 주소, 주소 캡처, 연락처 화면
    payment  입금 확인증 등 결제 증빙
    other    그 밖의 것
- product 이미지에서는 라벨코드와 인쇄된 상품명을 같은 호출에서 함께 읽어
  label_code 와 printed_name 에 각각 넣는다. 하나만 읽히면 나머지는 null 로 둔다.
  둘을 합쳐 하나로 만들지 않는다. 두 값이 서로를 검증하는 독립 신호이기 때문이다.
- address 이미지에서는 수령인·전화번호·주소를 읽는다. 주소는 base 와 detail 로 나눈다.
- 여러 장이 오면 각 결과가 어느 이미지에서 나왔는지 source_ref 에 img_1, img_2 형태로 적는다.
- 읽히지 않는 글자를 추측해 채우지 않는다. 확신이 없으면 null 로 둔다.
"""


def estimate_tokens(text):
    """한국어는 대략 2자당 1토큰. 정확한 값이 아니라 감을 잡기 위한 추정이다."""
    return max(1, int(len(str(text or "")) / 2))


def cost_usd(model, tin, tout):
    pin, pout = PRICING.get(model, DEFAULT_PRICE)
    return (tin / 1_000_000) * pin + (tout / 1_000_000) * pout


def candidates_for(text, catalog, mode, limit=8):
    """상품 마스터 전체를 프롬프트에 붓지 않는다. 검색으로 좁힌 것만 넘긴다."""
    text = str(text or "")
    hits = []

    if mode == "full":
        for expr, codes in list(catalog.by_synonym.items()) + list(catalog.by_canonical.items()):
            if expr and expr in text:
                hits.extend(codes)
        seen, out = set(), []
        for c in hits:
            if c not in seen:
                seen.add(c)
                out.append(c)
        if out:
            return out[:limit]

    # 축소 모드이거나 사전에서 못 찾았을 때 — 표시명 문자열 유사도 상위 5개
    scored = sorted(
        ((difflib.SequenceMatcher(None, text, catalog.display(c)).ratio(), c) for c in catalog.items),
        reverse=True)[:5]
    return [c for _, c in scored]


def build_system(policies, mode):
    """축소 모드는 지침 DB 를 쓰지 않고 일반적인 CS 지시문만 사용한다."""
    if mode == "reduced":
        return ("당신은 온라인 식료품 쇼핑몰의 고객 상담 챗봇입니다. "
                "친절하게 응대하고 고객의 주문을 도와주세요.\n\n" + OUTPUT_CONTRACT)

    persona = policies.get("PERSONA")
    lines = ["당신은 모모플러스의 주문 상담 담당자입니다."]
    if persona:
        lines.append("말투와 태도는 '%s' 입니다." % persona)
    lines += [
        "고객이 주문과 무관한 말을 걸어도 사람처럼 짧게 받아준 뒤 하던 일로 돌아옵니다.",
        "안내문을 읽는 듯한 문어체를 쓰지 않습니다.",
        "",
        "아래 운영 지침을 반드시 따릅니다.",
        "",
    ]
    for r in policies.prompt_rules():
        lines.append("- [%s] %s = %s : %s" % (
            r.get("구분", ""), r.get("키", ""), r.get("값", ""), r.get("설명", "")))
    lines += ["", OUTPUT_CONTRACT]
    return "\n".join(lines)


def build_user(text, state, catalog, cand_codes, mode, history=None,
               fixed_reply=None, upsell=None, pending=None, recent=4):
    parts = []

    # 전체 이력을 재전송하지 않는다. 최근 N턴만 보낸다.
    # 직전에 무엇을 물었는지 모르면 '후지요' 같은 답을 해석할 수 없다.
    if history:
        conv = []
        for h in history[-recent:]:
            conv.append("  고객: " + str(h.get("user") or ""))
            conv.append("  챗봇: " + str(h.get("bot") or "").replace("\n", " / "))
        parts.append("[최근 대화]\n" + "\n".join(conv))

    if fixed_reply:
        parts.append("[코드가 이미 고객에게 보낸 문장 — 반복하지 말 것]\n" + fixed_reply)
    if upsell:
        rows = "\n".join("  - %s %s원" % (s["name"], f"{s['price']:,}")
                         for s in upsell.get("suggestions") or [])
        parts.append(
            ("[무료배송까지 %s원 남음 — 자연스럽게 추가 구매를 권해볼 수 있다]\n"
             "%s 이상이면 배송비가 0원이 된다. 아래 상품만 제안한다. 목록에 없는 상품을 지어내지 않는다.\n%s\n"
             "강요하지 않는다. 한 문장으로 가볍게 권하고, 고객이 원하지 않으면 더 언급하지 않는다.")
            % (f"{upsell['gap']:,}", f"{upsell['threshold']:,}원", rows))

    if pending:
        want = []
        if pending.get("missing"):
            want.append("아직 못 받은 필수 정보: " + ", ".join(pending["missing"]))
        if pending.get("detail"):
            want.append("상세주소(동·호)가 없음 — 요청 수준: %s" % pending.get("detail_rule", "권장"))
        if want:
            parts.append(
                "[아직 채우지 못한 것 — 이번 답변에서 자연스럽게 물어본다]\n  "
                + "\n  ".join(want)
                + "\n이미 받은 정보는 절대 다시 묻지 않는다. 고객이 다른 이야기를 했더라도"
                  " 짧게 답한 뒤 위 항목을 물어 주문 흐름으로 돌아온다.")

    if state.lines or state.receiver or state.address_base:
        cur = []
        for l in state.lines:
            mark = ""
            if l.rejected:
                alts = ", ".join("%s=%s" % (c, catalog.display(c)) for c in l.alternatives)
                mark = (" ← 고객이 아니라고 해서 되묻는 중. 고객이 하나를 고르면 "
                        'op="choose" 와 chosen_code 로 돌려준다. 후보: %s' % (alts or "없음"))
            elif l.match and l.match.status == M.AMBIGUOUS:
                # 코드를 알려주지 않으면 고객이 골라도 chosen_code 를 채울 수 없다
                opts = ", ".join("%s=%s" % (c, catalog.display(c)) for c in l.match.candidates)
                mark = (" ← 어느 상품인지 되묻는 중. 고객이 하나를 고르면 "
                        'op="choose", name_hint="%s", chosen_code=해당코드 로 돌려준다. 후보: %s'
                        % (l.key, opts))
            cur.append("  - %s ×%s%s" % (l.key, l.quantity, mark))
        if cur:
            parts.append("[현재까지 담긴 품목]\n" + "\n".join(cur))
        info = []
        for label, f in (("수령인", state.receiver), ("전화", state.phone),
                         ("주소", state.address_base), ("상세주소", state.address_detail)):
            if f:
                info.append("  - %s: %s" % (label, f.value))
        if info:
            parts.append("[이미 확보한 정보 — 다시 묻지 않는다]\n" + "\n".join(info))

    if cand_codes:
        rows = []
        for c in cand_codes:
            r = catalog.items.get(c, {})
            if mode == "reduced":
                # 축소 모드는 표시명과 가격만 전달한다
                rows.append("  - %s / %s원" % (catalog.display(c), catalog.price(c)))
            else:
                # 전체 모드는 시트에 있는 모든 컬럼을 그대로 넘긴다
                rows.append("  - " + " / ".join(
                    "%s=%s" % (k, v) for k, v in r.items() if str(v).strip()))
        parts.append("[후보 상품]\n" + "\n".join(rows))

    parts.append("[고객 발화]\n" + str(text or ""))
    return "\n\n".join(parts)


def repair(s):
    """닫는 괄호가 빠진 채로 끝난 JSON 을 이어 붙인다.

    모델이 finish_reason=STOP 으로 정상 종료했는데도 마지막 } 를 빠뜨리는 일이 있다.
    내용은 멀쩡한데 통째로 버리면 그 턴이 목 모드로 떨어져 관찰이 끊긴다."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    out = s.rstrip()
    if in_str:
        out += '"'
    out = re.sub(r",\s*$", "", out)          # 끝에 남은 쉼표
    for ch in reversed(stack):
        out += "}" if ch == "{" else "]"
    return out


def parse(raw):
    """모델이 코드펜스를 붙이거나 앞뒤에 말을 덧붙여도 JSON 을 건져낸다."""
    s = str(raw or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        return json.loads(s)
    except Exception:
        pass

    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    # 여기까지 실패했다면 중간에 잘렸을 가능성이 크다. 괄호를 맞춰 한 번 더 시도한다.
    i = s.find("{")
    if i >= 0:
        try:
            return json.loads(repair(s[i:]))
        except Exception:
            pass
    return None


RETRYABLE = ("503", "429", "unavailable", "high demand", "overloaded",
             "resource_exhausted", "deadline")


def call(api_key, model, system, user, images=None, attempts=3):
    """반환값은 (출력 dict, 원본 텍스트, 사용량 dict).

    503 UNAVAILABLE 은 모델 쪽 일시 과부하라 잠시 뒤 다시 부르면 대개 통과한다.
    한 번 실패했다고 목 모드로 떨어뜨리면 관찰이 끊긴다."""
    import time as _t

    last = None
    for i in range(attempts):
        try:
            return _call_once(api_key, model, system, user, images)
        except Exception as e:
            last = e
            msg = str(e).lower()
            if i == attempts - 1 or not any(k in msg for k in RETRYABLE):
                raise
            _t.sleep(1.5 * (i + 1))
    raise last


def _call_once(api_key, model, system, user, images=None):
    if not api_key:
        return None, "", {}

    from google import genai
    from google.genai import types

    client = _CLIENTS.get(api_key)
    if client is None:
        client = _CLIENTS[api_key] = genai.Client(api_key=api_key)

    contents = []
    for img in images or []:
        contents.append(types.Part.from_bytes(data=img["bytes"], mime_type=img["mime"]))
    contents.append(user)

    cfg = {
        "system_instruction": system,
        "response_mime_type": "application/json",
        "temperature": 0.2,
    }

    # 이 작업은 추론이 아니라 추출이다. 사고 단계를 끄면 응답이 빨라지고,
    # 사고 파트가 섞여 JSON 파싱이 깨지는 일도 줄어든다.
    if _NO_THINKING.get(model, True):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0), **cfg),
            )
            return _unpack(resp, system, user)
        except Exception as e:
            # 모델이 이 인자를 안 받는 경우에만 한 번 더 부른다.
            # 다른 이유의 실패까지 재시도하면 한 턴에 API 를 두 번 태워 응답이 두 배로 느려진다.
            msg = str(e).lower()
            if not any(k in msg for k in ("thinking", "not supported", "unknown field",
                                          "invalid_argument", "unsupported")):
                raise
            _NO_THINKING[model] = False   # 이 모델은 앞으로 시도하지 않는다

    resp = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(**cfg),
    )
    return _unpack(resp, system, user)


def _unpack(resp, system, user):

    # resp.text 는 파트가 여러 개이거나 사고(thinking) 파트가 섞이면 비어 오기도 한다.
    # 비면 후보의 파트를 직접 훑어 텍스트를 모은다.
    text = ""
    try:
        text = resp.text or ""
    except Exception:
        pass
    if not text:
        for c in (getattr(resp, "candidates", None) or []):
            for part in (getattr(getattr(c, "content", None), "parts", None) or []):
                if getattr(part, "text", None):
                    text += part.text

    finish = ""
    for c in (getattr(resp, "candidates", None) or []):
        finish = str(getattr(c, "finish_reason", "") or "")
        break

    um = getattr(resp, "usage_metadata", None)
    usage = {
        "input": getattr(um, "prompt_token_count", None) or estimate_tokens(system + user),
        "output": getattr(um, "candidates_token_count", None) or estimate_tokens(text),
        "estimated": um is None,
        "finish_reason": finish,
    }
    return parse(text), text, usage


PHONE_RECHECK = (
    "이미지에서 전화번호만 다시 읽는다. 앞서 무엇을 읽었는지는 고려하지 말고 처음 보듯 읽는다. "
    '출력은 {"phones":[{"ref":"img_1","value":"010-1234-5678"}]} 형식의 JSON 하나뿐이다. '
    "읽히지 않으면 value 를 null 로 둔다."
)


def recheck_phone(api_key, model, images):
    """전화번호는 잘못 읽혀도 형식이 유효하면 어떤 검증에도 걸리지 않는다.
    그래서 같은 이미지를 한 번 더 읽어 결과가 다르면 PHONE_MISMATCH 를 부여한다."""
    if not api_key or not images:
        return None
    try:
        out, _, usage = call(api_key, model, PHONE_RECHECK, "전화번호를 읽어라.", images)
    except Exception:
        return None
    if not out:
        return None
    vals = [p.get("value") for p in (out.get("phones") or []) if p.get("value")]
    return {"values": vals, "usage": usage}


# ------------------------------------------------------------------ 목 모드
_QTY = re.compile(r"([가-힣A-Za-z]+)\s*([0-9]+)\s*(개|키로|kg|박스|팩|근)?")
_PHONE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
_ADDR = re.compile(r"([가-힣]+(?:시|도)\s*[가-힣]+(?:시|군|구)[^,\n]*)")


def mock(text, catalog, turn):
    """API 키가 없어도 화면이 뜨고 조작이 가능해야 한다.
    규칙 기반이라 품질은 낮지만 흐름 전체를 눌러볼 수 있다."""
    out = {"reply": "", "item_ops": [], "receiver": None, "phone": None,
           "address": None, "intent": "other", "handoff_request": False,
           "angry": False, "missing_info": [], "used_refs": {}}

    t = str(text or "")

    for m in _QTY.finditer(t):
        name, qty, unit = m.group(1), int(m.group(2)), m.group(3) or "개"
        if name in catalog.by_synonym or name in catalog.by_canonical:
            out["item_ops"].append({
                "op": "add", "raw_text": m.group(0), "name_hint": name,
                "quantity": qty, "unit_expr": unit,
                "source": "text", "source_ref": "turn_%d" % turn})

    if not out["item_ops"]:
        for expr in list(catalog.by_synonym) + list(catalog.by_canonical):
            if expr and expr in t:
                out["item_ops"].append({
                    "op": "add", "raw_text": expr, "name_hint": expr, "quantity": None,
                    "unit_expr": None, "source": "text", "source_ref": "turn_%d" % turn})
                break

    p = _PHONE.search(t)
    if p:
        out["phone"] = {"value": p.group(0), "source": "text", "source_ref": "turn_%d" % turn}

    a = _ADDR.search(t)
    if a:
        base = a.group(1).strip()
        detail = None
        d = re.search(r"(\d+동\s*\d+호|\d+호)", t)
        if d:
            detail = d.group(1)
            base = base.replace(detail, "").strip()
        out["address"] = {"base": base, "detail": detail,
                          "source": "text", "source_ref": "turn_%d" % turn}

    if re.search(r"화(났|나)|짜증|불만", t):
        out["angry"] = True
    if re.search(r"상담원|사람", t):
        out["handoff_request"] = True
    if re.search(r"입금(했|완료)", t):
        out["intent"] = "payment_claim"
    elif out["item_ops"]:
        out["intent"] = "order"
    elif "?" in t or re.search(r"뭐|어때|맛있", t):
        out["intent"] = "question"

    if re.search(r"아니|말고|틀렸|다른거", t):
        out["item_ops"] = [{"op": "reject", "name_hint": ""}]

    out["reply"] = "(목 모드 응답) 말씀 확인했습니다."
    return out
