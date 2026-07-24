import math
import os

import cv2
import numpy as np
import pytest
from PIL import Image

import olab_utils


PAYLOAD = 'OLAB_TEST_TAG'


def _decode(img):
    # Pad with a modest white margin before decoding -- with the default
    # border=0, the bare returned array has literally zero pixels beyond
    # its own edge, which cv2.QRCodeDetector can't reliably decode even
    # though real printed/photographed tags (with ordinary surrounding
    # whitespace -- ANY printed page's own margins) decode fine. This
    # mirrors the same padding olab_utils._qrRoundTripOk() applies
    # internally for its own decode check.
    pad = max(1, round(img.shape[0] * 0.15))
    canvas = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad, 3), 255, dtype=np.uint8)
    canvas[pad:pad + img.shape[0], pad:pad + img.shape[1]] = img

    detector = cv2.QRCodeDetector()
    (data, points, _) = detector.detectAndDecode(canvas)
    return (data, points)


def _grayscale_logo(size=50):
    logo = np.zeros((size, size), dtype=np.uint8)
    logo[size // 5: size - size // 5, size // 5: size - size // 5] = 200
    return logo


def _alpha_logo(size=60):
    logo = np.zeros((size, size, 4), dtype=np.uint8)
    logo[:, :, 0:3] = 100
    inset = size // 3
    logo[inset:size - inset, inset:size - inset, 3] = 255
    return logo


# ---- Core round-trip / sizing ----

def test_generateQR_round_trips_and_returns_bgr_array():
    img = olab_utils.generateQR(PAYLOAD, tag_size_inches=2.0, dpi=300)

    assert img.dtype == np.uint8
    assert img.ndim == 3 and img.shape[2] == 3

    (data, points) = _decode(img)
    assert data == PAYLOAD
    assert points is not None


def test_generateQR_symbol_size_matches_target_within_one_pixel():
    dpi = 300
    tag_size_inches = 2.0
    border = 4

    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=border, box_size=1)
    qr.add_data(PAYLOAD)
    qr.make(fit=True)
    module_count = qr.modules_count

    img = olab_utils.generateQR(PAYLOAD, tag_size_inches=tag_size_inches, dpi=dpi, border=border)

    target_symbol_px = round(dpi * tag_size_inches)
    full_modules = module_count + 2 * border
    recovered_symbol_px = img.shape[0] * module_count / full_modules

    assert abs(recovered_symbol_px - target_symbol_px) <= 1


def test_generateQR_nonzero_border_grows_file_but_not_symbol():
    """
    Locks down the contract for a nonzero border: tag_size_inches always
    means the QR symbol's own size (unaffected by border) -- a nonzero
    border makes the saved FILE larger than tag_size_inches (border added
    on top), it does not shrink the symbol to fit inside tag_size_inches.
    This is the opposite of what an earlier docstring draft mistakenly
    claimed; this test pins the actual (correct) behavior.
    """
    import qrcode

    dpi = 300
    tag_size_inches = 2.0
    border = 4

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=border, box_size=1)
    qr.add_data(PAYLOAD)
    qr.make(fit=True)
    module_count = qr.modules_count
    full_modules = module_count + 2 * border

    img = olab_utils.generateQR(PAYLOAD, tag_size_inches=tag_size_inches, dpi=dpi, border=border)

    target_symbol_px = round(dpi * tag_size_inches)
    expected_full_px = round(target_symbol_px * full_modules / module_count)

    # The saved file is strictly larger than the requested tag_size_inches
    # (in pixels at this dpi) whenever border > 0.
    assert img.shape[0] == expected_full_px
    assert img.shape[0] > target_symbol_px

    # But the QR symbol itself, recovered from the full image via the
    # module-count ratio, still matches tag_size_inches exactly (within
    # +/-1px) -- i.e. tag_size_inches' meaning didn't change with border.
    recovered_symbol_px = img.shape[0] * module_count / full_modules
    assert abs(recovered_symbol_px - target_symbol_px) <= 1


# ---- Parameter validation ----

def test_generateQR_rejects_non_string_payload():
    with pytest.raises(TypeError):
        olab_utils.generateQR(12345, 1.0)


def test_generateQR_rejects_empty_payload():
    with pytest.raises(ValueError):
        olab_utils.generateQR('', 1.0)


@pytest.mark.parametrize('value', [0, -1.0, float('nan'), float('inf')])
def test_generateQR_rejects_bad_tag_size_inches(value):
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, value)


def test_generateQR_rejects_non_numeric_tag_size_inches():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, '2.0')


def test_generateQR_rejects_bool_tag_size_inches():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, True)


@pytest.mark.parametrize('value', [0, -1, 1.5, True])
def test_generateQR_rejects_bad_dpi(value):
    with pytest.raises((TypeError, ValueError)):
        olab_utils.generateQR(PAYLOAD, 1.0, dpi=value)


def test_generateQR_rejects_invalid_ecc():
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, ecc='Z')


def test_generateQR_rejects_negative_border():
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, border=-1)


def test_generateQR_default_border_is_zero_and_full_image_equals_symbol():
    img = olab_utils.generateQR(PAYLOAD, tag_size_inches=2.0, dpi=300)
    assert img.shape[0] == round(300 * 2.0)
    assert img.shape[1] == round(300 * 2.0)

    (data, points) = _decode(img)
    assert data == PAYLOAD


def test_generateQR_accepts_border_zero_explicitly():
    img = olab_utils.generateQR(PAYLOAD, 1.0, border=0)
    (data, points) = _decode(img)
    assert data == PAYLOAD


def test_generateQR_rejects_bool_border():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, border=True)


def test_generateQR_rejects_tag_size_too_small_for_module_count():
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, tag_size_inches=0.001, dpi=10)


def test_generateQR_rejects_non_string_label():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, label=42)


def test_generateQR_accepts_none_label():
    img = olab_utils.generateQR(PAYLOAD, 1.0, label=None)
    assert img is not None


# ---- Color validation and consistency ----

def test_generateQR_rejects_rgba_color():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, fill_color=(0, 0, 0, 255))


def test_generateQR_rejects_unrecognized_color_type():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, back_color=object())


@pytest.mark.parametrize('back_color', ['red', (255, 0, 0)])
def test_generateQR_backing_square_color_matches_quiet_zone(back_color):
    # A nonzero border is required here so there's an actual quiet zone
    # region to sample -- generateQR()'s default border=0 means img[2, 2]
    # would otherwise land on the QR symbol's own finder pattern, not a
    # back_color quiet zone.
    logo = _grayscale_logo()
    tag_size_inches = 2.0
    dpi = 300
    border = 4
    logo_scale = 0.3

    img = olab_utils.generateQR(
        PAYLOAD, tag_size_inches=tag_size_inches, dpi=dpi, border=border,
        back_color=back_color, logo=logo, logo_scale=logo_scale)

    quiet_pixel = tuple(img[2, 2].tolist())

    target_symbol_px = round(dpi * tag_size_inches)
    backing_half = round(logo_scale * target_symbol_px) // 2
    (cy, cx) = (img.shape[0] // 2, img.shape[1] // 2)
    backing_edge_pixel = tuple(img[cy - backing_half + 2, cx - backing_half + 2].tolist())

    assert quiet_pixel == backing_edge_pixel
    assert quiet_pixel == (0, 0, 255)  # red in BGR


# ---- Logo validation and compositing ----

def test_generateQR_rejects_non_array_non_path_logo():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=[1, 2, 3])


def test_generateQR_rejects_unreadable_logo_path(tmp_path):
    bad_path = tmp_path / 'does_not_exist.png'
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=str(bad_path))


def test_generateQR_rejects_wrong_dtype_logo():
    logo = np.zeros((10, 10, 3), dtype=np.float32)
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=logo)


def test_generateQR_rejects_zero_sized_logo():
    logo = np.zeros((0, 10, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=logo)


def test_generateQR_rejects_wrong_channel_count_logo():
    logo = np.zeros((10, 10, 2), dtype=np.uint8)
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=logo)


@pytest.mark.parametrize('value', [-0.1, 0, 0.51, float('nan'), float('inf')])
def test_generateQR_rejects_bad_logo_scale(value):
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, logo=_grayscale_logo(), logo_scale=value)


def test_generateQR_grayscale_logo_round_trips():
    img = olab_utils.generateQR(PAYLOAD, 2.0, dpi=300, logo=_grayscale_logo(), logo_scale=0.3)
    (data, points) = _decode(img)
    assert data == PAYLOAD


def test_generateQR_alpha_logo_round_trips():
    img = olab_utils.generateQR(PAYLOAD, 2.0, dpi=300, logo=_alpha_logo(), logo_scale=0.3)
    (data, points) = _decode(img)
    assert data == PAYLOAD


def test_generateQR_logo_verification_failure_raises(monkeypatch):
    monkeypatch.setattr(olab_utils, '_qrRoundTripOk', lambda img, payload: False)
    with pytest.raises(RuntimeError):
        olab_utils.generateQR(PAYLOAD, 2.0, dpi=300, logo=_grayscale_logo(), logo_scale=0.3)


# ---- Label ----

def test_generateQR_label_appends_canvas_without_shrinking_qr():
    img_no_label = olab_utils.generateQR(PAYLOAD, 1.5, dpi=200)
    img_with_label = olab_utils.generateQR(PAYLOAD, 1.5, dpi=200, label='Tag A1')

    assert img_with_label.shape[1] == img_no_label.shape[1]
    assert img_with_label.shape[0] > img_no_label.shape[0]

    (data, points) = _decode(img_with_label)
    assert data == PAYLOAD


# ---- Output / DPI / format ----

def test_generateQR_output_embeds_dpi_metadata(tmp_path):
    dpi = 250
    out_path = tmp_path / 'tag.png'

    olab_utils.generateQR(PAYLOAD, 1.5, dpi=dpi, outputFile=str(out_path))

    reopened = Image.open(str(out_path))
    (dpi_x, dpi_y) = reopened.info['dpi']
    assert abs(dpi_x - dpi) < 1
    assert abs(dpi_y - dpi) < 1


def test_generateQR_output_content_matches_returned_array(tmp_path):
    out_path = tmp_path / 'tag.png'
    img = olab_utils.generateQR(PAYLOAD, 1.5, dpi=200, outputFile=str(out_path))

    reopened = np.array(Image.open(str(out_path)).convert('RGB'))
    reopened_bgr = cv2.cvtColor(reopened, cv2.COLOR_RGB2BGR)

    assert np.array_equal(img, reopened_bgr)


@pytest.mark.parametrize('ext', ['.png', '.tif', '.tiff', '.bmp'])
def test_generateQR_accepts_lossless_output_extensions(tmp_path, ext):
    out_path = tmp_path / f'tag{ext}'
    olab_utils.generateQR(PAYLOAD, 1.0, outputFile=str(out_path))
    assert out_path.exists()


@pytest.mark.parametrize('ext', ['.jpg', '.jpeg', '.gif'])
def test_generateQR_rejects_lossy_or_unsupported_output_extensions(tmp_path, ext):
    out_path = tmp_path / f'tag{ext}'
    with pytest.raises(ValueError):
        olab_utils.generateQR(PAYLOAD, 1.0, outputFile=str(out_path))
    assert not out_path.exists()


def test_generateQR_rejects_non_path_outputFile():
    with pytest.raises(TypeError):
        olab_utils.generateQR(PAYLOAD, 1.0, outputFile=12345)


def test_generateQR_outputFile_write_failure_raises_oserror(tmp_path):
    bad_path = tmp_path / 'nonexistent_subdir' / 'tag.png'
    with pytest.raises(OSError):
        olab_utils.generateQR(PAYLOAD, 1.0, outputFile=str(bad_path))
