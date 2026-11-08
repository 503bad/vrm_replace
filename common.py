"""全工程で共有するユーティリティ（Windows ネイティブ前提）。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
# 書き込む場所（モデル・作業ファイル・ログ・設定）。配布版（Electron）はコードと分けて %LOCALAPPDATA%\Replace2VRM に置く
DATA = Path(os.environ.get("VRM_REPLACE_DATA") or ROOT).resolve()
# 配布版では Electron の exe が three-vrm のレンダー（工程 4）を担う（Node.js と Chrome が要らない）
ELECTRON_EXE = os.environ.get("VRM_REPLACE_ELECTRON") or ""


OPTIONS_FILE = "options.json"
# GUI / run.py から上書きできる設定キー（work/<video>/options.json に保存される）
OPTION_KEYS = ("joint_limits", "hand_interpolate", "use_fingers", "use_hands", "preset")
PRESETS = ("quality", "speed")
PATH_KEYS = ("mediapipe_model", "mediapipe_hand_model", "work_dir",
             )

# 工程ごとに結果へ影響する設定。.done_N に記録し、変わっていれば run.py がその工程からやり直す
STAGE_KEYS = {
    1: ("mediapipe_model", "mask_threshold", "mask_temporal_smooth", "mask_hold_frames", "mask_num_poses"),
    2: ("mediapipe_model",),
    3: ("mask_dilate_px", "cleanplate_max_side", "cleanplate_flow_perf", "cleanplate_flow_grid",
        "cleanplate_bidirectional", "cleanplate_avatar_band", "cleanplate_band_margin_px",
        "cleanplate_key_interval", "cleanplate_fill", "cleanplate_fill_min_px", "cleanplate_segment_frames"),
}


def load_config(work=None):
    """config.yaml を読む。work が指定され work/options.json があればその値で上書き。
    最後に選ばれたプリセット（preset）の値を、上位に書かれていないキーにだけ適用する。"""
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if work:
        p = Path(work) / OPTIONS_FILE
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg.update(json.load(f))
    presets = cfg.pop("presets", {}) or {}
    name = cfg.get("preset") or "quality"
    if name not in presets:
        raise SystemExit(f"不明なプリセット: {name}（{', '.join(presets)} のどれか）")
    cfg["preset"] = name
    for k, v in presets[name].items():
        cfg.setdefault(k, v)
    # 相対パスは書き込み先（開発時はプロジェクトルート）基準に解決
    for k in PATH_KEYS:
        if k in cfg and cfg[k] and not Path(cfg[k]).is_absolute():
            cfg[k] = str((DATA / cfg[k]).resolve())
    return cfg


def stage_sig(cfg, stage):
    """工程の結果に効く設定の記録（.done_N の中身）。パスはファイル名だけ（置き場所の違いは無視）"""
    keys = STAGE_KEYS.get(stage)
    if not keys:
        return ""
    return json.dumps({k: (Path(cfg[k]).name if k in PATH_KEYS and cfg.get(k) else cfg.get(k)) for k in keys},
                      sort_keys=True)


def stage_changed(shot_dir, stage, cfg):
    """.done_N があり、記録された設定が今の設定と違えば True。中身が空（旧版で作った）なら変更なし扱い"""
    f = Path(shot_dir) / f".done_{stage}"
    if not f.exists():
        return False
    rec = f.read_text(encoding="utf-8").strip()
    return bool(rec) and rec != stage_sig(cfg, stage)


def write_options(work, opts):
    """run.py / GUI からのオプションを work/options.json に保存（既存とマージ）"""
    p = Path(work) / OPTIONS_FILE
    cur = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            cur = json.load(f)
    cur.update({k: v for k, v in opts.items() if v is not None})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    return cur


def option_snapshot(cfg):
    """mocap.json に記録し、変更検知に使う"""
    return {k: cfg.get(k) for k in OPTION_KEYS}


NON_NVIDIA_MSG = "NVIDIA 以外の GPUは利用できません"


def gpu_error():
    """NVIDIA GPU（NVIDIA Optical Flow 対応 = Turing 以降）が無ければ、その理由。使えれば None。
    torch を読み込まずに nvidia-smi とドライバーの DLL だけで調べる（GUI の起動を遅くしない）"""
    import ctypes
    smi = shutil.which("nvidia-smi")
    if not smi:
        return NON_NVIDIA_MSG
    try:
        out = subprocess.run([smi, "--query-gpu=name,compute_cap", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return NON_NVIDIA_MSG
    caps = []
    for line in out.strip().splitlines():
        try:
            caps.append(float(line.rsplit(",", 1)[1]))
        except (IndexError, ValueError):
            pass
    if not caps:
        return NON_NVIDIA_MSG
    if max(caps) < 7.5:
        return "この NVIDIA GPU は NVIDIA Optical Flow に対応していません（Turing / GeForce RTX 20 シリーズ以降が必要です）"
    try:
        ctypes.WinDLL("nvofapi64.dll") if sys.platform == "win32" else ctypes.CDLL("libnvidia-opticalflow.so.1")
    except OSError:
        return "NVIDIA ドライバーに NVIDIA Optical Flow が含まれていません。ドライバーを最新版に更新してください"
    return None


def run(cmd, cwd=None, env=None):
    """サブプロセスを実行。失敗したら例外。"""
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=e, check=True)


def ffmpeg_exe():
    """PATH の ffmpeg → 無ければ imageio-ffmpeg 同梱バイナリ。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def node_exe():
    p = shutil.which("node")
    if not p:
        raise SystemExit("node が見つかりません。Node.js 18+ を入れて PATH を通してください")
    return p


def shot_dirs(work):
    return sorted(p for p in Path(work).glob("shot_*") if p.is_dir())


def done(shot_dir, stage):
    return (Path(shot_dir) / f".done_{stage}").exists()


def reset_stages(shot_dir, first):
    """工程 first 以降の完了印を消す（やり直し）。工程 3 は区間単位の途中結果も使わない"""
    sd = Path(shot_dir)
    for f in sd.glob(".done_*"):
        if int(f.name.split("_")[-1]) >= first:
            f.unlink()
    if first <= 3:
        (sd / "cleanplate" / "_sig.txt").unlink(missing_ok=True)


def mark_done(shot_dir, stage, cfg=None):
    (Path(shot_dir) / f".done_{stage}").write_text(stage_sig(cfg, stage) if cfg else "", encoding="utf-8")


def read_meta(shot_dir):
    with open(Path(shot_dir) / "meta.json", encoding="utf-8") as f:
        return json.load(f)


def write_meta(shot_dir, meta):
    with open(Path(shot_dir) / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def frame_files(shot_dir):
    return sorted((Path(shot_dir) / "frames").glob("*.jpg"))


def stage_python():
    """各工程を動かす Python。pythonw.exe（GUI 用、標準出力を持たない）で呼ばれた場合は
    同じディレクトリの python.exe に置き換える。そうしないと工程の出力・エラーが失われる。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return sys.executable
