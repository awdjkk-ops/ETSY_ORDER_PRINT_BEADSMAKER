# -*- coding: utf-8 -*-
"""ETSY 통합 창 - '재고' 탭 (Etsy 판매분 → 카페24 재고 차감) + 연결 창 + 관리 탭의 카페24 카드"""
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import ttk, messagebox, simpledialog, filedialog

import datetime as dt
import os
import re
import threading

import backup as BK
import cafe24 as C
import orderprint as op
import premap as PM
import stock as S
import watch as W
import theme as T


def hint(parent, text, **grid):
    style = "PageHint.TLabel" if grid.pop("page", False) else "Hint.TLabel"
    lb = ttk.Label(parent, text=text, style=style, wraplength=grid.pop("wrap", 320), justify="left")
    lb.grid(**grid)
    return lb


class Tooltip:
    """마우스를 올리면 설명 말풍선"""

    def __init__(self, widget, text):
        self.w, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, _e=None):
        if self.tip:
            return
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        x = self.w.winfo_rootx() + 20
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.w)
        tw.wm_overrideredirect(True)
        tk.Label(tw, text=text, justify="left", background="#FFFDF2", foreground=T.INK, relief="solid", bd=1,
                 font=T.f(9), padx=10, pady=8, wraplength=640).pack()
        tw.update_idletasks()
        sw = tw.winfo_screenwidth()
        if x + tw.winfo_reqwidth() > sw:
            x = max(0, sw - tw.winfo_reqwidth() - 10)
        tw.wm_geometry(f"+{x}+{y}")

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def center_on(win, w, h):
    """창을 프로그램 창 가운데에 (화면 밖으로 나가지 않게)"""
    try:
        root = win.master.winfo_toplevel() if win.master else win
        root.update_idletasks()
        rx, ry, rw, rh = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
        x = rx + (rw - w) // 2
        y = max(ry + (rh - h) // 2, ry - 30)          # 창이 프로그램보다 커도 제목줄은 보이게
        win.geometry(f"{w}x{h}+{x}+{y}")
    except tk.TclError:
        win.geometry(f"{w}x{h}")


def hscroll(tree):
    """표에 가로 스크롤바를 붙이고 칸 너비를 마우스로 줄이거나 늘릴 수 있게.
    마지막 칸만 창 너비에 맞춰 늘어나고(처음 너비보다 좁아지지 않음), 나머지 칸은 끌어서 조절한 너비 그대로.
    칸 합이 표보다 넓으면 가로 스크롤바로 오른쪽을 볼 수 있음"""
    if getattr(tree, "_hs", False) or tree.winfo_manager() != "grid":
        return
    tree._hs = True
    master = tree.master
    info = {k: v for k, v in tree.grid_info().items() if k != "in"}
    vsb = None
    ys = str(tree.cget("yscrollcommand") or "").split()
    if ys:
        try:
            vsb = tree.nametowidget(ys[0])
        except (KeyError, tk.TclError):
            vsb = None
    master.update_idletasks()
    hs = ttk.Scrollbar(master, orient="horizontal", command=tree.xview)
    box = tk.Frame(master, width=120, height=tree.winfo_reqheight() + hs.winfo_reqheight(), bd=0, highlightthickness=0)
    box.grid(**info)
    box.grid_propagate(False)          # 칸 합이 넓어도 창(탭)이 같이 넓어지지 않게 — 넘치는 부분은 가로 스크롤
    box.rowconfigure(0, weight=1)
    box.columnconfigure(0, weight=1)
    tree.grid_forget()
    tree.grid(in_=box, row=0, column=0, sticky="nsew")
    tree.lift(box)
    hs.grid(in_=box, row=1, column=0, sticky="we")
    hs.lift(box)
    tree.configure(xscrollcommand=hs.set)
    if vsb is not None and vsb.winfo_manager() == "grid" and vsb.master is master:
        vsb.grid_forget()
        vsb.grid(in_=box, row=0, column=1, sticky="ns")
        vsb.lift(box)
    cols = list(tree["columns"])
    for c in cols:
        tree.column(c, stretch=False)
    if cols:
        last = cols[-1]
        tree.column(last, stretch=True, minwidth=int(tree.column(last, "width")))


def hscroll_all(root):
    """root 아래의 모든 표에 hscroll"""
    stack = [root]
    while stack:
        w = stack.pop()
        if isinstance(w, ttk.Treeview):
            hscroll(w)
        stack.extend(w.winfo_children())


def wide_combobox(cb, rows=20):
    """드롭다운 목록을 가장 긴 이름에 맞춰 넓게 열고(화면 밖이면 왼쪽으로), 고른 이름은 말풍선으로"""
    cb.configure(height=rows)

    def widen():
        try:
            pop = cb.tk.eval(f"ttk::combobox::PopdownWindow {cb}")
            lb = f"{pop}.f.l"
            fnt = tkfont.Font(cb, font=cb.tk.call(lb, "cget", "-font"))
            vals = cb.cget("values") or ()
            need = max((fnt.measure(str(v)) for v in vals), default=0) + 40
            cur_w = cb.winfo_width()
            if need <= cur_w:
                return
            h = int(cb.tk.call("winfo", "height", pop)) or 300
            x, y = cb.winfo_rootx(), cb.winfo_rooty() + cb.winfo_height()
            sw = cb.winfo_screenwidth()
            w = min(need, sw - 20)
            if x + w > sw - 10:
                x = max(10, sw - w - 10)
            cb.tk.call("wm", "geometry", pop, f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass
    cb.configure(postcommand=lambda: cb.after(15, widen))
    Tooltip(cb, lambda: cb.get() if len(cb.get()) > 30 else "")


def help_icon(parent, text, under=None):
    """동그라미 물음표 (?) — 마우스를 올리면 설명"""
    bg = under or T.CARD
    cv = tk.Canvas(parent, width=22, height=22, highlightthickness=0, bd=0, background=bg, cursor="question_arrow")
    cv.create_oval(2, 2, 20, 20, outline=T.MUTED, width=1.5)
    cv.create_text(11, 11, text="?", fill=T.MUTED, font=T.f(10, True))
    Tooltip(cv, text)
    return cv


def short_title(title, n=46):
    t = str(title or "")
    return t if len(t) <= n else t[:n - 1] + "…"


def opts_text(line):
    return ", ".join(f"{v['name']}: {v['value']}" for v in line["variations"]) or "(옵션 없음)"


STATUS_TEXT = {"상품 연결 필요": "● 연결 필요 (상품)", "옵션 연결 필요": "● 연결 필요 (옵션)",
               "품목 연결 필요": "● 연결 필요 (품목)", "확인 필요": "● 확인 필요 (상품이 바뀜)",
               "코드 다름": "⚠ 코드 다름"}


# ---------------------------------------------------------------- 머리글자 대응 (재고 탭·관리 탭·연결 창 공용)
def prefix_count(c24_prefix):
    """카페24 상품 이름 목록에서 이 머리글자로 시작하는 코드를 가진 상품 수 (None = 목록을 아직 안 받음)"""
    ps = C.load_products().get("products", [])
    if not ps:
        return None
    n = 0
    for p in ps:
        pc = C.norm_code(C.extract_code(p["name"])) or C.norm_code(p.get("custom"))
        if pc and C.split_code(pc)[0] == c24_prefix:
            n += 1
    return n


def add_prefix_rule(parent, etsy, c24):
    """Etsy 머리글자 = 카페24 머리글자 등록. 확인할 것이 있으면 물어보고, 등록하면 결과 문구를 돌려줌 (취소/잘못 입력 = None)"""
    a, b = C.norm_code(etsy), C.norm_code(c24)
    title = "머리글자 대응 추가"
    if not a or not b:
        messagebox.showinfo(title, "Etsy 머리글자와 카페24 머리글자를 둘 다 적어 주세요. (예: PD = PDT)", parent=parent)
        return None
    if not a.isalpha() or not b.isalpha():
        messagebox.showinfo(title, "머리글자는 영문자만 적어 주세요. (예: PD = PDT, 숫자는 빼고)\n"
                                   "하이픈·띄어쓰기 차이(CS-23 ↔ CS23)는 자동으로 맞추니 적지 않아도 됩니다.", parent=parent)
        return None
    if a == b:
        messagebox.showinfo(title, f"'{a}'는 양쪽이 같아서 따로 적을 필요가 없습니다.", parent=parent)
        return None
    rows = S.load_prefix()
    old = next((C.norm_code(r.get("cafe24")) for r in rows if C.norm_code(r.get("etsy")) == a), None)
    if old == b:
        messagebox.showinfo(title, f"이미 등록되어 있습니다: Etsy {a} = 카페24 {b}", parent=parent)
        return None
    n = prefix_count(b)
    ask = []
    if old:
        ask.append(f"Etsy '{a}'는 지금 카페24 '{old}'로 되어 있습니다. '{b}'로 바꿉니다.")
    if n == 0:
        ask.append(f"카페24 상품 이름 목록에 '{b}'로 시작하는 상품이 하나도 없습니다. 오타가 아닌지 확인해 주세요.")
    if ask and not messagebox.askyesno(title, "\n\n".join(ask) + "\n\n그래도 등록할까요?", parent=parent):
        return None
    rows = [r for r in rows if C.norm_code(r.get("etsy")) != a] + [{"etsy": a, "cafe24": b}]
    S.save_prefix(rows)
    op.log.info(f"[머리글자] Etsy {a} = 카페24 {b}" + (f" (전: {old})" if old else ""))
    cnt = "상품 이름 목록을 아직 안 받아서 개수는 모름" if n is None else f"카페24 '{b}' 상품 {n:,}개"
    return f"등록함: Etsy {a} = 카페24 {b}  ({cnt})"


def prefix_changed(app):
    """대응표가 바뀌면 열려 있는 화면들 새로 고침"""
    for fn in (getattr(app, "stock_admin_refresh", None), getattr(getattr(app, "stock_tab", None), "refresh_prefix", None)):
        if fn:
            try:
                fn()
            except Exception:
                pass


class PrefixWindow(tk.Toplevel):
    """머리글자 대응표 전체 보기·추가·삭제"""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.app = tab, tab.app
        self.title("머리글자 대응표")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 520, 560)
        self.minsize(460, 420)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        hint(body, "Etsy 상품명 앞 코드와 카페24 상품명 앞 코드의 영문 머리글자가 다른 경우만 적습니다.\n"
                   "예: Etsy PD-3338 ↔ 카페24 PDT3338 → PD = PDT\n"
                   "하이픈·띄어쓰기 차이(CS-23 ↔ CS23)는 자동으로 맞춥니다.",
             row=0, column=0, columnspan=2, sticky="w", pady=(0, 8), wrap=470, page=True)
        self.tree = ttk.Treeview(body, columns=("etsy", "c24", "n"), show="headings", selectmode="extended")
        for c, txt, w in (("etsy", "Etsy", 120), ("c24", "카페24", 120), ("n", "카페24 상품 수", 140)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self.pick)
        er = ttk.Frame(body, style="Page.TFrame")
        er.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(er, text="Etsy", style="Page.TLabel").pack(side="left")
        self.e1 = ttk.Entry(er, width=8)
        self.e1.pack(side="left", padx=(4, 0))
        ttk.Label(er, text=" = 카페24", style="Page.TLabel").pack(side="left")
        self.e2 = ttk.Entry(er, width=8)
        self.e2.pack(side="left", padx=(4, 0))
        self.e2.bind("<Return>", lambda e: self.add())
        ttk.Button(er, text="추가", command=self.add).pack(side="left", padx=6)
        ttk.Button(er, text="선택 삭제", command=self.delete).pack(side="left")
        self.msg = ttk.Label(body, text="", style="PageHint.TLabel")
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        hint(body, "줄을 두 번 누르면 위 입력칸으로 불러와 고칠 수 있습니다. 같은 Etsy 머리글자로 다시 추가하면 바뀝니다.",
             row=4, column=0, columnspan=2, sticky="w", pady=(2, 0), wrap=470, page=True)
        hscroll_all(self)
        self.fill()
        self.e1.focus_set()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for r in sorted(S.load_prefix(), key=lambda r: C.norm_code(r.get("etsy"))):
            k, b = C.norm_code(r.get("etsy")), C.norm_code(r.get("cafe24"))
            if k and not self.tree.exists(k):
                n = prefix_count(b)
                self.tree.insert("", "end", iid=k, values=(k, b, "목록 없음" if n is None else (f"{n:,}개" if n else "0개 (확인 필요)")))

    def pick(self, e=None):
        sel = self.tree.selection()
        if sel:
            a, b = self.tree.item(sel[0], "values")[:2]
            for w, v in ((self.e1, a), (self.e2, b)):
                w.delete(0, "end")
                w.insert(0, v)

    def add(self):
        res = add_prefix_rule(self, self.e1.get(), self.e2.get())
        if res:
            self.e1.delete(0, "end")
            self.e2.delete(0, "end")
            self.msg.configure(text="✓ " + res)
            self.fill()
            prefix_changed(self.app)
            self.e1.focus_set()

    def delete(self):
        sel = list(self.tree.selection())
        if not sel:
            return
        if not messagebox.askyesno("머리글자 대응 삭제", f"{', '.join(sel)} 대응을 지울까요?", parent=self):
            return
        S.save_prefix([r for r in S.load_prefix() if C.norm_code(r.get("etsy")) not in sel])
        op.log.info(f"[머리글자] 삭제 {', '.join(sel)}")
        self.msg.configure(text=f"지움: {', '.join(sel)}")
        self.fill()
        prefix_changed(self.app)


class StockTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12, style="Page.TFrame")
        self.app = app
        self.rows = []              # 차감 대기 줄 (계산 포함)
        self.stock = {}             # {(상품번호, 품목코드): 현재 재고}
        left = ttk.Frame(self, style="Page.TFrame")
        left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(self, style="Page.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ---- 왼쪽: 상태
        self.alert_fr = ttk.Frame(left, style="Page.TFrame")
        self.alert_fr.grid(row=0, column=0, sticky="we")
        sf = ttk.LabelFrame(left, text=" 지금 상태 ", padding=12)
        sf.grid(row=1, column=0, sticky="we")
        self.state_lb = ttk.Label(sf, text="", font=T.f(11, True), justify="left", wraplength=320)
        self.state_lb.grid(row=0, column=0, sticky="w")
        self.old_lb = ttk.Label(sf, text="", foreground=T.ORANGE, font=T.f(10, True), justify="left", wraplength=320)
        self.old_lb.grid(row=2, column=0, sticky="w", pady=(4, 0))
        hint(sf, "주문서를 인쇄하면 그 주문의 상품 줄이 '차감 대기'에 들어옵니다 (미리보기·위치 맞춤은 제외). "
                 "같은 상품 줄은 두 번 차감되지 않습니다.", row=1, column=0, sticky="w", pady=(4, 0))

        # ---- 차감하기
        af = ttk.LabelFrame(left, text=" 차감하기 ", padding=12)
        af.grid(row=2, column=0, sticky="we", pady=(10, 0))
        self.practice = tk.BooleanVar(value=bool(op.load_settings().get("stock_practice", True)))
        ttk.Checkbutton(af, text="연습 모드 (카페24 재고를 바꾸지 않고 계산만)", variable=self.practice,
                        style="Warn.TCheckbutton", command=self._save_practice).grid(row=0, column=0, sticky="w")
        hint(af, "처음 쓰는 분은 켜 두세요. 켜져 있으면 끝까지 해 봐도 카페24 재고는 바뀌지 않습니다.",
             row=1, column=0, sticky="w", pady=(2, 8))
        self.test_lb = ttk.Label(af, text="", foreground=T.ORANGE, wraplength=320, justify="left", font=T.f(9, True))
        self.test_lb.grid(row=8, column=0, sticky="w", pady=(6, 0))
        ttk.Label(af, text="① 뺄 줄 고르기", font=T.f(10, True)).grid(row=2, column=0, sticky="w")
        hint(af, "오른쪽 위 '출력 묶음'에서 묶음을 고르거나, 표에서 줄을 고르세요 (Ctrl·Shift+클릭, 끌기). "
                 "아무 줄도 안 고르면 지금 표에 보이는 줄 전체가 대상입니다.", row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Label(af, text="② 미리 보기", font=T.f(10, True)).grid(row=4, column=0, sticky="w")
        self.b_apply = ttk.Button(af, text="차감 미리 보기", style="Main.TButton", command=self.do_preview)
        self.b_apply.grid(row=5, column=0, sticky="we", pady=(2, 6))
        ttk.Label(af, text="③ 숫자 확인 후 빼기", font=T.f(10, True)).grid(row=6, column=0, sticky="w")
        self.go_hint = hint(af, "", row=7, column=0, sticky="w", pady=(0, 0))

        # ---- 결과
        rf = ttk.LabelFrame(left, text=" 마지막 결과 ", padding=10)
        rf.grid(row=3, column=0, sticky="we", pady=(10, 0))
        self.result = T.text_box(tk.Text(rf, width=44, height=10, wrap="word"))
        self.result.grid(row=0, column=0, sticky="we")
        self.result.tag_configure("bad", foreground=T.RED)
        self.result.tag_configure("muted", foreground=T.MUTED)
        self.result.configure(state="disabled")

        # ---- 오른쪽: 탭
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        sub = ttk.Notebook(right)
        sub.grid(row=0, column=0, sticky="nsew")
        self.sub = sub
        pf, hf, mf = ttk.Frame(sub, padding=10), ttk.Frame(sub, padding=10), ttk.Frame(sub, padding=10)
        sub.add(pf, text=" 차감 대기 ")
        sub.add(hf, text=" 차감 기록 ")
        sub.add(mf, text=" 연결표 ")
        xf = ttk.Frame(sub, padding=10)
        sub.add(xf, text=" 연결 안 함 ")
        sub.bind("<<NotebookTabChanged>>", lambda e: self.refresh_side())

        # --- 차감 대기
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(1, weight=1)
        top0 = ttk.Frame(pf)
        top0.grid(row=0, column=0, sticky="we", pady=(0, 6))
        topb = ttk.Frame(top0)                      # 0줄: 출력 묶음
        topb.pack(fill="x", pady=(0, 6))
        ttk.Label(topb, text="출력 묶음", font=T.f(10, True)).pack(side="left", padx=(0, 6))
        self.batch = tk.StringVar(value="")             # 처음에는 빈칸 → 묶음을 직접 골라야 줄이 보임 (모르고 전체를 빼지 않게)
        self.batch_cb = ttk.Combobox(topb, textvariable=self.batch, state="readonly", width=40)
        self.batch_cb.pack(side="left")
        self.batch_cb.bind("<<ComboboxSelected>>", lambda e: (self.pick_none(), self.fill(), self.refresh_side(light=True)))
        ttk.Button(topb, text="이 묶음 모두 선택", command=self.pick_all).pack(side="left", padx=6)
        ttk.Button(topb, text="이 묶음 사입 목록", command=self.batch_buy).pack(side="left")
        self.batch_lb = ttk.Label(topb, text="", style="Hint.TLabel")
        self.batch_lb.pack(side="left", padx=6)
        top = ttk.Frame(top0)                       # 1줄: 고르기·연결·목록 버튼
        top.pack(fill="x")
        top2 = ttk.Frame(top0)                      # 2줄: 머리글자 바로 추가
        top2.pack(fill="x", pady=(6, 0))
        ttk.Button(top, text="모두 선택", width=8, command=self.pick_all).pack(side="left")
        ttk.Button(top, text="선택 해제", width=8, command=self.pick_none).pack(side="left", padx=6)
        ttk.Button(top, text="자동 연결", width=8, style="Ghost.TButton", command=self.auto_connect).pack(side="left")
        ttk.Button(top, text="재고 부족 목록", width=11, command=self.show_shortage).pack(side="left", padx=(6, 0))
        self.b_manual = ttk.Button(top, text="직접 차감 목록", width=13, command=self.show_manual)
        self.b_manual.pack(side="left", padx=(6, 0))
        self.only_need = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="연결 필요만", variable=self.only_need, command=self.fill).pack(side="left", padx=10)
        ttk.Button(top, text="새로 고침", width=8, command=self.refresh).pack(side="right")
        ttk.Button(top2, text="차감 안 함 목록", command=lambda: SkippedWindow(self)).pack(side="right")
        pre = ttk.Frame(top2)
        pre.pack(side="left")
        ttk.Label(pre, text="머리글자", font=T.f(10, True)).pack(side="left", padx=(0, 6))
        ttk.Label(pre, text="Etsy").pack(side="left")
        self.pe1 = ttk.Entry(pre, width=6)
        self.pe1.pack(side="left", padx=(3, 0))
        ttk.Label(pre, text=" = 카페24").pack(side="left")
        self.pe2 = ttk.Entry(pre, width=6)
        self.pe2.pack(side="left", padx=(3, 0))
        self.pe1.bind("<Return>", lambda e: self.pe2.focus_set())
        self.pe2.bind("<Return>", lambda e: self.add_prefix_inline())
        ttk.Button(pre, text="추가", width=4, command=self.add_prefix_inline).pack(side="left", padx=(6, 0))
        self.b_prefix = ttk.Button(pre, text="대응표", width=9, style="Ghost.TButton", command=lambda: PrefixWindow(self))
        self.b_prefix.pack(side="left", padx=(6, 0))
        self.b_valmap = ttk.Button(pre, text="옵션 값 대응표", width=15, style="Ghost.TButton",
                                   command=lambda: ValueMapWindow(self))
        self.b_valmap.pack(side="left", padx=(24, 0))
        cols = (("when", "☐ 출력한 때", 96), ("oid", "주문번호", 88), ("item", "Etsy 상품", 170),
                ("opt", "Etsy 옵션", 140), ("qty", "수량", 38), ("c24", "카페24 상품", 170), ("to", "→ 카페24 품목", 140),
                ("n", "빼는 수", 50), ("stock", "재고", 80), ("st", "상태", 140), ("vendor", "업체명", 110))
        self.tree = ttk.Treeview(pf, columns=[c[0] for c in cols], show="headings", height=18, selectmode="extended")
        self._cols_all = [c[0] for c in cols]
        for c, txt, w in cols:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="center" if c in ("qty", "n", "stock") else "w",
                             stretch=c in ("item", "c24", "to"))
        self.tree.tag_configure("need", foreground=T.ORANGE)
        self.tree.tag_configure("low", foreground=T.RED)
        self.tree.tag_configure("partial", foreground=T.RED)
        self.tree.tag_configure("manual", foreground=T.BLUE)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(pf, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.heading("when", command=self.toggle_all)
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.refresh_side(light=True), self._show_detail(), self._head_mark()))
        self.empty_lb = ttk.Label(self.tree, text="", font=T.f(11, True), foreground=T.MUTED, background="white",
                                  justify="center")
        self.tree.bind("<Control-a>", lambda e: (self.pick_all(), "break")[1])
        self.tree.bind("<Double-1>", self.dbl)
        self.tree.bind("<Button-3>", self.menu_at)
        self.menu = tk.Menu(self, tearoff=0, font=T.f(10))
        hint(pf, "· 여러 줄 고르기: Ctrl·Shift+클릭 또는 마우스로 끌기 (Ctrl+A = 모두). 줄을 두 번 누르면 연결 창이 열립니다.\n"
                 "· 주황색 '연결 필요' 줄은 한 번 연결해 두면 다음부터 같은 상품·옵션은 자동으로 계산됩니다. [자동 연결]은 확실한 줄만 먼저 연결합니다.\n"
                 "· 오른쪽 클릭: 연결하기 · 이 줄 차감 안 함(취소 주문 등) · 다시 대기로.\n"
                 "· 빨간 재고는 차감하면 0 아래로 내려가는 품목입니다 (0으로 맞추고, 못 채운 주문 수량은 사입 목록에). "
                 "카페24 재고가 3,000개 이상인 품목은 '재고 풀어둠'이라 빼지 않습니다 (기준은 관리 탭).",
             row=3, column=0, sticky="w", pady=(6, 0), wrap=900)
        self.detail = ttk.Label(pf, text="", justify="left", wraplength=1100)   # 고른 줄의 전체 상품명
        self.detail.grid(row=2, column=0, sticky="w", pady=(6, 0))

        # --- 차감 기록
        hf.columnconfigure(0, weight=1)
        hf.rowconfigure(0, weight=1)
        self.hist = ttk.Treeview(hf, columns=("time", "n", "lines", "st"), show="headings", height=8, selectmode="browse")
        for c, txt, w in (("time", "차감한 때", 140), ("n", "품목", 60), ("lines", "주문 줄", 70), ("st", "상태", 300)):
            self.hist.heading(c, text=txt)
            self.hist.column(c, width=w, anchor="center" if c != "st" else "w")
        self.hist.grid(row=0, column=0, sticky="nsew")
        self.hist.bind("<<TreeviewSelect>>", lambda e: self.show_hist())
        hb = ttk.Frame(hf)
        hb.grid(row=1, column=0, sticky="we", pady=(6, 0))
        self.b_undo = ttk.Button(hb, text="선택한 기록 되돌리기", style="Stop.TButton", command=self.do_undo)
        self.b_undo.pack(side="right")
        ttk.Label(hb, text="되돌리면 그 기록에서 뺀 수만큼 카페24 재고에 다시 더하고, 주문 줄은 '차감 대기'로 돌아갑니다.",
                  style="Hint.TLabel").pack(side="left")
        self.hdetail = ttk.Treeview(hf, columns=("prod", "var", "ba"), show="headings", height=8)
        for c, txt, w in (("prod", "카페24 상품", 300), ("var", "품목", 220), ("ba", "재고 (전 → 후)", 330)):
            self.hdetail.heading(c, text=txt)
            self.hdetail.column(c, width=w, anchor="w" if c != "ba" else "center")
        self.hdetail.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        hf.rowconfigure(2, weight=1)

        # --- 연결표
        mf.columnconfigure(0, weight=1)
        mf.rowconfigure(2, weight=1)
        pre = ttk.LabelFrame(mf, text=" 미리 연결 (Etsy 상품 전체) ", padding=10)
        pre.grid(row=0, column=0, sticky="we")
        pre.columnconfigure(1, weight=1)
        self.pm_lb = ttk.Label(pre, text="", justify="left", font=T.f(10, True))
        self.pm_lb.grid(row=0, column=0, columnspan=2, sticky="w")
        pb = ttk.Frame(pre)
        pb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.b_pm = ttk.Button(pb, text="미리 연결 시작", style="Main.TButton", command=lambda: self.run_premap(False))
        self.b_pm.pack(side="left")
        self.b_pm_stop = ttk.Button(pb, text="멈추기", style="Stop.TButton", command=self.stop_premap)
        self.b_pm_stop.pack(side="left", padx=6)
        self.b_pm_stop.state(["disabled"])
        self.b_pm_redo = ttk.Button(pb, text="처음부터 다시", command=lambda: self.run_premap(True))
        self.b_pm_redo.pack(side="left")
        hint(pre, "Etsy에서 판매 중인 상품 전체와 옵션, 카페24 품목·재고를 받아서 확실한 것만 연결표에 넣습니다 "
                  "([자동 연결]과 같은 규칙, 재고 풀어둠 품목은 '안 뺌'으로 고정). 이미 연결한 것은 건드리지 않습니다.\n"
                  "상품이 많으면 시간이 걸립니다. 중간에 [멈추기]를 누르거나 프로그램을 꺼도, 다음에 [미리 연결 시작]을 누르면 이어서 합니다.",
             row=2, column=0, columnspan=2, sticky="w", pady=(6, 0), wrap=900)
        self._pm_stop = None

        fb = ttk.Frame(mf)
        fb.grid(row=1, column=0, sticky="we", pady=(10, 6))
        self.mview = tk.StringVar(value="map")
        ttk.Radiobutton(fb, text="연결된 상품", value="map", variable=self.mview, command=self.fill_map).pack(side="left")
        ttk.Radiobutton(fb, text="직접 연결이 필요한 상품", value="todo", variable=self.mview,
                        command=self.fill_map).pack(side="left", padx=(12, 0))
        self.rv_radio = ttk.Radiobutton(fb, text="확인 필요 (바뀐 상품)", value="review", variable=self.mview,
                                        command=self.fill_map)
        self.rv_radio.pack(side="left", padx=(12, 0))
        ttk.Radiobutton(fb, text="배수 확인", value="mult", variable=self.mview,
                        command=self.fill_map).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(fb, text="옵션 겹침", value="overlap", variable=self.mview,
                        command=self.fill_map).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(fb, text="코드 다름", value="code", variable=self.mview,
                        command=self.fill_map).pack(side="left", padx=(12, 0))
        help_icon(fb, "코드 다름: Etsy 상품 코드와 연결된 카페24 상품 코드가 다른 상품 (예: SI-172 ↔ SIL105). "
                      "머리글자 대응표·숫자 앞 0·끝 글자(OR·GD)는 무시하고 비교합니다. 확인 전까지 그 상품은 차감하지 않습니다.\n\n"
                      "배수 확인: 옵션이 여러 개인데 배수가 2 이상인 상품 (예: 2 pcs + 옵션 2개는 옵션마다 1이 맞음).\n\n옵션 겹침: 한 상품 안에서 잘못 연결됐을 수 있는 옵션을 찾습니다.\n"
                      "① Etsy 같은 값이 다른 카페24 품목에 연결됨 (예: First Letter: A → A, Second Letter: A → B)\n"
                      "② 카페24 품목 하나에 Etsy의 다른 값이 2개 이상 연결됨 (예: Silver·Gold → 무도금)\n"
                      "같은 값끼리 같은 품목(First·Second Letter: A → A)은 정상이라 나오지 않습니다.\n"
                      "재고 풀어둠·안 뺌, 직접 차감 칸과 '옵션 조합'·'옵션 상관없이' 방식 상품은 보지 않습니다.\n"
                      "맞게 연결된 상품은 [이대로 맞음]을 누르면 다음부터 안 나옵니다 (연결을 고치면 다시 확인).").pack(side="left", padx=(4, 0))
        self.msearch = ttk.Entry(fb, width=24)
        self.msearch.pack(side="right")
        self.msearch.bind("<KeyRelease>", lambda e: self.fill_map())
        ttk.Label(fb, text="찾기").pack(side="right", padx=(0, 6))
        self.mcount = ttk.Label(fb, text="", style="Hint.TLabel")
        self.mcount.pack(side="right", padx=(0, 16))

        self.mtree = ttk.Treeview(mf, columns=("etsy", "c24", "mode", "n", "why"), show="headings", height=12,
                                  selectmode="extended")
        for c, txt, w in (("etsy", "Etsy 상품", 300), ("c24", "카페24 상품", 240), ("mode", "방식", 100),
                          ("n", "연결된 옵션", 80), ("why", "메모", 280)):
            self.mtree.heading(c, text=txt)
            self.mtree.column(c, width=w, anchor="w" if c in ("etsy", "c24", "why") else "center", stretch=c in ("etsy", "why"))
        self.mtree.tag_configure("need", foreground=T.ORANGE)
        self.mtree.grid(row=2, column=0, sticky="nsew")
        msb = ttk.Scrollbar(mf, orient="vertical", command=self.mtree.yview)
        msb.grid(row=2, column=1, sticky="ns")
        self.mtree.configure(yscrollcommand=msb.set)
        self.mtree.bind("<Double-1>", lambda e: self.edit_map())
        mb = ttk.Frame(mf)
        mb.grid(row=3, column=0, sticky="we", pady=(6, 0))
        ttk.Button(mb, text="연결하기 / 고치기", command=self.edit_map).pack(side="left")
        ttk.Button(mb, text="연결 지우기", style="Stop.TButton", command=self.del_map).pack(side="left", padx=6)
        ttk.Button(mb, text="연결 안 함으로", command=self.exclude_sel).pack(side="left")
        self.b_ovok = ttk.Button(mb, text="이대로 맞음", command=self.overlap_ok_sel)

        # --- 연결 안 함
        xf.columnconfigure(0, weight=1)
        xf.rowconfigure(1, weight=1)
        ttk.Label(xf, text="카페24와 맞지 않거나 Etsy에서 내릴 상품처럼, 재고 차감에 쓰지 않을 Etsy 상품입니다.",
                  font=T.f(10, True)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.xtree = ttk.Treeview(xf, columns=("etsy", "lid", "time"), show="headings", height=14, selectmode="extended")
        for c, txt, w in (("etsy", "Etsy 상품", 560), ("lid", "Etsy 상품번호", 120), ("time", "표시한 날", 130)):
            self.xtree.heading(c, text=txt)
            self.xtree.column(c, width=w, anchor="w" if c == "etsy" else "center", stretch=c == "etsy")
        self.xtree.grid(row=1, column=0, sticky="nsew")
        xsb = ttk.Scrollbar(xf, orient="vertical", command=self.xtree.yview)
        xsb.grid(row=1, column=1, sticky="ns")
        self.xtree.configure(yscrollcommand=xsb.set)
        xb = ttk.Frame(xf)
        xb.grid(row=2, column=0, sticky="we", pady=(6, 0))
        ttk.Button(xb, text="다시 연결 대상으로", command=self.unexclude_sel).pack(side="left")
        ttk.Button(xb, text="목록 복사", style="Ghost.TButton", command=self.copy_excluded).pack(side="left", padx=6)
        self.xcount = ttk.Label(xb, text="", style="Hint.TLabel")
        self.xcount.pack(side="right")
        hint(xf, "· 연결표에서 상품을 고르고 [연결 안 함으로]를 누르면 여기로 옵니다 (여러 개 가능). 미리 연결에서 건너뛰고, "
                 "'직접 연결이 필요한 상품'에도 안 나옵니다.\n"
                 "· 이미 연결돼 있던 상품은 연결을 떼어서 보관합니다. [다시 연결 대상으로]를 누르면 연결도 그대로 돌아옵니다.\n"
                 "· 이 상품이 팔리면 차감 대기에 '연결 필요 (연결 안 함 상품)'으로 나옵니다 — [이 줄 차감 안 함]으로 빼면 됩니다.\n"
                 "· [목록 복사]: 고른 줄(안 고르면 전부)을 'Etsy 상품번호 ⇥ 상품명'으로 복사 — Etsy에서 내릴 상품 정리용.",
             row=3, column=0, sticky="w", pady=(6, 0), wrap=900)
        hint(mf, "연결된 상품: 잘못 연결했으면 고치거나 지우세요 (지우면 다음에 팔릴 때 '연결 필요'로 나옵니다).\n"
                 "직접 연결이 필요한 상품: 미리 연결에서 확실하지 않아 남겨 둔 상품입니다. 시간 날 때 두 번 눌러 연결해 두면 됩니다 "
                 "(주문이 들어오면 차감 대기에서 연결해도 됩니다).",
             row=5, column=0, sticky="w", pady=(6, 0), wrap=900)
        self.mdetail = ttk.Label(mf, text="", justify="left", wraplength=1100)   # 고른 줄의 메모 전체
        self.mdetail.grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.mtree.bind("<<TreeviewSelect>>", lambda e: self._map_detail())

        self.buttons = [self.b_apply, self.b_undo, self.b_pm, self.b_pm_redo]
        self._watching = False
        hscroll_all(self)
        self.refresh()
        self.after(20_000, self._watch_tick)

    # ------------------------------------------------------------ 목록
    def _save_practice(self):
        s = op.load_settings()
        s["stock_practice"] = bool(self.practice.get())
        op.save_settings(s)
        self._apply_texts()
        self.app.flash("연습 모드 켜짐 — 카페24 재고는 바뀌지 않습니다." if self.practice.get()
                       else "연습 모드 꺼짐 — 미리 보기 창의 [카페24에서 실제로 빼기]가 실제 재고를 바꿉니다.")

    def add_prefix_inline(self):
        res = add_prefix_rule(self, self.pe1.get(), self.pe2.get())
        if res:
            self.pe1.delete(0, "end")
            self.pe2.delete(0, "end")
            self.app.flash(res)
            prefix_changed(self.app)
            self.pe1.focus_set()

    def refresh_prefix(self):
        n = len(S.load_prefix())
        self.b_prefix.configure(text=f"대응표 ({n})")
        self.b_valmap.configure(text=f"옵션 값 대응표 ({len(S.load_valmap())})")

    def refresh(self):
        S.prune()
        self.rows = S.pending_rows()
        self._update_batches()
        self.fill()
        self.refresh_side()
        self.refresh_prefix()
        self._apply_texts()
        self.test_lb.configure(text="테스트 모드: 카페24 재고는 절대 바뀌지 않고 계산만 합니다 (연습 모드와 같음)."
                               if op.is_test() else "")

    def _apply_texts(self):
        if op.is_test():
            t = "테스트 모드: 미리 보기 창에서 계산만 해 볼 수 있습니다 (카페24 재고는 절대 안 바뀜)."
        elif self.practice.get():
            t = "연습 모드: 미리 보기 창의 [연습: 빼는 것처럼 해 보기]는 계산 결과만 보여 줍니다."
        else:
            t = "미리 보기 창의 [카페24에서 실제로 빼기]를 누르면 재고가 바뀝니다. 잘못 뺐으면 '차감 기록'에서 되돌릴 수 있습니다."
        if op.load_settings().get("deduct_on_print"):
            t += ("\n\n● 출력하면 바로 차감: 켜짐" + (" — 하지만 지금은 연습·테스트 모드라 바로 차감하지 않음"
                                                  if self.practice.get() or op.is_test() else
                                                  " (여기 남은 줄은 문제가 있었던 줄)"))
        self.go_hint.configure(text=t)

    @staticmethod
    def _batch_key(r):
        return (r.get("time") or "")[:16]

    def _update_batches(self):
        info = S.batches()
        pend = {}
        for r in self.rows:
            k = self._batch_key(r)
            pend[k] = pend.get(k, 0) + 1
        self._batches, self._batch_info = {}, {}
        for a in info:
            k = a["key"]
            lab = f"{k[5:10]} {k[11:16]} 출력 ({a['total']}줄)"
            if a["pending"] == 0:
                lab += " - 차감 완료!"
            elif a["pending"] < a["total"]:
                lab += f" - 남은 {a['pending']}줄"
            self._batches[lab] = k
            self._batch_info[k] = a
        vals = [f"전체 (남은 {len(self.rows)}줄)"] + list(self._batches)
        self.batch_cb.configure(values=vals)
        mode = getattr(self, "_batch_sel", "")             # 고르고 있던 것이 아직 있으면 그대로 (처음은 빈칸)
        if mode == "all":
            self.batch.set(vals[0])
        else:
            lab = next((lb for lb, k in self._batches.items() if k == mode), None) if mode else None
            self.batch.set(lab or "")
        n_open = sum(1 for a in info if a["pending"])
        self.batch_lb.configure(text=f"안 뺀 묶음 {n_open}개" if n_open else "")

    def _batch_mode(self):
        """'' = 아직 안 고름(빈칸) / 'all' = 전체 / 그 밖 = 묶음 키"""
        v = self.batch.get()
        if not v:
            mode = ""
        elif v.startswith("전체"):
            mode = "all"
        else:
            mode = getattr(self, "_batches", {}).get(v, "")
        self._batch_sel = mode
        return mode

    def _cur_batch_key(self):
        m = self._batch_mode()
        return m if m not in ("", "all") else None

    def _visible(self, r):
        if self.only_need.get() and not r["why"]:
            return False
        m = self._batch_mode()
        if m == "":
            return False
        return m == "all" or self._batch_key(r) == m

    def _show_empty(self):
        m = self._batch_mode()
        if self.tree.get_children():
            self.empty_lb.place_forget()
            return
        if m == "":
            t = "위 '출력 묶음'에서 묶음을 고르세요.\n(안 뺀 묶음만 보려면 묶음을, 전부 보려면 '전체')"
        elif m == "all":
            t = "차감 대기 줄이 없습니다." if not self.only_need.get() else "연결이 필요한 줄이 없습니다."
        elif self._batch_info.get(m, {}).get("pending") == 0:
            t = "이 묶음은 모두 차감했습니다.\n[이 묶음 사입 목록]으로 사입할 품목을 볼 수 있습니다."
        else:
            t = "이 묶음에 보일 줄이 없습니다."
        self.empty_lb.configure(text=t)
        self.empty_lb.place(relx=0.5, rely=0.35, anchor="center")

    def toggle_all(self):
        """표 맨 앞 제목 ☐/☑: 지금 보이는 줄 전체 선택 ↔ 해제"""
        kids = self.tree.get_children()
        if kids and set(self.tree.selection()) >= set(kids):
            self.pick_none()
        else:
            self.pick_all()
        self._head_mark()

    def _head_mark(self):
        kids = self.tree.get_children()
        on = bool(kids) and set(self.tree.selection()) >= set(kids)
        self.tree.heading("when", text=("☑" if on else "☐") + " 출력한 때")

    def batch_buy(self):
        m = self._batch_mode()
        if m in ("", "all"):
            messagebox.showinfo("이 묶음 사입 목록", "위 '출력 묶음'에서 묶음 하나를 먼저 골라 주세요.")
            return
        items = S.batch_shortage(m)
        if not items:
            a = self._batch_info.get(m, {})
            messagebox.showinfo("이 묶음 사입 목록", "이 묶음은 사입할 품목이 없습니다." + (
                f"\n(아직 안 뺀 줄 {a['pending']}개는 차감한 뒤에 나옵니다)" if a.get("pending") else ""))
            return
        ShortageWindow(self, items, False, when=f"{m[5:10]} {m[11:16]} 출력 묶음")

    def fill(self):
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        vshow = bool(op.load_settings().get("vendor_show"))
        self.tree.configure(displaycolumns=self._cols_all if vshow else [c for c in self._cols_all if c != "vendor"])
        for r in self.rows:
            if not self._visible(r):
                continue
            to = " · ".join([f"{lab or code}" for _p, code, lab, _n in r["ded"]] +
                            [f"✋{lab}" for _p, _c, lab, _n in r.get("manual", [])])
            n = " + ".join([str(x[3]) for x in r["ded"]] + [f"✋{x[3]}" for x in r.get("manual", [])])
            stock_txt, low = "", False
            if r["ded"]:
                parts = []
                for pno, code, _l, nn in r["ded"]:
                    cur = self.stock.get((pno, code), "")
                    if cur is None:
                        parts.append("관리 안 함")
                    elif cur != "" and S.is_plenty(cur):
                        parts.append(f"{cur:,} (재고 풀어둠)")
                    elif cur != "":
                        parts.append(f"{cur}→{max(cur - nn, 0)}")
                        low = low or cur - nn < 0
                stock_txt = ", ".join(parts)
            fixed_low = False
            if r.get("fixed"):                         # 재고 풀어둠·안 뺌 고정 품목도 재고를 보여 줌
                fp = []
                for pno, code, _l, nn in r["fixed"]:
                    cur = self.stock.get((pno, code), "")
                    if isinstance(cur, int):
                        if S.is_plenty(cur):
                            fp.append(f"{cur:,} (재고 풀어둠)")
                        else:
                            fp.append(f"⚠ {cur:,}")
                            fixed_low = True
                stock_txt = ", ".join(x for x in [stock_txt] + fp if x)
            elif not r["ded"] and not r.get("manual") and not r["why"]:
                stock_txt = stock_txt or "품목 미지정"
            if r["status"] == "partial":
                st, tag = "⚠ 일부만 차감됨 → 기록 되돌리기", "partial"
            elif r.get("short") and not r["why"]:
                st, tag = f"⚠ 재고 부족 (필요 {r['short']['need']} / 재고 {r['short']['stock']})", "partial"
            elif r["why"] == "코드 다름" and r.get("code"):
                st, tag = f"⚠ 코드 다름 ({r['code'][0]} ↔ {r['code'][1]})", "need"
            elif r["why"]:
                st, tag = STATUS_TEXT.get(r["why"], r["why"]), "need"
                if r.get("excluded"):
                    st = "● 연결 필요 (연결 안 함 상품)"
            elif r.get("manual"):
                st, tag = ("✋ 직접 차감" if not r["ded"] else "○ 연결됨 + ✋ 직접 차감 일부"), "manual"
            elif fixed_low:
                st, tag = "⚠ 재고 풀어둠 고정인데 재고 줄어듦", "need"
                if not r["ded"]:
                    to = "(재고 풀어둠·안 뺌으로 고정)"
            elif not r["ded"]:
                st, tag = "○ 연결됨 · 안 뺌(재고 풀어둠)", ""
                to = "(재고 풀어둠·안 뺌으로 고정)"
            else:
                st, tag = "○ 연결됨", ("low" if low else "")
            self.tree.insert("", "end", iid=r["tid"], tags=(tag,) if tag else (), values=(
                r["time"][5:16], r["receipt_id"], short_title(r["title"]), opts_text(r),
                r["quantity"], short_title(r.get("product_name", ""), 40) if not r.get("why", "").startswith("상품") else "",
                to or ("" if r["why"] else ""), n, stock_txt, st, S.row_vendor(r) if vshow else ""))
        self._show_empty()
        self._head_mark()
        keep = [t for t in keep if self.tree.exists(t)]
        if keep:
            self.tree.selection_set(keep)

    def _show_detail(self):
        sel = self.tree.selection()
        r = self._row(sel[0]) if sel else None
        if not r:
            self.detail.configure(text="")
            return
        more = f"   (외 {len(sel) - 1}줄 선택)" if len(sel) > 1 else ""
        self.detail.configure(text=f"Etsy:    {r['title']}{more}\n카페24:  {r.get('product_name') or '(연결 안 됨)'}")

    def refresh_side(self, light=False):
        ok = sum(1 for r in self.rows if not r["why"] and r["status"] == "pending")
        need = sum(1 for r in self.rows if r["why"] and r["why"] != "코드 다름")
        codebad = sum(1 for r in self.rows if r["why"] == "코드 다름")
        part = sum(1 for r in self.rows if r["status"] == "partial")
        cnt = {}
        for r in self.rows:
            k = self._batch_key(r)
            cnt[k] = cnt.get(k, 0) + 1
        bl = "".join(f"\n   {k[5:10]} {k[11:16]} 출력  {n}줄" for k, n in sorted(cnt.items(), reverse=True)[:6])
        if len(cnt) > 6:
            bl += f"\n   … 외 묶음 {len(cnt) - 6}개"
        today = dt.date.today().isoformat()
        old = sum(n for k, n in cnt.items() if k[:10] < today)
        self.state_lb.configure(text=f"차감 대기  {len(self.rows)}줄\n   연결됨 {ok}줄 · 연결 필요 {need}줄"
                                     + (f"\n   ⚠ 코드가 다른 줄 {codebad}줄 (확인 전까지 안 뺌)" if codebad else "")
                                     + (f"\n   일부만 차감됨 {part}줄" if part else "")
                                     + (bl if len(cnt) > 1 else "")
                                     + self._sel_text())
        self.old_lb.configure(text=f"⚠ 어제 이전 출력분 {old}줄 아직 안 뺌" if old else "")
        if light:
            return
        for w in self.alert_fr.winfo_children():
            w.destroy()
        st, txt = C.token_status()
        if st == "bad":
            T.alert(self.alert_fr, f"[확인 필요] 카페24 {txt} — 관리 탭 '카페24 연결'").pack(fill="x", pady=(0, 6))
        nrev = len(W.review_items())
        if nrev:
            T.alert(self.alert_fr, f"[확인 필요] 바뀐 상품 {nrev}개 — 확인 전까지 차감하지 않습니다. "
                                   "'연결표 → 확인 필요'에서 확인해 주세요.").pack(fill="x", pady=(0, 6))
        nman = len([x for x in S.load_queue() if x.get("status") == "manual"])
        self.b_manual.configure(text=f"직접 차감 목록 ({nman})" if nman else "직접 차감 목록")
        if nman:
            T.alert(self.alert_fr, f"[할 일] 카페24에서 직접 빼야 하는 줄 {nman}개 — [직접 차감 목록]에서 처리해 주세요."
                    ).pack(fill="x", pady=(0, 6))
        self.fill_hist()
        self.fill_map()
        self.fill_excluded()

    def _sel_text(self):
        sel = set(self.tree.selection()) if hasattr(self, "tree") else set()
        ok = sum(1 for r in self.rows if r["tid"] in sel and not r["why"] and r["status"] == "pending")
        return f"\n선택한 줄  {len(sel)}줄" + (f" (차감 가능 {ok}줄)" if len(sel) != ok else "")

    def pick_all(self):
        self.tree.selection_set(self.tree.get_children())

    def pick_none(self):
        self.tree.selection_remove(self.tree.selection())

    def _row(self, tid):
        return next((x for x in self.rows if x["tid"] == tid), None)

    def dbl(self, e):
        if self.tree.identify_region(e.x, e.y) == "heading":
            return
        tid = self.tree.identify_row(e.y)
        r = self._row(tid)
        if not r:
            return
        col = self.tree.identify_column(e.x)                # 주문번호 칸 → 복사 (Etsy에서 바로 검색)
        disp = list(self.tree["displaycolumns"])
        if disp == ["#all"]:
            disp = list(self.tree["columns"])
        try:
            name = disp[int(col[1:]) - 1]
        except (ValueError, IndexError):
            name = ""
        if name == "oid":
            self.clipboard_clear()
            self.clipboard_append(str(r["receipt_id"]))
            self.app.flash(f"주문번호 {r['receipt_id']} 복사함")
            return "break"
        self.connect(r)

    def menu_at(self, e):
        row = self.tree.identify_row(e.y)
        r = self._row(row)
        if not r:
            return
        sel = [s for s in self.tree.selection()] or [row]
        if row not in sel:
            sel = [row]
            self.tree.selection_set(row)
        self.menu.delete(0, "end")
        self.menu.add_command(label="확인하기 (바뀐 상품)" if r.get("why") == "확인 필요" else "연결하기 / 연결 고치기",
                              command=lambda: self.connect(r))
        if r.get("why") == "코드 다름":
            bad = [x for x in (self._row(t) for t in sel) if x and x.get("why") == "코드 다름"]
            self.menu.add_command(label=f"코드는 다르지만 이대로 맞음 ({len(bad)}줄)", command=lambda: self.code_ok_rows(bad))
        self.menu.add_separator()
        self.menu.add_command(label=f"이 줄 차감 안 함 ({len(sel)}줄 · 취소 주문 등)", command=lambda: self.skip(sel))
        self.menu.tk_popup(e.x_root, e.y_root)

    def code_ok_rows(self, rows):
        if not rows:
            return
        names = "\n".join(f"· {r['code'][0]} ↔ {r['code'][1]}  {short_title(r.get('product_name', ''), 30)}"
                          for r in rows[:8] if r.get("code"))
        if not messagebox.askyesno("코드 다름", f"고른 {len(rows)}줄은 코드는 다르지만 연결이 맞나요?\n\n{names}\n\n"
                                   "'예'를 누르면 다음부터 이 상품은 경고 없이 차감합니다 (연결을 바꾸면 다시 확인)."):
            return
        S.code_ok([(r["listing_id"], r["title"]) for r in rows])
        self.refresh()
        self.app.flash(f"{len(rows)}줄을 '이대로 맞음'으로 표시했습니다.")

    def skip(self, tids):
        if not messagebox.askyesno("차감 안 함", f"{len(tids)}줄을 차감 대기에서 뺄까요?\n(카페24 재고는 바뀌지 않습니다)\n\n"
                                   "※ 연습 모드여도 차감 대기에서 실제로 빠집니다. 같은 주문을 다시 넣어도 들어가지 않으니, "
                                   "되돌리려면 차감 대기 위 [차감 안 함 목록]에서 되돌리세요."):
            return
        S.set_status(set(tids), "skipped")
        self.refresh()

    # ------------------------------------------------------------ 연결
    def connect(self, r):
        if r.get("why") == "확인 필요":
            ReviewDialog(self, r["listing_id"])
            return
        ConnectDialog(self, r, on_saved=self.after_connect)

    def after_connect(self):
        self.refresh()
        self.app.flash("연결을 저장했습니다. 같은 상품·옵션은 다음부터 자동으로 계산됩니다.")

    # ------------------------------------------------------------ 재고 불러오기·차감
    def do_preview(self):
        """② 차감 미리 보기: 고른 줄, 안 골랐으면 지금 표에 보이는 줄 전체"""
        sel = set(self.tree.selection())
        if not sel and self._batch_mode() == "":
            messagebox.showinfo("차감 미리 보기", "위 '출력 묶음'에서 묶음(또는 '전체')을 먼저 고르거나, 표에서 뺄 줄을 골라 주세요.")
            return
        pool = [r for r in self.rows if r["tid"] in sel] if sel else [r for r in self.rows if self._visible(r)]
        ok = [r for r in pool if not r["why"] and r["status"] == "pending"]
        if not ok:
            messagebox.showinfo("차감 미리 보기", "뺄 수 있는 줄이 없습니다.\n연결이 필요한 줄은 먼저 연결해 주세요.")
            return
        k = self._cur_batch_key()
        self._target = (f"고른 줄 {len(ok)}개" if sel else
                        (f"{k[5:10]} {k[11:16]} 출력 묶음 {len(ok)}줄" if k else f"차감 대기 전체 {len(ok)}줄"))
        self.do_apply(tids={r["tid"] for r in pool})

    def _chosen(self, all_=False):
        """차감할 줄: 전체 또는 선택한 줄 중 연결된 것. 돌려줌: (줄 목록, 빠진 줄 수)"""
        sel = None if all_ else set(self.tree.selection())
        pool = [r for r in self.rows if sel is None or r["tid"] in sel]
        ok = [r for r in pool if not r["why"] and r["status"] == "pending"]
        return ok, len(pool) - len(ok)

    def load_stock(self, tids=None, checked=None):
        if tids is not None:
            rows = [r for r in self.rows if r["tid"] in tids and not r["why"] and r["status"] == "pending"]
        else:
            rows, _x = self._chosen()
            if not rows:
                rows, _x = self._chosen(all_=True)
        if not rows and checked is None:
            messagebox.showinfo("현재 재고", "연결된 줄이 없습니다.")
            return
        if rows and checked is None:
            keep = {r["tid"] for r in rows}
            self._precheck(rows, lambda res: self.load_stock(tids=keep, checked=res))
            return
        if checked and checked.get("flagged"):
            self.app.flash(f"바뀐 상품을 찾아 {checked['flagged']}줄은 '확인 필요'로 바꿨습니다.")
        if not rows:
            return
        agg = S.aggregate(rows)
        if checked and checked.get("stock") is not None:          # 차감 전 확인에서 이미 받음
            self.after(10, lambda: done(checked["stock"]))
            nlow = len(S.fixed_low(rows, checked["stock"]))
            if nlow:
                self.app.flash(f"⚠ 재고 풀어둠·안 뺌으로 고정된 품목 중 재고가 기준보다 적은 것 {nlow}개 — 재고 칸의 ⚠ 표시를 확인하세요.")

        def work(prog):
            return S.current_stock(agg, lambda d, t: prog(d, t, "카페24 현재 재고 불러오는 중"))

        def done(res):
            self.stock.update(res)
            self.fill()
            low = [k for k, a in agg.items() if res.get(k) is not None and not S.is_plenty(res[k]) and res[k] - a["n"] < 0]

            def after(k, a):
                v = res.get(k)
                if v is None:
                    return "(관리 안 함)"
                if S.is_plenty(v):
                    return f"{v}  (재고 풀어둠 — 안 뺌)"
                return str(max(v - a["n"], 0))
            self._show_result([f"{a['product_name'][:24]} · {a['label']}:  {res.get(k)} → {after(k, a)}"
                               + ("  ← 재고 부족" if k in low else "") for k, a in agg.items()],
                              title=f"현재 재고 (품목 {len(agg)}개)")
        if checked and checked.get("stock") is not None:
            return
        self.app.bg(work, done, "카페24 현재 재고 불러오는 중...", progress=True)

    def do_apply(self, all_=False, tids=None, checked=None):
        if tids is not None:
            rows = [r for r in self.rows if r["tid"] in tids and not r["why"] and r["status"] == "pending"]
            left_out = len(tids) - len(rows)
        else:
            rows, left_out = self._chosen(all_)
        if rows and checked is None:            # 차감 직전: 그 주문의 상품이 바뀌지 않았는지 먼저 확인
            keep = {r["tid"] for r in rows}
            self._precheck(rows, lambda res: self.do_apply(tids=keep, checked=res))
            return
        stock_now = (checked or {}).get("stock")
        if rows and stock_now is not None and not checked.get("fixed_done"):
            lowf = S.fixed_low(rows, stock_now)
            if lowf:
                lines = "\n".join(f"· {x['product_name'][:24]} · {S.opt_values(x['label']) or x['label']}  재고 {x['stock']:,} · 이번 주문 {x['need']}"
                                  for x in lowf[:12]) + (f"\n… 외 {len(lowf) - 12}개" if len(lowf) > 12 else "")
                ans = messagebox.askyesnocancel(
                    "재고 풀어둠 고정 품목 확인",
                    f"재고 풀어둠·안 뺌으로 고정된 품목 중 재고가 {S.plenty_limit():,}개보다 적은 것이 {len(lowf)}개 있습니다.\n\n{lines}\n\n"
                    "· 예: 이번부터 빼기 (고정을 풀고 앞으로도 정상으로 뺌)\n"
                    "· 아니요: 이번만 안 빼기 (고정 그대로, 다음에 또 물어봄)\n"
                    "· 취소: 차감하지 않음", icon="warning")
                if ans is None:
                    return
                if ans:
                    done_, left = S.unfix(lowf)
                    if left:
                        messagebox.showinfo("재고 풀어둠 고정 풀기", "아래 품목은 배수를 자동으로 정하지 못해 고정을 그대로 두었습니다. "
                                                         "연결 창에서 직접 고쳐 주세요.\n\n" + "\n".join(left[:12]))
                    self.refresh()
                    self.app.flash(f"재고 풀어둠 고정 {len(done_)}개를 풀었습니다 — 이번부터 뺍니다.")
                keep = {r["tid"] for r in rows}
                self.do_apply(tids=keep, checked={**checked, "fixed_done": True})
                return
        if rows and stock_now is not None and not checked.get("changes_seen"):
            ch = S.print_changes(rows, stock_now)
            if ch:
                keep = {r["tid"] for r in rows}
                ChangesWindow(self, ch, lambda: self.do_apply(tids=keep, checked={**checked, "fixed_done": True,
                                                                                    "changes_seen": True}))
                return
        if not rows:
            messagebox.showinfo("재고 차감", "연결된 줄이 없습니다." if all_ else
                                "오른쪽 목록에서 차감할 줄을 골라 주세요 (Ctrl·Shift+클릭, 끌기, Ctrl+A).\n"
                                "연결이 필요한 줄은 먼저 연결해야 합니다.")
            return
        practice = bool(self.practice.get()) or op.is_test()
        notes = []
        if left_out:
            notes.append(f"연결이 필요한 {left_out}줄은 빼고 계산했습니다.")
        if checked and checked.get("flagged"):
            notes.append(f"차감 전 확인에서 바뀐 상품을 찾아 {checked['flagged']}줄은 뺐습니다 ('연결표 → 확인 필요').")
        if checked and checked.get("err"):
            notes.append(f"⚠ 차감 전 확인(Etsy·카페24 상품이 바뀌었는지)을 못 했습니다: {checked['err'][:120]}")
        PreviewWindow(self, rows, stock_now or {}, practice, notes, getattr(self, "_target", "") or f"주문 줄 {len(rows)}개",
                      lambda: self._run_apply(rows, practice))
        self._target = ""

    def _run_apply(self, rows, practice):
        def work(prog):
            try:
                return S.apply(rows, practice, lambda d, t: prog(d, t, "카페24 재고 확인·차감 중"))
            except Exception:
                self.app.call(self.refresh)          # 중간에 멈춰도 '차감함/일부만' 상태를 화면에 반영
                raise

        def done(res):
            res, short, manual = res
            n_items = sum(1 for x in res if x["label"])
            title = "[연습] 계산 결과 — 카페24 재고는 바뀌지 않았습니다" if practice else f"차감 완료 — 품목 {n_items}개"
            if short:
                title += f" · 재고 부족 {len(short)}개는 0으로 맞추고 사입 목록에"
            self._show_result([(f"{x['product_name'][:24]} · {x['label']}:  {x['before']} → {x['after']}"
                                + (f"  ({x['note']})" if x["note"] else "")) if x["label"]
                               else f"{x['product_name']}: {x['note']}" for x in res], title=title)
            if not practice:
                self.stock = {}
            self.refresh()
            self.app.flash(title)
            if short:
                ShortageWindow(self, short, practice)
            if manual:
                ManualWindow(self, manual, practice)

        self.app.bg(work, done, "카페24 재고 확인·차감 중...", progress=True)

    def _precheck(self, rows, cont):
        """차감할 주문의 Etsy 상품·카페24 상품이 연결할 때와 같은지 확인 (몇 초) → cont({flagged, err})"""
        lids = sorted({r["listing_id"] for r in rows})
        cfg = self.app.cfg
        if not cfg:
            cont({"err": "Etsy 설정(config.ini)을 읽지 못함"})
            return
        tids = {r["tid"] for r in rows}

        keys = {(p, c) for r in rows for p, c, _l, _n in r.get("ded", []) + r.get("fixed", [])}

        def work(prog):
            res = {}
            try:
                res["curs"] = W.check_some(op.EtsyClient(cfg), lids, prog)
            except Exception as e:                       # 확인을 못 해도 차감 여부는 사람이 정하게
                op.log.warning(f"[차감 전 확인] 실패: {e}")
                res["err"] = str(e)
            try:                                         # 지금 재고 (재고 풀어둠 고정 품목 포함, 100개씩 한꺼번에)
                res["stock"] = C.stock_lookup({k[0] for k in keys}, wanted=keys) if keys else {}
            except Exception as e:
                op.log.warning(f"[차감 전 확인] 재고 못 받음: {e}")
            return res

        def done(res):
            if "curs" in res:
                W.merge(res["curs"])
            if res.get("stock"):
                self.stock.update(res["stock"])
            self.refresh()
            flagged = sum(1 for r in self.rows if r["tid"] in tids and r["why"] == "확인 필요")
            out = {"flagged": flagged, "stock": res.get("stock")}
            if res.get("err"):
                out["err"] = res["err"]
            cont(out)
        self.app.bg(work, done, "차감 전 확인: Etsy·카페24 상품이 바뀌지 않았는지 보는 중...", progress=True)

    # ------------------------------------------------------------ 바뀐 상품 확인 (정해 둔 시간)
    def _watch_tick(self):
        try:
            if not self._watching and not self.app.busy and W.due():
                self.run_watch(manual=False)
        except Exception as e:
            op.log.warning(f"[바뀐 상품 확인] 시간 확인 오류: {e}")
        self.after(60_000, self._watch_tick)

    def run_watch(self, manual=True):
        if self._watching:
            if manual:
                messagebox.showinfo("바뀐 상품 확인", "지금 확인하는 중입니다. 끝나면 알려 드립니다.")
            return
        if C.token_status()[0] == "bad" or not self.app.cfg:
            if manual:
                messagebox.showwarning("바뀐 상품 확인", "Etsy·카페24 연결을 먼저 확인해 주세요 (관리 탭).")
            return
        cfg = self.app.cfg
        self._watching = True

        def work(prog=None):
            try:
                return W.check_all(op.EtsyClient(cfg), prog or (lambda *a, **k: None))
            except Exception as e:
                op.log.warning(f"[바뀐 상품 확인] 실패: {e}")
                return {"__err__": str(e)}

        def done(res):
            self._watching = False
            if "__err__" in res:
                st = W.load_state()
                st.update({"last": W.now_s(), "note": f"실패: {res['__err__'][:150]}"})
                W.save_state(st)
                self.app.flash("바뀐 상품 확인을 못 했습니다 — 관리 탭 '바뀐 상품 확인'에서 내용을 보세요.")
                if manual:
                    messagebox.showwarning("바뀐 상품 확인", res["__err__"][:400])
            else:
                flagged, new, note = W.merge_all(res)
                total = len(W.review_items())
                self.app.flash(f"바뀐 상품 확인 끝 — 확인 필요 {total}개" + (f" (새로 {flagged}개)" if flagged else "")
                               + (f" · 새 Etsy 상품 {new}개" if new else ""))
                if manual:
                    messagebox.showinfo("바뀐 상품 확인", note + (f"\n\n확인이 필요한 상품: {total}개 (재고 탭 → 연결표 → 확인 필요)"
                                                           if total else "\n\n확인이 필요한 상품이 없습니다."))
            self.refresh()
            cb = getattr(self.app, "watch_admin_refresh", None)
            if cb:
                cb()
        if manual:
            self.app.bg(work, done, "바뀐 상품 확인 중...", progress=True)
        else:
            self.app.bg(lambda: work(), done, busy=False)

    def show_manual(self):
        items = S.manual_waiting()
        if not items:
            messagebox.showinfo("직접 차감 목록", "직접 차감할 줄이 없습니다.\n"
                                              "(연결 창에서 '직접 차감'으로 표시한 옵션이 팔리면, 차감할 때 여기로 옵니다)")
            return
        ManualWindow(self, items, practice=False)

    def show_shortage(self):
        d = S.load_shortage()
        if not d.get("items"):
            messagebox.showinfo("재고 부족 목록", "마지막 차감(또는 계산)에서 재고가 모자란 품목이 없었습니다.")
            return
        ShortageWindow(self, d["items"], d.get("practice", False), when=d.get("time", ""))

    def auto_connect(self):
        need = [r for r in self.rows if r["why"] and r["status"] == "pending"]
        if not need:
            messagebox.showinfo("자동 연결", "연결이 필요한 줄이 없습니다.")
            return
        if C.token_status()[0] == "bad":
            messagebox.showwarning("자동 연결", "카페24 연결이 필요합니다 (관리 탭 → 카페24 연결).")
            return
        if not messagebox.askyesno("자동 연결", f"'연결 필요' {len(need)}줄 중 확실한 것만 자동으로 연결합니다.\n\n"
                                              "확실하다 =\n"
                                              "· 카페24 상품이 코드로 딱 하나 (SI-022 = SIL22처럼 0·끝 글자 차이는 무시)\n"
                                              "· Etsy 옵션 값과 같은 카페24 품목이 딱 하나\n"
                                              "· 상품명 단위로 배수가 딱 나눠떨어짐\n\n"
                                              "애매한 줄은 그대로 두니 직접 연결해 주세요. 연결한 내용은 '연결표'에서 고칠 수 있습니다."):
            return

        def work(prog):
            return S.auto_connect(need, lambda d, t: prog(d, t, "자동 연결 중"))

        def done(res):
            ok, skip = res
            self._show_result([f"✓ {x}" for x in ok] + ([""] if ok and skip else []) +
                              [f"· 직접 연결: {x}" for x in skip],
                              title=f"자동 연결 {len(ok)}개 · 직접 연결 필요 {len(skip)}줄")
            self.refresh()
            self.app.flash(f"자동 연결 {len(ok)}개 완료" + (f" · {len(skip)}줄은 직접 연결해 주세요 (마지막 결과 참고)" if skip else ""))
        self.app.bg(work, done, "자동 연결 중...", progress=True)

    def _show_result(self, lines, title=""):
        t = self.result
        t.configure(state="normal")
        t.delete("1.0", "end")
        if title:
            t.insert("end", title + "\n", "muted")
        for ln in lines:
            t.insert("end", ln + "\n", "bad" if ("부족" in ln or "실패" in ln) else ())
        t.configure(state="disabled")

    # ------------------------------------------------------------ 기록·되돌리기
    def fill_hist(self):
        self.hist.delete(*self.hist.get_children())
        for h in reversed(S.load_history()):
            kept = len(h.get("kept", []))
            if h.get("undone"):
                st = f"되돌림 {h['undone'][5:16]}"
            elif not h["changes"]:
                st = f"재고 풀어둠만 — 안 뺌 ({kept}개)"
            else:
                st = "차감함" + (f" · 재고 풀어둠 {kept}개 안 뺌" if kept else "")
            self.hist.insert("", "end", iid=h["id"], values=(h["time"][:16], len(h["changes"]) + kept,
                                                             len(h["tids"]) + len(h.get("kept_tids", [])), st))

    def show_hist(self):
        self.hdetail.delete(*self.hdetail.get_children())
        sel = self.hist.selection()
        h = next((x for x in S.load_history() if sel and x["id"] == sel[0]), None)
        if not h:
            return
        for c in h["changes"]:
            self.hdetail.insert("", "end", values=(c.get("product_name", "") or c["product_no"], c["label"],
                                                   f"{c['before']} → {c['after']}"))
        for k in h.get("kept", []):
            self.hdetail.insert("", "end", values=(k.get("product_name", ""), k.get("label", ""),
                                                   f"재고 풀어둠 — 안 뺌 ({k.get('n', 0)}개 · {k.get('why', '')})"))

    def do_undo(self):
        sel = self.hist.selection()
        h = next((x for x in S.load_history() if sel and x["id"] == sel[0]), None)
        if not h:
            messagebox.showinfo("되돌리기", "차감 기록에서 되돌릴 줄을 골라 주세요.")
            return
        if h.get("undone"):
            messagebox.showinfo("되돌리기", "이미 되돌린 기록입니다.")
            return
        if not h["changes"]:
            messagebox.showinfo("되돌리기", "이 기록은 재고 풀어둠이라 카페24 재고를 빼지 않았습니다. 되돌릴 것이 없습니다.")
            return
        if not messagebox.askyesno("되돌리기", f"{h['time'][:16]}에 차감한 품목 {len(h['changes'])}개를 되돌릴까요?\n"
                                             "뺀 수만큼 카페24 재고에 다시 더하고, 주문 줄은 '차감 대기'로 돌아갑니다.",
                                   icon="warning"):
            return

        def work(prog):
            return S.undo(h["id"], lambda d, t: prog(d, t, "카페24 재고 되돌리는 중"))

        def done(res):
            self._show_result([f"{x['product_name'][:24]} · {x['label']}:  {x['before']} → {x['after']}" for x in res],
                              title=f"되돌리기 완료 — 품목 {len(res)}개")
            self.refresh()
        self.app.bg(work, done, "카페24 재고 되돌리는 중...", progress=True)

    # ------------------------------------------------------------ 연결표
    def fill_map(self):
        self.mtree.delete(*self.mtree.get_children())
        self.pm_lb.configure(text=PM.status_text())
        q = self.msearch.get().strip().lower()
        n = 0
        m = S.load_map()
        self.mdetail.configure(text="")
        if self.mview.get() in ("overlap", "code"):
            self.b_ovok.pack(side="left", padx=6)
        else:
            self.b_ovok.pack_forget()
        if self.mview.get() == "code":
            excl = S.load_excluded()
            for lid, e in m.items():
                if lid in excl:
                    continue
                cm = S.code_mismatch(e)
                if not cm:
                    continue
                t = e.get("etsy_title", lid)
                if q and q not in t.lower() and q not in e.get("product_name", "").lower():
                    continue
                self.mtree.insert("", "end", iid=lid, tags=("need",), values=(
                    short_title(t, 60), short_title(e.get("product_name", ""), 40),
                    {"each": "옵션마다 따로", "combo": "옵션 조합", "single": "옵션 상관없이"}.get(e.get("mode", "each"), ""),
                    self._nopt(e), f"⚠ 코드 다름: Etsy {cm[0]} ↔ 카페24 {cm[1]} — 다른 상품에 연결됐는지 확인 "
                                   "(머리글자만 다르면 '대응표'에 추가)"))
                n += 1
        elif self.mview.get() == "overlap":
            excl = S.load_excluded()
            for lid, e in m.items():
                if lid in excl:
                    continue
                iss = S.overlap_issues(e)
                if not iss:
                    continue
                t = e.get("etsy_title", lid)
                if q and q not in t.lower() and q not in e.get("product_name", "").lower():
                    continue
                self.mtree.insert("", "end", iid=lid, tags=("need",), values=(
                    short_title(t, 60), short_title(e.get("product_name", ""), 40), "옵션마다 따로", self._nopt(e),
                    " / ".join(iss)[:300]))
                n += 1
        elif self.mview.get() == "mult":
            for lid, e in m.items():
                if not e.get("product_no") or e.get("mode", "each") != "each":
                    continue
                names = {nm for nm, vals in e.get("each", {}).items()
                         if any(not v.get("skip") and not v.get("manual") for v in vals.values())}
                big = sorted({int(v.get("mult", 1)) for vals in e.get("each", {}).values() for v in vals.values()
                              if not v.get("skip") and not v.get("manual") and int(v.get("mult", 1)) >= 2})
                if len(names) < 2 or not big:
                    continue
                t = e.get("etsy_title", lid)
                if q and q not in t.lower():
                    continue
                self.mtree.insert("", "end", iid=lid, tags=("need",), values=(
                    short_title(t, 60), short_title(e.get("product_name", ""), 40), "옵션마다 따로", self._nopt(e),
                    f"옵션 {len(names)}개({', '.join(sorted(names))[:40]}) · 배수 {', '.join(map(str, big))}"
                    " — 상품명 개수가 합친 개수면 배수 1이 맞음"))
                n += 1
        elif self.mview.get() == "review":
            for lid, e, reasons in W.review_items():
                t = e.get("etsy_title", lid)
                if q and q not in t.lower():
                    continue
                self.mtree.insert("", "end", iid=lid, tags=("need",), values=(
                    short_title(t, 60), short_title(e.get("product_name", ""), 40), "확인 필요", self._nopt(e),
                    " / ".join(reasons)[:200]))
                n += 1
        elif self.mview.get() == "todo":
            for lid, title, st, why in PM.todo_list():
                if q and q not in title.lower():
                    continue
                e = m.get(lid, {})
                self.mtree.insert("", "end", iid=lid, tags=("need",), values=(
                    short_title(title, 60), short_title(e.get("product_name", ""), 40) or "(못 찾음)",
                    "일부만" if st == "partial" else "직접 연결", self._nopt(e), why))
                n += 1
        else:
            modes = {"each": "옵션마다 따로", "combo": "옵션 조합", "single": "옵션 상관없이"}
            for lid, e in m.items():
                t = e.get("etsy_title", lid)
                if q and q not in t.lower() and q not in e.get("product_name", "").lower():
                    continue
                if not e.get("product_no"):
                    continue
                skip = sum(1 for v in self._vals(e) if v.get("skip"))
                self.mtree.insert("", "end", iid=lid, values=(short_title(t, 60), short_title(e.get("product_name", ""), 40),
                                                              modes.get(e.get("mode", "each"), ""), self._nopt(e),
                                                              f"재고 풀어둠·안 뺌 {skip}개" if skip else ""))
                n += 1
        self.mcount.configure(text=f"{n:,}개")

    @staticmethod
    def _vals(e):
        out = list(e.get("combo", {}).values()) + ([e["single"]] if e.get("single") else [])
        for v in e.get("each", {}).values():
            out += list(v.values())
        return out

    def _nopt(self, e):
        return len(self._vals(e))

    def edit_map(self):
        sel = self.mtree.selection()
        if not sel:
            messagebox.showinfo("연결표", "연결할 줄을 골라 주세요.")
            return
        lid = sel[0]
        if self.mview.get() == "review":
            ReviewDialog(self, lid)
            return
        e = S.load_map().get(lid, {})
        line = PM.pseudo_line(lid)                       # 미리 받은 Etsy 옵션 전부
        if not line["title"]:
            line = next((x for x in reversed(S.load_queue()) if x["listing_id"] == lid), None) or \
                {"tid": "", "listing_id": lid, "title": e.get("etsy_title", ""), "quantity": 1, "variations": [], "receipt_id": ""}

        def saved():
            PM.mark_manual(lid)
            self.after_connect()
        ConnectDialog(self, line, on_saved=saved)

    def _map_detail(self):
        sel = self.mtree.selection()
        if not sel or self.mview.get() != "overlap":
            self.mdetail.configure(text="")
            return
        iss = S.overlap_issues(S.load_map().get(sel[0], {}), with_ok=True)
        self.mdetail.configure(text="\n".join(iss))

    def overlap_ok_sel(self):
        sel = list(self.mtree.selection())
        if self.mview.get() == "code":
            if not sel:
                messagebox.showinfo("코드 다름", "맞게 연결된 상품을 골라 주세요 (Ctrl·Shift+클릭으로 여러 개).")
                return
            if not messagebox.askyesno("코드 다름", f"고른 {len(sel)}개는 코드는 다르지만 연결이 맞다고 표시할까요?\n\n"
                                       "다음부터 경고 없이 차감합니다. 나중에 연결을 고치면 다시 확인합니다."):
                return
            m = S.load_map()
            S.code_ok([(lid, m.get(lid, {}).get("etsy_title", "")) for lid in sel])
            self.fill_map()
            self.app.flash(f"{len(sel)}개를 '이대로 맞음'으로 표시했습니다.")
            return
        if not sel:
            messagebox.showinfo("옵션 겹침 확인", "맞게 연결된 상품을 골라 주세요 (Ctrl·Shift+클릭으로 여러 개).")
            return
        if not messagebox.askyesno("옵션 겹침 확인", f"고른 {len(sel)}개는 지금 연결이 맞다고 표시할까요?\n\n"
                                   "다음부터 이 목록에 안 나옵니다. 나중에 연결을 고치면 다시 확인합니다."):
            return
        S.overlap_ok(sel)
        self.fill_map()
        self.app.flash(f"{len(sel)}개를 '이대로 맞음'으로 표시했습니다.")

    # ------------------------------------------------------------ 연결 안 함
    def exclude_sel(self):
        sel = list(self.mtree.selection())
        if not sel:
            messagebox.showinfo("연결 안 함", "연결표에서 상품을 골라 주세요 (Ctrl·Shift+클릭으로 여러 개).")
            return
        m = S.load_map()
        L = PM.load_listings().get("listings", {})
        items = [(lid, (L.get(lid) or {}).get("title") or (m.get(lid) or {}).get("etsy_title", lid)) for lid in sel]
        had = sum(1 for lid in sel if (m.get(lid) or {}).get("product_no"))
        msg = f"고른 {len(sel)}개를 '연결 안 함'으로 옮길까요?"
        if had:
            msg += f"\n\n이 중 {had}개는 이미 연결돼 있습니다. 연결을 떼어서 보관하고, 되돌리면 그대로 돌아옵니다.\n" \
                   "(떼는 동안 이 상품이 팔리면 차감되지 않고 '연결 필요'로 나옵니다)"
        if not messagebox.askyesno("연결 안 함", msg):
            return
        S.exclude(items)
        self.refresh()
        self.app.flash(f"{len(items)}개를 '연결 안 함'으로 옮겼습니다.")

    def unexclude_sel(self):
        sel = list(self.xtree.selection())
        if not sel:
            messagebox.showinfo("연결 안 함", "되돌릴 상품을 골라 주세요.")
            return
        S.unexclude(sel)
        self.refresh()
        self.app.flash(f"{len(sel)}개를 다시 연결 대상으로 돌렸습니다. (미리 연결을 다시 하면 함께 확인합니다)")

    def fill_excluded(self):
        self.xtree.delete(*self.xtree.get_children())
        ex = S.load_excluded()
        for lid, it in sorted(ex.items(), key=lambda kv: kv[1].get("time", ""), reverse=True):
            self.xtree.insert("", "end", iid=lid, values=(short_title(it.get("title", ""), 90), lid, it.get("time", "")[:16]))
        self.xcount.configure(text=f"{len(ex):,}개")

    def copy_excluded(self):
        ex = S.load_excluded()
        ids = list(self.xtree.selection()) or list(self.xtree.get_children())
        if not ids:
            return
        text = "\n".join(f"{lid}\t{ex.get(lid, {}).get('title', '')}" for lid in ids)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.flash(f"{len(ids)}개 복사했습니다. 엑셀이나 메신저에 붙여 넣으세요.")

    # ------------------------------------------------------------ 미리 연결
    def run_premap(self, redo):
        if C.token_status()[0] == "bad":
            messagebox.showwarning("미리 연결", "카페24 연결이 필요합니다 (관리 탭 → 카페24 연결).")
            return
        if redo:
            if not messagebox.askyesno("미리 연결 — 처음부터 다시",
                                       "Etsy 상품 전체를 처음부터 다시 확인합니다.\n"
                                       "이미 연결해 둔 것(직접 연결한 것 포함)은 지우지 않고 그대로 두고, 빈 곳만 채웁니다.\n\n계속할까요?"):
                return
        else:
            st = PM.load_state().get("items", {})
            msg = ("Etsy 상품 전체를 받아서 확실한 것만 연결표에 넣습니다.\n"
                   "· Etsy 상품 목록·옵션, 카페24 품목·재고를 한꺼번에 받습니다 (상품 수에 따라 수십 분 걸릴 수 있음)\n"
                   "· 그동안 다른 작업(주문서 인쇄 등)은 못 하니 한가할 때 하세요\n"
                   "· 중간에 [멈추기]를 누르면 거기까지 저장하고, 다음에 이어서 합니다")
            if st:
                msg += f"\n\n지난번에 확인한 {len(st):,}개 중 '연결 끝'인 상품은 건너뜁니다."
            if not messagebox.askyesno("미리 연결", msg + "\n\n시작할까요?"):
                return
        cfg = self.app.cfg
        if not cfg:
            messagebox.showwarning("미리 연결", "Etsy 설정(config.ini)을 읽지 못했습니다. 관리 탭에서 Etsy 연결을 확인해 주세요.")
            return
        self._pm_stop = threading.Event()
        stop = self._pm_stop

        def work(prog):
            client = op.EtsyClient(cfg)
            return PM.run(client, prog, stop, redo=redo)

        def done(sm):
            self.b_pm_stop.state(["disabled"])
            self._pm_stop = None
            self.refresh()
            head = "미리 연결을 멈췄습니다 (지금까지 한 것은 저장됨)" if sm["stopped"] else "미리 연결 끝"
            self._show_result([f"연결 끝 {sm.get('ok', 0):,}개", f"일부만 {sm.get('partial', 0):,}개",
                               f"직접 연결 필요 {sm.get('skip', 0):,}개",
                               "", "'연결표 → 직접 연결이 필요한 상품'에서 남은 것을 볼 수 있습니다."], title=head)
            self.mview.set("todo" if sm.get("skip") or sm.get("partial") else "map")
            self.fill_map()
            self.app.flash(head)
        self.b_pm_stop.state(["!disabled"])
        self.app.bg(work, done, "미리 연결 준비 중...", progress=True)
        self.app.after(500, self._watch_premap)

    def _watch_premap(self):
        """작업이 오류로 끝나도 [멈추기] 버튼이 남지 않게"""
        if self._pm_stop is not None and not self.app.busy:
            self.b_pm_stop.state(["disabled"])
            self._pm_stop = None
            self.fill_map()
        elif self._pm_stop is not None:
            self.app.after(1000, self._watch_premap)

    def stop_premap(self):
        if self._pm_stop is not None:
            self._pm_stop.set()
            self.b_pm_stop.state(["disabled"])
            self.app.flash("멈추는 중입니다... 지금 하던 묶음까지 저장하고 멈춥니다.")

    def del_map(self):
        sel = self.mtree.selection()
        if not sel:
            return
        m = S.load_map()
        e = m.get(sel[0], {})
        if not messagebox.askyesno("연결 지우기", f"'{short_title(e.get('etsy_title', ''), 50)}'의 연결을 지울까요?"):
            return
        m.pop(sel[0], None)
        S.save_map(m)
        self.refresh()


# ================================================================ 바뀐 상품 확인 창
class ReviewDialog(tk.Toplevel):
    def __init__(self, tab, lid):
        super().__init__(tab)
        self.tab, self.lid = tab, lid
        self.title("바뀐 상품 확인")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 900, 560)
        self.minsize(700, 420)
        e = S.load_map().get(lid, {})
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text=e.get("etsy_title", lid), style="Page.TLabel", font=T.f(11, True), wraplength=850,
                  justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=f"→ 카페24: {e.get('product_name', '')}", style="Page.TLabel", wraplength=850,
                  justify="left").grid(row=1, column=0, sticky="w", pady=(2, 8))
        txt = tk.Text(body, wrap="word", font=T.f(10), height=14)
        txt.tag_configure("h", font=T.f(10, True))
        txt.tag_configure("old", foreground=T.RED)
        txt.tag_configure("new", foreground=T.GREEN_D)
        for c in (e.get("review") or {}).get("changes", []):
            txt.insert("end", f"■ {c['what']}" + (f"  ({c['note']})" if c.get("note") else "") + "\n", "h")
            txt.insert("end", f"   전: {c['before']}\n", "old")
            txt.insert("end", f"   후: {c['after']}\n\n", "new")
        for x in S.load_queue():
            if x["listing_id"] == lid and x.get("status") == "pending" and S.title_mismatch(e, x["title"]):
                base = (e.get("snap") or {}).get("etsy_title") or e.get("etsy_title", "")
                txt.insert("end", f"■ 주문 {x['receipt_id']}의 상품명이 연결할 때와 다름\n", "h")
                txt.insert("end", f"   연결할 때: {base}\n", "old")
                txt.insert("end", f"   주문:     {x['title']}\n\n", "new")
        if (e.get("review") or {}).get("time"):
            txt.insert("end", f"(찾은 때: {e['review']['time'][:16]})", "h")
        txt.configure(state="disabled")
        txt.grid(row=2, column=0, sticky="nsew")
        hint(body, "같은 상품이 맞으면 [그대로 맞음] — 지금 모습을 새 기준으로 기억하고 차감을 다시 합니다.\n"
                   "다른 상품으로 바뀌었으면 [연결 고치기]로 카페24 상품·품목·배수를 다시 고르거나, [연결 지우기]를 누르세요.",
             row=3, column=0, sticky="w", pady=(8, 0), wrap=850, page=True)
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(bb, text="닫기", style="Page.TButton", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="연결 지우기", style="Stop.TButton", command=self.delete).pack(side="right", padx=6)
        ttk.Button(bb, text="연결 고치기", style="Page.TButton", command=self.edit).pack(side="right")
        ttk.Button(bb, text="그대로 맞음", style="Page.Accent.TButton", command=self.ok).pack(side="right", padx=6)

    def ok(self):
        W.confirm(self.lid)
        self.destroy()
        self.tab.refresh()
        self.tab.app.flash("확인했습니다. 지금 모습을 새 기준으로 기억하고, 이 상품도 다시 차감합니다.")

    def edit(self):
        lid = self.lid
        self.destroy()
        line = PM.pseudo_line(lid)
        if not line["title"]:
            line = next((x for x in reversed(S.load_queue()) if x["listing_id"] == lid), None) or \
                {"tid": "", "listing_id": lid, "title": S.load_map().get(lid, {}).get("etsy_title", ""), "quantity": 1,
                 "variations": [], "receipt_id": ""}
        ConnectDialog(self.tab, line, on_saved=self.tab.after_connect)

    def delete(self):
        if not messagebox.askyesno("연결 지우기", "이 상품의 연결을 지울까요?\n(이 상품이 팔리면 '연결 필요'로 나옵니다)", parent=self):
            return
        m = S.load_map()
        m.pop(self.lid, None)
        S.save_map(m)
        self.destroy()
        self.tab.refresh()


# ================================================================ 주문서를 뽑은 뒤 재고가 바뀐 줄
class ChangesWindow(tk.Toplevel):
    def __init__(self, tab, items, go_on):
        super().__init__(tab)
        self.tab, self.items, self.go_on = tab, items, go_on
        self.title("주문서를 뽑은 뒤 재고가 바뀐 줄")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 980, 520)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        worse = sum(1 for x in items if x["worse"])
        ttk.Label(body, text=f"주문서에 적힌 재고와 지금 재고가 달라진 줄 {len(items)}개"
                             + (f"  ·  그중 지금 모자라는 줄 {worse}개 (빨간색)" if worse else ""),
                  style="Page.TLabel", font=T.f(11, True)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        cols = (("oid", "주문", 170), ("c24", "카페24 상품", 300), ("opt", "옵션", 120), ("b", "주문서", 100),
                ("a", "지금", 100), ("t", "주문서 뽑은 때", 110))
        tree = ttk.Treeview(body, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for c, t, w in cols:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w" if c in ("oid", "c24", "opt") else "center", stretch=c == "c24")
        tree.tag_configure("worse", foreground=T.RED)
        for i, x in enumerate(items):
            tree.insert("", "end", iid=str(i), tags=("worse",) if x["worse"] else (), values=(
                f"{x['receipt_id']}  {x['name']}", short_title(x["product_name"], 44), x["label"], x["before"], x["after"],
                x["time"]))
        tree.grid(row=1, column=0, sticky="nsew")
        self.tree = tree
        hscroll_all(self)
        hint(body, "차감은 지금 재고로 다시 계산하니 안전합니다 (모자라면 0으로 맞추고 '재고 부족' 창에 사입 목록). "
                   "다만 주문서를 보고 상품을 담는 직원이 옛날 숫자를 보고 있을 수 있으니, 빨간 줄은 한 번 더 확인하세요. "
                   "[달라진 주문 다시 미리보기]로 지금 재고로 계산한 주문서를 볼 수 있습니다.",
             row=2, column=0, sticky="w", pady=(6, 0), wrap=930, page=True)
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=3, column=0, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="달라진 주문 다시 미리보기", style="Page.TButton", command=self.preview).pack(side="left")
        ttk.Button(bb, text="취소", style="Page.TButton", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="확인했음 — 차감 계속", style="Page.Accent.TButton", command=self.cont).pack(side="right", padx=6)

    def preview(self):
        sel = [self.items[int(i)] for i in self.tree.selection()] or self.items
        rids = {str(x["receipt_id"]) for x in sel}
        app = self.tab.app
        rs = [r for r in getattr(app, "receipts", []) if str(r.get("receipt_id")) in rids]
        if not rs:
            messagebox.showinfo("다시 미리보기", "출력 탭의 주문 목록에서 이 주문들을 찾지 못했습니다 (이미 발송 처리된 주문 등).",
                                parent=self)
            return
        app._preview(rs)

    def cont(self):
        self.destroy()
        self.go_on()


# ================================================================ 차감 미리 보기 창
class PreviewWindow(tk.Toplevel):
    """차감 전에 품목마다 '지금 재고 → 뺄 개수 → 차감 후'를 보여 주고, 확인하면 실제로 뺌 (연습 모드면 계산만)"""
    STATE = {"ded": "뺌", "short": "⚠ 재고 부족 → 0으로", "short_row": "⚠ 안 뺌 (같은 줄 재고 부족)",
             "plenty": "재고 풀어둠 — 안 뺌", "skip": "재고 관리 안 함 — 안 뺌", "unknown": "재고 못 받음 — 차감 때 다시 확인"}

    def __init__(self, tab, rows, stock, practice, notes, target, on_go):
        super().__init__(tab)
        self.tab, self.on_go = tab, on_go
        test = op.is_test()
        self.title("차감 미리 보기")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 1060, 640)
        self.minsize(780, 460)
        pv = S.preview(rows, stock)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        if test:
            band, bg, fg = "테스트 모드 — 계산만 합니다. 카페24 재고는 절대 바뀌지 않습니다.", "#FFF6C2", T.ORANGE
        elif practice:
            band, bg, fg = "연습 모드 — 끝까지 해 봐도 카페24 재고는 바뀌지 않습니다.", "#FFF6C2", T.ORANGE
        else:
            band, bg, fg = "실제 차감 — [카페24에서 실제로 빼기]를 누르면 카페24 재고가 바뀝니다.", "#FDEBEC", T.RED
        tk.Label(body, text=band, bg=bg, fg=fg, font=T.f(11, True), anchor="w", padx=10, pady=6).grid(
            row=0, column=0, sticky="we", pady=(0, 8))
        c = pv["counts"]
        summ = (f"대상: {target}   →   실제로 빼는 품목 {c['ded']}개"
                + (f" · ⚠ 재고 부족 {c['short']}개 (0으로 맞추고 사입 목록 {pv['buy']}개)" if c["short"] else "")
                + (f" · 재고 풀어둠 {c['plenty']}개 (안 뺌)" if c["plenty"] else "")
                + (f" · 재고 관리 안 함 {c['skip']}개" if c["skip"] else "")
                + (f" · 재고 못 받음 {c['unknown']}개" if c["unknown"] else ""))
        ttk.Label(body, text=summ, style="Page.TLabel", font=T.f(11, True), wraplength=1000, justify="left").grid(
            row=1, column=0, sticky="w")
        extra = []
        if pv["nothing"]:
            extra.append(f"재고 풀어둠·안 뺌으로 고정된 줄 {pv['nothing']}개는 빼지 않고 정리합니다.")
        if pv["manual"]:
            extra.append(f"✋ 직접 차감할 부분이 있는 줄 {pv['manual']}개는 끝나면 '직접 차감 목록'에 모읍니다.")
        extra += notes
        self.note = ttk.Label(body, text="\n".join(extra), style="PageHint.TLabel", wraplength=1000, justify="left")
        self.note.grid(row=2, column=0, sticky="w", pady=(2, 6))
        tf = ttk.Frame(body, style="Page.TFrame")
        tf.grid(row=3, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        vshow = bool(op.load_settings().get("vendor_show"))
        cols = [("prod", "카페24 상품", 260), ("var", "품목", 170)] + ([("vendor", "업체명", 110)] if vshow else []) + \
               [("before", "지금 재고", 80), ("n", "뺄 개수", 70), ("after", "차감 후", 80), ("st", "상태", 300)]
        self.tree = ttk.Treeview(tf, columns=[x[0] for x in cols], show="headings", selectmode="browse")
        for k, t, w in cols:
            self.tree.heading(k, text=t)
            self.tree.column(k, width=w, anchor="center" if k in ("before", "n", "after") else "w")
        self.tree.tag_configure("bad", foreground=T.RED)
        self.tree.tag_configure("muted", foreground=T.MUTED)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        def num(v):
            return f"{v:,}" if isinstance(v, int) else ("-" if v in (None, "") else str(v))
        for it in pv["items"]:
            st = self.STATE.get(it["state"], it["state"])
            if it["note"] and it["state"] in ("ded", "short"):
                st = f"{st} · {it['note']}" if it["state"] == "ded" else f"⚠ {it['note']}"
            vals = [short_title(it["product_name"], 40), it["label"]] + ([it["vendor"]] if vshow else []) + \
                   [num(it["before"]), it["n"] if it["state"] in ("ded", "unknown", "short") else f"({it['n']})",
                    num(it["after"]) if it["state"] in ("ded", "short") else "-", st]
            tag = "bad" if it["state"] in ("short", "short_row") else ("muted" if it["state"] in ("plenty", "skip") else "")
            self.tree.insert("", "end", values=vals, tags=(tag,) if tag else ())
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=4, column=0, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="취소", style="Page.TButton", command=self.destroy).pack(side="right")
        if test or practice:
            ttk.Button(bb, text="연습: 빼는 것처럼 해 보기", style="Page.Accent.TButton", command=self.go).pack(side="right", padx=6)
            ttk.Label(bb, text="계산 결과와 재고 부족 목록만 보여 주고, 차감 대기는 그대로 둡니다.",
                      style="PageHint.TLabel").pack(side="left")
        else:
            ttk.Button(bb, text="카페24에서 실제로 빼기", style="Stop.TButton", command=self.go).pack(side="right", padx=6)
            ttk.Label(bb, text="누르는 순간 카페24 재고를 다시 확인해서 뺍니다. 잘못 뺐으면 '차감 기록'에서 되돌릴 수 있습니다.",
                      style="PageHint.TLabel").pack(side="left")
        if not pv["items"] and not pv["nothing"] and not pv["manual"]:
            self.note.configure(text="뺄 품목이 없습니다.")
        hscroll_all(self)
        self.grab_set()

    def go(self):
        self.grab_release()
        self.destroy()
        self.on_go()


# ================================================================ 차감 안 함 목록 창
class SkippedWindow(tk.Toplevel):
    """[이 줄 차감 안 함]으로 뺀 줄 — 골라서 차감 대기로 되돌리기"""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.title("차감 안 함 목록")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 1000, 560)
        self.minsize(720, 400)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        self.head = ttk.Label(body, text="", style="Page.TLabel", font=T.f(11, True))
        self.head.grid(row=0, column=0, sticky="w", pady=(0, 6))
        tf = ttk.Frame(body, style="Page.TFrame")
        tf.grid(row=1, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        cols = (("time", "뺀 때", 130), ("oid", "주문", 190), ("q", "수량", 50), ("etsy", "Etsy 상품", 330), ("opt", "옵션", 260))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for c, t, w in cols:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("time", "q") else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=2, column=0, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="모두 선택", style="Page.TButton",
                   command=lambda: self.tree.selection_set(self.tree.get_children())).pack(side="left")
        ttk.Button(bb, text="닫기", style="Page.TButton", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="고른 줄 차감 대기로 되돌리기", style="Page.Accent.TButton", command=self.back).pack(side="right", padx=6)
        hint(body, "차감 대기에서 [이 줄 차감 안 함]으로 뺀 줄입니다 (취소 주문 등). 이 줄들은 같은 주문을 다시 넣어도 들어가지 않습니다. "
                   "잘못 뺐으면 골라서(Ctrl·Shift+클릭으로 여러 개) [고른 줄 차감 대기로 되돌리기]를 누르세요. 카페24 재고는 바뀌지 않습니다.",
             row=3, column=0, sticky="w", pady=(6, 0), wrap=950, page=True)
        hscroll_all(self)
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        rows = S.skipped_lines()
        for x in rows:
            self.tree.insert("", "end", iid=x["tid"], values=(
                (x.get("skipped_at") or "")[:16] or "-", f"{x['receipt_id']}  {x.get('name', '')}", x.get("quantity", 1),
                x.get("title", ""), opts_text(x)))
        self.head.configure(text=f"[이 줄 차감 안 함]으로 뺀 줄 {len(rows)}개")

    def back(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("차감 안 함 목록", "되돌릴 줄을 골라 주세요.", parent=self)
            return
        if not messagebox.askyesno("차감 안 함 목록", f"고른 {len(sel)}줄을 차감 대기로 되돌릴까요?", parent=self):
            return
        S.set_status(set(sel), "pending")
        op.log.info(f"[차감 안 함] {len(sel)}줄 차감 대기로 되돌림")
        self.fill()
        self.tab.refresh()
        self.tab.app.flash(f"{len(sel)}줄을 차감 대기로 되돌렸습니다.")


# ================================================================ 직접 차감 창
class ManualWindow(tk.Toplevel):
    """카페24에 없는 옵션이라 사람이 카페24에서 직접 빼야 하는 줄"""

    def __init__(self, tab, items, practice=False):
        super().__init__(tab)
        self.tab, self.items, self.practice = tab, items, practice
        self.title("직접 차감 목록")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 1040, 620)
        self.minsize(780, 460)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        head = f"카페24에서 직접 빼야 하는 줄 {len(items)}개"
        if practice:
            head += "   ·   [연습/테스트] 계산만 한 결과 (상태는 안 바뀜)"
        ttk.Label(body, text=head, style="Page.TLabel", font=T.f(11, True)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        tf = ttk.Frame(body, style="Page.TFrame")
        tf.grid(row=1, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        cols = (("chk", "", 30), ("oid", "주문", 160), ("c24", "카페24 상품", 300), ("what", "직접 뺄 것 (Etsy 옵션)", 260),
                ("n", "수량", 50), ("etsy", "Etsy 상품", 220))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in cols], show="headings", selectmode="none")
        for c, t, w in cols:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("chk", "n") else "w", stretch=c in ("c24", "etsy"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self.click)
        self.picked = set()
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=2, column=0, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="목록 복사", style="Page.TButton", command=self.copy).pack(side="left")
        ttk.Button(bb, text="모두 고르기", style="Page.TButton",
                   command=lambda: (self.picked.update(it["tid"] for it in self.items), self.fill())).pack(side="left", padx=6)
        ttk.Button(bb, text="닫기", style="Page.TButton", command=self.destroy).pack(side="right")
        self.b_skip = ttk.Button(bb, text="고른 줄 차감 안 함", style="Stop.TButton", command=self.skip)
        self.b_skip.pack(side="right", padx=6)
        self.b_done = ttk.Button(bb, text="고른 줄 차감했음", style="Page.Accent.TButton", command=self.done)
        self.b_done.pack(side="right")
        hint(body, "카페24 관리자에서 해당 상품 재고를 직접 뺀 뒤, 그 줄을 골라(☐) [고른 줄 차감했음]을 누르면 목록에서 정리됩니다. "
                   "빼지 않을 줄(취소 주문 등)은 [고른 줄 차감 안 함]. 처리하지 않은 줄은 남아 있고, 차감 대기 위 [직접 차감 목록]으로 "
                   "언제든 다시 열 수 있습니다. [목록 복사]: 고른 줄(안 고르면 전부)을 '카페24 상품명 - Etsy 옵션 * 수량개 (주문)'으로 복사.",
             row=3, column=0, sticky="w", pady=(6, 0), wrap=990, page=True)
        if practice:
            self.b_done.state(["disabled"])
            self.b_skip.state(["disabled"])
        hscroll_all(self)
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for it in self.items:
            for k, (lab, n) in enumerate(it["parts"] or [("(옵션)", 0)]):
                first = k == 0
                self.tree.insert("", "end", iid=f"{it['tid']}#{k}", values=(
                    ("☑" if it["tid"] in self.picked else "☐") if first else "",
                    f"{it['receipt_id']}  {it['name']}" if first else "", it["product_name"] if first else "",
                    lab, n, short_title(it["title"], 40) if first else ""))
        ok = bool(self.picked) and not self.practice
        for b in (self.b_done, self.b_skip):
            b.state(["!disabled"] if ok else ["disabled"])

    def click(self, e):
        row = self.tree.identify_row(e.y)
        if row:
            tid = row.split("#", 1)[0]
            (self.picked.discard if tid in self.picked else self.picked.add)(tid)
            self.fill()
        return "break"

    def copy(self):
        its = [it for it in self.items if it["tid"] in self.picked] or self.items
        text = "\n".join(S.manual_line(it) for it in its)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.tab.app.flash(f"직접 차감 목록 {len(its)}줄을 복사했습니다.")

    def _finish(self, status, label):
        tids = set(self.picked)
        if not tids:
            return
        if not messagebox.askyesno("직접 차감", f"고른 {len(tids)}줄을 '{label}'(으)로 정리할까요?", parent=self):
            return
        q = S.load_queue()
        for x in q:
            if x["tid"] in tids:
                x["status"] = status
                if status == "done":
                    x["hist"] = x.get("hist") or "manual"
                    x["manual_done"] = S.now_s()
        S.save_queue(q)
        op.log.info(f"[직접 차감] {len(tids)}줄 {label}")
        self.items = [it for it in self.items if it["tid"] not in tids]
        self.picked.clear()
        self.fill()
        self.tab.refresh()
        self.tab.app.flash(f"{len(tids)}줄을 '{label}'(으)로 정리했습니다.")
        if not self.items:
            self.destroy()

    def done(self):
        self._finish("done", "차감했음")

    def skip(self):
        self._finish("skipped", "차감 안 함")


# ================================================================ 재고 부족 창
class ShortageWindow(tk.Toplevel):
    """재고가 모자란 품목: 재고는 0으로 맞추고, 사입 목록(재고로 못 채운 주문의 수량 전체)을 복사"""

    def __init__(self, tab, items, practice, when=""):
        super().__init__(tab)
        self.tab, self.items = tab, items
        self.title("재고 부족 — 사입 목록")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 980, 680)
        self.minsize(760, 520)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        head = f"카페24 재고가 모자란 품목 {len(items)}개 — 재고는 0으로 맞춤" + (
            f"   ·   {when if '묶음' in when else when[:16]}" if when else "")
        if practice:
            head = f"카페24 재고가 모자란 품목 {len(items)}개   ·   [연습/테스트] 계산만 한 결과 (재고는 안 바뀜)"
        ttk.Label(body, text=head, style="Page.TLabel", font=T.f(11, True)).grid(row=0, column=0, sticky="w")
        # 사입 목록
        cp = ttk.LabelFrame(body, text=" 사입 목록 (복사해서 사입 담당에게 전달) ", padding=10)
        cp.grid(row=1, column=0, sticky="we", pady=(8, 0))
        cp.columnconfigure(0, weight=1)
        self.copy_text = "\n".join(S.short_line(it) for it in items)
        txt = tk.Text(cp, height=min(8, max(3, len(items))), wrap="none", font=T.f(10))
        txt.insert("1.0", self.copy_text)
        txt.configure(state="disabled")
        txt.grid(row=0, column=0, sticky="we")
        self.copy_short = "\n".join(S.short_line(it, detail=False) for it in items)
        cb = ttk.Frame(cp)
        cb.grid(row=0, column=1, sticky="n", padx=(10, 0))
        ttk.Button(cb, text="사입목록 전체 복사", style="Accent.TButton", command=self.copy).pack(fill="x")
        ttk.Button(cb, text="사입 목록 필요개수만 복사", command=self.copy_need).pack(fill="x", pady=(6, 0))
        hint(cp, "형식: 상품명 - 옵션 * 사입할 수량개 (필요 / 차감 전 재고). 상품명 끝의 판매 단위 '(1개)' 등은 뺐습니다.\n"
                 "사입할 수량 = 있는 재고로 다 채울 수 없는 주문들의 수량 전체 (예: 20개 주문 + 10개 주문에 재고 10 → "
                 "10개 주문은 재고로 채우고 사입 20개). [필요개수만 복사]는 뒤의 괄호 없이 복사합니다.",
             row=1, column=0, columnspan=2, sticky="w", pady=(4, 0), wrap=880)
        # 주문별
        ttk.Label(body, text="주문별 (재고로 채운 주문 / 사입이 필요한 주문)", style="Page.TLabel",
                  font=T.f(10, True)).grid(row=2, column=0, sticky="w", pady=(12, 4))
        tf = ttk.Frame(body, style="Page.TFrame")
        tf.grid(row=3, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, columns=("what", "n", "info"), show="headings", selectmode="none")
        for c, t, w in (("what", "품목 / 주문", 500), ("n", "필요", 60), ("info", "", 330)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c == "n" else "w")
        self.tree.tag_configure("item", font=T.f(10, True), background=T.CARD)
        self.tree.tag_configure("buy", foreground=T.RED)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=4, column=0, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="닫기", style="Page.Accent.TButton", command=self.destroy).pack(side="right")
        hint(body, "재고가 모자란 품목도 주문 줄은 모두 차감했습니다 (카페24 재고는 0). 사입이 들어오면 카페24 재고를 직접 채워 주세요. "
                   "잘못 뺐으면 '차감 기록'에서 되돌릴 수 있습니다.",
             row=5, column=0, sticky="w", pady=(6, 0), wrap=930, page=True)
        hscroll_all(self)
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for k, it in enumerate(self.items):
            ov = S.opt_values(it["label"])
            self.tree.insert("", "end", iid=f"i{k}", tags=("item",), values=(
                f"{it['product_name']}" + (f" - {ov}" if ov else ""), it["need"],
                f"재고 {it['stock']} → 0 · 사입 {it['short']}개"))
            for o in it["orders"]:
                cov = o.get("covered")
                self.tree.insert("", "end", iid=f"o{k}:{o['tid']}", tags=() if cov else ("buy",), values=(
                    f"     주문 {o['receipt_id']}  {o['name']}  ·  {o['title'][:40]}", o["n"],
                    "재고로 채움" if cov else f"사입 필요 {o['n']}개"))

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.copy_text)
        self.tab.app.flash(f"사입 목록 {len(self.items)}줄을 복사했습니다.")

    def copy_need(self):
        self.clipboard_clear()
        self.clipboard_append(self.copy_short)
        self.tab.app.flash(f"사입 목록 {len(self.items)}줄을 필요개수만 복사했습니다.")


# ================================================================ 옵션 값 대응표 창
class ValueMapWindow(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.title("옵션 값 대응표")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        center_on(self, 640, 560)
        self.minsize(520, 420)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        hint(body, "Etsy 옵션 값에 왼쪽 단어가 들어 있으면(예: Silver, Silver (Circle), Openable Bail-SILVER), "
                   "그 상품의 카페24 품목에서 오른쪽 값을 찾습니다.\n"
                   "· 카페24 품목 중 맞는 게 딱 하나면 자동 연결, 둘 이상(예: 무도금·백금도금 둘 다)이면 사람이 고릅니다.\n"
                   "· 카페24 값은 같거나 그 값으로 시작해야 맞습니다 ('백금도금-오픈고리' ✓, '무광백금' ✗).\n"
                   "· 단어가 여러 개 맞으면 긴 것을 씁니다 ('Rose Gold'는 Gold가 아니라 Rose Gold).\n"
                   "표를 고친 뒤 [미리 연결 시작]을 다시 누르면 남아 있던 상품들을 다시 시도합니다.",
             row=0, column=0, columnspan=2, sticky="w", pady=(0, 8), wrap=590, page=True)
        self.tree = ttk.Treeview(body, columns=("etsy", "c24"), show="headings", selectmode="extended")
        self.tree.heading("etsy", text="Etsy 값에 이 단어가 있으면")
        self.tree.heading("c24", text="카페24에서 찾을 값 (쉼표로 구분)")
        self.tree.column("etsy", width=190, anchor="w")
        self.tree.column("c24", width=360, anchor="w", stretch=True)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self.pick)
        er = ttk.Frame(body, style="Page.TFrame")
        er.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(er, text="Etsy", style="Page.TLabel").pack(side="left")
        self.e1 = ttk.Entry(er, width=14)
        self.e1.pack(side="left", padx=(4, 0))
        ttk.Label(er, text=" = 카페24", style="Page.TLabel").pack(side="left")
        self.e2 = ttk.Entry(er, width=30)
        self.e2.pack(side="left", padx=(4, 0))
        self.e2.bind("<Return>", lambda e: self.add())
        ttk.Button(er, text="추가·바꾸기", command=self.add).pack(side="left", padx=6)
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="선택 삭제", style="Stop.TButton", command=self.delete).pack(side="left")
        ttk.Button(bb, text="처음 값으로", style="Page.TButton", command=self.reset).pack(side="left", padx=6)
        ttk.Button(bb, text="닫기", style="Page.Accent.TButton", command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        hscroll_all(self)
        self.fill()
        self.e1.focus_set()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(S.load_valmap()):
            self.tree.insert("", "end", iid=str(i), values=(r["etsy"], ", ".join(r["cafe24"])))

    def pick(self, _e=None):
        sel = self.tree.selection()
        if sel:
            a, b = self.tree.item(sel[0], "values")[:2]
            for ent, v in ((self.e1, a), (self.e2, b)):
                ent.delete(0, "end")
                ent.insert(0, v)

    def add(self):
        a = self.e1.get().strip()
        b = [x.strip() for x in self.e2.get().replace("，", ",").split(",") if x.strip()]
        if not a or not b:
            messagebox.showinfo("옵션 값 대응표", "Etsy 단어와 카페24 값을 둘 다 적어 주세요. (예: Silver = 무도금, 백금도금)", parent=self)
            return
        rows = [r for r in S.load_valmap() if r["etsy"].strip().lower() != a.lower()]
        rows.append({"etsy": a, "cafe24": b})
        S.save_valmap(rows)
        op.log.info(f"[옵션 값 대응표] {a} = {', '.join(b)}")
        self.e1.delete(0, "end")
        self.e2.delete(0, "end")
        self.fill()

    def delete(self):
        sel = [int(x) for x in self.tree.selection()]
        if sel and messagebox.askyesno("옵션 값 대응표", f"고른 {len(sel)}줄을 지울까요?", parent=self):
            S.save_valmap([r for i, r in enumerate(S.load_valmap()) if i not in sel])
            self.fill()

    def reset(self):
        if messagebox.askyesno("옵션 값 대응표", "처음 값(Silver = 무도금, 백금도금 / Gold = 골드 / Rose Gold = 로즈골드)으로 "
                                              "되돌릴까요?", parent=self):
            S.save_valmap([dict(r, cafe24=list(r["cafe24"])) for r in S.DEFAULT_VALMAP])
            self.fill()

    def close(self):
        self.destroy()
        try:
            self.tab.refresh_prefix()
        except Exception:
            pass


# ================================================================ 단위 환산표 창
class UnitWindow(tk.Toplevel):
    def __init__(self, parent, on_close=None):
        super().__init__(parent)
        self.on_close = on_close
        self.title("단위 환산표")
        self.configure(background=T.BG)
        self.transient(parent.winfo_toplevel())
        center_on(self, 500, 600)
        self.minsize(440, 460)
        self.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        hint(body, "상품명의 '2 Pcs' '1 Pair' '1조' '(10개)'를 읽을 때 쓰는 표입니다.\n"
                   "· 숫자 = 낱개 몇 개인지 (Pcs = 1, Pair·쌍·조 = 2)\n"
                   "· 글자 = 낱개로 못 바꾸는 묶음 단위. 같은 글자끼리만 계산합니다 (예: Set ↔ 세트 = '세트').",
             row=0, column=0, columnspan=2, sticky="w", pady=(0, 8), wrap=450, page=True)
        self.tree = ttk.Treeview(body, columns=("w", "v"), show="headings", selectmode="extended")
        self.tree.heading("w", text="단위 글자")
        self.tree.heading("v", text="낱개 수 / 묶음 이름")
        self.tree.column("w", width=150, anchor="center")
        self.tree.column("v", width=200, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self.pick)
        er = ttk.Frame(body, style="Page.TFrame")
        er.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.e1 = ttk.Entry(er, width=10)
        self.e1.pack(side="left")
        ttk.Label(er, text=" = ", style="Page.TLabel").pack(side="left")
        self.e2 = ttk.Entry(er, width=10)
        self.e2.pack(side="left")
        self.e2.bind("<Return>", lambda e: self.add())
        ttk.Button(er, text="추가·바꾸기", command=self.add).pack(side="left", padx=6)
        ttk.Button(er, text="선택 삭제", command=self.delete).pack(side="left")
        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(bb, text="처음 값으로", style="Page.TButton", command=self.reset).pack(side="left")
        ttk.Button(bb, text="닫기", style="Page.Accent.TButton", command=self.close).pack(side="right")
        hscroll_all(self)
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        u = S.load_units()
        for w in sorted(u, key=lambda k: (isinstance(u[k], str), str(u[k]), k)):
            v = u[w]
            self.tree.insert("", "end", iid=w, values=(w, f"{v}개" if not isinstance(v, str) else f"묶음: {v}"))

    def pick(self, e=None):
        sel = self.tree.selection()
        if sel:
            v = S.load_units()[sel[0]]
            for ent, val in ((self.e1, sel[0]), (self.e2, v)):
                ent.delete(0, "end")
                ent.insert(0, str(val))

    def add(self):
        w, v = self.e1.get().strip().lower(), self.e2.get().strip()
        if not w or not v:
            messagebox.showinfo("단위 환산표", "단위 글자와 값을 둘 다 적어 주세요. (예: pair = 2, set = 세트)", parent=self)
            return
        if w[0].isdigit():
            messagebox.showinfo("단위 환산표", "단위 글자에는 숫자를 빼고 적어 주세요. (예: '2pcs' → 'pcs')", parent=self)
            return
        u = S.load_units()
        try:
            n = float(v)
            val = int(n) if n == int(n) else n
            if val <= 0:
                raise ValueError
        except ValueError:
            val = v
        u[w] = val
        S.save_units(u)
        op.log.info(f"[단위표] {w} = {val}")
        self.e1.delete(0, "end")
        self.e2.delete(0, "end")
        self.fill()

    def delete(self):
        sel = list(self.tree.selection())
        if sel and messagebox.askyesno("단위 환산표", f"{', '.join(sel)}을(를) 지울까요?", parent=self):
            u = S.load_units()
            for w in sel:
                u.pop(w, None)
            S.save_units(u)
            self.fill()

    def reset(self):
        if messagebox.askyesno("단위 환산표", "단위 환산표를 처음 값으로 되돌릴까요?", parent=self):
            S.save_units(dict(S.DEFAULT_UNITS))
            self.fill()

    def close(self):
        self.destroy()
        if self.on_close:
            self.on_close()


# ================================================================ 연결 창
class ConnectDialog(tk.Toplevel):
    MODES = (("each", "옵션마다 따로"), ("combo", "옵션 조합으로 하나"), ("single", "옵션 상관없이 하나"))

    def __init__(self, tab, line, on_saved):
        super().__init__(tab)
        self.tab, self.app, self.line, self.on_saved = tab, tab.app, line, on_saved
        self.title("카페24 상품·옵션 연결")
        self.configure(background=T.BG)
        self.transient(tab.winfo_toplevel())
        sh = self.winfo_screenheight()
        center_on(self, min(1240, self.winfo_screenwidth() - 60), min(880, sh - 80))
        self.minsize(760, 560)
        self.map = S.load_map()
        self.entry = dict(self.map.get(line["listing_id"], {}))
        self.variants = []
        self.product = None
        self.widgets = []
        self.inv = {}

        body = ttk.Frame(self, padding=14, style="Page.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ef = ttk.LabelFrame(body, text=" Etsy 주문 상품 ", padding=10)
        ef.grid(row=0, column=0, sticky="we")
        ef.columnconfigure(1, weight=1)
        code = C.extract_code(line["title"])
        rest = line["title"][len(code):] if code and line["title"].startswith(code) else line["title"]
        col = 0
        if code:                                     # 코드: 읽기 전용, 두 번 누르면 전체 선택 + 복사
            cv = tk.StringVar(value=code)
            ce = tk.Entry(ef, textvariable=cv, font=T.f(10, True), relief="solid", bd=1, width=max(len(code) + 2, 8),
                          readonlybackground="#FFFBE6", fg=T.INK, highlightthickness=0, cursor="hand2")
            ce.configure(state="readonly")
            ce.grid(row=0, column=0, sticky="nw", padx=(0, 6))

            def copy_code(_e=None, ce=ce, code=code):
                ce.focus_set()
                ce.select_range(0, "end")
                self.clipboard_clear()
                self.clipboard_append(code)
                self.app.flash(f"복사했습니다: {code}")
                return "break"
            ce.bind("<Double-1>", copy_code)
            ce.bind("<Control-c>", lambda e, ce=ce: (self.clipboard_clear(), self.clipboard_append(
                ce.selection_get() if ce.selection_present() else ce.get()), "break")[-1])
            col = 1
        ttk.Label(ef, text=rest.strip(), wraplength=700 if code else 820, justify="left",
                  font=T.f(10, True)).grid(row=0, column=col, sticky="w")
        ttk.Label(ef, text=f"옵션: {opts_text(line)}    ·    수량 {line['quantity']}", wraplength=820,
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # 카페24 상품
        pf = ttk.LabelFrame(body, text=" ① 카페24 상품 고르기 ", padding=10)
        pf.grid(row=1, column=0, sticky="we", pady=(10, 0))
        pf.columnconfigure(0, weight=1)
        self.code_lb = ttk.Label(pf, text="", style="Hint.TLabel", wraplength=820, justify="left")
        self.code_lb.grid(row=0, column=0, columnspan=4, sticky="w")
        self.ptree = ttk.Treeview(pf, columns=("no", "name"), show="headings", height=4, selectmode="browse")
        self.ptree.heading("no", text="상품번호")
        self.ptree.heading("name", text="카페24 상품명")
        self.ptree.column("no", width=90, anchor="center")
        self.ptree.column("name", width=680, anchor="w")
        self.ptree.grid(row=1, column=0, columnspan=4, sticky="we", pady=(4, 0))
        self.ptree.bind("<<TreeviewSelect>>", lambda e: self.pick_product())
        self.search = ttk.Entry(pf, width=30)
        self.search.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.search.bind("<Return>", lambda e: self.do_search())
        ttk.Button(pf, text="카페24에서 검색", command=self.do_search).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Button(pf, text="머리글자 대응 추가", command=self.add_prefix).grid(row=2, column=2, sticky="w", pady=(6, 0))
        hint(pf, "코드로 찾은 후보가 없으면 상품명 일부나 숫자로 검색하세요. 코드 앞글자가 달라서(예: Etsy PD ↔ 카페24 PDT) 못 찾으면 "
                 "[머리글자 대응 추가]로 한 번 등록해 두면 다음부터 자동으로 찾습니다.",
             row=3, column=0, columnspan=4, sticky="w", pady=(4, 0), wrap=820)

        # 옵션
        of = ttk.LabelFrame(body, text=" ② 옵션 연결 · 배수 ", padding=10)
        of.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        body.rowconfigure(2, weight=1)
        mrow = ttk.Frame(of)
        mrow.grid(row=0, column=0, sticky="w")
        self.mode = tk.StringVar(value=self.entry.get("mode", "each" if line["variations"] else "single"))
        for val, txt in self.MODES:
            ttk.Radiobutton(mrow, text=txt, value=val, variable=self.mode, command=self.build_rows).pack(side="left", padx=(0, 12))
        ttk.Button(mrow, text="단위 환산표", style="Ghost.TButton", command=self.open_units).pack(side="left", padx=(20, 0))
        ttk.Button(mrow, text="배수 한꺼번에", style="Ghost.TButton", command=self.bulk_mult).pack(side="left", padx=(6, 0))
        help_icon(mrow, "옵션마다 따로: 옵션 하나가 카페24 품목 하나 (예: 색상, 알파벳 두 글자는 각각 차감).\n"
                        "옵션 조합으로 하나: 여러 옵션을 합쳐야 카페24 품목 하나 (예: 색상+사이즈).\n"
                        "옵션 상관없이 하나: 카페24 상품에 옵션이 없거나, 어떤 옵션이든 같은 품목.\n\n"
                        "재고 무관: 그 옵션은 어떤 품목을 뺄지 정하는 데 쓰지 않음 (안내용 옵션 등). 같은 이름 줄은 함께 체크됨.\n"
                        "재고 풀어둠·안 뺌: 그 품목은 앞으로 빼지 않음 (카페24 재고가 기준 이상이면 자동 체크).\n"
                        "직접 차감: 카페24에 없는 옵션 — 팔리면 자동으로 빼지 않고 '직접 차감 목록'에 올려 사람이 카페24에서 직접 뺌.\n\n"
                        "배수: Etsy 1개 팔리면 카페24에서 몇 개를 뺄지. 상품명의 단위를 읽어 자동으로 채웁니다\n"
                        "  (1 PCS = 1개, 1 Pair·1쌍·1조 = 2개, 카페24는 괄호 안 단위를 먼저 읽음).\n"
                        "  예: Etsy 2 Pcs ÷ 카페24 1조(2개) = 1  ·  Etsy 6 Pcs ÷ 카페24 단위 없음(1개) = 6\n"
                        "  딱 나눠떨어지지 않거나 단위를 못 읽으면 주황색으로 알려 주니 배수를 직접 고르세요.").pack(side="left", padx=(10, 0))
        # 제목 줄 (스크롤해도 항상 보이게 옵션 줄 위에 따로)
        self.hdr_fr = ttk.Frame(of)
        self.hdr_fr.grid(row=1, column=0, sticky="w", pady=(10, 2))
        # 옵션 줄이 많아도 다 볼 수 있게 스크롤
        of.columnconfigure(0, weight=1)
        of.rowconfigure(2, weight=1)
        holder = ttk.Frame(of)
        holder.grid(row=2, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.rows_cv = tk.Canvas(holder, highlightthickness=0, background=T.CARD, height=160)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=self.rows_cv.yview)
        self.rows_cv.configure(yscrollcommand=vsb.set)
        self.rows_cv.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.rows_fr = ttk.Frame(self.rows_cv)
        self.rows_cv.create_window(0, 0, window=self.rows_fr, anchor="nw")
        self.rows_fr.bind("<Configure>", lambda e: self.rows_cv.configure(scrollregion=self.rows_cv.bbox("all")))

        bb = ttk.Frame(body, style="Page.TFrame")
        bb.grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(bb, text="취소", style="Page.TButton", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="저장", style="Page.Accent.TButton", command=self.save).pack(side="right", padx=6)

        self.load_candidates()
        hscroll_all(self)
        self.grab_set()

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    # ---- 상품
    def load_candidates(self):
        cands, want = S.candidates(self.line["title"])
        age = C.products_age_days()
        n = len(C.load_products().get("products", []))
        info = f"Etsy 코드 {C.extract_code(self.line['title']) or '(없음)'} → 찾는 카페24 코드 {want or '-'}"
        info += f"   ·   카페24 상품 이름 목록 {n:,}개" + (f" ({age:.0f}일 전)" if age is not None else " (아직 안 받음 → 관리 탭)")
        self.code_lb.configure(text=info)
        cur = self.entry.get("product_no")
        if cur and all(c["no"] != cur for c in cands):
            cands = [{"no": cur, "name": self.entry.get("product_name", "")}] + cands
        self._fill_products(cands)
        if cur:
            self.ptree.selection_set(str(cur))
        elif len(cands) == 1:
            self.ptree.selection_set(str(cands[0]["no"]))

    def _fill_products(self, cands):
        self.ptree.delete(*self.ptree.get_children())
        for c in cands:
            if not self.ptree.exists(str(c["no"])):
                self.ptree.insert("", "end", iid=str(c["no"]), values=(c["no"], c["name"]))

    def do_search(self):
        kw = self.search.get().strip()
        if not kw:
            return

        def done(res):
            if not self._alive():
                return
            self._fill_products(res)
            if not res:
                messagebox.showinfo("검색", f"카페24에서 '{kw}'로 찾은 상품이 없습니다.", parent=self)
        self.app.bg(lambda: C.search_products(kw), done, "카페24에서 검색 중...", busy=False)

    def add_prefix(self):
        code = C.norm_code(C.extract_code(self.line["title"]))
        pre, _ = C.split_code(code)
        to = simpledialog.askstring("머리글자 대응 추가", f"Etsy 머리글자 '{pre}'는 카페24에서 무엇인가요?\n(예: PD = PDT)",
                                    parent=self)
        if not to:
            return
        if add_prefix_rule(self, pre, to):
            prefix_changed(self.app)
            self.load_candidates()

    def pick_product(self):
        sel = self.ptree.selection()
        if not sel:
            return
        no = int(sel[0])
        name = self.ptree.item(sel[0], "values")[1]
        if self.product and self.product["no"] == no:
            return
        self.product = {"no": no, "name": name}

        def work(prog):
            vs = C.get_variants(no)
            inv = {}
            for i, v in enumerate(vs, 1):
                prog(i, len(vs), "카페24 품목 재고 불러오는 중")
                try:
                    inv[v["code"]] = S.variant_qty(no, v)
                except Exception as e:
                    op.log.info(f"[연결 창] 재고 확인 실패 {no}/{v['code']}: {e}")
                    inv[v["code"]] = "?"
            return vs, inv

        def done(res):
            # 불러오는 동안 창을 닫았거나 다른 상품을 골랐으면 버림
            if not self._alive() or not self.product or self.product["no"] != no:
                return
            vs, self.inv = res
            self.variants = vs
            if len(vs) == 1 and vs[0]["label"] == "(옵션 없음)" and not self.entry.get("mode"):
                self.mode.set("single")
            self.build_rows()
        self.app.bg(work, done, "카페24 옵션·재고 불러오는 중...", busy=False, progress=True)

    # ---- 옵션 줄
    def _vals(self):
        ign = set(self.entry.get("ignore", []))
        return [(v["name"], v["value"]) for v in self.line["variations"]]

    def build_rows(self):
        for w in self.rows_fr.winfo_children():
            w.destroy()
        self.widgets = []
        if not self.variants:
            ttk.Label(self.rows_fr, text="① 에서 카페24 상품을 먼저 고르세요.", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
            return
        labels = [v["label"] for v in self.variants]
        mode = self.mode.get()
        ign = set(self.entry.get("ignore", []))
        same_product = self.entry.get("product_no") == self.product["no"]
        self.units = S.load_units()
        if mode == "each" and self.line["variations"]:
            items = [(v["name"], v["value"]) for v in self.line["variations"]]
        elif mode == "combo" and self.line["variations"]:
            combos = S.combo_rows(self.line["variations"], ign)       # 값이 여러 개면 가능한 조합 전부
            if len(combos) > 300:
                messagebox.showwarning("옵션 조합", f"가능한 조합이 {len(combos):,}개라 너무 많습니다. 앞의 300개만 보여 드립니다.\n"
                                                  "재고와 상관없는 옵션은 '옵션마다 따로'에서 '재고 무관'으로 체크한 뒤 다시 고르세요.",
                                       parent=self)
                combos = combos[:300]
            self._combo_vals = dict(combos)
            items = [("__combo__", k) for k, _v in combos]
        else:
            items = [("__single__", "(모든 옵션)")]
        for i, (name, value) in enumerate(items, 1):
            text = f"{name}: {value}" if name not in ("__combo__", "__single__") else value
            ttk.Label(self.rows_fr, text=text, wraplength=260).grid(row=i, column=0, sticky="w", pady=3)
            iv = tk.BooleanVar(value=name in ign or S.is_ignorable(name))
            cb_ign = ttk.Checkbutton(self.rows_fr, variable=iv)
            if name not in ("__combo__", "__single__"):
                cb_ign.grid(row=i, column=1, sticky="w")
            box = ttk.Combobox(self.rows_fr, values=labels, width=34, state="readonly")
            box.grid(row=i, column=2, sticky="w", padx=(0, 10))
            wide_combobox(box)
            stock_lb = ttk.Label(self.rows_fr, text="", width=9, anchor="e")
            stock_lb.grid(row=i, column=3, sticky="e", padx=(0, 10))
            pv = tk.BooleanVar(value=False)
            ttk.Checkbutton(self.rows_fr, variable=pv).grid(row=i, column=4, sticky="w")
            mv = tk.BooleanVar(value=False)
            ttk.Checkbutton(self.rows_fr, variable=mv).grid(row=i, column=5, sticky="w")
            mult = tk.StringVar(value="")
            sb = ttk.Spinbox(self.rows_fr, from_=1, to=1000, textvariable=mult, width=6)
            sb.grid(row=i, column=6, sticky="w")
            auto_lb = ttk.Label(self.rows_fr, text="", wraplength=260, justify="left", style="Hint.TLabel")
            auto_lb.grid(row=i, column=7, sticky="w", padx=(10, 0))
            # 이미 연결된 값 또는 추천값
            cur = None
            if same_product:
                if name == "__single__":
                    cur = self.entry.get("single")
                elif name == "__combo__":
                    cur = self.entry.get("combo", {}).get(value)
                else:
                    cur = self.entry.get("each", {}).get(name, {}).get(value)
            if cur:
                code = cur["variant"]
            elif len(self.variants) == 1:
                code = self.variants[0]["code"]
            elif name == "__combo__":
                code = S.suggest_combo(getattr(self, "_combo_vals", {}).get(value, []), self.variants)
            elif name != "__single__":
                code = S.suggest_variant(value, self.variants)
            else:
                code = None
            if code:
                lab = next((v["label"] for v in self.variants if v["code"] == code), "")
                box.set(lab)
            skip_saved = bool(cur and (cur.get("skip") or cur.get("manual")))
            w = {"name": name, "value": value, "ign": iv, "box": box, "mult": mult, "auto_lb": auto_lb, "sb": sb,
                 "auto": None, "saved": cur.get("mult", 1) if cur and not skip_saved else None, "stock_lb": stock_lb,
                 "plenty": pv, "plenty_saved": skip_saved, "manual": mv}   # 저장된 재고 풀어둠/직접 차감은 자동으로 안 바꿈
            mv.set(bool(cur and cur.get("manual")))
            pv.set(bool(cur and cur.get("skip")))
            pv.trace_add("write", lambda *a, w=w: self._toggle(w, "plenty"))
            mv.trace_add("write", lambda *a, w=w: self._toggle(w, "manual"))
            iv.trace_add("write", lambda *a, w=w: self._sync_ignore(w))
            if cur and not skip_saved:
                mult.set(str(cur.get("mult", 1)))
            self.widgets.append(w)
            self._lock_box(w)
            box.bind("<<ComboboxSelected>>", lambda e, w=w: self.update_auto(w))
            self.update_auto(w)
        for x in self.widgets:                       # 옵션이 여러 개인지 다 만든 뒤 다시 계산
            self.update_auto(x, from_check=True)
        self._align_header()
        if mode == "combo":
            hint(self.rows_fr, "조합에서 빠질 옵션(재고 무관)은 '옵션마다 따로'에서 체크한 뒤 다시 '옵션 조합'을 고르세요.",
                 row=len(items) + 1, column=0, columnspan=8, sticky="w", pady=(4, 0), wrap=800)

    HDR = ("Etsy 옵션", "재고 무관", "카페24 품목", "카페24 재고", "재고 풀어둠·안 뺌", "직접 차감", "배수", "자동 계산")

    def _align_header(self):
        """옵션 줄 위의 고정 제목 줄을 옵션 줄 칸 폭에 맞춤"""
        for w in self.hdr_fr.winfo_children():
            w.destroy()
        if not self.widgets:
            return
        hl = []
        for c, h in enumerate(self.HDR):
            lb = ttk.Label(self.hdr_fr, text=h, font=T.f(9, True))
            lb.grid(row=0, column=c, sticky="w", padx=(0, 10))
            hl.append(lb)
        self.update_idletasks()
        need = {}
        for ch in self.rows_fr.winfo_children():
            try:
                gi = ch.grid_info()
            except tk.TclError:
                continue
            if not gi or int(gi.get("columnspan", 1)) > 1:
                continue
            pad = gi.get("padx", 0)
            pad = sum(int(float(x)) for x in re.findall(r"-?\d+(?:\.\d+)?", str(pad)))
            c = int(gi["column"])
            need[c] = max(need.get(c, 0), ch.winfo_reqwidth() + pad)
        for c, lb in enumerate(hl):
            w = max(need.get(c, 0), lb.winfo_reqwidth() + 10)
            self.rows_fr.columnconfigure(c, minsize=w)
            self.hdr_fr.columnconfigure(c, minsize=w)
            lb.grid_configure(padx=0)

    def _toggle(self, w, which):
        """재고 풀어둠·안 뺌과 직접 차감은 둘 중 하나만"""
        if getattr(self, "_toggling", False):
            return
        self._toggling = True
        try:
            if which == "manual" and w["manual"].get():
                w["plenty"].set(False)
                w["plenty_saved"] = True              # 자동으로 다시 체크되지 않게
            elif which == "plenty" and w["plenty"].get():
                w["manual"].set(False)
            self._lock_box(w)
            self.update_auto(w, from_check=True)
        finally:
            self._toggling = False

    def _lock_box(self, w):
        """직접 차감이면 카페24 품목 칸을 비우고 잠금 (저장할 때도 품목 없이 저장됨). 풀면 원래 고른 품목으로"""
        box = w["box"]
        if w["manual"].get():
            if box.get():
                w["box_before"] = box.get()
                box.set("")
            box.state(["disabled"])
        else:
            box.state(["!disabled"])
            if not box.get() and w.get("box_before"):
                box.set(w["box_before"])

    def _sync_ignore(self, w):
        """'재고 무관'은 옵션 이름 전체에 적용 → 같은 이름 줄을 모두 같이 체크/해제"""
        if getattr(self, "_syncing", False):
            return
        self._syncing = True
        try:
            on = w["ign"].get()
            for x in self.widgets:
                if x is not w and x["name"] == w["name"] and x["ign"].get() != on:
                    x["ign"].set(on)
            for x in self.widgets:
                if x["name"] == w["name"]:
                    self.update_auto(x, from_check=True)
        finally:
            self._syncing = False

    def update_auto(self, w, from_check=False):
        """카페24 재고 표시 + 넉넉이면 '안 뺌' 자동 체크, Etsy 단위 ÷ 카페24 단위 → 배수 추천. 사람이 고친 값은 건드리지 않음"""
        lab = w["box"].get()
        code = next((v["code"] for v in self.variants if v["label"] == lab), None)
        q = self.inv.get(code, "") if code else ""
        plenty = isinstance(q, int) and S.is_plenty(q)
        w["stock_lb"].configure(text="관리 안 함" if q is None else (f"{q:,}" if isinstance(q, int) else str(q)),
                                foreground=T.GREEN if plenty else "")
        if not from_check and not w["plenty_saved"]:
            w["plenty"].set(plenty)
            w["plenty_auto"] = plenty
        if w["ign"].get() and w["name"] not in ("__combo__", "__single__"):
            w["auto_lb"].configure(text="재고 무관 — 이 옵션은 차감 계산에서 빠짐", foreground=T.MUTED)
            return
        if w["manual"].get() or w["plenty"].get():
            if w["manual"].get():
                w["auto_lb"].configure(text="✋ 직접 차감 — 팔리면 자동으로 빼지 않고 '직접 차감 목록'에 올림",
                                       foreground=T.BLUE)
            else:
                why = (f"재고 {q:,} — {S.plenty_limit():,}개 이상이라 앞으로 이 품목은 빼지 않음 (고정)" if plenty
                       else "이 품목은 앞으로 빼지 않음 (고정)")
                w["auto_lb"].configure(text="✓ " + why, foreground=T.GREEN)
            if w["mult"].get() != "-":
                w["mult_before"] = w["mult"].get()
            w["mult"].set("-")
            w["sb"].state(["disabled"])
            return
        if w["mult"].get() == "-":                    # 재고 풀어둠·안 뺌을 풀었으면 배수 칸 되살리기
            w["sb"].state(["!disabled"])
            w["mult"].set(w.pop("mult_before", "") if w.get("mult_before") not in (None, "-") else "")
            w["auto"] = None
        ov = w["value"] if w["name"] not in ("__combo__", "__single__") else None
        others = [v for v in self.line["variations"] if v["name"] != w["name"]] if self.line.get("tid") else []
        eu, src = S.etsy_unit(self.line["title"], ov, others, self.units)
        cu = S.cafe24_unit(self.product["name"] if self.product else "", w["box"].get(), self.units)
        names = {x["name"] for x in self.widgets if x["name"] not in ("__combo__", "__single__") and not x["ign"].get()}
        multi_bad = False
        if src == "상품명" and len(names) > 1:           # '2 pcs' + 옵션 2개 → 옵션 하나당 1개
            per = S.per_option_unit(eu, len(names))
            if per is None:
                multi_bad = True
            else:
                eu = per
        k, why = S.suggest_mult(eu, cu)
        if multi_bad:
            why += f"  ※ 옵션이 여러 개({', '.join(sorted(names))})인데 상품명의 개수가 옵션 수와 달라 옵션마다인지 합쳐서인지 확인하세요"
        cur = w["mult"].get().strip()
        if w["saved"] is None and (cur == "" or (w["auto"] is not None and cur == str(w["auto"]))):
            w["mult"].set(str(k) if k else "")
        w["auto"] = k
        if k is None or "※" in why:
            w["auto_lb"].configure(text="⚠ " + why, foreground=T.ORANGE)
        elif w["saved"] is not None and int(w["saved"]) != k:
            w["auto_lb"].configure(text=f"{why}  (저장된 배수 {w['saved']}와 다름)", foreground=T.ORANGE)
        else:
            w["auto_lb"].configure(text="✓ " + why, foreground=T.GREEN)

    def bulk_mult(self):
        """모든 줄(재고 무관·재고 풀어둠·직접 차감 줄 제외)의 배수를 한 번에"""
        rows = [w for w in self.widgets if not w["ign"].get() and not w["plenty"].get() and not w["manual"].get()]
        if not rows:
            messagebox.showinfo("배수 한꺼번에", "바꿀 줄이 없습니다.", parent=self)
            return
        n = simpledialog.askinteger("배수 한꺼번에", f"{len(rows)}줄의 배수를 모두 몇으로 할까요?\n"
                                                 "(Etsy 1개 팔리면 카페24에서 뺄 개수 — 예: 이니셜 2 pcs + 옵션 2개면 1)",
                                    parent=self, minvalue=1, maxvalue=1000)
        if not n:
            return
        for w in rows:
            w["mult"].set(str(n))
            w["auto"] = None
            w["saved"] = n                       # 사람이 정한 값 → 자동 계산이 덮어쓰지 않음
            w["auto_lb"].configure(text=f"✓ 배수 {n} (한꺼번에 정함)", foreground=T.GREEN)

    def open_units(self):
        UnitWindow(self, on_close=self.build_rows)

    # ---- 저장
    def save(self):
        if not self.product:
            messagebox.showwarning("연결", "① 카페24 상품을 골라 주세요.", parent=self)
            return
        if not self.variants:
            messagebox.showwarning("연결", "카페24 옵션을 불러오는 중입니다. 잠시 후 다시 눌러 주세요.", parent=self)
            return
        by_label = {v["label"]: v["code"] for v in self.variants}
        e = self.entry if self.entry.get("product_no") == self.product["no"] else {}
        e.update({"product_no": self.product["no"], "product_name": self.product["name"], "mode": self.mode.get(),
                  "etsy_title": self.line["title"]})
        # 사람이 확인하고 저장 → 지금 모습을 새 기준으로 (바뀐 상품 확인용)
        e.pop("review", None)
        if e.get("seen") and e["seen"].get("c24_name") == self.product["name"]:
            e["snap"] = dict(e["seen"])
        else:
            e["snap"] = {"etsy_title": self.line["title"], "etsy_opts": None, "c24_name": self.product["name"],
                         "c24_vars": sorted(v["label"] for v in self.variants)}
        if self.line.get("tid"):
            e["ok_titles"] = sorted(set(e.get("ok_titles", [])) | {self.line["title"]})[-20:]
        ign = set(e.get("ignore", []))
        for w in self.widgets:
            name, value = w["name"], w["value"]
            if name not in ("__combo__", "__single__"):
                (ign.add if w["ign"].get() else ign.discard)(name)
                if w["ign"].get():
                    continue
            lab = w["box"].get()
            if w["manual"].get():
                val = {"variant": "", "label": "(직접 차감)", "mult": 1, "manual": True}
                if name == "__single__":
                    e["single"] = val
                elif name == "__combo__":
                    e.setdefault("combo", {})[value] = val
                else:
                    e.setdefault("each", {}).setdefault(name, {})[value] = val
                continue
            if w["plenty"].get():
                code = by_label.get(lab, "")
                q = self.inv.get(code)
                val = {"variant": code, "label": lab or "(안 뺌)", "mult": 1, "skip": "plenty"}
                if isinstance(q, int):
                    val["stock"] = q
                if name == "__single__":
                    e["single"] = val
                elif name == "__combo__":
                    e.setdefault("combo", {})[value] = val
                else:
                    e.setdefault("each", {}).setdefault(name, {})[value] = val
                continue
            if not lab:
                messagebox.showwarning("연결", f"'{value}'의 카페24 품목을 골라 주세요 (재고와 상관없는 옵션이면 '재고 무관' 체크).",
                                       parent=self)
                return
            raw = w["mult"].get().strip()
            if not raw:
                messagebox.showwarning("연결", f"'{value}'의 배수를 골라 주세요.\n\n{w['auto_lb'].cget('text').lstrip('⚠ ')}\n\n"
                                               "배수 = Etsy 1개가 팔렸을 때 카페24 재고에서 뺄 개수", parent=self)
                return
            try:
                mult = max(1, int(float(raw)))
            except ValueError:
                messagebox.showwarning("연결", "배수는 숫자로 입력해 주세요.", parent=self)
                return
            val = {"variant": by_label[lab], "label": lab, "mult": mult}
            if name == "__single__":
                e["single"] = val
            elif name == "__combo__":
                e.setdefault("combo", {})[value] = val
            else:
                e.setdefault("each", {}).setdefault(name, {})[value] = val
        e["ignore"] = sorted(ign)
        names = {v["name"] for v in self.line["variations"]}
        if self.mode.get() == "each" and names and names <= ign and not e.get("single"):
            if not messagebox.askyesno(
                    "연결", "모든 옵션이 '재고 무관'이라 어떤 카페24 품목을 뺄지 정할 수 없습니다.\n"
                          "이대로면 주문이 들어왔을 때 '연결 필요 (품목)'으로 나옵니다.\n\n"
                          "위에서 '옵션 상관없이 하나'를 고르고 카페24 품목을 지정하는 것을 권합니다.\n\n"
                          "그래도 이대로 저장할까요?", parent=self, icon="warning"):
                return
        self.map[self.line["listing_id"]] = e
        S.save_map(self.map)
        self.destroy()
        self.on_saved()

    def _learn_valmap(self, by_label):
        """직접 고른 품목이 대응표에 없으면 '앞으로 이 값도 찾을까요?' (대응표에 있는 단어만)"""
        if self.mode.get() != "each":
            return
        vm = S.load_valmap()
        add = {}
        for w in self.widgets:
            if w["name"] in ("__combo__", "__single__") or w["ign"].get() or w["plenty"].get() or w["manual"].get():
                continue
            code = by_label.get(w["box"].get())
            var = next((v for v in self.variants if v["code"] == code), None)
            if not var or len(var["values"]) != 1:
                continue
            cv = str(var["values"][0]).strip()
            row = S.valmap_entry(w["value"], vm)
            if not row or not cv or any(S._c24_hit(cv, t) for t in row["cafe24"]):
                continue
            full = S._clean_val(w["value"])
            # 값이 단어 그대로면(Silver) 그 줄에 추가, 다른 말이 붙어 있으면(Matte Silver) 새 줄로 → 긴 단어가 먼저 맞음
            key = row["etsy"] if full.lower() == row["etsy"].strip().lower() else full
            add.setdefault(key, set()).add(cv)
        if not add:
            return
        lines = "\n".join(f"· Etsy {k} → 카페24 {', '.join(sorted(v))}" for k, v in add.items())
        if messagebox.askyesno("옵션 값 대응표", f"직접 고른 품목을 옵션 값 대응표에 추가할까요?\n\n{lines}\n\n"
                                              "추가하면 다른 상품에서도 이 값을 찾아 자동으로 연결합니다\n"
                                              "(한 상품에 후보가 둘 이상이면 사람이 고름).", parent=self):
            for k, vals in add.items():
                row = next((r for r in vm if r["etsy"].strip().lower() == k.lower()), None)
                if row:
                    row["cafe24"] = row["cafe24"] + sorted(vals - set(row["cafe24"]))
                else:
                    vm.append({"etsy": k, "cafe24": sorted(vals)})
            S.save_valmap(vm)
            op.log.info(f"[옵션 값 대응표] 추가 {lines}")


# ================================================================ 관리 탭: 카페24 카드
def build_admin_cards(parent, app, row_start, width=560):
    """관리 탭 왼쪽 기둥에 '카페24 연결'·'재고 차감 준비' 카드를 붙임. 돌려줌: refresh 함수"""
    cfg = C.load_config()
    cf = ttk.LabelFrame(parent, text=" 카페24 연결 (재고 차감용) ", padding=12)
    cf.grid(row=row_start, column=0, sticky="we", pady=(12, 0))
    status = ttk.Label(cf, text="", wraplength=width, justify="left")
    status.grid(row=0, column=0, columnspan=3, sticky="w")
    vars_ = {}
    for i, (key, label, show) in enumerate((("mall_id", "쇼핑몰 아이디", ""), ("client_id", "Client ID", ""),
                                             ("client_secret", "Client Secret", "●"), ("redirect_uri", "Redirect URI", ""))):
        ttk.Label(cf, text=label).grid(row=1 + i, column=0, sticky="w", pady=2)
        v = tk.StringVar(value=cfg.get(key, ""))
        ttk.Entry(cf, textvariable=v, width=44, show=show).grid(row=1 + i, column=1, columnspan=2, sticky="w", pady=2)
        vars_[key] = v

    def save_cfg():
        c = {k: v.get().strip() for k, v in vars_.items()}
        C.save_config(c)
        app.flash("카페24 설정을 저장했습니다.")
        refresh()
    b0 = ttk.Button(cf, text="저장", command=save_cfg)
    b0.grid(row=5, column=1, sticky="w", pady=(4, 0))
    hint(cf, "카페24 개발자센터에서 만든 앱(ETSY 재고 차감)의 Client ID·Secret을 넣고 저장하세요. 이 값은 이 PC의 "
             "cafe24_config.json에만 저장됩니다.", row=6, column=0, columnspan=3, sticky="w", pady=(4, 8), wrap=width)

    def open_browser():
        c = C.load_config()
        if not C.ready(c):
            messagebox.showinfo("카페24 연결", "먼저 위 칸을 채우고 [저장]을 눌러 주세요.")
            return
        webbrowser.open(C.auth_url(c))
    b1 = ttk.Button(cf, text="① 브라우저 열기", command=open_browser)
    b1.grid(row=7, column=0, sticky="w")
    pasted = ttk.Entry(cf, width=30)
    pasted.grid(row=7, column=1, sticky="w", padx=6)

    def finish():
        url = pasted.get()

        def done(_):
            pasted.delete(0, "end")
            messagebox.showinfo("카페24 연결", "인증했습니다.")
            refresh()
        app.bg(lambda: C.finish_auth(C.load_config(), url), done, "카페24 인증 중...")
    b2 = ttk.Button(cf, text="② 인증 완료", command=finish)
    b2.grid(row=7, column=2, sticky="w")
    hint(cf, "① [브라우저 열기]를 누르고 쇼핑몰 '대표운영자' 계정으로 로그인해 앱을 승인합니다.\n"
             "② 쇼핑몰 화면으로 이동하면 주소창 전체를 복사해 가운데 칸에 붙여넣고 [인증 완료]를 누릅니다. (1분 이내)",
         row=8, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    # 재고 차감 준비
    pf = ttk.LabelFrame(parent, text=" 재고 차감 준비 ", padding=12)
    pf.grid(row=row_start + 1, column=0, sticky="we", pady=(12, 0))
    plist = ttk.Label(pf, text="", wraplength=width, justify="left")
    plist.grid(row=0, column=0, columnspan=3, sticky="w")

    def refresh_products():
        def done(n):
            miss = C.products_missing()
            if miss:
                messagebox.showwarning("카페24 상품 목록", f"카페24 전체 상품 {miss[1]:,}개 중 {miss[0]:,}개만 받았습니다.\n"
                                                         "관리 탭 → 문제 확인 도구 → [실행 기록 보기] 내용을 Claude에게 보여 주세요.")
            else:
                messagebox.showinfo("카페24 상품 목록", f"카페24 상품 이름 목록 {n:,}개를 받았습니다.")
            refresh()
        app.bg(lambda prog: C.refresh_products(lambda n: prog(n, 0, f"카페24 상품 이름 받는 중 {n:,}개")),
               done, "카페24 상품 이름 받는 중...", progress=True)
    b3 = ttk.Button(pf, text="상품 이름 목록 새로 받기", command=refresh_products)
    b3.grid(row=1, column=0, sticky="w", pady=(4, 0))
    hint(pf, "재고가 아니라 상품번호·상품명만 받습니다 (상품 1만 개면 1~2분). '바뀐 상품 확인' 때마다 함께 새로 받고(못 했으면 7일마다), "
             "목록에 없는 새 상품은 연결 창에서 카페24를 바로 검색할 수 있어서 평소에는 누를 필요가 없습니다.",
         row=2, column=0, columnspan=3, sticky="w", pady=(4, 10), wrap=width)
    ttk.Label(pf, text="머리글자 대응표", font=T.f(10, True)).grid(row=3, column=0, sticky="w")
    ptree = ttk.Treeview(pf, columns=("etsy", "c24"), show="headings", height=5, selectmode="browse")
    ptree.heading("etsy", text="Etsy 머리글자")
    ptree.heading("c24", text="카페24 머리글자")
    ptree.column("etsy", width=140, anchor="center")
    ptree.column("c24", width=140, anchor="center")
    ptree.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
    er = ttk.Frame(pf)
    er.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
    e1, e2 = ttk.Entry(er, width=8), ttk.Entry(er, width=8)
    e1.pack(side="left")
    ttk.Label(er, text=" = ").pack(side="left")
    e2.pack(side="left")

    def add():
        if add_prefix_rule(pf, e1.get(), e2.get()):
            e1.delete(0, "end")
            e2.delete(0, "end")
            prefix_changed(app)

    def delete():
        sel = ptree.selection()
        if sel:
            S.save_prefix([r for r in S.load_prefix() if C.norm_code(r.get("etsy")) != sel[0]])
            op.log.info(f"[머리글자] 삭제 {sel[0]}")
            prefix_changed(app)
    ttk.Button(er, text="추가", command=add).pack(side="left", padx=6)
    ttk.Button(er, text="선택 삭제", command=delete).pack(side="left")
    hint(pf, "코드 앞 글자가 다른 경우만 적습니다 (예: PD = PDT). 재고 탭 [차감 대기] 위에서도 바로 추가할 수 있습니다. 하이픈·띄어쓰기 차이(CS-23 ↔ CS23)는 자동으로 맞춥니다.",
         row=6, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)
    pl = ttk.Frame(pf)
    pl.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
    ttk.Label(pl, text="재고 풀어둠 기준", font=T.f(10, True)).pack(side="left")
    plenty = tk.StringVar(value=str(S.plenty_limit()))
    ttk.Spinbox(pl, from_=0, to=1000000, increment=500, textvariable=plenty, width=9).pack(side="left", padx=(8, 4))
    ttk.Label(pl, text="개 이상이면 빼지 않음").pack(side="left")

    def save_plenty():
        try:
            n = max(0, int(float(plenty.get())))
        except ValueError:
            messagebox.showinfo("재고 풀어둠 기준", "숫자로 적어 주세요.")
            return
        st = op.load_settings()
        st["stock_plenty"] = n
        op.save_settings(st)
        plenty.set(str(n))
        app.flash(f"재고 풀어둠 기준을 {n:,}개로 저장했습니다." if n else "재고 풀어둠 기준을 껐습니다 (모든 품목을 뺍니다).")
    ttk.Button(pl, text="저장", command=save_plenty).pack(side="left", padx=6)
    hint(pf, "카페24 재고가 이 숫자 이상인 품목은 항상 있는 상품으로 보고, 차감할 때 빼지 않고 '재고 풀어둠 — 안 뺌'으로 끝냅니다. 0 = 끄기.",
         row=8, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    # 주문서에 재고 표시
    psv = tk.BooleanVar(value=bool(op.load_settings().get("print_stock", True)))

    def save_ps():
        st = op.load_settings()
        st["print_stock"] = bool(psv.get())
        op.save_settings(st)
        app.flash("작업용 주문서에 카페24 재고를 표시합니다." if psv.get() else "주문서에 카페24 재고를 표시하지 않습니다.")
    ttk.Checkbutton(pf, text="작업용 주문서에 카페24 재고 표시 (모자라면 '미')", variable=psv, command=save_ps).grid(
        row=9, column=0, columnspan=3, sticky="w", pady=(12, 0))
    hint(pf, "주문서를 만들 때마다 카페24 재고를 새로 받아, 작업용 쪽 상품 줄 오른쪽에 '재고 12  백금도금' 또는 '[미 2]'로 적습니다. "
             "숫자는 카페24 개수 기준이고, 주문이 들어온 순서대로 재고를 채우며 차감 대기에 남은 주문 몫은 먼저 뺍니다.",
         row=10, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    scv = tk.BooleanVar(value=bool(op.load_settings().get("print_short_count", True)))

    def save_sc():
        st = op.load_settings()
        st["print_short_count"] = bool(scv.get())
        op.save_settings(st)
        app.flash("주문서의 '미' 상자에 개수까지 적습니다." if scv.get() else "주문서의 '미' 상자에는 '미'만 적습니다.")
    ttk.Checkbutton(pf, text="미 부족 품목 주문서에 개수까지 표기하기", variable=scv, command=save_sc).grid(
        row=14, column=0, columnspan=3, sticky="w", pady=(12, 0))
    hint(pf, "켜면 '미 3조 6개'처럼 사입해야 할 개수까지, 끄면 상자 안에 '미'만 적습니다. "
             "(재고로 다 채울 수 없는 주문은 그 주문의 필요 수 전체가 '미' 개수입니다)",
         row=15, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    # 출력하면 재고 바로 차감
    dov = tk.BooleanVar(value=bool(op.load_settings().get("deduct_on_print")))

    def save_do():
        st = op.load_settings()
        if dov.get() and not messagebox.askyesno(
                "출력하면 재고 바로 차감", "켜면 주문서를 실제로 인쇄할 때마다(지금 출력·선택 출력·자동 출력) 그 주문의 재고를 "
                                        "카페24에서 바로 뺍니다.\n\n· 문제없는 줄은 바로 차감 (차감 기록에서 되돌리기 가능)\n"
                                        "· 재고가 모자란 품목은 0으로 맞추고 사입 목록 창을 띄움\n"
                                        "· 연결 필요·코드 다름·바뀐 상품은 차감 대기에 남기고 알림\n\n"
                                        "재고 탭의 연습 모드와 테스트 모드가 꺼져 있어야 동작합니다. 켤까요?"):
            dov.set(False)
            return
        st["deduct_on_print"] = bool(dov.get())
        op.save_settings(st)
        try:
            app.stock_tab._apply_texts()
        except Exception:
            pass
        app.flash("출력하면 재고를 바로 차감합니다." if dov.get() else "출력해도 바로 차감하지 않습니다 (차감 대기에서 직접 뺌).")
    ttk.Checkbutton(pf, text="주문서를 출력하면 재고를 바로 차감", variable=dov, command=save_do).grid(
        row=16, column=0, columnspan=3, sticky="w", pady=(12, 0))
    hint(pf, "켜면 인쇄가 끝난 직후 그 주문들을 바로 차감하고, 문제가 있을 때만 알립니다 (자동 출력은 오른쪽 아래 결과 창에). "
             "재고 풀어둠 고정인데 재고가 기준보다 적은 품목은 이번만 빼지 않고 알립니다. "
             "연습 모드·테스트 모드가 켜져 있으면 바로 차감하지 않습니다.",
         row=17, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    # 자체 상품코드(업체명)
    vsv = tk.BooleanVar(value=bool(op.load_settings().get("vendor_show")))
    vpv = tk.BooleanVar(value=bool(op.load_settings().get("vendor_print")))

    def save_vendor():
        st = op.load_settings()
        st["vendor_show"] = bool(vsv.get())
        st["vendor_print"] = bool(vpv.get())
        op.save_settings(st)
        try:
            app.stock_tab.fill()
        except Exception:
            pass
        app.flash("업체명 설정을 저장했습니다.")
    ttk.Checkbutton(pf, text="자체 상품코드(업체명) 가져오기", variable=vsv, command=save_vendor).grid(
        row=11, column=0, columnspan=3, sticky="w", pady=(12, 0))
    ttk.Checkbutton(pf, text="자체 상품코드(업체명) 주문서에 표기", variable=vpv, command=save_vendor).grid(
        row=12, column=0, columnspan=3, sticky="w", pady=(4, 0))
    hint(pf, "가져오기: 재고 탭 차감 대기 표의 '상태' 오른쪽에 '업체명' 칸을 보여 줍니다 (차감 미리 보기 창에도).\n"
             "주문서에 표기: 작업용 주문서의 재고 표기 바로 위에 작은 회색 글씨로 적습니다.\n"
             "카페24 상품의 '자체 상품코드' 칸 값을 그대로 씁니다 (받아 둔 카페24 상품 목록 기준 — 새 상품은 미리 연결이나 "
             "바뀐 상품 확인 뒤에 나타납니다). 비어 있는 상품은 아무것도 안 나옵니다.",
         row=13, column=0, columnspan=3, sticky="w", pady=(4, 0), wrap=width)

    # 바뀐 상품 확인
    wf = ttk.LabelFrame(parent, text=" 바뀐 상품 확인 ", padding=12)
    wf.grid(row=row_start + 2, column=0, sticky="we", pady=(12, 0))
    wst = ttk.Label(wf, text="", wraplength=width, justify="left")
    wst.grid(row=0, column=0, columnspan=3, sticky="w")
    tr = ttk.Frame(wf)
    tr.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Label(tr, text="확인 시간").pack(side="left")
    times = tk.StringVar(value=", ".join(W.load_times()))
    ttk.Entry(tr, textvariable=times, width=28).pack(side="left", padx=(8, 4))

    def save_times():
        t = W.parse_times(times.get())
        st = op.load_settings()
        st["watch_times"] = t
        op.save_settings(st)
        times.set(", ".join(t))
        app.flash(("바뀐 상품 확인 시간을 저장했습니다: " + ", ".join(t)) if t else "자동 확인을 껐습니다 (지금 확인 버튼으로만).")
        wrefresh()
    ttk.Button(tr, text="저장", command=save_times).pack(side="left", padx=4)
    b_now = ttk.Button(tr, text="지금 확인", style="Ghost.TButton",
                       command=lambda: app.stock_tab.run_watch(manual=True) if hasattr(app, "stock_tab") else None)
    b_now.pack(side="left", padx=4)
    hint(wf, "여러 시간을 쉼표로 적습니다 (예: 09:00, 13:00, 17:00). 비우고 저장하면 자동 확인을 끕니다. "
             "그 시간에 프로그램이 꺼져 있었으면 다음에 켤 때 바로 한 번 확인합니다. 확인은 뒤에서 조용히 하고(몇 분), "
             "그동안 주문서 인쇄 등은 그대로 쓸 수 있습니다.\n"
             "확인하는 것: 연결된 상품의 Etsy 상품명·옵션, 카페24 상품명·품목이 연결할 때와 조금이라도 다르면 '확인 필요' "
             "(확인 전까지 차감 안 함). 새로 올라온 Etsy 상품은 확실한 것만 미리 연결해 둡니다. "
             "차감할 때마다 그 주문의 상품만 한 번 더 확인합니다.",
         row=2, column=0, columnspan=3, sticky="w", pady=(6, 0), wrap=width)

    def wrefresh():
        st = W.load_state()
        t = W.load_times()
        wst.configure(text=(f"자동 확인: {', '.join(t)}" if t else "자동 확인: 꺼짐 (지금 확인 버튼으로만)")
                      + f"\n마지막 확인: {st.get('last', '')[:16] or '아직 없음'}"
                      + (f"\n{st['note']}" if st.get("note") else ""),
                      foreground=T.RED if str(st.get("note", "")).startswith("실패") else "")
    app.watch_admin_refresh = wrefresh
    wrefresh()

    # 백업·가져오기
    bkf = ttk.LabelFrame(parent, text=" 연결 데이터 백업·가져오기 ", padding=12)
    bkf.grid(row=row_start + 3, column=0, sticky="we", pady=(12, 0))
    bkr = ttk.Frame(bkf)
    bkr.grid(row=0, column=0, sticky="w")

    def do_backup():
        os.makedirs(BK.BACKUP_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(title="연결 데이터 백업", initialdir=BK.BACKUP_DIR,
                                            initialfile=BK.default_name(), defaultextension=".zip",
                                            filetypes=[("백업 파일", "*.zip")])
        if not path:
            return
        try:
            done = BK.make(path)
        except Exception as e:
            messagebox.showerror("백업", f"백업하지 못했습니다.\n\n{e}")
            return
        messagebox.showinfo("백업", f"백업했습니다 ({len(done)}개):\n{path}\n\n" + "\n".join(f"· {d}" for _n, d in done)
                            + "\n\n이 파일을 USB 등으로 옮겨, 새 PC의 [백업 불러오기]로 넣으면 됩니다.")

    def do_restore():
        if op.is_test():
            messagebox.showinfo("백업 불러오기", "테스트 모드를 끄고 불러와 주세요.")
            return
        path = filedialog.askopenfilename(title="백업 불러오기", initialdir=BK.BACKUP_DIR if os.path.isdir(BK.BACKUP_DIR) else None,
                                          filetypes=[("백업 파일", "*.zip")])
        if not path:
            return
        try:
            when, items = BK.inspect(path)
        except op.UserError as e:
            messagebox.showerror("백업 불러오기", str(e))
            return
        if not messagebox.askyesno("백업 불러오기", f"{when[:16]}에 만든 백업입니다. 아래 데이터를 이 PC에 덮어씁니다.\n\n"
                                   + "\n".join(f"· {d}" for _n, d in items)
                                   + "\n\n지금 이 PC의 데이터는 덮어쓰기 전에 backup 폴더에 자동으로 저장해 둡니다.\n"
                                     "(Etsy·카페24 인증과 프린터 설정은 바뀌지 않습니다)\n\n불러올까요?", icon="warning"):
            return
        try:
            safety, items = BK.restore(path)
        except Exception as e:
            messagebox.showerror("백업 불러오기", f"불러오지 못했습니다.\n\n{e}")
            return
        if hasattr(app, "stock_tab"):
            app.stock_tab.refresh()
        refresh()
        wrefresh()
        messagebox.showinfo("백업 불러오기", f"불러왔습니다 ({len(items)}개).\n\n덮어쓰기 전 데이터는 여기에 저장해 뒀습니다:\n{safety}\n\n"
                                         "설정(재고 풀어둠 기준, 확인 시간 등)까지 바뀌었으면 프로그램을 한 번 껐다 켜 주세요.")
    ttk.Button(bkr, text="연결 데이터 백업", command=do_backup).pack(side="left")
    ttk.Button(bkr, text="백업 불러오기", style="Ghost.TButton", command=do_restore).pack(side="left", padx=6)
    hint(bkf, "연결표·머리글자·단위표·연결 안 함·미리 연결 상태·차감 대기와 기록·뽑음 기록·출력 기록·라벨 묶음·설정을 "
              "zip 파일 하나로 저장합니다. "
              "새 PC로 옮길 때는 그 파일을 [백업 불러오기]로 넣고, Etsy·카페24는 새 PC에서 다시 인증하세요. "
              "직접 연결을 많이 해 둔 뒤에는 가끔 백업해 두는 것을 권합니다.",
         row=1, column=0, sticky="w", pady=(6, 0), wrap=width)

    def refresh():
        st, txt = C.token_status()
        status.configure(text=f"상태: {txt}", foreground=T.GREEN if st == "ok" else T.ORANGE if st == "warn" else T.RED)
        n = len(C.load_products().get("products", []))
        age = C.products_age_days()
        miss = C.products_missing()
        plist.configure(text=f"카페24 상품 이름 목록: {n:,}개" + (f" · {age:.0f}일 전에 받음" if age is not None else " · 아직 안 받음")
                        + (f"  ⚠ 카페24 전체 {miss[1]:,}개 중 일부만 받음" if miss else ""),
                        foreground=T.ORANGE if miss else "")
        ptree.delete(*ptree.get_children())
        for r in S.load_prefix():
            k = C.norm_code(r.get("etsy"))
            if k and not ptree.exists(k):
                ptree.insert("", "end", iid=k, values=(k, r.get("cafe24", "")))
    refresh()
    return refresh, [b0, b1, b2, b3]


def auto_refresh_products(app):
    """프로그램을 켤 때: 카페24 인증이 되어 있고 상품 이름 목록이 7일 넘었으면 뒤에서 조용히 다시 받기"""
    if C.token_status()[0] == "bad":
        return
    age = C.products_age_days()
    need_list = age is None or age >= 7 or "expected" not in C.load_products()   # 예전 방식으로 받은 목록이면 한 번 다시

    def work():
        try:
            C.keep_alive(7)                    # 인증 남은 기간이 7일 이하면 미리 연장
        except Exception as e:
            op.log.warning(f"카페24 인증 미리 연장 실패: {e}")
        if not need_list:
            return None
        try:
            return C.refresh_products()
        except Exception as e:
            op.log.warning(f"카페24 상품 이름 목록 자동 받기 실패: {e}")
            return None
    app.bg(work, None, busy=False)
