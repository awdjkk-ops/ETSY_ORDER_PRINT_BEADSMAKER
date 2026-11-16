# -*- coding: utf-8 -*-
"""미리 연결: 주문이 들어오기 전에 Etsy 상품 전체를 카페24와 연결표에 미리 넣어 두기
① Etsy 판매 중 상품 목록(제목)  ② Etsy 옵션(100개씩 한꺼번에)  ③ 카페24 품목+재고(100개씩 한꺼번에)
④ 확실한 것만 연결 (자동 연결과 같은 규칙). 중간에 멈춰도 다음에 이어서 함"""
import datetime as dt
import os
import time

import cafe24 as C
import orderprint as op
import stock as S

LISTINGS_PATH = os.path.join(op.BASE_DIR, "premap_listings.json")
STATE_PATH = os.path.join(op.BASE_DIR, "premap_state.json")


class Stopped(Exception):
    pass


def now_s():
    return f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"


def load_listings():
    return op.read_json(LISTINGS_PATH, {"time": "", "listings": {}})


def load_state():
    return op.read_json(STATE_PATH, {"time": "", "items": {}})


def save_state(st):
    op.write_json(STATE_PATH, st)


def _check(stop):
    if stop is not None and stop.is_set():
        raise Stopped()


# ---------------------------------------------------------------- Etsy
def etsy_active(client, prog, stop):
    """판매 중인 Etsy 상품 목록 → {listing_id: {title, updated}}"""
    shop_id = client.token.get("shop_id")
    if not shop_id:
        raise op.UserError("Etsy 상점 ID가 없습니다. 관리 탭에서 Etsy를 다시 연결해 주세요.")
    if "listings_r" not in str(client.token.get("scopes", "")):
        raise op.UserError("Etsy 상품 목록을 읽는 권한이 추가되어서 Etsy 연결을 한 번 다시 해야 합니다.\n\n"
                           "관리 탭 → Etsy 연결 → [다시 연결하기]를 누르고, 브라우저에서 'Grant access(허용)'를 눌러 주세요.\n"
                           "(주문서 인쇄 등 다른 기능은 다시 연결하기 전에도 그대로 됩니다)")
    out, offset, total = {}, 0, 0
    while True:
        _check(stop)
        try:
            data = client.get(f"/shops/{shop_id}/listings",
                              {"state": "active", "limit": 100, "offset": offset, "sort_on": "created", "sort_order": "asc"})
        except op.UserError as e:
            if "(403)" in str(e) or "(401)" in str(e):
                raise op.UserError("Etsy 상품 목록을 읽을 권한이 없습니다. 관리 탭 → Etsy 연결 → [다시 연결하기]를 눌러 "
                                   "'Grant access(허용)'를 다시 해 주세요.\n\n" + str(e)[:200])
            raise
        total = data.get("count") or total
        batch = data.get("results", [])
        for x in batch:
            out[str(x["listing_id"])] = {"title": op.clean(x.get("title")),
                                         "updated": x.get("last_modified_timestamp") or x.get("updated_timestamp") or 0}
        prog(len(out), total, "Etsy 상품 목록 받는 중")
        if len(batch) < 100 or (total and offset + 100 >= total):
            break
        offset += 100
        time.sleep(0.15)
    if total and len(out) < total:
        op.log.warning(f"[Etsy] 상품 {len(out)}개만 받음 (전체 {total}개)")
    return out


def etsy_by_ids(client, lids, stop=None):
    """상품번호로 Etsy 상품 정보 → {listing_id: {title, updated, state}} (100개씩)"""
    out = {}
    lids = [str(x) for x in lids]
    for i in range(0, len(lids), 100):
        _check(stop)
        data = client.get("/listings/batch", {"listing_ids": ",".join(lids[i:i + 100])})
        for x in data.get("results", []):
            out[str(x["listing_id"])] = {"title": op.clean(x.get("title")), "state": x.get("state", ""),
                                         "updated": x.get("last_modified_timestamp") or x.get("updated_timestamp") or 0}
    return out


def etsy_options(client, lids, prog=None, stop=None, label="Etsy 옵션 받는 중"):
    """{listing_id: [[옵션 이름, [값...]]]} (100개씩, 안 되면 하나씩). 못 받은 상품은 빠짐"""
    out = {}
    lids = [str(x) for x in lids]
    batch_ok = True
    for i in range(0, len(lids), 100):
        _check(stop)
        chunk = lids[i:i + 100]
        if prog:
            prog(min(i + 100, len(lids)), len(lids), label)
        if batch_ok:
            try:
                data = client.get("/listings/batch/inventory", {"listing_ids": ",".join(chunk)})
                for x in data.get("results", []):
                    out[str(x.get("listing_id"))] = _options(x.get("inventory") or {})
                time.sleep(0.15)
                continue
            except op.UserError as e:
                op.log.info(f"[Etsy] 옵션 한꺼번에 받기 안 됨 → 상품마다 따로: {e}")
                batch_ok = False
        for lid in chunk:
            _check(stop)
            try:
                out[lid] = _options(client.get(f"/listings/{lid}/inventory"))
            except op.UserError as e:
                op.log.info(f"[Etsy] 옵션 못 받음 {lid}: {str(e)[:120]}")
            time.sleep(0.12)
    return out


def fetch_etsy(client, prog, stop):
    """판매 중인 Etsy 상품 + 옵션 → {listing_id: {title, updated, options: [[이름, [값...]], ...]}}"""
    act = etsy_active(client, lambda d, t, l=None: prog(d, t, "① Etsy 상품 목록 받는 중"), stop)
    opts = etsy_options(client, list(act), prog, stop, "② Etsy 옵션 받는 중")
    listings = {lid: {**v, "options": opts.get(lid, [])} for lid, v in act.items()}
    op.write_json(LISTINGS_PATH, {"time": now_s(), "listings": listings})
    op.log.info(f"[미리 연결] Etsy 상품 {len(listings)}개 받음")
    return listings


def _options(inv):
    """Etsy 재고 정보 → [[옵션 이름, [값...]]] (값 순서 유지)"""
    opts = {}
    for p in inv.get("products", []) or []:
        if p.get("is_deleted"):
            continue
        for pv in p.get("property_values", []) or []:
            name = op.clean(pv.get("property_name"))
            for v in pv.get("values", []) or []:
                v = op.clean(v)
                if name and v:
                    lst = opts.setdefault(name, [])
                    if v not in lst:
                        lst.append(v)
    return [[k, v] for k, v in opts.items()]


# ---------------------------------------------------------------- 연결
def run(client, prog, stop=None, redo=False, refresh_etsy=True):
    """돌려줌: 요약 dict. stop(Event)이 켜지면 지금까지 한 것을 저장하고 멈춤"""
    stopped = False
    m = S.load_map()
    st = load_state()
    items = {} if redo else st.get("items", {})
    summary = {"ok": 0, "partial": 0, "skip": 0, "already": 0, "stopped": False}
    try:
        cached = load_listings()
        if refresh_etsy or not cached["listings"]:
            listings = fetch_etsy(client, prog, stop)
        else:
            listings = cached["listings"]
        prog(0, 0, "③ 카페24 품목·재고 받는 중")
        allv = C.fetch_all_variants(lambda d, t: prog(d, t, "③ 카페24 품목·재고 받는 중"), stop)
        _check(stop)
        units = S.load_units()
        excl = S.load_excluded()
        todo = [lid for lid in listings if items.get(lid, {}).get("status") != "ok" and lid not in excl]
        done_n = 0
        for n, lid in enumerate(todo, 1):
            _check(stop)
            prog(n, len(todo), "④ 연결하는 중")
            e, st_, why, new = connect_one(listings[lid], m.get(lid), allv, units)
            if e is None:
                items[lid] = {"status": "skip", "why": why, "time": now_s()}
                continue
            m[lid] = e
            items[lid] = {"status": st_, "why": why, "time": now_s(), "new": len(new)}
            done_n += 1
            if done_n % 50 == 0:
                S.save_map(m)
                save_state({"time": now_s(), "items": items})
    except Stopped:
        stopped = True
    finally:
        S.save_map(m)
        save_state({"time": now_s(), "items": items})
    for it in items.values():
        summary[it["status"]] = summary.get(it["status"], 0) + 1
    summary["stopped"] = stopped
    summary["total"] = len(load_listings().get("listings", {}))
    op.log.info(f"[미리 연결] {summary}")
    return summary


def connect_one(L, e0, allv, units):
    """Etsy 상품 하나를 확실한 것만 연결 → (연결표 항목 또는 None, 상태 ok/partial/skip, 이유, 새로 연결한 것)"""
    title = L["title"]
    e = dict(e0 or {})
    if not e.get("product_no"):
        p, why = S.pick_product(title)
        if not p:
            return None, "skip", why, []
        e = {"product_no": p["no"], "product_name": p["name"], "etsy_title": title, "ignore": []}
    pno = e["product_no"]
    if pno not in allv:
        allv[pno] = C.get_variants(pno)
    variants = allv[pno]
    pairs = [(name, v) for name, vals in L.get("options", []) for v in vals]
    problems = []
    qty_opt = [name for name, vals in L.get("options", []) if any(S.read_unit(v, units) for v in vals)]
    if qty_opt:
        problems.append(f"수량이 들어 있는 옵션({', '.join(qty_opt)})이 있어 옵션은 직접 연결")
        new = []
    elif not variants:
        problems.append("카페24 품목을 못 받음")
        new = []
    else:
        new = S.connect_listing(e, title, pairs, variants, units, problems, other_units=False)
    status = "ok" if not problems else ("partial" if new or _has_any(e) else "skip")
    return e, status, "; ".join(problems)[:300], new


def _has_any(e):
    return bool(e.get("single") or e.get("combo") or any(e.get("each", {}).values()))


def mark_manual(lid):
    """연결 창에서 직접 저장했으면 '연결 끝'으로"""
    st = load_state()
    if lid in st.get("items", {}):
        st["items"][lid].update({"status": "ok", "why": "직접 연결", "time": now_s()})
        save_state(st)


def todo_list():
    """직접 연결이 필요한 Etsy 상품 [(listing_id, 제목, 상태, 이유)]"""
    L = load_listings().get("listings", {})
    excl = S.load_excluded()
    out = []
    for lid, it in load_state().get("items", {}).items():
        if it.get("status") in ("partial", "skip") and lid in L and lid not in excl:
            out.append((lid, L[lid]["title"], it["status"], it.get("why", "")))
    return out


def pseudo_line(lid):
    """연결 창을 열기 위한 가짜 주문 줄 (옵션 값 전부를 줄로)"""
    L = load_listings().get("listings", {}).get(lid, {})
    return {"tid": "", "listing_id": lid, "title": L.get("title", ""), "quantity": 1, "receipt_id": "",
            "variations": [{"name": n, "value": v} for n, vals in L.get("options", []) for v in vals]}


def status_text():
    st = load_state()
    items = st.get("items", {})
    if not items:
        return "아직 미리 연결을 한 적이 없습니다."
    c = {}
    excl = S.load_excluded()
    for lid, it in items.items():
        k = "excl" if lid in excl else it["status"]
        c[k] = c.get(k, 0) + 1
    total = len(load_listings().get("listings", {}))
    return (f"Etsy 상품 {total:,}개 중  ✓ 연결 끝 {c.get('ok', 0):,} · 일부만 {c.get('partial', 0):,} · "
            f"직접 연결 필요 {c.get('skip', 0):,}" + (f" · 연결 안 함 {c['excl']:,}" if c.get("excl") else "") + (f" · 아직 안 함 {max(total - len(items), 0):,}" if total > len(items) else "")
            + f"\n마지막 실행: {st.get('time', '')[:16]}")
