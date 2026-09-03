"""全工程で共有するユーティリティ（Windows ネイティブ前提）。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


OPTIONS_FILE = "options.json"
# GUI / run.py から上書きできる設定キー（work/<video>/options.json に保存される）
OPTION_KEYS = ("joint_limits", "hand_interpolate", "use_fingers", "use_hands")


def load_config(work=None):
    """config.yaml を読む。work が指定され work/options.json があればその値で上書き。"""
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 相対パスはプロジェクトルート基準に解決
    for k in ("sam2_ckpt", "propainter_dir", "mediapipe_model", "mediapipe_hand_model", "work_dir"):
        if k in cfg and cfg[k] and not Path(cfg[k]).is_absolute():
            cfg[k] = str((ROOT / cfg[k]).resolve())
    if work:
        p = Path(work) / OPTIONS_FILE
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg.update(json.load(f))
    return cfg


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


def mark_done(shot_dir, stage):
    (Path(shot_dir) / f".done_{stage}").touch()


def read_meta(shot_dir):
    with open(Path(shot_dir) / "meta.json", encoding="utf-8") as f:
        return json.load(f)


def write_meta(shot_dir, meta):
    with open(Path(shot_dir) / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def frame_files(shot_dir):
    return sorted((Path(shot_dir) / "frames").glob("*.jpg"))


def stage_python():
    return sys.executable
