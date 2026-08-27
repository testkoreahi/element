const socket = io({ transports: ["websocket", "polling"] });
const canvas = document.getElementById("gameCanvas");
const ctx = canvas.getContext("2d");
const hotbar = document.getElementById("hotbar");
const hpFill = document.getElementById("hpFill");
const batteryFill = document.getElementById("batteryFill");
const hpText = document.getElementById("hpText");
const batteryText = document.getElementById("batteryText");
const zoneText = document.getElementById("zoneText");
const zoneState = document.getElementById("zoneState");
const connectionState = document.getElementById("connectionState");
const countState = document.getElementById("countState");

const cardMeta = {
  fire_breath: { element: "fire", color: "#ff7a3d", name: "Fire Breath" },
  fire_in_hole: { element: "fire", color: "#ff4b39", name: "Fire in the Hole" },
  blue_fire: { element: "fire", color: "#7db7ff", name: "Blue Fire" },
  fire_fusion: { element: "fire", color: "#ff9966", name: "Fire Fusion" },
  wind_range: { element: "wind", color: "#6ee7a9", name: "Wind Range" },
  wind_barrier: { element: "wind", color: "#4ade80", name: "Wind Barrier" },
  wind_stun: { element: "wind", color: "#88f0c0", name: "Stun Gust" },
  usain_bolt: { element: "wind", color: "#7ef9d2", name: "Usain Bolt" },
  electric_basic: { element: "electric", color: "#ffe57a", name: "Basic Shock" },
  recharge: { element: "electric", color: "#fff0b0", name: "Recharge" },
  electrocute: { element: "electric", color: "#ffd84d", name: "Electrocute" },
  superconductor: { element: "electric", color: "#ffef87", name: "Superconductor" },
};

const state = {
  me: null,
  mySid: null,
  game: null,
  keys: { up: false, down: false, left: false, right: false },
  mouse: { x: 0, y: 0, worldX: 0, worldY: 0 },
  particles: [],
  inventorySnapshot: [],
  cameraShake: 0,
  lastHpBySid: {},
};

const MAX_RENDER_FPS = 30;

const viewport = {
  width: canvas.getBoundingClientRect().width,
  height: canvas.getBoundingClientRect().height,
};

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  viewport.width = rect.width;
  viewport.height = rect.height;
  canvas.width = Math.floor(rect.width * dpr);
  canvas.height = Math.floor(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function worldToScreen(worldX, worldY, camera) {
  return {
    x: (worldX - camera.x) * camera.zoom,
    y: (worldY - camera.y) * camera.zoom,
  };
}

function screenToWorld(screenX, screenY, camera) {
  return {
    x: screenX / camera.zoom + camera.x,
    y: screenY / camera.zoom + camera.y,
  };
}

function updateHUD() {
  if (!state.game || !state.me) {
    return;
  }
  const hpRatio = Math.max(0, Math.min(1, state.me.hp / 500));
  const batteryRatio = Math.max(0, Math.min(1, (state.me.battery ?? 0) / 100));
  hpFill.style.width = `${hpRatio * 100}%`;
  batteryFill.style.width = `${batteryRatio * 100}%`;
  hpText.textContent = `${state.me.hp} / 500`;
  batteryText.textContent = `${Math.round(state.me.battery ?? 0)} / 100`;
  zoneText.textContent = `반지름 ${Math.round(state.game.zone.radius)} | ${state.game.zone.secondsLeft}s 남음`;
  zoneState.textContent = state.game.zone.radius <= 400 ? "위험 단계" : "안전 단계";
  countState.textContent = `플레이어 ${state.game.players.length} / 10`;
}

function occupiedInventoryCount() {
  return (state.me?.inventory ?? []).filter(Boolean).length;
}

function buildHotbar() {
  hotbar.innerHTML = "";
  for (let index = 0; index < 7; index += 1) {
    const slot = document.createElement("div");
    slot.className = "slot empty";
    slot.innerHTML = `
      <div class="slot-index">${index + 1}</div>
      <div class="slot-name">EMPTY</div>
      <div class="slot-element">No card</div>
      <div class="cooldown"><span></span></div>
    `;
    hotbar.appendChild(slot);
  }
}

function syncHotbar() {
  const inventory = state.me?.inventory ?? [];
  const cooldowns = state.me?.cooldowns ?? {};
  const slots = Array.from(hotbar.children);
  slots.forEach((slot, index) => {
    const cardId = inventory[index];
    const cooldownBar = slot.querySelector(".cooldown span");
    if (!cardId) {
      slot.className = "slot empty";
      slot.querySelector(".slot-name").textContent = "EMPTY";
      slot.querySelector(".slot-element").textContent = "No card";
      cooldownBar.style.width = "0%";
      return;
    }

    const meta = cardMeta[cardId] ?? { element: "unknown", name: cardId, color: "#ffffff" };
    const remaining = cooldowns[cardId] ?? 0;
    const total = {
      fire_breath: 4,
      fire_in_hole: 9,
      blue_fire: 12,
      fire_fusion: 15,
      wind_range: 20,
      wind_barrier: 18,
      wind_stun: 14,
      usain_bolt: 16,
      electric_basic: 3,
      recharge: 8,
      electrocute: 10,
      superconductor: 25,
    }[cardId] ?? 10;
    const percent = Math.max(0, Math.min(100, (remaining / total) * 100));
    slot.className = `slot ${meta.element}`;
    slot.querySelector(".slot-name").textContent = meta.name;
    slot.querySelector(".slot-element").textContent = `${meta.element.toUpperCase()} · ${remaining > 0 ? `${remaining.toFixed(1)}s` : "READY"}`;
    cooldownBar.style.width = `${percent}%`;
  });
}

function drawBackground(camera) {
  const width = viewport.width;
  const height = viewport.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(6, 13, 25, 0.95)";
  ctx.fillRect(0, 0, width, height);

  const step = 80 * camera.zoom;
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  for (let x = -((camera.x * camera.zoom) % step); x < width; x += step) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = -((camera.y * camera.zoom) % step); y < height; y += step) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
}

function drawZone(camera) {
  const zone = state.game?.zone;
  if (!zone) {
    return;
  }
  const width = viewport.width;
  const height = viewport.height;
  const center = worldToScreen(zone.center.x, zone.center.y, camera);
  const radius = zone.radius * camera.zoom;
  const safeGradient = ctx.createRadialGradient(center.x, center.y, Math.max(20, radius * 0.7), center.x, center.y, radius);
  safeGradient.addColorStop(0, "rgba(94, 234, 212, 0.02)");
  safeGradient.addColorStop(1, "rgba(94, 234, 212, 0.08)");
  ctx.fillStyle = safeGradient;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.fill();

  ctx.save();
  ctx.setLineDash([16, 12]);
  ctx.strokeStyle = "rgba(120, 255, 210, 0.9)";
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  ctx.fillStyle = "rgba(255, 80, 100, 0.18)";
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2, true);
  ctx.fill("evenodd");
}

function drawCards(camera) {
  const cards = state.game?.cards ?? [];
  for (const card of cards) {
    const screen = worldToScreen(card.x, card.y, camera);
    const meta = cardMeta[card.cardId] ?? { color: "#ffffff", name: card.skill, element: card.element };
    ctx.save();
    ctx.translate(screen.x, screen.y);
    ctx.fillStyle = meta.color;
    ctx.shadowColor = meta.color;
    ctx.shadowBlur = 16;
    ctx.beginPath();
    ctx.roundRect(-14, -14, 28, 28, 8);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(5, 12, 20, 0.88)";
    ctx.font = "600 10px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(meta.element.slice(0, 1).toUpperCase(), 0, 4);
    ctx.restore();
  }
}

function drawBarriers(camera) {
  const barriers = state.game?.barriers ?? [];
  for (const barrier of barriers) {
    const screen = worldToScreen(barrier.x, barrier.y, camera);
    ctx.save();
    ctx.strokeStyle = "rgba(110, 231, 169, 0.95)";
    ctx.lineWidth = 3;
    ctx.setLineDash([10, 8]);
    ctx.beginPath();
    ctx.arc(screen.x, screen.y, barrier.radius * camera.zoom, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
}

function drawProjectiles(camera) {
  const projectiles = state.game?.projectiles ?? [];
  for (const projectile of projectiles) {
    const screen = worldToScreen(projectile.x, projectile.y, camera);
    ctx.save();
    ctx.translate(screen.x, screen.y);
    ctx.strokeStyle = "rgba(160, 255, 235, 0.95)";
    ctx.lineWidth = 3;
    ctx.shadowColor = "rgba(160, 255, 235, 0.85)";
    ctx.shadowBlur = 14;
    ctx.beginPath();
    ctx.moveTo(-projectile.dx * 14, -projectile.dy * 14);
    ctx.lineTo(projectile.dx * 20, projectile.dy * 20);
    ctx.stroke();
    ctx.restore();
  }
}

function drawPlayers(camera) {
  const players = state.game?.players ?? [];
  for (const player of players) {
    const screen = worldToScreen(player.x, player.y, camera);
    const isMe = player.sid === state.mySid;
    const hpRatio = Math.max(0, player.hp / 500);
    ctx.save();
    ctx.translate(screen.x, screen.y);
    ctx.fillStyle = isMe ? "#ffffff" : "rgba(214, 226, 255, 0.9)";
    ctx.shadowColor = isMe ? "#ffffff" : "rgba(120, 160, 255, 0.4)";
    ctx.shadowBlur = isMe ? 24 : 12;
    ctx.beginPath();
    ctx.arc(0, 0, isMe ? 18 : 14, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    if (player.barrier) {
      ctx.strokeStyle = "rgba(110, 231, 169, 0.9)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(0, 0, isMe ? 26 : 22, 0, Math.PI * 2);
      ctx.stroke();
    }

    ctx.fillStyle = "rgba(10, 18, 28, 0.92)";
    ctx.fillRect(-26, -34, 52, 7);
    ctx.fillStyle = hpRatio > 0.4 ? "#56f28f" : "#ff5f6d";
    ctx.fillRect(-26, -34, 52 * hpRatio, 7);

    ctx.fillStyle = "#edf4ff";
    ctx.font = "600 11px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(player.name, 0, -40);
    ctx.restore();
  }
}

function triggerScreenShake(strength = 10) {
  state.cameraShake = Math.max(state.cameraShake, strength);
}

function spawnDamageText(x, y, amount, color = "#ff7a7a") {
  if (!Number.isFinite(amount) || amount <= 0) {
    return;
  }
  state.particles.push({
    x,
    y,
    vx: 0,
    vy: -0.6,
    radius: 12,
    life: 1.0,
    maxLife: 1.0,
    decay: 0.03,
    color,
    kind: "text",
    text: `-${amount}`,
  });
}

function drawParticles(camera) {
  const width = viewport.width;
  const height = viewport.height;
  const updated = [];
  for (const particle of state.particles) {
    particle.x += particle.vx ?? 0;
    particle.y += particle.vy ?? 0;

    const screen = worldToScreen(particle.x, particle.y, camera);
    const alpha = Math.max(0, particle.life / particle.maxLife);
    if (screen.x < -80 || screen.y < -80 || screen.x > width + 80 || screen.y > height + 80) {
      particle.life -= 0.04;
      continue;
    }

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = particle.color;
    ctx.shadowColor = particle.color;
    ctx.shadowBlur = particle.kind === "ring" ? 8 : 18;

    if (particle.kind === "ring") {
      ctx.strokeStyle = particle.color;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(screen.x, screen.y, particle.radius + particle.life * 30, 0, Math.PI * 2);
      ctx.stroke();
    } else if (particle.kind === "beam") {
      ctx.strokeStyle = particle.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(screen.x - particle.radius * 1.2, screen.y - particle.radius * 1.2);
      ctx.lineTo(screen.x + particle.radius * 1.2, screen.y + particle.radius * 1.2);
      ctx.stroke();
    } else if (particle.kind === "text") {
      ctx.font = "700 16px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(particle.text, screen.x, screen.y);
    } else {
      ctx.beginPath();
      ctx.arc(screen.x, screen.y, particle.radius, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
    particle.life -= particle.decay ?? 0.016;
    if (particle.life > 0) {
      updated.push(particle);
    }
  }
  state.particles = updated;
}

function drawScene() {
  if (!state.game) {
    drawBackground({ x: 0, y: 0, zoom: 0.45 });
    return;
  }

  const me = state.me || state.game.players[0];
  if (!me) {
    drawBackground({ x: 0, y: 0, zoom: 0.45 });
    return;
  }

  const width = viewport.width;
  const height = viewport.height;
  const zoom = 0.42;
  const camera = {
    x: me.x - width / (2 * zoom),
    y: me.y - height / (2 * zoom),
    zoom,
  };

  ctx.save();
  const shakeX = state.cameraShake > 0 ? (Math.random() - 0.5) * state.cameraShake : 0;
  const shakeY = state.cameraShake > 0 ? (Math.random() - 0.5) * state.cameraShake : 0;
  ctx.translate(shakeX, shakeY);
  state.cameraShake = Math.max(0, state.cameraShake - 0.55);

  drawBackground(camera);
  drawZone(camera);
  drawCards(camera);
  drawBarriers(camera);
  drawProjectiles(camera);
  drawPlayers(camera);
  drawParticles(camera);

  ctx.restore();

  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,0.8)";
  ctx.font = "500 12px Inter, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(`좌표 ${Math.round(me.x)}, ${Math.round(me.y)}`, 18, 24);
  ctx.fillText(`인벤토리 ${occupiedInventoryCount()} / 7`, 18, 42);
  ctx.restore();
}

function pulseEffect(type, x, y) {
  const variants = {
    fire_breath: { color: "#ff7a3d", count: 18, spread: 52, radius: 5, maxLife: 0.8, kind: "beam", speed: 0.8 },
    fire_in_hole: { color: "#ff4b39", count: 26, spread: 92, radius: 6, maxLife: 1.2, kind: "ring", speed: 1.1 },
    blue_fire: { color: "#7db7ff", count: 20, spread: 64, radius: 5, maxLife: 0.9, kind: "spark", speed: 1.0 },
    fire_fusion: { color: "#ff9966", count: 30, spread: 80, radius: 7, maxLife: 1.1, kind: "spark", speed: 1.3 },
    wind_range: { color: "#6ee7a9", count: 16, spread: 56, radius: 4, maxLife: 0.9, kind: "ring", speed: 0.7 },
    wind_barrier: { color: "#4ade80", count: 22, spread: 78, radius: 5, maxLife: 1.0, kind: "ring", speed: 0.8 },
    wind_stun: { color: "#88f0c0", count: 18, spread: 60, radius: 5, maxLife: 0.9, kind: "beam", speed: 1.2 },
    usain_bolt: { color: "#7ef9d2", count: 22, spread: 68, radius: 5, maxLife: 0.85, kind: "spark", speed: 1.4 },
    electric_basic: { color: "#ffe57a", count: 18, spread: 58, radius: 5, maxLife: 0.8, kind: "beam", speed: 1.1 },
    recharge: { color: "#fff0b0", count: 20, spread: 70, radius: 5, maxLife: 1.0, kind: "ring", speed: 0.9 },
    electrocute: { color: "#ffd84d", count: 24, spread: 90, radius: 6, maxLife: 1.1, kind: "beam", speed: 1.3 },
    superconductor: { color: "#ffef87", count: 28, spread: 90, radius: 6, maxLife: 1.2, kind: "ring", speed: 1.0 },
    fire: { color: "#ff7a3d", count: 12, spread: 40, radius: 5, maxLife: 0.7, kind: "spark", speed: 0.9 },
    blue_fire: { color: "#7db7ff", count: 12, spread: 40, radius: 5, maxLife: 0.7, kind: "spark", speed: 0.9 },
    fusion: { color: "#ff9966", count: 14, spread: 45, radius: 6, maxLife: 0.8, kind: "spark", speed: 0.9 },
    wind: { color: "#6ee7a9", count: 10, spread: 40, radius: 5, maxLife: 0.7, kind: "ring", speed: 0.8 },
    barrier: { color: "#4ade80", count: 14, spread: 52, radius: 4, maxLife: 0.8, kind: "ring", speed: 0.8 },
    stun: { color: "#9ef0c8", count: 12, spread: 45, radius: 4, maxLife: 0.7, kind: "beam", speed: 1.0 },
    electric: { color: "#ffe57a", count: 12, spread: 45, radius: 5, maxLife: 0.7, kind: "beam", speed: 0.9 },
    chain: { color: "#ffdf69", count: 18, spread: 80, radius: 6, maxLife: 0.9, kind: "beam", speed: 1.2 },
    charge: { color: "#fff2a6", count: 16, spread: 60, radius: 5, maxLife: 0.8, kind: "ring", speed: 0.9 },
    super: { color: "#fff0bf", count: 16, spread: 70, radius: 6, maxLife: 0.9, kind: "ring", speed: 1.0 },
    drop: { color: "#ffffff", count: 10, spread: 34, radius: 4, maxLife: 0.6, kind: "spark", speed: 0.7 },
    pickup: { color: "#a5d8ff", count: 12, spread: 36, radius: 4, maxLife: 0.7, kind: "spark", speed: 0.8 },
    blast: { color: "#ff5f6d", count: 18, spread: 80, radius: 7, maxLife: 0.9, kind: "ring", speed: 1.1 },
    zone_hit: { color: "#ff8a8a", count: 10, spread: 52, radius: 5, maxLife: 0.7, kind: "spark", speed: 0.9 },
    burn: { color: "#ff6f3c", count: 12, spread: 54, radius: 6, maxLife: 0.8, kind: "spark", speed: 1.0 },
    speed: { color: "#6ee7a9", count: 14, spread: 50, radius: 4, maxLife: 0.7, kind: "ring", speed: 0.9 },
  };

  const config = variants[type] ?? { color: "#ffffff", count: 12, spread: 40, radius: 5, maxLife: 0.7, kind: "spark", speed: 0.9 };

  for (let index = 0; index < config.count; index += 1) {
    const angle = (Math.PI * 2 * index) / config.count + Math.random() * 0.6;
    const distance = Math.random() * config.spread;
    const vx = Math.cos(angle) * (config.speed + Math.random() * 0.8);
    const vy = Math.sin(angle) * (config.speed + Math.random() * 0.8);
    state.particles.push({
      x: (x ?? 0) + Math.cos(angle) * distance,
      y: (y ?? 0) + Math.sin(angle) * distance,
      vx,
      vy,
      radius: config.radius + Math.random() * 4,
      life: config.maxLife,
      maxLife: config.maxLife,
      decay: config.kind === "ring" ? 0.04 : 0.016,
      color: config.color,
      kind: config.kind,
    });
  }
}

function sendMovement() {
  socket.emit("move", state.keys);
}

function slotIndexFromEvent(event) {
  if (event.code && event.code.startsWith("Digit")) {
    const index = Number.parseInt(event.code.slice(5), 10) - 1;
    return Number.isNaN(index) ? null : index;
  }
  if (event.code && event.code.startsWith("Numpad")) {
    const index = Number.parseInt(event.code.slice(6), 10) - 1;
    return Number.isNaN(index) ? null : index;
  }
  const keyToSlot = {
    "1": 0,
    "!": 0,
    "2": 1,
    "@": 1,
    "3": 2,
    "#": 2,
    "4": 3,
    "$": 3,
    "5": 4,
    "%": 4,
    "6": 5,
    "^": 5,
    "7": 6,
    "&": 6,
  };
  if (Object.prototype.hasOwnProperty.call(keyToSlot, event.key)) {
    return keyToSlot[event.key];
  }
  return null;
}

function nearestPlayerTarget(maxDistance = 400) {
  if (!state.game || !state.mySid) {
    return null;
  }
  const me = state.game.players.find((player) => player.sid === state.mySid);
  if (!me) {
    return null;
  }
  let best = null;
  let bestDistance = maxDistance;
  for (const player of state.game.players) {
    if (player.sid === state.mySid || player.hp <= 0) {
      continue;
    }
    const dx = player.x - me.x;
    const dy = player.y - me.y;
    const dist = Math.hypot(dx, dy);
    if (dist < bestDistance) {
      bestDistance = dist;
      best = player;
    }
  }
  return best;
}

function useSlot(slot, drop = false) {
  const inventory = state.me?.inventory ?? [];
  const cardId = inventory[slot];
  if (!cardId) {
    return;
  }

  if (drop) {
    socket.emit("drop_card", { slot });
    return;
  }

  const target = nearestPlayerTarget();
  socket.emit("use_card", {
    slot,
    targetId: target?.sid ?? null,
    targetX: state.mouse.worldX,
    targetY: state.mouse.worldY,
  });
}

window.addEventListener("resize", () => {
  resizeCanvas();
});

window.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (key === "w" || key === "arrowup") state.keys.up = true;
  if (key === "s" || key === "arrowdown") state.keys.down = true;
  if (key === "a" || key === "arrowleft") state.keys.left = true;
  if (key === "d" || key === "arrowright") state.keys.right = true;

  const slotIndex = slotIndexFromEvent(event);
  if (slotIndex !== null && slotIndex >= 0 && slotIndex < 7) {
    event.preventDefault();
    useSlot(slotIndex, event.shiftKey);
  }

  sendMovement();
});

window.addEventListener("keyup", (event) => {
  const key = event.key.toLowerCase();
  if (key === "w" || key === "arrowup") state.keys.up = false;
  if (key === "s" || key === "arrowdown") state.keys.down = false;
  if (key === "a" || key === "arrowleft") state.keys.left = false;
  if (key === "d" || key === "arrowright") state.keys.right = false;
  sendMovement();
});

canvas.addEventListener("mousemove", (event) => {
  const rect = canvas.getBoundingClientRect();
  state.mouse.x = event.clientX - rect.left;
  state.mouse.y = event.clientY - rect.top;
  if (state.game && state.me) {
    const me = state.me;
    const zoom = 0.42;
    const camera = { x: me.x - viewport.width / (2 * zoom), y: me.y - viewport.height / (2 * zoom), zoom };
    const world = screenToWorld(state.mouse.x, state.mouse.y, camera);
    state.mouse.worldX = world.x;
    state.mouse.worldY = world.y;
  }
});

canvas.addEventListener("click", () => {
  const cards = state.game?.cards ?? [];
  const me = state.game?.players.find((player) => player.sid === state.mySid);
  if (!me) {
    return;
  }
  for (const card of cards) {
    const dist = Math.hypot(card.x - me.x, card.y - me.y);
    if (dist < 58) {
      socket.emit("pickup_card", { cardId: card.id });
      break;
    }
  }
});

socket.on("connect", () => {
  connectionState.textContent = "연결됨";
  connectionState.style.borderColor = "rgba(110, 231, 169, 0.28)";
});

socket.on("disconnect", () => {
  connectionState.textContent = "연결 해제";
});

socket.on("room_full", (payload) => {
  connectionState.textContent = payload.message || "방이 가득 찼습니다";
});

socket.on("connected", (payload) => {
  state.mySid = payload.sid;
  state.me = { inventory: payload.inventory || [], cooldowns: {}, hp: 500, battery: 100 };
  buildHotbar();
  socket.emit("ping_state");
  sendMovement();
});

socket.on("pickup_result", (payload) => {
  if (payload.ok) {
    pulseEffect("pickup", state.mouse.worldX, state.mouse.worldY);
  }
});

socket.on("drop_result", (payload) => {
  if (payload.ok) {
    pulseEffect("drop", state.mouse.worldX, state.mouse.worldY);
  }
});

socket.on("skill_result", (payload) => {
  if (payload.ok) {
    triggerScreenShake(12);
    pulseEffect(payload.effect, state.mouse.worldX, state.mouse.worldY);
  }
});

socket.on("skill_effect", (payload) => {
  pulseEffect(payload.type, payload.x, payload.y);
});

socket.on("state_update", (payload) => {
  const previousBySid = { ...state.lastHpBySid };
  state.game = payload;
  for (const player of payload.players ?? []) {
    const previous = previousBySid[player.sid];
    if (previous != null && player.hp < previous) {
      spawnDamageText(player.x, player.y, previous - player.hp, "#ff7a7a");
      triggerScreenShake(5 + (previous - player.hp) * 0.25);
    }
  }
  state.lastHpBySid = {};
  for (const player of payload.players ?? []) {
    state.lastHpBySid[player.sid] = player.hp;
  }

  state.me = payload.players.find((player) => player.sid === state.mySid) || state.me;
  if (state.me) {
    state.inventorySnapshot = state.me.inventory;
  }
  updateHUD();
  syncHotbar();
});

setInterval(() => {
  sendMovement();
  if (!state.game || !state.me) {
    return;
  }
  const me = state.me;
  if (!me) {
    return;
  }
  for (const card of state.game.cards) {
    if (Math.hypot(card.x - me.x, card.y - me.y) < 48) {
      socket.emit("pickup_card", { cardId: card.id });
      break;
    }
  }
}, 180);

function renderLoop() {
  if (renderLoop.lastFrameAt && performance.now() - renderLoop.lastFrameAt < 1000 / MAX_RENDER_FPS) {
    requestAnimationFrame(renderLoop);
    return;
  }
  renderLoop.lastFrameAt = performance.now();
  if (state.game && state.me) {
    const me = state.me;
    if (me) {
      const zoom = 0.42;
      const camera = { x: me.x - viewport.width / (2 * zoom), y: me.y - viewport.height / (2 * zoom), zoom };
      const world = screenToWorld(state.mouse.x, state.mouse.y, camera);
      state.mouse.worldX = world.x;
      state.mouse.worldY = world.y;
    }
  }
  drawScene();
  requestAnimationFrame(renderLoop);
}

resizeCanvas();
buildHotbar();
connectionState.textContent = "연결 시도 중";
requestAnimationFrame(renderLoop);