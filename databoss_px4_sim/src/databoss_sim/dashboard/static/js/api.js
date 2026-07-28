export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + ": " + r.status);
  return r.json();
}

export function getToken() {
  return localStorage.getItem("databoss_token") || "";
}

export function setToken(v) {
  localStorage.setItem("databoss_token", v);
}

export async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Databoss-Token": getToken() },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `${url}: ${r.status}`;
    const err = new Error(message);
    err.status = r.status;
    err.detail = detail;
    throw err;
  }
  return data;
}
