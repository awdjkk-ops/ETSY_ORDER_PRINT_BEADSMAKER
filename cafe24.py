# -*- coding: utf-8 -*-
"""카페24 관리자 API (재고 차감용) — 인증 방식은 카페24 주문서 프로그램과 같음
- 인증: 브라우저에서 앱 승인 → 이동한 주소창 전체를 붙여넣기 → 토큰 저장 (사용할 때마다 자동 연장)
- 권한: 상품 읽기·쓰기 (mall.read_product, mall.write_product)
- 상품 이름 목록만 가끔 받아두고(재고 아님), 재고는 팔린 품목만 그때그때 조회·수정"""
import base64
import datetime as dt
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import orderprint as op

CONFIG_PATH = os.path.join(op.BASE_DIR, "cafe24_config.json")
TOKEN_PATH = os.path.join(op.BASE_DIR, "cafe24_token.json")
PRODUCTS_PATH = os.path.join(op.BASE_DIR, "cafe24_products.json")
SCOPE = "mall.read_product,mall.write_product"
CONFIG_DEFAULT = {"mall_id": "newbeadsmaker", "client_id": "", "client_secret": "",
                  "redirect_uri": "https://www.beadsmaker.com"}

log = op.log


class Cafe24Error(op.UserError):
    pass


class AuthError(Cafe24Error):
    pass


# ---------------------------------------------------------------- 설정·토큰
def load_config():
    cfg = dict(CONFIG_DEFAULT)
    cfg.update(op.read_json(CONFIG_PATH, {}))
    return cfg


def save_config(cfg):
    op.write_json(CONFIG_PATH, cfg)


def ready(cfg=None):
    cfg = cfg or load_config()
    return all(cfg.get(k) for k in ("mall_id", "client_id", "client_secret", "redirect_uri"))


def api_base(cfg):
    return f"https://{cfg['mall_id']}.cafe24api.com/api/v2"


def _parse_time(v):
    try:
        return dt.datetime.fromisoformat(str(v)[:19])
    except Exception:
        return None


def request_token(cfg, form):
    url = f"{api_base(cfg)}/oauth/token"
    basic = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode()).decode()
    r = requests.post(url, data=form, timeout=30, headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code in (400, 401):
        r = requests.post(url, data=dict(form, client_id=cfg["client_id"], client_secret=cfg["client_secret"]),
                          timeout=30, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        txt = r.text[:300]
        if "invalid_grant" in txt or "expired" in txt.lower():
            raise AuthError("인증 코드가 만료되었거나 이미 사용되었습니다. ① 브라우저 열기부터 다시 하고, "
                            "주소를 붙여넣은 뒤 1분 안에 ② 인증 완료를 눌러 주세요.")
        if "invalid_client" in txt:
            raise AuthError("Client ID 또는 Client Secret이 맞지 않습니다. 카페24 개발자센터의 값을 다시 확인해 주세요.")
        raise AuthError(f"카페24 인증 실패 ({r.status_code}): {txt}")
    return r.json()


def save_tokens(tok):
    op.write_json(TOKEN_PATH, {k: tok.get(k) for k in
                               ("access_token", "refresh_token", "expires_at", "refresh_token_expires_at", "scopes")}
                  | {"saved_at": dt.datetime.now().isoformat(timespec="seconds")})


def auth_url(cfg):
    params = {"response_type": "code", "client_id": cfg["client_id"], "state": "etsystock",
              "redirect_uri": cfg["redirect_uri"], "scope": SCOPE}
    return f"{api_base(cfg)}/oauth/authorize?{urlencode(params)}"


def finish_auth(cfg, pasted):
    pasted = (pasted or "").strip()
    if not pasted:
        raise AuthError("승인 후 이동한 페이지의 주소창 전체를 붙여넣어 주세요.")
    q = parse_qs(urlparse(pasted).query)
    if "error" in q:
        raise AuthError(korean_error(q.get("error_description", q["error"])[0]))
    code = q.get("code", [pasted])[0]
    save_tokens(request_token(cfg, {"grant_type": "authorization_code", "code": code,
                                    "redirect_uri": cfg["redirect_uri"]}))


def korean_error(desc):
    """카페24 승인 오류를 읽기 쉬운 한글로"""
    from urllib.parse import unquote_plus
    d = str(desc or "")
    for _ in range(3):                       # 두 번 인코딩되어 오는 경우가 있어 몇 번 풀어 봄
        nd = unquote_plus(d)
        if nd == d:
            break
        d = nd
    low = d.lower()
    if "head admin" in low or "대표" in d:
        return ("대표운영자 계정으로 로그인해 주세요.\n(재고를 바꾸는 권한은 대표운영자만 승인할 수 있습니다. "
                "부운영자로 로그인되어 있으면 카페24에서 로그아웃하거나 시크릿 창을 써 주세요.)")
    if "access_denied" in low or "denied" in low or "cancel" in low:
        return "앱 승인을 취소했거나 거부되었습니다. ① 브라우저 열기부터 다시 해 주세요."
    if "redirect" in low:
        return ("Redirect URI가 개발자센터 앱 설정과 다릅니다. 관리 탭의 Redirect URI와 카페24 개발자센터의 값을 "
                "똑같이 맞춰 주세요.\n(" + d + ")")
    return "승인이 거부되었습니다: " + d


_TOKEN_LOCK = threading.RLock()


def get_access_token(cfg, force=False):
    with _TOKEN_LOCK:                 # 여러 요청을 동시에 보낼 때 갱신이 겹치지 않게
        return _get_access_token(cfg, force)


def _get_access_token(cfg, force=False):
    tok = op.read_json(TOKEN_PATH, None)
    if not tok:
        raise AuthError("카페24와 아직 연결되지 않았습니다. 관리 탭 '카페24 연결'에서 인증해 주세요.")
    exp = _parse_time(tok.get("expires_at"))
    if not force and exp and exp - dt.datetime.now() > dt.timedelta(minutes=10):
        return tok["access_token"]
    try:
        new = request_token(cfg, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    except AuthError:
        raise AuthError("카페24 인증이 만료되었습니다. 관리 탭 '카페24 연결'에서 다시 인증해 주세요.")
    save_tokens(new)
    return new["access_token"]


def keep_alive(days=7):
    """인증 남은 기간이 days일 이하면 미리 연장 (프로그램을 켤 때 호출). 돌려줌: 연장했는지"""
    tok = op.read_json(TOKEN_PATH, None)
    if not tok or not ready():
        return False
    rexp = _parse_time(tok.get("refresh_token_expires_at"))
    if not rexp or (rexp - dt.datetime.now()).total_seconds() / 86400 > days:
        return False
    get_access_token(load_config(), force=True)
    log.info("[카페24] 인증 기간을 미리 연장했습니다.")
    return True


def token_status():
    """(상태, 설명) 상태: ok / warn / bad"""
    if not ready():
        return "bad", "설정 전 (아래 칸에 Client ID·Secret을 넣고 저장해 주세요)"
    tok = op.read_json(TOKEN_PATH, None)
    if not tok:
        return "bad", "인증 전 (① 브라우저 열기 → ② 인증 완료)"
    rexp = _parse_time(tok.get("refresh_token_expires_at"))
    if not rexp:
        return "ok", "인증됨"
    days = (rexp - dt.datetime.now()).total_seconds() / 86400
    if days <= 0:
        return "bad", "인증 만료 (다시 인증해 주세요)"
    if days < 3:
        return "warn", f"인증 만료까지 {days:.0f}일 (사용할 때마다 자동 연장)"
    return "ok", f"정상 (인증 {days:.0f}일 남음, 사용할 때마다 자동 연장)"


# ---------------------------------------------------------------- API 호출
def _call(method, path, params=None, body=None, cfg=None):
    cfg = cfg or load_config()
    token = get_access_token(cfg)
    r = None
    for attempt in range(6):
        r = requests.request(method, f"{api_base(cfg)}{path}", params=params, json=body, timeout=60, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        if r.status_code == 429:                       # 호출 한도 → 잠시 쉬었다가 다시
            time.sleep(2 + attempt * 3)
            continue
        if r.status_code == 401 and attempt == 0:
            token = get_access_token(cfg, force=True)
            continue
        break
    if r.status_code == 401:
        raise AuthError("카페24 인증 실패(401). 관리 탭에서 다시 인증해 주세요.")
    if r.status_code == 403:
        raise Cafe24Error("카페24 권한이 없습니다(403). 개발자센터 앱에 '상품 읽기+쓰기' 권한이 있는지, "
                          "권한을 바꾼 뒤 다시 인증했는지 확인해 주세요.")
    if r.status_code not in (200, 201):
        raise Cafe24Error(f"카페24 요청 실패 {path} ({r.status_code}): {r.text[:300]}")
    return r.json()


# ---------------------------------------------------------------- 상품 코드
CODE_RE = re.compile(r"^\s*([A-Za-z]{1,8}[\s-]?\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*)")


def extract_code(name):
    """상품명 맨 앞의 코드: 'WR68 / 1 Pc ...' → 'WR68', 'GEM22-자마노 ...' → 'GEM22', 'IN-002-MG / ...' → 'IN-002-MG'"""
    m = CODE_RE.match(str(name or ""))
    return m.group(1).strip() if m else ""


def norm_code(code):
    return re.sub(r"[\s\-_/.]", "", str(code or "")).upper()


def split_code(ncode):
    """'PDT1111' → ('PDT', '1111')"""
    m = re.match(r"^([A-Z]+)(.*)$", ncode)
    return (m.group(1), m.group(2)) if m else ("", ncode)


# ---------------------------------------------------------------- 상품 이름 목록 (재고 아님)
def load_products():
    return op.read_json(PRODUCTS_PATH, {"time": "", "products": []})


_CUSTOM = {"mtime": None, "map": {}}


def custom_map():
    """{상품번호: 자체 상품코드} — 받아 둔 카페24 상품 목록에서 (파일이 바뀔 때만 다시 읽음)"""
    try:
        mt = os.path.getmtime(PRODUCTS_PATH)
    except OSError:
        return {}
    if _CUSTOM["mtime"] != mt:
        _CUSTOM["map"] = {int(p["no"]): (p.get("custom") or "").strip() for p in load_products().get("products", [])
                          if p.get("no") is not None}
        _CUSTOM["mtime"] = mt
    return _CUSTOM["map"]


def products_age_days():
    t = _parse_time(load_products().get("time"))
    return (dt.datetime.now() - t).total_seconds() / 86400 if t else None


def product_count():
    """카페24 전체 상품 수 (못 받으면 None)"""
    try:
        return int(_call("GET", "/admin/products/count").get("count"))
    except Exception as e:
        log.info(f"[카페24] 상품 수 확인 실패: {e}")
        return None


def refresh_products(progress=None):
    """카페24 상품번호·상품명만 전부 받기 (100개씩, 상품 1만 개면 100번 정도)
    ① since_product_no(이 번호 다음부터)로 끝까지 → ② 그래도 전체 수보다 적으면 offset으로 한 번 더 훑어서 채움"""
    fields = "product_no,product_code,custom_product_code,product_name"
    total = product_count()
    got = {}

    def add(batch):
        for p in batch:
            got[int(p["product_no"])] = {"no": int(p["product_no"]), "name": p.get("product_name") or "",
                                         "custom": p.get("custom_product_code") or ""}
        if progress:
            progress(len(got))

    # ① 상품번호 순으로 이어 받기
    since, use_since, k = 0, True, 0
    while True:
        params = {"limit": 100, "fields": fields}
        if use_since:
            params["since_product_no"] = since
        try:
            data = _call("GET", "/admin/products", params)
        except Cafe24Error as e:
            if k == 0 and use_since:          # 0을 안 받아 주면 첫 묶음만 since 없이
                log.info(f"[카페24] since_product_no=0 안 됨 → 없이 다시: {e}")
                use_since = False
                continue
            raise
        batch = data.get("products", [])
        k += 1
        nos = [int(p["product_no"]) for p in batch]
        log.info(f"[카페24] 상품 목록 {k}번째: {len(batch)}개" + (f" (번호 {min(nos)}~{max(nos)})" if nos else ""))
        if not batch:
            break
        add(batch)
        new_since = max(nos)
        if len(batch) < 100 or new_since <= since:
            break
        since, use_since = new_since, True
        time.sleep(0.3)

    # ② 부족하면 offset으로 (카페24 offset 최대 8000)
    if total and len(got) < total:
        log.info(f"[카페24] 받은 {len(got)}개 < 전체 {total}개 → offset으로 다시 훑기")
        for off in range(0, 8001, 100):
            data = _call("GET", "/admin/products", {"limit": 100, "offset": off, "fields": fields})
            batch = data.get("products", [])
            if not batch:
                break
            add(batch)
            if len(got) >= total:
                break
            time.sleep(0.3)
    uniq = sorted(got.values(), key=lambda p: p["no"])
    op.write_json(PRODUCTS_PATH, {"time": dt.datetime.now().isoformat(timespec="seconds"), "products": uniq,
                                  "expected": total})
    log.info(f"[카페24] 상품 이름 목록 {len(uniq)}개 받음" + (f" (카페24 전체 {total}개)" if total else ""))
    return len(uniq)


def products_missing():
    """받은 상품 수가 카페24 전체보다 적으면 (받은 수, 전체 수), 아니면 None"""
    d = load_products()
    exp, n = d.get("expected"), len(d.get("products", []))
    return (n, exp) if exp and n < exp else None


def search_products(keyword):
    """카페24에서 상품명으로 바로 검색 (목록에 없는 새 상품용)"""
    data = _call("GET", "/admin/products", {"product_name": keyword, "limit": 50,
                                            "fields": "product_no,product_code,custom_product_code,product_name"})
    return [{"no": int(p["product_no"]), "name": p.get("product_name") or "", "custom": p.get("custom_product_code") or ""}
            for p in data.get("products", [])]


def get_products(nos):
    nos = [str(n) for n in nos]
    if not nos:
        return []
    data = _call("GET", "/admin/products", {"product_no": ",".join(nos), "limit": 100,
                                            "fields": "product_no,product_name,custom_product_code"})
    return [{"no": int(p["product_no"]), "name": p.get("product_name") or ""} for p in data.get("products", [])]


# ---------------------------------------------------------------- 품목(옵션)·재고
def variant_label(v):
    opts = v.get("options") or []
    if not opts:
        return "(옵션 없음)"
    return " / ".join(f"{o.get('name', '')}={o.get('value', '')}" for o in opts)


def _variant(v):
    """카페24 품목 → {code, label, custom, values, qty}. qty: 재고 수(재고 관리 안 하면 None, 응답에 없으면 '?')"""
    if "quantity" in v:
        qty = int(v.get("quantity") or 0) if v.get("use_inventory", "T") == "T" else None
    else:
        qty = "?"
    return {"code": v.get("variant_code"), "label": variant_label(v),
            "custom": v.get("custom_variant_code") or "",
            "values": [str(o.get("value", "")) for o in (v.get("options") or [])], "qty": qty}


def get_variants(product_no):
    data = _call("GET", f"/admin/products/{product_no}/variants")
    return [_variant(v) for v in data.get("variants", [])]


def fetch_all_variants(progress=None, stop=None):
    """카페24 전체 상품의 품목+재고를 상품 목록에 붙여서 한꺼번에 받기 (100개씩).
    돌려줌: {상품번호: [품목...]}. 붙여서 못 주는 경우엔 빈 dict → 상품마다 따로 받기"""
    return fetch_catalog(progress, stop)[0]


_NAME_FIELDS = "product_no,product_name,custom_product_code"
# 품목(옵션·재고)을 상품 목록에 붙여 받는 방법 — 카페24가 받아 주는 것을 처음 한 번 찾아서 기억
_EMBED_MODES = [
    ("fields+variants", {"fields": _NAME_FIELDS + ",variants", "embed": "variants"}),
    ("no-fields", {"embed": "variants"}),
]
_EMBED = {"mode": None, "tried": False}


def _has_variants(batch):
    vs = batch[0].get("variants") if batch else None
    return isinstance(vs, list) and (not vs or "variant_code" in vs[0])


def _get_products(params):
    """상품 목록 한 쪽 받기 (품목이 붙어 오는 방법으로). 돌려줌: (상품 목록, 품목이 붙어 왔는지)"""
    if _EMBED["mode"] is not None or _EMBED["tried"]:
        modes = [m for m in _EMBED_MODES if m[0] == _EMBED["mode"]]
    else:
        modes = _EMBED_MODES
    for name, extra in modes:
        try:
            data = _call("GET", "/admin/products", {**params, **extra})
        except Cafe24Error as e:
            log.info(f"[카페24] 품목 붙여 받기 '{name}' 안 됨: {e}")
            continue
        batch = data.get("products", [])
        if not batch or _has_variants(batch):
            if batch and _EMBED["mode"] != name:
                _EMBED.update({"mode": name, "tried": True})
                vs = batch[0].get("variants") or []
                log.info(f"[카페24] 품목 붙여 받기 됨 ('{name}')" + ("" if not vs or "quantity" in vs[0]
                                                                  else " · 재고 수는 안 붙어 옴 → 따로 확인"))
            return batch, bool(batch)
        log.info(f"[카페24] 상품 목록에 품목이 안 붙어 옴 ('{name}')")
    _EMBED.update({"mode": None, "tried": True})
    data = _call("GET", "/admin/products", {**params, "fields": _NAME_FIELDS})
    return data.get("products", []), False


def fetch_catalog(progress=None, stop=None, product_nos=None):
    """카페24 상품 이름 + 품목(+재고)을 100개씩 한꺼번에 받기.
    product_nos를 주면 그 상품만 (차감 직전 확인·주문서 재고용). 돌려줌: ({상품번호: [품목...]}, [{no, name, custom}])
    품목이 안 붙어 오면 ({}, 이름 목록)"""
    out, names, k = {}, [], 0
    embed_ok = True

    def take(batch, ok):
        nonlocal embed_ok
        embed_ok = embed_ok and ok
        for p in batch:
            no = int(p["product_no"])
            names.append({"no": no, "name": p.get("product_name") or "", "custom": p.get("custom_product_code") or ""})
            if ok:
                out[no] = [_variant(v) for v in (p.get("variants") or [])]

    if product_nos:
        nos = sorted({int(x) for x in product_nos})
        for i in range(0, len(nos), 100):
            k += 1
            batch, ok = _get_products({"product_no": ",".join(map(str, nos[i:i + 100])), "limit": 100})
            take(batch, ok)
        return (out if embed_ok else {}), names
    total = product_count()
    since = 0
    while True:
        if stop and stop.is_set():
            break
        batch, ok = _get_products({"limit": 100, "since_product_no": since})
        k += 1
        if not batch:
            break
        take(batch, ok)
        if progress:
            progress(len(names), total or 0)
        new_since = max(int(p["product_no"]) for p in batch)
        if len(batch) < 100 or new_since <= since:
            break
        since = new_since
        time.sleep(0.3)
    log.info(f"[카페24] 상품·품목 한꺼번에 받기: 상품 {len(names)}개 ({k}번 요청)" + ("" if embed_ok else " · 품목 없음"))
    if not (stop and stop.is_set()) and names and (not total or len(names) >= total):
        uniq = {p["no"]: p for p in names}
        op.write_json(PRODUCTS_PATH, {"time": dt.datetime.now().isoformat(timespec="seconds"),
                                      "products": sorted(uniq.values(), key=lambda p: p["no"]), "expected": total})
    return (out if embed_ok else {}), names


def stock_lookup(product_nos, progress=None, wanted=None):
    """주문서용: 상품들의 품목별 현재 재고 → {(상품번호, 품목코드): 재고 수 또는 None(재고 관리 안 함)}
    ① 100개씩 한꺼번에 → ② 안 되면 상품마다 품목 목록(동시에 4개씩) → ③ 재고 수가 없으면 품목마다 재고(동시에 4개씩)"""
    from concurrent.futures import ThreadPoolExecutor
    nos = sorted({int(x) for x in product_nos})
    if not nos:
        return {}
    get_access_token(load_config())      # 동시에 보내기 전에 인증을 한 번 새로
    allv, _names = fetch_catalog(product_nos=nos)
    missing = [n for n in nos if n not in allv]
    if missing:
        with ThreadPoolExecutor(4) as ex:
            for n, vs in zip(missing, ex.map(lambda x: _safe(get_variants, x), missing)):
                if vs is not None:
                    allv[n] = vs
    out, need_inv = {}, []
    for n, vs in allv.items():
        for v in vs:
            if v.get("qty", "?") == "?":
                if wanted is None or (n, v["code"]) in wanted:      # 재고를 따로 물을 땐 필요한 품목만
                    need_inv.append((n, v["code"]))
            else:
                out[(n, v["code"])] = v["qty"]
    if need_inv:
        def one(key):
            inv = _safe(get_inventory, *key)
            if inv is None:
                return "?"
            return inv["quantity"] if inv.get("use_inventory", "T") == "T" else None
        with ThreadPoolExecutor(4) as ex:
            for key, q in zip(need_inv, ex.map(one, need_inv)):
                out[key] = q
    return out


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        log.info(f"[카페24] {fn.__name__}{a} 실패: {e}")
        return None
    total = product_count()
    since = 0
    while True:
        if stop and stop.is_set():
            break
        data = _call("GET", "/admin/products", {"limit": 100, "fields": fields, "embed": "variants",
                                                "since_product_no": since})
        batch = data.get("products", [])
        k += 1
        if not batch:
            break
        take(batch)
        if progress:
            progress(len(names), total or 0)
        new_since = max(int(p["product_no"]) for p in batch)
        if len(batch) < 100 or new_since <= since:
            break
        since = new_since
        time.sleep(0.3)
    log.info(f"[카페24] 상품·품목 한꺼번에 받기: 상품 {len(names)}개 ({k}번 요청)" + ("" if embed_ok else " · 품목 없음"))
    if not (stop and stop.is_set()) and names and (not total or len(names) >= total):
        uniq = {p["no"]: p for p in names}
        op.write_json(PRODUCTS_PATH, {"time": dt.datetime.now().isoformat(timespec="seconds"),
                                      "products": sorted(uniq.values(), key=lambda p: p["no"]), "expected": total})
    return (out if embed_ok else {}), names


def get_inventory(product_no, variant_code):
    data = _call("GET", f"/admin/products/{product_no}/variants/{variant_code}/inventories")
    inv = data.get("inventory") or {}
    if isinstance(inv, list):
        inv = inv[0] if inv else {}
    return {"quantity": int(inv.get("quantity") or 0), "use_inventory": inv.get("use_inventory", "T")}


def set_inventory(product_no, variant_code, quantity):
    data = _call("PUT", f"/admin/products/{product_no}/variants/{variant_code}/inventories",
                 body={"shop_no": 1, "request": {"quantity": int(quantity)}})
    inv = data.get("inventory") or {}
    if isinstance(inv, list):
        inv = inv[0] if inv else {}
    return int(inv.get("quantity", quantity))
