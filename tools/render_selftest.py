"""
s4（three-vrm レンダラ）の単体テスト。合成モーション 3 フレーム（T ポーズ / 90° 回転 / 左腕上げ）を
work/_selftest/shot_000 に作ってレンダーする。GPU が効いているか・VRM が読めるか・座標系が合っているかの確認用。

  python tools/render_selftest.py --vrm chara.vrm
  → work/_selftest/shot_000/render/f0000.png .. f0003.png
     f0000: 正面 T ポーズ  f0001: 画面右を向く  f0002: 左腕（画面右側）が上がる  f0003: 両手の指を握る
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stages"))
from mocap_common import parents_for, finger_bones, FINGER_SUFFIXES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--size", default="640x480")
    a = ap.parse_args()
    W, H = (int(v) for v in a.size.split("x"))
    sd = ROOT / "work" / "_selftest" / "shot_000"
    sd.mkdir(parents=True, exist_ok=True)
    bones = ["hips", "spine", "chest", "neck", "head", "leftUpperArm", "leftLowerArm",
             "rightUpperArm", "rightLowerArm", "leftUpperLeg", "leftLowerLeg", "leftFoot",
             "rightUpperLeg", "rightLowerLeg", "rightFoot"]
    f = float(max(W, H))
    K = [[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]]
    s, c = math.sin(math.pi / 4), math.cos(math.pi / 4)
    ident = [0, 0, 0, 1]
    r2 = [ident] * len(bones)
    r2[bones.index("leftUpperArm")] = [0, 0, -s, c]
    r2[bones.index("leftLowerArm")] = [0, 0, -s, c]
    rot = [[ident] * len(bones), [[0, -s, 0, c]] * len(bones), r2]
    # frame3: 手（左右）+ 指: 親指以外を握る。指は小指側の軸 (+z, cv) まわりに掌側へ 60°/60°/40° 累積
    nb = len(bones)
    bones = bones + ["leftHand", "rightHand"] + finger_bones("left") + finger_bones("right")
    rot = [r + [ident] * (len(bones) - nb) for r in rot]
    r3 = [ident] * len(bones)
    for side, sign in (("left", 1), ("right", -1)):
        for suf in FINGER_SUFFIXES:
            if suf.startswith("Thumb"):
                continue
            seg = {"Proximal": 60, "Intermediate": 120, "Distal": 160}[re.sub(r"^(Index|Middle|Ring|Little)", "", suf)]
            ang = math.radians(sign * seg) / 2
            r3[bones.index(f"{side}{suf}")] = [0, 0, math.sin(ang), math.cos(ang)]
    rot.append(r3)
    F = 4
    m = {"backend": "selftest", "fps": 30, "width": W, "height": H, "n_frames": F, "K": K,
         "bones": bones, "parents": parents_for(bones), "rot": rot,
         "hips": [[0, 0.15, 3.0]] * F, "valid": [True] * F, "hip_height": 0.9,
         "fingers_valid": [[False, False]] * 3 + [[True, True]]}
    (sd / "mocap.json").write_text(json.dumps(m), encoding="utf-8")
    (sd / "meta.json").write_text(json.dumps({"start": 0, "end": F, "fps": 30, "width": W,
                                              "height": H, "n_frames": F}), encoding="utf-8")
    subprocess.run([sys.executable, ROOT / "stages" / "s4_render.py", "--work", sd.parent,
                    "--vrm", a.vrm, "--no-skip"], check=True)
    print("ok:", sd / "render")


if __name__ == "__main__":
    main()
