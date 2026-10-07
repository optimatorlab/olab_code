"""Covers both OpenCV tracker-factory API shapes without needing both
OpenCV versions installed -- see olab_utils._resolveTrackerFactory()'s
capability-detection design (replaced a cv2.__version__ string-parsing
guess that broke outright on OpenCV 5.x)."""

import types

import olab_utils


def _sentinel(name):
    def factory():
        return name

    return factory


def test_resolve_tracker_factory_prefers_legacy_when_present():
    """Post-4.5.1-style cv2: Boosting/TLD/MedianFlow/MOSSE live under cv2.legacy."""
    cv2_module = types.SimpleNamespace(
        TrackerCSRT_create=_sentinel("top-level-csrt"),
        legacy=types.SimpleNamespace(
            TrackerBoosting_create=_sentinel("legacy-boosting"),
        ),
    )

    assert olab_utils._resolveTrackerFactory("CSRT", cv2_module=cv2_module)() == "top-level-csrt"
    assert olab_utils._resolveTrackerFactory("Boosting", cv2_module=cv2_module)() == "legacy-boosting"


def test_resolve_tracker_factory_falls_back_to_top_level_without_legacy():
    """Pre-4.5.1-style cv2 (e.g. the Raspberry Pi's old v4.4.0): no cv2.legacy at all."""
    cv2_module = types.SimpleNamespace(
        TrackerCSRT_create=_sentinel("top-level-csrt"),
        TrackerBoosting_create=_sentinel("top-level-boosting"),
    )

    assert olab_utils._resolveTrackerFactory("CSRT", cv2_module=cv2_module)() == "top-level-csrt"
    assert olab_utils._resolveTrackerFactory("Boosting", cv2_module=cv2_module)() == "top-level-boosting"


def test_resolve_tracker_factory_falls_back_when_legacy_lacks_the_tracker():
    """cv2.legacy exists but doesn't have this particular tracker -- fall back, don't guess wrong."""
    cv2_module = types.SimpleNamespace(
        TrackerCSRT_create=_sentinel("top-level-csrt"),
        legacy=types.SimpleNamespace(),  # present, but empty
    )

    assert olab_utils._resolveTrackerFactory("CSRT", cv2_module=cv2_module)() == "top-level-csrt"


def test_resolve_tracker_factory_returns_none_when_neither_shape_has_it():
    """issue #70: plain opencv-python has no cv2.legacy at all and lacks the
    classic-tracker factories other than MIL -- this must return None (so
    _buildOpenCvObjectTrackers can omit the tracker) instead of raising
    AttributeError and crashing `import olab_utils` outright."""
    cv2_module = types.SimpleNamespace()  # no legacy, no top-level factory at all

    assert olab_utils._resolveTrackerFactory("CSRT", cv2_module=cv2_module) is None


def test_build_opencv_object_trackers_covers_all_known_trackers():
    cv2_module = types.SimpleNamespace(
        TrackerCSRT_create=_sentinel("csrt"),
        TrackerKCF_create=_sentinel("kcf"),
        TrackerMIL_create=_sentinel("mil"),
        legacy=types.SimpleNamespace(
            TrackerBoosting_create=_sentinel("boosting"),
            TrackerTLD_create=_sentinel("tld"),
            TrackerMedianFlow_create=_sentinel("medianflow"),
            TrackerMOSSE_create=_sentinel("mosse"),
        ),
    )

    trackers = olab_utils._buildOpenCvObjectTrackers(cv2_module=cv2_module)

    assert set(trackers.keys()) == {"csrt", "kcf", "boosting", "mil", "tld", "medianflow", "mosse"}
    for key, factory in trackers.items():
        assert factory() == key


def test_build_opencv_object_trackers_skips_unavailable_trackers():
    """issue #70's actual fix: on plain opencv-python (no cv2.legacy, only
    TrackerMIL_create at the top level), OPENCV_OBJECT_TRACKERS must end up
    with only 'mil' -- not raise, and not include keys for the 6 missing
    trackers."""
    cv2_module = types.SimpleNamespace(TrackerMIL_create=_sentinel("mil"))

    trackers = olab_utils._buildOpenCvObjectTrackers(cv2_module=cv2_module)

    assert set(trackers.keys()) == {"mil"}
    assert trackers["mil"]() == "mil"


def test_real_opencv_object_trackers_resolve_without_error():
    """Sanity check against whatever OpenCV is actually installed in this
    environment -- tolerant of either flavor (issue #70): 'mil' must always be
    present, every key must be one of the 7 known trackers, and every value
    must be callable. (Previously asserted all 7 were always present, which
    only held for opencv-contrib-python; plain opencv-python only has MIL.)"""
    known = {"csrt", "kcf", "boosting", "mil", "tld", "medianflow", "mosse"}

    assert "mil" in olab_utils.OPENCV_OBJECT_TRACKERS
    assert set(olab_utils.OPENCV_OBJECT_TRACKERS.keys()) <= known
    for factory in olab_utils.OPENCV_OBJECT_TRACKERS.values():
        assert callable(factory)
