import { el } from "../dom.js";
import { getJSON, postJSON, getToken, setToken } from "../api.js";

export async function renderCreate() {
  const app = document.getElementById("app");
  const [authMode, vehicleModels, worlds] = await Promise.all([
    getJSON("/api/auth"),
    getJSON("/api/vehicle_models"),
    getJSON("/api/worlds"),
  ]);

  app.innerHTML = "";
  app.appendChild(el("p", { text: "Builds a complete scenario YAML from scratch - every field a real run needs, not a partial edit of an existing file - and hands back the file plus the exact command to run it yourself. Nothing here starts or stops a simulation. Fields not shown below are filled with the same safe defaults every accepted run uses (camera/rangefinder proof on, full logging, evidence-backed analysis) and aren't meant to be tuned per scenario." }));

  // --- token ---
  if (authMode.write_token_required) {
    const tokenSection = el("section", {}, [
      el("h2", { text: "Write token" }),
      el("p", { class: "help", text: "Required for both forms below. Generate one on the host with scripts/dashboard/generate_token.py, then paste it here (stored only in this browser's localStorage, sent only to this dashboard)." }),
    ]);
    const tokenInput = el("input", { type: "text", value: getToken(), placeholder: "X-Databoss-Token" });
    tokenInput.addEventListener("change", () => setToken(tokenInput.value.trim()));
    tokenSection.appendChild(tokenInput);
    app.appendChild(tokenSection);
  }

  // --- create scenario ---
  const scenarioSection = el("section", {});
  scenarioSection.appendChild(el("h2", { text: "Create scenario" }));
  const form = el("form", { class: "gen" });

  function fieldset(legendText, helpText, rows) {
    const fs = el("fieldset", { class: "gen" }, [el("legend", { text: legendText })]);
    if (helpText) fs.appendChild(el("p", { class: "help", text: helpText }));
    for (const [label, node] of rows) {
      fs.appendChild(el("label", { text: label }));
      fs.appendChild(node);
    }
    return fs;
  }

  // Basic
  const newNameInput = el("input", { type: "text", placeholder: "e.g. my_test_hover_15m (no .yaml)" });
  const descriptionInput = el("input", { type: "text", placeholder: "what is this run for / what's different about it" });
  form.appendChild(fieldset("Basic", "Every generated scenario is a complete, standalone file - not tied to any template.", [
    ["Scenario name", newNameInput],
    ["Description", descriptionInput],
  ]));

  // Vehicle
  const modelSelect = el("select", {}, vehicleModels.map(m => el("option", { value: m, text: m })));
  const startX = el("input", { type: "number", step: "0.1", value: "0" });
  const startY = el("input", { type: "number", step: "0.1", value: "0" });
  form.appendChild(fieldset("Vehicle", "Which drone model to fly, and where it spawns. The PX4 airframe and Gazebo model name are derived automatically from the model you pick - you never set those directly.", [
    ["Vehicle model", modelSelect],
    ["Spawn x_m", startX], ["Spawn y_m", startY],
  ]));

  // World - flat and terrain worlds shown as separate groups; browser-only
  // visualization substitutes (colored_tiles) are excluded here since the
  // backend refuses to fly them - see the "Apply wind" section for how
  // those are detected.
  const flyableWorlds = worlds.filter(w => !w.is_browser_substitute);
  const flatWorlds = flyableWorlds.filter(w => w.kind === "flat");
  const terrainWorlds = flyableWorlds.filter(w => w.kind === "terrain");
  const substituteCount = worlds.length - flyableWorlds.length;
  const worldSelect = el("select", {}, [
    el("optgroup", { label: "Flat worlds" }, flatWorlds.map(w => el("option", {
      value: w.name, text: `${w.name}${w.wind_enabled ? " (wind)" : ""}`,
    }))),
    el("optgroup", { label: "Terrain worlds (real heightmap)" }, terrainWorlds.map(w => el("option", {
      value: w.name, text: w.name,
    }))),
  ]);
  const worldHelp = "Pick one of the already-generated worlds below. Need a different one? Use the \"Create world\" form further down first, then come back and pick it here." +
    (substituteCount ? ` (${substituteCount} browser-visualization-only world${substituteCount > 1 ? "s" : ""} hidden here - never flyable, see docs/architecture/mvp_backend_contract.md.)` : "");
  form.appendChild(fieldset("World", worldHelp, [
    ["World", worldSelect],
  ]));

  // Route / altitude
  const altInput = el("input", { type: "number", step: "0.1", value: "2.5" });
  form.appendChild(fieldset("Altitude", "The hover altitude above ground. The hold-position controller's target height is kept in sync with this automatically - you never set them separately.", [
    ["Altitude (m AGL)", altInput],
  ]));

  // GNSS
  const gnssStartSelect = el("select", {}, [
    el("option", { value: "10", text: "Available (10)" }),
    el("option", { value: "0", text: "Unavailable from the start (0)" }),
  ]);
  const lossEnabledSelect = el("select", {}, [
    el("option", { value: "false", text: "No - GNSS stays on the whole flight" }),
    el("option", { value: "true", text: "Yes - cut GNSS partway through" }),
  ]);
  const lossAfterInput = el("input", { type: "number", step: "0.1", placeholder: "seconds after takeoff, e.g. 20" });
  const postLossHoverInput = el("input", { type: "number", step: "0.1", placeholder: "e.g. 50" });
  form.appendChild(fieldset(
    "GNSS",
    "Whether GPS is available when the flight starts, and whether it gets cut partway through to test " +
    "GNSS-denied navigation. Note: \"cut after how many seconds\" only enables/disables the cut for this " +
    "runner's control mode - it isn't a precise timer (the actual cut fires once the vehicle is stable at " +
    "altitude). \"GNSS-denied hold duration\" is the real, load-bearing knob for how long the flight actually " +
    "stays in the GNSS-denied state - leave it blank only if you're fine with the runner's own leftover-time default.",
    [
      ["GNSS at start", gnssStartSelect],
      ["Cut GNSS mid-flight?", lossEnabledSelect],
      ["Cut after how many seconds", lossAfterInput],
      ["GNSS-denied hold duration (s)", postLossHoverInput],
    ],
  ));

  // Failsafe
  const failsafeSelect = el("select", {}, [
    el("option", { value: "default", text: "default - standard PX4 failsafe timing" }),
    el("option", { value: "delayed_observation", text: "delayed_observation - tolerant of long GNSS-denied stretches" }),
  ]);
  form.appendChild(fieldset("Failsafe", "How aggressively PX4 reacts to lost position estimates. \"default\" will RTL quickly during a GNSS-denied test; \"delayed_observation\" gives a GNSS-denied navigation method time to prove itself first.", [
    ["Failsafe profile", failsafeSelect],
  ]));

  // Optical flow
  const flowEnabledSelect = el("select", {}, [
    el("option", { value: "false", text: "No" }),
    el("option", { value: "true", text: "Yes" }),
  ]);
  const estimatorSelect = el("select", {}, [
    el("option", { value: "lk", text: "lk (Lucas-Kanade)" }),
    el("option", { value: "sift", text: "sift" }),
  ]);
  form.appendChild(fieldset("Optical flow", "Whether the downward-camera optical-flow bridge is active as a GNSS-denied navigation aid. The camera's field of view is auto-derived from the vehicle model you picked above, not user-set - it must match the real camera hardware exactly or the flow math is silently wrong. Detailed tuning (axis_map, rate, EKF2 gate parameters) uses known-good defaults and isn't exposed here - see the run detail page's Connections panel to review what a generated run actually used.", [
    ["Enable optical flow", flowEnabledSelect],
    ["Estimator", estimatorSelect],
  ]));

  // Position hold
  const gnssLossFloorInput = el("input", { type: "number", step: "0.5", value: "3" });
  const vxInput = el("input", { type: "number", step: "0.1", value: "0" });
  const vyInput = el("input", { type: "number", step: "0.1", value: "0" });
  const vzInput = el("input", { type: "number", step: "0.1", value: "0" });
  form.appendChild(fieldset("Hold-position motion", "Small velocity biases during the hover, and the minimum time after entering offboard mode before GNSS can be cut (a floor, not a fixed trigger - the real cut is gated on reaching a stable altitude).", [
    ["vx (m/s)", vxInput], ["vy (m/s)", vyInput], ["vz (m/s)", vzInput],
    ["Min. seconds before GNSS cut", gnssLossFloorInput],
  ]));

  // Visualization
  const gzwebEnabledSelect = el("select", {}, [
    el("option", { value: "true", text: "Yes (default - keeps QGC + the web viewer live)" }),
    el("option", { value: "false", text: "No" }),
  ]);
  form.appendChild(fieldset("Visualization", "Whether the browser-based Gazebo viewer bridge is enabled for this run. Leave on unless you have a specific reason not to - it's the standing operator policy.", [
    ["gz-web bridge enabled", gzwebEnabledSelect],
  ]));

  // Advanced
  const extraEditsInput = el("textarea", { placeholder: '{"aiding.mode": "synthetic_external_odometry"}  (raw JSON, dotted paths - anything not in the real editable whitelist is rejected and reported, never silently applied or ignored)' });
  form.appendChild(fieldset("Advanced (optional)", "Anything editable not covered by the fields above - merged in as extra edits. Rejected paths are reported back, not silently dropped.", [
    ["Additional edits (raw JSON)", extraEditsInput],
  ]));

  const submitBtn = el("button", { type: "submit", text: "Create scenario" });
  form.appendChild(submitBtn);
  const resultDiv = el("div", {});
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    resultDiv.innerHTML = "";
    const edits = {
      "vehicle.model": modelSelect.value,
      "vehicle.start_pose": { x_m: parseFloat(startX.value), y_m: parseFloat(startY.value), z_m: 0, yaw_deg: 0 },
      "route.altitude_agl_m": parseFloat(altInput.value),
      "gnss.loss_enabled": lossEnabledSelect.value === "true",
      "failsafe.profile": failsafeSelect.value,
      "flow_bridge.enabled": flowEnabledSelect.value === "true",
      "flow_bridge.estimator": estimatorSelect.value,
      "control.gnss_loss_after_offboard_s": parseFloat(gnssLossFloorInput.value),
      "control.vx_m_s": parseFloat(vxInput.value),
      "control.vy_m_s": parseFloat(vyInput.value),
      "control.vz_m_s": parseFloat(vzInput.value),
      "visualization.gazebo_web.enabled": gzwebEnabledSelect.value === "true",
    };
    if (descriptionInput.value.trim() !== "") edits["run.description"] = descriptionInput.value.trim();
    if (lossEnabledSelect.value === "true" && lossAfterInput.value !== "") {
      edits["gnss.loss_after_takeoff_s"] = parseFloat(lossAfterInput.value);
    }
    if (extraEditsInput.value.trim() !== "") {
      try {
        Object.assign(edits, JSON.parse(extraEditsInput.value));
      } catch (e) {
        resultDiv.appendChild(el("p", { class: "err", text: "Additional edits is not valid JSON: " + e.message }));
        return;
      }
    }
    const body = {
      new_name: newNameInput.value.trim(),
      edits,
      world_name: worldSelect.value,
      gnss_start_used: parseInt(gnssStartSelect.value, 10),
    };
    if (postLossHoverInput.value.trim() !== "") {
      body.post_loss_hover_s = parseFloat(postLossHoverInput.value);
    }

    try {
      const data = await postJSON("/api/scenarios", body);
      resultDiv.innerHTML = "";
      resultDiv.appendChild(el("p", { text: "Created: " + data.path }));
      if (data.rejected_edits.length) {
        resultDiv.appendChild(el("p", { class: "warn", text: "Rejected (not editable, left unchanged): " + data.rejected_edits.join(", ") }));
      }
      if (data.confound.is_confounded) {
        resultDiv.appendChild(el("p", { class: "warn", text: "Note: multiple field groups changed at once (" + data.confound.blocks_changed.join(", ") + ") - fine for a fresh scenario, but if you meant this as a one-variable comparison against another run, double check." }));
      }
      resultDiv.appendChild(el("p", { text: "Run this yourself:" }));
      resultDiv.appendChild(el("div", { class: "run-command", text: data.run_command }));
    } catch (e) {
      resultDiv.appendChild(el("p", { class: "err", text: (e.status || "") + " " + e.message }));
    }
  });

  scenarioSection.appendChild(form);
  scenarioSection.appendChild(resultDiv);
  app.appendChild(scenarioSection);

  // --- create world ---
  const worldSection = el("section", {});
  worldSection.appendChild(el("h2", { text: "Create world" }));
  const wform = el("form", { class: "gen" });
  const wNewName = el("input", { type: "text", placeholder: "new_world_name (no .yaml)" });
  const wSizeX = el("input", { type: "number", value: "120" });
  const wSizeY = el("input", { type: "number", value: "120" });
  const wPattern = el("select", {}, [
    el("option", { value: "uniform_field", text: "uniform_field" }),
    el("option", { value: "checker_field", text: "checker_field" }),
  ]);
  const wWind = el("select", {}, [
    el("option", { value: "false", text: "false" }),
    el("option", { value: "true", text: "true (steady wind only, no gusts)" }),
  ]);
  const wWindMean = el("input", { type: "number", step: "0.1", placeholder: "wind mean_mps (only used if wind=true)" });
  const wWindEast = el("input", { type: "number", step: "0.1", value: "1", placeholder: "direction: east component" });
  const wWindNorth = el("input", { type: "number", step: "0.1", value: "0", placeholder: "direction: north component" });
  const wPadEnabled = el("select", {}, [
    el("option", { value: "true", text: "true (default, at world origin 0,0)" }),
    el("option", { value: "false", text: "false" }),
  ]);
  for (const [label, node] of [
    ["New world name", wNewName], ["size_m.x", wSizeX], ["size_m.y", wSizeY],
    ["texture.visual_pattern", wPattern], ["wind.enabled", wWind], ["wind.mean_mps", wWindMean],
    ["wind direction (east)", wWindEast], ["wind direction (north)", wWindNorth],
    ["pad.enabled", wPadEnabled],
  ]) {
    wform.appendChild(el("label", { text: label }));
    wform.appendChild(node);
  }
  const wSubmitBtn = el("button", { type: "submit", text: "Create world" });
  wform.appendChild(wSubmitBtn);
  const wResultDiv = el("div", {});
  wform.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    wResultDiv.innerHTML = "";
    const body = {
      new_name: wNewName.value.trim(),
      size_m: { x: parseFloat(wSizeX.value), y: parseFloat(wSizeY.value) },
      texture: { visual_pattern: wPattern.value },
      lighting: {},
      wind: wWind.value === "true"
        ? {
            enabled: true,
            mean_mps: parseFloat(wWindMean.value || "0"),
            direction_vector_enu: [parseFloat(wWindEast.value || "1"), parseFloat(wWindNorth.value || "0")],
          }
        : { enabled: false },
      pad: { enabled: wPadEnabled.value === "true" },
    };
    try {
      const data = await postJSON("/api/worlds/generate", body);
      wResultDiv.innerHTML = "";
      wResultDiv.appendChild(el("p", { text: "Created: " + data.world_config_path }));
      wResultDiv.appendChild(el("p", { text: "Generated SDF: " + data.sdf_path }));
    } catch (e) {
      wResultDiv.appendChild(el("p", { class: "err", text: (e.status || "") + " " + e.message }));
    }
  });
  worldSection.appendChild(wform);
  worldSection.appendChild(wResultDiv);
  app.appendChild(worldSection);

  // --- apply wind to an existing world (flat OR terrain, separately from
  // how that world's ground/terrain was generated) ---
  const windSection = el("section", {});
  windSection.appendChild(el("h2", { text: "Apply wind to an existing world" }));
  windSection.appendChild(el("p", { class: "help", text: "Adds wind to any already-existing world - flat or real terrain - as a new, separately-named world. Never modifies the source. Wind on a flat world is proven (Phase 16); wind on a terrain world is mechanically the same fix but has not actually been flown yet - treat it as experimental until a real run confirms it." }));
  const awForm = el("form", { class: "gen" });
  const awSource = el("select", {}, [
    el("optgroup", { label: "Flat worlds" }, flatWorlds.map(w => el("option", { value: w.name, text: w.name }))),
    el("optgroup", { label: "Terrain worlds" }, terrainWorlds.map(w => el("option", { value: w.name, text: w.name }))),
  ]);
  const awNewName = el("input", { type: "text", placeholder: "new_world_name" });
  const awMean = el("input", { type: "number", step: "0.1", value: "5" });
  const awEast = el("input", { type: "number", step: "0.1", value: "1" });
  const awNorth = el("input", { type: "number", step: "0.1", value: "0" });
  for (const [label, node] of [
    ["Source world", awSource], ["New world name", awNewName],
    ["Wind mean speed (m/s)", awMean],
    ["Direction (east)", awEast], ["Direction (north)", awNorth],
  ]) {
    awForm.appendChild(el("label", { text: label }));
    awForm.appendChild(node);
  }
  const awSubmitBtn = el("button", { type: "submit", text: "Apply wind" });
  awForm.appendChild(awSubmitBtn);
  const awResultDiv = el("div", {});
  awForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    awResultDiv.innerHTML = "";
    try {
      const data = await postJSON("/api/worlds/apply_wind", {
        source_world: awSource.value,
        new_name: awNewName.value.trim(),
        mean_mps: parseFloat(awMean.value),
        direction_vector_enu: [parseFloat(awEast.value), parseFloat(awNorth.value)],
      });
      awResultDiv.innerHTML = "";
      awResultDiv.appendChild(el("p", { text: "Created: " + data.sdf_path }));
    } catch (e) {
      awResultDiv.appendChild(el("p", { class: "err", text: (e.status || "") + " " + e.message }));
    }
  });
  windSection.appendChild(awForm);
  windSection.appendChild(awResultDiv);
  app.appendChild(windSection);

  // --- import a real terrain world ---
  const terrainSection = el("section", {});
  terrainSection.appendChild(el("h2", { text: "Import terrain world" }));
  terrainSection.appendChild(el("p", { class: "help", text: "Real heightmap terrain (satellite imagery + elevation, not the flat generator above) comes from a separate tool, gazebo_terrain_generator, proxied in below. Draw an area, generate it there, then import it here - the DATABOSS import step (PX4 sensor plugins, render engine fix, launch pad) makes it actually flyable and puts it in the World picker above. You'll still need your own Mapbox key inside that tool - that part is unchanged, this dashboard doesn't hold or proxy Mapbox credentials." }));
  terrainSection.appendChild(el("p", {}, [
    el("a", { href: "/terrain-generator/", target: "_blank", text: "Open the terrain generator" }),
  ]));

  const importListDiv = el("div", {});
  terrainSection.appendChild(importListDiv);

  const tForm = el("form", { class: "gen" });
  const tPackageSelect = el("select", {});
  const tNewName = el("input", { type: "text", placeholder: "leave blank to keep the package's own name" });
  const tAddPad = el("select", {}, [
    el("option", { value: "true", text: "true (recommended - terrain can be steep enough to tip the vehicle over)" }),
    el("option", { value: "false", text: "false" }),
  ]);
  for (const [label, node] of [
    ["Raw package", tPackageSelect], ["New name (optional)", tNewName], ["Add launch pad", tAddPad],
  ]) {
    tForm.appendChild(el("label", { text: label }));
    tForm.appendChild(node);
  }
  const tSubmitBtn = el("button", { type: "submit", text: "Import" });
  tForm.appendChild(tSubmitBtn);
  const tResultDiv = el("div", {});

  async function loadTerrainImports() {
    const pkgs = await getJSON("/api/worlds/terrain_imports");
    importListDiv.innerHTML = "";
    if (!pkgs.length) {
      importListDiv.appendChild(el("p", { class: "help", text: "No raw packages found under generated_worlds/terrain/_generator_output/." }));
    } else {
      const t = el("table", {}, [el("tr", {}, [
        el("th", { text: "package" }), el("th", { text: "lat" }), el("th", { text: "lon" }), el("th", { text: "elev (m)" }), el("th", { text: "status" }),
      ])]);
      for (const p of pkgs) {
        t.appendChild(el("tr", {}, [
          el("td", { text: p.name }),
          el("td", { text: p.home.lat_deg != null ? p.home.lat_deg.toFixed(5) : "" }),
          el("td", { text: p.home.lon_deg != null ? p.home.lon_deg.toFixed(5) : "" }),
          el("td", { text: p.home.elevation_m != null ? p.home.elevation_m.toFixed(1) : "" }),
          el("td", { text: p.already_imported ? "already imported" : "available", class: p.already_imported ? "help" : "" }),
        ]));
      }
      importListDiv.appendChild(t);
    }
    tPackageSelect.innerHTML = "";
    for (const p of pkgs.filter(p => !p.already_imported)) {
      tPackageSelect.appendChild(el("option", { value: p.name, text: p.name }));
    }
  }
  await loadTerrainImports();

  tForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    tResultDiv.innerHTML = "";
    try {
      const data = await postJSON("/api/worlds/import_terrain", {
        package_name: tPackageSelect.value,
        new_name: tNewName.value.trim() || null,
        add_pad: tAddPad.value === "true",
      });
      tResultDiv.innerHTML = "";
      tResultDiv.appendChild(el("p", { text: "Imported: " + data.sdf_path }));
      tResultDiv.appendChild(el("p", { text: "Now selectable in the World picker above and windable via \"Apply wind to an existing world\"." }));
      await loadTerrainImports();
    } catch (e) {
      tResultDiv.appendChild(el("p", { class: "err", text: (e.status || "") + " " + e.message }));
    }
  });
  terrainSection.appendChild(tForm);
  terrainSection.appendChild(tResultDiv);
  app.appendChild(terrainSection);
}
