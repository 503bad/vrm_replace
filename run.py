"""
使い方:
  python run.py --video input.mp4 --vrm chara.vrm --out result.mp4 [--auto] [--from N] [--no-cleanplate]

各工程は別プロセスで実行し、終了時に VRAM を解放する。
work/<video名>/shot_NNN/.done_N があれば、その工程はスキップ（再開可能）。
"""
import argparse
import sys
from pathlib import Path

from common import ROOT, load_config, run, stage_python


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--auto", action="store_true",
                    help="2ショット目以降は最初のクリック位置を使い回す")
    ap.add_argument("--from", dest="from_stage", type=int, default=0,
                    help="この工程からやり直す（.done を無視）")
    ap.add_argument("--no-cleanplate", action="store_true",
                    help="ProPainter を飛ばし、元映像の上にそのまま重ねる（確認用）")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="先頭 N フレームだけ処理する（確認用）")
    ap.add_argument("--work", default=None, help="作業ディレクトリを指定（省略時 work/<video名>）")
    args = ap.parse_args()

    cfg = load_config()
    video = Path(args.video).resolve()
    vrm = Path(args.vrm).resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")
    if not vrm.exists():
        sys.exit(f"vrm not found: {vrm}")
    work = Path(args.work).resolve() if args.work else (Path(cfg["work_dir"]) / video.stem).resolve()
    work.mkdir(parents=True, exist_ok=True)
    py = stage_python()
    st = ROOT / "stages"

    if args.from_stage > 0:
        for f in work.glob("shot_*/.done_*"):
            if int(f.name.split("_")[-1]) >= args.from_stage:
                f.unlink()

    print("== 0 shots", flush=True)
    run([py, st / "s0_shots.py", "--video", video, "--work", work,
         "--max-frames", args.max_frames])

    print("== 1 mask (SAM2)", flush=True)
    run([py, st / "s1_mask.py", "--work", work] + (["--auto"] if args.auto else []))

    print(f"== 2 mocap ({cfg['mocap_backend']})", flush=True)
    run([py, st / "s2_mocap.py", "--work", work])

    if args.no_cleanplate:
        print("== 3 cleanplate: skipped (--no-cleanplate)", flush=True)
    else:
        print("== 3 cleanplate (ProPainter)", flush=True)
        run([py, st / "s3_cleanplate.py", "--work", work])

    print("== 4 render (three-vrm)", flush=True)
    run([py, st / "s4_render.py", "--work", work, "--vrm", vrm])

    print("== 5 composite", flush=True)
    run([py, st / "s5_final.py", "--work", work, "--video", video,
         "--out", Path(args.out).resolve()] + (["--no-cleanplate"] if args.no_cleanplate else []))
    print("done:", args.out)


if __name__ == "__main__":
    main()
