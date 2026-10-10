"""Shared fixtures."""

from __future__ import annotations

import time
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
import pytest

from custom_components.ilink_light import ilink

ADDRESS = "AA:BB:CC:11:22:33"


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, enable_bluetooth):
    """Load custom integrations and a mocked Bluetooth stack in every test."""
    return


def service_info(address: str = ADDRESS, name: str = "ilink app") -> BluetoothServiceInfoBleak:
    device = BLEDevice(address, name, {})
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[ilink.SERVICE_UUID],
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[ilink.SERVICE_UUID],
        source="local",
        device=device,
        advertisement=adv,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


class FakeLamp:
    """Stands in for ilink.IlinkLight: records frames, answers with a settable status."""

    def __init__(self, ble_device) -> None:
        self.ble_device = ble_device
        self.on_disconnect = None
        self.connected = True
        self.sent: list[list[bytes]] = []
        self.state = ilink.Status(on=True, brightness=255, rgb=(0, 0, 0), cold=255, warm=0, scene=None)
        self.status = None
        self.last_reply = None
        self.fail = False

    async def send(self, *frames: bytes) -> None:
        if self.fail:
            raise TimeoutError("no answer")
        self.sent.append(list(frames))

    async def settled(self):
        if self.fail:
            raise TimeoutError("no answer")
        self.status = self.state
        return self.state

    async def stop(self) -> None:
        self.connected = False


@pytest.fixture
def lamp():
    """Patch the BLE layer with a FakeLamp and report the lamp as present."""
    holder: dict[str, FakeLamp] = {}

    def make(ble_device):
        holder["lamp"] = FakeLamp(ble_device)
        return holder["lamp"]

    info = service_info()
    with (
        patch("custom_components.ilink_light.IlinkLight", side_effect=make),
        patch("custom_components.ilink_light.bluetooth.async_ble_device_from_address", return_value=info.device),
        patch("custom_components.ilink_light.light.bluetooth.async_address_present", return_value=True),
    ):
        yield holder
