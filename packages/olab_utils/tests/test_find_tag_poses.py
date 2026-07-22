"""Tests for olab_utils.findTagPoses() (issue #20) -- the reusable
postFunction boilerplate consolidating the loop/objPoints/solvePnP/
world-pose-composition pattern previously hand-rolled in
docs/usage_guide.md's aruco_post_poses()/qr_post_poses() examples."""

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


def _project_synthetic_tag(rvec, tvec, tag_size=TAG_SIZE):
    half = tag_size / 2.0
    objectPoints = np.array([
        [-half, half, 0.0],
        [half, half, 0.0],
        [half, -half, 0.0],
        [-half, -half, 0.0],
    ], dtype=np.float64)
    imagePoints, _ = cv2.projectPoints(objectPoints, rvec, tvec, CAMERA_MATRIX, DIST_COEFFS)
    return imagePoints.reshape(4, 2)


def test_findTagPoses_single_detection_matches_findTagPose_directly():
    rvec_true = np.zeros((3, 1))
    tvec_true = np.array([0.0, 0.0, 1.5]).reshape(3, 1)
    corners = _project_synthetic_tag(rvec_true, tvec_true)

    results = olab_utils.findTagPoses([corners], ['tag_a'], TAG_SIZE, CAMERA_MATRIX, DIST_COEFFS)

    assert len(results) == 1
    result = results[0]
    assert result['id'] == 'tag_a'
    assert np.allclose(np.asarray(result['tvec']).flatten(), tvec_true.flatten(), atol=1e-3)
    assert result['worldPosition'] is None
    assert result['worldOrientation'] is None
    assert result['distance'] == pytest.approx(np.linalg.norm(tvec_true), abs=1e-3)


def test_findTagPoses_multi_detection_preserves_order_and_ids():
    rvec_true = np.zeros((3, 1))
    tvec_a = np.array([0.1, 0.0, 1.0]).reshape(3, 1)
    tvec_b = np.array([-0.2, 0.1, 2.0]).reshape(3, 1)
    corners_a = _project_synthetic_tag(rvec_true, tvec_a)
    corners_b = _project_synthetic_tag(rvec_true, tvec_b)

    results = olab_utils.findTagPoses([corners_a, corners_b], [1, 2], TAG_SIZE, CAMERA_MATRIX, DIST_COEFFS)

    assert [r['id'] for r in results] == [1, 2]
    assert np.allclose(np.asarray(results[0]['tvec']).flatten(), tvec_a.flatten(), atol=1e-3)
    assert np.allclose(np.asarray(results[1]['tvec']).flatten(), tvec_b.flatten(), atol=1e-3)


def test_findTagPoses_world_pose_matches_findTagPoseGlobal_directly():
    rvec = np.array([0.05, 0.3, -0.1]).reshape(3, 1)
    tvec = np.array([0.2, -0.1, 2.0]).reshape(3, 1)
    corners = _project_synthetic_tag(rvec, tvec)

    cameraPose = {'position': (10.0, -5.0, 2.0), 'orientation': (0.1, -0.2, 1.0)}
    cameraExtrinsics = {'position': (0.1, 0.0, -0.05), 'orientation': (0.0, 0.5, 0.0)}

    results = olab_utils.findTagPoses([corners], ['tag_a'], TAG_SIZE, CAMERA_MATRIX, DIST_COEFFS,
                                       cameraPose=cameraPose, cameraExtrinsics=cameraExtrinsics)

    assert len(results) == 1
    result = results[0]
    assert result['worldPosition'] is not None

    # Cross-check against calling findTagPose()/findTagPoseGlobal() directly.
    (ret, rvecDirect, tvecDirect) = olab_utils.findTagPose(
        np.array([[-TAG_SIZE/2, TAG_SIZE/2, 0], [TAG_SIZE/2, TAG_SIZE/2, 0],
                  [TAG_SIZE/2, -TAG_SIZE/2, 0], [-TAG_SIZE/2, -TAG_SIZE/2, 0]]),
        corners, CAMERA_MATRIX, DIST_COEFFS)
    assert ret
    (expectedWorldPos, expectedWorldRpy) = olab_utils.findTagPoseGlobal(
        cameraPose, rvecDirect, tvecDirect, cameraExtrinsics)

    assert np.allclose(result['worldPosition'], expectedWorldPos, atol=1e-6)
    assert np.allclose(result['worldOrientation'], expectedWorldRpy, atol=1e-6)


def test_findTagPoses_skips_failed_detection_without_raising(monkeypatch):
    # A failed per-detection solvePnP (ret=False, via findTagPose()) must be
    # silently omitted from the results, not raise or produce a None entry.
    # Simulated via monkeypatch rather than hunting for cv2.solvePnP inputs
    # that return ret=False instead of raising -- findTagPoses()'s contract
    # is about how it *reacts* to ret=False, not about solvePnP's own
    # failure modes.
    rvec_true = np.zeros((3, 1))
    tvec_true = np.array([0.0, 0.0, 1.5]).reshape(3, 1)
    goodCorners = _project_synthetic_tag(rvec_true, tvec_true)
    badCorners = _project_synthetic_tag(rvec_true, tvec_true)

    real_findTagPose = olab_utils.findTagPose

    def fake_findTagPose(objPoints, corners, cameraMatrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE):
        if (corners is badCorners):
            return (False, None, None)
        return real_findTagPose(objPoints, corners, cameraMatrix, dist, flags=flags)

    monkeypatch.setattr(olab_utils, 'findTagPose', fake_findTagPose)

    results = olab_utils.findTagPoses([badCorners, goodCorners], ['bad', 'good'],
                                       TAG_SIZE, CAMERA_MATRIX, DIST_COEFFS)

    ids = [r['id'] for r in results]
    assert 'bad' not in ids
    assert 'good' in ids


def test_findTagPoses_raises_valueerror_on_mismatched_lengths():
    corners = _project_synthetic_tag(np.zeros((3, 1)), np.array([0.0, 0.0, 1.5]).reshape(3, 1))

    with pytest.raises(ValueError):
        olab_utils.findTagPoses([corners, corners], ['only_one_id'], TAG_SIZE, CAMERA_MATRIX, DIST_COEFFS)


@pytest.mark.parametrize('bad_size', [0.0, -0.1, math.nan, math.inf, -math.inf])
def test_findTagPoses_raises_valueerror_on_invalid_numeric_tag_size(bad_size):
    corners = _project_synthetic_tag(np.zeros((3, 1)), np.array([0.0, 0.0, 1.5]).reshape(3, 1))

    with pytest.raises(ValueError):
        olab_utils.findTagPoses([corners], ['tag_a'], bad_size, CAMERA_MATRIX, DIST_COEFFS)


@pytest.mark.parametrize('bad_size', ['0.2', [0.2], np.array([0.2, 0.3]), None, True, False])
def test_findTagPoses_raises_valueerror_not_typeerror_on_invalid_type_tag_size(bad_size):
    corners = _project_synthetic_tag(np.zeros((3, 1)), np.array([0.0, 0.0, 1.5]).reshape(3, 1))

    with pytest.raises(ValueError):
        olab_utils.findTagPoses([corners], ['tag_a'], bad_size, CAMERA_MATRIX, DIST_COEFFS)


def test_findTagPoses_accepts_numpy_scalar_tag_size():
    rvec_true = np.zeros((3, 1))
    tvec_true = np.array([0.0, 0.0, 1.5]).reshape(3, 1)
    corners = _project_synthetic_tag(rvec_true, tvec_true)

    results = olab_utils.findTagPoses([corners], ['tag_a'], np.float64(TAG_SIZE), CAMERA_MATRIX, DIST_COEFFS)

    assert len(results) == 1
