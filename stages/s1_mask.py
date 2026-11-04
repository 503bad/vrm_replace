"""
人物クリック → MediaPipe Pose の人物マスクで全フレーム追跡 → mask/%06d.png（0/255）

- 学習データを公開していない・商用可（Apache-2.0）の MediaPipe だけを使う（SAM 2 は学習データ SA-1B が研究用のため不使用）
- 最初のフレームでクリック位置にいる人物を選び（右クリックの位置にいる人物は除外）、以降は前フレームの人物と
  最も重なる人物を追う。一時的に見失ったら直前のマスクでつなぎ、長く見失ったら最後の位置の近くで再出現を待つ
- 左クリック: 対象人物（正例）、右クリック: 除外したい人物（負例）、r: やり直し、Enter: 確定（CLI 時の画面）
- 選択点は shot_NNN/select.json に保存（再開時はクリック不要。GUI は自分の画面でクリックを受けて書く）
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

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


def keep_largest_component(m):
    """飛び地のゴミ成分を落とし、最大の連結成分（と、その 5% 以上の成分）だけ残す"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 2:
        return m
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = [i + 1 for i, a in enumerate(areas) if a >= 0.05 * areas.max()]
    return np.isin(lab, keep)


def pose_box(lms, W, H, min_vis=0.5):
    """ランドマーク → 人物の bbox [x0,y0,x1,y1]（見えている点だけ。少なすぎれば None）"""
    px = np.array([[l.x * W, l.y * H] for l in lms])
    vis = np.array([l.visibility for l in lms])
    good = px[vis > min_vis]
    if len(good) < 6:
        return None
    x0, y0 = good.min(0)
    x1, y1 = good.max(0)
    pad = 0.12 * max(x1 - x0, y1 - y0)
    return np.array([x0 - pad, y0 - pad, x1 + pad, y1 + pad])


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def contains(box, p):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


class PersonTracker:
    """クリックで選んだ人物を、MediaPipe Pose の検出（複数人）の中から追い続ける"""

    def __init__(self, pts, labels, W, H, hold_frames):
        self.pos = [np.array(p, float) for p, l in zip(pts, labels) if l == 1]
        self.neg = [np.array(p, float) for p, l in zip(pts, labels) if l == 0]
        self.W, self.H = W, H
        self.box = None          # 追っている人物の bbox
        self.mask = None         # 直前のマスク（確率）
        self.missing = 0
        self.hold = hold_frames

    def _first(self, cands):
        """まだ人物が決まっていない: 正例クリックを含む（無ければ最も近い）人物。負例を含む人物は除く"""
        best, best_d = None, 1e18
        for c in cands:
            if any(contains(c["box"], q) for q in self.neg):
                continue
            ctr = (c["box"][:2] + c["box"][2:]) / 2
            d = min(0.0 if contains(c["box"], q) else float(np.linalg.norm(q - ctr)) for q in self.pos)
            if d < best_d:
                best, best_d = c, d
        return best if best is not None and best_d < 0.25 * max(self.W, self.H) else None

    def _follow(self, cands):
        """前フレームの人物と最も重なる人物。見失っている間は、最後の位置の近くにいる人物"""
        best, score = None, 0.0
        for c in cands:
            s = iou(self.box, c["box"])
            if s > score:
                best, score = c, s
        if score >= 0.1:
            return best
        if self.missing > 0:
            ctr = (self.box[:2] + self.box[2:]) / 2
            size = max(self.box[2] - self.box[0], self.box[3] - self.box[1])
            near = [(float(np.linalg.norm((c["box"][:2] + c["box"][2:]) / 2 - ctr)), c) for c in cands]
            near = [x for x in near if x[0] < max(size, 0.1 * max(self.W, self.H))]
            if near:
                return min(near, key=lambda x: x[0])[1]
        return None

    def step(self, cands):
        c = self._first(cands) if self.box is None else self._follow(cands)
        if c is not None:
            self.box, self.mask, self.missing = c["box"], c["mask"], 0
            return c["mask"], "ok"
        self.missing += 1
        if self.mask is not None and self.missing <= self.hold:
            return self.mask, "hold"          # 一時的に見失った: 直前のマスクでつなぐ
        return np.zeros((self.H, self.W), np.float32), "lost"


def make_landmarker(cfg, num_poses):
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    opts = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=cfg["mediapipe_model"]),
        running_mode=vision.RunningMode.VIDEO, num_poses=num_poses, output_segmentation_masks=True,
        min_pose_detection_confidence=0.3, min_pose_presence_confidence=0.3, min_tracking_confidence=0.5)
    return vision.PoseLandmarker.create_from_options(opts)


def track_shot(sd, cfg, pts, labels):
    import mediapipe as mp
    meta = read_meta(sd)
    frames = frame_files(sd)
    n = len(frames)
    W, H = meta["width"], meta["height"]
    fps = meta.get("fps") or 30.0
    mask_dir = sd / "mask"
    mask_dir.mkdir(exist_ok=True)
    thr = float(cfg.get("mask_threshold", 0.5))
    smooth = float(cfg.get("mask_temporal_smooth", 0.3))
    tracker = PersonTracker(pts, labels, W, H, int(cfg.get("mask_hold_frames", 8)))
    stats = {"ok": 0, "hold": 0, "lost": 0}
    prev_prob = None
    with make_landmarker(cfg, int(cfg.get("mask_num_poses", 4))) as lm:
        for i, f in enumerate(frames):
            img = cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB)
            res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)),
                                      int(round(i * 1000.0 / fps)))
            cands = []
            for k, lms in enumerate(res.pose_landmarks or []):
                box = pose_box(lms, W, H)
                if box is None or not res.segmentation_masks or k >= len(res.segmentation_masks):
                    continue
                prob = res.segmentation_masks[k].numpy_view()
                prob = prob[:, :, 0] if prob.ndim == 3 else prob
                cands.append({"box": box, "mask": np.array(prob, np.float32)})
            prob, st = tracker.step(cands)
            stats[st] += 1
            # 時間方向に少しだけならす（輪郭のちらつき対策）。見失った直後はならさない
            if prev_prob is not None and st == "ok" and smooth > 0:
                prob = (1 - smooth) * prob + smooth * prev_prob
            prev_prob = prob if st == "ok" else None
            m = prob > thr
            if m.any():
                m = keep_largest_component(m)
            cv2.imwrite(str(mask_dir / f"{i:06d}.png"), m.astype(np.uint8) * 255)
            if (i + 1) % 50 == 0 or i + 1 == n:
                print(f"  {sd.name}: masked 0-{i + 1}/{n}", flush=True)
    if stats["ok"] == 0:
        raise SystemExit(f"{sd.name}: クリックした位置に人物が見つかりませんでした。人物の胴体あたりをクリックしてください")
    print(f"  {sd.name}: tracked {stats['ok']} / held {stats['hold']} / lost {stats['lost']} frames", flush=True)
    return n, W, H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--auto", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.work)

    # 先にクリックを全部済ませる
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

    for sd in todo:
        d = json.loads((sd / "select.json").read_text(encoding="utf-8"))
        n, W, H = track_shot(sd, cfg, d["points"], d["labels"])
        mark_done(sd, 1, cfg)
        print(f"  {sd.name}: mask ok ({n} frames, {W}x{H})", flush=True)


if __name__ == "__main__":
    main()
