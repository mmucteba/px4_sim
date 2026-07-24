import { el, kv } from "../dom.js";
import { getJSON } from "../api.js";

export async function renderRun(runId) {
  const app = document.getElementById("app");
  let run;
  try {
    run = await getJSON(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("p", { text: "Run not found: " + runId }));
    return;
  }
  app.innerHTML = "";
  app.appendChild(el("p", {}, [el("a", { href: "/", text: "< back to run list" })]));
  app.appendChild(el("h2", { text: run.run_id }));

  const overview = el("section", {}, [
    el("h2", { text: "Overview" }),
    kv([
      ["phase", run.phase], ["scenario_name", run.scenario_name],
      ["algorithm", run.algorithm], ["gnss_state", run.gnss_state],
      ["world_variant", run.world_variant], ["tag_source", run.tag_source],
      ["contract_status", run.contract_status], ["accepted", run.accepted],
      ["created_utc", run.created_utc], ["last_modified_utc", run.last_modified_utc],
    ]),
  ]);
  app.appendChild(overview);

  const metrics = el("section", {}, [
    el("h2", { text: "Key metrics" }),
    kv(Object.entries(run.key_metrics)),
  ]);
  app.appendChild(metrics);

  const conn = run.connections;
  const connections = el("section", {}, [
    el("h2", { text: "Connections" }),
    kv([
      ["QGC enabled", conn.qgc_enabled], ["QGC ip", conn.qgc_ip],
      ["QGC local port", conn.qgc_local_port], ["QGC remote port", conn.qgc_remote_port],
      ["QGC source", conn.qgc_source],
      ["gz-web enabled", conn.gazebo_web_enabled], ["gz-web port", conn.gazebo_web_port],
      ["gz-web publication Hz", conn.gazebo_web_publication_hz],
      ["flow_bridge enabled", conn.flow_bridge_enabled], ["estimator", conn.flow_bridge_estimator],
      ["flow_bridge rate Hz", conn.flow_bridge_rate_hz], ["axis_map", conn.axis_map],
      ["hfov_rad", conn.hfov_rad],
      ["EKF2_OF_CTRL", conn.ekf2_of_ctrl], ["EKF2_OF_QMIN", conn.ekf2_of_qmin],
      ["EKF2_OF_N_MIN", conn.ekf2_of_n_min], ["EKF2_OF_DELAY", conn.ekf2_of_delay],
      ["aiding mode", conn.aiding_mode], ["EKF2_EV_CTRL", conn.ekf2_ev_ctrl],
    ]),
  ]);
  app.appendChild(connections);

  const artifacts = el("section", {}, [
    el("h2", { text: "Artifacts" }),
    el("ul", {}, Object.entries(run.artifacts).map(([k, v]) =>
      el("li", {}, [el("a", { href: `/artifacts/runs/${run.run_id}/${v}`, text: `${k}: ${v}` })])
    )),
  ]);
  app.appendChild(artifacts);

  if (run.comparisons.length) {
    app.appendChild(el("section", {}, [
      el("h2", { text: "Comparisons" }),
      el("ul", {}, run.comparisons.map(c =>
        el("li", {}, [el("a", { href: `/comparisons/${c}`, text: c })])
      )),
    ]));
  }

  if (run.warnings.length) {
    app.appendChild(el("section", {}, [
      el("h2", { text: "Warnings" }),
      el("ul", {}, run.warnings.map(w => el("li", { text: w }))),
    ]));
  }
}
