# -*- coding: utf-8 -*-
"""ETSY 판매분 → 카페24 재고 차감
- 차감 대기: 주문서를 인쇄한 주문의 상품 줄(Etsy transaction)마다 1줄. 같은 줄은 두 번 차감되지 않음
- 연결표(매핑표): Etsy 상품(listing) → 카페24 상품, Etsy 옵션 → 카페24 품목 + 배수
  방식  each   : 옵션마다 따로 (예: First Letter:F → 품목 F, Second Letter:H → 품목 H)
        combo  : 옵션 조합으로 품목 하나 (예: Color:Red + Size:S → 품목 빨강/S)
        single : 옵션 상관없이 품목 하나 (카페24 상품에 옵션이 없을 때 등)
- 머리글자 대응표: Etsy 'PD' → 카페24 'PDT' 처럼 코드 앞 글자가 다른 경우
- 단위 환산표: 상품명의 '2 Pcs' '1 Pair' '1조' '(10개)'를 읽어 배수를 자동 계산 (1 PCS = 1개, 1쌍·1조 = 2개)
- 연습 모드: 카페24 재고를 바꾸지 않고 계산만"""
import bisect
import datetime as dt
import time
import itertools
import json
import os
import re
import uuid

import cafe24 as C
import orderprint as op

QUEUE_PATH = os.path.join(op.BASE_DIR, "stock_queue.json")
MAP_PATH = os.path.join(op.BASE_DIR, "stock_map.json")
PREFIX_PATH = os.path.join(op.BASE_DIR, "stock_prefix.json")
HISTORY_PATH = os.path.join(op.BASE_DIR, "stock_history.json")
UNITS_PATH = os.path.join(op.BASE_DIR, "stock_units.json")
EXCL_PATH = os.path.join(op.BASE_DIR, "stock_excluded.json")
SHORT_PATH = os.path.join(op.BASE_DIR, "stock_short.json")
SNAP_PATH = os.path.join(op.BASE_DIR, "stock_print_snap.json")      # 주문서에 적은 재고 상태 (차감 때 비교)
VALMAP_PATH = os.path.join(op.BASE_DIR, "stock_valuemap.json")
DEFAULT_VALMAP = [{"etsy": "Silver", "cafe24": ["무도금", "백금도금"]},
                  {"etsy": "Gold", "cafe24": ["골드"]},
                  {"etsy": "Rose Gold", "cafe24": ["로즈골드"]}]
KEEP_DAYS = 90                 # 끝난(차감함·제외) 줄과 기록 보관

IGNORE_WORDS = ("personal",)   # 이름에 이 글자가 들어간 옵션은 재고와 상관없음 (개인화 문구 등)

# 흔한 색 이름 추천용 (최종 선택은 사람이)
COLOR_KO = {
    "white": ["화이트", "흰색", "백색"], "black": ["블랙", "검정", "흑색"], "ivory": ["아이보리"], "beige": ["베이지"],
    "red": ["레드", "빨강", "적색"], "pink": ["핑크", "분홍"], "orange": ["오렌지", "주황"], "yellow": ["옐로우", "노랑", "옐로"],
    "green": ["그린", "초록", "녹색"], "mint": ["민트"], "blue": ["블루", "파랑", "청색"], "navy": ["네이비", "남색"],
    "purple": ["퍼플", "보라", "보라색"], "lavender": ["라벤더"], "brown": ["브라운", "갈색"], "gray": ["그레이", "회색"],
    "grey": ["그레이", "회색"], "gold": ["골드", "금색"], "silver": ["실버", "은색"], "rose gold": ["로즈골드"],
    "clear": ["투명", "클리어"], "transparent": ["투명"], "champagne": ["샴페인"], "khaki": ["카키"], "coral": ["코랄"],
    "mustard": ["머스타드", "겨자"], "matte gold": ["무광골드", "무광 골드"], "rhodium": ["로듐"], "bronze": ["브론즈"],
}


def now_s():
    return f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"


# ---------------------------------------------------------------- 파일
def load_queue():
    return [q for q in op.read_json(QUEUE_PATH, []) if isinstance(q, dict)]


def save_queue(q):
    op.write_json(QUEUE_PATH, q)


def load_map():
    return op.read_json(MAP_PATH, {})


def save_map(m):
    op.write_json(MAP_PATH, m)


_PFX = {"key": "x", "rows": []}


def load_prefix():
    """[{etsy: 'PD', cafe24: 'PDT'}] (파일이 안 바뀌었으면 읽어 둔 것을 씀)"""
    key = _file_key(PREFIX_PATH)
    if _PFX["key"] != key:
        _PFX.update({"key": key, "rows": op.read_json(PREFIX_PATH, [])})
    return [dict(r) for r in _PFX["rows"]]


def save_prefix(rows):
    op.write_json(PREFIX_PATH, rows)
    _PFX["key"] = "x"


def load_excluded():
    """연결 안 함: {listing_id: {title, time, map(지우기 전 연결)}}"""
    return op.read_json(EXCL_PATH, {})


def save_excluded(d):
    op.write_json(EXCL_PATH, d)


def exclude(items):
    """items: [(listing_id, 제목)] → 연결 안 함으로. 연결표에 있던 것은 떼어서 같이 보관 (되돌리면 복구)"""
    ex, m = load_excluded(), load_map()
    for lid, title in items:
        ex[lid] = {"title": title, "time": now_s(), "map": m.pop(lid, None)}
    save_excluded(ex)
    save_map(m)
    op.log.info(f"[연결 안 함] {len(items)}개 추가")


def unexclude(lids):
    ex, m = load_excluded(), load_map()
    for lid in lids:
        it = ex.pop(lid, None)
        if it and it.get("map") and lid not in m:
            m[lid] = it["map"]
    save_excluded(ex)
    save_map(m)
    op.log.info(f"[연결 안 함] {len(lids)}개 되돌림")


def load_history():
    return op.read_json(HISTORY_PATH, [])


def save_history(h):
    op.write_json(HISTORY_PATH, h)


# ---------------------------------------------------------------- 단위 환산 (배수 자동 계산)
# 값이 숫자 = 낱개 몇 개인지 / 글자 = 개수로 못 바꾸는 묶음 단위 (같은 묶음 단위끼리만 계산)
DEFAULT_UNITS = {
    "pc": 1, "pcs": 1, "piece": 1, "pieces": 1, "ea": 1, "개": 1, "개입": 1, "알": 1, "피스": 1,
    "pair": 2, "pairs": 2, "쌍": 2, "조": 2,
    "set": "세트", "sets": "세트", "세트": "세트",
    "strand": "줄", "strands": "줄", "줄": "줄",
    "lot": "lot", "lots": "lot",
    "pack": "팩", "packs": "팩", "팩": "팩", "봉": "팩",
}


def load_units():
    u = op.read_json(UNITS_PATH, None)
    return u if isinstance(u, dict) and u else dict(DEFAULT_UNITS)


def save_units(u):
    op.write_json(UNITS_PATH, u)


_URE = {}


def _unit_re(units):
    k = tuple(sorted(units))
    if k in _URE:
        return _URE[k]
    _URE.clear()
    _URE[k] = _unit_re_make(units)
    return _URE[k]


def _unit_re_make(units):
    words = sorted(units, key=len, reverse=True)
    alt = "|".join(re.escape(w) for w in words)
    return re.compile(r"(\d+(?:\.\d+)?)\s*(" + alt + r")(?![A-Za-z])", re.I)


def read_unit(text, units=None, prefer="first"):
    """'HP97 / 2 Pcs - ...' → {'n': 2, 'word': 'Pcs', 'pcs': 2, 'family': None}
    '10 Set' → {'n': 10, 'word': 'Set', 'pcs': None, 'family': '세트'}  /  못 읽으면 None
    prefer='first': 처음 나온 단위 (Etsy) / 'paren_last': 괄호 안 단위 → 없으면 맨 뒤 단위 (카페24: '3봉 산팬던트(1개)' → 1개)"""
    units = units or load_units()
    text = str(text or "")
    rx = _unit_re(units)
    m = None
    if prefer == "paren_last":
        for inner in reversed(re.findall(r"[(\[]([^()\[\]]*)[)\]]", text)):
            found = list(rx.finditer(inner))
            if found:
                m = found[-1]
                break
        if m is None:
            found = list(rx.finditer(text))
            m = found[-1] if found else None
    else:
        m = rx.search(text)
    if not m:
        return None
    n = float(m.group(1))
    n = int(n) if n == int(n) else n
    word = m.group(2)
    val = units.get(word.lower(), units.get(word))
    if isinstance(val, (int, float)):
        return {"n": n, "word": word, "pcs": n * val, "family": None, "text": _ut(n, word)}
    return {"n": n, "word": word, "pcs": None, "family": str(val), "text": _ut(n, word)}


def _ut(n, word):
    return f"{n}{word}" if not word.isascii() else f"{n} {word}"


def etsy_unit(title, option_value=None, variations=None, units=None):
    """Etsy 쪽 1묶음: 그 옵션 값 → 다른 옵션 값 → 상품명 순으로 단위를 찾음"""
    units = units or load_units()
    for t in [option_value] + [v.get("value") for v in (variations or [])]:
        if t:
            u = read_unit(t, units)
            if u:
                return u, "옵션"
    title = str(title or "")
    # 'HP97 / 2 Pcs - ...' 처럼 '/' 뒤를 먼저
    tail = title.split("/", 1)[1] if "/" in title else title
    u = read_unit(tail, units) or read_unit(title, units)
    return (u, "상품명") if u else (None, "")


def cafe24_unit(product_name, variant_label=None, units=None):
    """카페24 쪽 1개: 품목(옵션) 이름 → 상품명 순. 단위 표기가 없으면 낱개 1개로 봄"""
    units = units or load_units()
    for t in (variant_label, product_name):
        if t:
            u = read_unit(t, units, prefer="paren_last")
            if u:
                return u
    return {"n": 1, "word": "", "pcs": 1, "family": None, "text": "단위 없음(1개)"}


def per_option_unit(etsy_u, n_opts):
    """상품명의 개수가 여러 옵션을 합친 개수일 때 → 옵션 하나당 개수
    예: 'IN-002 / 2 pcs' + First Letter·Second Letter → 옵션 하나당 1개. 나눠떨어지지 않으면 None"""
    if not etsy_u or n_opts < 2 or etsy_u.get("pcs") is None:
        return None
    pcs = etsy_u["pcs"]
    if pcs != n_opts:                 # 개수 = 옵션 수일 때만 확실 (2 pcs + 옵션 2개). 4 pcs + 옵션 2개 등은 사람이 확인
        return None
    per = 1
    return {**etsy_u, "n": per, "pcs": per, "word": "Pc", "text": f"{etsy_u['text']}÷옵션 {n_opts}개"}


def suggest_mult(etsy_u, c24_u):
    """돌려줌: (배수 또는 None, 설명). None이면 사람이 배수를 골라야 함"""
    if not etsy_u:
        return None, "Etsy 상품명에서 단위(Pcs·Pair 등)를 못 읽었습니다. 배수를 직접 골라 주세요."
    if etsy_u["pcs"] is not None and c24_u["pcs"] is not None:
        a, b = etsy_u["pcs"], c24_u["pcs"]
        how = f"Etsy {_pl(etsy_u)} ÷ 카페24 {_pl(c24_u)}"
    elif etsy_u["family"] and etsy_u["family"] == c24_u["family"]:
        a, b = etsy_u["n"], c24_u["n"]
        how = f"Etsy {etsy_u['text']} ÷ 카페24 {c24_u['text']}"
    elif etsy_u["family"] and c24_u["pcs"] is not None and not c24_u["word"]:
        return None, (f"Etsy는 '{etsy_u['text']}' 묶음 단위라 낱개로 바꿀 수 없습니다. "
                      f"카페24 1개가 Etsy 1 {etsy_u['word']}과 같으면 배수 {_n(etsy_u['n'])}, 아니면 직접 골라 주세요.")
    else:
        return None, f"단위가 서로 다릅니다: Etsy {etsy_u['text']} ↔ 카페24 {c24_u['text']}. 배수를 직접 골라 주세요."
    if b and a / b >= 1 and abs(a / b - round(a / b)) < 1e-9:
        k = int(round(a / b))
        return k, f"{how} = {k}"
    return None, f"단위가 안 맞습니다: {how} = {_n(a / b if b else 0)}. 배수를 직접 골라 주세요."


def _pl(u):
    """'1조(2개)' / '2 Pcs' / '단위 없음(1개)' — 낱개와 같은 단위면 괄호 생략"""
    if not u["word"] or u["pcs"] == u["n"]:
        return u["text"]
    return f"{u['text']}({_n(u['pcs'])}개)"


def _n(x):
    try:
        return str(int(x)) if float(x) == int(x) else f"{x:g}"
    except (TypeError, ValueError):
        return str(x)


# ---------------------------------------------------------------- 차감 대기에 넣기 (주문서 인쇄할 때)
def enqueue(receipts):
    q = load_queue()
    have = {x["tid"] for x in q}
    added = 0
    for r in receipts:
        for t in r.get("transactions", []) or []:
            tid = str(t.get("transaction_id") or f"{r.get('receipt_id')}-{t.get('listing_id')}-{len(q)}")
            if tid in have:
                continue
            q.append({
                "tid": tid, "receipt_id": str(r.get("receipt_id")), "name": op.clean(r.get("name")),
                "time": now_s(), "listing_id": str(t.get("listing_id") or ""), "title": op.clean(t.get("title")),
                "quantity": int(t.get("quantity") or 1),
                "variations": [{"name": op.clean(v.get("formatted_name")), "value": op.clean(v.get("formatted_value"))}
                               for v in (t.get("variations") or []) if op.clean(v.get("formatted_value"))],
                "status": "pending", "hist": ""})
            have.add(tid)
            added += 1
    if added:
        save_queue(q)
    return added


def prune():
    limit = (dt.datetime.now() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    q = load_queue()
    keep = [x for x in q if x["status"] == "pending" or x.get("time", "") >= limit]
    if len(keep) != len(q):
        save_queue(keep)
    h = load_history()
    hk = [x for x in h if x.get("time", "") >= limit]
    if len(hk) != len(h):
        save_history(hk)


# ---------------------------------------------------------------- 코드 찾기
def apply_prefix(ncode):
    pre, rest = C.split_code(ncode)
    for row in load_prefix():
        if C.norm_code(row.get("etsy")) == pre and row.get("cafe24"):
            return C.norm_code(row["cafe24"]) + rest
    return ncode


def zfix(ncode):
    """숫자 앞의 0 빼기: 'SIL022' → 'SIL22', 'IN002MG' → 'IN2MG'"""
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", ncode)


def core_code(ncode):
    """머리글자+번호만: 'SIL22OR' → 'SIL22' (카페24에서 안 쓰는 끝 글자 OR·GD 등 빼기)"""
    m = re.match(r"^([A-Z]*[0-9]+)", ncode)
    return m.group(1) if m else ncode


_IDX = {"key": None}


def _file_key(path):
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def product_index():
    """카페24 상품 이름 목록 → 코드로 바로 찾는 색인 (파일이 바뀔 때만 다시 만듦)"""
    key = _file_key(C.PRODUCTS_PATH)
    if _IDX["key"] != key or "codes" not in _IDX:
        codes, cores, keys = {}, {}, []
        for p in C.load_products().get("products", []):
            pc = zfix(C.norm_code(C.extract_code(p["name"])) or C.norm_code(p.get("custom")))
            if not pc:
                continue
            codes.setdefault(pc, []).append(p)
            cores.setdefault(core_code(pc), []).append(p)
            keys.append(pc)
        keys = sorted(set(keys))
        _IDX.update({"key": key, "codes": codes, "cores": cores, "keys": keys})
    return _IDX


def match_products(title):
    """Etsy 상품명 → (똑같은 코드, 번호까지 같은 코드, 앞부분만 같은 코드, 찾는 코드)
    비교할 때 하이픈·띄어쓰기, 숫자 앞의 0(022 = 22)은 무시. 머리글자 대응표 적용"""
    code = C.extract_code(title)
    if not code:
        return [], [], [], ""
    want = zfix(apply_prefix(C.norm_code(code)))
    wcore = core_code(want)
    ix = product_index()
    exact = list(ix["codes"].get(want, []))
    seen = {id(p) for p in exact}
    core = [p for p in ix["cores"].get(wcore, []) if id(p) not in seen]
    seen |= {id(p) for p in core}
    near = []
    keys = ix["keys"]
    k = bisect.bisect_left(keys, wcore)                       # wcore로 시작하는 코드
    while k < len(keys) and keys[k].startswith(wcore) and len(near) < 60:
        near += [p for p in ix["codes"][keys[k]] if id(p) not in seen]
        k += 1
    for n in range(1, len(want)):                             # want가 그 코드로 시작
        for p in ix["codes"].get(want[:n], []):
            if id(p) not in seen:
                near.append(p)
    uniq, ids = [], set()
    for p in near:
        if id(p) not in ids and id(p) not in seen:
            ids.add(id(p))
            uniq.append(p)
    return exact, core, uniq, want


def candidates(title, limit=30):
    """Etsy 상품명 → 카페24 상품 후보 (상품 이름 목록에서). 똑같은 코드 → 번호까지 같은 코드 → 앞부분이 같은 코드 순"""
    exact, core, near, want = match_products(title)
    return (exact + core + near)[:limit], want


def is_ignorable(prop_name):
    n = (prop_name or "").lower()
    return any(w in n for w in IGNORE_WORDS)


def _loose_hits(value, variants):
    """Etsy 옵션 값과 비슷한 카페24 품목들 (같은 글자 / 흔한 색 이름 / 포함)"""
    v = str(value or "").strip().lower()
    if ")" in v[:5]:
        v = v.split(")", 1)[1].strip()
    if not v:
        return []
    words = COLOR_KO.get(v, [])
    hits = []
    for var in variants:
        for x in var["values"]:
            xs = str(x)
            if xs.strip().lower() == v or any(w in xs for w in words) or v in xs.lower():
                hits.append(var)
                break
    return hits


def suggest_combo(values, variants):
    """조합(여러 옵션 값) → 그 값들이 모두 들어 있는 카페24 품목이 딱 하나면 그 품목 코드"""
    pool = None
    vm = load_valmap()
    for v in values:
        ids = {id(x) for x in _loose_hits(v, variants)}
        row = valmap_entry(v, vm)
        if row:
            ids |= {id(x) for x in variants if any(_c24_hit(c, t) for c in x["values"] for t in row["cafe24"])}
        pool = ids if pool is None else pool & ids
        if not pool:
            return None
    found = [x for x in variants if id(x) in (pool or set())]
    return found[0]["code"] if len(found) == 1 else None


def combo_rows(variations, ignore=()):
    """옵션 조합 방식의 줄: 옵션마다 값이 하나면 1줄, 여러 값이면 가능한 조합 전부 [(키, [값...])]"""
    names, vals = [], {}
    for v in variations:
        n = v["name"]
        if n in ignore or is_ignorable(n):
            continue
        if n not in vals:
            names.append(n)
            vals[n] = []
        if v["value"] not in vals[n]:
            vals[n].append(v["value"])
    if not names:
        return []
    out = []
    for combo in itertools.product(*[vals[n] for n in names]):
        pairs = list(zip(names, combo))
        out.append((_combo_key(pairs), list(combo)))
    return out


def suggest_variant(value, variants):
    """Etsy 옵션 값 → 카페24 품목 추천 (확실한 것·대응표 먼저, 그다음 같은 글자 / 흔한 색 이름). 못 찾으면 None"""
    sv = strict_variant(value, variants)
    if sv:
        return sv["code"]
    if valmap_entry(value):                 # 대응표에 있는 값인데 못 정했으면(여러 개) 사람이 고르게 비워 둠
        return None
    raw = str(value or "").strip()
    v = raw.lower()
    # '13) Beige' 처럼 앞 번호 떼기
    if ")" in v[:5]:
        v = v.split(")", 1)[1].strip()
    for var in variants:
        if any(str(x).strip().lower() == v for x in var["values"]):
            return var["code"]
    words = COLOR_KO.get(v, [])
    for var in variants:
        for x in var["values"]:
            xs = str(x)
            if any(w in xs for w in words) or (v and v in xs.lower()):
                return var["code"]
    return None


# ---------------------------------------------------------------- 옵션 값 대응표 (Etsy Silver → 카페24 무도금·백금도금)
def load_valmap():
    d = op.read_json(VALMAP_PATH, None)
    return d if isinstance(d, list) else [dict(x, cafe24=list(x["cafe24"])) for x in DEFAULT_VALMAP]


def save_valmap(rows):
    op.write_json(VALMAP_PATH, rows)


def _clean_val(value):
    v = str(value or "").strip()
    if ")" in v[:5]:                      # '13) Beige' 앞 번호 떼기
        v = v.split(")", 1)[1].strip()
    return v


def valmap_entry(value, vm=None):
    """Etsy 옵션 값에 들어 있는 대응표 단어 (여러 개면 가장 긴 것: 'Rose Gold'가 'Gold'보다 먼저)"""
    vm = load_valmap() if vm is None else vm
    v = _clean_val(value).lower()
    best = None
    for row in vm:
        w = str(row.get("etsy", "")).strip().lower()
        if w and re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", v):
            if best is None or len(w) > len(best["etsy"]):
                best = row
    return best


def _c24_hit(cval, target):
    """카페24 옵션 값이 대응 값과 같거나 그 값으로 시작 ('백금도금-오픈고리' ✓, '무광백금' ✗)"""
    c = str(cval or "").strip().replace(" ", "")
    t = str(target or "").strip().replace(" ", "")
    return bool(t) and (c == t or c.startswith(t))


def valmap_match(value, variants, vm=None):
    """대응표로 찾기 → (품목 또는 None, 이유). 그 상품의 카페24 품목 중 대응 값에 맞는 게 딱 하나일 때만"""
    row = valmap_entry(value, vm)
    if not row:
        return None, ""
    targets = row.get("cafe24", [])
    hits = [var for var in variants if any(_c24_hit(x, t) for x in var["values"] for t in targets)]
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, f"대응표({row['etsy']} → {', '.join(targets)})에 맞는 카페24 품목이 없음"
    found = sorted({x for var in hits for x in var["values"] if any(_c24_hit(x, t) for t in targets)})
    return None, f"대응표로 찾은 카페24 품목이 여러 개({', '.join(found[:4])})라 사람이 골라야 함"


def strict_variant(value, variants):
    """자동 연결용: Etsy 옵션 값과 확실히 같은 카페24 품목이 딱 하나일 때만 그 품목 (아니면 None)"""
    v = str(value or "").strip().lower()
    if ")" in v[:5]:
        v = v.split(")", 1)[1].strip()
    if not v:
        return None
    words = {v} | set(COLOR_KO.get(v, []))
    hit = [var for var in variants if any(str(x).strip().lower() in words for x in var["values"])]
    if len(hit) == 1:
        return hit[0]
    var, _why = valmap_match(value, variants)          # 옵션 값 대응표 (Silver → 무도금·백금도금 등)
    return var


def variant_qty(pno, var):
    """품목의 카페24 재고 (재고 관리 안 하면 None). 품목 목록에 재고가 같이 왔으면 그대로, 아니면 따로 확인"""
    q = var.get("qty", "?")
    if q != "?":
        return q
    inv = C.get_inventory(pno, var["code"])
    return inv["quantity"] if inv.get("use_inventory", "T") == "T" else None


def connect_listing(e, title, pairs, variants, units, problems, other_units=True):
    """한 Etsy 상품(listing)의 옵션 값들을 확실한 것만 연결표 항목 e에 채움 (자동 연결·미리 연결 공용)
    pairs: [(옵션 이름, 값)] — 옵션이 없으면 [] / 돌려줌: 새로 연결한 [(이름, 값, 연결, 설명)]
    못 한 것은 problems에 이유를 넣음. 이미 연결된 값은 건드리지 않음"""
    pno = e["product_no"]
    ignore = set(e.get("ignore", []))
    vals = [(a, b) for a, b in pairs if a not in ignore and not is_ignorable(a)]
    mode = e.get("mode") or ("single" if not vals or len(variants) == 1 else "each")
    if mode == "combo":
        problems.append("옵션 조합 방식이라 직접 연결")
        return []
    if mode == "single" or not vals:
        if e.get("single"):
            e["mode"] = mode
            return []
        if len(variants) != 1:
            problems.append(f"카페24 품목이 {len(variants)}개인데 Etsy 옵션이 없어 고를 수 없음")
            return []
        items = [("__single__", None, variants[0])]
    else:
        items = []
        for name, value in vals:
            if e.get("each", {}).get(name, {}).get(value):
                continue
            var = strict_variant(value, variants) if len(variants) > 1 else variants[0]
            if not var:
                _v, why = valmap_match(value, variants)
                problems.append(f"옵션 '{name}: {value}' — " + (why or "같은 카페24 품목을 못 찾음"))
                continue
            items.append((name, value, var))
    names = {n for n, _v, _var in items if n != "__single__"} | {n for n in e.get("each", {}) if n not in ignore}
    multi = names if len(names) > 1 else set()
    new = []
    for name, value, var in items:
        try:
            qn = variant_qty(pno, var)
        except Exception:
            qn = None
        if is_plenty(qn):
            new.append((name, value, {"variant": var["code"], "label": var["label"], "mult": 1, "skip": "plenty",
                                      "stock": qn, "auto": now_s()}, f"재고 {qn:,} → 재고 풀어둠·안 뺌으로 고정"))
            continue
        eu, src = etsy_unit(title, value, [{"value": b} for a, b in vals if a != name] if other_units else [], units)
        if src == "상품명" and multi:
            # 예: 'IN-002 / 2 pcs' + First Letter·Second Letter → 2개가 두 옵션에 나뉨 → 옵션 하나당 1개
            per = per_option_unit(eu, len(multi))
            if per is None:
                problems.append(f"'{value}' 옵션이 여러 개({', '.join(sorted(multi))})인데 상품명의 개수({eu['text'] if eu else '?'})가 "
                                f"옵션 수와 달라 옵션마다인지 합쳐서인지 알 수 없음 — 배수 확인 필요")
                continue
            eu = per
        k, why = suggest_mult(eu, cafe24_unit(e["product_name"], var["label"], units))
        if not k:
            problems.append(f"'{value or '옵션 없음'}' 배수 확인 필요 ({why.replace(' 배수를 직접 골라 주세요.', '')})")
            continue
        new.append((name, value, {"variant": var["code"], "label": var["label"], "mult": k, "auto": now_s()}, why))
    if new:
        e["mode"] = mode
        e.setdefault("etsy_title", title)
        for name, value, val, _why in new:
            if name == "__single__":
                e["single"] = val
            else:
                e.setdefault("each", {}).setdefault(name, {})[value] = val
    return new


def _overlap_sig(e):
    """연결 상태 지문 — [이대로 맞음] 뒤에 연결이 바뀌면 다시 확인 대상이 되게"""
    import hashlib
    rows = sorted((n, v, s.get("variant") or "", int(s.get("mult", 1)), s.get("skip") or "", bool(s.get("manual")))
                  for n, vals in e.get("each", {}).items() for v, s in vals.items())
    return hashlib.md5(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def overlap_issues(e, with_ok=False):
    """한 상품 안의 옵션 겹침 (옵션마다 따로 방식만)
    ① Etsy 같은 값이 옵션 이름에 따라 다른 카페24 품목에 연결됨 (예: First Letter: A → A, Second Letter: A → B)
    ② 카페24 품목 하나에 Etsy의 다른 값 2개 이상이 연결됨 (예: Silver·Gold → 무도금)
       같은 값끼리(First/Second Letter: A → A)는 정상이라 뺌. 재고 풀어둠·안 뺌, 직접 차감 칸은 보지 않음
    with_ok=False면 [이대로 맞음] 처리한 상품은 [] """
    if not e.get("product_no") or e.get("mode", "each") != "each":
        return []
    if not with_ok and e.get("overlap_ok") and e["overlap_ok"] == _overlap_sig(e):
        return []
    ignore = set(e.get("ignore", []))
    cells = [(n, v, s) for n, vals in e.get("each", {}).items() if n not in ignore and not is_ignorable(n)
             for v, s in vals.items() if s.get("variant") and not s.get("skip") and not s.get("manual")]
    out = []
    by_val = {}
    for n, v, s in cells:
        by_val.setdefault(op.clean(v).lower(), []).append((n, v, s))
    for _k, lst in by_val.items():
        codes = {s["variant"] for _n, _v, s in lst}
        if len(codes) > 1:
            out.append("① Etsy '" + lst[0][1] + "'이 품목 " + str(len(codes)) + "개로 나뉨: "
                       + ", ".join(f"{n} → {s.get('label') or s['variant']}" for n, _v, s in lst))
    by_var = {}
    for n, v, s in cells:
        by_var.setdefault(s["variant"], []).append((n, v, s))
    for code, lst in by_var.items():
        vals = {op.clean(v).lower() for _n, v, _s in lst}
        if len(vals) > 1:
            seen, parts = set(), []
            for n, v, s in lst:
                if (n, v) not in seen:
                    seen.add((n, v))
                    parts.append(f"{n}: {v}(배수 {int(s.get('mult', 1))})")
            out.append(f"② 카페24 '{lst[0][2].get('label') or code}'에 Etsy {len(vals)}개 연결: " + ", ".join(parts))
    return out


def overlap_ok(lids):
    """[이대로 맞음] — 지금 연결 상태를 맞다고 기억 (연결이 바뀌면 다시 나옴)"""
    m = load_map()
    for lid in lids:
        e = m.get(lid)
        if e:
            e["overlap_ok"] = _overlap_sig(e)
    save_map(m)
    op.log.info(f"[옵션 겹침] {len(lids)}개 이대로 맞음")


def pick_product(title):
    """코드로 카페24 상품이 딱 하나면 그 상품, 아니면 (None, 이유)"""
    exact, core, _near, _want = match_products(title)
    pick = exact if exact else core
    if len(pick) != 1:
        return None, "카페24 상품 후보가 " + ("없음" if not pick else f"{len(pick)}개라 고를 수 없음")
    return pick[0], ""


def auto_connect(rows, progress=None):
    """'연결 필요' 줄 중 확실한 것만 연결표에 저장. 돌려줌: (연결한 결과 [문구], 못 한 줄 [문구])
    확실하다 = 카페24 상품이 코드로 딱 하나 + 옵션 값이 카페24 품목과 딱 하나 같음 + 단위로 배수가 딱 계산됨
    (카페24 재고가 기준 이상이면 배수 없이 '안 뺌'으로 고정)"""
    m = load_map()
    units = load_units()
    vcache, done, skip, changed = {}, [], [], False
    todo = [r for r in rows if r.get("why") and r.get("status", "pending") == "pending"]
    seen = set()
    for i, r in enumerate(todo, 1):
        if progress:
            progress(i, len(todo))
        key = (r["listing_id"], tuple((v["name"], v["value"]) for v in r["variations"]))
        if key in seen:
            continue
        seen.add(key)
        title = r["title"]
        e = dict(m.get(r["listing_id"]) or {})
        if not e.get("product_no"):
            p, why = pick_product(title)
            if not p:
                skip.append(f"{title[:40]} — {why}")
                continue
            e = {"product_no": p["no"], "product_name": p["name"], "etsy_title": title, "ignore": []}
        pno = e["product_no"]
        if pno not in vcache:
            vcache[pno] = C.get_variants(pno)
        problems = []
        new = connect_listing(e, title, [(v["name"], v["value"]) for v in r["variations"]], vcache[pno], units, problems)
        for name, value, val, why in new:
            done.append(f"{title[:34]} → {e['product_name'][:24]} · {val['label']} · "
                        + ("안 뺌" if val.get("skip") else f"배수 {val['mult']}") + f"  ({why})")
        if problems:
            skip.append(f"{title[:40]} — " + "; ".join(problems))
        if new or not (m.get(r["listing_id"]) or {}).get("product_no"):
            m[r["listing_id"]] = e           # 옵션은 못 했어도 상품 연결은 남겨 둠
            changed = True
    if changed:
        save_map(m)
        op.log.info(f"[자동 연결] {len(done)}개 연결, {len(skip)}줄은 직접 연결")
    return done, skip


def plenty_limit():
    """카페24 재고가 이 숫자 이상이면 '재고 풀어둠' — 빼지 않음 (0 = 끔)"""
    try:
        return max(0, int(op.load_settings().get("stock_plenty", 3000)))
    except (TypeError, ValueError):
        return 3000


def is_plenty(qty):
    lim = plenty_limit()
    return bool(lim) and qty is not None and qty >= lim


# ---------------------------------------------------------------- 연결표로 계산
def _combo_key(vals):
    return " + ".join(f"{a}:{b}" for a, b in vals)


def _ded(e, s, qty, out, what=""):
    """연결 한 칸 → 차감 목록에 추가. '재고 풀어둠·안 뺌'으로 고정된 칸은 아무것도 안 뺌.
    '직접 차감' 칸은 품목코드 None으로 넣음 (사람이 카페24에서 직접 뺄 것)"""
    if s.get("skip"):
        return
    if s.get("manual"):
        out.append((e["product_no"], None, what or "(옵션)", qty))
        return
    out.append((e["product_no"], s["variant"], s.get("label", ""), qty * int(s.get("mult", 1))))


_CODE_LIKE = re.compile(r"^[A-Z]{1,6}[0-9]")


def _code_sig(e, title=None):
    return f"{op.clean(title or e.get('etsy_title') or '')}|{e.get('product_no')}"


_C24_CODE = re.compile(r"^\s*([A-Za-z]{1,8})[\s\-]?(\d+)")


def _c24_code(text):
    """카페24 상품명·자체코드 → 머리글자+번호까지만 ('PDT3330-8분음표' → 'PDT3330', 'CAP45-6mm' → 'CAP45')"""
    m = _C24_CODE.match(str(text or ""))
    return (m.group(1) + m.group(2)).upper() if m else ""


def code_mismatch(e, title=None):
    """Etsy 상품 코드와 연결된 카페24 상품 코드가 다른지 → (Etsy 코드, 카페24 코드) 또는 None
    카페24 코드는 '머리글자+번호'까지만 봄 (PDT3330-8 → PDT3330). 숫자 앞 0, Etsy 끝 글자(OR·GD 등)는 무시.
    번호가 같고 머리글자가 같거나(대응표 적용 후) 카페24 머리글자가 Etsy 머리글자로 시작하면 맞음 (PL ↔ PLS, SI ↔ SIL).
    한쪽이라도 코드를 못 읽으면 비교 안 함. [이대로 맞음] 한 상품은 None (상품명·연결 상품이 바뀌면 다시 확인)"""
    if not e or not e.get("product_no"):
        return None
    t = title or e.get("etsy_title") or ""
    ec = C.extract_code(t)
    cc = _c24_code(e.get("product_name") or "")
    if not cc and str(e["product_no"]).isdigit():
        cc = _c24_code(C.custom_map().get(int(e["product_no"]), ""))
    if not ec or not cc:
        return None
    raw = core_code(zfix(C.norm_code(ec)))                    # 대응표 적용 전
    want = core_code(zfix(apply_prefix(C.norm_code(ec))))     # 대응표 적용 후
    have = zfix(cc)
    hp, hn = C.split_code(have)
    for w in {want, raw}:
        wp, wn = C.split_code(w)
        if wn and wn == hn and wp and (hp == wp or hp.startswith(wp)):
            return None
    ok = e.get("code_ok", [])
    if _code_sig(e, t) in ok or _code_sig(e) in ok:
        return None
    return ec, cc


def code_ok(lids_titles):
    """[이대로 맞음]: [(listing_id, 상품명)] → 지금 연결을 맞다고 기억"""
    m = load_map()
    for lid, title in lids_titles:
        e = m.get(lid)
        if e:
            ok = list(e.get("code_ok", []))
            sig = _code_sig(e, title)
            if sig not in ok:
                ok.append(sig)
            e["code_ok"] = ok[-10:]
    save_map(m)
    op.log.info(f"[코드 다름] {len(lids_titles)}개 이대로 맞음")


def title_mismatch(e, title):
    """주문의 Etsy 상품명이 연결할 때 기억해 둔 상품명과 다른지 (확인하고 '그대로 맞음'한 상품명은 제외)"""
    base = (e.get("snap") or {}).get("etsy_title") or e.get("etsy_title")
    t = op.clean(title)
    return bool(base and t and t != op.clean(base) and t not in e.get("ok_titles", []))


def resolve(line, m=None):
    """한 줄 → (차감 목록 [(상품번호, 품목코드, 품목이름, 개수)], 못 찾은 이유 또는 '')
    차감 목록이 비어 있고 이유도 ''이면 = 연결은 됐지만 뺄 것 없음 (재고 풀어둠·안 뺌)"""
    m = load_map() if m is None else m
    e = m.get(line["listing_id"])
    if not e or not e.get("product_no"):
        return [], "상품 연결 필요"
    if e.get("review") or title_mismatch(e, line.get("title")):
        return [], "확인 필요"
    if code_mismatch(e, line.get("title")):
        return [], "코드 다름"
    qty = int(line["quantity"])
    ignore = set(e.get("ignore", []))
    vals = [(v["name"], v["value"]) for v in line["variations"]
            if v["name"] not in ignore and not is_ignorable(v["name"])]
    mode = e.get("mode", "each")
    out = []
    if mode == "single" or not vals:
        s = e.get("single")
        if not s:
            return [], "품목 연결 필요"
        _ded(e, s, qty, out, "(모든 옵션)")
    elif mode == "combo":
        s = e.get("combo", {}).get(_combo_key(vals))
        if not s:
            return [], "옵션 연결 필요"
        _ded(e, s, qty, out, _combo_key(vals))
    else:
        for name, value in vals:
            s = e.get("each", {}).get(name, {}).get(value)
            if not s:
                return [], "옵션 연결 필요"
            _ded(e, s, qty, out, f"{name}: {value}")
    return out, ""


def pending_rows():
    """화면용: 대기 줄 + 계산 결과"""
    m = load_map()
    excl = load_excluded()
    rows = []
    for x in load_queue():
        if x["status"] != "pending":
            continue
        out, why = resolve(x, m)
        e = m.get(x["listing_id"], {})
        rows.append({**x, "ded": [d for d in out if d[1]], "manual": [d for d in out if not d[1]], "why": why,
                     "code": code_mismatch(e, x.get("title")) if why == "코드 다름" else None,
                     "product_name": e.get("product_name", ""), "excluded": x["listing_id"] in excl,
                     "fixed": [] if why else fixed_parts(x, m)})
    return rows


def fixed_parts(line, m=None):
    """'재고 풀어둠·안 뺌'으로 고정된 칸 중 카페24 품목이 정해진 것 → [(상품번호, 품목코드, 품목 이름, 필요 수)]
    (안 빼지만, 재고가 기준 아래로 내려갔는지 확인용)"""
    m = load_map() if m is None else m
    e = m.get(line["listing_id"]) or {}
    if not e.get("product_no"):
        return []
    qty = int(line["quantity"])
    ignore = set(e.get("ignore", []))
    vals = [(v["name"], v["value"]) for v in line["variations"] if v["name"] not in ignore and not is_ignorable(v["name"])]
    mode = e.get("mode", "each")
    if mode == "single" or not vals:
        ss = [e.get("single")]
    elif mode == "combo":
        ss = [e.get("combo", {}).get(_combo_key(vals))]
    else:
        ss = [e.get("each", {}).get(a, {}).get(b) for a, b in vals]
    return [(e["product_no"], s["variant"], s.get("label", ""), qty * int(s.get("mult", 1)))
            for s in ss if s and s.get("skip") and s.get("variant")]


def fixed_low(rows, stock):
    """고정 품목 중 지금 재고가 기준 아래인 것 → [{tid, listing_id, product_no, variant, label, product_name, stock, need, ...}]"""
    out = []
    for r in rows:
        for pno, code, label, need in r.get("fixed", []):
            q = stock.get((pno, code))
            if isinstance(q, int) and not is_plenty(q):
                out.append({"tid": r["tid"], "listing_id": r["listing_id"], "receipt_id": r["receipt_id"],
                            "product_no": pno, "variant": code, "label": label, "product_name": r.get("product_name", ""),
                            "stock": q, "need": need, "title": r["title"]})
    return out


def unfix(items):
    """[이번부터 빼기]: 고정을 풀고 배수를 다시 계산해 정상 연결로. 돌려줌: (푼 것, 배수를 못 정해 그대로 둔 것)"""
    m = load_map()
    units = load_units()
    done, left = [], []
    for it in items:
        e = m.get(it["listing_id"]) or {}
        slots = []
        if e.get("single", {}).get("variant") == it["variant"]:
            slots.append(("__single__", None, e["single"]))
        for k, v in e.get("combo", {}).items():
            if v.get("variant") == it["variant"]:
                slots.append(("__combo__", k, v))
        for nm, vals in e.get("each", {}).items():
            for val, v in vals.items():
                if v.get("variant") == it["variant"]:
                    slots.append((nm, val, v))
        for nm, val, v in slots:
            if not v.get("skip"):
                continue
            eu, _src = etsy_unit(e.get("etsy_title", it["title"]), val if nm not in ("__single__", "__combo__") else None, [], units)
            names = [n for n in e.get("each", {}) if n not in set(e.get("ignore", []))]
            if e.get("mode", "each") == "each" and len(names) > 1:
                eu = per_option_unit(eu, len(names)) or eu
            k, why = suggest_mult(eu, cafe24_unit(e.get("product_name", ""), v.get("label", ""), units))
            if not k:
                left.append(f"{e.get('product_name', '')[:30]} · {v.get('label', '')} — 배수를 못 정함 (연결 창에서 직접)")
                continue
            v.pop("skip", None)
            v.pop("stock", None)
            v["mult"] = k
            v["unfixed"] = now_s()
            done.append(f"{e.get('product_name', '')[:30]} · {v.get('label', '')} → 배수 {k}")
    save_map(m)
    if done:
        op.log.info(f"[재고 풀어둠 고정 풀기] {len(done)}개: " + "; ".join(done))
    return done, left


def live_states(rows, stock):
    """지금 재고로 다시 계산 (주문 순서대로 채움) → {tid: {품목코드: (상태, 재고, 모자란 수)}}"""
    avail = {k: v for k, v in stock.items() if isinstance(v, int)}
    out = {}
    for r in sorted(rows, key=lambda r: (r.get("time", ""), r["tid"])):
        st = {}
        for pno, code, _l, need in r.get("ded", []):
            q = stock.get((pno, code))
            if not isinstance(q, int) or is_plenty(q):
                st[code] = ("plenty" if isinstance(q, int) else "nostock", q, 0)
                continue
            a = avail.get((pno, code), q)
            st[code] = ("ok", q, 0) if a >= need else ("short", q, need - max(a, 0))
            avail[(pno, code)] = a - need
        out[r["tid"]] = st
    return out


def load_snap():
    return op.read_json(SNAP_PATH, {})


def print_changes(rows, stock):
    """주문서에 적은 재고 상태와 지금 상태가 달라진 줄 → [{tid, receipt_id, name, product_name, label, before, after, worse}]"""
    snap = load_snap()
    now = live_states(rows, stock)
    out = []
    for r in rows:
        s0 = snap.get(str(r["tid"]))
        if not s0:
            continue
        for pno, code, label, need in r.get("ded", []):
            b = s0.get("parts", {}).get(code)
            a = now.get(r["tid"], {}).get(code)
            if not b or not a or b[0] not in ("ok", "short") or a[0] not in ("ok", "short"):
                continue
            if b[0] == a[0] and (b[0] != "short" or b[2] == a[2]):
                continue
            fmt = lambda x: f"미 {x[2]}" if x[0] == "short" else f"재고 {x[1]}"
            out.append({"tid": r["tid"], "receipt_id": r["receipt_id"], "name": r.get("name", ""),
                        "product_name": r.get("product_name", ""), "label": opt_values(label) or label,
                        "before": fmt(b), "after": fmt(a), "worse": a[0] == "short" and (b[0] != "short" or a[2] > b[2]),
                        "time": s0.get("time", "")})
    out.sort(key=lambda x: (not x["worse"], x["receipt_id"]))
    return out


def set_status(tids, status):
    q = load_queue()
    for x in q:
        if x["tid"] in tids:
            x["status"] = status
            if status == "pending":
                x["hist"] = ""
                x.pop("skipped_at", None)
                x.pop("short_items", None)                  # 되돌리면 그 줄의 사입 기록도 지움
            elif status == "skipped":
                x["skipped_at"] = now_s()
    save_queue(q)


def batch_key(x):
    return (x.get("time") or "")[:16]


def batches(days=7):
    """출력 묶음 목록 (최근 것부터): [{key, total, pending}] — 최근 days일 묶음은 다 차감해도 포함,
    그보다 오래된 묶음은 아직 안 뺀 줄이 있을 때만"""
    since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    agg = {}
    for x in load_queue():
        k = batch_key(x)
        if not k:
            continue
        a = agg.setdefault(k, {"key": k, "total": 0, "pending": 0})
        a["total"] += 1
        if x.get("status") == "pending":
            a["pending"] += 1
    return sorted((a for a in agg.values() if a["pending"] or a["key"] >= since), key=lambda a: a["key"], reverse=True)


def batch_shortage(key):
    """그 출력 묶음의 사입 목록 (차감할 때 줄마다 적어 둔 재고 부족 품목을 모음) → 재고 부족 창 형식"""
    items = {}
    for x in load_queue():
        if batch_key(x) != key:
            continue
        for e in x.get("short_items") or []:
            k = (e["product_no"], e["variant"])
            it = items.setdefault(k, {"product_no": e["product_no"], "variant": e["variant"], "label": e["label"],
                                      "product_name": e["product_name"], "need": 0, "stock": e["stock"], "short": 0,
                                      "orders": [], "time": e.get("time", "")})
            it["need"] += e["n"]
            if not e["covered"]:
                it["short"] += e["n"]
            it["stock"] = e["stock"] if e.get("time", "") <= it["time"] else it["stock"]
            it["orders"].append({"tid": x["tid"], "receipt_id": x["receipt_id"], "name": x.get("name", ""),
                                 "title": x.get("title", "")[:60], "n": e["n"], "covered": e["covered"]})
    return [it for it in items.values() if it["short"] > 0]


def skipped_lines():
    """[이 줄 차감 안 함]으로 뺀 줄 (최근 것부터)"""
    return sorted((x for x in load_queue() if x.get("status") == "skipped"),
                  key=lambda x: x.get("skipped_at") or x.get("time", ""), reverse=True)


STATUS_WORD = {"pending": "차감 대기에 있음", "done": "차감함 (재고 풀어둠으로 정리된 줄 포함)", "partial": "일부만 차감",
               "manual": "직접 차감 목록에 있음", "skipped": "[이 줄 차감 안 함]으로 뺀 줄"}


def existing_lines(receipts):
    """주문들의 상품 줄 중 이미 차감 대기 파일에 있는 것 → {상태: [줄]}"""
    tids = {str(t.get("transaction_id")) for r in receipts for t in (r.get("transactions") or []) if t.get("transaction_id")}
    out = {}
    for x in load_queue():
        if x["tid"] in tids:
            out.setdefault(x.get("status", "pending"), []).append(x)
    return out


# ---------------------------------------------------------------- 차감·되돌리기
def aggregate(rows):
    """같은 품목끼리 합치기 → {(상품번호, 품목코드): {"n": 개수, "label": 이름, "tids": [...]}}"""
    agg = {}
    for r in rows:
        for pno, code, label, n in r["ded"]:
            a = agg.setdefault((pno, code), {"n": 0, "label": label, "tids": set(), "product_name": r.get("product_name", "")})
            a["n"] += n
            a["tids"].add(r["tid"])
    return agg


def current_stock(agg, progress=None):
    """{(상품번호, 품목코드): 현재 재고 또는 None(재고관리 안 함)}"""
    out = {}
    for i, key in enumerate(agg, 1):
        inv = C.get_inventory(*key)
        out[key] = inv["quantity"] if inv.get("use_inventory", "T") == "T" else None
        if progress:
            progress(i, len(agg))
    return out


def opt_values(label):
    """'색상=화이트 / 사이즈=S' → '화이트 / S', '(옵션 없음)' → ''"""
    if not label or label.startswith("("):
        return ""
    return " / ".join(p.split("=", 1)[-1].strip() for p in label.split(" / "))


_UNIT_TAIL = re.compile(r"\s*[(（]\s*\d+(?:[.,]\d+)?\s*[가-힣A-Za-z]{0,5}\s*[)）]\s*$")


def opts_line(line):
    return ", ".join(f"{v['name']}: {v['value']}" for v in line.get("variations", []))


def drop_unit(text):
    """끝의 판매 단위 수량 빼기: '크리스탈/골드(1개)' → '크리스탈/골드', '귀걸이 (1조)' → '귀걸이'"""
    t = str(text or "")
    return _UNIT_TAIL.sub("", t).rstrip() or t


def short_line(it, detail=True):
    """사입 목록 한 줄: 상품명 - 옵션 * 사입수량개 (필요 N / 재고 M)  — 사입수량 = 재고로 못 채운 주문들의 필요 수 합
    상품명·옵션 끝의 판매 단위 '(1개)' 등은 뺌. detail=False면 괄호 (필요/재고) 없이"""
    ov = drop_unit(opt_values(it["label"]))
    line = drop_unit(it["product_name"]) + (f" - {ov}" if ov else "") + f" * {it['short']}개"
    return line + (f" (필요 {it['need']} / 재고 {it['stock']})" if detail else "")


def load_shortage():
    return op.read_json(SHORT_PATH, {"time": "", "items": []})


def cover_orders(needs, stock):
    """있는 재고로 '다 채울 수 있는' 주문을 골라 최대한 많이 채움 (남는 사입이 가장 적게). 같으면 먼저 들어온 주문 우선.
    needs: [필요 수, ...] (주문 순서) → 채우는 주문 번호 집합"""
    stock = max(int(stock or 0), 0)
    if sum(needs) <= stock:
        return set(range(len(needs)))
    best = {0: ()}                                   # 합 → 고른 번호들 (먼저 만든 조합 유지 = 앞 주문 우선)
    for i, n in enumerate(needs):
        if n <= 0 or n > stock:
            continue
        for tot, pick in sorted(best.items(), reverse=True):
            t = tot + n
            if t <= stock and t not in best:
                best[t] = pick + (i,)
    return set(best[max(best)])


def find_shortage(rows, stock):
    """재고가 모자란 품목 → [{product_no, variant, label, product_name, need, stock, short, orders:[...]}]
    재고로 다 채울 수 있는 주문은 채우고(covered), 못 채운 주문은 그 주문의 필요 수 전체를 사입 목록에 (short = 그 합).
    차감은 모든 줄을 그대로 하고 재고는 0 아래로 내려가지 않게 0으로 맞춤.
    stock: {(상품번호, 품목코드): 현재 재고 또는 None(관리 안 함)}. 재고 풀어둠·관리 안 함 품목은 제외. 돌려줌: (목록, 빈 집합)"""
    agg = aggregate(rows)
    short = []
    for key, a in agg.items():
        q = stock.get(key)
        if q is None or q == "?" or is_plenty(q) or q >= a["n"]:
            continue
        orders = []
        for r in rows:
            n = sum(nn for pno, code, _l, nn in r["ded"] if (pno, code) == key)
            if n:
                orders.append({"tid": r["tid"], "receipt_id": r["receipt_id"], "name": r.get("name", ""),
                               "title": r["title"][:60], "n": n})
        cov = cover_orders([o["n"] for o in orders], q)
        for i, o in enumerate(orders):
            o["covered"] = i in cov
        buy = sum(o["n"] for i, o in enumerate(orders) if i not in cov)
        short.append({"product_no": key[0], "variant": key[1], "label": a["label"], "product_name": a["product_name"],
                      "need": a["n"], "stock": q, "short": buy, "orders": orders})
    return short, set()


def vendor_of(pno):
    """카페24 자체 상품코드(업체명). 모르면 ''"""
    if not pno:
        return ""
    try:
        return C.custom_map().get(int(pno), "")
    except (TypeError, ValueError):
        return ""


def row_vendor(r):
    m = r.get("product_no")
    if not m:
        e = load_map().get(r.get("listing_id", ""), {})
        m = e.get("product_no")
    return vendor_of(m)


def annotate_vendor(orders):
    """작업용 주문서에 업체명 표시용: item['_vendor'] (관리 탭에서 켰을 때만)"""
    if not op.load_settings().get("vendor_print"):
        return
    m = load_map()
    for o in orders:
        for it in o["items"]:
            e = m.get(str(it.get("listing_id") or ""), {})
            v = vendor_of(e.get("product_no"))
            if v:
                it["_vendor"] = v


def preview(rows, stock):
    """차감 미리 보기: apply와 같은 규칙으로 계산만 (카페24 재고는 안 바꿈)
    stock: {(상품번호, 품목코드): 지금 재고 / None(관리 안 함)} — 없는 품목은 '재고 못 받음'
    돌려줌: {items: [{product_name, label, vendor, before, n, after, state, note}], counts, nothing, manual, short_lines}"""
    nothing = [r for r in rows if not r["ded"] and not r.get("manual") and not r["why"]]
    manual = [r for r in rows if r.get("manual") and not r["why"]]
    ded_rows = [r for r in rows if r["ded"] and not r["why"]]
    known = {k: v for k, v in stock.items()}
    short, out_tids = find_shortage(ded_rows, known)
    short_keys = {(x["product_no"], x["variant"]) for x in short}
    agg_all = aggregate(ded_rows)
    agg_ok = aggregate([r for r in ded_rows if r["tid"] not in out_tids])
    items = []
    cnt = {"ded": 0, "short": 0, "plenty": 0, "skip": 0, "unknown": 0}
    for key, a in agg_all.items():
        before = stock.get(key, "?")
        n = agg_ok.get(key, {}).get("n", 0)
        it = {"product_name": a["product_name"], "label": a["label"], "vendor": vendor_of(key[0]),
              "before": before, "n": a["n"], "after": "", "state": "", "note": ""}
        if before == "?":
            it.update(state="unknown", note="재고를 미리 받지 못함 — 차감할 때 다시 확인", n=n)
            cnt["unknown"] += 1
        elif before is None:
            it.update(state="skip", note="카페24에서 재고 관리 안 함 — 안 뺌")
            cnt["skip"] += 1
        elif is_plenty(before):
            it.update(state="plenty", note=f"재고 풀어둠 ({plenty_limit():,}개 이상) — 안 뺌")
            cnt["plenty"] += 1
        elif key in short_keys:
            buy = next(x["short"] for x in short if (x["product_no"], x["variant"]) == key)
            it.update(state="short", n=a["n"], after=0,
                      note=f"재고 부족 (필요 {a['n']} / 재고 {before}) → 0으로 맞추고 사입 목록에 {buy}개")
            cnt["short"] += 1
        else:
            it.update(state="ded", n=n, after=before - n,
                      note="" if n == a["n"] else f"재고 부족 줄 {a['n'] - n}개 몫은 빼고 계산")
            cnt["ded"] += 1
        items.append(it)
    order = {"short": 0, "short_row": 1, "unknown": 2, "ded": 3, "plenty": 4, "skip": 5}
    items.sort(key=lambda x: order.get(x["state"], 9))
    return {"items": items, "counts": cnt, "nothing": len(nothing), "manual": len(manual),
            "short_lines": 0, "buy": sum(x["short"] for x in short), "lines": len(rows)}


def apply(rows, practice=True, progress=None):
    """고른 줄 차감. 연습 모드면 계산만. 돌려줌: (결과 목록 [{label, before, after, n, note}], 재고 부족 목록)
    ① 차감할 품목의 카페24 재고를 먼저 다 확인 → ② 재고가 모자란 품목은 사입 목록을 만듦 (재고로 다 채울 수 있는 주문은
    채우고, 못 채운 주문은 그 주문 수량 전체) → ③ 모든 줄을 차감 (모자란 품목은 0으로). 중간에 오류가 나면: 다 끝난 줄은 '차감함', 일부 품목만 끝난 줄은 '일부만'으로 표시하고 기록을 남김"""
    if op.is_test():
        practice = True                 # 테스트 모드에서는 절대 카페24 재고를 바꾸지 않음
    nothing = [r for r in rows if not r["ded"] and not r.get("manual") and not r["why"]]   # 재고 풀어둠·안 뺌으로만 이루어진 줄
    manual_rows = [r for r in rows if r.get("manual") and not r["why"]]                    # 직접 차감할 부분이 있는 줄
    only_manual = {r["tid"] for r in manual_rows if not r["ded"]}
    rows = [r for r in rows if r["ded"] and not r["why"]]
    hid = uuid.uuid4().hex[:12]
    kept = []                           # 재고 풀어둠이라 빼지 않은 품목 (차감 기록에 0개로 남김)
    for r in nothing:
        parts = r.get("fixed") or [(None, None, opts_line(r) or "(옵션)", int(r.get("quantity") or 1))]
        for _p, _c, lab, n in parts:
            kept.append({"product_name": r.get("product_name", ""), "label": lab, "n": n, "receipt_id": r["receipt_id"],
                         "why": "연결표에 재고 풀어둠 고정"})
    if nothing and not practice:
        q = load_queue()
        ids = {r["tid"] for r in nothing}
        for x in q:
            if x["tid"] in ids:
                x["status"], x["hist"] = "done", hid
        save_queue(q)
        op.log.info(f"[재고 차감] 뺄 것 없는 줄(재고 풀어둠) {len(ids)}개 정리")
    results, finished, changes = [], set(), []
    if nothing:
        results.append({"label": "", "product_name": f"주문 줄 {len(nothing)}개", "before": "-", "after": "-", "n": 0,
                        "note": "연결표에 '재고 풀어둠·안 뺌'으로 고정 — 뺄 것 없음" + ("" if practice else ", 차감 대기에서 정리함")})
    # ① 재고 확인
    agg0 = aggregate(rows)
    stock = {}
    for i, key in enumerate(agg0, 1):
        inv = C.get_inventory(*key)
        stock[key] = inv["quantity"] if inv.get("use_inventory", "T") == "T" else None
        if progress:
            progress(i, len(agg0) * 2)
    # ② 부족한 품목이 들어간 줄 빼기
    short, out_tids = find_shortage(rows, stock)
    rows = [r for r in rows if r["tid"] not in out_tids]
    agg = aggregate(rows)
    op.write_json(SHORT_PATH, {"time": now_s(), "practice": practice, "items": short})
    q = load_queue()
    st_by = {o["tid"]: {"need": it["need"], "stock": it["stock"]} for it in short for o in it["orders"]}
    for x in q:
        if x["status"] == "pending":
            if x["tid"] in st_by:
                x["short"] = {**st_by[x["tid"]], "time": now_s()}
            elif x["tid"] in {r["tid"] for r in rows}:
                x.pop("short", None)
    save_queue(q)
    if short:
        op.log.info(f"[재고 차감] 재고 부족 품목 {len(short)}개 → 0으로 맞추고 사입 목록에 "
                    + ", ".join(f"{x['label'] or x['product_name'][:20]} {x['short']}개" for x in short[:10]))
    need = {r["tid"]: {(pno, code) for pno, code, _l, _n in r["ded"]} for r in rows}
    try:
        for i, ((pno, code), a) in enumerate(agg.items(), 1):
            before = stock.get((pno, code))
            if before is None:
                results.append({"label": a["label"], "product_name": a["product_name"], "before": "-",
                                "after": "-", "n": a["n"], "note": "카페24에서 재고 관리 안 함 (건너뜀)"})
                finished.add((pno, code))
                continue
            if is_plenty(before):
                results.append({"label": a["label"], "product_name": a["product_name"], "before": before,
                                "after": before, "n": a["n"],
                                "note": f"재고 풀어둠 ({plenty_limit():,}개 이상) — 안 뺌"})
                kept.append({"product_name": a["product_name"], "label": a["label"], "n": a["n"], "stock": before,
                             "why": f"재고 {before:,}개 (기준 {plenty_limit():,}개 이상)"})
                finished.add((pno, code))
                continue
            after = max(before - a["n"], 0)                 # 재고가 모자라면 0으로 (모자란 주문은 사입 목록에)
            if not practice:
                after = C.set_inventory(pno, code, after)
                changes.append({"product_no": pno, "variant": code, "label": a["label"],
                                "product_name": a["product_name"], "before": before, "after": after,
                                "n": before - after})
            finished.add((pno, code))
            if (pno, code) not in {(x["product_no"], x["variant"]) for x in short}:
                results.append({"label": a["label"], "product_name": a["product_name"], "before": before,
                                "after": after, "n": a["n"], "note": ""})
            if progress:
                progress(len(agg0) + i, len(agg0) + len(agg))
    finally:
        done = {t for t, keys in need.items() if keys <= finished}
        partial = {t for t, keys in need.items() if keys & finished and not keys <= finished}
        if not practice and (changes or kept):
            h = load_history()
            h.append({"id": hid, "time": now_s(), "changes": changes, "tids": sorted(done | partial),
                      "kept": kept, "kept_tids": sorted(r["tid"] for r in nothing),
                      "lines": [{"tid": r["tid"], "receipt_id": r["receipt_id"], "title": r["title"][:60],
                                 "options": ", ".join(f"{v['name']}:{v['value']}" for v in r["variations"]),
                                 "quantity": r["quantity"]} for r in rows + nothing if r["tid"] in done | partial
                                or r in nothing],
                      "undone": ""})
            save_history(h)
        if not practice and finished:
            q = load_queue()
            man = {r["tid"] for r in manual_rows}
            line_short = {}                                  # 줄마다 재고 부족 품목 (출력 묶음별 사입 목록용)
            for it in short:
                for o in it["orders"]:
                    line_short.setdefault(o["tid"], []).append({
                        "product_no": it["product_no"], "variant": it["variant"], "label": it["label"],
                        "product_name": it["product_name"], "n": o["n"], "stock": it["stock"],
                        "covered": bool(o.get("covered")), "time": now_s()})
            for x in q:
                if x["tid"] in done:
                    x["status"], x["hist"] = ("manual" if x["tid"] in man else "done"), hid
                    x.pop("short", None)
                elif x["tid"] in partial:
                    x["status"], x["hist"] = "partial", hid
                if x["tid"] in line_short and x["tid"] in done | partial:
                    x["short_items"] = line_short[x["tid"]]
            save_queue(q)
            op.log.info(f"[재고 차감] 품목 {len(changes)}개 · 주문 줄 {len(done)}개" +
                        (f" · 일부만 {len(partial)}개" if partial else ""))
    for it in short:
        results.append({"label": it["label"], "product_name": it["product_name"], "before": it["stock"],
                        "after": 0, "n": it["need"],
                        "note": f"재고 부족 — 필요 {it['need']} / 재고 {it['stock']} → 0으로 맞춤, 사입 목록 {it['short']}개"})
    # 직접 차감: 자동 부분이 없거나 다 끝난 줄 → '직접 차감 대기'로
    ready = [r for r in manual_rows if r["tid"] in only_manual or r["tid"] in {x["tid"] for x in rows}]
    if not practice and only_manual:
        q = load_queue()
        for x in q:
            if x["tid"] in only_manual and x["status"] == "pending":
                x["status"] = "manual"
        save_queue(q)
    manual = [manual_item(r) for r in ready]
    if manual:
        results.append({"label": "", "product_name": f"직접 차감 {len(manual)}줄",
                        "before": "-", "after": "-", "n": 0,
                        "note": "카페24에 없는 옵션 — 사람이 카페24에서 직접 빼야 함 ('직접 차감 목록' 창)"})
    return results, short, manual


def manual_item(r, m=None):
    """직접 차감 목록 한 줄"""
    if m is None:
        e = {"product_name": r.get("product_name", "")}
    else:
        e = m.get(r["listing_id"], {})
    parts = r.get("manual")
    if parts is None:                                    # 대기열에 저장된 줄 → 다시 계산
        out, _why = resolve(r, m)
        parts = [d for d in out if not d[1]]
    return {"tid": r["tid"], "receipt_id": r["receipt_id"], "name": r.get("name", ""), "title": r["title"],
            "options": ", ".join(f"{v['name']}: {v['value']}" for v in r.get("variations", [])),
            "product_name": r.get("product_name") or e.get("product_name", ""),
            "parts": [(lab, n) for _p, _c, lab, n in parts]}


def manual_waiting():
    """사람이 카페24에서 직접 빼야 하는 줄 (상태 '직접 차감')"""
    m = load_map()
    return [manual_item(x, m) for x in load_queue() if x.get("status") == "manual"]


def manual_line(it):
    """복사용: 카페24 상품명 - Etsy 옵션 * 수량개 (주문번호 손님)"""
    return "\n".join(f"{it['product_name'] or it['title'][:50]} - {lab} * {n}개 (주문 {it['receipt_id']} {it['name']})"
                     for lab, n in it["parts"])


def undo(hist_id, progress=None):
    """차감 되돌리기: 그사이 다른 판매가 있었어도 맞도록 '지금 재고 + 뺀 수'로 되돌림"""
    h = load_history()
    entry = next((x for x in h if x["id"] == hist_id), None)
    if not entry or entry.get("undone"):
        raise op.UserError("되돌릴 수 없는 기록입니다 (이미 되돌렸거나 없음).")
    if not entry.get("changes"):
        raise op.UserError("이 기록은 재고 풀어둠이라 카페24 재고를 빼지 않았습니다 — 되돌릴 것이 없습니다.")
    out = []
    for i, c in enumerate(entry["changes"], 1):
        cur = C.get_inventory(c["product_no"], c["variant"])["quantity"]
        new = C.set_inventory(c["product_no"], c["variant"], cur + int(c["n"]))
        out.append({"label": c["label"], "product_name": c.get("product_name", ""), "before": cur, "after": new})
        if progress:
            progress(i, len(entry["changes"]))
    entry["undone"] = now_s()
    save_history(h)
    set_status(set(entry["tids"]), "pending")
    op.log.info(f"[재고 되돌리기] 기록 {hist_id} · 품목 {len(out)}개")
    return out


op.apply_data_mode()        # 테스트 모드면 차감 대기·기록을 test_data 폴더에서 씀


# ---------------------------------------------------------------- 주문서 작업용: 카페24 재고 표시
def _display_parts(item, m):
    """주문서 상품 한 줄 → 표시할 칸 [(옵션 줄 번호, 종류, 상품번호, 품목코드, 필요 수, 카페24 옵션 값)]
    종류: ded(뺄 품목) / plenty / manual / none(연결 안 됨) / review(확인 필요)
    줄 번호: 0 = Quantity 줄, 1~ = 그 옵션 줄"""
    pairs = item.get("var_pairs") or []
    line = {"listing_id": str(item.get("listing_id") or ""), "title": item.get("title", ""),
            "quantity": item["quantity"], "variations": [{"name": a, "value": b} for a, b in pairs]}
    e = m.get(line["listing_id"])
    if not e or not e.get("product_no"):
        return [(0, "none", None, None, 0, "")]
    if e.get("review") or title_mismatch(e, line["title"]) or code_mismatch(e, line["title"]):
        return [(0, "review", None, None, 0, "")]
    qty = int(item["quantity"])
    ignore = set(e.get("ignore", []))
    vals = [(a, b) for a, b in pairs if a not in ignore and not is_ignorable(a)]
    mode = e.get("mode", "each")

    def row_of(name):
        for i, (a, _b) in enumerate(pairs):
            if a == name:
                return i + 1
        return 0

    def part(row, s):
        if not s:
            return (row, "none", None, None, 0, "")
        if s.get("skip"):
            if s.get("variant"):          # 고정이어도 재고는 받아서, 기준 아래로 내려갔으면 실제 숫자를 적음
                return (row, "fixed", e["product_no"], s["variant"], qty * int(s.get("mult", 1)), opt_values(s.get("label", "")))
            return (row, "plenty", None, None, 0, "")
        if s.get("manual"):
            return (row, "manual", None, None, 0, "")
        return (row, "ded", e["product_no"], s["variant"], qty * int(s.get("mult", 1)), opt_values(s.get("label", "")))
    if mode == "single" or not vals:
        return [part(0, e.get("single"))]
    if mode == "combo":
        return [part(0, e.get("combo", {}).get(_combo_key(vals)))]
    return [part(row_of(a), e.get("each", {}).get(a, {}).get(b)) for a, b in vals]


def annotate_print_stock(orders, progress=None, save_snap=False):
    """주문서를 만들기 전에: 주문 순서대로 카페24 재고를 채워 나가며 상품마다 표시할 내용을 붙임.
    item['_stock'] = [(줄 번호, 상태, 카페24 재고, 모자란 수, 옵션 값)], order['_stock_time'] = 'MM-DD HH:MM'
    상태: ok / short / plenty / manual / none / review / nostock(재고 관리 안 함) / unknown
    차감 대기에 남아 있는 다른 주문 몫은 먼저 빼고 계산. 돌려줌: (상품 수, 걸린 초)"""
    t0 = time.time()
    m = load_map()
    plans = [[_display_parts(it, m) for it in o["items"]] for o in orders]
    keys = {(p[2], p[3]) for o in plans for it in o for p in it if p[1] in ("ded", "fixed")}
    pnos = {k[0] for k in keys}
    stock = C.stock_lookup(pnos, progress, wanted=keys) if pnos else {}
    # 이번에 뽑는 주문이 아닌, 아직 안 뺀 차감 대기 몫
    printing = {str(it.get("tid")) for o in orders for it in o["items"] if it.get("tid")}
    avail = {k: v for k, v in stock.items() if isinstance(v, int)}
    for r in pending_rows():
        if r["tid"] in printing or r["why"]:
            continue
        for pno, code, _l, n in r["ded"]:
            if (pno, code) in avail:
                avail[(pno, code)] -= n
    when = f"{dt.datetime.now():%m-%d %H:%M}"
    low = 0
    snap = load_snap() if save_snap else None
    units = load_units()
    pnames = {}
    for e in m.values():
        if e.get("product_no") and e.get("product_name"):
            pnames.setdefault(e["product_no"], e["product_name"])

    def unit_of(pno):
        """카페24 상품명의 단위 → ('개', 1) / ('조', 2) / ('쌍', 2). 묶음((10개) 등)·세트 등은 None (숫자만)"""
        u = cafe24_unit(pnames.get(pno, ""), units=units)
        if u.get("n") != 1 or not u.get("pcs"):
            return None
        if u["pcs"] == 1:
            return ("개", 1)
        if u["pcs"] == 2:
            return ("쌍", 2) if u["word"] in ("쌍", "pair", "pairs") else ("조", 2)
        return None
    for o, plan in zip(orders, plans):
        o["_stock_time"] = when
        for it, parts in zip(o["items"], plan):
            out = []
            for row, kind, pno, code, need, opt in parts:
                if kind == "fixed":
                    q = stock.get((pno, code), "?")
                    if not isinstance(q, int) or is_plenty(q):
                        out.append((row, "plenty", None, 0, ""))
                        continue
                    low += 1                                # 고정인데 기준 아래 → 실제 숫자로
                    kind = "ded"
                if kind != "ded":
                    out.append((row, kind, None, 0, ""))
                    continue
                q = stock.get((pno, code), "?")
                if q is None:
                    out.append((row, "nostock", None, 0, opt))
                elif q == "?":
                    out.append((row, "unknown", None, 0, opt))
                elif is_plenty(q):
                    out.append((row, "plenty", q, 0, opt))
                else:
                    a = avail.get((pno, code), q)
                    if a >= need:
                        out.append((row, "ok", q, 0, opt, unit_of(pno)))
                        avail[(pno, code)] = a - need
                    else:                                   # 이 주문은 재고로 다 못 채움 → 주문 수량 전체를 사입 (재고는 뒤 주문 몫으로 남김)
                        out.append((row, "short", q, need, opt, unit_of(pno)))
            it["_stock"] = out
            if snap is not None and it.get("tid"):
                codes = [p[3] for p in parts]
                snap[str(it["tid"])] = {"time": when, "parts": {c: [st, q, sh] for c, (_r, st, q, sh, _o, *_u) in zip(codes, out)
                                                                if c and st in ("ok", "short")}}
    if snap is not None:
        keep = sorted(snap.items(), key=lambda kv: kv[1].get("time", ""))[-3000:]
        op.write_json(SNAP_PATH, dict(keep))
    secs = time.time() - t0
    op.log.info(f"[주문서 재고] 주문 {len(orders)}건 · 카페24 상품 {len(pnos)}종 · {secs:.1f}초"
                + (f" · 재고 풀어둠 고정인데 재고 줄어든 품목 {low}개" if low else ""))
    return len(pnos), secs, low
