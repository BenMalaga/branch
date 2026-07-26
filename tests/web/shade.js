const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
const gj={type:"FeatureCollection",features:[
 {type:"Feature",properties:{Ward:"E",FID:1,OBJECTID:11,Shape_Area:38086601.6,
   Shape__Length:10827.5,count:8,acres:1661.2,per_acre:0.0048,GLOBALID:"a"},
  geometry:{type:"Polygon",coordinates:[[[-74.8,40.2],[-74.7,40.2],[-74.7,40.3],[-74.8,40.2]]]}},
 {type:"Feature",properties:{Ward:"N",FID:2,OBJECTID:12,Shape_Area:20000000.0,
   Shape__Length:9000.1,count:7,acres:874.0,per_acre:0.0080,GLOBALID:"b"},
  geometry:{type:"Polygon",coordinates:[[[-74.7,40.2],[-74.6,40.2],[-74.6,40.3],[-74.7,40.2]]]}}]};
const f=shadeableFields(gj);
ok("real measures are offered", f.includes("count")&&f.includes("per_acre")&&f.includes("acres"));
ok("identifiers are not offered", !f.includes("FID")&&!f.includes("OBJECTID")&&!f.includes("GLOBALID"));
ok("the source's own bookkeeping is not offered",
   !f.includes("Shape_Area")&&!f.includes("Shape__Length"));
ok("text columns are not offered", !f.includes("Ward"));
ok("the list is stable and sorted", JSON.stringify(f)===JSON.stringify([...f].sort()));

// a column that never varies makes a flat map, which says nothing
const flat={type:"FeatureCollection",features:[
 {type:"Feature",properties:{same:5,varies:1},geometry:{type:"Point",coordinates:[0,0]}},
 {type:"Feature",properties:{same:5,varies:9},geometry:{type:"Point",coordinates:[1,1]}}]};
const f2=shadeableFields(flat);
ok("a constant column is not offered", !f2.includes("same")&&f2.includes("varies"));
ok("a layer with no measures offers nothing",
   shadeableFields({type:"FeatureCollection",features:[
     {type:"Feature",properties:{name:"a"},geometry:{type:"Point",coordinates:[0,0]}}]}).length===0);

// the automatic pick must still be the sensible one, and be overridable
map={addSource(){},addLayer(){},getSource(){},getLayer(){},removeLayer(){},removeSource(){},
 on(){},setPaintProperty(){},setLayoutProperty(){},fitBounds(){},getCanvas:()=>({style:{}}),
 getBounds:()=>({getWest:()=>-75,getSouth:()=>40,getEast:()=>-74,getNorth:()=>41}),
 getStyle:()=>({layers:[]}),getCenter:()=>({lat:40,lng:-74}),getZoom:()=>10};
const L=addLayer("Wards",gj,{fit:false});
ok("it shades by count without being asked", L.field==="count");
L.shadeBy="per_acre"; removeLayerPaint(L); drawLayer(L);
ok("an explicit choice wins", L.field==="per_acre");
L.shadeBy=null; removeLayerPaint(L); drawLayer(L);
ok("'one colour' really means no shading", L.field===null);
