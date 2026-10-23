import numpy as np
import pytest
from PIL import Image

from rltracker.heatmap import density, save_heatmaps


def test_density_is_normalized_and_centered():
    rng = np.random.default_rng(0)
    h = rng.normal(2000, 100, 5000)
    v = rng.normal(-1000, 100, 5000)
    grid = density(h, v)
    assert grid.sum() == pytest.approx(1)
    row, col = np.unravel_index(grid.argmax(), grid.shape)
    # Cells are 100 uu; the grid starts at the corner of the field + goal depth.
    assert -4096 + row * 100 == pytest.approx(-1000, abs=200)
    assert -5120 - 880 + col * 100 == pytest.approx(2000, abs=200)


def test_density_of_nothing_is_empty():
    assert not density(np.array([]), np.array([])).any()


def test_save_heatmaps(doubles, tmp_path):
    paths = save_heatmaps(doubles, tmp_path)
    names = {p.stem for p in paths}
    assert {"overview", "ball", "touches"} <= names
    assert len(paths) == 3 + len(doubles.players)
    for path in paths:
        with Image.open(path) as image:
            assert image.width > 500
            # Not a blank image.
            assert np.unique(np.asarray(image.convert("L"))).size > 20
