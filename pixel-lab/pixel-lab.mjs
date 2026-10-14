import './pixel-lab.js';
const api = globalThis.PixelLab;
export const { Dashboard, ComparisonSummary, Histogram, binHistogram, comparisonDistributions, RequestExplorer, RequestContext, TraceMap, OutputInspector, Timeline, normalizeContext, normalizeRequest, normalizeUsage, summarize, summarizeModels, comparisonStatus, diffLines } = api;
export default api;
