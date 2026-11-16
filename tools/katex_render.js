// Render LaTeX math spans to KaTeX HTML.
// Reads a JSON array of expressions on stdin, writes a JSON array of HTML strings.
const katex = require("katex");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  let items = [];
  try {
    items = JSON.parse(input);
  } catch (e) {
    process.stderr.write("bad input: " + e.message);
    process.exit(1);
  }
  const out = items.map(({ tex, display }) => {
    try {
      return katex.renderToString(tex, {
        displayMode: !!display,
        throwOnError: false,
        strict: false,
        output: "html",
      });
    } catch (e) {
      return "";
    }
  });
  process.stdout.write(JSON.stringify(out));
});
