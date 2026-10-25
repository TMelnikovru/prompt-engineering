function fakeSummary(dashboard) {
  const pending = dashboard.tasks.filter((task) => task.status !== "done");
  const minutes = dashboard.plan.blocks.reduce((total, block) => total + block.minutes, 0);
  return {
    fake: true,
    summary: {
      summary: `В примере осталось заданий: ${pending.length}. Материалов: ${dashboard.materials.length}. `
        + `В план включено ${minutes} мин из доступных ${dashboard.plan.available_minutes} мин.`,
      highlights: dashboard.plan.blocks.map((block) => ({
        text: `${dashboard.tasks.find((task) => task.id === block.task_id)?.title || "Задание"}. ${block.reason}`,
      })),
    },
  };
}

// Invalidation also covers group changes: a late live response must not replace
// the summary of another profile or leak live content into the fake example.
export function createSummaryLoader({ getDashboard, getDataSource, fetchSummary, onLoading, onResult, onError }) {
  let version = 0;
  let loaded = false;
  return {
    get loaded() { return loaded; },
    invalidate() {
      version++;
      loaded = false;
      onLoading(false);
    },
    async load() {
      const request = ++version;
      loaded = false;
      onLoading(true);
      try {
        const dashboard = getDashboard();
        if (!dashboard) return;
        const result = getDataSource() === "fake" ? fakeSummary(dashboard) : await fetchSummary();
        if (request !== version) return;
        onResult(result);
        loaded = true;
      } catch (error) {
        if (request === version) onError(error);
      } finally {
        if (request === version) onLoading(false);
      }
    },
  };
}
