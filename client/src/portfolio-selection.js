// Which saved portfolio the page should show on its own.
// While the user is creating a new one nothing is auto-selected, so the blank form stays blank.
export function autoSelect(list, selected, creating) {
  if (creating || selected || !list?.length) return null;
  return list[0].name;
}

// Where to go when creation is cancelled: the portfolio that was open before, if it still exists, else the first one.
export function selectionAfterCancel(list, previous) {
  const names = (list || []).map((p) => p.name);
  if (previous && names.includes(previous)) return previous;
  return names[0] ?? null;
}
