import { el, kv, tabs } from "../dom.js";
import { getJSON } from "../api.js";

export async function renderRun(runId) {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(el("span", { class: "spinner" }));
  app.appendChild(document.createTextNode("loading..."));

  let run;
  try {
    run = await getJSON(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("div", { class: "error-box", text: "Run not found: " + runId }));
    return;
  }

  app.innerHTML = "";
  app.appendChild(el("p", {}, [el("a", { href: "/", text: "< back to run list" })]));
  app.appendChild(el("h1", { text: run.run_id }));

  const sections = [
    {
      label: "Overview",
      render: () => kv([
        ["phase", run.phase], ["scenario_name", run.scenario_name],
        ["algorithm", run.algorithm], ["gnss_state", run.gnss_state],
        ["world_variant", run.world_variant], ["tag_source", run.tag_source],
        ["contract_status", run.contract_status], ["accepted", run.accepted],
        ["created_utc", run.created_utc], ["last_modified_utc", run.last_modified_utc],
      ]),
    },
    {
      label: "Metrics",
      render: () => kv(Object.entries(run.key_metrics)),
    },
    {
      label: "Connections",
      render: () => {
        const conn = run.connections;
        return kv([
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
        ]);
      },
    },
    {
      label: "Artifacts",
      render: () => el("ul", {}, Object.entries(run.artifacts).map(([k, v]) =>
        el("li", {}, [el("a", { href: `/artifacts/runs/${run.run_id}/${v}`, text: `${k}: ${v}` })])
      )),
    },
  ];

  if (run.comparisons.length) {
    sections.push({
      label: `Comparisons (${run.comparisons.length})`,
      render: () => el("ul", {}, run.comparisons.map(c =>
        el("li", {}, [el("a", { href: `/comparisons/${c}`, text: c })])
      )),
    });
  }

  if (run.warnings.length) {
    sections.push({
      label: `Warnings (${run.warnings.length})`,
      render: () => el("ul", {}, run.warnings.map(w => el("li", { text: w }))),
    });
  }

  app.appendChild(tabs(sections));
}
