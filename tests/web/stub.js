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
    // Enough of a selector engine to test wiring: scans this element's own HTML
    // for [data-x] and .cls, and returns stub elements carrying that dataset.
    // Not a parser. It exists so "does the click handler get attached to the
    // right rows" is a question the harness can actually answer.
    // Matches are cached per (element, html, selector) so that wiring a handler
    // and then clicking touch the SAME object. Returning fresh stubs each call
    // would make every wiring test vacuously pass.
    querySelectorAll(sel){
      this._q ||= new Map();
      const key = sel + "\u0000" + (this.innerHTML || "");
      if(!this._q.has(key)) this._q.set(key, scan(this.innerHTML, sel));
      return this._q.get(key);
    },
    querySelector(sel){ return this.querySelectorAll(sel)[0] || mkEl(); },
    closest(){ return mkEl(); },
    insertAdjacentHTML(pos,html){ this.innerHTML=(this.innerHTML||'')+html; },
    showModal(){}, show(){}, close(){},
    setAttribute(){}, getAttribute(){return null}, scrollIntoView(){},
  };
  return e;
};
// Returns one stub element per matching tag found in `html`.
function scan(html, sel){
  html = html || "";
  const attr = /^\[data-([a-z0-9-]+)\]$/i.exec(sel);
  const out = [];
  if(attr){
    const name = attr[1];
    const camel = name.replace(/-([a-z])/g, (_,c)=>c.toUpperCase());
    const re = new RegExp('data-'+name+'="([^"]*)"', 'g');
    let m;
    while((m = re.exec(html))){
      const el = mkEl();
      el.dataset[camel] = m[1];
      el._html = html;
      out.push(el);
    }
    return out;
  }
  const cls = /^\.([a-zA-Z0-9_-]+)$/.exec(sel);
  if(cls){
    const re = new RegExp('class="[^"]*\\b'+cls[1]+'\\b[^"]*"', 'g');
    while(re.exec(html)) out.push(mkEl());
  }
  return out;
}
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
