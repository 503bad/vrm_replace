"""GUI の処理側。画面（app/ui）からは pywebview の js_api として呼ばれる。

流れ: 待機 → 準備（run.py --until 0 でショット分割）→ 人物の選択（画面でクリック）→ 実行（run.py）→ 完了 / 失敗 / 停止
画面は poll() を 0.2 秒ごとに呼び、状態とログの差分を受け取る。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import media
from common import DATA  # noqa: E402

# ライセンス画面のクレジットのリンク（画面から開けるのはこの 2 つだけ）
CREDIT_LINKS = {
    "x": "https://x.com/@503_bad",
    "youtube": "https://youtube.com/@503badgateway",
}
LICENSE_DOCS = (("NOTICE", "NOTICE"), ("THIRD PARTY", "THIRD_PARTY_NOTICES.md"), ("LICENSE", "LICENSE"))

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = DATA / "gui_settings.json"
# 出力の既定の置き場所（配布版はユーザーの「ビデオ」フォルダ。Electron が環境変数で渡す）
OUT_DIR = Path(os.environ.get("VRM_REPLACE_OUT") or ROOT / "out")
DEFAULTS = {
    "video": "", "vrm": "", "out": "", "out_auto": True,
    "preset": "quality", "joint_limits": True, "hand_interpolate": True, "fingers": True,
    "cleanplate": True, "auto": False, "max_frames": 0, "from_stage": 0,
}
# 実行順（3 クリーンプレートは 4 レンダーの後）
STAGES = [
    {"id": 0, "key": "SHOTS", "label": "ショット分割"},
    {"id": 1, "key": "MASK", "label": "人物マスク"},
    {"id": 2, "key": "MOCAP", "label": "モーションキャプチャ"},
    {"id": 4, "key": "RENDER", "label": "VRM レンダー"},
    {"id": 3, "key": "CLEAN PLATE", "label": "人物を消す"},
    {"id": 5, "key": "COMPOSITE", "label": "合成・書き出し"},
]
MAX_LOG = 4000

RE_STAGE = re.compile(r"^== (\d) ")
RE_SKIP = re.compile(r"^== (\d) .*skipped")
RE_SHOTS = re.compile(r"^(\d+) shots, ")
RE_SHOT = re.compile(r"shot_(\d+):")
RE_PROG = {
    1: re.compile(r"masked \d+-(\d+)/(\d+)"),
    2: re.compile(r"^\s+frame (\d+)/(\d+)"),
    4: re.compile(r"^\s+(\d+)/(\d+)\s+[\d.]+ fps"),
    3: re.compile(r"cleanplate \d+-(\d+)/(\d+)"),
}
RE_SHOT_DONE = {1: "mask ok", 2: "mocap ok", 4: "rendered", 3: "cleanplate ok", 5: "composited"}


def console_python():
    """工程を動かす Python。GUI が pythonw.exe で起動されていても、子プロセスの標準出力を受け取れるよう python.exe を使う"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return sys.executable


class Api:
    def __init__(self):
        self._window = None
        self._lock = threading.RLock()
        self._s = dict(DEFAULTS)
        if SETTINGS.exists():
            try:
                self._s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
            except Exception:
                pass
        self._files = {"video": None, "vrm": None}
        self._errors = {"video": None, "vrm": None}
        self._ver = 0
        self._log = []          # 完成した行
        self._log_base = 0      # _log[0] の通し番号（古い行を捨てたぶん）
        self._partial = ""      # 改行待ちの行（\r で上書きされる tqdm の行もここ）
        self._proc = None
        self._job = self._new_job("idle")
        for kind in ("video", "vrm"):
            if self._s.get(kind) and Path(self._s[kind]).exists():
                self._load(kind, self._s[kind], save=False)

    # ------------------------------------------------------------ state
    def _new_job(self, phase):
        return {"phase": phase, "stages": {st["id"]: {"state": "wait", "p": 0.0, "t0": None, "t1": None}
                                           for st in STAGES},
                "current": None, "nshots": 1, "shot": 0, "t0": time.time(), "t1": None, "message": "",
                "select": None, "result": None, "exit": None}

    def _bump(self):
        self._ver += 1

    def _save(self):
        try:
            SETTINGS.write_text(json.dumps(self._s, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _state(self):
        j = self._job
        now = time.time()
        stages = []
        for st in STAGES:
            x = j["stages"][st["id"]]
            el = ((x["t1"] or now) - x["t0"]) if x["t0"] else None
            eta = el * (1 - x["p"]) / x["p"] if (x["state"] == "run" and el and x["p"] > 0.04) else None
            stages.append({**st, "state": x["state"], "p": x["p"], "elapsed": el, "eta": eta})
        return {
            "settings": self._s, "files": self._files, "errors": self._errors,
            "job": {"phase": j["phase"], "stages": stages, "current": j["current"], "nshots": j["nshots"],
                    "shot": j["shot"], "elapsed": (j["t1"] or now) - j["t0"] if j["phase"] != "idle" else 0,
                    "message": j["message"], "select": j["select"], "result": j["result"]},
        }

    # ------------------------------------------------------------ API（画面から呼ぶ）
    def init(self):
        with self._lock:
            return {"ver": self._ver, "state": self._state(), "stages": STAGES}

    def poll(self, ver, log_from):
        with self._lock:
            out = {"ver": self._ver}
            if ver != self._ver or self._job["phase"] in ("prep", "run"):
                out["state"] = self._state()
            start = max(log_from, self._log_base)
            out["log"] = self._log[start - self._log_base:]
            out["log_next"] = self._log_base + len(self._log)
            out["partial"] = self._partial
            return out

    def dialog_info(self, kind):
        """ファイル選択ダイアログの初期フォルダとファイル名（kind: video / vrm / out）"""
        if kind == "out":
            cur = Path(self._s["out"]) if self._s.get("out") else OUT_DIR / "result.mp4"
            cur.parent.mkdir(parents=True, exist_ok=True)
            return {"dir": str(cur.parent), "name": cur.name}
        cur = self._s.get(kind)
        return {"dir": str(Path(cur).parent) if cur and Path(cur).parent.exists() else str(Path.home()), "name": ""}

    def pick(self, kind):
        """pywebview 版のファイル選択（Electron 版は electron/main.js がダイアログを出して set_file を呼ぶ）"""
        import webview
        types = {"video": ("動画 (*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v)",),
                 "vrm": ("VRM (*.vrm)",)}[kind]
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=self.dialog_info(kind)["dir"],
                                            file_types=types)
        if r:
            self.set_file(r[0])

    def pick_output(self):
        import webview
        d = self.dialog_info("out")
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=d["dir"],
                                            save_filename=d["name"], file_types=("mp4 (*.mp4)",))
        if r:
            self.set_output(r if isinstance(r, str) else r[0])

    def set_output(self, p):
        if not p.lower().endswith(".mp4"):
            p += ".mp4"
        with self._lock:
            self._s["out"], self._s["out_auto"] = p, False
            self._save()
            self._bump()

    def set_file(self, path):
        """ドロップ・選択されたファイルを種類（拡張子）で振り分ける"""
        kind = media.classify(path)
        with self._lock:
            if kind is None:
                self._job["message"] = f"対応していないファイルです: {Path(path).name}（動画か .vrm をドロップしてください）"
                self._bump()
                return None
            if self._job["phase"] in ("prep", "select", "run"):
                self._job["message"] = "実行中はファイルを変えられません"
                self._bump()
                return None
        self._load(kind, path)
        return kind

    def _load(self, kind, path, save=True):
        try:
            info = media.video_info(path) if kind == "video" else media.vrm_info(path)
            err = None
        except Exception as e:
            info, err = None, f"{Path(path).name}: {e}"
        with self._lock:
            self._files[kind], self._errors[kind] = info, err
            self._s[kind] = str(path) if info else self._s.get(kind, "")
            if kind == "video" and info and (self._s.get("out_auto", True) or not self._s.get("out")):
                self._s["out"] = str(OUT_DIR / (Path(path).stem + "_vrm.mp4"))
                self._s["out_auto"] = True
            if self._job["phase"] in ("done", "failed", "stopped"):
                self._job = self._new_job("idle")
            self._job["message"] = ""
            if save:
                self._save()
            self._bump()

    def clear_file(self, kind):
        with self._lock:
            if self._job["phase"] in ("prep", "select", "run"):
                return
            self._files[kind], self._errors[kind], self._s[kind] = None, None, ""
            self._save()
            self._bump()

    def set_option(self, key, value):
        with self._lock:
            if key not in DEFAULTS or key in ("video", "vrm", "out", "out_auto"):
                return
            self._s[key] = value
            self._save()
            self._bump()

    def start(self):
        with self._lock:
            if self._job["phase"] in ("prep", "select", "run"):
                return {"ok": False, "message": "実行中です"}
            s = self._s
            if not (self._files["video"] and Path(s["video"]).exists()):
                return {"ok": False, "message": "動画をドロップしてください"}
            if not (self._files["vrm"] and Path(s["vrm"]).exists()):
                return {"ok": False, "message": "VRM をドロップしてください"}
            if not s.get("out"):
                return {"ok": False, "message": "出力先を指定してください"}
            self._job = self._new_job("prep")
            self._log, self._log_base, self._partial = [], 0, ""
            self._job["current"] = 0
            self._job["stages"][0].update(state="run", t0=time.time())
            self._bump()
        self._spawn(self._args(until0=True), self._after_prep)
        return {"ok": True}

    def stop(self):
        with self._lock:
            phase = self._job["phase"]
            if phase == "select":
                self._finish("stopped", "停止しました")
                return
            proc = self._proc
        if proc and proc.poll() is None:
            with self._lock:
                self._job["phase_stop"] = True
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)

    def submit_points(self, points, labels):
        """人物の選択（画面のクリック）。1 ショットずつ受け取り、全部そろったら本処理を始める"""
        with self._lock:
            sel = self._job["select"]
            if self._job["phase"] != "select" or not sel:
                return
            if not any(l == 1 for l in labels):
                return
            shot = sel["queue"][sel["index"]]
            pts = [[int(round(x)), int(round(y))] for x, y in points]
            self._write_select(shot, pts, labels)
            if self._s.get("auto"):
                # 2 ショット目以降は同じ位置を使い回す
                for sd in sel["queue"][sel["index"] + 1:]:
                    self._write_select(sd, pts, labels)
                sel["index"] = len(sel["queue"])
            else:
                sel["index"] += 1
            if sel["index"] < len(sel["queue"]):
                self._fill_select_frame()
                self._bump()
                return
            self._job["select"] = None
            self._job["phase"] = "run"
            self._bump()
        self._spawn(self._args(), self._after_run)

    def open_output(self):
        p = self._s.get("out")
        if p and Path(p).exists():
            os.startfile(p)

    def licenses(self):
        """ライセンス画面に出す文書（本体の NOTICE、サードパーティ、Apache-2.0 本文）"""
        docs = []
        for label, name in LICENSE_DOCS:
            p = ROOT / name
            docs.append({"label": label, "name": name,
                         "text": p.read_text(encoding="utf-8") if p.exists() else f"{name} が見つかりません"})
        return {"docs": docs}

    def open_link(self, key):
        import webbrowser
        url = CREDIT_LINKS.get(key)
        if url:
            webbrowser.open(url)

    def open_folder(self):
        p = self._s.get("out")
        if p and Path(p).exists():
            subprocess.Popen(["explorer", "/select,", str(Path(p))])
        elif p:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(Path(p).parent))

    def dismiss(self):
        """完了・失敗の表示を閉じて待機に戻る"""
        with self._lock:
            if self._job["phase"] in ("done", "failed", "stopped"):
                self._job = self._new_job("idle")
                self._bump()

    def is_busy(self):
        return self._job["phase"] in ("prep", "select", "run")

    # ------------------------------------------------------------ process
    def _work(self):
        return DATA / "work" / Path(self._s["video"]).stem

    def _args(self, until0=False):
        s = self._s
        onoff = lambda b: "on" if b else "off"  # noqa: E731
        cmd = [console_python(), "-u", str(ROOT / "run.py"), "--video", s["video"], "--vrm", s["vrm"], "--out", s["out"],
               "--preset", s["preset"], "--joint-limits", onoff(s["joint_limits"]),
               "--hand-interpolate", onoff(s["hand_interpolate"]), "--fingers", onoff(s["fingers"])]
        if not s["cleanplate"]:
            cmd.append("--no-cleanplate")
        if int(s.get("max_frames") or 0) > 0:
            cmd += ["--max-frames", str(int(s["max_frames"]))]
        if int(s.get("from_stage") or 0) > 0 and not until0:
            cmd += ["--from", str(int(s["from_stage"]))]
        if until0:
            cmd += ["--until", "0"]
        return cmd

    def _spawn(self, cmd, on_exit):
        self._append_log("$ " + " ".join(cmd[2:]) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with self._lock:
            self._proc = proc
        threading.Thread(target=self._reader, args=(proc, on_exit), daemon=True).start()

    def _reader(self, proc, on_exit):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            self._append_log(dec.decode(chunk))
        rc = proc.wait()
        with self._lock:
            if self._partial:
                self._line(self._partial)
                self._partial = ""
            self._proc = None
            stopped = self._job.pop("phase_stop", False)
        if stopped:
            self._finish("stopped", "停止しました（同じ設定でもう一度実行すると、済んだ工程を飛ばして続きから進みます）")
        elif rc != 0:
            self._finish("failed", self._error_summary() or f"失敗しました（終了コード {rc}）")
        else:
            on_exit()

    def _after_prep(self):
        """ショット分割が済んだ。クリックが要るショットを集める（1: マスクからやり直す指定ならクリックからやり直す）"""
        work = self._work()
        shots = sorted(p for p in work.glob("shot_*") if p.is_dir())
        if int(self._s.get("from_stage") or 0) == 1:
            for sd in shots:
                (sd / "select.json").unlink(missing_ok=True)
        need = [sd for sd in shots if not (sd / "select.json").exists()]
        with self._lock:
            self._job["stages"][0].update(state="done", p=1.0, t1=time.time())
            if need:
                self._job["phase"] = "select"
                self._job["current"] = None
                self._job["select"] = {"queue": [str(p) for p in need], "index": 0, "total": len(need)}
                self._fill_select_frame()
                self._bump()
                return
            self._job["phase"] = "run"
            self._bump()
        self._spawn(self._args(), self._after_run)

    def _fill_select_frame(self):
        sel = self._job["select"]
        sd = Path(sel["queue"][sel["index"]])
        meta = json.loads((sd / "meta.json").read_text(encoding="utf-8"))
        sel.update(shot=sd.name, width=meta["width"], height=meta["height"],
                   image=media.image_data_url(sd / "frames" / "000000.jpg"),
                   start=meta.get("start", 0), frames=meta.get("n_frames", 0))

    def _write_select(self, shot_dir, pts, labels):
        (Path(shot_dir) / "select.json").write_text(json.dumps({"points": pts, "labels": labels}), encoding="utf-8")

    def _after_run(self):
        out = Path(self._s["out"])
        res = {"path": str(out), "name": out.name, "size": out.stat().st_size if out.exists() else 0,
               "preview": media.frame_data_url(out) if out.exists() else None}
        with self._lock:
            self._job["result"] = res
        self._finish("done", "完了しました")

    def _finish(self, phase, message):
        with self._lock:
            j = self._job
            j["phase"], j["message"], j["t1"] = phase, message, time.time()
            for x in j["stages"].values():
                if x["state"] == "run":
                    x["state"] = "done" if phase == "done" else ("stopped" if phase == "stopped" else "failed")
                    x["t1"] = time.time()
            if phase == "done":
                for x in j["stages"].values():
                    if x["state"] == "wait":
                        x["state"] = "skip"
            j["current"] = None
            j["select"] = None
            self._bump()

    def _error_summary(self):
        for line in reversed(self._log[-60:]):
            t = line.strip()
            if not t or t.startswith(("File ", "^", "raise ", "subprocess.CalledProcessError", "Traceback")):
                continue
            if "Error" in t or "SystemExit" in t or "見つかりません" in t or "ありません" in t or "失敗" in t:
                return t
        return None

    # ------------------------------------------------------------ log → progress
    def _append_log(self, text):
        with self._lock:
            buf = self._partial + text.replace("\r\n", "\n")
            self._partial = ""
            i = 0
            while i < len(buf):
                jn, jr = buf.find("\n", i), buf.find("\r", i)
                cand = [k for k in (jn, jr) if k >= 0]
                if not cand:
                    self._partial = buf[i:]
                    break
                j = min(cand)
                seg = buf[i:j]
                if buf[j] == "\n":
                    self._line(seg)
                # \r: tqdm の進捗。行は確定させずに捨てる（最後の状態だけ _partial に残る）
                i = j + 1
            if len(self._log) > MAX_LOG:
                drop = len(self._log) - MAX_LOG
                del self._log[:drop]
                self._log_base += drop

    def _line(self, line):
        self._log.append(line)
        j = self._job
        m = RE_STAGE.match(line)
        if m:
            sid = int(m.group(1))
            now = time.time()
            cur = j["current"]
            if cur is not None and cur != sid and j["stages"][cur]["state"] == "run":
                j["stages"][cur].update(state="done", p=1.0, t1=now)
            if RE_SKIP.match(line):
                j["stages"][sid].update(state="skip", p=0.0)
                j["current"] = None
            else:
                j["stages"][sid].update(state="run", p=0.0, t0=now, t1=None)
                j["current"] = sid
                j["shot"] = 0
            self._bump()
            return
        m = RE_SHOTS.match(line)
        if m:
            j["nshots"] = max(1, int(m.group(1)))
            return
        cur = j["current"]
        if cur is None:
            return
        st = j["stages"][cur]
        m = RE_SHOT.search(line)
        if m:
            j["shot"] = int(m.group(1))
        n = j["nshots"]
        if cur in RE_PROG:
            m = RE_PROG[cur].search(line)
            if m and int(m.group(2)) > 0:
                st["p"] = min(1.0, (j["shot"] + int(m.group(1)) / int(m.group(2))) / n)
        if cur in RE_SHOT_DONE and RE_SHOT_DONE[cur] in line:
            st["p"] = min(1.0, (j["shot"] + 1) / n)
