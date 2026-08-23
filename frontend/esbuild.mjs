import { build } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";
import path from "node:path";

const output = path.resolve(process.env.HIVE_FRONTEND_OUT_DIR || "../src/hive/webpanel/resources");
await mkdir(output, { recursive: true });
await build({
  entryPoints: ["src/main.tsx"],
  bundle: true,
  outfile: path.join(output, "panel.js"),
  format: "iife",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  sourcemap: false,
  legalComments: "none",
});
await copyFile("src/panel.html", path.join(output, "panel.html"));
