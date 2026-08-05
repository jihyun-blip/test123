# -*- coding: utf-8 -*-
"""
momo_bot_logs 읽기·쓰기.

Apps Script 웹앱을 거친다. 로그 시트를 비공개로 유지하면서도
두 테스터의 기록을 한곳에 모으고 보고서가 집계할 수 있게 하기 위해서다.

전송 실패가 앱을 멈추게 하지 않는다. 실험 중 앱이 멈추면 관찰 흐름이 끊긴다.
실패는 화면에 경고로 띄우고, 결과 화면의 CSV 다운로드로 수동 회수할 수 있게 한다.
"""
import json

import pandas as pd
import requests
import streamlit as st

from . import sheets

TABS = ["conversations", "turns", "field_verdicts", "flag_verdicts",
        "gaps", "notes", "policy_versions"]


def configured():
    return bool(sheets.secret("APPS_SCRIPT_URL") and sheets.secret("APPS_SCRIPT_TOKEN"))


def write(tab, rows):
    """반환값은 (성공여부, 메시지). 예외를 밖으로 던지지 않는다."""
    if not rows:
        return True, "보낼 행 없음"
    if not configured():
        return False, "APPS_SCRIPT_URL / APPS_SCRIPT_TOKEN 미설정"

    try:
        r = requests.post(
            sheets.secret("APPS_SCRIPT_URL"),
            json={"token": sheets.secret("APPS_SCRIPT_TOKEN"), "tab": tab, "rows": rows},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    if not data.get("ok"):
        return False, str(data.get("error"))

    msg = "%s 에 %d행 기록" % (tab, data.get("appended", 0))
    unknown = data.get("unknown_keys") or []
    if unknown:
        # 시트에 없는 컬럼으로 보낸 것. 조용히 넘기지 않고 드러낸다.
        msg += " · 시트에 없어 버려진 키: %s" % ", ".join(unknown)
    return True, msg


@st.cache_data(ttl=60, show_spinner=False)
def _fetch(url, token, tab):
    r = requests.get(url, params={"token": token, "tab": tab}, timeout=90)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error"))
    if "rows" not in data:
        # 예전 배포는 doGet 에 tab 인자가 없어 탭 목록만 돌려준다.
        # 쓰기는 되는데 읽기만 안 되는 상태라 원인을 짚어주지 않으면 찾기 어렵다.
        raise RuntimeError(
            "Apps Script 가 읽기를 지원하지 않는 예전 버전입니다. "
            "tools/apps_script_doPost.gs 를 다시 붙여넣고 새 버전으로 배포해주세요.")
    return pd.DataFrame(data.get("rows") or [])


def read(tab):
    """반환값은 (DataFrame, 오류메시지). 실패해도 빈 표를 돌려준다."""
    if not configured():
        return pd.DataFrame(), "APPS_SCRIPT_URL / APPS_SCRIPT_TOKEN 미설정"
    try:
        return _fetch(sheets.secret("APPS_SCRIPT_URL"),
                      sheets.secret("APPS_SCRIPT_TOKEN"), tab), None
    except Exception as e:
        return pd.DataFrame(), "%s: %s" % (type(e).__name__, e)


def clear_cache():
    _fetch.clear()


# ------------------------------------------------------------------ 행 조립
def _j(v):
    return json.dumps(v, ensure_ascii=False, default=str)


def _avg(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    return int(sum(nums) / len(nums)) if nums else ""


def build_rows(conv_id, tester, mode_label, model, state, quote, history,
               verdicts, sources, note, policy_version, started_at, ended_at,
               flag_settings):
    """대화 하나가 끝났을 때 각 탭에 넣을 행을 한꺼번에 만든다."""
    common = {"conversation_id": conv_id, "tester_name": tester,
              "knowledge_mode": mode_label, "policy_version_id": policy_version}

    conv = dict(common)
    conv.update({
        "started_at": started_at, "ended_at": ended_at, "model": model,
        "flag_settings_json": _j(flag_settings),
        "turn_count": len(history),
        "image_count": sum(len(h.get("img_refs") or []) for h in history),
        "final_receiver": state.receiver.value or "",
        "final_phone": state.phone.value or "",
        "final_address_base": state.address_base.value or "",
        "final_address_detail": state.address_detail.value or "",
        "final_zipno": state.zipno or "",
        "final_road_addr": state.road_addr or "",
        "item_count": len(state.lines),
        "subtotal": quote["subtotal"],
        "shipping_fee": quote["shipping"],
        "total": quote["total"] if quote["total"] is not None else "",
        # 토큰·지연은 대화 단위로도 남긴다. turns 를 매번 합산하지 않고 보고서에서 바로 쓰기 위해서다.
        "tokens_in": sum((h.get("usage") or {}).get("input") or 0 for h in history),
        "tokens_out": sum((h.get("usage") or {}).get("output") or 0 for h in history),
        "latency_avg_ms": _avg(h.get("latency_ms") for h in history),
        "latency_max_ms": max([h.get("latency_ms") or 0 for h in history] or [0]),
        "note": note,
    })

    turns = []
    for h in history:
        api = h.get("addr_api") or {}
        turns.append(dict(common, **{
            "turn_no": h["turn"], "timestamp": h.get("at", ""),
            "user_text": h["user"], "image_refs": ",".join(h.get("img_refs") or []),
            "bot_text": h["bot"],
            "llm_raw_json": _j(h.get("out")),
            "state_diff_json": _j(h.get("diff")),
            "used_refs_json": _j((h.get("out") or {}).get("used_refs")),
            "missing_info_json": _j((h.get("out") or {}).get("missing_info")),
            "flags_raised": ",".join(f.key for f in h.get("flags") or []),
            "auto_detect_hits": ",".join(d["감지"] for d in h.get("detect") or []),
            "addr_keyword_raw": api.get("raw", ""),
            "addr_keyword_clean": api.get("clean", ""),
            "addr_error_code": api.get("error", ""),
            "addr_total_count": api.get("total", ""),
            "addr_road_addr": api.get("road_addr", ""),
            "addr_zipno": api.get("zipno", ""),
            "latency_ms": h.get("latency_ms", ""),
            "images_json": _j((h.get("out") or {}).get("images")),
        }))

    fields = []
    for key, v in verdicts.items():
        fields.append(dict(common, **{
            "field_type": key, "field_key": "",
            "final_value": v.get("final_value", ""),
            "source": sources.get(key, ""), "source_ref": "", "source_turn": "",
            "verdict": v["verdict"], "cause_tag": v.get("cause") or "",
            "judged_at": "", "note": "",
        }))

    # 이번 대화에서 뜬 플래그. 정탐·미탐 판정은 사람이 나중에 채운다.
    raised = {}
    for h in history:
        for f in h.get("flags") or []:
            raised.setdefault(f.key, (f.value, f.evidence, h["turn"]))
    flag_rows = [dict(common, **{
        "flag_key": k, "flag_value": v[0], "expected": "", "raised": "Y",
        "verdict": "", "behavior_verdict": "", "raised_turn": v[2],
        "evidence": v[1], "judged_at": "", "note": "",
    }) for k, v in raised.items()]

    gaps = []
    for h in history:
        for mi in ((h.get("out") or {}).get("missing_info") or []):
            gaps.append(dict(common, **{
                "turn_no": h["turn"], "asked": mi.get("asked", ""),
                "needed": mi.get("needed", ""),
                "found": "Y" if mi.get("found") else "N",
                "source_rule": "NO_PRODUCT_FACT_GUESS", "category": "", "logged_at": "",
            }))

    notes = [dict(common, **{"turn_no": "", "note_text": note, "tag": "", "logged_at": ""})] \
        if note else []

    return {"conversations": [conv], "turns": turns, "field_verdicts": fields,
            "flag_verdicts": flag_rows, "gaps": gaps, "notes": notes}
