"""Choose a nearby safe movement input when a visible attack crosses our route."""

import math


def attack_paths(arena, config, obs):
    me = obs["self"]
    x, y, z = me["pos"]
    vx, vy, vz = me["vel"]
    stats = me["stats"]
    height, radius = stats["height"], stats["radius"]
    paths = []
    for shot in obs["projectiles"]:
        px, py, pz = shot["pos"]
        dx, dy, dz = shot["vel"]
        speed = math.hypot(dx, dy)
        if speed < 1:
            continue
        ux, uy = dx / speed, dy / speed
        distance = (x - px) * ux + (y - py) * uy
        t = distance / max(1, speed - vx * ux - vy * uy)
        mortar = shot["kind"] == "mortar"
        if mortar:
            gravity = config["weapons"]["mortar"]["gravity"]
            # Approximate the shell's floor intersection, then check the actual
            # surface height there. A blast can land well beside its flight line.
            ground = arena.ground(x, y)
            for _ in range(2):
                discriminant = dz * dz + 2 * gravity * (pz - ground)
                if discriminant < 0:
                    break
                impact = (dz + math.sqrt(discriminant)) / gravity
                if not 0.04 < impact < 1.05:
                    break
                ix, iy = px + dx * impact, py + dy * impact
                ground = arena.ground(ix, iy)
            else:
                width = config["weapons"]["mortar"]["splash_radius"] + radius
                if abs(z - ground) < width and math.hypot(ix - x, iy - y) < width + stats["boost_speed"] * impact:
                    paths.append((impact, ix - x, iy - y, 0, 0, width, 2.5))
        if not 0.04 < t < 0.85:
            continue
        cross = (px - x) * -uy + (py - y) * ux
        if abs(cross) > radius + stats["boost_speed"] * t + 1:
            continue
        altitude = pz + dz * t - z - vz * t
        if mortar:
            altitude -= 0.5 * config["weapons"]["mortar"]["gravity"] * t * t
        if -0.5 < altitude < height + 0.5:
            paths.append((t, cross, 0, -uy, ux, radius + 0.6, 1.4))

    for enemy in obs["mechs"]:
        if not enemy.get("charging"):
            continue
        ex, ey, ez = enemy["pos"]
        yaw, pitch = enemy["aim"]
        ux, uy = math.cos(yaw), math.sin(yaw)
        distance = (x - ex) * ux + (y - ey) * uy
        cross = (ex - x) * -uy + (ey - y) * ux
        if not 0 < distance < config["weapons"]["laser"]["range"] or abs(cross) > radius + 8:
            continue
        scale = 1 + enemy.get("cubes", 0) * config["cubes"]["size_per_cube"]
        muzzle = ez + config["mech"]["muzzle_height"] * scale
        rise = distance * math.tan(pitch)
        if not z - 1.5 < muzzle + rise < z + height + 1.5:
            continue
        if not arena.clear_shot((ex, ey, muzzle), (distance * ux, distance * uy, rise), 1):
            continue
        for t, weight in ((0.28, 1.2), (0.50, 1.0)):
            paths.append((t, cross, 0, -uy, ux, radius + 1.0, weight))
    return paths


def displacement(me, move, boost, t):
    stats = me["stats"]
    speed = stats["boost_speed" if boost else "walk_speed"]
    if any(w and w.get("charge", 0) > 0 for w in me["weapons"]):
        speed *= stats["charge_move_mult"]
    vx, vy = me["vel"][:2]
    dx, dy = move[0] * speed - vx, move[1] * speed - vy
    ramp = math.hypot(dx, dy) / max(1, stats["ground_accel"])
    # Integrate velocity as the mech accelerates toward the command.
    gain = t - ramp / 2 if t >= ramp else t * t / (2 * ramp)
    return vx * t + dx * gain, vy * t + dy * gain


def exposure(me, move, boost, paths):
    loss = 0.0
    for t, a, b, nx, ny, width, weight in paths:
        dx, dy = displacement(me, move, boost, t)
        miss = abs(dx * nx + dy * ny - a) if nx or ny else math.hypot(dx - a, dy - b)
        loss += weight * max(0.0, 1.25 - miss / width)
    return loss


def guarded_move(arena, config, obs, move, boost, jump, goal, mode):
    me = obs["self"]
    if jump or not me["grounded"] or not me["in_zone"]:
        return move, boost
    paths = attack_paths(arena, config, obs)
    if not paths:
        return move, boost
    original_loss = exposure(me, move, boost, paths)
    if original_loss < 0.25:
        return move, boost
    stats, pos, zone = me["stats"], me["pos"], obs["zone"]
    t = max(path[0] for path in paths)
    # Compare the consequences over at least half a second so a single escape
    # does not roll us outside a nearly closed circle or a capture ring.
    horizon = max(0.5, t)
    ox, oy = displacement(me, move, boost, horizon)
    center = zone["center"]
    zone_limit = max(zone["radius"] - 0.5, math.hypot(pos[0] + ox - center[0], pos[1] + oy - center[1]))
    capture_limit = None
    if mode == "capture":
        ring = next((d for d in obs["drops"] if d["pos"] == goal), None)
        if ring and ring["lands_in"] <= 0 and math.dist(pos[:2], goal[:2]) < ring["radius"]:
            capture_limit = ring["radius"] - 0.4
    best_move, best_boost, best_score = move, boost, original_loss - 0.18
    # Include the baseline direction itself: sometimes accelerating is enough.
    angles = [math.atan2(move[1], move[0])] if any(move) else []
    angles.extend(k * math.pi / 6 for k in range(12))
    for angle in angles:
        heading = math.cos(angle), math.sin(angle)
        for fast in ((False, True) if me["energy"] > 15 else (False,)):
            risk = exposure(me, heading, fast, paths)
            dx, dy = displacement(me, heading, fast, horizon)
            deviation = math.hypot(dx - ox, dy - oy)
            score = risk + 0.035 * deviation + 0.06 * fast
            if score >= best_score:
                continue
            end = pos[0] + dx, pos[1] + dy
            if math.dist(end, center) > zone_limit or (capture_limit is not None and math.dist(end, goal[:2]) > capture_limit):
                continue
            length = math.hypot(dx, dy)
            if length < 0.1 or not arena.walkable(pos, (dx / length, dy / length), length + 0.4,
                                                  stats["radius"], stats["height"], stats["step_up"], False):
                continue
            best_move, best_boost, best_score = heading, fast, score
    return best_move, best_boost
