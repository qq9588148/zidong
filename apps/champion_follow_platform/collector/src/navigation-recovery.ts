export interface NavigationRecoverySteps {
  pause(): Promise<void>;
  resetHistory(generation: number): void;
  reconcileSession(): Promise<void>;
  injectPageHook(generation: number): Promise<void>;
  waitForSessionRetry(signal: AbortSignal): Promise<void>;
  sessionReady(generation: number): void;
  startLoops(): void;
  failClosed(): void;
}

export interface CommittedMainFrame {
  generation: number;
  done: Promise<void>;
}

function reloadError(): Error {
  return new Error("collector_reload_failed");
}

export class NavigationRecoveryCoordinator {
  private generation = 0;
  private acceptedGeneration: number | null = null;
  private sessionGeneration: number | null = null;
  private awaitingHistoryGeneration: number | null = null;
  private loadedOnce = false;
  private failed = false;
  private active: AbortController | null = null;
  private tail: Promise<void> = Promise.resolve();

  constructor(private readonly steps: NavigationRecoverySteps) {}

  get currentGeneration(): number {
    return this.generation;
  }

  committedMainFrame(): CommittedMainFrame {
    const generation = ++this.generation;
    this.active?.abort();
    const controller = new AbortController();
    this.active = controller;
    this.acceptedGeneration = null;
    this.sessionGeneration = null;
    this.awaitingHistoryGeneration = null;
    const done = this.tail.then(() => this.process(generation, controller));
    this.tail = done.catch(() => undefined);
    return { generation, done };
  }

  acceptsPageState(generation: number): boolean {
    return !this.failed && this.acceptedGeneration === generation;
  }

  historyReady(generation: number): boolean {
    return !this.failed && this.sessionGeneration === generation;
  }

  async stop(): Promise<void> {
    this.active?.abort();
    this.acceptedGeneration = null;
    this.sessionGeneration = null;
    this.awaitingHistoryGeneration = null;
    await this.tail.catch(() => undefined);
  }

  historyRecovered(generation: number): void {
    if (
      this.failed ||
      generation !== this.generation ||
      this.sessionGeneration !== generation ||
      this.awaitingHistoryGeneration !== generation
    ) {
      return;
    }
    this.awaitingHistoryGeneration = null;
    try {
      this.steps.startLoops();
    } catch {
      if (generation !== this.generation) return;
      this.fail();
      throw reloadError();
    }
  }

  private async process(
    generation: number,
    controller: AbortController,
  ): Promise<void> {
    if (this.failed) throw reloadError();
    const isReload = this.loadedOnce;
    try {
      if (isReload) {
        await this.steps.pause();
        if (!this.current(generation, controller)) return;
      }
      if (!this.current(generation, controller)) return;
      this.steps.resetHistory(generation);
      await this.steps.injectPageHook(generation);
      if (!this.current(generation, controller)) return;
      this.loadedOnce = true;
      this.acceptedGeneration = generation;
      if (isReload) {
        for (;;) {
          try {
            await this.steps.reconcileSession();
            break;
          } catch (error) {
            if (!this.current(generation, controller)) return;
            if (!this.transientSessionError(error)) throw error;
            await this.steps.waitForSessionRetry(controller.signal);
            if (!this.current(generation, controller)) return;
          }
        }
      }
      if (!this.current(generation, controller)) return;
      this.sessionGeneration = generation;
      this.awaitingHistoryGeneration = isReload ? generation : null;
      this.steps.sessionReady(generation);
    } catch {
      if (!this.current(generation, controller)) return;
      this.fail();
      throw reloadError();
    }
  }

  private current(
    generation: number,
    controller: AbortController,
  ): boolean {
    return generation === this.generation && !controller.signal.aborted;
  }

  private transientSessionError(error: unknown): boolean {
    return (
      error instanceof Error &&
      (error.message === "collector_network_error" ||
        error.message === "collector_server_error")
    );
  }

  private fail(): void {
    if (this.failed) return;
    this.failed = true;
    this.active?.abort();
    this.acceptedGeneration = null;
    this.sessionGeneration = null;
    this.awaitingHistoryGeneration = null;
    this.steps.failClosed();
  }
}
