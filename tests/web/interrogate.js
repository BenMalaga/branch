const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
map={getCenter:()=>({lat:40.71,lng:-73.98}),getZoom:()=>12.5,setStyle(){},jumpTo(){},resize(){},
 getSource(){},getLayer:()=>true,addSource(){},addLayer(){},removeLayer(){},removeSource(){},
 setLayoutProperty(){},setPaintProperty(){},on(){},once(){},getCanvas:()=>({style:{}}),fitBounds(){},
 getStyle:()=>({layers:[]}),getPitch:()=>0,easeTo(){},project:c=>({x:c[0],y:c[1]}),
 getBounds:()=>({getWest:()=>-74,getSouth:()=>40.6,getEast:()=>-73.9,getNorth:()=>40.8})};

// geometry the popup relies on: a Manhattan block is ~ the right order of magnitude
const block={type:"Polygon",coordinates:[[[-73.9800,40.7500],[-73.9790,40.7500],
  [-73.9790,40.7508],[-73.9800,40.7508],[-73.9800,40.7500]]]};
const a=geomAreaM2(block);
ok("polygon area is plausible ("+Math.round(a)+" m2)", a>6000 && a<10000);
const withHole={type:"Polygon",coordinates:[block.coordinates[0],
  [[-73.9798,40.7502],[-73.9794,40.7502],[-73.9794,40.7505],[-73.9798,40.7505],[-73.9798,40.7502]]]};
ok("a courtyard is subtracted, not added", geomAreaM2(withHole) < a);
ok("multipolygon sums", Math.abs(geomAreaM2({type:"MultiPolygon",coordinates:[block.coordinates,block.coordinates]})-2*a)<1);
ok("a line is not an area", geomAreaM2({type:"LineString",coordinates:[[0,0],[1,1]]})===0);
const len=geomLenM({type:"LineString",coordinates:[[-73.98,40.75],[-73.97,40.75]]});
ok("length is plausible ("+Math.round(len)+" m)", len>800 && len<900);

// the popup must translate, and must stay quiet where Overture has nothing
let shown=null;
globalThis.maplibregl={Popup:class{constructor(){}setLngLat(){return this}
  setHTML(h){shown=h;return this} addTo(){return this}}};
overlayPopup({lngLat:[0,0],features:[{properties:{"@name":"Chrysler Building",subtype:"commercial",height:318},
  geometry:block,layer:{"source-layer":"building"}}]},{label:"Buildings"});
ok("popup names the feature", /Chrysler Building/.test(shown));
ok("popup translates height to floors", /318 m, about 91 floors/.test(shown));
ok("popup reports footprint in both units", /footprint/.test(shown)&&/acres/.test(shown));
ok("popup estimates floor area", /floor area, roughly/.test(shown));
overlayPopup({lngLat:[0,0],features:[{properties:{},geometry:{type:"Point",coordinates:[0,0]},
  layer:{"source-layer":"building"}}]},{label:"Buildings"});
ok("no data means saying so, not an empty table", /publishes no further detail/.test(shown)&&!/footprint/.test(shown));

// the wrong-EPSG guard: valid numbers, wrong hemisphere
const nyc={features:[{geometry:{type:"Point",coordinates:[-73.98,40.71]}}]};
const gulf={features:[{geometry:{type:"Point",coordinates:[0.0,0.0]}}]};
ok("local data raises nothing", farFromView(nyc)===null);
const f=farFromView(gulf);
ok("a file in the Gulf of Guinea is flagged", f && f.km>8000);
ok("the flag is a warning, not a block", typeof f.lon==="number");
