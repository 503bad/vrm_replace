"""GUI に出すファイル情報（動画の解像度・長さ・サムネイル、VRM のバージョン・メタ情報・利用条件）"""
import base64
import json
import struct
from pathlib import Path

import cv2

VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
VRM_EXT = {".vrm"}


def classify(path):
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in VRM_EXT:
        return "vrm"
    return None


def _data_url(img, max_w=480, quality=85):
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else None


def image_data_url(path, max_w=1600):
    img = cv2.imread(str(path))
    return _data_url(img, max_w, 90) if img is not None else None


def video_info(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("動画を開けません")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, min(max(n // 10, 0), 30))
    ok, frame = cap.read()
    cap.release()
    return {
        "path": str(path), "name": Path(path).name, "width": w, "height": h, "fps": round(fps, 3), "frames": n,
        "duration": n / fps if fps else 0.0, "size": Path(path).stat().st_size,
        "thumb": _data_url(frame) if ok else None,
    }


def _read_glb(path):
    """GLB の JSON と、BIN チャンクの開始位置を返す（BIN 全体は読まない）"""
    with open(path, "rb") as f:
        magic, _ver, _length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise ValueError("VRM（glb）ではありません")
        jlen, jtype = struct.unpack("<II", f.read(8))
        if jtype != 0x4E4F534A:
            raise ValueError("glTF の JSON チャンクがありません")
        js = json.loads(f.read(jlen))
        bin_start = None
        head = f.read(8)
        if len(head) == 8:
            _blen, btype = struct.unpack("<II", head)
            if btype == 0x004E4942:
                bin_start = f.tell()
    return js, bin_start


def _image(path, js, bin_start, index):
    try:
        img = js["images"][index]
        bv = js["bufferViews"][img["bufferView"]]
        with open(path, "rb") as f:
            f.seek(bin_start + bv.get("byteOffset", 0))
            data = f.read(bv["byteLength"])
        return f"data:{img.get('mimeType', 'image/png')};base64," + base64.b64encode(data).decode()
    except Exception:
        return None


# 利用条件の表記（VRM 0.x / 1.0 で値が違うのを日本語にそろえる）
_COMMERCIAL0 = {"Allow": ("可", True), "Disallow": ("不可", False)}
_COMMERCIAL1 = {"personalNonProfit": ("不可（個人の非営利のみ）", False), "personalProfit": ("個人のみ可", True),
                "corporation": ("可（法人も可）", True)}
_USER0 = {"OnlyAuthor": ("作者のみ", False), "ExplicitlyLicensedPerson": ("許可された人のみ", False),
          "Everyone": ("誰でも", True)}
_USER1 = {"onlyAuthor": ("作者のみ", False), "onlySeparatelyLicensedPerson": ("許可された人のみ", False),
          "everyone": ("誰でも", True)}


def vrm_info(path):
    js, bin_start = _read_glb(path)
    ext = js.get("extensions", {})
    info = {"path": str(path), "name": Path(path).name, "size": Path(path).stat().st_size, "thumb": None, "terms": []}
    if "VRMC_vrm" in ext:
        v = ext["VRMC_vrm"]
        m = v.get("meta", {})
        info.update(version="1.0", title=m.get("name") or "", author=", ".join(m.get("authors", []) or []),
                    model_version=m.get("version") or "")
        if bin_start is not None and m.get("thumbnailImage") is not None:
            info["thumb"] = _image(path, js, bin_start, m["thumbnailImage"])
        c = _COMMERCIAL1.get(m.get("commercialUsage"))
        if c:
            info["terms"].append({"label": "商用利用", "value": c[0], "ok": c[1]})
        u = _USER1.get(m.get("avatarPermission"))
        if u:
            info["terms"].append({"label": "アバターの利用", "value": u[0], "ok": u[1]})
        if m.get("licenseUrl"):
            info["terms"].append({"label": "ライセンス", "value": m["licenseUrl"], "ok": None})
    elif "VRM" in ext:
        v = ext["VRM"]
        m = v.get("meta", {})
        info.update(version="0.x", title=m.get("title") or "", author=m.get("author") or "",
                    model_version=m.get("version") or "")
        tex = m.get("texture")
        if bin_start is not None and tex is not None and tex >= 0:
            try:
                info["thumb"] = _image(path, js, bin_start, js["textures"][tex]["source"])
            except Exception:
                pass
        c = _COMMERCIAL0.get(m.get("commercialUssageName"))
        if c:
            info["terms"].append({"label": "商用利用", "value": c[0], "ok": c[1]})
        u = _USER0.get(m.get("allowedUserName"))
        if u:
            info["terms"].append({"label": "アバターの利用", "value": u[0], "ok": u[1]})
        if m.get("licenseName"):
            info["terms"].append({"label": "ライセンス", "value": m["licenseName"], "ok": None})
    else:
        raise ValueError("VRM の拡張（VRM / VRMC_vrm）がありません")
    return info


def frame_data_url(video, frac=0.3):
    """出力動画の途中の 1 フレーム（完了画面のプレビュー用）"""
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
    ok, frame = cap.read()
    cap.release()
    return _data_url(frame, 960) if ok else None
