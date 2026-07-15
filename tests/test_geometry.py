import os
import sys
import types

from panda3d.core import PNMImage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dune_buggy_sandbox import make_box, make_wedge, make_torus, DuneBuggy, TERRAIN_H


def test_make_box_geometry():
    np = make_box(1.0, 2.0, 3.0, (1, 0, 0, 1))
    geom = np.node().getGeom(0)
    prim = geom.getPrimitive(0)
    assert prim.getNumPrimitives() == 12          # 6 faces * 2 triangles
    assert geom.getVertexData().getNumRows() == 24  # 6 faces * 4 verts (no sharing)


def test_make_wedge_points():
    width, length, height = 4.0, 5.0, 6.0
    np, points = make_wedge(width, length, height)
    assert len(points) == 6
    assert all(len(p) == 3 for p in points)
    xs = [abs(p[0]) for p in points]
    zs = [p[2] for p in points]
    assert max(xs) == width / 2.0
    assert max(zs) == height
    ys = [abs(p[1]) for p in points]
    assert max(ys) == length / 2.0


def test_make_torus_triangle_count():
    seg_major, seg_minor = 8, 6
    np, mesh = make_torus(10.0, 1.0, seg_major, seg_minor)
    assert mesh.getNumTriangles() == seg_major * seg_minor * 2


def _fake_dune_buggy(size, offset, fill_value):
    img = PNMImage(size, size, 1)
    img.fill(fill_value, fill_value, fill_value)
    return types.SimpleNamespace(hm_img=img, hm_size=size, hm_offset=offset)


def test_ground_z_reads_expected_pixel():
    size = 16
    offset = size / 2.0 - 0.5
    img = PNMImage(size, size, 1)
    for y in range(size):
        for x in range(size):
            img.setGray(x, y, x / (size - 1))
    fake_self = types.SimpleNamespace(hm_img=img, hm_size=size, hm_offset=offset)

    wx, wy = 3 - offset, 0 - offset
    expected = img.getGray(3, 0) * TERRAIN_H - TERRAIN_H / 2.0
    assert DuneBuggy.ground_z(fake_self, wx, wy) == expected


def test_ground_z_clamps_out_of_bounds():
    size = 16
    offset = size / 2.0 - 0.5
    img = PNMImage(size, size, 1)
    for y in range(size):
        for x in range(size):
            img.setGray(x, y, x / (size - 1))
    fake_self = types.SimpleNamespace(hm_img=img, hm_size=size, hm_offset=offset)

    edge_wx = (size - 1) - offset
    within_bounds = DuneBuggy.ground_z(fake_self, edge_wx, 0 - offset)
    far_out_of_bounds = DuneBuggy.ground_z(fake_self, 10000, 0 - offset)

    assert far_out_of_bounds == within_bounds


def test_ground_z_clamps_negative_out_of_bounds():
    size = 16
    offset = size / 2.0 - 0.5
    img = PNMImage(size, size, 1)
    for y in range(size):
        for x in range(size):
            img.setGray(x, y, x / (size - 1))
    fake_self = types.SimpleNamespace(hm_img=img, hm_size=size, hm_offset=offset)

    edge_wx = 0 - offset
    within_bounds = DuneBuggy.ground_z(fake_self, edge_wx, 0 - offset)
    far_out_of_bounds = DuneBuggy.ground_z(fake_self, -10000, 0 - offset)

    assert far_out_of_bounds == within_bounds
