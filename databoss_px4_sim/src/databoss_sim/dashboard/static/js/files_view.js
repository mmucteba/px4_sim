import { el } from "./dom.js";

// Shared by the Files tab (run_detail.js, comparison_detail.js) and the
// Plots tab, which filters this same tree instead of making a second call.

export function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileRow(entry) {
  const container = el("div", {});
  const row = el("div", { class: "file-row" });
  row.appendChild(el("span", { class: "file-name", text: entry.name }));
  row.appendChild(el("span", { class: "file-size", text: humanSize(entry.size) }));

  if (entry.kind === "image") {
    const link = el("a", { href: entry.url, target: "_blank", rel: "noopener" });
    link.appendChild(el("img", {
      src: entry.url,
      alt: entry.name,
      loading: "lazy",
      style: "max-width:90px;max-height:60px;object-fit:cover;border-radius:4px;vertical-align:middle;",
    }));
    row.appendChild(link);
    container.appendChild(row);
    return container;
  }

  const previewable = entry.kind === "markdown" || entry.kind === "json" || entry.kind === "text";
  if (previewable && !entry.preview_disabled) {
    const pre = el("pre", { class: "file-preview", style: "display:none;" });
    const btn = el("button", { type: "button", class: "tab-btn", text: "view" });
    let loaded = false;
    btn.addEventListener("click", async () => {
      const visible = pre.style.display !== "none";
      if (visible) {
        pre.style.display = "none";
        btn.textContent = "view";
        return;
      }
      if (!loaded) {
        btn.textContent = "loading...";
        try {
          const text = await (await fetch(entry.url)).text();
          pre.textContent = entry.kind === "json" ? JSON.stringify(JSON.parse(text), null, 2) : text;
        } catch (e) {
          pre.textContent = "failed to load or parse: " + ((e && e.message) || e);
        }
        loaded = true;
      }
      pre.style.display = "block";
      btn.textContent = "hide";
    });
    row.appendChild(btn);
    container.appendChild(row);
    container.appendChild(pre);
    return container;
  }

  row.appendChild(el("a", { href: entry.url, download: entry.name, text: "download" }));
  container.appendChild(row);
  return container;
}

// A grid of lazy-loaded thumbnails linking to full-res - used for the
// Plots tab, and reused below for any Files-tab directory group that is
// (mostly) images, e.g. flow_recording/frames/ (up to ~240 JPEGs).
export function buildGallery(entries, emptyMessage) {
  const images = entries.filter(e => e.kind === "image");
  if (!images.length) {
    return el("p", { class: "help", text: emptyMessage || "No images found." });
  }
  const grid = el("div", { class: "gallery" });
  for (const img of images) {
    const link = el("a", { href: img.url, target: "_blank", rel: "noopener" });
    link.appendChild(el("img", { src: img.url, alt: img.name, loading: "lazy" }));
    grid.appendChild(el("figure", {}, [link, el("figcaption", { text: img.path })]));
  }
  return grid;
}

// Directory groups with more than this many files, all images, render as
// a gallery grid instead of one row per file (the flow_recording/frames/
// worst case: ~240 JPEGs would otherwise be 240 near-identical rows).
const GALLERY_THRESHOLD = 4;

// entries: the raw list[dict] from /api/runs|comparisons/{id}/files
export function buildFilesView(entries) {
  if (!entries.length) {
    return el("p", { class: "help", text: "No files recorded for this run." });
  }

  const groups = new Map();
  for (const e of entries) {
    const dir = e.dir || "";
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir).push(e);
  }

  const wrap = el("div", { class: "file-browser" });
  for (const dir of [...groups.keys()].sort()) {
    const files = groups.get(dir).sort((a, b) => a.name.localeCompare(b.name));
    const totalSize = files.reduce((s, f) => s + f.size, 0);
    const attrs = { class: "file-group" };
    if (dir === "") attrs.open = "";
    const details = el("details", attrs);
    const label = dir === "" ? "(top level)" : dir + "/";
    details.appendChild(el("summary", {
      text: `${label} (${files.length} file${files.length === 1 ? "" : "s"}, ${humanSize(totalSize)})`,
    }));
    const allImages = files.length > GALLERY_THRESHOLD && files.every(f => f.kind === "image");
    if (allImages) {
      details.appendChild(buildGallery(files));
    } else {
      for (const f of files) details.appendChild(fileRow(f));
    }
    wrap.appendChild(details);
  }
  return wrap;
}
