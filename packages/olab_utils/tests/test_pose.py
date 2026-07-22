import math

import numpy as np
import cv2
import pytest

import olab_utils


CAMERA_MATRIX = np.array([[800.0, 0.0, 320.0],
                           [0.0, 800.0, 240.0],
                           [0.0, 0.0, 1.0]])
DIST_COEFFS = np.zeros(5)
TAG_SIZE = 0.2


def _project_synthetic_tag(rvec, tvec):
    half = TAG_SIZE / 2.0
    objectPoints = np.array([
        [-half, half, 0.0],
        [half, half, 0.0],
        [half, -half, 0.0],
        [-half, -half, 0.0],
    ], dtype=np.float64)
    imagePoints, _ = cv2.projectPoints(objectPoints, rvec, tvec, CAMERA_MATRIX, DIST_COEFFS)
    return imagePoints.reshape(4, 2), objectPoints


def test_findTagPose_recovers_head_on_pose():
    rvec_true = np.zeros((3, 1))
    tvec_true = np.array([0.0, 0.0, 1.5]).reshape(3, 1)
    corners, objectPoints = _project_synthetic_tag(rvec_true, tvec_true)

    # Default flags (cv2.SOLVEPNP_IPPE_SQUARE), matching docs/usage_guide.md's example.
    (ret, rvec, tvec) = olab_utils.findTagPose(objectPoints, corners, CAMERA_MATRIX, DIST_COEFFS)

    assert ret
    assert np.allclose(rvec.flatten(), rvec_true.flatten(), atol=1e-3)
    assert np.allclose(tvec.flatten(), tvec_true.flatten(), atol=1e-3)


def test_findTagPose_recovers_nonzero_yaw_pose():
    rvec_true = np.array([0.1, 0.5, 0.05]).reshape(3, 1)
    tvec_true = np.array([0.05, -0.02, 1.0]).reshape(3, 1)
    corners, objectPoints = _project_synthetic_tag(rvec_true, tvec_true)

    (ret, rvec, tvec) = olab_utils.findTagPose(objectPoints, corners, CAMERA_MATRIX, DIST_COEFFS)

    assert ret
    assert np.allclose(rvec.flatten(), rvec_true.flatten(), atol=1e-3)
    assert np.allclose(tvec.flatten(), tvec_true.flatten(), atol=1e-3)


def test_findTagPoseGlobal_and_inverse_round_trip_with_nonidentity_pose_and_extrinsics():
    # A non-identity vehicle attitude and a nonzero camera mount offset --
    # the identity case alone would not catch a body/optical frame-convention bug.
    cameraPose = {'position': (10.0, -5.0, 2.0), 'orientation': (0.1, -0.2, 1.0)}
    cameraExtrinsics = {'position': (0.1, 0.0, -0.05), 'orientation': (0.0, 0.5, 0.0)}

    rvec = np.array([0.05, 0.3, -0.1]).reshape(3, 1)
    tvec = np.array([0.2, -0.1, 2.0]).reshape(3, 1)

    (tagPos, tagRpy) = olab_utils.findTagPoseGlobal(cameraPose, rvec, tvec, cameraExtrinsics)
    tagPose = {'position': tagPos, 'orientation': tagRpy}
    (bodyPos, bodyRpy) = olab_utils.findCameraPoseGlobal(tagPose, rvec, tvec, cameraExtrinsics)

    assert np.allclose(bodyPos, cameraPose['position'], atol=1e-6)
    assert np.allclose(bodyRpy, cameraPose['orientation'], atol=1e-6)


@pytest.mark.parametrize('pitch_true', [math.pi / 2, -math.pi / 2])
def test_findTagPoseGlobal_round_trip_recovers_correct_rotation_matrix_at_gimbal_lock(pitch_true):
    '''
    At pitch = +/-90 degrees (gimbal lock), roll and yaw are not individually
    recoverable from a rotation matrix -- only their sum/difference is -- so
    the round trip is checked against the *rotation matrix*, not the raw
    (roll, pitch, yaw) scalars, which need not match the originals even
    though the matrix (and therefore the physical orientation) does.
    Reproduces the reviewer's finding: body RPY (0.4, +/-pi/2, -0.7),
    non-identity extrinsics, and a nonzero detection.
    '''
    cameraPose = {'position': (10.0, -5.0, 2.0), 'orientation': (0.4, pitch_true, -0.7)}
    cameraExtrinsics = {'position': (0.1, 0.0, -0.05), 'orientation': (0.0, 0.5, 0.0)}
    rvec = np.array([0.05, 0.3, -0.1]).reshape(3, 1)
    tvec = np.array([0.2, -0.1, 2.0]).reshape(3, 1)

    (tagPos, tagRpy) = olab_utils.findTagPoseGlobal(cameraPose, rvec, tvec, cameraExtrinsics)
    tagPose = {'position': tagPos, 'orientation': tagRpy}
    (bodyPos, bodyRpy) = olab_utils.findCameraPoseGlobal(tagPose, rvec, tvec, cameraExtrinsics)

    R_true = olab_utils._rpyToMatrix(*cameraPose['orientation'])
    R_recovered = olab_utils._rpyToMatrix(*bodyRpy)

    assert np.allclose(bodyPos, cameraPose['position'], atol=1e-6)
    assert np.allclose(R_true, R_recovered, atol=1e-6)


@pytest.mark.parametrize('pitch_offset', [0.001, 0.05])
def test_matrixToRpy_preserves_roll_near_but_not_at_gimbal_lock(pitch_offset):
    '''
    Regression for a bug in an earlier fix: thresholding the singularity
    check on `1 - abs(sin(pitch))` (rather than on `cos(pitch)` directly) is
    wrong because sin(pitch) is *quadratically* insensitive to the actual
    deviation from the pole (sin(pi/2 - eps) = 1 - eps^2/2 + ...) -- a
    threshold intended to catch only floating-point noise at the exact
    singularity instead silently zeroed out a real, nonzero roll for any
    attitude within about +/-0.06 degrees of vertical (reviewer's exact
    repro: pitch = pi/2 - 0.001 with roll=0.4 came back as roll=0). Assert
    that a genuinely near-singular (but not exactly singular) attitude
    round-trips its *specific* (roll, pitch, yaw) values, not just the
    rotation matrix -- unlike the exact +/-pi/2 case, roll/yaw are each
    individually well-defined and recoverable here.
    '''
    for pitch_true in (math.pi / 2 - pitch_offset, -math.pi / 2 + pitch_offset):
        R = olab_utils._rpyToMatrix(0.4, pitch_true, -0.7)
        (roll, pitch, yaw) = olab_utils._matrixToRpy(R)

        assert abs(roll) > 0.3   # must not have been zeroed out
        assert np.allclose([roll, pitch, yaw], [0.4, pitch_true, -0.7], atol=1e-9)


def test_findTagPoseGlobal_defaults_to_identity_extrinsics():
    cameraPose = {'position': (0.0, 0.0, 1.0), 'orientation': (0.0, 0.0, 0.0)}
    rvec = np.zeros((3, 1))
    tvec = np.array([0.0, 0.0, 2.0]).reshape(3, 1)

    (tagPos, tagRpy) = olab_utils.findTagPoseGlobal(cameraPose, rvec, tvec)   # cameraExtrinsics omitted
    tagPose = {'position': tagPos, 'orientation': tagRpy}
    (bodyPos, bodyRpy) = olab_utils.findCameraPoseGlobal(tagPose, rvec, tvec)   # cameraExtrinsics omitted

    assert np.allclose(bodyPos, (0.0, 0.0, 1.0), atol=1e-6)
    assert np.allclose(bodyRpy, (0.0, 0.0, 0.0), atol=1e-6)


# ─── Deprecated aliases (issue #21) ────────────────────────────────────────

def test_arucoFindPose_deprecated_alias_still_works_and_warns():
    rvec_true = np.zeros((3, 1))
    tvec_true = np.array([0.0, 0.0, 1.5]).reshape(3, 1)
    corners, objectPoints = _project_synthetic_tag(rvec_true, tvec_true)

    with pytest.warns(DeprecationWarning):
        (ret, rvec, tvec) = olab_utils.arucoFindPose(objectPoints, corners, CAMERA_MATRIX, DIST_COEFFS)

    assert ret
    assert np.allclose(tvec.flatten(), tvec_true.flatten(), atol=1e-3)


def test_arucoFindPoseGlobal_deprecated_alias_still_works_and_warns():
    cameraPose = {'position': (0.0, 0.0, 1.0), 'orientation': (0.0, 0.0, 0.0)}
    rvec = np.zeros((3, 1))
    tvec = np.array([0.0, 0.0, 2.0]).reshape(3, 1)

    with pytest.warns(DeprecationWarning):
        (tagPos, tagRpy) = olab_utils.arucoFindPoseGlobal(cameraPose, rvec, tvec)

    # Cross-check against calling the new name directly, rather than
    # hand-deriving the optical/cameralink frame conversion here.
    (expectedPos, _) = olab_utils.findTagPoseGlobal(cameraPose, rvec, tvec)
    assert np.allclose(tagPos, expectedPos, atol=1e-6)


def test_arucoFindCameraPoseGlobal_deprecated_alias_still_works_and_warns():
    tagPose = {'position': (0.0, 0.0, 3.0), 'orientation': (0.0, 0.0, 0.0)}
    rvec = np.zeros((3, 1))
    tvec = np.array([0.0, 0.0, 2.0]).reshape(3, 1)

    with pytest.warns(DeprecationWarning):
        (bodyPos, bodyRpy) = olab_utils.arucoFindCameraPoseGlobal(tagPose, rvec, tvec)

    assert np.allclose(bodyPos, (0.0, 0.0, 1.0), atol=1e-6)


def _synthetic_qr_image(payload, skew_frac=0.0):
    '''
    Render `payload` as a QR bitmap via cv2.QRCodeEncoder (already a transitive
    dependency via opencv-contrib-python -- no need for the separate `qrcode`
    PyPI package), scale it up, add a quiet-zone border, and optionally warp it
    perspective-wise to simulate a skewed/oblique viewing angle. Produces a
    square canvas (when skew_frac=0) so np.rot90() can be used directly for
    the corner-identity invariance test below.
    '''
    encoder = cv2.QRCodeEncoder.create()
    small = encoder.encode(payload)
    scale = 12
    big = cv2.resize(small, (small.shape[1] * scale, small.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    border = 40
    base = np.full((big.shape[0] + 2 * border, big.shape[1] + 2 * border), 255, dtype=np.uint8)
    base[border:border + big.shape[0], border:border + big.shape[1]] = big
    base = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    h, w = base.shape[:2]

    if skew_frac:
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32([[0, int(h * skew_frac)], [w, 0], [w, h], [0, int(h * (1 - skew_frac * 0.3))]])
        M = cv2.getPerspectiveTransform(src, dst)
        base = cv2.warpPerspective(base, M, (w, h), borderValue=(255, 255, 255))

    canvas = np.full((h + 200, w + 200, 3), 255, dtype=np.uint8)
    canvas[100:100 + h, 100:100 + w] = base
    return canvas


@pytest.mark.parametrize('rotation_k', [0, 1, 2, 3])
def test_cv2_qr_corner_order_is_symbol_frame_anchored(rotation_k):
    '''
    cv2.QRCodeDetector's corner order tracks the physical tag's own
    finder-pattern-derived frame, not the image/viewing frame -- this is why
    addQR()'s docstring recommends decoder='cv2' when a caller intends to
    compute pose (via findTagPose()) from the returned corners: reordering
    by image position would make recovered yaw jump by multiples of 90
    degrees depending on viewing angle instead of tracking the physical tag.
    Verify: physically rotating the same QR image by 90-degree increments
    (np.rot90, a genuine physical rotation of the printed tag) produces a
    *consistent cyclic relabeling* of the detector's returned corner order
    (each labeled corner keeps tracking the same physical/printed corner),
    not an arbitrary or image-anchored one.
    '''
    base = _synthetic_qr_image('OLAB_TEST_PAYLOAD')
    detector = cv2.QRCodeDetector()

    ret0, points0 = detector.detect(base)
    assert ret0
    points0 = points0.reshape(4, 2)

    rotated = np.ascontiguousarray(np.rot90(base, k=rotation_k))
    ret, points = detector.detect(rotated)
    assert ret
    points = points.reshape(4, 2)

    expected = np.roll(points0, shift=rotation_k, axis=0)
    assert np.allclose(points, expected, atol=3)


def test_pyzbar_qr_polygon_order_verification_documents_limitation():
    '''
    This test documents the empirical finding that pyzbar's polygon order is
    NOT reliably anchored to the QR symbol's own frame across physical
    rotations -- justifying addQR()'s docstring recommendation to prefer
    decoder='cv2' when a caller intends to compute pose (orientation) from
    the returned corners via findTagPose(). It does not assert a specific
    (unstable) corner order; it only asserts that pyzbar successfully
    locates the tag's four corners at each rotation, so a caller can
    independently confirm the limitation still holds if zbar's algorithm
    ever changes.
    '''
    pyzbar = pytest.importorskip('pyzbar.pyzbar')
    base = _synthetic_qr_image('OLAB_TEST_PAYLOAD')

    for k in [0, 1, 2, 3]:
        rotated = np.rot90(base, k=k)
        decoded = pyzbar.decode(rotated, symbols=[pyzbar.ZBarSymbol.QRCODE])
        assert len(decoded) == 1
        assert len(decoded[0].polygon) == 4
