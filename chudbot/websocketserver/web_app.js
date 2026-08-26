const key = "chudbot.web.code",
  $ = (id) => document.getElementById(id),
  state = {
    socket: null,
    code:
      localStorage.getItem(key) ||
      crypto.randomUUID().replaceAll("-", "").slice(0, 6).toUpperCase(),
  };
function status(t, on = false) {
  $("connection").innerHTML = `<i></i> ${t}`;
  $("connection").classList.toggle("connected", on);
}
function draw() {
  $("code").textContent = state.code;
}
function send(x) {
  if (state.socket?.readyState === 1) state.socket.send(JSON.stringify(x));
}
function eventName(item) {
  const wagerType = item.details?.wager_type;
  const labels = {
    coin_flip: "Coin Flip",
    slots: "Slots",
    blackjack: "Blackjack",
    roulette_red: "Roulette — Red",
    roulette_black: "Roulette — Black",
    roulette_green: "Roulette — Green",
  };
  if (wagerType) return labels[wagerType] || wagerType.replaceAll("_", " ");
  return item.event.replaceAll("_", " ");
}
function activity(items) {
  $("activity").innerHTML = items.length
    ? items
        .map((x) => {
          const amount = x.amount > 0 ? `+${x.amount}` : `${x.amount}`;
          const cls = x.amount >= 0 ? "positive" : "negative";
          return `<div class="activity-row"><span>${eventName(x)}<br><small>${new Date(x.at * 1000).toLocaleString()}</small></span><b class="${cls}">${amount}</b></div>`;
        })
        .join("")
    : '<p class="empty">nothing yet</p>';
}
function connect() {
  state.socket?.close();
  status("connecting");
  const ws = (state.socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
  ));
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", code: state.code }));
  ws.onmessage = ({ data }) => {
    const m = JSON.parse(data);
    if (m.type === "auth_ok") {
      status("online", true);
      $("link-view").hidden = true;
      $("dashboard").hidden = false;
      send({ type: "profile" });
      send({ type: "recent_activity" });
      send({ type: "salvage_options" });
    } else if (m.type === "registration_pending") {
      status("waiting");
      $("link-message").textContent =
        "Now run /register " + state.code + " in Discord.";
    } else if (m.type === "profile") {
      for (const [id, value] of [
        ["balance", m.balance],
        ["loan", m.loan_balance],
        ["security", m.security_level],
        ["speed", m.dumpster_speed_tier],
      ])
        $(id).textContent = Number(value).toLocaleString();
    } else if (m.type === "recent_activity") activity(m.items);
    else if (m.type === "salvage_options") drawSalvageOptions(m);
    else if (m.type === "salvage_cooldown") {
      showSalvageMessage(`The salvage sites are empty for ${formatWait(m.retry_after)}.`);
    } else if (m.type === "salvage_started" || m.type === "salvage_progress" || m.type === "salvage_scan" || m.type === "salvage_hazard_progress") {
      showSalvageRun(m);
      if (m.balance !== undefined) $("balance").textContent = Number(m.balance).toLocaleString();
    } else if (m.type === "salvage_complete" || m.type === "salvage_left" || m.type === "salvage_hazard" || m.type === "salvage_destroyed" || m.type === "salvage_out_of_fuel") {
      showSalvageResult(m);
      if (m.balance !== undefined) $("balance").textContent = Number(m.balance).toLocaleString();
      send({ type: "salvage_options" });
      send({ type: "recent_activity" });
    }
    else if (m.type === "error")
      showSalvageMessage(m.message || "Connection rejected.");
  };
  ws.onclose = () => {
    if (!$("dashboard").hidden) status("offline");
  };
}
function formatWait(seconds) {
  const minutes = Math.floor(Number(seconds) / 60);
  const rest = Number(seconds) % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}
function showSalvageMessage(message) {
  $("salvage-message").textContent = message;
}
function drawSalvageOptions(m) {
  const select = $("salvage-equipment");
  select.innerHTML = '<option value="">none</option>';
  for (const item of m.equipment) {
    const option = document.createElement("option");
    option.value = item.key;
    option.textContent = `${item.emoji} ${item.name} (${item.available}) — ${item.description}`;
    option.disabled = !item.available;
    select.append(option);
  }
  $("salvage-locations").innerHTML = m.locations.map((location) =>
    `<button class="salvage-location" data-location="${location.key}"><b>${location.emoji} ${location.name}</b><small>${location.description}</small></button>`
  ).join("");
  document.querySelectorAll("[data-location]").forEach((button) => {
    button.onclick = () => send({ type: "salvage_start", location: button.dataset.location, equipment: select.value || null });
  });
}
function haulText(items) {
  return items.length ? items.map((item) => `${item.emoji} ${item.name} ×${item.quantity}`).join("<br>") : "Nothing yet";
}
function showSalvageRun(m) {
  $("salvage-setup").hidden = true;
  $("salvage-run").hidden = false;
  $("salvage-message").textContent = m.message || "";
  $("salvage-status").textContent = `${m.location.emoji} ${m.location.name} — sector ${m.round}/${m.max_rounds} · ⛽ ${m.fuel} fuel · 🛡️ ${m.hull} hull · 🔥 combo ${m.combo}`;
  $("salvage-haul").innerHTML = `<small>current haul</small><br>${haulText(m.haul)}`;
  if (m.scan) $("salvage-message").textContent += " Scan bonus active: +25% rare-loot weighting.";
}
function showSalvageResult(m) {
  $("salvage-run").hidden = true;
  $("salvage-setup").hidden = false;
  const prefix = m.type === "salvage_destroyed" ? "💥 Your ship was destroyed. " : m.type === "salvage_out_of_fuel" ? "⛽ You ran out of fuel. " : m.hazard ? "🚨 A hazard damaged your ship. " : m.type === "salvage_left" ? "🏠 You returned with your haul. " : "✅ Expedition complete. ";
  const lost = m.lost?.length ? `<br>Lost: ${haulText(m.lost)}` : "";
  showSalvageMessage(`${prefix}Saved: ${haulText(m.saved)}${lost}`);
}
$("open-salvage").onclick = () => { $("salvage").hidden = false; $("salvage").scrollIntoView({ behavior: "smooth" }); send({ type: "salvage_options" }); };
$("close-salvage").onclick = () => { $("salvage").hidden = true; };
document.querySelectorAll("[data-salvage-action]").forEach((button) => {
  button.onclick = () => send({ type: "salvage_action", action: button.dataset.salvageAction });
});
$("check").onclick = () => {
  localStorage.setItem(key, state.code);
  connect();
};
$("copy").onclick = () => navigator.clipboard?.writeText(state.code);
$("new-code").onclick = () => {
  state.socket?.close();
  state.code = crypto
    .randomUUID()
    .replaceAll("-", "")
    .slice(0, 6)
    .toUpperCase();
  localStorage.setItem(key, state.code);
  $("link-view").hidden = false;
  $("dashboard").hidden = true;
  draw();
};
draw();
if (!localStorage.getItem(key)) localStorage.setItem(key, state.code);
connect();
