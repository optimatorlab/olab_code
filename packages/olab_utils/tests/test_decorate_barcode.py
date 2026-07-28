"""Regression test for decorateBarcode()'s addText bug (same indexing bug
fixed in decorateFaceDetect() -- issue #25 follow-up): corners[i][0][1][0]
treated a plain (x1,y1) 2-tuple as if it had a further indexable level,
disabled inside a commented-out block. Fixed by mirroring decorateQR()'s
working label-drawing pattern."""

import numpy as np

import olab_utils


def test_decorateBarcode_addText_renders_label_without_raising():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    corners = [[(30, 30), (60, 60)]]
    data = ['12345']

    olab_utils.decorateBarcode(img, corners, data, color=(0, 0, 255), addText=True)

    (x1, y1) = corners[0][0]
    roi = img[max(0, y1 - 15):y1, x1:x1 + 40]
    assert roi.sum() > 0
