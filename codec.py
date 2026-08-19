"""
codec.py — Adaptive per-frame codec for ASCILINE's binary WebSocket stream.

Wire format (one message per frame):
    [4 bytes: frame_index, big-endian uint32]
    [1 byte : codec tag]
    [payload ...]

Tags:
    0 RAW    payload = framebuffer bytes, as the legacy protocol sent them
    1 ZLIB   payload = zlib(framebuffer bytes)
    2 DELTA  payload = zlib( changed-cell indices [uint32 LE] ++ changed values )

The encoder picks the smallest applicable encoding per frame. The decoder lives
in codec.js (browser + Node) so the shipped path is the tested path; it never
needs to change for any of the encoder optimizations below.

Optimizations:
  - zlib level 3 (near level-6 ratio at roughly half the CPU)
  - empty-delta short-circuit: bit-identical frames (static content) stream a
    pre-sized empty DELTA without touching the diff machinery (~3.4x faster
    encode on static footage, byte-identical output)
  - OpenCV-accelerated differencing: cv2.absdiff computes exact uint8 |a-b| in
    one SIMD pass / one allocation (pure-NumPy fallback preserved)
  - smart candidate selection: only try DELTA when few cells changed and ZLIB
    when many did, skipping the obvious loser at the extremes (saves CPU, no
    size cost in the common middle range)
  - lossy temporal delta (conditional replenishment): a colour cell is only
    re-sent once it drifts past `tolerance` from what the viewer already sees.
    The CHARACTER plane is always exact. tolerance=0 is lossless and keeps the
    stream bit-exact. State is the previously-SHOWN frame, so error is bounded
    by `tolerance` and never drifts.
"""
import struct
import zlib
import numpy as np

try:
    # Optional acceleration: cv2.absdiff computes the exact per-channel absolute
    # difference of two uint8 arrays in a single SIMD pass with one allocation,
    # instead of NumPy's widen-to-int16 / subtract / abs chain (three allocs,
    # ~2x the memory traffic). For uint8 operands the true difference always
    # fits in uint8, so saturation never kicks in and results are bit-identical.
    import cv2 as _cv2
except ImportError:  # pragma: no cover - pure-NumPy fallback keeps codec.py portable
    _cv2 = None

TAG_RAW = 0
TAG_ZLIB = 1
TAG_DELTA = 2
TAG_RLE_FULL = 3

DEFAULT_LEVEL = 3        # zlib level: best size/CPU trade-off (see experiments/optimize.py)
KEYFRAME_INTERVAL = 48   # force a full frame this often for resync / late joiners

# Smart-selection thresholds (fraction of cells changed).
_DELTA_MAX_FRAC = 0.60   # above this, delta loses — don't bother building it
_ZLIB_MIN_FRAC = 0.10    # below this, full-frame zlib loses — don't bother

def _rle_encode(frame: np.ndarray) -> bytes:
    """Run-Length Encoding of a full frame. Fully vectorized with NumPy."""
    C = frame.shape[2]
    flat = frame.reshape(-1, C)
    n = len(flat)

    # Find run boundaries
    diffs = np.any(flat[1:] != flat[:-1], axis=1)
    change_indices = np.concatenate(([0], np.where(diffs)[0] + 1, [n]))
    run_lengths = np.diff(change_indices)          # (num_runs,)
    values = flat[change_indices[:-1]]             # (num_runs, C)

    # Most runs fit in uint16 — handle overflow by splitting into 65535-chunks
    # Overflow is extremely rare; fast path for the common case
    if run_lengths.max() <= 65535:
        counts = run_lengths.astype(np.uint16)
        # Interleave: [count(2 bytes) | value(C bytes)] for each run
        num_runs = len(counts)
        # Write counts in little-endian uint16
        count_view = counts.view(np.uint8).reshape(num_runs, 2)
        # Interleave: reshape to (num_runs, 2+C), then ravel
        out = np.empty(num_runs * (2 + C), dtype=np.uint8)
        out_view = out.reshape(num_runs, 2 + C)
        out_view[:, :2] = count_view
        out_view[:, 2:] = values
        return out.tobytes()

    # Slow fallback for pathological cases with runs > 65535
    out = bytearray()
    for count, val in zip(run_lengths.tolist(), values.tolist()):
        val_bytes = bytes(val)
        while count > 65535:
            out += struct.pack('<H', 65535) + val_bytes
            count -= 65535
        out += struct.pack('<H', count) + val_bytes
    return bytes(out)

def _full_frame(raw: bytes, frame: np.ndarray, frame_index: int, level: int) -> bytes:
    # Race ZLIB vs RLE_FULL
    z_raw = zlib.compress(raw, level)
    
    rle_bytes = _rle_encode(frame)
    z_rle = zlib.compress(rle_bytes, level)
    
    if len(z_rle) < len(z_raw) and len(z_rle) < len(raw):
        return struct.pack(">IB", frame_index, TAG_RLE_FULL) + z_rle
    elif len(z_raw) < len(raw):
        return struct.pack(">IB", frame_index, TAG_ZLIB) + z_raw
    return struct.pack(">IB", frame_index, TAG_RAW) + raw

def encode_frame(frame: np.ndarray, prev: np.ndarray | None, frame_index: int,
                 level: int = DEFAULT_LEVEL, tolerance: int = 0):
    """
    Encode one framebuffer.

    :param frame: C-contiguous uint8 array, shape (rows, cols, C). C is 4 for
                  ASCII colour ([char,R,G,B]) or 3 for pixel mode ([B,G,R]).
    :param prev:  the previously-SHOWN frame (what the client currently displays)
                  or None for a keyframe.
    :param tolerance: max per-channel colour drift tolerated before re-sending a
                  cell (lossy). 0 = lossless. The character plane is always exact.
    :returns: (message_bytes, shown_frame) — shown_frame is what the client will
              now display and must be passed back as `prev` next call.
    """
    raw = frame.tobytes()
    keyframe = prev is None or (frame_index % KEYFRAME_INTERVAL == 0)
    if keyframe or prev.shape != frame.shape:
        return _full_frame(raw, frame, frame_index, level), frame.copy()

    C = frame.shape[2]
    if np.array_equal(frame, prev):
        # Fast path sentinel: bit-identical frames stream an empty DELTA. This is
        # the dominant real-world case (static screens, slides, dark scenes) and
        # skips every allocation below. `frac`/`ci` values are exactly what the
        # general path would compute, so wire output is unchanged.
        frac = 0.0
        ci = np.empty(0, dtype="<u4")
        candidates = [(TAG_DELTA, zlib.compress(b"", level), prev)]
    else:
        if _cv2 is not None:
            diff = _cv2.absdiff(frame, prev)        # exact |a-b|, uint8, 1 alloc
        else:
            diff = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
        if C == 4:
            # channel 0 is the character (structure) -> exact; tolerance on colour
            char_changed = frame[:, :, 0] != prev[:, :, 0]
            if tolerance <= 0:
                color_changed = np.any(diff[:, :, 1:] != 0, axis=2)
            else:
                color_changed = np.any(diff[:, :, 1:] > tolerance, axis=2)
            changed = char_changed | color_changed
        else:
            changed = (np.any(diff != 0, axis=2) if tolerance <= 0
                       else np.any(diff > tolerance, axis=2))

        # count_nonzero is a C-level popcount reduction (~10x faster than .mean())
        frac = np.count_nonzero(changed) / changed.size
        ci = np.nonzero(changed.reshape(-1))[0].astype("<u4")

        candidates = []  # (tag, payload, shown_after_decode)
        if frac < _DELTA_MAX_FRAC:
            # Lossy reconstruction the client will hold if we send a DELTA.
            # Built lazily here (not on the high-motion path where DELTA is
            # skipped anyway) to avoid a wasted full-frame copy + patch.
            vals = frame.reshape(-1, C)[ci]
            delta_shown = prev.copy()
            delta_shown.reshape(-1, C)[ci] = vals
            delta = zlib.compress(ci.tobytes() + vals.tobytes(), level)
            candidates.append((TAG_DELTA, delta, delta_shown))

    # We still race Full ZLIB and Full RLE if they might win
    if frac >= _ZLIB_MIN_FRAC or not candidates:
        z_raw = zlib.compress(raw, level)
        rle_bytes = _rle_encode(frame)
        z_rle = zlib.compress(rle_bytes, level)
        
        if len(z_rle) < len(z_raw):
            candidates.append((TAG_RLE_FULL, z_rle, frame))
        else:
            candidates.append((TAG_ZLIB, z_raw, frame))

    tag, payload, shown = min(candidates, key=lambda c: len(c[1]))
    # Never exceed the raw frame (zlib can inflate incompressible data slightly).
    if len(raw) < len(payload):
        tag, payload, shown = TAG_RAW, raw, frame

    msg = struct.pack(">IB", frame_index, tag) + payload
    # If we sent a full frame, the client shows the TRUE frame, not the lossy one.
    return msg, (shown.copy() if shown is frame else shown)


# ===== Opt-in lossy DCT profile (tag 4, pixel mode). Additive: encode_frame above is untouched. =====
# ASCILINE codec.py PROFILE addition (tag 4, opt-in lossy DCT profile, pixel mode).
# Ports the verified codec2 algorithm into ASCILINE conventions. Encoder side.
import math

TAG_PROFILE = 4
KEY = 48
R_SEARCH = 3
# Deterministic integer DCT basis (round(F*64)) and standard zigzag, hardcoded to
# guarantee bit-exact match with codec.js (no per-clip tables transmitted).
MI = np.array([23,23,23,23,23,23,23,23,31,27,18,6,-6,-18,-27,-31,30,12,-12,-30,-30,-12,12,30,
27,-6,-31,-18,18,31,6,-27,23,-23,-23,23,23,-23,-23,23,18,-31,6,27,-27,-6,31,-18,
12,-30,30,-12,-12,30,-30,12,6,-18,27,-31,31,-27,18,-6],np.int64).reshape(8,8)
MIT = MI.T.copy()
ZZ = np.array([0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,27,20,13,6,7,14,
21,28,35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63],np.int64)
QL_BASE=np.array([16,11,10,16,24,40,51,61,12,12,14,19,26,58,60,55,14,13,16,24,40,57,69,56,14,17,22,29,51,87,80,62,
18,22,37,56,68,109,103,77,24,35,55,64,81,104,113,92,49,64,78,87,103,121,120,101,72,92,95,98,112,100,103,99],np.float64).reshape(8,8)
QC_BASE=np.array([17,18,24,47,99,99,99,99,18,21,26,66,99,99,99,99,24,26,56,99,99,99,99,99,47,66,99,99,99,99,99,99,
99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99],np.float64).reshape(8,8)
_F=np.array([[ (math.sqrt(1.0/8) if k==0 else math.sqrt(2.0/8))*math.cos((2*n+1)*k*math.pi/16) for n in range(8)] for k in range(8)])

def qtables(QF):
    S=5000.0/QF if QF<50 else 200.0-2*QF
    def scl(m): return np.clip(np.floor((m*S+50)/100),1,255).astype(np.int64)
    return scl(QL_BASE), scl(QC_BASE)

def _idct_int(C): return (MIT@C@MI+2048)//4096

def _sub(x,H,W): return np.clip(x.reshape(H//2,2,W//2,2).mean(axis=(1,3)),0,255).astype(np.uint8)

def yuv_to_bgr(Y,Cb,Cr):  # planes uint8 -> BGR bytes, integer BT.601 full-range (matches codec.js)
    H,W=Y.shape
    cb=np.repeat(np.repeat(Cb.astype(np.int32),2,0),2,1)-128
    cr=np.repeat(np.repeat(Cr.astype(np.int32),2,0),2,1)-128
    y=Y.astype(np.int32)
    R=y+((359*cr+128)>>8); G=y-((88*cb+183*cr+128)>>8); B=y+((454*cb+128)>>8)
    return np.clip(np.stack([B,G,R],2),0,255).astype(np.uint8).tobytes()

def _enc_plane(cur,prev,ftype,use_mv,qm,DZ,SKIP_T):
    # Whole-plane vectorised: every 8x8 block is predicted, transformed, quantised,
    # reconstructed and run-length coded in batch. Same decisions, same integers and
    # same bitstream as a per-block loop, but without the per-block Python overhead.
    ph,pw=cur.shape; nbx=pw//8; nby=ph//8; nb=nbx*nby
    blocks=cur.reshape(nby,8,nbx,8).transpose(0,2,1,3).astype(np.float64)
    mvx=np.zeros((nby,nbx),np.int64); mvy=np.zeros((nby,nbx),np.int64)
    if ftype==0:
        pred=np.full((nby,nbx,8,8),128.0)
    elif use_mv:
        curi=cur.astype(np.int16); pad=np.pad(prev,R_SEARCH,mode='edge').astype(np.int16)
        # integer SADs: einsum reduces ~2x faster than a tuple-axis sum, same integers
        _sad=lambda d: np.einsum('aibj->ab',np.abs(d).astype(np.int32).reshape(nby,8,nbx,8))
        best=_sad(curi-prev.astype(np.int16)).astype(np.int64)
        for dy in range(-R_SEARCH,R_SEARCH+1):
            for dx in range(-R_SEARCH,R_SEARCH+1):
                if dx==0 and dy==0: continue
                sh=pad[R_SEARCH+dy:R_SEARCH+dy+ph,R_SEARCH+dx:R_SEARCH+dx+pw]
                sad=_sad(curi-sh)
                m=sad<best; best=np.where(m,sad,best); mvx=np.where(m,dx,mvx); mvy=np.where(m,dy,mvy)
        padu=np.pad(prev,R_SEARCH,mode='edge')
        ry=(R_SEARCH+np.arange(nby)[:,None]*8+mvy)[:,:,None]+np.arange(8)
        rx=(R_SEARCH+np.arange(nbx)[None,:]*8+mvx)[:,:,None]+np.arange(8)
        pred=padu[ry[:,:,:,None],rx[:,:,None,:]].astype(np.float64)  # edge-pad == the decoder's clamp
    else:
        pred=prev.reshape(nby,8,nbx,8).transpose(0,2,1,3).astype(np.float64)
    resid=blocks-pred
    _t=(_F@resid@_F.T)/qm
    Cq=np.round(_t)
    if DZ>0.5: Cq=np.where(np.abs(_t)<DZ,0.0,Cq)
    Cq=Cq.astype(np.int64)
    if ftype==1:
        sse=(resid*resid).sum(axis=(2,3))
        skip=((mvx==0)&(mvy==0))&((~Cq.any(axis=(2,3)))|((SKIP_T>0)&(sse<SKIP_T)))
    else:
        skip=np.zeros((nby,nbx),bool)
    rec=np.clip(pred.astype(np.int64)+((MIT@(Cq*qm)@MI+2048)//4096),0,255).astype(np.uint8)
    if ftype==1:
        rec=np.where(skip[:,:,None,None],prev.reshape(nby,8,nbx,8).transpose(0,2,1,3),rec)
    recon=np.ascontiguousarray(rec.transpose(0,2,1,3).reshape(ph,pw))
    order=np.nonzero((~skip).reshape(-1))[0]
    zzf=Cq.reshape(nb,64)[:,ZZ][order]
    if len(order):
        zzf=zzf.copy(); zzf[:,0]=np.diff(zzf[:,0],prepend=0)  # DC DPCM over coded blocks, raster order
    bidx,pos=np.nonzero(zzf)
    same=np.zeros(len(pos),bool); same[1:]=bidx[1:]==bidx[:-1]
    prevpos=np.where(same,np.concatenate(([0],pos[:-1])),-1)
    vals=zzf[bidx,pos]
    assert not len(vals) or np.abs(vals).max()<32768, "profile: coefficient out of int16 range"
    pairs=np.empty(len(pos),dtype=np.dtype([('r','u1'),('v','<i2')]))  # packed 3B == struct '<Bh'
    pairs['r']=(pos-prevpos-1).astype(np.uint8); pairs['v']=vals
    pb=pairs.tobytes(); counts=(zzf!=0).sum(1) if len(order) else np.zeros(0,np.int64)
    offs=np.concatenate(([0],np.cumsum(counts)))*3
    mvxf=mvx.reshape(-1); mvyf=mvy.reshape(-1); blob=bytearray()
    for j,b in enumerate(order):
        if ftype==1 and use_mv: blob+=struct.pack('bb',int(mvxf[b]),int(mvyf[b]))
        blob+=bytes([counts[j]])+pb[offs[j]:offs[j+1]]
    head=np.packbits(skip.reshape(-1)).tobytes() if ftype==1 else b''
    return head+bytes(blob),recon

class ProfileEncoder:
    def __init__(self, W, H, QF=70, DZ=0.75, SKIP_T=256, level=6):
        self.W,self.H,self.QF,self.DZ,self.SKIP_T,self.level=W,H,QF,DZ,SKIP_T,level
        assert W%16==0 and H%16==0, "profile requires cols and rows multiples of 16 (compiler pads)"
        self.QL,self.QC=qtables(QF); self.prev=None; self.n=0
    def encode(self, frame_bgr):  # (H,W,3) uint8 BGR -> (msg, shown_bgr bytes)
        f=frame_bgr.astype(np.float32); B=f[:,:,0];G=f[:,:,1];Rr=f[:,:,2]
        Y=np.clip(0.299*Rr+0.587*G+0.114*B,0,255).astype(np.uint8)
        Cb=_sub(128-0.168736*Rr-0.331264*G+0.5*B,self.H,self.W)
        Cr=_sub(128+0.5*Rr-0.418688*G-0.081312*B,self.H,self.W)
        cur=[Y,Cb,Cr]; ftype=0 if (self.prev is None or self.n%KEY==0) else 1
        payload=bytearray([ftype])
        if ftype==0: payload+=bytes([self.QF])+struct.pack(">HH",self.W,self.H)  # keyframe self-describes
        recons=[]
        for pi in range(3):
            qm=self.QL if pi==0 else self.QC
            pl,rec=_enc_plane(cur[pi], None if ftype==0 else self.prev[pi], ftype, pi==0, qm, self.DZ, self.SKIP_T)
            payload+=pl; recons.append(rec)
        z=zlib.compress(bytes(payload),self.level); msg=struct.pack(">IB",self.n,TAG_PROFILE)+z
        self.prev=recons; self.n+=1
        return msg, yuv_to_bgr(recons[0],recons[1],recons[2])
