/* 3D view of the active extract. Data and configuration are injected by the app; nothing is sent back. */
'use strict';
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(n,d=3)=>Number(n).toLocaleString('en-US',{maximumFractionDigits:d});
const ICONS={search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',assembly:'<path d="m12 3 9 5v9l-9 5-9-5V8Z M3 8l9 5 9-5 M12 13v9"/>',layers:'<path d="m12 3 9 5-9 5-9-5Z M3 12l9 5 9-5 M3 16l9 5 9-5"/>',explode:'<path d="m12 2 5 3-5 3-5-3Z M12 16l5 3-5 3-5-3Z M12 9v6 M2 12h5 M17 12h5"/>',fit:'<path d="M8 3H3v5 M16 3h5v5 M21 16v5h-5 M3 16v5h5"/>',expand:'<path d="M8 3H3v5 M16 3h5v5 M21 16v5h-5 M3 16v5h5 M3 3l6 6 M21 3l-6 6 M3 21l6-6 M21 21l-6-6"/>',section:'<path d="M5 3v18 M10 5h10v14H10 M10 9l4-4 M10 15 20 5 M12 19l8-8 M18 19l2-2"/>',rotate:'<path d="M20 9a8 8 0 1 0 0 7 M20 3v6h-6"/>',reset:'<path d="M4 8a8 8 0 1 1 0 8 M4 3v5h5"/>',camera:'<path d="M3 7h4l2-3h6l2 3h4v14H3Z"/><circle cx="12" cy="13" r="4"/>',list:'<path d="M5 5h15 M5 12h15 M5 19h15"/>',chevron:'<path d="m9 5 7 7-7 7"/>',eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'};
function icon(name){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]||ICONS.assembly}</svg>`;}
document.querySelectorAll('[data-icon]').forEach(e=>e.innerHTML=icon(e.dataset.icon));

const COMPACT=CONFIG.mode==='compact';
if(COMPACT)document.body.classList.add('compact');
const HEX=/^#[0-9a-fA-F]{6}$/;
const CUSTOM=Object.fromEntries(Object.entries(CONFIG.colors||{}).filter(([,c])=>HEX.test(c)));
const HAS_CUSTOM=CONFIG.colors!==null&&CONFIG.colors!==undefined;
const LEGEND=(CONFIG.legend||[]).filter(r=>Array.isArray(r)&&HEX.test(r[1]));
const FOCUS=new Set(CONFIG.focus||[]);
const UNCOLOURED='#b8c2cc';
const KNOWN_BLOCKS={'E-motor':'#c88b59',Reducer:'#77a8b6',Housing:'#a3adb8'};
const PALETTE=['#c88b59','#77a8b6','#a3adb8','#7fae8c','#b9a6cd','#d0a85c','#8d9fcf'];
const PLACE={xml:'#4d9d83',estimated:'#d69743'};
const shellTypes=new Set(['motorcase','gearcase','jacket']);
const vector=a=>new THREE.Vector3(...a);
const clean=s=>String(s).replace(/_/g,' ');

const data=DATA,items=data.items,unique=[...new Map(items.map(i=>[i.pid,i])).values()];
const nameCounts=unique.reduce((a,i)=>a.set(i.name,(a.get(i.name)||0)+1),new Map());
const ZOOM_HINT=`${/Mac|iPhone|iPad/.test(navigator.platform)?'⌘':'Ctrl'} + scroll to zoom`;
const BLOCKS=[...new Set(items.map(i=>i.block))];
const COLORS=Object.fromEntries(BLOCKS.map((b,n)=>[b,KNOWN_BLOCKS[b]||PALETTE[n%PALETTE.length]]));
let scene,camera,renderer,controls,model,plane,groups=[],dirty=true,lastRender=0;
let selected=null,scope='all',preset='inside',mode=HAS_CUSTOM?'custom':'material',isolated=false,refs=false,section=false,explode=0,targetExplode=0;
let hidden=new Set(),collapsed=new Set(),cameraTween=null,lastTime=performance.now(),toastTimer,needsFit=true;
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;

function toast(s){$('toast').textContent=s;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,2600);}
function blockMass(b){return items.filter(i=>i.block===b).reduce((a,i)=>a+i.mass,0);}

function start(){
 try{
  header();
  renderer=new THREE.WebGLRenderer({canvas:$('scene'),antialias:true,alpha:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputEncoding=THREE.sRGBEncoding;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.78;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.localClippingEnabled=true;
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(34,1,.001,30);camera.up.set(0,0,1);
  controls=new THREE.OrbitControls(camera,$('scene'));controls.enableDamping=true;controls.dampingFactor=.09;controls.minDistance=.06;controls.maxDistance=5;controls.autoRotateSpeed=.45;
  controls.addEventListener('start',()=>cameraTween=null);controls.addEventListener('change',()=>dirty=true);
  if(COMPACT){controls.enableZoom=false;$('viewport').addEventListener('wheel',e=>{controls.enableZoom=e.ctrlKey||e.metaKey;if(!controls.enableZoom)toast(ZOOM_HINT);},{capture:true,passive:true});}
  const pm=new THREE.PMREMGenerator(renderer),room=new THREE.RoomEnvironment();scene.environment=pm.fromScene(room,.04).texture;pm.dispose();room.dispose?.();
  scene.add(new THREE.HemisphereLight(0xe7f2ff,0x5f6c79,.4));
  const key=new THREE.DirectionalLight(0xfff5e8,1.5),size=COMPACT?1024:2048;key.position.set(.5,-.6,1.4);key.castShadow=true;key.shadow.mapSize.set(size,size);
  Object.assign(key.shadow.camera,{left:-1,right:1,top:1,bottom:-1,near:.1,far:4});key.shadow.bias=-.00012;key.shadow.normalBias=.001;key.shadow.radius=3;scene.add(key);
  const rim=new THREE.DirectionalLight(0xd3e9ff,.8);rim.position.set(-1,.4,.6);scene.add(rim);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(12,12),new THREE.ShadowMaterial({opacity:.10}));ground.receiveShadow=true;scene.add(ground);
  const grid=new THREE.GridHelper(2,40,0x80959f,0xa8b7be);grid.rotateX(Math.PI/2);grid.material.transparent=true;grid.material.opacity=.10;scene.add(grid);
  plane=new THREE.Plane(new THREE.Vector3(0,-1,0),0);
  buildModel();
  const floor=new THREE.Box3().setFromObject(model).min.z-.01;ground.position.z=floor;grid.position.z=floor-.0005;
  renderTree();card();updateAppearance();
  new ResizeObserver(resize).observe($('viewport'));resize();bind();animate();
  $('loading').hidden=true;
 }catch(e){console.error(e);$('loading').hidden=true;$('failure').hidden=false;$('failure-message').textContent=e.message+' Try a browser with WebGL enabled.';}
}
function header(){
 $('v-title').textContent=CONFIG.title;$('v-sub').textContent=CONFIG.subtitle;
 $('root-name').textContent=data.root.name;$('root-rev').textContent=`Revision ${data.root.rev||'—'} · ${data.root.maturity||'—'}`;
 $('stat-parts').textContent=data.n_parts;$('stat-occ').textContent=data.n_occ;$('stat-mass').textContent=num(data.total_mass,1);
 $('reference-count').textContent=items.filter(i=>i.artefact).length;$('references-toggle').hidden=!items.some(i=>i.artefact);
 $('view-nav').innerHTML=(BLOCKS.length>1?['all',...BLOCKS]:[]).map(b=>`<button data-scope="${esc(b)}">${b==='all'?'Complete':esc(b)}</button>`).join('');
 const custom=$('render-mode').querySelector('[value=custom]');
 if(HAS_CUSTOM){custom.hidden=false;custom.textContent=CONFIG.color_label;}else custom.remove();
 $('render-mode').value=mode;
}
function buildModel(){
 model=new THREE.Group();scene.add(model);
 const centre=items.reduce((a,i)=>a.add(vector(i.pos)),new THREE.Vector3()).divideScalar(items.length);
 for(const it of items){
  const group=new THREE.Group(),sh=it.shape;group.userData.item=it;
  for(const part of build(sh,it)){
   const mat=new THREE.MeshStandardMaterial({...pbr(part.key),envMapIntensity:.65,side:THREE.DoubleSide,clippingPlanes:[]});
   let mesh;if(part.inst){mesh=new THREE.InstancedMesh(part.geo,mat,part.inst.length);part.inst.forEach((m,i)=>mesh.setMatrixAt(i,m));mesh.instanceMatrix.needsUpdate=true;}else mesh=new THREE.Mesh(part.geo,mat);
   mesh.userData={item:it,key:part.key};mesh.castShadow=true;mesh.receiveShadow=true;group.add(mesh);
  }
  if(sh.pos)group.position.copy(vector(sh.pos));if(sh.ax)group.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),vector(sh.ax).normalize());
  if(sh.transform_delta){const R=sh.transform_delta.R,m=new THREE.Matrix4().set(R[0][0],R[0][1],R[0][2],0,R[1][0],R[1][1],R[1][2],0,R[2][0],R[2][1],R[2][2],0,0,0,0,1);group.quaternion.setFromRotationMatrix(m);group.position.copy(vector(sh.transform_delta.T));}
  group.userData.base=group.position.clone();group.userData.offset=explosionOffset(it,centre);model.add(group);groups.push(group);
 }
}
function explosionOffset(it,centre){
 const sh=it.shape,p=vector(sh.pos||it.pos),v=new THREE.Vector3();
 if(it.block==='E-motor'){v.y=.22;if(sh.t==='bearing')v.y+=.12;if(sh.t==='hairpins')v.y+=.03;if(sh.t==='jacket')v.y+=.25;}
 else if(it.block==='Reducer'){v.y=-.08;if(sh.t==='bearing')v.y+=(it.pos[1]<-.1?-.15:.13);if(['gear','bevel','sidegear'].includes(sh.t))v.y+=(it.pos[1]<-.09?-.08:.09);}
 else if(it.block==='Housing'){
  if(sh.t==='motorcase'){v.y=.16;v.x=.32;v.z=.06;}
  else if(sh.t==='gearcase'){v.y=sh.side<0?-.30:.15;v.x=sh.side<0?-.08:.29;v.z=.025;}
  else if(sh.t==='bolt'){v.set((p.x-.04)*.55,p.y>.12?.38:-.24,(p.z-.12)*.55);}
  else if(sh.t==='plate'){v.y=.49;}
  else {v.x=.12;v.z=-.06;}
 }
 else v.copy(vector(it.pos).sub(centre).multiplyScalar(.6));
 return v;
}
function resize(){if(!renderer)return;dirty=true;const r=$('viewport').getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();if(needsFit){home(true);needsFit=false;}}
function eligible(it){return !hidden.has(it.pid)&&(scope==='all'||it.block===scope)&&(!isolated||selected?.pid===it.pid)&&(!it.artefact||refs||selected?.pid===it.pid||FOCUS.has(it.plm));}
function colour(it,spec){
 if(mode==='custom')return CUSTOM[it.plm]||UNCOLOURED;
 if(mode==='block')return COLORS[it.block];
 if(mode==='source')return PLACE[it.place]||PLACE.xml;
 return spec.color;
}
function updateAppearance(){
 dirty=true;let visible=0;const focusing=!!selected||FOCUS.size>0;
 for(const g of groups){
  const it=g.userData.item;g.visible=eligible(it);if(g.visible)visible++;
  const isSelected=selected?selected.pid===it.pid:FOCUS.has(it.plm),ghost=shellTypes.has(it.shape.t)&&preset==='inside'&&!isSelected;
  const coloured=mode==='custom'&&it.plm in CUSTOM;
  let opacity=ghost?.075:1;
  if(focusing&&!isSelected)opacity=ghost?.025:Math.min(opacity,coloured?.4:.22);
  if(mode==='custom'&&CONFIG.ghost_others&&!coloured&&!isSelected)opacity=ghost?.025:Math.min(opacity,.22);
  g.children.forEach(m=>{const mat=m.material,spec=pbr(m.userData.key),custom=mode!=='material';
   mat.color.set(colour(it,spec));mat.metalness=custom?.35:spec.metalness;mat.roughness=custom?.48:spec.roughness;
   mat.emissive.set(isSelected&&!coloured?'#1b4e63':'#000000');mat.emissiveIntensity=isSelected?.10:0;
   mat.opacity=opacity;mat.transparent=opacity<1;mat.depthWrite=opacity>=1;mat.side=ghost?THREE.FrontSide:THREE.DoubleSide;
   mat.clippingPlanes=section?[plane]:[];mat.needsUpdate=true;m.castShadow=opacity>.5;m.renderOrder=ghost?2:0;});
 }
 $('status-message').textContent=`${visible} / ${items.length} occurrences visible${isolated?' · isolated':''}`;
 $('references-toggle').setAttribute('aria-pressed',refs);$('references-toggle').firstChild.textContent=refs?'Hide modelling references ':'Show modelling references ';
 $('selection-banner').hidden=!selected;$('selection-label').textContent=selected?`${clean(selected.name)} · Rev ${selected.rev}`:'';
 document.querySelectorAll('[data-scope]').forEach(e=>e.classList.toggle('active',e.dataset.scope===scope));
 document.querySelectorAll('[data-preset]').forEach(e=>e.classList.toggle('active',e.dataset.preset===preset));
 legend();
}
function legend(){
 const row=(c,label)=>`<div class="legend-row"><i style="background:${c}"></i>${esc(label)}</div>`;
 let html='';
 if(mode==='custom')html=LEGEND.map(([label,c])=>row(c,label)).join('');
 else if(mode==='block')html=BLOCKS.map(b=>row(COLORS[b],b)).join('');
 else if(mode==='source'){
  const xml=items.filter(i=>i.place==='xml').length;
  html=row(PLACE.xml,`Position read from the XML · ${xml}`)+row(PLACE.estimated,`Placement reconstructed · ${items.length-xml}`)+
   `<p>Counts include hidden references. All shapes are illustrative.${data.axes?.d12!=null?'<br>Shaft centres: '+num(data.axes.d12*1000,1)+' / '+num(data.axes.d23*1000,1)+' mm, from the XML bearing origins.':''}</p>`;
 }
 $('legend').hidden=!html;$('legend').innerHTML=html;
}
function renderTree(){
 const q=$('search').value.trim().toLowerCase();let hits=0;
 $('tree').innerHTML=BLOCKS.map(b=>{
  const list=unique.filter(i=>i.block===b&&[i.name,i.plm,i.pid,i.material,i.rev].join(' ').toLowerCase().includes(q));hits+=list.length;
  if(!list.length)return '';const open=q||!collapsed.has(b);
  return `<section class="tree-group"><button class="group-head" data-group="${esc(b)}" aria-expanded="${!!open}"><span style="transform:rotate(${open?90:0}deg)">${icon('chevron')}</span><i class="group-dot" style="background:${COLORS[b]}"></i>${esc(b)}<small>${num(blockMass(b),2)} kg</small></button>${open?list.map(i=>`<button class="part-row ${selected?.pid===i.pid?'selected':''}" data-part="${esc(i.pid)}" title="${esc(i.name)} · ${esc(i.pid)} · Rev ${esc(i.rev)}" aria-pressed="${selected?.pid===i.pid}"><span class="source-dot ${i.place==='xml'?'':'estimated'}"></span><span class="part-name">${esc(clean(i.name))}${nameCounts.get(i.name)>1?` · Rev ${esc(i.rev)}`:''}</span><span class="qty">×${i.qty}</span></button>`).join(''):''}</section>`;
 }).join('')||'<p class="no-results">No matching components.<br>Try a name, material or PLM ID.</p>';
 $('tree-count').textContent=q?`${hits} / ${unique.length}`:unique.length;
}
function card(){
 const C=$('card'),it=selected;
 if(!it){C.innerHTML='<p class="part-empty">Click a part in the 3D view or in the structure to see its PLM data.</p>';return;}
 const kv=(label,value,cls='')=>`<div class="${cls}"><dt>${label}</dt><dd>${value}</dd></div>`;
 C.innerHTML=`<div class="part-head"><h3>${esc(clean(it.name))}</h3><span class="badge">${esc(it.block)}</span><span class="badge">Rev ${esc(it.rev)}</span><span class="badge state">${esc(it.maturity)}</span>
  <div class="part-actions"><button data-action="isolate" class="${isolated?'active':''}">${icon('eye')}${isolated?'Show context':'Isolate'}</button><button data-action="fit">${icon('fit')}Focus</button><button data-action="hide">Hide</button></div></div>
 <dl class="part-kv">
  ${kv('Unit mass',`<span class="fig">${num(it.mass)}</span> kg`)}
  ${kv('Quantity in the product',`<span class="fig">${it.qty}</span>`)}
  ${kv('Contribution',`<span class="fig">${num(it.mass*it.qty)}</span> kg · ${num(it.mass*it.qty/data.total_mass*100,1)} % of the mass`)}
  ${kv('Material',it.material?esc(it.material):'<span class="muted">not supplied</span>')}
  ${kv('Item (EI)',it.ei?esc(it.ei):'<span class="muted">none: instanced directly</span>')}
  ${kv('Description',it.desc?esc(it.desc):'<span class="muted">empty in the extract</span>')}
  ${kv('PLM ID',`<span class="mono">${esc(it.plm)}</span>`)}
  ${kv('Product element',`<span class="mono">${esc(it.pid)}</span>, XML line ${esc(it.pline)}`)}
  ${kv('3DEXPERIENCE XID',`<span class="mono">${esc(it.xid)}</span>`,'span2')}
 </dl>`;
}
function select(it){selected=it||null;isolated=false;if(it){hidden.delete(it.pid);if(scope!=='all'&&scope!==it.block)scope='all';}card();updateAppearance();renderTree();}
function setScope(s){scope=s;selected=null;isolated=false;card();updateAppearance();renderTree();fit('iso');}
function fit(view='iso',instant=false,only=null){
 if(!model)return;model.updateMatrixWorld(true);const box=new THREE.Box3(),all=new THREE.Box3();
 groups.filter(g=>g.visible).forEach(g=>{const b=new THREE.Box3().setFromObject(g);all.union(b);if(!only||only(g))box.union(b);});
 if(box.isEmpty())return;const center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
 const dirs={iso:[1.1,-1.55,1.05],top:[.001,.001,1],front:[0,-1,.001],side:[1,0,.001]},dir=vector(dirs[view]||dirs.iso).normalize();
 let radius=size.length()/2;if(only)radius=Math.max(radius,all.getSize(new THREE.Vector3()).length()*.28);
 const angle=Math.min(camera.fov*Math.PI/360,Math.atan(Math.tan(camera.fov*Math.PI/360)*camera.aspect));
 const target=center.clone();target.z+=size.z*.025;
 const position=target.clone().addScaledVector(dir,radius/Math.sin(angle)*1.12);
 if(instant||reduced){camera.position.copy(position);controls.target.copy(target);controls.update();cameraTween=null;}else cameraTween={from:camera.position.clone(),to:position,tf:controls.target.clone(),tt:target,start:performance.now()};
 document.querySelectorAll('[data-camera]').forEach(e=>e.classList.toggle('active',e.dataset.camera===view));
}
function home(instant=false){FOCUS.size&&!selected?fit('iso',instant,g=>FOCUS.has(g.userData.item.plm)):fit('iso',instant);}
function setPreset(p){preset=p;targetExplode=p==='exploded'?1:0;$('explode').value=targetExplode*100;$('explode-value').textContent=`${Math.round(targetExplode*100)}%`;updateAppearance();if(reduced){explode=targetExplode;moveParts();home();}else setTimeout(()=>home(),360);}
function moveParts(){dirty=true;for(const g of groups)g.position.copy(g.userData.base).addScaledVector(g.userData.offset,explode);}
function reset(){
 scope='all';selected=null;isolated=false;refs=false;hidden.clear();collapsed.clear();$('search').value='';
 mode=HAS_CUSTOM?'custom':'material';$('render-mode').value=mode;section=false;$('section').setAttribute('aria-pressed',false);$('section-depth').disabled=true;
 controls.autoRotate=false;$('rotate').setAttribute('aria-pressed',false);card();renderTree();setPreset('inside');
}
function bind(){
 $('tree').addEventListener('click',e=>{const p=e.target.closest('[data-part]'),g=e.target.closest('[data-group]');if(p){select(unique.find(i=>i.pid===p.dataset.part));fit('iso',false,g2=>g2.userData.item.pid===selected.pid);}if(g){collapsed.has(g.dataset.group)?collapsed.delete(g.dataset.group):collapsed.add(g.dataset.group);renderTree();}});
 $('search').addEventListener('input',renderTree);$('collapse').onclick=()=>{if(collapsed.size)collapsed.clear();else BLOCKS.forEach(b=>collapsed.add(b));renderTree();};
 document.querySelectorAll('[data-scope]').forEach(e=>e.onclick=()=>setScope(e.dataset.scope));document.querySelectorAll('[data-preset]').forEach(e=>e.onclick=()=>setPreset(e.dataset.preset));document.querySelectorAll('[data-camera]').forEach(e=>e.onclick=()=>fit(e.dataset.camera));
 $('fit').onclick=()=>home();$('clear-selection').onclick=()=>select(null);$('reset').onclick=reset;
 $('explode').oninput=e=>{targetExplode=+e.target.value/100;$('explode-value').textContent=e.target.value+'%';};$('explode').onchange=()=>setTimeout(()=>home(),350);
 $('section').onclick=()=>{section=!section;$('section').setAttribute('aria-pressed',section);$('section-depth').disabled=!section;updatePlane();updateAppearance();};$('section-depth').oninput=updatePlane;
 $('render-mode').onchange=e=>{mode=e.target.value;updateAppearance();};$('rotate').onclick=()=>{controls.autoRotate=!controls.autoRotate;$('rotate').setAttribute('aria-pressed',controls.autoRotate);};
 $('present').onclick=()=>{const v=document.body.classList.toggle('presentation');$('present').querySelector('span:last-child').textContent=v?'Exit present':'Present';setTimeout(()=>{resize();home();},50);};
 $('references-toggle').onclick=()=>{refs=!refs;updateAppearance();};
 $('capture').onclick=()=>{renderer.render(scene,camera);const c=document.createElement('canvas');c.width=renderer.domElement.width;c.height=renderer.domElement.height;const ctx=c.getContext('2d');ctx.fillStyle='#e4e9e9';ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(renderer.domElement,0,0);ctx.fillStyle='#334b58';ctx.font=`${Math.max(12,c.width/85)}px sans-serif`;ctx.fillText(`${data.root.name} · Illustrative reconstruction — not OEM CAD`,20,c.height-22);const a=document.createElement('a');a.download='3D_view.png';a.href=c.toDataURL('image/png');a.click();toast('Image saved with the reconstruction label.');};
 $('card').onclick=e=>{const a=e.target.closest('[data-action]');if(!a||!selected)return;
  if(a.dataset.action==='isolate'){isolated=!isolated;card();updateAppearance();fit('iso');}
  if(a.dataset.action==='fit')fit('iso',false,g=>g.userData.item.pid===selected.pid);
  if(a.dataset.action==='hide'){hidden.add(selected.pid);select(null);toast('Component hidden. Reset restores all parts.');}};
 window.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))return;const k=e.key.toLowerCase();if(e.key==='Escape')select(null);if(k==='f')home();if(k==='r')reset();if(k==='p'&&!COMPACT)$('present').click();if(e.key==='/'&&!COMPACT){e.preventDefault();$('search').focus();}});
 const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();let down=null;
 function hit(e){const r=$('scene').getBoundingClientRect();mouse.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(mouse,camera);return ray.intersectObjects(groups.filter(g=>g.visible),true).find(h=>h.object.material.opacity>.1&&(!section||plane.distanceToPoint(h.point)>=0));}
 $('scene').addEventListener('pointerdown',e=>{down=[e.clientX,e.clientY];$('tooltip').hidden=true;});
 $('scene').addEventListener('pointerup',e=>{if(down&&Math.hypot(e.clientX-down[0],e.clientY-down[1])<5){const h=hit(e);select(h?h.object.userData.item:null);}down=null;});
 $('scene').addEventListener('pointermove',e=>{if(e.buttons||down){$('tooltip').hidden=true;return;}const h=hit(e);$('scene').style.cursor=h?'pointer':'grab';$('tooltip').hidden=!h;if(h){const r=$('viewport').getBoundingClientRect(),it=h.object.userData.item;$('tooltip').innerHTML=`${esc(clean(it.name))}<small>Rev ${esc(it.rev)} · ${num(it.mass)} kg${it.qty>1?' · ×'+it.qty:''} · ${esc(it.material)}</small>`;$('tooltip').style.left=Math.min(e.clientX-r.left+14,r.width-260)+'px';$('tooltip').style.top=Math.max(8,e.clientY-r.top-50)+'px';}});
 $('scene').addEventListener('pointerleave',()=>{$('tooltip').hidden=true;down=null;});
 $('scene').addEventListener('webglcontextlost',e=>{e.preventDefault();$('failure').hidden=false;$('failure-message').textContent='The browser lost its graphics context. Reload the page to restore the scene.';});
}
function updatePlane(){dirty=true;const box=new THREE.Box3().setFromObject(model);plane.constant=box.min.y+(box.max.y-box.min.y)*(+$('section-depth').value/100);}
function animate(t=performance.now()){
 requestAnimationFrame(animate);const dt=Math.min((t-lastTime)/1000,.05);lastTime=t;
 if(Math.abs(explode-targetExplode)>.0001){explode=reduced?targetExplode:THREE.MathUtils.lerp(explode,targetExplode,Math.min(1,dt*9));moveParts();if(section)updatePlane();}
 if(cameraTween){const a=Math.min(1,(t-cameraTween.start)/650),k=1-Math.pow(1-a,3);camera.position.lerpVectors(cameraTween.from,cameraTween.to,k);controls.target.lerpVectors(cameraTween.tf,cameraTween.tt,k);if(a===1)cameraTween=null;}
 controls.update();if(dirty&&t-lastRender>1000/45){renderer.render(scene,camera);dirty=false;lastRender=t;}
}
requestAnimationFrame(()=>setTimeout(start,30));
