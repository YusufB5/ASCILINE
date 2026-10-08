//! Optional file/URL engine. Transport, playback sessions and webcam stay in Python.
mod ascii;
mod codec;
mod dct;
mod decoder;

use pyo3::prelude::*;

#[pymodule]
fn _asciline_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API_VERSION", 1)?;
    m.add("DECODE_THREADS_SUPPORTED", true)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<decoder::PyVideoDecoder>()?;
    m.add_class::<dct::RustProfileEncoder>()?;
    m.add_function(wrap_pyfunction!(ascii::build_frame_buf, m)?)?;
    m.add_function(wrap_pyfunction!(ascii::build_text_frame, m)?)?;
    m.add_function(wrap_pyfunction!(codec::encode_frame, m)?)?;
    Ok(())
}
