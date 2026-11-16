# -*- coding: utf-8 -*-
"""
Etsy 주문서 자동 출력 프로그램 (orderprint)

사용법 (명령 프롬프트에서):
  python orderprint.py login       처음 1회: Etsy 상점 접근 허용
  python orderprint.py             새 주문(결제완료·미발송)을 가져와 PDF 생성 후 인쇄
  python orderprint.py --no-print  PDF만 만들고 인쇄는 하지 않음
  python orderprint.py --all       이미 출력한 주문도 다시 포함
  python orderprint.py --test      Etsy 연결 없이 샘플 주문으로 양식 미리보기
"""

import base64
import configparser
import datetime as dt
import hashlib
import html
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------- 기본 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
PRINTED_PATH = os.path.join(BASE_DIR, "printed_orders.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_PATH = os.path.join(BASE_DIR, "orderprint.log")

API_BASE = "https://api.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
CONNECT_URL = "https://www.etsy.com/oauth/connect"
REDIRECT_PORT = 3003
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/oauth/redirect"
SCOPES = "transactions_r shops_r listings_r"  # 주문·상점 정보·상품 목록 읽기 (모두 읽기 전용)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")] + ([logging.StreamHandler(sys.stdout)] if sys.stdout else []),
)
log = logging.getLogger("orderprint")


class UserError(Exception):
    """사용자에게 그대로 보여줄 오류"""


def load_config():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str  # 메모의 SKU 대소문자 유지
    if not os.path.exists(CONFIG_PATH):
        raise UserError("config.ini 파일이 없습니다. 프로그램 폴더에 config.ini가 있는지 확인하세요.")
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg


def api_credentials(cfg):
    key = cfg.get("etsy", "keystring", fallback="").strip()
    secret = cfg.get("etsy", "shared_secret", fallback="").strip()
    if not key or not secret:
        raise UserError("config.ini에 keystring과 shared_secret을 입력해 주세요.")
    return key, secret


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 1. 로그인 (OAuth PKCE)
def login(cfg):
    key, secret = api_credentials(cfg)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/oauth/redirect":
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            result["code"] = q.get("code", [None])[0]
            result["state"] = q.get("state", [None])[0]
            result["error"] = q.get("error_description", q.get("error", [None]))[0]
            ok = result["code"] and result["state"] == state
            msg = ("연결 완료! 이 창을 닫고 프로그램 화면으로 돌아가세요." if ok
                   else f"연결 실패: {result['error'] or '알 수 없는 오류'}")
            body = f"<html><meta charset='utf-8'><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    except OSError:
        raise UserError(f"{REDIRECT_PORT}번 포트를 사용할 수 없습니다. 프로그램이 이미 실행 중인지 확인하세요.")

    params = {
        "response_type": "code",
        "client_id": key,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = CONNECT_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    log.info("브라우저에서 Etsy 로그인 후 '허용(Grant access)'을 눌러 주세요.")
    log.info("브라우저가 열리지 않으면 아래 주소를 복사해서 열어주세요:\n%s", url)
    webbrowser.open(url)

    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(timeout=300)
    server.server_close()

    if not result.get("code"):
        raise UserError("5분 안에 허용 응답을 받지 못했습니다. 다시 시도해 주세요. "
                        f"(Etsy 앱 설정에 콜백 URL {REDIRECT_URI} 이 등록돼 있어야 합니다)")
    if result.get("state") != state:
        raise UserError("보안 확인(state) 값이 일치하지 않습니다. 다시 시도해 주세요.")

    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": key,
        "redirect_uri": REDIRECT_URI,
        "code": result["code"],
        "code_verifier": verifier,
    }, headers={"x-api-key": f"{key}:{secret}"}, timeout=30)
    if resp.status_code != 200:
        raise UserError(f"토큰 발급 실패 ({resp.status_code}): {resp.text}")
    token = save_token(resp.json())

    me = EtsyClient(cfg, token).get("/users/me")
    token["shop_id"] = me.get("shop_id")
    token["user_id"] = me.get("user_id")
    token["scopes"] = SCOPES
    write_json(TOKEN_PATH, token)
    if not token["shop_id"]:
        raise UserError("로그인은 됐지만 이 계정에 연결된 상점을 찾지 못했습니다.")
    log.info("연결 완료! 상점 ID: %s", token["shop_id"])


def save_token(data, old=None):
    token = dict(old or {})
    token.update({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", token.get("refresh_token")),
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 60,
    })
    write_json(TOKEN_PATH, token)
    return token


# ---------------------------------------------------------------- 2. Etsy API 클라이언트
class EtsyClient:
    def __init__(self, cfg, token=None):
        self.key, self.secret = api_credentials(cfg)
        self.token = token or read_json(TOKEN_PATH, None)
        if not self.token:
            raise UserError("아직 Etsy와 연결되지 않았습니다. 먼저 '2_login.bat'을 실행하세요.")

    def _refresh(self):
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": self.key,
            "refresh_token": self.token["refresh_token"],
        }, headers={"x-api-key": f"{self.key}:{self.secret}"}, timeout=30)
        if resp.status_code != 200:
            raise UserError("Etsy 연결이 만료되었습니다(보통 90일 미사용 시). "
                            "'2_login.bat'을 다시 실행해 주세요.")
        self.token = save_token(resp.json(), self.token)

    def ensure_fresh(self):
        if time.time() > self.token.get("expires_at", 0):
            self._refresh()

    def get(self, path, params=None):
        self.ensure_fresh()
        for attempt in range(5):
            resp = requests.get(API_BASE + path, params=params, timeout=30, headers={
                "x-api-key": f"{self.key}:{self.secret}",
                "Authorization": f"Bearer {self.token['access_token']}",
            })
            if resp.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise UserError(f"Etsy API 오류 ({resp.status_code}) {path}: {resp.text[:300]}")
            return resp.json()
        raise UserError("Etsy 서버가 응답하지 않습니다. 잠시 후 다시 시도하세요.")

    def unshipped_receipts(self):
        shop_id = self.token.get("shop_id")
        if not shop_id:
            raise UserError("상점 ID가 없습니다. '2_login.bat'을 다시 실행하세요.")
        out, offset = [], 0
        while True:
            data = self.get(f"/shops/{shop_id}/receipts", {
                "was_paid": "true", "was_shipped": "false", "was_canceled": "false",
                "sort_on": "created", "sort_order": "asc", "limit": 100, "offset": offset,
            })
            batch = data.get("results", [])
            out.extend(batch)
            if len(batch) < 100:
                return out
            offset += 100
            time.sleep(0.2)

    def shop_name(self):
        if not self.token.get("shop_name"):
            try:
                shop = self.get(f"/shops/{self.token['shop_id']}")
                self.token["shop_name"] = shop.get("shop_name") or ""
                write_json(TOKEN_PATH, self.token)
            except UserError as e:
                log.info("상점 이름을 가져오지 못했습니다: %s", e)
                return ""
        return self.token["shop_name"]

    def listing_image(self, listing_id, image_id):
        """상품 사진(75x75)을 images 폴더에 받아두고 경로를 돌려줌. 실패하면 None"""
        os.makedirs(IMAGE_DIR, exist_ok=True)
        if image_id:
            path = os.path.join(IMAGE_DIR, f"{image_id}.jpg")
            if os.path.exists(path):
                try:
                    os.utime(path)
                except OSError:
                    pass
                return path
        try:
            if image_id:
                info = self.get(f"/listings/{listing_id}/images/{image_id}")
            else:
                res = self.get(f"/listings/{listing_id}/images").get("results", [])
                if not res:
                    return None
                info = res[0]
                image_id = info.get("listing_image_id")
            url = info.get("url_75x75") or info.get("url_170x135") or info.get("url_570xN")
            path = os.path.join(IMAGE_DIR, f"{image_id}.jpg")
            if os.path.exists(path):
                return path
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception as e:  # 삭제된 상품 등
            log.info("사진을 받지 못했습니다 (상품 %s): %s", listing_id, e)
            return None


IMAGE_DIR = os.path.join(BASE_DIR, "images")


# ---------------------------------------------------------------- 3. 주문 데이터 정리
def ts_to_date(ts):
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(int(ts))
    return f"{d:%b} {d.day}, {d.year}"          # 예: Oct 22, 2026


CURRENCY = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "KRW": "₩", "CAD": "CA$", "AUD": "A$"}


def money(m):
    if not m or m.get("amount") is None:
        return ""
    amt = m["amount"] / (m.get("divisor") or 100)
    code = m.get("currency_code", "")
    sym = CURRENCY.get(code)
    num = f"{amt:,.0f}" if code in ("JPY", "KRW") else f"{amt:,.2f}"
    return f"{sym}{num}" if sym else f"{num} {code}"


def clean(s):
    return html.unescape(str(s or "")).strip()


def normalize(receipt):
    """Etsy 영수증 → 주문서에 필요한 값만 정리"""
    items, ship_by, methods = [], [], []
    for t in receipt.get("transactions", []) or []:
        variations, pairs = [], []
        for v in t.get("variations", []) or []:
            name, value = clean(v.get("formatted_name")), clean(v.get("formatted_value"))
            if value:
                variations.append(f"{name}: {value}" if name else value)
                pairs.append((name, value))
        if t.get("expected_ship_date"):
            ship_by.append(int(t["expected_ship_date"]))
        m = clean(t.get("shipping_upgrade")) or clean(t.get("shipping_method"))
        if m and m not in methods:
            methods.append(m)
        items.append({
            "title": clean(t.get("title")),
            "quantity": int(t.get("quantity") or 1),
            "sku": clean(t.get("sku")),
            "variations": variations,
            "listing_id": t.get("listing_id"),
            "listing_image_id": t.get("listing_image_id"),
            "tid": t.get("transaction_id"),
            "var_pairs": pairs,
            "image_path": t.get("_image_path"),
        })

    addr = receipt.get("formatted_address")
    if addr:
        address_lines = [clean(l) for l in addr.splitlines() if l.strip()]
    else:
        city_line = " ".join(clean(x) for x in [receipt.get("city"), receipt.get("state"), receipt.get("zip")] if x)
        address_lines = [clean(x) for x in [receipt.get("name"), receipt.get("first_line"),
                                            receipt.get("second_line"), city_line, receipt.get("country_iso")] if x]
    iso = receipt.get("country_iso") or ""
    country_name = address_lines[-1] if addr and address_lines else iso

    pm = (receipt.get("payment_method") or "").lower()
    payment = "Paid via PayPal" if pm == "paypal" else "Paid via Etsy Payments"

    return {
        "receipt_id": receipt.get("receipt_id"),
        "buyer_name": clean(receipt.get("name")),
        "order_date": ts_to_date(receipt.get("created_timestamp") or receipt.get("create_timestamp")),
        "ship_by": ts_to_date(min(ship_by)) if ship_by else "",
        "address_lines": address_lines,
        "country": iso,
        "country_name": country_name,
        "payment": payment,
        "shipping_method": ", ".join(methods),
        "shipping_cost": money(receipt.get("total_shipping_cost")),
        "shipping_free": (receipt.get("total_shipping_cost") or {}).get("amount", 1) == 0,
        "grandtotal": money(receipt.get("grandtotal")),
        "buyer_message": clean(receipt.get("message_from_buyer")),
        "is_gift": bool(receipt.get("is_gift")),
        "gift_message": clean(receipt.get("gift_message")),
        "items": items,
    }


def attach_images(client, receipts, progress=None):
    """상품 사진 준비. 같은 사진은 한 번만, 4개씩 동시에 받아서 빠르게 처리"""
    from concurrent.futures import ThreadPoolExecutor
    keys = []
    for r in receipts:
        for t in r.get("transactions", []) or []:
            k = (t.get("listing_id"), t.get("listing_image_id"))
            if k[0] and k not in keys:
                keys.append(k)
    client.ensure_fresh()
    results, done = {}, 0
    if progress:
        progress(0, len(keys))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(client.listing_image, lid, iid): (lid, iid) for lid, iid in keys}
        for f in futures:
            results[futures[f]] = f.result()
            done += 1
            if progress:
                progress(done, len(keys))
    for r in receipts:
        for t in r.get("transactions", []) or []:
            t["_image_path"] = results.get((t.get("listing_id"), t.get("listing_image_id")))


# ---------------------------------------------------------------- 4. PDF 주문서 (A4 가로, 한 장에 2부)
# 역할별 글꼴 후보 (Windows 기본 글꼴 → 다른 환경용 대체 글꼴). (경로, ttc 안의 번호)
WIN = r"C:\Windows\Fonts"
FONT_CANDIDATES = {
    "Serif": [(os.path.join(WIN, "cambriab.ttf"), 0), ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0)],
    "Sans":  [(os.path.join(WIN, "arial.ttf"), 0), ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 0)],
    "SansB": [(os.path.join(WIN, "arialbd.ttf"), 0), ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0)],
    "Kr":    [(os.path.join(WIN, "malgun.ttf"), 0), (os.path.join(BASE_DIR, "fonts", "NanumGothic.ttf"), 0)],
    "KrB":   [(os.path.join(WIN, "malgunbd.ttf"), 0), (os.path.join(BASE_DIR, "fonts", "NanumGothicBold.ttf"), 0)],
}
# 맑은 고딕에 없는 글자를 찾을 대체 글꼴 (필요할 때만 불러옴): 일본어 → 중국어 → 기호 → 이모지 → 태국어
FALLBACKS = [
    ("FbJa", os.path.join(WIN, "YuGothM.ttc"), 0), ("FbJa2", os.path.join(WIN, "msgothic.ttc"), 0),
    ("FbZh", os.path.join(WIN, "msyh.ttc"), 0), ("FbSym", os.path.join(WIN, "seguisym.ttf"), 0),
    ("FbEmoji", os.path.join(WIN, "seguiemj.ttf"), 0), ("FbThai", os.path.join(WIN, "LeelawUI.ttf"), 0),
    ("FbDejavu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]
FONTS = {"Serif": "Times-Bold", "Sans": "Helvetica", "SansB": "Helvetica-Bold", "Kr": "Helvetica", "KrB": "Helvetica-Bold"}
_cmaps = {}          # 글꼴 이름 → 들어 있는 글자 목록
_fb_loaded = []      # 불러온 대체 글꼴 이름
_fb_tried = False
_fonts_ready = False


def _register(name, path, idx=0):
    try:
        f = TTFont(name, path, subfontIndex=idx)
        pdfmetrics.registerFont(f)
        _cmaps[name] = set(f.face.charToGlyph.keys())
        return True
    except Exception as e:
        log.info("글꼴을 불러오지 못했습니다 (%s): %s", os.path.basename(path), e)
        return False


def setup_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    for role, cands in FONT_CANDIDATES.items():
        for path, idx in cands:
            if os.path.exists(path) and _register(role, path, idx):
                FONTS[role] = role
                break
    if FONTS["Kr"] == "Helvetica":
        log.info("한글 글꼴(맑은 고딕)을 찾지 못했습니다. 한글이 깨질 수 있어요.")
    _fonts_ready = True


def _load_fallbacks():
    global _fb_tried
    if _fb_tried:
        return
    _fb_tried = True
    for name, path, idx in FALLBACKS:
        if os.path.exists(path) and _register(name, path, idx):
            _fb_loaded.append(name)


def _has(font, ch):
    cm = _cmaps.get(font)
    return cm is not None and ord(ch) in cm


IGNORABLE = {0x200D, 0xFE0F, 0xFE0E, 0x200B}   # 이모지 연결 문자 등 (그리지 않음)


def font_for_char(ch, bold=False):
    """글자 하나를 그릴 글꼴. 없으면 None"""
    latin = FONTS["SansB" if bold else "Sans"]
    kr = FONTS["KrB" if bold else "Kr"]
    if ch in " \t" or ord(ch) < 0x80:
        return latin
    for f in (latin, kr):
        if _has(f, ch):
            return f
    _load_fallbacks()
    for f in _fb_loaded:
        if _has(f, ch):
            return f
    return None


def runs(s, bold=False):
    """문자열을 (글꼴, 조각) 묶음으로 나눔. 그릴 수 없는 글자는 '?'로"""
    out = []
    for ch in str(s):
        if ord(ch) in IGNORABLE:
            continue
        f = font_for_char(ch, bold)
        if f is None:
            f, ch = FONTS["SansB" if bold else "Sans"], "?"
        if out and out[-1][0] == f:
            out[-1][1] += ch
        else:
            out.append([f, ch])
    return out


def missing_chars(s):
    """어떤 글꼴에도 없는 글자 목록"""
    return [ch for ch in str(s) if ord(ch) not in IGNORABLE and font_for_char(ch) is None]


def measure(s, size, bold=False):
    return sum(pdfmetrics.stringWidth(t, f, size) for f, t in runs(s, bold))


PAGE_W, PAGE_H = landscape(A4)
HALF = PAGE_W / 2
# 한쪽(A5) 안에서의 위치 — Etsy 기본 주문서 비율에 맞춤 (단위 pt, 위에서부터 거리)
LM = 14            # 왼쪽 여백
LCW = 92           # 왼쪽 정보 칸 너비
IX = 114           # 상품 칸 시작
IMG = 31           # 사진 크기
TX = IX + 40       # 상품 글자 시작
TW = 186           # 상품 글자 폭
RX = HALF - 14     # 오른쪽 끝
FS = 7             # 본문 글자 크기
LINE = 9           # 본문 줄 간격
VLINE = 13         # 수량·옵션 줄 간격
BOTTOM = 545       # 상품이 내려올 수 있는 한계
LAST_BOTTOM = 512  # 마지막 페이지는 꼬리말 자리를 남김
FOOT_RULE = 522

INK = HexColor("#222222")
MUTED = HexColor("#777777")
RULE = HexColor("#222222")
SOFT = HexColor("#f1efe9")


def Y(top):
    return PAGE_H - top


def _wrap_para(para, width, size, bold):
    """단어 단위로 줄바꿈. 띄어쓰기 없는 긴 글(일본어 등)은 글자 단위로 자름"""
    lines, cur = [], ""
    for word in para.split(" "):
        cand = word if not cur else cur + " " + word
        if measure(cand, size, bold) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while measure(word, size, bold) > width and len(word) > 1:
            n = len(word)
            while n > 1 and measure(word[:n], size, bold) > width:
                n -= 1
            lines.append(word[:n])
            word = word[n:]
        cur = word
    lines.append(cur)
    return lines


def wrap(text, width, size=FS, bold=False):
    out = []
    for para in str(text).splitlines() or [""]:
        out.extend(_wrap_para(para, width, size, bold))
    return out


def text(c, x, top, s, size=FS, bold=False, color=INK, right=False):
    c.setFillColor(color)
    if right:
        x -= measure(s, size, bold)
    for f, t in runs(s, bold):
        c.setFont(f, size)
        c.drawString(x, Y(top), t)
        x += pdfmetrics.stringWidth(t, f, size)


def item_layout(item):
    title = wrap(item["title"], TW, bold=True)
    rows = [f"Quantity: {item['quantity']}"] + item["variations"]
    rows_wrapped = [wrap(r, TW) for r in rows]
    n_rows = sum(len(r) for r in rows_wrapped)
    text_h = 7 + (len(title) - 1) * LINE + n_rows * VLINE      # 사진 위 → 마지막 글자 기준선
    h = 9 + max(text_h + 11, IMG + 9)                           # 위 구분선 → 아래 구분선
    return title, rows_wrapped, h


def paginate(items, with_footer=False):
    """상품 목록을 페이지별로 나눔. 첫 페이지는 108pt, 다음 페이지는 28pt부터 시작
    상품이 모두 1쪽에 들어갔는데 꼬리말(감사 문구)만 넘치면 → 꼬리말을 생략하고 1쪽으로 끝냄 (1장 주문으로).
    상품이 2쪽 이상이면 예전처럼 꼬리말 자리가 없을 때 꼬리말만 있는 쪽을 추가.
    with_footer=True면 (페이지들, 꼬리말 그림 여부)"""
    pages, cur, top = [], [], 108
    for it in items:
        h = item_layout(it)[2]
        if cur and top + h > BOTTOM:
            pages.append(cur)
            cur, top = [], 28
        cur.append(it)
        top += h
    pages.append(cur)
    footer = True
    if top > LAST_BOTTOM:      # 꼬리말 들어갈 자리가 없음
        if len(pages) == 1:
            footer = False     # 상품은 1쪽에 다 들어감 → 꼬리말만 생략 (꼬리말만 있는 2쪽을 만들지 않음)
        else:
            pages.append([])   # Etsy처럼 꼬리말만 있는 페이지 추가
    return (pages, footer) if with_footer else pages


def order_sheets(order, long_layout=None):
    """이 주문이 인쇄될 종이 장수 (build_pdf와 같은 계산)"""
    if long_layout is None:
        long_layout = load_settings().get("long_order_layout", "continuous")
    n = len(paginate(order["items"]))
    if n == 1 or long_layout != "continuous":
        return n
    return (n + 1) // 2 * 2


def short_first(orders, long_layout=None):
    """1장 주문 먼저, 2장 이상 주문은 뒤에 모아서 (각각 원래 순서 유지) → 새 순번 목록"""
    if long_layout is None:
        long_layout = load_settings().get("long_order_layout", "continuous")
    n = [order_sheets(o, long_layout) for o in orders]
    return [i for i in range(len(orders)) if n[i] == 1] + [i for i in range(len(orders)) if n[i] != 1]


def memo_lines(order, cfg):
    """config.ini [memo] 에 적은 문구 중 이 주문에 해당하는 것"""
    if cfg is None or not cfg.has_section("memo"):
        return []
    out = []
    skus = {i["sku"] for i in order["items"] if i["sku"]}
    for key, val in cfg.items("memo"):
        val = val.replace("|", "\n").strip()
        if not val:
            continue
        k = key.strip()
        if (k.lower() == "all"
                or (k.lower().startswith("country.") and k.split(".", 1)[1].upper() == order["country"].upper())
                or (k.lower().startswith("sku.") and k.split(".", 1)[1] in skus)):
            out.append(val)
    return out


def draw_label_value(c, x, top, label, lines, width=LCW, label_bold=True, value_size=FS, value_bold=False):
    text(c, x, top, label, bold=label_bold)
    top += LINE
    for ln in lines:
        for part in wrap(ln, width, value_size, value_bold):
            if top > LAST_BOTTOM:
                return top
            text(c, x, top, part, size=value_size, bold=value_bold)
            top += max(LINE, value_size + 2)
    return top


HIGHLIGHT = HexColor("#FFE94D")
STOCK_FS = 7.6
STOCK_GREY = HexColor("#555555")


def unit_count(n, unit=None):
    """카페24 개수 → 단위 붙인 글자. unit=(단위 이름, 낱개 수): ('개',1) → '6개' / ('조',2) → '3조 6개' / None → '6'"""
    if not unit:
        return f"{n}"
    word, per = unit
    if per == 1:
        return f"{n}{word}"
    return f"{n}{word} {n * per}개"


def draw_stock_note(c, x_right, y, st, q, short, opt="", unit=None):
    """작업용: 상품 줄 오른쪽 끝에 카페24 재고 (카페24 개수 기준, 단위 표시). 모자라면 [미 N] 테두리 상자"""
    tail = f"  {opt}" if opt else ""
    if st == "ok":
        text(c, x_right, y, f"재고 {unit_count(q, unit)}{tail}", size=STOCK_FS, color=STOCK_GREY, right=True)
    elif st == "short":
        tw = 0
        if tail:
            text(c, x_right, y, tail, size=STOCK_FS, color=STOCK_GREY, right=True)
            tw = measure(tail, STOCK_FS)
        lab = f"미 {unit_count(short, unit)}" if load_settings().get("print_short_count", True) else "미"
        w = measure(lab, 9, True) + 7
        x1 = x_right - tw - w
        c.setStrokeColor(INK)
        c.setLineWidth(1.1)
        c.roundRect(x1, Y(y + 2.6), w, 11, 2, stroke=1, fill=0)
        text(c, x1 + 3.5, y + 0.3, lab, size=9, bold=True)
    else:
        txt = {"plenty": "재고 풀어둠", "manual": "직접 차감", "none": "카페24 연결 안 됨", "review": "확인 필요",
               "nostock": "재고 관리 안 함", "unknown": "재고 확인 못 함"}.get(st, "")
        strong = st in ("manual", "review")
        text(c, x_right, y, txt + (tail if st in ("plenty", "nostock", "unknown") else ""), size=STOCK_FS,
             bold=strong, color=INK if strong else STOCK_GREY, right=True)


def draw_copy(c, x0, order, page_items, page_no, n_pages, shop, cfg, work, footer=True):
    c.saveState()
    c.translate(x0, 0)
    footer_title = cfg.get("layout", "footer_title", fallback="Thank you for your order!") if cfg else "Thank you!"
    footer_text = cfg.get("layout", "footer_text", fallback="") if cfg else ""

    if page_no == 0:
        # 머리글
        c.setFillColor(INK)
        c.setFont(FONTS["Serif"], 12.5)
        c.drawString(LM, Y(51), f"Order #{order['receipt_id']}")
        if work:
            text(c, RX, 51, "작업용", size=7, bold=True, color=MUTED, right=True)

        # 왼쪽 정보 칸
        top = 100
        top = draw_label_value(c, LM, top, "Ship to", order["address_lines"] or ["-"]) + 9
        if order["ship_by"]:
            top = draw_label_value(c, LM, top, "Scheduled to ship by", [order["ship_by"]]) + 9
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(LM, Y(top - 2), LM + LCW - 4, Y(top - 2))
        top += 13
        if shop:
            top = draw_label_value(c, LM, top, "Shop", [shop]) + 9
        top = draw_label_value(c, LM, top, "Order date", [order["order_date"]]) + 9
        top = draw_label_value(c, LM, top, "Payment method", [order["payment"]]) + 9
        if order["is_gift"] or order["gift_message"]:
            top = draw_label_value(c, LM, top, "Gift message", [order["gift_message"] or "(no message)"]) + 9

        # 작업용 사본에만 들어가는 추가 정보
        if work:
            c.line(LM, Y(top - 2), LM + LCW - 4, Y(top - 2))
            top += 13
            top = draw_label_value(c, LM, top, "배송 국가", [f"{order['country_name']} ({order['country']})"],
                                   value_size=9, value_bold=True) + 7
            if order["shipping_method"]:
                top = draw_label_value(c, LM, top, "배송 방법", [order["shipping_method"]]) + 7
            cost = "무료배송" if order["shipping_free"] else order["shipping_cost"]
            if cost:
                top = draw_label_value(c, LM, top, "배송비", [cost]) + 7
            if order["grandtotal"]:
                top = draw_label_value(c, LM, top, "주문 금액", [order["grandtotal"]]) + 7
            if order["buyer_message"]:
                top = draw_label_value(c, LM, top, "구매자 메시지", [order["buyer_message"]]) + 7
            memos = memo_lines(order, cfg)
            if memos:
                top = draw_label_value(c, LM, top, "메모", memos) + 7
            if order.get("_missing"):
                top = draw_label_value(c, LM, top, "※ 확인 필요",
                                       ["인쇄할 수 없는 글자가 있어 ?로 표시했습니다. Etsy에서 원문을 확인하세요."]) + 7

        # 상품 수
        n = sum(i["quantity"] for i in order["items"])
        text(c, IX, 100, f"{n} item{'s' if n != 1 else ''}", bold=True)
        if work and order.get("_stock_time"):
            text(c, RX, 100, f"카페24 재고 · {order['_stock_time']} 출력 시점", size=6.5, color=MUTED, right=True)
        elif work and order.get("_stock_err"):
            text(c, RX, 100, f"카페24 재고 확인 못 함 ({order['_stock_err'][:30]})", size=6.5, bold=True, right=True)
        top = 108
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(IX, Y(top), RX, Y(top))
    else:
        top = 28

    # 상품 목록
    for it in page_items:
        title, rows, h = item_layout(it)
        img_top = top + 9
        if it.get("image_path") and os.path.exists(it["image_path"]):
            try:
                c.drawImage(it["image_path"], IX, Y(img_top + IMG), IMG, IMG,
                            preserveAspectRatio=True, anchor="c", mask="auto")
            except Exception:
                c.setFillColor(SOFT)
                c.rect(IX, Y(img_top + IMG), IMG, IMG, stroke=0, fill=1)
        else:
            c.setFillColor(SOFT)
            c.rect(IX, Y(img_top + IMG), IMG, IMG, stroke=0, fill=1)
        yy = img_top + 7
        for ln in title:
            text(c, TX, yy, ln, bold=True)
            yy += LINE
        yy += VLINE - LINE
        row_y = []
        for i, parts in enumerate(rows):
            strong = work and i == 0 and it["quantity"] > 1      # 작업용: 수량 2개 이상 굵게 + 노란 형광펜
            row_y.append(yy)
            for p in parts:
                if strong:
                    c.setFillColor(HIGHLIGHT)
                    c.rect(TX - 2, Y(yy + 2.5), measure(p, FS, True) + 4, FS + 3.2, stroke=0, fill=1)
                text(c, TX, yy, p, bold=strong)
                yy += VLINE
        if work and it.get("_vendor") and row_y:          # 업체명: 재고 표기 위, 2pt 작게 회색
            vrow = min((x[0] for x in it.get("_stock") or [] if x[0] < len(row_y)), default=0)
            text(c, RX, row_y[vrow] - VLINE + 2, it["_vendor"][:30], size=STOCK_FS - 2, color=STOCK_GREY, right=True)
        if work and it.get("_stock"):
            for row, st, q, short, opt, *rest in it["_stock"]:
                if row < len(row_y):
                    draw_stock_note(c, RX, row_y[row], st, q, short, opt, rest[0] if rest else None)
        top += h
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(IX, Y(top), RX, Y(top))

    # 꼬리말 (마지막 페이지). 상품이 1쪽에 다 들어갔는데 꼬리말 자리만 모자라면 생략(footer=False)
    if page_no == n_pages - 1 and footer:
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(LM, Y(FOOT_RULE), RX, Y(FOOT_RULE))
        c.setFillColor(INK)
        c.setFont(FONTS["Serif"], 10)
        name = shop or ""
        c.drawString(LM, Y(545), name[:22])
        fx = IX
        text(c, fx, 540, footer_title, bold=True)
        yy = 549
        for ln in wrap(footer_text.replace("|", "\n"), RX - fx):
            text(c, fx, yy, ln)
            yy += LINE
    elif n_pages > 1 and page_no < n_pages - 1:
        text(c, RX, PAGE_H - 20, f"{page_no + 1}/{n_pages}", size=6, color=MUTED, right=True)
    c.restoreState()


def order_texts(o):
    yield from o["address_lines"]
    yield from (o["buyer_message"], o["gift_message"], o["shipping_method"])
    for it in o["items"]:
        yield it["title"]
        yield from it["variations"]


def build_pdf(orders, cfg, path, shop="", long_layout=None):
    """주문서 PDF를 만들고, 주문별 장수 목록을 돌려줌.
    한 쪽(반 장)에 다 들어가는 주문: 한 장 양쪽에 같은 주문서 (원본 | 작업용)
    2쪽 이상인 긴 주문: long_layout="continuous"(이어서 2부)면 한 장 안에서 쪽이 이어지고
        원본 1장 → 작업용 1장 → 원본 2장 → 작업용 2장 … 처럼 번갈아 나옴.
        "side"(같은 쪽 나란히)면 예전처럼 장마다 같은 쪽을 양쪽에."""
    setup_fonts()
    side = (cfg.get("layout", "extras_side", fallback="right") if cfg else "right").strip().lower()
    if long_layout is None:
        long_layout = load_settings().get("long_order_layout", "continuous")
    c = canvas.Canvas(path, pagesize=landscape(A4))
    c.setTitle(f"Etsy orders {dt.datetime.now():%Y-%m-%d %H:%M}")
    sheets = []
    for o in orders:
        o["_missing"] = any(missing_chars(t) for t in order_texts(o) if t)
        if o["_missing"]:
            log.info("주문 %s: 인쇄할 수 없는 글자가 있어 ?로 표시", o["receipt_id"])
        pages, footer = paginate(o["items"], with_footer=True)
        n = len(pages)
        if n == 1 or long_layout != "continuous":
            for p, items in enumerate(pages):
                for x0, is_right in ((0, False), (HALF, True)):
                    work = (side == "right") == is_right if side in ("left", "right") else False
                    draw_copy(c, x0, o, items, p, n, shop, cfg, work, footer)
                c.showPage()
            sheets.append(n)
            continue
        # 이어서 2부: 원본(고객용)을 모두 뽑은 뒤 작업용을 모두 (원본 1·2쪽 → 원본 3·4쪽 → 작업용 1·2쪽 → 작업용 3·4쪽)
        count = 0
        for work in (False, True):
            for s_i in range(0, n, 2):
                for x0, p in ((0, s_i), (HALF, s_i + 1)):
                    if p < n:
                        draw_copy(c, x0, o, pages[p], p, n, shop, cfg, work)
                        c.saveState()
                        c.translate(x0, 0)
                        text(c, RX, 18, f"{'작업용' if work else '원본'} · {p + 1}/{n}쪽", size=6.5,
                             bold=work, color=MUTED, right=True)
                        c.restoreState()
                c.showPage()
                count += 1
        sheets.append(count)
    c.save()
    return sheets


# ---------------------------------------------------------------- 5. 인쇄
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
SETTINGS_DEFAULT = {"printer": "", "batch_orders": 20, "long_order_layout": "continuous", "pdf_keep_days": 14, "image_keep_days": 60,
                    "label_keep_days": 30, "label_print_dialog": True,
                    "test_mode": False, "test_keep_days": 14,
                    "label_cal": {"offset_x": 0.0, "offset_y": 0.0, "scale_x": 0.0, "scale_y": 0.0}}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def load_settings():
    s = dict(SETTINGS_DEFAULT)
    s.update(read_json(SETTINGS_PATH, {}))
    return s


def save_settings(s):
    write_json(SETTINGS_PATH, s)


def printer_name(cfg):
    """관리 탭에서 고른 프린터 → 없으면 config.ini → 없으면 윈도우 기본 프린터('')"""
    p = load_settings().get("printer") or ""
    if not p and cfg is not None:
        p = cfg.get("print", "printer_name", fallback="").strip()
    return p


def find_sumatra(cfg):
    custom = cfg.get("print", "sumatra_path", fallback="").strip() if cfg is not None else ""
    candidates = [custom,
                  os.path.join(BASE_DIR, "SumatraPDF.exe"),
                  r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                  r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "SumatraPDF", "SumatraPDF.exe")]
    return next((p for p in candidates if p and os.path.exists(p)), None)


def list_printers():
    """이 PC에 설치된 프린터 이름 목록과 기본 프린터"""
    if os.name != "nt":
        try:
            out = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, timeout=10).stdout
            return [l.split()[0] for l in out.splitlines() if l.strip()], ""
        except Exception:
            return [], ""
    ps = ("$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "Get-CimInstance Win32_Printer | ForEach-Object { ($(if($_.Default){'*'}else{' '})) + $_.Name }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                             creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
    except Exception as e:
        log.info("프린터 목록을 읽지 못했습니다: %s", e)
        return [], ""
    names, default = [], ""
    for line in out.splitlines():
        if len(line) < 2:
            continue
        name = line[1:].strip()
        names.append(name)
        if line[0] == "*":
            default = name
    return names, default


class PrintError(Exception):
    def __init__(self, msg, done_orders):
        super().__init__(msg)
        self.done_orders = done_orders


STALL_WARN_SEC = 300   # 이 시간이 지나도 끝나지 않으면 '확인해 주세요' 알림 (강제로 끊지는 않음)


def _run_print(cmd, label, on_wait=None):
    """인쇄 명령을 실행하고 끝날 때까지 기다림. 시간이 오래 걸려도 강제로 끊지 않고 알림만 보냄"""
    proc = subprocess.Popen(cmd, creationflags=NO_WINDOW)
    start = time.time()
    while proc.poll() is None:
        time.sleep(1)
        if on_wait:
            el = time.time() - start
            on_wait(label, int(el), el > STALL_WARN_SEC)
    return proc.returncode


def print_pdf(path, cfg, sheets=None, on_batch=None, on_wait=None):
    """PDF를 인쇄. sheets(주문별 장수)를 주면 주문 N건씩 나눠서 보냄.
    on_batch(끝난 주문 순번 목록, 묶음 번호, 전체 묶음 수): 묶음마다 끝나면 호출
    on_wait(설명, 경과 초, 오래 걸림 여부): 기다리는 동안 1초마다 호출"""
    printer = printer_name(cfg)
    n_orders = len(sheets) if sheets else 0
    if is_test():                      # 테스트 모드: 프린터로 보내지 않고 화면에 열기만
        open_file(path)
        log.info("[테스트 모드] 인쇄 대신 PDF를 열었습니다: %s", os.path.basename(path))
        if on_batch and sheets:
            on_batch(list(range(n_orders)), 1, 1)
        return
    if os.name != "nt":
        subprocess.run(["lp"] + (["-d", printer] if printer else []) + [path], check=True)
        if on_batch:
            on_batch(list(range(n_orders)), 1, 1)
        return
    sumatra = find_sumatra(cfg)
    if not sumatra:
        # PDF 기본 프로그램으로 한 번에 인쇄 (나눠서 보내기 불가)
        os.startfile(path, "print")
        if on_batch:
            on_batch(list(range(n_orders)), 1, 1)
        return
    target = ["-print-to", printer] if printer else ["-print-to-default"]
    size = max(1, int(load_settings().get("batch_orders") or 20))
    if not sheets:
        rc = _run_print([sumatra] + target + ["-print-settings", "fit,simplex", "-silent", path], "프린터로 보내는 중", on_wait)
        if rc != 0:
            raise PrintError(f"인쇄 프로그램이 오류를 알렸습니다 (코드 {rc}).", [])
        return
    batches, start_page = [], 1
    for i in range(0, n_orders, size):
        idx = list(range(i, min(i + size, n_orders)))
        pages = sum(sheets[j] for j in idx)
        batches.append((idx, start_page, start_page + pages - 1))
        start_page += pages
    done = []
    for bno, (idx, p1, p2) in enumerate(batches, 1):
        label = f"프린터로 보내는 중 · 묶음 {bno}/{len(batches)} ({p1}~{p2}장)"
        rc = _run_print([sumatra] + target + ["-print-settings", f"{p1}-{p2},fit,simplex", "-silent", path], label, on_wait)
        if rc != 0:
            raise PrintError(f"묶음 {bno}/{len(batches)} 인쇄 중 인쇄 프로그램이 오류를 알렸습니다 (코드 {rc}).\n"
                             f"앞의 {len(done)}건은 프린터로 보냈고, 나머지는 '다음에 출력'으로 남겨 두었습니다.", done)
        done.extend(idx)
        if on_batch:
            on_batch(idx, bno, len(batches))


def test_print(cfg):
    """안내문 1장 테스트 인쇄"""
    setup_fonts()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "_test_print.pdf")
    c = canvas.Canvas(path, pagesize=landscape(A4))
    text(c, 60, 80, "Etsy 주문서 출력 관리 · 테스트 인쇄", size=20, bold=True)
    text(c, 60, 115, f"프린터: {printer_name(cfg) or '윈도우 기본 프린터'}", size=12)
    text(c, 60, 135, f"시각: {dt.datetime.now():%Y-%m-%d %H:%M:%S}", size=12)
    text(c, 60, 165, "이 종이가 나왔다면 주문서도 이 프린터로 잘 나옵니다. 주문 기록에는 영향이 없습니다.", size=11, color=MUTED)
    c.setStrokeColor(RULE)
    c.rect(40, 40, PAGE_W - 80, PAGE_H - 80)
    c.save()
    print_pdf(path, cfg)


def folder_stats(folder, pattern_ext=None):
    n = size = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            fp = os.path.join(folder, name)
            if os.path.isfile(fp) and (not pattern_ext or name.lower().endswith(pattern_ext)):
                n += 1
                size += os.path.getsize(fp)
    return n, size


def cleanup():
    """보관 기간이 지난 PDF·사진 지우기, 실행 기록이 너무 크면 정리"""
    s = load_settings()
    now = time.time()
    removed = 0
    for folder, days, ext in ((OUTPUT_DIR, s["pdf_keep_days"], ".pdf"), (IMAGE_DIR, s["image_keep_days"], None)):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            fp = os.path.join(folder, name)
            if os.path.isfile(fp) and (not ext or name.lower().endswith(ext)) and now - os.path.getmtime(fp) > days * 86400:
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    pass
    removed += prune_label_batches(s.get("label_keep_days", 30))
    removed += prune_test_data(s.get("test_keep_days", 14))
    try:
        if os.path.getsize(LOG_PATH) > 5 * 1024 * 1024:
            for h in list(log.handlers) + list(logging.getLogger().handlers):
                if isinstance(h, logging.FileHandler):
                    h.close()
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass
    return removed


MASK_KEYS = {"name", "first_line", "second_line", "city", "zip", "formatted_address", "buyer_email",
             "message_from_buyer", "gift_message", "gift_sender", "message_from_payment", "payment_email",
             "seller_email", "message_from_seller"}


def mask(obj):
    """고객 정보를 가린 사본"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in MASK_KEYS and v:
                out[k] = f"(가림 · {len(str(v))}자)"
            else:
                out[k] = mask(v)
        return out
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    return obj


# ---------------------------------------------------------------- 샘플 (--test)
def sample_receipts():
    now = int(time.time())
    letters = "FHJNEIKLMP"
    qty = [1, 1, 1, 1, 2, 1, 3, 1, 1, 2]
    usd = lambda v: {"amount": int(v * 100), "divisor": 100, "currency_code": "USD"}
    tx = [{"title": "IN-002-MG / 2 pcs - Initial Tiny Pendant, Alphabet, Capital letter, Matte Gold Plated over Brass, Upper case A-Z, Choose Two Letters!",
           "quantity": q, "sku": "IN-002-MG", "listing_id": 1, "listing_image_id": "A",
           "expected_ship_date": now + 6 * 86400, "shipping_method": "Standard Shipping",
           "variations": [{"formatted_name": "First Letter", "formatted_value": L},
                          {"formatted_name": "Second Letter", "formatted_value": L}]}
          for L, q in zip(letters, qty)]
    tx.append({"title": "PD-1742-MG / 2 Pcs - Tiny Christmas Charms Series, Deer Charm Pendant, Santa Claus Deer, Matte Gold Plated over Pewter / 14mm x 13mm",
               "quantity": 4, "sku": "PD-1742-MG", "listing_id": 2, "listing_image_id": "B", "variations": []})
    return [
        {"receipt_id": 4204473248, "name": "Jayme Vincent - TSM", "created_timestamp": now - 86400,
         "formatted_address": "Jayme Vincent - TSM\n408 White Oak Dr\nSULPHUR, LA 70663-6263\nUnited States",
         "country_iso": "US", "payment_method": "cc", "message_from_buyer": "",
         "total_shipping_cost": usd(0), "grandtotal": usd(58.4), "transactions": tx},
        {"receipt_id": 4204519907, "name": "Yuki Tanaka", "created_timestamp": now - 40000,
         "formatted_address": "Yuki Tanaka\n2-14-7 Jingumae\nShibuya-ku, Tokyo 150-0001\nJapan",
         "country_iso": "JP", "payment_method": "paypal",
         "message_from_buyer": "Please pack them separately, it's a gift for two friends. Thank you!",
         "is_gift": True, "gift_message": "Happy birthday! — Yuki",
         "total_shipping_cost": usd(6.5), "grandtotal": usd(24.9),
         "transactions": [{"title": "IN-002-MG / 2 pcs - Initial Tiny Pendant, Alphabet, Capital letter, Matte Gold Plated over Brass, Upper case A-Z, Choose Two Letters!",
                           "quantity": 2, "sku": "IN-002-MG", "listing_id": 1, "listing_image_id": "A",
                           "expected_ship_date": now + 4 * 86400, "shipping_upgrade": "Express",
                           "variations": [{"formatted_name": "First Letter", "formatted_value": "Y"},
                                          {"formatted_name": "Second Letter", "formatted_value": "T"}]}]},
    ]


def sample_images():
    """샘플용 가짜 상품 사진"""
    from PIL import Image, ImageDraw
    os.makedirs(IMAGE_DIR, exist_ok=True)
    out = {}
    for key, color in (("A", (214, 178, 92)), ("B", (196, 160, 70))):
        p = os.path.join(IMAGE_DIR, f"sample_{key}.jpg")
        img = Image.new("RGB", (75, 75), (238, 236, 228))
        d = ImageDraw.Draw(img)
        for i in range(12):
            x, y = 6 + (i % 4) * 17, 10 + (i // 4) * 20
            d.rounded_rectangle([x, y, x + 11, y + 14], 3, fill=color)
        img.save(p, quality=90)
        out[key] = p
    return out


# ---------------------------------------------------------------- 실행
HISTORY_PATH = os.path.join(BASE_DIR, "print_history.json")


def load_printed():
    return set(map(str, read_json(PRINTED_PATH, [])))


def save_printed(ids):
    write_json(PRINTED_PATH, sorted(ids, key=lambda s: int(s) if s.isdigit() else 0)[-5000:])


def load_history():
    """출력 기록 읽기. 예전 버전이 남긴 형식(주문번호만 있는 기록)도 같은 모양으로 바꿔서 돌려줌"""
    out = []
    for h in read_json(HISTORY_PATH, []):
        if not isinstance(h, dict):
            continue
        orders = []
        for o in h.get("orders", []) or []:
            if isinstance(o, dict):
                orders.append({"id": str(o.get("id", "")), "name": o.get("name", ""), "country": o.get("country", "")})
            else:
                orders.append({"id": str(o), "name": "", "country": ""})
        out.append({"time": str(h.get("time", "")), "kind": h.get("kind") or "출력", "file": h.get("file", ""),
                    "note": h.get("note", ""), "orders": orders})
    return out


LABEL_DATA_PATH = os.path.join(BASE_DIR, "label_orders.json")


def label_order(r):
    """라벨에 필요한 것만 (이름·주소 구분용·상품명·옵션)"""
    items = []
    for t in r.get("transactions", []) or []:
        # 옵션은 Etsy 주문 CSV(Variations)와 같은 모양: "First Letter:F,Second Letter:F" (웹 도구 라벨과 동일)
        vs = [f"{clean(v.get('formatted_name'))}:{clean(v.get('formatted_value'))}" if clean(v.get("formatted_name"))
              else clean(v.get("formatted_value"))
              for v in t.get("variations", []) or [] if clean(v.get("formatted_value"))]
        items.append({"title": clean(t.get("title")), "options": ",".join(vs)})
    addr = " ".join(clean(r.get(k)) for k in ("first_line", "second_line", "zip"))
    if not addr.strip():
        addr = clean(r.get("formatted_address"))
    return {"id": str(r.get("receipt_id")), "name": clean(r.get("name")), "addr": addr,
            "country": r.get("country_iso") or "", "items": items}


def load_label_batches():
    return [b for b in read_json(LABEL_DATA_PATH, []) if isinstance(b, dict)]


def save_label_batch(kind, receipts):
    """주문서를 인쇄한 묶음을 라벨용으로 저장"""
    if not receipts:
        return
    data = load_label_batches()
    now = dt.datetime.now()
    data.append({"id": f"{now:%Y%m%d%H%M%S%f}", "time": f"{now:%Y-%m-%d %H:%M:%S}", "kind": kind,
                 "orders": [label_order(r) for r in receipts], "label_printed": ""})
    write_json(LABEL_DATA_PATH, data)


def mark_label_printed(batch_ids, when=None):
    data = load_label_batches()
    stamp = when or f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    for b in data:
        if b["id"] in batch_ids:
            b["label_printed"] = stamp
    write_json(LABEL_DATA_PATH, data)


def set_label_batch_deleted(batch_ids, deleted=True):
    """라벨 목록에서 묶음 삭제(숨김) / 되살리기. 내용은 보관 기간 동안 그대로 남아 있음"""
    data = load_label_batches()
    stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M}" if deleted else ""
    for b in data:
        if b["id"] in batch_ids:
            b["deleted"] = stamp
    write_json(LABEL_DATA_PATH, data)


def prune_label_batches(days):
    data = load_label_batches()
    limit = (dt.datetime.now() - dt.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    keep = [b for b in data if b.get("time", "") >= limit]
    if len(keep) != len(data):
        write_json(LABEL_DATA_PATH, keep)
    return len(data) - len(keep)


def print_label_pdf(path, cfg, dialog=True, on_wait=None):
    """라벨 PDF 인쇄. dialog=True면 인쇄 창을 띄워 프린터·트레이를 직접 고름. 크기는 실제 크기(100%)"""
    if is_test():
        open_file(path)
        return "test"
    if os.name != "nt":
        subprocess.run(["lp", path], check=True)
        return "sent"
    sumatra = find_sumatra(cfg)
    if not sumatra:
        os.startfile(path)        # 인쇄 프로그램이 없으면 PDF를 열어서 직접 인쇄
        return "opened"
    if dialog:
        cmd = [sumatra, "-print-dialog", "-print-settings", "noscale,simplex", "-exit-when-done", path]
    else:
        printer = printer_name(cfg)
        cmd = [sumatra] + (["-print-to", printer] if printer else ["-print-to-default"]) + \
              ["-print-settings", "noscale,simplex", "-silent", path]
    rc = _run_print(cmd, "라벨 인쇄 중", on_wait)
    if rc == 1 and dialog:
        # 인쇄 창 방식에서는 잘 인쇄돼도(또는 창을 닫아도) 코드 1로 끝나는 경우가 있음 → 오류 대신 사람에게 물어봄
        log.info("[라벨 인쇄] 인쇄 프로그램이 코드 1로 끝남 (인쇄 창 방식) — 나왔는지 확인 요청")
        return "unsure"
    if rc != 0:
        raise PrintError(f"라벨 인쇄 중 인쇄 프로그램이 오류를 알렸습니다 (코드 {rc}).", [])
    return "sent"


def add_history(kind, file, receipts, note=""):
    """출력 기록 한 줄 추가 (최근 500개만 보관)"""
    hist = load_history()
    hist.append({"time": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "kind": kind,
                 "file": os.path.basename(file) if file else "", "note": note,
                 "orders": [{"id": str(r.get("receipt_id")), "name": r.get("name") or "",
                             "country": r.get("country_iso") or ""} for r in receipts]})
    write_json(HISTORY_PATH, hist[-500:])


# ---------------------------------------------------------------- 테스트 모드 (기록을 따로 저장)
TEST_DIR = os.path.join(BASE_DIR, "test_data")
# 테스트 모드에서 따로 저장하는 기록 (연결표·머리글자표·설정·인증은 함께 씀)
DATA_FILES = {"PRINTED_PATH": "printed_orders.json", "HISTORY_PATH": "print_history.json",
              "LABEL_DATA_PATH": "label_orders.json"}
STOCK_FILES = {"QUEUE_PATH": "stock_queue.json", "HISTORY_PATH": "stock_history.json", "SHORT_PATH": "stock_short.json",
               "SNAP_PATH": "stock_print_snap.json"}


def is_test():
    return bool(load_settings().get("test_mode"))


def apply_data_mode():
    """테스트 모드면 기록 파일 위치를 test_data 폴더로, 아니면 원래 위치로"""
    base = TEST_DIR if is_test() else BASE_DIR
    if base == TEST_DIR:
        os.makedirs(TEST_DIR, exist_ok=True)
    g = globals()
    for name, fn in DATA_FILES.items():
        g[name] = os.path.join(base, fn)
    st = sys.modules.get("stock")
    if st is not None:
        for name, fn in STOCK_FILES.items():
            setattr(st, name, os.path.join(base, fn))


def open_file(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        log.info("PDF를 열지 못했습니다: %s", e)


def test_data_stats():
    n = size = 0
    if os.path.isdir(TEST_DIR):
        for f in os.listdir(TEST_DIR):
            fp = os.path.join(TEST_DIR, f)
            if os.path.isfile(fp):
                n += 1
                size += os.path.getsize(fp)
    hist = read_json(os.path.join(TEST_DIR, "print_history.json"), [])
    return len(hist), size


def clear_test_data():
    if os.path.isdir(TEST_DIR):
        for f in os.listdir(TEST_DIR):
            fp = os.path.join(TEST_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    log.info("[테스트 모드] 테스트 기록을 모두 지웠습니다.")


def prune_test_data(days):
    """테스트 기록 중 보관 기간이 지난 것 지우기 (출력 기록·라벨 묶음·재고 대기·재고 기록)"""
    if not os.path.isdir(TEST_DIR):
        return 0
    limit = (dt.datetime.now() - dt.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    removed = 0
    for fn in ("print_history.json", "label_orders.json", "stock_queue.json", "stock_history.json"):
        fp = os.path.join(TEST_DIR, fn)
        data = read_json(fp, None)
        if not isinstance(data, list):
            continue
        keep = [x for x in data if not isinstance(x, dict) or str(x.get("time", "")) >= limit]
        if len(keep) != len(data):
            write_json(fp, keep)
            removed += len(data) - len(keep)
    return removed


apply_data_mode()


def run(args):
    cfg = load_config()
    if "login" in args:
        login(cfg)
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    if "--test" in args:
        imgs = sample_images()
        receipts = sample_receipts()
        for r in receipts:
            for t in r["transactions"]:
                t["_image_path"] = imgs[t["listing_image_id"]]
        path = os.path.join(OUTPUT_DIR, f"sample_{stamp}.pdf")
        build_pdf([normalize(r) for r in receipts], cfg, path, shop="beadsmaker")
        log.info("샘플 주문서를 만들었습니다: %s", path)
        if os.name == "nt":
            os.startfile(path)
        return

    client = EtsyClient(cfg)
    receipts = client.unshipped_receipts()
    receipts.sort(key=lambda r: r.get("created_timestamp") or 0)
    printed = load_printed()
    if "--all" not in args:
        receipts = [r for r in receipts if str(r.get("receipt_id")) not in printed]
    log.info("출력할 새 주문: %d건", len(receipts))
    if not receipts:
        return

    attach_images(client, receipts, lambda d, n: (d == n or d % 10 == 0) and log.info("상품 사진 준비 %d/%d", d, n))
    orders = [normalize(r) for r in receipts]
    path = os.path.join(OUTPUT_DIR, f"orders_{stamp}.pdf")
    sheets = build_pdf(orders, cfg, path, shop=client.shop_name())
    log.info("주문서 PDF 생성: %s (%d장)", path, sum(sheets))

    auto = cfg.getboolean("print", "auto_print", fallback=True) and "--no-print" not in args
    if not auto:
        if os.name == "nt":
            os.startfile(path)
        return

    done = []

    def batch_done(idx, bno, total):
        done.extend(idx)
        printed.update(str(receipts[i].get("receipt_id")) for i in idx)
        save_printed(printed)
        log.info("묶음 %d/%d 프린터로 보냄 (%d건)", bno, total, len(idx))

    last = [0]

    def waiting(label, sec, slow):
        if sec - last[0] >= 30:
            last[0] = sec
            log.info("%s · %d초 경과%s", label, sec, " (오래 걸리고 있어요. 프린터를 확인해 주세요)" if slow else "")
    try:
        print_pdf(path, cfg, sheets, batch_done, waiting)
    finally:
        add_history("자동/명령", path, [receipts[i] for i in done],
                    "" if len(done) == len(receipts) else f"{len(receipts) - len(done)}건 인쇄 못 함")
        save_label_batch("자동/명령", [receipts[i] for i in done])
        try:
            import stock
            stock.enqueue([receipts[i] for i in done])
        except Exception as e:
            log.warning(f"재고 차감 대기에 넣기 실패: {e}")
    log.info("인쇄 완료: %d건", len(done))


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except (UserError, PrintError) as e:
        log.error("[오류] %s", e)
        sys.exit(1)
    except Exception:
        log.exception("[예상치 못한 오류] 아래 내용을 캡처해서 알려주세요.")
        sys.exit(2)
