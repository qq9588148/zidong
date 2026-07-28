import { readFileSync } from "node:fs";

import { ProcessNativeHelper, type NativeHelper } from "./native-helper";
import { nativeHelperPaths } from "./paths";

export function createNativeHelper(): NativeHelper {
  const paths = nativeHelperPaths();
  const expectedSha256 = readFileSync(paths.sha256, "utf8").trim();
  return new ProcessNativeHelper(paths.executable, expectedSha256);
}
