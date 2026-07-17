from __future__ import annotations

import asyncio
import csv
import io
import json
from importlib.resources import files

from olab_rf.config import OlabRfConfig
from olab_rf.services.checks import environment_check
from olab_rf.services.session_manager import SessionManager


def create_app(
    manager: SessionManager | None = None,
    config: OlabRfConfig | None = None,
    config_path: str | None = None,
):
    """Create the optional FastAPI demo app.

    The web layer is an adapter over ``SessionManager``. RF workflow decisions
    belong in the Python library; route handlers should translate HTTP payloads
    into Python API calls and return model/dict output.
    """

    try:
        from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install olab_rf with the web extra to run the demo server") from exc

    config = config or OlabRfConfig.default()
    if manager is None:
        manager = SessionManager.from_config(config)
    elif manager.config is None:
        manager.config = config
    app = FastAPI(title="olab_rf demo")

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict[str, object]:
        return manager.status_dict()

    @app.get("/api/session")
    def session() -> dict[str, object] | None:
        return manager.session_dict()

    @app.get("/api/config")
    def api_config() -> dict[str, object]:
        return config.to_dict()

    @app.get("/api/frequency/catalog")
    def frequency_catalog() -> dict[str, object]:
        return manager.frequency_catalog_dict()

    @app.get("/api/frequency/scan")
    def frequency_scan_status() -> dict[str, object] | None:
        status = manager.poll_frequency_scan()
        return status.to_dict() if status else None

    @app.post("/api/frequency/scan")
    def start_frequency_scan(payload: dict[str, object]) -> dict[str, object]:
        try:
            backend = str(payload.get("backend") or "rtl_power")
            range_id = str(payload.get("range_id") or "custom")
            use_catalog_range = (
                range_id != "custom" and manager.frequency_catalog.range_by_id(range_id) is not None
            )
            status = manager.start_range_scan(
                path=str(payload["path"]) if payload.get("path") else None,
                backend=backend,
                range_id=range_id,
                min_freq_hz=None if use_catalog_range else _optional_int(payload.get("min_freq_hz")),
                max_freq_hz=None if use_catalog_range else _optional_int(payload.get("max_freq_hz")),
                step_hz=_optional_int(payload.get("bin_size_hz")),
                duration_sec=float(payload["duration_sec"]),
                channel_frequencies_hz=[
                    int(item) for item in payload.get("channel_frequencies_hz") or []
                ],
                channel_width_hz=_optional_int(payload.get("channel_width_hz")),
                gain_db=_optional_float(payload.get("gain_db")),
                sample_rate_hz=_optional_int(payload.get("sample_rate_hz")),
                resume_previous=bool(payload.get("resume_previous", False)),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return manager.frequency_scan_dict() or status.to_dict()

    @app.post("/api/frequency/baseline")
    def start_frequency_baseline(payload: dict[str, object]) -> dict[str, object]:
        try:
            backend = str(payload.get("backend") or "rtl_power")
            range_id = str(payload.get("range_id") or "custom")
            use_catalog_range = (
                range_id != "custom" and manager.frequency_catalog.range_by_id(range_id) is not None
            )
            status = manager.capture_range_baseline(
                path=str(payload["path"]) if payload.get("path") else None,
                backend=backend,
                range_id=range_id,
                min_freq_hz=None if use_catalog_range else _optional_int(payload.get("min_freq_hz")),
                max_freq_hz=None if use_catalog_range else _optional_int(payload.get("max_freq_hz")),
                step_hz=_optional_int(payload.get("bin_size_hz")),
                duration_sec=float(payload["duration_sec"]),
                channel_frequencies_hz=[
                    int(item) for item in payload.get("channel_frequencies_hz") or []
                ],
                channel_width_hz=_optional_int(payload.get("channel_width_hz")),
                gain_db=_optional_float(payload.get("gain_db")),
                sample_rate_hz=_optional_int(payload.get("sample_rate_hz")),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return manager.frequency_scan_dict() or status.to_dict()

    @app.get("/api/frequency/scans")
    def persisted_frequency_scans(limit: int = 50) -> list[dict[str, object]]:
        if not manager.history:
            return []
        return manager.history.list_frequency_scans(limit=limit)

    @app.get("/api/frequency/scans/export.json")
    def export_frequency_scans_json(limit: int = 1000):
        scans = manager.history.list_frequency_scans(limit=limit) if manager.history else []
        return Response(
            content=json.dumps(scans, indent=2, sort_keys=True),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=olab_rf_frequency_scans.json"},
        )

    @app.get("/api/frequency/scans/export.csv")
    def export_frequency_scans_csv(limit: int = 1000):
        scans = manager.history.list_frequency_scans(limit=limit) if manager.history else []
        output = io.StringIO()
        fieldnames = [
            "scan_id",
            "status",
            "started_at",
            "stopped_at",
            "min_freq_hz",
            "max_freq_hz",
            "best_frequency_hz",
            "best_label",
            "candidates",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for scan in scans:
            request = scan.get("request") or {}
            best = scan.get("best_candidate") or {}
            writer.writerow(
                {
                    "scan_id": scan.get("scan_id", ""),
                    "status": scan.get("status", ""),
                    "started_at": scan.get("started_at", ""),
                    "stopped_at": scan.get("stopped_at", ""),
                    "min_freq_hz": request.get("min_freq_hz", ""),
                    "max_freq_hz": request.get("max_freq_hz", ""),
                    "best_frequency_hz": best.get("matched_frequency_hz")
                    or best.get("frequency_hz", ""),
                    "best_label": best.get("label", ""),
                    "candidates": len(scan.get("candidates") or []),
                }
            )
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=olab_rf_frequency_scans.csv"},
        )

    @app.get("/api/check")
    def api_check() -> dict[str, object]:
        skip_probe = None
        if manager.status.process_running:
            skip_probe = f"{manager.status.mode} session is running"
        return environment_check(
            tool_paths={
                "readsb": config.decoders["readsb"].path,
                "rtl_ais": config.decoders["rtl_ais"].path,
                "rtl_power": config.decoders["rtl_power"].path,
                "rtl_fm": config.decoders["rtl_fm"].path,
            },
            sdrtrunk_paths=config.sdrtrunk.to_dict(),
            skip_rtlsdr_probe_reason=skip_probe,
            config_path=config_path,
        )

    @app.get("/api/spectrum/presets")
    def spectrum_presets() -> list[dict[str, object]]:
        return _catalog_ranges_as_legacy_presets(manager)

    @app.get("/api/spectrum")
    def spectrum() -> dict[str, object]:
        manager.ingest_spectrum_stdout()
        return _enrich_spectrum_payload(manager, manager.spectrum_dict())

    @app.post("/api/spectrum/watch")
    def set_spectrum_watch(payload: dict[str, object]) -> dict[str, object]:
        frequency_hz = _optional_int(payload.get("frequency_hz"))
        if frequency_hz is None:
            raise HTTPException(status_code=400, detail="frequency_hz is required")
        return manager.set_watch_frequency(
            frequency_hz,
            modulation=str(payload.get("modulation") or ""),
        )

    @app.get("/api/spectrum/events")
    def persisted_spectrum_events(limit: int = 200) -> list[dict[str, object]]:
        return _enriched_spectrum_events(manager, limit=limit)

    @app.get("/api/spectrum/events/export.json")
    def export_spectrum_events_json(limit: int = 1000):
        events = _enriched_spectrum_events(manager, limit=limit)
        return Response(
            content=json.dumps(events, indent=2, sort_keys=True),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=olab_rf_spectrum_events.json"},
        )

    @app.get("/api/spectrum/events/export.csv")
    def export_spectrum_events_csv(limit: int = 1000):
        events = _enriched_spectrum_events(manager, limit=limit)
        output = io.StringIO()
        fieldnames = [
            "captured_at",
            "center_hz",
            "label",
            "annotation_label",
            "preset_label",
            "modulation",
            "power_db",
            "noise_floor_db",
            "margin_db",
            "threshold_db",
            "preset_id",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow({name: event.get(name, "") for name in fieldnames})
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=olab_rf_spectrum_events.csv"},
        )

    @app.get("/api/spectrum/favorites")
    def frequency_favorites() -> list[dict[str, object]]:
        return manager.list_frequency_favorites()

    @app.post("/api/spectrum/favorites")
    def save_frequency_favorite(payload: dict[str, object]) -> dict[str, object]:
        frequency_hz = _optional_int(payload.get("frequency_hz"))
        if frequency_hz is None:
            raise HTTPException(status_code=400, detail="frequency_hz is required")
        return manager.save_frequency_favorite(
            frequency_hz=frequency_hz,
            modulation=str(payload.get("modulation") or "nfm"),
            label=str(payload.get("label") or "") or None,
        )

    @app.delete("/api/spectrum/favorites/{frequency_hz}")
    def delete_frequency_favorite(frequency_hz: int) -> dict[str, object]:
        return manager.delete_frequency_favorite(frequency_hz)

    @app.get("/api/tracks")
    def tracks() -> list[dict[str, object]]:
        manager.poll()
        return [track.to_dict() for track in manager.track_store.list()]

    @app.get("/api/tracks/{track_id}")
    def track(track_id: str) -> dict[str, object]:
        found = manager.track_store.get(track_id)
        if not found:
            raise HTTPException(status_code=404, detail="track not found")
        return found.to_dict()

    @app.get("/api/tracks/{track_id}/trail")
    def trail(track_id: str) -> list[dict[str, object]]:
        if not manager.track_store.get(track_id):
            raise HTTPException(status_code=404, detail="track not found")
        return manager.track_store.trail_for(track_id)

    @app.post("/api/session/start")
    def start(payload: dict[str, object] | None = None) -> dict[str, object]:
        mode = (payload or {}).get("mode", "replay")
        try:
            if mode == "replay":
                return manager.start_replay().to_dict()
            if mode == "adsb":
                return manager.start_adsb().to_dict()
            if mode == "ais":
                return manager.start_ais().to_dict()
            if mode == "listen":
                payload = payload or {}
                return manager.start_listen(
                    frequency_hz=_optional_int(payload.get("frequency_hz")),
                    modulation=str(payload.get("modulation") or ""),
                ).to_dict()
            if mode == "spectrum":
                payload = payload or {}
                return manager.start_spectrum(
                    preset_id=str(payload.get("preset_id", "noaa_weather")),
                    start_hz=_optional_int(payload.get("start_hz")),
                    stop_hz=_optional_int(payload.get("stop_hz")),
                    bin_hz=_optional_int(payload.get("bin_hz")),
                    interval_s=int(payload.get("interval_s", 2)),
                    gain_db=_optional_float(payload.get("gain_db")),
                    sample_rate_hz=_optional_int(payload.get("sample_rate_hz")),
                    threshold_db=float(payload.get("threshold_db", 12)),
                ).to_dict()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")

    @app.post("/api/session/mode")
    def set_mode(payload: dict[str, object] | None = None) -> dict[str, object]:
        return start(payload)

    @app.post("/api/session/stop")
    def stop() -> dict[str, object]:
        manager.stop()
        return manager.status_dict()

    async def track_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                manager.poll()
                await websocket.send_json(
                    {
                        "status": manager.status_dict(),
                        "session": manager.session_dict(),
                        "tracks": [track.to_dict() for track in manager.track_store.list()],
                        "spectrum": _enrich_spectrum_payload(manager, manager.spectrum_dict()),
                        "frequency_scan": manager.frequency_scan_dict(),
                    }
                )
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return

    track_stream.__annotations__["websocket"] = WebSocket
    app.websocket("/ws/tracks")(track_stream)

    static_dir = files("olab_rf").joinpath("web/static")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _catalog_ranges_as_legacy_presets(manager: SessionManager) -> list[dict[str, object]]:
    presets = []
    for frequency_range in manager.catalog_with_favorites().ranges:
        presets.append(
            {
                "preset_id": frequency_range.id,
                "id": frequency_range.id,
                "label": frequency_range.label,
                "modulation": frequency_range.default_modulation or "custom",
                "ranges": [
                    {
                        "start_hz": frequency_range.min_freq_hz,
                        "stop_hz": frequency_range.max_freq_hz,
                        "bin_hz": frequency_range.default_bin_size_hz or 100_000,
                    }
                ],
                "annotations": [
                    {
                        "label": channel.label,
                        "modulation": channel.modulation
                        or frequency_range.default_modulation
                        or "custom",
                        "center_hz": channel.frequency_hz,
                        "start_hz": None,
                        "stop_hz": None,
                        "tolerance_hz": frequency_range.default_bin_size_hz or 2_500,
                    }
                    for channel in frequency_range.channels
                ],
            }
        )
    presets.append(
        {
            "preset_id": "custom",
            "id": "custom",
            "label": "Custom",
            "modulation": "custom",
            "ranges": [{"start_hz": 88_000_000, "stop_hz": 108_000_000, "bin_hz": 100_000}],
            "annotations": [],
        }
    )
    return presets


def _enriched_spectrum_events(manager: SessionManager, limit: int) -> list[dict[str, object]]:
    events = []
    for event in manager.persisted_spectrum_events(limit=limit):
        events.append(_enrich_frequency_payload(manager, dict(event), frequency_key="center_hz"))
    return events


def _enrich_spectrum_payload(
    manager: SessionManager,
    payload: dict[str, object],
) -> dict[str, object]:
    enriched = dict(payload)
    enriched["events"] = [
        _enrich_frequency_payload(manager, dict(event), frequency_key="center_hz")
        for event in payload.get("events", [])
        if isinstance(event, dict)
    ]
    return enriched


def _enrich_frequency_payload(
    manager: SessionManager,
    payload: dict[str, object],
    *,
    frequency_key: str,
) -> dict[str, object]:
    frequency_hz = int(payload[frequency_key])
    match = manager.catalog_with_favorites().match_frequency(frequency_hz)
    payload["label"] = match.label
    payload["annotation_label"] = match.channel_label or match.range_label or ""
    payload["range_label"] = match.range_label or ""
    payload["preset_label"] = match.range_label or str(payload.get("preset_id") or "")
    payload["modulation"] = match.modulation or ""
    return payload
