#!/usr/bin/env node
/**
 * IFC -> That Open Fragments (.frag) offline converter (Phase A / A-04).
 *
 * Usage:
 *   node ifc_to_fragments.mjs <input.ifc> <output.frag>
 *
 * Uses @thatopen/fragments IfcImporter (web-ifc parses the IFC) to produce a
 * Fragments binary that the front-end Fragments loader can render efficiently.
 *
 * Exit codes:
 *   0  success (.frag written)
 *   1  bad arguments / usage error
 *   2  conversion failure (input unreadable, parse error, empty output, ...)
 */
import { readFile, writeFile, access } from "node:fs/promises";
import { constants as FS } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import * as FRAGS from "@thatopen/fragments";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

// web-ifc WASM shipped inside this package's node_modules. Kept local so no
// external CDN is required at runtime (offline / CSP friendly). Trailing slash
// is required by web-ifc's loader.
const WASM_DIR = resolve(SCRIPT_DIR, "node_modules", "web-ifc") + "/";

function printUsage() {
  process.stderr.write(
    "Usage: node ifc_to_fragments.mjs <input.ifc> <output.frag>\n",
  );
}

/**
 * Convert IFC bytes to Fragments bytes.
 * @param {Uint8Array} ifcBytes
 * @returns {Promise<Uint8Array>}
 */
async function convertIfcBytes(ifcBytes) {
  const importer = new FRAGS.IfcImporter();
  // absolute:true tells web-ifc to treat `path` as a filesystem/base path
  // rather than resolving relative to a web origin.
  importer.wasm = { absolute: true, path: WASM_DIR };

  const result = await importer.process({ bytes: ifcBytes });
  // process() may return an ArrayBuffer or a Uint8Array depending on version;
  // normalise to Uint8Array so callers get a consistent, writable buffer.
  const bytes =
    result instanceof Uint8Array ? result : new Uint8Array(result);
  if (!bytes || bytes.byteLength === 0) {
    throw new Error("IfcImporter produced empty fragments output");
  }
  return bytes;
}

async function main() {
  const [inputPath, outputPath] = process.argv.slice(2);
  if (!inputPath || !outputPath) {
    printUsage();
    process.exit(1);
  }

  try {
    await access(inputPath, FS.R_OK);
  } catch {
    process.stderr.write(`Input IFC not readable: ${inputPath}\n`);
    process.exit(2);
  }

  try {
    const ifcBuffer = await readFile(inputPath);
    const ifcBytes = new Uint8Array(
      ifcBuffer.buffer,
      ifcBuffer.byteOffset,
      ifcBuffer.byteLength,
    );

    const fragBytes = await convertIfcBytes(ifcBytes);
    await writeFile(outputPath, fragBytes);

    process.stdout.write(
      `OK ${outputPath} ${fragBytes.byteLength} bytes\n`,
    );
    process.exit(0);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    process.stderr.write(`IFC->Fragments conversion failed: ${message}\n`);
    process.exit(2);
  }
}

main();
