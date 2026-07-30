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
    if (v === null || v === undefined || v === "") continue;
    div.appendChild(el("div", { class: "k", text: k }));
    div.appendChild(el("div", { text: String(v) }));
  }
  return div;
}

// sections: {label: string, render: () => Node | Promise<Node>}[]
// Only the active tab's render() runs, and it runs lazily on first
// activation - required for tabs backed by a second API call (Files,
// Plots, Report) so opening a run page never fires every tab's fetch.
export function tabs(sections) {
  const wrap = el("div", { class: "tabs-wrap" });
  const bar = el("div", { class: "tabs" });
  const content = el("div", { class: "tab-content" });
  wrap.appendChild(bar);
  wrap.appendChild(content);

  const cache = new Map();
  const buttons = sections.map((section, i) => {
    const btn = el("button", { class: "tab-btn", type: "button", text: section.label });
    btn.addEventListener("click", () => activate(i));
    bar.appendChild(btn);
    return btn;
  });

  let renderToken = 0;

  async function activate(i) {
    buttons.forEach((b, j) => b.classList.toggle("active", j === i));
    const token = ++renderToken;
    if (cache.has(i)) {
      content.innerHTML = "";
      content.appendChild(cache.get(i));
      return;
    }
    content.innerHTML = "";
    content.appendChild(el("span", { class: "spinner" }));
    content.appendChild(document.createTextNode("loading..."));
    let result;
    try {
      result = await sections[i].render();
    } catch (e) {
      if (token !== renderToken) return;
      content.innerHTML = "";
      content.appendChild(el("div", { class: "error-box", text: String((e && e.message) || e) }));
      return;
    }
    if (token !== renderToken) return;
    content.innerHTML = "";
    if (result instanceof Node) {
      cache.set(i, result);
      content.appendChild(result);
    }
  }

  activate(0);
  return wrap;
}
