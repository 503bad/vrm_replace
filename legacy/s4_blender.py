"""
Blender 内で実行する。VRM を読み込み、mocap.npz の SMPL モーションを Humanoid ボーンに適用し、
カメラ座標系でカメラを固定 → 透過 PNG 連番を render/ に出力。

  blender -b --python s4_blender.py -- --work <dir> --vrm <file> [--engine E] [--samples N]

依存: VRM Add-on for Blender (saturday06)。yaml 等は使わない（Blender 同梱 Python だけで動く）。
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

# ---- 調整パラメータ ------------------------------------------------------
PERSON_HIP_HEIGHT = 0.93   # SMPL 標準体型の腰の高さ[m]。VRM をこれに合わせてスケールする
ARM_OFFSET_DEG = 0.0       # 腕が上がりすぎ/下がりすぎなら ±5〜15 で調整
SUN_ROTATION = (math.radians(50), 0.0, math.radians(30))
SUN_STRENGTH = 3.0
WORLD_STRENGTH = 0.6
# -------------------------------------------------------------------------

# SMPL 22 関節: index → (parent, VRM humanoid bone name)
SMPL = {
    0: (-1, "hips"), 1: (0, "leftUpperLeg"), 2: (0, "rightUpperLeg"), 3: (0, "spine"),
    4: (1, "leftLowerLeg"), 5: (2, "rightLowerLeg"), 6: (3, "chest"),
    7: (4, "leftFoot"), 8: (5, "rightFoot"), 9: (6, "upperChest"),
    10: (7, "leftToes"), 11: (8, "rightToes"), 12: (9, "neck"),
    13: (9, "leftShoulder"), 14: (9, "rightShoulder"), 15: (12, "head"),
    16: (13, "leftUpperArm"), 17: (14, "rightUpperArm"),
    18: (16, "leftLowerArm"), 19: (17, "rightLowerArm"),
    20: (18, "leftHand"), 21: (19, "rightHand"),
}
# OpenCV カメラ座標 (x右 y下 z前) → Blender (カメラは原点で +Y を向く)
C = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0)))
C_inv = C.transposed()


def aa_to_mat(v):
    v = Vector(v)
    ang = v.length
    if ang < 1e-8:
        return Matrix.Identity(3)
    return Matrix.Rotation(ang, 3, v / ang)


def humanoid_map(arm):
    """VRM humanoid 名 → 実ボーン名。VRM 1.0 / 0.x 両対応。"""
    ext = arm.data.vrm_addon_extension
    m = {}
    if ext.spec_version.startswith("1"):
        hb = ext.vrm1.humanoid.human_bones
        for name in SMPL_NAMES:
            prop = getattr(hb, snake(name), None)
            if prop and prop.node.bone_name:
                m[name] = prop.node.bone_name
    else:
        for hb in ext.vrm0.humanoid.human_bones:
            if hb.node.bone_name:
                m[hb.bone] = hb.node.bone_name
    return m


def snake(s):
    return "".join("_" + c.lower() if c.isupper() else c for c in s)


SMPL_NAMES = [v[1] for v in SMPL.values()]


def nearest_present_ancestor(j, present):
    p = SMPL[j][0]
    while p >= 0 and p not in present:
        p = SMPL[p][0]
    return p


def setup_camera(scene, K, w, h):
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 0)
    cam.rotation_euler = (math.pi / 2, 0, 0)
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = 36.0
    cam_data.lens = float(K[0, 0]) * 36.0 / w
    cam_data.shift_x = (float(K[0, 2]) - w / 2) / w
    cam_data.shift_y = (h / 2 - float(K[1, 2])) / w
    cam_data.clip_start = 0.05
    scene.camera = cam


def setup_light(scene):
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = SUN_STRENGTH
    o = bpy.data.objects.new("sun", sun)
    o.rotation_euler = SUN_ROTATION
    scene.collection.objects.link(o)
    scene.world = scene.world or bpy.data.worlds.new("w")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes["Background"]
    bg.inputs[1].default_value = WORLD_STRENGTH


def apply_motion(arm, bone_map, pose, transl, scale):
    """pose (F,22,3), transl (F,3) をアーマチュアにキーフレームとして焼き込む。"""
    present = {j for j, (_, n) in SMPL.items() if n in bone_map}
    rest = {j: arm.data.bones[bone_map[SMPL[j][1]]].matrix_local.to_3x3() for j in present}
    hips_head = arm.data.bones[bone_map["hips"]].head_local.copy()

    arm.animation_data_clear()
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)

    off_l = Matrix.Rotation(math.radians(ARM_OFFSET_DEG), 3, "Z")
    off_r = Matrix.Rotation(-math.radians(ARM_OFFSET_DEG), 3, "Z")

    for f in range(len(pose)):
        # SMPL ローカル回転 → FK でグローバル回転
        L = [aa_to_mat(pose[f, j]) for j in range(22)]
        L[16] = L[16] @ off_l
        L[17] = L[17] @ off_r
        G = [None] * 22
        for j in range(22):
            p = SMPL[j][0]
            G[j] = L[j] if p < 0 else G[p] @ L[j]

        for j in present:
            pb = arm.pose.bones[bone_map[SMPL[j][1]]]
            anc = nearest_present_ancestor(j, present)
            D = G[j] if anc < 0 else G[anc].transposed() @ G[j]
            R = rest[j].transposed() @ (C @ D @ C_inv) @ rest[j]
            pb.rotation_quaternion = R.to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=f)

        hips = arm.pose.bones[bone_map["hips"]]
        target = (C @ Vector(transl[f])) / scale
        hips.location = rest[0].transposed() @ (target - hips_head)
        hips.keyframe_insert("location", frame=f)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--engine", default="BLENDER_EEVEE_NEXT")
    ap.add_argument("--samples", type=int, default=16)
    a = ap.parse_args(argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.vrm(filepath=a.vrm)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    bone_map = humanoid_map(arm)
    missing = [n for n in ("hips", "spine", "head", "leftUpperArm", "leftUpperLeg") if n not in bone_map]
    if missing:
        raise SystemExit(f"VRM humanoid bones missing: {missing}")

    hip_z = arm.data.bones[bone_map["hips"]].head_local.z
    scale = PERSON_HIP_HEIGHT / hip_z if hip_z > 0.05 else 1.0
    arm.scale = (scale, scale, scale)
    for o in bpy.data.objects:
        if o.parent is None and o is not arm and o.type == "MESH":
            o.scale = (scale, scale, scale)

    scene = bpy.context.scene
    setup_light(scene)
    scene.render.engine = a.engine
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = a.samples
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    for sd in sorted(Path(a.work).glob("shot_*")):
        if (sd / ".done_4").exists():
            continue
        meta = json.loads((sd / "meta.json").read_text(encoding="utf-8"))
        d = np.load(sd / "mocap.npz")
        w, h = meta["width"], meta["height"]

        if scene.camera:
            bpy.data.objects.remove(scene.camera)
        setup_camera(scene, d["K"], w, h)
        scene.render.resolution_x, scene.render.resolution_y = w, h
        scene.render.fps = int(round(meta["fps"]))
        scene.render.fps_base = scene.render.fps / meta["fps"]

        apply_motion(arm, bone_map, d["pose"], d["transl"], scale)

        scene.frame_start, scene.frame_end = 0, len(d["pose"]) - 1
        (sd / "render").mkdir(exist_ok=True)
        scene.render.filepath = str(sd / "render" / "f")   # → f0000.png, f0001.png ...
        bpy.ops.render.render(animation=True)
        (sd / ".done_4").touch()
        print(f"  {sd.name}: rendered {len(d['pose'])} frames")


if __name__ == "__main__":
    main()
