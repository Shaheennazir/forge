#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const path = require("path");

const script = path.join(__dirname, "..", "run_forge.py");
const args = process.argv.slice(2);

const child = spawn("python3", [script, ...args], {
  stdio: "inherit",
  shell: false,
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error("Error: python3 not found. Please install Python 3.11+");
    process.exit(1);
  }
  throw err;
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
