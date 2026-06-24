"use strict";

// Reads static JSON files exported by export.py.
// Works on GitHub Pages with no backend server.
const DATA_BASE = "./data";

// In-memory cache: month string → event array
const _cache = {};

// ── Data loading ─────────────────────────────────────────────────────────────

async function loadMonth(month) {
  if (_cache[month] !== undefined) return _cache[month];
  try {
    const resp = await fetch(`${DATA_BASE}/${month}.json`);
    _cache[month] = resp.ok ? await resp.json() : [];
  } catch {
    _cache[month] = [];
  }
  return _cache[month];
}

async function getEventDates(month) {
  const events = await loadMonth(month);
  const counts = {};
  events.forEach(e => { counts[e.date] = (counts[e.date] || 0) + 1; });
  return Object.entries(counts).map(([date, count]) => ({ date, count }));
}

async function getEvents(date) {
  const month = date.slice(0, 7);
  const events = await loadMonth(month);
  return events.filter(e => e.date === date);
}

async function getSources() {
  // Derive source counts from whichever months are already cached
  const counts = {};
  Object.values(_cache).flat().forEach(e => {
    counts[e.source] = (counts[e.source] || 0) + 1;
  });
  return Object.entries(counts)
    .map(([source, count]) => ({ source, count }))
    .sort((a, b) => b.count - a.count);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt12(t) {
  if (!t) return null;
  const [hStr, mStr] = t.split(":");
  const h = parseInt(hStr, 10);
  const m = mStr || "00";
  const ampm = h >= 12 ? "PM" : "AM";
  return `${h % 12 || 12}:${m} ${ampm}`;
}

function fmtDate(dateStr) {
  const [y, mo, d] = dateStr.split("-").map(Number);
  return new Date(y, mo - 1, d).toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric",
  });
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── Calendar ──────────────────────────────────────────────────────────────────

let calendar;
let activeSource = "";

async function loadMonthDots(start) {
  const month = start.toISOString().slice(0, 7);
  const dates = await getEventDates(month);
  calendar.removeAllEvents();
  dates.forEach(({ date, count }) => {
    if (activeSource) {
      // re-filter from cache
      const filtered = (_cache[month] || []).filter(e => e.source === activeSource && e.date === date);
      if (!filtered.length) return;
    }
    calendar.addEvent({
      start: date,
      allDay: true,
      title: `${activeSource
        ? (_cache[month] || []).filter(e => e.source === activeSource && e.date === date).length
        : count}`,
      color: "var(--accent)",
      extendedProps: { date },
    });
  });
}

// ── Panel ─────────────────────────────────────────────────────────────────────

function openPanel(dateStr, events) {
  const panel = document.getElementById("event-panel");
  document.getElementById("panel-date").textContent = fmtDate(dateStr);
  const shown = activeSource ? events.filter(e => e.source === activeSource) : events;
  document.getElementById("panel-count").textContent =
    shown.length === 0 ? "No events" : `${shown.length} event${shown.length > 1 ? "s" : ""}`;

  const list = document.getElementById("event-list");
  list.innerHTML = shown.length === 0
    ? `<div class="no-events">No events found for this day.</div>`
    : shown.map(renderCard).join("");

  panel.classList.remove("closed");
  list.scrollTop = 0;
  // Resize calendar after the CSS transition completes (250ms)
  setTimeout(() => calendar.updateSize(), 260);
}

function renderCard(e) {
  const time = fmt12(e.start_time);
  const end  = fmt12(e.end_time);
  const timeStr = time ? (end ? `${time} – ${end}` : time) : "";

  const img = e.image_url
    ? `<img class="event-img" src="${esc(e.image_url)}" alt="" loading="lazy" onerror="this.remove()">`
    : "";

  const title = e.url
    ? `<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>`
    : esc(e.title);

  const meta = [
    timeStr && `<div class="row"><span class="icon">🕐</span><span>${esc(timeStr)}</span></div>`,
    e.venue  && `<div class="row"><span class="icon">📍</span><span>${esc(e.venue)}</span></div>`,
    e.price  && `<div class="row"><span class="icon">🎟</span><span>${esc(e.price)}</span></div>`,
  ].filter(Boolean).join("");

  const desc = e.description
    ? `<div class="event-desc">${esc(e.description.slice(0, 200))}${e.description.length > 200 ? "…" : ""}</div>`
    : "";

  return `
    <div class="event-card">
      ${img}
      <div class="source-badge">${esc(e.source)}</div>
      <div class="event-title">${title}</div>
      <div class="event-meta">${meta}</div>
      ${desc}
    </div>`;
}

// ── Source filter dropdown ────────────────────────────────────────────────────

async function populateSources(month) {
  // Load current month to get real source counts
  await loadMonth(month);
  const sources = await getSources();
  const sel = document.getElementById("source-filter");
  // Remove old dynamic options (keep the "All sources" default)
  while (sel.options.length > 1) sel.remove(1);
  sources.forEach(({ source, count }) => {
    const opt = document.createElement("option");
    opt.value = source;
    opt.textContent = `${source} (${count})`;
    sel.appendChild(opt);
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const calEl = document.getElementById("calendar");

  calendar = new FullCalendar.Calendar(calEl, {
    initialView: "dayGridMonth",
    height: "auto",
    headerToolbar: { left: "prev,next today", center: "title", right: "" },

    datesSet(info) {
      const month = info.start.toISOString().slice(0, 7);
      loadMonthDots(info.start);
      populateSources(month);
    },

    async dateClick(info) {
      const events = await getEvents(info.dateStr);
      openPanel(info.dateStr, events);
    },

    async eventClick(info) {
      const dateStr = info.event.startStr.slice(0, 10);
      const events  = await getEvents(dateStr);
      openPanel(dateStr, events);
    },
  });

  calendar.render();

  document.getElementById("close-panel").addEventListener("click", () => {
    document.getElementById("event-panel").classList.add("closed");
    setTimeout(() => calendar.updateSize(), 260);
  });

  document.getElementById("source-filter").addEventListener("change", (e) => {
    activeSource = e.target.value;
    loadMonthDots(calendar.view.currentStart);
  });
});
