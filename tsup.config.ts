import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    cli: "src/cli.ts",
    mcp: "src/mcp.ts",
    index: "src/browser.ts"
  },
  format: ["esm"],
  target: "node22",
  clean: true,
  banner: {
    js: "#!/usr/bin/env node"
  }
});

