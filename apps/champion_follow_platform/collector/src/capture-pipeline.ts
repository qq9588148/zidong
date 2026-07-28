export const CAPTURE_EVENT_CHUNK_LIMIT = 1_000;
export const CAPTURE_MESSAGE_LIMIT = 100;

export function isSecureCollectorPage(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

export function chunkCaptureEvents<T>(values: readonly T[]): T[][] {
  const chunks: T[][] = [];
  for (let start = 0; start < values.length; start += CAPTURE_EVENT_CHUNK_LIMIT) {
    chunks.push(values.slice(start, start + CAPTURE_EVENT_CHUNK_LIMIT));
  }
  return chunks;
}

export function createFifoDispatcher<T>(
  handle: (value: T) => Promise<void>,
  onError: (error: unknown) => void,
): (value: T) => Promise<void> {
  let tail = Promise.resolve();
  return (value) => {
    const operation = tail.then(() => handle(value));
    tail = operation.catch(onError);
    return tail;
  };
}
