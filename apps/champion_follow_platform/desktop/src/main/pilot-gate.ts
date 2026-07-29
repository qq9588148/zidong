export function parsePilotMaxConfirmedOrders(
  value: string | undefined,
): number | null {
  if (value === undefined || value.trim() === "") return null;
  return value.trim() === "1" ? 1 : 0;
}

export function pilotMaxConfirmedOrdersFromArgs(
  args: readonly string[],
): number | null {
  const prefix = "--pilot-max-confirmed-orders=";
  const values = args.filter((value) => value.startsWith(prefix));
  if (values.length === 0) return null;
  if (values.length !== 1) return 0;
  return parsePilotMaxConfirmedOrders(values[0]!.slice(prefix.length));
}
