// Minimal DOM + MapLibre stub: enough to actually EXECUTE the app script and
// prove it loads, defines its API, and that share/replay logic is correct.
const els = new Map();
const mkEl = (id="") => {
  const e = {
    id, innerHTML:"", textContent:"", value:"", hidden:false, disabled:false,
    dataset:{}, style:{}, children:[], type:"",
    classList:{ _s:new Set(), add(c){this._s.add(c)}, remove(c){this._s.delete(c)},
      contains(c){return this._s.has(c)}, toggle(c,f){ f===undefined ? (this._s.has(c)?this._s.delete(c):this._s.add(c)) : (f?this._s.add(c):this._s.delete(c)); } },
    addEventListener(){}, removeEventListener(){}, appendChild(c){this.children.push(c); return c;},
    removeChild(){}, remove(){}, focus(){}, select(){}, click(){},
    querySelector(){ return mkEl(); }, querySelectorAll(){ return []; },
    closest(){ return mkEl(); },
    insertAdjacentHTML(pos,html){ this.innerHTML=(this.innerHTML||'')+html; },
    showModal(){}, show(){}, close(){},
    setAttribute(){}, getAttribute(){return null}, scrollIntoView(){},
  };
  return e;
};
const doc = {
  getElementById(id){ if(!els.has(id)) els.set(id, mkEl(id)); return els.get(id); },
  querySelector(){ return mkEl(); }, querySelectorAll(){ return []; },
  createElement(){ return mkEl(); }, addEventListener(){},
  body:{ appendChild(){}, classList:mkEl().classList },
};
class MapStub {
  constructor(){ this.handlers={}; this.sources={}; this.lyrs={}; this._c={lat:40.7,lng:-73.99}; this._z=13;
    this.doubleClickZoom={disable(){},enable(){}}; }
  on(ev,a,b){ (this.handlers[ev] ||= []).push(b||a); }
  once(ev,fn){ (this.handlers[ev] ||= []).push(fn); }
  addControl(){} resize(){} setStyle(){} jumpTo(o){ if(o.center){this._c={lng:o.center[0],lat:o.center[1]};} if(o.zoom!=null)this._z=o.zoom; }
  fitBounds(){} flyTo(){} loaded(){return true} isStyleLoaded(){return true}
  addSource(id,s){ this.sources[id]={...s,setData(d){this.data=d;}}; }
  getSource(id){ return this.sources[id]; }
  addLayer(l){ this.lyrs[l.id]=l; } getLayer(id){ return this.lyrs[id]; }
  removeLayer(id){ delete this.lyrs[id]; } removeSource(id){ delete this.sources[id]; }
  setLayoutProperty(){}
  setPaintProperty(id,k,v){ (this.paint||(this.paint={}))[id]=Object.assign(this.paint[id]||{},{[k]:v}); }
  moveLayer(id){ const l=this.lyrs[id]; if(l){ delete this.lyrs[id]; this.lyrs[id]=l; } }
  getCanvas(){ return { style:{} }; }
  getStyle(){ return { layers: Object.values(this.lyrs) }; }
  getCenter(){ return this._c; } getZoom(){ return this._z; }
  getBounds(){ return {getWest:()=>-74,getSouth:()=>40.69,getEast:()=>-73.98,getNorth:()=>40.71}; }
}
globalThis.document = doc;
globalThis.window = globalThis;
globalThis.location = { origin:"http://localhost:8000", pathname:"/", hash:"" };
Object.defineProperty(globalThis,"navigator",{configurable:true,writable:true,value:{clipboard:{writeText:async t=>{globalThis.__copied=t;}}}});
globalThis.ResizeObserver = class { observe(){} };
globalThis.maplibregl = { Map: MapStub, NavigationControl: class {}, ScaleControl: class {}, LngLatBounds: class {
    constructor(){ this.w=Infinity; this.s=Infinity; this.e=-Infinity; this.n=-Infinity; }
    extend(c){ const [x,y]=Array.isArray(c)?c:[c.lng,c.lat];
      this.w=Math.min(this.w,x); this.e=Math.max(this.e,x);
      this.s=Math.min(this.s,y); this.n=Math.max(this.n,y); return this; }
    isEmpty(){ return !(isFinite(this.w)&&isFinite(this.s)); }
    getWest(){return this.w} getSouth(){return this.s} getEast(){return this.e} getNorth(){return this.n}
  } };
globalThis.__fetchQueue = [];
globalThis.fetch = async (url, opts) => {
  globalThis.__lastFetch = { url, body: opts && opts.body ? JSON.parse(opts.body) : null };
  if (String(url).includes("/api/tools") && !opts) return { json: async()=>[] };
  const next = globalThis.__fetchQueue.shift();
  return { json: async()=> next ?? { result:{ type:"FeatureCollection", features:[{type:"Feature",properties:{},geometry:{type:"Point",coordinates:[-73.99,40.7]}}] } } };
};
// simulate a style that is not ready yet, then becomes ready

globalThis.addEventListener=()=>{};
globalThis.removeEventListener=()=>{};
globalThis.innerWidth=1440; globalThis.innerHeight=900;
