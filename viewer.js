import * as THREE from '../../资料整理/tmp/viewer_deps/node_modules/three/build/three.module.js';
import { OrbitControls } from '../../资料整理/tmp/viewer_deps/node_modules/three/examples/jsm/controls/OrbitControls.js';

const labels={eave_rafters:'出檐椽',eave_purlins:'廊桁',step_purlins:'步桁',qiangshan:'戗山木',support:'定位支承',old_corner:'老戗',young_corner:'嫩戗',fan_rafters:'摔网椽',flying_rafters:'立脚飞椽',straight_flying_rafters:'正身飞椽',likou:'弯里口木',lower_board:'下层木望板',upper_board:'上层卷戗板',blocks:'过渡承托木',scraping_blocks:'飞椽尾托',lingjiao:'菱角木',biandan:'扁担木',transition_supports:'交界承托木'};
const colors={eave_rafters:0xc7aa77,eave_purlins:0xa1815b,step_purlins:0x9d7652,qiangshan:0xd2b388,support:0x87918a,old_corner:0x85583b,young_corner:0xb66b33,fan_rafters:0xc9ae85,flying_rafters:0xd5a26a,straight_flying_rafters:0xd5a26a,likou:0x9e7951,lower_board:0xc3c5a3,upper_board:0xccbd9b,blocks:0xa58b64,scraping_blocks:0xb69463,lingjiao:0xc29360,biandan:0x9b693f,transition_supports:0xb9a27d};
const levels={eave_rafters:110,eave_purlins:0,step_purlins:180,qiangshan:300,support:-220,old_corner:0,young_corner:200,fan_rafters:110,blocks:60,lower_board:280,likou:370,flying_rafters:530,straight_flying_rafters:530,upper_board:710,scraping_blocks:440,lingjiao:270,biandan:360,transition_supports:220};
const el=id=>document.getElementById(id);
try{init();}catch(error){el('load-error').hidden=false;el('load-error').textContent='三维视图未能载入：'+error.message;console.error(error);}

function init(){
  const data=JSON.parse(el('model-data').textContent);
  if(!Array.isArray(data.parts)||!data.parts.length)throw Error('没有可显示的构件。');
  const stageNumber=data.metadata?.stage;
  const cornerAngle=Number(data.metadata?.corner_angle_deg ?? 90);
  const halfAngle=cornerAngle*Math.PI/360;
  el('model-title').textContent='戗角 · '+(data.metadata?.case_id||'参数模型');
  document.title=el('model-title').textContent;
  el('stage-label').textContent=`${cornerAngle}° 对称角部 · 参数生成`;
  el('scope-note').textContent='排料页仅包含立脚飞椽，采用本次生成结果的中心截面梯形。曲线拟合只作比较，不改变三维模型。';
  if(data.metadata?.visual_review_label){el('stage-label').textContent=data.metadata.visual_review_label;el('scope-note').textContent='视觉检查副本：几何沿用所选结果，仅使用当前查看器显示。请打开阴影和三角面网格检查。';}
  if(stageNumber>=2)el('show-skeleton').checked=false;
  el('board-controls').hidden=!data.parts.some(p=>/board/.test(p.group));
  const stage=el('viewport');
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
  renderer.setClearColor(0x000000,0);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.1;
  renderer.shadowMap.enabled=el('show-shadows').checked;
  renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  stage.appendChild(renderer.domElement);
  const scene=new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff,.6));
  const sun=new THREE.DirectionalLight(0xffefcf,2.4);sun.position.set(2000,-4000,6000);scene.add(sun);
  const fill=new THREE.DirectionalLight(0xe8f2ff,.55);fill.position.set(-3000,2000,2500);scene.add(fill);
  const group=new THREE.Group();scene.add(group);
  const skeleton=new THREE.Group();skeleton.visible=el('show-skeleton').checked;scene.add(skeleton);
  const meshes=[];const categories=new Map();let selected=null,hovered=null,isolated=false;
  for(const part of data.parts){
    if(!part.vertices?.length||!part.triangles?.length)throw Error('构件 '+part.id+' 缺少三角网。');
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(part.vertices,3));
    geometry.setIndex(part.triangles);geometry.computeVertexNormals();geometry.computeBoundingBox();
    const color=colors[part.group]??0xb59c76;
    const board=/board/.test(part.group);
    const material=new THREE.MeshStandardMaterial({color,roughness:.83,metalness:0,side:THREE.DoubleSide,transparent:board,opacity:board?.35:1,depthWrite:!board,flatShading:true});
    const mesh=new THREE.Mesh(geometry,material);mesh.name=part.name||part.id;mesh.userData={...part,baseColor:color,board};
    mesh.castShadow=!board;mesh.receiveShadow=true;
    const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geometry,24),new THREE.LineBasicMaterial({color:0x493f2b,transparent:true,opacity:board?.21:.28}));
    edges.raycast=()=>{};mesh.add(edges);mesh.userData.edges=edges;
    const triangles=new THREE.LineSegments(new THREE.WireframeGeometry(geometry),new THREE.LineBasicMaterial({color:0x27372f,transparent:true,opacity:.38,depthWrite:false}));
    triangles.raycast=()=>{};triangles.visible=el('show-triangles').checked;mesh.add(triangles);mesh.userData.triangles=triangles;
    group.add(mesh);meshes.push(mesh);
    if(!categories.has(part.group))categories.set(part.group,[]);categories.get(part.group).push(mesh);
  }
  for(const line of data.control_lines||[]){
    if(!line.points?.length)continue;
    const points=line.points.map(p=>new THREE.Vector3(...p));
    const geometry=new THREE.BufferGeometry().setFromPoints(points);
    const object=new THREE.Line(geometry,new THREE.LineBasicMaterial({color:line.group==='support'?0x8f9990:0x2d8183,transparent:true,opacity:.66,depthTest:false}));
    object.renderOrder=5;skeleton.add(object);
  }
  const bounds=new THREE.Box3().setFromObject(group),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const radius=Math.max(size.length()/2,1);
  sun.position.copy(center).addScaledVector(new THREE.Vector3(2,-4,6).normalize(),radius*3);
  sun.target.position.copy(center);scene.add(sun.target);sun.castShadow=true;
  sun.shadow.mapSize.set(4096,4096);
  Object.assign(sun.shadow.camera,{left:-radius*1.15,right:radius*1.15,top:radius*1.15,bottom:-radius*1.15,near:radius*.1,far:radius*8});
  sun.shadow.camera.updateProjectionMatrix();sun.shadow.bias=-.00003;sun.shadow.normalBias=.3;
  const camera=new THREE.OrthographicCamera(-radius,radius,radius,-radius,.1,100*radius);camera.up.set(0,0,1);
  const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.12;controls.screenSpacePanning=true;controls.target.copy(center);
  const gridSize=Math.ceil(Math.max(size.x,size.y)/500)*500+1000;
  const grid=new THREE.GridHelper(gridSize,Math.max(4,Math.round(gridSize/200)),0xaeb9aa,0xd3dbce);
  grid.rotation.x=Math.PI/2;grid.position.set(center.x,center.y,bounds.min.z-65);grid.material.transparent=true;grid.material.opacity=.4;scene.add(grid);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(gridSize,gridSize),new THREE.ShadowMaterial({opacity:.2}));
  ground.position.set(center.x,center.y,bounds.min.z-66);ground.receiveShadow=true;scene.add(ground);
  const axes=new THREE.AxesHelper(radius*.2);axes.position.set(0,0,0);scene.add(axes);
  const selector=new THREE.Box3Helper(new THREE.Box3(),0x326e60);selector.visible=false;scene.add(selector);
  for(const [key,items] of categories){
    const optionGroup=document.createElement('optgroup');optionGroup.label=labels[key]||key;
    for(const item of items){const option=document.createElement('option');option.value=item.userData.id;option.textContent=item.userData.id+' · '+item.name;optionGroup.appendChild(option);}
    el('part-picker').appendChild(optionGroup);
    const row=document.createElement('label');row.className='group-row';
    const input=document.createElement('input');input.type='checkbox';input.checked=true;input.dataset.group=key;
    const dot=document.createElement('span');dot.className='group-dot';dot.style.background='#'+new THREE.Color(colors[key]??0xb59c76).getHexString();
    const label=document.createElement('span');label.className='group-name';label.textContent=labels[key]||key;
    const count=document.createElement('span');count.className='group-number';count.textContent=items.length;
    row.append(input,dot,label,count);el('group-list').appendChild(row);
    input.addEventListener('change',()=>{isolated=false;applyVisibility();});
  }
  const units=data.metadata?.units||'mm';
  el('model-summary').textContent=meshes.length+' 个构件 · 单位 '+units+' · 同一比例';
  function applyVisibility(){
    for(const input of el('group-list').querySelectorAll('input'))for(const mesh of categories.get(input.dataset.group))mesh.visible=input.checked&&(!isolated||mesh===selected);
    if(selected&&!selected.visible)selector.visible=false;
    el('visible-count').textContent='显示 '+meshes.filter(m=>m.visible).length+' / '+meshes.length+' 个构件';
  }
  function changeView(name){
    const directions={iso:new THREE.Vector3(2,-3,1.65),top:new THREE.Vector3(0,0,1),bottom:new THREE.Vector3(0,0,-1),side:new THREE.Vector3(-1,0,0),corner:new THREE.Vector3(-Math.sin(halfAngle),Math.cos(halfAngle),0)};
    const direction=directions[name]||directions.iso;
    camera.up.set(0,name==='top'?-1:name==='bottom'?1:0,name==='top'||name==='bottom'?0:1);
    controls.target.copy(center);camera.position.copy(center).addScaledVector(direction.normalize(),radius*4);camera.zoom=1;camera.updateProjectionMatrix();controls.update();fit();
    for(const button of document.querySelectorAll('[data-view]'))button.classList.toggle('active',button.dataset.view===name);
  }
  function fit(){
    const visible=meshes.filter(m=>m.visible);const box=new THREE.Box3();for(const mesh of visible)box.expandByObject(mesh);
    if(box.isEmpty())return;
    const c=box.getCenter(new THREE.Vector3()),extent=box.getSize(new THREE.Vector3()).length()/2;
    const direction=camera.position.clone().sub(controls.target).normalize();controls.target.copy(c);camera.position.copy(c).addScaledVector(direction,Math.max(extent,100)*4);
    camera.lookAt(c);camera.updateMatrixWorld(true);
    const viewSize=box.clone().applyMatrix4(camera.matrixWorldInverse).getSize(new THREE.Vector3());
    const aspect=stage.clientWidth/Math.max(stage.clientHeight,1),halfHeight=Math.max(viewSize.y/2,viewSize.x/(2*aspect),50)*1.12;
    camera.left=-halfHeight*aspect;camera.right=halfHeight*aspect;camera.top=halfHeight;camera.bottom=-halfHeight;camera.zoom=1;camera.updateProjectionMatrix();controls.update();
  }
  function resize(){
    const width=stage.clientWidth,height=stage.clientHeight;
    renderer.setSize(width,height,false);
    fit();
  }
  new ResizeObserver(resize).observe(stage);
  for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>changeView(button.dataset.view));
  for(const button of document.querySelectorAll('[data-orbit]'))button.addEventListener('click',()=>{
    const offset=camera.position.clone().sub(controls.target),angle=Math.PI/12;
    camera.up.set(0,0,1);
    if(button.dataset.orbit==='left'||button.dataset.orbit==='right')offset.applyAxisAngle(new THREE.Vector3(0,0,1),button.dataset.orbit==='left'?angle:-angle);
    else{const right=new THREE.Vector3().crossVectors(offset,new THREE.Vector3(0,0,1));if(right.lengthSq()<1e-8)right.set(1,0,0);offset.applyAxisAngle(right.normalize(),button.dataset.orbit==='up'?angle:-angle);}
    camera.position.copy(controls.target).add(offset);camera.lookAt(controls.target);controls.update();
  });
  el('fit').addEventListener('click',fit);
  el('show-skeleton').addEventListener('change',event=>{skeleton.visible=event.target.checked&&Number(el('explode').value)===0;});
  el('show-edges').addEventListener('change',event=>{for(const mesh of meshes)mesh.userData.edges.visible=event.target.checked;});
  function setTriangles(visible){for(const mesh of meshes){mesh.userData.triangles.visible=visible;mesh.material.polygonOffset=visible;mesh.material.polygonOffsetFactor=1;mesh.material.polygonOffsetUnits=1;mesh.material.needsUpdate=true;}}
  el('show-triangles').addEventListener('change',event=>setTriangles(event.target.checked));
  el('show-shadows').addEventListener('change',event=>{renderer.shadowMap.enabled=event.target.checked;for(const mesh of meshes)mesh.material.needsUpdate=true;});
  function setBoardOpacity(percent){
    const value=1-percent/100;el('board-opacity').value=String(percent);el('board-opacity-value').textContent=percent+'%';
    for(const mesh of meshes.filter(m=>m.userData.board)){mesh.material.opacity=value;mesh.material.transparent=value<1;mesh.material.depthWrite=value>=1;mesh.castShadow=value>=.8;mesh.material.needsUpdate=true;}
  }
  el('board-opacity').addEventListener('input',event=>setBoardOpacity(Number(event.target.value)));
  el('visual-inspection').addEventListener('click',()=>{
    el('show-shadows').checked=true;renderer.shadowMap.enabled=true;
    el('show-triangles').checked=true;setTriangles(true);setBoardOpacity(0);
  });
  el('explode').addEventListener('input',event=>{
    const value=Number(event.target.value)/100;el('explode-value').textContent=Math.round(value*100)+'%';
    for(const mesh of meshes)mesh.position.z=(levels[mesh.userData.group]??0)*value;
    if(selected)selector.box.setFromObject(selected);skeleton.visible=el('show-skeleton').checked&&value===0;
  });
  el('isolate').addEventListener('click',()=>{if(!selected)return;isolated=true;applyVisibility();fit();});
  el('restore').addEventListener('click',()=>{isolated=false;for(const input of el('group-list').querySelectorAll('input'))input.checked=true;applyVisibility();fit();});
  const flyingView=el('flying-view');
  if(flyingView){
    flyingView.hidden=!data.parts.some(p=>/flying_rafters/.test(p.group));
    flyingView.textContent=stageNumber>=5?'本层构造':'飞椽构造';
    flyingView.addEventListener('click',()=>{
      isolated=false;
      const contextGroups=new Set(['flying_rafters','straight_flying_rafters','old_corner','young_corner','likou']);
      if(stageNumber>=5)for(const groupName of ['scraping_blocks','lingjiao','biandan','upper_board','transition_supports'])contextGroups.add(groupName);
      for(const input of el('group-list').querySelectorAll('input'))input.checked=contextGroups.has(input.dataset.group);
      applyVisibility();fit();
    });
  }
  const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();let down=null;
  function hit(event){const rect=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);return raycaster.intersectObjects(meshes.filter(m=>m.visible&&(!m.userData.board||m.material.opacity>.8)),false)[0]?.object||null;}
  function highlight(){for(const mesh of meshes)mesh.material.emissive.setHex(mesh===selected?0x2c2512:mesh===hovered?0x17180c:0x000000);}
  function selectMesh(mesh){
    selected=mesh;highlight();selector.visible=!!selected;el('isolate').disabled=!selected;
    el('part-picker').value=selected?.userData.id||'';
    if(selected){selector.box.setFromObject(selected);el('selection').textContent=selected.userData.id+' · '+selected.name+'\n'+(labels[selected.userData.group]||selected.userData.group);}
    else el('selection').textContent='点击木构件，查看编号。';
  }
  el('part-picker').addEventListener('change',event=>{
    selectMesh(meshes.find(mesh=>mesh.userData.id===event.target.value)||null);isolated=!!selected;
    if(selected)for(const input of el('group-list').querySelectorAll('input'))if(input.dataset.group===selected.userData.group)input.checked=true;
    applyVisibility();fit();
  });
  renderer.domElement.addEventListener('pointermove',event=>{hovered=hit(event);renderer.domElement.style.cursor=hovered?'pointer':'grab';highlight();});
  renderer.domElement.addEventListener('pointerdown',event=>{down=[event.clientX,event.clientY];});
  renderer.domElement.addEventListener('pointerup',event=>{
    if(!down||Math.hypot(event.clientX-down[0],event.clientY-down[1])>5)return;
    selectMesh(hit(event));
  });
  resize();changeView('iso');applyVisibility();
  function draw(){requestAnimationFrame(draw);controls.update();renderer.render(scene,camera);}draw();
  window.cornerViewer={ready:true,sourceMeshHash:data.metadata?.viewer_source_sha256,partCount:meshes.length,groups:[...categories.keys()],get visibleCount(){return meshes.filter(m=>m.visible).length;},renderer,scene,camera,controls,meshes,changeView,fit};
  document.documentElement.dataset.viewerReady='true';
}
