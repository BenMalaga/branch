const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
map={fitBounds(){},addSource(){},addLayer(){},getSource(){},getLayer(){},removeLayer(){},
 removeSource(){},on(){},setPaintProperty(){},setLayoutProperty(){},getCanvas:()=>({style:{}}),
 getStyle:()=>({layers:[]}),getCenter:()=>({lat:40.73,lng:-73.99}),getZoom:()=>15,
 getBounds:()=>({getWest:()=>-74,getSouth:()=>40.7,getEast:()=>-73.9,getNorth:()=>40.8})};
const fc=n=>({type:"FeatureCollection",features:Array.from({length:n},(_,i)=>
  ({type:"Feature",properties:{},geometry:{type:"Point",coordinates:[-73.99,40.73]}}))});

// the happy path
let calls=[];
callTool=async(id)=>{ calls.push(id); return {result:fc(5),recipe:{tool:id}}; };
layers.length=0; await runExample();
ok("the example runs the full chain", JSON.stringify(calls)===JSON.stringify(["osm","density_hexbin"]));
ok("it leaves two layers on the map", layers.length===2);
ok("the layers carry lineage", layers.some(l=>l.src&&l.src.t==="osm"));

// Overpass down: the visitor must still see something work
calls=[]; layers.length=0;
callTool=async(id)=>{ calls.push(id);
  if(id==="osm") throw new Error("Overpass did not answer");
  return {result:fc(9),recipe:{tool:id}}; };
await runExample();
ok("it falls back to another provider", calls.includes("census_geo"));
ok("the visitor still gets a layer", layers.length===1);
ok("it says which service was down and that it is not their fault",
   /Overpass service is not answering/.test($("dockBody").innerHTML)
   && /neither is something branch controls/.test($("dockBody").innerHTML));

// everything down: say so plainly, do not show a stack trace
calls=[]; layers.length=0;
callTool=async()=>{ throw new Error("connection refused"); };
await runExample();
// the title lives in the dock header, not the body
ok("total outage is stated plainly", /Both services are down/.test($("dockTitle").textContent)
   && /could not reach OpenStreetMap or the US Census/.test($("dockBody").innerHTML));
ok("it points at what still works offline", /buffer and clip/.test($("dockBody").innerHTML));
ok("no layer is invented when nothing loaded", layers.length===0);
