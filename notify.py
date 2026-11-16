# -*- coding: utf-8 -*-
"""자동 출력 진행·결과 작은 창 (화면 오른쪽 아래).
자동 출력과는 '따로' 실행되어 진행 상황 파일(auto_status.json)만 읽습니다.
이 창이 떠 있든, 잠금 화면 뒤에 있든, 닫히든 인쇄에는 아무 영향이 없습니다."""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
STATUS = os.path.join(BASE, "auto_status.json")
STOPFILE = os.path.join(BASE, "auto_stop.flag")
PIDFILE = os.path.join(BASE, "notify.pid")

import theme as T  # noqa: E402


def read_status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def close_previous():
    """이전 결과 창이 남아 있으면 닫고 새 창으로 대신함 (창이 쌓이지 않게)"""
    try:
        with open(PIDFILE) as f:
            old = int(f.read().strip())
        if old != os.getpid() and pid_alive(old):
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(old), "/F"], capture_output=True, creationflags=0x08000000)
            else:
                os.kill(old, 15)
    except Exception:
        pass
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


class Notice(tk.Tk):
    W, H = 440, 260

    def __init__(self):
        super().__init__()
        T.apply(self)
        self.title("Etsy 주문서 자동 출력")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{self.W}x{self.H}+{sw - self.W - 24}+{sh - self.H - 70}")
        self.configure(background=T.CARD)
        self.protocol("WM_DELETE_WINDOW", self.ok)
        self.dead_since = None

        head = tk.Frame(self, bg=T.CARD)
        head.pack(fill="x", padx=16, pady=(14, 4))
        self.dot = tk.Canvas(head, width=14, height=14, bg=T.CARD, highlightthickness=0)
        self.dot.create_oval(2, 2, 12, 12, fill=T.GREEN, outline="", tags="d")
        self.dot.pack(side="left")
        self.title_lb = tk.Label(head, text="자동 출력 중", bg=T.CARD, fg=T.INK, font=T.f(12, True))
        self.title_lb.pack(side="left", padx=6)
        self.time_lb = tk.Label(head, text="", bg=T.CARD, fg=T.MUTED, font=T.f(9))
        self.time_lb.pack(side="right")

        self.body = tk.Label(self, text="", bg=T.CARD, fg=T.INK, font=T.f(10), justify="left", anchor="w", wraplength=405)
        self.body.pack(fill="x", padx=16)
        self.warn = tk.Label(self, text="", bg=T.CARD, fg=T.RED, font=T.f(10, True), justify="left", anchor="w",
                             wraplength=405)
        self.warn.pack(fill="x", padx=16)
        self.pbar = ttk.Progressbar(self, length=405)
        self.pbar.pack(padx=16, pady=(8, 2), anchor="w")
        self.hint = tk.Label(self, text="", bg=T.CARD, fg=T.MUTED, font=T.f(9), justify="left", anchor="w", wraplength=405)
        self.hint.pack(fill="x", padx=16, pady=(4, 0))
        self.btns = tk.Frame(self, bg=T.CARD)
        self.btns.pack(side="bottom", fill="x", padx=16, pady=12)
        self.b_ok = ttk.Button(self.btns, text="확인", style="Accent.TButton", command=self.ok)
        self.b_ok.pack(side="right")
        self.b_reprint = ttk.Button(self.btns, text="이 묶음 다시 뽑기", command=self.reprint)
        self.b_stop = ttk.Button(self.btns, text="자동 출력 중지", style="Stop.TButton", command=self.stop)
        self.b_stop.pack(side="right", padx=6)
        self.stopping = False
        self.b_open = ttk.Button(self.btns, text="프로그램 열기", command=self.open_manager)
        self.b_open.pack(side="left")
        self.tick()

    # ------------------------------------------------------------
    def _dot(self, color):
        self.dot.itemconfigure("d", fill=color)

    def tick(self):
        st = read_status()
        started = str(st.get("started", ""))
        self.time_lb.configure(text=f"{started[5:10].replace('-', '/')} {started[11:16]}" if started else "")
        if not st.get("final"):
            self.show_running(st)
            self.after(700, self.tick)
        else:
            self.show_final(st)

    def show_running(self, st):
        self._dot(T.GREEN)
        phase = st.get("phase", "진행 중")
        self.title_lb.configure(text=f"자동 출력 중 · {phase}" + (" (테스트 모드)" if st.get("test") else ""))
        lines = []
        if st.get("wait_note"):
            lines.append(st["wait_note"])
        if st.get("count"):
            lines.append(f"주문 {st['count']}건" + (f" · 주문서 {st['pages']}장" if st.get("pages") else ""))
        if st.get("printer_progress"):
            lines.append(f"프린터: {st['printer_progress']}")
        self.body.configure(text="\n".join(lines) or "Etsy에서 새 주문을 확인하고 있습니다.")
        warn = list(st.get("printer_issues") or [])
        if st.get("stock_err"):
            warn.append(st["stock_err"])
        self.warn.configure(text="⚠ " + "\n⚠ ".join(warn) if warn else "")
        done, total = st.get("done", 0), st.get("total") or 0
        if total and phase not in ("인터넷 연결 기다리는 중", "Etsy 주문 확인 중"):
            self.pbar.stop()
            self.pbar.configure(mode="determinate", maximum=total, value=done)
        else:
            self.pbar.configure(mode="indeterminate")
            self.pbar.start(15)
        if not self.stopping:
            self.hint.configure(text="인쇄는 이 창과 상관없이 진행됩니다. 창을 닫아도 괜찮습니다.")
        if not pid_alive(st.get("pid")):              # 자동 출력이 도중에 사라졌는지
            self.dead_since = self.dead_since or dt.datetime.now()
            if (dt.datetime.now() - self.dead_since).seconds > 15:
                st.update(final=True, result="fail", phase="중간에 멈춤",
                          message="자동 출력이 끝까지 진행되지 않았습니다. 프로그램 → 관리 탭 → '실행 기록 보기'를 확인해 주세요.")
                self.show_final(st)
                return
        else:
            self.dead_since = None
        self.geometry(f"{self.W}x{max(self.H, self.winfo_reqheight())}")

    def show_final(self, st):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.b_stop.pack_forget()
        res = st.get("result")
        slot = str(st.get("started", ""))[11:16]
        last = st.get("last") or {}
        test = " (테스트 모드)" if st.get("test") else ""
        warn = []
        if st.get("stock_err"):
            warn.append(st["stock_err"] + " — 주문서에 '재고 확인 못 함'으로 표시")
        if res == "none":
            self._dot(T.MUTED)
            self.title_lb.configure(text=f"{slot} 자동 출력 · 새 주문 없음{test}")
            self.body.configure(text="새로 출력할 주문이 없어 인쇄하지 않았습니다.")
            self.hint.configure(text="")
        elif res in ("ok", "unknown"):
            self._dot(T.GREEN)
            self.title_lb.configure(text=f"✓ {slot} 자동 출력 완료{test}")
            self.body.configure(text=f"주문 {st.get('count', 0)}건 · 주문서 {st.get('pages', '?')}장\n"
                                     f"마지막 주문: {last.get('order_id', '')}  {last.get('name', '')}")
            self.hint.configure(text=("테스트 모드라 인쇄하지 않고 PDF로 열었습니다." if st.get("test") else
                                      "프린터에서 주문서가 모두 나왔는지 장 수를 확인하세요.")
                                     + ("\n(이 프린터는 상태를 알려주지 않아 인쇄 완료를 직접 확인하지 못했습니다)" if res == "unknown" else ""))
        elif res == "problem":
            self._dot(T.RED)
            self.title_lb.configure(text=f"⚠ {slot} 자동 출력 확인 필요", fg=T.RED)
            issues = ", ".join(st.get("printer_issues") or [])
            warn.insert(0, f"프린터 문제: {issues or '알 수 없음'}")
            self.body.configure(text=f"주문 {st.get('count', 0)}건 · {st.get('pages', '?')}장\n{st.get('printer_note', '')}")
            self.hint.configure(text="종이를 채우거나 걸린 종이를 빼면 대부분 멈춘 곳부터 이어서 인쇄됩니다. "
                                     "끝내 다 나오지 않으면 [이 묶음 다시 뽑기]를 누르세요.")
            if st.get("pdf") and os.path.exists(st["pdf"]):
                self.b_reprint.pack(side="right", padx=6)
        elif res == "stopped":
            self._dot(T.ORANGE)
            self.title_lb.configure(text=f"■ {slot} 자동 출력 중지됨", fg=T.ORANGE)
            n, sent = st.get("count", 0), st.get("sent", 0)
            if sent:
                self.body.configure(text=f"주문 {n}건 중 {sent}건은 프린터로 보내 '뽑음'으로 표시했습니다.\n"
                                         f"남은 {n - sent}건은 다음 출력에 나옵니다.")
            else:
                self.body.configure(text="인쇄하기 전에 중지했습니다." + (f"\n이번 주문 {n}건은 다음 출력에 나옵니다." if n else ""))
            self.hint.configure(text="바로 뽑으려면 [프로그램 열기] → 출력 탭 → 지금 출력.")
        elif res == "skipped":
            self._dot(T.ORANGE)
            self.title_lb.configure(text=f"{slot} 자동 출력 건너뜀", fg=T.ORANGE)
            self.body.configure(text=str(st.get("message", ""))[:300])
            self.hint.configure(text="")
        else:   # fail
            self._dot(T.RED)
            self.title_lb.configure(text="⚠ 자동 출력 실패", fg=T.RED)
            self.body.configure(text=str(st.get("message", ""))[:400])
            self.hint.configure(text="프로그램을 열어 확인한 뒤 출력 탭 → '지금 출력'으로 다시 뽑아 주세요.")
        self.warn.configure(text="⚠ " + "\n⚠ ".join(warn) if warn else "")
        self.update_idletasks()
        self.geometry(f"{self.W}x{max(self.H, self.winfo_reqheight())}")

    # ------------------------------------------------------------
    def stop(self):
        from tkinter import messagebox
        if self.stopping:
            return
        if not messagebox.askyesno("자동 출력 중지", "자동 출력을 중지할까요?\n\n"
                                                 "프린터로 이미 보낸 묶음은 그대로 나오고 '뽑음'으로 남습니다.\n"
                                                 "아직 안 보낸 주문은 다음 출력에 나옵니다.", parent=self):
            return
        self.stopping = True
        try:
            with open(STOPFILE, "w", encoding="utf-8") as f:
                f.write(dt.datetime.now().isoformat(timespec="seconds"))
        except Exception:
            pass
        self.b_stop.state(["disabled"])
        self.hint.configure(text="중지하는 중입니다... 지금 보내는 묶음이 끝나면 멈춥니다.")

    def ok(self):
        st = read_status()
        if st.get("final"):
            st["acknowledged"] = dt.datetime.now().isoformat(timespec="seconds")
            try:
                with open(STATUS, "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False, indent=1)
            except Exception:
                pass
        self.destroy()

    def reprint(self):
        st = read_status()
        self.b_reprint.state(["disabled"])
        self.hint.configure(text="다시 인쇄를 보내는 중입니다...")

        def work():
            try:
                import orderprint as op
                op.print_pdf(st["pdf"], op.load_config())
                op.log.info(f"[결과 창] 자동 출력 묶음 다시 뽑기: {os.path.basename(st['pdf'])}")
                msg = "다시 인쇄를 보냈습니다. 주문서가 모두 나오는지 확인하세요."
            except Exception as e:
                msg = f"다시 뽑기 실패: {e}"
            self.after(0, lambda: self.hint.configure(text=msg))
        threading.Thread(target=work, daemon=True).start()

    def open_manager(self):
        exe = sys.executable or "python"
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        subprocess.Popen([pyw if os.path.exists(pyw) else exe, os.path.join(BASE, "etsy_app.py")], cwd=BASE)


if __name__ == "__main__":
    close_previous()
    try:
        Notice().mainloop()
    finally:
        try:
            with open(PIDFILE) as f:
                mine = f.read().strip() == str(os.getpid())
            if mine:
                os.remove(PIDFILE)
        except Exception:
            pass
