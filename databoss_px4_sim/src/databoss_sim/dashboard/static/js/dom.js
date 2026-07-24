export function el(tag, attrs, children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children || []) e.appendChild(c);
  return e;
}

export function kv(pairs) {
  const div = el("div", { class: "kv" });
  for (const [k, v] of pairs) {
    div.appendChild(el("div", { class: "k", text: k }));
    div.appendChild(el("div", { text: v === null || v === undefined ? "-" : String(v) }));
  }
  return div;
}
