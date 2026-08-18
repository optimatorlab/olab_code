# _OmvHelper -- shared envelope encode/decode + channel-publish primitives
# for olab_camera OpenMV profile scripts.
#
# This file is a text asset, not imported by CPython. render_script()
# concatenates its raw text ahead of a profile body, so every profile shares
# exactly one top-level name (_OmvHelper) as its collision surface with
# custom-script authors -- see docs/usage_guide.md's OpenMV section for the
# reserved-name rule this implies for hand-assembled scripts.
#
# `time` and `ujson` (used below) are imported by the profile body that
# follows this asset in the concatenated script -- see render_script()'s
# fixed layout. Referencing them here is safe: Python/MicroPython resolve
# global names at call time, not at class-definition time, so this class
# body only needs those names bound in the shared module namespace by the
# time encode_envelope()/publish() actually run, not before.
#
# Design correction (review round 3): the config/health *telemetry* channel
# (this class) and the standard OpenMV *frame* stream
# (device.streaming()/read_frame(), driven by csi0.snapshot() in the
# profile body -- see genx_histogram_preview.py, whose GENX320 sensor-control
# calls are confirmed against OpenMV's own published docs as of review round
# 4) are two independent, unrelated mechanisms -- the frame stream does not
# depend on this new channel system at all. _channel_write() is
# intentionally NOT a real device call yet: the MicroPython-side
# channel-registration/write primitive for this new OpenMV Protocol V2 is
# not yet confirmed against real firmware (hardware bring-up is out of
# scope this round -- see docs/plans/olab_camera_openmv_support_plan.md step
# 5). publish() therefore treats a channel-write failure as non-fatal,
# best-effort telemetry: it prints the failure (visible to the host via
# OpenMVDevice.readStdout(), a confirmed-real API) and continues, rather
# than either fabricating a device API call or letting an unconfirmed
# telemetry feature block the confirmed frame stream. See
# test_openmv_profiles.py's rendered-script structural-execution test.
#
# Rate-limiting correction (review round 5): the frame loop calls publish()
# once per frame -- at the documented 20-350 FPS, catching-and-printing on
# *every* call would allocate an exception and flood stdout/USB on every
# single frame, competing with the host frame protocol and potentially
# making the preview unusable. publish() now short-circuits after the
# first failure: it prints exactly one warning, then permanently disables
# further channel-write attempts for the rest of this script session (a
# class-level flag, since _OmvHelper is the one shared instance across the
# whole script) -- no repeated exception allocation, no repeated output.

class _OmvHelper:
    SCHEMA_VERSION = 1
    _telemetry_disabled = False

    @staticmethod
    def encode_envelope(profile_id, kind, payload, seq=0):
        envelope = {
            "schema_version": _OmvHelper.SCHEMA_VERSION,
            "profile_id": profile_id,
            "device_seq": seq,
            "device_time_ms": time.ticks_ms(),
            "kind": kind,
            "payload": payload,
        }
        return ujson.dumps(envelope)

    @staticmethod
    def publish(channel, profile_id, kind, payload, seq=0):
        # Best-effort telemetry -- never let a channel-write failure stop
        # the profile's frame stream, and never retry after the first
        # failure (see module note above).
        if _OmvHelper._telemetry_disabled:
            return
        try:
            data = _OmvHelper.encode_envelope(profile_id, kind, payload, seq=seq)
            _OmvHelper._channel_write(channel, data)
        except Exception as e:
            _OmvHelper._telemetry_disabled = True
            print("_OmvHelper.publish failed (non-fatal, telemetry disabled for this session):", e)

    @staticmethod
    def _channel_write(channel, data):
        # Not yet implemented on purpose -- see module note above. Confirm
        # the real MicroPython-side channel-write primitive against OpenMV
        # firmware during hardware bring-up (step 5) and fill this in.
        raise NotImplementedError(
            "_OmvHelper._channel_write: the on-device channel-write primitive "
            "is not yet confirmed against real OpenMV firmware (hardware "
            "bring-up, step 5, is out of scope this round)")
