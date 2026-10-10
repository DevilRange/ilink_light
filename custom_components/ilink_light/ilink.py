"""BLE protocol and connection handling for iLink (JieLi) lights.

Frame: 55 AA <type> <cmd_hi> <cmd_lo> <data...> <crc>, crc = (0xFF - sum(preceding)) & 0xFF.
Commands go to a040; the lamp sends no notifications, replies are read from a041.
Status reports the live (fading/animating) output, so callers use settled().
The lamp stops advertising while connected; we keep one persistent connection
and treat a dropped link as "power cut at the wall".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

_LOGGER = logging.getLogger(__name__)

WRITE_CHAR = "0000a040-0000-1000-8000-00805f9b34fb"
READ_CHAR = "0000a041-0000-1000-8000-00805f9b34fb"
SERVICE_UUID = "0000a032-0000-1000-8000-00805f9b34fb"
MIN_KELVIN, MAX_KELVIN = 3000, 6000


def frame(kind: int, cmd: int, *data: int) -> bytes:
    body = bytes([0x55, 0xAA, kind, cmd >> 8, cmd & 0xFF, *data])
    return body + bytes([(0xFF - sum(body)) & 0xFF])


ON = frame(1, 0x0805, 1)
OFF = frame(1, 0x0805, 0)
STATUS = frame(1, 0x0815, 6)


def brightness(value: int) -> bytes:
    return frame(1, 0x0801, max(1, min(255, value)))


def rgb(r: int, g: int, b: int) -> bytes:
    return frame(3, 0x0802, r, g, b)


def kelvin(k: int) -> bytes:
    """Continuous white: 0 = cold (6000K) .. 255 = warm (3000K). Keeps brightness."""
    k = max(MIN_KELVIN, min(MAX_KELVIN, k))
    return frame(1, 0x0807, round((MAX_KELVIN - k) * 255 / (MAX_KELVIN - MIN_KELVIN)))


def preset(level: int) -> bytes:
    """White presets 1-5; 2/3/4 are off the kelvin slider's line (3 = both channels full). Keeps brightness."""
    return frame(1, 0x0809, level)


def scene(scene_id: int) -> bytes:
    return frame(1, 0x0806, scene_id)


@dataclass
class Status:
    on: bool
    brightness: int
    rgb: tuple[int, int, int]
    cold: int
    warm: int
    scene: int | None

    @property
    def kelvin(self) -> int | None:
        """None in RGB mode (both white channels off)."""
        if not self.cold + self.warm:
            return None
        return round(MIN_KELVIN + (MAX_KELVIN - MIN_KELVIN) * self.cold / (self.cold + self.warm))

    @property
    def key(self) -> tuple:
        """What must stop changing for a reading to count as settled (scenes animate forever)."""
        if self.scene is not None:
            return (self.on, self.scene)
        return (self.on, self.brightness, self.rgb, self.cold, self.warm)

    @classmethod
    def parse(cls, d: bytes) -> Status | None:
        if len(d) != 15 or d[:5] != b"\x55\xaa\x09\x88\x15" or d[14] != (0xFF - sum(d[:14])) & 0xFF:
            return None
        return cls(d[11] == 1, d[10], (d[5], d[6], d[7]), d[8], d[9], d[12] - 1 if d[12] else None)


class IlinkLight:
    def __init__(self, ble_device: BLEDevice | None) -> None:
        self.ble_device = ble_device
        self.on_disconnect: Callable[[], None] | None = None
        self.status: Status | None = None
        self.last_reply: bytes | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _disconnected(self, _client) -> None:
        if self.on_disconnect:
            self.on_disconnect()

    async def _io(self, frames: tuple[bytes, ...], read: bool) -> bytes | None:
        if self.ble_device is None:
            raise ConnectionError("lamp not seen yet (powered off?)")
        async with self._lock:
            for attempt in (1, 2):  # a stale link fails on first write; reconnect once
                try:
                    if not self.connected:
                        self._client = await establish_connection(
                            BleakClientWithServiceCache,
                            self.ble_device,
                            self.ble_device.name or self.ble_device.address,
                            disconnected_callback=self._disconnected,
                            ble_device_callback=lambda: self.ble_device,
                        )
                    for f in frames:
                        await self._client.write_gatt_char(WRITE_CHAR, f, response=True)
                    return bytes(await self._client.read_gatt_char(READ_CHAR)) if read else None
                except Exception:
                    await self.stop()
                    if attempt == 2:
                        raise
        return None

    async def send(self, *frames: bytes) -> None:
        await self._io(frames, read=False)

    async def read(self) -> Status | None:
        self.last_reply = reply = await self._io((STATUS,), read=True)
        if status := Status.parse(reply):
            self.status = status
        else:
            _LOGGER.debug("%s: unexpected status reply %s", self.ble_device.address, reply.hex())
        return status

    async def settled(self, max_wait: float = 3.0) -> Status | None:
        """Read until two consecutive readings agree (fades take up to ~1.3 s)."""
        loop = asyncio.get_running_loop()
        end = loop.time() + max_wait
        prev = await self.read()
        while loop.time() < end:
            await asyncio.sleep(0.4)
            cur = await self.read()
            if cur and prev and cur.key == prev.key:
                return cur
            prev = cur
        return prev

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client:
            await client.disconnect()
