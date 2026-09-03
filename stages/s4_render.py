"""three-vrm（Node + Puppeteer, GPU）で VRM をレンダー → shot_NNN/render/f%04d.png（RGBA 透過）"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ROOT, load_config, shot_dirs, done, run, node_exe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--shot", default=None, help="このショットだけ（デバッグ用）")
    ap.add_argument("--max-frames", type=int, default=0, help="各ショット先頭 N フレームだけ（デバッグ用）")
    ap.add_argument("--no-skip", action="store_true", help=".done_4 を無視して再レンダー")
    a = ap.parse_args()
    cfg = load_config()
    js = ROOT / "render" / "s4_threevrm.js"
    if not (ROOT / "render" / "node_modules").exists():
        raise SystemExit("render/node_modules がありません。`cd render && npm install` を実行してください")

    cmd = [node_exe(), js, "--work", Path(a.work).resolve(), "--vrm", Path(a.vrm).resolve(),
           "--hip-height", cfg["person_hip_height"], "--warmup", cfg["render_warmup_frames"],
           "--light-dir", ",".join(str(x) for x in cfg["light_dir"]),
           "--light-intensity", cfg["light_intensity"], "--ambient", cfg["ambient_intensity"]]
    if cfg.get("chrome_exe"):
        cmd += ["--chrome", cfg["chrome_exe"]]
    if cfg.get("allow_software_gl"):
        cmd += ["--allow-software"]
    if a.shot:
        cmd += ["--shot", a.shot]
    if a.max_frames:
        cmd += ["--max-frames", a.max_frames]
    if a.no_skip:
        cmd += ["--no-skip"]
    run(cmd, cwd=ROOT / "render")

    missing = [sd.name for sd in shot_dirs(a.work) if not done(sd, 4) and not a.max_frames
               and (a.shot is None or sd.name == a.shot)]
    if missing:
        raise SystemExit(f"render not finished for: {missing}")


if __name__ == "__main__":
    main()
