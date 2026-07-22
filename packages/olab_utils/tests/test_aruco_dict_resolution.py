"""Covers both OpenCV cv2.aruco API shapes without needing both OpenCV
versions installed -- see olab_utils._resolveArucoDictAndParams()'s
capability-detection design (replaced a cv2.__version__ string-parsing
guess that broke outright on OpenCV 5.x, see issue #9)."""

import types

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


def test_resolve_aruco_dict_and_params_falls_back_to_deprecated_api():
    """Pre-4.7-style cv2.aruco: only Dictionary_get()/DetectorParameters_create()."""
    cv2_module = types.SimpleNamespace(
        aruco=types.SimpleNamespace(
            Dictionary_get=lambda dictID: ('deprecated-dict', dictID),
            DetectorParameters_create=lambda: 'deprecated-params',
        )
    )

    (d, p) = olab_utils._resolveArucoDictAndParams('DICT_4X4_50', cv2_module=cv2_module)
    assert d == ('deprecated-dict', 'DICT_4X4_50')
    assert p == 'deprecated-params'


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
