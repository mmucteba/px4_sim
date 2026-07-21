# Phase 8G — Live Modular Optical-Flow Bridge

**CONTRACT SUPERSEDED 2026-07-17 / 2026-07-20** — this phase's accepted
`axis_map: "-yx"` was found by Phase 8L Gate 6b to be self-consistent in
open-loop testing but not PX4-EKF-consistent for fused route flight
(corrected to `"-x-y"` + `EKF2_OF_N_MIN=0.5`), and Phase 8N later found
even that correction was still sign-inverted — `axis_map: "xy"` is the
current sign-correct contract. See
`docs/phases/phase_08l_sensor_sanity_ladder.md` and
`docs/phases/phase_08n_flow_sign_inversion_probe.md`. The bridge plumbing
and fusion-reaches-PX4 proof below remain valid; the axis contract does not.

Status: **Accepted** (2026-07-13). 8G.3 primary closed-loop fusion passed in
run `20260713_151744`; 8G.4 documentation / handoff complete.

Status: In progress (planned 2026-07-11)

## Goal

Run the 8F-validated modular flow estimator LIVE: subscribe the Gazebo
downward camera + TF03 lidar, compute flow in real time, send MAVLink
`OPTICAL_FLOW_RAD` to PX4 so EKF2 fuses it — GNSS still ON (safety net).
GNSS-denied comparison is 8I.

## Why this phase exists

8F proved the algorithm offline. 8G proves the live path:
`gz camera → SiftFlowEstimator → OPTICAL_FLOW_RAD → mavlink_receiver →
sensor_optical_flow → EKF2 flow fusion → ULog evidence`.
Operator requirement: maximum modularity — inputs are frames, outputs are
EKF-ready flow; new algorithms enter via the `make_estimator(name)` registry
+ `--replay` regression + open-loop scenario, never by editing the bridge.

## Sub-phases

```text
8G.0  Rehearsal: stock x500_flow proves the EKF2 flow-fusion path (reference ULog)
8G.1  Bridge script + PX4 adapter, offline-replay tested (no sim, no PX4)
8G.2  Live OPEN-LOOP on x500_cam_lidar_down (EKF2_OF_CTRL=0): runner integration,
      sign validation vs truth, latency/EKF2_OF_DELAY calibration, quality mapping
8G.3  Live CLOSED-LOOP fusion (EKF2_OF_CTRL=1): 3 worlds, ULog flow analyzer,
      acceptance flags, flow-vs-truth via 8F machinery
8G.4  Documentation + phase acceptance (accepted 2026-07-13)
```

Open-loop/closed-loop split is deliberate: sign or delay errors are diagnosed
from `sensor_optical_flow` in the ULog before EKF2 acts on them (GNSS on would
mask, not neutralize, a bad closed loop).

## Design decisions

### D1 — Interpreter split → dedicated bridge venv, single process

gz.transport13 lives only in system python3; pymavlink only in the main venv.
Resolution: `venv_bridge` created from `/usr/bin/python3` with
`--system-site-packages` (inherits gz.transport13/gz.msgs10/cv2 4.13) +
`pip install pymavlink`. No system modification; recorded in environment.txt.
Loop stays in-process: `gz callback → estimator.update() → optical_flow_rad_send`.
Fallbacks (documented contingency only): `pip install --user pymavlink`;
two-process JSON-over-UDP split. cv2 4.13-vs-5.0 drift is caught by the 8G.1
replay regression.

### D2 — Timestamping → arrival-stamped; calibrate EKF2_OF_DELAY

Verified in PX4 source: `mavlink_receiver.cpp` `handle_message_optical_flow_rad`
IGNORES the message `time_usec` and stamps `hrt_absolute_time()` on arrival.
So total pipeline latency (frame sim-time → SIFT compute → UDP arrival + half
the integration interval) must be absorbed by `EKF2_OF_DELAY`. 8G.2 measures
it: match sent-CSV frame `t_sim` to ULog `sensor_optical_flow.timestamp_sample`
by order (lockstep hrt ≈ sim time), set
`EKF2_OF_DELAY ≈ median(arrival − frame t_sim) + integration_dt/2`.
`EKF2_OF_DELAY` is reboot-required → set via airframe default, not pxh mid-run.
Still send `time_usec` = frame sim time for forensics.

### D3 — Gyro compensation → send NaN gyros, EKF2 uses vehicle IMU

SIFT v1 has no gyro compensation. Verified: non-finite
`integrated_xgyro/ygyro/zgyro` → `delta_angle_available=false` → EKF2 falls
back to internal IMU delta angles (same as PX4's own GZBridge stock flow path).
Leave `EKF2_OF_GYR_SRC=0` (Auto).

### D4 — Distance field → send distance = −1.0 (unknown)

Negative distance → `distance_available=false`; EKF2 takes HAGL from the
proven TF03 `distance_sensor` stream (8D/8E, SIM_GZ_EN_LIDAR path). Do not
double-feed range. Set `SENS_FLOW_MINHGT 0.1`, `SENS_FLOW_MAXHGT 100`
(mavlink flow path leaves sensor limits NaN so the params substitute);
review `SENS_FLOW_MAXR`.

### D5 — Frame/sign mapping → parameterized axis map + mandatory open-loop
### translation-leg validation

Camera: image x right / y down, nadir mount. The FlowSample→OPTICAL_FLOW_RAD
mapping lives in a pure adapter (`px4_adapter.py`) parameterized by a named
`--axis-map` so a wrong guess is a config change, not a code change. Keep
`SENS_FLOW_ROT=0` and own the rotation in the adapter (mirrors stock sim).
Empirical gate (8G.2, open loop, GNSS on): two slow translation legs
(+0.5 m/s body X, then +0.5 m/s body Y) with truth-known velocity signs;
check `sensor_optical_flow.pixel_flow[0/1]` sign and magnitude
(≈ v·dt/dist) per leg. Only after both axes pass does 8G.3 enable fusion.

### D6 — Quality mapping → bridge-level linear rescale + calibrated EKF2_OF_QMIN

Raw SIFT quality sits at 26–59 (8F distributions), but EKF2 interpolates noise
from `EKF2_OF_N_MAX` (q=0) to `EKF2_OF_N_MIN` (q=255) — raw values would always
read near max noise. Estimator quality stays untouched (it is the algorithm's
metric); the bridge applies `--quality-in-min/--quality-in-max → 0..255`
(clamped), calibrated offline from the three 8F recordings so good
high-texture hover maps near ~200 and the low-texture bad tail falls below
`EKF2_OF_QMIN`. Mapping recorded per scenario (`flow_bridge.quality_map`).

### D7 — 8G.0 rehearsal GNSS gate → run rehearsal GNSS-on

Airframe 4021 (x500_flow) disables GPS, which breaks the runner's global
position gate. Rehearsal runs GNSS-on by re-enabling at pxh after boot
(`param set SYS_HAS_GPS 1`, `SIM_GPS_USED 10`, `EKF2_GPS_CTRL 7`) — matches
8G's GNSS-on design and still exercises flow fusion alongside GNSS.
Manual documented procedure first; wrapped in a scenario only if repeated.

## Implementation map

Create:
- `src/databoss_sim/flow/px4_adapter.py` — pure FlowSample→OPTICAL_FLOW_RAD
  field mapping (axis map, quality rescale, NaN gyros, distance −1). No gz or
  pymavlink imports → unit-testable in the main venv.
- `scripts/sim/flow_mavlink_bridge.py` — live bridge (venv_bridge python):
  record_camera_frames.py subscription skeleton + registry estimator +
  adapter + pymavlink `udpout:127.0.0.1:14600` (source_system=42,
  source_component=197, MAVLINK20, 1 Hz heartbeat). Sent-CSV log. Modes:
  live, `--replay <flow_recording_dir>` (regression harness for new
  estimators), `--dry-run` (compute, don't send).
- `venv_bridge/` — one-time env (see D1).
- `scripts/analysis/analyze_flow_bridge_openloop.py` (8G.2) — sent-CSV↔ULog
  matching: latency distribution, per-leg sign agreement vs truth, delivery %.

Modify:
- `scripts/runner/auto_takeoff_land_pxh_truth.py` — new `flow_bridge:` section
  (mirror `flow_recording`/`aiding` patterns): cfg parse, onboard mavlink
  gating (`onboard_mavlink_needed |= flow_bridge_enabled`), pxh param block
  (EKF2_OF_CTRL/QMIN, SENS_FLOW_*), Popen + finally stop, acceptance flags
  (`flow_bridge_sent_rows >= min_sent_samples`), and (8G.3) a new
  `analyze_ulog_optical_flow` analyzer: `sensor_optical_flow` rows/rate,
  `estimator_status_flags.cs_opt_flow`, `estimator_aid_src_optical_flow`
  fused/rejected, `xy_reset_counter` delta.
- `src/databoss_sim/airframes/4022_gz_x500_cam_lidar_down` — calibrated
  `EKF2_OF_DELAY` default (reboot-required param), after 8G.2 measures it.

Scenarios:
- `phase8g_flow_openloop_flat_rural_high_texture_noon.yaml` (8G.2:
  `flow_bridge.ekf2_of_ctrl: 0`, flow_recording kept on in parallel,
  two translation legs)
- `phase8g_flow_fused_{flat_rural_high_texture_noon,flat_rural_low_texture_noon,serefli_koschisar_terrain}.yaml`
  (8G.3: `ekf2_of_ctrl: 1`, hover 60 s + slow translation leg)

## Acceptance criteria

- 8G.0: reference ULog saved; `cs_opt_flow` active in air; fused > 100 and
  rejected/fused < 10 %; reference signature (rate, quality range, effective
  EKF2_OF_* params) recorded here.
- 8G.1: replay over the 8F high-texture recording reproduces the 8F offline
  samples within tolerance (per-sample integrated flow within a few percent,
  same quality ordering); adapter unit checks for every axis-map value;
  `--dry-run` starts/stops cleanly.
- 8G.2: ULog `sensor_optical_flow` rows ≈ sent rows (> 90 % delivery) at
  ~10 Hz; both translation legs show correct sign and magnitude within ~30 %
  of truth-derived expectation; latency measured and `EKF2_OF_DELAY` chosen
  and written to the airframe; OF_CTRL=0 confirmed in ULog.
- 8G.3 (per world): `cs_opt_flow` active while airborne;
  rejected/fused < 20 %; `xy_reset_counter` delta 0; landing clean;
  flow-vs-truth speed error mean ≤ ~0.05 m/s at hover (8F machinery on the
  parallel recording); EKF-vs-truth not degraded vs 8F baselines. Low texture
  may be "accepted with limitations" if quality gating drops coverage
  (document coverage %).

## Risks

1. cv2 4.13 vs 5.0 SIFT drift — caught by 8G.1 replay regression.
2. 10 Hz flow slower than typical flow sensors — mitigate via calibrated
   OF_DELAY + EKF2_OF_N_*; measure SIFT compute time in replay; fallback
   smaller max_width.
3. OpticalFlowSystem gz plugin missing from installed stack — 8G.0 blocked;
   fallback (operator sign-off) is source-reading + 8G.2 open-loop evidence.
4. Terrain/HAGL gating silently disabling fusion — explicit analyzer check.
5. PEP 668 blocking pymavlink install — D1 fallbacks.
6. Sign error masked by GNSS — prevented by mandatory open-loop gate (D5).
7. Wall-clock vs sim-time skew at RTF≠1 — latency measured in the ULog time
   base, so calibrated OF_DELAY is correct by construction; re-check if RTF
   changes between worlds.

## Results

### 8G.1 — bridge + adapter (2026-07-11): DONE, all acceptance checks pass

- `venv_bridge` created (`--system-site-packages` + pymavlink 2.4.49);
  verified one interpreter with pymavlink + cv2 4.13 + gz.transport13,
  OPTICAL_FLOW_RAD (msgid 106) available.
- `px4_adapter.py` self-test passes: all 8 axis maps, NaN gyros, distance −1,
  quality rescale (0 stays 0, clamping both ends).
- Sign-convention research: PX4's own OpticalFlowSystem feeds
  `calcFlow`'s scene-displacement pixel flow (`current − previous`,
  `atan2(px, focal)`) into `integrated_x/y` with NO swap or sign flip
  (`flow_opencv.cpp:126,199,206`; `OpticalFlowSensor.cpp:154`), from a camera
  mounted identically to ours (`0 1.5707 0`, zero yaw). Our estimator uses the
  same scene-displacement convention → default `axis_map: "xy"` (identity),
  still gated by the 8G.2 translation legs.
- Replay regression (8F high-texture recording, 640 px): 404/404 samples
  matched vs the 8F offline baseline; flow differs by ≤ 5e-8 rad (float32
  rounding), quality identical 404/404. cv2 4.13-vs-5.0 risk RETIRED.
- Real-send replay: 404/404 OPTICAL_FLOW_RAD messages sent without error
  (dummy UDP port); quality rescale live (raw [20,153] → sent [1,255]).
- Live-mode smoke without a sim: subscribes, runs, exits cleanly on SIGTERM.
- Compute-time measurement (replay, this VM): 640 px SIFT = 84.9 ms mean /
  111.5 ms p95 — MARGINAL at 10 Hz. 480 px = 46.3 ms / 60.6 ms p95 with
  validity nearly unchanged (83.7 % vs 84.4 %, quality 39.4 vs 48.0).
  → 8G.2 live scenarios should use `max_width: 480` (or rate 8 Hz at 640).

### 8G.2 prep — quality calibration from the 8F recordings (2026-07-13)

Raw SIFT quality distributions (valid samples): high texture p5=33 / med=53 /
p95=102; low texture p5=20 / med=30 / p95=66; terrain p5=50 / med=76 /
p95=104. The four low-texture bad-match spikes (speed > 0.3 m/s at hover)
have quality {20, 25, 25, 40} — INSIDE the good low-texture distribution
(good p5=20, p25=25), so quality alone cannot cleanly gate bad matches on low
texture: raw ≥30 keeps only 56 % of low-texture samples and still passes one
bad sample, while high texture / terrain lose nothing at raw ≥25.

Decision:
- Bridge rescale window `quality_in_min=20, quality_in_max=100` → 0..255
  (spans low-texture p5 to high/terrain ~p95; good high-texture hover maps
  to ~105–260 range, terrain ~95+).
- `EKF2_OF_QMIN = 17` (≈ raw 25): a floor that costs ~20 % of low-texture
  samples and nothing elsewhere. Explicit limitation: the remaining bad
  matches must be caught by EKF2's innovation gate (`EKF2_OF_GATE`) — that
  separation is exactly what the low-texture world exists to measure, and
  low-texture degradation remains an accepted RESULT, not a defect.

### 8G.0 — stock x500_flow rehearsal (2026-07-13): ACCEPTED

Reference run: `experiments/runs/20260713_062909_phase8g0_x500_flow_rehearsal`
(driver: `scripts/runner/phase8g0_x500_flow_rehearsal.py`). Three attempts:

1. ogre2 segfault — in PX4-managed mode `gz_env.sh` unconditionally resets
   `GZ_SIM_SERVER_CONFIG_PATH`, so a config override cannot set the render
   engine; the supported knob is `PX4_GZ_SIM_RENDER_ENGINE=ogre` (CLI flag
   wins). Also fixed a driver bug (missing `preexec_fn=os.setsid`).
2. RC-loss failsafe fired at arming and escalated to AUTO_LAND mid-hover at
   t=33.9 s (run 20260713_062101, rejected) — the rehearsal driver lacked the
   runner profiles' automation root `COM_RC_IN_MODE 4`. Also learned:
   RTF ≈ 0.25 on this VM (50 Hz flow camera, software rendering), so
   wall-clock hover times must be ~4× the desired sim duration.
3. ACCEPTED: 36.7 sim-s airborne, clean commanded landing.

Reference signature (analysis JSON in the run folder):
- `estimator_aid_src_optical_flow`: 2097 rows (~45 Hz — full EKF input rate);
  `sensor_optical_flow` itself logs at ~1 Hz under the default profile
  (42 rows) — analyzers must use the aid-source topic for stream evidence.
- `cs_opt_flow` active 85 % of flag samples (airborne).
- Airborne fused 874 / rejected 92 (10.5 % incl. transients); steady hover
  (excluding 4 s entry + 2 s landing) fused 782 / rejected 54 = **6.9 %**,
  rejections again clustered at the hover-entry braking transient (~10.4–11 s).
- xy resets: 2, both pre-takeoff on the ground (GPS runtime enable). None in
  flight.
- D7 confirmed live: `SIM_GPS_USED 10` at pxh revives GPS
  (`satellites_used=10`) over the GNSS-off airframe 4021.
- Stock flow quality on the default-world gray floor is mostly 0
  (88 % of logged samples) — textureless ground, as expected; EKF2 fuses the
  valid subset. Our textured worlds are the favorable case.

What this proves: the full EKF2 optical-flow fusion path
(gz flow sensor → SIM_GZ_EN_FLOW → sensor_optical_flow → EKF2 cs_opt_flow,
fused >> rejected in steady state) works in this build, with a saved
reference ULog to compare our bridge against. What it does not prove:
anything about our own bridge (8G.2/8G.3) or flow navigation quality.

### 8G.2 open-loop — hover flight (2026-07-13): first live bridge flight PASSED

Run: `experiments/runs/20260713_064248_phase8g_flow_openloop_hover_flat_rural_high_texture_noon_pxh_takeoff_land_truth`
(scenario `phase8g_flow_openloop_hover_flat_rural_high_texture_noon.yaml`,
`--hover-s 60`, EKF2_OF_CTRL=0, GNSS on). End-to-end accepted, and the bridge
flew live for the first time: 331 OPTICAL_FLOW_RAD samples sent in-flight
(266 with quality > 0) while the standard truth/alignment pipeline ran
unchanged (EKF-vs-truth 0.0506 m mean — GNSS-on class, flow not fused, as
designed).

Analyzer `scripts/analysis/analyze_flow_bridge_openloop.py`
(JSON in the run folder, `openloop_ok=True`):
- Delivery spot check: every informative ULog `sensor_optical_flow` row
  value-matches a sent sample (37 matched; this run predates the
  SDLOG_PROFILE=2179 change, so the topic is 1 Hz-capped — full delivery
  fraction gating starts with the leg runs).
- **Latency (frame sim time → PX4 arrival stamp): median 38 ms, p90 46 ms**
  → `EKF2_OF_DELAY` candidate = median + integration_dt/2 ≈ **104 ms**
  (to be confirmed on the full-rate leg runs before baking into airframe 4022).
- Analyzer lesson: quality-0 samples carry integrated (0,0), which
  value-matches any other zero row and fabricates multi-second latency tails
  — matching now excludes them (fix in the analyzer, commented).
- ~~RTF observation: x500_cam_lidar_down runs near RTF ≈ 0.9~~ **CORRECTED
  2026-07-13 (legx analysis): actual RTF ≈ 0.09–0.10.** The bridge CSV
  wall-vs-sim stamps show 0.087 for this hover run and 0.099 for the legx
  run. The earlier 0.9 was a mis-measure. Practical rule: runner durations
  (`--hover-s`, `--land-timeout-s`) are WALL clock — multiply desired sim
  seconds by ~10 for this vehicle.

### 8G.2 sign-gate legx (2026-07-13): flight OK, gate FAILED — with three major findings

Runs: `20260713_090116_..._legx_...` (rejected: `--hover-s 35` wall-clock
bought only ~2.8 sim-s of leg — see RTF correction above) and
`20260713_091302_..._legx_...` (`--hover-s 310 --land-timeout-s 300`,
flight physically correct: 27.7 sim-s airborne, 2.503 m alt, ~10.3 m
traverse at 0.47–0.5 m/s, 426 bridge samples sent, delivery 127/127 = 1.0
at full-rate logging, latency median 34 ms / p90 42 ms → OF_DELAY candidate
100 ms, confirming the hover-run 104 ms).

Analyzer `--expect vx`: `sign_ok=True` but `axis_dominance_ok=False`,
`magnitude_ratio=0.155` → **openloop_ok=False**. Investigation produced
three findings that supersede parts of earlier records:

1. **Truth aligner ENU→NED axis bug (fixed).** `align_latest_truth_run.py`
   compared PX4 NED x/y directly against Gazebo ENU x/y. Every previously
   accepted comparison was hover (zero displacement) so the bug was
   invisible; the first translating flight showed a fake 5.4 m mean error
   that dropped to 0.144 m mean / 0.332 m max after mapping NED_x=ENU_y,
   NED_y=ENU_x. Historical hover numbers and the magnitude-dominated 8A
   anchors are unaffected.
2. **The camera worlds are essentially featureless.** Saved frames (every
   frame now recorded) show `flat_rural_high_texture_noon` renders as
   5×5 m solid-color tiles — the world SDF contains no image textures at
   all ("high texture" is a label, not a rendered texture). The deprecated
   serefli `_web_mesh` experiment likewise used flat gray box visuals in its
   active world; do not use that artifact as the terrain web process. Native
   terrain browser monitoring now uses the original generated terrain world
   plus the terrain proxy flags in `../gazebo_web_visualization.md`. The 8F
   "feature quality" numbers came from tile edges, not ground texture.
3. **The drone's own shadow poisons SIFT on featureless ground.** At noon
   the shadow sits dead-center in the downward camera and moves WITH the
   vehicle; SIFT's median displacement then reports ~zero flow at healthy
   quality (quality 153 → rate 0.03 rad/s where truth expects 0.2).
   Phase-correlation on the raw frames confirms the images themselves show
   ~0.1 px shift where ~7 px is expected — the estimator faithfully
   reported what the camera saw.

Conclusion: the bridge plumbing (delivery, latency, sign convention) is
validated; the sign/magnitude gate CANNOT pass on the current worlds. The
leg gates are blocked on a world whose ground actually renders visual
texture.

### Approved unblock plan (operator decisions 2026-07-13)

Decisions: flat photo-texture world FIRST for the leg gates (serefli terrain
texture repair deferred to pre-8I); **shadows OFF** in the flow-validation
world (re-enable with an angled sun for 8I realism runs, limitation
documented). Note `aerial.png` is 207×207 px over 192 m (~0.9 m/px) — even
rendered, it is featureless at flow scale (~6 texture px per camera
footprint at 2.5 m AGL); a procedural ~1 cm/px texture is required.

- Step 0 — prove image-texture rendering under the pinned `ogre` classic
  engine: `scripts/worlds/make_ground_texture.py` (procedural, seeded,
  ~2048 px over 20 m), minimal standalone render test (server-only gz sim +
  frame grab). Fallback if ogre classic won't texture: ogre2 with
  `--headless-rendering` (EGL, a different path from the xvfb-GLX segfault).
- Step 1 — extend `build_gazebo_world.py` (`texture.image` ground-plane
  mode + `scene.shadows` knob), generate `flat_rural_phototex_noon`,
  validate camera proof frame BEFORE any PX4 flight, clone the three
  open-loop scenarios to the new world.
- Step 2 — re-fly legx (`--expect vx`) and legy (`--expect vy`) with
  `--hover-s 310 --land-timeout-s 300` (wall-clock ×10 rule); close D5;
  confirm EKF2_OF_DELAY ≈ 100 ms on full-rate data.
- Step 3 — 8G.3 closed-loop (`ekf2_of_ctrl: 1`): phototex flat primary,
  old tile world as documented low-feature stress case; bake OF_DELAY into
  airframe 4022; 8G.4 docs and acceptance.

### 8G.2 phototex re-fly (2026-07-13): D5 CLOSED — both leg gates GREEN

Steps 0 and 1 executed (see phases README / PROJECT_LOG): PBR `albedo_map`
renders under `ogre` classic (400/400 SIFT kp), world
`flat_rural_phototex_noon` built (36 textured tiles, shadows off) and
camera-proofed at 2.5 m AGL (400 SIFT kp) before any flight.

**Result — both orthogonal open-loop gates pass under `axis_map: -yx`:**

| Leg | Run | Body motion | Dominant flow axis | Sign | magnitude_ratio | Gate |
|-----|-----|-------------|--------------------|------|-----------------|------|
| legx | `20260713_130549` | +X (North) | `integrated_y` +0.205 | + | 1.073 | pass |
| legy | `20260713_131822` | +Y (East)  | `integrated_x` −0.222 | − | 1.163 | pass |

Both match the PX4 convention (EKF2.cpp negates pixel_flow, then
optical_flow_fusion.cpp predicts flow = (+vel_body_y, −vel_body_x)/hagl).
Delivery 1.0, latency median 43 ms, OF_DELAY candidate 109 ms on both legs
(consistent with the hover-run 104 ms). Truth alignment healthy (legx 0.18 m
mean / 0.43 m max; legy 0.22 m / 0.47 m). **D5 (camera→body axis map) is
empirically gated on two orthogonal translations: `-yx`.**

**The world was never the whole story — the analyzer was also mis-measuring
magnitude.** First phototex legx run (`20260713_122205`, identity axis_map
`xy` on the wire) gave clean RAW camera flow: +X body at 0.5 m/s →
`+0.209 rad/s` on the camera x-axis, cross ≈ 0, vs `v/AGL = 0.5/2.5 = 0.197`
(1.06× — the estimator reads the textured ground correctly). The old analyzer
had reported garbage magnitude because it selected "moving" samples by
`|flow| > median`, which swept in takeoff/acceleration transients. Fixes in
`scripts/analysis/analyze_flow_bridge_openloop.py`:
- Leg window is now the longest contiguous above-threshold segment of the RAW
  Gazebo truth CSV (shares the gz sim-time base with the sent flow), trimmed
  `--leg-trim-s` (default 2.5 s) at each end → averages only the steady
  plateau. Re-measured legx magnitude on the plateau: 1.06×.
- Truth velocity read via the correct ENU→NED mapping (NED_x=ENU_y).
- `magnitude_ok` added to the pass criteria (was computed but not gated).

Two tooling bugs were fixed along the way. (a) Runner passed `--axis-map -yx`
positionally; argparse read the leading-dash `-yx` as an option and the bridge
died at launch with zero flow (run `20260713_125325` lost its bridge). Fixed to
`--axis-map=VALUE`. (b) The analyzer's AGL used `range_m.median()` over a column
that contains `inf` on rangefinder dropouts; the median could land on `inf` and
silently zero the expected rate (`magnitude_ratio=None`). Fixed to median over
finite positive readings only, and it now reports `agl_finite_fraction`.

**Rangefinder finding (blocks 8G.3, not the flow gates).** That
`agl_finite_fraction` exposed a real sensor/world bug: on legy the RAW downward
gpu_lidar was `inf` 82% of the time (legx: 0%). Correlation with truth shows it
is not attitude (roll/pitch ≈ 0° throughout) — it tracks world +x position and
persists even while the vehicle sits landed at wx≈15 m. The camera stayed at
quality 255 over the same ground, so the visual ground exists; the single-point
lidar was slipping through the hairline gaps between the 36 thin (4 mm) tile
visuals, asymmetrically in x. First fix attempt emitted a visual-only
`ground_base`; run `20260713_134012` improved only to 25.8% finite RAW range,
so it did **not** verify the repair. Final fix: `build_gazebo_world.py` emits
the base slab as both collision and visual geometry (full size, 0.2 m thick,
top just below the tile top), so every ray that misses a tile still hits
continuous ground. Re-fly `20260713_143843` verifies the fix: RAW rangefinder
4440/4440 finite (100.0%) through `t_sim=100.84s`; bridge range 644/644
finite; analyzer `agl_finite_fraction=1.0`, `openloop_ok=True`,
`magnitude_ratio=1.063`, OF_DELAY candidate 111 ms.

**Standing run rule (operator, 2026-07-13):** every run keeps BOTH operator
connections live — QGroundControl MAVLink (runner default, no `--no-qgc`;
target 100.109.200.5) and the gz-web viz (`visualization.gazebo_web.enabled`,
raw runner bridge on 9003; browser connects through the enum-patch proxy on
9002 to avoid the gray gz-web missing-enum failure).

### 8G.3 closed-loop phototex fusion (2026-07-13): primary gate GREEN

Primary closed-loop run `20260713_151744` used the corrected webgz pipeline
(enum-patch proxy on 9002 for the browser, raw runner `gz-launch` bridge on
9003), `axis_map: -yx`, and airframe `EKF2_OF_DELAY=111` with
`EKF2_OF_CTRL=1`. Runner acceptance is green:

- `accepted=True`, `landing_ok=True`, `qgc_connected=True`.
- `ulog_airborne_duration_s=62.72` for `requested_airborne_s=60.0`;
  `ulog_max_height_up_m=2.505`.
- RAW rangefinder `5580/5580` finite (100.0%), final `t_sim=123.5s`;
  ULog distance sensor `3649` rows, height agreement 0.006 m.
- Flow bridge `813` sent rows, final bridge `t_frame_sim_s=123.486`;
  flow recording `3382` frames.
- ULog fusion analyzer: `flow_fusion_ok=True`, `sensor_optical_flow_rows=525`,
  `aid_src_optical_flow_rows=527`, `cs_opt_flow_active_count=65/95` (68.4%),
  `flow_fused_count=443`, `flow_rejected_count=21`,
  `flow_rejected_over_fused=0.0474`, `xy_reset_counter_delta=2`.
- Postprocess accepted truth/ULog extraction: `truth_rows=23401`,
  `truth_duration_s=112.672`, `csv_count=10`.

Two runner lifecycle bugs were fixed while getting this gate clean. First,
local-hold runs were sleeping wall time, but this stack runs at RTF far below
1; the first 8G.3 attempt landed after only `12.888s` ULog airborne although
QGC wall-clock looked like a normal minute-long flight. The runner now waits
on PX4 `vehicle_land_detected.timestamp` sim time for local-hold hover
duration. Second, after switching to sim-time waits, the recorder, flow
bridge, and offboard setpoint sender could expire at their old wall-time caps;
they now share a local-hold auxiliary duration budget
(`sim_time_wall_multiplier`, default now 30 after the slower terrain proof).
The fixed 8G.3 accepted run used `--duration-s 1222.0` for all three helpers
under the earlier flat-world budget. A final ergonomics fix replaces
the unconditional post-land `sleep(land_timeout_s)` with a landing-complete
poll so future accepted runs do not wait the full timeout after
`Disarmed by landing`.

### 8G.4 phase acceptance / handoff (2026-07-13)

Phase 8G is accepted on the phototex primary world. The accepted evidence
chain is: 8G.1 replay regression → 8G.2 open-loop D5 sign/magnitude gates on
two orthogonal legs → airframe `EKF2_OF_DELAY=111` → 8G.3 GNSS-on EKF2
closed-loop fusion green in ULog. The standing operator connection rule is
frozen for later phases: QGroundControl stays enabled, the raw runner web
bridge owns 9003 only during a run, and browser traffic enters through the
enum-patch proxy on 9002.

Handoff to 8I: the estimator config is frozen (`sift`, `max_width 480`,
`axis_map: -yx`, quality scaling [20,100], `EKF2_OF_QMIN=17`,
`EKF2_OF_DELAY=111`). The flat favorable world is
`flat_rural_phototex_noon`. Before terrain 8I runs, repair the
`serefli_koschisar` downward-camera texture path; do not use the old gray
launch-pad / unrendered heightmap view as a flow-quality claim.

## What this phase proves / does not prove

Proves: our modular estimator is fusable by EKF2 in real time through the
standard OPTICAL_FLOW_RAD path, GNSS on, quantitatively consistent with truth.
Does not prove: GNSS-denied navigation on flow (8I), gyro-compensated flow in
aggressive maneuvers, real-hardware latency behavior.

## Next phase

Phase 8H — GNSS-on fusion quality check folds into 8G.3 here; next standalone
phase is 8I — GNSS-denied A/B/C/D comparison against the frozen 8A anchors.
