"""Compare short ground routes against solid cover in the campus map."""

import math

from guard import displacement


class LanePlanner:
    def __init__(self):
        self.use_cover = True
        self.keep_pressure = True
        self.until = -1
        self.context = None
        self.heading = None

    def choose(self, arena, config, obs, enemies, target, mode, move, boost, jump, damage_time):
        me, pos = obs["self"], obs["self"]["pos"]
        if (obs["alive"] <= 2 or jump or not me["grounded"] or not me["in_zone"]
                or mode in ("zone", "capture") or not enemies):
            self.context = None
            return move
        primary = target if target and target["kind"] == "mech" else enemies[0]
        health = me["hp"] / me["max_hp"]
        enemy_hp = primary.get("hp", me["max_hp"])
        defensive = (self.use_cover and health < 0.58 and me["hp"] < enemy_hp * 0.8
                     and obs["t"] - damage_time < config["mech"]["regen_delay"] + 0.4)
        attacking = self.keep_pressure and target is not None and mode == "fight"
        if not defensive and not attacking:
            self.context = None
            return move
        # Keep the normal strafe against an evenly matched, isolated opponent.
        if not defensive and len(enemies) == 1 and enemy_hp > me["hp"] * 0.7:
            self.context = None
            return move
        others = [e for e in enemies if e["id"] != primary["id"]][:2]
        context = (primary["id"], tuple(e["id"] for e in others), defensive, mode, boost)
        if self.context == context and obs["t"] < self.until:
            return self.heading if self.heading is not None else move
        self.context, self.until, self.heading = context, obs["t"] + 0.29, None
        stats = me["stats"]
        observers = []
        for enemy in [primary, *others]:
            ex, ey, ez = enemy["pos"]
            scale = 1 + enemy.get("cubes", 0) * config["cubes"]["size_per_cube"]
            muzzle = (ex, ey, ez + config["mech"]["muzzle_height"] * scale)
            incoming = math.atan2(pos[1] - ey, pos[0] - ex)
            facing = math.cos(incoming - enemy["aim"][0])
            weight = 0.95 + 0.30 * facing + 0.25 * enemy.get("charging", False)
            if enemy is primary and not defensive:
                weight = -1.55 if enemy_hp < me["hp"] * 0.7 else -1.15
            observers.append((muzzle, weight))

        def projected_cost(heading, check_walk):
            dx, dy = displacement(me, heading, boost, 1.25)
            length = math.hypot(dx, dy)
            if check_walk and length > 0.1 and not arena.walkable(
                pos, (dx / length, dy / length), length + 0.3,
                stats["radius"], stats["height"], stats["step_up"], False
            ):
                return math.inf
            x, y = pos[0] + dx, pos[1] + dy
            zone = obs["zone"]
            if math.dist((x, y), zone["center"]) > zone["radius"] - stats["radius"]:
                return math.inf
            ground = arena.ground(x, y)
            # Ground-level extrapolation is unreliable for a mech on a roof.
            if abs(pos[2] - arena.ground(*pos[:2])) > stats["step_up"] + 1:
                ground = pos[2]
            z = ground + stats["height"] * 0.6
            cost = 0.
            for muzzle, weight in observers:
                ray = (x - muzzle[0], y - muzzle[1], z - muzzle[2])
                if arena.clear_shot(muzzle, ray, 1):
                    cost += weight
            return cost

        baseline = projected_cost(move, False)
        minimum = sum(min(0, weight) for _, weight in observers)
        if baseline <= minimum + 0.01 or math.isinf(baseline):
            return move
        angle = math.atan2(move[1], move[0])
        best_cost = baseline - 0.27
        for delta in (0.66, -0.66, 1.30, -1.30, 2.1, -2.1, math.pi):
            heading = (math.cos(angle + delta), math.sin(angle + delta))
            cost = projected_cost(heading, True) + 0.23 * (1 - math.cos(delta))
            if cost < best_cost:
                best_cost, self.heading = cost, heading
        return self.heading if self.heading is not None else move
