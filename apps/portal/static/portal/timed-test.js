(() => {
  const form=document.getElementById('timed-form');if(!form)return;
  const deadline=Date.parse(form.dataset.deadline), offset=Date.parse(form.dataset.serverTime)-Date.now();
  const status=document.getElementById('save-status');let saving=false,finishing=false,dirty=false;
  function palette(){document.querySelectorAll('.test-palette a').forEach(link=>{const key=link.dataset.key;const fields=[...form.querySelectorAll('[name="answer_'+key+'"]')];const answered=fields.some(field=>field.type==='text'?field.value.trim():field.checked);const review=form.querySelector('[name="review_'+key+'"]')?.checked;link.classList.toggle('answered',answered);link.classList.toggle('review',review);});}
  async function save(){if(saving||finishing)return;saving=true;status.textContent='Saving…';const data=new FormData(form);data.set('action','save');try{const response=await fetch(location.href,{method:'POST',body:data,credentials:'same-origin'});if(!response.ok)throw Error();const payload=await response.json();if(payload.result_url){location.href=payload.result_url;return;}status.textContent='Saved '+new Date().toLocaleTimeString();dirty=false;}catch(_){status.textContent='Save failed. Keep this page open and retry.';dirty=true;}finally{saving=false;}}
  form.addEventListener('change',()=>{dirty=true;palette();});form.addEventListener('input',()=>{dirty=true;palette();});
  document.getElementById('save-now').addEventListener('click',save);setInterval(save,10000);
  const questions=[...document.querySelectorAll('.timed-question')];let active=0,subject='all';
  function show(index){active=index;questions.forEach((question,i)=>{question.hidden=i!==index;});document.getElementById('question-position').textContent='Question '+(index+1)+' of '+questions.length;document.querySelectorAll('.test-palette a').forEach(link=>link.classList.toggle('current',link.dataset.key===questions[index].dataset.key));}
  document.querySelectorAll('.test-subjects button').forEach(button=>button.addEventListener('click',()=>{subject=button.dataset.subject;const index=questions.findIndex(question=>subject==='all'||question.dataset.subject===subject);if(index>=0)show(index);document.querySelectorAll('.test-subjects button').forEach(item=>item.classList.toggle('selected',item===button));}));
  document.querySelectorAll('.test-palette a').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();subject='all';show(questions.findIndex(question=>question.dataset.key===link.dataset.key));}));
  document.getElementById('test-previous').addEventListener('click',()=>{for(let i=active-1;i>=0;i--){if(subject==='all'||questions[i].dataset.subject===subject){show(i);break;}}});
  document.getElementById('test-next').addEventListener('click',()=>{save();for(let i=active+1;i<questions.length;i++){if(subject==='all'||questions[i].dataset.subject===subject){show(i);break;}}});
  show(0);
  document.querySelectorAll('.clear-answer').forEach(button=>button.addEventListener('click',()=>{form.querySelectorAll('[name="answer_'+button.dataset.key+'"]').forEach(field=>{if(field.type==='text')field.value='';else field.checked=false;});dirty=true;palette();}));
  form.addEventListener('submit',event=>{if(!finishing&&!confirm('Submit this paper? Answers cannot be changed afterwards.')){event.preventDefault();return;}finishing=true;});
  function tick(){const left=Math.max(0,deadline-(Date.now()+offset));document.getElementById('test-clock').textContent=Math.floor(left/3600000)+':'+String(Math.floor(left/60000)%60).padStart(2,'0')+':'+String(Math.floor(left/1000)%60).padStart(2,'0');if(left<=0&&!finishing){finishing=true;const action=document.createElement('input');action.name='action';action.value='submit';action.type='hidden';form.append(action);form.submit();}}
  window.addEventListener('beforeunload',event=>{if(dirty&&!finishing){event.preventDefault();event.returnValue='';}});palette();tick();setInterval(tick,500);
})();
