export class TouchGestures {
  constructor({emit,point,setTimer=setTimeout,clearTimer=clearTimeout}){this.emit=emit;this.point=point;this.setTimer=setTimer;this.clearTimer=clearTimer;this.pointers=new Map();this.gesture=null;this.timer=null}
  down(event){
    this.pointers.set(event.pointerId,event);
    if(this.pointers.size===1){
      const start=this.point(event);this.gesture={x:event.clientX,y:event.clientY,lastY:event.clientY,start,scroll:false,drag:false};
      this.emit({type:'move',...start});
      this.timer=this.setTimer(()=>{if(this.gesture&&!this.gesture.scroll&&this.pointers.size===1){this.gesture.drag=true;this.emit({type:'button',button:0,down:true,...this.gesture.start})}},350);
    }else{
      this.clearTimer(this.timer);if(this.gesture?.drag)this.emit({type:'button',button:0,down:false});
      if(this.gesture){this.gesture.scroll=true;this.gesture.drag=false;this.gesture.lastY=this.averageY()}
    }
  }
  averageY(){return [...this.pointers.values()].reduce((sum,event)=>sum+event.clientY,0)/this.pointers.size}
  move(event){
    if(!this.pointers.has(event.pointerId)||!this.gesture)return;
    this.pointers.set(event.pointerId,event);const g=this.gesture,y=this.pointers.size>1?this.averageY():event.clientY;
    if(g.drag){this.emit({type:'move',...this.point(event)});return}
    if(this.pointers.size>1||Math.hypot(event.clientX-g.x,event.clientY-g.y)>8){this.clearTimer(this.timer);g.scroll=true}
    if(g.scroll){const delta=(y-g.lastY)*3;if(delta)this.emit({type:'wheel',delta:Math.round(delta),...g.start})}
    g.lastY=y;
  }
  up(event){
    if(!this.pointers.has(event.pointerId))return;
    this.clearTimer(this.timer);const g=this.gesture;
    if(this.pointers.size===1&&g){
      if(g.drag)this.emit({type:'button',button:0,down:false,...this.point(event)});
      else if(!g.scroll){const p=this.point(event);this.emit({type:'button',button:0,down:true,...p});this.emit({type:'button',button:0,down:false,...p})}
    }
    this.pointers.delete(event.pointerId);if(!this.pointers.size)this.gesture=null;else if(g)g.lastY=this.averageY();
  }
  cancel(){this.clearTimer(this.timer);if(this.gesture?.drag)this.emit({type:'button',button:0,down:false});this.pointers.clear();this.gesture=null}
}
