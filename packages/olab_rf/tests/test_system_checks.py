from __future__ import annotations

from olab_rf.receivers.system import (
    find_rtlsdr_usb_devices,
    loaded_dvb_modules,
    parse_lsmod,
    parse_lsusb,
)


def test_parse_lsusb_finds_rtlsdr_device():
    devices = parse_lsusb(
        """
Bus 003 Device 078: ID 0bda:2838 Realtek Semiconductor Corp. RTL2838 DVB-T
Bus 003 Device 079: ID 413c:b080 Dell Computer Corp. Dell DA20 Adapter
"""
    )

    rtlsdr = find_rtlsdr_usb_devices(devices)

    assert len(rtlsdr) == 1
    assert rtlsdr[0].usb_id == "0bda:2838"


def test_loaded_dvb_modules_filters_relevant_modules():
    modules = parse_lsmod(
        """
Module                  Size  Used by
rtl2832_sdr            40960  0
dvb_usb_rtl28xxu       45056  1
bluetooth            1032192  36
"""
    )

    assert loaded_dvb_modules(modules) == ["dvb_usb_rtl28xxu", "rtl2832_sdr"]
