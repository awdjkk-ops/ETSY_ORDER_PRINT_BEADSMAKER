# -*- coding: utf-8 -*-
"""자동 출력 일정 계산과 윈도우 작업 스케줄러 등록 (카페24 주문서 프로그램에서 가져와 Etsy에 맞춤)
설정은 settings.json 의 "auto" 칸에 저장 (다른 PC로 백업을 가져와도 이 칸은 그 PC 것을 그대로 둠)"""
import datetime as dt
import json
import os
import subprocess
import sys

import orderprint as op

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
TASK_MAIN = "EtsyOrderPrintAuto"
OLD_TASKS = ["Etsy Order Print"]          # 예전 4_schedule_daily_9am.bat 이 만든 예약 → 두 번 뽑히지 않게 지움
BOOT_LNK = "EtsyOrderPrint_BootCheck.lnk"

SLOTS = ("am", "pm")
SLOT_NAMES = {"am": "오전", "pm": "오후"}

DEFAULT_SETTINGS = {
    "auto_enabled": False,          # 처음에는 꺼둠 — 운영을 시작할 때 켬
    "power_mode": "sleep",          # "sleep" 또는 "rtc"
    "skip_public_holidays": True,
    "weekdays": {                   # 0=월 ... 6=일, 요일마다 오전·오후
        str(i): {"am": {"on": i < 5, "time": "09:00"}, "pm": {"on": False, "time": "14:00"}} for i in range(7)
    },
    # 날짜 지정: 그날의 오전·오후를 통째로 정함. 둘 다 off면 쉬는 날
    "exceptions": [],
}


def _slot(v, on, time):
    return {"on": bool(v.get("on", on)) if isinstance(v, dict) else on,
            "time": (v.get("time") if isinstance(v, dict) else None) or time}


def load_settings():
    s = op.load_settings().get("auto") or {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    merged.update({k: v for k, v in s.items() if k not in ("weekdays", "exceptions")})
    for k, v in (s.get("weekdays") or {}).items():
        merged["weekdays"][k] = {"am": _slot(v.get("am") or {}, False, "09:00"), "pm": _slot(v.get("pm") or {}, False, "14:00")}
    merged["exceptions"] = [{"date": e["date"], "am": _slot(e.get("am") or {}, False, "09:00"),
                             "pm": _slot(e.get("pm") or {}, False, "14:00")} for e in s.get("exceptions", []) if e.get("date")]
    return merged


def save_settings(s):
    allst = op.load_settings()
    allst["auto"] = {k: s[k] for k in DEFAULT_SETTINGS}
    op.save_settings(allst)


def holidays_ready():
    try:
        import holidays  # noqa: F401
        return True
    except ImportError:
        return False


def holiday_name(day):
    try:
        import holidays
        return holidays.KR(years=day.year).get(day)
    except ImportError:
        return None


def plan_for(day, s):
    """그날 출력 시각 목록(['09:00', '14:00'])과 설명. 출력 안 하는 날이면 ([], 이유)."""
    iso = day.isoformat()
    for e in s.get("exceptions", []):
        if e.get("date") == iso:
            times = sorted(e[k]["time"] for k in SLOTS if e[k]["on"])
            return times, ("날짜 지정" if times else "지정한 쉬는 날")
    if s.get("skip_public_holidays", True):
        name = holiday_name(day)
        if name:
            return [], f"공휴일({name})"
    w = s["weekdays"][str(day.weekday())]
    times = sorted(w[k]["time"] for k in SLOTS if w[k]["on"])
    if not times:
        return [], f"{WEEKDAYS[day.weekday()]}요일 출력 안 함"
    return times, f"{WEEKDAYS[day.weekday()]}요일 기본"


def upcoming(s, days=14, start=None):
    start = start or dt.date.today()
    return [(d, *plan_for(d, s)) for d in (start + dt.timedelta(days=i) for i in range(days))]


def next_run(s, now=None):
    """다음 자동 출력 시각 (없으면 None)"""
    if not s.get("auto_enabled"):
        return None
    now = now or dt.datetime.now()
    for d, times, _why in upcoming(s, 30, now.date()):
        for t in times:
            x = dt.datetime.combine(d, dt.time(*map(int, t.split(":"))))
            if x > now:
                return x
    return None


def earliest_time(s):
    times = [w[k]["time"] for w in s["weekdays"].values() for k in SLOTS if w[k]["on"]]
    times += [e[k]["time"] for e in s.get("exceptions", []) for k in SLOTS if e[k]["on"]]
    return min(times) if times else None


def times_text(times):
    return " · ".join(times) + " 출력" if times else ""


# ---------------------------------------------------------------- 작업 스케줄러
_PS_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _ps_time(t, date=None):
    """지역 설정과 상관없이 정확히 읽히도록 날짜/시각을 고정 형식으로 전달"""
    if date:
        return f"([datetime]::ParseExact('{date} {t}','yyyy-MM-dd HH:mm',$null))"
    return f"([datetime]::ParseExact('{t}','HH:mm',$null))"


def build_task_script(s):
    """작업 스케줄러 등록용 PowerShell 스크립트 (요일별 트리거 + 날짜 지정 트리거)"""
    d = str(op.BASE_DIR).replace("'", "''")
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$dir = '{d}'",
        f"Unregister-ScheduledTask -TaskName '{TASK_MAIN}' -Confirm:$false -ErrorAction SilentlyContinue",
    ]
    lines += [f"Unregister-ScheduledTask -TaskName '{t}' -Confirm:$false -ErrorAction SilentlyContinue" for t in OLD_TASKS]
    lines += [
        "$startup = [Environment]::GetFolderPath('Startup')",
        f"$bootLnk = Join-Path $startup '{BOOT_LNK}'",
        "if (Test-Path $bootLnk) { Remove-Item $bootLnk -Force }",
    ]
    if not s.get("auto_enabled"):
        lines.append("Write-Output 'AUTO_OFF'")
        return "\r\n".join(lines)

    trig = []
    for i in range(7):
        w = s["weekdays"][str(i)]
        for k in SLOTS:
            if w[k]["on"]:
                trig.append(f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {_PS_DAYS[i]} -At {_ps_time(w[k]['time'])}")
    today = dt.date.today()
    for e in s.get("exceptions", []):
        if dt.date.fromisoformat(e["date"]) >= today:
            for k in SLOTS:
                if e[k]["on"]:
                    trig.append(f"New-ScheduledTaskTrigger -Once -At {_ps_time(e[k]['time'], e['date'])}")
    if trig:
        lines.append("$triggers = @(" + ", ".join(f"({t})" for t in trig) + ")")
        lines += [
            # 검은 창 없이 실행 (pythonw → pyw). 둘 다 없으면 bat 파일로
            "$script = Join-Path $dir 'autoprint.py'",
            "$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source",
            "if (-not $pyw) { $pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source }",
            "if ($pyw) { $action = New-ScheduledTaskAction -Execute $pyw -Argument ('\"' + $script + '\" --auto') -WorkingDirectory $dir }",
            "else { $action = New-ScheduledTaskAction -Execute (Join-Path $dir 'auto_print.bat') -WorkingDirectory $dir }",
            "$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)",
            f"Register-ScheduledTask -TaskName '{TASK_MAIN}' -Action $action -Trigger $triggers "
            "-Settings $settings -Description 'Etsy order sheet auto print' -Force | Out-Null",
        ]
    if s.get("power_mode") == "rtc":
        # RTC 방식: 로그인할 때 '오늘이 쉬는 날이면 종료' 검사 (시작프로그램 폴더 사용, 관리자 권한 불필요)
        lines += [
            "$ws = New-Object -ComObject WScript.Shell",
            "$l = $ws.CreateShortcut($bootLnk)",
            "$l.TargetPath = Join-Path $dir 'boot_check.bat'",
            "$l.WorkingDirectory = $dir",
            "$l.WindowStyle = 7",
            "$l.Save()",
        ]
    lines.append("Write-Output 'OK'")
    return "\r\n".join(lines)


def register_tasks(s):
    """설정을 작업 스케줄러에 반영. (성공여부, 메시지)"""
    if sys.platform != "win32":
        return True, "(윈도우가 아니라 작업 스케줄러 등록은 건너뜀)"
    ps1 = os.path.join(op.BASE_DIR, "register_tasks.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:          # BOM: 한글 경로 대응
        f.write(build_task_script(s))
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                       capture_output=True, text=True, creationflags=0x08000000)  # 창 숨김
    if r.returncode != 0:
        op.log.warning(f"[자동 출력] 작업 스케줄러 등록 실패: {(r.stderr or r.stdout)[-500:]}")
        return False, (r.stderr or r.stdout)[-800:]
    op.log.info("[자동 출력] 작업 스케줄러 " + ("등록" if "OK" in r.stdout else "해제"))
    return True, "자동 출력 예약을 저장했습니다." if "OK" in r.stdout else "자동 출력을 껐습니다 (예약을 지웠습니다)."
