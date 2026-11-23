import helpers from "graphology-layout-forceatlas2/helpers.js";
import defaults from "graphology-layout-forceatlas2/defaults.js";

// A separate bounded supervisor leaves the existing graph page unchanged.
// Each generation owns its worker and ignores any late response after stop.
export function createCloudLayout(graph, onError = () => {}, options = {}) {
  const {
    createWorker = () =>
      new Worker("/static/graph_force_worker.js", { type: "module" }),
    schedule = setTimeout,
    unschedule = clearTimeout,
    maxDuration = 4000,
    maxRounds = 160,
  } = options;
  let worker = null,
    timer = null,
    generation = 0,
    rounds = 0,
    running = false;
  const settings = {
    ...defaults,
    barnesHutOptimize: true,
    gravity: 0.8,
    scalingRatio: 9,
    slowDown: 5,
  };
  function stop() {
    generation += 1;
    running = false;
    if (timer !== null) unschedule(timer);
    timer = null;
    if (worker) {
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
      worker = null;
    }
  }
  function start() {
    stop();
    // Isolated notes keep their deterministic positions without an unneeded
    // worker. They remain present and selectable.
    if (graph.order < 2 || graph.size === 0) return;
    const ownedGeneration = generation;
    try {
      worker = createWorker();
      const ownedWorker = worker;
      const matrices = helpers.graphToByteArrays(
        graph,
        (_edge, attributes) => attributes.weight || 1,
      );
      running = true;
      rounds = 0;
      const fail = () => {
        if (generation !== ownedGeneration) return;
        stop();
        onError("layout");
      };
      worker.onerror = fail;
      worker.onmessage = (event) => {
        if (!running || generation !== ownedGeneration) return;
        try {
          const nodes = new Float32Array(event.data.nodes);
          if (
            nodes.length !== graph.order * 10 ||
            !nodes.every(Number.isFinite)
          )
            return fail();
          helpers.assignLayoutChanges(graph, nodes);
          rounds += 1;
          if (rounds >= maxRounds) return stop();
          ownedWorker.postMessage({ settings, nodes: nodes.buffer }, [
            nodes.buffer,
          ]);
        } catch {
          fail();
        }
      };
      worker.postMessage(
        {
          settings,
          nodes: matrices.nodes.buffer,
          edges: matrices.edges.buffer,
        },
        [matrices.nodes.buffer, matrices.edges.buffer],
      );
      timer = schedule(stop, maxDuration);
    } catch {
      stop();
      onError("layout");
    }
  }
  return { start, stop, kill: stop, isRunning: () => running };
}
