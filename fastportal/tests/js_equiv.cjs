// 주석 걷기 검사용: 두 JS 가 주석·공백만 다른지 — TypeScript 파서(agent-page-web/node_modules 에 이미 있음, 새 의존성 아님)로 AST 를 비교.
// 사용: node js_equiv.cjs <typescript 경로> <a.js> <b.js>  → 같으면 exit 0, 다르면 1(첫 차이 출력)
const [tsPath, a, b] = process.argv.slice(2);
const ts = require(tsPath);
const fs = require("fs");
function shape(file) {
  const src = fs.readFileSync(file, "utf8");
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const out = [];
  (function walk(n) {
    const kids = [];
    ts.forEachChild(n, (c) => { kids.push(c); });
    out.push(ts.SyntaxKind[n.kind] + (kids.length ? "" : ":" + n.getText(sf)));
    kids.forEach(walk);
  })(sf);
  return { out, diags: (sf.parseDiagnostics || []).length };
}
const x = shape(a), y = shape(b);
if (x.diags !== y.diags) { console.log("parse diagnostics", x.diags, "!=", y.diags); process.exit(1); }
for (let i = 0; i < Math.max(x.out.length, y.out.length); i++) {
  if (x.out[i] !== y.out[i]) { console.log("diff at", i, JSON.stringify(x.out[i]), JSON.stringify(y.out[i])); process.exit(1); }
}
console.log("same", x.out.length, "nodes", x.diags, "diags");
