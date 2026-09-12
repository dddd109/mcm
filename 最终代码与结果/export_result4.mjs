// Official template: fixed physical distances, then the moving true surface.
// Outside positions are blank, never extrapolated. No images are generated.
import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';
const out=path.resolve(process.argv[2] ?? 'outputs/problem4');
const data=JSON.parse(await fs.readFile(path.join(out,'xlsx_data.json'),'utf8'));
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(data.template));
const sheet=wb.worksheets.getItemAt(0);
sheet.getRange('A1:F5').clear({applyTo:'contents'});
const n=data.times.length+1;
const cols=data.positions_cm.length+2;
sheet.getRangeByIndexes(0,0,1,cols).values=[[data.header_label,...data.positions_cm,data.surface_label]];
sheet.getRangeByIndexes(1,0,n-1,cols).values=data.times.map((t,i)=>[t,...data.values[i]]);
sheet.getRangeByIndexes(0,1,1,cols-2).format.numberFormat='0.0';
sheet.getRangeByIndexes(1,0,n-1,1).format.numberFormat='0';
sheet.getRangeByIndexes(1,1,n-1,cols-1).format.numberFormat='0.0000';
sheet.getRange('A1').format.columnWidth=32;
sheet.getRangeByIndexes(0,1,1,cols-1).format.columnWidth=11;
sheet.freezePanes.freezeRows(1);sheet.freezePanes.freezeColumns(1);
wb.recalculate();
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out,'result4.xlsx'));
console.log(`Saved ${path.join(out,'result4.xlsx')}`);
