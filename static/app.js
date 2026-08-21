const form = document.getElementById("search-form");
const input = document.getElementById("flight-input");
const button = document.getElementById("search-btn");
const result = document.getElementById("result");
const cardTemplate = document.getElementById("tpl-card");

const LAST_FLIGHT_KEY = "lastFlightNumber";

function setLoading(isLoading) {
  button.disabled = isLoading;
  button.textContent = isLoading ? "Searching..." : "Search";
}

function showState(html) {
  result.innerHTML = html;
}

function showSpinner() {
  showState(`<div class="state"><div class="spinner"></div>Looking up flight...</div>`);
}

function showError(message) {
  showState(`<div class="state-error">${escapeHtml(message)}</div>`);
}

function showHelp(message) {
  showState(`
    <div class="state-help">
      <p>${escapeHtml(message)}</p>
      <p>
        1. Create a free account at
        <a href="https://rapidapi.com/aedbx-aedbx/api/aerodatabox" target="_blank" rel="noopener">RapidAPI - AeroDataBox</a>.<br>
        2. Subscribe to the API's free plan.<br>
        3. Start the server with your key:<br>
        <code>export AERODATABOX_API_KEY="your_key"</code><br>
        <code>python3 server.py</code>
      </p>
    </div>
  `);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.textContent;
}

function formatTime(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatTimeShort(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-US", { hour: "numeric", minute: "2-digit" });
}

function formatDelay(scheduledIso, revisedIso) {
  const scheduled = new Date(scheduledIso);
  const revised = new Date(revisedIso);
  if (isNaN(scheduled) || isNaN(revised)) return "";
  const diffMin = Math.round((revised - scheduled) / 60000);
  if (diffMin === 0) return "";
  const sign = diffMin > 0 ? "+" : "";
  return ` (${sign}${diffMin} min)`;
}

function fillStop(el, stop, kind) {
  if (!stop) {
    el.querySelector(".stop-code").textContent = "?";
    el.querySelector(".stop-city").textContent = "";
    el.querySelector(".time-scheduled").textContent = "";
    el.querySelector(".time-revised").textContent = "";
    el.querySelector(".stop-terminal").textContent = "";
    el.querySelector(".stop-gate").textContent = "";
    return;
  }
  el.querySelector(".stop-code").textContent = stop.iata || stop.icao || "?";
  el.querySelector(".stop-city").textContent = stop.municipality || stop.airportName || "";

  const scheduledEl = el.querySelector(".time-scheduled");
  const revisedEl = el.querySelector(".time-revised");
  scheduledEl.textContent = formatTime(stop.scheduledTime);
  if (stop.delayed && stop.revisedTime) {
    scheduledEl.classList.add("struck");
    const prefix = kind === "arrival" ? "New ETA: " : "Now: ";
    const delay = formatDelay(stop.scheduledTime, stop.revisedTime);
    revisedEl.textContent = `${prefix}${formatTimeShort(stop.revisedTime)}${delay}`;
  } else {
    scheduledEl.classList.remove("struck");
    revisedEl.textContent = "";
  }

  el.querySelector(".stop-terminal").textContent = stop.terminal ? `Terminal ${stop.terminal}` : "";
  el.querySelector(".stop-gate").textContent = stop.gate ? `Gate ${stop.gate}` : "";
}

function formatDuration(seconds) {
  const totalMin = Math.round(seconds / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} min`;
  return `${h} hr ${m} min`;
}

const LANDING_WINDOW_MS = 15 * 60000; // final descent starts ~15 min before ETA
const LANDED_GRACE_MS = 20 * 60000; // taxi-in + data lag before we trust "landed"

// Live trackers (including the upstream API) can lag reality by many minutes
// near touchdown, so once the ETA has clearly passed we override the raw
// status instead of leaving a stale "En Route" badge on screen.
function deriveStatus(flight) {
  const statusKey = (flight.statusRaw || "").toLowerCase().replace(/[^a-z]/g, "");
  const terminalStatus = ["arrived", "landed", "canceled", "cancelled", "canceleduncertain", "diverted"];
  if (terminalStatus.some((s) => statusKey.includes(s))) {
    const phase = statusKey.includes("divert") ? "diverted" : statusKey.includes("cancel") ? "cancelled" : "landed";
    return { label: flight.statusLabel, color: flight.statusColor, phase };
  }

  const arr = flight.arrival;
  const arrIso = arr && (arr.revisedTime || arr.scheduledTime);
  const arrTime = arrIso ? new Date(arrIso).getTime() : NaN;
  const now = Date.now();

  if (!isNaN(arrTime)) {
    if (now >= arrTime + LANDED_GRACE_MS) {
      return { label: "Landed", color: "green", phase: "landed" };
    }
    if (now >= arrTime - LANDING_WINDOW_MS) {
      return { label: "Landing", color: "teal", phase: "landing" };
    }
  }

  const isLive = ["departed", "enroute", "approaching"].some((s) => statusKey.includes(s));
  return { label: flight.statusLabel, color: flight.statusColor, phase: isLive ? "enroute" : "scheduled" };
}

function flightProgress(flight, phase) {
  if (phase === "landed") return 1;

  const dep = flight.departure;
  const arr = flight.arrival;
  if (!dep || !arr) return 0;

  const depTime = new Date(dep.revisedTime || dep.scheduledTime);
  const arrTime = new Date(arr.revisedTime || arr.scheduledTime);
  if (isNaN(depTime) || isNaN(arrTime) || arrTime <= depTime) return 0;

  const now = new Date();
  const fraction = (now - depTime) / (arrTime - depTime);
  return Math.min(1, Math.max(0, fraction));
}

function animateFlightPath(node, flight, phase) {
  const isLive = phase === "enroute" || phase === "landing";
  const progress = flightProgress(flight, phase);

  const plane = node.querySelector(".plane");
  const bar = node.querySelector(".route-progress");
  plane.style.left = `${progress * 100}%`;
  bar.style.width = `${progress * 100}%`;
  plane.classList.toggle("in-flight", isLive);
}

function setupDirections(node, itemEl, stop, kind, flight) {
  const btn = itemEl.querySelector(".directions-btn");
  const out = itemEl.querySelector(".directions-result");

  if (!stop || !stop.icao) {
    itemEl.remove();
    return;
  }

  const airportLabel = stop.iata || stop.icao;
  const defaultLabel = `📍 Travel time to ${airportLabel}`;
  btn.textContent = defaultLabel;

  btn.addEventListener("click", () => {
    if (!("geolocation" in navigator)) {
      out.classList.add("is-error");
      out.textContent = "Geolocation isn't available in this browser.";
      return;
    }

    btn.disabled = true;
    btn.textContent = "Locating you...";
    out.classList.remove("is-error");
    out.textContent = "";

    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const { latitude, longitude } = position.coords;
        try {
          const res = await fetch(
            `/api/travel-time?icao=${encodeURIComponent(stop.icao)}&lat=${latitude}&lon=${longitude}`
          );
          const data = await res.json();
          if (data.error) {
            out.classList.add("is-error");
            out.textContent = data.message || "Couldn't calculate travel time.";
          } else {
            const eta = new Date(Date.now() + data.durationSeconds * 1000);
            const etaText = eta.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
            const terminalText = stop.terminal ? ` (Terminal ${stop.terminal})` : "";
            out.classList.remove("is-error");
            let html =
              `&#128663; ${formatDuration(data.durationSeconds)} drive to ${airportLabel}${terminalText}.` +
              ` Leave now and arrive by <span class="eta">${etaText}</span>.`;

            if (kind === "arrival") {
              const landing = new Date(stop.revisedTime || stop.scheduledTime);
              if (!isNaN(landing)) {
                const spareSeconds = Math.round((landing - eta) / 1000);
                const spareText = formatDuration(Math.abs(spareSeconds));
                const landingText = landing.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
                if (spareSeconds >= 0) {
                  html += ` That's <span class="spare-ok">${spareText} before</span> the flight lands at ${landingText}.`;
                } else {
                  html += ` That's <span class="spare-late">${spareText} after</span> the flight lands at ${landingText}.`;
                }
              }
            }

            out.innerHTML = html;
          }
        } catch (err) {
          out.classList.add("is-error");
          out.textContent = "Couldn't reach the server for travel time.";
        } finally {
          btn.disabled = false;
          btn.textContent = defaultLabel;
        }
      },
      (err) => {
        out.classList.add("is-error");
        out.textContent =
          err.code === err.PERMISSION_DENIED
            ? "Location access denied. Enable it in your browser to see travel time."
            : "Couldn't determine your location.";
        btn.disabled = false;
        btn.textContent = defaultLabel;
      }
    );
  });
}

function pickMostRelevantFlight(flights) {
  if (flights.length <= 1) return flights[0] || null;

  const now = Date.now();
  let best = flights[0];
  let bestDiff = Infinity;

  for (const f of flights) {
    const depIso = f.departure && (f.departure.revisedTime || f.departure.scheduledTime);
    if (!depIso) continue;
    const depTime = new Date(depIso).getTime();
    const arrIso = f.arrival && (f.arrival.revisedTime || f.arrival.scheduledTime);
    const arrTime = arrIso ? new Date(arrIso).getTime() : depTime;

    // A flight currently in the air is always the one you're looking for.
    if (depTime <= now && now <= arrTime) return f;

    const diff = Math.min(Math.abs(depTime - now), Math.abs(arrTime - now));
    if (diff < bestDiff) {
      bestDiff = diff;
      best = f;
    }
  }
  return best;
}

function renderFlights(flights, flightNumber) {
  result.innerHTML = "";
  flights.forEach((flight) => {
    const node = cardTemplate.content.cloneNode(true);
    node.querySelector(".flight-number").textContent = flight.number || flightNumber;
    node.querySelector(".airline").textContent = flight.airline || "";

    const status = deriveStatus(flight);
    const badge = node.querySelector(".badge");
    badge.textContent = status.label;
    badge.classList.add(status.color || "gray");

    fillStop(node.querySelector(".stop-dep"), flight.departure, "departure");
    fillStop(node.querySelector(".stop-arr"), flight.arrival, "arrival");
    setupDirections(node, node.querySelector(".directions-arr"), flight.arrival, "arrival", flight);
    animateFlightPath(node, flight, status.phase);

    node.querySelector(".aircraft-model").textContent = flight.aircraftModel || "";
    node.querySelector(".aircraft-reg").textContent = flight.aircraftReg || "";
    node.querySelector(".refresh-btn").addEventListener("click", () => search(flightNumber));

    result.appendChild(node);
  });
}

async function search(flightNumber) {
  flightNumber = flightNumber.trim().toUpperCase();
  if (!flightNumber) return;

  input.value = flightNumber;
  localStorage.setItem(LAST_FLIGHT_KEY, flightNumber);
  setLoading(true);
  showSpinner();

  try {
    const res = await fetch(`/api/status?flight=${encodeURIComponent(flightNumber)}`);
    const data = await res.json();

    if (data.error === "missing_api_key") {
      showHelp(data.message);
    } else if (data.error) {
      showError(data.message || "Something went wrong.");
    } else if (!data.flights || data.flights.length === 0) {
      showError(`No flight found for ${flightNumber}.`);
    } else {
      const flight = pickMostRelevantFlight(data.flights);
      renderFlights([flight], flightNumber);
    }
  } catch (err) {
    showError("Couldn't reach the server. Make sure it's running.");
  } finally {
    setLoading(false);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  search(input.value);
});

const lastFlight = localStorage.getItem(LAST_FLIGHT_KEY);
if (lastFlight) {
  input.value = lastFlight;
  search(lastFlight);
}
