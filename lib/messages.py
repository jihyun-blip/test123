# -*- coding: utf-8 -*-
"""
화면과 고객 문장의 언어별 문구표.

코드가 만드는 문장은 두 종류다.
    bot  고객에게 나가는 문장 (거래명세서·되물음·안내)
    ui   테스터가 보는 화면 (탭·라벨·버튼·판정 항목)
둘 다 파이썬 문자열에 박혀 있으면 언어를 늘릴 수 없다. 태국인 테스터가
한국어 화면에서 한국어 답변을 읽고 판정할 수는 없기 때문에 여기로 모은다.

한국어 문구는 지금까지 쓰던 것과 글자 하나까지 같아야 한다.
한국어 동작이 달라지면 지금까지 쌓은 판정 기록과 비교가 끊긴다.

조사(은/는, 을/를, 이/가)는 한국어에만 있다. 언어별로 붙였다 뺐다 하려면
문구표 밖에서 처리해야 하므로, 같은 이름의 함수를 언어별로 다르게 동작시킨다.
"""
import re

DEFAULT_LANG = "th"
LANGS = ["th", "ko"]

# 진단 정보의 언어. 읽는 사람이 다르다.
#
#   고객 문장·화면 조작        태국 테스터와 태국 고객이 읽는다 → 세션 언어
#   플래그 근거·자동 감지·인계 메모  왜 떴는지 보고 고치는 한국 개발자가 읽는다 → 여기 값
#
# 이 문장들은 flag_verdicts.evidence / auto_detect_hits / notes 로 로그 시트에 쌓인다.
# 언어별로 다른 문장이 같은 컬럼에 섞이면 한국어 대화와 태국어 대화를 나란히 비교할 수 없다.
# 진단은 한 언어로 고정한다. 태국 직원이 이 부분까지 읽어야 하면 이 값만 바꾸면 된다.
DEV_LANG = "ko"

# 배송유형은 전사 원장(master_products)의 값이라 한국어로 들어온다.
# 고객에게 그대로 보여줄 수는 없어 여기서만 옮긴다.
SHIP_TYPE = {
    "ko": {},
    "th": {"냉동": "แช่แข็ง", "냉장": "แช่เย็น", "상온": "ปกติ"},
}

# 플래그 값은 코드가 문자열로 직접 비교하는 제어값이다. 값 자체는 절대 번역하지 않고
# 화면에 보여줄 때만 옮긴다. 시트·로그·조건문에는 언제나 한국어 원값이 남는다.
FLAG_ACTION = {
    "ko": {},
    "th": {"되물음": "ถามกลับ", "차단": "บล็อก", "미완료": "ยังไม่ครบ",
           "상담원연결": "ส่งต่อเจ้าหน้าที่", "검수필수": "ต้องตรวจ"},
}

# 어느 단계에서 상품이 확정됐는지. 관찰용 표시라 화면에서만 옮긴다.
MATCH_RULE = {
    "ko": {},
    "th": {
        "라벨코드": "รหัสฉลาก",
        "라벨코드-인쇄명 불일치": "รหัสฉลากกับชื่อที่พิมพ์ไม่ตรงกัน",
        "정식명 정확일치": "ชื่อทางการตรงกันพอดี",
        "정식명 중복": "ชื่อทางการซ้ำกัน",
        "정식명+유사어 중복": "ชื่อทางการกับคำพ้องซ้ำกัน",
        "유사어 정확일치": "คำพ้องตรงกันพอดี",
        "유사어 중복": "คำพ้องซ้ำกัน",
        "정규화 일치": "ตรงกันหลังจัดรูปแบบ",
        "정규화 후 중복": "ซ้ำกันหลังจัดรูปแบบ",
        "미발견": "ไม่พบ",
        "표현 없음": "ไม่มีคำที่ระบุสินค้า",
        "고객 선택": "ลูกค้าเลือกเอง",
        "후보 좁힘": "แคบตัวเลือกลงแล้ว",
        "유사도 미달(축소)": "ความคล้ายไม่ถึงเกณฑ์ (โหมดจำกัด)",
        "문자열 유사도(축소)": "ความคล้ายของข้อความ (โหมดจำกัด)",
        "유사도 상위5(축소)": "ความคล้าย 5 อันดับแรก (โหมดจำกัด)",
    },
}

KO = {
    # ---------------------------------------------------------------- 고객 문장
    "greeting": "안녕하세요 고객님!",
    "unknown_item": "말씀하신 상품",
    "money": "%s원",

    "reject_ask": "말씀하신 %s %s 중 어떤 것일까요?",
    "soldout_ask": "%s 지금 품절이에요. 대신 %s 어떠세요?",
    "ambiguous_attr": "%s 종류가 많아요. %s",
    "attr_species": "%s 중 어느 쪽일까요?",
    "attr_part": "%s 중에서요?",
    "ambiguous_top": "%s 여러 종류가 있어요. %s %s 이(가) 제일 많이 나가는데 이걸로 드릴까요?",
    "ambiguous_list": "%s %s 중 어떤 것을 말씀하시는 걸까요?",
    "notfound_near": "%s 이 중 어떤 것일까요? %s\n이 중에 없으면 사진 보내주시면 찾아드릴게요.",
    "notfound_bare": "%s 어떤 상품인지 조금만 더 알려주시겠어요? 사진을 보내주셔도 좋아요.",
    "order_ask_image": ("보내주신 사진에서 상품을 확인하지 못했어요. "
                        "상품명을 알려주시거나 라벨이 보이게 다시 찍어주시면 담아드릴게요."),
    "order_ask": "어떤 상품 찾으세요? 상품명을 말씀해주시거나 사진을 보내주시면 담아드릴게요.",
    "qty_ask_each": "%s 각각 몇 개씩 필요하신가요?",
    "qty_ask_example": "\n(예: %s 처럼 알려주세요)",
    "qty_example_item": "%s %d개",
    "qty_ask_one": "%s 몇 개 필요하신가요?",
    # 포장단위로 안 떨어지는 수량. 올려서 더 청구하지 않고 고객이 고르게 한다
    "pack_ask_two": "%s %s 단위로만 판매해요. %d개(%s)와 %d개(%s) 중 어느 쪽으로 드릴까요?",
    "pack_ask_one": "%s %s 단위로만 판매해요. %d개(%s)로 드릴까요?",
    "qty_marker": "몇 개",
    "qty_each_marker": "각각 몇 개씩",
    "blocked": "%s 가격을 확인하고 있어요. 확인되는 대로 총액 알려드릴게요.",
    "payment_proof": "입금증 받았습니다.",
    "given_up": "%s 확인이 어려워 담당자가 따로 확인드릴게요.",
    "done": "주문 감사합니다! 확인하는 대로 바로 보내드릴게요.",
    "ask_missing": "%s 알려주시겠어요?",
    "ask_detail": "택배 기사님이 찾아갈 수 있게 건물이나 눈에 띄는 표시를 알려주시겠어요?",
    "got_fields": "%s 확인했습니다.",

    "invoice_line": "%s %d개 %s",
    "ship_fee": "배송비 %s",
    "ship_fee_typed": "%s 배송비 %s",
    "ship_free": "배송비 0원 (%s 이상 무료배송)",
    "ship_free_typed": "%s 배송비 0원 (%s 이상 무료배송)",
    "ship_max_only": "배송비는 비싼 쪽 하나만 받아 %s",
    "invoice_total": "총 %s을 아래 계좌로 입금주시면 감사하겠습니다.",

    "drop_not_found": "%s 아직 취급하지 않는 상품이에요.",
    "drop_soldout": "%s 지금 품절이라 주문에서 뺐어요. 들어오면 알려드릴게요.",
    "drop_rejected": "%s 주문에서 뺐어요. 필요하시면 다시 말씀해주세요.",
    "drop_rest": "나머지로 도와드릴게요.",
    "no_reply": "(응답 없음)",

    "field_receiver": "받으실 분 성함",
    "field_phone": "연락처",
    "field_address": "배송지 주소",
    "got_receiver": "성함",
    "got_phone": "연락처",
    "got_address": "주소",

    # ---------------------------------------------------------------- 화면
    "ui_title": "기능 B 챗봇 테스트",
    "ui_tester": "테스터",
    "ui_mode": "지식 수준",
    "ui_mode_full": "전체",
    "ui_mode_reduced": "축소",
    "ui_mode_help": "축소 모드는 외부 개발사가 실제로 갖게 될 수준을 재현합니다",
    "ui_model": "모델",
    "ui_lang": "언어",
    "ui_lang_help": "상품명·유사어·단위가 갈리는 축",
    "ui_channel": "채널",
    "ui_channel_help": "판매가·배송비가 갈리는 축",
    "ui_refresh": "DB 새로고침",
    "ui_reset": "대화 초기화",
    "ui_build": "빌드 %s",

    "tab_report": "📊 보고서",
    "tab_chat": "💬 대화",
    "tab_verdict": "✅ 판정",
    "tab_data": "🗄 데이터",

    "chat_ended": "대화가 종료되었습니다. **✅ 판정** 탭에서 주문서를 확인하고 통과·실패를 찍어주세요.",
    "chat_image_only": "(사진만 보냄)",
    "chat_input": "고객 발화를 입력하세요",
    "chat_attached": "첨부: ",
    "chat_thinking": "답변 생성 중…",
    "chat_fallback": "⚠ %s 과부하 → %s 로 대체 응답",
    "chat_latency": "%.1f초%s",
    "chat_retried": " · 모델 과부하로 %d회 재시도",
    "chat_mock": "LLM 미사용 · 목 모드로 대체됨 — %s",
    "chat_raw": "모델이 실제로 돌려준 원문",

    "panel_order": "#### 주문 현황",
    "panel_no_flags": "발생한 플래그 없음",
    "panel_receiver": "수령자명",
    "panel_phone": "전화번호",
    "panel_address": "**주소**",
    "panel_addr_read": "추출: %s <small>%s</small>",
    "panel_addr_detail": "상세: %s",
    "panel_addr_api": "API: %s  \n우편번호: **%s**",
    "panel_addr_none": "API: 검색 결과 없음",
    "panel_no_items": "담긴 품목 없음",
    "panel_sum": "소계 **%s원** + 배송비 **%s원** = 합계 **%s**",
    "panel_blocked": "확정 차단",
    "panel_finish": "🧾 상담 완료 — 주문서 확정",
    "panel_ended": "종료됨 · 판정 탭으로 이동",
    "panel_observe": "이번 턴 관찰 패널",
    "panel_diff": "누적 상태 변화",
    "panel_no_diff": "변화 없음",
    "panel_detect": "자동 감지",
    "panel_none": "없음",
    "panel_gaps": "결핍 로그 (missing_info)",
    "panel_llm_raw": "LLM 원본 응답",
    "panel_used_refs": "참조한 데이터 (used_refs)",
    "panel_addr_api_cap": "주소 API",
    "panel_addr_api_none": "미호출",
    "panel_images": "이미지 판별",
    "panel_no_images": "업로드 없음",
    "panel_label_read": "라벨 2차 판독 (1차에서 품목을 못 건졌을 때)",
    "panel_phone2": "전화번호 2차 판독: %s",
    "panel_tokens": "토큰 입력 %s / 출력 %s %s · 모델 %s · 지식수준 %s",
    "panel_estimated": "(추정)",

    "vd_need_finish": "대화 탭에서 **상담 완료** 를 눌러야 판정할 수 있습니다.",
    "vd_title": "### 최종 주문서",
    "vd_no_items": "품목 없음",
    "vd_receiver": "수령자명",
    "vd_phone": "전화번호",
    "vd_address": "주소",
    "vd_address_detail": "상세주소",
    "vd_zip": "우편번호",
    "vd_total": "합계",
    "vd_handoff": "### 상담원 인계 메모",
    "vd_handoff_help": "코드가 상태와 플래그에서 뽑은 것입니다. 상담원이 확정 전에 확인할 목록입니다.",
    "vd_handoff_none": "확인할 사항이 없습니다. 그대로 확정하셔도 됩니다.",
    "vd_fields": "### 항목별 판정",
    "vd_fields_help": ("주문서가 자동으로 제대로 입력되었는지 항목별로 찍어주세요. "
                       "실패라면 원인까지 골라야 무엇을 고쳐야 하는지가 남습니다."),
    "vd_pass": "통과",
    "vd_fail": "실패",
    "vd_cause": "원인",
    # 플래그 판정. 값은 로그에 쌓이므로 코드는 한국어로 고정하고 화면만 언어를 탄다
    "vd_flags": "### 플래그 판정",
    "vd_flags_help": ("이번 대화에서 뜬 플래그가 제대로 뜬 것인지 찍어주세요. "
                      "정탐·오탐이 쌓여야 어떤 플래그를 고치거나 지울지 정할 수 있습니다."),
    "vd_flag_turn": "%d턴",
    "vd_no_flags": "이번 대화에서 뜬 플래그가 없습니다.",
    "fv_true": "정탐",
    "fv_false": "오탐",
    "fv_hold": "판단보류",
    "vd_missed": "떴어야 했는데 안 뜬 플래그",
    "vd_missed_hint": "플래그를 고르세요 (없으면 비워두세요)",
    "vd_missed_help": ("대화를 보고 떴어야 한다고 생각하는 플래그를 고르세요. "
                       "오탐보다 미탐이 찾기 어렵습니다. 없으면 비워두세요."),
    "vd_note": "관찰 메모",
    "vd_note_hint": "무엇이 부족했는지, 어떤 지침이 필요한지",
    "vd_save": "판정 저장",
    "vd_saved": "판정을 저장했습니다 — %s",
    "vd_new": "새 대화 시작",
    "vd_writing": "시트에 기록하는 중…",
    "vd_writing_tab": "%s 기록 중…",
    "vd_written": "기록 완료",
    "vd_log_off": "로그 미설정 — 이 세션 안에서만 집계됩니다",
    "vd_log_fail": "로그 기록 실패 — %s: %s",
    "vd_after_save": ("이어서 새 대화를 하시려면 위의 **새 대화 시작** 버튼을 눌러주세요. "
                      "(이 화면을 벗어났다 돌아오면 보입니다)"),

    "vf_invoice": "거래명세서 (품목·수량·가격)",
    "vf_address": "주소",
    "vf_receiver": "수령자명",
    "vf_phone": "전화번호",
    "cause_extract": "추출오류",
    "cause_match": "매칭오류",
    "cause_unit": "단위오해",
    "cause_nodb": "DB에없음",
    "cause_policy": "지침부족",
    "cause_etc": "기타",

    "rp_from_sheet": "로그 시트에서 읽었습니다. 두 테스터의 기록이 함께 집계됩니다.",
    "rp_sheet_fail": "로그 시트를 읽지 못해 이 세션 기록만 보여줍니다 — %s",
    "rp_sheet_lag": "시트에 기록은 됐지만 아직 읽히지 않습니다. 이 세션 기록으로 보여줍니다.",
    "rp_no_log": ("Apps Script 로그가 설정되지 않아 **이 브라우저 세션 안에서만** 집계됩니다. "
                  "새로고침하면 사라지니 아래 CSV 다운로드로 받아두세요."),
    "rp_empty": ("아직 판정된 대화가 없습니다. **💬 대화** 탭에서 대화를 진행하고 "
                 "**상담 완료 → 판정 저장** 을 하면 여기에 집계됩니다."),
    "rp_conversations": "테스트한 대화",
    "rp_calls": "LLM 호출",
    "rp_images": "업로드 이미지",
    "rp_tokens": "토큰 (추정)",
    "rp_tokens_delta": "입력 %s / 출력 %s",
    "rp_cost": "예상 비용",
    "rp_cost_delta": "대화 1건당 %s원",
    "rp_cost_note": "환율 %s원/$ 기준 · 상담 1만 건 환산 시 약 %s원",
    "rp_latency": "응답 시간 — 평균 %.1f초 · 최대 %.1f초 (자체 구축 시 체감 속도의 실측값)",
    "rp_by_field": "### 항목별 성공률",
    "rp_col_field": "항목",
    "rp_col_pass": "통과",
    "rp_col_fail": "실패",
    "rp_col_rate": "성공률",
    "rp_by_source": "### 입력 유형별 성공률",
    "rp_by_source_help": "같은 항목이라도 텍스트에서 왔는지 이미지에서 왔는지에 따라 난이도가 다릅니다.",
    "rp_col_source": "입력 유형",
    "rp_col_count": "건수",
    "rp_src_text": "텍스트",
    "rp_src_image": "이미지",
    "rp_no_data": "데이터 없음",
    "rp_causes": "### 실패 원인 순위",
    "rp_col_cause": "원인",
    "rp_no_fail": "실패 없음",
    "rp_by_mode": "### 지식 수준 모드별 비교",
    "rp_col_mode": "모드",
    "rp_col_conv": "대화",
    "rp_col_all_rate": "전체 성공률",
    "rp_notes": "### 남긴 메모",
    "rp_download": "결과 CSV 다운로드",

    "dt_caption": "시트 수정이 반영됐는지 확인하는 화면입니다. 실험 결과와는 무관합니다.",
    "dt_policies": "지침 DB",
    "dt_collision": "유사어 충돌 — AMBIGUOUS_ALIAS 가 떠야 할 지점",
    "dt_col_expr": "표현",
    "dt_col_items": "걸리는 상품",
    "dt_none": "없음",
    "dt_sheet_fail": "**%s** 를 읽지 못했습니다",
    "dt_sheet_warn": "**%s** 탭을 읽지 못해 폴백으로 돕니다 — %s",
    "dt_share_fix": ("시트 공유 설정이 풀렸습니다. **DB 새로고침 버튼 때문이 아닙니다.**\n\n"
                     "해당 구글 시트 → **공유** → **일반 액세스** 를 "
                     "`링크가 있는 모든 사용자` · **뷰어** 로 바꾼 뒤 "
                     "**DB 새로고침** 을 눌러주세요."),

    # 플래그 근거 · 자동 감지 (관찰 패널에서 테스터가 읽는다)
    "fl_no_alt": "대체 후보 없음",
    "fl_no_price": "가격없음",
    "fl_soldout": "'%s' → %s 품절. 대체 후보: %s",
    "fl_rejected": "고객이 '%s' → %s 이(가) 아니라고 함. 후보: %s",
    "fl_ambiguous": "'%s' 이(가) %d개 상품에 걸림 → %s",
    "fl_notfound": "'%s' 을(를) DB에서 찾지 못함 (%s)",
    "fl_missing_price": "단가 없는 항목: %s → 합계 확정 차단",
    "fl_receiver_missing": "수령인 이름 미확보",
    "fl_phone_dot": "'%s' → 소수점이 있음. 시트가 숫자로 저장해 앞자리 0 이 사라진 값",
    "fl_phone_zero": "'%s' → 0 으로 시작하지 않음",
    "fl_phone_len": "'%s' → 숫자 %d자리 (10~11자리 아님)",
    "fl_phone_mismatch": "1차 '%s' vs 2차 '%s' — 이미지 판독 결과 불일치",
    "fl_address_missing": "주소 자체가 없음",
    "fl_address_detail": "기본주소는 있으나 상세주소가 없음",
    "fl_address_image": "주소를 %s 에서 추출 — 육안 확인 필요",
    "fl_address_unverified": "'%s' 검색 결과 0건 — 우편번호 추출 실패",
    "fl_address_ambiguous": "검색 %d건인데 우편번호가 %d종류 (%s) — 주소 불완전 가능성",
    "fl_handoff": "고객이 상담원 연결을 명시적으로 요청",
    "fl_angry": "고객 불만·화남 감지",
    "fl_proof": "입금증 이미지 수신(%s) — 은행 내역과 대조 필요",
    "fl_payment_claim": "고객이 입금을 주장했으나 입금증 없음 — 상담원이 직접 확인 필요",
    "fl_payment_pending": "주문 정보는 모였으나 입금증 미수신",
    "fl_amount": "응답의 %s 이(가) 계산값과 불일치",

    "dt_col_hit": "감지",
    "dt_col_rule": "근거 규칙",
    "dt_col_body": "내용",
    "dt_amount": "금액 환각",
    "dt_amount_body": "응답의 %s 이(가) 계산값과 불일치 → AMOUNT_MISMATCH",
    "dt_state_lost": "상태 유실",
    "dt_state_lost_body": "이미 확보한 '%s' 정보를 다시 물음",
    "dt_schema": "스키마 결핍",
    "dt_schema_body": "'%s' → 필요한 정보: %s",
    "dt_payment": "입금 단정",
    "dt_payment_body": "응답에 입금 확인 취지 문구가 있음",
    "dt_question": "고객 질문 무시",
    "dt_question_body": "고객이 '%s' 라고 물었는데 답하지 않음",
    "dt_unavailable": "취급 여부 오안내",
    "dt_unavailable_body": "'%s' 을(를) 취급하지 않는다고 했으나 DB 에 있음 → %s",
    "dt_unavailable_kept": "'%s' 을(를) 없다고 했는데 주문에는 %s 이(가) 그대로 남아 있음",
    "dt_ask_missing": "되물음 누락",
    "dt_ask_missing_body": "모호 항목 %s 이(가) 있는데 후보 제시 없이 진행",
    "dt_unchecked": "미확인 통과",
    "dt_unchecked_body": "DB에 없는 표현 %s 을(를) 확인 없이 진행",
    "dt_smalltalk": "잡담 미복귀",
    "dt_smalltalk_body": "잡담·추천 후 진행 중이던 되물음으로 복귀하지 않음",
    "dt_extract": "추출 실패",
    "dt_extract_body": "주문 의도로 분류됐는데 item_ops 가 비어 있음",

    # 주문표(견적) 열 이름과 매칭 상태. 화면에만 쓰고 저장 키는 그대로 둔다
    "col_expr": "표현",
    "col_pack": "포장단위",
    "col_match": "매칭",
    "col_ship_type": "배송유형",
    "col_qty": "수량",
    "col_request": "요청",
    "col_price": "단가",
    "col_amount": "소계",
    "col_soldout": "품절",
    "col_pending_qty": "수량미정",
    "col_origin": "근거",
    "st_confirmed": "확정",
    "st_ambiguous": "모호",
    "st_not_found": "미발견",
    "st_conflict": "충돌",

    # 인계 메모 (판정 탭에서 테스터가 읽는다)
    "ho_request": "고객 요청",
    "ho_check": "확인 필요",
    "ho_missing": "미확보",
    "ho_eye": "육안 확인",
    "ho_payment": "입금",
    "ho_talk": "대화",
    "ho_drop_soldout": "%s 품절이라 주문에서 뺐습니다. 입고 예정을 안내해야 합니다.",
    "ho_drop_notfound": "%s 찾으셨으나 취급하지 않아 주문에서 뺐습니다. 취급 예정이면 안내가 필요합니다.",
    "ho_soldout": "%s 품절입니다. 대체 상품을 안내 중입니다 (%s).",
    "ho_ambiguous": "%s %s 중 무엇인지 확정되지 않았습니다.",
    "ho_notfound": "%s 상품 DB 에서 찾지 못했습니다.",
    "ho_price": "%s 의 단가가 없어 총액을 확정하지 못했습니다.",
    "ho_gaveup": "%s 여러 번 여쭈었으나 받지 못했습니다. 직접 확인이 필요합니다.",
    "ho_empty": "%s 아직 비어 있습니다.",
    "ho_detail": "상세주소가 없습니다. 건물명·표시 등 기사님이 찾아갈 단서가 필요합니다.",
    "ho_image": "%s 사진에서 읽었습니다(%s). 원본과 대조해주세요.",
    "ho_zip_none": "주소 검색 결과가 0건이라 우편번호를 얻지 못했습니다.",
    "ho_zip_many": "주소 검색에서 우편번호가 %d종류 나왔습니다(%s). 주소가 불완전할 수 있습니다.",
    "ho_proof": "입금증 이미지를 받았습니다(%s). 은행 내역과 대조해주세요.",
    "ho_no_proof": "입금 확인이 되지 않았습니다. 은행 내역에서 직접 확인해주세요.",
    "ho_handoff_req": "%d번째 턴에서 상담원 연결을 요청하셨습니다.",
    "ho_angry": "%d번째 턴에서 불만이 감지되었습니다.",
}

TH = {
    # ---------------------------------------------------------------- 고객 문장
    "greeting": "สวัสดีค่ะ คุณลูกค้า",
    "unknown_item": "สินค้าที่แจ้งมา",
    "money": "%s วอน",

    "reject_ask": "%s ที่แจ้งไว้ หมายถึงอันไหนคะ %s",
    "soldout_ask": "ตอนนี้ %s หมดค่ะ รับ %s แทนไหมคะ",
    "ambiguous_attr": "%s มีหลายแบบค่ะ %s",
    "attr_species": "เป็น %s คะ",
    "attr_part": "ส่วน %s คะ",
    "ambiguous_top": "%s มีหลายแบบค่ะ %s %s ขายดีที่สุด รับตัวนี้ไหมคะ",
    "ambiguous_list": "%s หมายถึงอันไหนคะ %s",
    "notfound_near": "%s หมายถึงอันไหนคะ %s\nถ้าไม่มีในนี้ ส่งรูปมาได้เลยค่ะ เดี๋ยวหาให้",
    "notfound_bare": "%s เป็นสินค้าแบบไหนคะ ส่งรูปมาก็ได้ค่ะ",
    "order_ask_image": ("ในรูปที่ส่งมา ยังดูไม่ออกว่าเป็นสินค้าอะไรค่ะ "
                        "บอกชื่อสินค้า หรือถ่ายให้เห็นฉลากอีกครั้งได้ไหมคะ"),
    "order_ask": "รับสินค้าอะไรดีคะ บอกชื่อสินค้าหรือส่งรูปมาได้เลยค่ะ",
    "qty_ask_each": "%s เอาอย่างละกี่ชิ้นคะ",
    "qty_ask_example": "\n(เช่น %s)",
    "qty_example_item": "%s %d ชิ้น",
    "qty_ask_one": "%s เอากี่ชิ้นคะ",
    "pack_ask_two": "%s ขายเป็น %s เท่านั้นค่ะ รับ %d ชิ้น (%s) หรือ %d ชิ้น (%s) ดีคะ",
    "pack_ask_one": "%s ขายเป็น %s เท่านั้นค่ะ รับ %d ชิ้น (%s) ไหมคะ",
    "qty_marker": "กี่ชิ้น",
    "qty_each_marker": "อย่างละกี่ชิ้น",
    "blocked": "กำลังเช็คราคา %s อยู่ค่ะ ได้แล้วจะแจ้งยอดรวมให้นะคะ",
    "payment_proof": "ได้รับสลิปแล้วค่ะ",
    "given_up": "%s ยังยืนยันไม่ได้ เดี๋ยวเจ้าหน้าที่ติดต่อกลับนะคะ",
    "done": "ขอบคุณสำหรับคำสั่งซื้อค่ะ ตรวจสอบแล้วจะจัดส่งให้ทันทีนะคะ",
    "ask_missing": "ขอ %s ด้วยค่ะ",
    "ask_detail": "ขอจุดสังเกตหรือชื่ออาคารที่คนส่งของหาเจอด้วยค่ะ",
    "got_fields": "รับ %s เรียบร้อยค่ะ",

    "invoice_line": "%s %d ชิ้น %s",
    "ship_fee": "ค่าส่ง %s",
    "ship_fee_typed": "ค่าส่ง%s %s",
    "ship_free": "ค่าส่ง 0 วอน (ซื้อครบ %s ส่งฟรี)",
    "ship_free_typed": "ค่าส่ง%s 0 วอน (ซื้อครบ %s ส่งฟรี)",
    "ship_max_only": "ค่าส่งคิดเฉพาะฝั่งที่แพงกว่า %s",
    "invoice_total": "ยอดรวม %s โอนเข้าบัญชีด้านล่างได้เลยค่ะ",

    "drop_not_found": "ตอนนี้ยังไม่มี %s จำหน่ายค่ะ",
    "drop_soldout": "%s หมดของ เลยเอาออกจากรายการก่อนนะคะ ของเข้าแล้วจะแจ้งค่ะ",
    "drop_rejected": "เอา %s ออกจากรายการแล้วค่ะ ถ้าต้องการแจ้งได้อีกครั้งนะคะ",
    "drop_rest": "ที่เหลือจัดให้เลยนะคะ",
    "no_reply": "(ไม่มีคำตอบ)",

    "field_receiver": "ชื่อผู้รับ",
    "field_phone": "เบอร์โทร",
    "field_address": "ที่อยู่จัดส่ง",
    "got_receiver": "ชื่อ",
    "got_phone": "เบอร์โทร",
    "got_address": "ที่อยู่",

    # ---------------------------------------------------------------- 화면
    "ui_title": "เครื่องมือทดสอบแชทบอท (ฟังก์ชัน B)",
    "ui_tester": "ผู้ทดสอบ",
    "ui_mode": "ระดับข้อมูล",
    "ui_mode_full": "ทั้งหมด",
    "ui_mode_reduced": "จำกัด",
    "ui_mode_help": "โหมดจำกัดจำลองระดับข้อมูลที่บริษัทพัฒนาภายนอกจะได้รับจริง",
    "ui_model": "โมเดล",
    "ui_lang": "ภาษา",
    "ui_lang_help": "แกนที่ทำให้ชื่อสินค้า คำพ้อง และหน่วยต่างกัน",
    "ui_channel": "ช่องทาง",
    "ui_channel_help": "แกนที่ทำให้ราคาขายและค่าส่งต่างกัน",
    "ui_refresh": "โหลดข้อมูลใหม่",
    "ui_reset": "เริ่มบทสนทนาใหม่",
    "ui_build": "บิลด์ %s",

    "tab_report": "📊 รายงาน",
    "tab_chat": "💬 บทสนทนา",
    "tab_verdict": "✅ ตรวจผล",
    "tab_data": "🗄 ข้อมูล",

    "chat_ended": "บทสนทนาจบแล้วค่ะ ไปที่แท็บ **✅ ตรวจผล** เพื่อดูใบสั่งซื้อและกดผ่าน/ไม่ผ่าน",
    "chat_image_only": "(ส่งมาแต่รูป)",
    "chat_input": "พิมพ์ข้อความของลูกค้า",
    "chat_attached": "แนบมา: ",
    "chat_thinking": "กำลังสร้างคำตอบ…",
    "chat_fallback": "⚠ %s โหลดเกิน → ตอบด้วย %s แทน",
    "chat_latency": "%.1f วินาที%s",
    "chat_retried": " · ลองใหม่ %d ครั้งเพราะโมเดลโหลดเกิน",
    "chat_mock": "ไม่ได้ใช้ LLM · ใช้โหมดจำลองแทน — %s",
    "chat_raw": "ข้อความดิบที่โมเดลตอบกลับมา",

    "panel_order": "#### สถานะคำสั่งซื้อ",
    "panel_no_flags": "ยังไม่มีแฟลก",
    "panel_receiver": "ชื่อผู้รับ",
    "panel_phone": "เบอร์โทร",
    "panel_address": "**ที่อยู่**",
    "panel_addr_read": "อ่านได้: %s <small>%s</small>",
    "panel_addr_detail": "รายละเอียด: %s",
    "panel_addr_api": "API: %s  \nรหัสไปรษณีย์: **%s**",
    "panel_addr_none": "API: ไม่พบผลการค้นหา",
    "panel_no_items": "ยังไม่มีสินค้าในรายการ",
    "panel_sum": "ยอดสินค้า **%s วอน** + ค่าส่ง **%s วอน** = รวม **%s**",
    "panel_blocked": "ยังยืนยันยอดไม่ได้",
    "panel_finish": "🧾 จบการสนทนา — ยืนยันใบสั่งซื้อ",
    "panel_ended": "จบแล้ว · ไปที่แท็บตรวจผล",
    "panel_observe": "แผงสังเกตการณ์ของเทิร์นนี้",
    "panel_diff": "การเปลี่ยนแปลงของสถานะ",
    "panel_no_diff": "ไม่มีการเปลี่ยนแปลง",
    "panel_detect": "ตรวจจับอัตโนมัติ",
    "panel_none": "ไม่มี",
    "panel_gaps": "บันทึกข้อมูลที่ขาด (missing_info)",
    "panel_llm_raw": "คำตอบดิบจาก LLM",
    "panel_used_refs": "ข้อมูลที่อ้างอิง (used_refs)",
    "panel_addr_api_cap": "API ที่อยู่",
    "panel_addr_api_none": "ยังไม่ได้เรียก",
    "panel_images": "การจำแนกรูป",
    "panel_no_images": "ไม่มีการอัปโหลด",
    "panel_label_read": "อ่านฉลากรอบสอง (เมื่อรอบแรกไม่ได้สินค้าเลย)",
    "panel_phone2": "อ่านเบอร์โทรรอบที่สอง: %s",
    "panel_tokens": "โทเคน เข้า %s / ออก %s %s · โมเดล %s · ระดับข้อมูล %s",
    "panel_estimated": "(ประมาณ)",

    "vd_need_finish": "ต้องกด **จบการสนทนา** ในแท็บบทสนทนาก่อน จึงจะตรวจผลได้ค่ะ",
    "vd_title": "### ใบสั่งซื้อสุดท้าย",
    "vd_no_items": "ไม่มีสินค้า",
    "vd_receiver": "ชื่อผู้รับ",
    "vd_phone": "เบอร์โทร",
    "vd_address": "ที่อยู่",
    "vd_address_detail": "รายละเอียดที่อยู่",
    "vd_zip": "รหัสไปรษณีย์",
    "vd_total": "ยอดรวม",
    "vd_handoff": "### บันทึกส่งต่อเจ้าหน้าที่",
    "vd_handoff_help": "โค้ดดึงมาจากสถานะและแฟลก เป็นรายการที่เจ้าหน้าที่ต้องตรวจก่อนยืนยัน",
    "vd_handoff_none": "ไม่มีรายการต้องตรวจ ยืนยันได้เลยค่ะ",
    "vd_fields": "### ตรวจผลรายข้อ",
    "vd_fields_help": ("ช่วยกดว่าใบสั่งซื้อถูกกรอกอัตโนมัติได้ถูกต้องไหมในแต่ละข้อ "
                       "ถ้าไม่ผ่าน ต้องเลือกสาเหตุด้วย จะได้รู้ว่าต้องแก้อะไร"),
    "vd_pass": "ผ่าน",
    "vd_fail": "ไม่ผ่าน",
    "vd_cause": "สาเหตุ",
    "vd_flags": "### ตรวจผลแฟลก",
    "vd_flags_help": ("ช่วยกดว่าแฟลกที่ขึ้นในบทสนทนานี้ขึ้นถูกต้องไหม "
                      "ต้องมีข้อมูลจับถูก/จับผิดสะสม ถึงจะตัดสินใจได้ว่าจะแก้หรือลบแฟลกไหน"),
    "vd_flag_turn": "เทิร์นที่ %d",
    "vd_no_flags": "บทสนทนานี้ไม่มีแฟลกขึ้นเลย",
    "fv_true": "จับถูก",
    "fv_false": "จับผิด",
    "fv_hold": "ยังไม่ตัดสิน",
    "vd_missed": "แฟลกที่ควรขึ้นแต่ไม่ขึ้น",
    "vd_missed_hint": "เลือกแฟลก (ถ้าไม่มีก็เว้นไว้)",
    "vd_missed_help": ("ดูบทสนทนาแล้วเลือกแฟลกที่คิดว่าควรขึ้น "
                       "แฟลกที่ควรขึ้นแต่ไม่ขึ้นหายากกว่าแฟลกที่ขึ้นผิด ถ้าไม่มีก็เว้นไว้"),
    "vd_note": "บันทึกของผู้ทดสอบ",
    "vd_note_hint": "ขาดอะไรไป ต้องมีแนวทางอะไรเพิ่ม",
    "vd_save": "บันทึกผลตรวจ",
    "vd_saved": "บันทึกผลตรวจแล้ว — %s",
    "vd_new": "เริ่มบทสนทนาใหม่",
    "vd_writing": "กำลังบันทึกลงชีต…",
    "vd_writing_tab": "กำลังบันทึก %s…",
    "vd_written": "บันทึกเสร็จแล้ว",
    "vd_log_off": "ยังไม่ได้ตั้งค่าล็อก — นับเฉพาะในเซสชันนี้",
    "vd_log_fail": "บันทึกล็อกไม่สำเร็จ — %s: %s",
    "vd_after_save": ("ถ้าจะคุยบทสนทนาใหม่ต่อ กดปุ่ม **เริ่มบทสนทนาใหม่** ด้านบนได้เลยค่ะ "
                      "(ออกจากหน้านี้แล้วกลับมาจะเห็น)"),

    "vf_invoice": "ใบแจ้งรายการ (สินค้า·จำนวน·ราคา)",
    "vf_address": "ที่อยู่",
    "vf_receiver": "ชื่อผู้รับ",
    "vf_phone": "เบอร์โทร",
    "cause_extract": "อ่านค่าผิด",
    "cause_match": "จับคู่สินค้าผิด",
    "cause_unit": "เข้าใจหน่วยผิด",
    "cause_nodb": "ไม่มีใน DB",
    "cause_policy": "แนวทางไม่พอ",
    "cause_etc": "อื่น ๆ",

    "rp_from_sheet": "อ่านจากชีตล็อก รวมบันทึกของผู้ทดสอบทุกคน",
    "rp_sheet_fail": "อ่านชีตล็อกไม่ได้ จึงแสดงเฉพาะบันทึกของเซสชันนี้ — %s",
    "rp_sheet_lag": "บันทึกลงชีตแล้วแต่ยังอ่านกลับไม่ได้ จึงแสดงบันทึกของเซสชันนี้",
    "rp_no_log": ("ยังไม่ได้ตั้งค่าล็อก Apps Script จึงนับ **เฉพาะในเบราว์เซอร์เซสชันนี้** "
                  "รีเฟรชแล้วจะหาย กรุณาดาวน์โหลด CSV ด้านล่างเก็บไว้"),
    "rp_empty": ("ยังไม่มีบทสนทนาที่ตรวจผล ไปที่แท็บ **💬 บทสนทนา** คุยให้จบ แล้วกด "
                 "**จบการสนทนา → บันทึกผลตรวจ** ผลจะมารวมที่นี่"),
    "rp_conversations": "บทสนทนาที่ทดสอบ",
    "rp_calls": "เรียก LLM",
    "rp_images": "รูปที่อัปโหลด",
    "rp_tokens": "โทเคน (ประมาณ)",
    "rp_tokens_delta": "เข้า %s / ออก %s",
    "rp_cost": "ค่าใช้จ่ายโดยประมาณ",
    "rp_cost_delta": "ต่อบทสนทนา %s วอน",
    "rp_cost_note": "คิดที่ %s วอน/$ · ถ้าคิด 10,000 บทสนทนา ประมาณ %s วอน",
    "rp_latency": "เวลาตอบ — เฉลี่ย %.1f วินาที · สูงสุด %.1f วินาที (ค่าที่วัดได้จริง)",
    "rp_by_field": "### อัตราสำเร็จรายข้อ",
    "rp_col_field": "ข้อ",
    "rp_col_pass": "ผ่าน",
    "rp_col_fail": "ไม่ผ่าน",
    "rp_col_rate": "อัตราสำเร็จ",
    "rp_by_source": "### อัตราสำเร็จตามชนิดข้อมูลเข้า",
    "rp_by_source_help": "ข้อเดียวกันก็ยากง่ายไม่เท่ากัน ขึ้นกับว่ามาจากข้อความหรือรูป",
    "rp_col_source": "ชนิดข้อมูลเข้า",
    "rp_col_count": "จำนวน",
    "rp_src_text": "ข้อความ",
    "rp_src_image": "รูป",
    "rp_no_data": "ไม่มีข้อมูล",
    "rp_causes": "### อันดับสาเหตุที่ไม่ผ่าน",
    "rp_col_cause": "สาเหตุ",
    "rp_no_fail": "ไม่มีข้อที่ไม่ผ่าน",
    "rp_by_mode": "### เปรียบเทียบตามระดับข้อมูล",
    "rp_col_mode": "โหมด",
    "rp_col_conv": "บทสนทนา",
    "rp_col_all_rate": "อัตราสำเร็จรวม",
    "rp_notes": "### บันทึกที่ทิ้งไว้",
    "rp_download": "ดาวน์โหลดผลเป็น CSV",

    "dt_caption": "หน้านี้ใช้ดูว่าการแก้ชีตมีผลแล้วหรือยัง ไม่เกี่ยวกับผลการทดลอง",
    "dt_policies": "ฐานข้อมูลแนวทาง",
    "dt_collision": "คำพ้องที่ชนกัน — จุดที่ AMBIGUOUS_ALIAS ควรขึ้น",
    "dt_col_expr": "คำที่ใช้",
    "dt_col_items": "สินค้าที่ชนกัน",
    "dt_none": "ไม่มี",
    "dt_sheet_fail": "อ่าน **%s** ไม่ได้",
    "dt_sheet_warn": "อ่านแท็บ **%s** ไม่ได้ จึงใช้ค่าสำรองแทน — %s",
    "dt_share_fix": ("การแชร์ชีตถูกปิดอยู่ **ไม่ใช่เพราะปุ่มโหลดข้อมูลใหม่**\n\n"
                     "ไปที่ Google Sheet → **แชร์** → **สิทธิ์เข้าถึงทั่วไป** "
                     "เปลี่ยนเป็น `ทุกคนที่มีลิงก์` · **ผู้อ่าน** แล้วกด "
                     "**โหลดข้อมูลใหม่** อีกครั้ง"),

    "fl_no_alt": "ไม่มีสินค้าทดแทน",
    "fl_no_price": "ไม่มีราคา",
    "fl_soldout": "'%s' → %s หมดของ สินค้าทดแทน: %s",
    "fl_rejected": "ลูกค้าบอกว่า '%s' → %s ไม่ใช่ ตัวเลือก: %s",
    "fl_ambiguous": "'%s' ตรงกับสินค้า %d รายการ → %s",
    "fl_notfound": "หา '%s' ในฐานข้อมูลไม่พบ (%s)",
    "fl_missing_price": "รายการที่ไม่มีราคาต่อหน่วย: %s → ยืนยันยอดรวมไม่ได้",
    "fl_receiver_missing": "ยังไม่ได้ชื่อผู้รับ",
    "fl_phone_dot": "'%s' → มีจุดทศนิยม ชีตเก็บเป็นตัวเลขจนเลข 0 ข้างหน้าหายไป",
    "fl_phone_zero": "'%s' → ไม่ได้ขึ้นต้นด้วย 0",
    "fl_phone_len": "'%s' → มี %d หลัก (ไม่ใช่ 10~11 หลัก)",
    "fl_phone_mismatch": "รอบแรก '%s' กับรอบสอง '%s' — อ่านจากรูปได้ไม่ตรงกัน",
    "fl_address_missing": "ไม่มีที่อยู่เลย",
    "fl_address_detail": "มีที่อยู่หลักแต่ไม่มีรายละเอียดที่อยู่",
    "fl_address_image": "อ่านที่อยู่มาจาก %s — ต้องตรวจด้วยตา",
    "fl_address_unverified": "ค้นหา '%s' ไม่พบเลย — ไม่ได้รหัสไปรษณีย์",
    "fl_address_ambiguous": "ค้นเจอ %d รายการ แต่รหัสไปรษณีย์มี %d แบบ (%s) — ที่อยู่อาจไม่ครบ",
    "fl_handoff": "ลูกค้าขอคุยกับเจ้าหน้าที่โดยตรง",
    "fl_angry": "ตรวจพบความไม่พอใจของลูกค้า",
    "fl_proof": "ได้รับรูปสลิป (%s) — ต้องเทียบกับรายการเดินบัญชี",
    "fl_payment_claim": "ลูกค้าบอกว่าโอนแล้วแต่ไม่มีสลิป — เจ้าหน้าที่ต้องตรวจเอง",
    "fl_payment_pending": "ข้อมูลคำสั่งซื้อครบแล้วแต่ยังไม่ได้รับสลิป",
    "fl_amount": "ตัวเลข %s ในคำตอบไม่ตรงกับที่คำนวณได้",

    "dt_col_hit": "สิ่งที่ตรวจพบ",
    "dt_col_rule": "แนวทางอ้างอิง",
    "dt_col_body": "รายละเอียด",
    "dt_amount": "ตัวเลขเงินหลอน",
    "dt_amount_body": "ตัวเลข %s ในคำตอบไม่ตรงกับที่คำนวณได้ → AMOUNT_MISMATCH",
    "dt_state_lost": "ลืมข้อมูลที่มีแล้ว",
    "dt_state_lost_body": "ถามซ้ำเรื่อง '%s' ทั้งที่ได้ข้อมูลแล้ว",
    "dt_schema": "ข้อมูลในฐานข้อมูลไม่พอ",
    "dt_schema_body": "'%s' → ข้อมูลที่ต้องมี: %s",
    "dt_payment": "ฟันธงเรื่องการโอน",
    "dt_payment_body": "ในคำตอบมีข้อความทำนองว่ายืนยันการโอนแล้ว",
    "dt_question": "ไม่ตอบคำถามลูกค้า",
    "dt_question_body": "ลูกค้าถามว่า '%s' แต่ไม่ได้ตอบ",
    "dt_unavailable": "แจ้งเรื่องมี/ไม่มีสินค้าผิด",
    "dt_unavailable_body": "บอกว่าไม่มี '%s' จำหน่าย แต่ในฐานข้อมูลมี → %s",
    "dt_unavailable_kept": "บอกว่าไม่มี '%s' แต่ %s ยังอยู่ในรายการสั่งซื้อ",
    "dt_ask_missing": "ไม่ได้ถามกลับ",
    "dt_ask_missing_body": "มีรายการที่ยังไม่ชัด %s แต่ไปต่อโดยไม่เสนอตัวเลือก",
    "dt_unchecked": "ปล่อยผ่านทั้งที่ยังไม่ยืนยัน",
    "dt_unchecked_body": "คำว่า %s ไม่มีในฐานข้อมูล แต่ไปต่อโดยไม่ยืนยัน",
    "dt_smalltalk": "คุยเล่นแล้วไม่กลับเข้าเรื่อง",
    "dt_smalltalk_body": "หลังคุยเล่นหรือแนะนำสินค้า ไม่กลับไปที่คำถามที่ค้างอยู่",
    "dt_extract": "ดึงข้อมูลไม่ได้",
    "dt_extract_body": "จัดว่าเป็นการสั่งซื้อ แต่ item_ops ว่างเปล่า",

    "col_expr": "คำที่ลูกค้าใช้",
    "col_pack": "หน่วยบรรจุ",
    "col_match": "สินค้าที่จับคู่ได้",
    "col_ship_type": "ประเภทการส่ง",
    "col_qty": "จำนวน",
    "col_request": "ที่ขอมา",
    "col_price": "ราคาต่อหน่วย",
    "col_amount": "รวม",
    "col_soldout": "หมดของ",
    "col_pending_qty": "รอเลือกจำนวน",
    "col_origin": "ที่มา",
    "st_confirmed": "ยืนยันแล้ว",
    "st_ambiguous": "ยังไม่ชัด",
    "st_not_found": "ไม่พบ",
    "st_conflict": "ขัดกัน",

    "ho_request": "คำขอของลูกค้า",
    "ho_check": "ต้องตรวจสอบ",
    "ho_missing": "ยังไม่ได้ข้อมูล",
    "ho_eye": "ต้องตรวจด้วยตา",
    "ho_payment": "การชำระเงิน",
    "ho_talk": "บทสนทนา",
    "ho_drop_soldout": "%s หมดของ จึงเอาออกจากรายการ ต้องแจ้งกำหนดของเข้าให้ลูกค้า",
    "ho_drop_notfound": "ลูกค้าหา %s แต่ไม่มีจำหน่าย จึงเอาออกจากรายการ ถ้าจะรับเข้าต้องแจ้งลูกค้า",
    "ho_soldout": "%s หมดของ กำลังเสนอสินค้าทดแทน (%s)",
    "ho_ambiguous": "%s ยังไม่ชัดว่าเป็นอันไหนใน %s",
    "ho_notfound": "หา %s ในฐานข้อมูลสินค้าไม่พบ",
    "ho_price": "%s ไม่มีราคาต่อหน่วย จึงยืนยันยอดรวมไม่ได้",
    "ho_gaveup": "ถาม %s หลายครั้งแล้วแต่ยังไม่ได้ ต้องติดต่อยืนยันเอง",
    "ho_empty": "%s ยังว่างอยู่",
    "ho_detail": "ไม่มีรายละเอียดที่อยู่ ต้องมีชื่ออาคารหรือจุดสังเกตให้คนส่งของหาเจอ",
    "ho_image": "อ่าน %s มาจากรูป (%s) ช่วยเทียบกับต้นฉบับด้วย",
    "ho_zip_none": "ค้นหาที่อยู่ไม่พบเลย จึงไม่ได้รหัสไปรษณีย์",
    "ho_zip_many": "ค้นหาที่อยู่แล้วได้รหัสไปรษณีย์ %d แบบ (%s) ที่อยู่อาจไม่ครบ",
    "ho_proof": "ได้รับรูปสลิปแล้ว (%s) ช่วยเทียบกับรายการเดินบัญชีด้วย",
    "ho_no_proof": "ยังไม่ได้ยืนยันการโอน ช่วยตรวจจากรายการเดินบัญชีเอง",
    "ho_handoff_req": "เทิร์นที่ %d ลูกค้าขอคุยกับเจ้าหน้าที่",
    "ho_angry": "เทิร์นที่ %d ตรวจพบความไม่พอใจ",
}

TABLES = {"ko": KO, "th": TH}

# 조사가 있는 언어. 여기 없으면 조사 함수는 낱말을 그대로 돌려준다
JOSA_LANGS = {"ko"}

# 숫자를 소리 내어 읽었을 때 받침이 있는지. 영/일/삼/육/칠/팔 은 있고 이/사/오/구 는 없다.
_DIGIT_BATCHIM = {"0": True, "1": True, "3": True, "6": True, "7": True, "8": True,
                  "2": False, "4": False, "5": False, "9": False}


def _has_batchim(word):
    w = str(word or "").rstrip("'\"), ]}").strip()
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return _DIGIT_BATCHIM.get(ch, False)
    if ch.isalpha():
        # 영문은 소리 기준. 자음으로 끝나면 받침이 있는 것으로 본다
        return ch.lower() not in "aeiou"
    return False


class Msg:
    """한 언어의 문구표. 없는 키는 한국어로 폴백한다."""

    def __init__(self, lang=DEFAULT_LANG):
        self.lang = str(lang or DEFAULT_LANG).strip().lower()
        self.table = TABLES.get(self.lang, TABLES[DEFAULT_LANG])

    def t(self, key, *args):
        s = self.table.get(key, KO.get(key, key))
        return (s % args) if args else s

    def money(self, n):
        return self.t("money", "%s" % f"{int(n or 0):,}")

    def ship_type(self, name):
        return SHIP_TYPE.get(self.lang, {}).get(str(name or "").strip(), name)

    def action(self, value):
        """플래그 값의 화면 표기. 비교와 저장에는 원값을 그대로 쓴다."""
        return FLAG_ACTION.get(self.lang, {}).get(str(value or "").strip(), value)

    def rule(self, name):
        """매칭 단계 이름의 화면 표기."""
        return MATCH_RULE.get(self.lang, {}).get(str(name or "").strip(), name)

    # ---------------------------------------------------------------- 조사
    # 상품명은 시트에서 오므로 받침을 미리 알 수 없다. '삼겹살는' 같은 것을 막는다.
    # 태국어에는 조사가 없으므로 낱말을 그대로 돌려준다.
    def _josa(self, word, with_b, without):
        if self.lang not in JOSA_LANGS:
            return word
        return word + (with_b if _has_batchim(word) else without)

    def eun(self, word):
        return self._josa(word, "은", "는")

    def i_ga(self, word):
        return self._josa(word, "이", "가")

    def eul(self, word):
        return self._josa(word, "을", "를")

    def quote_word(self, word):
        """되물음에서 고객 표현을 감쌀 때. 한국어는 작은따옴표를 쓴다."""
        return "'%s'" % word if self.lang in JOSA_LANGS else word


# 견적표의 열 이름과 매칭 상태. 저장·계산에 쓰는 키는 한국어로 고정돼 있고,
# 화면에 보여줄 때만 이 표로 갈아 끼운다. 키를 언어마다 바꾸면 로그가 갈린다.
QUOTE_COLUMNS = ["col_expr", "col_pack", "col_match", "col_ship_type", "col_qty",
                 "col_request", "col_price", "col_amount", "col_soldout", "col_pending_qty", "col_origin"]
STATUS_KEYS = {"확정": "st_confirmed", "모호": "st_ambiguous",
               "미발견": "st_not_found", "충돌": "st_conflict"}


def display_quote(rows, lang):
    """견적 행을 화면에 보여줄 형태로 옮긴다. 값은 건드리지 않는다."""
    T = Msg(lang)
    ko_names = [KO[k] for k in QUOTE_COLUMNS]
    out = []
    for r in rows:
        item = {}
        for ko_name, key in zip(ko_names, QUOTE_COLUMNS):
            if ko_name not in r:
                continue
            v = r[ko_name]
            if key == "col_match" and v in STATUS_KEYS:
                v = T.t(STATUS_KEYS[v])
            elif key == "col_ship_type" and v:
                v = T.ship_type(v)
            item[T.t(key)] = v
        out.append(item)
    return out


def for_lang(lang):
    return Msg(lang)


def for_dev():
    """플래그 근거·자동 감지·인계 메모처럼 개발자가 읽고 로그에 쌓이는 문장."""
    return Msg(DEV_LANG)


def strip_code(expr, fallback_lang=DEFAULT_LANG):
    """고객에게 되읊을 표현에서 내부 품목코드를 지운다."""
    cleaned = re.sub(r"\b[A-Za-z]\d{3,}\b", "", str(expr or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,-")
    return cleaned or Msg(fallback_lang).t("unknown_item")
