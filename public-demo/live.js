"use strict";
const $ = (id) => document.getElementById(id);
let revision = 0, pending;
function clear(message) { $("status").textContent = message; $("digest").textContent = ""; $("timeline").replaceChildren(); }
$("form").addEventListener("input", () => { revision++; pending?.abort(); clear("Inputs changed. Run again."); $("run").disabled = false; });
$("form").addEventListener("submit", async (event) => {
  event.preventDefault(); const current = ++revision; pending?.abort();
  const controller = new AbortController(); pending = controller;
  const timer = setTimeout(() => controller.abort(), 12000);
  clear("Running native upload and store components..."); $("run").disabled = true;
  const input = Object.fromEntries(["chunks", "chunk_bytes", "seed", "retry_chunk", "restart_after"].map((name) => [name, Number($(name).value)]));
  input.corrupt = $("corrupt").value === "true";
  try {
    const response = await fetch("/api/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal: controller.signal });
    const result = await response.json(); if (current !== revision) return;
    if (!response.ok) throw new Error(result.error || "No protocol result available.");
    $("status").textContent = result.completed ? "Upload completed and digest verified" : "Corrupted completion rejected";
    $("digest").textContent = `${result.total_bytes} synthetic bytes. Expected SHA-256: ${result.expected_sha256}. Recorded: ${result.recorded_sha256 || "none"}.`;
    for (const row of result.timeline) { const item = document.createElement("li"); item.textContent = `${row.event}: offset ${row.offset}${row.complete === undefined ? "" : `, complete ${row.complete}`}`; $("timeline").append(item); }
  } catch (error) { if (current === revision) clear(error.name === "AbortError" ? "Request timed out. No result is available." : error.message); }
  finally { clearTimeout(timer); if (current === revision) $("run").disabled = false; }
});
