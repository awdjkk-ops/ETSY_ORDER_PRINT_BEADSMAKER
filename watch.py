# -*- coding: utf-8 -*-
"""바뀐 상품 확인
연결표의 상품마다 '연결할 때 모습(snap)'을 기억해 두고, Etsy·카페24의 지금 모습과 비교.
상품명이 조금이라도 다르거나 옵션(품목)이 추가·삭제·변경되면 → '확인 필요'(review). 확인 전까지 그 상품은 차감 안 함.
- 정해 둔 시간마다 전체 확인 (check_all) — Etsy 판매 중 상품 전체 + 카페24 상품 전체(품목 포함)
- 차감 직전에 그 주문의 상품만 확인 (check_some)
무거운 받기(check_*)는 작업 쪽에서, 연결표에 쓰는 것(merge_*)은 화면 쪽에서 (연결 창 저장과 엇갈리지 않게)"""
import datetime as dt
import os

import cafe24 as C
import orderprint as op
import premap as PM
import stock as S

STATE_PATH = os.path.join(op.BASE_DIR, "watch_state.json")
DEFAULT_TIMES = ["09:00", "13:00", "17:00"]


def now_s():
    return f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"


def load_state():
    return op.read_json(STATE_PATH, {"last": "", "last_ok": "", "note": ""})


def save_state(st):
    op.write_json(STATE_PATH, st)


# ---------------------------------------------------------------- 비교
def opt_list(options):
    """[[이름, [값...]]] → ['이름: 값', ...] (정렬)"""
    return sorted(f"{n}: {v}" for n, vals in (options or []) for v in vals)


def make_cur(title, options, pno, names, allv):
    """지금 모습. 모르는 칸은 None (비교 안 함)"""
    cur = {"etsy_title": title, "etsy_opts": opt_list(options) if options is not None else None,
           "c24_name": None, "c24_vars": None, "c24_missing": False}
    if pno in names:
        cur["c24_name"] = names[pno]
    elif names:
        cur["c24_missing"] = True
    if pno in allv:
        cur["c24_vars"] = sorted(v["label"] for v in allv[pno])
    return cur


def compare(snap, cur):
    """기억해 둔 모습과 지금 모습 → 바뀐 점 [{what, before, after}]"""
    ch = []
    if cur.get("c24_missing"):
        ch.append({"what": "카페24 상품", "before": snap.get("c24_name") or "", "after": "(카페24에 없음 — 삭제됐거나 번호가 바뀜)"})
    for key, what in (("etsy_title", "Etsy 상품명"), ("c24_name", "카페24 상품명")):
        a, b = snap.get(key), cur.get(key)
        if a is not None and b is not None and a != b:
            ch.append({"what": what, "before": a, "after": b})
    for key, what in (("etsy_opts", "Etsy 옵션"), ("c24_vars", "카페24 품목")):
        a, b = snap.get(key), cur.get(key)
        if a is not None and b is not None and a != b:
            add, rem = sorted(set(b) - set(a)), sorted(set(a) - set(b))
            ch.append({"what": what, "before": ", ".join(rem) or "-", "after": ", ".join(add) or "-",
                       "note": (f"추가 {len(add)}개" if add else "") + (" · " if add and rem else "") +
                               (f"없어짐/바뀜 {len(rem)}개" if rem else "")})
    return ch


def merge(curs):
    """지금 모습들을 연결표에 반영 (화면 쪽에서 호출). 돌려줌: 새로 '확인 필요'가 된 상품 수"""
    m = S.load_map()
    excl = S.load_excluded()
    flagged = 0
    for lid, cur in curs.items():
        e = m.get(lid)
        if not e or not e.get("product_no") or lid in excl:
            continue
        snap = e.get("snap")
        if not snap:                                  # 처음 보는 상품 → 지금 모습을 기준으로
            e["snap"] = {k: v for k, v in cur.items() if k != "c24_missing"}
            continue
        for k, v in cur.items():                      # 기준에 비어 있던 칸은 채우기만
            if snap.get(k) is None and v is not None and k != "c24_missing":
                snap[k] = v
        ch = compare(snap, cur)
        e["seen"] = {k: v for k, v in cur.items() if k != "c24_missing"}
        if ch:
            if not e.get("review"):
                flagged += 1
            e["review"] = {"time": now_s(), "changes": ch}
        else:
            e.pop("review", None)
    S.save_map(m)
    return flagged


# ---------------------------------------------------------------- 전체 확인 (정해 둔 시간마다)
def check_all(client, prog, stop=None):
    """돌려줌: 화면 쪽 merge_all에 넘길 결과"""
    act = PM.etsy_active(client, lambda d, t, l=None: prog(d, t, "바뀐 상품 확인: Etsy 상품 목록"), stop)
    opts = PM.etsy_options(client, list(act), prog, stop, "바뀐 상품 확인: Etsy 옵션")
    allv, names_l = C.fetch_catalog(lambda d, t: prog(d, t, "바뀐 상품 확인: 카페24 상품"), stop)
    names = {p["no"]: p["name"] for p in names_l}
    m = S.load_map()
    curs = {}
    for lid, e in m.items():
        if lid in act and e.get("product_no"):
            curs[lid] = make_cur(act[lid]["title"], opts.get(lid), e["product_no"], names, allv)
    # 새로 올라온 Etsy 상품 → 확실한 것만 연결 제안
    listings = {lid: {**v, "options": opts.get(lid, [])} for lid, v in act.items()}
    known = PM.load_state().get("items", {})
    excl = S.load_excluded()
    units = S.load_units()
    new = {}
    for lid, L in listings.items():
        if lid in m or lid in known or lid in excl:
            continue
        e, st_, why, _n = PM.connect_one(L, None, allv, units)
        new[lid] = (e, st_, why)
    return {"curs": curs, "listings": listings, "new": new, "etsy": len(act), "c24": len(names_l)}


def merge_all(res):
    flagged = merge(res["curs"])
    op.write_json(PM.LISTINGS_PATH, {"time": now_s(), "listings": res["listings"]})
    m = S.load_map()
    st = PM.load_state()
    items = st.setdefault("items", {})
    added = 0
    for lid, (e, st_, why) in res["new"].items():
        if lid in m:
            continue
        if e is not None:
            m[lid] = e
            added += 1
        items[lid] = {"status": st_, "why": why, "time": now_s()}
    S.save_map(m)
    PM.save_state(st)
    note = (f"Etsy {res['etsy']:,}개 · 카페24 {res['c24']:,}개 확인 — 바뀐 상품 {flagged}개 새로 찾음"
            + (f", 새 Etsy 상품 {len(res['new'])}개 (연결 {added}개)" if res["new"] else ""))
    s = load_state()
    s.update({"last": now_s(), "last_ok": now_s(), "note": note, "flagged": flagged})
    save_state(s)
    op.log.info(f"[바뀐 상품 확인] {note}")
    return flagged, len(res["new"]), note


# ---------------------------------------------------------------- 차감 직전 확인 (그 주문의 상품만)
def check_some(client, lids, prog=None):
    m = S.load_map()
    lids = [str(x) for x in dict.fromkeys(lids) if (m.get(str(x)) or {}).get("product_no")]
    if not lids:
        return {}
    info = PM.etsy_by_ids(client, lids)
    opts = PM.etsy_options(client, lids, prog, None, "차감 전 확인: Etsy 옵션")
    pnos = {m[lid]["product_no"] for lid in lids}
    allv, names_l = C.fetch_catalog(product_nos=pnos)
    names = {p["no"]: p["name"] for p in names_l}
    curs = {}
    for lid in lids:
        if lid not in info:
            continue                                     # Etsy에서 내렸거나 못 받음 → 비교 안 함
        curs[lid] = make_cur(info[lid]["title"], opts.get(lid), m[lid]["product_no"], names, allv)
        if not names:
            curs[lid]["c24_missing"] = False             # 카페24를 못 받았으면 '없어짐'으로 보지 않음
    return curs


# ---------------------------------------------------------------- 확인 처리
def review_items():
    """확인이 필요한 상품: [(listing_id, 연결표 항목, 이유 목록)]
    이유 = 연결표의 review(상품이 바뀜) 또는 차감 대기 주문의 상품명이 연결할 때와 다름"""
    m = S.load_map()
    out = {}
    for lid, e in m.items():
        if e.get("review"):
            out[lid] = [f"{c['what']}: {c['before']} → {c['after']}" for c in e["review"]["changes"]]
    for x in S.load_queue():
        if x.get("status") != "pending":
            continue
        e = m.get(x["listing_id"])
        if e and S.title_mismatch(e, x["title"]):
            out.setdefault(x["listing_id"], []).append(f"주문 {x['receipt_id']}의 상품명이 연결할 때와 다름: {x['title']}")
    return [(lid, m[lid], reasons) for lid, reasons in out.items()]


def confirm(lid):
    """[그대로 맞음]: 지금 모습을 새 기준으로, 차감 대기 주문의 상품명도 맞는 것으로"""
    m = S.load_map()
    e = m.get(lid)
    if not e:
        return
    if e.get("seen"):
        e["snap"] = dict(e["seen"])
    e.pop("review", None)
    ok = set(e.get("ok_titles", []))
    for x in S.load_queue():
        if x["listing_id"] == lid and x.get("status") == "pending" and S.title_mismatch(e, x["title"]):
            ok.add(x["title"])
    e["ok_titles"] = sorted(ok)[-20:]
    S.save_map(m)
    op.log.info(f"[바뀐 상품 확인] {lid} 그대로 맞음")


# ---------------------------------------------------------------- 시간
def parse_times(text):
    out = []
    for t in str(text or "").replace("，", ",").replace(" ", ",").split(","):
        t = t.strip()
        if not t:
            continue
        try:
            h, mi = (t.split(":") + ["0"])[:2]
            h, mi = int(h), int(mi)
            if 0 <= h < 24 and 0 <= mi < 60:
                out.append(f"{h:02d}:{mi:02d}")
        except ValueError:
            continue
    return sorted(set(out))


def load_times():
    t = op.load_settings().get("watch_times")
    return parse_times(",".join(t)) if isinstance(t, list) else list(DEFAULT_TIMES)


def due(now=None):
    """지금 확인할 차례인지: 가장 최근에 지난 확인 시각 이후로 아직 확인 안 했으면 True"""
    times = load_times()
    if not times:
        return False
    now = now or dt.datetime.now()
    cands = []
    for d in (now.date() - dt.timedelta(days=1), now.date()):
        for t in times:
            h, mi = map(int, t.split(":"))
            x = dt.datetime.combine(d, dt.time(h, mi))
            if x <= now:
                cands.append(x)
    if not cands:
        return False
    last_slot = max(cands)
    last = load_state().get("last", "")
    try:
        last_t = dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S") if last else None
    except ValueError:
        last_t = None
    return last_t is None or last_t < last_slot
