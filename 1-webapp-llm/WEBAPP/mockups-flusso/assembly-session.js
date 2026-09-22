(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AssemblySession = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const ACTIVE_KEY = "smart-factory:active-execution:v1";
  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

  function defaultFeatures(mode) {
    return { assemblyGuard: true, assistant: mode === "assisted" };
  }

  function normalizeActive(state) {
    if (state?.status !== "active") return state;
    let normalized = state;
    if (!hasOwn(state, "mode")) normalized = { ...normalized, mode: "baseline" };
    if (!hasOwn(state, "appVersion")) normalized = { ...normalized, appVersion: "prototype-1" };
    if (!hasOwn(state, "features")) normalized = { ...normalized, features: defaultFeatures(normalized.mode) };
    return normalized;
  }

  function createStore(options = {}) {
    const storage = options.storage ?? localStorage;
    const idFactory = options.idFactory ?? (() => crypto.randomUUID());
    const clock = options.clock ?? (() => Date.now());
    const onError = options.onError ?? (() => {});
    let memoryState = null;
    let preferMemory = false;

    function read() {
      try {
        const raw = storage.getItem(ACTIVE_KEY);
        if (!preferMemory) {
          const state = raw ? JSON.parse(raw) : memoryState;
          memoryState = normalizeActive(state);
          if (raw && memoryState !== state) write(memoryState);
        }
      } catch (error) {
        preferMemory = true;
        onError(error);
      }
      return memoryState;
    }

    function write(state) {
      memoryState = state;
      try {
        storage.setItem(ACTIVE_KEY, JSON.stringify(state));
        preferMemory = false;
      } catch (error) {
        preferMemory = true;
        onError(error);
      }
      return state;
    }

    function begin({ commissionId, operatorId, mode = "baseline", appVersion = "prototype-1", features = {}, forceNew = false }) {
      const active = read();
      if (!forceNew && active?.status === "active" && active.commissionId === commissionId) return active;
      return write({
        schemaVersion: 1,
        executionId: idFactory(),
        commissionId,
        operatorId: operatorId || "ANON",
        mode,
        appVersion,
        features: structuredClone(features),
        startedAt: clock(),
        status: "active",
        navigator: null
      });
    }

    function saveNavigator(executionId, navigator) {
      const active = read();
      if (!active || active.executionId !== executionId) return null;
      return write({ ...active, navigator, updatedAt: clock() });
    }

    function end(executionId) {
      const active = read();
      if (!active || active.executionId !== executionId) return false;
      memoryState = null;
      try {
        storage.removeItem(ACTIVE_KEY);
        preferMemory = false;
      } catch (error) {
        preferMemory = true;
        onError(error);
      }
      return true;
    }

    return { begin, current: read, saveNavigator, end };
  }

  return { ACTIVE_KEY, createStore };
});
