"""
環境セットアップ（Windows）。run.cmd から自動で呼ばれる。手動でも実行できる。

  python setup_env.py            # 足りないものだけ導入（冪等）。前回完了時から変更が無ければ即終了
  python setup_env.py --check    # 状態表示のみ（不足があれば終了コード 1）
  python setup_env.py --force    # 全項目を再確認・再導入

導入するもの（すべてプロジェクト内 or pip）:
  1. PyTorch (CUDA 12.8 版)              pip。無い場合のみ。約 3GB のダウンロード
  2. requirements.txt                    pip
  3. モデル（各配布元から直接取得）      models/ にダウンロード: MediaPipe（pose heavy / full, hand）
  4. Node 依存 (three, three-vrm, puppeteer + Chrome)  render/ で npm install
  5. ヘッドレス Chrome の GPU 確認

AI モデルは「学習データ非公開かつ商用可」または「学習データも重みも商用可」のものだけ（THIRD_PARTY_NOTICES.md）。
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
STAMP = ROOT / ".setup_done"

TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MODELS = {
    "models/mediapipe/pose_landmarker_heavy.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
    "models/mediapipe/hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    # 速度優先プリセット用
    "models/mediapipe/pose_landmarker_full.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    # クリーンプレート（工程 3）はモデル不要: フロー = NVIDIA Optical Flow（ドライバー同梱）、穴埋め = OpenCV
}
PY_MODULES = ("cv2", "scenedetect", "mediapipe", "imageio_ffmpeg", "scipy", "yaml", "PIL", "webview")


# ------------------------------------------------------------------ util
class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def log(msg):
    print(msg, flush=True)


def sh(cmd, cwd=None, env=None):
    log("  $ " + " ".join(str(c) for c in cmd))
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=e, check=True)


def have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def download(url, dst):
    """一時ファイルに落としてから rename（途中で止まっても壊れたファイルが残らない）"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    log(f"  downloading {url}")
    last = [-1]

    def hook(n, bs, total):
        if total > 0:
            pct = min(100, n * bs * 100 // total)
            if pct // 5 != last[0] // 5:
                last[0] = pct
                print(f"\r    {pct:3d}%  {n * bs / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
    urllib.request.urlretrieve(url, tmp, hook)
    print()
    tmp.replace(dst)


def stamp_hash():
    h = hashlib.sha256()
    for f in (ROOT / "requirements.txt", ROOT / "render" / "package.json", Path(__file__)):
        h.update(f.read_bytes())
    h.update(sys.executable.encode())
    return h.hexdigest()


# ------------------------------------------------------------------ steps
def opencv_contrib_ok():
    try:
        import cv2
        return hasattr(getattr(cv2, "xphoto", None), "inpaint")
    except Exception:
        return False


def check(verbose=True):
    """状態を表示し、全部そろっていれば True"""
    ok = True

    def item(label, good, hint=""):
        nonlocal ok
        ok &= bool(good)
        if verbose:
            log(f"  [{'OK' if good else 'NG'}] {label}" + ("" if good or not hint else f"  -> {hint}"))

    v = sys.version_info
    item(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})", v >= (3, 10), "Python 3.10 以上が必要")
    try:
        import torch
        cuda = torch.cuda.is_available()
        item(f"torch {torch.__version__} CUDA={cuda}" + (f" {torch.cuda.get_device_name(0)}" if cuda else ""),
             cuda, "CUDA 版 torch が必要（NVIDIA ドライバを確認）")
    except Exception:
        item("torch", False, "未導入")
    for m in PY_MODULES:
        item(f"python module: {m}", have(m))
    for rel in MODELS:
        item(rel, (ROOT / rel).exists())
    # 警告のみ（無くても Telea で動く）。opencv-python と opencv-contrib-python が両方あると上書きし合い、contrib が消える
    if verbose and not opencv_contrib_ok():
        log("  [!!] OpenCV contrib（cv2.xphoto.inpaint）が無効です。クリーンプレートの穴埋めが Telea（にじみやすい）になります")
        log("       直し方: python -m pip uninstall -y opencv-python opencv-python-headless && "
            "python -m pip install --force-reinstall --no-deps opencv-contrib-python")
    item(f"node ({shutil.which('node') or 'not found'})", shutil.which("node"), "Node.js LTS を https://nodejs.org/ から導入")
    item("render/node_modules", (ROOT / "render" / "node_modules" / "puppeteer").exists())
    item("nvidia-smi（GPU ドライバ）", shutil.which("nvidia-smi"), "NVIDIA GPU とドライバが必要")
    return ok


def ensure(force=False):
    py = sys.executable
    v = sys.version_info
    if v < (3, 10):
        raise SystemExit(f"Python 3.10 以上が必要です（現在 {v.major}.{v.minor}）。https://www.python.org/downloads/windows/")
    if v >= (3, 14):
        log(f"  [!!] Python {v.major}.{v.minor} は未検証です。問題が出たら 3.12 を使ってください")

    # 1. torch
    torch_ok = False
    try:
        import torch
        torch_ok = torch.cuda.is_available()
        if not torch_ok:
            log(f"  [!!] torch {torch.__version__} は CUDA が使えません。CUDA 12.8 版に入れ替えます")
    except Exception:
        log("  torch が無いので CUDA 12.8 版を導入します（約 3GB のダウンロード。時間がかかります）")
    if not torch_ok or force:
        sh([py, "-m", "pip", "install", "--upgrade", "torch", "--index-url", TORCH_INDEX])

    # 2. requirements
    missing = [m for m in PY_MODULES if not have(m)]
    if missing or force:
        log(f"  pip: requirements.txt を導入（不足: {missing or 'none'}）")
        sh([py, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    # scenedetect などが opencv-python を引き込むと contrib 版（穴埋めの ShiftMap）が上書きされて消えるので入れ直す
    if not opencv_contrib_ok():
        log("  OpenCV: opencv-python / -headless が contrib 版を上書きしているので、contrib 版を入れ直します")
        sh([py, "-m", "pip", "uninstall", "-y", "opencv-python", "opencv-python-headless"])
        sh([py, "-m", "pip", "install", "--force-reinstall", "--no-deps", "opencv-contrib-python"])

    # 3. モデル
    for rel, url in MODELS.items():
        p = ROOT / rel
        if p.exists() and not force:
            continue
        download(url, p)

    # 4. Node
    if not shutil.which("node"):
        raise SystemExit("Node.js が見つかりません。https://nodejs.org/ から LTS を導入して PATH を通し、再実行してください")
    npm = shutil.which("npm") or shutil.which("npm.cmd")   # Windows では npm.cmd（shell 無しでは名前解決されない）
    if not npm:
        raise SystemExit("npm が見つかりません。Node.js を入れ直してください（npm 同梱）")
    if not (ROOT / "render" / "node_modules" / "puppeteer").exists() or force:
        log("  npm install（three, three-vrm, puppeteer + Chrome 約 170MB）")
        sh([npm, "install", "--no-audit", "--no-fund"], cwd=ROOT / "render")

    # 5. GPU 確認
    sh([shutil.which("node"), "s4_threevrm.js", "--gpu-test"], cwd=ROOT / "render")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="状態表示のみ")
    ap.add_argument("--force", action="store_true", help="全項目を再確認・再導入")
    ap.add_argument("--ensure", action="store_true", help="（既定と同じ）足りないものだけ導入")
    a = ap.parse_args()

    if a.check:
        sys.exit(0 if check() else 1)

    h = stamp_hash()
    if not a.force and STAMP.exists() and STAMP.read_text().strip() == h and check(verbose=False):
        log("setup: up to date")
        return

    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "setup.log", "a", encoding="utf-8") as f:
        sys.stdout = Tee(sys.__stdout__, f)
        try:
            log("== setup: 不足しているものを導入します")
            ensure(force=a.force)
            log("== check")
            if not check():
                raise SystemExit("セットアップが完了していません。上の [NG] を確認してください（logs/setup.log）")
            STAMP.write_text(h)
            log("== setup: done")
        finally:
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
