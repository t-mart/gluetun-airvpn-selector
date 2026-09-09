import assert from "node:assert/strict";
import test from "node:test";
import { attributes, facets, fields, matchingRows, pruneSelection, sameSelection, toggleSelection } from "../src/gluetun_airvpn_selector/static/selection.js";

const row = (name, country, city, region) => ({ name, country, city, region, bandwidth: 1250, bandwidthMax: 2000, users: 10 });
const rows = [
  row("Albali", "Sweden", "Uppsala", "Europe"),
  row("Algorab", "Sweden", "Stockholm", "Europe"),
  row("Alrami", "Sweden", "Stockholm", "Europe"),
  row("Nor", "Norway", "Oslo", "Europe"),
  row("Japan1", "Japan", "Tokyo", "Asia"),
];
const selection = (values = {}) => ({ names: [], countries: [], cities: [], regions: [], ...values });
const values = (options) => [...options.values()].map((option) => option.value).sort();
const toggle = (selected, field, value) => toggleSelection(rows, selected, field, value);

test("empty filters offer every value with row counts", () => {
  const options = facets(rows, selection());
  assert.deepEqual(fields.map((field) => options[field].size), [5, 3, 4, 2]);
  assert.equal(options.countries.get("sweden").count, 3);
  assert.equal(options.cities.get("stockholm").count, 2);
  assert.equal(matchingRows(rows, selection()).length, 5);
});

test("Sweden keeps all countries available and implies Europe", () => {
  const selected = toggle(selection(), "countries", "Sweden").selection;
  const options = facets(rows, selected);
  assert.deepEqual(values(options.countries), ["Japan", "Norway", "Sweden"]);
  assert.deepEqual(values(options.cities), ["Stockholm", "Uppsala"]);
  assert.deepEqual(values(options.names), ["Albali", "Algorab", "Alrami"]);
  assert.equal(options.regions.get("europe").state, "implied");
  assert.equal(options.countries.get("sweden").state, "selected");
  assert.deepEqual(toggle(selected, "regions", "Europe"), { selection: selected, removed: [] });
  assert.deepEqual(selected.regions, []);
});

test("additional countries widen the result and candidate sets", () => {
  const sweden = selection({ countries: ["Sweden"] });
  const norway = toggle(sweden, "countries", "Norway").selection;
  assert.equal(facets(rows, norway).regions.get("europe").state, "implied");
  assert.deepEqual(values(facets(rows, norway).cities), ["Oslo", "Stockholm", "Uppsala"]);
  const japan = toggle(sweden, "countries", "Japan");
  assert.deepEqual(japan.removed, []);
  assert.equal(matchingRows(rows, japan.selection).length, 4);
  assert.deepEqual(values(facets(rows, japan.selection).regions), ["Asia", "Europe"]);
  assert.deepEqual(values(facets(rows, japan.selection).cities), ["Stockholm", "Tokyo", "Uppsala"]);
});

test("Europe excludes Japan as an option and prunes an existing Japan selection", () => {
  const europe = selection({ regions: ["Europe"] });
  assert.equal(facets(rows, europe).countries.has("japan"), false);
  assert.deepEqual(toggle(europe, "countries", "Japan").selection, europe);
  const result = toggle(selection({ countries: ["Sweden", "Japan"] }), "regions", "Europe");
  assert.deepEqual(result.selection, selection({ countries: ["Sweden"], regions: ["Europe"] }));
  assert.deepEqual(result.removed, [{ field: "countries", value: "Japan" }]);
});

test("deselection prunes cities and names that lose their last row", () => {
  const result = toggle(selection({
    countries: ["Sweden", "Japan"], cities: ["Uppsala", "Tokyo"], names: ["Albali", "Japan1"],
  }), "countries", "Japan");
  assert.deepEqual(result.selection, selection({ countries: ["Sweden"], cities: ["Uppsala"], names: ["Albali"] }));
  assert.deepEqual(result.removed, [{ field: "names", value: "Japan1" }, { field: "cities", value: "Tokyo" }]);
});

test("a second city widens and a server name retains compatible filters", () => {
  const cities = toggle(selection({ countries: ["Sweden"], cities: ["Uppsala"] }), "cities", "Stockholm");
  assert.equal(matchingRows(rows, cities.selection).length, 3);
  const name = toggle(cities.selection, "names", "Albali");
  assert.deepEqual(name.selection, selection({ countries: ["Sweden"], cities: ["Uppsala"], names: ["Albali"] }));
  assert.deepEqual(name.removed, [{ field: "cities", value: "Stockholm" }]);
});

test("a server name implies three sections and a city implies two", () => {
  const name = facets(rows, selection({ names: ["Albali"] }));
  for (const field of ["countries", "cities", "regions"]) {
    assert.equal([...name[field].values()][0].state, "implied");
  }
  const city = facets(rows, selection({ cities: ["Stockholm"] }));
  assert.equal(city.countries.get("sweden").state, "implied");
  assert.equal(city.regions.get("europe").state, "implied");
  assert.equal(city.names.size, 2);
});

test("candidate badges and metrics ignore only their own filter", () => {
  const options = facets(rows, selection({ countries: ["Sweden"], cities: ["Stockholm"] }));
  assert.equal(options.countries.get("sweden").count, 2);
  assert.equal(options.countries.get("sweden").bandwidth, 2500);
  assert.equal(options.countries.get("sweden").bandwidthMax, 4000);
  assert.equal(options.countries.get("sweden").users, 20);
  assert.equal(options.cities.get("uppsala").count, 1);
});

test("shared names, cities, and countries preserve row relationships", () => {
  const shared = [
    row("Shared", "One", "Twin", "East"),
    row("Shared", "Two", "Other", "West"),
    row("Third", "Two", "Twin", "East"),
    row("Fourth", "One", "Elsewhere", "West"),
  ];
  const options = facets(shared, selection({ names: ["Shared"] }));
  assert.deepEqual(values(options.countries), ["One", "Two"]);
  assert.deepEqual(values(options.cities), ["Other", "Twin"]);
  assert.deepEqual(values(options.regions), ["East", "West"]);
  assert.equal(options.names.get("shared").count, 2);
  const countries = facets(shared, selection({ countries: ["One"] }));
  assert.deepEqual(values(countries.regions), ["East", "West"]);
  const city = facets(shared, selection({ cities: ["Twin"] }));
  assert.deepEqual(values(city.countries), ["One", "Two"]);
  const result = toggleSelection(shared, selection({ names: ["Shared"], countries: ["One", "Two"] }), "cities", "Twin");
  assert.deepEqual(result.selection.countries, ["One"]);
  assert.deepEqual(result.removed, [{ field: "countries", value: "Two" }]);
});

test("stale and contradictory initial filters reach a stable draft", () => {
  const stale = pruneSelection(rows, selection({ names: ["Missing", "Albali"] }));
  assert.deepEqual(stale.selection.names, ["Albali"]);
  const contradictory = pruneSelection(rows, selection({ countries: ["Japan"], regions: ["Europe"] }));
  assert.deepEqual(contradictory.selection, selection());
  assert.equal(contradictory.removed.length, 2);
  assert.deepEqual(pruneSelection(rows, contradictory.selection).removed, []);
  assert.deepEqual(pruneSelection([], selection()).selection, selection());
});

test("comparisons ignore case and input selections remain unchanged", () => {
  const original = selection({ countries: ["SWEDEN"] });
  const copy = structuredClone(original);
  assert.equal(facets(rows, original).countries.get("sweden").state, "selected");
  assert.deepEqual(toggle(original, "countries", "sweden").selection, selection());
  assert.deepEqual(original, copy);
  assert.equal(sameSelection(original, selection({ countries: ["Sweden"] })), true);
});

test("every reachable toggle leaves rows and support for every selected value", () => {
  const pending = [selection()];
  const visited = new Set();
  while (pending.length) {
    const current = pending.pop();
    const identity = JSON.stringify(fields.map((field) => [...current[field]].sort()));
    if (visited.has(identity)) continue;
    visited.add(identity);
    const matches = matchingRows(rows, current);
    assert.ok(matches.length > 0);
    for (const field of fields) {
      for (const value of current[field]) assert.ok(matches.some((row) => row[attributes[field]] === value));
      for (const option of facets(rows, current)[field].values()) {
        pending.push(toggle(current, field, option.value).selection);
      }
    }
  }
  assert.ok(visited.size > 100);
});
