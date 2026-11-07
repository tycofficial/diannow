"""Synthetic 480P profile and parallel conversion checks; no capture or network."""
import concurrent.futures
import threading
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import rtc_agent as module
from stream_profile import stream_bitrate, stream_dimensions


for source_width, source_height in [(1920, 1080), (3840, 2160), (1080, 1920), (640, 360)]:
    width, height = stream_dimensions(source_width, source_height, "480")
    assert 0 < width <= min(854, source_width)
    assert 0 < height <= min(480, source_height)
    assert width % 2 == height % 2 == 0
    assert abs(width / height - source_width / source_height) < 0.02

assert stream_dimensions(1920, 1080, "720") == (1280, 720)
assert stream_dimensions(1920, 1080, "native") == (1920, 1080)
assert stream_bitrate(852, 480, 60) == 1_500_000
assert stream_bitrate(852, 480, 30) == 750_000

camera = SimpleNamespace(is_capturing=True)
source = module.SharedFrameSource(camera, hardware=True, clock=lambda: 0)
raw = np.zeros((96, 128, 4), dtype=np.uint8)
source.snapshot = lambda after=None: (raw, 0, 1)
barrier = threading.Barrier(2)


def resize(image, size, interpolation):
    barrier.wait(timeout=2)
    return np.zeros((size[1], size[0], 4), dtype=np.uint8)


try:
    with patch.object(module, "cv2", SimpleNamespace(resize=resize, INTER_LINEAR=1)):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda size: source.get_yuv(*size)[0], [(64, 48), (32, 24)]))
    assert [value.shape for value in results] == [(48, 64, 4), (24, 32, 4)]
    assert source.conversions == 2
    same = [source.get_yuv(64, 48)[0] for _ in range(3)]
    assert same[0] is same[1] is same[2]
    assert source.conversions == 2
finally:
    source.close()

print("Verified 480P aspect ratio, bitrate budget, parallel conversion and same-resolution sharing.")
