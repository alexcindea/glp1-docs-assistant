// Copy the files pdf.js fetches at runtime into public/pdfjs, from the installed
// pdfjs-dist, so they always match its version. Runs before `dev` and `build`.
//
// - wasm/: image decoders. Scanned pages are often JBIG2 or JPEG 2000 images;
//   without these, pdf.js draws them blank. (JBIG2 from PDFium, BSD-3-Clause;
//   OpenJPEG, BSD-2-Clause; qcms, MIT; licence files are copied along.)
// - standard_fonts/: the 14 standard PDF fonts, for files that don't embed them.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const source = dirname(require.resolve("pdfjs-dist/package.json"));
const target = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "public",
  "pdfjs",
);

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const folder of ["wasm", "standard_fonts"]) {
  cpSync(join(source, folder), join(target, folder), {
    recursive: true,
    // quickjs runs JavaScript embedded in PDFs, which the viewer never enables.
    filter: (path) => !basename(path).startsWith("quickjs"),
  });
}
console.log(`pdf.js assets copied to ${target}`);
