"""
環境構築（Windows ネイティブ）。冪等なので何度実行してもよい。

  python setup_env.py            # 全部
  python setup_env.py --check    # 確認だけ

やること:
  1. pip: requirements.txt（torch は別途 cu128 版を入れておく。無ければここで入れる）
  2. SAM 2 を third_party/sam2 に clone して pip install -e（CUDA 拡張なし）
  3. ProPainter を third_party/ProPainter に clone（重みは初回実行時に自動 DL）
  4. SAM2 チェックポイント / MediaPipe Pose モデルを models/ に DL
  5. render/ で npm install（three, @pixiv/three-vrm, puppeteer + Chrome）
  6. ヘッドレス Chrome で GPU WebGL が効くか確認
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TP = ROOT / "third_party"
MODELS = {
    "models/sam2/sam2.1_hiera_base_plus.pt":
        "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt",
    "models/mediapipe/pose_landmarker_heavy.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
    "models/mediapipe/hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
}


def sh(cmd, cwd=None, env=None):
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=e, check=True)


def have(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def check():
    ok = True
    print(f"python {sys.version.split()[0]}")
    try:
        import torch
        print(f"torch {torch.__version__} cuda={torch.cuda.is_available()} "
              f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''}")
        ok &= torch.cuda.is_available()
    except ImportError:
        print("torch: MISSING"); ok = False
    for m in ("cv2", "scenedetect", "mediapipe", "sam2", "imageio_ffmpeg", "scipy", "einops"):
        print(f"{m}: {'ok' if have(m) else 'MISSING'}"); ok &= have(m)
    for rel in MODELS:
        p = ROOT / rel
        print(f"{rel}: {'ok' if p.exists() else 'MISSING'}"); ok &= p.exists()
    print(f"ProPainter: {'ok' if (TP / 'ProPainter' / 'inference_propainter.py').exists() else 'MISSING'}")
    print(f"node: {shutil.which('node') or 'MISSING'}")
    print(f"render/node_modules: {'ok' if (ROOT / 'render' / 'node_modules').exists() else 'MISSING'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check() else 1)

    py = sys.executable
    if not have("torch"):
        sh([py, "-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu128"])
    sh([py, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])

    TP.mkdir(exist_ok=True)
    if not (TP / "sam2").exists():
        sh(["git", "clone", "--depth", "1", "https://github.com/facebookresearch/sam2.git", TP / "sam2"])
    if not have("sam2"):
        sh([py, "-m", "pip", "install", "-e", "."], cwd=TP / "sam2", env={"SAM2_BUILD_CUDA": "0"})
    if not (TP / "ProPainter").exists():
        sh(["git", "clone", "--depth", "1", "https://github.com/sczhou/ProPainter.git", TP / "ProPainter"])

    for rel, url in MODELS.items():
        p = ROOT / rel
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {url}", flush=True)
        urllib.request.urlretrieve(url, p)

    if not shutil.which("node"):
        sys.exit("Node.js が見つかりません。https://nodejs.org から LTS を入れて再実行してください")
    if not (ROOT / "render" / "node_modules").exists():
        sh(["npm", "install", "--no-audit", "--no-fund"], cwd=ROOT / "render")
    sh(["node", "s4_threevrm.js", "--gpu-test"], cwd=ROOT / "render")

    print("\n== check")
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    main()
