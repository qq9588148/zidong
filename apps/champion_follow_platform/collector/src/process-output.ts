interface ErrorStream {
  on(
    event: "error",
    listener: (error: NodeJS.ErrnoException) => void,
  ): unknown;
}

export function ignoreBrokenPipe(stream: ErrorStream | null | undefined): void {
  stream?.on("error", (error) => {
    if (error.code === "EPIPE") return;
    throw error;
  });
}
