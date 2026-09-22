(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AssemblyNavigation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function createNavigator(options) {
    const stepCount = options.stepCount;
    const holdMs = options.holdMs ?? 2500;
    const debounceMs = options.debounceMs ?? 300;
    const now = options.now ?? (() => performance.now());
    const onChange = options.onChange ?? (() => {});
    const onEvent = options.onEvent ?? (() => {});
    const restored = options.initialState ?? {};

    if (!Number.isInteger(stepCount) || stepCount < 1) throw new Error("stepCount must be positive");

    let currentIndex = Math.min(Math.max(restored.currentIndex ?? 0, 0), stepCount - 1);
    let maxReached = Math.min(Math.max(restored.maxReached ?? currentIndex, currentIndex), stepCount - 1);
    let seen = Array.from({ length: stepCount }, (_, i) => Boolean(restored.seen?.[i]));
    let traversed = Array.from({ length: stepCount }, (_, i) => Boolean(restored.traversed?.[i]));
    let pageActive = options.initialActive !== false;
    let lockStartedAt = seen[currentIndex] ? null : (pageActive ? now() : null);
    let lastNavigationAt = Number.NEGATIVE_INFINITY;
    const heldKeys = new Set();

    function snapshot() {
      return { currentIndex, maxReached, seen: [...seen], traversed: [...traversed] };
    }

    function status() {
      return seen[currentIndex] ? "ready" : "locked";
    }

    function progress(at = now()) {
      if (seen[currentIndex]) return 1;
      if (!pageActive || lockStartedAt === null) return 0;
      return Math.max(0, Math.min(1, (at - lockStartedAt) / holdMs));
    }

    function view(at = now()) {
      return { ...snapshot(), status: status(), progress: progress(at), pageActive };
    }

    function emit(type, payload = {}) {
      onEvent({ type, stepIndex: currentIndex, ...payload });
    }

    function changed() {
      onChange(snapshot());
    }

    function blocked(reason, source) {
      emit("advance_blocked", { reason, source });
      return { accepted: false, reason, complete: false };
    }

    function tick(at = now()) {
      if (!seen[currentIndex] && pageActive && lockStartedAt !== null && at - lockStartedAt >= holdMs) {
        seen[currentIndex] = true;
        lockStartedAt = null;
        emit("step_seen", { source: "timer" });
        changed();
      }
      return view(at);
    }

    function setActive(active, at = now()) {
      const nextActive = Boolean(active);
      if (!nextActive) heldKeys.clear();
      if (nextActive === pageActive) return view(at);
      pageActive = nextActive;
      if (!seen[currentIndex]) lockStartedAt = pageActive ? at : null;
      emit("assembly_visibility_changed", { active: pageActive });
      return view(at);
    }

    function debounceBlocked(at, source) {
      return at - lastNavigationAt < debounceMs ? blocked("debounce", source) : null;
    }

    function enter(index, at, source) {
      const previousIndex = currentIndex;
      currentIndex = index;
      lastNavigationAt = at;
      if (!seen[currentIndex]) lockStartedAt = pageActive ? at : null;
      emit(index <= previousIndex ? "step_revisited" : "step_entered", { source, fromIndex: previousIndex });
      changed();
      return { accepted: true, reason: null, complete: false };
    }

    function goTo(index, at = now(), source = "pointer") {
      tick(at);
      if (!Number.isInteger(index) || index < 0 || index >= stepCount) return blocked("range", source);
      if (index > maxReached) return blocked("future", source);
      if (index === currentIndex) return { accepted: true, reason: null, complete: false };
      const debounce = debounceBlocked(at, source);
      if (debounce) return debounce;
      return enter(index, at, source);
    }

    function previous(at = now(), source = "pointer") {
      if (currentIndex === 0) return blocked("first_step", source);
      return goTo(currentIndex - 1, at, source);
    }

    function next(at = now(), source = "pointer") {
      tick(at);
      const debounce = debounceBlocked(at, source);
      if (debounce) return debounce;
      if (!seen[currentIndex]) return blocked("locked", source);

      const fromIndex = currentIndex;
      traversed[fromIndex] = true;
      lastNavigationAt = at;
      emit("step_advanced", { source, fromIndex });

      if (fromIndex === stepCount - 1) {
        changed();
        emit("assembly_completed", { source });
        return { accepted: true, reason: null, complete: true };
      }

      currentIndex += 1;
      maxReached = Math.max(maxReached, currentIndex);
      lockStartedAt = seen[currentIndex] ? null : (pageActive ? at : null);
      emit("step_entered", { source, fromIndex });
      changed();
      return { accepted: true, reason: null, complete: false };
    }

    function keyDown(input, at = now()) {
      const { key, repeat = false, editable = false, modalOpen = false } = input;
      if (editable || modalOpen || repeat || !["ArrowLeft", "ArrowRight"].includes(key)) {
        return { accepted: false, reason: "ignored", complete: false };
      }
      if (heldKeys.has(key)) return { accepted: false, reason: "held", complete: false };
      heldKeys.add(key);
      return key === "ArrowRight" ? next(at, "keyboard") : previous(at, "keyboard");
    }

    function keyUp(key) {
      heldKeys.delete(key);
    }

    return { view, tick, setActive, next, previous, goTo, keyDown, keyUp, snapshot };
  }

  return { createNavigator };
});
