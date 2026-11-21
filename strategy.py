"""Placement policy with fixed-map pathfinding and avoidance of crowded encounters."""
import heapq
import math
from pathlib import Path
import struct
import zlib

from base_policy import Strategy as Base, _distance_xy


class Navigation:
    def __init__(self):
        data = Path(__file__).with_name("nav.bin").read_bytes()
        self.nx, self.ny, self.x, self.y, self.step = struct.unpack_from("<IIfff", data)
        self.wall = zlib.decompress(data[20:])
        self.edges = {}
        for node, solid in enumerate(self.wall):
            if solid:
                continue
            adjacent = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    target = node + dx + dy * self.nx
                    if self.wall[target] or (dx and dy and (self.wall[node+dx] or self.wall[node+dy*self.nx])):
                        continue
                    adjacent.append((target, math.hypot(dx, dy)))
            self.edges[node] = adjacent

    def point(self, n):
        return (self.x + n % self.nx * self.step, self.y + n // self.nx * self.step)

    def nearest(self, p):
        i = max(1, min(self.nx-2, round((p[0]-self.x)/self.step)))
        j = max(1, min(self.ny-2, round((p[1]-self.y)/self.step)))
        cell = j*self.nx + i
        if cell in self.edges:
            return cell
        for radius in range(1, 8):
            candidates = []
            for y in range(max(1, j-radius), min(self.ny-1, j+radius+1)):
                for x in range(max(1, i-radius), min(self.nx-1, i+radius+1)):
                    if max(abs(x-i), abs(y-j)) != radius:
                        continue
                    n = y*self.nx+x
                    if n in self.edges:
                        candidates.append(n)
            if candidates:
                return min(candidates, key=lambda n: _distance_xy(self.point(n), p))
        return None

    def route(self, start, end):
        if start is None or end is None:
            return []
        gx, gy = end % self.nx, end // self.nx
        cost, parent = {start: 0}, {start: None}
        best = start
        estimate = lambda n: math.hypot(gx - n % self.nx, gy - n // self.nx)
        queue = [(estimate(start), 0, start)]
        while queue and len(parent) < 2800:
            _, sofar, node = heapq.heappop(queue)
            if node == end:
                best = node
                break
            if sofar > cost[node]:
                continue
            if estimate(node) < estimate(best):
                best = node
            for other, length in self.edges[node]:
                new_cost = sofar + length
                if new_cost >= cost.get(other, math.inf):
                    continue
                cost[other] = new_cost
                parent[other] = node
                heapq.heappush(queue, (new_cost + 1.12*estimate(other), new_cost, other))
        path = []
        while best != start:
            path.append(self.point(best))
            best = parent[best]
        path.reverse()
        return path


class Strategy(Base):
    def __init__(self, hello):
        super().__init__(hello)
        # Windows' official checker may send a host-normalized /arena path to Linux.
        if self.arena is None and Path("/arena/v2/world.json").is_file():
            from base_policy import _Arena
            self.arena = _Arena("/arena")
        self.nav = Navigation()
        self.route = []
        self.waypoint_time = -100
        self.destination = None
        self.anchor = None
        self.anchor_time = -100

    def _destination(self, obs, stats, enemies, pickups, recovering, healing):
        me, z, now = obs["self"], obs["zone"], obs["t"]
        p = me["pos"]
        if self.anchor is None or now >= self.anchor_time:
            # Select a point deep enough to survive the next shrink but away from crowded areas.
            nx, ny = z["next_center"]
            radius = z["next_radius"]
            depth = max(0, min(radius-8, radius * 0.70))
            picks = [(nx, ny)]
            for i in range(16):
                angle = math.tau*i/16
                picks.append((nx+depth*math.cos(angle), ny+depth*math.sin(angle)))
            if _distance_xy(p,(nx,ny)) <= max(0, radius-10):
                picks.append(p[:2])
            def rating(q):
                n = self.nav.nearest(q)
                if n is None:
                    return -1e5
                at = self.nav.point(n)
                threat = min((_distance_xy(at, e["pos"]) for e in enemies), default=190)
                # Nearby walls provide somewhere to disengage as the ring closes.
                neighboring_walls = 8 - len(self.nav.edges[n])
                return min(threat,190) * 0.60 - _distance_xy(at,p)*0.29 - _distance_xy(at,q)*0.9 + neighboring_walls*2.0
            self.anchor = max(picks, key=rating)
            self.anchor_time = min(now+6, max(now+0.5,z["shrink_end"]+0.1))
        outer = z["radius"] - _distance_xy(p,z["center"])
        next_depth = z["next_radius"] - _distance_xy(p,z["next_center"])
        if not me["in_zone"] or outer < min(11,z["radius"]*0.4) or next_depth < min(8,z["next_radius"]*0.25):
            return self.anchor, "zone", min(2, z["next_radius"]*0.1)
        nearest = min(enemies, key=lambda e:_distance_xy(p,e["pos"]), default=None)
        if nearest and _distance_xy(p,nearest["pos"]) < (135 if recovering or len(enemies)>=2 else 98):
            # Evaluate game-space moves against every currently tracked opponent, including their game aim.
            best = None
            for i in range(12):
                angle = i*math.tau/12
                span = min(32, max(6,z["radius"]*0.50))
                q=(p[0]+span*math.cos(angle),p[1]+span*math.sin(angle))
                n = self.nav.nearest(q)
                if n is None:
                    continue
                at = self.nav.point(n)
                dist = sorted(_distance_xy(at,e["pos"]) for e in enemies[:4])
                value = dist[0] + 0.25*sum(min(d,140) for d in dist[1:]) - 1.2*_distance_xy(at,q)
                value -= 3.0*max(0,_distance_xy(at,z["center"])-z["radius"]+4)
                value -= 1.8*max(0,_distance_xy(at,z["next_center"])-z["next_radius"]+3)
                if best is None or value>best[0]:
                    best=(value,at)
            if best:
                return best[1],"recover",1.5
        base = super()._destination(obs,stats,enemies,pickups,recovering,healing)
        if base[1] in ("cube","supply","pickup","crate"):
            return base
        return self.anchor, "shelter", 3

    def _move(self,pos,goal,stop,stats,me,now):
        if _distance_xy(pos,goal) <= max(stop,7):
            return super()._move(pos,goal,stop,stats,me,now)
        if self.destination is None or _distance_xy(self.destination,goal)>12 or now-self.waypoint_time>2.5:
            self.route = self.nav.route(self.nav.nearest(pos),self.nav.nearest(goal))
            self.destination, self.waypoint_time = tuple(goal), now
        while self.route and _distance_xy(pos,self.route[0])<4.2:
            self.route.pop(0)
        if self.route:
            return super()._move(pos,self.route[0],0,stats,me,now)
        return super()._move(pos,goal,stop,stats,me,now)
