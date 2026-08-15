async function fetchJSON(path) {
  const resp = await fetch(path, { cache: "no-store" });
  if (!resp.ok) {
    throw new Error(`${path} -> HTTP ${resp.status}`);
  }
  return resp.json();
}

function startPolling(tasks, intervalMs) {
  const runAll = () => {
    for (const task of tasks) {
      task().catch((err) => console.error("[poll]", err));
    }
  };
  runAll();
  return setInterval(runAll, intervalMs);
}
