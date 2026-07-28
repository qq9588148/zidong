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

export type NativeHelperPaths = {
  executable: string;
  sha256: string;
};

export function nativeHelperPaths(options?: {
  packaged: boolean;
  appPath: string;
  resourcesPath: string;
}): NativeHelperPaths {
  const environment = options ?? {
    packaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath,
  };
  const directory = environment.packaged
    ? join(
        environment.resourcesPath,
        "native",
        "ChampionFollow.DeviceIdentity",
      )
    : join(
        environment.appPath,
        "native",
        "ChampionFollow.DeviceIdentity",
        "bin",
        "Release",
        "net10.0-windows",
        "win-x64",
        "publish",
      );
  return {
    executable: join(directory, "ChampionFollow.DeviceIdentity.exe"),
    sha256: join(directory, "ChampionFollow.DeviceIdentity.sha256"),
  };
}
