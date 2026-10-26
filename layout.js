'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function bezierControlsFromSegment(x0, x1, y0, y1, coefficients) {
    if (![x0, x1, y0, y1].every(Number.isFinite) || x1 <= x0
      || !Array.isArray(coefficients) || coefficients.length !== 4 || !coefficients.every(Number.isFinite)) {
      throw new Error('三次样条分段数据无效');
    }
    const [a, b, c] = coefficients;
    const h = x1 - x0;
    const slope0 = c;
    const slope1 = 3 * a * h * h + 2 * b * h + c;
    return [
      [x0, y0],
      [x0 + h / 3, y0 + h * slope0 / 3],
      [x1 - h / 3, y1 - h * slope1 / 3],
      [x1, y1]
    ];
  }
  if (typeof module === 'object' && module.exports) {
    module.exports = { bezierControlsFromSegment };
    return;
  }
  try {
    const data = JSON.parse(byId('layout-data').textContent);
    const layout = data.layout, pieces = layout.pieces, fits = data.fits;
    const translation = data.translation || {};
    const hasTranslation = Number.isFinite(translation.dx_mm) && Number.isFinite(translation.dy_mm);
    const colors = {b:'#b04950',c:'#277d9b',d:'#c28c2f'};
    const labels = {natural_cubic:'自然三次样条',pchip:'PCHIP 保形三次'};
    const methods = ['natural_cubic','pchip'].filter(m => fits.b[m] && fits.c[m] && fits.d[m]);
    if (!methods.length || !pieces.length) throw Error('缺少曲线或排料数据');
    const pieceSelect = byId('piece'), methodSelect = byId('method');
    pieceSelect.innerHTML = '<option value="all">全部 '+pieces.length+' 根模板</option>'+pieces.map(p => '<option value="'+esc(p.id)+'">'+esc(p.id)+'</option>').join('');
    methodSelect.innerHTML = methods.map(m => '<option value="'+m+'">'+labels[m]+'</option>').join('');
    byId('translated').disabled = !hasTranslation;
    byId('translated').checked = hasTranslation;
    const f = v => Number(v).toFixed(3);
    const W = layout.board_width_x_mm, H = layout.board_height_y_mm, T = layout.board_thickness_mm;
    const area = pieces.reduce((s,p) => {
      const v = [p.A,p.B,p.C,p.D];
      return s+Math.abs(v.reduce((a,q,i) => a+q[0]*v[(i+1)%4][1]-q[1]*v[(i+1)%4][0],0))/2;
    },0);
    byId('summary').textContent = pieces.length+' 种单侧模板 × 2 套 = '+(2*pieces.length)+' 根立脚飞椽';
    byId('layout-stat').textContent = '每套板面 '+f(W)+' × '+f(H)+'，板厚 '+f(T)+'；面积利用率 '+(100*area/(W*H)).toFixed(2)+'%。';
    byId('translation').textContent = hasTranslation
      ? 'c 平移近似：d(x − '+f(translation.dx_mm)+') + '+f(translation.dy_mm)+'；最大角点误差 '+f(translation.max_abs_error_mm)+' mm，RMS '+f(translation.rms_error_mm)+' mm。此项只报告误差。'
      : '本次未计算 c、d 平移拟合。b、c、d 仍按原角点独立生成。';
    byId('point-rows').innerHTML = pieces.map(p => '<tr><td>'+esc(p.id)+'</td><td>'+esc((p.model_ids||[]).join(' / '))+'</td>'+[p.A[0],p.B[0],p.A[1],p.B[1],p.C[1],p.D[1]].map(v=>'<td>'+f(v)+'</td>').join('')+'</tr>').join('');
    function evaluate(curve,method,x) {
      const fit = fits[curve][method], knots = fit.knots_x_mm;
      let j = knots.length-2;
      for (let k=0;k<knots.length-1;k++) if (x <= knots[k+1]) { j=k; break; }
      const dx=x-knots[j], c=fit.segments[j].coeffs_descending_in_dx_mm;
      return ((c[0]*dx+c[1])*dx+c[2])*dx+c[3];
    }
    const cApprox = x => evaluate('d',translation.method || 'natural_cubic',x-translation.dx_mm)+translation.dy_mm;
    function curvePoints(curve,method) {
      const xs=fits[curve][method].knots_x_mm;
      const pts=[];
      for(let j=0;j<xs.length-1;j++) for(let k=0;k<24;k++){
        const x=xs[j]+(xs[j+1]-xs[j])*k/24;
        pts.push([x,evaluate(curve,method,x)]);
      }
      pts.push([xs.at(-1),evaluate(curve,method,xs.at(-1))]); return pts;
    }
    function bezierSegments(curve,method) {
      const fit=fits[curve][method], xs=fit.knots_x_mm, ys=fit.knots_y_mm;
      return xs.slice(0,-1).map((x0,j)=>{
        const x1=xs[j+1], y0=Array.isArray(ys)?ys[j]:evaluate(curve,method,x0), y1=Array.isArray(ys)?ys[j+1]:evaluate(curve,method,x1);
        return {index:j,points:bezierControlsFromSegment(x0,x1,y0,y1,fit.segments[j].coeffs_descending_in_dx_mm)};
      });
    }
    function draw() {
      const method=methodSelect.value, selected=pieceSelect.value, step=Number(byId('step').value);
      const bezierByCurve=Object.fromEntries(['b','c','d'].map(key=>[key,bezierSegments(key,method)]));
      const showBezierPoints=byId('bezier-points').checked, showBezierHandles=byId('bezier-handles').checked;
      const allBezierPoints=showBezierPoints||showBezierHandles?['b','c','d'].flatMap(key=>bezierByCurve[key].flatMap(segment=>[segment.points[1],segment.points[2]])):[];
      const chosen=pieces.filter(p=>selected==='all'||p.id===selected);
      const xmin=selected==='all'?0:chosen[0].A[0], xmax=selected==='all'?W:chosen[0].B[0];
      const height=selected==='all'?H:Math.max(...chosen.flatMap(p=>[p.C[1],p.D[1]]));
      const scale=Math.min(650/Math.max(xmax-xmin,1),605/Math.max(height,1));
      const offset=55+(650-(xmax-xmin)*scale)/2;
      const px=x=>offset+(x-xmin)*scale, py=y=>650-y*scale;
      const lines=layout.x_verticals_mm.filter(x=>x>=xmin-1e-8&&x<=xmax+1e-8);
      let out='<rect x="'+px(xmin)+'" y="'+py(height)+'" width="'+((xmax-xmin)*scale)+'" height="'+height*scale+'" fill="#fff8e6" stroke="#ac6647" stroke-width="2"/>';
      for(const x of lines) out+='<line x1="'+px(x)+'" y1="'+py(0)+'" x2="'+px(x)+'" y2="'+py(height)+'" stroke="#a1ad9e" stroke-width="0.9"/>';
      out+='<line x1="'+px(xmin)+'" y1="'+py(0)+'" x2="'+px(xmax)+'" y2="'+py(0)+'" stroke="#355e48" stroke-width="3"/>';
      if(step>=3) for (const p of chosen) {
        const points=[p.A,p.B,p.C,p.D].map(q=>px(q[0])+','+py(q[1])).join(' ');
        out+='<polygon points="'+points+'" fill="#78a789" fill-opacity=".28" stroke="#426c52" stroke-width="1.3"/>';
        out+='<text x="'+((px(p.A[0])+px(p.B[0]))/2)+'" y="'+py((p.B[1]+p.C[1]+p.D[1])/4)+'" text-anchor="middle" font-size="11" fill="#2f5940">'+esc(p.id)+'</text>';
      }
      if(step>=2){
        for(const key of ['b','c','d']){
          const pts=curvePoints(key,method).filter(q=>q[0]>=xmin&&q[0]<=xmax);
          out+='<polyline points="'+pts.map(q=>px(q[0])+','+py(q[1])).join(' ')+'" fill="none" stroke="'+colors[key]+'" stroke-width="2.2"/>';
        }
        for(const p of chosen) for(const key of ['A','B','C','D']){
          const q=p[key]; out+='<circle cx="'+px(q[0])+'" cy="'+py(q[1])+'" r="2.6" fill="'+(colors[key.toLowerCase()]||'#355e48')+'"/>';
          if(selected!=='all') out+='<text x="'+(px(q[0])+(key==='A'||key==='D'?-8:8))+'" y="'+(py(q[1])-6)+'" text-anchor="'+(key==='A'||key==='D'?'end':'start')+'" font-size="15">'+key+'</text>';
        }
      }
      out+='<text x="380" y="686" text-anchor="middle" font-size="13">x = '+f(xmin)+' … '+f(xmax)+' mm；底线 y = 0</text>';
      byId('layout').innerHTML=out;
      const all=['b','c','d'].flatMap(k=>curvePoints(k,method).map(q=>[k,...q]));
      const curveY=all.map(q=>q[2]).concat(allBezierPoints.map(point=>point[1]));
      let ymin=Math.min(...curveY),ymax=Math.max(...curveY);
      const margin=Math.max((ymax-ymin)*.07,8);ymin-=margin;ymax+=margin;
      const x=x=>62+x/W*768, y=v=>640-(v-ymin)/(ymax-ymin)*606;
      let linesSvg='';
      for(let i=0;i<=5;i++){
        const yy=ymin+(ymax-ymin)*i/5;
        linesSvg+='<line x1="62" y1="'+y(yy)+'" x2="830" y2="'+y(yy)+'" stroke="#e3e8e0"/><text x="54" y="'+(y(yy)+4)+'" text-anchor="end" font-size="11">'+yy.toFixed(0)+'</text>';
        const xx=W*i/5;
        linesSvg+='<text x="'+x(xx)+'" y="664" text-anchor="middle" font-size="11">'+xx.toFixed(1)+'</text>';
      }
      for(const key of ['b','c','d']){
        const pts=curvePoints(key,method);
        linesSvg+='<polyline points="'+pts.map(q=>x(q[0])+','+y(q[1])).join(' ')+'" stroke="'+colors[key]+'" stroke-width="2.3" fill="none"/>';
        for(const node of data.curve_nodes[key]) linesSvg+='<circle cx="'+x(node.x_mm)+'" cy="'+y(node.y_mm)+'" r="3.3" fill="'+colors[key]+'"><title>'+esc(node.id)+': '+f(node.y_mm)+'</title></circle>';
        const last=pts.at(-1);
        linesSvg+='<text x="'+(x(last[0])-4)+'" y="'+(y(last[1])-10)+'" text-anchor="end" font-size="16" fill="'+colors[key]+'">'+key+'</text>';
      }
      if(showBezierHandles) for(const key of ['b','c','d']) for(const segment of bezierByCurve[key]){
        const [p0,p1,p2,p3]=segment.points;
        linesSvg+='<line x1="'+x(p0[0])+'" y1="'+y(p0[1])+'" x2="'+x(p1[0])+'" y2="'+y(p1[1])+'" stroke="'+colors[key]+'" stroke-width="1.1" stroke-dasharray="4 3" opacity=".65"/>';
        linesSvg+='<line x1="'+x(p2[0])+'" y1="'+y(p2[1])+'" x2="'+x(p3[0])+'" y2="'+y(p3[1])+'" stroke="'+colors[key]+'" stroke-width="1.1" stroke-dasharray="4 3" opacity=".65"/>';
      }
      if(showBezierPoints) for(const key of ['b','c','d']) for(const segment of bezierByCurve[key]) for(const index of [1,2]){
        const point=segment.points[index], px=x(point[0]), py=y(point[1]), r=4.2;
        const title=key.toUpperCase()+' 曲线第'+(segment.index+1)+'段 P'+index+'：('+f(point[0])+', '+f(point[1])+') mm；Bezier 内控制点，一般不在曲线上。';
        linesSvg+='<path d="M '+px+' '+(py-r)+' L '+(px+r)+' '+py+' L '+px+' '+(py+r)+' L '+(px-r)+' '+py+' Z" fill="white" stroke="'+colors[key]+'" stroke-width="1.5"><title>'+esc(title)+'</title></path>';
      }
      if(hasTranslation&&byId('translated').checked){
        const xs=fits.c[method].knots_x_mm, pts=[];
        for(let k=0;k<=240;k++){const xx=xs[0]+(xs.at(-1)-xs[0])*k/240;pts.push(x(xx)+','+y(cApprox(xx)));}
        linesSvg+='<polyline points="'+pts.join(' ')+'" stroke="#73558c" stroke-dasharray="7 5" stroke-width="2.2" fill="none"/>';
      }
      linesSvg+='<text x="445" y="692" text-anchor="middle" font-size="13">x / mm（纵轴放大显示曲线形态）</text>';
      byId('curves').innerHTML=linesSvg;
      renderBezierCoordinates(bezierByCurve);
      byId('curve-stat').textContent=labels[method]+'；蓝 c、黄 d、红 b。实心圆为 B/C/D 插值节点（曲线必过）。'
        +(showBezierPoints?' 菱形为 Bezier 内控制点（一般不在曲线上）。':' 勾选“显示 Bezier 内控制点”可查看控制点。')
        +(showBezierHandles?' 彩色虚线为 Bezier 控制柄。':' 勾选“显示控制柄线”可查看控制柄。')
        +' 紫色虚线（若勾选）为 d 平移近似。';
    }
    function renderBezierCoordinates(segmentsByCurve) {
      const meanings=['端点（曲线必过，也是插值节点）','内控制点（一般不在曲线上）','内控制点（一般不在曲线上）','端点（曲线必过，也是插值节点）'];
      const rows=[];
      for(const key of ['b','c','d']) for(const segment of segmentsByCurve[key]) for(let index=0;index<4;index++){
        const point=segment.points[index];
        rows.push('<tr><td>'+key.toUpperCase()+'</td><td>'+(segment.index+1)+'</td><td>P'+index+'</td><td>'+f(point[0])+'</td><td>'+f(point[1])+'</td><td>'+meanings[index]+'</td></tr>');
      }
      byId('bezier-rows').innerHTML=rows.join('');
    }
    for(const id of ['step','piece','method','translated','bezier-points','bezier-handles']) byId(id).addEventListener('change',draw);
    byId('save-svg').addEventListener('click',()=>{
      const svg=byId('layout').cloneNode(true);svg.setAttribute('xmlns','http://www.w3.org/2000/svg');
      const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));
      const a=document.createElement('a');a.href=url;a.download='立脚飞椽排料.svg';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    draw(); window.layoutViewer={ready:true,pieceCount:pieces.length,draw,evaluate,bezierSegments,bezierControlsFromSegment};
  } catch(error) {
    byId('error').hidden=false;byId('error').textContent='排料数据无法显示：'+error.message;console.error(error);
  }
})();
