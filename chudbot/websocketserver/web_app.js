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
    else if (m.type === "error")
      $("link-message").textContent = m.message || "Connection rejected.";
  };
  ws.onclose = () => {
    if (!$("dashboard").hidden) status("offline");
  };
}
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
if (localStorage.getItem(key)) connect();
