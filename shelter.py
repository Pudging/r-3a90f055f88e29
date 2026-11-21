"""Burrow's map-derived shelter planner, shared with its original ablation."""
from functools import lru_cache
import heapq
import math
from pathlib import Path
import struct
import zlib


def horizontal(first, second):
    return math.dist(first[:2], second[:2])


class ShelterMap:
    def __init__(self):
        data = Path(__file__).with_name("cover.bin").read_bytes()
        width, height, self.ox, self.oy, self.step = struct.unpack_from("<IIfff", data)
        self.width, self.height = width, height
        decoded = zlib.decompress(data[20:])
        size = width * height
        self.blocked, self.rays = decoded[:size], decoded[size:]
        self.points = [(self.ox + i % width * self.step, self.oy + i // width * self.step) for i in range(size)]
        self.neighbors = {}
        self.exposure = {}
        for i, blocked in enumerate(self.blocked):
            if blocked:
                continue
            self.neighbors[i] = []
            for dx, dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                j = i + dx + dy * width
                if self.blocked[j] or (dx and dy and (self.blocked[i+dx] or self.blocked[i+dy*width])):
                    continue
                self.neighbors[i].append((j, self.step * math.hypot(dx, dy)))
            distances = self.rays[i*32:i*32+32]
            self.exposure[i] = sum(min(110, d) for d in distances) / 32

    def nearest(self, point):
        x = max(1, min(self.width-2, round((point[0]-self.ox)/self.step)))
        y = max(1, min(self.height-2, round((point[1]-self.oy)/self.step)))
        node = y*self.width + x
        if node in self.neighbors:
            return node
        for radius in range(1, 8):
            choices = (j*self.width+i for j in range(max(1,y-radius),min(self.height-1,y+radius+1))
                       for i in range(max(1,x-radius),min(self.width-1,x+radius+1)))
            best = min((i for i in choices if i in self.neighbors), default=None,
                       key=lambda i: horizontal(self.points[i], point))
            if best is not None:
                return best
        return None

    def visibility(self, node, point):
        x, y = self.points[node]
        dx, dy = point[0]-x, point[1]-y
        distance = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx)*32/math.tau)
        start = node*32
        # Adjacent rays soften errors close to polygon edges.
        visible = sum(self.rays[start+((angle+offset)%32)] + 4 >= distance for offset in (-1,0,1))
        return distance, visible/3


class ShelterPlanner:
    def __init__(self):
        self.map = ShelterMap()
        self.route = []
        self.next_plan = -1
        self.previous_threats = ()
        self.landmark = None

    def plan(self, obs, me, enemies):
        grid = self.map
        start = grid.nearest(me["pos"])
        if start is None:
            self.route = []
            return
        zone, now = obs["zone"], obs["t"]
        # Look 28 seconds ahead and preserve room to get around a blocked building.
        begin = max(now, zone["shrink_start"])
        end = zone["shrink_end"]
        fraction = max(0, min(1, (now+28-begin)/max(0.1,end-begin)))
        center = tuple(a + fraction*(b-a) for a,b in zip(zone["center"],zone["next_center"]))
        radius = zone["radius"] + fraction*(zone["next_radius"]-zone["radius"])
        health = me["hp"]/me["max_hp"]
        threats = [e["pos"] for e in enemies[:4]]
        resources = []
        if not threats:
            held = {w["kind"] for w in me["weapons"] if w}
            resources.extend((p["pos"], 16) for p in obs["pickups"]
                             if p["kind"] in {"laser", "lightning"}-held and len(held)<2)
            resources.extend((p["pos"], 21) for p in obs["cubes"])
            resources = [(q, v) for q,v in resources if horizontal(q,me["pos"]) < 45 and abs(q[2]-me["pos"][2])<4]
            resources.extend((d["pos"], 29+28*(1-health)) for d in obs["drops"]
                             if d["lands_in"]<3 and d.get("holder") in (me["id"],None)
                             and horizontal(d["pos"],me["pos"])<60)
        resources = sorted(resources, key=lambda item: horizontal(item[0],me["pos"]))[:4]

        @lru_cache(maxsize=None)
        def risk(i):
            values = []
            for q in threats:
                d, visibility = grid.visibility(i, q)
                values.append(visibility * max(0, 190-d)*0.48 + max(0, 39-d)*0.50)
            return (max(values,default=0) + 0.4*sum(values))*(1.35-0.38*health)

        cost, previous = {start: 0.0}, {start: None}
        queue = [(0.0, start)]
        best, best_value = start, math.inf
        for _ in range(315):
            if not queue:
                break
            travel, i = heapq.heappop(queue)
            if travel != cost[i]:
                continue
            pos = grid.points[i]
            distance = horizontal(pos, center)
            next_dist = horizontal(pos, zone["next_center"])
            margin = min(13, radius*0.36)
            score = travel*0.24 + 0.68*grid.exposure[i] + risk(i)
            score += 4.8 * max(0, distance-radius+margin)
            score += 0.15 * max(0, next_dist-zone["next_radius"]+margin)
            score += 5.0 * max(0, horizontal(pos,zone["center"])-zone["radius"]+margin)
            # Prefer the outer half of a large circle while the center is crowded.
            score += 0.12 * max(0, min(radius*0.7, 240)-distance)
            if resources:
                score -= max(max(0, value - 1.3*horizontal(pos, q)) for q,value in resources)
            if score < best_value:
                best_value, best = score, i
            for other, length in grid.neighbors[i]:
                new_cost = travel + length * (1 + risk(other)*0.009 + 0.002*grid.exposure[other])
                if new_cost < cost.get(other, math.inf):
                    cost[other], previous[other] = new_cost, i
                    heapq.heappush(queue,(new_cost, other))
        self.landmark = grid.points[best]
        points = []
        while best != start:
            points.append(grid.points[best])
            best = previous[best]
        self.route = points[::-1]
        if resources:
            q, _ = min(resources, key=lambda item: horizontal(self.landmark, item[0]))
            if horizontal(self.landmark, q) < 6:
                self.route.append(q)

    def goal(self, obs, me, enemies):
        now = obs["t"]
        ids = tuple(sorted(e["id"] for e in enemies[:4]))
        if now >= self.next_plan or (ids != self.previous_threats and now >= self.next_plan-1):
            self.plan(obs, me, enemies)
            self.next_plan = now + (1.4 if enemies else 2.5)
            self.previous_threats = ids
        while self.route and horizontal(self.route[0], me["pos"]) < 3.0:
            self.route.pop(0)
        if self.route:
            # Skip collinear nodes to avoid slowing down mid-corridor.
            if len(self.route)>1:
                a,b = self.route[:2]
                ax,ay = a[0]-me["pos"][0],a[1]-me["pos"][1]
                bx,by = b[0]-a[0],b[1]-a[1]
                if ax*bx+ay*by > 0.96*math.hypot(ax,ay)*math.hypot(bx,by):
                    self.route.pop(0)
            return self.route[0], "zone" if enemies or not me["in_zone"] else "loot"
        return self.landmark if self.landmark is not None else obs["zone"]["next_center"], "capture"


