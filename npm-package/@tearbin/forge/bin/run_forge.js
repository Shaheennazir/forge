#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

// Resolve the hermes venv forge binary — this is where forge-cli is installed
const VENV_FORGE = "/home/shaheen/.hermes/hermes-agent/venv/bin/forge";

// Fallback: search PATH for any 'forge' binary
function findForgeInPath() {
  const PATH_SEP = process.platform === "win32" ? ";" : ":";
  const pathDirs = (process.env.PATH || "").split(PATH_SEP);
  for (const dir of pathDirs) {
    const candidate = path.join(dir, "forge");
    if (fs.existsSync(candidate) || fs.existsSync(candidate + ".exe")) {
      return candidate;
    }
  }
  return null;
}

const forgeBin = fs.existsSync(VENV_FORGE) ? VENV_FORGE : findForgeInPath();
const args = process.argv.slice(2);

if (!forgeBin) {
  console.error("Error: forge not found.");
  console.error("Make sure forge-cli is installed in your Python environment:");
  console.error("  pip install forge-cli");
  process.exit(1);
}

const child = spawn(forgeBin, args, {
  stdio: "inherit",
  shell: false,
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error("Error: forge not found. Install with: pip install forge-cli");
    process.exit(1);
  }
  throw err;
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
