const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
map={getCenter:()=>({lat:40.71,lng:-73.98}),getZoom:()=>12.5,setStyle(){},flyTo(){},jumpTo(){},
 resize(){},getSource(){},getLayer(){},addSource(){},addLayer(){},removeLayer(){},removeSource(){},
 setLayoutProperty(){},setPaintProperty(){},on(){},once(){},getCanvas:()=>({style:{}}),getPitch:()=>0,
 easeTo(){},fitBounds(){},getStyle:()=>({layers:[]}),queryRenderedFeatures:()=>[],
 getBounds:()=>({getWest:()=>-74,getSouth:()=>40.6,getEast:()=>-73.9,getNorth:()=>40.8})};

const poly={type:"FeatureCollection",features:[{type:"Feature",properties:{drawn:true},
  geometry:{type:"Polygon",coordinates:[[[-74,40.7],[-73.9,40.7],[-73.9,40.8],[-74,40.8],[-74,40.7]]]}}]};
const A=addLayer("Drawn area",poly,{fit:false,src:{d:{type:"Polygon"}}});
const B=addLayer("Walkshed",{type:"FeatureCollection",features:[]},{fit:false,src:{t:"walkshed",p:{area:{ref:A.id},minutes:15}}});

// audit must name the STEP a layer came from, not an index into a list the user can shuffle
let st=auditSteps();
ok("audit records both steps", st.length===2);
ok("audit names the drawn origin", /drawn as a polygon/.test(st[0].how));
ok("audit resolves the dependency by name", /area = step 1 \(Drawn area\)/.test(st[1].how));

// reorder the panel: lineage must NOT follow position
layers.reverse();
st=auditSteps();
ok("reorder does not corrupt lineage", /area = step 1 \(Drawn area\)/.test(st[1].how) && st[0].name==="Drawn area");

// a share link must emit a dependency before the thing that needs it
const {st:share,skipped}=shareState();
ok("share emits both", share.l.length===2 && skipped===0);
ok("dependency emitted first", share.l[0].d && share.l[1].t==="walkshed");
ok("ref rewritten to a position", share.l[1].p.area.$===0);
ok("plain params survive", share.l[1].p.minutes===15);
ok("hash round trips", JSON.parse(unb64(b64(JSON.stringify(share)))).l.length===2);

// an uploaded layer has no recipe, so it must be declared skipped, never faked
addLayer("From file",{type:"FeatureCollection",features:[]},{fit:false,src:{f:"parcels.geojson"}});
ok("upload declared skipped, not invented", shareState().skipped===1);
ok("upload still shown in audit", /imported from the file parcels.geojson/.test(auditSteps().find(x=>x.name==="From file").how));

// history refuses honestly when Esri is unreachable
waybackErr=new Error("network");
ok("history admits the failure", /Could not load the list of imagery dates/.test(historyPanel()));
waybackErr=null; wayback=[{date:"2014-02-20",url:"u1"},{date:"2026-06-01",url:"u2"}];
const h=historyPanel();
ok("history shows the real range", h.includes("2014-02-20") && h.includes("2026-06-01") && h.includes("2 dated snapshots"));

// basemap state has to be single-sourced or a share link lies about what was seen
setBasemapTo("satellite");
ok("basemap tracked for sharing", curBasemap==="satellite" && shareState().st.v[3]==="satellite");
layers.length=0;
ok("audit empty state teaches next action", /Run a tool, draw an area/.test(auditPanel()));
