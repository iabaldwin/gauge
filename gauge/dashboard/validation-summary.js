/* Gauge-specific quality and correlation readout. Pixel Lab remains task-agnostic. */
(function (root) {
  'use strict';
  class ValidationSummary {
    constructor(el) { this.el=el;this.signature=''; }
    update(validation) {
      const signature=JSON.stringify(validation || null);
      if(signature===this.signature)return;
      this.signature=signature;this.el.hidden=!validation;
      if(!validation){this.el.replaceChildren();return;}
      const wasOpen=this.el.open;
      this.el.replaceChildren();this.el.open=wasOpen;
      const add=(tag,text,parent=this.el)=>{const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;};
      add('summary','Dataset validation · gold-label quality & trace correlation');
      const body=add('div','');body.className='validation-body';
      const c=validation.correlation || {};
      add('p',`${c.mapped_calls ?? '—'}/${c.expected_calls ?? '—'} exchanges matched · ${c.correct_parent_links ?? '—'}/${c.explicit_parent_links ?? '—'} explicit parent links · ${c.false_trace_merges ?? '—'} false merges · ${c.history_only_turns_left_unresolved ?? '—'} history-only turns left unresolved`,body);
      add('p','Quality uses dataset labels. Literal agreement with the source is shown separately below. Conversation quality uses answer annotations or instruction checkers.',body);
      const scroll=add('div','',body);scroll.className='validation-table-scroll';
      const table=add('table','',scroll);table.setAttribute('aria-label','Dataset quality scores');
      const head=add('tr','',add('thead','',table));
      for(const label of ['Dataset','Model','Scored / calls','Quality'])add('th',label,head).scope='col';
      const rows=add('tbody','',table);
      for(const [key,value] of Object.entries(validation.quality || {})){
        const slash=key.indexOf('/'),dataset=key.slice(0,slash),model=key.slice(slash+1).split('/').pop();
        const row=add('tr','',rows);
        add('td',dataset,row);add('td',model,row);add('td',`${value.scored} / ${value.calls}`,row);
        const metrics=Object.entries(value.metrics).map(([name,m])=>`${name}: ${(m.mean*100).toFixed(1)}% (n=${m.n})`).join(' · ');
        add('td',metrics || 'Not scored',row);
      }
    }
  }
  root.GaugeValidationSummary=ValidationSummary;
})(globalThis);
