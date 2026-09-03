"""
vrm_replace の GUI ランチャー（tkinter）。

  python gui.py

動画 / VRM / 出力先を選び、オプションを ON/OFF して「実行」。run.py を子プロセスで起動し、ログを表示する。
人物クリックのウィンドウは従来どおり途中で出る。設定は gui_settings.json に保存。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "gui_settings.json"
DEFAULTS = {
    "video": "", "vrm": "", "out": "",
    "joint_limits": True, "hand_interpolate": True, "fingers": True,
    "cleanplate": True, "auto": False, "max_frames": 0, "from_stage": "",
}


def console_python():
    """run.py を動かす Python。GUI が pythonw.exe で起動されていても、工程の子プロセスに標準出力を
    正しく継承させるためコンソール版 python.exe を使う（ウィンドウは CREATE_NO_WINDOW で出さない）。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("vrm_replace")
        self.proc = None
        self._line = ""
        self.q = queue.Queue()
        s = dict(DEFAULTS)
        if SETTINGS.exists():
            try:
                s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
            except Exception:
                pass
        self.v_video = tk.StringVar(value=s["video"])
        self.v_vrm = tk.StringVar(value=s["vrm"])
        self.v_out = tk.StringVar(value=s["out"])
        self.v_joint = tk.BooleanVar(value=s["joint_limits"])
        self.v_hand = tk.BooleanVar(value=s["hand_interpolate"])
        self.v_fingers = tk.BooleanVar(value=s["fingers"])
        self.v_clean = tk.BooleanVar(value=s["cleanplate"])
        self.v_auto = tk.BooleanVar(value=s["auto"])
        self.v_max = tk.IntVar(value=s["max_frames"])
        self.v_from = tk.StringVar(value=s["from_stage"])
        self._build()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- UI ----
    def _build(self):
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        def row(r, label, var, browse):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(f, textvariable=var, width=70).grid(row=r, column=1, sticky="ew", padx=4)
            ttk.Button(f, text="参照...", command=browse).grid(row=r, column=2)

        row(0, "動画 (mp4/avi)", self.v_video, lambda: self._pick(self.v_video, [("動画", "*.mp4 *.avi *.mov *.mkv"), ("すべて", "*.*")]))
        row(1, "VRM", self.v_vrm, lambda: self._pick(self.v_vrm, [("VRM", "*.vrm"), ("すべて", "*.*")]))
        row(2, "出力 mp4", self.v_out, self._pick_out)

        o = ttk.LabelFrame(f, text="オプション", padding=8)
        o.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Checkbutton(o, text="関節を人間の可動域に制限する（肘・膝の逆曲がり、手首の折れ等を抑える）",
                        variable=self.v_joint).grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(o, text="手が検出できない区間を前後の検出で補間する（手のひらのパタつき対策）",
                        variable=self.v_hand).grid(row=1, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(o, text="指をトラッキングする", variable=self.v_fingers).grid(row=2, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(o, text="クリーンプレート（ProPainter で実写の人物を消す。OFF は確認用で速い）",
                        variable=self.v_clean).grid(row=3, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(o, text="2 ショット目以降は最初のクリック位置を使い回す (--auto)",
                        variable=self.v_auto).grid(row=4, column=0, sticky="w", columnspan=3)
        ttk.Label(o, text="先頭 N フレームだけ処理（0 = 全部）").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(o, from_=0, to=100000, increment=30, textvariable=self.v_max, width=8).grid(row=5, column=1, sticky="w", pady=(6, 0))
        ttk.Label(o, text="やり直し開始工程").grid(row=6, column=0, sticky="w")
        ttk.Combobox(o, textvariable=self.v_from, width=30, state="readonly",
                     values=["", "1: マスク（クリックから）", "2: モーキャプ", "3: クリーンプレート", "4: レンダー", "5: 合成"]).grid(row=6, column=1, sticky="w")
        ttk.Label(o, text="※ 可動域・手の補間・指の ON/OFF を変えただけなら、やり直し指定は不要（自動でモーキャプから再計算）",
                  foreground="#555").grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))

        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.btn_run = ttk.Button(b, text="実行", command=self._run)
        self.btn_run.pack(side="left")
        self.btn_stop = ttk.Button(b, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.status = ttk.Label(b, text="")
        self.status.pack(side="left", padx=10)

        self.log = tk.Text(f, height=22, width=110, font=("Consolas", 9))
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        f.rowconfigure(5, weight=1)
        sb = ttk.Scrollbar(f, command=self.log.yview)
        sb.grid(row=5, column=3, sticky="ns", pady=(8, 0))
        self.log["yscrollcommand"] = sb.set

    def _initdir(self, var):
        cur = var.get()
        if cur and Path(cur).parent.exists():
            return str(Path(cur).parent)
        return str(ROOT / "sample") if (ROOT / "sample").exists() else str(ROOT)

    def _pick(self, var, types):
        p = filedialog.askopenfilename(filetypes=types, initialdir=self._initdir(var))
        if p:
            var.set(p)
            if var is self.v_video and not self.v_out.get():
                self.v_out.set(str(ROOT / "out" / (Path(p).stem + "_vrm.mp4")))

    def _pick_out(self):
        (ROOT / "out").mkdir(exist_ok=True)
        p = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("mp4", "*.mp4")],
                                         initialdir=self._initdir(self.v_out) if self.v_out.get() else str(ROOT / "out"))
        if p:
            self.v_out.set(p)

    # ---- run ----
    def _settings(self):
        return {"video": self.v_video.get(), "vrm": self.v_vrm.get(), "out": self.v_out.get(),
                "joint_limits": self.v_joint.get(), "hand_interpolate": self.v_hand.get(),
                "fingers": self.v_fingers.get(), "cleanplate": self.v_clean.get(), "auto": self.v_auto.get(),
                "max_frames": int(self.v_max.get() or 0), "from_stage": self.v_from.get()}

    def _run(self):
        s = self._settings()
        if not (s["video"] and Path(s["video"]).exists()):
            messagebox.showerror("vrm_replace", "動画ファイルを指定してください"); return
        if not (s["vrm"] and Path(s["vrm"]).exists()):
            messagebox.showerror("vrm_replace", "VRM ファイルを指定してください"); return
        if not s["out"]:
            messagebox.showerror("vrm_replace", "出力先を指定してください"); return
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        onoff = lambda b: "on" if b else "off"  # noqa: E731
        cmd = [console_python(), "-u", str(ROOT / "run.py"), "--video", s["video"], "--vrm", s["vrm"], "--out", s["out"],
               "--joint-limits", onoff(s["joint_limits"]), "--hand-interpolate", onoff(s["hand_interpolate"]),
               "--fingers", onoff(s["fingers"])]
        if not s["cleanplate"]:
            cmd.append("--no-cleanplate")
        if s["auto"]:
            cmd.append("--auto")
        if s["max_frames"] > 0:
            cmd += ["--max-frames", str(s["max_frames"])]
        if s["from_stage"]:
            cmd += ["--from", s["from_stage"].split(":")[0]]
        self.log.delete("1.0", "end")
        self._append("$ " + " ".join(cmd) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._reader, daemon=True).start()
        self.btn_run["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self.status["text"] = "実行中..."

    def _reader(self):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(4096)
            if not chunk:
                break
            self.q.put(dec.decode(chunk))
        self.q.put(None)

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    rc = self.proc.wait()
                    self.status["text"] = "完了" if rc == 0 else f"失敗 (exit {rc})"
                    self._append(f"\n== {'完了: ' + self.v_out.get() if rc == 0 else '失敗'} ==\n")
                    self.btn_run["state"] = "normal"
                    self.btn_stop["state"] = "disabled"
                    self.proc = None
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append(self, text):
        """\r で行頭に戻る出力（tqdm の進捗バー）は現在行を上書きする。「== 」で始まる行は工程名としてステータスに出す"""
        buf = text.replace("\r\n", "\n")
        i = 0
        while i < len(buf):
            j_n, j_r = buf.find("\n", i), buf.find("\r", i)
            cand = [k for k in (j_n, j_r) if k >= 0]
            j = min(cand) if cand else len(buf)
            seg = buf[i:j]
            if seg:
                self.log.insert("end", seg)
                self._line += seg
            if j < len(buf) and buf[j] == "\n":
                if self._line.startswith("== "):
                    self.status["text"] = self._line.strip()
                self.log.insert("end", "\n")
                self._line = ""
            elif j < len(buf) and buf[j] == "\r":
                self.log.delete("end-1c linestart", "end-1c")
                self._line = ""
            i = j + 1
        self.log.see("end")

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            # 子（各工程）ごと止める
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            self._append("\n== 停止しました（同じ設定で実行すると途中から再開します） ==\n")

    def _close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("vrm_replace", "実行中です。停止して終了しますか？"):
                return
            self._stop()
        self.destroy()


def main():
    try:
        App().mainloop()
    except Exception:
        import traceback
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "gui_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("vrm_replace", traceback.format_exc())
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
