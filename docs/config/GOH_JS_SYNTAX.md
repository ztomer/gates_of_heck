# GOH_JS_SYNTAX
Area: structural
Kind: setting
Order: 310
Default: unset (check skipped)

Set to `on` to run `goh js-syntax` in the structural pipeline: every JavaScript source this
repository TRACKS (`.js`, `.mjs`, `.cjs`) must parse, **with the goal the BROWSER gives that file**.
**THE GOAL COMES FIRST, AND IT IS THE BROWSER'S**: a `.js` file is a CLASSIC SCRIPT -- what a
browser's `<script src>` is, and the goal that REFUSES a top-level `return`, a top-level `await`,
`import` and `export`; a `.mjs` file is a MODULE -- what a `<script type="module">` is, where
`import`, `export` and a top-level `await` are legal; a `.cjs` file is node's COMMONJS, which no
browser loads, where a top-level `return` is legal. **THE GOAL COMES FROM THE NAME**, because a
browser's goal is decided by the HTML that loads the file and the HTML is not in the repository: a
file that carries `import` in a `.js` file is a module that no classic script can load, and a module
is named `.mjs`. **TWO ENGINES, IN THE HOUSE'S ORDER**: node is the PRIMARY engine and reaches the
browser's goals exactly without running anything (`vm.Script` IS the classic-script goal,
`vm.compileFunction` is the CommonJS goal, `node --check` on a `.mjs` file is the module goal), and
macOS's `JavaScriptCore` (`jsc`) is the FALLBACK for a machine with nothing installed -- it reaches
the classic goal in TWO probes, because `new Function` builds a function BODY and a function body
may `return` at its top level where a classic script may not. **THE SCOPE IS PRINTED**: the count,
the shape of the paths, every path the vendor/build list took out, and the GOAL each source was
judged with, because an exclusion can hide the subject and a silent scope reads like a clean tree.
**A TEMPLATE IS JUDGED BY ITS SUBSTITUTED FORM**: a file the tree's own convention names as a
template (`templates_*.js`, `*.tmpl.js`, `*.template.js`) carries placeholders, and each is replaced
with a neutral expression (`0`) before the parse -- excluding a template instead would hide the
subject, which is the reason the gate exists. Only the placeholders the estate's own convention
defines are replaced (`${NAME}`, `@NAME@`), so a hole holding an EXPRESSION (`${window.TIMEOUT}`) is
left alone and is still judged by the parse; a malformed hole is left alone too, and the parse fails
on it. **A SOURCE THE ENGINE CANNOT READ IS REFUSED** (exit 2), never passed: the `JavaScriptCore`
fallback parses a module only by RUNNING it, and a gate does not run the repository's code, so a
module source is named as unmeasured (node reads the module goal). Full scope only: the check
answers "is this tree's JavaScript sound", and the tree is what a push carries. Any other value is
exit 2 NAMING THIS KEY, never a silent off.
