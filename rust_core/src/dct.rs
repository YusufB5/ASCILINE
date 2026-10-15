//! Tag-4 profile encoder. Reconstructed planes must match codec.js exactly.
use numpy::{PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::io::Write;

pub const MI: [i64; 64] = [
    23, 23, 23, 23, 23, 23, 23, 23, 31, 27, 18, 6, -6, -18, -27, -31, 30, 12, -12, -30, -30, -12,
    12, 30, 27, -6, -31, -18, 18, 31, 6, -27, 23, -23, -23, 23, 23, -23, -23, 23, 18, -31, 6, 27,
    -27, -6, 31, -18, 12, -30, 30, -12, -12, 30, -30, 12, 6, -18, 27, -31, 31, -27, 18, -6,
];
pub const F_MAT: [f64; 64] = [
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.3535533905932738,
    0.4903926402016152,
    0.4157348061512726,
    0.2777851165098011,
    0.0975451610080642,
    -0.0975451610080641,
    -0.2777851165098010,
    -0.4157348061512727,
    -0.4903926402016152,
    0.4619397662556434,
    0.1913417161825449,
    -0.1913417161825449,
    -0.4619397662556434,
    -0.4619397662556434,
    -0.1913417161825452,
    0.1913417161825450,
    0.4619397662556433,
    0.4157348061512726,
    -0.0975451610080641,
    -0.4903926402016152,
    -0.2777851165098011,
    0.2777851165098009,
    0.4903926402016152,
    0.0975451610080644,
    -0.4157348061512726,
    0.3535533905932738,
    -0.3535533905932737,
    -0.3535533905932738,
    0.3535533905932737,
    0.3535533905932738,
    -0.3535533905932733,
    -0.3535533905932736,
    0.3535533905932733,
    0.2777851165098011,
    -0.4903926402016152,
    0.0975451610080642,
    0.4157348061512728,
    -0.4157348061512726,
    -0.0975451610080640,
    0.4903926402016153,
    -0.2777851165098008,
    0.1913417161825449,
    -0.4619397662556434,
    0.4619397662556433,
    -0.1913417161825449,
    -0.1913417161825453,
    0.4619397662556434,
    -0.4619397662556432,
    0.1913417161825448,
    0.0975451610080642,
    -0.2777851165098011,
    0.4157348061512728,
    -0.4903926402016153,
    0.4903926402016153,
    -0.4157348061512725,
    0.2777851165098008,
    -0.0975451610080643,
];
pub const ZZ: [usize; 64] = [
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20,
    13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59,
    52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
];
pub const QL_BASE: [f64; 64] = [
    16.0, 11.0, 10.0, 16.0, 24.0, 40.0, 51.0, 61.0, 12.0, 12.0, 14.0, 19.0, 26.0, 58.0, 60.0, 55.0,
    14.0, 13.0, 16.0, 24.0, 40.0, 57.0, 69.0, 56.0, 14.0, 17.0, 22.0, 29.0, 51.0, 87.0, 80.0, 62.0,
    18.0, 22.0, 37.0, 56.0, 68.0, 109.0, 103.0, 77.0, 24.0, 35.0, 55.0, 64.0, 81.0, 104.0, 113.0,
    92.0, 49.0, 64.0, 78.0, 87.0, 103.0, 121.0, 120.0, 101.0, 72.0, 92.0, 95.0, 98.0, 112.0, 100.0,
    103.0, 99.0,
];
pub const QC_BASE: [f64; 64] = [
    17.0, 18.0, 24.0, 47.0, 99.0, 99.0, 99.0, 99.0, 18.0, 21.0, 26.0, 66.0, 99.0, 99.0, 99.0, 99.0,
    24.0, 26.0, 56.0, 99.0, 99.0, 99.0, 99.0, 99.0, 47.0, 66.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0,
    99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0,
    99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0,
];

// Math helpers
fn qtables(qf: u32) -> ([i64; 64], [i64; 64]) {
    let s = if qf < 50 {
        5000.0 / qf as f64
    } else {
        200.0 - 2.0 * qf as f64
    };
    let mut ql = [0i64; 64];
    let mut qc = [0i64; 64];
    for i in 0..64 {
        let l = ((QL_BASE[i] * s + 50.0) / 100.0).floor() as i64;
        ql[i] = l.clamp(1, 255);
        let c = ((QC_BASE[i] * s + 50.0) / 100.0).floor() as i64;
        qc[i] = c.clamp(1, 255);
    }
    (ql, qc)
}

#[pyclass(name = "ProfileEncoder")]
pub struct RustProfileEncoder {
    w: usize,
    h: usize,
    qf: u32,
    dz: f64,
    skip_t: i32,
    level: u32,
    ql: [i64; 64],
    qc: [i64; 64],
    n: u32,
    prev_y: Vec<u8>,
    prev_cb: Vec<u8>,
    prev_cr: Vec<u8>,
}

#[pymethods]
impl RustProfileEncoder {
    #[new]
    #[pyo3(signature = (w, h, qf=70, dz=0.75, skip_t=256, level=6))]
    fn new(w: usize, h: usize, qf: u32, dz: f64, skip_t: i32, level: u32) -> PyResult<Self> {
        if w == 0
            || h == 0
            || w > 16384
            || h > 16384
            || w * h > 16_777_216
            || w % 16 != 0
            || h % 16 != 0
        {
            return Err(PyValueError::new_err("DCT dimensions must be positive multiples of 16, at most 16384 per axis and 16777216 pixels"));
        }
        if !(1..=100).contains(&qf) || !dz.is_finite() || dz < 0.0 || skip_t < 0 || level > 9 {
            return Err(PyValueError::new_err(
                "DCT requires qf 1..100, finite dz >= 0, skip_t >= 0 and level 0..9",
            ));
        }
        let (ql, qc) = qtables(qf);
        Ok(Self {
            w,
            h,
            qf,
            dz,
            skip_t,
            level,
            ql,
            qc,
            n: 0,
            prev_y: Vec::new(),
            prev_cb: Vec::new(),
            prev_cr: Vec::new(),
        })
    }

    fn encode(&mut self, py: Python, bgr: PyReadonlyArray3<u8>) -> PyResult<(PyObject, PyObject)> {
        if bgr.shape() != [self.h, self.w, 3] {
            return Err(PyValueError::new_err(
                "BGR array shape must match the encoder's (height, width, 3)",
            ));
        }
        let owned = bgr.as_slice()?.to_vec();
        drop(bgr);
        let (msg, shown) = py
            .allow_threads(|| self.encode_owned(&owned))
            .map_err(PyRuntimeError::new_err)?;
        Ok((
            PyBytes::new_bound(py, &msg).into(),
            PyBytes::new_bound(py, &shown).into(),
        ))
    }

    fn reset(&mut self) {
        self.n = 0;
        self.prev_y.clear();
        self.prev_cb.clear();
        self.prev_cr.clear();
    }
}

impl RustProfileEncoder {
    fn encode_owned(&mut self, bgr_slice: &[u8]) -> Result<(Vec<u8>, Vec<u8>), String> {
        let (y, cb, cr) = bgr_to_yuv(bgr_slice, self.w, self.h);

        let ftype = if self.prev_y.is_empty() || self.n % 48 == 0 {
            0
        } else {
            1
        };
        let mut payload = vec![ftype];

        if ftype == 0 {
            payload.push(self.qf as u8);
            payload.extend_from_slice(&(self.w as u16).to_be_bytes());
            payload.extend_from_slice(&(self.h as u16).to_be_bytes());
        }

        let (y_pl, rec_y) = enc_plane(
            &y,
            if ftype == 0 { None } else { Some(&self.prev_y) },
            self.w,
            self.h,
            ftype,
            true,
            &self.ql,
            self.dz,
            self.skip_t,
        );
        payload.extend(y_pl);

        let (cb_pl, rec_cb) = enc_plane(
            &cb,
            if ftype == 0 {
                None
            } else {
                Some(&self.prev_cb)
            },
            self.w / 2,
            self.h / 2,
            ftype,
            false,
            &self.qc,
            self.dz,
            self.skip_t,
        );
        payload.extend(cb_pl);

        let (cr_pl, rec_cr) = enc_plane(
            &cr,
            if ftype == 0 {
                None
            } else {
                Some(&self.prev_cr)
            },
            self.w / 2,
            self.h / 2,
            ftype,
            false,
            &self.qc,
            self.dz,
            self.skip_t,
        );
        payload.extend(cr_pl);

        let mut e =
            flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(self.level));
        e.write_all(&payload).map_err(|err| err.to_string())?;
        let z = e.finish().map_err(|err| err.to_string())?;

        let mut msg = Vec::with_capacity(5 + z.len());
        msg.extend_from_slice(&self.n.to_be_bytes());
        msg.push(4); // TAG_PROFILE = 4
        msg.extend(z);

        self.prev_y = rec_y;
        self.prev_cb = rec_cb;
        self.prev_cr = rec_cr;
        self.n = self.n.wrapping_add(1);

        let bgr_rec = yuv_to_bgr(&self.prev_y, &self.prev_cb, &self.prev_cr, self.w, self.h);

        Ok((msg, bgr_rec))
    }
}

#[inline]
fn dct_8x8(resid: &[f64; 64], qm: &[i64; 64], dz: f64) -> [i64; 64] {
    let mut t1 = [0.0; 64];
    for i in 0..8 {
        for j in 0..8 {
            let mut sum = 0.0;
            for k in 0..8 {
                sum += F_MAT[i * 8 + k] * resid[k * 8 + j];
            }
            t1[i * 8 + j] = sum;
        }
    }
    let mut cq = [0i64; 64];
    for i in 0..8 {
        for j in 0..8 {
            let mut sum = 0.0;
            for k in 0..8 {
                sum += t1[i * 8 + k] * F_MAT[j * 8 + k];
            }
            let val = sum / (qm[i * 8 + j] as f64);
            if val.abs() < dz {
                cq[i * 8 + j] = 0;
            } else {
                cq[i * 8 + j] = val.round_ties_even() as i64;
            }
        }
    }
    cq
}

#[inline]
fn idct_8x8(cq: &[i64; 64], qm: &[i64; 64]) -> [i64; 64] {
    if cq[1..].iter().all(|&v| v == 0) {
        return [(529 * cq[0] * qm[0] + 2048).div_euclid(4096); 64];
    }
    let mut c_times_qm = [0i64; 64];
    for i in 0..64 {
        c_times_qm[i] = cq[i] * qm[i];
    }
    let mut t1 = [0i64; 64];
    for i in 0..8 {
        for j in 0..8 {
            let mut sum = 0i64;
            for k in 0..8 {
                sum += MI[k * 8 + i] * c_times_qm[k * 8 + j];
            }
            t1[i * 8 + j] = sum;
        }
    }
    let mut rec = [0i64; 64];
    for i in 0..8 {
        for j in 0..8 {
            let mut sum = 0i64;
            for k in 0..8 {
                sum += t1[i * 8 + k] * MI[k * 8 + j];
            }
            // Rust signed division truncates; Python // and JS Math.floor do not.
            rec[i * 8 + j] = (sum + 2048).div_euclid(4096);
        }
    }
    rec
}
fn sub_2x2(plane: &[f64], w: usize, h: usize) -> Vec<f64> {
    let hw = w / 2;
    let hh = h / 2;
    let mut out = vec![0.0; hw * hh];
    for y in 0..hh {
        for x in 0..hw {
            let p0 = plane[(y * 2) * w + (x * 2)];
            let p1 = plane[(y * 2) * w + (x * 2 + 1)];
            let p2 = plane[(y * 2 + 1) * w + (x * 2)];
            let p3 = plane[(y * 2 + 1) * w + (x * 2 + 1)];
            let mut val = (p0 + p1 + p2 + p3) / 4.0;
            if val < 0.0 {
                val = 0.0;
            }
            if val > 255.0 {
                val = 255.0;
            }
            // Already clamped to 0..255: integer conversion truncates exactly.
            out[y * hw + x] = (val as u8) as f64;
        }
    }
    out
}

fn bgr_to_yuv(bgr: &[u8], w: usize, h: usize) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let mut y = vec![0.0; w * h];
    let mut cb_full = vec![0.0; w * h];
    let mut cr_full = vec![0.0; w * h];
    for i in 0..(w * h) {
        let b = bgr[i * 3] as f32;
        let g = bgr[i * 3 + 1] as f32;
        let r = bgr[i * 3 + 2] as f32;

        let mut y_val = 0.299 * r + 0.587 * g + 0.114 * b;
        if y_val < 0.0 {
            y_val = 0.0;
        }
        if y_val > 255.0 {
            y_val = 255.0;
        }
        y[i] = (y_val as u8) as f64;

        cb_full[i] = (128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b) as f64;
        cr_full[i] = (128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b) as f64;
    }
    let cb = sub_2x2(&cb_full, w, h);
    let cr = sub_2x2(&cr_full, w, h);
    (y, cb, cr)
}

fn yuv_to_bgr(y_plane: &[u8], cb_plane: &[u8], cr_plane: &[u8], w: usize, h: usize) -> Vec<u8> {
    let mut bgr = vec![0u8; w * h * 3];
    let hw = w / 2;
    for y in 0..h {
        let cy = y / 2;
        for x in 0..w {
            let cx = x / 2;
            let yy = y_plane[y * w + x] as i32;
            let cb = (cb_plane[cy * hw + cx] as i32) - 128;
            let cr = (cr_plane[cy * hw + cx] as i32) - 128;

            let mut r = yy + ((359 * cr + 128) >> 8);
            let mut g = yy - ((88 * cb + 183 * cr + 128) >> 8);
            let mut b = yy + ((454 * cb + 128) >> 8);

            if r < 0 {
                r = 0;
            } else if r > 255 {
                r = 255;
            }
            if g < 0 {
                g = 0;
            } else if g > 255 {
                g = 255;
            }
            if b < 0 {
                b = 0;
            } else if b > 255 {
                b = 255;
            }

            let i = (y * w + x) * 3;
            bgr[i] = b as u8;
            bgr[i + 1] = g as u8;
            bgr[i + 2] = r as u8;
        }
    }
    bgr
}
const R_SEARCH: isize = 3;

/// SAD of an 8x8 byte block. A losing candidate may return a partial sum >=
/// `limit`; a candidate that can win always returns its exact sum. Checking
/// once per row preserves the old pixel-wise cutoff's winner and tie order.
#[inline]
fn sad_bounded(block: &[u8; 64], reference: &[u8], stride: usize, limit: i32) -> i32 {
    assert!(reference.len() >= 7 * stride + 8);
    #[cfg(target_arch = "x86_64")]
    {
        use std::arch::x86_64::{_mm_cvtsi128_si64, _mm_loadl_epi64, _mm_sad_epu8};
        let mut sad = 0;
        for row in 0..8 {
            // SSE2 is guaranteed on x86_64. Each unaligned load reads exactly
            // eight bytes; the block type and reference bound above cover them.
            unsafe {
                let a = _mm_loadl_epi64(block.as_ptr().add(row * 8).cast());
                let b = _mm_loadl_epi64(reference.as_ptr().add(row * stride).cast());
                sad += _mm_cvtsi128_si64(_mm_sad_epu8(a, b)) as i32;
            }
            if sad >= limit {
                break;
            }
        }
        sad
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        sad_bounded_scalar(block, reference, stride, limit)
    }
}

#[cfg(any(not(target_arch = "x86_64"), test))]
fn sad_bounded_scalar(block: &[u8; 64], reference: &[u8], stride: usize, limit: i32) -> i32 {
    let mut sad = 0;
    for row in 0..8 {
        for col in 0..8 {
            sad += (block[row * 8 + col] as i32 - reference[row * stride + col] as i32).abs();
        }
        if sad >= limit {
            break;
        }
    }
    sad
}

fn enc_plane(
    cur: &[f64],
    prev: Option<&[u8]>,
    w: usize,
    h: usize,
    ftype: u8,
    use_mv: bool,
    qm: &[i64; 64],
    dz: f64,
    skip_t: i32,
) -> (Vec<u8>, Vec<u8>) {
    let nbx = w / 8;
    let nby = h / 8;
    let nb = nbx * nby;

    let mut recon = vec![0u8; w * h];
    let mut payload = Vec::new();

    let mut mvx_arr = vec![0i8; nb];
    let mut mvy_arr = vec![0i8; nb];
    let mut skip_arr = vec![false; nb];

    let mut zzf_all = Vec::with_capacity(nb * 64);
    // Edge-pad once, rather than clamp two coordinates for every SAD sample.
    // This is the same edge extension used by main's motion search.
    let radius = R_SEARCH as usize;
    let stride = w + 2 * radius;
    let padded = if ftype == 1 && use_mv {
        let previous = prev.expect("predicted plane requires a reference");
        let mut padded = vec![0u8; stride * (h + 2 * radius)];
        for y in 0..h + 2 * radius {
            let sy = y.saturating_sub(radius).min(h - 1);
            for x in 0..stride {
                let sx = x.saturating_sub(radius).min(w - 1);
                padded[y * stride + x] = previous[sy * w + sx];
            }
        }
        padded
    } else {
        Vec::new()
    };

    for by in 0..nby {
        for bx in 0..nbx {
            let b_idx = by * nbx + bx;

            // Extract block
            let mut block = [0.0; 64];
            for y in 0..8 {
                for x in 0..8 {
                    block[y * 8 + x] = cur[(by * 8 + y) * w + (bx * 8 + x)];
                }
            }

            let mut pred = [128.0; 64];
            let mut best_mvx = 0;
            let mut best_mvy = 0;

            if ftype == 0 {
                // Keyframe: pred is already 128.0
            } else if let Some(prev_frame) = prev {
                if use_mv {
                    // Luma samples are integral 0..255. Convert once per block,
                    // rather than for every pixel of every motion candidate.
                    let block_bytes = block.map(|value| value as u8);
                    // Prefer zero motion on a tie, matching the main encoder.
                    let zero_base = (by * 8 + radius) * stride + bx * 8 + radius;
                    let mut best_sad =
                        sad_bounded(&block_bytes, &padded[zero_base..], stride, i32::MAX);
                    for dy in -R_SEARCH..=R_SEARCH {
                        for dx in -R_SEARCH..=R_SEARCH {
                            if best_sad == 0 || (dx == 0 && dy == 0) {
                                continue;
                            }
                            let base_y = (by * 8 + radius) as isize + dy;
                            let base_x = (bx * 8 + radius) as isize + dx;
                            let base = base_y as usize * stride + base_x as usize;
                            let sad = sad_bounded(&block_bytes, &padded[base..], stride, best_sad);
                            if sad < best_sad {
                                best_sad = sad;
                                best_mvx = dx;
                                best_mvy = dy;
                            }
                        }
                    }
                    mvx_arr[b_idx] = best_mvx as i8;
                    mvy_arr[b_idx] = best_mvy as i8;

                    for y in 0..8 {
                        for x in 0..8 {
                            let py = (by * 8) as isize + y as isize + best_mvy;
                            let px = (bx * 8) as isize + x as isize + best_mvx;
                            let clamp_y = py.clamp(0, h as isize - 1) as usize;
                            let clamp_x = px.clamp(0, w as isize - 1) as usize;
                            pred[y * 8 + x] = prev_frame[clamp_y * w + clamp_x] as f64;
                        }
                    }
                } else {
                    for y in 0..8 {
                        for x in 0..8 {
                            pred[y * 8 + x] = prev_frame[(by * 8 + y) * w + (bx * 8 + x)] as f64;
                        }
                    }
                }
            }

            // Residual
            let mut resid = [0.0; 64];
            for i in 0..64 {
                resid[i] = block[i] - pred[i];
            }

            // DCT & Quantize
            let cq = dct_8x8(&resid, qm, dz);

            let mut is_zero = true;
            for i in 0..64 {
                if cq[i] != 0 {
                    is_zero = false;
                    break;
                }
            }

            let mut skip = false;
            if ftype == 1 {
                let mut sse = 0.0;
                for i in 0..64 {
                    sse += resid[i] * resid[i];
                }
                if (best_mvx == 0 && best_mvy == 0)
                    && (is_zero || (skip_t > 0 && sse < (skip_t as f64)))
                {
                    skip = true;
                }
            }
            skip_arr[b_idx] = skip;

            // Reconstruction
            if skip {
                if let Some(prev_frame) = prev {
                    for y in 0..8 {
                        for x in 0..8 {
                            recon[(by * 8 + y) * w + (bx * 8 + x)] =
                                prev_frame[(by * 8 + y) * w + (bx * 8 + x)];
                        }
                    }
                }
            } else {
                let rec_resid = idct_8x8(&cq, qm);
                let mut zzf = [0i64; 64];
                for i in 0..64 {
                    zzf[i] = cq[ZZ[i]];
                }
                zzf_all.extend_from_slice(&zzf);

                for y in 0..8 {
                    for x in 0..8 {
                        let mut val = pred[y * 8 + x] as i64 + rec_resid[y * 8 + x];
                        if val < 0 {
                            val = 0;
                        }
                        if val > 255 {
                            val = 255;
                        }
                        recon[(by * 8 + y) * w + (bx * 8 + x)] = val as u8;
                    }
                }
            }
        }
    }

    // DC DPCM for coded blocks
    let mut coded_count = 0;
    for b in 0..nb {
        if !skip_arr[b] {
            coded_count += 1;
        }
    }

    if coded_count > 0 {
        let mut prev_dc = 0;
        let mut offset = 0;
        for b in 0..nb {
            if !skip_arr[b] {
                let curr_dc = zzf_all[offset];
                zzf_all[offset] = curr_dc - prev_dc;
                prev_dc = curr_dc;
                offset += 64;
            }
        }
    }

    // RLE Encode
    let mut bidx = Vec::new();
    let mut pos = Vec::new();
    let mut zzf_vals = Vec::new();
    let mut offset = 0;
    for b in 0..nb {
        if !skip_arr[b] {
            for i in 0..64 {
                let val = zzf_all[offset + i];
                if val != 0 {
                    bidx.push(b);
                    pos.push(i);
                    zzf_vals.push(val);
                }
            }
            offset += 64;
        }
    }

    let mut counts = vec![0u8; nb];
    for &b in &bidx {
        counts[b] += 1;
    }

    let mut prevpos = vec![-1i32; pos.len()];
    for i in 1..pos.len() {
        if bidx[i] == bidx[i - 1] {
            prevpos[i] = pos[i - 1] as i32;
        }
    }

    // Skip bitmask
    if ftype == 1 {
        let mut head = Vec::new();
        let mut current_byte = 0u8;
        let mut bit_idx = 0;
        for b in 0..nb {
            if skip_arr[b] {
                current_byte |= 128 >> bit_idx;
            }
            bit_idx += 1;
            if bit_idx == 8 {
                head.push(current_byte);
                current_byte = 0;
                bit_idx = 0;
            }
        }
        if bit_idx > 0 {
            head.push(current_byte);
        }
        payload.extend(head);
    }

    let mut vals_idx = 0;
    for b in 0..nb {
        if !skip_arr[b] {
            if ftype == 1 && use_mv {
                payload.push(mvx_arr[b] as u8);
                payload.push(mvy_arr[b] as u8);
            }
            payload.push(counts[b]);
            for _ in 0..counts[b] {
                let r = (pos[vals_idx] as i32 - prevpos[vals_idx] - 1) as u8;
                let v = zzf_vals[vals_idx] as i16;
                payload.push(r);
                payload.extend_from_slice(&v.to_le_bytes());
                vals_idx += 1;
            }
        }
    }

    (payload, recon)
}

#[cfg(test)]
mod tests {
    use super::{sad_bounded, sad_bounded_scalar};

    #[test]
    fn vector_sad_matches_scalar_at_unaligned_offsets_and_cutoffs() {
        let mut seed = 721u32;
        let mut byte = || {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            (seed >> 24) as u8
        };
        for stride in [8, 14, 17, 470] {
            for offset in 0..16 {
                let mut reference = vec![0; offset + 7 * stride + 8];
                reference.iter_mut().for_each(|v| *v = byte());
                for block in [[0; 64], [255; 64], std::array::from_fn(|_| byte())] {
                    let reference = &reference[offset..];
                    let exact = sad_bounded_scalar(&block, reference, stride, i32::MAX);
                    for limit in [0, 1, exact / 2, exact, exact + 1, i32::MAX] {
                        let actual = sad_bounded(&block, reference, stride, limit);
                        assert_eq!(actual, sad_bounded_scalar(&block, reference, stride, limit));
                        assert_eq!(actual < limit, exact < limit);
                        if actual < limit {
                            assert_eq!(actual, exact);
                        }
                    }
                }
            }
        }
        assert_eq!(sad_bounded(&[42; 64], &[42; 64], 8, i32::MAX), 0);
        assert_eq!(sad_bounded(&[0; 64], &[255; 64], 8, i32::MAX), 64 * 255);
    }
}
