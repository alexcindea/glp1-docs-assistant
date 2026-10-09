// Copy the files pdf.js fetches at runtime into public/pdfjs, from the installed
// pdfjs-dist, so they always match its version. Runs before `dev` and `build`.
//
// - wasm/: image decoders. Scanned pages are often JBIG2 or JPEG 2000 images;
//   without these, pdf.js draws them blank. JBIG2 is PDFium's (BSD-3-Clause) in
//   a pdf.js wrapper (Apache-2.0); OpenJPEG is BSD-2-Clause; qcms is MIT.
// - standard_fonts/: the standard PDF fonts (Foxit's, from PDFium: BSD), for
//   files that don't embed them.
//
// Left out: quickjs (runs JavaScript inside PDFs, which the viewer never
// enables, and ships without a licence file here) and the Liberation fonts,
// which are GPLv2 (with a font exception) and so break the no-copyleft rule;
// pdf.js falls back to another font without them. Licence files of what is
// copied go along.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED = /^(quickjs|Liberation|LICENSE_LIBERATION)/;

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
    filter: (path) => !EXCLUDED.test(basename(path)),
  });
}
console.log(`pdf.js assets copied to ${target}`);
