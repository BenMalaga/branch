const ok=(n,c)=>console.log((c?"PASS":"FAIL")+"  "+n);
map={getCenter:()=>({lat:40.71,lng:-73.98}),getZoom:()=>12.5,setStyle(){},jumpTo(){},resize(){},
 getSource(){},getLayer(){},addSource(){},addLayer(){},removeLayer(){},removeSource(){},setLayoutProperty(){},
 setPaintProperty(){},on(){},once(){},getCanvas:()=>({style:{}}),fitBounds(){},getStyle:()=>({layers:[]}),
 getBounds:()=>({getWest:()=>-74,getSouth:()=>40.6,getEast:()=>-73.9,getNorth:()=>40.8})};
const calls=[];
callTool=async(id,params)=>{ calls.push({id,params});
  return {result:{type:"FeatureCollection",features:[{type:"Feature",properties:{},geometry:null}]},recipe:{tool:id}}; };
const shared={v:[40.71,-73.98,12,"dark"],l:[
  {n:"Drawn area",d:{type:"Polygon",coordinates:[[[-74,40.7],[-73.9,40.7],[-73.9,40.8],[-74,40.7]]]}},
  {n:"Walkshed",t:"walkshed",p:{area:{$:0},minutes:15}}]};
replayState(shared).then(()=>{
  ok("replay re-ran the tool, not a snapshot", calls.length===1 && calls[0].id==="walkshed");
  ok("the drawn shape was passed as real input", calls[0].params.area.type==="FeatureCollection");
  ok("plain param carried through", calls[0].params.minutes===15);
  ok("both layers rebuilt", layers.length===2);
  ok("rebuilt lineage is intact for re-sharing", shareState().st.l[1].p.area.$===0);
  // a failing step must say so, not leave a half-built analysis looking complete
  layers.length=0; calls.length=0;
  callTool=async()=>{ throw new Error("Overpass is unreachable"); };
  let said="";
  openCard=(t,h)=>{ said=t+" "+h; };
  return replayState(shared).then(()=>{
    ok("a broken step is reported", /Could not rebuild/.test(said) && /Overpass is unreachable/.test(said));
    ok("the step that did work is kept", layers.length===1);
  });
});
