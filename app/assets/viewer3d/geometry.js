// ---------- materials ----------
const MATS = {};
function pbr(key){
  const spec = {
    steel:[0x596879,0.92,0.29], bearing:[0x8c9bac,0.95,0.23], gear:[0x435369,0.88,0.32], elec:[0x222b36,0.65,0.5],
    copper:[0xb25221,0.88,0.27], castAl:[0x80909d,0.62,0.47], al:[0xb8bec4,0.9,0.34], iron:[0x55595e,0.65,0.62],
    bolt:[0x435260,0.85,0.42], plastic:[0x1d1f22,0.0,0.45], rubber:[0x3a2a23,0.0,0.75], cage:[0x9a7a3e,0.9,0.4]
  }[key];
  return {color:spec[0], metalness:spec[1], roughness:spec[2]};
}
function matKey(m, t){
  if(t==='hairpins'||t==='onecoil'||/Copper/.test(m)) return 'copper';
  if(/Electrical steel/.test(m)) return 'elec';
  if(/Bearing steel/.test(m)) return 'bearing';
  if(/AlSi/.test(m)) return 'castAl';
  if(/EN AW/.test(m)) return 'al';
  if(/cast iron/.test(m)) return 'iron';
  if(/PA66/.test(m)) return 'plastic';
  if(/FKM/.test(m)) return 'rubber';
  if(/10\.9/.test(m)) return 'bolt';
  if(/MnCr5/.test(m)) return 'gear';
  return 'steel';
}

// ---------- geometry helpers (all built around +Y) ----------
const BGU = THREE.BufferGeometryUtils;
const V2 = (x,y)=>new THREE.Vector2(x,y);
function lathe(pts, seg=64, phi0=0, phiL=Math.PI*2){ return new THREE.LatheGeometry(pts.map(p=>V2(p[0],p[1])), seg, phi0, phiL); }
function ringProfile(ri, ro, h, c){ c = Math.min(c, (ro-ri)/3, h/3); return [[ri+c,-h/2],[ro-c,-h/2],[ro,-h/2+c],[ro,h/2-c],[ro-c,h/2],[ri+c,h/2],[ri,h/2-c],[ri,-h/2+c],[ri+c,-h/2]]; }
function extrudeY(shape, depth, opts={}){ const g = new THREE.ExtrudeGeometry(shape, Object.assign({depth, bevelEnabled:false, curveSegments:24}, opts)); g.translate(0,0,-depth/2); g.rotateX(-Math.PI/2); return g; }
function circlePath(r, cx=0, cy=0, cw=true){ const p = new THREE.Path(); p.absarc(cx,cy,r,0,Math.PI*2,cw); return p; }
function toothShape(z, r, m, bore, holes){
  const ra = r + m, rf = r - 1.25*m, s = new THREE.Shape(), pitch = Math.PI*2/z;
  const prof = [[rf,0],[rf,.16],[r-0.35*m,.24],[r,.29],[ra-0.15*m,.36],[ra,.40],[ra,.46],[ra-0.15*m,.50],[r,.57],[r-0.35*m,.62],[rf,.70],[rf,1]];
  let first = true;
  for(let i=0;i<z;i++) for(const [rr,f] of prof){ const a=(i+f)*pitch, x=rr*Math.cos(a), y=rr*Math.sin(a); if(first){s.moveTo(x,y); first=false;} else s.lineTo(x,y); }
  s.holes.push(circlePath(bore));
  (holes||[]).forEach(hh=>s.holes.push(circlePath(hh[2],hh[0],hh[1])));
  return s;
}
function twist(g, h, total){ const p = g.attributes.position; for(let i=0;i<p.count;i++){ const y=p.getY(i), a=(y/h)*total, x=p.getX(i), zz=p.getZ(i); p.setX(i, x*Math.cos(a)-zz*Math.sin(a)); p.setZ(i, x*Math.sin(a)+zz*Math.cos(a)); } p.needsUpdate=true; g.computeVertexNormals(); return g; }
function taper(g, h, k){ const p = g.attributes.position; for(let i=0;i<p.count;i++){ const t=(p.getY(i)+h/2)/h, s=1-k*t; p.setX(i,p.getX(i)*s); p.setZ(i,p.getZ(i)*s);} p.needsUpdate=true; g.computeVertexNormals(); return g; }
function hexHead(d){ const g = new THREE.CylinderGeometry(d*0.92,d*0.92,d*0.62,6); g.rotateY(Math.PI/6); return g; }

// returns array of {geo, mat, pos?, inst?(matrices)}
function build(sh, it){
  const out = [], mk = matKey(it.material, sh.t);
  const P = (geo, key=mk, extra={}) => out.push(Object.assign({geo, key}, extra));
  switch(sh.t){
  case 'bearing': {
    const {ri, ro, w} = sh, t = (ro-ri), tr = t*0.24, rp = (ri+ro)/2, db = t*0.46;
    const outer = ringProfile(ro-tr, ro, w, 0.0012), inner = ringProfile(ri, ri+tr, w, 0.0012);
    P(lathe(outer)); P(lathe(inner));
    const n = Math.max(7, Math.floor(Math.PI*2*rp/(db*1.32)));
    const ball = new THREE.SphereGeometry(db/2, 18, 12), mats=[];
    for(let i=0;i<n;i++){ const a=i/n*Math.PI*2; mats.push(new THREE.Matrix4().makeTranslation(rp*Math.cos(a),0,rp*Math.sin(a))); }
    P(ball, 'bearing', {inst:mats});
    P(lathe(ringProfile(rp-db*0.18, rp+db*0.18, w*0.62, 0.0004)), 'cage');
    break; }
  case 'shaft': {
    const pts = [[0, sh.segs[0][0]]]; const c = 0.0012;
    sh.segs.forEach(([y0,y1,r])=>{ pts.push([r-c,y0]); pts.push([r,y0+c]); pts.push([r,y1-c]); pts.push([r-c,y1]); });
    pts.push([0, sh.segs[sh.segs.length-1][1]]);
    P(lathe(pts, 48), 'steel');
    (sh.splines||[]).forEach(([y0,y1,r0,r1])=>{
      const z = Math.round(Math.PI*2*r1/0.0035); const s = toothShape(z, (r0+r1)/2, (r1-r0)/1.1, r0*0.5);
      const g = extrudeY(s, (y1-y0)*0.92); g.translate(0,(y0+y1)/2,0); P(g,'steel'); });
    break; }
  case 'gear': {
    const holes = [];
    if(sh.web){ const rh = (sh.bore + (sh.r-1.25*sh.m))/2, n=6, hr=(sh.r-1.25*sh.m-sh.bore)*0.24; for(let i=0;i<n;i++){ const a=i/n*Math.PI*2+0.3; holes.push([rh*Math.cos(a), rh*Math.sin(a), hr]); } }
    const g = extrudeY(toothShape(sh.z, sh.r, sh.m, sh.bore, holes), sh.h, {steps:6,bevelEnabled:true,bevelSize:.0006,bevelThickness:.0006,bevelSegments:2});
    twist(g, sh.h, sh.h*Math.tan(sh.helix*Math.PI/180)/sh.r);
    P(g,'gear');
    P(lathe(ringProfile(sh.bore, sh.bore+(sh.r-sh.bore)*0.28, sh.h*1.18, 0.001)),'gear');
    break; }
  case 'bevel': case 'sidegear': {
    const bore = sh.t==='bevel'?0.0105:0.012;
    const g = extrudeY(toothShape(sh.z, sh.r, sh.r*0.17, bore), sh.h, {steps:4}); taper(g, sh.h, 0.42); P(g,'gear');
    if(sh.t==='sidegear'){ const s = lathe([[0,-sh.h/2-0.03],[0.017,-sh.h/2-0.03],[0.017,-sh.h/2]],32); P(s,'gear');
      const sp = extrudeY(toothShape(14,0.0175,0.0016,0.008), 0.026); sp.translate(0,-sh.h/2-0.015,0); P(sp,'gear'); }
    break; }
  case 'pin': P(lathe([[0,-sh.h/2],[sh.r-0.001,-sh.h/2],[sh.r,-sh.h/2+0.001],[sh.r,sh.h/2-0.001],[sh.r-0.001,sh.h/2],[0,sh.h/2]],32),'steel'); break;
  case 'jacket': {
    const pts=[[sh.ri,-sh.h/2]]; const n=9, step=sh.h/n;
    for(let i=0;i<n;i++){ const y=-sh.h/2+i*step; pts.push([sh.ro, y+step*0.12]); pts.push([sh.ro, y+step*0.55]); pts.push([sh.ro-0.004, y+step*0.62]); pts.push([sh.ro-0.004, y+step*0.95]); }
    pts.push([sh.ro, sh.h/2]); pts.push([sh.ri, sh.h/2]); pts.push([sh.ri,-sh.h/2]);
    P(lathe(pts,72),'al'); break; }
  case 'stator': {
    const s = new THREE.Shape(); s.absarc(0,0,sh.ro,0,Math.PI*2,false);
    const hole = new THREE.Path(), n = sh.slots, sd = 0.022, so = 0.18, sw = 0.55;
    for(let i=0;i<n;i++){
      const a0 = i/n*Math.PI*2, p = Math.PI*2/n;
      const pts = [[sh.ri,a0],[sh.ri,a0+p*(0.5-so/2)],[sh.ri+0.003,a0+p*(0.5-so/2)],[sh.ri+0.004,a0+p*(0.5-sw/2)],[sh.ri+sd,a0+p*(0.5-sw/2)],[sh.ri+sd,a0+p*(0.5+sw/2)],[sh.ri+0.004,a0+p*(0.5+sw/2)],[sh.ri+0.003,a0+p*(0.5+so/2)],[sh.ri,a0+p*(0.5+so/2)]];
      pts.forEach(([r,a],j)=>{ const x=r*Math.cos(a), y=r*Math.sin(a); (i===0&&j===0)?hole.moveTo(x,y):hole.lineTo(x,y); });
    }
    s.holes.push(hole); P(extrudeY(s, sh.h, {curveSegments:96}),'elec');
    // lamination stack lines
    for(let k=1;k<10;k++){ const y=-sh.h/2+k*sh.h/10; P(lathe([[sh.ro+0.0004,y-0.0005],[sh.ro+0.0004,y+0.0005]],96),'steel'); }
    break; }
  case 'hairpins': case 'onecoil': {
    const loop = new THREE.TorusGeometry(0.0062,0.0024,8,14,Math.PI); // arc in XY, bulges +Y
    const leg = new THREE.CylinderGeometry(0.0024,0.0024,0.014,8);
    const l1 = leg.clone(); l1.translate(0.0062,-0.007,0); const l2 = leg.clone(); l2.translate(-0.0062,-0.007,0);
    const hp = BGU.mergeBufferGeometries([loop,l1,l2]); hp.translate(0,0.014,0);
    const mats=[]; const n = sh.t==='onecoil'?1:sh.n;
    for(const side of [-1,1]) for(const layer of (sh.t==='onecoil'?[0]:[0,1])) for(let i=0;i<n;i++){
      const a = (i + layer*0.5)/n*Math.PI*2, r = sh.r + layer*0.008;
      const t = new THREE.Vector3(-Math.sin(a),0,Math.cos(a)), ax = new THREE.Vector3(0,side,0), rad = new THREE.Vector3().crossVectors(t, ax);
      const m = new THREE.Matrix4().makeBasis(t, ax, rad); m.setPosition(r*Math.cos(a), side*sh.gap, r*Math.sin(a)); mats.push(m);
    }
    P(hp,'copper',{inst:mats});
    if(sh.t==='hairpins') for(const side of [-1,1]) P((()=>{const g=lathe(ringProfile(sh.r-0.006,sh.r+0.014,0.006,0.001)); g.translate(0,side*(sh.gap-0.003),0); return g;})(),'copper');
    break; }
  case 'tooth': { const g = new THREE.BoxGeometry(0.012,0.13,0.024); g.translate(sh.r,0,0); P(g,'elec'); break; }
  case 'diffcase': {
    const prof=[[0.026,-0.072],[0.040,-0.070],[0.041,-0.058],[0.054,-0.046],[0.061,-0.034],[0.074,-0.032],[0.074,-0.018],[0.062,-0.016],[0.063,0.012],[0.055,0.036],[0.042,0.050],[0.040,0.066],[0.026,0.068],[0.026,-0.072]];
    for(const a0 of [0.35, 0.35+Math.PI]) P(lathe(prof,24,a0,Math.PI*0.62),'iron');
    P(lathe([[0.062,-0.032],[0.074,-0.032],[0.074,-0.018],[0.062,-0.018],[0.062,-0.032]],64),'iron');
    break; }
  case 'bolt': {
    const {d,L} = sh, r=d/2, pitch=d*0.15, thr=Math.min(L*0.55, 0.045), pts=[[0,0],[r*0.98,0],[r*0.98,L-thr]];
    const nT = Math.floor(thr/pitch); for(let i=0;i<nT;i++){ const y=L-thr+i*pitch; pts.push([r*0.86,y+pitch*0.5]); pts.push([r,y+pitch]); }
    pts.push([r*0.8,L]); pts.push([0,L]);
    P(lathe(pts,20),'bolt');
    const head = hexHead(d); head.translate(0,-d*0.31-d*0.12,0); P(head,'bolt');
    const fl = lathe([[0,-d*0.12],[d*1.05,-d*0.12],[d*1.05,0],[0,0]],24); P(fl,'bolt');
    break; }
  case 'elbow': {
    const g = new THREE.TorusGeometry(0.03,0.0085,12,24,Math.PI/2); g.rotateX(Math.PI/2); P(g,'plastic');
    const s1 = new THREE.CylinderGeometry(0.0085,0.0085,0.025,16); s1.rotateZ(Math.PI/2); s1.translate(-0.0125,0,0.03); P(s1,'plastic');
    const s2 = new THREE.CylinderGeometry(0.0085,0.0085,0.025,16); s2.rotateX(Math.PI/2); s2.translate(0.03,0,-0.0125); P(s2,'plastic');
    break; }
  case 'bearingseat': {
    P(lathe(ringProfile(sh.ri, sh.ro, sh.h, 0.0015)),'castAl');
    for(let i=0;i<3;i++){ const a=i/3*Math.PI*2; const g=new THREE.CylinderGeometry(0.009,0.009,sh.h*0.6,16); g.translate((sh.ro+0.006)*Math.cos(a),-sh.h*0.2,(sh.ro+0.006)*Math.sin(a)); P(g,'castAl'); }
    break; }
  case 'seal': P(lathe([[sh.ri,-sh.h/2],[sh.ro,-sh.h/2],[sh.ro,sh.h/2],[sh.ri+0.004,sh.h/2],[sh.ri+0.002,0],[sh.ri,-sh.h*0.1],[sh.ri,-sh.h/2]],48),'rubber'); break;
  case 'plate': {
    const s = new THREE.Shape(); s.absarc(0,0,sh.ro,0,Math.PI*2,false); s.holes.push(circlePath(sh.ri));
    for(let i=0;i<8;i++){ const a=i/8*Math.PI*2+0.2; s.holes.push(circlePath(0.0055,(sh.ro-0.012)*Math.cos(a),(sh.ro-0.012)*Math.sin(a))); }
    P(extrudeY(s, sh.h, {bevelEnabled:true,bevelSize:0.0008,bevelThickness:0.0008,bevelSegments:1}),'steel'); break; }
  case 'bracket': {
    const s = new THREE.Shape(); s.moveTo(-0.09,0); s.lineTo(0.09,0); s.lineTo(0.09,0.012); s.lineTo(0.02,0.012); s.lineTo(0.012,0.05); s.lineTo(-0.012,0.05); s.lineTo(-0.02,0.012); s.lineTo(-0.09,0.012); s.lineTo(-0.09,0);
    const g = new THREE.ExtrudeGeometry(s,{depth:0.05,bevelEnabled:true,bevelSize:0.002,bevelThickness:0.002,bevelSegments:2}); g.translate(0,0,-0.025);
    // bracket profile lies in X-Z(world) with depth along Y
    g.rotateX(Math.PI/2); P(g,'castAl'); break; }
  case 'motorcase': {
    const {ri,ro,y0,y1} = sh, f=0.014, pts=[[ri,y0],[ro+0.012,y0],[ro+0.012,y0+f],[ro,y0+f+0.004],[ro,y1-f-0.004],[ro+0.012,y1-f],[ro+0.012,y1],[ri,y1],[ri,y0]];
    P(lathe(pts,96),'castAl');
    for(let i=0;i<12;i++){ const a=i/12*Math.PI*2; const rib=new THREE.BoxGeometry(0.006,(y1-y0)-2*f-0.01,0.008); rib.translate(0,(y0+y1)/2,0); rib.translate(0,0,0); const m=new THREE.Matrix4().makeRotationY(-a); rib.applyMatrix4(new THREE.Matrix4().makeTranslation(ro+0.003,0,0)); rib.applyMatrix4(m); P(rib,'castAl'); }
    break; }
  case 'gearcase': {
    // Hollow casting, a separate thin end face and open flange. All dimensions illustrative.
    const outline=sh.outline, cx=outline.reduce((a,p)=>a+p[0],0)/outline.length,
      cz=outline.reduce((a,p)=>a+p[1],0)/outline.length, d=sh.y1-sh.y0;
    function perimeter(inset=0){
      const p=new THREE.Shape(),points=outline.map(([x,z])=>{const l=Math.hypot(x-cx,z-cz),f=(l-inset)/l;return [cx+(x-cx)*f,cz+(z-cz)*f];});
      const corners=points.map((v,i)=>{const before=points[(i+points.length-1)%points.length],after=points[(i+1)%points.length];const a=new THREE.Vector2(...before).sub(new THREE.Vector2(...v)),b=new THREE.Vector2(...after).sub(new THREE.Vector2(...v));const r=Math.min(.008,a.length()*.22,b.length()*.22);a.normalize().multiplyScalar(r).add(new THREE.Vector2(...v));b.normalize().multiplyScalar(r).add(new THREE.Vector2(...v));return {a,b,v};});
      p.moveTo(corners[0].a.x,corners[0].a.y);for(const {a,b,v} of corners){p.lineTo(a.x,a.y);p.quadraticCurveTo(v[0],v[1],b.x,b.y);}p.closePath();return p;
    }
    function cast(shape,depth,y){const g=new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:false,curveSegments:36});g.rotateX(Math.PI/2);g.translate(0,y,0);P(g,'castAl');}
    const wall=perimeter(); wall.holes.push(perimeter(0.006));cast(wall,d,sh.y1);
    const flange=perimeter(-0.004);flange.holes.push(perimeter(0.012));cast(flange,0.008,sh.side<0?sh.y1:sh.y0+0.008);
    const face=perimeter();sh.bores.forEach(([bx,bz,br])=>face.holes.push(circlePath(br*0.75,bx,bz)));
    cast(face,0.006,sh.side<0?sh.y0+0.006:sh.y1);
    sh.bosses.forEach(([bx,bz])=>{const g=lathe(ringProfile(0.0055,0.0105,Math.min(d,0.026),0.001),24);g.translate(bx,sh.side<0?sh.y1-0.013:sh.y0+0.013,bz);P(g,'castAl');});
    sh.bores.forEach(([bx,bz,br])=>{const g=lathe(ringProfile(br*0.75,br+0.009,0.014,0.0015),48);g.translate(bx,sh.side<0?sh.y0:sh.y1,bz);P(g,'al');});
    // Radial reinforcing webs on the external face.
    sh.bores.forEach(([bx,bz,br])=>{for(let k=0;k<6;k++){
      const a=k/6*Math.PI*2,l=0.025;const g=new THREE.BoxGeometry(l,0.007,0.004);
      g.rotateY(-a);g.translate(bx+(br+l/2)*Math.cos(a),sh.side<0?sh.y0-0.004:sh.y1+0.004,bz+(br+l/2)*Math.sin(a));P(g,'castAl');
    }});
    break; }
  case 'generic': P(new THREE.IcosahedronGeometry(0.02,1),'steel'); break;
  }
  return out;
}
