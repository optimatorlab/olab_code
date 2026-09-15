from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from olab_rf.models import ReceiverConfig


@dataclass(slots=True)
class HistoryConfig:
    sqlite_path: str = "data/olab_rf.sqlite"
    trail_retention_hours: int = 6
    raw_capture_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "sqlite_path": self.sqlite_path,
            "trail_retention_hours": self.trail_retention_hours,
            "raw_capture_enabled": self.raw_capture_enabled,
        }


@dataclass(slots=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8787

    def to_dict(self) -> dict[str, object]:
        return {"host": self.host, "port": self.port}


@dataclass(slots=True)
class DecoderConfig:
    """Decoder invocation settings.

    ``fir_size`` and ``atan_math`` are typed rather than left to ``args`` because
    they need validation and read-only display in operator UIs. ``args`` remains
    an escape hatch for the small set of flags that cannot affect pipeline
    correctness; it is validated at load, never silently dropped.
    """

    path: str
    args: list[str] = field(default_factory=list)
    fir_size: int | None = None
    atan_math: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "args": self.args,
            "fir_size": self.fir_size,
            "atan_math": self.atan_math,
        }


@dataclass(slots=True)
class SdrTrunkConfig:
    """Local SDRTrunk paths used only for readiness checks in Slice 0."""

    launcher_path: str | None = None
    java_path: str | None = None
    working_directory: str | None = None
    profile_path: str | None = None
    jmbe_path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "launcher_path": self.launcher_path,
            "java_path": self.java_path,
            "working_directory": self.working_directory,
            "profile_path": self.profile_path,
            "jmbe_path": self.jmbe_path,
        }


@dataclass(slots=True)
class OlabRfConfig:
    receivers: list[ReceiverConfig] = field(default_factory=list)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    web: WebConfig = field(default_factory=WebConfig)
    decoders: dict[str, DecoderConfig] = field(default_factory=dict)
    sdrtrunk: SdrTrunkConfig = field(default_factory=SdrTrunkConfig)
    digital_system_catalog: dict[str, Any] = field(default_factory=dict)
    frequency_catalog: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "receivers": [receiver.to_dict() for receiver in self.receivers],
            "history": self.history.to_dict(),
            "web": self.web.to_dict(),
            "decoders": {
                name: decoder.to_dict() for name, decoder in self.decoders.items()
            },
            "sdrtrunk": self.sdrtrunk.to_dict(),
            "digital_system_catalog": self.digital_system_catalog,
            "frequency_catalog": self.frequency_catalog,
        }

    @classmethod
    def default(cls) -> OlabRfConfig:
        return cls(
            receivers=[ReceiverConfig(id="rtlsdr-1")],
            decoders={
                "readsb": DecoderConfig(path="readsb"),
                "rtl_ais": DecoderConfig(path="rtl_ais"),
                "rtl_power": DecoderConfig(path="rtl_power"),
                "rtl_fm": DecoderConfig(path="rtl_fm"),
            },
        )


def load_config(path: str | Path | None = None) -> OlabRfConfig:
    if path is None:
        return OlabRfConfig.default()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return config_from_dict(payload)


def config_from_dict(payload: dict[str, Any]) -> OlabRfConfig:
    default = OlabRfConfig.default()
    receiver_payload = payload.get("receivers")
    receivers = (
        [ReceiverConfig.from_dict(item) for item in receiver_payload]
        if receiver_payload is not None
        else default.receivers
    )
    history_payload = payload.get("history") or {}
    web_payload = payload.get("web") or {}
    decoder_payload = payload.get("decoders") or {}
    sdrtrunk_payload = payload.get("sdrtrunk") or {}
    decoders = dict(default.decoders)
    decoders.update({
        name: _decoder_config(name, data) for name, data in decoder_payload.items()
    })
    return OlabRfConfig(
        receivers=receivers,
        history=HistoryConfig(
            sqlite_path=history_payload.get("sqlite_path", default.history.sqlite_path),
            trail_retention_hours=int(
                history_payload.get(
                    "trail_retention_hours",
                    default.history.trail_retention_hours,
                )
            ),
            raw_capture_enabled=bool(
                history_payload.get("raw_capture_enabled", default.history.raw_capture_enabled)
            ),
        ),
        web=WebConfig(
            host=web_payload.get("host", default.web.host),
            port=int(web_payload.get("port", default.web.port)),
        ),
        decoders=decoders,
        sdrtrunk=SdrTrunkConfig(
            launcher_path=sdrtrunk_payload.get("launcher_path"),
            java_path=sdrtrunk_payload.get("java_path"),
            working_directory=sdrtrunk_payload.get("working_directory"),
            profile_path=sdrtrunk_payload.get("profile_path"),
            jmbe_path=sdrtrunk_payload.get("jmbe_path"),
        ),
        digital_system_catalog=dict(payload.get("digital_system_catalog") or {}),
        frequency_catalog=dict(payload.get("frequency_catalog") or {}),
    )


def _decoder_config(name: str, data: dict) -> DecoderConfig:
    """Build one decoder config, validating rtl_fm passthrough at load time.

    Validation happens here so a bad value fails when the config is read, with the
    offending token named, rather than producing a process that runs and records
    nothing.
    """
    args = list(data.get("args") or [])
    fir_size = data.get("fir_size")
    atan_math = data.get("atan_math")
    if name == "rtl_fm":
        from olab_rf.decoders.rtl_fm import (
            ATAN_MATHS,
            FIR_SIZES,
            RtlFmArgumentError,
            validate_rtl_fm_passthrough,
        )

        try:
            validate_rtl_fm_passthrough(args)
        except RtlFmArgumentError as exc:
            raise ValueError(f"decoders.{name}.args: {exc}") from exc
        if fir_size is not None and int(fir_size) not in FIR_SIZES:
            raise ValueError(f"decoders.{name}.fir_size must be one of {list(FIR_SIZES)}")
        if atan_math is not None and atan_math not in ATAN_MATHS:
            raise ValueError(f"decoders.{name}.atan_math must be one of {list(ATAN_MATHS)}")
    return DecoderConfig(
        path=data.get("path", name),
        args=args,
        fir_size=int(fir_size) if fir_size is not None else None,
        atan_math=atan_math,
    )
