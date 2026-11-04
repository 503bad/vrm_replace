"""NVIDIA Optical Flow（GPU 内蔵のオプティカルフロー専用ハードウェア）を ctypes で呼ぶ。

- 実行部分 nvofapi64.dll は NVIDIA の GPU ドライバーに同梱されている（SDK は不要。ダウンロード・同梱しない）
- 関数・構造体の定義は NVIDIA が公開しているヘッダー（NVIDIA/NVIDIAOpticalFlowSDK: nvOpticalFlowCommon.h /
  nvOpticalFlowCuda.h、API 2.0、BSD-3-Clause, Copyright (c) 2020 NVIDIA CORPORATION）に合わせて書いたもの
- 学習データを使わない（ハードウェアの動き推定）。Turing 以降の GPU が必要
- バッファは PyTorch と同じ CUDA コンテキスト（primary context）に作り、CUDA のコピーで受け渡す
"""
import ctypes as C
import sys

import torch

NVOF_API_VERSION = (2 << 4) | 0          # ヘッダーの NV_OF_API_VERSION（構造体の形はこの版に合わせてある）

# enums（nvOpticalFlowCommon.h）
NV_OF_SUCCESS = 0
NV_OF_CAPS_SUPPORTED_OUTPUT_GRID_SIZES = 0
NV_OF_PERF_LEVEL = {"slow": 5, "medium": 10, "fast": 20}
NV_OF_MODE_OPTICALFLOW = 1
NV_OF_BUFFER_USAGE_INPUT, NV_OF_BUFFER_USAGE_OUTPUT = 1, 2
NV_OF_BUFFER_FORMAT_GRAYSCALE8, NV_OF_BUFFER_FORMAT_SHORT2 = 1, 5
NV_OF_CUDA_BUFFER_TYPE_CUDEVICEPTR = 2
CU_MEMORYTYPE_DEVICE = 2

STATUS = {0: "SUCCESS", 1: "ERR_OF_NOT_AVAILABLE", 2: "ERR_UNSUPPORTED_DEVICE", 3: "ERR_DEVICE_DOES_NOT_EXIST",
          4: "ERR_INVALID_PTR", 5: "ERR_INVALID_PARAM", 6: "ERR_INVALID_CALL", 7: "ERR_INVALID_VERSION",
          8: "ERR_OUT_OF_MEMORY", 9: "ERR_NOT_INITIALIZED", 10: "ERR_UNSUPPORTED_FEATURE", 11: "ERR_GENERIC"}


class NV_OF_INIT_PARAMS(C.Structure):
    _fields_ = [("width", C.c_uint32), ("height", C.c_uint32), ("outGridSize", C.c_int), ("hintGridSize", C.c_int),
                ("mode", C.c_int), ("perfLevel", C.c_int), ("enableExternalHints", C.c_int),
                ("enableOutputCost", C.c_int), ("hPrivData", C.c_void_p), ("disparityRange", C.c_int),
                ("enableRoi", C.c_int)]


class NV_OF_BUFFER_DESCRIPTOR(C.Structure):
    _fields_ = [("width", C.c_uint32), ("height", C.c_uint32), ("bufferUsage", C.c_int), ("bufferFormat", C.c_int)]


class NV_OF_EXECUTE_INPUT_PARAMS(C.Structure):
    _fields_ = [("inputFrame", C.c_void_p), ("referenceFrame", C.c_void_p), ("externalHints", C.c_void_p),
                ("disableTemporalHints", C.c_int), ("padding", C.c_uint32), ("hPrivData", C.c_void_p),
                ("padding2", C.c_uint32), ("numRois", C.c_uint32), ("roiData", C.c_void_p)]


class NV_OF_EXECUTE_OUTPUT_PARAMS(C.Structure):
    _fields_ = [("outputBuffer", C.c_void_p), ("outputCostBuffer", C.c_void_p), ("hPrivData", C.c_void_p)]


class NV_OF_BUFFER_STRIDE(C.Structure):
    _fields_ = [("strideXInBytes", C.c_uint32), ("strideYInBytes", C.c_uint32)]


class NV_OF_CUDA_BUFFER_STRIDE_INFO(C.Structure):
    _fields_ = [("strideInfo", NV_OF_BUFFER_STRIDE * 3), ("numPlanes", C.c_uint32)]


_FN = C.WINFUNCTYPE if sys.platform == "win32" else C.CFUNCTYPE   # NVOFAPI = __stdcall（x64 では同じ）
_ST = C.c_int


class NV_OF_CUDA_API_FUNCTION_LIST(C.Structure):
    _fields_ = [
        ("nvCreateOpticalFlowCuda", _FN(_ST, C.c_void_p, C.POINTER(C.c_void_p))),
        ("nvOFInit", _FN(_ST, C.c_void_p, C.POINTER(NV_OF_INIT_PARAMS))),
        ("nvOFCreateGPUBufferCuda", _FN(_ST, C.c_void_p, C.POINTER(NV_OF_BUFFER_DESCRIPTOR), C.c_int,
                                         C.POINTER(C.c_void_p))),
        ("nvOFGPUBufferGetCUarray", _FN(C.c_void_p, C.c_void_p)),
        ("nvOFGPUBufferGetCUdeviceptr", _FN(C.c_uint64, C.c_void_p)),
        ("nvOFGPUBufferGetStrideInfo", _FN(_ST, C.c_void_p, C.POINTER(NV_OF_CUDA_BUFFER_STRIDE_INFO))),
        ("nvOFSetIOCudaStreams", _FN(_ST, C.c_void_p, C.c_void_p, C.c_void_p)),
        ("nvOFExecute", _FN(_ST, C.c_void_p, C.POINTER(NV_OF_EXECUTE_INPUT_PARAMS),
                            C.POINTER(NV_OF_EXECUTE_OUTPUT_PARAMS))),
        ("nvOFDestroyGPUBufferCuda", _FN(_ST, C.c_void_p)),
        ("nvOFDestroy", _FN(_ST, C.c_void_p)),
        ("nvOFGetLastError", _FN(_ST, C.c_void_p, C.c_char_p, C.POINTER(C.c_uint32))),
        ("nvOFGetCaps", _FN(_ST, C.c_void_p, C.c_int, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32))),
    ]


class CUDA_MEMCPY2D(C.Structure):
    _fields_ = [("srcXInBytes", C.c_size_t), ("srcY", C.c_size_t), ("srcMemoryType", C.c_int),
                ("srcHost", C.c_void_p), ("srcDevice", C.c_uint64), ("srcArray", C.c_void_p), ("srcPitch", C.c_size_t),
                ("dstXInBytes", C.c_size_t), ("dstY", C.c_size_t), ("dstMemoryType", C.c_int),
                ("dstHost", C.c_void_p), ("dstDevice", C.c_uint64), ("dstArray", C.c_void_p), ("dstPitch", C.c_size_t),
                ("WidthInBytes", C.c_size_t), ("Height", C.c_size_t)]


class NvOFError(RuntimeError):
    pass


def available():
    """NVIDIA Optical Flow が使えるか（ドライバーに nvofapi64.dll があり、GPU が対応しているか）と理由"""
    try:
        f = NvOF(64, 64, "fast", 4)
        f.close()
        return True, ""
    except Exception as e:
        return False, str(e)


class NvOF:
    def __init__(self, width, height, perf="slow", grid=1):
        if not torch.cuda.is_available():
            raise NvOFError("CUDA が使えません")
        try:
            self._dll = C.WinDLL("nvofapi64.dll") if sys.platform == "win32" else C.CDLL("libnvidia-opticalflow.so.1")
            self._cu = C.WinDLL("nvcuda.dll") if sys.platform == "win32" else C.CDLL("libcuda.so.1")
        except OSError as e:
            raise NvOFError(f"NVIDIA Optical Flow のライブラリがありません（NVIDIA のドライバーが必要）: {e}")
        self.W, self.H = int(width), int(height)
        self.fn = NV_OF_CUDA_API_FUNCTION_LIST()
        create = self._dll.NvOFAPICreateInstanceCuda
        create.restype = _ST
        self._check(create(C.c_uint32(NVOF_API_VERSION), C.byref(self.fn)), "NvOFAPICreateInstanceCuda")
        torch.zeros(1, device="cuda")                        # PyTorch に primary context を作らせ、それを使う
        ctx = C.c_void_p()
        self._cu.cuCtxGetCurrent.restype = C.c_int
        if self._cu.cuCtxGetCurrent(C.byref(ctx)) != 0 or not ctx.value:
            raise NvOFError("CUDA コンテキストを取得できません")
        self.h = C.c_void_p()
        self._check(self.fn.nvCreateOpticalFlowCuda(ctx, C.byref(self.h)), "nvCreateOpticalFlowCuda")
        self.grid = self._pick_grid(int(grid))
        ip = NV_OF_INIT_PARAMS(width=self.W, height=self.H, outGridSize=self.grid, hintGridSize=0,
                               mode=NV_OF_MODE_OPTICALFLOW, perfLevel=NV_OF_PERF_LEVEL[perf])
        self._check(self.fn.nvOFInit(self.h, C.byref(ip)), "nvOFInit")
        self.ow, self.oh = -(-self.W // self.grid), -(-self.H // self.grid)
        self.bufs = []
        self.inp = [self._buffer(self.W, self.H, NV_OF_BUFFER_USAGE_INPUT, NV_OF_BUFFER_FORMAT_GRAYSCALE8)
                    for _ in range(2)]
        self.out = self._buffer(self.ow, self.oh, NV_OF_BUFFER_USAGE_OUTPUT, NV_OF_BUFFER_FORMAT_SHORT2)
        self._cu.cuMemcpy2D_v2.restype = C.c_int
        self._cu.cuCtxSynchronize.restype = C.c_int

    def _check(self, st, what):
        if st != NV_OF_SUCCESS:
            msg = ""
            if getattr(self, "h", None) and self.h.value:
                buf = C.create_string_buffer(256)
                n = C.c_uint32(256)
                try:
                    self.fn.nvOFGetLastError(self.h, buf, C.byref(n))
                    msg = buf.value.decode(errors="replace")
                except Exception:
                    pass
            raise NvOFError(f"{what} failed: {STATUS.get(st, st)} {msg}".strip())

    def _pick_grid(self, want):
        n = C.c_uint32(0)
        self._check(self.fn.nvOFGetCaps(self.h, NV_OF_CAPS_SUPPORTED_OUTPUT_GRID_SIZES, None, C.byref(n)), "nvOFGetCaps")
        vals = (C.c_uint32 * max(1, n.value))()
        self._check(self.fn.nvOFGetCaps(self.h, NV_OF_CAPS_SUPPORTED_OUTPUT_GRID_SIZES, vals, C.byref(n)), "nvOFGetCaps")
        sup = sorted(v for v in vals[:n.value] if v)
        if not sup:
            raise NvOFError("出力グリッドサイズの情報が取れません")
        ok = [g for g in sup if g >= want]
        return ok[0] if ok else sup[-1]

    def _buffer(self, w, h, usage, fmt):
        d = NV_OF_BUFFER_DESCRIPTOR(width=w, height=h, bufferUsage=usage, bufferFormat=fmt)
        hb = C.c_void_p()
        self._check(self.fn.nvOFCreateGPUBufferCuda(self.h, C.byref(d), NV_OF_CUDA_BUFFER_TYPE_CUDEVICEPTR, C.byref(hb)),
                    "nvOFCreateGPUBufferCuda")
        si = NV_OF_CUDA_BUFFER_STRIDE_INFO()
        self._check(self.fn.nvOFGPUBufferGetStrideInfo(hb, C.byref(si)), "nvOFGPUBufferGetStrideInfo")
        ptr = self.fn.nvOFGPUBufferGetCUdeviceptr(hb)
        self.bufs.append(hb)
        return {"h": hb, "ptr": ptr, "pitch": si.strideInfo[0].strideXInBytes}

    def _copy(self, src_ptr, src_pitch, dst_ptr, dst_pitch, width_bytes, rows):
        m = CUDA_MEMCPY2D(srcMemoryType=CU_MEMORYTYPE_DEVICE, srcDevice=src_ptr, srcPitch=src_pitch,
                          dstMemoryType=CU_MEMORYTYPE_DEVICE, dstDevice=dst_ptr, dstPitch=dst_pitch,
                          WidthInBytes=width_bytes, Height=rows)
        st = self._cu.cuMemcpy2D_v2(C.byref(m))
        if st != 0:
            raise NvOFError(f"cuMemcpy2D failed: {st}")

    @torch.inference_mode()
    def flow(self, a, b):
        """a, b: (H,W) uint8 の CUDA テンソル（グレースケール）→ a の各画素が b のどこへ動いたか (2,H,W) float32 [px]"""
        torch.cuda.synchronize()
        for buf, img in zip(self.inp, (a, b)):
            img = img.contiguous()
            self._copy(img.data_ptr(), self.W, buf["ptr"], buf["pitch"], self.W, self.H)
        ip = NV_OF_EXECUTE_INPUT_PARAMS(inputFrame=self.inp[0]["h"], referenceFrame=self.inp[1]["h"],
                                        disableTemporalHints=1)
        op = NV_OF_EXECUTE_OUTPUT_PARAMS(outputBuffer=self.out["h"])
        self._check(self.fn.nvOFExecute(self.h, C.byref(ip), C.byref(op)), "nvOFExecute")
        if self._cu.cuCtxSynchronize() != 0:
            raise NvOFError("cuCtxSynchronize failed")
        out = torch.empty((self.oh, self.ow, 2), dtype=torch.int16, device="cuda")
        self._copy(self.out["ptr"], self.out["pitch"], out.data_ptr(), self.ow * 4, self.ow * 4, self.oh)
        f = out.permute(2, 0, 1).float() / 32.0                          # S10.5 固定小数点 → px
        if self.grid > 1:
            f = torch.nn.functional.interpolate(f[None], size=(self.oh * self.grid, self.ow * self.grid),
                                                mode="bilinear", align_corners=False)[0, :, :self.H, :self.W]
        return f

    def close(self):
        for hb in getattr(self, "bufs", []):
            try:
                self.fn.nvOFDestroyGPUBufferCuda(hb)
            except Exception:
                pass
        self.bufs = []
        if getattr(self, "h", None) and self.h.value:
            try:
                self.fn.nvOFDestroy(self.h)
            except Exception:
                pass
            self.h = C.c_void_p()
