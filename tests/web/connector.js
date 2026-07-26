const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
map={getBounds:()=>({getWest:()=>-75.5,getSouth:()=>39.5,getEast:()=>-73.5,getNorth:()=>41.5}),
 getCenter:()=>({lat:40.5,lng:-74.5}),getZoom:()=>10,getCanvas:()=>({style:{}}),on(){},once(){},
 addSource(){},addLayer(){},getSource(){},getLayer(){},resize(){},setStyle(){},jumpTo(){},fitBounds(){},
 getStyle:()=>({layers:[]})};
tools=[{id:"arcgis",title:"Bring in a layer your town already publishes",noun:"Local layer",
  description:"Pull parcels...",category:"map data",returns:"layer",
  params:{type:"object",required:["url","bbox"],properties:{
    url:{type:"string",description:"the layer URL"},
    bbox:{type:"array",maxItems:4,items:{type:"number"}},
    where:{type:"string",default:"1=1"},
    limit:{type:"integer",default:2000}}}}];
$("panelBody").innerHTML=""; tab="tools";
openTool("arcgis");
const body=$("panelBody").innerHTML;
ok("the connector form renders", /Bring in a layer/.test(body));
// the button is inserted next to the url field, so it lands on that element in
// the stub rather than in the panel's html string. Assert the contract, not the
// stub's flattened DOM: the button exists and is wired.
ok("a URL field gets a look-first button", /Check this layer first/.test($("f_url").innerHTML));
ok("the look-first button is wired", typeof $("peek").onclick==="function");
ok("bbox becomes a map-view button", /Use the current map view/.test(body));

// a service that does not reach the view must SAY so, not hand over an empty layer
let reply;
globalThis.fetch=async()=>({json:async()=>reply});
reply={result:{name:"Hunterdon Parcels",geometry_type:"esriGeometryPolygon",
  extent:[-75.2,40.3,-74.7,40.7],fields:[{name:"OWNER",type:"String"},{name:"ACRES",type:"Double"}],
  description:"Tax parcels."}};
await peekLayer();
ok("it names the layer and its columns", /Hunterdon Parcels/.test($("peekOut").innerHTML)
   && /OWNER/.test($("peekOut").innerHTML) && /ACRES/.test($("peekOut").innerHTML));
ok("coverage in view is confirmed", /covers the current view/.test($("peekOut").innerHTML));

reply={result:{name:"Trenton Parcels",geometry_type:"esriGeometryPolygon",
  extent:[-74.80,40.19,-74.72,40.25],fields:[]}};
map.getBounds=()=>({getWest:()=>-118.5,getSouth:()=>33.9,getEast:()=>-118.1,getNorth:()=>34.2});
await peekLayer();
ok("a Trenton service viewed from LA is refused", /does not reach where you are looking/.test($("peekOut").innerHTML));

reply={result:{name:"Mystery",geometry_type:"esriGeometryPoint",extent:null,fields:[]}};
await peekLayer();
ok("no published extent is admitted, not assumed", /cannot check coverage/.test($("peekOut").innerHTML));

reply={error:"That does not look like an ArcGIS layer URL."};
await peekLayer();
ok("a bad URL surfaces the real reason", /does not look like an ArcGIS layer URL/.test($("peekOut").innerHTML));
