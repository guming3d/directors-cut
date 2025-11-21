from pathlib import Path

from PIL import Image

from sora_workflow.images import prepare_scene_start_images


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
