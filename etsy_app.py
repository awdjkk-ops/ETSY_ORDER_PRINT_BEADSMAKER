# -*- coding: utf-8 -*-
"""ETSY 주문서 출력 관리 - 통합 창 (출력 / 관리). 디자인은 theme.py · 디자인규칙.md (카페24 프로그램과 같은 모양)"""
import datetime as dt
import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import orderprint as op
import theme as T
from gui_labels import LabelTab
import gui_stock
import autoprint as AP
import autodeduct as AD
import watch as W
import gui_schedule
import print_schedule as sch
import updater as UP
from gui_scroll import scrolled

APP_VERSION = UP.local_info().get("version") if UP.local_info().get("version") != "0.0.0" else "0.8.0"


def hint(parent, text, **grid):
    style = "PageHint.TLabel" if grid.pop("page", False) else "Hint.TLabel"
    lb = ttk.Label(parent, text=text, style=style, wraplength=grid.pop("wrap", 430), justify="left")
    lb.grid(**grid)
    return lb


def open_path(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def fmt_ts(ts, with_time=True):
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(int(ts))
    return d.strftime("%m-%d %H:%M") if with_time else d.strftime("%m-%d")


def ship_by_ts(r):
    vals = [int(t["expected_ship_date"]) for t in r.get("transactions", []) or [] if t.get("expected_ship_date")]
    return min(vals) if vals else None


def item_count(r):
    return sum(int(t.get("quantity") or 1) for t in r.get("transactions", []) or [])


CUR_SIGN = {"USD": "$", "EUR": "€", "GBP": "£", "KRW": "₩", "JPY": "¥", "CAD": "CA$", "AUD": "A$"}


def item_price(r):
    """상품 금액 (배송비·세금 빼고, 할인 전) — Etsy 영수증의 total_price, 없으면 상품 줄 합계"""
    def money(m):
        if not isinstance(m, dict) or m.get("amount") is None:
            return None, ""
        return int(m["amount"]) / (int(m.get("divisor") or 100) or 100), m.get("currency_code") or ""
    amt, cur = money(r.get("total_price"))
    if amt is None:
        amt, cur = 0.0, ""
        for t in r.get("transactions", []) or []:
            a, c = money(t.get("price"))
            if a is not None:
                amt += a * int(t.get("quantity") or 1)
                cur = cur or c
        if not cur:
            return ""
    sign = CUR_SIGN.get(cur, cur + " ")
    return f"{sign}{amt:,.0f}" if cur in ("KRW", "JPY") else f"{sign}{amt:,.2f}"


def is_express(r):
    return any((t.get("shipping_upgrade") or "").strip() for t in r.get("transactions", []) or [])


def size_text(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def tomorrow_end():
    return time.mktime((dt.date.today() + dt.timedelta(days=2)).timetuple())


GROUPS = [   # (타일 이름, 고르는 방법)
    ("새 주문", lambda r: True),
    ("발송기한 임박", lambda r: (ship_by_ts(r) or 9e12) < tomorrow_end()),
    ("선물 주문", lambda r: bool(r.get("is_gift"))),
    ("빠른 배송", is_express),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Etsy 주문서 출력 관리")
        try:                                            # 창·작업 표시줄 아이콘 (바탕화면 아이콘과 같은 그림)
            ico = os.path.join(op.BASE_DIR, "etsy_order.ico")
            if os.path.exists(ico) and os.name == "nt":
                self.iconbitmap(default=ico)
        except Exception:
            pass
        self.minsize(760, 520)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1260, sw - 40)}x{min(960, sh - 80)}+10+10")
        self.q = queue.Queue()
        self.busy = False
        self.cfg = None
        self.client = None
        self.shop = ""
        self.receipts = []          # 미발송 주문 (주문 시각 순)
        self.loaded = False
        self.undo_stack = []        # 위치 맞추기 되돌리기용
        T.apply(self)
        try:
            self.cfg = op.load_config()
        except op.UserError as e:
            messagebox.showerror("설정 확인", str(e))
        self.printed = op.load_printed()

        # ---- 머리글
        top = tk.Frame(self, bg=T.CARD, highlightbackground=T.LINE, highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text="Etsy 주문서 출력 관리", bg=T.CARD, fg=T.INK, font=T.f(17, True)).pack(side="left", padx=(22, 8), pady=10)
        tk.Label(top, text=f"v{APP_VERSION}", bg=T.CARD, fg=T.MUTED, font=T.f(9)).pack(side="left", pady=(16, 10))
        self.update_pill = T.Pill(top, text=" ", bg="#E8F0FE", fg="#1A56C4")      # 새 버전 알림 (check_update)
        self.update_pill.configure(cursor="hand2")
        self.update_pill.bind("<Button-1>", lambda e: self.do_update())
        self.pending_update = None
        self.short_pill = T.Pill(top, text=" ", bg="#FDEBEC", fg=T.RED)          # 자동 출력 뒤 사입 필요 알림
        self.short_pill.configure(cursor="hand2")
        self.short_pill.bind("<Button-1>", lambda e: self.show_auto_short())
        self.update_msg = "확인 전"
        self.conn_pill = T.pill(top, "Etsy 연결 확인 중")
        self.conn_pill.pack(side="right", padx=(8, 22))
        self.result_pill = T.pill(top, "")          # 자동 출력 상태 (refresh_auto)
        self.result_pill.configure(cursor="hand2")
        self.result_pill.bind("<Button-1>", lambda e: self.show_tab("auto"))
        self._auto_seen = (AP.load_status() or {}).get("updated", "")
        self.test_pill = T.Pill(top, text="테스트 모드", bg="#FFE8D6", fg=T.ORANGE)
        td = dt.date.today()
        tk.Label(top, text=f"{td.year}년 {td.month}월 {td.day}일({'월화수목금토일'[td.weekday()]})",
                 bg=T.CARD, fg=T.MUTED, font=T.f(10)).pack(side="right", padx=8)

        # ---- 메뉴 버튼
        self.nav = tk.Frame(self, bg=T.BG)
        self.nav.pack(fill="x", padx=18, pady=(12, 2))
        self.nav_btns = {}
        # 테스트 모드 안내 띠 (켜져 있는 동안 항상 보임)
        self.test_banner = T.RoundBox(self, text="테스트 모드 — 주문서·라벨은 프린터로 보내지 않고 PDF로만 열고, 뽑음 표시·출력 기록·라벨 묶음·"
                                                  "재고 차감 대기는 테스트용으로 따로 저장합니다. 카페24 재고는 절대 바뀌지 않습니다. "
                                                  "(끄기: 관리 탭 → 테스트 모드)",
                                      fill="#FFF1E3", fg=T.ORANGE, under=T.BG, font=T.f(10, True))

        # ---- 작업 중 띠
        self.band = tk.Canvas(self, height=42, background=T.BG, highlightthickness=0, bd=0)
        self.band_fill, self.band_line = T.GREEN_L, "#BFE3D2"
        self.band_dot = tk.Label(self.band, text="●", bg=T.GREEN_L, fg=T.GREEN, font=T.f(11, True))
        self.prog_lb = tk.Label(self.band, text="", bg=T.GREEN_L, fg=T.GREEN_D, font=T.f(10, True), anchor="w")
        self.pbar = ttk.Progressbar(self.band, length=320)
        self.band.create_window(16, 21, window=self.band_dot, anchor="w")
        self.band.create_window(36, 21, window=self.prog_lb, anchor="w")
        self._w_bar = self.band.create_window(0, 21, window=self.pbar, anchor="e", state="hidden")
        self._band_job = None
        self.band.bind("<Configure>", lambda e: self._band_draw_soon())
        self._busy_msg = ""
        self._flash_job = None

        nb = ttk.Notebook(self, style="Main.TNotebook")
        nb.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.nb = nb
        self.pages = {}

        def add(key, text, build):
            area, tab = scrolled(nb, build)
            nb.add(area, text=text)
            self.pages[key] = area
            b = T.Pill(self.nav, text=text, bg=T.CARD, fg=T.INK, outline=T.LINE, under=T.BG, padx=16, pady=8)
            b.configure(cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            self.nav_btns[key] = b
            return tab
        self.print_tab = add("print", "출력", lambda p: ttk.Frame(p, padding=12, style="Page.TFrame"))
        self.label_tab = add("label", "라벨", lambda p: LabelTab(p, self))
        self.stock_tab = add("stock", "재고", lambda p: gui_stock.StockTab(p, self))
        self.auto_tab = add("auto", "자동 출력", lambda p: gui_schedule.ScheduleTab(p, self))
        self.admin_tab = add("admin", "관리", lambda p: ttk.Frame(p, padding=12, style="Page.TFrame"))

        def on_tab(e):
            self._paint_nav()
            self.refresh_admin()
            if nb.select() == str(self.pages["label"]):
                self.label_tab.on_show()
            if nb.select() == str(self.pages["stock"]):
                self.stock_tab.refresh()
            if nb.select() == str(self.pages["auto"]):
                self.auto_tab.show_result()
        nb.bind("<<NotebookTabChanged>>", on_tab)

        self._build_print_tab()
        self._build_admin_tab()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll)
        try:
            n = op.cleanup()
            if n:
                op.log.info(f"보관 기간이 지난 파일 {n}개를 정리했습니다.")
        except Exception as e:
            op.log.warning(f"정리 중 오류: {e}")
        self.refresh_status()
        self.after(300, self.load_board)
        self.after(5000, lambda: gui_stock.auto_refresh_products(self))
        self.update_test_ui()
        self.after(1500, self.refresh_auto)
        self.after(3000, self.check_update)              # 켜자마자 새 버전 확인 (깃허브)
        self.after(2000, lambda: self.refresh_link_alert(schedule=True))   # 출력 탭 '연결 확인' 알림 (1분마다 갱신)

    def show_auto_short(self):
        st = AP.load_status() or {}
        items = (st.get("deduct") or {}).get("short") or []
        if items:
            t = str(st.get("started", ""))
            gui_stock.ShortageWindow(self.stock_tab, items, False, when=f"{t[5:10]} {t[11:16]} 자동 출력분")
        st["short_seen"] = True
        op.write_json(AP.STATUS_PATH, st)
        self.short_pill.pack_forget()

    # ================================================================ 프로그램 업데이트 (깃허브)
    def check_update(self, manual=False):
        def work():
            try:
                upd = UP.check()
                msg = (f"새 버전 v{upd['version']} 있음" if upd else f"최신 버전입니다 (v{APP_VERSION})")
                ok = True
            except Exception as e:
                upd, msg, ok = None, f"확인 못 함: {str(e)[:120]}", False
            self.after(0, lambda: done(upd, msg, ok))

        def done(upd, msg, ok):
            self.pending_update = upd
            self.update_msg = f"{dt.datetime.now():%m/%d %H:%M} {msg}"
            if upd:
                self.update_pill.configure(text=f"새 버전 v{upd['version']} 있음 · 업데이트")
                self.update_pill.pack(side="left", padx=(14, 0), pady=12)
            else:
                self.update_pill.pack_forget()
            if hasattr(self, "upd_lb"):
                self.upd_lb.configure(text=self.update_msg, foreground=T.ORANGE if not ok else T.INK)
            if manual:
                if upd:
                    self.do_update()
                elif ok:
                    messagebox.showinfo("업데이트 확인", msg)
                else:
                    messagebox.showwarning("업데이트 확인", msg)
        threading.Thread(target=work, daemon=True).start()

    def do_update(self):
        upd = self.pending_update
        if not upd:
            return
        if self.busy or AP.lock_owner():
            messagebox.showinfo("업데이트", "지금 출력·차감 등 다른 작업이 진행 중입니다. 끝난 뒤 다시 눌러 주세요.")
            return
        notes = str(upd.get("notes") or "").strip() or "(설명 없음)"
        if not messagebox.askyesno("프로그램 업데이트", f"새 버전 v{upd['version']}을 설치할까요? (지금 v{APP_VERSION})\n\n"
                                                    f"바뀐 내용:\n{notes[:900]}\n\n"
                                                    "설치가 끝나면 프로그램이 다시 켜집니다. 설정·인증·기록은 그대로 둡니다."):
            return

        def work(prog):
            return UP.apply(upd, lambda d, n: prog(d, n, "새 버전 받는 중"))

        def done(ver):
            op.log.info(f"[업데이트] v{APP_VERSION} → v{ver}")
            messagebox.showinfo("업데이트 완료", f"v{ver} 설치를 마쳤습니다. 프로그램을 다시 켭니다.")
            UP.restart_app()
            self.destroy()
        self.bg(work, done, "새 버전 받는 중...", progress=True)

    def rollback_update(self):
        bs = UP.backups()
        if not bs:
            messagebox.showinfo("이전 버전으로 되돌리기", "되돌릴 이전 버전이 없습니다 (업데이트를 한 적이 없음).")
            return
        name = bs[0].name.split("_")[0]
        if not messagebox.askyesno("이전 버전으로 되돌리기", f"v{APP_VERSION} → v{name} 로 되돌릴까요?\n\n"
                                                         "설정·인증·기록은 그대로 둡니다. 되돌린 뒤 프로그램이 다시 켜집니다.\n"
                                                         "(깃허브에 더 새 버전이 있으면 머리글에 다시 업데이트 알림이 뜹니다)"):
            return
        try:
            ver = UP.rollback()
        except Exception as e:
            messagebox.showerror("되돌리기 실패", str(e))
            return
        op.log.info(f"[업데이트] 이전 버전으로 되돌림 → v{ver}")
        messagebox.showinfo("되돌리기 완료", f"v{ver}(으)로 되돌렸습니다. 프로그램을 다시 켭니다.")
        UP.restart_app()
        self.destroy()

    # ================================================================ 자동 출력 상태
    def refresh_auto(self):
        """머리글의 자동 출력 알약 + 자동 출력이 끝났으면 주문 목록 새로 고침 (30초마다)"""
        try:
            st = AP.load_status() or {}
            s = sch.load_settings()
            today = dt.date.today().isoformat()
            if st.get("started", "")[:10] == today and st.get("final"):
                r = st.get("result")
                txt = "오늘 자동 출력: " + gui_schedule.RESULT_TEXT.get(r, r or "")
                if r in ("ok", "unknown", "problem"):
                    txt += f" · {st.get('count', 0)}건"
                bad = r in ("problem", "fail") or st.get("stock_err")
                if st.get("stock_err") and r not in ("problem", "fail"):
                    txt += " · 재고 확인 못 함"
                self.result_pill.configure(text=txt, bg="#FDEBEC" if bad else T.GREEN_L, fg=T.RED if bad else T.GREEN_D)
            elif st.get("started", "")[:10] == today and not st.get("final") and AP.pid_alive(st.get("pid")):
                self.result_pill.configure(text=f"자동 출력 중 · {st.get('phase', '')}", bg="#FFF6C2", fg=T.ORANGE)
            else:
                nx = sch.next_run(s)
                txt = (f"다음 자동 출력 {nx:%m/%d} {nx:%H:%M}" if nx else "자동 출력 꺼짐")
                self.result_pill.configure(text=txt, bg=T.CARD if nx else "#F1F1EF", fg=T.MUTED)
            if not self.result_pill.winfo_ismapped():
                self.result_pill.pack(side="right", padx=4, after=self.conn_pill)
            ded = st.get("deduct") or {}
            if st.get("final") and ded.get("short") and not st.get("short_seen"):
                self.short_pill.configure(text=f"⚠ 사입 필요 {len(ded['short'])}품목 (자동 출력)")
                if not self.short_pill.winfo_ismapped():
                    self.short_pill.pack(side="right", padx=4, after=self.result_pill)
            elif self.short_pill.winfo_ismapped():
                self.short_pill.pack_forget()
            upd = st.get("updated", "")
            if upd != self._auto_seen and st.get("final"):
                self._auto_seen = upd
                if st.get("result") in ("ok", "unknown", "problem", "stopped") and not self.busy:
                    self.after_print()
        except Exception as e:
            op.log.warning(f"[자동 출력 상태] {e}")
        self.after(30000, self.refresh_auto)

    # ================================================================ 공통
    def show_tab(self, key):
        self.nb.select(self.pages[key])
        self._paint_nav()
        if key == "admin":
            self.refresh_admin()
        if key == "label":
            self.label_tab.on_show()
        if key == "stock":
            self.stock_tab.refresh()
        if key == "auto":
            self.auto_tab.show_result()

    def _paint_nav(self):
        cur = self.nb.select()
        for key, b in self.nav_btns.items():
            on = str(self.pages[key]) == cur
            b.configure(bg=T.INK if on else T.CARD, fg="white" if on else T.INK,
                        highlightbackground=T.INK if on else T.LINE)

    def bg(self, work, done=None, msg=None, busy=True, progress=False):
        """오래 걸리는 일은 따로 실행하고, 끝나면 done(결과)을 화면 쪽에서 호출"""
        if busy:
            if self.busy:
                messagebox.showinfo("잠시만요", "다른 작업이 진행 중입니다. 끝난 뒤 다시 눌러 주세요.")
                return
            self._set_busy(True, msg or "처리 중...")

        def prog(d, t, label=None):
            self.q.put(("progress", (d, t, label)))

        def runner():
            try:
                res = work(prog) if progress else work()
                self.q.put(("done", (done, res, busy)))
            except Exception as e:
                if not isinstance(e, (op.UserError, op.PrintError)):
                    op.log.exception("작업 오류")
                self.q.put(("error", (e, busy)))
        threading.Thread(target=runner, daemon=True).start()

    def call(self, fn):
        """작업 쪽에서 화면을 바꿀 때"""
        self.q.put(("call", fn))

    def _poll(self):
        """작업 쪽에서 보낸 알림을 화면에 반영. 한 건에서 오류가 나도 계속 돌아감"""
        try:
            for _ in range(200):
                try:
                    kind, data = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(kind, data)
                except Exception as e:
                    op.log.exception("화면 처리 오류")
                    if kind in ("done", "error") and self.busy:
                        self._set_busy(False)
                    messagebox.showerror("오류", f"화면을 고치는 중 오류가 났습니다.\n\n{e}\n\n"
                                               "관리 탭 → '실행 기록 보기' 내용을 Claude에게 보여주세요.")
        finally:
            self.after(100, self._poll)

    def _handle(self, kind, data):
        if kind == "progress":
            d, t, label = data
            if label:
                self._busy_msg = label
            base = self._busy_msg.rstrip(".") or "처리 중"
            if t:
                self.pbar.stop()
                self.pbar.configure(mode="determinate", maximum=t, value=d)
                self.prog_lb.configure(text=f"{base}  {d} / {t}   ·   끝날 때까지 창을 닫지 마세요", fg=T.GREEN_D)
            else:          # 개수를 셀 수 없는 단계
                self.pbar.configure(mode="indeterminate")
                self.pbar.start(12)
                self.prog_lb.configure(text=f"{base}...   ·   끝날 때까지 창을 닫지 마세요", fg=T.GREEN_D)
        elif kind == "wait":
            label, sec, slow = data
            if self.busy:
                el = f"{sec // 60}분 {sec % 60:02d}초" if sec >= 60 else f"{sec}초"
                if slow:
                    self.prog_lb.configure(text=f"⚠ {label} · {el} — 오래 걸리고 있어요. 프린터(용지·전원·대기열)를 "
                                                "확인해 주세요. 끊지 않고 계속 기다리는 중입니다", fg=T.RED)
                else:
                    self.prog_lb.configure(text=f"{label} · {el}   ·   끝날 때까지 창을 닫지 마세요", fg=T.GREEN_D)
        elif kind == "call":
            data()
        elif kind == "done":
            done, res, busy = data
            if busy:
                self._set_busy(False)
            if done:
                done(res)
        elif kind == "error":
            e, busy = data
            if busy:
                self._set_busy(False)
            self._show_error(e)

    def report_callback_exception(self, exc, val, tb):
        """버튼 등에서 난 오류를 기록하고 알려줌 (창이 멈춘 것처럼 보이지 않게)"""
        op.log.error("화면 오류", exc_info=(exc, val, tb))
        if self.busy:
            self._set_busy(False)
        messagebox.showerror("오류", f"오류가 났습니다.\n\n{val}\n\n관리 탭 → '실행 기록 보기' 내용을 Claude에게 보여주세요.")

    def _show_error(self, e):
        if isinstance(e, op.PrintError):
            messagebox.showerror("인쇄가 끝까지 되지 않았습니다", str(e))
            self.after_print()
        elif isinstance(e, op.UserError):
            messagebox.showerror("확인이 필요합니다", str(e))
        else:
            messagebox.showerror("오류", f"작업 중 오류가 발생했습니다.\n\n{e}\n\n계속되면 관리 탭 → '실행 기록 보기' 내용을 "
                                       "Claude에게 보여주세요.")
        self.refresh_status()

    def _set_busy(self, on, msg=""):
        self.busy = on
        for b in self.action_buttons + getattr(self, "action_buttons_admin", []):
            b.state(["disabled"] if on else ["!disabled"])
        for b in getattr(getattr(self, "stock_tab", None), "buttons", []) + getattr(self, "stock_admin_buttons", []):
            b.state(["disabled"] if on else ["!disabled"])
        if hasattr(self, "label_tab"):
            if on:
                for b in self.label_tab.buttons:
                    b.state(["disabled"])
            else:
                self.label_tab.draw()
        if on:
            self._busy_msg = msg
            self._band_show(msg, T.GREEN_L, T.GREEN_D, progress=True)
            self.pbar.configure(mode="indeterminate")
            self.pbar.start(12)
            self._blink()
        else:
            self.pbar.stop()
            self.pbar.configure(mode="determinate", value=0)
            self._band_hide()

    def _band_draw_soon(self):
        if self._band_job:
            self.after_cancel(self._band_job)
        self._band_job = self.after(40, self._band_draw)

    def _band_draw(self):
        self._band_job = None
        w = max(self.band.winfo_width(), 100)
        self.band.delete("frame")
        T._round_poly(self.band, 1, 1, w - 2, 40, T.RADIUS, fill=self.band_fill, outline=self.band_line, tags="frame")
        self.band.tag_lower("frame")
        self.band.coords(self._w_bar, w - 16, 21)

    def _band_show(self, text, bg, fg, progress=False):
        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None
        self.band_fill, self.band_line = bg, ("#BFE3D2" if bg == T.GREEN_L else T.LINE)
        for w in (self.band_dot, self.prog_lb):
            w.configure(bg=bg)
        self.prog_lb.configure(text=text, fg=fg)
        self.band_dot.configure(fg=T.GREEN if progress else fg)
        self.band.itemconfigure(self._w_bar, state="normal" if progress else "hidden")
        if not self.band.winfo_ismapped():
            self.band.pack(fill="x", padx=18, pady=(8, 0), before=self.nb)
        self._band_draw()

    def _band_hide(self):
        self.band.pack_forget()

    def _blink(self):
        if not self.busy:
            return
        cur = self.band_dot.cget("fg")
        self.band_dot.configure(fg=T.GREEN_L if cur == T.GREEN else T.GREEN)
        self.after(500, self._blink)

    def flash(self, text):
        """짧은 알림 — 3초 뒤 사라짐"""
        if self.busy:
            return
        self._band_show(text, T.CARD, T.INK)
        self._flash_job = self.after(3000, self._band_hide)

    def on_close(self):
        if self.busy and not messagebox.askyesno("작업 중", "작업이 진행 중입니다. 지금 닫으면 인쇄가 중단될 수 있어요.\n그래도 닫을까요?"):
            return
        self.destroy()

    def _client(self):
        if self.cfg is None:
            raise op.UserError("config.ini를 확인해 주세요.")
        if self.client is None:
            self.client = op.EtsyClient(self.cfg)
        return self.client

    # ================================================================ 상태 표시
    def refresh_status(self):
        token = op.read_json(op.TOKEN_PATH, None)
        if not token:
            self.conn_pill.configure(text="Etsy 연결 필요", bg="#FDEBEC", fg=T.RED)
            self._alerts["conn"] = "[확인 필요] Etsy와 연결되지 않았습니다 — 관리 탭에서 [다시 연결하기]를 눌러 주세요"
        elif self._alerts.get("conn_fail"):
            self.conn_pill.configure(text="Etsy 연결 확인 필요", bg="#FDEBEC", fg=T.RED)
            self._alerts.pop("conn", None)
        else:
            name = token.get("shop_name") or self.shop
            self.conn_pill.configure(text=f"Etsy 연결됨{' · ' + name if name else ''}", bg=T.GREEN_L, fg=T.GREEN_D)
            self._alerts.pop("conn", None)
        if os.name == "nt" and not op.find_sumatra(self.cfg):
            self._alerts["sumatra"] = "[확인 필요] 인쇄 프로그램(SumatraPDF)이 없습니다 — 관리 탭 '프린터' 안내를 확인해 주세요"
        else:
            self._alerts.pop("sumatra", None)
        self._render_alerts()

    def _render_alerts(self):
        for w in self.alert_fr.winfo_children():
            w.destroy()
        for key, text in self._alerts.items():
            if key == "conn_fail" and "conn" in self._alerts:
                continue
            if key == "link":                              # 연결 확인 알림: 누르면 재고 탭 연결표로
                box = T.RoundBox(self.alert_fr, text=text, fill=T.ORANGE if not text.startswith("연결 확인 중") else "#8A8F98",
                                 fg="white", under=T.BG)
                box.configure(cursor="hand2")
                box.bind("<Button-1>", lambda e: self.goto_link())
                box.pack(fill="x", pady=(0, 6))
                continue
            T.alert(self.alert_fr, text).pack(fill="x", pady=(0, 6))

    # ================================================================ 연결 확인 알림 (출력 탭)
    def refresh_link_alert(self, schedule=False):
        try:
            a = W.alert_summary()
            checking = getattr(self.stock_tab, "_order_checking", False)      # 주문 상품 확인 (몇 초)
            prog = getattr(self.stock_tab, "order_prog", "") if checking else ""
            if a["total"]:
                last = a.get("last", "")
                self._alerts["link"] = (f"⚠ 연결 확인 필요 {a['total']}개 — {W.alert_text(a)}"
                                        + (f"  (마지막 전체 확인 {last[5:16]})" if last else "")
                                        + (f"  · 주문 상품 확인 중… {prog}" if checking else "")
                                        + "\n누르면 재고 탭 연결표에서 확인합니다. 확인 전까지 해당 상품은 재고 차감에 반영되지 않습니다.")
            elif checking:
                self._alerts["link"] = (f"연결 확인 중… 이번 주문 상품만 보는 중 {prog}\n"
                                        "보통 몇 초면 끝납니다. (새 상품은 자동 연결을 시도합니다)")
            else:
                self._alerts.pop("link", None)
            self._render_alerts()
        except Exception as e:
            op.log.warning(f"[연결 확인 알림] {e}")
        if schedule:
            self.after(60_000, lambda: self.refresh_link_alert(schedule=True))

    def goto_link(self):
        a = W.alert_summary()
        st = self.stock_tab
        self.show_tab("stock")
        try:
            st.sub.select(2)
            view = ("review" if (a.get("name") or a.get("option") or a.get("missing")) else
                    "new" if a.get("new") else "code" if a.get("code") else "map")
            st.mview.set(view)
            st.fill_map()
        except Exception as e:
            op.log.warning(f"[연결 확인 알림] 이동 오류: {e}")

    def _link_gate(self, rs):
        """출력 전에 연결 확인이 필요한 것이 있으면 경고. 돌려줌: True = 출력 계속"""
        iss = W.order_issues(rs)                 # 이번에 출력할 주문 줄 중 재고 차감에 반영 안 될 것만
        checking = getattr(self.stock_tab, "_order_checking", False)
        if not iss and not checking:
            return True
        msg = ("연결 확인을 해야하는 품목이 있습니다.\n"
               "무시하고 출력하는 경우 재고 차감에 반영되지 않을 수 있습니다.\n"
               "재고탭에서 연결확인을 먼저 해주세요.")
        detail = []
        if iss:
            detail.append("· 이번에 출력할 주문 중: " + W.issues_text(iss))
        if checking:
            detail.append("· 지금 주문 상품을 확인하는 중입니다 (몇 초). 끝난 뒤 출력하면 더 정확합니다.")
        res = {"v": "cancel"}
        win = tk.Toplevel(self)
        win.title("연결 확인 필요")
        win.configure(background=T.CARD)
        win.transient(self)
        win.resizable(False, False)
        body = tk.Frame(win, bg=T.CARD, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="⚠ " + msg, bg=T.CARD, fg=T.INK, font=T.f(11, True), justify="left", anchor="w").pack(anchor="w")
        tk.Label(body, text="\n".join(detail), bg=T.CARD, fg=T.ORANGE, font=T.f(10), justify="left", anchor="w").pack(
            anchor="w", pady=(10, 0))
        bb = tk.Frame(body, bg=T.CARD)
        bb.pack(fill="x", pady=(16, 0))

        def pick(v):
            res["v"] = v
            win.destroy()
        ttk.Button(bb, text="취소", command=lambda: pick("cancel")).pack(side="right")
        ttk.Button(bb, text="무시하고 출력", style="Stop.TButton", command=lambda: pick("print")).pack(side="right", padx=6)
        ttk.Button(bb, text="재고탭으로 가기", style="Accent.TButton", command=lambda: pick("go")).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: pick("cancel"))
        gui_stock.center_on(win, 600, 260)
        win.grab_set()
        self.wait_window(win)
        if res["v"] == "go":
            self.goto_link()
        elif res["v"] == "print":
            op.log.info(f"[연결 확인] 경고 무시하고 출력 ({W.issues_text(iss) or '주문 상품 확인 중'})")
        return res["v"] == "print"

    # ================================================================ 출력 탭
    def _build_print_tab(self):
        t = self.print_tab
        left = ttk.Frame(t, style="Page.TFrame")
        left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(t, style="Page.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        self.alert_fr = ttk.Frame(left, style="Page.TFrame")
        self.alert_fr.grid(row=0, column=0, sticky="we")
        self._alerts = {}
        nf = ttk.Frame(left, style="Page.TFrame")
        nf.grid(row=1, column=0, sticky="we", pady=(4, 6))
        head = ttk.Frame(nf, style="Page.TFrame")
        head.pack(fill="x")
        self.new_title = ttk.Label(head, text="지금 새 주문  확인 중...", style="PageTitle.TLabel")
        self.new_title.pack(side="left")
        b = ttk.Button(head, text="새로 고침", style="Page.TButton", command=self.load_board)
        b.pack(side="right")
        self.tiles_fr = ttk.Frame(nf, style="Page.TFrame")
        self.tiles_fr.pack(fill="x", pady=(8, 0))

        # 출력하기
        af = ttk.LabelFrame(left, text=" 출력하기 ", padding=12)
        af.grid(row=2, column=0, sticky="we", pady=(8, 0))
        brow = ttk.Frame(af)
        brow.grid(row=0, column=0, sticky="w")
        b1 = ttk.Button(brow, text="지금 출력", style="Main.TButton", command=self.print_now)
        b1.pack(side="left")
        b2 = ttk.Button(brow, text="미리보기", command=self.preview_new, padding=(22, 10))
        b2.pack(side="left", padx=10)
        hint(af, "지금 출력: 아직 안 뽑은 주문을 모두 인쇄합니다. 인쇄 전에 한 번 확인하고, 출력한 주문은 기록되어 "
                 "다시 나오지 않습니다. 주문서 전체를 인쇄 작업 1개로 보내고, 다 보낸 뒤 기록합니다.\n"
                 "미리보기: 인쇄하지 않고 주문서를 화면(PDF)으로만 봅니다.",
             row=1, column=0, sticky="w", pady=(8, 0))
        lo = ttk.Frame(af)
        lo.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(lo, text="긴 주문 (2쪽 이상)", font=T.f(10, True)).pack(side="left", padx=(0, 10))
        self.long_layout = tk.StringVar(value=op.load_settings().get("long_order_layout", "continuous"))
        for val, txt in (("continuous", "이어서 2부"), ("side", "같은 쪽 나란히")):
            ttk.Radiobutton(lo, text=txt, value=val, variable=self.long_layout,
                            command=self._save_long_layout).pack(side="left", padx=(0, 10))
        hint(af, "이어서 2부: 한 장에 1쪽·2쪽이 나란히 이어지고, 원본(고객용)이 모두 나온 뒤 작업용이 모두 나옵니다 "
                 "(예: 원본 1·2쪽 → 원본 3·4쪽 → 작업용 1·2쪽 → 작업용 3·4쪽. 각 쪽에 '원본'/'작업용' 표시).\n"
                 "같은 쪽 나란히: 예전처럼 장마다 같은 쪽이 양쪽에 (원본 | 작업용).\n"
                 "한 쪽에 다 들어가는 주문은 어느 쪽을 골라도 지금처럼 양쪽에 같은 주문서가 나옵니다.",
             row=3, column=0, sticky="w", pady=(4, 0))

        # 어디까지 뽑았나
        lf = ttk.LabelFrame(left, text=" 어디까지 뽑았나 ", padding=12)
        lf.grid(row=3, column=0, sticky="we", pady=(8, 0))
        self.last_lb = ttk.Label(lf, text="", justify="left", wraplength=420, font=T.f(11, True))
        self.last_lb.grid(row=0, column=0, sticky="w")
        hint(lf, "오른쪽 주문 현황의 초록 줄 위치입니다. 다음 출력은 선 아래 주문부터 나옵니다. "
                 "Etsy에서 직접 뽑은 날은 주문 현황에서 선을 맞춰 주세요.", row=1, column=0, sticky="w", pady=(4, 0))

        # 오른쪽: 주문 현황 / 출력 기록
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        sub = ttk.Notebook(right)
        sub.grid(row=0, column=0, sticky="nsew")
        bf = ttk.Frame(sub, padding=10)
        hf = ttk.Frame(sub, padding=10)
        sub.add(bf, text=" 주문 현황 · 어디까지 뽑았나 ")
        sub.add(hf, text=" 출력 기록 ")
        sub.bind("<<NotebookTabChanged>>", lambda e: self.show_history())

        # --- 주문 현황
        bf.columnconfigure(0, weight=1)
        bf.rowconfigure(2, weight=1)
        tp = ttk.Frame(bf)
        tp.grid(row=0, column=0, sticky="we")
        self.bsearch = ttk.Entry(tp, width=24)
        self.bsearch.pack(side="left")
        self.bsearch.bind("<Return>", lambda e: self.board_find())
        bs = ttk.Button(tp, text="목록에서 찾기", command=self.board_find)
        bs.pack(side="left", padx=4)
        br = ttk.Button(tp, text="목록 새로 고침", command=self.load_board)
        br.pack(side="right")
        hint(bf, "Etsy의 결제 완료 · 미발송 주문 전체를 주문 시각 순으로 보여줍니다. 초록 줄이 '여기까지 뽑음' 위치입니다. "
                 "주문번호, 이름, 국가 코드(US, JP 등)를 입력해 [목록에서 찾기]를 누르면 그 주문으로 이동합니다.",
             row=1, column=0, sticky="w", pady=(4, 6), wrap=640)
        cols = (("time", "주문시각", 95), ("oid", "주문번호", 110), ("name", "수령인", 150), ("country", "국가", 50),
                ("items", "상품", 45), ("price", "상품 금액", 85), ("shipby", "발송기한", 70), ("st", "상태", 210))
        self.board = ttk.Treeview(bf, columns=[c[0] for c in cols], show="headings", height=14, selectmode="extended")
        for c, txt, w in cols:
            self.board.heading(c, text=txt)
            self.board.column(c, width=w, anchor="center" if c in ("items", "country", "shipby") else ("e" if c == "price" else "w"),
                              stretch=c in ("name", "st"))
        self.board.tag_configure("done", foreground=T.DIM)
        self.board.tag_configure("todo", foreground=T.INK)
        self.board.tag_configure("line", background=T.GREEN, foreground="white", font=T.f(10, True))
        self.board.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(bf, orient="vertical", command=self.board.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self.board.configure(yscrollcommand=sb.set)
        btns = ttk.Frame(bf)
        btns.grid(row=3, column=0, sticky="we", pady=(8, 0))
        b7 = ttk.Button(btns, text="선택한 주문까지 뽑은 것으로", command=self.set_line)
        b7.grid(row=0, column=0, sticky="w")
        b8 = ttk.Button(btns, text="이 고객부터 출력", command=self.print_from)
        b8.grid(row=0, column=1, sticky="w", padx=6)
        b9 = ttk.Button(btns, text="선택한 주문만 출력", command=self.print_selected)
        b9.grid(row=0, column=2, sticky="w")
        b12 = ttk.Button(btns, text="안 뽑은 상태로", command=self.revert_selected)
        b12.grid(row=0, column=3, sticky="w", padx=(6, 0))
        b10 = ttk.Button(btns, text="되돌리기", command=self.undo_line)
        b10.grid(row=0, column=4, sticky="e", padx=(12, 0))
        b13 = ttk.Button(btns, text="선택한 주문 미리보기", style="Ghost.TButton", command=self.preview_selected)
        b13.grid(row=1, column=0, sticky="w", pady=(6, 0))
        b14 = ttk.Button(btns, text="선택한 주문 라벨에 넣기", style="Ghost.TButton", command=self.add_to_labels)
        b14.grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        b15 = ttk.Button(btns, text="선택한 주문 라벨·재고 차감에 넣기", style="Ghost.TButton", command=self.add_to_labels_stock)
        b15.grid(row=1, column=2, columnspan=3, sticky="w", pady=(6, 0))
        self.board_menu = tk.Menu(self, tearoff=0, font=T.f(10))
        self.board_menu.add_command(label="선택한 주문까지 뽑은 것으로", command=self.set_line)
        self.board_menu.add_command(label="이 고객부터 출력", command=self.print_from)
        self.board_menu.add_command(label="선택한 주문만 출력", command=self.print_selected)
        self.board_menu.add_command(label="선택한 주문 미리보기", command=self.preview_selected)
        self.board_menu.add_command(label="선택한 주문 라벨에 넣기", command=self.add_to_labels)
        self.board_menu.add_command(label="선택한 주문 라벨·재고 차감에 넣기", command=self.add_to_labels_stock)
        self.board_menu.add_separator()
        self.board_menu.add_command(label="안 뽑은 상태로 되돌리기", command=self.revert_selected)

        def popup(e):
            row = self.board.identify_row(e.y)
            if row and row != "__line__" and row not in self.board.selection():
                self.board.selection_set(row)
            if self.board.selection() and not self.busy:
                self.board_menu.tk_popup(e.x_root, e.y_root)
        self.board.bind("<Button-3>", popup)
        self.board.bind("<Double-1>", self._cell_copy)
        self.board.bind("<MouseWheel>", lambda e: self._cell_close(), add="+")
        hint(bf, "· 선택한 주문까지 뽑은 것으로: 고른 주문과 그 위는 '뽑음', 아래는 '다음에 출력'이 됩니다. 인쇄는 안 합니다. "
                 "Etsy에서 직접 뽑은 뒤 위치를 맞출 때 씁니다(위·아래 어느 쪽으로든 옮길 수 있음).\n"
                 "· 이 고객부터 출력: 고른 주문 앞까지는 '뽑음'으로 두고, 고른 주문부터 끝까지 인쇄합니다.\n"
                 "· 선택한 주문만 출력: 고른 주문들만 인쇄합니다 (Ctrl·Shift+클릭으로 여러 개).\n"
                 "· 안 뽑은 상태로: 이미 뽑은(회색) 주문을 다음 출력에 다시 나오게 합니다.\n"
                 "· 되돌리기: 바로 전의 선 옮기기·안 뽑은 상태로 되돌리기를 취소합니다. (주문 위에서 오른쪽 클릭하면 메뉴가 나와요)\n"
                 "· 선택한 주문 라벨에 넣기: 고른 주문을 라벨 탭에 새 묶음으로 넣습니다 (재고 차감 대기에는 안 넣음).\n"
                 "· 선택한 주문 라벨·재고 차감에 넣기: 라벨 탭과 재고 차감 대기에 함께 넣습니다 (이미 대기에 있는 상품은 다시 안 들어감).\n"
                 "  두 버튼 모두, 고른 주문 중 안 뽑은 주문이 있으면 '뽑음'으로 바꿀지 먼저 묻습니다.\n"
                 "· 상태 칸의 '기한 임박'은 내일까지 보내야 하는 주문, '선물'·'빠른 배송'은 Etsy 주문 정보 그대로입니다.",
             row=4, column=0, sticky="w", pady=(6, 0), wrap=640)

        # --- 출력 기록
        hf.columnconfigure(0, weight=1)
        hf.rowconfigure(0, weight=1)
        cols = (("time", "날짜·시간", 110), ("kind", "구분", 90), ("count", "건수", 50),
                ("first", "첫 주문", 200), ("last", "마지막 주문", 200))
        self.hist = ttk.Treeview(hf, columns=[c[0] for c in cols], show="headings", height=8, selectmode="browse")
        for c, txt, w in cols:
            self.hist.heading(c, text=txt)
            self.hist.column(c, width=w, anchor="center" if c in ("count", "kind") else "w")
        self.hist.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.hist.bind("<<TreeviewSelect>>", lambda e: self.show_batch())
        self.hist.bind("<Double-1>", lambda e: self.open_batch_pdf())
        b3 = ttk.Button(hf, text="선택한 묶음 다시 뽑기", command=self.reprint_batch)
        b3.grid(row=1, column=2, sticky="e", pady=(6, 0))
        b4 = ttk.Button(hf, text="PDF 열기", command=self.open_batch_pdf)
        b4.grid(row=1, column=1, sticky="e", pady=(6, 0), padx=6)
        hint(hf, "최근 출력과 위치 맞추기 기록입니다. 줄을 누르면 아래에 주문 목록이 보이고, 용지 걸림 등으로 안 나왔을 때 "
                 "[선택한 묶음 다시 뽑기]로 그대로 다시 인쇄합니다 (이미 발송 처리된 주문은 빠집니다).",
             row=1, column=0, sticky="w", pady=(6, 0), wrap=420)
        self.blist = ttk.Treeview(hf, columns=("oid", "name", "country"), show="headings", height=8)
        for c, txt, w in (("oid", "주문번호", 150), ("name", "수령인", 220), ("country", "국가", 60)):
            self.blist.heading(c, text=txt)
            self.blist.column(c, width=w, anchor="w" if c != "country" else "center")
        self.blist.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        hf.rowconfigure(2, weight=1)
        self.action_buttons = [b, b1, b2, b3, b4, b7, b8, b9, b10, b12, b13, b14, bs, br]

    def _render_tiles(self, todo):
        for w in self.tiles_fr.winfo_children():
            w.destroy()
        self.new_title.configure(text=f"지금 새 주문  {len(todo)}건")
        if not todo:
            ttk.Label(self.tiles_fr, text="새로 출력할 주문이 없습니다.", style="PageHint.TLabel").grid(row=0, column=0, sticky="w")
            return
        shown = [(name, [r for r in todo if fn(r)]) for name, fn in GROUPS]
        shown = [(n, rs) for i, (n, rs) in enumerate(shown) if i == 0 or rs]
        for i, (name, rs) in enumerate(shown):
            t = T.tile(self.tiles_fr, name, len(rs), width=138)
            t.grid(row=i // 3, column=i % 3, padx=(0, 8), pady=(0, 8))
            t.configure(cursor="hand2")
            t.bind("<Button-1>", lambda e, nm=name, group=rs: self.print_group(nm, group))
            t.bind("<Enter>", lambda e, tt=t: tt.hover(True))
            t.bind("<Leave>", lambda e, tt=t: tt.hover(False))
        ttk.Label(self.tiles_fr, text="타일을 누르면 그 주문들만 인쇄합니다.", style="PageHint.TLabel").grid(
            row=(len(shown) - 1) // 3 + 1, column=0, columnspan=3, sticky="w")

    # ---------------------------------------------------------------- 주문 현황
    def load_board(self):
        if self.cfg is None:
            return
        self.new_title.configure(text="지금 새 주문  Etsy에서 확인 중...")

        def work():
            c = self._client()
            rs = c.unshipped_receipts()
            shop = c.shop_name()
            rs.sort(key=lambda r: r.get("created_timestamp") or 0)
            return rs, shop

        def done(res):
            self.receipts, self.shop = res
            self.loaded = True
            self._alerts.pop("conn_fail", None)
            self.fill_board()
            self.refresh_status()
            try:                                   # 출력 전: 아직 안 뽑은 주문의 상품만 확인 (몇 초)
                self.stock_tab.run_order_check(self._todo())
            except Exception as e:
                op.log.warning(f"[주문 상품 확인] 시작 실패: {e}")

        def fail(e):
            self.new_title.configure(text="지금 새 주문  확인 실패")
            if isinstance(e, op.UserError) and "연결" in str(e):
                self._alerts["conn_fail"] = f"[확인 필요] {e}"
            else:
                self._alerts["conn_fail"] = "[확인 필요] Etsy에서 주문을 불러오지 못했습니다 — 인터넷 연결을 확인하고 [새로 고침]을 눌러 주세요"
            op.log.warning(f"주문 불러오기 실패: {e}")
            self.refresh_status()

        def runner():
            try:
                res = work()
                self.call(lambda: done(res))
            except Exception as e:
                self.call(lambda: fail(e))
        threading.Thread(target=runner, daemon=True).start()

    def done_ids(self):
        return {str(r.get("receipt_id")) for r in self.receipts} & self.printed

    def fill_board(self):
        self._cell_close()
        self.printed = op.load_printed()
        rows = self.receipts
        sel = set(self.board.selection())
        self.board.delete(*self.board.get_children())
        is_done = [str(r.get("receipt_id")) in self.printed for r in rows]
        last_done = max((i for i, d in enumerate(is_done) if d), default=-1)
        if last_done == -1:
            self.board.insert("", "end", iid="__line__", tags=("line",), values=("", "▲ 아직 뽑은 주문 없음", "", "", "", "", "", ""))
        te = tomorrow_end()
        for i, (r, done) in enumerate(zip(rows, is_done)):
            rid = str(r.get("receipt_id"))
            marks = []
            if (ship_by_ts(r) or 9e12) < te and not done:
                marks.append("기한 임박")
            if r.get("is_gift"):
                marks.append("선물")
            if is_express(r):
                marks.append("빠른 배송")
            st = "✓ 뽑음" if done else ("● " + " · ".join(marks) + " · 다음에 출력" if marks else "○ 다음에 출력")
            self.board.insert("", "end", iid=rid, tags=("done" if done else "todo",), values=(
                fmt_ts(r.get("created_timestamp")), rid, r.get("name") or "", r.get("country_iso") or "",
                item_count(r), item_price(r), fmt_ts(ship_by_ts(r), False), st))
            if i == last_done:
                self.board.insert("", "end", iid="__line__", tags=("line",), values=("", "▲ 여기까지 뽑음", "", "", "", "", "", ""))
        keep = [i for i in sel if self.board.exists(i)]
        if keep:
            self.board.selection_set(keep)
        self.board.see("__line__")
        todo = [r for r, d in zip(rows, is_done) if not d]
        self._render_tiles(todo)
        if last_done >= 0:
            r = rows[last_done]
            self.last_lb.configure(text=f"마지막으로 뽑은 주문: {r.get('receipt_id')}  {r.get('name') or ''}\n"
                                        f"주문 {fmt_ts(r.get('created_timestamp'))} · {r.get('country_iso') or ''}")
        else:
            self.last_lb.configure(text="아직 뽑은 것으로 표시된 주문이 없습니다.\n"
                                        "오른쪽 주문 현황에서 Etsy에서 마지막으로 뽑은 주문을 골라\n"
                                        "[선택한 주문까지 뽑은 것으로]를 눌러 위치를 맞춰 주세요.")
        self.show_history()

    def _cell_copy(self, e):
        """주문번호·수령인 칸을 두 번 누르면 그 자리에 읽기 전용 칸을 띄워 글자를 블록으로 잡아 둠 → Ctrl+C로 복사"""
        self._cell_close()
        row, col = self.board.identify_row(e.y), self.board.identify_column(e.x)
        if not row or row == "__line__":
            return
        cid = self.board["columns"][int(col[1:]) - 1] if col and col[1:].isdigit() else ""
        if cid not in ("oid", "name"):
            return
        bbox = self.board.bbox(row, col)
        if not bbox:
            return
        x, y, w, h = bbox
        val = str(self.board.set(row, cid))
        var = tk.StringVar(value=val)
        ent = tk.Entry(self.board, textvariable=var, font=T.f(10), relief="solid", bd=1, readonlybackground="#FFFBE6",
                       fg=T.INK, highlightthickness=0)
        ent.configure(state="readonly")
        ent.place(x=x, y=y, width=max(w, 60), height=h)
        ent.focus_set()
        ent.select_range(0, "end")
        ent.icursor("end")

        def copy(_e=None):
            txt = ent.selection_get() if ent.selection_present() else val
            self.clipboard_clear()
            self.clipboard_append(txt)
            self.flash(f"복사했습니다: {txt}")
            return "break"
        ent.bind("<Control-c>", copy)
        ent.bind("<Control-C>", copy)
        ent.bind("<Escape>", lambda _e: self._cell_close())
        ent.bind("<FocusOut>", lambda _e: self._cell_close())
        self._cell = ent
        return "break"

    def _cell_close(self):
        ent = getattr(self, "_cell", None)
        if ent is not None:
            self._cell = None
            try:
                ent.destroy()
            except tk.TclError:
                pass

    def _selected_idx(self):
        ids = [i for i in self.board.selection() if i != "__line__"]
        idx = {str(r.get("receipt_id")): n for n, r in enumerate(self.receipts)}
        return sorted(idx[i] for i in ids if i in idx)

    def board_find(self):
        q = self.bsearch.get().strip().lower()
        if not q:
            return
        hits = [str(r.get("receipt_id")) for r in self.receipts
                if q in str(r.get("receipt_id")) or q in (r.get("name") or "").lower()
                or q == (r.get("country_iso") or "").lower()]
        if not hits:
            messagebox.showinfo("찾기", f"주문 현황에서 '{q}'을(를) 찾지 못했습니다.\n(이미 발송 처리된 주문은 목록에 없습니다)")
            return
        self.board.selection_set(hits)
        self.board.see(hits[0])

    def _change_printed(self, new_set, note):
        self.undo_stack.append(set(self.printed))
        self.undo_stack = self.undo_stack[-20:]
        op.save_printed(new_set)
        op.log.info(f"[표시 변경] {note}")
        self.fill_board()

    def set_line(self):
        sel = self._selected_idx()
        if len(sel) != 1:
            messagebox.showinfo("위치 맞추기", "주문 현황에서 '마지막으로 뽑은 주문' 하나를 골라 주세요.")
            return
        i = sel[0]
        above = [str(r.get("receipt_id")) for r in self.receipts[:i + 1]]
        below = [str(r.get("receipt_id")) for r in self.receipts[i + 1:]]
        r = self.receipts[i]
        if not messagebox.askyesno("위치 맞추기", f"{r.get('receipt_id')} {r.get('name') or ''} "
                                                f"(주문 {fmt_ts(r.get('created_timestamp'))})까지 뽑은 것으로 맞춥니다.\n\n"
                                                f"· 뽑음: {len(above)}건\n· 다음에 출력: {len(below)}건\n\n"
                                                "인쇄는 하지 않습니다. 계속할까요?"):
            return
        self._change_printed((self.printed | set(above)) - set(below), f"{r.get('receipt_id')}까지 뽑은 것으로")
        op.add_history("위치 맞춤", "", [r])
        self.show_history()

    def revert_selected(self):
        sel = self._selected_idx()
        targets = [str(self.receipts[i].get("receipt_id")) for i in sel]
        targets = [t for t in targets if t in self.printed]
        if not targets:
            messagebox.showinfo("안 뽑은 상태로", "주문 현황에서 이미 뽑은(회색) 주문을 골라 주세요. (Ctrl·Shift+클릭으로 여러 개)")
            return
        if not messagebox.askyesno("안 뽑은 상태로", f"{len(targets)}건을 '다음에 출력' 상태로 바꿀까요?\n다음 '지금 출력' 때 다시 인쇄됩니다."):
            return
        self._change_printed(self.printed - set(targets), f"{len(targets)}건 안 뽑은 상태로")
        self.flash(f"{len(targets)}건을 안 뽑은 상태로 되돌렸습니다.")

    def undo_line(self):
        if not self.undo_stack:
            messagebox.showinfo("되돌리기", "되돌릴 표시 변경이 없습니다.")
            return
        prev = self.undo_stack.pop()
        op.save_printed(prev)
        op.log.info("[표시 변경] 되돌리기")
        self.fill_board()
        self.flash("바로 전의 표시 변경을 되돌렸습니다.")

    # ---------------------------------------------------------------- 출력
    def _confirm(self, rs, what):
        extra = (f"\n\n가장 이른 주문: {fmt_ts(rs[0].get('created_timestamp'))}"
                 f"\n가장 늦은 주문: {fmt_ts(rs[-1].get('created_timestamp'))}") if rs else ""
        return messagebox.askyesno("인쇄 확인", f"{what} {len(rs)}건을 인쇄할까요?{extra}")

    def _make_pdf(self, rs, prefix, prog):
        path, sheets, note, _err, _low = AP.make_pdf(self.cfg, self._client(), self.shop, rs, prefix, prog,
                                                     lambda p: self.q.put(("progress", (0, 0, p))))
        self._stock_note = note
        return path, sheets

    def _print(self, rs, kind):
        rs = list(rs)

        def work(prog):
            try:
                lock = AP.run_lock(kind)
                lock.__enter__()
            except AP.Busy as e:
                raise op.UserError(f"{e}\n끝난 뒤 다시 눌러 주세요.")
            try:
                if kind == "지금 출력":                 # 화면을 연 사이 자동 출력이 이미 뽑은 주문은 빼기 (두 번 안 뽑히게)
                    fresh = op.load_printed()
                    gone = [r for r in rs if str(r.get("receipt_id")) in fresh]
                    if gone:
                        rs[:] = [r for r in rs if str(r.get("receipt_id")) not in fresh]
                        op.log.info(f"[지금 출력] 그사이 이미 뽑힌 {len(gone)}건 제외")
                        if not rs:
                            raise op.UserError("그사이 자동 출력이 이 주문들을 이미 뽑았습니다. 새로 출력할 주문이 없습니다.")
                path, sheets = self._make_pdf(rs, "orders", prog)

                def on_wait(label, sec, slow):
                    self.q.put(("wait", (label, sec, slow)))
                done = AP.send(self.cfg, rs, path, sheets, kind, prog, on_wait)
                ded = None
                if done and AD.setting_on():                # 출력하면 재고 바로 차감 (관리 탭에서 켬)
                    try:
                        ded = AD.run([rs[i] for i in done], self.cfg, lambda p: self.q.put(("progress", (0, 0, p))))
                    except Exception as e:
                        op.log.exception("[바로 차감] 오류")
                        ded = {"on": True, "err": f"재고 차감 중 오류: {str(e)[:150]} (줄은 차감 대기에 남음)"}
            finally:
                lock.__exit__(None, None, None)
            return len(done), sum(sheets), ded

        def finished(res):
            n, pages, ded = res
            note = getattr(self, "_stock_note", "")
            if note:
                self.flash(note)
            if op.is_test():
                msg = (f"{n}건 ({pages}장)을 인쇄하지 않고 PDF로 열었습니다.\n"
                       "테스트 기록에 '뽑음'·라벨 묶음·재고 차감 대기가 저장되었습니다.")
                title = "출력 완료 (테스트 모드)"
            else:
                msg, title = f"{n}건 ({pages}장)을 프린터로 보냈습니다.", "출력 완료"
            line = AD.summary_line(ded)
            probs = AD.problems(ded) if ded else []
            if line:
                msg += f"\n\n{line}"
            if probs:
                messagebox.showwarning(title + " · 확인할 것 있음", msg + "\n\n⚠ " + "\n⚠ ".join(probs))
            else:
                messagebox.showinfo(title, msg)
            self.after_print()
            try:
                self.stock_tab.refresh()
            except Exception:
                pass
            if ded and ded.get("short"):                   # 사입이 필요하면 사입 목록 창을 바로 띄움
                gui_stock.ShortageWindow(self.stock_tab, ded["short"], False, when="방금 출력분")
        self.bg(work, finished, "주문서 준비 중...", progress=True)

    def after_print(self):
        self.printed = op.load_printed()
        self.fill_board()
        self.load_board()
        self.refresh_admin()

    def _save_long_layout(self):
        st = op.load_settings()
        st["long_order_layout"] = self.long_layout.get()
        op.save_settings(st)
        self.flash("긴 주문 출력 방식: " + ("이어서 2부" if self.long_layout.get() == "continuous" else "같은 쪽 나란히"))

    def _todo(self):
        return [r for r in self.receipts if str(r.get("receipt_id")) not in self.printed]

    def print_now(self):
        if not self.loaded:
            messagebox.showinfo("지금 출력", "아직 Etsy 주문을 불러오지 못했습니다. [새로 고침]을 눌러 주세요.")
            return
        rs = self._todo()
        if not rs:
            messagebox.showinfo("지금 출력", "새로 출력할 주문이 없습니다.")
            return
        if self._link_gate(rs) and self._confirm(rs, "새 주문"):
            self._print(rs, "지금 출력")

    def print_group(self, name, rs):
        if self.busy or not rs:
            return
        if self._link_gate(rs) and self._confirm(rs, f"'{name}'"):
            self._print(rs, name)

    def print_from(self):
        sel = self._selected_idx()
        if len(sel) != 1:
            messagebox.showinfo("이 고객부터 출력", "주문 현황에서 출력을 시작할 주문 하나를 골라 주세요.")
            return
        i = sel[0]
        before = [str(r.get("receipt_id")) for r in self.receipts[:i]]
        target = self.receipts[i:]
        r = self.receipts[i]
        if not messagebox.askyesno("이 고객부터 출력", f"{r.get('receipt_id')} {r.get('name') or ''}부터 끝까지 인쇄합니다.\n"
                                                    f"· 인쇄: {len(target)}건\n· 그 앞 {len(before)}건은 '뽑음'으로 표시\n\n계속할까요?"):
            return
        if before:
            self._change_printed(self.printed | set(before), "이 고객부터 출력 (앞쪽 뽑음 표시)")
        self._print(target, "이 고객부터")

    def print_selected(self):
        sel = self._selected_idx()
        if not sel:
            messagebox.showinfo("선택한 주문만 출력", "주문 현황에서 인쇄할 주문을 골라 주세요 (Ctrl·Shift+클릭으로 여러 개).")
            return
        rs = [self.receipts[i] for i in sel]
        if self._confirm(rs, "고른 주문"):
            self._print(rs, "선택 출력")

    def _preview(self, rs):
        if not rs:
            messagebox.showinfo("미리보기", "미리볼 주문이 없습니다.")
            return

        def work(prog):
            path, _ = self._make_pdf(list(rs), "preview", prog)
            return path
        self.bg(work, lambda p: (open_path(p), self.flash(f"미리보기를 열었습니다 · {len(rs)}건"
                                                          + (f" · {self._stock_note}" if getattr(self, "_stock_note", "") else ""))),
                "미리보기 준비 중...", progress=True)

    def preview_new(self):
        if not self.loaded:
            messagebox.showinfo("미리보기", "아직 Etsy 주문을 불러오지 못했습니다. [새로 고침]을 눌러 주세요.")
            return
        self._preview(self._todo())

    def add_to_labels(self):
        self._add_selected(with_stock=False)

    def add_to_labels_stock(self):
        self._add_selected(with_stock=True)

    def _add_selected(self, with_stock):
        title = "라벨·재고 차감에 넣기" if with_stock else "라벨에 넣기"
        where = "라벨 탭과 재고 차감 대기" if with_stock else "라벨 탭"
        sel = self._selected_idx()
        if not sel:
            messagebox.showinfo(title, f"주문 현황에서 {where}에 넣을 주문을 골라 주세요 (Ctrl·Shift+클릭으로 여러 개).")
            return
        rs = [self.receipts[i] for i in sel]
        ids = {str(r.get("receipt_id")) for r in rs}
        have = set()
        for b in op.load_label_batches():
            if not b.get("deleted"):
                have |= {o["id"] for o in b.get("orders", [])} & ids
        note = ""
        if have:
            note = f"\n\n※ 이 중 {len(have)}건은 이미 다른 라벨 묶음에 들어 있습니다. 라벨을 고를 때 같은 주문은 한 번만 나옵니다."
        unprinted = ids - self.printed
        mark = False
        if unprinted:
            ans = messagebox.askyesnocancel(
                title,
                f"미출력 주문입니다. 뽑음 상태로 변경하시겠습니까?\n\n"
                f"고른 {len(rs)}건 중 {len(unprinted)}건이 아직 안 뽑은 주문입니다.\n"
                f"· 예 : 뽑음으로 바꾸고 {where}에 넣기\n"
                f"· 아니오 : 안 뽑은 상태 그대로 {where}에 넣기\n"
                f"· 취소 : 아무것도 하지 않기" + note)
            if ans is None:
                return
            mark = bool(ans)
        else:
            extra = "" if with_stock else "\n(재고 차감 대기에는 넣지 않습니다)"
            if not messagebox.askyesno(title, f"고른 주문 {len(rs)}건을 {where}에 넣을까요?{extra}" + note):
                return
        if mark:
            self._change_printed(self.printed | unprinted, f"{len(unprinted)}건 뽑음으로 ({title})")
        op.save_label_batch(title, rs)
        self.label_tab.on_show()
        msg = f"{len(rs)}건을 라벨 탭에 넣었습니다."
        if with_stock:
            import stock
            had = stock.existing_lines(rs)
            added = stock.enqueue(rs)
            back = 0
            sk = had.get("skipped", [])
            if sk and messagebox.askyesno(title, f"이 중 {len(sk)}줄은 전에 [이 줄 차감 안 함]으로 뺀 상품 줄이라 다시 들어가지 않았습니다.\n\n"
                                          + "\n".join(f"· {x['receipt_id']}  {x['title'][:40]}" for x in sk[:8])
                                          + (f"\n… 외 {len(sk) - 8}줄" if len(sk) > 8 else "")
                                          + "\n\n차감 대기로 되돌릴까요?"):
                stock.set_status({x["tid"] for x in sk}, "pending")
                back = len(sk)
                op.log.info(f"[{title}] 차감 안 함 {back}줄 차감 대기로 되돌림")
            self.stock_tab.refresh()
            msg += f" 재고 차감 대기 {added + back}줄 추가" + (f" (차감 안 함에서 되돌림 {back}줄)" if back else "") + "."
            rest = [f"{stock.STATUS_WORD.get(k, k)} {len(v)}줄" for k, v in had.items() if not (k == "skipped" and back)]
            if rest:
                msg += " 이미 들어 있던 줄: " + ", ".join(rest) + "."
        if mark:
            msg += f" {len(unprinted)}건은 뽑음으로 바꿨습니다."
        op.log.info(f"[{title}] {len(rs)}건, 뽑음 변경 {len(unprinted) if mark else 0}건")
        self.flash(msg)

    # ---------------------------------------------------------------- 테스트 모드
    def update_test_ui(self):
        on = op.is_test()
        if on:
            if not self.test_pill.winfo_ismapped():
                self.test_pill.pack(side="right", padx=(8, 0), after=self.conn_pill)
            if not self.test_banner.winfo_ismapped():
                self.test_banner.pack(fill="x", padx=18, pady=(8, 0), after=self.nav)
        else:
            self.test_pill.pack_forget()
            self.test_banner.pack_forget()

    def set_test_mode(self, on):
        if self.busy:
            messagebox.showinfo("테스트 모드", "작업이 끝난 뒤 바꿔 주세요.")
            self.test_var.set(not on)
            return
        if on:
            ok = messagebox.askyesno("테스트 모드 켜기", "테스트 모드를 켤까요?\n\n"
                                     "· 주문서·라벨은 프린터로 보내지 않고 PDF로만 엽니다.\n"
                                     "· 뽑음 표시·출력 기록·라벨 묶음·재고 차감 대기는 테스트용으로 따로 저장됩니다.\n"
                                     "· 카페24 재고는 절대 바뀌지 않습니다 (계산만).\n"
                                     "· 연결표·머리글자표·설정은 실제와 같이 씁니다.")
        else:
            ok = messagebox.askyesno("테스트 모드 끄기", "테스트 모드를 끄고 실제 사용으로 돌아갈까요?\n\n"
                                     "실제 기록(뽑음 표시·출력 기록·라벨·재고)으로 돌아가고, 테스트 기록은 그대로 남아 있습니다.\n"
                                     "끄면 '지금 출력'은 실제로 프린터로 보내집니다.")
        if not ok:
            self.test_var.set(not on)
            return
        st = op.load_settings()
        st["test_mode"] = bool(on)
        op.save_settings(st)
        op.apply_data_mode()
        op.log.info(f"[테스트 모드] {'켜짐' if on else '꺼짐'}")
        self.undo_stack = []
        self.printed = op.load_printed()
        self.fill_board()
        self.label_tab.reload(keep=False)
        self.stock_tab.refresh()
        self.update_test_ui()
        self.refresh_admin()
        self.flash("테스트 모드를 켰습니다." if on else "테스트 모드를 껐습니다. 이제 실제로 인쇄됩니다.")

    def clear_test(self):
        n, size = op.test_data_stats()
        if not messagebox.askyesno("테스트 기록 지우기", f"테스트 기록(출력 {n}묶음 · {size_text(size)})을 모두 지울까요?\n"
                                                      "실제 기록과 연결표는 지워지지 않습니다.", icon="warning"):
            return
        op.clear_test_data()
        if op.is_test():
            self.printed = op.load_printed()
            self.fill_board()
            self.label_tab.reload(keep=False)
            self.stock_tab.refresh()
        self.refresh_admin()
        self.flash("테스트 기록을 모두 지웠습니다.")

    def preview_selected(self):
        sel = self._selected_idx()
        if not sel:
            messagebox.showinfo("미리보기", "주문 현황에서 미리볼 주문을 골라 주세요.")
            return
        self._preview([self.receipts[i] for i in sel])

    # ---------------------------------------------------------------- 출력 기록
    def _history(self):
        return list(reversed(op.load_history()))[:200]

    def show_history(self):
        self.hist.delete(*self.hist.get_children())
        fmt = lambda o: f"{o.get('id', '')} {o.get('name', '')}"
        for i, b in enumerate(self._history()):
            os_ = b.get("orders", [])
            f, l = (os_[0], os_[-1]) if os_ else ({}, {})
            kind = b.get("kind", "") + (" ⚠" if b.get("note") else "")
            self.hist.insert("", "end", iid=str(i), values=(str(b.get("time", ""))[5:16], kind,
                                                          len(os_) if b.get("kind") != "위치 맞춤" else "-",
                                                          fmt(f), fmt(l) if b.get("kind") != "위치 맞춤" else ""))

    def _batch(self):
        sel = self.hist.selection()
        if not sel:
            return None
        h = self._history()
        i = int(sel[0])
        return h[i] if i < len(h) else None

    def show_batch(self):
        b = self._batch()
        self.blist.delete(*self.blist.get_children())
        if b:
            for o in b.get("orders", []):
                self.blist.insert("", "end", values=(o.get("id", ""), o.get("name", ""), o.get("country", "")))
            if b.get("note"):
                self.flash(f"이 기록: {b['note']}")

    def open_batch_pdf(self):
        b = self._batch()
        if not b:
            messagebox.showinfo("출력 기록", "기록을 먼저 골라 주세요.")
            return
        p = os.path.join(op.OUTPUT_DIR, b.get("file") or "")
        if b.get("file") and os.path.exists(p):
            open_path(p)
        else:
            messagebox.showinfo("출력 기록", "PDF 파일이 없습니다. (위치 맞춤 기록이거나, 보관 기간이 지나 지워졌습니다)")

    def reprint_batch(self):
        b = self._batch()
        if not b or b.get("kind") == "위치 맞춤":
            messagebox.showinfo("다시 뽑기", "출력 기록에서 다시 뽑을 묶음을 골라 주세요.")
            return
        by_id = {str(r.get("receipt_id")): r for r in self.receipts}
        rs = [by_id[o.get("id")] for o in b.get("orders", []) if o.get("id") in by_id]
        missing = len(b.get("orders", [])) - len(rs)
        if not rs:
            messagebox.showinfo("다시 뽑기", "이 묶음의 주문은 모두 발송 처리되어 목록에 없습니다.")
            return
        what = "다시 뽑을 주문" + (f" (발송 처리된 {missing}건은 빠짐)" if missing else "")
        if self._confirm(rs, what):
            self._print(rs, "다시 뽑기")

    # ================================================================ 관리 탭
    def _build_admin_tab(self):
        t = self.admin_tab
        left = ttk.Frame(t, style="Page.TFrame")
        left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(t, style="Page.TFrame")
        right.grid(row=0, column=1, sticky="nw", padx=(16, 0))
        W = 560

        # Etsy 연결
        cf = ttk.LabelFrame(left, text=" Etsy 연결 ", padding=12)
        cf.grid(row=0, column=0, sticky="we")
        self.conn_lb = ttk.Label(cf, text="", wraplength=W, justify="left")
        self.conn_lb.grid(row=0, column=0, columnspan=2, sticky="w")
        hint(cf, "연결이 끊겼거나(오래 쓰지 않으면 만료) 다른 Etsy 계정으로 바꿀 때 누릅니다. 브라우저가 열리면 Etsy에 로그인한 상태에서 "
                 "'Grant access(허용)'를 누르고, '연결 완료'가 뜨면 창을 닫으세요. (5분 안에)",
             row=1, column=0, columnspan=2, sticky="w", pady=(4, 8), wrap=W)
        a1 = ttk.Button(cf, text="다시 연결하기", style="Accent.TButton", command=self.relogin)
        a1.grid(row=2, column=0, sticky="w")
        a2 = ttk.Button(cf, text="연결 확인", command=self.check_conn)
        a2.grid(row=2, column=1, sticky="w", padx=6)

        # 프린터
        pf = ttk.LabelFrame(left, text=" 프린터 ", padding=12)
        pf.grid(row=1, column=0, sticky="we", pady=(12, 0))
        hint(pf, "주문서를 뽑을 프린터를 고르세요. '윈도우 기본 프린터 사용'을 고른 경우, 윈도우 설정의 'Windows에서 기본 프린터 관리'는 "
                 "꺼두는 것이 안전합니다.", row=0, column=0, columnspan=3, sticky="w", pady=(0, 8), wrap=W)
        self.printer_cb = ttk.Combobox(pf, width=42, state="readonly")
        self.printer_cb.grid(row=1, column=0, sticky="w")
        a3 = ttk.Button(pf, text="저장", command=self.save_printer)
        a3.grid(row=1, column=1, sticky="w", padx=6)
        a4 = ttk.Button(pf, text="테스트 인쇄", command=self.test_print)
        a4.grid(row=1, column=2, sticky="w")
        self.sumatra_lb = ttk.Label(pf, text="")
        self.sumatra_lb.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        hint(pf, "테스트 인쇄는 안내문 1장이 나오며, 주문 기록에는 영향이 없습니다.\n"
                 "SumatraPDF가 없으면 https://www.sumatrapdfreader.org 에서 받아 설치하세요(설치하면 자동으로 찾습니다).",
             row=3, column=0, columnspan=3, sticky="w", wrap=W)
        hint(pf, "주문서는 카페24 주문서 프로그램과 같은 방식으로, 이번에 뽑는 주문 전체를 인쇄 작업 1개로 보냅니다. "
                 "보내는 동안 인쇄 대기열을 지켜보고, 시간이 오래 걸려도 강제로 끊지 않으며, 5분 동안 진행이 없으면 위쪽 띠에 "
                 "'프린터를 확인해 주세요'라고 알려줍니다. 다 보낸 뒤 '뽑음'으로 기록합니다.",
             row=4, column=0, columnspan=3, sticky="w", pady=(8, 0), wrap=W)

        # 저장 공간
        sf = ttk.LabelFrame(right, text=" 저장 공간 ", padding=12)
        sf.grid(row=0, column=0, sticky="we")
        hint(sf, "프로그램을 켤 때마다 보관 기간이 지난 파일을 자동으로 지웁니다. 지워도 '이미 출력한 주문' 기록은 남아 있어서 "
                 "예전 주문이 다시 출력되지 않고, 상품 사진은 필요할 때 Etsy에서 다시 받아옵니다.",
             row=0, column=0, columnspan=3, sticky="w", pady=(0, 8), wrap=470)
        self.stat_lbs = {}
        for i, key in enumerate(("주문서 PDF", "상품 사진", "실행 기록(로그)", "출력 기록", "라벨용 주문 기록")):
            ttk.Label(sf, text=key).grid(row=1 + i, column=0, sticky="w", pady=1)
            a, b_ = ttk.Label(sf, text=""), ttk.Label(sf, text="")
            a.grid(row=1 + i, column=1, sticky="w", padx=(20, 0))
            b_.grid(row=1 + i, column=2, sticky="w", padx=(20, 0))
            self.stat_lbs[key] = (a, b_)
        kf = ttk.Frame(sf)
        kf.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(kf, text="주문서 PDF 보관 기간").grid(row=0, column=0, sticky="w")
        self.pdf_keep = ttk.Combobox(kf, width=6, state="readonly", values=["3일", "7일", "14일", "30일", "60일"])
        self.pdf_keep.grid(row=0, column=1, padx=6, pady=2)
        self.pdf_keep.bind("<<ComboboxSelected>>", lambda e: self._save_setting("pdf_keep_days", int(self.pdf_keep.get()[:-1])))
        ttk.Label(kf, text="상품 사진 보관 기간").grid(row=1, column=0, sticky="w")
        self.img_keep = ttk.Combobox(kf, width=6, state="readonly", values=["30일", "60일", "90일"])
        self.img_keep.grid(row=1, column=1, padx=6, pady=2)
        self.img_keep.bind("<<ComboboxSelected>>", lambda e: self._save_setting("image_keep_days", int(self.img_keep.get()[:-1])))
        ttk.Label(kf, text="라벨용 주문 기록 보관 기간").grid(row=2, column=0, sticky="w")
        self.lbl_keep = ttk.Combobox(kf, width=6, state="readonly", values=["7일", "14일", "30일", "60일", "90일"])
        self.lbl_keep.grid(row=2, column=1, padx=6, pady=2)
        self.lbl_keep.bind("<<ComboboxSelected>>", lambda e: self._save_setting("label_keep_days", int(self.lbl_keep.get()[:-1])))
        a5 = ttk.Button(kf, text="지금 정리하기", command=self.clean_now)
        a5.grid(row=0, column=2, rowspan=3, padx=(14, 0))
        hint(sf, "상품 사진은 마지막으로 쓴 날부터 계산합니다. 출력 기록은 최근 500개까지 보관합니다.\n"
                 "라벨용 주문 기록은 주문서를 뽑은 날부터 계산하며, 고객 이름이 들어 있어 기간이 지나면 지웁니다. "
                 "지난 주문의 라벨을 뽑으려면 그만큼 길게 두세요.",
             row=7, column=0, columnspan=3, sticky="w", pady=(6, 0), wrap=470)

        # 문제 확인 도구
        tf = ttk.LabelFrame(right, text=" 문제 확인 도구 ", padding=12)
        tf.grid(row=1, column=0, sticky="we", pady=(12, 0))
        a6 = ttk.Button(tf, text="데이터 확인 파일 만들기", command=self.data_check)
        a6.grid(row=0, column=0, sticky="w")
        hint(tf, "Etsy에서 받은 미발송 주문 몇 건의 원본 데이터를 고객 정보(이름·주소·메시지 등)를 가린 채 파일로 저장합니다. "
                 "주문서에 옵션이나 글자가 이상하게 나올 때 이 파일을 Claude에게 보여주세요.",
             row=1, column=0, sticky="w", pady=(4, 8), wrap=470)
        a7 = ttk.Button(tf, text="실행 기록 보기", command=lambda: open_path(op.LOG_PATH) if os.path.exists(op.LOG_PATH)
                        else messagebox.showinfo("실행 기록", "아직 실행 기록이 없습니다."))
        a7.grid(row=2, column=0, sticky="w")
        hint(tf, "오류가 났을 때 '실행 기록 보기'의 마지막 부분을 Claude에게 보여주면 원인을 찾을 수 있습니다.",
             row=3, column=0, sticky="w", pady=(4, 0), wrap=470)

        # 폴더 · 정보
        tmf = ttk.LabelFrame(right, text=" 테스트 모드 ", padding=12)
        tmf.grid(row=3, column=0, sticky="we", pady=(12, 0))
        self.test_var = tk.BooleanVar(value=op.is_test())
        ttk.Checkbutton(tmf, text="테스트 모드 사용", variable=self.test_var, style="Big.TCheckbutton",
                        command=lambda: self.set_test_mode(self.test_var.get())).grid(row=0, column=0, columnspan=3, sticky="w")
        hint(tmf, "실제로 쓰기 전에 연습할 때 켭니다. 주문서·라벨은 PDF로만 열리고, 뽑음 표시·출력 기록·라벨 묶음·재고 차감 대기는 "
                  "테스트용으로 따로 저장됩니다. Etsy 주문은 실제 주문을 읽기만 하고, 카페24 재고는 절대 바뀌지 않습니다. "
                  "연결표·머리글자표는 실제와 같이 씁니다.", row=1, column=0, columnspan=3, sticky="w", pady=(2, 8), wrap=470)
        self.test_stat = ttk.Label(tmf, text="")
        self.test_stat.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(tmf, text="테스트 기록 보관 기간").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.test_keep = ttk.Combobox(tmf, width=6, state="readonly", values=["7일", "14일", "30일"])
        self.test_keep.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        self.test_keep.bind("<<ComboboxSelected>>", lambda e: self._save_setting("test_keep_days", int(self.test_keep.get()[:-1])))
        a11 = ttk.Button(tmf, text="테스트 기록 모두 지우기", style="Stop.TButton", command=self.clear_test)
        a11.grid(row=3, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        hint(tmf, "보관 기간이 지난 테스트 기록은 프로그램을 켤 때 자동으로 지웁니다.", row=4, column=0, columnspan=3,
             sticky="w", pady=(4, 0), wrap=470)

        of = ttk.LabelFrame(right, text=" 폴더 · 프로그램 정보 ", padding=12)
        of.grid(row=2, column=0, sticky="we", pady=(12, 0))
        fr = ttk.Frame(of)
        fr.grid(row=0, column=0, sticky="w")
        ttk.Button(fr, text="주문서 PDF 폴더 열기",
                   command=lambda: (os.makedirs(op.OUTPUT_DIR, exist_ok=True), open_path(op.OUTPUT_DIR))).pack(side="left")
        ttk.Button(fr, text="프로그램 폴더 열기", command=lambda: open_path(op.BASE_DIR)).pack(side="left", padx=6)
        ttk.Label(of, text=f"지금 버전 v{APP_VERSION}", font=T.f(11, True)).grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(of, text=f"업데이트 저장소: github.com/{UP.repo_of()}", style="Hint.TLabel").grid(
            row=2, column=0, sticky="w")
        self.upd_lb = ttk.Label(of, text=self.update_msg, wraplength=460, justify="left")
        self.upd_lb.grid(row=3, column=0, sticky="w", pady=(4, 0))
        ub = ttk.Frame(of)
        ub.grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Button(ub, text="지금 업데이트 확인", command=lambda: self.check_update(manual=True)).pack(side="left")
        ttk.Button(ub, text="이전 버전으로 되돌리기", command=self.rollback_update).pack(side="left", padx=6)
        hint(of, "프로그램을 켤 때마다 깃허브에 새 버전이 있는지 확인하고, 있으면 머리글에 '새 버전 … · 업데이트'가 뜹니다. "
                 "누르면 바뀐 내용을 보여 주고 설치합니다. config.ini·token.json·카페24 인증·settings.json·모든 기록(.json)·"
                 "출력 폴더는 업데이트로 절대 바뀌지 않습니다. 새 버전에 문제가 있으면 [이전 버전으로 되돌리기].",
             row=5, column=0, sticky="w", wrap=470)
        lf = ttk.LabelFrame(left, text=" 라벨 인쇄 위치 (폼텍 LS-3145) ", padding=12)
        lf.grid(row=2, column=0, sticky="we", pady=(12, 0))
        hint(lf, "① [맞춤 확인용 인쇄]로 일반 A4에 칸 테두리를 뽑아 라벨지와 겹쳐 불빛에 비춰봅니다.\n"
                 "② 테두리가 오른쪽으로 밀렸으면 가로를 −, 아래로 밀렸으면 세로를 − 방향으로 옮깁니다 (0.1mm 단위).\n"
                 "③ 위쪽은 맞는데 아래로 갈수록 밀리면 배율을 조정합니다. 저장 후 다시 확인하세요.",
             row=0, column=0, columnspan=4, sticky="w", pady=(0, 8), wrap=W)
        self.cal_vars = {}
        for i, (key, label, lo, hi) in enumerate((("offset_x", "가로 위치(mm)", -20, 20), ("offset_y", "세로 위치(mm)", -20, 20),
                                                  ("scale_x", "가로 배율(%)", -10, 10), ("scale_y", "세로 배율(%)", -10, 10))):
            v = tk.StringVar(value="0.0")
            self.cal_vars[key] = v
            ttk.Label(lf, text=label).grid(row=1 + i // 2, column=(i % 2) * 2, sticky="e", padx=(0 if i % 2 == 0 else 20, 6), pady=3)
            ttk.Spinbox(lf, from_=lo, to=hi, increment=0.1, textvariable=v, width=7, format="%.1f").grid(
                row=1 + i // 2, column=(i % 2) * 2 + 1, sticky="w", pady=3)
        self.lbl_dialog = tk.BooleanVar(value=True)
        ttk.Checkbutton(lf, text="라벨 인쇄할 때 인쇄 창 열기 (프린터·뒷면 트레이를 직접 고르기)", variable=self.lbl_dialog,
                        command=lambda: self._save_setting("label_print_dialog", self.lbl_dialog.get())).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        hint(lf, "켜짐: 라벨 인쇄 때 인쇄 창이 뜹니다. 프린터를 고르고 [기본 설정]에서 급지를 '뒷면 트레이'로 바꾼 뒤 인쇄하세요. "
                 "끄면 위 '프린터'에서 고른 프린터로 창 없이 바로 보냅니다(트레이는 프린터 기본 설정대로).",
             row=4, column=0, columnspan=4, sticky="w", wrap=W)
        lb_row = ttk.Frame(lf)
        lb_row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
        a8 = ttk.Button(lb_row, text="저장", command=self.save_cal)
        a8.pack(side="left")
        a9 = ttk.Button(lb_row, text="맞춤 확인용 인쇄", command=self.cal_print)
        a9.pack(side="left", padx=6)
        a10 = ttk.Button(lb_row, text="0으로 되돌리기", command=self.reset_cal)
        a10.pack(side="left")
        self.action_buttons_admin = [a1, a2, a3, a4, a5, a6, a8, a9, a10]
        self.action_buttons_admin.append(a11)
        self.stock_admin_refresh, self.stock_admin_buttons = gui_stock.build_admin_cards(left, self, 3, W)
        self._printers = []
        self.after(500, self.load_printers)

    def refresh_admin(self):
        if not hasattr(self, "conn_lb"):
            return
        if hasattr(self, "stock_admin_refresh"):
            self.stock_admin_refresh()
        token = op.read_json(op.TOKEN_PATH, None)
        if not token:
            self.conn_lb.configure(text="상태: 연결 안 됨 — [다시 연결하기]를 눌러 주세요", foreground=T.ORANGE)
        elif self._alerts.get("conn_fail"):
            self.conn_lb.configure(text=f"상태: 확인 필요 — {self._alerts['conn_fail'].replace('[확인 필요] ', '')}",
                                   foreground=T.ORANGE)
        else:
            self.conn_lb.configure(text=f"상태: 정상 (상점 {token.get('shop_name') or self.shop or token.get('shop_id')}, "
                                        "사용할 때마다 자동 연장)", foreground=T.GREEN)
        sm = op.find_sumatra(self.cfg)
        if os.name == "nt":
            self.sumatra_lb.configure(text="인쇄 프로그램(SumatraPDF): 설치됨" if sm else "인쇄 프로그램(SumatraPDF): 없음 — 설치가 필요합니다",
                                      foreground=T.GREEN if sm else T.ORANGE)
        s = op.load_settings()
        self.pdf_keep.set(f"{s['pdf_keep_days']}일")
        self.img_keep.set(f"{s['image_keep_days']}일")
        self.lbl_keep.set(f"{s['label_keep_days']}일")
        self.test_keep.set(f"{s.get('test_keep_days', 14)}일")
        self.test_var.set(bool(s.get("test_mode")))
        tn, tsize = op.test_data_stats()
        self.test_stat.configure(text=("지금: 테스트 모드 켜짐" if s.get("test_mode") else "지금: 실제 사용 중")
                                 + f"   ·   테스트 기록 출력 {tn}묶음 · {size_text(tsize)}",
                                 foreground=T.ORANGE if s.get("test_mode") else T.INK)
        cal = s.get("label_cal") or {}
        for k, v in self.cal_vars.items():
            v.set(f"{float(cal.get(k, 0)):.1f}")
        self.lbl_dialog.set(bool(s.get("label_print_dialog", True)))
        stats = {"주문서 PDF": op.folder_stats(op.OUTPUT_DIR, ".pdf"), "상품 사진": op.folder_stats(op.IMAGE_DIR)}
        logs = [p for p in (op.LOG_PATH, op.LOG_PATH + ".old") if os.path.exists(p)]
        stats["실행 기록(로그)"] = (len(logs), sum(os.path.getsize(p) for p in logs))
        hist = op.load_history()
        stats["출력 기록"] = (len(hist), os.path.getsize(op.HISTORY_PATH) if os.path.exists(op.HISTORY_PATH) else 0)
        lb = op.load_label_batches()
        stats["라벨용 주문 기록"] = (len(lb), os.path.getsize(op.LABEL_DATA_PATH) if os.path.exists(op.LABEL_DATA_PATH) else 0)
        for k, (n, size) in stats.items():
            a, b_ = self.stat_lbs[k]
            a.configure(text=f"{n}{'묶음' if k in ('출력 기록', '라벨용 주문 기록') else '개'}")
            b_.configure(text=size_text(size))

    def _cal_values(self):
        out = {}
        for k, v in self.cal_vars.items():
            try:
                out[k] = round(float(v.get()), 1)
            except ValueError:
                raise op.UserError("보정 값은 숫자로 입력해 주세요 (예: -0.5).")
        return out

    def save_cal(self):
        try:
            self._save_setting("label_cal", self._cal_values())
        except op.UserError as e:
            messagebox.showwarning("라벨 위치", str(e))

    def reset_cal(self):
        for v in self.cal_vars.values():
            v.set("0.0")
        self.save_cal()

    def cal_print(self):
        try:
            cal = self._cal_values()
        except op.UserError as e:
            messagebox.showwarning("라벨 위치", str(e))
            return
        dialog = self.lbl_dialog.get()
        if not messagebox.askyesno("맞춤 확인용 인쇄", "일반 A4 용지에 라벨 칸 테두리 1장을 인쇄할까요?\n(지금 칸에 입력한 보정 값으로 그립니다)"):
            return
        import etsy_labels as L

        def work():
            os.makedirs(op.OUTPUT_DIR, exist_ok=True)
            path = os.path.join(op.OUTPUT_DIR, "_label_calibration.pdf")
            L.write_calibration_pdf(path, cal, fonts=(op.FONT_CANDIDATES["Kr"][-1][0], op.FONT_CANDIDATES["KrB"][-1][0]))
            return op.print_label_pdf(path, self.cfg, dialog)
        self.bg(work, lambda r: self.flash("맞춤 확인용 인쇄를 보냈습니다." if r != "opened" else "PDF를 열었습니다. 실제 크기로 인쇄하세요."),
                "맞춤 확인용 인쇄 준비 중...")

    def _save_setting(self, key, val):
        s = op.load_settings()
        s[key] = val
        op.save_settings(s)
        self.flash("저장했습니다.")

    def load_printers(self):
        def work():
            return op.list_printers()

        def done(res):
            names, default = res
            self._printers = names
            cur = op.printer_name(self.cfg)
            vals = ["(윈도우 기본 프린터 사용" + (f": {default})" if default else ")")] + names
            if cur and cur not in names:
                vals.append(cur)
            self.printer_cb.configure(values=vals)
            self.printer_cb.set(cur if cur else vals[0])
            self.refresh_admin()
        self.bg(work, done, busy=False)

    def save_printer(self):
        v = self.printer_cb.get()
        name = "" if v.startswith("(윈도우 기본") else v
        self._save_setting("printer", name)
        self.flash(f"주문서 프린터: {name or '윈도우 기본 프린터'} 로 저장했습니다.")

    def test_print(self):
        if not messagebox.askyesno("테스트 인쇄", f"'{op.printer_name(self.cfg) or '윈도우 기본 프린터'}'로 안내문 1장을 인쇄할까요?\n"
                                                "(프린터를 바꿨다면 먼저 [저장]을 눌러 주세요)"):
            return
        self.bg(lambda: op.test_print(self.cfg), lambda r: self.flash("테스트 인쇄를 보냈습니다."), "테스트 인쇄 보내는 중...")

    def relogin(self):
        if not messagebox.askyesno("Etsy 다시 연결", "브라우저에서 Etsy 허용 화면이 열립니다.\n'Grant access(허용)'를 누르고 "
                                                  "'연결 완료'가 뜨면 이 창으로 돌아오세요. 계속할까요?"):
            return

        def done(_):
            self.client = None
            self._alerts.pop("conn_fail", None)
            messagebox.showinfo("Etsy 다시 연결", "연결했습니다.")
            self.refresh_status()
            self.refresh_admin()
            self.load_board()
        self.bg(lambda: op.login(self.cfg), done, "브라우저에서 Etsy 'Grant access(허용)'를 눌러 주세요 (5분 안에)...")

    def check_conn(self):
        def work():
            self.client = None
            c = self._client()
            c.token.pop("shop_name", None)
            return c.shop_name()

        def done(name):
            self._alerts.pop("conn_fail", None)
            self.refresh_status()
            self.refresh_admin()
            messagebox.showinfo("연결 확인", f"정상입니다. 상점: {name}")
        self.bg(work, done, "Etsy 연결 확인 중...")

    def clean_now(self):
        n = op.cleanup()
        self.refresh_admin()
        messagebox.showinfo("정리", f"보관 기간이 지난 파일 {n}개를 지웠습니다." if n else "지울 파일이 없습니다.")

    def data_check(self):
        def work():
            rs = self._client().unshipped_receipts()[:5]
            folder = os.path.join(op.BASE_DIR, "logs")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"data_check_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(op.mask(rs), f, ensure_ascii=False, indent=2)
            return path

        def done(path):
            messagebox.showinfo("데이터 확인 파일", f"{os.path.basename(path)}\n\n고객 정보를 가린 주문 데이터를 저장했습니다. "
                                                "폴더가 열리면 이 파일을 Claude에게 보여주세요.")
            open_path(os.path.dirname(path))
        self.bg(work, done, "주문 데이터 받는 중...")


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
