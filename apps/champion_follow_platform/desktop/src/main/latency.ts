const MAX_SAMPLES = 500;
const MIN_LEARNED_SAMPLES = 30;

export class LatencyWindow {
  private readonly samples: number[] = [];

  constructor(private readonly configuredMarginMs: number) {
    if (!Number.isSafeInteger(configuredMarginMs) || configuredMarginMs < 0) {
      throw new Error("latency_margin_invalid");
    }
  }

  get count(): number {
    return this.samples.length;
  }

  add(milliseconds: number): void {
    if (!Number.isSafeInteger(milliseconds) || milliseconds < 0 ||
        milliseconds > 60_000) {
      throw new Error("latency_sample_invalid");
    }
    this.samples.push(milliseconds);
    if (this.samples.length > MAX_SAMPLES) this.samples.shift();
  }

  safeLeadMs(): number {
    if (this.samples.length < MIN_LEARNED_SAMPLES) return 2_000;
    return clamp(
      nearestRankP99(this.samples) + this.configuredMarginMs,
      700,
      3_000,
    );
  }
}

export function nearestRankP99(values: readonly number[]): number {
  if (values.length === 0) throw new Error("latency_samples_missing");
  const sorted = [...values].sort((left, right) => left - right);
  const rank = Math.ceil(0.99 * sorted.length);
  return sorted[rank - 1]!;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
