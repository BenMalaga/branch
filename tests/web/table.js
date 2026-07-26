const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
let selData=null, fitted=null;
map={getCenter:()=>({lat:40.71,lng:-73.98}),getZoom:()=>12.5,setStyle(){},jumpTo(){},resize(){},
 getSource:id=>id==="tblsel"?{setData:d=>{selData=d}}:null,getLayer:()=>true,
 addSource(){},addLayer(){},removeLayer(){},removeSource(){},setLayoutProperty(){},setPaintProperty(){},
 on(){},once(){},getCanvas:()=>({style:{}}),fitBounds:b=>{fitted=b},getStyle:()=>({layers:[]}),
 getBounds:()=>({getWest:()=>-74,getSouth:()=>40.6,getEast:()=>-73.9,getNorth:()=>40.8})};
const gj={type:"FeatureCollection",features:[
  {type:"Feature",properties:{name:"A",pop:10},geometry:{type:"Point",coordinates:[-73.98,40.71]}},
  {type:"Feature",properties:{name:"B",pop:30},geometry:{type:"Point",coordinates:[-73.95,40.75]}},
  {type:"Feature",properties:{name:"C",pop:20},geometry:{type:"Point",coordinates:[-73.90,40.70]}}]};
const L=addLayer("Sites",gj,{fit:false});
tableState={id:L.id,col:null,dir:1};

// selecting a row must put THAT feature on the map, not the row's screen position
selectRow(L,1,false);
ok("selection carries the right feature", selData.features.length===1 && selData.features[0].properties.name==="B");
ok("selection does not move the map", fitted===null);
selectRow(L,2,true);
ok("double click frames the feature", fitted!==null && selData.features[0].properties.name==="C");
clearSelection();
ok("clearing empties the highlight", selData.features.length===0 && selRow===null);

// sorting changes what row 1 shows, so the row must carry the FEATURE index,
// not its position in the sorted view, or clicking highlights the wrong shape
tableState={id:L.id,col:"pop",dir:-1};
const html=tableHTML(L);
const picks=[...html.matchAll(/data-pick="(\d+)"/g)].map(m=>+m[1]);
ok("sorted rows keep their true feature index", JSON.stringify(picks)===JSON.stringify([1,2,0]));
const firstCell=/data-pick="1"[^>]*>\s*<td>([^<]*)/.exec(html);
ok("descending sort really is descending", firstCell && firstCell[1]==="B");
selectRow(L,picks[0],false);
ok("clicking the top sorted row selects B", selData.features[0].properties.name==="B");
