import { describe, expect, it } from "vitest";

import { canonicalJson } from "../src/canonical-json.js";

describe("canonicalJson", () => {
  it("sorts object keys recursively without changing array order", () => {
    expect(canonicalJson({ z: [{ b: 2, a: 1 }], a: "中文" })).toBe(
      '{"a":"中文","z":[{"a":1,"b":2}]}',
    );
  });
});
