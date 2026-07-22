"""
Minimal example: generate a printable QR tag with olab_utils.generateQR(),
for use as a physical fiducial with olab_camera's QR detection/pose pipeline
(see packages/olab_camera/docs/usage_guide.md's "QR Codes" section).

Setup (run once, from the repo root -- there is no shared repo-wide venv,
so olab_utils isn't importable without this):
    python3 -m venv venv
    source venv/bin/activate
    pip install -e packages/olab_utils

Run:
cd ~/Projects/olab_code
source venv/bin/activate

cd examples
python3 generate_qr_tag.py
"""

import json

import olab_utils


def _report_size(filename, img, tag_size_inches, dpi):
    """
    With the default border=0, tag_size_inches sizes the ENTIRE saved
    image, edge to edge -- exactly what a print dialog's "actual size"
    setting or an image editor's "set image size" controls, and exactly
    what a ruler measures on the printed page. No separate quiet-zone
    margin to account for.
    """
    actual_size_inches = img.shape[1] / dpi
    print(f"Wrote {filename} -- {actual_size_inches:.2f}in square (requested {tag_size_inches:.2f}in)")


# ---- 1. A basic tag: a 4-inch QR tag at 300 DPI, saved as a lossless
# PNG (only .png/.tif/.tiff/.bmp are accepted -- a lossy format like JPEG
# would corrupt the module edges QR decoding depends on). ----
TAG_SIZE_INCHES = 4.0
DPI = 300

img = olab_utils.generateQR(
    payload='PAD_A',
    tag_size_inches=TAG_SIZE_INCHES,
    dpi=DPI,
    outputFile='pad_a_tag.png')
_report_size('pad_a_tag.png', img, TAG_SIZE_INCHES, DPI)


# ---- 2. With an optional human-readable label printed below the tag
# (extra canvas below the tag -- doesn't affect tag_size_inches). ----
img = olab_utils.generateQR(
    payload='PAD_B',
    tag_size_inches=TAG_SIZE_INCHES,
    dpi=DPI,
    label='PAD_B',
    outputFile='pad_b_tag_labeled.png')
_report_size('pad_b_tag_labeled.png', img, TAG_SIZE_INCHES, DPI)


# ---- 3. With a logo embedded in the center (path or numpy BGR/BGRA/
# grayscale image). logo_scale is a side-length fraction of the tag
# (area coverage is logo_scale ** 2); generateQR() verifies the result
# still decodes and raises if the logo broke it. ----
try:
    img = olab_utils.generateQR(
        payload='PAD_C',
        tag_size_inches=TAG_SIZE_INCHES,
        dpi=DPI,
        logo='olab_logo.png',   # replace with a real logo file to try this
        logo_scale=0.25,
        outputFile='pad_c_tag_with_logo.png')
    _report_size('pad_c_tag_with_logo.png', img, TAG_SIZE_INCHES, DPI)
except ValueError as e:
    print(f"Skipped logo example (no olab_logo.png handy): {e}")


# ---- 4. A WiFi credentials QR code. Most phone cameras auto-detect the
# "WIFI:" prefix format and offer to join the network directly -- this is a
# plain QR payload, not olab_camera/pose-related, just a generic use of
# generateQR() for a non-fiducial tag. Replace SSID/PASSWORD with your own
# network's values. ----
WIFI_SSID = 'MyNetworkName'
WIFI_PASSWORD = 'MyNetworkPassword'
wifi_payload = f"WIFI:T:WPA;S:{WIFI_SSID};P:{WIFI_PASSWORD};;"

img = olab_utils.generateQR(
    payload=wifi_payload,
    tag_size_inches=3.0,
    dpi=DPI,
    label='Scan to join WiFi',
    outputFile='wifi_tag.png')
_report_size('wifi_tag.png', img, 3.0, DPI)


# ---- 5. A more information-rich payload: encode a dict as JSON. generateQR()
# only accepts str payloads, so serialize the dict yourself first -- and
# remember more data means a higher QR version (more modules), which for a
# fixed tag_size_inches/dpi means finer print detail is needed to resolve
# each module (see generateQR()'s "too small to render N modules" error). ----
tag_info = {
    'id': 'PAD_D',
    'location': 'Lab 3, Bay 2',
    'installed': '2026-01-15',
    'contact': 'lab-ops@example.edu',
}
rich_payload = json.dumps(tag_info)
print(f"Rich payload ({len(rich_payload)} chars): {rich_payload}")

img = olab_utils.generateQR(
    payload=rich_payload,
    tag_size_inches=5.0,   # larger tag -- more data needs more modules to resolve
    dpi=DPI,
    outputFile='pad_d_tag_rich_payload.png')
_report_size('pad_d_tag_rich_payload.png', img, 5.0, DPI)


# ---- Printing / pose notes ----
print(
    "\n"
    "IMPORTANT when printing these tags:\n"
    "  - Print at ACTUAL SIZE / 100% scale -- turn off any 'fit to page' or\n"
    "    'scale to fit' option. DPI is embedded as file metadata, but a\n"
    "    print dialog's fit-to-page ignores that and resamples anyway.\n"
    "  - After printing, measure the printed tag with a ruler before\n"
    "    trusting it for pose. It should match tag_size_inches (within\n"
    "    +/-1 pixel at the dpi used to generate it) -- pass that same\n"
    "    tag_size_inches value into olab_utils.findTagPose()'s\n"
    "    objectPoints, exactly as shown in\n"
    "    packages/olab_camera/docs/usage_guide.md's QR Codes section.\n"
    "  - The default border=0 means there's no quiet-zone margin baked\n"
    "    into the file -- leave ordinary white space around the tag when\n"
    "    printing/placing it (a normal printed page's own margins are\n"
    "    already enough for a single tag on its own sheet).")
