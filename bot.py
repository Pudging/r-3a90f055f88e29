"""Supplied-mech strategy for the simulated Mech Royale protocol."""

import math
from pathlib import Path
from random import Random

from arena import Arena
from guard import guarded_move
from lane import LanePlanner
from navigation import Navigation
import mechfight as mf


TAU = 2 * math.pi
WEAPON_VALUE = {"laser": 14, "mortar": 11, "lightning": 8, "gun": 4}
UPGRADE_VALUE = {
    "armor": 9, "plating": 8, "nanites": 8, "jets": 8, "servos": 7,
    "gun_damage": 7, "gun_mag": 7, "laser_charge": 8, "laser_damage": 8,
    "mortar_reload": 8, "mortar_splash": 7, "lightning_range": 8,
    "lightning_efficiency": 7, "reactor": 6, "capacitor": 5, "sensors": 5,
    "gyros": 6, "gun_rate": 6, "gun_velocity": 5, "mortar_velocity": 5,
}


def wrap(angle):
    return (angle + math.pi) % TAU - math.pi


def direction(dx, dy):
    distance = math.hypot(dx, dy)
    return (dx / distance, dy / distance) if distance > 0.001 else (0.0, 0.0)


def heading_to(a, b):
    return direction(b[0] - a[0], b[1] - a[1])


def horizontal(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bearing(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


def aim_for(vector):
    return (
        math.atan2(vector[1], vector[0]),
        math.atan2(vector[2], math.hypot(vector[0], vector[1])),
    )


def intercept(offset, velocity, speed):
    # Solve |offset + velocity * t|² = speed² * t² for the intercept.
    a = speed * speed - sum(v * v for v in velocity)
    b = sum(p * v for p, v in zip(offset, velocity))
    c = sum(p * p for p in offset)
    if a <= 0:
        return None
    return (b + math.sqrt(b * b + a * c)) / a


class Helios:
    name = "potato-guard-route"

    def __init__(self, hello):
        self.config = hello["config"]
        self.rng = Random(hello["seed"])
        self.navigation = Navigation()
        map_dir = Path(hello["map"]["dir"])
        # On a Windows host check.py can resolve the Linux /arena mount to C:\arena.
        if not (map_dir / "v2" / "world.json").is_file():
            map_dir = Path("/arena")
        self.arena = Arena(map_dir)
        self.side = self.rng.choice((-1, 1))
        self.target_id = None
        self.anchor = None
        self.anchor_time = 0.0
        self.last_damage = -100.0
        self.damage_bearing = None
        self.lanes = LanePlanner()

    def shot(self, me, target, weapon):
        kind = weapon["kind"]
        stats = weapon["stats"]
        target_pos = target["pos"]
        target_vel = target.get("vel", [0.0, 0.0, 0.0])
        target_kind = target["kind"]
        height = (
            self.config["mech"]["height"] * (1 + target.get("cubes", 0) * self.config["cubes"]["size_per_cube"])
            if target_kind == "mech" else self.config["npc" if target_kind == "npc" else "crates"]["height"]
        )
        muzzle = me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"]["muzzle_height"]
        offset = [target_pos[i] - muzzle[i] for i in range(3)]
        offset[2] += height * 0.55
        dist = math.hypot(*offset)
        if kind == "mortar":
            if horizontal(me["pos"], target_pos) <= max(16, 2 * stats["splash_radius"]):
                return None
            speed = stats["speed"]
            gravity = stats["gravity"]
            # Find the first ballistic crossing for the moving target, favoring short flight.
            previous = 0.0
            for step in range(1, 35):
                dt = step * 0.18
                vector = [offset[i] + target_vel[i] * dt for i in range(3)]
                # Land near a target's feet so a near miss can still splash on the ground.
                vector[2] += 0.5 * gravity * dt * dt - height * 0.35
                if math.hypot(*vector) <= speed * dt:
                    low, high = previous, dt
                    for _ in range(10):
                        dt = (low + high) * 0.5
                        vector = [offset[i] + target_vel[i] * dt for i in range(3)]
                        vector[2] += 0.5 * gravity * dt * dt - height * 0.35
                        if math.hypot(*vector) > speed * dt:
                            low = dt
                        else:
                            high = dt
                    velocity = [v / dt for v in vector]
                    angles = aim_for(vector)
                    if self.arena.clear_shot(muzzle, velocity, dt, gravity):
                        return angles, dt
                    break
                previous = dt
            return None
        if dist > stats["range"] * 0.99:
            return None
        if kind == "lightning":
            return (aim_for(offset), 0.0) if self.arena.clear_shot(muzzle, offset, 1.0) else None
        if kind == "gun":
            flight = intercept(offset, target_vel, stats["speed"])
            if flight is None or flight * stats["speed"] > stats["range"]:
                return None
            if flight > 0.001:
                offset = [p + v * flight for p, v in zip(offset, target_vel)]
        else:
            flight = 0.0
        angles = aim_for(offset)
        if not me["stats"]["min_pitch"] <= angles[1] <= me["stats"]["max_pitch"]:
            return None
        return (angles, flight) if self.arena.clear_shot(muzzle, offset, 1.0) else None

    def finish_bonus(self, target, damage):
        if target["kind"] != "mech" or "hp" not in target:
            return 0.0
        # Value damage that removes a gun from a fight within a partial magazine.
        rounds = max(0.0, target["hp"]) / damage
        return 18.0 / (1.0 + (rounds / 8.0) ** 2)

    def engage(self, obs, me, enemies):
        pos = me["pos"]
        action = {"aim": me["aim"], "fire": [False, False]}
        options = []
        finish_damage = None
        if len(enemies) > 1:
            # Preserve Guard Lane's choice whenever that choice could trigger its cover rule.
            cover_needed = (me["hp"] < me["max_hp"] * 0.58
                            and obs["t"] - self.last_damage < self.config["mech"]["regen_delay"] + 0.4
                            and any(me["hp"] < enemy.get("hp", me["max_hp"]) * 0.8 for enemy in enemies))
            if not cover_needed:
                finish_damage = next((w["stats"]["damage"] for w in me["weapons"] if w and w["kind"] == "gun"),
                                     self.config["weapons"]["gun"]["damage"])
        if not enemies:
            drones = [
                item for item in obs["mechs"]
                if item["kind"] == "npc" and horizontal(pos, item["pos"]) < 76
            ]
            crates = [
                dict(item, kind="crate") for item in obs["crates"]
                if horizontal(pos, item["pos"]) < 75
            ]
            enemies = sorted(drones + crates, key=lambda item: horizontal(pos, item["pos"]))[:3]
        for target in enemies[:5]:
            distance = horizontal(pos, target["pos"])
            finish = self.finish_bonus(target, finish_damage) if finish_damage else 0.0
            for slot, weapon in enumerate(me["weapons"]):
                if weapon is None:
                    continue
                kind = weapon["kind"]
                stats = weapon["stats"]
                if kind == "lightning" and me["energy"] < stats["energy"] + 9:
                    continue
                solution = self.shot(me, target, weapon)
                if solution is None:
                    continue
                angles, flight = solution
                if not me["stats"]["min_pitch"] <= angles[1] <= me["stats"]["max_pitch"]:
                    continue
                yaw_error = abs(wrap(angles[0] - me["aim"][0]))
                pitch_error = abs(angles[1] - me["aim"][1])
                ready = max(weapon["cooldown"], weapon.get("reload", 0))
                value = min(11.5, stats["damage"] * 0.042) - 3.6 * yaw_error - 1.8 * pitch_error
                value -= min(9, ready * 10) + distance * 0.025 + flight * 1.1
                value += (6 if target["kind"] == "mech" else -1) + finish
                if target["id"] == self.target_id:
                    value += 1.8
                if target.get("hp", 1000) < stats["damage"] * 1.3:
                    value += 5
                if kind == "laser":
                    value += 13 if weapon.get("charge", 0) > 0 else -1
                    if "mortar" in target.get("weapons", []):
                        value += 3
                if kind == "lightning" and target.get("charging", False):
                    value += 7
                options.append((value, slot, target, angles, distance))
        if not options:
            if self.damage_bearing is not None and obs["t"] - self.last_damage < 2.4:
                action["aim"] = [self.damage_bearing, 0.0]
            else:
                scanning = me["aim"][0] + 0.33 * self.side
                action["aim"] = [wrap(scanning), 0.0]
                if any(w and w["kind"] == "gun" and w.get("ammo", 100) <= 4 for w in me["weapons"]):
                    action["reload"] = True
            # Keep a live laser charge through a momentary loss of sight.
            for i, weapon in enumerate(me["weapons"]):
                if weapon and weapon["kind"] == "laser" and weapon.get("charge", 0) > 0:
                    action["fire"][i] = True
            return action, None
        _, _, chased, aim, _ = max(options, key=lambda option: option[0])
        self.target_id = chased["id"]
        action["aim"] = aim
        for slot, weapon in enumerate(me["weapons"]):
            if weapon is None:
                continue
            kind = weapon["kind"]
            stats = weapon["stats"]
            matching = [option for option in options if option[1] == slot and option[2]["id"] == chased["id"]]
            charge = weapon.get("charge", 0)
            if kind == "laser" and charge:
                action["fire"][slot] = True
            if not matching or weapon["cooldown"] > 0.11:
                continue
            _, _, target, angles, distance = matching[0]
            yaw_error = abs(wrap(angles[0] - me["aim"][0]))
            pitch_error = abs(angles[1] - me["aim"][1])
            degrees = math.radians(stats.get("cone_deg", 10)) * 0.94
            tolerance = (
                degrees if kind == "lightning"
                else max(0.012, min(0.18, (2.8 if target["kind"] == "mech" else 1.45) / max(distance, 4)))
            )
            commanded = abs(wrap(angles[0] - aim[0])) < tolerance and abs(angles[1] - aim[1]) < tolerance
            aligned = commanded and yaw_error < tolerance and (kind == "lightning" or pitch_error < tolerance)
            if kind == "laser":
                target_hp = target.get("hp", stats["damage"])
                threshold = stats["charge"] * (0.36 if target_hp < stats["damage"] * 0.50 else 0.95)
                action["fire"][slot] = (commanded or charge > 0) and not (
                    charge >= max(stats["min_charge"], threshold) and aligned
                )
            elif kind == "gun":
                action["fire"][slot] = aligned and weapon.get("reload", 0) <= 0.1 and weapon.get("ammo", 0) > 0
            else:
                action["fire"][slot] = aligned
        return action, chased

    def goal(self, obs, me, enemies, target):
        pos = me["pos"]
        zone = obs["zone"]
        center, next_center = zone["center"], zone["next_center"]
        present_margin = zone["radius"] - horizontal(center, pos)
        next_margin = zone["next_radius"] - horizontal(next_center, pos)
        seconds = zone["shrink_end"] - obs["t"]
        walking = me["stats"]["walk_speed"]
        if not me["in_zone"] or present_margin < 9 or next_margin < 10 - max(0, seconds - 6) * walking * 0.66:
            self.anchor = None
            return next_center, "zone"
        enemy_range = min((horizontal(pos, enemy["pos"]) for enemy in enemies), default=1000)
        health = me["hp"] / me["max_hp"]
        drops = []
        for drop in obs["drops"]:
            distance = horizontal(pos, drop["pos"])
            if horizontal(center, drop["pos"]) + 5 >= zone["radius"]:
                continue
            if drop.get("holder") == me["id"]:
                return drop["pos"], "capture"
            if not (drop["lands_in"] <= distance / walking + 7 and distance < 150):
                continue
            inside_enemy = any(horizontal(m["pos"], drop["pos"]) < 20 for m in enemies)
            if not inside_enemy and (enemy_range > 24 or distance < 10):
                drops.append((distance, drop["pos"]))
        if drops:
            return min(drops, key=lambda item: item[0])[1], "capture"
        if enemy_range > 22 or health > 0.65:
            held = {w["kind"] for w in me["weapons"] if w}
            items = [
                (horizontal(pos, p["pos"]) / 1.4, p["pos"])
                for p in obs["cubes"] if horizontal(pos, p["pos"]) < 80
                and abs(p["pos"][2] - pos[2]) < 15
                and horizontal(center, p["pos"]) < zone["radius"] - 3
            ]
            items.extend(
                (horizontal(pos, p["pos"]) - 10, p["pos"])
                for p in obs["pickups"]
                if p["kind"] not in held and p["kind"] != "gun" and horizontal(pos, p["pos"]) < (105 if len(held) < 2 else 24)
                and abs(p["pos"][2] - pos[2]) < 18
                and horizontal(center, p["pos"]) < zone["radius"] - 4
            )
            if items:
                return min(items, key=lambda item: item[0])[1], "loot"
        if target and target["kind"] == "mech":
            rx, ry = heading_to(pos, target["pos"])
            dist = horizontal(pos, target["pos"])
            preferred = 34 if any(w and w["kind"] == "lightning" for w in me["weapons"]) else 105
            advance = max(-0.95, min(0.73, (dist - preferred) / 46))
            if health < 0.46 and obs["alive"] > 2:
                advance = min(advance, -0.77)
            dx, dy = direction(rx * advance - ry * self.side * 0.75, ry * advance + rx * self.side * 0.75)
            x, y = pos[0] + 30 * dx, pos[1] + 30 * dy
            if horizontal(center, (x, y)) > zone["radius"] - 11 and zone["radius"] > 12:
                ix, iy = heading_to((x, y), center)
                x, y = x + ix * 22, y + iy * 22
            return (x, y), "fight"
        if target:
            if horizontal(pos, target["pos"]) > 25:
                return target["pos"], "farm"
            return pos, "farm"
        if self.anchor is None or horizontal(pos, self.anchor) < 10 or obs["t"] > self.anchor_time + 11:
            self.anchor_time = obs["t"]
            nx, ny = heading_to(center, pos)
            if not nx and not ny:
                nx = 1.0
            ring = max(5.0, min(zone["next_radius"] * 0.64, max(7.0, horizontal(next_center, pos) - 22)))
            angle = math.atan2(ny, nx) + self.rng.uniform(0.1, 0.55) * self.side
            self.anchor = next_center[0] + ring * math.cos(angle), next_center[1] + ring * math.sin(angle)
        return self.anchor, "patrol"

    def move(self, obs, me, goal, mode, routed=False):
        pos = me["pos"]
        distance = horizontal(pos, goal)
        if mode in ("capture", "farm") and distance < (3.2 if mode == "capture" else 1):
            return [0, 0], False, False
        if distance < 0.8:
            return [0, 0], False, False
        base = bearing(pos, goal)
        stats = me["stats"]
        airborne = not me["grounded"]
        danger = mode == "fight" and me["hp"] < me["max_hp"] * 0.63
        boost = me["energy"] > (8 if mode == "zone" else 23)
        boost = boost and (mode == "zone" or danger or (mode in ("capture", "loot") and distance > 19))
        horizon = min(13.0, max(4.2, distance), stats["boost_speed" if boost else "walk_speed"] * 0.75)
        if routed:
            horizon = min(horizon, distance)
        radius = stats["radius"]
        allowed = self.arena.walkable
        obstacles = []
        for delta in (0, 0.34, -0.34, 0.69, -0.69, 1.02, -1.02, 1.55, -1.55, 2.15, -2.15, math.pi):
            angle = base + delta * self.side
            heading = math.cos(angle), math.sin(angle)
            if not allowed(pos, heading, horizon, radius, stats["height"], stats["step_up"], airborne):
                continue
            risk = self.projectile_risk(obs, me, heading, boost)
            progress = math.cos(delta)
            momentum = sum(heading[i] * me["vel"][i] for i in range(2)) / max(1, stats["boost_speed"])
            obstacles.append((progress + 0.17 * momentum - risk, heading))
        if obstacles:
            heading = max(obstacles, key=lambda item: item[0])[1]
            scale = min(1.0, distance / 5) if routed or mode in ("capture", "loot", "farm") else 1.0
            jump = airborne and me["energy"] > 18 and goal[2:3] and goal[2] > pos[2] + 5
            return [v * scale for v in heading], bool(boost or jump), False
        needed = mode in ("zone", "loot", "capture") and me["energy"] > 31
        return [math.cos(base), math.sin(base)], bool(needed and airborne), bool(needed and not airborne)

    def projectile_risk(self, obs, me, heading, boost):
        x, y, z = me["pos"]
        stats = me["stats"]
        speed = stats["boost_speed" if boost else "walk_speed"]
        mx = 0.38 * me["vel"][0] + 0.62 * speed * heading[0]
        my = 0.38 * me["vel"][1] + 0.62 * speed * heading[1]
        risk = 0.0
        for projectile in obs["projectiles"]:
            px, py, pz = projectile["pos"]
            px -= x
            py -= y
            if px * px + py * py > 95 * 95:
                continue
            vx = projectile["vel"][0] - mx
            vy = projectile["vel"][1] - my
            horizon = -(px * vx + py * vy) / max(1, vx * vx + vy * vy)
            if not 0 < horizon < 0.72:
                continue
            radius = stats["radius"] + (7 if projectile["kind"] == "mortar" else 0.9)
            if (px + vx * horizon) ** 2 + (py + vy * horizon) ** 2 > radius * radius:
                continue
            pz += projectile["vel"][2] * horizon
            if projectile["kind"] == "mortar":
                pz -= 0.5 * self.config["weapons"]["mortar"]["gravity"] * horizon ** 2
                extra = radius
            else:
                extra = 0.6
            if z - extra < pz < z + stats["height"] + extra:
                risk += 1.6 if projectile["kind"] == "mortar" else 0.83
        return risk

    def act(self, obs):
        me = obs["self"]
        for event in obs["events"]:
            if event["type"] == "damaged" and event.get("bearing") is not None:
                self.last_damage = obs["t"]
                self.damage_bearing = event["bearing"]
        nearby = sorted(
            (enemy for enemy in obs["mechs"] if enemy["kind"] == "mech"),
            key=lambda enemy: horizontal(me["pos"], enemy["pos"]) - (18 if enemy["id"] == self.target_id else 0),
        )
        action, target = self.engage(obs, me, nearby)
        goal, mode = self.goal(obs, me, nearby, target)
        waypoint = self.navigation.steer(self.arena, obs, goal, mode, nearby)
        move, boost, jump = self.move(obs, me, waypoint, mode, routed=waypoint is not goal)
        move = self.lanes.choose(self.arena, self.config, obs, nearby, target, mode,
                                 move, boost, jump, self.last_damage)
        move, boost = guarded_move(self.arena, self.config, obs, move, boost, jump, goal, mode)
        action.update(tick=obs["tick"], move=move, boost=boost, jump=jump)
        if obs["offer"]:
            action["pick"] = max(
                range(len(obs["offer"]["choices"])),
                key=lambda i: UPGRADE_VALUE.get(obs["offer"]["choices"][i]["id"], 4),
            )
        held = {w["kind"] for w in me["weapons"] if w}
        loot = [
            p for p in obs["pickups"]
            if horizontal(p["pos"], me["pos"]) < self.config["loot"]["pickup_radius"]
            and abs(p["pos"][2] - me["pos"][2]) < 4 and p["kind"] not in held
        ]
        if loot:
            best = max(loot, key=lambda p: WEAPON_VALUE[p["kind"]])
            if None in me["weapons"]:
                action["take"] = best["id"]
            else:
                secondary = 1 if me["weapons"][0]["kind"] == "gun" else 0
                old = me["weapons"][secondary]
                if WEAPON_VALUE[best["kind"]] > WEAPON_VALUE[old["kind"]] and not old.get("charge", 0):
                    if not nearby or horizontal(me["pos"], nearby[0]["pos"]) > 18:
                        action.update(take=best["id"], drop=secondary)
        return action


if __name__ == "__main__":
    mf.run(Helios)
