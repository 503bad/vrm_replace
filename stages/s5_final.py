"""背景（cleanplate/%06d.png または元フレーム）の上に render/f%04d.png を重ね、全ショットを結合し、元動画の音声を付けて出力。"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, read_meta, ffmpeg_exe  # noqa: E402


def nvenc_av1_ok(ff):
    """この GPU の NVENC が AV1 を書き出せるか（RTX 40 以降）。短い黒画面を試しに書き出して調べる"""
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                        "-c:v", "av1_nvenc", "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


def codec_args(cfg, ff):
    """(映像の引数, 音声の引数, 表示名)。特許料を求めない方針の形式だけを使う（H.264 / AAC は使わない）:
    映像は AV1（NVENC が対応する GPU = RTX 40 以降）か VP9（それ以外、CPU）、音声は Opus"""
    q = str(cfg.get("video_quality", 26))
    opus = ["-c:a", "libopus", "-b:a", "160k"]
    if nvenc_av1_ok(ff):
        return (["-c:v", "av1_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", q, "-b:v", "0"], opus, "AV1 (NVENC) / Opus")
    return (["-c:v", "libvpx-vp9", "-crf", q, "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"],
            opus, "VP9 / Opus")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-cleanplate", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.work)
    work = Path(a.work)
    ff = ffmpeg_exe()
    vargs, aargs, desc = codec_args(cfg, ff)
    print(f"  codec: {desc}", flush=True)

    parts = []
    for sd in shot_dirs(work):
        meta = read_meta(sd)
        fps = f"{meta['fps']:.6f}"
        w, h = meta["width"], meta["height"]
        n_render = len(list((sd / "render").glob("f*.png")))
        if n_render == 0:
            raise SystemExit(f"{sd.name}: render/ が空です（s4 未完了）")
        if a.no_cleanplate or not (sd / "cleanplate").exists():
            bg = str(sd / "frames" / "%06d.jpg")
        else:
            bg = str(sd / "cleanplate" / "%06d.png")
        comp = sd / "composite.mp4"
        subprocess.run([
            ff, "-y", "-loglevel", "error",
            "-framerate", fps, "-i", bg,
            "-framerate", fps, "-i", str(sd / "render" / "f%04d.png"),
            "-filter_complex",
            f"[0:v]scale={w}:{h}:flags=lanczos[bg];[bg][1:v]overlay=0:0:format=auto:shortest=1,format=yuv420p",
            *vargs, "-r", fps, str(comp),
        ], check=True)
        parts.append(comp)
        print(f"  {sd.name}: composited ({n_render} frames)", flush=True)

    lst = work / "concat.txt"
    lst.write_text("".join("file '" + str(p.resolve()).replace("\\", "/") + "'\n" for p in parts),
                   encoding="utf-8")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ff, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-i", str(a.video),
        "-map", "0:v", "-map", "1:a?", "-c:v", "copy", *aargs, "-shortest",
        "-movflags", "+faststart", str(out),
    ], check=True)
    print(f"  wrote {out}", flush=True)


if __name__ == "__main__":
    main()
