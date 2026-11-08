"""背景（cleanplate/%06d.png または元フレーム）の上に render/f%04d.png を重ね、全ショットを結合し、元動画の音声を付けて出力。"""
import argparse
from fractions import Fraction
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, read_meta, ffmpeg_exe  # noqa: E402


FORMATS = ("mp4", "mov")


def codec_args(cfg, fmt):
    """(映像の引数, 画素形式, 音声の引数, 表示名)
    mp4: H.264（GPU の NVENC）/ AAC。X や YouTube への投稿、Premiere などでそのまま使える
    mov: GoPro CineForm（Apache-2.0 で公開されている、特許料のない編集向けの形式）/ PCM。ファイルは大きい"""
    if fmt == "mov":
        return (["-c:v", "cfhd"], "yuv422p10le", ["-c:a", "pcm_s16le"], "CineForm / PCM (mov)")
    cq = str(cfg.get("h264_cq", 19))
    return (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
             "-profile:v", "high"], "yuv420p", ["-c:a", "aac", "-b:a", "192k"], "H.264 (NVENC) / AAC (mp4)")


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
    out = Path(a.out)
    fmt = out.suffix.lower().lstrip(".")
    if fmt not in FORMATS:
        raise SystemExit(f"出力は .mp4 か .mov にしてください: {out.name}")
    vargs, pix, aargs, desc = codec_args(cfg, fmt)
    print(f"  codec: {desc}", flush=True)

    parts = []
    for sd in shot_dirs(work):
        meta = read_meta(sd)
        # 29.97 などは 30000/1001 のような分数で渡す（小数だとタイムベースが半端な値になり、投稿先や編集ソフトで扱いにくい）
        fr = Fraction(meta["fps"]).limit_denominator(1001)
        fps = f"{fr.numerator}/{fr.denominator}"
        w, h = meta["width"], meta["height"]
        n_render = len(list((sd / "render").glob("f*.png")))
        if n_render == 0:
            raise SystemExit(f"{sd.name}: render/ が空です（s4 未完了）")
        if a.no_cleanplate or not (sd / "cleanplate").exists():
            bg = str(sd / "frames" / "%06d.jpg")
        else:
            bg = str(sd / "cleanplate" / "%06d.png")
        comp = sd / f"composite.{fmt}"
        subprocess.run([
            ff, "-y", "-loglevel", "error",
            "-framerate", fps, "-i", bg,
            "-framerate", fps, "-i", str(sd / "render" / "f%04d.png"),
            "-filter_complex",
            f"[0:v]scale={w}:{h}:flags=lanczos[bg];[bg][1:v]overlay=0:0:format=auto:shortest=1,format={pix}",
            *vargs, "-r", fps, str(comp),
        ], check=True)
        parts.append(comp)
        print(f"  {sd.name}: composited ({n_render} frames)", flush=True)

    lst = work / "concat.txt"
    lst.write_text("".join("file '" + str(p.resolve()).replace("\\", "/") + "'\n" for p in parts),
                   encoding="utf-8")
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
