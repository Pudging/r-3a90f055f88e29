"""Independent Mech Royale controller for Burrow's shelter-navigation experiment."""
import math

import mechfight
from shelter import ShelterPlanner, horizontal


def angle_difference(left, right):
    return math.remainder(left-right, math.tau)


class BurrowIndependent:
    name = "potato-burrow-independent"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.navigation = ShelterPlanner()
        self.scan_phase = (hello["seed"] % 4096) * math.tau / 4096
        self.watched = None
        self.alert = None
        self.last_alert = -100
        self.last_move = None
        self.stationary_since = None
        self.jet_until = -1

    def movement(self, obs, goal, mode):
        me, now = obs["self"], obs["t"]
        pos = me["pos"]
        dx, dy = goal[0]-pos[0], goal[1]-pos[1]
        distance = math.hypot(dx, dy)
        if distance < (2.0 if mode == "capture" else 0.55):
            self.stationary_since = None
            self.last_move = None
            return [0, 0], False, False
        speed = me["stats"]["walk_speed"]
        urgent = mode == "zone" and me["energy"] > 24
        if urgent:
            speed = me["stats"]["boost_speed"]
        # Use velocity feedback to brake and turn at the corners of the shelter path.
        command = min(1.0, distance / (speed*0.36))
        ux = dx/distance*command - 0.19*me["vel"][0]/speed
        uy = dy/distance*command - 0.19*me["vel"][1]/speed
        scale = max(1.0, math.hypot(ux,uy))
        move = [ux/scale, uy/scale]
        if self.last_move and distance > 3 and math.hypot(*me["vel"][:2]) < 1.2:
            if self.stationary_since is None:
                self.stationary_since = now
        else:
            self.stationary_since = None
        self.last_move = move
        stuck = self.stationary_since is not None and now-self.stationary_since > 0.9
        if stuck and me["grounded"] and me["energy"] > 42 and now > self.jet_until + 1.5:
            self.jet_until = now + 1.4
            self.navigation.next_plan = now
            return move, False, True
        use_boost = urgent and distance > 4 if me["grounded"] else now < self.jet_until
        return move, bool(use_boost), False

    def notice(self, obs):
        now = obs["t"]
        hits = [e for e in obs["events"] if e["type"] == "damaged" and e.get("bearing") is not None]
        if hits:
            self.alert = max(hits, key=lambda e:e["amount"])["bearing"]
            self.last_alert = now
            return
        if now-self.last_alert < 1.2:
            return
        for sound in obs["sounds"]:
            if sound["dist"] == "near" and sound["kind"] in {"laser_charge","gun","lightning","mortar"}:
                self.alert = sound["bearing"]
                self.last_alert = now - sound["age"]
                break

    def choose_actor(self, obs, players):
        me = obs["self"]
        grid = self.navigation.map
        node = grid.nearest(me["pos"])
        actors = list(players)
        if not players:
            actors.extend(e for e in obs["mechs"] if e["kind"]=="npc" and horizontal(e["pos"],me["pos"])<33)
            if me["hp"] > 0.66*me["max_hp"]:
                actors.extend(dict(c,kind="crate") for c in obs["crates"] if horizontal(c["pos"],me["pos"])<27)
        best = None
        best_cost = math.inf
        for actor in actors:
            x, y = actor["pos"][:2]
            dx, dy = x-me["pos"][0], y-me["pos"][1]
            bearing = math.atan2(dy,dx)
            distance = math.hypot(dx,dy)
            openness = grid.visibility(node, (x,y))[1] if node is not None else 1
            if openness == 0 and distance > 18:
                continue
            cost = distance + 42*(1-openness)
            cost += 10*abs(angle_difference(bearing, me["aim"][0]))
            cost -= 14 if actor["id"] == self.watched else 0
            cost -= 28 if actor.get("charging") else 0
            if self.alert is not None and obs["t"]-self.last_alert < 1:
                cost -= 25*math.cos(angle_difference(bearing,self.alert))
            if actor.get("hp",math.inf) < 175:
                cost -= 25
            if cost < best_cost:
                best, best_cost = actor, cost
        return best

    def direct_weapon(self, me, actor, weapon):
        p, q = me["pos"], actor["pos"]
        kind, params = weapon["kind"], weapon["stats"]
        distance = horizontal(p,q)
        if kind not in {"gun","laser","lightning"} or distance > params["range"]:
            return None
        size = self.cfg["mech" if actor["kind"]=="mech" else "npc" if actor["kind"]=="npc" else "crates"]
        height = size["height"]*(1+self.cfg["cubes"]["size_per_cube"]*actor.get("cubes",0))
        flight = distance/params["speed"] if kind=="gun" else 0
        velocity = actor.get("vel",(0,0,0))
        dx = q[0] + flight*velocity[0] - p[0]
        dy = q[1] + flight*velocity[1] - p[1]
        elevation = q[2]+height*0.52 + flight*velocity[2] - (p[2]+me["stats"]["muzzle_height"])
        yaw = math.atan2(dy,dx)
        pitch = math.atan2(elevation, math.hypot(dx,dy))
        if not me["stats"]["min_pitch"] <= pitch <= me["stats"]["max_pitch"] and kind != "lightning":
            return None
        tolerance = math.atan2(size["radius"]*0.88, max(1,distance))
        yaw_tol = math.radians(params["cone_deg"])*0.44 if kind=="lightning" else tolerance
        pitch_tol = math.pi if kind=="lightning" else math.atan2(height*0.43,max(1,distance))
        return [yaw,pitch], yaw_tol, pitch_tol

    def aim_and_fire(self, obs, actor):
        me = obs["self"]
        fire = [False,False]
        if actor is None:
            yaw = self.alert if self.alert is not None and obs["t"]-self.last_alert < 2.6 else self.scan_phase + obs["t"]*1.25
            return [math.remainder(yaw,math.tau),0], fire
        self.watched = actor["id"]
        solutions = []
        for slot, w in enumerate(me["weapons"]):
            if w is None or (w["kind"]=="lightning" and me["energy"] < w["stats"]["energy"]+3):
                continue
            solution = self.direct_weapon(me,actor,w)
            if solution is None:
                continue
            aim, yaw_tol, pitch_tol = solution
            ready_in = max(w["cooldown"],w.get("reload",0))
            priority = w["stats"]["damage"] / (0.5+ready_in)
            if w["kind"]=="gun" and w["ammo"]==0:
                priority = 0
            if w.get("charge",0)>0:
                priority *= 2
            solutions.append((priority,slot,aim,yaw_tol,pitch_tol))
        if not solutions:
            q, p = actor["pos"], me["pos"]
            return [math.atan2(q[1]-p[1],q[0]-p[0]),0], fire
        _, _, wanted, _, _ = max(solutions, key=lambda x:x[0])
        for _, slot, aim, yaw_tol, pitch_tol in solutions:
            w = me["weapons"][slot]
            aligned = abs(angle_difference(me["aim"][0],aim[0])) < yaw_tol and abs(me["aim"][1]-aim[1])<pitch_tol
            if w["kind"]=="laser":
                full = w["stats"]["charge"]
                fire[slot] = w["cooldown"] <= 0.1 and (w.get("charge",0)<full or not aligned)
            else:
                fire[slot] = aligned and w["cooldown"] <= 0.1
        return wanted, fire

    def equipment(self, action, obs, actor):
        me, offers = obs["self"], obs.get("offer")
        if offers and offers["choices"]:
            order = ("plating","armor","nanites","servos","gun_mag","laser_damage","gun_damage","gun_rate","jets","reactor")
            def preference(index):
                label = offers["choices"][index]["id"]
                return order.index(label) if label in order else len(order)
            action["pick"] = min(range(len(offers["choices"])),key=preference)
        if any(w and w["kind"]=="gun" and w["reload"]==0 and
               (w["ammo"]==0 or (actor is None and w["ammo"]<w["stats"]["mag"]*0.60)) for w in me["weapons"]):
            action["reload"] = True
        held = {w["kind"] for w in me["weapons"] if w}
        if len(held)<2:
            pickups = [item for item in obs["pickups"] if item["kind"] in {"laser","lightning"}-held and
                       horizontal(item["pos"],me["pos"])<=self.cfg["loot"]["pickup_radius"] and
                       abs(item["pos"][2]-me["pos"][2])<=4]
            if pickups:
                action["take"] = max(pickups,key=lambda item:item["kind"]=="laser")["id"]

    def act(self, obs):
        self.notice(obs)
        me = obs["self"]
        players = sorted((e for e in obs["mechs"] if e["kind"]=="mech"),
                         key=lambda e:horizontal(e["pos"],me["pos"]))
        goal, mode = self.navigation.goal(obs,me,players)
        move, boost, jump = self.movement(obs,goal,mode)
        actor = self.choose_actor(obs, players)
        aim, fire = self.aim_and_fire(obs, actor)
        action = dict(tick=obs["tick"],move=move,boost=boost,jump=jump,aim=aim,fire=fire)
        self.equipment(action,obs,actor)
        return action


if __name__ == "__main__":
    mechfight.run(BurrowIndependent)
