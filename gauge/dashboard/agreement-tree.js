/* Reference-prefix evaluation UI. The SDK and standalone Pixel Lab have no dataset dependency. */
(function(root){
  'use strict';
  const names={equivalent:'Agrees',different:'Diverges',uncertain:'Uncertain'};
  const short=name=>name.split('/').pop();
  const add=(parent,tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;parent.append(el);return el;};
  class AgreementTree {
    constructor(el){this.el=el;this.signature='';this.selected='';this.model='';this.openNodes=new Set();this.onToggle=event=>{const key=event.target.dataset.agreementKey;if(key){if(event.target.open)this.openNodes.add(key);else this.openNodes.delete(key);}};this.el.addEventListener('toggle',this.onToggle,true);}
    update(data){
      const signature=JSON.stringify(data||null);if(signature===this.signature)return;
      this.signature=signature;this.data=data;this.render();
    }
    render(){
      this.el.replaceChildren();
      const data=this.data;this.el.hidden=!data?.conversations?.length;if(this.el.hidden)return;
      add(this.el,'span','GAUGE / CONVERSATION AGREEMENT','pl-eyebrow');
      
      add(this.el,'h2','Where does agreement break?');
      add(this.el,'p','CoQA and QuAC use JEV to compare answer meaning against human references and the full request context. F1 is reported separately. Multi-IF uses explicit instruction checks and shared OpenAI history. Later agreement does not erase an earlier departure.','agreement-intro');
      const counts=data.statuses;
      add(this.el,'p',`${data.conversations.length} conversations · ${(counts.scored||0)+(counts.completed||0)} decided · ${counts.uncertain||0} uncertain · ${counts.pending||0} pending · ${counts.unjudged||0} unjudged · ${(counts.unavailable||0)+(counts.error||0)+(counts.not_scored||0)} unavailable / unscored`,'agreement-meta');
      const summary=add(this.el,'div',undefined,'agreement-overview');
      const chart=add(summary,'div',undefined,'agreement-distribution');
      add(chart,'h3','First confirmed departure');
      const maxTurn=Math.max(1,...data.conversations.flatMap(c=>c.turns.map(t=>t.turn+1)));
      const peak=Math.max(1,...Object.values(data.models).flatMap(s=>Object.entries(s.first_divergence).filter(([k])=>k!=='earlier_unresolved').map(([,v])=>v)));
      for(let turn=1;turn<=maxTurn;turn++){
        const group=add(chart,'div',undefined,'agreement-bin');add(group,'strong','Turn '+turn);
        for(const [model,stats] of Object.entries(data.models)){
          const count=stats.first_divergence[String(turn)]||0;
          const item=add(group,'div',undefined,'agreement-bar-row');item.setAttribute('aria-label',`${short(model)}, turn ${turn}: ${count} first confirmed departures`);
          add(item,'span',short(model));const bar=add(item,'i');bar.style.width=(count/peak*100)+'%';add(item,'b',count);
        }
      }
      const statsBox=add(summary,'div',undefined,'agreement-stats');
      const table=add(statsBox,'table');table.setAttribute('aria-label','Conversation agreement summary');
      const head=add(add(table,'thead'),'tr');['Model','Known first turn','Earlier unresolved','All agree'].forEach(t=>add(head,'th',t));
      const body=add(table,'tbody');
      for(const [model,stats] of Object.entries(data.models)){
        const row=add(body,'tr');add(row,'th',short(model));add(row,'td',Object.entries(stats.first_divergence).filter(([key])=>key!=='earlier_unresolved').reduce((a,[,b])=>a+b,0));
        add(row,'td',stats.first_divergence.earlier_unresolved||0);add(row,'td',stats.all_agree);
      }
      add(statsBox,'p','A QA departure requires a confident JEV meaning difference and a usable reference. Instruction failures use Multi-IF checks. F1 never determines tree agreement. Uncertain judgments and missing outputs prevent an exact first-departure claim.');
      const filters=add(this.el,'div',undefined,'agreement-controls');
      const label=add(filters,'label','Conversation '),picker=add(label,'select');picker.setAttribute('aria-label','Agreement conversation');
      for(const c of data.conversations){const option=add(picker,'option',`${c.id.split('/').slice(-2).join('/')} · ${c.turns.length} turns`);option.value=c.id;}
      if(!data.conversations.some(c=>c.id===this.selected))this.selected=data.conversations[0].id;
      picker.value=this.selected;picker.onchange=()=>{this.selected=picker.value;this.drawDetail();};
      const ml=add(filters,'label','Model '),mp=add(ml,'select');mp.setAttribute('aria-label','Agreement model');add(mp,'option','All models').value='';
      for(const m of Object.keys(data.models))add(mp,'option',short(m)).value=m;
      mp.value=this.model;mp.onchange=()=>{this.model=mp.value;this.drawDetail();};
      this.detail=add(this.el,'div',undefined,'agreement-detail');this.drawDetail();
    }
    drawDetail(){
      this.detail.replaceChildren();const c=this.data.conversations.find(c=>c.id===this.selected);if(!c)return;const native=c.turns[0]?.assessment?.task==='instruction';
      const lanes=add(this.detail,'div',undefined,'agreement-lanes');
      for(const [model,summary] of Object.entries(c.models)){
        if(this.model&&this.model!==model)continue;
        const first=summary.first_divergence;
        const card=add(lanes,'div',undefined,'agreement-lane');add(card,'strong',short(model));
        add(card,'span',first?(first.exact?`First ${native?'instruction failure':'divergence'} · turn ${first.turn}`:`${native?'Failure':'Divergence'} observed · turn ${first.turn}; earlier turns unresolved`):(summary.all_agree?(native?'Passes all evaluated turns':'Agrees through all evaluated turns'):'Evaluation unresolved'));
        const total=this.data.models[model].agreement_through;
        add(card,'small',(native?'Suite passes through turn: ':'Suite agreement through turn: ')+Object.entries(total).map(([t,v])=>`${t}: ${v.agreed}/${v.total}`).join(' · '));
      }
      const spine=add(this.detail,'ol',undefined,'agreement-spine');
      for(const turn of c.turns){
        const node=add(spine,'li',undefined,'agreement-turn');
        add(node,'span',`TURN ${turn.turn+1}`,'pl-eyebrow');
        const finalUser=[...turn.context].reverse().find(m=>m.role==='user');
        add(node,'h3',typeof finalUser?.content==='string'?finalUser.content:'Request unavailable');
        add(node,'p',`${turn.dataset} · ${turn.history_mode==='source'?'shared OpenAI-generated history':'recorded dataset history'}`,'agreement-meta');
        const reference=add(node,'div',undefined,'agreement-reference');add(reference,'strong',turn.assessment?.task==='instruction'?'Instruction criteria':'Dataset reference');add(reference,'p',turn.reference);
        if(turn.assessment){const criteria=add(reference,'details');add(criteria,'summary',native?'Instruction checks':'Human reference answers');
          if(native)add(criteria,'pre',JSON.stringify(turn.assessment.expected,null,2));
          else {const counts=new Map();for(const answer of turn.assessment.expected.answers)counts.set(answer,(counts.get(answer)||0)+1);const list=add(criteria,'ul');for(const [answer,count] of counts)add(list,'li',`${answer}${count>1?' ×'+count:''}`);}}

        if(turn.evidence){const evidence=add(reference,'details');add(evidence,'summary','Dataset evidence');add(evidence,'p',typeof turn.evidence==='string'?turn.evidence:JSON.stringify(turn.evidence));}
        const context=add(node,'details',undefined,'agreement-context');context.dataset.agreementKey=c.id+'/'+turn.turn+'/context';context.open=this.openNodes.has(context.dataset.agreementKey);add(context,'summary',`Full request context · ${turn.context.length} messages`);
        for(const message of turn.context){add(context,'strong',message.role);add(context,'pre',typeof message.content==='string'?message.content:JSON.stringify(message.content,null,2));}
        const branches=add(node,'div',undefined,'agreement-branches');
        for(const [model,output] of Object.entries(turn.outputs)){
          if(this.model&&this.model!==model)continue;
          const j=output.judgment||{},verdict=j.verdict||'uncertain';
          const branch=add(branches,'details',undefined,'agreement-branch');branch.dataset.verdict=verdict;branch.dataset.agreementKey=c.id+'/'+turn.turn+'/'+model;branch.open=this.openNodes.has(branch.dataset.agreementKey);
          const heading=add(branch,'summary');add(heading,'strong',short(model));
          const reference=j.answers?.reference_validity?.verdict;
          const label=native?(output.score?.status==='scored'?(output.score.passed?'Passes criterion':'Criterion failed'):'Not scored'):reference&&reference!=='valid'?`Uncertain · reference ${reference}`:['completed','uncertain'].includes(j.status)?names[verdict]:j.status||'unjudged';
          add(heading,'span',label,'agreement-badge');
          const first=c.models[model]?.first_divergence;
          if(first?.turn===turn.turn+1)add(heading,'small',first.exact?(native?'FIRST INSTRUCTION FAILURE':'FIRST CONFIRMED DIVERGENCE'):'EARLIER TURNS UNRESOLVED');
          add(branch,'p',output.text||'No complete output available.');
          add(branch,'p',`Decision: ${j.reason==='none'?'Same substantive answer':j.reason||'Awaiting reference evaluation'}${Number.isFinite(j.confidence)?` · semantic confidence ${(j.confidence*100).toFixed(0)}%`:''}`,'agreement-meta');
          if(output.score){const score=add(branch,'details');add(score,'summary',native?'Instruction check results':'Benchmark metrics · separate from agreement');
            if(native)add(score,'pre',JSON.stringify(output.score,null,2));
            else {const metrics=add(score,'dl');for(const name of ['f1','exact_match','human_f1','human_equivalence'])if(Number.isFinite(output.score[name])){add(metrics,'dt',name.replaceAll('_',' '));add(metrics,'dd',(output.score[name]*100).toFixed(1)+'%');}if(output.score.reason)add(score,'p',output.score.reason.replaceAll('_',' '));add(score,'p',output.score.scorer||'Not scored');}}

          if(j.answers){const evidence=add(branch,'dl');for(const [name,answer] of Object.entries(j.answers)){add(evidence,'dt',name.replaceAll('_',' '));add(evidence,'dd',`${answer.verdict} · ${(answer.confidence*100).toFixed(0)}%`);}}
        }
      }
    }
    destroy(){this.el.removeEventListener('toggle',this.onToggle,true);this.el.replaceChildren();}
  }
  root.GaugeAgreementTree=AgreementTree;
})(globalThis);
