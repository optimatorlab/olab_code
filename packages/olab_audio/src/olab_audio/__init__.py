"""olab_audio - audio device I/O (Mic/Speaker/recording), device enumeration,
and PulseAudio/PipeWire port control, plus an optional `analysis` extra with
a DSP/teaching/research toolkit (Wave/Spectrogram/Spectrum, tone/chirp/pitch
synthesis, trim/normalize, matplotlib plotting) and a `resample` extra for
lightweight cross-rate PCM conversion.

Core (this module) only needs pyaudio, pulsectl, and numpy -- it does not
import `olab_audio.analysis` or the `resample` extra's `soxr` backend at
all unless a caller actually needs cross-rate recording or the analysis
toolkit. See README.md for the extras split.

**API parity with the original `ub_audio` module**: `resample()` (the
function -- not a submodule; see `_resample.py`) is always available at
package root, matching every core symbol. Every `analysis`-extra symbol
(`Wave`, `Spectrum`, `Spectrogram`, `createTone`, `createChirp`,
`createPitch`, `trim`, `read_wave`, `read_wave_librosa`, `decorate`,
`legend`, `remove_from_legend`, `pitch_map`, `stft`, `find_index`,
`ftt_freq`, `spectrum`, `peaks`, `underride`, `truncate`, `zero_pad`,
`normalize`, `unbias`) is *also* available directly at `olab_audio.<name>`
via lazy module `__getattr__` (PEP 562) -- `olab_audio.analysis` is only
actually imported the first time one of those names is accessed, so a
core-only install never pays for it, but a caller with `[analysis]`
installed sees a single flat namespace, same as the original module.
Accessing one of these names without the `analysis` extra installed raises
a clear `AttributeError` naming the install command, not a confusing
`ImportError` from deep inside `analysis.py`.

Classes:
    Mic: Microphone capture -- device-safe enumeration, recording, dB level.
    Speaker: Simple playback.
    Recording / Recording_bytes / Recording_np: In-progress recording buffers.

Functions:
    get_input_devices / get_output_devices / get_connected_devices: Device
        enumeration (ALSA pseudo-device plugins filtered out -- opening one
        as a capture stream can segfault the process; see device.py).
    get_default_source_ports / set_default_source_port: PulseAudio/PipeWire
        port control for hardware exposing multiple mutually-exclusive
        ports on one physical source (e.g. a laptop's internal/jack mic).
    reinit_audio: Force PortAudio to re-probe hardware.
    terminate: Release PortAudio system resources.
    resample: Cross-rate PCM conversion (needs the `resample` extra to
        actually convert; same-rate calls are always a no-op -- see
        _resample.py).
"""

from importlib.metadata import PackageNotFoundError, version

try:
	__version__ = version("olab-audio")
except PackageNotFoundError:
	__version__ = "0.0.0"

from ._constants import CHANNELS, CHUNK, FORMAT, ONE_OVER_MAX_INT16, SAMPLERATE, SPEAKER_FORMAT
from ._resample import StreamResampler, resample
from ._util import bytes2np, convert_to_db, defaultFromNone, np2bytes, np2np
from .device import (
	audio,
	get_connected_devices,
	get_default_source_ports,
	get_input_devices,
	get_output_devices,
	reinit_audio,
	set_default_source_port,
	terminate,
)
from .mic import Mic
from .recording import Recording, Recording_bytes, Recording_np, append, saveAudio
from .speaker import Speaker

# Names that live in olab_audio.analysis (the `analysis` extra) but are
# also exposed at package root -- see the module docstring above and
# __getattr__ below.
_ANALYSIS_NAMES = frozenset({
	"pitch_map",
	"createPitch",
	"createTone",
	"createChirp",
	"decorate",
	"legend",
	"remove_from_legend",
	"read_wave",
	"read_wave_librosa",
	"trim",
	"underride",
	"truncate",
	"zero_pad",
	"normalize",
	"unbias",
	"stft",
	"find_index",
	"ftt_freq",
	"spectrum",
	"peaks",
	"Spectrogram",
	"Spectrum",
	"Wave",
})


def __getattr__(name):
	if name in _ANALYSIS_NAMES:
		try:
			from . import analysis
		except ImportError as exc:
			raise AttributeError(
				f"olab_audio.{name} needs the 'analysis' extra. "
				f"Install with: pip install olab-audio[analysis]"
			) from exc
		return getattr(analysis, name)
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
	return sorted(set(globals()) | _ANALYSIS_NAMES)


__all__ = [
	"__version__",
	"CHANNELS",
	"CHUNK",
	"FORMAT",
	"ONE_OVER_MAX_INT16",
	"SAMPLERATE",
	"SPEAKER_FORMAT",
	"audio",
	"append",
	"bytes2np",
	"convert_to_db",
	"defaultFromNone",
	"get_connected_devices",
	"get_default_source_ports",
	"get_input_devices",
	"get_output_devices",
	"Mic",
	"np2bytes",
	"np2np",
	"reinit_audio",
	"Recording",
	"Recording_bytes",
	"Recording_np",
	"resample",
	"saveAudio",
	"set_default_source_port",
	"Speaker",
	"StreamResampler",
	"terminate",
	*sorted(_ANALYSIS_NAMES),
]
