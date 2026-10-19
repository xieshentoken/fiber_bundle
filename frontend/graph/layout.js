import helpers from "graphology-layout-forceatlas2/helpers.js";
import defaults from "graphology-layout-forceatlas2/defaults.js";

export function createLayout(graph, onFailure) {
  let worker = null,
    running = false,
    timer = null,
    matrices = null;
  const settings = {
    ...defaults,
    barnesHutOptimize: true,
    gravity: 1,
    scalingRatio: 8,
    slowDown: 4,
  };
  function stop() {
    running = false;
    clearTimeout(timer);
    worker?.terminate();
    worker = null;
    matrices = null;
  }
  function start() {
    stop();
    if (graph.order < 2) return;
    try {
      worker = new Worker("/static/graph_force_worker.js", { type: "module" });
      running = true;
      matrices = helpers.graphToByteArrays(
        graph,
        (_edge, attributes) => attributes.weight || 1,
      );
      worker.onerror = () => {
        stop();
        onFailure("布局失败，已保留静态图和笔记列表。");
      };
      worker.onmessage = (event) => {
        if (!running) return;
        const nodes = new Float32Array(event.data.nodes);
        if (
          nodes.length !== graph.order * 10 ||
          !nodes.every(Number.isFinite)
        ) {
          stop();
          onFailure("布局结果不可用，已暂停。");
          return;
        }
        helpers.assignLayoutChanges(graph, nodes);
        matrices.nodes = nodes;
        worker.postMessage({ settings, nodes: nodes.buffer }, [nodes.buffer]);
      };
      worker.postMessage(
        {
          settings,
          nodes: matrices.nodes.buffer,
          edges: matrices.edges.buffer,
        },
        [matrices.nodes.buffer, matrices.edges.buffer],
      );
      timer = setTimeout(stop, 8000);
    } catch {
      stop();
      onFailure("无法启动布局，已保留静态视图。");
    }
  }
  return { start, stop, isRunning: () => running, kill: stop };
}
