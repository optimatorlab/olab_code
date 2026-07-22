"""Covers olab_utils._resolveArucoDictAndParams()/arucoDetectMarkers()'s
OpenCV-5-compatible design (issue #9) -- requires the modern cv2.aruco API
(getPredefinedDictionary()/DetectorParameters()/ArucoDetector), since
opencv-contrib-python>=4.10.0 is already both packages' declared minimum
and the deprecated Dictionary_get()/DetectorParameters_create()/
detectMarkers() free-function API no longer exists at all on OpenCV 5.x."""

import types

import numpy as np
import pytest

import olab_utils


def test_resolve_aruco_dict_and_params_prefers_modern_api_when_present():
    """OpenCV >=4.7-style cv2.aruco: getPredefinedDictionary()/DetectorParameters()."""
    cv2_module = types.SimpleNamespace(
        aruco=types.SimpleNamespace(
            getPredefinedDictionary=lambda dictID: ('modern-dict', dictID),
            DetectorParameters=lambda: 'modern-params',
        )
    )

    (d, p) = olab_utils._resolveArucoDictAndParams('DICT_4X4_50', cv2_module=cv2_module)
    assert d == ('modern-dict', 'DICT_4X4_50')
    assert p == 'modern-params'


def test_resolve_aruco_dict_and_params_raises_when_cv2_lacks_modern_api():
    """
    OpenCV <4.7-style cv2.aruco (only the removed-in-5.x
    Dictionary_get()/DetectorParameters_create() API) must fail loud
    (AttributeError), not silently fall back to the wrong API shape --
    opencv-contrib-python>=4.10.0 is the declared minimum, so this should
    never happen for a correctly-installed environment.
    """
    cv2_module = types.SimpleNamespace(
        aruco=types.SimpleNamespace(
            Dictionary_get=lambda dictID: ('deprecated-dict', dictID),
            DetectorParameters_create=lambda: 'deprecated-params',
        )
    )

    with pytest.raises(AttributeError):
        olab_utils._resolveArucoDictAndParams('DICT_4X4_50', cv2_module=cv2_module)


def test_resolve_aruco_dict_and_params_does_not_use_cv2_version_string():
    """
    OpenCV 5.0 is major=5, minor=0 -- a `minor >= 7` version-string check
    (the bug this replaces) would wrongly pick the deprecated branch, which
    doesn't exist in cv2 5.x. Simulate a modern-API-shaped cv2.aruco with a
    5.0.0 __version__ string to confirm resolution is feature-detected, not
    version-parsed.
    """
    cv2_module = types.SimpleNamespace(
        __version__='5.0.0',
        aruco=types.SimpleNamespace(
            getPredefinedDictionary=lambda dictID: ('modern-dict', dictID),
            DetectorParameters=lambda: 'modern-params',
        )
    )

    (d, p) = olab_utils._resolveArucoDictAndParams('DICT_4X4_50', cv2_module=cv2_module)
    assert d == ('modern-dict', 'DICT_4X4_50')
    assert p == 'modern-params'


def test_real_opencv_aruco_dict_and_params_resolve_without_error():
    """Sanity check against whatever OpenCV is actually installed in this environment."""
    (d, p) = olab_utils._resolveArucoDictAndParams(olab_utils.ARUCO_DICT['DICT_4X4_50']['dict'])
    assert d is not None
    assert p is not None


# ─── arucoDetectMarkers()'s detector= caching contract ─────────────────────

class _FakeArucoDetector:
    def __init__(self):
        self.detectMarkersCallCount = 0

    def detectMarkers(self, img):
        self.detectMarkersCallCount += 1
        return ([], None, [])


def test_arucoDetectMarkers_uses_given_detector_without_constructing_one(monkeypatch):
    """A caller-supplied `detector=` must be used directly -- no new
    ArucoDetector should be constructed (this is the whole point of
    _Aruco caching self.arucoDetector once instead of rebuilding it every
    detection cycle)."""
    fake = _FakeArucoDetector()

    def _explode(*args, **kwargs):
        raise AssertionError('ArucoDetector should not be constructed when detector= is given')

    import cv2
    monkeypatch.setattr(cv2.aruco, 'ArucoDetector', _explode)

    img = np.zeros((10, 10), dtype='uint8')
    olab_utils.arucoDetectMarkers(img, arucoDict=None, arucoParams=None, detector=fake)
    assert fake.detectMarkersCallCount == 1


def test_arucoDetectMarkers_builds_a_detector_when_none_given():
    """Backward-compatible default path for callers like countArucoInImage()
    that don't have a cached detector to pass in."""
    real_dict, real_params = olab_utils._resolveArucoDictAndParams(olab_utils.ARUCO_DICT['DICT_4X4_50']['dict'])
    img = np.zeros((10, 10), dtype='uint8')

    (corners, ids, rejected, centers, rotations) = olab_utils.arucoDetectMarkers(img, real_dict, real_params)
    assert list(corners) == []


def test_real_arucoDetectMarkers_end_to_end_on_blank_image_does_not_raise():
    """Real end-to-end check against the actual installed OpenCV -- this is
    the check that would have caught issue #9's `arucoDetectMarkers()` gap
    (cv2.aruco.detectMarkers() removed in OpenCV 5.x) had it existed before
    manual testing found it."""
    import cv2
    (real_dict, real_params) = olab_utils._resolveArucoDictAndParams(olab_utils.ARUCO_DICT['DICT_4X4_50']['dict'])
    detector = cv2.aruco.ArucoDetector(real_dict, real_params)
    img = np.zeros((480, 640), dtype='uint8')

    (corners, ids, rejected, centers, rotations) = olab_utils.arucoDetectMarkers(
        img, real_dict, real_params, detector=detector)
    assert list(corners) == []
