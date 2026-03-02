import base64
from pathlib import Path

import pytest
from PIL import Image

from backend import images as image_module
from backend.images import (
    decode_image_payload,
    prepare_scene_start_images,
    prepare_scene_tail_image,
    save_payload_as_png,
)
from backend.models import EncodedImagePayload


def test_prepare_scene_start_images_resizes(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    tmp_dir = tmp_path / "tmp"

    for idx in (1, 2):
        img = Image.new("RGB", (1000, 500), color=(idx * 10, idx * 10, idx * 10))
        img.save(source_dir / f"scene{idx}.png")

    prepared = prepare_scene_start_images(source_dir, tmp_dir, scene_count=2, resolution="720x1280")

    assert len(prepared) == 2
    for path in prepared:
        assert Path(path).is_file()
        with Image.open(path) as resized:
            assert resized.size == (720, 1280)


def test_prepare_scene_tail_image(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    img = Image.new("RGB", (640, 640), color=(10, 10, 10))
    img.save(source_dir / "scene1_tail.png")

    prepared = prepare_scene_tail_image(source_dir, tmp_dir, scene_index=1, resolution="1280x720")
    assert prepared is not None
    prepared_path = Path(prepared)
    assert prepared_path.is_file()
    with Image.open(prepared_path) as resized:
        assert resized.size == (1280, 720)


def test_rejects_non_image_data_url(tmp_path):
    upload = EncodedImagePayload(
        filename="payload.txt",
        dataUrl="data:text/plain;base64,SGVsbG8=",
        contentType="text/plain",
    )
    with pytest.raises(ValueError, match="Unsupported upload type"):
        save_payload_as_png(upload, tmp_path / "bad.png")


def test_rejects_invalid_image_bytes(tmp_path):
    payload = base64.b64encode(b"not-a-real-image").decode("ascii")
    upload = EncodedImagePayload(
        filename="fake.png",
        dataUrl=f"data:image/png;base64,{payload}",
        contentType="image/png",
    )
    target = tmp_path / "fake.png"
    with pytest.raises(ValueError, match="not a valid image"):
        save_payload_as_png(upload, target)
    assert not target.exists()


def test_rejects_oversized_inline_payload(monkeypatch):
    monkeypatch.setattr(image_module, "JSON_IMAGE_MAX_UPLOAD_BYTES", 8)
    payload = base64.b64encode(b"123456789").decode("ascii")
    upload = EncodedImagePayload(
        filename="large.png",
        dataUrl=f"data:image/png;base64,{payload}",
        contentType="image/png",
    )
    with pytest.raises(ValueError, match="exceeds"):
        decode_image_payload(upload)
