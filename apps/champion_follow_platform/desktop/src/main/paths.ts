import { app } from "electron";
import { join, resolve } from "node:path";

export type DesktopPaths = {
  profile: string;
  journal: string;
  diagnostics: string;
};

export function desktopPaths(): DesktopPaths {
  const profile = app.getPath("userData");
  return {
    profile,
    journal: join(profile, "journal"),
    diagnostics: join(profile, "diagnostics"),
  };
}

export type ContractName =
  | "device-task-v1.schema.json"
  | "client-event-v1.schema.json";

export function contractPath(
  name: ContractName,
  options?: {
    packaged: boolean;
    appPath: string;
    resourcesPath: string;
  },
): string {
  const environment = options ?? {
    packaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath,
  };
  return environment.packaged
    ? join(environment.resourcesPath, "contracts", name)
    : resolve(environment.appPath, "../contracts", name);
}
