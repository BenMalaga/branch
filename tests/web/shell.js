const store={};
Object.defineProperty(globalThis,"localStorage",{configurable:true,value:{
  getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=String(v)},removeItem:k=>{delete store[k]}}});
tools=JSON.parse(require('fs').readFileSync('/tmp/tools.json','utf8')).map(t=>({...t,noun:t.noun||t.title}));
loadLayout(); applyLayout();
map={getCenter:()=>({lat:40.71,lng:-73.98}),getZoom:()=>12.5,setStyle(){},flyTo(){},jumpTo(){},
  resize(){},getSource(){},getLayer(){},addSource(){},addLayer(){},removeLayer(){},removeSource(){},
  setLayoutProperty(){},setPaintProperty(){},on(){},getCanvas:()=>({style:{}}),getPitch:()=>0,
  easeTo(){},fitBounds(){},getStyle:()=>({layers:[]}),queryRenderedFeatures:()=>[],
  getBounds:()=>({getWest:()=>-74,getSouth:()=>40.6,getEast:()=>-73.9,getNorth:()=>40.8})};

// --- Ask renders, keeps a thread, and shows receipts ---
tab="ask"; renderPanel();
console.log("ask panel        :", /Ask the map/.test(document.getElementById("panelBody").innerHTML));
thread=[{role:"you",text:"how many restaurants in the bronx"},
        {role:"branch",text:"237 are inside the county line.",
         steps:[{tool:"boundary",recipe:{name:"Bronx County"}},{tool:"osm",feature_count:337,recipe:{}}],
         receipts:{checked:2,traced:2,clean:true,orphans:[]}}];
renderPanel();
let h=document.getElementById("panelBody").innerHTML;
console.log("thread rendered  :", /237 are inside/.test(h));
console.log("receipts badge   :", /All 2 figures here came from a tool run/.test(h));
console.log("steps shown      :", /2 steps/.test(h) && /337 features/.test(h));
thread.push({role:"branch",text:"median income is $41,895",
  receipts:{checked:1,traced:0,clean:false,orphans:[{value:"$41,895",phrase:"median income is $41,895"}]}});
renderPanel();
console.log("orphan flagged   :", /0 of 1 figures traced/.test(document.getElementById("panelBody").innerHTML));

// --- the assistant acting on the workspace ---
let acted=[];
map.setStyle=()=>acted.push("basemap"); map.flyTo=()=>acted.push("fly");
active.clear();
const realToggle=globalThis.toggleOverlay;
globalThis.toggleOverlay=id=>{ acted.push("overlay:"+id); active.add(id); };
applyWorkspaceActions([
  {tool:"workspace",recipe:{action:"show_overlay",target:"buildings"}},
  {tool:"workspace",recipe:{action:"set_basemap",target:"satellite"}},
  {tool:"workspace",recipe:{action:"fly_to",lon:-73.9,lat:40.8,zoom:14}},
  {tool:"workspace",recipe:{action:"set_dock",target:"bottom"}},
  {tool:"osm",recipe:{}},
]);
console.log("actions carried out:", acted.join(", "));
console.log("dock moved by the assistant:", layout.dock);

// --- Saved work round trip ---
layers=[{id:"l1",name:"Density",color:"#8ed3a6",opacity:1,visible:true,geojson:{type:"FeatureCollection",features:[{type:"Feature",properties:{},geometry:{type:"Point",coordinates:[-73.9,40.7]}}]}}];
tab="saved"; renderPanel();
document.getElementById("saveName").value="Bronx study";
document.getElementById("doSave").onclick();
console.log("saved            :", loadSaved().length, "|", loadSaved()[0].name, "|", loadSaved()[0].layers.length, "layer");
console.log("NEXT SHELL OK");
