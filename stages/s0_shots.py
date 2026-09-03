"""ショット分割 → 各ショットを frames/%06d.jpg に展開。mp4/avi どちらも ffmpeg で読む。"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, write_meta, done, mark_done, ffmpeg_exe  # noqa: E402

import cv2  # noqa: E402
from scenedetect import detect, ContentDetector  # noqa: E402


def probe(video):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, w, h, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--max-frames", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config()
    work = Path(a.work)
    fps, w, h, n = probe(a.video)
    if a.max_frames > 0:
        n = min(n, a.max_frames)

    scenes = detect(str(a.video), ContentDetector(threshold=cfg["scene_threshold"]))
    ranges = [(s.frame_num, e.frame_num) for s, e in scenes] if scenes else [(0, n)]
    # 範囲を n に切り詰め、短すぎるショットは前に吸収
    clipped = []
    for s, e in ranges:
        s, e = min(s, n), min(e, n)
        if e <= s:
            continue
        if clipped and (e - s) < cfg["min_shot_frames"]:
            clipped[-1] = (clipped[-1][0], e)
        else:
            clipped.append((s, e))
    if clipped and clipped[-1][1] < n:
        clipped[-1] = (clipped[-1][0], n)
    ranges = clipped or [(0, n)]
    print(f"{len(ranges)} shots, {fps:.3f} fps, {w}x{h}, {n} frames", flush=True)

    ff = ffmpeg_exe()
    for i, (s, e) in enumerate(ranges):
        sd = work / f"shot_{i:03d}"
        if done(sd, 0):
            continue
        fr = sd / "frames"
        fr.mkdir(parents=True, exist_ok=True)
        for old in fr.glob("*.jpg"):
            old.unlink()
        # SAM2 は jpg 連番を要求する
        subprocess.run([
            ff, "-y", "-loglevel", "error", "-i", str(a.video),
            "-vf", f"select='between(n\\,{s}\\,{e - 1})'", "-vsync", "0",
            "-q:v", "2", "-start_number", "0", str(fr / "%06d.jpg"),
        ], check=True)
        got = len(list(fr.glob("*.jpg")))
        if got == 0:
            raise SystemExit(f"no frames extracted for shot {i} ({s}-{e})")
        write_meta(sd, {"start": s, "end": s + got, "fps": fps, "width": w, "height": h,
                        "n_frames": got})
        mark_done(sd, 0)
        print(f"  shot {i}: frames {s}-{s + got}", flush=True)


if __name__ == "__main__":
    main()
