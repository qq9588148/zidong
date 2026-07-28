interface CommandLinePort {
  appendSwitch(name: string, value: string): void;
}

export function configureCollectorDiagnostics(
  commandLine: CommandLinePort,
  enabled: string | undefined,
): boolean {
  if (enabled === undefined || enabled === "") return false;
  if (enabled !== "1") throw new Error("collector_diagnostics_invalid");
  commandLine.appendSwitch("remote-debugging-address", "127.0.0.1");
  commandLine.appendSwitch("remote-debugging-port", "9223");
  return true;
}
