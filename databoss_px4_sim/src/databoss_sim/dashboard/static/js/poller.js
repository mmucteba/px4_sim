export function startPolling(fn, { baseMs = 5000, maxMs = 30000 } = {}) {
  let stopped = false;
  let timer = null;
  let delay = baseMs;
  let running = false;
  let queued = false;

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function schedule() {
    clearTimer();
    if (stopped || document.visibilityState === "hidden") return;
    timer = setTimeout(run, delay);
  }

  async function run() {
    if (stopped) return;
    if (document.visibilityState === "hidden") {
      schedule();
      return;
    }
    if (running) {
      queued = true;
      return;
    }

    running = true;
    try {
      await fn();
      delay = baseMs;
    } catch (e) {
      delay = Math.min(maxMs, Math.ceil(delay * 1.5));
    } finally {
      running = false;
      if (queued) {
        queued = false;
        run();
      } else {
        schedule();
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === "hidden") clearTimer();
    else {
      delay = baseMs;
      run();
    }
  }

  function stop() {
    stopped = true;
    clearTimer();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("beforeunload", stop);
  }

  function kick() {
    delay = baseMs;
    run();
  }

  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("beforeunload", stop);
  run();

  return { stop, kick };
}
