import Fuse from "https://cdn.jsdelivr.net/npm/fuse.js@7.5.0/dist/fuse.min.mjs";

const fields = ["names", "countries", "cities", "regions"];
const attributes = { names: "name", countries: "country", cities: "city", regions: "region" };

function initialize(root = document) {
  const dashboard = root.matches?.("#dashboard") ? root : root.querySelector?.("#dashboard");
  if (!dashboard || dashboard.dataset.enhanced === "true") return;
  dashboard.dataset.enhanced = "true";

  const inputs = [...dashboard.querySelectorAll("input[data-filter]")];
  const confirmedSelection = selectedValues(inputs);
  const serverCards = [...dashboard.querySelectorAll("[data-filter-section='names'] .option-card[data-name]")];
  const allServers = serverCards.map(serverFromElement);

  inputs.forEach((input) => input.addEventListener("change", () => {
    if (input.dataset.filter === "names") {
      inputs.filter((item) => item.dataset.filter !== "names").forEach((item) => { item.checked = false; });
    } else {
      inputs.filter((item) => item.dataset.filter === "names").forEach((item) => { item.checked = false; });
    }
    updatePreview(dashboard, inputs, allServers);
  }));
  dashboard.querySelector("#selected-servers")?.addEventListener("click", (event) => {
    const card = event.target.closest("[data-server]");
    if (!card) return;
    const currentMatches = matchingServers(selectedValues(inputs), allServers);
    if (currentMatches.length <= 1) {
      const error = dashboard.querySelector("#selection-error");
      showError(error, "At least one eligible server is required.");
      error.dataset.preview = "true";
      return;
    }
    inputs.filter((input) => input.dataset.filter !== "names").forEach((input) => { input.checked = false; });
    const names = inputs.filter((input) => input.dataset.filter === "names");
    if (!names.some((input) => input.checked)) {
      const remaining = new Set(currentMatches.filter((server) => server.name !== card.dataset.name).map((server) => server.name));
      names.forEach((input) => { input.checked = remaining.has(input.value); });
    } else {
      const selected = names.find((input) => input.value === card.dataset.name);
      if (selected) selected.checked = false;
    }
    updatePreview(dashboard, inputs, allServers);
  });

  const search = dashboard.querySelector("#filter-search");
  if (search) setupSearch(search, dashboard);

  dashboard.querySelector("#save-selection")?.addEventListener("click", () => saveSelection(dashboard, inputs, confirmedSelection, allServers));
  dashboard.querySelector("#connectivity-test")?.addEventListener("click", () => checkPublicIp(dashboard));
  updatePreview(dashboard, inputs, allServers);
}

function serverFromElement(element) {
  return {
    name: element.dataset.name,
    country: element.dataset.country,
    city: element.dataset.city,
    region: element.dataset.region,
    bandwidth: element.dataset.bandwidth,
    bandwidthMax: element.dataset.bandwidthMax,
    users: element.dataset.users,
    load: element.dataset.load,
    flag: element.dataset.flag,
  };
}

function selectedValues(inputs) {
  return Object.fromEntries(fields.map((field) => [
    field,
    inputs.filter((input) => input.dataset.filter === field && input.checked).map((input) => input.value),
  ]));
}

function updatePreview(dashboard, inputs, servers) {
  const selection = selectedValues(inputs);
  const matches = matchingServers(selection, servers);

  dashboard.querySelector("#eligible-count").textContent = matches.length;
  renderSelectedServers(dashboard.querySelector("#selected-servers"), matches);
  const save = dashboard.querySelector("#save-selection");
  save.disabled = dashboard.dataset.canChange !== "true" || matches.length === 0;
  const error = dashboard.querySelector("#selection-error");
  if (matches.length === 0) {
    showError(error, "The selected filters match no healthy AirVPN servers.");
    error.dataset.preview = "true";
  } else if (error.dataset.preview === "true") {
    hideError(error);
  }
}

function matchingServers(selection, servers) {
  return servers.filter((server) => fields.every((field) => {
    const values = selection[field].map((value) => value.toLocaleLowerCase());
    return values.length === 0 || values.includes(server[attributes[field]].toLocaleLowerCase());
  }));
}

function renderSelectedServers(container, servers) {
  container.replaceChildren();
  if (servers.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No server matches the selected filters.";
    container.append(empty);
    return;
  }
  servers.forEach((server) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "server-card";
    card.dataset.server = "";
    card.dataset.name = server.name;
    card.setAttribute("aria-label", `Exclude ${server.name} from the selection`);
    const title = document.createElement("div");
    title.className = "server-title";
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = server.flag;
    const name = document.createElement("strong");
    name.textContent = server.name;
    const location = document.createElement("span");
    location.textContent = [server.city, server.country].filter(Boolean).join(", ");
    const metrics = document.createElement("small");
    metrics.textContent = `${Math.round(server.bandwidth)} / ${Math.round(server.bandwidthMax)} Mbps · ${Math.round(server.users)} users · ${Math.round(server.load)}% load`;
    title.append(flag, name);
    card.append(title, location, metrics);
    container.append(card);
  });
}

function setupSearch(input, dashboard) {
  const cards = [...dashboard.querySelectorAll(".option-card")];
  const records = cards.map((element) => ({ element, text: element.dataset.search }));
  const fuse = new Fuse(records, { keys: ["text"], threshold: 0.35, ignoreLocation: true });
  input.addEventListener("input", () => {
    const query = input.value.trim();
    const matches = query ? new Set(fuse.search(query).map((result) => result.item.element)) : new Set(cards);
    cards.forEach((card) => { card.hidden = !matches.has(card); });
  });
}

async function saveSelection(dashboard, inputs, confirmedSelection, servers) {
  const button = dashboard.querySelector("#save-selection");
  const error = dashboard.querySelector("#selection-error");
  button.disabled = true;
  hideError(error);
  try {
    const response = await fetch("/api/selection", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selectedValues(inputs)),
    });
    const result = await response.json();
    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }
    if (!response.ok) throw new Error(result.error?.message || "The selection update failed.");
    await refreshDashboard(true);
    await checkPublicIp(document.querySelector("#dashboard"), true);
  } catch (requestError) {
    inputs.forEach((input) => {
      input.checked = confirmedSelection[input.dataset.filter].includes(input.value);
    });
    updatePreview(dashboard, inputs, servers);
    showError(error, requestError.message);
  }
}

async function checkPublicIp(dashboard, afterSelection = false) {
  const button = dashboard.querySelector("#connectivity-test");
  const card = button.closest(".public-ip-card");
  const detail = dashboard.querySelector("#public-ip-detail");
  const error = dashboard.querySelector("#selection-error");
  const previousDetail = detail.textContent;
  const previousLabel = button.textContent;
  let complete = false;
  button.disabled = true;
  button.textContent = "Please wait";
  card.classList.add("is-loading");
  card.setAttribute("aria-busy", "true");
  detail.textContent = "The public IP check is in progress.";
  hideError(error);
  try {
    const path = afterSelection ? "/api/connectivity-test?after_selection=1" : "/api/connectivity-test";
    const response = await fetch(path, { method: "POST" });
    const result = await response.json();
    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }
    if (!response.ok) throw new Error(result.error?.message || "The public IP check failed.");
    dashboard.querySelector("#public-ip").textContent = result.ip;
    detail.textContent = `${result.duration_ms}ms at ${result.observed_at} · ${result.source_url}`;
    complete = true;
  } catch (requestError) {
    showError(error, requestError.message);
  } finally {
    if (!complete) detail.textContent = previousDetail;
    button.disabled = false;
    button.textContent = previousLabel;
    card.classList.remove("is-loading");
    card.removeAttribute("aria-busy");
  }
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function hideError(element) {
  element.textContent = "";
  element.hidden = true;
  delete element.dataset.preview;
}

function refreshDashboard(skipPublicIp = false) {
  const path = skipPublicIp ? "/?skip_public_ip=1" : "/";
  return window.htmx.ajax("GET", path, { target: "#dashboard", select: "#dashboard", swap: "outerHTML" });
}

initialize();
document.addEventListener("htmx:after:swap", () => initialize());
