//! One receive/feed/drain path for next(), grab() and accurate timestamp seeking.
use ffmpeg::software::scaling::{context::Context as Scaler, flag::Flags};
use ffmpeg::util::frame::video::Video as VideoFrame;
use ffmpeg::{format::Pixel, Error, Packet};
use ffmpeg_next as ffmpeg;
use numpy::{PyArray1, PyArrayMethods};
use pyo3::exceptions::{PyFileNotFoundError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::sync::Mutex;

type FrameOutput = (Option<Vec<u8>>, Vec<u8>);

fn dimensions(cols: u32, rows: u32) -> PyResult<()> {
    if cols == 0
        || rows == 0
        || cols > 16384
        || rows > 16384
        || u64::from(cols) * u64::from(rows) > 16_777_216
    {
        return Err(PyValueError::new_err(
            "grid dimensions must be positive, at most 16384 per axis and 16777216 cells",
        ));
    }
    Ok(())
}

struct DecoderCore {
    input: ffmpeg::format::context::Input,
    stream_index: usize,
    decoder: ffmpeg::decoder::Video,
    scaler: Option<Scaler>,
    scale_source: Option<(Pixel, u32, u32)>,
    cols: u32,
    rows: u32,
    mirror: bool,
    skip_gray: bool,
    time_base: f64,
    origin_seconds: f64,
    fps: f64,
    draining: bool,
    ended: bool,
    pending: Option<VideoFrame>,
    position: Option<f64>,
    fallback_time: f64,
}

// libswscale's context is not marked Send by ffmpeg-next. This context is
// exclusively owned and every access, including Drop, is serialized by Mutex.
// No FFmpeg context or NumPy view is shared with a concurrent native call.
unsafe impl Send for DecoderCore {}

impl DecoderCore {
    fn open(
        path: &str,
        cols: u32,
        rows: u32,
        skip_gray: bool,
        mirror: bool,
        fallback_fps: f64,
    ) -> Result<(Self, Metadata), Error> {
        ffmpeg::init()?;
        let input = ffmpeg::format::input(&path)?;
        let stream = input
            .streams()
            .best(ffmpeg::media::Type::Video)
            .ok_or(Error::StreamNotFound)?;
        let stream_index = stream.index();
        let decoder = ffmpeg::codec::context::Context::from_parameters(stream.parameters())?
            .decoder()
            .video()?;
        let time_base = f64::from(stream.time_base());
        let origin_seconds = if stream.start_time() == ffmpeg::ffi::AV_NOPTS_VALUE {
            0.0
        } else {
            stream.start_time() as f64 * time_base
        };
        let reported = f64::from(stream.avg_frame_rate());
        let fps = if fallback_fps > 0.0 {
            fallback_fps
        } else if reported.is_finite() && reported > 0.0 {
            reported
        } else {
            24.0
        };
        let stream_duration = stream.duration();
        let duration = if stream_duration > 0 && stream_duration != ffmpeg::ffi::AV_NOPTS_VALUE {
            stream_duration as f64 * time_base
        } else if input.duration() > 0 {
            input.duration() as f64 / ffmpeg::ffi::AV_TIME_BASE as f64
        } else {
            0.0
        };
        let frame_count = if stream.frames() > 0 {
            stream.frames()
        } else {
            (duration * fps).round() as i64
        };
        let metadata = Metadata {
            fps,
            frame_count,
            duration,
            vid_w: decoder.width(),
            vid_h: decoder.height(),
        };
        Ok((
            Self {
                input,
                stream_index,
                decoder,
                scaler: None,
                scale_source: None,
                cols,
                rows,
                mirror,
                skip_gray,
                time_base,
                origin_seconds,
                fps,
                draining: false,
                ended: false,
                pending: None,
                position: None,
                fallback_time: 0.0,
            },
            metadata,
        ))
    }

    fn receive(&mut self) -> Result<Option<VideoFrame>, Error> {
        if self.ended {
            return Ok(None);
        }
        loop {
            let mut frame = VideoFrame::empty();
            match self.decoder.receive_frame(&mut frame) {
                Ok(()) => return Ok(Some(frame)),
                Err(Error::Eof) => {
                    self.ended = true;
                    return Ok(None);
                }
                Err(Error::Other { errno }) if errno == ffmpeg::error::EAGAIN => {}
                Err(error) => return Err(error),
            }
            if self.draining {
                return Err(Error::InvalidData);
            }
            loop {
                let mut packet = Packet::empty();
                match packet.read(&mut self.input) {
                    Ok(()) if packet.stream() == self.stream_index => {
                        self.decoder.send_packet(&packet)?;
                        break;
                    }
                    Ok(()) => continue,
                    Err(Error::Eof) => {
                        self.decoder.send_eof()?;
                        self.draining = true;
                        break;
                    }
                    Err(error) => return Err(error),
                }
            }
        }
    }

    fn timestamp(&self, frame: &VideoFrame) -> f64 {
        frame
            .timestamp()
            .or(frame.pts())
            .map(|pts| pts as f64 * self.time_base - self.origin_seconds)
            .unwrap_or(self.fallback_time)
    }

    fn consume(&mut self) -> Result<Option<VideoFrame>, Error> {
        let frame = match self.pending.take() {
            Some(frame) => Some(frame),
            None => self.receive()?,
        };
        if let Some(ref frame) = frame {
            let timestamp = self.timestamp(frame).max(0.0);
            self.position = Some(timestamp);
            self.fallback_time = timestamp + 1.0 / self.fps;
        }
        Ok(frame)
    }

    fn next_frame(&mut self) -> Result<Option<FrameOutput>, Error> {
        let decoded = match self.consume()? {
            Some(frame) => frame,
            None => return Ok(None),
        };
        let source = (decoded.format(), decoded.width(), decoded.height());
        if self.scaler.is_none() || self.scale_source != Some(source) {
            self.scaler = Some(Scaler::get(
                source.0,
                source.1,
                source.2,
                Pixel::BGR24,
                self.cols,
                self.rows,
                Flags::BILINEAR,
            )?);
            self.scale_source = Some(source);
        }
        let mut scaled = VideoFrame::empty();
        self.scaler.as_mut().unwrap().run(&decoded, &mut scaled)?;
        let stride = scaled.stride(0);
        let width_bytes = self.cols as usize * 3;
        let mut bgr = vec![0u8; self.rows as usize * width_bytes];
        for row in 0..self.rows as usize {
            bgr[row * width_bytes..(row + 1) * width_bytes]
                .copy_from_slice(&scaled.data(0)[row * stride..row * stride + width_bytes]);
            if self.mirror {
                for col in 0..self.cols as usize / 2 {
                    for channel in 0..3 {
                        bgr.swap(
                            row * width_bytes + col * 3 + channel,
                            row * width_bytes + (self.cols as usize - 1 - col) * 3 + channel,
                        );
                    }
                }
            }
        }
        let gray = if self.skip_gray {
            None
        } else {
            Some(
                bgr.chunks_exact(3)
                    .map(|cell| {
                        ((cell[2] as u32 * 4899
                            + cell[1] as u32 * 9617
                            + cell[0] as u32 * 1868
                            + 8192)
                            >> 14) as u8
                    })
                    .collect(),
            )
        };
        Ok(Some((gray, bgr)))
    }

    fn seek(&mut self, seconds: f64) -> Result<bool, Error> {
        let absolute = ((seconds + self.origin_seconds) * ffmpeg::ffi::AV_TIME_BASE as f64) as i64;
        self.input.seek(absolute, ..absolute)?;
        self.decoder.flush();
        self.draining = false;
        self.ended = false;
        self.pending = None;
        self.position = None;
        self.fallback_time = seconds;
        while let Some(frame) = self.receive()? {
            let time = self.timestamp(&frame);
            if time + 1e-7 >= seconds {
                self.position = Some(time.max(0.0));
                self.pending = Some(frame);
                return Ok(true);
            }
        }
        Ok(false)
    }
}

struct Metadata {
    fps: f64,
    frame_count: i64,
    duration: f64,
    vid_w: u32,
    vid_h: u32,
}

#[pyclass(name = "VideoDecoder")]
pub struct PyVideoDecoder {
    inner: Mutex<Option<DecoderCore>>,
    #[pyo3(get)]
    fps: f64,
    #[pyo3(get)]
    frame_count: i64,
    #[pyo3(get)]
    duration: f64,
    #[pyo3(get)]
    vid_w: u32,
    #[pyo3(get)]
    vid_h: u32,
}

impl PyVideoDecoder {
    fn with_core<T, F>(&self, py: Python<'_>, operation: F) -> PyResult<T>
    where
        T: Send,
        F: FnOnce(&mut DecoderCore) -> PyResult<T> + Send,
    {
        py.allow_threads(|| {
            let mut guard = self
                .inner
                .lock()
                .map_err(|_| PyRuntimeError::new_err("decoder lock poisoned"))?;
            let core = guard
                .as_mut()
                .ok_or_else(|| PyRuntimeError::new_err("decoder is closed"))?;
            operation(core)
        })
    }
}

fn runtime_error(error: Error) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pymethods]
impl PyVideoDecoder {
    #[new]
    #[pyo3(signature = (path, cols, rows, skip_gray=false, mirror=false, fallback_fps=0.0))]
    fn new(
        py: Python<'_>,
        path: &str,
        cols: u32,
        rows: u32,
        skip_gray: bool,
        mirror: bool,
        fallback_fps: f64,
    ) -> PyResult<Self> {
        dimensions(cols, rows)?;
        if !fallback_fps.is_finite() || fallback_fps < 0.0 {
            return Err(PyValueError::new_err(
                "fallback_fps must be finite and non-negative",
            ));
        }
        let (core, metadata) = py
            .allow_threads(|| DecoderCore::open(path, cols, rows, skip_gray, mirror, fallback_fps))
            .map_err(|error| {
                PyFileNotFoundError::new_err(format!(
                    "Could not open video source {path:?}: {error}"
                ))
            })?;
        Ok(Self {
            inner: Mutex::new(Some(core)),
            fps: metadata.fps,
            frame_count: metadata.frame_count,
            duration: metadata.duration,
            vid_w: metadata.vid_w,
            vid_h: metadata.vid_h,
        })
    }

    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<Option<(Option<PyObject>, PyObject)>> {
        let (output, rows, cols) = self.with_core(py, |core| {
            Ok((
                core.next_frame().map_err(runtime_error)?,
                core.rows as usize,
                core.cols as usize,
            ))
        })?;
        match output {
            None => Ok(None),
            Some((gray, bgr)) => {
                let bgr = PyArray1::from_vec_bound(py, bgr).reshape([rows, cols, 3])?;
                let gray = match gray {
                    Some(values) => Some(
                        PyArray1::from_vec_bound(py, values)
                            .reshape([rows, cols])?
                            .into_py(py),
                    ),
                    None => None,
                };
                Ok(Some((gray, bgr.into_py(py))))
            }
        }
    }

    fn grab(&self, py: Python<'_>) -> PyResult<bool> {
        self.with_core(py, |core| {
            core.consume()
                .map(|frame| frame.is_some())
                .map_err(runtime_error)
        })
    }

    fn seek(&self, py: Python<'_>, target_sec: f64) -> PyResult<bool> {
        if !target_sec.is_finite() || target_sec < 0.0 {
            return Err(PyValueError::new_err(
                "seek time must be finite and non-negative",
            ));
        }
        self.with_core(py, |core| core.seek(target_sec).map_err(runtime_error))
    }

    #[getter]
    fn position(&self, py: Python<'_>) -> PyResult<Option<f64>> {
        self.with_core(py, |core| Ok(core.position))
    }

    fn resize(&self, py: Python<'_>, cols: u32, rows: u32) -> PyResult<()> {
        dimensions(cols, rows)?;
        self.with_core(py, |core| {
            core.cols = cols;
            core.rows = rows;
            core.scaler = None;
            core.scale_source = None;
            Ok(())
        })
    }

    fn set_skip_gray(&self, py: Python<'_>, skip: bool) -> PyResult<()> {
        self.with_core(py, |core| {
            core.skip_gray = skip;
            Ok(())
        })
    }

    fn release(&self, py: Python<'_>) -> PyResult<()> {
        py.allow_threads(|| {
            let mut guard = self
                .inner
                .lock()
                .map_err(|_| PyRuntimeError::new_err("decoder lock poisoned"))?;
            drop(guard.take());
            Ok(())
        })
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.release(py)
    }
    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }
    fn __exit__(
        &self,
        py: Python<'_>,
        _ty: PyObject,
        _value: PyObject,
        _traceback: PyObject,
    ) -> PyResult<()> {
        self.release(py)
    }
}
