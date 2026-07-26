const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
const recipe={tool:"notice_list",distance_ft:200,parcels_notified:3,
  owner_field:"OWNER",address_field:"MAIL_ADDR",subject_parcels_excluded:1,
  notice_list:[{owner:"Ann Reyes",address:"12 Elm St",distance_ft:14.2},
               {owner:"nan",address:"nan",distance_ft:41.0},
               {owner:'Smith, "Bo" & Co',address:"9 Oak Ave, Apt 2",distance_ft:88.5}],
  note:"1 of these parcels have no owner recorded in the data. Confirm the radius against your local ordinance."};
const h=listHTML(recipe);
ok("the list is shown as a list", /Who has to be notified/.test(h)&&/Ann Reyes/.test(h));
ok("it says how many and how far", /3 properties within 200 feet/.test(h));
ok("a missing owner reads as missing, not as 'nan'", /not recorded/.test(h)&&!/>nan</.test(h));
ok("the ordinance warning survives to the screen", /ordinance/.test(h));
ok("there is a way to take it away", /Download the list as a spreadsheet/.test(h));
ok("nothing renders when there is no list", listHTML({tool:"buffer"})==="");

// the CSV is the artifact that gets mailed, so quoting has to be right
let saved=null;
globalThis.URL={createObjectURL:b=>{saved=b;return "blob:x"},revokeObjectURL(){}};
globalThis.Blob=class{constructor(parts){this.text=parts.join("")}};
document.createElement=()=>({set href(v){},set download(v){this._d=v},click(){},remove(){}});
document.body={appendChild(){},removeChild(){}};
wireList(recipe); $("noticeCsv").onclick();
const csv=saved.text, lines=csv.split("\n");
ok("csv has a header and every row", lines.length===4);
ok("a name containing a comma and quotes is escaped", lines[3].startsWith('"Smith, ""Bo"" & Co"'));
ok("an address containing a comma is quoted", /"9 Oak Ave, Apt 2"/.test(lines[3]));
ok("'nan' does not reach the spreadsheet", !/nan/.test(csv));
ok("distances are carried through", /14.2/.test(csv)&&/88.5/.test(csv));
