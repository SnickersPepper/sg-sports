#!/usr/bin/env node
import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const queue = resolve(projectRoot, ".compose-clean-install-queue");
const [command = "health", id] = process.argv.slice(2);

if (command === "health") {
  process.stdout.write(await readFile(resolve(queue, "health.json"), "utf8"));
} else if (command === "run") {
  const health = JSON.parse(await readFile(resolve(queue, "health.json"), "utf8"));
  if (health.command !== "compose-clean-install" || health.status !== "ready") {
    throw new Error("Compose clean-install worker is not ready");
  }
  if (Date.now() / 1000 - health.updated > 30) {
    throw new Error("Compose clean-install worker heartbeat is stale");
  }
  const job = randomUUID();
  const folder = resolve(queue, job);
  await mkdir(folder, { recursive: false });
  await chmod(folder, 0o770);
  await writeFile(resolve(folder, "request.tmp"), JSON.stringify({ command: "compose-clean-install" }));
  await rename(resolve(folder, "request.tmp"), resolve(folder, "request.json"));
  console.log(JSON.stringify({ job, command: "compose-clean-install", status: "queued" }));
  console.log(`Use: node scripts/compose-clean-install.mjs status ${job}`);
} else if (command === "status" || command === "log") {
  if (!/^[a-f0-9-]{36}$/.test(id ?? "")) throw new Error("Valid job UUID required");
  const folder = resolve(queue, id);
  if (command === "log") {
    process.stdout.write(await readFile(resolve(folder, "output.log"), "utf8").catch(() => ""));
  } else {
    for (const name of ["running.json", "result.json"]) {
      console.log(`${name}: ${await readFile(resolve(folder, name), "utf8").catch(() => "pending")}`);
    }
  }
} else {
  throw new Error("Usage: compose-clean-install.mjs health|run|status JOB|log JOB");
}
