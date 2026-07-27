import { app } from "electron";
import { join } from "node:path";

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
