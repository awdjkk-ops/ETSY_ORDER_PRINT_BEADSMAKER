# -*- coding: utf-8 -*-
"""Etsy 주문서 자동 출력 (윈도우 작업 스케줄러가 실행) — 카페24 주문서 프로그램의 자동 출력을 가져와 Etsy에 맞춤

  python autoprint.py --auto      정해 둔 시간에 새 주문 인쇄 (작업 스케줄러가 실행)
  python autoprint.py --auto --force   일정과 상관없이 지금 한 번 (자동 출력 탭의 [지금 시험 실행])
  python autoprint.py --boot      RTC 방식에서 PC가 켜질 때: 쉬는 날이면 종료

[지금 출력]과 똑같이 뽑음: 아직 안 뽑은 미발송 주문 → 작업용 주문서(카페24 재고 표시) → 프린터 →
'뽑음' 표시 · 출력 기록 · 라벨 탭 · 재고 차감 대기 · 출력 때 재고 기록.
진행·결과는 auto_status.json 에 쓰고, 오른쪽 아래 작은 창(notify.py)이 그것을 읽어 보여줌."""
import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time

import orderprint as op
import print_schedule as sch

BASE = op.BASE_DIR
STATUS_PATH = os.path.join(BASE, "auto_status.json")
STOP_PATH = os.path.join(BASE, "auto_stop.flag")          # 결과 창에서 [자동 출력 중지]를 누르면 생김
LOCK_PATH = os.path.join(BASE, "running.lock")            # 자동 출력·지금 출력이 동시에 돌지 않게
STATE_PATH = os.path.join(BASE, "auto_state.json")        # 오늘 이미 한 시간대
ONLINE_WAIT = 300                                         # 인터넷 연결을 기다리는 최대 시간(초)
STOCK_TRIES, STOCK_GAP = 3, 30                            # 카페24 재고 받기 다시 시도


class Busy(Exception):
    pass


class AutoStopped(Exception):
    """결과 창에서 사람이 자동 출력을 중지함"""


# ---------------------------------------------------------------- 실행 잠금 (두 출력이 겹치지 않게)
def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259                        # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_owner():
    """지금 잠금을 쥔 실행 {"pid", "who", "time"} — 없거나 이미 끝난 실행이면 None"""
    d = op.read_json(LOCK_PATH, None)
    if not isinstance(d, dict) or not pid_alive(d.get("pid")):
        return None
    return d


class run_lock:
    """자동 출력과 프로그램 창의 인쇄가 동시에 돌지 않게. 같은 프로그램 안에서는 who가 달라도 겹치지 않게 막음.
    wait초까지 기다렸다가 그래도 다른 작업 중이면 Busy"""

    def __init__(self, who, wait=0):
        self.who, self.wait = who, wait

    def __enter__(self):
        end = time.time() + self.wait
        while True:
            d = lock_owner()
            if d is None:
                break
            if time.time() >= end:
                raise Busy(f"다른 작업이 진행 중입니다: {d.get('who', '')} ({d.get('time', '')[11:16]} 시작)")
            time.sleep(5)
        op.write_json(LOCK_PATH, {"pid": os.getpid(), "who": self.who, "time": now_s()})
        return self

    def __exit__(self, *a):
        try:
            d = op.read_json(LOCK_PATH, None)
            if isinstance(d, dict) and d.get("pid") == os.getpid() and d.get("who") == self.who:
                os.remove(LOCK_PATH)
        except OSError:
            pass
        return False


def now_s():
    return f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"


# ---------------------------------------------------------------- 진행 상황 (결과 창·프로그램 머리글이 읽음)
def status(reset=False, **kw):
    st = {} if reset else op.read_json(STATUS_PATH, {})
    st.update(kw)
    st["updated"] = now_s()
    try:
        op.write_json(STATUS_PATH, st)
    except Exception as e:
        op.log.warning(f"[자동 출력] 진행 상황 기록 실패: {e}")


def load_status():
    return op.read_json(STATUS_PATH, {})


def stop_requested():
    return os.path.exists(STOP_PATH)


def check_stop():
    if stop_requested():
        raise AutoStopped()


def _pythonw():
    exe = sys.executable or "python"
    for name in ("pythonw.exe", "pyw.exe"):
        p = os.path.join(os.path.dirname(exe), name)
        if os.path.exists(p):
            return p
    return exe


def spawn_notifier():
    """진행·결과 작은 창을 '따로' 띄움 → 창이 자동 출력을 절대 막지 않음"""
    try:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([_pythonw(), os.path.join(BASE, "notify.py")], cwd=BASE, close_fds=True, **kw)
    except Exception as e:
        op.log.warning(f"[자동 출력] 결과 창을 띄우지 못했습니다(인쇄에는 영향 없음): {e}")


# ---------------------------------------------------------------- 인터넷 연결 기다리기
def _reach(host, port=443, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_online(stage=None, limit=None, need_c24=True):
    """절전에서 막 깨어나면 인터넷이 몇 초~1분 늦게 붙음 → 붙을 때까지 기다림 (10초마다 확인, 최대 limit초)
    돌려줌: (Etsy 연결됨, 카페24 연결됨 또는 None=확인 안 함)"""
    limit = ONLINE_WAIT if limit is None else limit
    c24_host = None
    if need_c24:
        try:
            import cafe24 as C
            cfg = C.load_config()
            if C.ready(cfg):
                c24_host = f"{cfg['mall_id']}.cafe24api.com"
        except Exception:
            c24_host = None
    start = time.time()
    while True:
        e_ok = _reach("api.etsy.com")
        c_ok = _reach(c24_host) if c24_host else None
        if e_ok and c_ok is not False:
            if time.time() - start > 1:
                op.log.info(f"[자동 출력] 인터넷 연결됨 ({time.time() - start:.0f}초 기다림)")
            return e_ok, c_ok
        waited = time.time() - start
        if waited >= limit:
            op.log.warning(f"[자동 출력] {limit // 60}분 기다려도 연결 안 됨 (Etsy {e_ok}, 카페24 {c_ok})")
            return e_ok, c_ok
        if stage:
            stage("인터넷 연결 기다리는 중", wait_note=f"{int(waited)}초째 · 최대 {limit // 60}분 기다림"
                  + ("" if e_ok else " · Etsy 연결 안 됨") + (" · 카페24 연결 안 됨" if c_ok is False else ""))
        check_stop()
        time.sleep(10)


# ---------------------------------------------------------------- 주문서 만들기·보내기 (프로그램 창의 인쇄도 같이 씀)
def add_stock(orders, save_snap=False, stage=None, tries=1, gap=None):
    """작업용 주문서에 카페24 재고 표시 (관리 탭에서 끌 수 있음).
    돌려줌: (화면에 알릴 문구, 실패 이유 또는 '', 재고 풀어둠 고정인데 적은 품목 수)"""
    if not op.load_settings().get("print_stock", True):
        return "", "", 0
    import cafe24 as C
    import stock as S
    if C.token_status()[0] == "bad":
        for o in orders:
            o["_stock_err"] = "카페24 연결 필요"
        return "카페24 연결이 필요해 재고는 표시하지 못했습니다", "카페24 인증이 필요합니다 — 관리 탭에서 카페24 다시 인증", 0
    err = ""
    gap = STOCK_GAP if gap is None else gap
    for k in range(1, tries + 1):
        if stage:
            stage("카페24 재고 확인 중" + (f" (다시 시도 {k}/{tries})" if k > 1 else ""))
        try:
            n, secs, low = S.annotate_print_stock(orders, save_snap=save_snap)
            note = (f"카페24 재고 확인 {secs:.1f}초 (상품 {n}종)"
                    + (f" · ⚠ 재고 풀어둠 고정인데 재고가 기준보다 적은 품목 {low}개 (주문서에 실제 숫자로 적음)" if low else ""))
            return note, "", low
        except Exception as e:
            err = str(e)
            op.log.warning(f"[주문서 재고] 실패 ({k}/{tries}): {e}")
            if k < tries:
                for _ in range(gap):
                    check_stop()
                    time.sleep(1)
    for o in orders:
        o["_stock_err"] = err[:60]
    return "카페24 재고를 받지 못해 '재고 확인 못 함'으로 표시했습니다", f"카페24 재고를 받지 못함: {err[:120]}", 0


def make_pdf(cfg, client, shop, rs, prefix, prog, stage, stock_tries=1):
    """돌려줌: (pdf 경로, 주문별 장수, 재고 문구, 재고 실패 이유, 적은 품목 수)"""
    op.attach_images(client, rs, lambda d, n: prog(d, n, "상품 사진 준비 중"))
    stage("주문서 PDF 만드는 중")
    os.makedirs(op.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(op.OUTPUT_DIR, f"{prefix}_{dt.datetime.now():%Y%m%d_%H%M%S}.pdf")
    orders = [op.normalize(r) for r in rs]
    order = op.short_first(orders)                 # 1장 주문 먼저, 2장 이상은 뒤에 모아서 (rs도 같은 순서로 → '뽑음' 표시가 맞게)
    orders = [orders[i] for i in order]
    rs[:] = [rs[i] for i in order]
    note, err, low = add_stock(orders, save_snap=(prefix == "orders"), stage=stage, tries=stock_tries)
    try:
        import stock as S
        S.annotate_vendor(orders)                  # 관리 탭 '업체명 주문서에 표기'가 켜졌을 때만
    except Exception as e:
        op.log.warning(f"[주문서 업체명] {e}")
    stage("주문서 PDF 만드는 중")
    sheets = op.build_pdf(orders, cfg, path, shop=shop)
    return path, sheets, note, err, low


def send(cfg, rs, path, sheets, kind, prog, on_wait=None, stop=None):
    """프린터로 보내고(주문서 전체를 인쇄 작업 1개로), 다 보내면 보낸 주문마다 '뽑음' 표시.
    끝나면 출력 기록·라벨 묶음·재고 차감 대기. stop(): 보내는 도중에도 확인 — True면 대기열에서 지우고 AutoStopped
    (이미 프린터로 넘어간 몇 장은 나올 수 있음). 돌려줌: 보낸 주문 순번 목록"""
    done = []

    def on_batch(idx, bno, total):
        done.extend(idx)
        printed = op.load_printed() | {str(rs[i].get("receipt_id")) for i in idx}
        op.save_printed(printed)
        prog(len(done), len(rs), "프린터로 보냄")

    op.log.info(f"[{kind}] {len(rs)}건 · {sum(sheets)}장 인쇄 시작")
    try:
        op.print_pdf(path, cfg, sheets, on_batch, on_wait, stop=stop)
    except op.PrintStopped:
        raise AutoStopped()
    finally:
        op.add_history(kind, path, [rs[i] for i in done],
                       "" if len(done) == len(rs) else f"{len(rs) - len(done)}건 인쇄 못 함")
        op.save_label_batch(kind, [rs[i] for i in done])
        try:
            import stock
            stock.enqueue([rs[i] for i in done])
        except Exception as e:
            op.log.warning(f"재고 차감 대기에 넣기 실패: {e}")
        op.log.info(f"[{kind}] 프린터로 보낸 주문 {len(done)}/{len(rs)}건")
    return done


# ---------------------------------------------------------------- 프린터 상태 지켜보기 (참고용)
_PRINTER_PROBLEMS = {"PaperOut": "용지 없음", "NoPaper": "용지 없음", "PaperJam": "용지 걸림", "PaperProblem": "용지 문제",
                     "Offline": "프린터 꺼짐·연결 끊김", "DoorOpen": "덮개 열림", "Error": "프린터 오류",
                     "UserIntervention": "프린터 확인 필요", "OutputBinFull": "출력함 가득 참", "NotAvailable": "프린터 사용 불가",
                     "OutOfMemory": "프린터 메모리 부족", "Blocked": "인쇄 막힘"}


def watch_print_job(cfg, pdf, pages, stage=None):
    """인쇄를 보낸 뒤 대기열과 프린터 상태를 지켜봄. 모든 프린터가 정확히 알려주지는 않으므로 참고용.
    돌려줌: {"state": ok/problem/unknown, "issues": [...], "note": str}"""
    if sys.platform != "win32":
        return {"state": "unknown", "issues": [], "note": "프린터 상태를 확인할 수 없는 환경"}
    printer = op.printer_name(cfg) or op.list_printers()[1]
    if not printer:
        return {"state": "unknown", "issues": [], "note": "기본 프린터를 찾지 못함"}
    name = printer.replace("'", "''")
    cmd = ("$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
           f"$p=Get-Printer -Name '{name}' -ErrorAction SilentlyContinue; "
           f"$j=@(Get-PrintJob -PrinterName '{name}' -ErrorAction SilentlyContinue | Select-Object DocumentName,"
           "@{n='St';e={\"$($_.JobStatus)\"}},PagesPrinted,TotalPages); "
           "[pscustomobject]@{S=\"$($p.PrinterStatus)\"; J=$j} | ConvertTo-Json -Depth 3 -Compress")
    stem = os.path.splitext(os.path.basename(pdf))[0]
    issues, gone_count, last_prog, pst = [], 0, "", ""
    t_start = time.time()
    deadline = time.time() + min(max(180, pages * 10), 1500)
    while time.time() < deadline:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=40,
                                 creationflags=op.NO_WINDOW).stdout.decode("utf-8", "replace")
            data = json.loads(out.strip() or "{}")
        except Exception:
            return {"state": "unknown", "issues": issues, "note": "프린터 상태를 읽지 못함"}
        pst = str(data.get("S") or "")
        jobs = data.get("J") or []
        jobs = jobs if isinstance(jobs, list) else [jobs]
        mine = [j for j in jobs if stem in str(j.get("DocumentName") or "")]
        sent = [j for j in mine if any(k in str(j.get("St") or "") for k in ("Complete", "Printed", "Deleted"))]
        mine = [j for j in mine if j not in sent]
        for key, ko in _PRINTER_PROBLEMS.items():
            if key in pst or any(key in str(j.get("St") or "") for j in mine):
                if ko not in issues:
                    issues.append(ko)
                    op.log.warning(f"[프린터 감시] {ko} 감지 ({printer})")
        if mine:
            gone_count = 0
            j = mine[0]
            last_prog = f"{j.get('PagesPrinted') or 0}/{j.get('TotalPages') or '?'}장"
            if stage:
                stage("프린터 확인 중", printer_progress=last_prog, printer_issues=issues)
        else:
            gone_count += 1
            if gone_count >= 2 and not any(k in pst for k in _PRINTER_PROBLEMS):
                note = ("프린터로 모두 보냄" if sent else "인쇄 대기열을 모두 보냄") + \
                       (" (중간에 문제가 있었지만 이어서 인쇄됨)" if issues else "")
                op.log.info(f"[프린터 감시] 끝: {note} ({time.time() - t_start:.0f}초)")
                return {"state": "problem" if issues else "ok", "issues": issues, "note": note}
        time.sleep(5)
    op.log.warning(f"[프린터 감시] 시간 안에 끝나지 않음: 마지막 확인 {last_prog}, 프린터 상태 '{pst}'")
    return {"state": "problem", "issues": issues or ["인쇄가 끝나지 않음"],
            "note": f"시간 안에 인쇄가 끝나지 않았습니다 (마지막 확인 {last_prog})"}


# ---------------------------------------------------------------- 실패 안내문
def print_notice(cfg, title, message):
    """[확인 필요] 안내문 1장 인쇄 (자동 출력이 실패했을 때)"""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfgen import canvas
        op.setup_fonts()
        os.makedirs(op.OUTPUT_DIR, exist_ok=True)
        path = os.path.join(op.OUTPUT_DIR, f"안내_{dt.datetime.now():%Y%m%d_%H%M%S}.pdf")
        c = canvas.Canvas(path, pagesize=landscape(A4))
        op.text(c, 60, 80, title, size=22, bold=True, color=op.HexColor("#C62828"))
        op.text(c, 60, 112, f"{dt.datetime.now():%Y-%m-%d %H:%M}", size=12, color=op.MUTED)
        top = 150
        for para in str(message).split("\n"):
            for line in op.wrap(para, op.PAGE_W - 140, 13) or [""]:
                op.text(c, 60, top, line, size=13)
                top += 20
        c.setStrokeColor(op.RULE)
        c.rect(40, 40, op.PAGE_W - 80, op.PAGE_H - 80)
        c.save()
        op.print_pdf(path, cfg)
        op.log.info(f"[자동 출력] 안내문 인쇄: {title}")
    except Exception as e:
        op.log.error(f"[자동 출력] 안내문 출력도 실패: {e}")


# ---------------------------------------------------------------- 자동 출력
def _mark_done(today, slots):
    state = op.read_json(STATE_PATH, {})
    prev = state.get("auto_done", {})
    done = set(prev.get("slots", [])) if prev.get("date") == today.isoformat() else set()
    state["auto_done"] = {"date": today.isoformat(), "slots": sorted(done | set(slots))}
    op.write_json(STATE_PATH, state)


def run_auto(force=False):
    s = sch.load_settings()
    today = dt.date.today()
    now = dt.datetime.now()
    due = ["지금 시험 실행"]
    if not force:
        if not s.get("auto_enabled"):
            op.log.info("[자동 출력] 꺼져 있습니다 (자동 출력 탭에서 켤 수 있음).")
            return
        planned, why = sch.plan_for(today, s)
        if not planned:
            op.log.info(f"[자동 출력] 오늘은 쉬는 날({why})이라 하지 않습니다.")
            return
        due = [t for t in planned if now >= dt.datetime.combine(today, dt.time(*map(int, t.split(":"))))
               - dt.timedelta(minutes=10)]
        if not due:
            op.log.info(f"[자동 출력] 오늘 출력 시각은 {', '.join(planned)}이라 지금은 건너뜁니다.")
            return
        done = op.read_json(STATE_PATH, {}).get("auto_done", {})
        done_slots = set(done.get("slots", [])) if done.get("date") == today.isoformat() else set()
        if set(due) <= done_slots:
            op.log.info(f"[자동 출력] 오늘 {', '.join(due)} 자동 출력은 이미 했습니다.")
            return
        if op.is_test():
            op.log.info("[자동 출력] 테스트 모드가 켜져 있어 건너뜁니다.")
            status(reset=True, pid=os.getpid(), started=now.isoformat(timespec="seconds"), slot=", ".join(due),
                   phase="건너뜀", final=True, result="skipped",
                   message="테스트 모드가 켜져 있어 자동 출력을 하지 않았습니다. 관리 탭에서 테스트 모드를 끄면 다음 시간부터 뽑습니다.")
            spawn_notifier()
            _mark_done(today, due)
            return

    try:
        os.remove(STOP_PATH)                            # 예전 중지 요청은 지움
    except OSError:
        pass
    status(reset=True, pid=os.getpid(), started=now.isoformat(timespec="seconds"), slot=", ".join(due),
           phase="시작", final=False, test=op.is_test(), force=force)
    spawn_notifier()

    def stage(phase, **kw):
        status(phase=phase, **kw)

    e_ok, _c_ok = wait_online(stage, need_c24=op.load_settings().get("print_stock", True))
    if not e_ok:
        raise op.UserError("인터넷에 연결되지 않아 Etsy 주문을 받지 못했습니다 (5분 동안 기다림).\n"
                           "인터넷(공유기·랜선)을 확인한 뒤 프로그램의 [지금 출력]으로 뽑아 주세요.")
    stage("Etsy 주문 확인 중", wait_note="")
    cfg = op.load_config()
    client = op.EtsyClient(cfg)
    rs = client.unshipped_receipts()
    rs.sort(key=lambda r: r.get("created_timestamp") or 0)
    printed = op.load_printed()
    rs = [r for r in rs if str(r.get("receipt_id")) not in printed]
    op.log.info(f"[자동 출력] 새로 출력할 주문 {len(rs)}건")
    if stop_requested():
        status(phase="중지됨", final=True, result="stopped", sent=0, count=len(rs))
        op.log.info("[자동 출력 중지] 주문 확인 직후 중지")
        _mark_done(today, due)
        return
    if not rs:
        status(phase="새 주문 없음", count=0, final=True, result="none")
        _mark_done(today, due)
        return

    def brief(r):
        return {"order_id": str(r.get("receipt_id")), "name": r.get("name", "")}
    status(phase="주문서 만드는 중", count=len(rs), done=0, total=len(rs), first=brief(rs[0]), last=brief(rs[-1]))

    def prog(d, n, label=None):
        if not (label or "").startswith("프린터로 보냄"):      # 보내는 중의 중지는 print_pdf가 처리 (대기열에서 지움)
            check_stop()
        if label and (d % 5 == 0 or d == n):
            status(phase=label, done=d, total=n)

    kind = "자동 출력" if not force else "자동 출력(시험)"
    sent = []
    try:
        check_stop()
        _order_check(cfg, rs, lambda p: (check_stop(), stage(p)))
        path, sheets, note, stock_err, low = make_pdf(cfg, client, client.shop_name(), rs, "orders", prog,
                                                      lambda p: (check_stop(), stage(p)), stock_tries=STOCK_TRIES)
        status(stock_note=note, stock_err=stock_err, stock_low=low, pages=sum(sheets), pdf=path)
        check_stop()
        stage("프린터로 보내는 중", done=0, total=len(rs))

        def on_wait(label, sec, slow):
            if sec % 5 == 0:
                status(phase="프린터로 보내는 중", printer_progress=label,
                       printer_issues=["5분째 진행이 없어요 — 프린터를 확인하세요 (계속 기다리는 중)"] if slow else [])
        done = send(cfg, rs, path, sheets, kind, prog, on_wait, stop=stop_requested)
        sent = done
        w = {"state": "ok", "issues": [], "note": ""} if op.is_test() else watch_print_job(cfg, path, sum(sheets), stage)
        ded = _deduct(cfg, rs, done, stage)
        link = _link_check(cfg, stage, [rs[i] for i in done])
        status(phase="완료" if w["state"] != "problem" else "확인 필요", final=True,
               result=w["state"], printer_issues=w["issues"], printer_note=w["note"], count=len(done),
               sent=len(done), last=brief(rs[done[-1]]) if done else {}, deduct=ded, link=link)
    except AutoStopped:
        sent = [i for i in range(len(rs)) if str(rs[i].get("receipt_id")) in op.load_printed()]
        ded = _deduct(cfg, rs, sent, stage)
        status(phase="중지됨", final=True, result="stopped", sent=len(sent), count=len(rs),
               pages=None if not sent else load_status().get("pages"), deduct=ded)
        op.log.info(f"[자동 출력 중지] 보낸 {len(sent)}건 · 남은 {len(rs) - len(sent)}건은 다음 출력에")
    finally:
        try:
            os.remove(STOP_PATH)
        except OSError:
            pass
    if not force:
        _mark_done(today, due)
    try:
        op.cleanup()
    except Exception as e:
        op.log.warning(f"정리 중 오류: {e}")


def _order_check(cfg, rs, stage):
    """출력 전: 이번 주문의 상품만 확인 (몇 초). 새 상품은 자동 연결 시도 → 바로 차감 때 반영"""
    try:
        import cafe24 as C
        import watch as W
        if not rs or op.is_test() or C.token_status()[0] == "bad":
            return
        stage("주문 상품 확인 중 (새 상품 자동 연결)")
        W.merge_orders(W.check_orders(op.EtsyClient(cfg), rs))
    except AutoStopped:
        raise
    except Exception as e:
        op.log.warning(f"[자동 출력] 주문 상품 확인 실패 (그대로 진행): {e}")


def _link_check(cfg, stage, rs=None):
    """결과 창용: 이번에 출력한 주문 중 재고 차감에 반영 안 되는 줄 (연결 필요·바뀐 상품·코드 다름)
    상점 전체 확인은 하지 않음 (정해 둔 시간에 프로그램이 따로 함)"""
    try:
        import watch as W
        iss = W.order_issues(rs or [])
        if not iss:
            return None
        return {"total": sum(iss.values()), "text": "이번 주문 중 " + W.issues_text(iss)}
    except Exception as e:
        op.log.warning(f"[자동 출력] 연결 확인 실패: {e}")
        return None


def _deduct(cfg, rs, idx, stage):
    """출력하면 재고 바로 차감 (관리 탭에서 켰을 때). 결과 창이 읽을 요약 (사입 목록 글자 포함)"""
    import autodeduct as AD
    if not idx or not AD.setting_on():
        return None
    try:
        res = AD.run([rs[i] for i in idx], cfg, lambda p: stage(p))
    except Exception as e:
        op.log.exception("[자동 출력] 바로 차감 오류")
        res = {"on": True, "err": f"재고 차감 중 오류: {str(e)[:150]} (줄은 차감 대기에 남음)"}
    try:
        import stock as S
        res["short_text"] = "\n".join(S.short_line(it) for it in res.get("short") or [])
        res["short_need"] = "\n".join(S.short_line(it, detail=False) for it in res.get("short") or [])
    except Exception:
        pass
    res["summary"] = AD.summary_line(res)
    res["problems"] = AD.problems(res)
    return res


def run_boot():
    """RTC 방식: 켜진 직후 쉬는 날이면 종료"""
    s = sch.load_settings()
    planned, why = sch.plan_for(dt.date.today(), s)
    up = 0
    if sys.platform == "win32":
        import ctypes
        up = ctypes.windll.kernel32.GetTickCount64() / 60000
    if s.get("auto_enabled") and not planned and up < 15:
        op.log.info(f"[자동 출력] 오늘은 쉬는 날({why})이라 2분 후 PC를 종료합니다.")
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/s", "/t", "120", "/c",
                            f"오늘은 쉬는 날({why})이라 2분 후 종료합니다. 취소: Win+R → shutdown /a"])


def _fail(msg):
    st = load_status()
    running = str(st.get("started", ""))[:10] == dt.date.today().isoformat() and not st.get("final")
    status(reset=not running, pid=os.getpid(), phase="실패", final=True, result="fail", message=msg,
           started=st.get("started") if running else dt.datetime.now().isoformat(timespec="seconds"))
    if not running:
        spawn_notifier()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--boot", action="store_true")
    a = ap.parse_args(argv)
    op.apply_data_mode()
    if a.boot:
        run_boot()
        return
    if not a.auto:
        ap.print_help()
        return
    cfg = None
    try:
        cfg = op.load_config()
        with run_lock("자동 출력", wait=600):          # 프로그램 창에서 인쇄 중이면 최대 10분 기다림
            run_auto(force=a.force)
    except Busy as e:
        msg = f"자동 출력 시간에 {e}\n10분을 기다려도 끝나지 않아 자동 출력을 하지 못했습니다. 프로그램의 [지금 출력]으로 뽑아 주세요."
        op.log.warning(f"[자동 출력] {msg}")
        print_notice(cfg, "[확인 필요] Etsy 주문서 자동 출력 못 함", msg)
        _fail(msg)
    except Exception as e:
        op.log.exception("[자동 출력] 오류")
        if isinstance(e, op.UserError):
            msg = str(e)
        else:
            msg = (f"주문서 자동 출력 중 오류가 났습니다.\n\n상세: {e}\n\n"
                   "프로그램을 열어 [지금 출력]으로 다시 뽑아 주세요. 계속 실패하면 관리 탭 → '실행 기록 보기' 내용을 Claude에게 보여주세요.")
        if cfg is not None:
            print_notice(cfg, "[확인 필요] Etsy 주문서 자동 출력 실패", msg)
        _fail(msg)


if __name__ == "__main__":
    main()
