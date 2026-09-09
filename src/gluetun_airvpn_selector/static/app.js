import Fuse from "https://cdn.jsdelivr.net/npm/fuse.js@7.5.0/dist/fuse.min.mjs";

import { facets, matchingRows, pruneSelection, sameSelection, toggleSelection } from "./selection.js";

function initialize(root = document) {
  const dashboard = root.matches?.("#dashboard") ? root : root.querySelector?.("#dashboard");
  if (!dashboard || dashboard.dataset.enhanced === "true") return;
  dashboard.dataset.enhanced = "true";

  const inputs = [...dashboard.querySelectorAll("input[data-filter]")];
  const rows = JSON.parse(dashboard.dataset.rows);
  const confirmed = JSON.parse(dashboard.dataset.selection);
  const draft = pruneSelection(rows, confirmed);
  const context = {
    dashboard,
    inputs,
    rows,
    confirmed,
    selection: draft.selection,
    saving: false,
    search: setupSearch(dashboard.querySelector("#filter-search"), dashboard),
  };

  inputs.forEach((input) => {
    input.addEventListener("change", () => {
      const result = toggleSelection(rows, context.selection, input.dataset.filter, input.value);
      context.selection = result.selection;
      announceRemoval(dashboard, result.removed);
      updatePreview(context);
    });
    const describe = () => describeToggle(context, input);
    input.closest(".option-card").addEventListener("mouseenter", describe);
    input.addEventListener("focus", describe);
  });

  dashboard.querySelector("#save-selection").addEventListener("click", () => saveSelection(context));
  announceRemoval(dashboard, draft.removed);
  updatePreview(context);
}

function updatePreview(context) {
  const { dashboard, inputs, rows, selection, confirmed, saving } = context;
  const options = facets(rows, selection);
  const matches = matchingRows(rows, selection);
  const canChange = dashboard.dataset.canChange === "true" && !saving;

  dashboard.querySelector("#match-count").textContent = `${matches.length} servers match`;
  dashboard.querySelector("#save-selection").disabled = !canChange || matches.length === 0 || sameSelection(selection, confirmed);
  for (const input of inputs) {
    const option = options[input.dataset.filter].get(input.value.toLowerCase());
    const card = input.closest(".option-card");
    const state = option?.state ?? "unavailable";
    card.dataset.state = state;
    card.removeAttribute("title");
    input.removeAttribute("aria-description");
    input.checked = state === "selected" || state === "implied";
    input.disabled = !canChange || state === "unavailable" || state === "implied";
    card.querySelector(".option-state").textContent = state === "selected" ? "Selected" : state === "implied" ? "Implied" : "";
    if (!option) continue;
    const count = card.querySelector(".option-count");
    count.textContent = option.count;
    count.setAttribute("aria-label", `${option.count} servers`);
    card.querySelector(".option-metrics").textContent = `${formatBandwidthUsage(option.bandwidth, option.bandwidthMax)} utilized · ${formatUsers(option.users)} users`;
    const utilization = bandwidthUtilization(option.bandwidth, option.bandwidthMax);
    const progress = card.querySelector("progress");
    progress.value = utilization;
    progress.className = `utilization utilization-${utilizationColor(utilization)}`;
    progress.setAttribute("aria-label", `${Math.round(utilization)}% bandwidth utilization`);
  }
  for (const section of dashboard.querySelectorAll("[data-filter-section]")) {
    section.querySelector("[data-option-count]").textContent = `${options[section.dataset.filterSection].size} options`;
  }
  context.search();
}

function removalList(removed) {
  return removed.map(({ field, value }) => `${value} (${field})`).join(", ");
}

function announceRemoval(dashboard, removed) {
  const notice = dashboard.querySelector("#selection-notice");
  notice.textContent = removed.length ? `Deselected: ${removalList(removed)}.` : "";
  notice.hidden = removed.length === 0;
}

function describeToggle(context, input) {
  const card = input.closest(".option-card");
  let description;
  if (card.dataset.state === "implied") {
    description = `${input.value} follows from the other filters.`;
  } else {
    const result = toggleSelection(context.rows, context.selection, input.dataset.filter, input.value);
    description = `${input.checked ? "Deselect" : "Select"} ${input.value}.`;
    if (result.removed.length) description += ` This also deselects ${removalList(result.removed)}.`;
  }
  card.title = description;
  input.setAttribute("aria-description", description);
}

function bandwidthUtilization(bandwidth, bandwidthMax) {
  const maximum = Number(bandwidthMax);
  if (maximum <= 0) return 0;
  return Math.min(100, Math.max(0, Number(bandwidth) / maximum * 100));
}

function utilizationColor(value) {
  if (value >= 95) return "red";
  if (value >= 75) return "yellow";
  return "green";
}

function formatUsers(value) {
  return Math.round(Number(value)).toLocaleString("en-US");
}

function formatBandwidthUsage(bandwidth, bandwidthMax) {
  const maximum = Number(bandwidthMax);
  const [scale, unit] = bandwidthUnit(maximum);
  const percentage = Math.round(bandwidthUtilization(bandwidth, bandwidthMax));
  return `${percentage}% of ${formatDecimal(maximum / scale)} ${unit}`;
}

function bandwidthUnit(mbps) {
  if (mbps >= 1_000_000) return [1_000_000, "Tbps"];
  if (mbps >= 1_000) return [1_000, "Gbps"];
  return [1, "Mbps"];
}

function formatDecimal(value) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function setupSearch(input, dashboard) {
  const cards = [...dashboard.querySelectorAll(".option-card")];
  const records = cards.map((element) => ({ element, text: element.dataset.search }));
  const fuse = new Fuse(records, { keys: ["text"], threshold: 0.35, ignoreLocation: true });
  const update = () => {
    const query = input.value.trim();
    const matches = query ? new Set(fuse.search(query).map((result) => result.item.element)) : new Set(cards);
    cards.forEach((card) => { card.hidden = card.dataset.state === "unavailable" || !matches.has(card); });
  };
  input.addEventListener("input", update);
  return update;
}

async function saveSelection(context) {
  const { dashboard, selection } = context;
  const error = dashboard.querySelector("#selection-error");
  context.saving = true;
  updatePreview(context);
  hideError(error);
  try {
    const response = await fetch("/api/selection", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selection),
    });
    const result = await response.json();
    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }
    if (!response.ok) throw new Error(result.error?.message || "The selection update failed.");
    let publicIpError = "";
    try {
      await requestPublicIp(true);
    } catch (requestError) {
      publicIpError = requestError.message;
    }
    await refreshDashboard(true);
    if (publicIpError) showError(document.querySelector("#selection-error"), publicIpError);
  } catch (requestError) {
    showError(error, requestError.message);
  } finally {
    context.saving = false;
    updatePreview(context);
  }
}

async function requestPublicIp(afterSelection = false) {
  const path = afterSelection ? "/api/connectivity-test?after_selection=1" : "/api/connectivity-test";
  const response = await fetch(path, { method: "POST" });
  const result = await response.json();
  if (response.status === 401) {
    window.location.assign("/login");
    return;
  }
  if (!response.ok) throw new Error(result.error?.message || "The public IP check failed.");
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function hideError(element) {
  element.textContent = "";
  element.hidden = true;
}

function refreshDashboard(skipPublicIp = false) {
  const path = skipPublicIp ? "/?skip_public_ip=1" : "/";
  return window.htmx.ajax("GET", path, { target: "#dashboard", select: "#dashboard", swap: "outerHTML" });
}

initialize();
document.addEventListener("htmx:after:swap", () => initialize());
