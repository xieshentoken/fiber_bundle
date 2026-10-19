// One failed secondary view must not hide a successfully loaded note selector.
export async function loadGraphScope(graph, scope, query = "", offset = 0) {
  const results = await Promise.allSettled([
    graph.notes(scope.client_id, scope.vault_id, query, offset),
    graph.suggestions(scope.client_id, scope.vault_id),
    graph.jobs(scope.client_id, scope.vault_id),
  ]);
  for (const item of results)
    if (item.status === "rejected" && [401, 403].includes(item.reason.status))
      throw item.reason;
  if (results[0].status === "rejected") throw results[0].reason;
  return {
    notes: results[0].value,
    suggestions:
      results[1].status === "fulfilled" ? results[1].value : { items: [] },
    tasks: results[2].status === "fulfilled" ? results[2].value : [],
    warnings: results
      .slice(1)
      .flatMap((item, i) =>
        item.status === "rejected"
          ? [(i ? "任务记录" : "关联建议") + "暂不可用：" + item.reason.message]
          : [],
      ),
  };
}
