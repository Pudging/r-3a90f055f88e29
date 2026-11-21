from heapq import heappop, heappush
import math


CELL = 5.0
EDGE = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
EXPANSIONS = 66


def segment(arena, start, end, stats):
    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)
    return not distance or arena.walkable(
        start, (dx / distance, dy / distance), distance,
        stats["radius"], stats["height"], stats["step_up"], False,
    )


def waypoints(arena, pos, goal, stats, zone):
    initial_distance = math.dist(pos[:2], goal[:2])
    zone_limit = max(zone["radius"] - 2, math.dist(pos[:2], zone["center"][:2]))
    gx, gy = (round(pos[i] / CELL) for i in range(2))
    frontier, costs, previous, points = [], {}, {}, {}

    def point(node):
        if node not in points:
            x, y = node[0] * CELL, node[1] * CELL
            points[node] = x, y, arena.ground(x, y)
        return points[node]

    def visit(node, parent, height, cost):
        if cost >= costs.get(node, math.inf):
            return
        target = point(node)
        if math.dist(target[:2], zone["center"][:2]) > zone_limit:
            return
        if not segment(arena, height, target, stats):
            return
        costs[node] = cost
        previous[node] = parent
        direct = math.dist(target[:2], goal[:2])
        heappush(frontier, (cost + 1.20 * direct, direct, cost, node))

    for x in range(gx - 1, gx + 2):
        for y in range(gy - 1, gy + 2):
            node = (x, y)
            visit(node, None, pos, math.dist(pos[:2], point(node)[:2]))

    best = None
    best_distance = initial_distance - 2.5
    for _ in range(EXPANSIONS):
        if not frontier:
            break
        _, direct, cost, node = heappop(frontier)
        if cost != costs[node]:
            continue
        start = point(node)
        if direct <= 16.0 and segment(arena, start, goal, stats):
            best = node
            break
        if direct < best_distance:
            best, best_distance = node, direct
        for dx, dy in EDGE:
            visit((node[0] + dx, node[1] + dy), node, start, cost + CELL * math.hypot(dx, dy))

    route = []
    while best is not None:
        route.append(point(best)[:2])
        best = previous[best]
    route.reverse()
    return route


class Navigation:
    def __init__(self):
        self.route = []
        self.next_search = 0.0
        self.target = None

    def steer(self, arena, obs, goal, mode, enemies):
        me = obs["self"]
        pos, stats = me["pos"], me["stats"]
        distance = math.dist(pos[:2], goal[:2])
        if (not me["grounded"] or mode not in {"zone", "loot", "capture"} or distance < 8
                or (mode != "zone" and any(math.dist(pos[:2], e["pos"][:2]) < 52 for e in enemies))):
            self.route.clear()
            return goal

        target = (goal[0], goal[1], mode)
        if self.target != target:
            self.route.clear()
            self.target = target
        if distance < 18 and segment(arena, pos, goal, stats):
            self.route.clear()
            return goal
        if not self.route:
            look = min(13, distance) / distance
            direct = tuple(pos[i] + (goal[i] - pos[i]) * look for i in range(2))
            if segment(arena, pos, direct, stats):
                return goal
        if obs["t"] >= self.next_search and not self.route:
            self.route = waypoints(arena, pos, goal, stats, obs["zone"])
            self.next_search = obs["t"] + 0.65

        for i in range(len(self.route) - 1, -1, -1):
            cursor = self.route[i]
            if 2.8 < math.dist(pos[:2], cursor) <= 15.0 and segment(arena, pos, cursor, stats):
                self.route = self.route[i:]
                return cursor
        self.route.clear()
        return goal
