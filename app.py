from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_SIZE = 8000
ROOM_LIMIT = 10
INITIAL_HP = 500
MAX_INVENTORY = 7
INITIAL_CARDS = 2
CARD_SPAWN_INTERVAL = 12.0
STATE_BROADCAST_INTERVAL = 0.15
SECOND_TICK_INTERVAL = 1.0
ZONE_DURATION = 1200.0
ZONE_START_RADIUS = 7000.0
ZONE_END_RADIUS = 180.0
ZONE_CENTER = (MAP_SIZE / 2, MAP_SIZE / 2)


CARD_LIBRARY: list[dict[str, Any]] = [
    {"id": "fire_breath", "element": "fire", "skill": "Fire Breath", "cooldown": 4.0, "battery": 0},
    {"id": "fire_in_hole", "element": "fire", "skill": "Fire in the Hole", "cooldown": 9.0, "battery": 0},
    {"id": "blue_fire", "element": "fire", "skill": "Blue Fire", "cooldown": 12.0, "battery": 0},
    {"id": "fire_fusion", "element": "fire", "skill": "Fire Fusion", "cooldown": 15.0, "battery": 0},
    {"id": "wind_range", "element": "wind", "skill": "Wind Range", "cooldown": 20.0, "battery": 0},
    {"id": "wind_barrier", "element": "wind", "skill": "Wind Barrier", "cooldown": 18.0, "battery": 0},
    {"id": "wind_stun", "element": "wind", "skill": "Stun Gust", "cooldown": 14.0, "battery": 0},
    {"id": "usain_bolt", "element": "wind", "skill": "Usain Bolt", "cooldown": 16.0, "battery": 0},
    {"id": "electric_basic", "element": "electric", "skill": "Basic Shock", "cooldown": 3.0, "battery": 10},
    {"id": "recharge", "element": "electric", "skill": "Recharge", "cooldown": 8.0, "battery": 0},
    {"id": "electrocute", "element": "electric", "skill": "Electrocute", "cooldown": 10.0, "battery": 20},
    {"id": "superconductor", "element": "electric", "skill": "Superconductor", "cooldown": 25.0, "battery": 30},
]
CARD_BY_ID = {card["id"]: card for card in CARD_LIBRARY}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def random_position() -> tuple[float, float]:
    margin = 80
    return random.uniform(margin, MAP_SIZE - margin), random.uniform(margin, MAP_SIZE - margin)


def random_card_id() -> str:
    return random.choice(CARD_LIBRARY)["id"]


@dataclass
class WorldCard:
    instance_id: str
    card_id: str
    x: float
    y: float
    spawned_at: float


@dataclass
class WindBarrier:
    barrier_id: str
    owner_sid: str
    x: float
    y: float
    radius: float
    expires_at: float


@dataclass
class WindProjectile:
    projectile_id: str
    owner_sid: str
    x: float
    y: float
    dx: float
    dy: float
    speed: float
    expires_at: float
    stun_until: float
    damage: int


@dataclass
class Player:
    sid: str
    name: str
    x: float
    y: float
    hp: int = INITIAL_HP
    battery: float = 100.0
    inventory: list[str | None] = field(default_factory=list)
    cooldowns: dict[str, float] = field(default_factory=dict)
    input_state: dict[str, bool] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)
    speed_multiplier_until: float = 0.0
    range_multiplier_until: float = 0.0
    stun_until: float = 0.0
    barrier_until: float = 0.0
    superconductor_until: float = 0.0
    burn_until: float = 0.0
    burn_stacks: int = 0
    burn_tick: int = 0
    last_attacker: str | None = None

    def is_stunned(self, now: float) -> bool:
        return now < self.stun_until

    def move_speed(self, now: float) -> float:
        base = 290.0
        if now < self.speed_multiplier_until:
            return base * 1.8
        return base

    def range_multiplier(self, now: float) -> float:
        if now < self.range_multiplier_until:
            return 1.5
        return 1.0

    def skill_ready_at(self, skill_id: str) -> float:
        return self.cooldowns.get(skill_id, 0.0)

    def set_cooldown(self, skill_id: str, cooldown: float, now: float) -> None:
        if now < self.superconductor_until:
            cooldown *= 0.75
        self.cooldowns[skill_id] = now + cooldown


class GameRoom:
    def __init__(self) -> None:
        self.players: dict[str, Player] = {}
        self.cards: list[WorldCard] = []
        self.effects: list[dict[str, Any]] = []
        self.started_at = time.monotonic()
        self.barriers: list[WindBarrier] = []
        self.projectiles: list[WindProjectile] = []
        self.last_spawn_at = self.started_at
        self.last_state_broadcast = 0.0
        self.last_second_tick = 0.0
        self.running = False
        self.lock = Lock()

    def reset(self) -> None:
        self.players.clear()
        self.cards.clear()
        self.barriers.clear()
        self.projectiles.clear()
        self.effects.clear()
        self.started_at = time.monotonic()
        self.last_spawn_at = self.started_at
        self.last_state_broadcast = 0.0
        self.last_second_tick = 0.0
        self.running = True
        for _ in range(8):
            self.spawn_card()

    def ensure_running(self) -> None:
        if not self.running:
            self.reset()

    def add_player(self, sid: str, name: str | None = None) -> Player:
        self.ensure_running()
        if len(self.players) >= ROOM_LIMIT:
            raise ValueError("room full")
        px, py = random_position()
        player = Player(sid=sid, name=name or f"Player-{len(self.players) + 1}", x=px, y=py)
        player.inventory = [None] * MAX_INVENTORY
        for card_id in self.draw_initial_cards():
            self._fill_inventory_slot(player, card_id)
        self.players[sid] = player
        return player

    def remove_player(self, sid: str) -> None:
        self.players.pop(sid, None)
        if not self.players:
            self.running = False

    def draw_initial_cards(self) -> list[str]:
        return [random_card_id() for _ in range(INITIAL_CARDS)]

    def _first_empty_inventory_slot(self, player: Player) -> int | None:
        for index, card_id in enumerate(player.inventory):
            if card_id is None:
                return index
        return None

    def _fill_inventory_slot(self, player: Player, card_id: str) -> bool:
        slot = self._first_empty_inventory_slot(player)
        if slot is None:
            return False
        player.inventory[slot] = card_id
        return True

    def spawn_card(self) -> None:
        x, y = random_position()
        card = WorldCard(
            instance_id=f"card-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
            card_id=random_card_id(),
            x=x,
            y=y,
            spawned_at=time.monotonic(),
        )
        self.cards.append(card)

    def zone_radius(self, now: float | None = None) -> float:
        now = now or time.monotonic()
        elapsed = clamp(now - self.started_at, 0.0, ZONE_DURATION)
        progress = elapsed / ZONE_DURATION
        return ZONE_START_RADIUS + (ZONE_END_RADIUS - ZONE_START_RADIUS) * progress

    def zone_damage(self, now: float | None = None) -> float:
        now = now or time.monotonic()
        elapsed = clamp(now - self.started_at, 0.0, ZONE_DURATION)
        return 6.0 + (18.0 * (elapsed / ZONE_DURATION))

    def update(self) -> None:
        now = time.monotonic()
        self.ensure_running()

        if now - self.last_spawn_at >= CARD_SPAWN_INTERVAL:
            self.last_spawn_at = now
            if len(self.cards) < 24:
                self.spawn_card()

        if now - self.last_second_tick >= SECOND_TICK_INTERVAL:
            self.last_second_tick = now
            self.tick_second(now)

        self.tick_movement(now)
        self.tick_projectiles(now)
        self.tick_pickups()
        self.tick_expirations(now)
        self.tick_death_cleanup()

    def tick_movement(self, now: float) -> None:
        for player in self.players.values():
            if player.hp <= 0 or player.is_stunned(now):
                continue

            dx = 0.0
            dy = 0.0
            if player.input_state.get("left"):
                dx -= 1.0
            if player.input_state.get("right"):
                dx += 1.0
            if player.input_state.get("up"):
                dy -= 1.0
            if player.input_state.get("down"):
                dy += 1.0

            if dx == 0.0 and dy == 0.0:
                continue

            length = math.hypot(dx, dy) or 1.0
            speed = player.move_speed(now)
            player.x = clamp(player.x + (dx / length) * speed * 0.1, 0.0, MAP_SIZE)
            player.y = clamp(player.y + (dy / length) * speed * 0.1, 0.0, MAP_SIZE)

    def tick_pickups(self) -> None:
        for player in self.players.values():
            if self._first_empty_inventory_slot(player) is None:
                continue
            for card in list(self.cards):
                if distance((player.x, player.y), (card.x, card.y)) <= 44:
                    if not self._fill_inventory_slot(player, card.card_id):
                        continue
                    self.cards.remove(card)
                    self.effects.append({"type": "pickup", "x": card.x, "y": card.y, "ttl": 0.8})
                    break

    def tick_second(self, now: float) -> None:
        zone_radius = self.zone_radius(now)
        zone_center = ZONE_CENTER
        for player in self.players.values():
            if player.hp <= 0:
                continue

            if player.battery < 100:
                player.battery = min(100.0, player.battery + 5.0)

            if distance((player.x, player.y), zone_center) > zone_radius:
                player.hp -= int(self.zone_damage(now))
                self.effects.append({"type": "zone_hit", "x": player.x, "y": player.y, "ttl": 0.6})

            if now < player.burn_until and player.burn_stacks > 0:
                player.burn_tick += 1
                burn_damage = 8 * player.burn_stacks
                player.hp -= burn_damage
                self.effects.append({"type": "burn", "x": player.x, "y": player.y, "ttl": 0.7})

    def tick_projectiles(self, now: float) -> None:
        active_barriers = [barrier for barrier in self.barriers if barrier.expires_at > now]
        if len(active_barriers) != len(self.barriers):
            self.barriers = active_barriers

        active_projectiles: list[WindProjectile] = []
        for projectile in self.projectiles:
            if now >= projectile.expires_at:
                continue

            previous_x, previous_y = projectile.x, projectile.y
            projectile.x += projectile.dx * projectile.speed * 0.05
            projectile.y += projectile.dy * projectile.speed * 0.05

            if not self._segment_hits_barrier(previous_x, previous_y, projectile.x, projectile.y, active_barriers):
                victim = self._projectile_hit_player(projectile, previous_x, previous_y)
                if victim:
                    victim.stun_until = max(victim.stun_until, projectile.stun_until)
                    victim.hp -= projectile.damage
                    victim.last_attacker = projectile.owner_sid
                    self.effects.append({"type": "stun", "x": victim.x, "y": victim.y, "ttl": 0.9})
                    continue
                active_projectiles.append(projectile)
            else:
                self.effects.append({"type": "barrier_block", "x": projectile.x, "y": projectile.y, "ttl": 0.6})
        self.projectiles = active_projectiles

    def _segment_hits_barrier(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        barriers: list[WindBarrier],
    ) -> bool:
        for barrier in barriers:
            if self._distance_point_to_segment(barrier.x, barrier.y, start_x, start_y, end_x, end_y) <= barrier.radius:
                return True
        return False

    def _projectile_hit_player(self, projectile: WindProjectile, previous_x: float, previous_y: float) -> Player | None:
        for player in self.players.values():
            if player.sid == projectile.owner_sid or player.hp <= 0:
                continue
            if self._distance_point_to_segment(player.x, player.y, previous_x, previous_y, projectile.x, projectile.y) <= 28:
                return player
        return None

    @staticmethod
    def _distance_point_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
        segment_dx = x2 - x1
        segment_dy = y2 - y1
        if segment_dx == 0 and segment_dy == 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * segment_dx + (py - y1) * segment_dy) / (segment_dx * segment_dx + segment_dy * segment_dy)
        t = clamp(t, 0.0, 1.0)
        closest_x = x1 + t * segment_dx
        closest_y = y1 + t * segment_dy
        return math.hypot(px - closest_x, py - closest_y)

    def tick_expirations(self, now: float) -> None:
        for player in self.players.values():
            if now >= player.speed_multiplier_until:
                player.speed_multiplier_until = 0.0
            if now >= player.range_multiplier_until:
                player.range_multiplier_until = 0.0
            if now >= player.stun_until:
                player.stun_until = 0.0
            if now >= player.barrier_until:
                player.barrier_until = 0.0
            if now >= player.superconductor_until:
                player.superconductor_until = 0.0
            if now >= player.burn_until:
                player.burn_stacks = 0
                player.burn_tick = 0

        self.effects = [effect for effect in self.effects if effect["ttl"] > 0]
        for effect in self.effects:
            effect["ttl"] -= 0.1

    def register_wind_barrier(self, owner: Player, x: float, y: float) -> WindBarrier:
        barrier = WindBarrier(
            barrier_id=f"barrier-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
            owner_sid=owner.sid,
            x=clamp(x, 0.0, MAP_SIZE),
            y=clamp(y, 0.0, MAP_SIZE),
            radius=72.0,
            expires_at=time.monotonic() + 8.0,
        )
        self.barriers.append(barrier)
        return barrier

    def spawn_stun_projectile(self, owner: Player, target_x: float, target_y: float) -> WindProjectile:
        origin_x = owner.x
        origin_y = owner.y
        dx = target_x - origin_x
        dy = target_y - origin_y
        length = math.hypot(dx, dy) or 1.0
        projectile = WindProjectile(
            projectile_id=f"gust-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
            owner_sid=owner.sid,
            x=origin_x,
            y=origin_y,
            dx=dx / length,
            dy=dy / length,
            speed=760.0,
            expires_at=time.monotonic() + 1.8,
            stun_until=time.monotonic() + 2.0,
            damage=16,
        )
        self.projectiles.append(projectile)
        return projectile

    def tick_death_cleanup(self) -> None:
        for player in list(self.players.values()):
            if player.hp > 0:
                continue
            self.drop_inventory(player)
            player.hp = 0

    def drop_inventory(self, player: Player) -> None:
        dropped_any = False
        for index, card_id in enumerate(player.inventory):
            if card_id is None:
                continue
            self.cards.append(
                WorldCard(
                    instance_id=f"drop-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
                    card_id=card_id,
                    x=player.x,
                    y=player.y,
                    spawned_at=time.monotonic(),
                )
            )
            player.inventory[index] = None
            dropped_any = True
        if dropped_any:
            self.effects.append({"type": "drop", "x": player.x, "y": player.y, "ttl": 1.0})

    def nearby_player(self, player: Player, max_distance: float) -> Player | None:
        candidate: Player | None = None
        best = max_distance
        for other in self.players.values():
            if other.sid == player.sid or other.hp <= 0:
                continue
            current_distance = distance((player.x, player.y), (other.x, other.y))
            if current_distance <= best:
                best = current_distance
                candidate = other
        return candidate

    def players_in_radius(self, x: float, y: float, radius: float, exclude_sid: str | None = None) -> list[Player]:
        result = []
        for player in self.players.values():
            if exclude_sid and player.sid == exclude_sid:
                continue
            if player.hp <= 0:
                continue
            if distance((x, y), (player.x, player.y)) <= radius:
                result.append(player)
        return result

    def handle_pickup(self, sid: str, card_id: str) -> dict[str, Any]:
        player = self.players.get(sid)
        if not player:
            return {"ok": False, "reason": "missing_player"}

        if self._first_empty_inventory_slot(player) is None:
            return {"ok": False, "reason": "inventory_full"}

        card = next((item for item in self.cards if item.instance_id == card_id), None)
        if not card:
            return {"ok": False, "reason": "missing_card"}

        if distance((player.x, player.y), (card.x, card.y)) > 56:
            return {"ok": False, "reason": "too_far"}

        if not self._fill_inventory_slot(player, card.card_id):
            return {"ok": False, "reason": "inventory_full"}
        self.cards.remove(card)
        self.effects.append({"type": "pickup", "x": card.x, "y": card.y, "ttl": 0.7})
        return {"ok": True, "card_id": card.card_id}

    def handle_drop(self, sid: str, card_index: int) -> dict[str, Any]:
        player = self.players.get(sid)
        if not player:
            return {"ok": False, "reason": "missing_player"}
        if card_index < 0 or card_index >= len(player.inventory):
            return {"ok": False, "reason": "invalid_slot"}

        card_id = player.inventory[card_index]
        if card_id is None:
            return {"ok": False, "reason": "empty_slot"}
        player.inventory[card_index] = None
        self.cards.append(
            WorldCard(
                instance_id=f"drop-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
                card_id=card_id,
                x=player.x,
                y=player.y,
                spawned_at=time.monotonic(),
            )
        )
        self.effects.append({"type": "drop", "x": player.x, "y": player.y, "ttl": 0.8})
        return {"ok": True, "card_id": card_id}

    def handle_skill(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        player = self.players.get(sid)
        if not player or player.hp <= 0:
            return {"ok": False, "reason": "missing_player"}

        slot = int(payload.get("slot", -1))
        if slot < 0 or slot >= len(player.inventory):
            return {"ok": False, "reason": "invalid_slot"}

        card_id = player.inventory[slot]
        if card_id is None:
            return {"ok": False, "reason": "empty_slot"}
        card = CARD_BY_ID[card_id]
        now = time.monotonic()
        ready_at = player.skill_ready_at(card_id)
        if now < ready_at:
            return {"ok": False, "reason": "cooldown"}

        if card["battery"] and player.battery < card["battery"]:
            return {"ok": False, "reason": "battery_low"}

        if card["battery"]:
            player.battery -= float(card["battery"])

        target_id = payload.get("targetId")
        target_x = float(payload.get("targetX", player.x))
        target_y = float(payload.get("targetY", player.y))
        target = self.players.get(target_id) if target_id else None
        if target and target.hp <= 0:
            target = None

        effect_name = card_id
        result: dict[str, Any] = {"ok": True, "skill": card_id}
        if card_id == "fire_breath":
            victim = target or self.nearby_player(player, 220 * player.range_multiplier(now))
            if victim:
                victim.hp -= 24
                self.apply_burn(victim, stacks=1, duration=5.0)
                victim.last_attacker = player.sid
                self.effects.append({"type": "fire", "x": victim.x, "y": victim.y, "ttl": 0.9})
                result["target"] = victim.sid
        elif card_id == "fire_in_hole":
            victims = self.players_in_radius(target_x, target_y, 150)
            for victim in victims:
                victim.hp -= 42
                self.apply_burn(victim, stacks=1, duration=4.0)
                victim.last_attacker = player.sid
            self.effects.append({"type": "blast", "x": target_x, "y": target_y, "ttl": 1.2})
        elif card_id == "blue_fire":
            victim = target or self.nearby_player(player, 260 * player.range_multiplier(now))
            if victim:
                victim.hp -= 18
                self.apply_burn(victim, stacks=2, duration=7.0)
                victim.last_attacker = player.sid
                self.effects.append({"type": "blue_fire", "x": victim.x, "y": victim.y, "ttl": 1.0})
                result["target"] = victim.sid
        elif card_id == "fire_fusion":
            victim = target or self.nearby_player(player, 230 * player.range_multiplier(now))
            if victim:
                burst = 65 + (victim.burn_stacks * 30)
                if victim.burn_stacks > 0:
                    victim.hp -= burst
                    victim.burn_stacks = 0
                    victim.burn_until = 0.0
                else:
                    victim.hp -= 28
                    self.apply_burn(victim, stacks=1, duration=6.0)
                victim.last_attacker = player.sid
                self.effects.append({"type": "fusion", "x": victim.x, "y": victim.y, "ttl": 1.4})
                result["target"] = victim.sid
        elif card_id == "wind_range":
            player.range_multiplier_until = now + 10.0
            self.effects.append({"type": "wind", "x": player.x, "y": player.y, "ttl": 0.8})
        elif card_id == "wind_barrier":
            player.barrier_until = now + 8.0
            barrier = self.register_wind_barrier(player, float(payload.get("targetX", player.x)), float(payload.get("targetY", player.y)))
            self.effects.append({"type": "barrier", "x": barrier.x, "y": barrier.y, "ttl": 1.2})
        elif card_id == "wind_stun":
            projectile = self.spawn_stun_projectile(player, target_x, target_y)
            self.effects.append({"type": "gust", "x": projectile.x, "y": projectile.y, "ttl": 0.8})
        elif card_id == "usain_bolt":
            player.speed_multiplier_until = now + 8.0
            self.effects.append({"type": "speed", "x": player.x, "y": player.y, "ttl": 0.8})
        elif card_id == "electric_basic":
            victim = target or self.nearby_player(player, 240)
            if victim:
                victim.hp -= 34
                victim.last_attacker = player.sid
                self.effects.append({"type": "electric", "x": victim.x, "y": victim.y, "ttl": 0.9})
                result["target"] = victim.sid
        elif card_id == "recharge":
            player.battery = min(100.0, player.battery + 35.0)
            self.effects.append({"type": "charge", "x": player.x, "y": player.y, "ttl": 0.7})
        elif card_id == "electrocute":
            victim = target or self.nearby_player(player, 260)
            if victim:
                victim.hp -= 38
                victim.last_attacker = player.sid
                chain_targets = self.players_in_radius(victim.x, victim.y, 170, exclude_sid=victim.sid)
                for chain in chain_targets[:2]:
                    chain.hp -= 18
                    chain.last_attacker = player.sid
                self.effects.append({"type": "chain", "x": victim.x, "y": victim.y, "ttl": 1.2})
                result["target"] = victim.sid
        elif card_id == "superconductor":
            player.superconductor_until = now + 12.0
            self.effects.append({"type": "super", "x": player.x, "y": player.y, "ttl": 1.0})
        else:
            return {"ok": False, "reason": "unknown_skill"}

        player.set_cooldown(card_id, float(card["cooldown"]), now)
        result["cooldownUntil"] = player.skill_ready_at(card_id)
        result["effect"] = effect_name
        return result

    def apply_burn(self, player: Player, stacks: int, duration: float) -> None:
        player.burn_stacks = min(5, player.burn_stacks + stacks)
        player.burn_until = max(player.burn_until, time.monotonic() + duration)

    def public_state(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "time": now,
            "mapSize": MAP_SIZE,
            "zone": {
                "center": {"x": ZONE_CENTER[0], "y": ZONE_CENTER[1]},
                "radius": self.zone_radius(now),
                "damage": self.zone_damage(now),
                "secondsLeft": max(0, int(ZONE_DURATION - (now - self.started_at))),
            },
            "players": [
                {
                    "sid": player.sid,
                    "name": player.name,
                    "x": player.x,
                    "y": player.y,
                    "hp": max(0, player.hp),
                    "battery": round(player.battery, 1),
                    "inventory": list(player.inventory),
                    "cooldowns": {card_id: max(0.0, until - now) for card_id, until in player.cooldowns.items()},
                    "rangeBoost": now < player.range_multiplier_until,
                    "speedBoost": now < player.speed_multiplier_until,
                    "stunned": now < player.stun_until,
                    "barrier": now < player.barrier_until,
                    "burnStacks": player.burn_stacks,
                }
                for player in self.players.values()
            ],
            "cards": [
                {
                    "id": card.instance_id,
                    "cardId": card.card_id,
                    "element": CARD_BY_ID[card.card_id]["element"],
                    "skill": CARD_BY_ID[card.card_id]["skill"],
                    "x": card.x,
                    "y": card.y,
                }
                for card in self.cards
            ],
            "barriers": [
                {
                    "id": barrier.barrier_id,
                    "ownerSid": barrier.owner_sid,
                    "x": barrier.x,
                    "y": barrier.y,
                    "radius": barrier.radius,
                    "expiresIn": max(0.0, barrier.expires_at - now),
                }
                for barrier in self.barriers
                if barrier.expires_at > now
            ],
            "projectiles": [
                {
                    "id": projectile.projectile_id,
                    "ownerSid": projectile.owner_sid,
                    "x": projectile.x,
                    "y": projectile.y,
                    "dx": projectile.dx,
                    "dy": projectile.dy,
                    "expiresIn": max(0.0, projectile.expires_at - now),
                }
                for projectile in self.projectiles
                if projectile.expires_at > now
            ],
            "effects": [effect.copy() for effect in self.effects],
        }


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "element-dev-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=os.environ.get("SOCKETIO_ASYNC_MODE", "eventlet"))
room = GameRoom()
background_task_started = False
background_lock = Lock()


def ensure_background_task() -> None:
    global background_task_started
    with background_lock:
        if background_task_started:
            return
        background_task_started = True
        socketio.start_background_task(game_loop)


def game_loop() -> None:
    while True:
        socketio.sleep(0.05)
        with room.lock:
            room.update()
            now = time.monotonic()
            if now - room.last_state_broadcast >= STATE_BROADCAST_INTERVAL:
                room.last_state_broadcast = now
                socketio.emit("state_update", room.public_state())


@app.route("/")
def index() -> str:
    return render_template("index.html")


@socketio.on("connect")
def handle_connect() -> bool:
    ensure_background_task()
    sid = request.sid
    name = request.args.get("name") or f"Player-{random.randint(1000, 9999)}"
    with room.lock:
        try:
            player = room.add_player(sid=sid, name=name)
        except ValueError:
            emit("room_full", {"message": "Room is full"})
            return False
        emit(
            "connected",
            {
                "sid": sid,
                "name": player.name,
                "inventory": list(player.inventory),
                "mapSize": MAP_SIZE,
            },
        )
        socketio.emit("state_update", room.public_state())
    return True


@socketio.on("disconnect")
def handle_disconnect() -> None:
    with room.lock:
        room.remove_player(request.sid)
        socketio.emit("state_update", room.public_state())


@socketio.on("move")
def handle_move(payload: dict[str, Any]) -> None:
    with room.lock:
        player = room.players.get(request.sid)
        if not player:
            return
        player.input_state = {
            "up": bool(payload.get("up")),
            "down": bool(payload.get("down")),
            "left": bool(payload.get("left")),
            "right": bool(payload.get("right")),
        }
        player.last_seen = time.monotonic()


@socketio.on("pickup_card")
def handle_pickup(payload: dict[str, Any]) -> None:
    card_id = str(payload.get("cardId", ""))
    with room.lock:
        result = room.handle_pickup(request.sid, card_id)
    socketio.emit("pickup_result", result, to=request.sid)


@socketio.on("drop_card")
def handle_drop(payload: dict[str, Any]) -> None:
    with room.lock:
        result = room.handle_drop(request.sid, int(payload.get("slot", -1)))
    socketio.emit("drop_result", result, to=request.sid)


@socketio.on("use_card")
def handle_use_card(payload: dict[str, Any]) -> None:
    with room.lock:
        result = room.handle_skill(request.sid, payload)
        if result.get("ok"):
            player = room.players.get(request.sid)
            if player and player.hp <= 0:
                room.drop_inventory(player)
        socketio.emit("skill_result", result, to=request.sid)
        socketio.emit("state_update", room.public_state())
        if result.get("ok") and result.get("effect"):
            socketio.emit("skill_effect", {"type": result["effect"], "x": payload.get("targetX"), "y": payload.get("targetY")})


@socketio.on("ping_state")
def handle_ping_state() -> None:
    with room.lock:
        emit("state_update", room.public_state())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)