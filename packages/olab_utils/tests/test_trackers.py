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


def test_real_opencv_object_trackers_resolve_without_error():
    """Sanity check against whatever OpenCV is actually installed in this environment."""
    assert set(olab_utils.OPENCV_OBJECT_TRACKERS.keys()) == {
        "csrt", "kcf", "boosting", "mil", "tld", "medianflow", "mosse",
    }
    for factory in olab_utils.OPENCV_OBJECT_TRACKERS.values():
        assert callable(factory)
