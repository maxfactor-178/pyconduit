// PyConduit frontend — vanilla JS, no build step.
// Talks JSON over a WebSocket to the server, which owns the real XMPP session.
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  // ---- State ---------------------------------------------------------------
  const state = {
    me: null,               // bare JID we're acting as
    username: null,
    brandTitle: "PyConduit",
    soundEnabled: true,
    mucServers: [],         // configured MUC discovery domains (from 'ready')
    roster: new Map(),      // jid -> {name, subscription, show, status}
    convos: new Map(),      // id  -> {kind, id, name, messages, unread, complete, oldestId, occupants}
    active: null,           // active convo id
    ws: null,
  };

  function loadSoundPref(def) {
    const v = localStorage.getItem("pyconduit.sound");
    return v === null ? def : v === "1";
  }

  // ---- Conversations -------------------------------------------------------
  function getConvo(id, kind, name) {
    let c = state.convos.get(id);
    if (!c) {
      c = { kind: kind || "chat", id, name: name || id, messages: [],
            unread: 0, complete: false, oldestId: null, occupants: [] };
      state.convos.set(id, c);
    }
    return c;
  }

  function setActive(id) {
    state.active = id;
    // A roster contact may not have a conversation object yet (contacts are
    // listed from the roster, not from open convos). Create it on first open.
    const c = getConvo(id, "chat");
    c.unread = 0;
    // Lazily load history the first time a 1:1 conversation is opened.
    if (c.kind === "chat" && c.messages.length === 0 && !c.complete) {
      send({ type: "load_history", jid: id, before: null });
    }
    renderAll();
    focusComposer();
    scrollMessages();
  }

  function focusComposer() {
    const input = $("#composer-input");
    const c = state.convos.get(state.active);
    input.disabled = !c;
    $("#send-btn").disabled = !c;
    if (c) input.focus();
  }

  // ---- Rendering -----------------------------------------------------------
  function renderAll() {
    renderContacts();
    renderRooms();
    renderConvoHeader();
    renderMessages();
    renderOccupants();
    updateTitle();
  }

  function presenceOf(jid) {
    const r = state.roster.get(jid);
    return r && r.show ? r.show : "offline";
  }

  // Deterministic hue from a JID, so each contact/room gets a stable avatar color.
  function hashHue(str) {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) | 0;
    return Math.abs(h) % 360;
  }

  // A colored circle with the initial (or "#" for rooms); presence dot as a
  // corner badge for 1:1 contacts. dotClass === null means "no presence" (a room).
  function makeAvatar(id, label, dotClass) {
    const av = document.createElement("span");
    av.className = "avatar";
    av.style.backgroundColor = `hsl(${hashHue(id)} 52% 42%)`;
    const initial = document.createElement("span");
    initial.textContent = dotClass === null ? "#" : (label.trim()[0] || "?").toUpperCase();
    av.appendChild(initial);
    if (dotClass !== null) {
      const dot = document.createElement("span");
      dot.className = "presence-dot " + dotClass;
      av.appendChild(dot);
    }
    return av;
  }

  function liFor(convoId, label, dotClass, action) {
    const c = state.convos.get(convoId);
    const li = document.createElement("li");
    if (convoId === state.active) li.classList.add("active");
    li.onclick = () => setActive(convoId);
    // Hover shows the full JID — disambiguates same-named rooms on different servers.
    li.title = convoId;

    li.appendChild(makeAvatar(convoId, label, dotClass));
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = label;
    li.appendChild(name);

    if (c && c.unread > 0) {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = c.unread;
      li.appendChild(b);
    }
    if (action) {
      const btn = document.createElement("button");
      btn.className = "row-action";
      btn.textContent = action.label;
      btn.title = action.title;
      btn.onclick = (e) => { e.stopPropagation(); action.onClick(); };
      li.appendChild(btn);
    }
    return li;
  }

  function renderContacts() {
    const ul = $("#contacts");
    ul.innerHTML = "";
    // Show roster contacts; ensure each has a conversation entry lazily.
    const jids = new Set(state.roster.keys());
    // Also include any open chat convos not in roster.
    for (const [id, c] of state.convos) if (c.kind === "chat") jids.add(id);
    [...jids].sort().forEach((jid) => {
      const r = state.roster.get(jid);
      const label = (r && r.name) || jid;
      const li = liFor(jid, label, presenceOf(jid),
        { label: "✕", title: "Remove contact", onClick: () => removeContact(jid) });
      ul.appendChild(li);
    });
  }

  function renderRooms() {
    const ul = $("#rooms");
    ul.innerHTML = "";
    for (const [id, c] of state.convos) {
      if (c.kind !== "muc") continue;
      const li = liFor(id, c.name, null,
        { label: "✕", title: "Leave room", onClick: () => leaveRoom(id) });
      ul.appendChild(li);
    }
  }

  function renderConvoHeader() {
    const c = state.convos.get(state.active);
    const leaveBtn = $("#leave-room-btn");
    const avatar = $("#convo-avatar");
    avatar.innerHTML = "";
    if (!c) {
      $("#convo-title").textContent = "Select a conversation";
      $("#convo-title").title = "";
      $("#convo-sub").textContent = "";
      leaveBtn.classList.add("hidden");
      return;
    }
    $("#convo-title").textContent = c.name;
    $("#convo-title").title = c.id;   // full JID on hover (disambiguates rooms)
    if (c.kind === "chat") {
      const r = state.roster.get(c.id);
      $("#convo-sub").textContent = r && r.status ? r.status : presenceOf(c.id);
      avatar.appendChild(makeAvatar(c.id, c.name, presenceOf(c.id)));
      leaveBtn.classList.add("hidden");
    } else {
      // Show the MUC domain so same-named rooms on different servers are clear.
      $("#convo-sub").textContent = c.id.split("@")[1] || "";
      avatar.appendChild(makeAvatar(c.id, c.name, null));
      leaveBtn.classList.remove("hidden");
    }
  }

  function renderMessages() {
    const wrap = $("#messages");
    wrap.innerHTML = "";
    const c = state.convos.get(state.active);
    $("#load-older").classList.toggle("hidden", !c || c.kind !== "chat" || c.complete);
    if (!c) return;
    c.messages.forEach((m) => wrap.appendChild(renderMessage(m)));
  }

  function renderMessage(m) {
    if (m.notice) {
      const d = document.createElement("div");
      d.className = "notice";
      d.textContent = m.notice;
      return d;
    }
    if (m.warning) {
      const d = document.createElement("div");
      d.className = "warning";
      d.textContent = m.warning;
      return d;
    }
    const div = document.createElement("div");
    div.className = "msg " + (m.direction === "outgoing" || m.is_self ? "outgoing" : "incoming");
    if (m.mention) div.classList.add("mention");   // someone @-mentioned us
    const time = new Date(m.timestamp || Date.now());
    const author = document.createElement("span");
    author.className = "author";
    author.textContent = m.author;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const body = document.createElement("span");
    body.className = "body";
    body.textContent = m.body;
    div.append(author, meta, body);
    return div;
  }

  function renderOccupants() {
    const el = $("#occupants");
    const c = state.convos.get(state.active);
    if (!c || c.kind !== "muc") { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    el.innerHTML = "";

    const head = document.createElement("div");
    head.className = "occ-head";
    head.textContent = `Members — ${c.occupants.length}`;
    el.appendChild(head);

    [...c.occupants]
      .sort((a, b) => a.nick.localeCompare(b.nick))
      .forEach((o) => {
        const row = document.createElement("div");
        row.className = "occ";
        // Hover shows the member's real JID when the room exposes it, else the
        // in-room address (room@server/nick).
        row.title = o.jid || `${c.id}/${o.nick}`;
        // Color the avatar from a stable id; show presence as the corner dot.
        row.appendChild(makeAvatar(o.jid || `${c.id}/${o.nick}`, o.nick, o.show || "online"));
        const name = document.createElement("span");
        name.className = "occ-name";
        name.textContent = o.nick;
        row.appendChild(name);
        el.appendChild(row);
      });
  }

  function updateTitle() {
    let total = 0;
    for (const c of state.convos.values()) total += c.unread;
    document.title = (total > 0 ? `(${total}) ` : "") + state.brandTitle;
    $("#brand-title").textContent = state.brandTitle;
    $("#my-dot").className = "presence-dot " + ($("#presence-select").value || "online");
  }

  function scrollMessages() {
    const wrap = $("#messages-wrap");
    requestAnimationFrame(() => { wrap.scrollTop = wrap.scrollHeight; });
  }

  // ---- Incoming message handling ------------------------------------------
  function bump(convoId, mention) {
    const c = state.convos.get(convoId);
    if (c && !(convoId === state.active && !document.hidden)) {
      c.unread += 1;                                // count each message once
      if (state.soundEnabled) (mention ? Sound.mention() : Sound.blip());
    }
  }

  // ---- WebSocket -----------------------------------------------------------
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws${location.search}`;
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.onmessage = (ev) => handleFrame(JSON.parse(ev.data));
    ws.onclose = () => {
      $("#composer-input").disabled = true;
      $("#send-btn").disabled = true;
      setTimeout(connect, 2000);                    // browser<->server reconnect
    };
  }

  function send(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(obj));
    }
  }

  function handleFrame(f) {
    switch (f.type) {
      case "ready":
        state.me = f.jid;
        state.username = f.username;
        state.brandTitle = f.brand_title || "PyConduit";
        state.soundEnabled = loadSoundPref(f.sound_enabled_default);
        state.mucServers = f.muc_servers || [];
        if (f.max_message_chars) $("#composer-input").maxLength = f.max_message_chars;
        $("#sound-toggle").checked = state.soundEnabled;
        renderAll();
        break;

      case "roster":
        f.contacts.forEach((ct) => {
          const cur = state.roster.get(ct.jid) || {};
          state.roster.set(ct.jid, {
            name: ct.name, subscription: ct.subscription,
            show: cur.show || "offline", status: cur.status,
          });
        });
        renderAll();
        break;

      case "presence": {
        const r = state.roster.get(f.jid) || { name: f.jid, subscription: "none" };
        r.show = f.show; r.status = f.status;
        state.roster.set(f.jid, r);
        renderAll();
        break;
      }

      case "message": {
        const c = getConvo(f.conversation, "chat");
        c.messages.push({
          author: f.direction === "outgoing" ? "You" : (labelFor(f.from)),
          body: f.body, timestamp: f.timestamp, direction: f.direction, id: f.id,
        });
        if (f.direction === "incoming") bump(f.conversation);
        if (f.conversation === state.active) { renderMessages(); scrollMessages(); }
        renderContacts(); updateTitle();
        break;
      }

      case "history": {
        const c = getConvo(f.conversation, "chat");
        c.complete = f.complete;
        if (f.messages.length) c.oldestId = f.messages[0].id;
        const mapped = f.messages.map((m) => ({
          author: m.direction === "outgoing" ? "You" : labelFor(m.from),
          body: m.body, timestamp: m.timestamp, direction: m.direction, id: m.id,
        }));
        c.messages = mapped.concat(c.messages);
        if (f.conversation === state.active) renderMessages();
        break;
      }

      case "subscription_request":
        showSubscriptionRequest(f.jid);
        break;

      case "muc_joined": {
        const c = getConvo(f.room, "muc", f.room.split("@")[0]);
        c.name = f.room.split("@")[0];
        renderRooms();
        if (!state.active) setActive(f.room);
        break;
      }

      case "muc_left": {
        state.convos.delete(f.room);
        if (state.active === f.room) state.active = null;
        renderAll();
        break;
      }

      case "muc_message": {
        const c = getConvo(f.room, "muc");
        c.messages.push({
          author: f.nick, body: f.body, timestamp: f.timestamp,
          is_self: f.is_self, id: f.id, mention: f.mentions_me,
        });
        if (!f.is_self) bump(f.room, f.mentions_me);
        if (f.room === state.active) { renderMessages(); scrollMessages(); }
        renderRooms(); updateTitle();
        break;
      }

      case "muc_occupants": {
        const c = getConvo(f.room, "muc");
        c.occupants = f.occupants;
        if (f.room === state.active) { renderOccupants(); renderConvoHeader(); }
        break;
      }

      case "muc_presence": {
        const c = getConvo(f.room, "muc");
        c.messages.push({ notice: `${f.nick} ${f.joined ? "joined" : "left"}`,
                          timestamp: Date.now() });
        if (f.room === state.active) { renderMessages(); scrollMessages(); }
        break;
      }

      case "disco_servers":
        renderDiscoverServers(f.servers);
        break;

      case "error": {
        const target = f.conversation && state.convos.get(f.conversation);
        if (target) {
          target.messages.push({ warning: f.message, timestamp: Date.now() });
          if (f.conversation === state.active) { renderMessages(); scrollMessages(); }
        } else {
          alert(f.message);
        }
        break;
      }
    }
  }

  function labelFor(jid) {
    const r = state.roster.get(jid);
    return (r && r.name) || jid;
  }

  // ---- Subscription requests ----------------------------------------------
  function showSubscriptionRequest(jid) {
    const el = $("#subscription-banner");
    el.classList.remove("hidden");
    el.innerHTML = "";
    const t = document.createElement("div");
    t.textContent = `${jid} wants to add you as a contact.`;
    const actions = document.createElement("div");
    actions.className = "sub-actions";
    const accept = document.createElement("button");
    accept.className = "btn-accept"; accept.textContent = "Accept";
    accept.onclick = () => { send({ type: "subscription", jid, action: "accept" });
                             send({ type: "add_contact", jid }); el.classList.add("hidden"); };
    const decline = document.createElement("button");
    decline.className = "btn-decline"; decline.textContent = "Decline";
    decline.onclick = () => { send({ type: "subscription", jid, action: "decline" });
                              el.classList.add("hidden"); };
    actions.append(accept, decline);
    el.append(t, actions);
  }

  // ---- User actions --------------------------------------------------------
  function addContact() {
    const jid = prompt("Contact JID (e.g. bob@example.com):");
    if (jid) {
      const bare = jid.trim();
      send({ type: "add_contact", jid: bare });
      setActive(bare);  // open the conversation right away
    }
  }
  function removeContact(jid) {
    if (confirm(`Remove ${jid}?`)) {
      send({ type: "remove_contact", jid });
      state.roster.delete(jid);
      state.convos.delete(jid);
      if (state.active === jid) state.active = null;
      renderAll();
    }
  }
  function leaveRoom(room) {
    if (confirm(`Leave ${room.split("@")[0]}?`)) send({ type: "leave_room", room });
  }

  // ---- Discover modal ------------------------------------------------------
  // Render the configured MUC servers (already sorted server-side), each with an
  // online/offline badge and its rooms. Non-responding servers show as offline.
  function renderDiscoverServers(servers) {
    const root = $("#discover-results");
    root.innerHTML = "";
    if (!servers || !servers.length) {
      root.innerHTML = "<div class='disco-empty'>No MUC servers configured.</div>";
      return;
    }
    servers.forEach((srv) => {
      const group = document.createElement("div");
      group.className = "disco-server";

      const head = document.createElement("div");
      head.className = "disco-server-head";
      const name = document.createElement("span");
      name.textContent = srv.server;
      const badge = document.createElement("span");
      badge.className = "disco-badge " + (srv.online ? "online" : "offline");
      badge.textContent = srv.online ? "online" : "offline";
      head.append(name, badge);
      group.appendChild(head);

      if (srv.online && srv.rooms.length) {
        const ul = document.createElement("ul");
        ul.className = "list disco-rooms";
        srv.rooms.forEach((room) => {
          const li = document.createElement("li");
          const label = document.createElement("span");
          label.className = "name";
          label.textContent = room.name;
          li.appendChild(label);
          li.title = room.jid;
          li.onclick = () => {
            send({ type: "join_room", room: room.jid });
            $("#discover-modal").classList.add("hidden");
          };
          ul.appendChild(li);
        });
        group.appendChild(ul);
      } else {
        const empty = document.createElement("div");
        empty.className = "disco-empty";
        empty.textContent = srv.online ? "No rooms." : "Server did not respond.";
        group.appendChild(empty);
      }
      root.appendChild(group);
    });
  }

  // ---- Wiring --------------------------------------------------------------
  function wire() {
    $("#composer").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("#composer-input");
      const body = input.value.trim();
      const c = state.convos.get(state.active);
      if (!body || !c) return;
      if (c.kind === "muc") send({ type: "send_muc", room: c.id, body });
      else send({ type: "send_message", to: c.id, body });
      input.value = "";
    });

    $("#presence-select").addEventListener("change", (e) => {
      send({ type: "set_presence", show: e.target.value });
      updateTitle();
    });

    $("#add-contact-btn").onclick = addContact;
    $("#leave-room-btn").onclick = () => {
      const c = state.convos.get(state.active);
      if (c && c.kind === "muc") leaveRoom(c.id);
    };
    $("#discover-btn").onclick = () => {
      // Browse the operator-configured MUC servers. The domains live server-side;
      // the client sends no server name, so users can't reach arbitrary domains.
      $("#discover-results").innerHTML = "<div class='disco-loading'>Loading…</div>";
      $("#discover-modal").classList.remove("hidden");
      send({ type: "disco_servers" });
    };
    $("#discover-close").onclick = () => $("#discover-modal").classList.add("hidden");

    $("#load-older").onclick = () => {
      const c = state.convos.get(state.active);
      if (c) send({ type: "load_history", jid: c.id, before: c.oldestId });
    };

    $("#settings-btn").onclick = () => $("#settings-modal").classList.remove("hidden");
    $("#settings-close").onclick = () => $("#settings-modal").classList.add("hidden");
    $("#sound-toggle").addEventListener("change", (e) => {
      state.soundEnabled = e.target.checked;
      localStorage.setItem("pyconduit.sound", e.target.checked ? "1" : "0");
    });

    window.addEventListener("focus", () => {
      const c = state.convos.get(state.active);
      if (c) { c.unread = 0; renderAll(); }
    });
  }

  wire();
  connect();
})();
