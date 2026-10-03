(() => {
  const key = 'yuanli-ui-proof-v1';
  const user = {name:'陈予安',phone:'13800138026',company:'青禾科技',location:'A 座 · 3 层'};
  const statuses = {pending:'待派单',assigned:'已派单',repairing:'维修中',confirm:'待确认',closed:'已完成'};
  const devices = [
    {id:'KT-A301',name:'中央空调室内机',location:'A 座 · 3 层 · 会议室',category:'空调通风',code:'KT-A301',model:'风管式空调机组',lastService:'2026-09-18'},
    {id:'JS-A302',name:'茶水间净水设备',location:'A 座 · 3 层 · 茶水间',category:'给排水',code:'JS-A302',model:'商用净水设备',lastService:'2026-09-20'},
    {id:'ZM-B201',name:'走廊应急照明',location:'B 座 · 2 层 · 东侧走廊',category:'照明电气',code:'ZM-B201',model:'应急照明灯具',lastService:'2026-09-25'},
    {id:'MJ-A101',name:'主入口自动感应门',location:'A 座 · 1 层 · 大堂',category:'门禁设施',code:'MJ-A101',model:'双开平移感应门',lastService:'2026-09-12'},
    {id:'KT-C401',name:'新风系统送风机与控制组件',location:'C 座 · 4 层 · 开放办公区',category:'空调通风',code:'KT-C401',model:'新风处理机组',lastService:'2026-09-15'},
    {id:'GP-B101',name:'卫生间洗手台排水管',location:'B 座 · 1 层 · 公共卫生间',category:'给排水',code:'GP-B101',model:'公共设施管路',lastService:'2026-09-10'}
  ];
  const technicians = [
    {id:'T01',name:'周师傅',fullName:'周明远',specialty:'空调通风',phone:'13800138017',shift:'08:30–17:30'},
    {id:'T02',name:'林师傅',fullName:'林晓峰',specialty:'给排水',phone:'13800138018',shift:'08:30–17:30'},
    {id:'T03',name:'吴师傅',fullName:'吴建华',specialty:'照明电气',phone:'13800138019',shift:'09:00–18:00'},
    {id:'T04',name:'郑师傅',fullName:'郑文涛',specialty:'门禁设施',phone:'13800138020',shift:'09:00–18:00'}
  ];
  const specs = [
    ['会议室空调漏水，地面有积水',0,'pending','紧急','陈予安','空调运行约半小时后，出风口持续滴水，会议室东侧地面有积水。已暂停使用，请检查冷凝水排水管与接头。','2026-10-03 09:42'],
    ['茶水间净水设备出水量变小',1,'pending','普通','陈予安','最近两天出水量明显变小，指示灯没有报警。请安排检查滤芯和进水阀。','2026-10-03 09:28'],
    ['东侧走廊照明反复闪烁',2,'repairing','普通','许知夏','东侧第三盏灯持续闪烁，已反馈给服务台。','2026-10-03 09:10'],
    ['入口感应门无法正常开启',3,'assigned','紧急','陆文川','感应门在有人靠近时没有打开，暂时从侧门通行。','2026-10-03 08:56'],
    ['新风系统送风不足并伴有异响',4,'pending','普通','江映澄','开放办公区送风明显减弱，设备运行时有间歇性异响。位置靠近北侧会议区，需检查风机与控制组件。','2026-10-03 08:45'],
    ['洗手台下方排水接头渗漏',5,'confirm','普通','陈予安','洗手时下方接头渗水，地面较湿。请检查并更换密封件。','2026-10-03 08:32'],
    ['会议室空调遥控失灵',0,'closed','普通','陈予安','遥控器更换电池后仍无法操作。','2026-10-02 16:20'],
    ['公共卫生间排水缓慢',5,'assigned','普通','赵雨禾','洗手台排水速度很慢，积水约五分钟后才能排空。','2026-10-02 15:18'],
    ['茶水间设备接水盘松动',1,'repairing','普通','顾景行','接水盘卡扣松动，放杯子时有晃动。','2026-10-02 14:50'],
    ['办公区新风控制面板无显示',4,'pending','紧急','沈嘉言','控制面板没有显示，无法确认设备是否运行。','2026-10-02 14:15'],
    ['感应门关闭时发出异响',3,'closed','普通','陈予安','关闭时轨道有明显摩擦声。','2026-10-02 11:40'],
    ['走廊应急照明电池指示异常',2,'confirm','普通','唐思远','电池状态灯显示异常，需安排检查。','2026-10-02 10:20']
  ];
  const seed = specs.map((x,i) => {
    const d = devices[x[1]], tech = technicians.find(t=>t.specialty===d.category);
    const row={id:`BX20261003${String(i+1).padStart(3,'0')}`,title:x[0],deviceId:d.id,device:d.name,location:d.location,category:d.category,status:x[2],priority:x[3],reporter:x[4],phone:x[4]===user.name?user.phone:'13800138066',description:x[5],created:x[6],technician:x[2]==='pending'?'':tech.id,appointment:'2026-10-03 14:00',images:[],timeline:[{time:x[6],title:'已提交报修',note:`${x[4]}提交报修，等待服务中心派单。`}]};
    if(x[2]!=='pending') row.timeline.push({time:x[6].slice(0,11)+'10:10',title:'已安排维修',note:`${tech.name}负责处理，服务中心已完成派单。`});
    if(['repairing','confirm','closed'].includes(x[2]))row.timeline.push({time:'2026-10-03 10:25',title:'正在维修',note:'维修人员已到场，正在排查问题。'});
    if(['confirm','closed'].includes(x[2])){row.summary=i===5?'已更换排水接头密封圈，测试排水正常，无渗漏。':'设备已检修，运行测试正常。';row.timeline.push({time:'2026-10-03 11:05',title:'维修已完成',note:row.summary});}
    if(x[2]==='closed')row.timeline.push({time:'2026-10-03 11:20',title:'报修人已确认',note:'问题已解决，本次服务结束。'});
    return row;
  });
  let records, failNext=false;
  try { records=JSON.parse(localStorage.getItem(key))||structuredClone(seed); } catch { records=structuredClone(seed); }
  const listeners=new Set();
  const stamp=()=>new Date().toLocaleString('sv-SE').slice(0,16);
  const notify=()=>listeners.forEach(fn=>fn());
  const persist=()=>{localStorage.setItem(key,JSON.stringify(records));notify();};
  const list=()=>structuredClone(records);
  const get=id=>structuredClone(records.find(x=>x.id===id));
  const wait=async()=>{await new Promise(resolve=>setTimeout(resolve,500));if(failNext){failNext=false;throw new Error('模拟网络请求失败，请重试。');}};
  const find=id=>{const row=records.find(x=>x.id===id);if(!row)throw new Error('报修记录不存在。');return row;};
  window.addEventListener('storage',event=>{if(event.key===key){records=event.newValue?JSON.parse(event.newValue):structuredClone(seed);notify();}});
  const stats=()=>Object.fromEntries(Object.keys(statuses).map(s=>[s,records.filter(x=>x.status===s).length]));
  window.Park={user,devices,technicians,statuses,list,get,stats,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},
    failOnce(){failNext=true;},
    reset(){records=structuredClone(seed);persist();},
    async create(payload){
      const device=devices.find(d=>d.id===payload.deviceId);
      if(!device)throw new Error('请选择报修设备。');
      if(!payload.description?.trim())throw new Error('请填写故障描述。');
      if(Array.from(payload.description).length>300)throw new Error('故障描述不能超过300字。');
      if((payload.images||[]).length>3)throw new Error('最多添加3张照片。');
      if(!payload.reporter?.trim()||!/^1\d{10}$/.test(payload.phone))throw new Error('请检查联系人和手机号。');
      await wait();
      const created=stamp(),nextNumber=Math.max(0,...records.map(r=>Number(r.id.slice(-3))))+1,id=`BX${created.slice(0,10).replaceAll('-','')}${String(nextNumber).padStart(3,'0')}`;
      const row={id,title:payload.title||payload.description.trim().slice(0,22),deviceId:device.id,device:device.name,location:device.location,category:device.category,priority:payload.priority||'普通',status:'pending',description:payload.description.trim(),reporter:payload.reporter.trim(),phone:payload.phone,created,technician:'',images:payload.images||[],timeline:[{time:created,title:'已提交报修',note:'报修已送达服务中心，等待派单。'}]};
      records.unshift(row);persist();return get(id);
    },
    async dispatch(id,technician,appointment,note){
      if(find(id).status!=='pending')throw new Error('当前报修无需重复派单。');
      const tech=technicians.find(t=>t.id===technician);if(!tech)throw new Error('请选择维修人员。');
      await wait();const row=find(id);if(row.status!=='pending')throw new Error('报修状态已更新，请重新查看。');
      row.status='assigned';row.technician=tech.id;row.appointment=appointment||'';row.timeline.push({time:stamp(),title:'已安排维修',note:`${tech.name}负责处理。${note||''}`});persist();return get(id);
    },
    async complete(id,summary){
      if(!['assigned','repairing'].includes(find(id).status))throw new Error('当前状态不能登记维修结果。');
      if(!summary?.trim())throw new Error('请填写维修结果。');
      await wait();const row=find(id);row.status='confirm';row.summary=summary.trim();row.timeline.push({time:stamp(),title:'维修已完成',note:row.summary});persist();return get(id);
    },
    async confirm(id){
      const existing=find(id);if(existing.status!=='confirm'||existing.reporter!==user.name)throw new Error('当前记录不能确认完成。');
      await wait();const row=find(id);row.status='closed';row.timeline.push({time:stamp(),title:'报修人已确认',note:'问题已解决，本次服务结束。'});persist();return get(id);
    }
  };
})();
