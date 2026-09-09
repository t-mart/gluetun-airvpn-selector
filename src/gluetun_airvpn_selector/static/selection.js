export const attributes = { names: "name", countries: "country", cities: "city", regions: "region" };
export const fields = Object.keys(attributes);

const key = (value) => value.toLowerCase();

export function matchingRows(rows, selection, excludedField) {
  const constraints = fields.filter((field) => field !== excludedField).map((field) => [
    attributes[field], new Set(selection[field].map(key)),
  ]);
  return rows.filter((row) => constraints.every(([attribute, values]) =>
    values.size === 0 || values.has(key(row[attribute]))));
}

export function facets(rows, selection) {
  return Object.fromEntries(fields.map((field) => {
    const options = new Map();
    for (const row of matchingRows(rows, selection, field)) {
      const value = row[attributes[field]];
      const option = options.get(key(value)) ?? { value, count: 0, bandwidth: 0, bandwidthMax: 0, users: 0 };
      option.count += 1;
      option.bandwidth += row.bandwidth;
      option.bandwidthMax += row.bandwidthMax;
      option.users += row.users;
      options.set(key(value), option);
    }
    const selected = new Set(selection[field].map(key));
    for (const [value, option] of options) {
      option.state = selected.has(value) ? "selected"
        : selected.size === 0 && options.size === 1 ? "implied" : "available";
    }
    return [field, options];
  }));
}

export function pruneSelection(rows, selection) {
  const removed = [];
  while (true) {
    const matches = matchingRows(rows, selection);
    const pruned = Object.fromEntries(fields.map((field) => {
      const supported = new Set(matches.map((row) => key(row[attributes[field]])));
      return [field, selection[field].filter((value) => {
        if (supported.has(key(value))) return true;
        removed.push({ field, value });
        return false;
      })];
    }));
    if (sameSelection(selection, pruned)) return { selection: pruned, removed };
    selection = pruned;
  }
}

export function toggleSelection(rows, selection, field, value) {
  const option = facets(rows, selection)[field].get(key(value));
  if (!option || option.state === "implied") return { selection, removed: [] };
  const selected = selection[field].some((item) => key(item) === key(value));
  return pruneSelection(rows, {
    ...selection,
    [field]: selected ? selection[field].filter((item) => key(item) !== key(value)) : [...selection[field], option.value],
  });
}

export function sameSelection(left, right) {
  return fields.every((field) => {
    const one = left[field].map(key).sort();
    const other = right[field].map(key).sort();
    return one.length === other.length && one.every((value, index) => value === other[index]);
  });
}
