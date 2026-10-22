export type Status = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
export type ComparisonStatus = Status | 'completed' | 'original' | 'mixed';
/** output includes reasoning. Non-reasoning includes any unattributed output; answer is independently reported. */
export interface Tokens {
  input?: number | null; output?: number | null; reasoning?: number | null; answer?: number | null;
  nonReasoning?: number | null; unattributed?: number | null; sources?: Record<string,string>; issues?: string[];
}
export function normalizeUsage(usage?: Record<string,unknown> | null, options?: {textOnly?: boolean; answerText?: string}): Tokens;
export interface ContextEntry { label: string; role: string; source: string; content?: unknown; fields: Record<string,unknown> }
export interface Context { entries: ContextEntry[]; settings: Record<string,unknown>; notices: string[] }
/** Preserves input order and unknown fields; never fetches provider-held context. */
export function normalizeContext(input?: unknown): Context;
export class RequestContext {
  constructor(element: Mount, options?: {input?: unknown});
  update(input?: unknown): void;
  destroy(): void;
}
export interface Result {
  model: string; status: Status; output?: unknown; durationMs?: number | null;
  tokens?: Tokens; error?: string; reason?: string;
}
export interface Judgment { status: 'pending' | 'running' | 'completed' | 'uncertain' | 'error' | 'skipped'; verdict?: 'equivalent' | 'different' | 'uncertain'; confidence?: number; probabilities?: Record<string,number>; model?: string; rubric_version?: string }
export interface Candidate extends Result { id: string; provider?: string; judgment?: Judgment; grade?: { label: string; explanation?: string } }
export interface Span {
  id: string; name: string; startMs: number; durationMs: number; status: Status;
  kind?: 'original' | 'capture' | 'queue' | 'candidate' | 'grade';
}
export interface Request {
  id: string; name?: string; startedAt?: number; captured?: boolean; input?: unknown;
  original: Result; candidates?: Candidate[]; spans?: Span[];
}
export interface NormalizedRequest extends Request {
  name: string; startedAt: number; captured: boolean; candidates: Candidate[]; spans: Span[];
}
export interface Summary { total: number; succeeded: number; captured: number; compared: number; skipped: number; failed: number }
export function normalizeRequest(request: Request): NormalizedRequest;
export function summarize(requests: NormalizedRequest[]): Summary;
export interface ModelSummary {
  key: string; model: string; provider: string; reference: string;
  total: number; completed: number; failed: number; skipped: number; pending: number; running: number;
  matches: number; scored: number; agreement: number | null;
  semanticMatches: number; semanticScored: number; semanticUncertain: number; semanticAgreement: number | null;
  latencySamples: number; latencyMedian: number | null; referenceLatencyMedian: number | null;
  answerMean: number | null; answerSamples: number; reasoningMean: number | null; reasoningSamples: number;
  nonReasoningMean: number | null; nonReasoningSamples: number;
  outputMean: number | null; outputSamples: number;
}
export function summarizeModels(requests: NormalizedRequest[]): ModelSummary[];
export function comparisonStatus(request: NormalizedRequest): ComparisonStatus;
export function diffLines(original: unknown, candidate: unknown): Array<{ original: string; candidate: string; changed: boolean }>;
type Mount = string | HTMLElement;
export interface DashboardOptions {
  title?: string; subtitle?: string; requests?: Request[]; maxRequests?: number;
  layout?: 'trace' | 'comparisons';
  onRun?: (dashboard: Dashboard) => void | Promise<void>;
}
export interface SourceSink { upsert(request: Request): void; upsertMany(requests: Request[]): void; status(text: string): void; error(error: Error | string): void }
export interface DashboardEvents {
  'request:select': NormalizedRequest;
  'candidate:select': { id: string; request: NormalizedRequest };
  'node:select': { id: string; request: NormalizedRequest };
  'span:select': Span;
  'run:error': unknown;
}
export class Dashboard {
  constructor(element: Mount, options?: DashboardOptions);
  readonly requests: NormalizedRequest[];
  readonly selectedId: string | null;
  readonly candidateId: string | null;
  readonly paused: boolean;
  readonly explorer: RequestExplorer; readonly trace: TraceMap;
  readonly inspector: OutputInspector; readonly timeline: Timeline;
  readonly comparisons: ComparisonSummary | null;
  setData(requests: Request[]): void;
  /** Full record replacement, keyed by id; not a partial merge. */
  upsert(request: Request): void;
  upsertMany(requests: Request[]): void;
  select(id: string, candidateId?: string | null): boolean;
  selected(): NormalizedRequest | null;
  setPaused(paused: boolean): void;
  run(): Promise<void>;
  on<K extends keyof DashboardEvents>(name: K, handler: (detail: DashboardEvents[K]) => void): () => void;
  connect(subscribe: (sink: SourceSink) => (() => void) | void): () => void;
  disconnect(): void;
  exportData(): string;
  download(): void;
  destroy(): void;
}
export class RequestExplorer {
  constructor(element: Mount, options?: { onSelect?: (id: string) => void });
  update(requests: NormalizedRequest[], selectedId?: string | null): void;
  setFilter(filter: ComparisonStatus | 'all'): void;
  destroy(): void;
}
export class ComparisonSummary {
  constructor(element: Mount, options?: {requests?: Request[]; onSelect?: (selection: {requestId: string; candidateId: string}) => void});
  update(requests: Request[]): void;
  destroy(): void;
}
export interface HistogramSeries {
  id: string; label?: string; values: Array<number | null | undefined>;
  color?: 'blue' | 'purple' | 'mint' | 'orange';
}
export interface HistogramData {
  max: number; bins: Array<{start: number; end: number}>;
  series: Array<{id: string; label: string; color: string; values: number[]; excluded: number; counts: number[]; median: number | null; p95: number | null}>;
}
export function binHistogram(series: HistogramSeries[], bins?: number): HistogramData;
export function comparisonDistributions(requests: NormalizedRequest[], metric: 'latency' | 'reasoning' | 'nonReasoning' | 'answer', model?: string): HistogramSeries[];
export class Histogram {
  constructor(element: Mount, options?: {title?: string; subtitle?: string; unit?: string; bins?: number; layout?: 'grouped' | 'overlay'; series?: HistogramSeries[]});
  update(series: HistogramSeries[]): void;
  setBins(bins: number): void;
  destroy(): void;
}
export class TraceMap {
  constructor(element: Mount, options?: { onNodeSelect?: (id: string) => void });
  update(request: NormalizedRequest | null, candidateId?: string | null): void;
  setZoom(value: number): void;
  fit(): void;
  destroy(): void;
}
export class OutputInspector {
  constructor(element: Mount, options?: { onCandidateSelect?: (id: string) => void });
  update(request: NormalizedRequest | null, candidateId?: string | null): void;
  destroy(): void;
}
export class Timeline {
  constructor(element: Mount, options?: { onSpanSelect?: (span: Span) => void });
  update(request: NormalizedRequest | null): void;
  replay(): void;
  stop(): void;
  destroy(): void;
}
declare const PixelLab: {
  Dashboard: typeof Dashboard; ComparisonSummary: typeof ComparisonSummary; RequestExplorer: typeof RequestExplorer; TraceMap: typeof TraceMap;
  Histogram: typeof Histogram; binHistogram: typeof binHistogram; comparisonDistributions: typeof comparisonDistributions;
  OutputInspector: typeof OutputInspector; Timeline: typeof Timeline; normalizeRequest: typeof normalizeRequest;
  RequestContext: typeof RequestContext; normalizeContext: typeof normalizeContext;
  normalizeUsage: typeof normalizeUsage;
  summarize: typeof summarize; summarizeModels: typeof summarizeModels; comparisonStatus: typeof comparisonStatus; diffLines: typeof diffLines;
};
export default PixelLab;
