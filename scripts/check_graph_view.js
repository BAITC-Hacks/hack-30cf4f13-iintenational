const fs = require("fs");

const html = fs.readFileSync("outputs/graph_view.html", "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) throw new Error("script missing");
new Function(match[1]);

for (const marker of ['id="gid"', 'id="mode"', "drawArrow", "clusterColor", "гипотезы"]) {
  if (!html.includes(marker)) throw new Error(`missing ${marker}`);
}
console.log("graph_view JavaScript and controls: OK");
