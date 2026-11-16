# -*- coding: utf-8 -*-
"""탭 내용을 스크롤할 수 있게 감싸는 틀. 창이 작아도 오른쪽·아래 스크롤바와 마우스 휠로 전부 볼 수 있음."""
import sys
import tkinter as tk
from tkinter import ttk

_AREAS = []


class ScrollArea(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, style="Page.TFrame")
        import theme
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=theme.BG)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1); self.columnconfigure(0, weight=1)
        self.inner = None
        self._win = None
        self._job = None
        self._last = None
        self.canvas.bind("<Configure>", lambda e: self._fit_soon())
        self.bind("<Map>", lambda e: self._fit_soon(), add="+")
        _AREAS.append(self)
        if len(_AREAS) == 1:
            root = self.winfo_toplevel()
            if sys.platform.startswith("linux"):
                root.bind_all("<Button-4>", lambda e: _wheel(e, -1), add="+")
                root.bind_all("<Button-5>", lambda e: _wheel(e, 1), add="+")
            else:
                root.bind_all("<MouseWheel>", lambda e: _wheel(e, -1 if e.delta > 0 else 1), add="+")
            install_wheel_guard(root)

    def set_inner(self, widget):
        """widget은 반드시 self.canvas를 부모로 만들어야 함"""
        self.inner = widget
        self._win = self.canvas.create_window(0, 0, window=widget, anchor="nw")
        widget.bind("<Configure>", lambda e: self._fit_soon(), add="+")

    def _fit_soon(self):
        """창 크기를 끄는 동안에는 미뤄 두었다가, 손을 멈추면 한 번만 다시 맞춤 (버벅임 줄이기)"""
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(60, self._fit)

    def _fit(self):
        self._job = None
        if not self.inner or not self.winfo_ismapped():
            return
        rw, rh = self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        key = (rw, rh, cw, ch)
        if key == self._last:                  # 바뀐 게 없으면 다시 그리지 않음
            return
        self._last = key
        # 창이 크면 내용을 창 크기에 맞춰 늘리고, 작으면 원래 크기 그대로 두고 스크롤
        self.canvas.itemconfigure(self._win, width=max(rw, cw), height=max(rh, ch))
        self.canvas.configure(scrollregion=(0, 0, max(rw, cw), max(rh, ch)))
        # 스크롤할 필요가 없으면 스크롤바 숨기기
        (self.vbar.grid if rh > ch else self.vbar.grid_remove)()
        (self.hbar.grid if rw > cw else self.hbar.grid_remove)()


def _wheel(event, direction):
    """마우스 휠: 목록·글상자는 자기 스크롤, 나머지는 감싸고 있는 스크롤 칸(탭·연결 창 옵션 칸)을 스크롤"""
    w = event.widget
    try:
        if isinstance(w, str) or w.winfo_class() in ("Treeview", "Text", "Listbox"):
            return            # 목록·글상자는 자기 스크롤을 씀
        if w.winfo_class() in ("TCombobox", "TSpinbox", "Spinbox"):
            return            # 아래 _guard가 처리
    except tk.TclError:
        return
    _scroll_up_from(w, direction)


def _scroll_up_from(w, direction):
    while w is not None:
        try:
            if isinstance(w, tk.Canvas) and str(w.cget("yscrollcommand")):
                top, bottom = w.yview()
                if top > 0 or bottom < 1:
                    w.yview_scroll(direction * 2, "units")
                return
        except tk.TclError:
            return
        w = getattr(w, "master", None)


def _guard(event, direction):
    """콤보상자·숫자 칸 위에서 휠을 굴려도 값은 안 바뀌고, 화면만 스크롤 (실수로 품목·배수가 바뀌는 것 막기)"""
    try:
        _scroll_up_from(event.widget, direction)
    except tk.TclError:
        pass
    return "break"


def install_wheel_guard(root):
    for cls in ("TCombobox", "TSpinbox", "Spinbox"):
        if sys.platform.startswith("linux"):
            root.bind_class(cls, "<Button-4>", lambda e: _guard(e, -1))
            root.bind_class(cls, "<Button-5>", lambda e: _guard(e, 1))
        else:
            root.bind_class(cls, "<MouseWheel>", lambda e: _guard(e, -1 if e.delta > 0 else 1))


def scrolled(notebook, build):
    """build(부모) → 탭 위젯. 스크롤 틀에 넣어 돌려줌: (틀, 탭)"""
    area = ScrollArea(notebook)
    tab = build(area.canvas)
    area.set_inner(tab)
    return area, tab
