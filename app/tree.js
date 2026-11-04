// Collapsible product tree. The chevron folds and unfolds a node, a click on the row selects it.
// data: {rows: [{id, parent, depth, name, kind, qty, mass, rev, plm_id, match, has_children}],
//        selected, expanded: [id], searching}

const scrollByKey = new Map();

const CHEVRON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" ' +
  'stroke-linecap="round" stroke-linejoin="round"/></svg>';

function cell(className, text) {
  const div = document.createElement("div");
  div.className = className;
  div.textContent = text;
  return div;
}

function header() {
  const head = document.createElement("div");
  head.className = "tree-head";
  head.append(
    cell("col-name", "Structure"),
    cell("col-num", "Qty"),
    cell("col-num", "Unit mass (kg)"),
    cell("col-rev", "Rev"),
  );
  return head;
}

function rowElement(row) {
  const el = document.createElement("div");
  el.className = `tree-row is-${row.kind}` + (row.match ? " is-match" : "");
  el.dataset.id = row.id;
  el.setAttribute("role", "treeitem");
  el.setAttribute("aria-level", String(row.depth + 1));
  el.tabIndex = -1;
  el.title = row.plm_id;

  const name = document.createElement("div");
  name.className = "col-name";
  name.style.setProperty("--depth", row.depth);
  if (row.has_children) {
    const caret = document.createElement("button");
    caret.type = "button";
    caret.className = "caret";
    caret.tabIndex = -1;
    caret.innerHTML = CHEVRON;
    name.append(caret);
  } else {
    name.append(cell("caret-spacer", ""));
  }
  name.append(cell("name", row.name));

  el.append(
    name,
    cell("col-num", String(row.qty)),
    cell("col-num", row.mass.toFixed(3)),
    cell("col-rev", row.rev),
  );
  return el;
}

function applyVisibility(root) {
  const { rows, expanded } = root._tree;
  const shown = new Set();
  for (const row of rows) {
    const visible = row.parent === null || (shown.has(row.parent) && expanded.has(row.parent));
    if (visible) shown.add(row.id);
    const el = root._elements.get(row.id);
    el.hidden = !visible;
    if (row.has_children) el.setAttribute("aria-expanded", String(expanded.has(row.id)));
  }
}

function visibleRows(root) {
  return [...root._body.querySelectorAll(".tree-row:not([hidden])")];
}

function toggle(root, id, open) {
  const { expanded, searching, setStateValue } = root._tree;
  const isOpen = expanded.has(id);
  if (open === isOpen) return;
  if (isOpen) expanded.delete(id);
  else expanded.add(id);
  applyVisibility(root);
  if (!searching) setStateValue("expanded", [...expanded]);
}

function select(root, id) {
  root._tree.selected = id;
  for (const el of root._body.querySelectorAll(".tree-row[aria-selected='true']")) {
    el.setAttribute("aria-selected", "false");
  }
  const el = root._elements.get(id);
  el.setAttribute("aria-selected", "true");
  root._tree.setTriggerValue("select", id);
}

function focusRow(root, el, preventScroll = false) {
  if (!el) return;
  for (const other of root._body.querySelectorAll(".tree-row[tabindex='0']")) other.tabIndex = -1;
  el.tabIndex = 0;
  el.focus({ preventScroll });
}

function bind(root) {
  root._body.addEventListener("click", (event) => {
    const el = event.target.closest(".tree-row");
    if (!el) return;
    const id = el.dataset.id;
    if (event.target.closest(".caret")) {
      toggle(root, id);
      return;
    }
    focusRow(root, el);
    select(root, id);
  });

  root._body.addEventListener("dblclick", (event) => {
    const el = event.target.closest(".tree-row");
    if (el && !event.target.closest(".caret") && root._tree.byId.get(el.dataset.id).has_children) {
      toggle(root, el.dataset.id);
    }
  });

  root._body.addEventListener("keydown", (event) => {
    const el = event.target.closest(".tree-row");
    if (!el) return;
    const row = root._tree.byId.get(el.dataset.id);
    const rows = visibleRows(root);
    const index = rows.indexOf(el);
    const keys = {
      ArrowDown: () => focusRow(root, rows[index + 1]),
      ArrowUp: () => focusRow(root, rows[index - 1]),
      ArrowRight: () => (row.has_children ? toggle(root, row.id, true) : null),
      ArrowLeft: () =>
        row.has_children && root._tree.expanded.has(row.id)
          ? toggle(root, row.id, false)
          : focusRow(root, root._elements.get(row.parent)),
      Enter: () => select(root, row.id),
      " ": () => select(root, row.id),
    };
    if (keys[event.key]) {
      event.preventDefault();
      keys[event.key]();
    }
  });

  root._body.addEventListener("scroll", () => scrollByKey.set(root._key, root._body.scrollTop));
}

export default function ({ data, key, parentElement, setStateValue, setTriggerValue }) {
  let root = parentElement.querySelector(".tree");
  if (!root) {
    root = document.createElement("div");
    root.className = "tree";
    root._body = document.createElement("div");
    root._body.className = "tree-body";
    root._body.setAttribute("role", "tree");
    root._body.setAttribute("aria-label", "Product structure");
    root.append(header(), root._body);
    parentElement.append(root);
    root._key = key;
    bind(root);
  }
  const scrollTop = scrollByKey.get(key) ?? root._body.scrollTop;
  const focused = root.getRootNode().activeElement?.closest?.(".tree-row")?.dataset.id;

  const rows = data.rows;
  root._tree = {
    rows,
    byId: new Map(rows.map((row) => [row.id, row])),
    expanded: new Set(data.expanded),
    selected: data.selected,
    searching: data.searching,
    setStateValue,
    setTriggerValue,
  };
  root._elements = new Map();
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const el = rowElement(row);
    el.setAttribute("aria-selected", String(row.id === data.selected));
    root._elements.set(row.id, el);
    fragment.append(el);
  }
  root._body.replaceChildren(fragment);
  root.classList.toggle("is-searching", Boolean(data.searching));
  applyVisibility(root);

  const selected = root._elements.get(data.selected);
  const first = selected && !selected.hidden ? selected : visibleRows(root)[0];
  if (first) first.tabIndex = 0;
  root._body.scrollTop = scrollTop;
  const refocus = root._elements.get(focused);
  if (refocus && !refocus.hidden) focusRow(root, refocus, true);
}
