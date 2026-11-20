"""Terrain and indexed polygon collision queries for the v2 arena."""

from array import array
from collections import defaultdict
import json
import math
from pathlib import Path
import sys


CELL = 24


def edges(flat):
    points = list(zip(flat[::2], flat[1::2]))
    return tuple(zip(points, points[1:] + points[:1]))


def contains(x, y, ring):
    inside = False
    for (ax, ay), (bx, by) in ring:
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


class Arena:
    def __init__(self, arena_dir):
        root = Path(arena_dir) / "v2"
        world = json.loads((root / "world.json").read_text(encoding="utf-8"))
        self.bounds = world["bounds"]
        self.grid = world["terrain"]
        self.terrain = array("h")
        self.terrain.frombytes((root / "terrain.i16").read_bytes())
        if sys.byteorder != "little":
            self.terrain.byteswap()
        self.cells = defaultdict(list)
        for prism in world["prisms"]:
            ring = prism["ring"]
            min_x, max_x = min(ring[::2]), max(ring[::2])
            min_y, max_y = min(ring[1::2]), max(ring[1::2])
            slab = (
                min_x, max_x, min_y, max_y, prism["base"], prism["top"],
                edges(ring), tuple(edges(hole) for hole in prism["holes"]),
            )
            for ix in range(math.floor(min_x / CELL), math.floor(max_x / CELL) + 1):
                for iy in range(math.floor(min_y / CELL), math.floor(max_y / CELL) + 1):
                    self.cells[ix, iy].append(slab)

    def ground(self, x, y):
        grid = self.grid
        nx, ny = grid["nx"], grid["ny"]
        u = min(nx - 1.001, max(0, (x - grid["minX"]) / grid["step"]))
        v = min(ny - 1.001, max(0, (y - grid["minY"]) / grid["step"]))
        i, j = int(u), int(v)
        frac_x, frac_y = u - i, v - j
        row = j * nx + i
        a = self.terrain[row] * (1 - frac_x) + self.terrain[row + 1] * frac_x
        b = self.terrain[row + nx] * (1 - frac_x) + self.terrain[row + nx + 1] * frac_x
        return (a * (1 - frac_y) + b * frac_y) * 0.01

    def solid(self, x, y, bottom, top):
        for x0, x1, y0, y1, z0, z1, ring, holes in self.cells.get(
            (math.floor(x / CELL), math.floor(y / CELL)), ()
        ):
            if (
                x0 <= x <= x1 and y0 <= y <= y1 and bottom < z1 and top > z0
                and contains(x, y, ring) and not any(contains(x, y, hole) for hole in holes)
            ):
                return True
        return False

    def walkable(self, pos, heading, distance, radius, height, step, airborne):
        x, y, z = pos
        dx, dy = heading
        bounds = self.bounds
        for length in (distance * 0.30, distance * 0.62, distance):
            cx, cy = x + dx * length, y + dy * length
            if not (bounds["minX"] + radius < cx < bounds["maxX"] - radius):
                return False
            if not (bounds["minY"] + radius < cy < bounds["maxY"] - radius):
                return False
            for side in (0, -radius * 0.9, radius * 0.9):
                tx, ty = cx - dy * side, cy + dx * side
                ground = self.ground(tx, ty)
                allowed = step if airborne else step + length * 0.28
                if ground - z > allowed:
                    return False
                feet = max(ground, z)
                if self.solid(tx, ty, feet + step + 0.08, feet + height):
                    return False
        return True

    def clear_shot(self, start, velocity, duration, gravity=0):
        x, y, z = start
        vx, vy, vz = velocity
        distance = math.hypot(vx, vy, vz) * duration
        steps = max(2, min(125, math.ceil(distance / 1.9)))
        for i in range(steps):
            dt = duration * i / steps
            px, py = x + vx * dt, y + vy * dt
            pz = z + vz * dt - 0.5 * gravity * dt * dt
            if pz < self.ground(px, py) - 0.05 or self.solid(px, py, pz - 0.025, pz + 0.025):
                return False
        return True
