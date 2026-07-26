const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
const res={found:4,usable:2,note:"2 of 4 are readable without a login, and 1 reaches where you are looking.",
 results:[
  {name:"Mercer Parcels",org:"Mercer County GIS",summary:"Tax parcels.",open:true,covers:true,
   geometry_type:"esriGeometryPolygon",fields:["OWNER","ACRES","LANDUSE"],
   url:"https://services.arcgis.com/a/arcgis/rest/services/Parcels/FeatureServer/0"},
  {name:"Trenton Township Zoning",org:"Some Other State",open:true,covers:false,
   geometry_type:"esriGeometryPolygon",fields:["ZONE"],
   url:"https://services.arcgis.com/b/arcgis/rest/services/Z/FeatureServer/0"},
  {name:"County Parcels (internal)",org:"County",open:false,reason:"it needs a login",
   url:"https://services.arcgis.com/c/arcgis/rest/services/P/FeatureServer/0"}]};
const h=searchHTML(res);
ok("results are listed with their publisher", /Mercer Parcels/.test(h)&&/Mercer County GIS/.test(h));
ok("the honest tally is shown", /2 of 4 are readable/.test(h));
ok("a layer that fits is marked", /covers your view/.test(h));
ok("a same-named layer elsewhere is marked", /somewhere else/.test(h));
ok("a locked layer says why, and offers no button", /needs a login/.test(h)
   && !/data-bring="2"/.test(h));
ok("open layers get a button", /data-bring="0"/.test(h)&&/data-bring="1"/.test(h));
ok("columns are previewed", /OWNER/.test(h)&&/LANDUSE/.test(h));
ok("status uses the pill component, not .tag", /class="pill/.test(h)&&!/class="tag/.test(h));
ok("empty search renders nothing", searchHTML({results:[]})==="");

// clicking through must fetch the real service and land a layer
map={getBounds:()=>({getWest:()=>-74.82,getSouth:()=>40.19,getEast:()=>-74.70,getNorth:()=>40.26}),
 getCenter:()=>({lat:40.22,lng:-74.76}),getZoom:()=>13,addSource(){},addLayer(){},getSource(){},
 getLayer(){},on(){},once(){},getCanvas:()=>({style:{}}),fitBounds(){},resize(){},
 getStyle:()=>({layers:[]}),setPaintProperty(){},setLayoutProperty(){}};
let asked=null;
callTool=async(id,p)=>{ asked={id,p};
  return {result:{type:"FeatureCollection",features:[{type:"Feature",properties:{OWNER:"A"},
    geometry:{type:"Point",coordinates:[-74.76,40.22]}}]},recipe:{tool:id}}; };
$("dockBody").innerHTML=h;
wireSearchResults();
await $("dockBody").querySelectorAll("[data-bring]")[0].onclick();
ok("it calls the connector with that URL", asked&&asked.id==="arcgis"
   && /Parcels\/FeatureServer\/0$/.test(asked.p.url));
ok("it asks only for the current view", JSON.stringify(asked.p.bbox)==="[-74.82,40.19,-74.7,40.26]");
ok("the layer lands with its real name", layers[0]&&layers[0].name==="Mercer Parcels");
ok("lineage records where it came from", layers[0].src.t==="arcgis"&&layers[0].src.p.url===asked.p.url);
