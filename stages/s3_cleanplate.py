"""ProPainter で人物を消した背景 → cleanplate/%06d.png（元解像度）

長尺対策: propainter_segment_frames ごとに分割して実行（ProPainter は全フレームを RAM に持つため）。
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, done, mark_done, run, read_meta, frame_files, stage_python  # noqa: E402


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    cfg = load_config()
    pp = Path(cfg["propainter_dir"])
    if not (pp / "inference_propainter.py").exists():
        raise SystemExit(f"ProPainter が見つかりません: {pp}  (git clone https://github.com/sczhou/ProPainter third_party/ProPainter)")
    k = np.ones((cfg["mask_dilate_px"], cfg["mask_dilate_px"]), np.uint8)
    seg_len = int(cfg.get("propainter_segment_frames", 600))

    for sd in shot_dirs(a.work):
        if done(sd, 3):
            continue
        meta = read_meta(sd)
        frames = frame_files(sd)
        n = len(frames)
        out_dir = sd / "cleanplate"
        out_dir.mkdir(exist_ok=True)

        # 太らせたマスク（境界のにじみを消す）
        dm = sd / "mask_dilated"
        dm.mkdir(exist_ok=True)
        for i in range(n):
            f = sd / "mask" / f"{i:06d}.png"
            cv2.imwrite(str(dm / f.name), cv2.dilate(cv2.imread(str(f), 0), k))

        # 長辺を制限（VRAM 対策）。8 の倍数に
        w, h = meta["width"], meta["height"]
        s = min(1.0, cfg["propainter_max_side"] / max(w, h))
        rw, rh = int(w * s) // 8 * 8, int(h * s) // 8 * 8

        for seg_start in range(0, n, seg_len):
            seg_end = min(n, seg_start + seg_len)
            if all((out_dir / f"{i:06d}.png").exists() for i in range(seg_start, seg_end)):
                continue
            with tempfile.TemporaryDirectory(prefix="pp_", dir=sd) as td:
                td = Path(td)
                (td / "frames").mkdir()
                (td / "masks").mkdir()
                for j, i in enumerate(range(seg_start, seg_end)):
                    link_or_copy(frames[i], td / "frames" / f"{j:06d}.jpg")
                    link_or_copy(dm / f"{i:06d}.png", td / "masks" / f"{j:06d}.png")
                run([stage_python(), "inference_propainter.py",
                     "--video", td / "frames", "--mask", td / "masks",
                     "--output", td / "out", "--width", rw, "--height", rh,
                     "--subvideo_length", cfg["propainter_subvideo"],
                     "--neighbor_length", cfg.get("propainter_neighbor", 10),
                     "--fp16", "--save_frames", "--save_fps", int(round(meta["fps"]))], cwd=pp,
                    env={"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
                res = td / "out" / "frames" / "frames"
                outs = sorted(res.glob("*.png"))
                if len(outs) != seg_end - seg_start:
                    raise SystemExit(f"ProPainter output count mismatch: {len(outs)} != {seg_end - seg_start}")
                # インペイントした領域だけを元フレームに貼り戻す（縮小処理による背景全体の劣化を防ぐ）
                for j, i in enumerate(range(seg_start, seg_end)):
                    src = cv2.imread(str(frames[i]))
                    inp = cv2.imread(str(outs[j]))
                    if inp.shape[:2] != src.shape[:2]:
                        inp = cv2.resize(inp, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_CUBIC)
                    m = cv2.imread(str(dm / f"{i:06d}.png"), 0)
                    m = cv2.GaussianBlur(m, (0, 0), 3).astype(np.float32)[:, :, None] / 255.0
                    comp = (src * (1 - m) + inp * m).astype(np.uint8)
                    cv2.imwrite(str(out_dir / f"{i:06d}.png"), comp)
            print(f"  {sd.name}: cleanplate {seg_start}-{seg_end}/{n}", flush=True)
        mark_done(sd, 3)
        print(f"  {sd.name}: cleanplate ok", flush=True)


if __name__ == "__main__":
    main()
