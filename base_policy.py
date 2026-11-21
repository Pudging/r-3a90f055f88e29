"""Placement-first policy for the local mechfight/1 contest game."""

from __future__ import annotations

from array import array
from collections import defaultdict
import json
import math
from pathlib import Path
import sys


_UPGRADES = {
    "armor": 100,
    "plating": 95,
    "nanites": 85,
    "servos": 75,
    "laser_charge": 69,
    "gun_mag": 66,
    "laser_damage": 64,
    "lightning_range": 60,
    "jets": 57,
    "reactor": 55,
    "gun_damage": 52,
    "gun_rate": 51,
    "gyros": 46,
    "lightning_efficiency": 43,
    "capacitor": 39,
    "sensors": 34,
}
_WEAPON_VALUE = {"laser": 3, "lightning": 2, "mortar": 1}
_STEER_ANGLES = (0, 0.46, -0.46, 0.9, -0.9, 1.55, -1.55, math.pi)
_EPSILON = 1e-6


def _unit(x, y):
    length = math.hypot(x, y)
    return (x / length, y / length) if length > _EPSILON else (0.0, 0.0)


def _yaw_error(target, current):
    return math.remainder(target - current, math.tau)


def _distance_xy(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ring_contains(ring, x, y):
    inside = False
    last_x, last_y = ring[-2:]
    for index in range(0, len(ring), 2):
        next_x, next_y = ring[index:index + 2]
        if (last_y > y) != (next_y > y):
            hit_x = last_x + (next_x - last_x) * (y - last_y) / (next_y - last_y)
            inside ^= x < hit_x
        last_x, last_y = next_x, next_y
    return inside


class _Arena:
    """Small local collision approximation using the officially mounted game map."""

    cell_size = 24

    def __init__(self, folder):
        root = Path(folder)
        world = json.loads((root / "v2" / "world.json").read_text(encoding="utf-8"))
        self.bounds = world["bounds"]
        self.grid = world["terrain"]
        self.terrain = array("h")
        self.terrain.frombytes((root / "v2" / "terrain.i16").read_bytes())
        if sys.byteorder != "little":
            self.terrain.byteswap()
        if len(self.terrain) != self.grid["nx"] * self.grid["ny"]:
            raise ValueError("unexpected arena terrain grid")
        self.cells = defaultdict(list)
        for prism in world["prisms"]:
            ring = prism["ring"]
            low_x, high_x = min(ring[::2]), max(ring[::2])
            low_y, high_y = min(ring[1::2]), max(ring[1::2])
            entry = (low_x, high_x, low_y, high_y, prism)
            for gx in range(self._cell(low_x), self._cell(high_x) + 1):
                for gy in range(self._cell(low_y), self._cell(high_y) + 1):
                    self.cells[gx, gy].append(entry)

    def _cell(self, value):
        return math.floor(value / self.cell_size)

    def _ground(self, x, y):
        grid = self.grid
        gx = min(grid["nx"] - 1, max(0, (x - grid["minX"]) / grid["step"]))
        gy = min(grid["ny"] - 1, max(0, (y - grid["minY"]) / grid["step"]))
        i = min(grid["nx"] - 2, int(gx))
        j = min(grid["ny"] - 2, int(gy))
        fx, fy = gx - i, gy - j
        index = j * grid["nx"] + i
        upper = index + grid["nx"]
        lower_height = self.terrain[index] * (1 - fx) + self.terrain[index + 1] * fx
        upper_height = self.terrain[upper] * (1 - fx) + self.terrain[upper + 1] * fx
        return (lower_height * (1 - fy) + upper_height * fy) * grid.get("scale", 0.01)

    def _solids(self, x, y, feet, head):
        for low_x, high_x, low_y, high_y, prism in self.cells.get(
            (self._cell(x), self._cell(y)), ()
        ):
            if (
                low_x <= x <= high_x and low_y <= y <= high_y
                and prism["top"] > feet + 0.06 and prism["base"] < head
                and _ring_contains(prism["ring"], x, y)
                and not any(_ring_contains(hole, x, y) for hole in prism["holes"])
            ):
                yield prism

    def clear_at(self, point):
        x, y, z = point
        return z >= self._ground(x, y) and not any(self._solids(x, y, z, z + 0.07))

    def straight_clear(self, origin, target):
        length = math.dist(origin, target)
        samples = max(1, math.ceil(length / 2.0))
        # Probe at segment midpoints, including the space immediately beyond the muzzle.
        return all(
            self.clear_at(tuple(a + (b - a) * (index + 0.5) / samples for a, b in zip(origin, target)))
            for index in range(samples)
        )

    def walk_score(self, pos, direction, stats):
        """Return fractional local progress and whether a short hop can clear the obstacle."""
        x, y, z = pos
        dx, dy = direction
        step = stats.get("step_up", 1.2)
        height = stats.get("height", 9.0)
        radius = stats.get("radius", 2.5)
        width = min(radius * 0.78, 3.5)
        passable = 0
        for distance in (2.5, 5.0, 8.0):
            blocked = False
            hop = False
            for lateral in (0, -width, width):
                px = x + dx * distance - dy * lateral
                py = y + dy * distance + dx * lateral
                if not (
                    self.bounds["minX"] + radius <= px <= self.bounds["maxX"] - radius
                    and self.bounds["minY"] + radius <= py <= self.bounds["maxY"] - radius
                ):
                    return passable / 3, False
                ground = self._ground(px, py)
                top = max(ground, z - 0.7)
                for prism in self._solids(px, py, top, max(z, ground) + height):
                    if prism["base"] > max(z, ground) + 0.4:
                        return passable / 3, False
                    top = max(top, prism["top"])
                climb = top - z
                allowed = step + 0.25 * distance
                if climb > allowed:
                    blocked = True
                    hop = 0 < climb <= 6.0
                    if not hop:
                        break
            if blocked:
                return passable / 3, hop
            passable += 1
        return 1.0, False


class Strategy:
    """Deterministic decision loop using current game state and bounded local probes."""

    def __init__(self, hello):
        self.config = hello["config"]
        self.seed = hello.get("seed", 0)
        match = self.config.get("match", {})
        self.decision_s = match.get("decide_every", 2) / match.get("tick_hz", 20)
        self.arena = None
        folder = hello.get("map", {}).get("dir")
        if folder:
            try:
                self.arena = _Arena(folder)
            except (OSError, ValueError, KeyError):
                pass
        self.damage_time = -math.inf
        self.damage_bearing = None
        self.healing = False
        self.short_until = -math.inf
        self.last_heading = None
        self.last_t = None
        self.last_pos = None
        self.stuck_for = 0.0

    def _update_events(self, obs):
        now = obs["t"]
        for event in obs.get("events", ()):
            if event["type"] == "damaged" and event.get("bearing") is not None:
                self.damage_time = now
                self.damage_bearing = event["bearing"]
            if event["type"] == "effect" and event.get("effect") == "short":
                spec = self.config.get("weapons", {}).get("lightning", {})
                self.short_until = now + spec.get("short_s", 2.0)

    def _available_pickups(self, obs, me):
        if all(me["weapons"]):
            return []
        held = {weapon["kind"] for weapon in me["weapons"] if weapon}
        return [
            pickup for pickup in obs.get("pickups", ())
            if pickup["kind"] in _WEAPON_VALUE and pickup["kind"] not in held
            and abs(pickup["pos"][2] - me["pos"][2]) <= 4.0
        ]

    def _zone_goal(self, obs, stats):
        zone = obs["zone"]
        now = obs["t"]
        pos = obs["self"]["pos"]
        speed = stats.get("walk_speed", 11.7)
        edge = zone["radius"] - _distance_xy(pos, zone["center"])
        next_depth = zone["next_radius"] - _distance_xy(pos, zone["next_center"])
        retreat_margin = min(12.0, zone["next_radius"] * 0.45)
        desired_depth = min(30.0, zone["next_radius"] * 0.58)
        until_end = max(0.0, zone["shrink_end"] - now)
        required_distance = max(0.0, desired_depth - next_depth)
        urgent = (
            not obs["self"]["in_zone"] or edge < 6.0
            or (next_depth < retreat_margin and until_end < required_distance / speed + 10.0)
        )
        if not urgent:
            return None
        if edge < 6.0 and now < zone["shrink_start"]:
            center = zone["center"]
        else:
            center = zone["next_center"]
        return center

    @staticmethod
    def _foe_priority(actor, pos):
        distance = _distance_xy(actor["pos"], pos)
        hp_factor = actor.get("hp", 900) / max(1, actor.get("max_hp", 1400))
        return distance + 26 * hp_factor - (17 if actor.get("charging") else 0)

    def _destination(self, obs, stats, enemies, pickups, recovering, healing):
        me = obs["self"]
        pos = me["pos"]
        zone = obs["zone"]
        center = zone["next_center"]
        urgent = self._zone_goal(obs, stats)
        if urgent is not None and _distance_xy(pos, urgent) > 0.75:
            return urgent, "zone", 0.75
        if recovering:
            if enemies:
                dx, dy = _unit(pos[0] - enemies[0]["pos"][0], pos[1] - enemies[0]["pos"][1])
            else:
                dx, dy = -math.cos(self.damage_bearing), -math.sin(self.damage_bearing)
            if _distance_xy(pos, zone["center"]) > max(0.0, zone["radius"] - 38):
                ix, iy = _unit(zone["center"][0] - pos[0], zone["center"][1] - pos[1])
                dx, dy = dx + 1.5 * ix, dy + 1.5 * iy
            return (pos[0] + 32 * dx, pos[1] + 32 * dy), "recover", 0.0

        if enemies and _distance_xy(enemies[0]["pos"], pos) < 63:
            enemy = enemies[0]["pos"]
            dx, dy = _unit(pos[0] - enemy[0], pos[1] - enemy[1])
            side = 1 if (int(obs["t"] / 3) + self.seed) % 2 else -1
            return (pos[0] + 12 * (dx - dy * side), pos[1] + 12 * (dy + dx * side)), "space", 0.0

        opportunities = []
        for item in obs.get("cubes", ()):
            if abs(item["pos"][2] - pos[2]) <= 4.0:
                opportunities.append((item["pos"], "cube", 15, 58, 1.9))
        for item in pickups:
            value = 11 + 4 * _WEAPON_VALUE[item["kind"]]
            reach = self.config.get("loot", {}).get("pickup_radius", 4.0)
            opportunities.append((item["pos"], "pickup", value, 64, reach * 0.7))
        if not enemies or _distance_xy(enemies[0]["pos"], pos) > 85:
            for item in obs.get("drops", ()):
                if item["lands_in"] <= 3.0 and item["holder"] in (None, me["id"]):
                    value = 30 + 30 * (1 - me["hp"] / me["max_hp"])
                    opportunities.append((item["pos"], "supply", value, 90, item["radius"] * 0.45))
            if not healing and me["cubes"] < 6:
                for item in obs.get("crates", ()):
                    if abs(item["pos"][2] - pos[2]) <= 4.0:
                        opportunities.append((item["pos"], "crate", 7, 43, 11.0))

        best = None
        best_score = -math.inf
        for point, label, value, limit, stop in opportunities:
            distance = _distance_xy(point, pos)
            if distance > limit or _distance_xy(point, zone["center"]) > zone["radius"] - 4:
                continue
            if _distance_xy(point, center) > zone["next_radius"] + 18 and obs["t"] > zone["shrink_start"] - 8:
                continue
            score = value - distance * 0.32
            if enemies:
                threat_dist = min(_distance_xy(point, enemy["pos"]) for enemy in enemies)
                score -= max(0, 65 - threat_dist) * 0.8
            if score > best_score:
                best_score = score
                best = point, label, stop
        if best and best_score >= 0:
            return best

        desired_depth = min(26, zone["next_radius"] * 0.6)
        if _distance_xy(pos, center) > zone["next_radius"] - desired_depth:
            stop = max(0.0, zone["next_radius"] - desired_depth)
            return center, "rotate", stop
        return pos, "heal" if healing else "hold", 0.0

    def _move(self, pos, goal, stop, stats, me, now):
        distance = _distance_xy(pos, goal)
        if self.last_pos is not None and self.last_heading is not None:
            dt = min(0.25, max(0.0, now - self.last_t))
            if _distance_xy(pos, self.last_pos) < 1.5 * dt:
                self.stuck_for += dt
            else:
                self.stuck_for = 0.0
        self.last_pos, self.last_t = pos, now
        if distance <= stop + _EPSILON:
            self.last_heading = None
            self.stuck_for = 0.0
            return [0.0, 0.0], False
        speed = stats.get("walk_speed", 11.7)
        magnitude = min(1.0, (distance - stop) / max(2.0, 0.28 * speed))
        heading = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        hop = False
        if self.arena is not None and me["grounded"]:
            best_score = -math.inf
            best_heading = heading
            for offset in _STEER_ANGLES:
                angle = heading + offset
                direction = (math.cos(angle), math.sin(angle))
                progress, hop_option = self.arena.walk_score(pos, direction, stats)
                continuity = (
                    0.10 * math.cos(angle - self.last_heading) if self.last_heading is not None else 0.0
                )
                score = 2.3 * progress + math.cos(offset) + continuity
                if hop_option and me["energy"] >= 20 and self.stuck_for > 0.25:
                    score += 1.25
                if score > best_score:
                    best_heading, best_score, hop = angle, score, hop_option
                if offset == 0 and progress == 1 and self.stuck_for < 0.5:
                    break
            heading = best_heading
        if self.stuck_for > 1.6 and not hop:
            bias = 1 if (int(now / 2) + self.seed) % 2 else -1
            heading += 0.55 * bias
        self.last_heading = heading
        return [math.cos(heading) * magnitude, math.sin(heading) * magnitude], hop

    def _target_profile(self, target):
        if target.get("kind") == "mech":
            base = self.config.get("mech", {})
            size = 1 + self.config.get("cubes", {}).get("size_per_cube", 0.05) * target.get("cubes", 0)
            return size * base.get("height", 9.0), size * base.get("radius", 2.5)
        kind = "npc" if target.get("kind") == "npc" else "crates"
        body = self.config.get(kind, {})
        return body.get("height", 3.5 if kind == "npc" else 2.4), body.get("radius", 1.4)

    def _shot(self, me, target, weapon):
        kind = weapon["kind"]
        stats = dict(self.config.get("weapons", {}).get(kind, {}))
        stats.update(weapon.get("stats", {}))
        body_height, radius = self._target_profile(target)
        target_pos = target["pos"]
        target_vel = target.get("vel", (0.0, 0.0, 0.0))
        origin_height = me["stats"].get("muzzle_height", 7.0)
        if kind == "mortar":
            origin_height = me["stats"].get("height", 9.0) + 0.5
        origin = (me["pos"][0], me["pos"][1], me["pos"][2] + origin_height)
        midpoint = (target_pos[0], target_pos[1], target_pos[2] + body_height / 2)
        speed = stats.get("speed", 0.0)
        gravity = stats.get("gravity", 0.0) if kind == "mortar" else 0.0
        flight = 0.0
        delta = tuple(a - b for a, b in zip(midpoint, origin))
        horizontal = math.hypot(*delta[:2])
        reach = stats.get("range", math.inf)
        if kind == "mortar" and (speed <= 0 or gravity <= 0 or horizontal < stats.get("splash_radius", 7.0) * 2.3):
            return None
        for _ in range(4 if speed > 0 else 1):
            predicted = tuple(pos + vel * flight for pos, vel in zip(midpoint, target_vel))
            delta = tuple(a - b for a, b in zip(predicted, origin))
            horizontal = math.hypot(*delta[:2])
            if gravity > 0:
                disc = speed ** 4 - gravity * (gravity * horizontal ** 2 + 2 * delta[2] * speed ** 2)
                if disc < 0 or horizontal < _EPSILON:
                    return None
                pitch = math.atan2(speed * speed - math.sqrt(disc), gravity * horizontal)
                if flight > stats.get("ttl", 7.0):
                    return None
                flight = horizontal / (speed * math.cos(pitch))
            else:
                pitch = math.atan2(delta[2], horizontal)
                if speed > 0:
                    muzzle_offset = me["stats"].get("radius", 2.5) + 0.5
                    flight = max(0, math.hypot(horizontal, delta[2]) - muzzle_offset) / speed
        distance = math.sqrt(horizontal * horizontal + delta[2] * delta[2])
        if horizontal < _EPSILON or distance > reach:
            return None

        yaw = math.atan2(delta[1], delta[0])
        aim_limits = self.config.get("mech", {})
        if kind == "lightning":
            pitch = max(aim_limits.get("min_pitch", -0.7), min(pitch, aim_limits.get("max_pitch", 1.3)))
        elif not aim_limits.get("min_pitch", -0.7) <= pitch <= aim_limits.get("max_pitch", 1.3):
            return None
        if self.arena is not None:
            if gravity:
                direction = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
                samples = max(1, math.ceil(flight * speed / 3))
                if not all(
                    self.arena.clear_at((
                        origin[0] + speed * direction[0] * t,
                        origin[1] + speed * direction[1] * t,
                        origin[2] + speed * direction[2] * t - 0.5 * gravity * t * t,
                    ))
                    for t in (flight * (index + 0.5) / samples for index in range(samples))
                ):
                    return None
            elif not self.arena.straight_clear(origin, predicted):
                return None
        tolerance_yaw = math.atan2(radius * 0.8, max(1.0, horizontal))
        tolerance_pitch = math.atan2(body_height * 0.37, max(1.0, horizontal))
        if kind == "lightning":
            tolerance_yaw = math.radians(stats.get("cone_deg", 30)) * 0.44
            tolerance_pitch = math.pi
        return (yaw, pitch), tolerance_yaw, tolerance_pitch, stats

    def _combat(self, obs, me, enemies, reason, cease_fire):
        fire = [False, False]
        if cease_fire:
            return None, fire, None

        targets = list(enemies[:3])
        if not enemies:
            pos = me["pos"]
            nearby_crates = [
                item for item in obs.get("crates", ())
                if (reason == "crate" or me["cubes"] < 4) and _distance_xy(item["pos"], pos) <= 43
            ]
            nearby_npcs = [
                actor for actor in obs.get("mechs", ())
                if actor["kind"] == "npc" and _distance_xy(actor["pos"], pos) <= 34
            ]
            targets = sorted(nearby_crates + nearby_npcs, key=lambda item: _distance_xy(item["pos"], pos))[:2]
        shots = []
        now = obs["t"]
        for target in targets:
            for index, weapon in enumerate(me["weapons"]):
                if not weapon:
                    continue
                kind = weapon["kind"]
                if kind == "gun" and (weapon.get("ammo", 0) <= 0 or weapon.get("reload", 0) > 0):
                    continue
                if kind == "laser" and now < self.short_until:
                    continue
                if kind == "lightning":
                    cost = weapon.get("stats", {}).get("energy", self.config.get("weapons", {}).get(kind, {}).get("energy", 3))
                    if me["energy"] < cost:
                        continue
                shot = self._shot(me, target, weapon)
                if shot is None:
                    continue
                aim, yaw_tolerance, pitch_tolerance, stats = shot
                charge = weapon.get("charge", 0.0)
                ready = weapon.get("cooldown", 0.0) <= _EPSILON
                yaw_error = abs(_yaw_error(aim[0], me["aim"][0]))
                pitch_error = abs(aim[1] - me["aim"][1])
                aligned = yaw_error <= yaw_tolerance and pitch_error <= pitch_tolerance
                rating = (30 if target.get("kind") == "mech" else 0) - self._foe_priority(target, me["pos"]) * 0.16
                rating += {"laser": 8, "lightning": 7, "mortar": 5, "gun": 4}[kind]
                rating += (9 if aligned else 0) + (10 if ready else 0) + (35 if charge else 0)
                shots.append((rating, target, index, weapon, aim, yaw_tolerance, pitch_tolerance, stats, aligned))
            if shots and target.get("kind") != "mech":
                break
        if not shots:
            return None, fire, None

        _, target, _, _, aim, _, _, _, _ = max(shots, key=lambda shot: shot[0])
        for _, candidate, index, weapon, preferred, yaw_tol, pitch_tol, stats, aligned in shots:
            if candidate["id"] != target["id"]:
                continue
            kind = weapon["kind"]
            ready = weapon.get("cooldown", 0.0) <= _EPSILON
            sharing = abs(_yaw_error(aim[0], preferred[0])) <= yaw_tol and abs(aim[1] - preferred[1]) <= pitch_tol
            if kind == "laser":
                charge = weapon.get("charge", 0.0)
                full = stats.get("charge", 0.75)
                minimum = stats.get("min_charge", 0.15)
                if charge > 0:
                    lethal = target.get("hp", math.inf) <= stats.get("damage", 315) * max(
                        stats.get("min_frac", 0.35), min(1.0, charge / max(full, _EPSILON))
                    )
                    charged = charge >= max(minimum, full - self.decision_s * 0.5)
                    forcing = charge + self.decision_s >= full + stats.get("hold_s", 2.0)
                    fire[index] = not (forcing or (aligned and sharing and (charged or (lethal and charge >= minimum))))
                else:
                    nearly_aligned = abs(_yaw_error(preferred[0], me["aim"][0])) < 0.35 and abs(preferred[1] - me["aim"][1]) < 0.25
                    fire[index] = ready and sharing and nearly_aligned
            else:
                fire[index] = ready and aligned and sharing
        return aim, fire, target

    def decide(self, obs):
        """Return the action and a short movement/target explanation for debug drawing."""
        self._update_events(obs)
        me = obs["self"]
        stats = self.config.get("mech", {}) | me["stats"]
        pos = me["pos"]
        now = obs["t"]
        enemies = sorted(
            (actor for actor in obs.get("mechs", ()) if actor["kind"] == "mech"),
            key=lambda actor: self._foe_priority(actor, pos),
        )
        recent_damage = now - self.damage_time < 3.0
        # Keep a damage-free window after retreating so regen can restore a useful buffer.
        heal_threshold = 0.68 if self.healing else 0.46
        self.healing = me["hp"] <= heal_threshold * me["max_hp"]
        recovering = self.healing and (
            recent_damage or any(_distance_xy(actor["pos"], pos) < 125 for actor in enemies)
        )
        healing = self.healing and me["in_zone"]

        pickups = self._available_pickups(obs, me)
        goal, reason, stop = self._destination(obs, stats, enemies, pickups, recovering, healing)
        movement, hop = self._move(pos, goal, stop, stats, me, now)
        aim, fire, target = self._combat(obs, me, enemies, reason, recovering or healing)
        if aim is None:
            if recent_damage and self.damage_bearing is not None:
                aim = (self.damage_bearing, 0.0)
            elif enemies:
                lead = enemies[0]["pos"]
                aim = (math.atan2(lead[1] - pos[1], lead[0] - pos[0]), 0.0)
            elif reason not in ("hold", "supply", "heal"):
                aim = (math.atan2(goal[1] - pos[1], goal[0] - pos[0]), 0.0)
            else:
                aim = (math.remainder(now * 0.7 + (self.seed % 360) * math.pi / 180, math.tau), 0.0)

        drain = self.decision_s * stats.get("boost_drain", 17.0)
        moving = math.hypot(*movement) > 0.4
        run = moving and me["grounded"] and (
            reason == "zone" or (reason == "recover" and me["energy"] >= 18.0)
        )
        jump = bool(hop and moving and me["grounded"] and me["energy"] >= 20.0 and self.stuck_for >= 0.35)
        jet = not me["grounded"] and reason == "zone" and self.stuck_for > 0.25 and me["energy"] >= 22.0
        action = {
            "tick": obs["tick"],
            "move": movement,
            "aim": list(aim),
            "fire": fire,
            "boost": bool((run or jet) and me["energy"] >= drain),
            "jump": jump,
        }
        if obs.get("offer") and obs["offer"]["choices"]:
            choices = obs["offer"]["choices"][:3]
            action["pick"] = max(range(len(choices)), key=lambda index: _UPGRADES.get(choices[index]["id"], 0))

        reach = self.config.get("loot", {}).get("pickup_radius", 4.0)
        reachable = [item for item in pickups if _distance_xy(item["pos"], pos) <= reach]
        if reachable:
            best = max(
                reachable,
                key=lambda item: (_WEAPON_VALUE[item["kind"]], -_distance_xy(item["pos"], pos), -item["id"]),
            )
            action["take"] = best["id"]
        for weapon in me["weapons"]:
            if weapon and weapon["kind"] == "gun" and weapon.get("reload", 0) <= 0:
                magazine = weapon.get("stats", {}).get("mag", 12)
                ammo = weapon.get("ammo", 0)
                if ammo <= 0 or (target is None and ammo <= magazine / 3):
                    action["reload"] = True
        if target:
            reason = f"{reason} / {target.get('kind', 'crate')} {target['id']}"
        return action, reason
