"""Tests for the QR tag support added in this task: the new _QRCode class
(corners-only detection, like _Aruco/_Barcode) and the Camera.pose/
Camera.extrinsics attributes that feed olab_utils.findTagPoseGlobal()/
findCameraPoseGlobal() from a user's own postFunction -- see
docs/usage_guide.md's "QR Codes" section for the full worked pattern this
mirrors.

NOTE: _Aruco itself is untouched by this task (no changes needed -- pose was
already available via the pre-existing olab_utils.findTagPose(), called
from a user's postFunction, per docs/usage_guide.md). These tests therefore
only cover the new _QRCode class and the pose-composition helpers.
"""

import time

import numpy as np
import cv2
import pytest

from olab_camera.camera import Camera


def _synthetic_qr_image(payload, skew_frac=0.0):
    '''Render `payload` as a QR image via cv2.QRCodeEncoder (already a
    transitive dependency via opencv-contrib-python), optionally warped to
    simulate a skewed/oblique viewing angle.'''
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


def _make_camera_with_frame(img):
    cam = Camera({'res_rows': img.shape[0], 'res_cols': img.shape[1], 'fps_target': 5})
    cam.frameDeque.append(img)
    cam.camOn = True
    return cam


def _stop_feature_thread(cam, featureDict, idName, wait=0.3):
    featureDict[idName].isThreadActive = False
    cam.camOn = False
    time.sleep(wait)


# ─── Camera.pose / Camera.extrinsics defaults ──────────────────────────────

def test_pose_and_extrinsics_defaults():
    cam = Camera({'res_rows': 480, 'res_cols': 640, 'fps_target': 5})
    assert cam.pose is None
    assert cam.extrinsics == {'position': (0.0, 0.0, 0.0), 'orientation': (0.0, 0.0, 0.0)}


def test_setPose_and_setExtrinsics_store_public_attributes():
    cam = Camera({'res_rows': 480, 'res_cols': 640, 'fps_target': 5})
    cam.setPose(x=1.0, y=2.0, z=3.0, roll=0.1, pitch=0.2, yaw=0.3)
    cam.setExtrinsics(x=0.1, y=0.0, z=-0.05, roll=0.0, pitch=0.5, yaw=0.0)

    assert cam.pose == {'position': (1.0, 2.0, 3.0), 'orientation': (0.1, 0.2, 0.3)}
    assert cam.extrinsics == {'position': (0.1, 0.0, -0.05), 'orientation': (0.0, 0.5, 0.0)}


def test_camera_pose_feeds_findTagPoseGlobal_round_trip():
    # Mirrors docs/usage_guide.md's precision-landing pattern: camera.pose/
    # camera.extrinsics are read directly by the user's own postFunction and
    # passed into olab_utils.findTagPoseGlobal()/findCameraPoseGlobal().
    import olab_utils

    cam = Camera({'res_rows': 480, 'res_cols': 640, 'fps_target': 5})
    cam.setPose(x=10.0, y=-5.0, z=2.0, roll=0.1, pitch=-0.2, yaw=1.0)
    cam.setExtrinsics(x=0.1, y=0.0, z=-0.05, roll=0.0, pitch=0.5, yaw=0.0)

    rvec = np.array([0.05, 0.3, -0.1]).reshape(3, 1)
    tvec = np.array([0.2, -0.1, 2.0]).reshape(3, 1)

    (tagPos, tagRpy) = olab_utils.findTagPoseGlobal(cam.pose, rvec, tvec, cam.extrinsics)
    tagPose = {'position': tagPos, 'orientation': tagRpy}
    (bodyPos, bodyRpy) = olab_utils.findCameraPoseGlobal(tagPose, rvec, tvec, cam.extrinsics)

    assert np.allclose(bodyPos, (10.0, -5.0, 2.0), atol=1e-6)
    assert np.allclose(bodyRpy, (0.1, -0.2, 1.0), atol=1e-6)


# ─── _QRCode ─────────────────────────────────────────────────────────────

def test_addQR_cv2_decoder_decodes_skewed_tag():
    # Regression check for the "skewed reads fail" problem this task targets.
    img = _synthetic_qr_image('PAD_A', skew_frac=0.15)
    cam = _make_camera_with_frame(img)

    cam.addQR(idName='default', decoder='cv2')
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    assert 'PAD_A' in d['data']
    idx = d['data'].index('PAD_A')
    assert d['corners'][idx].shape == (4, 2)


def test_addQR_cv2_decoder_corners_usable_for_pose_via_findTagPose():
    # Proves the corners _QRCode reports are directly usable with the
    # existing, established findTagPose() helper -- the same pattern shown
    # for ArUco in docs/usage_guide.md -- to get a sane distance.
    import olab_utils

    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)
    cam.setIntrinsics(f'{img.shape[1]}x{img.shape[0]}', fx=800, fy=800,
                       cx=img.shape[1] / 2, cy=img.shape[0] / 2, dist=[0, 0, 0, 0, 0])

    cam.addQR(idName='default', decoder='cv2')
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    idx = d['data'].index('PAD_A')
    res = f'{cam.res_cols}x{cam.res_rows}'
    cameraMatrix = cam.intrinsics[res]['matrix']
    dist = cam.intrinsics[res]['dist']
    tag_size = 0.15
    half = tag_size / 2
    objPoints = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]])

    (ret, rvec, tvec) = olab_utils.findTagPose(objPoints, d['corners'][idx], cameraMatrix, dist)
    assert ret
    assert tvec[2] > 0   # tag is in front of the camera, some positive z distance


def test_addQR_pyzbar_decoder_also_decodes():
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)

    cam.addQR(idName='default', decoder='pyzbar')
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    assert 'PAD_A' in d['data']


def test_addQR_unknown_decoder_does_not_raise_and_does_not_register():
    # addQR() validates `decoder` before ever constructing/storing a
    # _QRCode, so an invalid decoder must not raise and must not leave a
    # partially-initialized entry in cam.qr.
    img = _synthetic_qr_image('PAD_A')
    cam = _make_camera_with_frame(img)

    cam.addQR(idName='default', decoder='not-a-real-decoder')   # must not raise
    assert 'default' not in cam.qr
    cam.camOn = False


def test_addQR_retry_with_valid_decoder_after_invalid_decoder_typo():
    # A caller's first attempt has a typo'd decoder; a corrected retry with
    # the same idName must actually start (not be silently blocked by a
    # leftover broken registry entry from the failed first attempt).
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)

    cam.addQR(idName='default', decoder='not-a-real-decoder')
    assert 'default' not in cam.qr

    cam.addQR(idName='default', decoder='cv2')
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    assert 'PAD_A' in d['data']


def test_addQR_processes_at_a_lower_resolution_and_scales_corners_back():
    # Corners must always be reported in the *original* capture resolution's
    # coordinate system, even when res_rows/res_cols request a smaller
    # processing resolution (e.g. to reduce CPU cost) -- mirrors _Aruco's
    # img_x_y/orig_x_y resizing/scaling behavior.
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    orig_h, orig_w = img.shape[:2]
    cam = _make_camera_with_frame(img)

    proc_w, proc_h = orig_w // 2, orig_h // 2
    cam.addQR(idName='default', decoder='cv2', res_rows=proc_h, res_cols=proc_w)
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    assert 'PAD_A' in d['data']
    idx = d['data'].index('PAD_A')
    corners = d['corners'][idx]
    # Scaled-back corners should span a good fraction of the *original*
    # resolution, not be confined to the (much smaller) processing-resolution
    # pixel range -- a broken/no-op scale-back would leave them within
    # [0, proc_w] x [0, proc_h] instead.
    assert corners[:, 0].max() > proc_w
    assert corners[:, 1].max() > proc_h
    assert corners[:, 0].max() <= orig_w
    assert corners[:, 1].max() <= orig_h


def test_addQR_ids_of_interest_filters_reported_payloads():
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)

    cam.addQR(idName='default', decoder='cv2', ids_of_interest=['SOME_OTHER_PAYLOAD'])
    time.sleep(1.5)
    d = cam.qr['default'].deque[0]
    _stop_feature_thread(cam, cam.qr, 'default')

    assert 'PAD_A' not in d['data']


def test_addQR_two_instances_with_default_postFunctionArgs_get_independent_dicts():
    # `postFunctionArgs={}` (a shared mutable default) would make both
    # callbacks receive the *second* feature's idName, and both _QRCode
    # instances would reference the exact same dict object. Reproduces the
    # reviewer's finding: two concurrent QR IDs started with a callback and
    # no explicit postFunctionArgs.
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)

    received = {}

    def cb(argsDict):
        received[argsDict['idName']] = argsDict

    cam.addQR(idName='first', decoder='cv2', postFunction=cb)
    cam.addQR(idName='second', decoder='cv2', postFunction=cb)
    time.sleep(1.5)
    _stop_feature_thread(cam, cam.qr, 'first')
    cam.camOn = True   # keep 'second' alive long enough to also observe its callback
    time.sleep(0.3)
    cam.qr['second'].isThreadActive = False
    cam.camOn = False
    time.sleep(0.3)

    assert cam.qr['first'].postFunctionArgs is not cam.qr['second'].postFunctionArgs
    assert cam.qr['first'].postFunctionArgs['idName'] == 'first'
    assert cam.qr['second'].postFunctionArgs['idName'] == 'second'
    assert 'first' in received and received['first']['idName'] == 'first'
    assert 'second' in received and received['second']['idName'] == 'second'


def test_addQR_does_not_mutate_caller_supplied_postFunctionArgs():
    img = _synthetic_qr_image('PAD_A', skew_frac=0.0)
    cam = _make_camera_with_frame(img)

    myArgs = {'label': 'mine'}
    cam.addQR(idName='default', decoder='cv2', postFunctionArgs=myArgs)
    time.sleep(1.5)
    _stop_feature_thread(cam, cam.qr, 'default')

    assert myArgs == {'label': 'mine'}   # caller's own dict must be untouched
    assert cam.qr['default'].postFunctionArgs == {'label': 'mine', 'idName': 'default'}
