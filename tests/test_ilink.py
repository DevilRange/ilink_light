"""Protocol frames and status parsing, checked against bytes captured from a real lamp."""

from custom_components.ilink_light import ilink


def test_frames() -> None:
    assert ilink.ON.hex() == "55aa01080501f1"
    assert ilink.STATUS.hex() == "55aa01081506dc"
    assert ilink.rgb(255, 0, 0).hex() == "55aa030802ff0000f4"
    assert ilink.kelvin(6000).hex() == "55aa01080700f0"
    assert ilink.kelvin(3000).hex() == "55aa010807fff1"
    assert ilink.scene(6).hex() == "55aa01080606eb"
    assert ilink.preset(3).hex() == "55aa01080903eb"
    assert ilink.brightness(0)[-2] == 1  # clamped, 0 is not a valid level


def test_parse_white() -> None:
    s = ilink.Status.parse(bytes.fromhex("55aa0988150000007f80ff0100005b"))
    assert s.on and s.brightness == 255 and s.scene is None
    assert 4450 < s.kelvin < 4550


def test_parse_rgb_and_scene() -> None:
    s = ilink.Status.parse(bytes.fromhex("55aa098815ff0000000080010000da"))
    assert s.kelvin is None and s.rgb == (255, 0, 0) and s.brightness == 0x80
    s = ilink.Status.parse(bytes.fromhex("55aa0988150000ff0000c40107008f"))
    assert s.scene == 6 and s.key == (True, 6)


def test_parse_rejects_garbage() -> None:
    assert ilink.Status.parse(bytes.fromhex("55aa098815000000ff00ff0000005d")) is None  # bad crc
    assert ilink.Status.parse(b"") is None
