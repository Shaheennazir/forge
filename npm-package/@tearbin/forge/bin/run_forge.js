#!/usr/bin/env node
"use strict";

const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

function whichForge() {
  // Try to locate forge on PATH via `which`
  try {
    return execSync("which forge", { stdio: ["pipe", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    // not found
  }
  // Fallback: scan PATH dirs manually
  const PATH_SEP = process.platform === "win32" ? ";" : ":";
  const pathDirs = (process.env.PATH || "").split(PATH_SEP);
  for (const dir of pathDirs) {
    if (!dir) continue;
    const candidate = path.join(dir, "forge");
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // not executable or doesn't exist
    }
  }
  return null;
}

const forgeBin = whichForge();
const args = process.argv.slice(2);

if (!forgeBin) {
  console.error("Error: 'forge' command not found on PATH.");
  console.error("Install forge-tui first:");
  console.error("  pip install forge-tui");
  console.error("");
  console.error("Or use npx which will download and run forge automatically:");
  console.error("  npx @tearbin/forge");
  process.exit(1);
}

const child = spawn(forgeBin, args, {
  stdio: "inherit",
  shell: false,
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error("Error: failed to execute forge at:", forgeBin);
    process.exit(1);
  }
  throw err;
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
