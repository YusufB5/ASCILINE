//! codec.rs — ports codec.py's `encode_frame()` (the RAW/ZLIB/DELTA/
//! RLE_FULL adaptive path that's actually used by default).
//!
//! NOT ported in this pass: the opt-in lossy DCT profile (TAG_PROFILE = 4,
//! ProfileEncoder in codec.py) with its motion search and custom integer
//! DCT. That path must stay bit-exact with codec.js's decoder or playback
//! silently corrupts, and getting an integer DCT + motion-search port
//! bit-exact without being able to compile-test against the real
//! codec.js decoder here would be guessing, not engineering. It's a
//! well-scoped follow-up if you want it — flag it and we'll do it as
//! its own pass with a way to test against codec.js.
//!
//! Wire format (unchanged from codec.py):
//!   [4 bytes frame_index, big-endian u32][1 byte tag][payload...]

use flate2::write::ZlibEncoder;
use flate2::Compression;
use numpy::{PyArray1, PyArrayMethods, PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::io::Write;

const TAG_RAW: u8 = 0;
const TAG_ZLIB: u8 = 1;
const TAG_DELTA: u8 = 2;
const TAG_RLE_FULL: u8 = 3;

const KEYFRAME_INTERVAL: u32 = 48;
const DELTA_MAX_FRAC: f64 = 0.60;
const ZLIB_MIN_FRAC: f64 = 0.10;

fn zlib_compress(data: &[u8], level: u32) -> Vec<u8> {
    let mut e = ZlibEncoder::new(Vec::new(), Compression::new(level.min(9)));
    e.write_all(data).expect("zlib write");
    e.finish().expect("zlib finish")
}

/// Same run-length scheme as codec.py's _rle_encode: [count: u16 LE][value: C bytes]
/// per run, splitting runs longer than 65535 into repeated max-length chunks.
fn rle_encode(frame: &[u8], channels: usize) -> Vec<u8> {
    let n = frame.len() / channels;
    if n == 0 {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut run_start = 0usize;
    for i in 1..=n {
        let same = i < n
            && frame[i * channels..i * channels + channels]
                == frame[run_start * channels..run_start * channels + channels];
        if !same {
            let mut count = i - run_start;
            let val = &frame[run_start * channels..run_start * channels + channels];
            while count > 65535 {
                out.extend_from_slice(&(65535u16).to_le_bytes());
                out.extend_from_slice(val);
                count -= 65535;
            }
            out.extend_from_slice(&(count as u16).to_le_bytes());
            out.extend_from_slice(val);
            run_start = i;
        }
    }
    out
}

fn full_frame(raw: &[u8], frame: &[u8], channels: usize, frame_index: u32, level: u32) -> Vec<u8> {
    let z_raw = zlib_compress(raw, level);
    let rle_bytes = rle_encode(frame, channels);
    let z_rle = zlib_compress(&rle_bytes, level);

    let (tag, payload): (u8, &[u8]) = if z_rle.len() < z_raw.len() && z_rle.len() < raw.len() {
        (TAG_RLE_FULL, &z_rle)
    } else if z_raw.len() < raw.len() {
        (TAG_ZLIB, &z_raw)
    } else {
        (TAG_RAW, raw)
    };
    let mut msg = Vec::with_capacity(5 + payload.len());
    msg.extend_from_slice(&frame_index.to_be_bytes());
    msg.push(tag);
    msg.extend_from_slice(payload);
    msg
}

pub struct EncodeResult {
    pub msg: Vec<u8>,
    pub shown: Vec<u8>,
}

/// Direct port of codec.py's encode_frame(). `frame`/`prev` are raw,
/// C-contiguous bytes: shape (rows, cols, channels) flattened, channels=4
/// for ASCII colour ([char,R,G,B]) or 3 for pixel mode ([B,G,R]) — exactly
/// as codec.py documents.
pub fn encode_frame_core(
    frame: &[u8],
    prev: Option<&[u8]>,
    channels: usize,
    frame_index: u32,
    level: u32,
    tolerance: i32,
) -> EncodeResult {
    let keyframe = prev.is_none()
        || frame_index % KEYFRAME_INTERVAL == 0
        || prev.map_or(false, |p| p.len() != frame.len());

    if keyframe {
        let msg = full_frame(frame, frame, channels, frame_index, level);
        return EncodeResult {
            msg,
            shown: frame.to_vec(),
        };
    }
    let prev = prev.unwrap();
    if frame == prev {
        let mut msg = Vec::from(frame_index.to_be_bytes());
        msg.push(TAG_DELTA);
        msg.extend(zlib_compress(&[], level));
        return EncodeResult {
            msg,
            shown: prev.to_vec(),
        };
    }
    let n_cells = frame.len() / channels;

    let mut changed = vec![false; n_cells];
    let mut changed_count = 0usize;
    for i in 0..n_cells {
        let base = i * channels;
        let cell_changed = if channels == 4 {
            // Channel 0 is the character (structure) plane -> always exact;
            // tolerance only applies to the R,G,B colour channels.
            let char_changed = frame[base] != prev[base];
            let mut color_changed = false;
            for k in 1..4 {
                let diff = (frame[base + k] as i32 - prev[base + k] as i32).abs();
                let over = if tolerance <= 0 {
                    diff != 0
                } else {
                    diff > tolerance
                };
                if over {
                    color_changed = true;
                    break;
                }
            }
            char_changed || color_changed
        } else {
            let mut any = false;
            for k in 0..channels {
                let diff = (frame[base + k] as i32 - prev[base + k] as i32).abs();
                let over = if tolerance <= 0 {
                    diff != 0
                } else {
                    diff > tolerance
                };
                if over {
                    any = true;
                    break;
                }
            }
            any
        };
        if cell_changed {
            changed[i] = true;
            changed_count += 1;
        }
    }

    let frac = changed_count as f64 / n_cells as f64;

    let mut ci: Vec<u32> = Vec::with_capacity(changed_count);
    let mut delta_shown = prev.to_vec();
    for i in 0..n_cells {
        if changed[i] {
            ci.push(i as u32);
            let base = i * channels;
            delta_shown[base..base + channels].copy_from_slice(&frame[base..base + channels]);
        }
    }

    let mut candidates: Vec<(u8, Vec<u8>, Vec<u8>)> = Vec::new(); // (tag, payload, shown)

    if frac < DELTA_MAX_FRAC {
        let mut plain = Vec::with_capacity(ci.len() * 4 + ci.len() * channels);
        for &idx in &ci {
            plain.extend_from_slice(&idx.to_le_bytes());
        }
        for &idx in &ci {
            let base = idx as usize * channels;
            plain.extend_from_slice(&frame[base..base + channels]);
        }
        let delta = zlib_compress(&plain, level);
        candidates.push((TAG_DELTA, delta, delta_shown));
    }

    if frac >= ZLIB_MIN_FRAC || candidates.is_empty() {
        let z_raw = zlib_compress(frame, level);
        let rle_bytes = rle_encode(frame, channels);
        let z_rle = zlib_compress(&rle_bytes, level);
        if z_rle.len() < z_raw.len() {
            candidates.push((TAG_RLE_FULL, z_rle, frame.to_vec()));
        } else {
            candidates.push((TAG_ZLIB, z_raw, frame.to_vec()));
        }
    }

    candidates.sort_by_key(|c| c.1.len());
    let (mut tag, mut payload, mut shown) = candidates.into_iter().next().unwrap();

    // Never exceed the raw frame — zlib can inflate incompressible data slightly.
    if frame.len() < payload.len() {
        tag = TAG_RAW;
        payload = frame.to_vec();
        shown = frame.to_vec();
    }

    let mut msg = Vec::with_capacity(5 + payload.len());
    msg.extend_from_slice(&frame_index.to_be_bytes());
    msg.push(tag);
    msg.extend_from_slice(&payload);

    EncodeResult { msg, shown }
}

#[pyfunction]
#[pyo3(signature = (frame, prev=None, frame_index=0, level=3, tolerance=0))]
pub fn encode_frame<'py>(
    py: Python<'py>,
    frame: PyReadonlyArrayDyn<'py, u8>,
    prev: Option<PyReadonlyArrayDyn<'py, u8>>,
    frame_index: u32,
    level: u32,
    tolerance: i32,
) -> PyResult<(Py<PyBytes>, PyObject)> {
    let shape = frame.shape().to_vec();
    if shape.len() != 3 || shape[0] == 0 || shape[1] == 0 || ![3, 4].contains(&shape[2]) {
        return Err(PyValueError::new_err(
            "frame must have non-empty shape (rows, cols, 3 or 4)",
        ));
    }
    if level > 9 || !(0..=255).contains(&tolerance) {
        return Err(PyValueError::new_err(
            "level must be 0..9 and tolerance 0..255",
        ));
    }
    let channels = shape[2];
    // Own the data before releasing the GIL: another Python/NumPy thread may
    // mutate its inputs while this call compresses them.
    let frame_owned = frame.as_slice()?.to_vec();
    let prev_owned = match &prev {
        Some(p) if p.shape() == shape => Some(p.as_slice()?.to_vec()),
        Some(_) => None, // dimension change forces a full keyframe
        None => None,
    };
    let result = py.allow_threads(|| {
        encode_frame_core(
            &frame_owned,
            prev_owned.as_deref(),
            channels,
            frame_index,
            level,
            tolerance,
        )
    });

    let msg_py = PyBytes::new_bound(py, &result.msg).unbind();
    let shown_arr = PyArray1::from_vec_bound(py, result.shown).reshape(shape)?;
    Ok((msg_py, shown_arr.into_py(py)))
}
