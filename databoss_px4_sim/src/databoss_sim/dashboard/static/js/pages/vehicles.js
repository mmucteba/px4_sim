import { getJSON, postJSON } from "../api.js";
import { el, kv, tabs } from "../dom.js";

const X500_REASON = "Only x500 is allowed because the wind-response patch is on x500_base; other bases make wind scenarios invalid.";

function present(value) {
  return value !== null && value !== undefined && value !== "" && !(Array.isArray(value) && !value.length);
}

function badge(ok, text) {
  return el("span", { class: `badge ${ok ? "badge-ok" : "badge-caution"}`, text });
}

function stateBadge(vehicle) {
  if (vehicle.model_sync_status === "IN_SYNC") return el("span", { class: "badge badge-ok", text: "installed" });
  if (vehicle.model_sync_status === "NOT_INSTALLED") return el("span", { class: "badge badge-info", text: "needs install" });
  return el("span", { class: "badge badge-err", text: vehicle.model_sync_status || "attention" });
}

function parsePose(value, label) {
  const parts = value.trim().split(/[,\s]+/).filter(Boolean).map(Number);
  if (parts.length !== 6 || parts.some((v) => !Number.isFinite(v))) {
    throw new Error(`${label} must be six numbers: x y z roll pitch yaw`);
  }
  return parts;
}

function optionalText(value) {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function vehicleRow(vehicle, selectVehicle, selectedName) {
  const meta = [
    vehicle.sensors?.length ? vehicle.sensors.join(", ") : "",
    vehicle.airframe_filename || vehicle.repo_airframe_filename,
    vehicle.autostart_id ? `autostart ${vehicle.autostart_id}` : "",
    vehicle.camera_hfov_rad ? `hfov ${vehicle.camera_hfov_rad}` : "",
    vehicle.install_state,
    vehicle.verify_state,
  ].filter(present).map((value) => el("span", { text: String(value) }));
  const button = el("button", { class: "btn-ghost", type: "button", text: "Preflight" });
  button.addEventListener("click", () => selectVehicle(vehicle));
  return el("div", { class: selectedName === vehicle.name ? "list-row vehicle-row selected" : "list-row vehicle-row" }, [
    el("div", {}, [
      el("div", { class: "row-main", text: vehicle.name }),
      meta.length ? el("div", { class: "row-meta" }, meta) : el("div", {}),
    ]),
    el("div", { class: "cluster archive-row-tail" }, [
      badge(vehicle.is_vehicle || vehicle.needs_install, vehicle.is_vehicle ? "flyable" : vehicle.needs_install ? "generated" : "not flyable"),
      stateBadge(vehicle),
      button,
    ]),
  ]);
}

function field(labelText, helpText, node) {
  return el("div", { class: "field" }, [
    el("label", { text: labelText }),
    node,
    el("p", { class: "help", text: helpText }),
  ]);
}

function textInput(value = "", attrs = {}) {
  return el("input", { type: "text", value, ...attrs });
}

function numberInput(value, attrs = {}) {
  return el("input", { type: "number", value: String(value), step: attrs.step || "0.01", ...attrs });
}

function selectInput(value, options) {
  const node = el("select", {}, options.map((option) => el("option", { value: option, text: option })));
  node.value = value;
  return node;
}

function defaultSensor(kind) {
  if (kind === "include") {
    return { kind, model: "afbr_s50", mount: "0 0 -0.05 0 0 0", child_link: "", include_name: "" };
  }
  if (kind === "camera") {
    return { kind, link_name: "camera_link", sensor_name: "camera", hfov_rad: "1.74", width: "640", height: "480", rate_hz: "30", mount: "0.08 0 -0.04 0 1.5708 0" };
  }
  return {
    kind,
    link_name: "lidar_link",
    sensor_name: "down_lidar",
    mount: "0 0 -0.06 0 1.5708 0",
    sensor_pose: "0 0 0 0 0 0",
    h_samples: "8",
    v_samples: "2",
    h_min_angle_rad: "-0.1",
    h_max_angle_rad: "0.1",
    v_min_angle_rad: "-0.1",
    v_max_angle_rad: "0.1",
    range_min_m: "0.08",
    range_max_m: "40",
    range_resolution_m: "0.01",
    rate_hz: "30",
    visualize: "false",
  };
}

function sensorSpec(sensor) {
  if (sensor.kind === "include") {
    const out = {
      kind: "include",
      model: sensor.model.trim(),
      mount: parsePose(sensor.mount, "include mount"),
    };
    const child = optionalText(sensor.child_link);
    const includeName = optionalText(sensor.include_name);
    if (child) out.child_link = child;
    if (includeName) out.include_name = includeName;
    return out;
  }
  if (sensor.kind === "camera") {
    return {
      kind: "camera",
      link_name: sensor.link_name.trim(),
      sensor_name: sensor.sensor_name.trim(),
      hfov_rad: parseFloat(sensor.hfov_rad),
      width: parseInt(sensor.width, 10),
      height: parseInt(sensor.height, 10),
      rate_hz: parseFloat(sensor.rate_hz),
      mount: parsePose(sensor.mount, "camera mount"),
    };
  }
  return {
    kind: "gpu_lidar",
    link_name: sensor.link_name.trim(),
    sensor_name: sensor.sensor_name.trim(),
    mount: parsePose(sensor.mount, "lidar mount"),
    sensor_pose: parsePose(sensor.sensor_pose, "lidar sensor pose"),
    h_samples: parseInt(sensor.h_samples, 10),
    v_samples: parseInt(sensor.v_samples, 10),
    h_min_angle_rad: parseFloat(sensor.h_min_angle_rad),
    h_max_angle_rad: parseFloat(sensor.h_max_angle_rad),
    v_min_angle_rad: parseFloat(sensor.v_min_angle_rad),
    v_max_angle_rad: parseFloat(sensor.v_max_angle_rad),
    range_min_m: parseFloat(sensor.range_min_m),
    range_max_m: parseFloat(sensor.range_max_m),
    range_resolution_m: parseFloat(sensor.range_resolution_m),
    rate_hz: parseFloat(sensor.rate_hz),
    visualize: sensor.visualize === "true",
  };
}

function boundText(entry, key, attrs = {}) {
  const node = textInput(entry[key], attrs);
  node.addEventListener("input", () => {
    entry[key] = node.value;
  });
  return node;
}

function boundNumber(entry, key, attrs = {}) {
  const node = numberInput(entry[key], attrs);
  node.addEventListener("input", () => {
    entry[key] = node.value;
  });
  return node;
}

function boundSelect(entry, key, options) {
  const node = selectInput(entry[key], options);
  node.addEventListener("change", () => {
    entry[key] = node.value;
  });
  return node;
}

function renderSensorEditor(sensors, rerender) {
  const host = el("div", { class: "vehicle-sensor-list" });
  sensors.forEach((entry, index) => {
    const kind = selectInput(entry.kind, ["include", "camera", "gpu_lidar"]);
    kind.addEventListener("change", () => {
      sensors[index] = defaultSensor(kind.value);
      rerender();
    });
    const remove = el("button", { class: "btn-ghost", type: "button", text: "Remove" });
    remove.addEventListener("click", () => {
      sensors.splice(index, 1);
      rerender();
    });
    const rows = [field("Kind", "Choose whether this entry includes an existing model, adds a camera, or adds a GPU lidar.", kind)];
    if (entry.kind === "include") {
      rows.push(
        field("Model", "Existing model directory to merge into the vehicle.", boundText(entry, "model")),
        field("Mount", "Six-number pose relative to the vehicle: x y z roll pitch yaw.", boundText(entry, "mount")),
        field("Child link", "Optional link name when the included model has more than one top-level link.", boundText(entry, "child_link")),
        field("Include name", "Optional instance name for the included model.", boundText(entry, "include_name")),
      );
    } else if (entry.kind === "camera") {
      rows.push(
        field("Link name", "Name of the SDF link that carries the camera.", boundText(entry, "link_name")),
        field("Sensor name", "Name used by Gazebo topics and diagnostics.", boundText(entry, "sensor_name")),
        field("HFOV radians", "Horizontal field of view used by rendering and optical flow math.", boundNumber(entry, "hfov_rad")),
        field("Width", "Camera image width in pixels.", boundNumber(entry, "width", { step: "1" })),
        field("Height", "Camera image height in pixels.", boundNumber(entry, "height", { step: "1" })),
        field("Rate Hz", "Camera update rate in frames per second.", boundNumber(entry, "rate_hz")),
        field("Mount", "Six-number pose relative to the vehicle: x y z roll pitch yaw.", boundText(entry, "mount")),
      );
    } else {
      rows.push(
        field("Link name", "Name of the SDF link that carries the lidar.", boundText(entry, "link_name")),
        field("Sensor name", "Name used by Gazebo topics and diagnostics.", boundText(entry, "sensor_name")),
        field("Mount", "Six-number link pose relative to the vehicle.", boundText(entry, "mount")),
        field("Sensor pose", "Six-number pose inside the lidar link.", boundText(entry, "sensor_pose")),
        field("Horizontal samples", "Number of horizontal rays; 1x1 lidar is rejected.", boundNumber(entry, "h_samples", { step: "1" })),
        field("Vertical samples", "Number of vertical rays; 1x1 lidar is rejected.", boundNumber(entry, "v_samples", { step: "1" })),
        field("Horizontal min", "Minimum horizontal angle in radians.", boundNumber(entry, "h_min_angle_rad")),
        field("Horizontal max", "Maximum horizontal angle in radians.", boundNumber(entry, "h_max_angle_rad")),
        field("Vertical min", "Minimum vertical angle in radians.", boundNumber(entry, "v_min_angle_rad")),
        field("Vertical max", "Maximum vertical angle in radians.", boundNumber(entry, "v_max_angle_rad")),
        field("Range min", "Nearest returned range in meters.", boundNumber(entry, "range_min_m")),
        field("Range max", "Farthest returned range in meters.", boundNumber(entry, "range_max_m")),
        field("Range resolution", "Depth resolution in meters.", boundNumber(entry, "range_resolution_m")),
        field("Rate Hz", "Lidar update rate in scans per second.", boundNumber(entry, "rate_hz")),
        field("Visualize", "Whether Gazebo draws the lidar rays.", boundSelect(entry, "visualize", ["false", "true"])),
      );
    }
    host.appendChild(el("fieldset", { class: "gen vehicle-sensor-card" }, [
      el("legend", { text: `Sensor ${index + 1}` }),
      ...rows,
      remove,
    ]));
  });
  return host;
}

function renderGenerationPreflight(items, loading = false) {
  if (loading) {
    return el("p", { class: "help" }, [el("span", { class: "spinner" }), document.createTextNode("checking repo write access...")]);
  }
  if (!items) {
    return el("p", { class: "help", text: "Repo write preflight has not run yet." });
  }
  const failures = items.filter((item) => item.status === "FAIL");
  if (!failures.length) {
    return el("p", { class: "help", text: "Repo write preflight is clear." });
  }
  return el("ul", { class: "vehicle-blockers" }, failures.map((item) => el("li", { text: item.message })));
}

function renderComposeForm(reloadVehicles) {
  const name = textInput("", { placeholder: "x500_new_sensor_stack" });
  const description = textInput("", { placeholder: "what this vehicle adds" });
  const base = textInput("x500", { disabled: "disabled" });
  const baseAirframe = textInput("4001_gz_x500", { disabled: "disabled" });
  const bootParams = el("textarea", { placeholder: "{\"EKF2_RNG_CTRL\": 2}" });
  const sensors = [];
  const sensorHost = el("div", {});
  const result = el("div", {});
  const generationPreflightHost = el("div", {});
  let generationPreflight = null;

  function rerenderSensors() {
    sensorHost.replaceChildren(renderSensorEditor(sensors, rerenderSensors));
  }

  const addKind = selectInput("include", ["include", "camera", "gpu_lidar"]);
  const addButton = el("button", { class: "btn", type: "button", text: "Add sensor" });
  addButton.addEventListener("click", () => {
    sensors.push(defaultSensor(addKind.value));
    rerenderSensors();
  });

  function buildSpec(write) {
    let params = {};
    if (bootParams.value.trim()) params = JSON.parse(bootParams.value);
    return {
      name: name.value.trim(),
      base: "x500",
      base_airframe: "4001_gz_x500",
      description: description.value.trim() || name.value.trim(),
      sensors: sensors.map(sensorSpec),
      boot_params: params,
      write,
    };
  }

  function generationFailures() {
    return (generationPreflight || []).filter((item) => item.status === "FAIL");
  }

  function updateGenerateState() {
    generate.disabled = !generationPreflight || generationFailures().length > 0;
  }

  async function loadGenerationPreflight() {
    generationPreflight = null;
    generate.disabled = true;
    generationPreflightHost.replaceChildren(renderGenerationPreflight(generationPreflight, true));
    try {
      generationPreflight = await getJSON("/api/vehicles/generate/preflight");
      generationPreflightHost.replaceChildren(renderGenerationPreflight(generationPreflight));
      updateGenerateState();
    } catch (e) {
      generationPreflightHost.replaceChildren(el("div", { class: "error-box", text: "Failed to load repo write preflight: " + ((e && e.message) || e) }));
      generate.disabled = true;
    }
    return generationFailures();
  }

  async function submit(write) {
    result.replaceChildren();
    try {
      if (write) {
        const failures = await loadGenerationPreflight();
        if (failures.length) {
          result.replaceChildren(el("p", { class: "err", text: failures.map((item) => item.message).join(" ") }));
          return;
        }
      }
      const data = await postJSON("/api/vehicles/generate", buildSpec(write));
      const pairs = [
        ["airframe", data.airframe_filename],
        ["autostart", data.autostart_id],
        ["camera hfov", data.camera_hfov_rad],
        ["written", data.written ? "yes" : ""],
        ["paths", data.written_paths?.join(", ")],
        ["warnings", data.warnings?.join(", ")],
      ];
      result.replaceChildren(kv(pairs), el("pre", { text: data.model_sdf }));
      if (data.written) reloadVehicles();
    } catch (e) {
      result.replaceChildren(el("p", { class: "err", text: `${e.status || ""} ${e.message}`.trim() }));
    }
  }

  const preview = el("button", { class: "btn", type: "button", text: "Preview" });
  const generate = el("button", { class: "btn-primary", type: "button", text: "Generate" });
  generate.disabled = true;
  preview.addEventListener("click", () => submit(false));
  generate.addEventListener("click", () => submit(true));

  const form = el("form", { class: "gen vehicle-compose-form" }, [
    field("Name", "Lowercase model directory name using letters, numbers, and underscores.", name),
    field("Base", X500_REASON, base),
    field("Base airframe", "PX4's x500 airframe is inherited by generated vehicles.", baseAirframe),
    field("Description", "Short text written into model.config and the airframe comments.", description),
    field("Boot params", "Optional JSON object of PX4 param defaults written into the airframe.", bootParams),
    el("fieldset", { class: "gen" }, [
      el("legend", { text: "Sensors" }),
      el("p", { class: "help", text: "Add zero or more sensors. Empty optional fields are omitted from the request." }),
      el("div", { class: "cluster" }, [addKind, addButton]),
      sensorHost,
    ]),
    el("div", { class: "launch-actions" }, [preview, generate]),
    generationPreflightHost,
  ]);
  form.addEventListener("submit", (ev) => ev.preventDefault());
  rerenderSensors();
  loadGenerationPreflight();
  return el("div", { class: "vehicle-compose-layout" }, [form, result]);
}

function renderPreflightPanel(state) {
  const vehicle = state.selected;
  const install = el("button", { class: "btn-primary", type: "button", text: "Install" });
  const result = el("div", {});
  const failures = (state.preflight || []).filter((item) => item.status === "FAIL");
  install.disabled = !vehicle || !vehicle.has_repo_airframe || !state.preflight || failures.length > 0;
  install.addEventListener("click", async () => {
    install.disabled = true;
    result.replaceChildren();
    try {
      const data = await postJSON(`/api/vehicles/${encodeURIComponent(vehicle.name)}/install`, {});
      result.replaceChildren(el("p", {}, [
        document.createTextNode("Job queued: "),
        el("a", { href: data.job_url, text: data.job_id }),
      ]));
    } catch (e) {
      result.replaceChildren(el("p", { class: "err", text: `${e.status || ""} ${e.message}`.trim() }));
      install.disabled = false;
    }
  });
  if (!vehicle) return el("p", { class: "empty", text: "No vehicles were found." });
  const rows = state.preflight
    ? state.preflight.map((item) => el("div", { class: "check-row" }, [
        el("span", { class: `dot ${item.status === "OK" ? "dot-ok" : item.status === "FAIL" ? "dot-err" : "dot-warn"}` }),
        el("strong", { text: item.status }),
        el("span", { text: `${item.step}: ${item.message}` }),
      ]))
    : [el("p", { class: "help", text: "Select Preflight for a vehicle to load install blockers." })];
  const reasons = failures.length
    ? el("ul", { class: "vehicle-blockers" }, failures.map((item) => el("li", { text: item.message })))
    : el("p", { class: "help", text: state.preflight ? "No preflight failures reported." : "" });
  const installNote = vehicle.needs_install
    ? el("p", { class: "help", text: "Generated vehicle is ready for PX4 install; run Install after preflight is clear." })
    : el("p", { class: "help", text: "" });
  return el("div", { class: "stack" }, [
    el("h2", { text: `Preflight: ${vehicle.name}` }),
    installNote,
    ...rows,
    reasons,
    el("div", { class: "launch-actions" }, [install]),
    result,
  ]);
}

export async function renderVehicles() {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));
  const state = { vehicles: [], selected: null, preflight: null };
  const listHost = el("div", { class: "list" });
  const preflightHost = el("div", {});

  async function loadPreflight(vehicle) {
    state.selected = vehicle;
    state.preflight = null;
    renderList();
    preflightHost.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("checking..."));
    try {
      state.preflight = await getJSON(`/api/vehicles/${encodeURIComponent(vehicle.name)}/preflight`);
      preflightHost.replaceChildren(renderPreflightPanel(state));
    } catch (e) {
      preflightHost.replaceChildren(el("div", { class: "error-box", text: "Failed to load preflight: " + ((e && e.message) || e) }));
    }
    renderList();
  }

  function renderList() {
    listHost.replaceChildren();
    state.vehicles.forEach((vehicle) => {
      listHost.appendChild(vehicleRow(vehicle, loadPreflight, state.selected?.name));
    });
    if (!state.vehicles.length) {
      listHost.appendChild(el("p", { class: "empty", text: "No model directories were found." }));
    }
  }

  async function reloadVehicles() {
    state.vehicles = await getJSON("/api/vehicles");
    if (!state.selected) state.selected = state.vehicles.find((vehicle) => vehicle.is_vehicle) || state.vehicles[0] || null;
    renderList();
  }

  try {
    await reloadVehicles();
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load vehicles: " + ((e && e.message) || e) }));
    return;
  }

  app.replaceChildren(tabs([
    { label: "Vehicles", render: () => el("div", { class: "vehicle-grid" }, [listHost, preflightHost]) },
    { label: "Compose", render: () => renderComposeForm(reloadVehicles) },
  ]));
  if (state.selected) loadPreflight(state.selected);
}
