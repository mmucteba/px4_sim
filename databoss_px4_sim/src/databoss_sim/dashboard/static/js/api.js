function errorMessage(url, status, data) {
  const detail = data.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (detail) return JSON.stringify(detail);
  return `${url}: ${status}`;
}

export async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(errorMessage(url, r.status, data));
    err.status = r.status;
    err.detail = data.detail;
    throw err;
  }
  return data;
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
    const err = new Error(errorMessage(url, r.status, data));
    err.status = r.status;
    err.detail = data.detail;
    throw err;
  }
  return data;
}
