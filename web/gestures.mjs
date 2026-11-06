export class TouchGestures {
  constructor({emit,point,mode=()=> 'native',clock=()=>performance.now()}){this.emit=emit;this.point=point;this.mode=mode;this.clock=clock;this.pointers=new Map();this.gesture=null}
  down(event){
    this.pointers.set(event.pointerId,event);
    if(this.pointers.size===1){
      const start=this.point(event);this.gesture={mode:this.mode(),started:this.clock(),start,startClientX:event.clientX,startClientY:event.clientY,last:event,multi:false,moved:false,lastY:event.clientY};
      if(this.gesture.mode==='native')this.emit({type:'touch',phase:'down',...start});
      else if(this.gesture.mode==='drag')this.emit({type:'button',button:0,down:true,...start});
    }else if(this.gesture){
      const g=this.gesture;
      if(!g.multi){
        if(g.mode==='native')this.emit({type:'touch',phase:'cancel',...this.point(g.last)});
        if(g.mode==='drag')this.emit({type:'button',button:0,down:false,...this.point(g.last)});
        const pair=[...this.pointers.values()];g.mid=this.point({clientX:(pair[0].clientX+pair[1].clientX)/2,clientY:(pair[0].clientY+pair[1].clientY)/2});
        g.origins=new Map(pair.map(p=>[p.pointerId,{x:p.clientX,y:p.clientY}]));g.lastY=this.averageY();g.multi=true;
      }
      if(this.pointers.size>2)g.moved=true;
    }
  }
  averageY(){return [...this.pointers.values()].reduce((sum,event)=>sum+event.clientY,0)/this.pointers.size}
  move(event){
    if(!this.pointers.has(event.pointerId)||!this.gesture)return;
    this.pointers.set(event.pointerId,event);const g=this.gesture;
    if(g.multi){
      const origin=g.origins.get(event.pointerId);if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>8)g.moved=true;
      if(this.pointers.size===2){const y=this.averageY(),delta=(y-g.lastY)*3;if(g.moved&&delta)this.emit({type:'wheel',delta:Math.round(delta),...g.mid});g.lastY=y}
    }else{
      if(Math.hypot(event.clientX-g.last.clientX,event.clientY-g.last.clientY)>0){
        if(g.mode==='native')this.emit({type:'touch',phase:'move',...this.point(event)});
        else if(g.mode==='drag')this.emit({type:'move',...this.point(event)});
        else {const delta=(event.clientY-g.lastY)*3;if(Math.hypot(event.clientX-g.startClientX,event.clientY-g.startClientY)>6)g.moved=true;if(g.moved&&delta)this.emit({type:'wheel',delta:Math.round(delta),...g.start})}
      }
      g.last=event;g.lastY=event.clientY;
    }
  }
  up(event){
    if(!this.pointers.has(event.pointerId))return;const g=this.gesture;
    if(g&&!g.multi){
      if(g.mode==='native')this.emit({type:'touch',phase:'up',...this.point(event)});
      else if(g.mode==='drag')this.emit({type:'button',button:0,down:false,...this.point(event)});
      else if(!g.moved){this.emit({type:'button',button:0,down:true,...g.start});this.emit({type:'button',button:0,down:false,...g.start})}
    }
    this.pointers.delete(event.pointerId);
    if(!this.pointers.size){
      if(g?.multi&&!g.moved&&this.clock()-g.started<450){this.emit({type:'button',button:2,down:true,...g.mid});this.emit({type:'button',button:2,down:false,...g.mid})}
      this.gesture=null;
    }
  }
  cancel(){const g=this.gesture;if(g&&!g.multi){if(g.mode==='native')this.emit({type:'touch',phase:'cancel',...this.point(g.last)});if(g.mode==='drag')this.emit({type:'button',button:0,down:false,...this.point(g.last)})}this.pointers.clear();this.gesture=null}
}

export class TrackpadGestures {
  constructor({emit,haptic=()=>{},speed=()=>1.5,clock=()=>performance.now()}){this.emit=emit;this.haptic=haptic;this.speed=speed;this.clock=clock;this.pointers=new Map();this.gesture=null;this.dragging=false}
  down(event){
    this.pointers.set(event.pointerId,event);
    if(this.pointers.size===1)this.gesture={started:this.clock(),moved:false,multi:false,lastX:event.clientX,lastY:event.clientY,origins:new Map([[event.pointerId,{x:event.clientX,y:event.clientY}]])};
    else if(this.gesture){const g=this.gesture;g.multi=true;g.lastY=this.averageY();g.origins.set(event.pointerId,{x:event.clientX,y:event.clientY});if(this.pointers.size>2)g.moved=true}
  }
  averageY(){return [...this.pointers.values()].reduce((sum,e)=>sum+e.clientY,0)/this.pointers.size}
  move(event){
    if(!this.pointers.has(event.pointerId)||!this.gesture)return;this.pointers.set(event.pointerId,event);const g=this.gesture,origin=g.origins.get(event.pointerId);
    if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>5)g.moved=true;
    if(g.multi){if(this.pointers.size===2){const y=this.averageY(),delta=(y-g.lastY)*3;if(g.moved&&delta)this.emit({type:'wheel',delta:Math.round(delta)});g.lastY=y}}
    else{const dx=(event.clientX-g.lastX)*this.speed(),dy=(event.clientY-g.lastY)*this.speed();if(dx||dy)this.emit({type:'relative',dx,dy});g.lastX=event.clientX;g.lastY=event.clientY}
  }
  click(button=0){this.emit({type:'button',button,down:true});this.emit({type:'button',button,down:false});this.haptic()}
  up(event){if(!this.pointers.has(event.pointerId))return;this.pointers.delete(event.pointerId);if(!this.pointers.size){const g=this.gesture;if(g&&!g.moved&&this.clock()-g.started<400)this.click(g.multi?2:0);this.gesture=null}}
  toggleDrag(){this.dragging=!this.dragging;this.emit({type:'button',button:0,down:this.dragging});this.haptic();return this.dragging}
  cancel(){if(this.dragging)this.emit({type:'button',button:0,down:false});this.dragging=false;this.pointers.clear();this.gesture=null}
}
