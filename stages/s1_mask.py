"""
人物クリック → SAM 2 で全フレーム追跡 → mask/%06d.png（0/255）

- 左クリック: 対象人物（正例）、右クリック: 除外したい場所（負例）、r: やり直し、Enter: 確定
- 長尺対策として sam2_chunk_frames ごとに区切り、前チャンク最終フレームのマスクをプロンプトに次を繋ぐ
- 選択点は shot_NNN/select.json に保存（再開時はクリック不要）
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, done, mark_done, read_meta, frame_files  # noqa: E402


def pick_points(img_path, title):
    """tkinter でクリック UI（OpenCV の highgui に依存しない。headless ビルドでも動く）。
    左クリック: 正例、右クリック: 負例、r: リセット、Enter: 確定、Esc: 中止。"""
    import tkinter as tk
    from PIL import Image, ImageTk

    img = Image.open(img_path).convert("RGB")
    W, H = img.size
    root = tk.Tk()
    root.title(title)
    sw, sh = root.winfo_screenwidth() - 80, root.winfo_screenheight() - 160
    scale = min(1.0, sw / W, sh / H)
    dw, dh = int(W * scale), int(H * scale)
    photo = ImageTk.PhotoImage(img.resize((dw, dh), Image.LANCZOS))
    tk.Label(root, text="左クリック: 置き換える人物   右クリック: 除外したい場所   r: やり直し   Enter: 確定   Esc: 中止",
             font=("", 11)).pack(fill="x")
    cv = tk.Canvas(root, width=dw, height=dh, highlightthickness=0)
    cv.pack()
    cv.create_image(0, 0, anchor="nw", image=photo)
    pts, labels, marks, result = [], [], [], {"ok": False}

    def draw():
        for m in marks:
            cv.delete(m)
        marks.clear()
        for (x, y), l in zip(pts, labels):
            X, Y = x * scale, y * scale
            marks.append(cv.create_oval(X - 8, Y - 8, X + 8, Y + 8, outline="lime" if l else "red", width=3))

    def click(ev, label):
        pts.append((int(ev.x / scale), int(ev.y / scale))); labels.append(label); draw()

    def reset(_=None):
        pts.clear(); labels.clear(); draw()

    def ok(_=None):
        if 1 in labels:
            result["ok"] = True
            root.destroy()

    cv.bind("<Button-1>", lambda e: click(e, 1))
    cv.bind("<Button-3>", lambda e: click(e, 0))
    root.bind("<Return>", ok)
    root.bind("r", reset)
    root.bind("<Escape>", lambda _: root.destroy())
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.mainloop()
    if not result["ok"]:
        raise SystemExit("cancelled")
    return pts, labels


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy(src, dst)


def keep_largest_component(m):
    """飛び地のゴミ成分を落とし、最大の連結成分だけ残す"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 2:
        return m
    areas = stats[1:, cv2.CC_STAT_AREA]
    big = 1 + int(np.argmax(areas))
    # 最大成分の 5% 以上の成分は残す（手足が一時的に切れた場合）
    keep = [i + 1 for i, a in enumerate(areas) if a >= 0.05 * areas.max()]
    return np.isin(lab, keep) if len(keep) > 1 else (lab == big)


def expand_prompt_with_pose(img_path, pts, labels, cfg):
    """クリック1点だと SAM2 は「腕」など部位を選びがち。MediaPipe で人物を見つけ、
    人物 bbox + 主要関節の正例点に展開する。見つからなければ None。"""
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except ImportError:
        return None
    img = cv2.cvtColor(cv2.imread(str(img_path)), cv2.COLOR_BGR2RGB)
    H, W = img.shape[:2]
    opts = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=cfg["mediapipe_model"]),
        running_mode=vision.RunningMode.IMAGE, num_poses=6,
        min_pose_detection_confidence=0.3, min_pose_presence_confidence=0.3)
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)))
    click = np.array([p for p, l in zip(pts, labels) if l == 1][0], float)
    best, best_d = None, 1e18
    for lms in res.pose_landmarks or []:
        px = np.array([[l.x * W, l.y * H] for l in lms])
        vis = np.array([l.visibility for l in lms])
        good = px[vis > 0.5]
        if len(good) < 6:
            continue
        x0, y0 = good.min(0); x1, y1 = good.max(0)
        pad = 0.12 * max(x1 - x0, y1 - y0)
        box = [max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)]
        inside = box[0] <= click[0] <= box[2] and box[1] <= click[1] <= box[3]
        d = 0 if inside else np.linalg.norm(click - [(x0 + x1) / 2, (y0 + y1) / 2])
        if d < best_d:
            best_d, best = d, (box, px, vis)
    if best is None or best_d > 0.25 * max(W, H):
        return None
    box, px, vis = best
    key = [0, 11, 12, 23, 24, 25, 26, 27, 28]   # 鼻 肩 腰 膝 足首
    extra = [px[i].tolist() for i in key if vis[i] > 0.6]
    return {"points": list(pts) + extra, "labels": list(labels) + [1] * len(extra),
            "box": [float(v) for v in box]}


def largest_multimask(predictor, img_path, pts, labels):
    """フォールバック: 画像予測の multimask から、クリックを含む最大のマスク（＝人物全体）を選ぶ"""
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    ip = SAM2ImagePredictor(predictor)
    img = cv2.cvtColor(cv2.imread(str(img_path)), cv2.COLOR_BGR2RGB)
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        ip.set_image(img)
        masks, scores, _ = ip.predict(point_coords=np.array(pts, np.float32),
                                      point_labels=np.array(labels, np.int32), multimask_output=True)
    click = [p for p, l in zip(pts, labels) if l == 1][0]
    cand = [m for m in masks if m[int(click[1]), int(click[0])]]
    if not cand:
        cand = list(masks)
    return max(cand, key=lambda m: m.sum()).astype(bool)


def track_chunk(predictor, frames, first_prompt, out_dir, offset, skip_first):
    """frames: このチャンクの jpg リスト。first_prompt: ('points', dict) | ('mask', bool HxW)。
    マスクを out_dir/{offset+i:06d}.png に書き、最終フレームのマスク(bool)を返す。"""
    with tempfile.TemporaryDirectory(prefix="sam2_", dir=out_dir.parent) as td:
        for i, f in enumerate(frames):
            link_or_copy(f, Path(td) / f"{i:06d}.jpg")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state = predictor.init_state(video_path=td, offload_video_to_cpu=len(frames) > 400)
            kind, a = first_prompt
            if kind == "points":
                predictor.add_new_points_or_box(
                    state, frame_idx=0, obj_id=1,
                    points=np.array(a["points"], dtype=np.float32), labels=np.array(a["labels"], np.int32),
                    box=np.array(a["box"], np.float32) if a.get("box") else None)
            else:
                predictor.add_new_mask(state, frame_idx=0, obj_id=1, mask=torch.from_numpy(a))
            last = None
            for fidx, _, logits in predictor.propagate_in_video(state):
                m = keep_largest_component((logits[0, 0] > 0).cpu().numpy())
                last = m
                if fidx == 0 and skip_first:
                    continue
                cv2.imwrite(str(out_dir / f"{offset + fidx:06d}.png"), m.astype(np.uint8) * 255)
            predictor.reset_state(state)
    torch.cuda.empty_cache()
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--auto", action="store_true")
    a = ap.parse_args()
    cfg = load_config()

    # 先にクリックを全部済ませる（GPU ロード待ちの間に UI が出ないように）
    todo = [sd for sd in shot_dirs(a.work) if not done(sd, 1)]
    first = None
    for sd in todo:
        sel = sd / "select.json"
        if sel.exists():
            d = json.loads(sel.read_text(encoding="utf-8"))
            first = first or (d["points"], d["labels"])
            continue
        if a.auto and first is not None:
            pts, labels = first
        else:
            pts, labels = pick_points(frame_files(sd)[0], f"{sd.name}: click the person")
            first = first or (pts, labels)
        sel.write_text(json.dumps({"points": pts, "labels": labels}), encoding="utf-8")

    if not todo:
        return
    from sam2.build_sam import build_sam2_video_predictor
    predictor = build_sam2_video_predictor(cfg["sam2_cfg"], cfg["sam2_ckpt"], device="cuda")
    chunk = int(cfg["sam2_chunk_frames"])

    for sd in todo:
        meta = read_meta(sd)
        frames = frame_files(sd)
        n = len(frames)
        mask_dir = sd / "mask"
        mask_dir.mkdir(exist_ok=True)
        d = json.loads((sd / "select.json").read_text(encoding="utf-8"))
        # クリック → 人物全体のプロンプトへ展開
        exp = expand_prompt_with_pose(frames[0], d["points"], d["labels"], cfg)
        if exp is not None:
            prompt = ("points", exp)
            print(f"  {sd.name}: prompt expanded via pose (box + {len(exp['points'])} points)", flush=True)
        else:
            prompt = ("mask", largest_multimask(predictor, frames[0], d["points"], d["labels"]))
            print(f"  {sd.name}: prompt expanded via multimask (largest)", flush=True)
        start = 0
        while start < n:
            end = min(n, start + chunk)
            last = track_chunk(predictor, frames[start:end], prompt, mask_dir, start, skip_first=start > 0)
            print(f"  {sd.name}: masked {start}-{end}/{n}", flush=True)
            if end >= n:
                break
            if last is None or not last.any():
                # 人物がフレームアウトした: 以降は空マスク。再出現は追えない（制約）
                for i in range(end, n):
                    cv2.imwrite(str(mask_dir / f"{i:06d}.png"), np.zeros(last.shape, np.uint8))
                print(f"  {sd.name}: target lost at frame {end}, rest empty", flush=True)
                break
            prompt = ("mask", last)
            start = end - 1   # 1 フレーム重ねてマスクで繋ぐ
        assert len(list(mask_dir.glob("*.png"))) >= n, "mask count mismatch"
        mark_done(sd, 1)
        print(f"  {sd.name}: mask ok ({n} frames, {meta['width']}x{meta['height']})", flush=True)


if __name__ == "__main__":
    main()
