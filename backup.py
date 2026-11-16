# -*- coding: utf-8 -*-
"""연결 데이터 백업·가져오기 (다른 PC로 옮길 때, 만일을 대비할 때)
담는 것: 연결표, 머리글자·단위표, 연결 안 함, 미리 연결 상태, 바뀐 상품 확인 상태, 차감 대기·기록, 설정,
         뽑음 기록·출력 기록·라벨 묶음
안 담는 것: Etsy·카페24 인증 파일, config.ini (새 PC에서 다시 인증)"""
import datetime as dt
import json
import os
import zipfile

import orderprint as op

FILES = [
    ("stock_map.json", "연결표"),
    ("stock_prefix.json", "머리글자 대응표"),
    ("stock_units.json", "단위 환산표"),
    ("stock_valuemap.json", "옵션 값 대응표"),
    ("stock_excluded.json", "연결 안 함"),
    ("premap_state.json", "미리 연결 상태"),
    ("premap_listings.json", "Etsy 상품 목록(미리 연결용)"),
    ("watch_state.json", "바뀐 상품 확인 상태"),
    ("stock_queue.json", "차감 대기"),
    ("stock_history.json", "차감 기록"),
    ("stock_short.json", "재고 부족 목록"),
    ("printed_orders.json", "뽑음 기록 (이미 인쇄한 주문 — 다시 인쇄되지 않게)"),
    ("print_history.json", "출력 기록"),
    ("label_orders.json", "라벨 묶음"),
    ("settings.json", "설정 (프린터 제외하고 가져옴)"),
]
BACKUP_DIR = os.path.join(op.BASE_DIR, "backup")
KEEP_LOCAL = ("printer", "test_mode", "auto")  # 가져올 때 이 PC 값을 그대로 두는 설정 (자동 출력 예약은 PC마다 따로)


def default_name():
    return f"연결데이터_백업_{dt.datetime.now():%Y%m%d_%H%M}.zip"


def make(path):
    """돌려줌: 담은 파일 [(이름, 설명)]"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    done = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, desc in FILES:
            src = os.path.join(op.BASE_DIR, name)
            if os.path.exists(src):
                z.write(src, name)
                done.append((name, desc))
        z.writestr("manifest.json", json.dumps({"kind": "etsy-orderprint-backup", "time": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}",
                                                "files": [n for n, _ in done]}, ensure_ascii=False, indent=1))
    op.log.info(f"[백업] {path} ({len(done)}개 파일)")
    return done


def inspect(path):
    """백업 파일 확인 → (만든 때, 들어 있는 [(이름, 설명)]). 우리 백업이 아니면 UserError"""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            man = json.loads(z.read("manifest.json").decode("utf-8")) if "manifest.json" in names else {}
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise op.UserError(f"백업 파일을 열 수 없습니다: {e}")
    if man.get("kind") != "etsy-orderprint-backup":
        raise op.UserError("이 프로그램에서 만든 백업 파일이 아닙니다.")
    desc = dict(FILES)
    return man.get("time", ""), [(n, desc[n]) for n, _ in FILES if n in names]


def restore(path):
    """가져오기. 먼저 지금 데이터를 backup 폴더에 자동 백업. 돌려줌: (자동 백업 경로, 가져온 [(이름, 설명)])"""
    _t, items = inspect(path)
    safety = os.path.join(BACKUP_DIR, f"가져오기전_자동백업_{dt.datetime.now():%Y%m%d_%H%M%S}.zip")
    make(safety)
    with zipfile.ZipFile(path) as z:
        for name, _d in items:
            data = z.read(name)
            dst = os.path.join(op.BASE_DIR, name)
            if name == "settings.json":
                new = json.loads(data.decode("utf-8"))
                cur = op.read_json(dst, {})
                for k in KEEP_LOCAL:
                    if k in cur:
                        new[k] = cur[k]
                    else:
                        new.pop(k, None)
                op.write_json(dst, new)
            else:
                tmp = dst + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, dst)
    op.log.info(f"[가져오기] {path} ({len(items)}개 파일), 자동 백업 {safety}")
    return safety, items
