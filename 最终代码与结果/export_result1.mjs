// Reproducible template-preserving XLSX export of numerical solver output.
import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const out = path.resolve(process.argv[2] ?? 'outputs/problem1');
const data = JSON.parse(await fs.readFile(path.join(out, 'xlsx_data.json'), 'utf8'));
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(data.template));
const qa = path.join(out, 'qa');
await fs.mkdir(qa, {recursive: true});
for (const name of ['温度', '水分浓度']) {
  const before = await wb.render({sheetName: name, range: 'A1:F5', scale: 1.5, format: 'png'});
  await fs.writeFile(path.join(qa, `${name}_template.png`), new Uint8Array(await before.arrayBuffer()));
}
if (process.argv.includes('--preview-only')) process.exit(0);
for (const name of ['温度', '水分浓度']) {
  const sheet = wb.worksheets.getItem(name);
  const originalLabel = sheet.getRange('A1').values[0][0];
  sheet.getRange('A1:F5').clear({applyTo: 'contents'});
  const rows = [[originalLabel, ...data.positions_cm],
    ...data.times.map((t, i) => [t, ...data[name][i]])];
  sheet.getRange(`A1:V${rows.length}`).values = rows;
  // Expand the template's numerical headings to all 21 positions.
  sheet.getRange('B1:V1').format.numberFormat = '0.0';
  sheet.getRange(`A2:A${rows.length}`).format.numberFormat = '0';
  sheet.getRange(`B2:V${rows.length}`).format.numberFormat = '0.0000';
  sheet.getRange('B1:V1').format.columnWidth = 11;
  sheet.getRange('A1').format.columnWidth = 32;
  sheet.getRange('A1:V1').format.rowHeight = 28;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(1);
}
wb.recalculate();
for (const name of ['温度', '水分浓度']) {
  console.log((await wb.inspect({kind: 'table', range: `${name}!A1799:V1801`,
    include: 'values,formulas', tableMaxRows: 3, tableMaxCols: 4, maxChars: 1000})).ndjson);
  const image = await wb.render({sheetName: name, range: 'A1:G9', scale: 1.5, format: 'png'});
  await fs.writeFile(path.join(qa, `${name}_output.png`), new Uint8Array(await image.arrayBuffer()));
}
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',
  options:{useRegex:true,maxResults:20}, maxChars:1000})).ndjson);
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out, 'result1.xlsx'));
console.log(`Saved ${path.join(out, 'result1.xlsx')}`);
