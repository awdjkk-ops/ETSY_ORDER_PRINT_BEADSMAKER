# -*- coding: utf-8 -*-
"""ETSY 통합 창 - '라벨' 탭: 주문서를 뽑은 묶음 고르기 + 라벨지 미리보기 + 사용한 칸 지정 + 인쇄·엑셀
(조작 방법은 카페24 프로그램의 라벨 탭·웹 도구와 같음)"""
import datetime as dt
import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

import etsy_labels as L
import orderprint as op
import theme as T

NUM_W = 20          # 줄 번호 칸 폭(px)
CELL_W, CELL_H = 150, 24
GAP = 10
USED_BG, EMPTY_BG, BORDER = "#9E9E9E", "#FFFFFF", "#D0D0D0"
LABEL_XLSX_DIR = os.path.join(op.BASE_DIR, "라벨 엑셀")
WEEK = "월화수목금토일"


def hint(parent, text, **grid):
    style = "PageHint.TLabel" if grid.pop("page", False) else "Hint.TLabel"
    lb = ttk.Label(parent, text=text, style=style, wraplength=grid.pop("wrap", 300), justify="left")
    lb.grid(**grid)
    return lb


def batch_when(b):
    try:
        d = dt.datetime.strptime(b["time"], "%Y-%m-%d %H:%M:%S")
        return f"{d:%m-%d}({WEEK[d.weekday()]}) {d:%H:%M}"
    except Exception:
        return b.get("time", "")[5:16]


class LabelTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12, style="Page.TFrame")
        self.app = app
        self.batches = []          # 저장된 묶음 (최신 먼저)
        self.checked = set()       # 고른 묶음 id
        self.used = {}             # {페이지: {칸번호}}
        self.removed = []          # 지운 라벨의 원래 순번
        self.page = 0
        self.drag = None
        self.last_click = None
        self.last_drag_slot = None
        self._first_show = True
        self.f_txt = tkfont.Font(family=T.FONT, size=7)
        self.f_opt = tkfont.Font(family=T.FONT, size=7, weight="bold")
        self.f_name = tkfont.Font(family=T.FONT, size=9, weight="bold")
        self.f_num = tkfont.Font(family=T.FONT, size=7)

        left = ttk.Frame(self, style="Page.TFrame")
        left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(self, style="Page.TFrame")
        right.grid(row=0, column=1, sticky="nw", padx=(16, 0))

        # ---- 라벨 대상 (주문서를 뽑은 묶음)
        tf = ttk.LabelFrame(left, text=" 라벨 대상 ", padding=10)
        tf.grid(row=0, column=0, sticky="we")
        self.target_lb = ttk.Label(tf, text="", wraplength=330, justify="left", font=T.f(11, True))
        self.target_lb.grid(row=0, column=0, sticky="w")
        self.count_lb = ttk.Label(tf, text="", wraplength=330, justify="left")
        self.count_lb.grid(row=1, column=0, sticky="w", pady=(2, 6))
        cols = (("chk", "", 28), ("when", "주문서 뽑은 때", 120), ("n", "주문", 50), ("st", "라벨", 110))
        self.blist = ttk.Treeview(tf, columns=[c[0] for c in cols], show="headings", height=8, selectmode="none")
        for c, txt, w in cols:
            self.blist.heading(c, text=txt)
            self.blist.column(c, width=w, anchor="center" if c != "when" else "w", stretch=c == "st")
        self.blist.tag_configure("done", foreground=T.DIM)
        self.blist.tag_configure("deleted", foreground=T.DIM, background="#F4F4F2")
        self.blist.grid(row=2, column=0, sticky="we")
        self.blist.bind("<Button-1>", self.toggle_batch)
        self.blist.bind("<Button-3>", self.batch_menu)
        self.bmenu = tk.Menu(self, tearoff=0, font=T.f(10))
        bb = ttk.Frame(tf)
        bb.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(bb, text="라벨 안 뽑은 것만 고르기", command=self.pick_unprinted).pack(side="left")
        ttk.Button(bb, text="모두 해제", command=self.pick_none).pack(side="left", padx=6)
        self.show_deleted = tk.BooleanVar(value=False)
        ttk.Checkbutton(tf, text="삭제한 묶음도 보기 (되살리기)", variable=self.show_deleted,
                        command=self.fill_batches).grid(row=6, column=0, sticky="w", pady=(6, 0))
        hint(tf, "주문서를 뽑을 때마다 그 주문들이 한 묶음으로 저장됩니다. 줄을 누르면 고르기/해제이고, 처음 열면 라벨을 "
                 "아직 안 뽑은 묶음이 자동으로 골라져 있습니다. 어제나 그 전에 주문서만 뽑은 묶음도 여기서 고르면 됩니다.\n"
                 "필요 없는 묶음은 오른쪽 클릭 → '이 묶음 삭제'. 삭제한 묶음은 아래 '삭제한 묶음도 보기'를 켜고 "
                 "오른쪽 클릭 → '되살리기'로 다시 불러올 수 있습니다 (관리 탭의 보관 기간 동안).", row=4, column=0, sticky="w", pady=(6, 0), wrap=330)
        lg = ttk.Frame(tf)
        lg.grid(row=5, column=0, sticky="w", pady=(8, 0))
        for color, text in (("#FFC0CB", "일반"), ("#FFFF00", "묶음배송 확인 (같은 이름·주소의 주문 2건 이상)")):
            tk.Label(lg, text="  ", bg=color, relief="solid", bd=1).pack(side="left")
            ttk.Label(lg, text=f" {text}  ", style="Hint.TLabel").pack(side="left")

        # ---- 인쇄
        pf = ttk.LabelFrame(left, text=" 인쇄 ", padding=10)
        pf.grid(row=1, column=0, sticky="we", pady=(10, 0))
        self.b_print = ttk.Button(pf, text="라벨 인쇄", style="Main.TButton", command=lambda: self.output("pdf"))
        self.b_print.grid(row=0, column=0, sticky="we")
        hint(pf, "실제 크기(100%)로 인쇄합니다. 인쇄 창이 뜨면 프린터를 고르고 [기본 설정]에서 급지를 '뒷면 트레이'로 바꾼 뒤 "
                 "인쇄하세요. 크기 옵션이 보이면 '실제 크기'를 고르세요.", row=1, column=0, sticky="w", pady=(2, 8), wrap=330)
        self.b_xlsx = ttk.Button(pf, text="라벨 엑셀 저장", command=lambda: self.output("xlsx"))
        self.b_xlsx.grid(row=2, column=0, sticky="we")
        hint(pf, "웹 도구와 같은 모양의 라벨 엑셀을 '라벨 엑셀' 폴더에 저장합니다.", row=3, column=0, sticky="w",
             pady=(2, 0), wrap=330)

        # ---- 사용한 칸 지정 방법
        kf = ttk.LabelFrame(left, text=" 사용한 칸 지정 방법 ", padding=10)
        kf.grid(row=2, column=0, sticky="we", pady=(10, 0))
        for i, (k, v) in enumerate((("클릭", "그 칸을 사용함 / 다시 누르면 해제"),
                                    ("드래그", "지나가는 칸을 한 번에 칠하기"),
                                    ("Shift + 클릭", "마지막으로 클릭한 칸부터 여기까지 전부 사용함"),
                                    ("오른쪽 클릭", "그 라벨 지우기 (뒤의 라벨이 당겨져 채워짐)"))):
            ttk.Label(kf, text=k, font=T.f(9, True), width=11).grid(row=i, column=0, sticky="nw", pady=1)
            ttk.Label(kf, text=v, wraplength=220, justify="left").grid(row=i, column=1, sticky="w", pady=1)
        hint(kf, "회색 칸은 건너뛰고 위→아래, 왼쪽 줄부터 채워집니다. 페이지마다 따로 지정할 수 있고, "
                 "지정과 지우기는 이번 인쇄에만 적용됩니다.", row=4, column=0, columnspan=2, sticky="w", pady=(6, 0), wrap=330)
        ttk.Button(kf, text="모든 페이지 초기화", command=self.reset_all).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ---- 오른쪽: 미리보기
        nav = ttk.Frame(right, style="Page.TFrame")
        nav.grid(row=0, column=0, sticky="we")
        self.nav_btns = []
        for text, cmd in (("◀◀ 맨 앞", lambda: self.go(first=True)), ("◀ 이전", lambda: self.go(-1))):
            b = ttk.Button(nav, text=text, style="Page.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            self.nav_btns.append(b)
        self.page_lb = ttk.Label(nav, text="페이지 - / -", style="PageTitle.TLabel", width=14, anchor="center")
        self.page_lb.pack(side="left", padx=8)
        for text, cmd in (("다음 ▶", lambda: self.go(1)), ("맨 뒤 ▶▶", lambda: self.go(last=True))):
            b = ttk.Button(nav, text=text, style="Page.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            self.nav_btns.append(b)
        width = 5 * (NUM_W + CELL_W) + 4 * GAP + 20
        self.cv = tk.Canvas(right, width=width, height=29 * CELL_H + 16, background=T.CARD,
                            highlightthickness=1, highlightbackground=T.LINE, cursor="hand2")
        self.cv.grid(row=1, column=0, pady=(8, 6))
        self.cv.bind("<Button-1>", self.press)
        self.cv.bind("<B1-Motion>", self.motion)
        self.cv.bind("<ButtonRelease-1>", self.release)
        self.cv.bind("<Button-3>", self.right_click)
        self.menu = tk.Menu(self, tearoff=0, font=T.f(10))
        bot = ttk.Frame(right, style="Page.TFrame")
        bot.grid(row=2, column=0, sticky="we")
        self.info_lb = ttk.Label(bot, text="", style="Page.TLabel")
        self.info_lb.pack(side="left")
        ttk.Button(bot, text="이 페이지 초기화", style="Page.TButton", command=self.reset_page).pack(side="right")
        rb = ttk.Frame(right, style="Page.TFrame")
        rb.grid(row=3, column=0, sticky="we", pady=(6, 0))
        self.removed_lb = ttk.Label(rb, text="", style="Page.TLabel", foreground=T.ORANGE)
        self.removed_lb.pack(side="left")
        self.b_restore = ttk.Button(rb, text="지운 항목 모두 복원", style="Page.TButton", command=self.restore_all)
        self.b_restore.pack(side="right")
        self.b_undo = ttk.Button(rb, text="되돌리기", style="Page.TButton", command=self.undo_remove)
        self.b_undo.pack(side="right", padx=(0, 6))
        self.buttons = [self.b_print, self.b_xlsx]
        self.reload()

    # ------------------------------------------------------------ 묶음
    def reload(self, keep=True):
        """저장된 묶음 다시 읽기. 처음에는 라벨 안 뽑은 묶음을 자동으로 고름"""
        self.batches = list(reversed(op.load_label_batches()))
        ids = {b["id"] for b in self.batches}
        if self._first_show or not keep:
            self.checked = {b["id"] for b in self.batches if not b.get("label_printed") and not b.get("deleted")}
            self._first_show = False
        else:
            self.checked &= {b["id"] for b in self.batches if not b.get("deleted")}
        self.fill_batches()
        self.reset_marks()

    def on_show(self):
        """탭을 열 때: 새로 뽑은 묶음이 생겼으면 목록에 추가 (라벨 안 뽑은 새 묶음은 자동으로 고름)"""
        old = {b["id"] for b in self.batches}
        new = [b for b in reversed(op.load_label_batches()) if b["id"] not in old]
        if new:
            self.batches = list(reversed(op.load_label_batches()))
            self.checked |= {b["id"] for b in new if not b.get("label_printed") and not b.get("deleted")}
            self.fill_batches()
            self.reset_marks()

    def fill_batches(self):
        self.blist.delete(*self.blist.get_children())
        show_del = self.show_deleted.get() if hasattr(self, "show_deleted") else False
        for b in self.batches:
            if b.get("deleted"):
                if show_del:
                    self.blist.insert("", "end", iid=b["id"], tags=("deleted",),
                                      values=("–", batch_when(b), f"{len(b['orders'])}건", f"삭제됨 {b['deleted'][5:]}"))
                continue
            on = b["id"] in self.checked
            st = f"✓ {b['label_printed'][5:]} 뽑음" if b.get("label_printed") else "안 뽑음"
            self.blist.insert("", "end", iid=b["id"], tags=("done",) if b.get("label_printed") and not on else (),
                              values=("☑" if on else "☐", batch_when(b), f"{len(b['orders'])}건", st))

    def _batch(self, bid):
        return next((b for b in self.batches if b["id"] == bid), None)

    def batch_menu(self, e):
        row = self.blist.identify_row(e.y)
        b = self._batch(row) if row else None
        if not b:
            return
        self.bmenu.delete(0, "end")
        what = f"{batch_when(b)} · 주문 {len(b['orders'])}건"
        if b.get("deleted"):
            self.bmenu.add_command(label=f"이 묶음 되살리기 — {what}", command=lambda: self.restore_batch(b))
        else:
            self.bmenu.add_command(label=f"이 묶음 삭제 — {what}", command=lambda: self.delete_batch(b))
        self.bmenu.tk_popup(e.x_root, e.y_root)

    def delete_batch(self, b):
        if not messagebox.askyesno("묶음 삭제", f"{batch_when(b)}에 주문서를 뽑은 묶음(주문 {len(b['orders'])}건)을 "
                                              "라벨 목록에서 삭제할까요?\n\n주문서 출력 기록과 '뽑음' 표시에는 영향이 없고, "
                                              "'삭제한 묶음도 보기'를 켜면 다시 되살릴 수 있습니다."):
            return
        op.set_label_batch_deleted({b["id"]}, True)
        self.checked.discard(b["id"])
        self.batches = list(reversed(op.load_label_batches()))
        self.fill_batches()
        self.reset_marks()
        self.app.flash("묶음을 삭제했습니다. '삭제한 묶음도 보기'에서 되살릴 수 있어요.")

    def restore_batch(self, b):
        op.set_label_batch_deleted({b["id"]}, False)
        self.batches = list(reversed(op.load_label_batches()))
        self.fill_batches()
        self.app.flash("묶음을 되살렸습니다. 줄을 눌러 고르면 라벨에 들어갑니다.")

    def toggle_batch(self, e):
        row = self.blist.identify_row(e.y)
        if not row:
            return
        b = self._batch(row)
        if not b or b.get("deleted"):
            return
        (self.checked.discard if row in self.checked else self.checked.add)(row)
        self.fill_batches()
        self.reset_marks()

    def pick_unprinted(self):
        self.checked = {b["id"] for b in self.batches if not b.get("label_printed") and not b.get("deleted")}
        self.fill_batches()
        self.reset_marks()

    def pick_none(self):
        self.checked = set()
        self.fill_batches()
        self.reset_marks()

    def reset_marks(self):
        self.used, self.removed, self.page, self.last_click = {}, [], 0, None
        self.draw()

    def chosen(self):
        """고른 묶음 (주문서를 뽑은 순서: 오래된 것 먼저)"""
        return [b for b in reversed(self.batches) if b["id"] in self.checked and not b.get("deleted")]

    def orders(self):
        out, seen = [], set()
        for b in self.chosen():
            for o in b["orders"]:
                if o["id"] not in seen:          # 같은 주문을 두 번 뽑은 경우 한 번만
                    seen.add(o["id"])
                    out.append(o)
        return out

    # ------------------------------------------------------------ 그리기
    def _pages(self):
        orders = self.orders()
        if not orders:
            return None
        seq = L.drop_removed(L.number(L.build_sequence(orders)), self.removed)
        return L.paginate(seq, self.used)

    def _cut(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _xy(self, slot):
        col, g = divmod(slot - 1, 29)
        return 10 + col * (NUM_W + CELL_W + GAP) + NUM_W, 8 + g * CELL_H

    def draw(self):
        cv = self.cv
        cv.delete("all")
        orders = self.orders()
        for b in (self.b_undo, self.b_restore):
            b.state(["!disabled"] if self.removed else ["disabled"])
        if not orders:
            self.target_lb.configure(text="고른 묶음 없음" if self.batches else "저장된 묶음 없음")
            self.count_lb.configure(text="왼쪽 목록에서 라벨을 뽑을 묶음을 골라 주세요." if self.batches else
                                    "출력 탭에서 주문서를 인쇄하면 여기에 묶음이 생깁니다.")
            self.page_lb.configure(text="페이지 - / -")
            self.info_lb.configure(text="")
            self.removed_lb.configure(text="")
            for b in self.nav_btns + self.buttons:
                b.state(["disabled"])
            return
        if not getattr(self.app, "busy", False):
            for b in self.buttons:
                b.state(["!disabled"])
        seq = L.build_sequence(orders)
        pages = self._pages()
        ykeys = L.yellow_keys(orders)
        self.page = max(0, min(self.page, len(pages) - 1))
        items = pages[self.page]
        used = self.used.get(self.page, set())
        ch = self.chosen()
        self.target_lb.configure(text=f"묶음 {len(ch)}개 · 주문 {len(orders)}건")
        n_used = sum(len(v) for v in self.used.values())
        n_left = len(seq) - len(self.removed)
        last_n = sum(1 for x in pages[-1] if x is not None)
        save = (f"\n마지막 장 {last_n}칸 사용 · {last_n}칸 지우면 {len(pages) - 1}장으로 줄어요"
                if len(pages) > 1 and last_n <= 10 else "")
        self.count_lb.configure(text=f"라벨 {n_left}칸 · {len(pages)}장" + (f" · 사용함 표시 {n_used}칸" if n_used else "")
                                + (f" · 지운 항목 {len(self.removed)}개" if self.removed else "") + save)
        self.removed_lb.configure(text=f"지운 항목 {len(self.removed)}개" if self.removed else "오른쪽 클릭으로 라벨을 지울 수 있습니다")
        self.page_lb.configure(text=f"페이지 {self.page + 1} / {len(pages)}")
        states = [self.page > 0, self.page > 0, self.page < len(pages) - 1, self.page < len(pages) - 1]
        for b, ok in zip(self.nav_btns, states):
            b.state(["!disabled"] if ok else ["disabled"])
        for col in range(5):
            gx = 10 + col * (NUM_W + CELL_W + GAP)
            for g in range(29):
                cv.create_text(gx + NUM_W - 4, 8 + g * CELL_H + CELL_H / 2, text=str(g + 1), anchor="e",
                               font=self.f_num, fill="#9A9A9A")
        for slot in range(1, 146):
            x, y = self._xy(slot)
            it = items[slot - 1]
            tag = f"s{slot}"
            if slot in used:
                cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill=USED_BG, outline=USED_BG, tags=tag)
                cv.create_text(x + CELL_W / 2, y + CELL_H / 2 - 1, text="사용함", fill="white", font=self.f_opt, tags=tag)
            elif it is None:
                cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill=EMPTY_BG, outline=BORDER, dash=(2, 2), tags=tag)
            elif it["type"] == "NAME":
                color = "#" + L.name_color(it, ykeys)[2:]
                cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill=color, outline=BORDER, tags=tag)
                cv.create_text(x + CELL_W / 2, y + CELL_H / 2 - 1, text=self._cut(it["text"], self.f_name, CELL_W - 8),
                               font=self.f_name, tags=tag)
            else:
                cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill="white", outline=BORDER, tags=tag)
                if it["line2"]:
                    cv.create_text(x + 4, y + 6, text=self._cut(it["line1"], self.f_txt, CELL_W - 8), anchor="w",
                                   font=self.f_txt, tags=tag)
                    cv.create_text(x + CELL_W / 2, y + 16, text=self._cut(it["line2"], self.f_opt, CELL_W - 8),
                                   font=self.f_opt, fill="#4F81BD", tags=tag)
                else:
                    cv.create_text(x + CELL_W / 2, y + CELL_H / 2 - 1, text=self._cut(it["line1"], self.f_txt, CELL_W - 8),
                                   font=self.f_txt, tags=tag)
        self.info_lb.configure(text=f"이 페이지에서 {len(used)}칸 사용됨으로 표시됨" if used else "이 페이지는 전체 칸 사용 가능")

    # ------------------------------------------------------------ 지우기·되돌리기
    def right_click(self, e):
        slot = self._slot_at(e.x, e.y)
        pages = self._pages()
        if slot is None or not pages:
            return
        it = pages[self.page][slot - 1]
        if it is None:
            return
        name = it["text"] if it["type"] == "NAME" else it["line1"]
        what = "이름 칸" if it["type"] == "NAME" else "라벨"
        self.menu.delete(0, "end")
        self.menu.add_command(label=f"이 {what} 지우기 — {name[:24]}", command=lambda i=it["_i"]: self.remove(i))
        self.menu.tk_popup(e.x_root, e.y_root)

    def remove(self, i):
        self.removed.append(i)
        self.draw()

    def undo_remove(self):
        if self.removed:
            self.removed.pop()
            self.draw()

    def restore_all(self):
        if self.removed:
            self.removed = []
            self.draw()

    # ------------------------------------------------------------ 조작 (웹 도구와 같음)
    def _slot_at(self, x, y):
        for col in range(5):
            gx = 10 + col * (NUM_W + CELL_W + GAP) + NUM_W
            if gx <= x <= gx + CELL_W:
                g = int((y - 8) // CELL_H)
                if 0 <= g < 29:
                    return col * 29 + g + 1
        return None

    def _paint_cell(self, slot, on):
        x, y = self._xy(slot)
        self.cv.delete(f"s{slot}")
        if on:
            self.cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill=USED_BG, outline=USED_BG, tags=f"s{slot}")
            self.cv.create_text(x + CELL_W / 2, y + CELL_H / 2 - 1, text="사용함", fill="white", font=self.f_opt,
                                tags=f"s{slot}")
        else:
            self.cv.create_rectangle(x, y, x + CELL_W, y + CELL_H - 2, fill="#EFEFEF", outline=BORDER, tags=f"s{slot}")

    def press(self, e):
        if not self.orders():
            return
        slot = self._slot_at(e.x, e.y)
        if slot is None:
            return
        cur = self.used.setdefault(self.page, set())
        if (e.state & 0x0001) and self.last_click and self.last_click[0] == self.page:     # Shift+클릭
            lo, hi = sorted((self.last_click[1], slot))
            cur.update(range(lo, hi + 1))
            self.draw()
            return
        self.last_click = (self.page, slot)
        self.drag = slot not in cur
        (cur.add if self.drag else cur.discard)(slot)
        self.last_drag_slot = slot
        self._paint_cell(slot, self.drag)

    def motion(self, e):
        if self.drag is None:
            return
        slot = self._slot_at(e.x, e.y)
        if slot is None or slot == self.last_drag_slot:
            return
        self.last_drag_slot = slot
        cur = self.used.setdefault(self.page, set())
        (cur.add if self.drag else cur.discard)(slot)
        self._paint_cell(slot, self.drag)

    def release(self, e):
        if self.drag is not None:
            self.drag = None
            self.last_drag_slot = None
            self.draw()

    def go(self, d=0, first=False, last=False):
        pages = self._pages()
        if not pages:
            return
        self.page = 0 if first else len(pages) - 1 if last else max(0, min(len(pages) - 1, self.page + d))
        self.draw()

    def reset_page(self):
        self.used.pop(self.page, None)
        self.draw()

    def reset_all(self):
        self.used = {}
        self.draw()

    # ------------------------------------------------------------ 인쇄·저장
    def output(self, kind):
        orders = self.orders()
        pages = self._pages()
        if not pages:
            messagebox.showinfo("라벨", "왼쪽 목록에서 라벨을 뽑을 묶음을 골라 주세요.")
            return
        ykeys = L.yellow_keys(orders)
        ids = [b["id"] for b in self.chosen()]
        s = op.load_settings()
        dialog = bool(s.get("label_print_dialog", True))
        n = sum(1 for p in pages for x in p if x is not None)
        if kind == "pdf":
            msg = (f"라벨 {n}칸 ({len(pages)}장)을 인쇄할까요?" + (f" (지운 항목 {len(self.removed)}개 빼고)" if self.removed else "")
                   + "\n뒷면 트레이에 라벨지(폼텍 LS-3145)를 넣었는지 확인해 주세요.")
            if dialog:
                msg += "\n\n인쇄 창이 뜨면: 프린터 선택 → [기본 설정]에서 급지를 '뒷면 트레이'로 → 인쇄"
            if not messagebox.askyesno("라벨 인쇄", msg):
                return
        stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"

        def work():
            if kind == "pdf":
                os.makedirs(op.OUTPUT_DIR, exist_ok=True)
                path = os.path.join(op.OUTPUT_DIR, f"labels_{stamp}.pdf")
                L.write_label_pdf(pages, ykeys, path, s.get("label_cal") or {},
                                  fonts=(op.FONT_CANDIDATES["Kr"][-1][0], op.FONT_CANDIDATES["KrB"][-1][0]))
                if dialog:
                    self.app.q.put(("progress", (0, 0, "인쇄 창에서 프린터·트레이를 고르고 [인쇄]를 눌러 주세요")))
                res = op.print_label_pdf(path, self.app.cfg, dialog,
                                         None if dialog else lambda l, sec, slow: self.app.q.put(("wait", (l, sec, slow))))
                op.log.info(f"[라벨 인쇄] {n}칸 {len(pages)}장 · 묶음 {len(ids)}개 · {res}")
                return path, res
            ensure_openpyxl(lambda t: self.app.q.put(("progress", (0, 0, t))))
            os.makedirs(LABEL_XLSX_DIR, exist_ok=True)
            path = os.path.join(LABEL_XLSX_DIR, f"ETSY_라벨_{stamp}.xlsx")
            L.write_label_xlsx(pages, ykeys, path)
            op.log.info(f"[라벨 엑셀] {n}칸 {len(pages)}장 · {os.path.basename(path)}")
            return path, "xlsx"

        def done(r):
            path, res = r
            if res == "xlsx":
                ok = messagebox.askyesno("라벨 엑셀", f"{os.path.basename(path)} 저장 ({n}칸, {len(pages)}장)\n\n"
                                                    "고른 묶음을 '라벨 뽑음'으로 표시할까요?\n(엑셀로 인쇄할 거라면 '예')")
            elif res == "test":
                ok = messagebox.askyesno("라벨 인쇄 (테스트 모드)", "테스트 모드라서 인쇄하지 않고 라벨 PDF를 화면에 열었습니다.\n\n"
                                                    "테스트 기록에 '라벨 뽑음'으로 표시할까요?")
            elif res == "opened":
                ok = messagebox.askyesno("라벨 인쇄", "인쇄 프로그램(SumatraPDF)이 없어 PDF를 열었습니다. 직접 인쇄해 주세요 "
                                                    "(실제 크기 100%).\n\n인쇄를 마쳤으면 '예'를 눌러 '라벨 뽑음'으로 표시합니다.")
            elif res == "unsure":
                ok = messagebox.askyesno("라벨 인쇄", f"라벨 {n}칸 ({len(pages)}장)이 나왔나요?\n\n"
                                                    "인쇄 프로그램이 끝나면서 '코드 1'을 알렸습니다. 인쇄 창 방식에서는 잘 인쇄돼도 "
                                                    "이렇게 알릴 때가 있어 직접 확인을 부탁드립니다.\n\n"
                                                    "'예': 나왔음 → 고른 묶음을 '라벨 뽑음'으로 표시\n"
                                                    "'아니요': 안 나왔음(취소함) → 그대로 둠")
            else:
                ok = messagebox.askyesno("라벨 인쇄", f"라벨 {n}칸 ({len(pages)}장)이 잘 나왔나요?\n\n"
                                                    "'예'를 누르면 고른 묶음을 '라벨 뽑음'으로 표시합니다.\n"
                                                    "(인쇄 창에서 취소했거나 다시 뽑을 거라면 '아니요')")
            if ok:
                op.mark_label_printed(set(ids))
                self.reload(keep=False)
                self.app.flash("라벨 뽑음으로 표시했습니다.")
            if res == "xlsx":
                open_path(LABEL_XLSX_DIR)
        self.app.bg(work, done, "라벨 만드는 중...")


def ensure_openpyxl(report=None):
    """라벨 엑셀에 필요한 부품(openpyxl)이 없으면 설치"""
    try:
        import openpyxl  # noqa: F401
        return
    except ImportError:
        pass
    if report:
        report("엑셀 부품(openpyxl) 설치 중 · 처음 한 번만")
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    r = subprocess.run([exe, "-m", "pip", "install", "openpyxl"], capture_output=True, text=True,
                       creationflags=op.NO_WINDOW)
    if r.returncode != 0:            # 권한 문제면 사용자 폴더에 설치
        r = subprocess.run([exe, "-m", "pip", "install", "--user", "openpyxl"], capture_output=True, text=True,
                           creationflags=op.NO_WINDOW)
    if r.returncode != 0:
        op.log.warning("openpyxl 설치 실패: " + (r.stderr or "")[-500:])
        raise op.UserError("엑셀 부품(openpyxl) 설치에 실패했습니다. 인터넷 연결을 확인하거나 1_install.bat을 다시 실행해 주세요.")
    import importlib
    importlib.invalidate_caches()


def open_path(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])
