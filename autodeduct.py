# -*- coding: utf-8 -*-
"""출력하면 재고 바로 차감 (관리 탭 '주문서를 출력하면 재고를 바로 차감'이 켜졌을 때)
[지금 출력]·선택 출력·자동 출력으로 실제 인쇄가 끝난 뒤, 방금 뽑은 주문 줄을 바로 카페24에서 뺍니다.
- 문제없는 줄: 바로 차감 (차감 기록에 남고 되돌리기 가능)
- 재고가 모자란 품목: 0으로 맞추고 사입 목록에 (출력 묶음의 [이 묶음 사입 목록]으로 다시 볼 수 있음)
- 연결 필요·코드 다름·바뀐 상품·카페24 연결 실패: 빼지 않고 차감 대기에 남김
- 재고 풀어둠 고정인데 재고가 기준보다 적은 품목: 이번만 안 빼고 경고
연습 모드·테스트 모드가 켜져 있으면 하지 않습니다."""
import orderprint as op

REASON_TEXT = {"연결 필요": "연결 필요", "품목 연결 필요": "연결 필요 (품목)", "확인 필요": "바뀐 상품 확인 필요",
               "코드 다름": "코드 다름"}


def setting_on():
    return bool(op.load_settings().get("deduct_on_print"))


def blocked_reason():
    """켜져 있어도 지금 바로 차감을 못 하는 이유 ('' = 할 수 있음)"""
    if op.is_test():
        return "테스트 모드라 바로 차감하지 않았습니다"
    if op.load_settings().get("stock_practice", True):
        return "재고 탭의 연습 모드가 켜져 있어 바로 차감하지 않았습니다"
    return ""


def _tids(receipts):
    return {str(t.get("transaction_id")) for r in receipts for t in (r.get("transactions") or []) if t.get("transaction_id")}


def run(receipts, cfg, stage=None):
    """방금 뽑은 주문들의 재고를 바로 차감. 돌려줌(요약, json으로 저장 가능):
    {on, skipped, err, lines, done, left: {이유: 줄 수}, short: [사입 품목], fixed_low: [문구], manual}"""
    out = {"on": setting_on(), "skipped": "", "err": "", "lines": 0, "done": 0, "left": {}, "short": [],
           "fixed_low": [], "manual": 0}
    if not out["on"]:
        return out
    why = blocked_reason()
    if why:
        out["skipped"] = why
        return out
    tids = _tids(receipts)
    if not tids:
        return out
    import cafe24 as C
    import stock as S
    import watch as W
    if C.token_status()[0] == "bad":
        out["err"] = "카페24 연결이 필요해 재고를 빼지 못했습니다 — 관리 탭에서 카페24 다시 인증 (줄은 차감 대기에 남음)"
        out["lines"] = sum(1 for x in S.load_queue() if x["tid"] in tids and x.get("status") == "pending")
        return out
    rows = [r for r in S.pending_rows() if r["tid"] in tids and r["status"] == "pending"]
    out["lines"] = len(rows)
    if not rows:
        return out
    # ① 차감 전 확인: Etsy·카페24 상품이 연결할 때와 같은지 (바뀐 상품은 '확인 필요'로 빠짐)
    if stage:
        stage("재고 차감 전 확인 중")
    try:
        W.merge(W.check_some(op.EtsyClient(cfg), sorted({r["listing_id"] for r in rows})))
        rows = [r for r in S.pending_rows() if r["tid"] in tids and r["status"] == "pending"]
    except Exception as e:
        op.log.warning(f"[바로 차감] 차감 전 확인 실패 (그대로 진행): {e}")
    ok = [r for r in rows if not r["why"]]
    for r in rows:
        if r["why"]:
            k = REASON_TEXT.get(r["why"], r["why"])
            out["left"][k] = out["left"].get(k, 0) + 1
    if not ok:
        return out
    # ② 재고 풀어둠 고정인데 재고가 기준보다 적은 품목 → 이번만 안 빼고 경고
    fixed_keys = {(p, c) for r in ok for p, c, _l, _n in r.get("fixed", [])}
    if fixed_keys:
        try:
            st = C.stock_lookup({k[0] for k in fixed_keys}, wanted=fixed_keys)
            for x in S.fixed_low(ok, st):
                out["fixed_low"].append(f"{x.get('product_name', '')[:30]} {x.get('label', '')} (재고 {x.get('stock')})")
        except Exception as e:
            op.log.warning(f"[바로 차감] 재고 풀어둠 품목 확인 실패: {e}")
    # ③ 차감 (모자란 품목은 0으로, 사입 목록 저장)
    if stage:
        stage("재고 차감 중")
    try:
        _res, short, manual = S.apply(ok, practice=False)
    except Exception as e:
        op.log.exception("[바로 차감] 실패")
        out["err"] = f"재고 차감 중 오류: {str(e)[:150]} (줄은 차감 대기에 남음)"
        return out
    okt = {r["tid"] for r in ok}
    st = {x["tid"]: x.get("status") for x in S.load_queue() if x["tid"] in okt}
    out["done"] = sum(1 for t in okt if st.get(t) in ("done", "partial", "manual"))
    still = sum(1 for t in okt if st.get(t) == "pending")
    if still:
        out["left"]["차감 못 함 (재고 확인 실패 등)"] = still
    out["manual"] = len(manual or [])
    out["short"] = [{k: it[k] for k in ("product_no", "variant", "label", "product_name", "need", "stock", "short", "orders")}
                    for it in short or []]
    op.log.info(f"[바로 차감] {out['done']}/{out['lines']}줄 뺌 · 남은 {sum(out['left'].values())}줄 · 사입 {len(out['short'])}품목")
    return out


def problems(res):
    """사람이 볼 문제 문구 목록 (없으면 [])"""
    p = []
    if res.get("err"):
        p.append(res["err"])
    for k, n in (res.get("left") or {}).items():
        p.append(f"차감 대기에 남은 줄 {n}개 — {k}")
    if res.get("short"):
        p.append(f"재고 부족 {len(res['short'])}품목 — 0으로 맞추고 사입 목록에 저장")
    for t in res.get("fixed_low") or []:
        p.append(f"재고 풀어둠 고정인데 재고가 기준보다 적음 (이번만 안 뺌): {t}")
    if res.get("manual"):
        p.append(f"✋ 직접 차감할 줄 {res['manual']}개 — 재고 탭 '직접 차감 목록'")
    return p


def summary_line(res):
    if not res or not res.get("on"):
        return ""
    if res.get("skipped"):
        return res["skipped"]
    return f"재고 바로 차감 {res.get('done', 0)}/{res.get('lines', 0)}줄"
