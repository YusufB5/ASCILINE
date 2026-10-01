//! ascii.rs — replaces both ascii_video_player2.py's AsciiMapper AND the
//! inline indices/char_codes/rgb NumPy code that used to live directly in
//! stream_server.py's `produce()` closure. Two entry points:
//!   - build_frame_buf   -> (rows, cols, 4) [char, R, G, B] for color modes
//!   - build_text_frame  -> plain-text grid for mode 1 (B&W ASCII)
//! Both use the exact same intensity->index mapping as the Python code:
//!   indices = (gray * (n-1)) // 255   (integer division, matches NumPy)

use numpy::{PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[inline]
fn char_index(gray_val: u8, n: usize) -> usize {
    ((gray_val as u32) * (n as u32 - 1) / 255) as usize
}

/// Builds the (rows, cols, 4) framebuffer: channel 0 = character byte,
/// channels 1..3 = R,G,B (optionally quantized by right-shifting `qb` bits,
/// same as `(rgb >> qb) << qb` in the Python path).
#[pyfunction]
#[pyo3(signature = (gray, bgr, char_byte_lut, qb=0))]
pub fn build_frame_buf<'py>(
    py: Python<'py>,
    gray: PyReadonlyArray2<'py, u8>,
    bgr: PyReadonlyArray1<'py, u8>, // flat (rows*cols*3), BGR order
    char_byte_lut: PyReadonlyArray1<'py, u8>,
    qb: u8,
) -> PyResult<PyObject> {
    let gray_shape = gray.shape();
    let (rows, cols) = (gray_shape[0], gray_shape[1]);
    let gray_slice = gray.as_slice()?.to_vec();
    let bgr_slice = bgr.as_slice()?.to_vec();
    let lut = char_byte_lut.as_slice()?.to_vec();
    let n = lut.len();
    if rows == 0 || cols == 0 || bgr_slice.len() != rows * cols * 3 || n == 0 || n > 256 || qb > 7 {
        return Err(PyValueError::new_err(
            "non-empty gray, matching flat BGR, 1..256 palette entries and qb 0..7 required",
        ));
    }
    let out = py.allow_threads(|| {
        let mut out = vec![0u8; rows * cols * 4];
        let mask: u8 = if qb > 0 { !((1u8 << qb) - 1) } else { 0xFF };
        for i in 0..rows * cols {
            let idx = char_index(gray_slice[i], n);
            out[i * 4] = lut[idx];
            // BGR -> RGB with optional bit-quantization, matching
            // `rgb = bgr[:, :, ::-1]; rgb = (rgb >> qb) << qb`.
            let b = bgr_slice[i * 3] & mask;
            let g = bgr_slice[i * 3 + 1] & mask;
            let r = bgr_slice[i * 3 + 2] & mask;
            out[i * 4 + 1] = r;
            out[i * 4 + 2] = g;
            out[i * 4 + 3] = b;
        }
        out
    });
    let arr = PyArray1::from_vec_bound(py, out).reshape([rows, cols, 4])?;
    Ok(arr.into_py(py))
}

/// B&W text mode (render_mode == 1): returns a plain multi-line string,
/// one character per cell, no color escape codes (the browser client
/// renders this as plain monospace text, same as the existing protocol).
#[pyfunction]
pub fn build_text_frame<'py>(
    py: Python<'py>,
    gray: PyReadonlyArray2<'py, u8>,
    palette: &str,
) -> PyResult<String> {
    let shape = gray.shape();
    let (rows, cols) = (shape[0], shape[1]);
    let gray_slice = gray.as_slice()?.to_vec();
    let chars: Vec<char> = palette.chars().collect();
    let n = chars.len();
    if rows == 0 || cols == 0 || n == 0 || n > 4096 {
        return Err(PyValueError::new_err(
            "non-empty gray and a palette with 1..4096 characters required",
        ));
    }
    let text = py.allow_threads(|| {
        let mut text = String::with_capacity(rows * (cols + 1));
        for row in 0..rows {
            for col in 0..cols {
                let idx = char_index(gray_slice[row * cols + col], n);
                text.push(chars[idx]);
            }
            if row + 1 < rows {
                text.push('\n');
            }
        }
        text
    });
    Ok(text)
}
