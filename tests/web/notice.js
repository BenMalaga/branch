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

// A spreadsheet runs a cell starting with = + - or @. Owner names come out of an
// assessor's database and are not trusted text.
const nasty={...recipe,notice_list:[
  {owner:'=HYPERLINK("http://x","click")',address:"1 Main St",distance_ft:5},
  {owner:"+1-555-0100",address:"2 Oak",distance_ft:6},
  {owner:"-Smith",address:"@3 Elm",distance_ft:7},
  {owner:"Ann Reyes",address:"3 MAIN ST\rSUITE 4",distance_ft:8}]};
wireList(nasty); $("noticeCsv").onclick();
const c2=saved.text, l2=c2.split("\n");
ok("a formula in an owner name is neutralised", /^"'=HYPERLINK/.test(l2[1]));
ok("a leading + is neutralised", /'\+1-555-0100/.test(c2));
ok("a leading - is neutralised", /'-Smith/.test(c2));
ok("a leading @ in an address is neutralised", /'@3 Elm/.test(c2));
ok("a bare carriage return is quoted, not a new row",
   /"3 MAIN ST\rSUITE 4"/.test(c2) && l2.length===5);
