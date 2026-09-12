// Fill the official problem-2 template. No figures or preview images requested.
import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';

const out = path.resolve(process.argv[2] ?? 'outputs/problem2');
const data = JSON.parse(await fs.readFile(path.join(out, 'xlsx_data.json'), 'utf8'));
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(data.template));
for (const name of ['温度', '水分浓度']) {
  const sheet = wb.worksheets.getItem(name);
  const label = sheet.getRange('A1').values[0][0];
  const n = data.times.length + 1;
  sheet.getRange('A1:F5').clear({applyTo:'contents'});
  sheet.getRange('A1:V1').values = [[label, ...data.positions_cm]];
  for(let i=0;i<data.times.length;i+=1000){
    const end=Math.min(i+1000,data.times.length);
    const rows=data.times.slice(i,end).map((t,j)=>[t,...data[name][i+j]]);
    sheet.getRange(`A${i+2}:V${end+1}`).values=rows;
  }
  sheet.getRange('B1:V1').format.numberFormat='0.0';
  sheet.getRange(`A2:A${n}`).format.numberFormat='0';
  sheet.getRange(`B2:V${n}`).format.numberFormat='0.0000';
  sheet.getRange('B1:V1').format.columnWidth=11;
  sheet.getRange('A1').format.columnWidth=32;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(1);
}
wb.recalculate();
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out,'result2.xlsx'));
console.log(`Saved ${path.join(out,'result2.xlsx')}`);
