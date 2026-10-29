"""Tests for the 0.27.0 network write guard (and, 0.28.0, its ONE-SHOT ALLOWANCE): the JS that the probe, kbclick and
kbopt install on every call (no request leaves the page with a method other than
GET), run in node against stubbed fetch / XMLHttpRequest / sendBeacon /
HTMLFormElement and a submit listener; the Python mirror (request_blocked,
form_blocked) agrees with the JS rule; every generated JS parses. Hermetic: no
browser, no network.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)

import kb_capture as C

NODE = shutil.which("node")

HARNESS = r"""
const vm=require('vm');const fs=require('fs');
const sc=JSON.parse(fs.readFileSync(0,'utf8'));
const log=[],listeners=[];
class XHR{open(m,u){log.push('xhr.open '+m);}send(b){log.push('xhr.send');return 'sent';}}
class Form{constructor(m,a){this.method=m;this.action=a||'/save/';}
  submit(){log.push('form.submit');return 'sub';}
  requestSubmit(s){log.push('form.requestSubmit');return 'rs';}}
const ctx={URL,log,listeners,Form,XHR,G:sc.guard,PROBE:sc.probe,CLICK:sc.click,OPT:sc.opt};
ctx.window=ctx;
ctx.location={href:'https://demo.example/app/',pathname:'/app/',search:''};
ctx.innerWidth=1470;ctx.innerHeight=802;ctx.devicePixelRatio=2;
ctx.fetch=function(i,o){log.push('fetch');return Promise.resolve('real');};
ctx.XMLHttpRequest=XHR;ctx.HTMLFormElement=Form;
ctx.navigator={sendBeacon(u,d){log.push('beacon');return true;}};
ctx.sessionStorage={getItem:()=>null};
ctx.getComputedStyle=()=>({cursor:'',backgroundColor:'rgba(0,0,0,0)'});
ctx.document={title:'',body:{innerText:'',querySelectorAll:()=>[]},querySelectorAll:()=>[],querySelector:()=>null,
  addEventListener(t,f,c){listeners.push({t,f,c});},elementFromPoint:()=>null};
vm.createContext(ctx);
if(sc.preinstall!==false)vm.runInContext(sc.guard,ctx);
(async()=>{
  let out;
  try{out=await vm.runInContext('(async()=>{'+sc.scenario+'})()',ctx);}catch(e){out={threw:String(e&&e.message||e)};}
  console.log(JSON.stringify({out,log,blocked:ctx.__kbBlocked,last:ctx.__kbLast,listeners:listeners.length}));
})();
"""


def run(scenario, preinstall=True):
    r = subprocess.run([NODE, "-e", HARNESS], capture_output=True, text=True, timeout=60,
                       input=json.dumps({"guard": C.guard_source(), "probe": C.probe_source(),
                                         "click": C.click_source(), "opt": C.option_source(),
                                         "scenario": scenario, "preinstall": preinstall}))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


METHODS_BLOCKED = ["POST", "PUT", "PATCH", "DELETE", "post", "Delete", " patch "]


@unittest.skipUnless(NODE, "node not installed")
class Fetch(unittest.TestCase):
    def test_get_passes(self):
        r = run("""
          const a=await fetch('/api/list');
          const b=await fetch('/api/list',{method:'GET'});
          const c=await fetch('/api/list',{method:'get'});
          const d=await fetch({url:'/api/list',method:'GET'});
          const e=await fetch(new URL('https://demo.example/x'));
          return [a,b,c,d,e];""")
        self.assertEqual(r["out"], ["real"] * 5)
        self.assertEqual(r["log"], ["fetch"] * 5)
        self.assertEqual((r["blocked"] or 0), 0)

    def test_writes_are_rejected_and_counted(self):
        for m in METHODS_BLOCKED:
            with self.subTest(m):
                r = run(f"""
                  try{{await fetch('/api/sessions/12/',{{method:{json.dumps(m)},body:'x'}});return 'sent';}}
                  catch(e){{return e instanceof TypeError&&e.message;}}""")
                self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
                self.assertEqual(r["log"], [])                                  # never reached the real fetch
                self.assertEqual(r["blocked"], 1)
                self.assertEqual(r["last"], f"{m.strip().upper()} /api/sessions/12/")

    def test_request_object_and_init_override(self):
        r = run("""
          const out=[];
          for(const args of [[{url:'/a',method:'POST'}],[{url:'/b',method:'GET'},{method:'PUT'}],[{url:'/c'},{method:'DELETE'}]]){
            try{await fetch(...args);out.push('sent');}catch(e){out.push('blocked');}}
          out.push(await fetch({url:'/d',method:'POST'},{method:'GET'}));       // init wins: GET
          return out;""")
        self.assertEqual(r["out"], ["blocked", "blocked", "blocked", "real"])
        self.assertEqual(r["blocked"], 3)

    def test_counter_increments_and_keeps_the_last(self):
        r = run("""
          for(const m of ['POST','PUT','PATCH'])try{await fetch('/api/'+m.toLowerCase(),{method:m});}catch(e){}
          await fetch('/api/read');
          return window.__kbBlocked;""")
        self.assertEqual((r["out"], r["blocked"], r["last"]), (3, 3, "PATCH /api/patch"))
        self.assertEqual(r["log"], ["fetch"])                                   # only the GET got through


@unittest.skipUnless(NODE, "node not installed")
class Xhr(unittest.TestCase):
    def test_get_passes_and_writes_throw(self):
        r = run("""
          const out=[];
          const go=(m,u)=>{const x=new XMLHttpRequest();x.open(m,u);try{out.push(x.send(null));}catch(e){out.push(e.message);}};
          go('GET','/api/list');go('get','/api/list');go('POST','/api/x/');go('PUT','/api/y/');go('PATCH','/z');go('DELETE','/d');
          return out;""")
        self.assertEqual(r["out"], ["sent", "sent"] + ["KB_WRITE_BLOCKED"] * 4)
        self.assertEqual(r["log"], ["xhr.open GET", "xhr.send", "xhr.open get", "xhr.send",
                                    "xhr.open POST", "xhr.open PUT", "xhr.open PATCH", "xhr.open DELETE"])
        self.assertEqual((r["blocked"], r["last"]), (4, "DELETE /d"))


@unittest.skipUnless(NODE, "node not installed")
class Beacon(unittest.TestCase):
    def test_send_beacon_returns_false(self):
        r = run("return [navigator.sendBeacon('/analytics/','x'),navigator.sendBeacon('/b')];")
        self.assertEqual(r["out"], [False, False])
        self.assertEqual((r["log"], r["blocked"], r["last"]), ([], 2, "POST /b"))


@unittest.skipUnless(NODE, "node not installed")
class Forms(unittest.TestCase):
    def test_get_form_passes_post_form_does_nothing(self):
        r = run("""
          const g=new Form('get'),p=new Form('post','/users/save/');
          return [g.submit(),p.submit(),g.requestSubmit(),p.requestSubmit(),new Form('dialog').submit(),new Form('').submit()];""")
        self.assertEqual(r["out"], ["sub", None, "rs", None, None, "sub"])      # method "" = get, as in a browser
        self.assertEqual(r["log"], ["form.submit", "form.requestSubmit", "form.submit"])
        self.assertEqual((r["blocked"], r["last"]), (3, "DIALOG /save/"))

    def test_submitter_formmethod_wins(self):
        r = run("""
          const g=new Form('get'),p=new Form('post');
          return [g.requestSubmit({formMethod:'post'}),p.requestSubmit({formMethod:'get'})];""")
        self.assertEqual(r["out"], [None, "rs"])
        self.assertEqual((r["log"], r["blocked"]), (["form.requestSubmit"], 1))

    def test_submit_event_listener(self):
        r = run("""
          const l=listeners[0];
          const ev=(m,s)=>({target:new Form(m,'/x/'),submitter:s||null,prevented:false,stopped:false,
            preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}});
          const res=[];
          for(const e of [ev('post'),ev('get'),ev('get',{formMethod:'post'}),ev('post',{formMethod:'get'}),ev('dialog')]){l.f(e);res.push(e.prevented&&e.stopped);}
          return [l.t,l.c,res];""")
        self.assertEqual(r["out"], ["submit", True, [True, False, True, False, True]])    # capture phase
        self.assertEqual((r["blocked"], r["listeners"]), (3, 1))


FRAME = r"""
const mkFrame=(origin)=>{const f={log:[],URL,
  fetch(i,o){f.log.push('fetch');return Promise.resolve('real');},
  XMLHttpRequest:class{open(m,u){f.log.push('xhr.open '+m);}send(){f.log.push('xhr.send');}},
  HTMLFormElement:class{submit(){f.log.push('form.submit');}},
  navigator:{sendBeacon(){f.log.push('beacon');return true;}},
  location:{href:origin+'/inner/'},listeners:[],
  document:{addEventListener(t,fn,c){f.listeners.push(t);},querySelectorAll:()=>[]}};
  f.window=f;return f;};
"""


@unittest.skipUnless(NODE, "node not installed")
class XhrWebSocketFrames(unittest.TestCase):
    def test_an_xhr_opened_before_the_install_is_blocked_on_send(self):
        r = run("""
          const x=new XMLHttpRequest();x.open('GET','/r');        // opened by page code before the guard ran
          eval(G);
          let msg='sent';try{x.send();}catch(e){msg=e.message;}
          const y=new XMLHttpRequest();y.open('GET','/r');y.send();   // opened after the install: a GET passes
          return msg;""", preinstall=False)
        self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
        self.assertEqual(r["log"], ["xhr.open GET", "xhr.open GET", "xhr.send"])      # only the second send ran
        self.assertEqual(r["blocked"], 1)

    def test_websocket_send_throws_and_counts(self):
        r = run("""
          window.WebSocket=class{constructor(u){this.url=u;}send(d){log.push('ws.send');}};
          eval(G);eval(G);
          let msg='sent';try{new WebSocket('wss://demo.example/live/x').send('hi');}catch(e){msg=e.message;}
          return [msg,WebSocket.prototype.send.__kb];""", preinstall=False)
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", 1])
        self.assertEqual((r["log"], r["blocked"], r["last"]), ([], 1, "WS /live/x"))

    def test_installs_into_same_origin_frames_and_skips_cross_origin(self):
        r = run(FRAME + """
          const a=mkFrame('https://demo.example'),b=mkFrame('https://demo.example');
          const cross={get document(){throw new Error('SecurityError');}};
          window.frames=[a,cross,b];window.frames.length=3;
          eval(G);
          const out=[];
          for(const f of [a,b]){try{await f.fetch('/w',{method:'POST'});out.push('sent');}catch(e){out.push(e.message);}
            const x=new f.XMLHttpRequest();x.open('PUT','/w');try{x.send();out.push('sent');}catch(e){out.push(e.message);}
            await f.fetch('/r');}
          return [out,a.__kbBlocked,b.__kbBlocked,a.log,window.__kbBlocked||0];""", preinstall=False)
        self.assertEqual(r["out"][0], ["KB_WRITE_BLOCKED"] * 4)
        self.assertEqual(r["out"][1:3], [2, 2])
        self.assertEqual(r["out"][3], ["xhr.open PUT", "fetch"])          # the POST fetch and the PUT send never ran
        self.assertEqual(r["out"][4], 0)                                   # each window counts its own writes

    def test_a_frame_added_later_is_hooked_by_the_observer_and_again_on_load(self):
        r = run(FRAME + """
          const seen=[];
          window.MutationObserver=class{constructor(cb){this.cb=cb;seen.push(this);}observe(t,o){this.t=t;this.o=o;}};
          eval(G);eval(G);                                                 // idempotent: one observer
          const child=mkFrame('https://demo.example');
          const handlers={};
          const iframe={tagName:'IFRAME',contentWindow:child,addEventListener(t,fn){handlers[t]=fn;}};
          const wrapper={tagName:'DIV',querySelectorAll:s=>s==='iframe'?[iframe]:[]};
          seen[0].cb([{addedNodes:[wrapper]}]);
          let first='sent';try{await child.fetch('/w',{method:'POST'});}catch(e){first=e.message;}
          const reload=mkFrame('https://demo.example');                    // the frame navigates: a fresh window
          iframe.contentWindow=reload;handlers.load();
          let second='sent';try{await reload.fetch('/w',{method:'DELETE'});}catch(e){second=e.message;}
          return [seen.length,seen[0].o,first,second,Object.keys(handlers)];""", preinstall=False)
        self.assertEqual(r["out"], [1, {"childList": True, "subtree": True}, "KB_WRITE_BLOCKED",
                                    "KB_WRITE_BLOCKED", ["load"]])


@unittest.skipUnless(NODE, "node not installed")
class Idempotent(unittest.TestCase):
    def test_reinstalling_wraps_nothing_twice(self):
        r = run("""
          const before=[fetch,XMLHttpRequest.prototype.open,XMLHttpRequest.prototype.send,navigator.sendBeacon,
                        Form.prototype.submit,Form.prototype.requestSubmit];
          try{await fetch('/w',{method:'POST'});}catch(e){}
          for(let i=0;i<3;i++)eval(G);
          const after=[fetch,XMLHttpRequest.prototype.open,XMLHttpRequest.prototype.send,navigator.sendBeacon,
                       Form.prototype.submit,Form.prototype.requestSubmit];
          const same=before.every((f,i)=>f===after[i]);
          const marks=after.every(f=>f.__kb===1);
          await fetch('/r');
          const x=new XMLHttpRequest();x.open('GET','/r');x.send();
          new Form('get').submit();
          return {same,marks,count:window.__kbBlocked};""")
        self.assertEqual(r["out"], {"same": True, "marks": True, "count": 1})        # the count survives re-installs
        self.assertEqual(r["log"], ["fetch", "xhr.open GET", "xhr.send", "form.submit"])   # each real call ran once
        self.assertEqual(r["listeners"], 1)

    def test_a_write_counts_once_after_reinstall(self):
        r = run("""eval(G);eval(G);try{await fetch('/w',{method:'DELETE'});}catch(e){} return window.__kbBlocked;""")
        self.assertEqual(r["out"], 1)

    def test_installs_into_a_fresh_window(self):
        r = run("return [fetch.__kb||null,Form.prototype.submit.__kb||null,window.__kbBlocked||null];", preinstall=False)
        self.assertEqual(r["out"][:2], [None, None])
        r = run("eval(G);return [fetch.__kb,Form.prototype.submit.__kb,window.__kbBlocked];", preinstall=False)
        self.assertEqual(r["out"], [1, 1, 0])

    def test_page_that_replaces_fetch_is_wrapped_again(self):
        r = run("""
          window.fetch=function(){return Promise.resolve('app');};     // an SPA swapping in its own fetch
          eval(G);
          try{await fetch('/w',{method:'POST'});return 'sent';}catch(e){return [e.message,await fetch('/r')];}""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "app"])


@unittest.skipUnless(NODE, "node not installed")
class InstalledByEveryCall(unittest.TestCase):
    def test_probe_installs_and_reports_the_count(self):
        r = run("""
          const f=(0,eval)(PROBE);
          const a={v:1,login:'/users/login/',portal:null,labels:[],side:'nav',allow:[],domain:'x.com'};
          const first=f(a);
          try{await fetch('/api/w',{method:'POST'});}catch(e){}
          const second=f(a);
          return [fetch.__kb,first.wb,second.wb,second.wl,first.h];""", preinstall=False)
        self.assertEqual(r["out"], [1, 0, 1, "POST /api/w", None])

    def test_kbclick_and_kbopt_install_even_when_they_throw(self):
        for name in ("CLICK", "OPT"):
            with self.subTest(name):
                args = {"text": "Nope", "side": "nav"} if name == "CLICK" else {"option": "Nope"}
                r = run(f"""
                  const f=(0,eval)({name});
                  try{{f({json.dumps(args)});}}catch(e){{}}
                  try{{await fetch('/api/w',{{method:'PUT'}});return 'sent';}}catch(e){{return e.message;}}""",
                        preinstall=False)
                self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
                self.assertEqual(r["blocked"], 1)

    def test_every_source_carries_the_guard_and_no_placeholder(self):
        g = C.guard_source()
        for name, src in (("probe", C.probe_source()), ("click", C.click_source()), ("opt", C.option_source())):
            with self.subTest(name):
                self.assertIn(g, src)
                self.assertNotIn("__GUARD__", src)
                self.assertTrue(src.startswith("(function(a){") and src.endswith("})"))
                self.assertNotIn("//", src.replace("javascript:", "").replace("(#|", "").replace("https://", ""))

    def test_the_guard_has_no_lift_switch(self):
        g = C.guard_source()
        self.assertNotIn("a.write", g)                       # write mode never touches the network guard
        self.assertIsNone(re.search(r"(?<![A-Za-z_])a\.", g))   # takes no arguments (no `a.<arg>` reads)


# ── 0.28.0: the one-shot allowance ────────────────────────────────────────────
# A richer harness: a tiny DOM (an open "Notify Participants?" dialog with buttons, a Save
# button behind it), fetch / XHR stubs that answer like a server (status 201, {"id": ...}),
# and the page scripts' own helpers. Used here and by test_kb_flow.py / test_kb_click_guard.py.
HARNESS_DOM = r"""
const vm=require('vm');const fs=require('fs');
const sc=JSON.parse(fs.readFileSync(0,'utf8'));
const log=[],listeners=[];
const ctx={URL,log,listeners,G:sc.guard,PROBE:sc.probe,CLICK:sc.click,OPT:sc.opt,DATA:sc.data||null,STORE:sc.store||{}};
ctx.window=ctx;
let clk=null;const RD=Date;ctx.Date=class extends RD{static now(){return clk?clk():RD.now();}};ctx.clock=f=>{clk=f;};
class El{
 constructor(o){this.tagName=(o.tag||'button').toUpperCase();this.innerText=o.text||'';this.textContent=this.innerText;
  this.attrs=o.attrs||{};this.className=this.attrs['class']||'';this.kids=[];this.parent=null;
  this.rect=o.hidden?{left:0,top:0,width:0,height:0,right:0,bottom:0}:{left:100,top:50,width:80,height:30,right:180,bottom:80};
  this.attributes=Object.keys(this.attrs).map(k=>({name:k,value:String(this.attrs[k])}));}
 getAttribute(n){return n in this.attrs?String(this.attrs[n]):null;}
 hasAttribute(n){return n in this.attrs;}
 getBoundingClientRect(){return this.rect;}
 closest(){return null;}
 contains(x){for(let p=x;p;p=p.parent)if(p===this)return true;return false;}
 querySelector(){return null;}
 querySelectorAll(s){const d=[];const walk=e=>e.kids.forEach(k=>{d.push(k);walk(k);});walk(this);
  return /button/.test(s)?d.filter(e=>/^(BUTTON|A|INPUT)$/.test(e.tagName)):[];}
 scrollIntoView(){ctx.cur=this;}
}
const spec=sc.dom||{};
const dialogs=[],buttons=[];
if(spec.dialog){const d=new El({tag:'div',text:spec.dialog.text,attrs:{role:'dialog'}});
 (spec.dialog.buttons||[]).forEach(t=>{const b=new El({text:t});b.parent=d;d.kids.push(b);buttons.push(b);});dialogs.push(d);}
(spec.outside||[]).forEach(t=>{buttons.push(new El({text:t}));});
ctx.location={href:'https://demo.example/app/',pathname:'/app/',search:'',origin:'https://demo.example'};
ctx.innerWidth=1470;ctx.innerHeight=802;ctx.devicePixelRatio=2;
ctx.fetch=function(i,o){log.push('fetch '+((o&&o.method)||(i&&i.method)||'GET')+' '+((i&&i.url)||i));
 return Promise.resolve({status:201,clone(){return {json(){return Promise.resolve({id:'S-77'});}};}});};
class XHR{open(m,u){this.m=m;this.u=u;this.l=[];}addEventListener(t,f){this.l.push(f);}
 send(){log.push('xhr '+this.m+' '+this.u);this.status=200;this.responseText='{"data":{"id":42}}';
  setTimeout(()=>this.l.forEach(f=>f()),0);return 'sent';}}
class Form{constructor(m,a){this.method=m;this.action=a||'/x/';}submit(){log.push('form.submit');}requestSubmit(){log.push('form.requestSubmit');}}
ctx.XMLHttpRequest=XHR;ctx.HTMLFormElement=Form;ctx.Form=Form;ctx.setTimeout=setTimeout;
ctx.navigator={sendBeacon(u,d){log.push('beacon');return true;}};
ctx.sessionStorage={getItem:k=>(sc.store||{})[k]||null};
ctx.getComputedStyle=()=>({cursor:'',backgroundColor:'rgba(0,0,0,0)'});
ctx.document={title:'',body:{innerText:(spec.dialog?spec.dialog.text:'')+' '+(spec.body||''),querySelectorAll:()=>[]},
 querySelectorAll(s){if(/\.modal\.show/.test(s)&&!/modal-title/.test(s))return dialogs;if(/^a,button/.test(s))return buttons;return [];},
 querySelector(s){return /data-v-app/.test(s)?{}:null;},addEventListener(t,f,c){listeners.push({t,f,c});},
 removeEventListener(t,f){const i=listeners.findIndex(l=>l.f===f);if(i>=0)listeners.splice(i,1);},
 elementFromPoint:()=>ctx.cur||null};
ctx.buttons=buttons;
ctx.fire=(target,trusted)=>listeners.filter(l=>l.t==='click').slice().forEach(l=>l.f({target,isTrusted:trusted!==false}));
vm.createContext(ctx);
const ARGS={v:1,login:'/users/login/',portal:null,labels:[],side:'nav',allow:[],domain:'medtricslab.com'};
ctx.ARGS=ARGS;
vm.runInContext(sc.guard,ctx);
(async()=>{
  let out;
  try{out=await vm.runInContext('(async()=>{'+sc.scenario+'})()',ctx);}catch(e){out={threw:String(e&&e.message||e)};}
  console.log(JSON.stringify({out,log,blocked:ctx.__kbBlocked||0,last:ctx.__kbLast,saved:ctx.__kbSaved||null,allowed:ctx.__kbAllowed||[],blog:ctx.__kbLog||[]}));
})();
"""

DIALOG = {"dialog": {"text": "Notify Participants? Send emails about this schedule? Yes, send notifications "
                             "No, don't send notifications Cancel",
                     "buttons": ["Yes, send notifications", "No, don't send notifications", "Cancel"]},
          "outside": ["Save"]}


def page_store():
    """What install_action() puts in sessionStorage: the stubs run these."""
    return {"kbp": C.probe_source(), "kbc": C.click_source(), "kbo": C.option_source()}


def run_dom(scenario, dom=DIALOG, data=None, store=None):
    r = subprocess.run([NODE, "-e", HARNESS_DOM], capture_output=True, text=True, timeout=60,
                       input=json.dumps({"guard": C.guard_source(), "probe": C.probe_source(),
                                         "click": C.click_source(), "opt": C.option_source(),
                                         "scenario": scenario, "dom": dom, "data": data, "store": store}))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# The allowance is armed the way the page script does it: the probe sees the dialog, kbclick
# checks the dialog's "No, don't send notifications" button and returns it, and the owner's real
# click lands on that button (`fire(window.cur)` - the element kbclick scrolled to). `arm(path,
# method, query)` does all three; `kb(...)` is kbclick without the click.
PRE = """
const p=(0,eval)(PROBE);const probe=()=>p(ARGS);const k=(0,eval)(CLICK);
const NO="No, don't send notifications";
const kb=(path,method,query,text)=>k({side:'nav',text:text||NO,write:['Save',NO],
  allow:{method:method||'POST',path:path||'/api/sched/',dlg:'Notify Participants?',query:query||''}});
const arm=(path,method,query)=>{probe();const r=kb(path,method,query);fire(window.cur);return r;};
const tryFetch=async(u,m)=>{try{const r=await fetch(u,{method:m||'POST'});return r.status;}catch(e){return e.message;}};
"""
ARM = PRE + "arm();"


@unittest.skipUnless(NODE, "node not installed")
class OneShotAllowance(unittest.TestCase):
    """Guard level: an armed allowance lets exactly ONE request through - the one with the armed
    METHOD and path, inside the TTL, same origin, fetch or XHR in the top window - and every
    other non-GET (a second identical one included) stays blocked and counted."""

    def test_exactly_one_request_passes_a_second_identical_one_is_blocked(self):
        r = run_dom(ARM + """
          const a=await fetch('/api/sched/',{method:'POST',body:'x'});
          let second;try{second=await fetch('/api/sched/',{method:'POST'});}catch(e){second=e.message;}
          let third;try{third=await fetch('/api/sched/',{method:'POST'});}catch(e){third=e.message;}
          return [a.status,second,third];""")
        self.assertEqual(r["out"], [201, "KB_WRITE_BLOCKED", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["log"], ["fetch POST /api/sched/"])                   # one request reached the network
        self.assertEqual((r["blocked"], r["last"]), (2, "POST /api/sched/"))     # the other two were counted
        self.assertEqual(r["allowed"], ["POST /api/sched/"])

    def test_response_status_and_json_id_are_captured(self):
        r = run_dom(ARM + """
          await fetch('/api/sched/',{method:'POST'});
          await new Promise(res=>setTimeout(res,5));
          return window.__kbSaved;""")
        self.assertEqual(r["out"], {"m": "POST", "p": "/api/sched/", "s": 201, "id": "S-77"})
        self.assertEqual(r["saved"], r["out"])

    def test_xhr_is_allowed_once_too_and_a_data_id_is_read(self):
        r = run_dom(ARM + """
          const go=()=>{const x=new XMLHttpRequest();x.open('POST','/api/sched/');try{return x.send('b');}catch(e){return e.message;}};
          const out=[go(),go()];
          await new Promise(res=>setTimeout(res,5));
          return [out,window.__kbSaved];""")
        self.assertEqual(r["out"][0], ["sent", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["out"][1], {"m": "POST", "p": "/api/sched/", "s": 200, "id": "42"})
        self.assertEqual((r["blocked"], r["allowed"]), (1, ["POST /api/sched/"]))

    def test_other_path_method_origin_query_are_blocked_and_do_not_use_the_allowance_up(self):
        r = run_dom(ARM + """
          const bad=[['/api/other/','POST'],['/api/sched/','PUT'],['/api/sched/','DELETE'],['/api/sched','POST'],
                     ['/api/sched/extra/','POST'],['https://evil.example/api/sched/','POST'],['//evil.example/api/sched/','POST'],
                     ['/api/sched/?action=delete_all','POST']];
          const res=[];
          for(const [u,m] of bad){try{await fetch(u,{method:m});res.push('sent');}catch(e){res.push('blocked');}}
          const ok=await fetch('/api/sched/',{method:'post'});          // the one that was armed still passes
          return [res,ok.status];""")
        self.assertEqual(r["out"], [["blocked"] * 8, 201])
        self.assertEqual(r["blocked"], 8)
        self.assertEqual(r["log"], ["fetch post /api/sched/"])

    def test_after_the_ttl_it_is_blocked(self):
        r = run_dom(PRE + """
          let t=Date.now();clock(()=>t);
          arm();                                                         // the click is at t
          t+=9999;const a=await tryFetch('/api/sched/');                 // inside 10 s of the click: passes
          arm();
          t+=10001;const b=await tryFetch('/api/sched/');                // past 10 s: blocked
          return [a,b];""")
        self.assertEqual(r["out"], [201, "KB_WRITE_BLOCKED"])
        self.assertEqual(r["blocked"], 1)

    def test_the_window_opens_at_the_click_not_at_the_kbclick_call(self):
        r = run_dom(PRE + """
          let t=Date.now();clock(()=>t);
          probe();kb();                                                  // kbclick at t, no click yet
          t+=5000;const before=await tryFetch('/api/sched/');            // not armed before the click
          t+=25000;fire(window.cur);                                     // the click, 30 s after kbclick
          t+=9000;const inside=await tryFetch('/api/sched/');            // 9 s after the click: passes
          probe();kb();t+=61000;fire(window.cur);                        // a click after the pending window: nothing
          const late=await tryFetch('/api/sched/');
          probe();kb();fire(window.cur);t+=10001;const expired=await tryFetch('/api/sched/');
          return [before,inside,late,expired];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", 201, "KB_WRITE_BLOCKED", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["allowed"], ["POST /api/sched/"])

    def test_nothing_armed_means_everything_is_blocked(self):
        r = run_dom("""
          const out=[];
          for(const m of ['POST','PUT','PATCH','DELETE'])try{await fetch('/api/sched/',{method:m});out.push('sent');}catch(e){out.push('blocked');}
          return out;""")
        self.assertEqual(r["out"], ["blocked"] * 4)
        self.assertEqual((r["blocked"], r["allowed"], r["saved"]), (4, [], None))

    def test_forms_beacons_and_websockets_are_never_allowed(self):
        r = run_dom(ARM + """
          const f=new Form('post','/api/sched/');f.submit();f.requestSubmit();
          const b=navigator.sendBeacon('/api/sched/','x');
          return [b,window.__kbAllowed||[]];""")
        self.assertEqual(r["out"], [False, []])
        self.assertEqual(r["log"], [])
        self.assertEqual((r["blocked"], r["allowed"]), (3, []))

    def test_a_frame_window_never_uses_the_allowance(self):
        r = run_dom(PRE + """
          probe();window.top={};                                         // this window is not the top window
          let a;try{a=kb();}catch(e){a=e.message;}
          fire(window.cur);
          return [a,await tryFetch('/api/sched/')];""")
        self.assertEqual(r["out"], ["KB_CLICK_REFUSED:allow_not_top", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["allowed"], [])

    def test_a_page_written_kbA_is_ignored(self):
        # the old switch: the page (or anything) setting window.__kbA must open nothing
        r = run_dom("""
          const go=async(u,m)=>{try{await fetch(u,{method:m});return 'sent';}catch(e){return e.message;}};
          window.__kbA={m:'DELETE',p:'/api/x',t:1e15,used:false};
          const a=await go('/api/x','DELETE');
          window.__kbA={m:'POST',p:'/api/sched/',t:Date.now()+10000,used:false};
          const b=await go('/api/sched/','POST');
          const x=new XMLHttpRequest();x.open('DELETE','/api/x');let c;try{c=x.send();}catch(e){c=e.message;}
          return [a,b,c];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED"] * 3)
        self.assertEqual((len(r["blog"]), r["allowed"]), (3, []))
        # ... and with a real allowance armed, a DELETE written into __kbA still does not pass
        r = run_dom(ARM + """
          window.__kbA={m:'DELETE',p:'/api/x',t:1e15,used:false};
          const a=await tryFetch('/api/x','DELETE');
          return [a,await tryFetch('/api/sched/')];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", 201])

    def test_a_yes_button_click_does_not_arm_and_cancels_the_pending_arming(self):
        r = run_dom(PRE + """
          probe();kb();                                                  // kbclick checked the "No" button
          fire(buttons[0]);                                              // the click lands on "Yes, send notifications"
          const yes=await tryFetch('/api/sched/');
          fire(buttons[1]);                                              // a later click on the checked button: too late
          return [yes,await tryFetch('/api/sched/')];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["allowed"], [])

    def test_only_a_click_on_or_inside_the_checked_element_arms(self):
        r = run_dom(PRE + """
          probe();kb();
          const inner={parent:window.cur,tagName:'SPAN'};                // a child of the checked button
          fire(inner);
          const a=await tryFetch('/api/sched/');
          probe();kb();fire(window.cur,false);                           // a page-made (untrusted) click is ignored ...
          const b=await tryFetch('/api/sched/');
          fire(window.cur);                                              // ... the owner's real click arms
          const c=await tryFetch('/api/sched/');
          return [a,b,c];""")
        self.assertEqual(r["out"], [201, "KB_WRITE_BLOCKED", 201])        # a click inside the element arms; a page-made one does not

    def test_the_learned_query_is_part_of_the_allowance(self):
        r = run_dom(PRE + """
          arm('/api/sched/','POST','?v=2');
          const a=await tryFetch('/api/sched/?v=3');                     // another query
          const b=await tryFetch('/api/sched/?action=delete_all');
          const c=await tryFetch('/api/sched/?v=2');                     // exactly the learned one
          return [a,b,c];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "KB_WRITE_BLOCKED", 201])

    def test_every_blocked_write_is_logged_with_a_timestamp_and_the_log_is_bounded(self):
        r = run_dom(PRE + """
          for(let i=0;i<25;i++)await tryFetch('/api/w'+i+'?q='+i,'POST');
          const x=new XMLHttpRequest();x.open('PUT','/api/x');try{x.send();}catch(e){}
          const l=probe().bl;
          return [l.length,l[0].n,l[l.length-1],probe().wq];""")
        n, first, last, wq = r["out"]
        self.assertEqual((n, first), (20, 7))                              # 26 blocked, the last 20 kept
        self.assertEqual((last["m"], last["p"], last["s"], last["n"]), ("PUT", "/api/x", "xhr", 26))
        self.assertTrue(isinstance(last["t"], (int, float)) and last["t"] > 1e12)
        self.assertEqual(wq, "")
        r = run_dom(PRE + "await tryFetch('/api/w?q=7');return [probe().wq,probe().bl[0].q];")
        self.assertEqual(r["out"], ["?q=7", "?q=7"])

    def test_python_mirror_agrees_with_the_guard(self):
        origin, base = "https://demo.example", "https://demo.example/app/"
        cases = [("POST", "/api/sched/", "fetch", 0), ("post", "/api/sched/?x=1", "fetch", 0),
                 ("PUT", "/api/sched/", "fetch", 0), ("POST", "/api/other/", "fetch", 0),
                 ("POST", "https://evil.example/api/sched/", "fetch", 0), ("POST", "https://demo.example/api/sched/", "fetch", 0),
                 ("POST", "//evil.example/api/sched/", "fetch", 0), ("POST", "//demo.example/api/sched/", "fetch", 0),
                 ("POST", "https:\\\\evil.example\\api\\sched\\", "fetch", 0),
                 ("POST", "/api/sched/?action=delete_all", "fetch", 0), ("POST", "/api/sched/?v=2", "fetch", 0),
                 ("POST", "/api/y/../sched/", "fetch", 0), ("POST", "sched/", "fetch", 0), ("POST", "../app/../api/sched/", "fetch", 0),
                 ("POST", "/api/sched/", "fetch", 10001), ("POST", "/api/sched/", "fetch", 10000),
                 ("DELETE", "/api/sched/", "fetch", 0), ("POST", "/api/sched", "fetch", 0),
                 ("POST", "/api/sched/#frag", "fetch", 0), ("POST", "/api/sched/?", "fetch", 0)]
        scen = PRE + "const out=[];for(const [m,u,s,dt] of " + json.dumps(
            [[m, u, s, dt] for m, u, s, dt in cases]) + """){
          const t0=Date.now();clock(()=>t0);
          arm('/api/sched/','POST','?v=2');clock(()=>t0+dt);
          out.push(await tryFetch(u,m)===201);
          clock(null);}
          return out;"""
        got = run_dom(scen)["out"]
        want = [C.allowance_passes({"m": "POST", "p": "/api/sched/", "q": "?v=2", "t": 10000, "used": False},
                                   m, u, origin, dt, s, base=base)[0] for m, u, s, dt in cases]
        self.assertEqual(got, want)
        self.assertEqual(want, [True, False, False, False, False, True, False, True, False, False, True, True,
                                False, True, False, True, False, False, True, True])
        # one-shot in the mirror too
        armed = {"m": "POST", "p": "/a/", "t": 10, "used": False}
        ok1, armed = C.allowance_passes(armed, "POST", "/a/", origin, 1)
        ok2, armed = C.allowance_passes(armed, "POST", "/a/", origin, 2)
        self.assertEqual((ok1, ok2), (True, False))

    def test_python_mirror_rejects_other_origins_and_foreign_queries(self):
        a = {"m": "POST", "p": "/api/sched/", "q": "", "t": 10, "used": False}
        origin = "https://demo.example"
        for url in ("//evil.test/api/sched/", "https://evil.test/api/sched/", "http://demo.example/api/sched/",
                    "https://demo.example:8443/api/sched/", "/api/sched/?action=delete_all"):
            with self.subTest(url):
                self.assertFalse(C.allowance_passes(a, "POST", url, origin, 1)[0])
        for url in ("/api/sched/", "https://demo.example/api/sched/", "//demo.example/api/sched/", "/api/sched/?"):
            with self.subTest(url):
                self.assertTrue(C.allowance_passes(a, "POST", url, origin, 1)[0])


@unittest.skipUnless(NODE, "node not installed")
class ArmedOnlyByKbclick(unittest.TestCase):
    """The allowance is armed by kbclick - never by anything else - and only for a click on a
    button of the open dialog, after the probe saw that dialog, never on Save."""
    ALLOW = {"method": "POST", "path": "/api/sched/", "dlg": "Notify Participants?"}
    CLICK_CHOOSE = ("const k=(0,eval)(CLICK);"
                    "const go=(text,allow)=>{try{return k({side:'nav',text,write:['Save',\"No, don't send notifications\"],allow});}"
                    "catch(e){return {err:e.message};}};")
    PROBE = "const p=(0,eval)(PROBE);const probe=()=>p(ARGS);"

    def test_the_probe_records_what_the_dialog_says(self):
        r = run_dom(self.PROBE + "const x=probe();return [x.dlg,x.al,x.saved,window.__kbSeen];")
        dlg, al, saved, seen = r["out"]
        self.assertEqual(dlg["bt"], ["Yes, send notifications", "No, don't send notifications", "Cancel"])
        self.assertIn("notify participants?", seen)
        self.assertEqual((al, saved), ([], None))

    def test_arming_needs_the_dialog_seen_by_the_probe_first(self):
        r = run_dom(self.CLICK_CHOOSE + "const a=go(\"No, don't send notifications\"," + json.dumps(self.ALLOW) +
                    ");fire(window.cur);return [a,await fetch('/api/sched/',{method:'POST'}).then(()=>'sent',e=>e.message)];")
        self.assertEqual(r["out"], [{"err": "KB_CLICK_REFUSED:allow_dialog_not_seen"}, "KB_WRITE_BLOCKED"])
        r = run_dom(self.CLICK_CHOOSE + "let x;try{await fetch('/api/sched/',{method:'POST'});x='sent';}catch(e){x='blocked';}return x;")
        self.assertEqual(r["out"], "blocked")

    def test_probe_then_kbclick_arms_then_one_request_passes(self):
        r = run_dom(self.PROBE + self.CLICK_CHOOSE + """
          probe();
          const a=go("No, don't send notifications",""" + json.dumps(self.ALLOW) + """);
          let early;try{await fetch('/api/sched/',{method:'POST'});early='sent';}catch(e){early='blocked';}   // kbclick alone arms nothing
          fire(window.cur);                                              // the real click on the checked button
          const one=await fetch('/api/sched/',{method:'POST'});
          let two;try{await fetch('/api/sched/',{method:'POST'});two='sent';}catch(e){two='blocked';}
          let three;try{await fetch('/api/other/',{method:'POST'});three='sent';}catch(e){three='blocked';}
          await new Promise(res=>setTimeout(res,5));
          const after=probe();
          return [a.ok,a.armed,one.status,two,three,after.al,after.saved,after.wb,after.wl,after.wk,early];""")
        ok, armed, status, two, three, al, saved, wb, wl, wk, early = r["out"]
        self.assertEqual(early, "blocked")
        self.assertEqual((ok, armed, status, two, three), (True, "POST /api/sched/", 201, "blocked", "blocked"))
        self.assertEqual(al, ["POST /api/sched/"])
        self.assertEqual(saved, {"m": "POST", "p": "/api/sched/", "s": 201, "id": "S-77"})
        self.assertEqual((wb, wl, wk), (3, "POST /api/other/", "fetch"))        # the early one, the second + the third were blocked and counted
        self.assertEqual(r["log"], ["fetch POST /api/sched/"])

    def test_save_never_gets_an_allowance(self):
        # Save as a dialog button (the worst case) and Save outside the dialog
        for dom, why in (({"dialog": {"text": "Notify Participants? Save", "buttons": ["Save"]}}, "allow_on_save"),
                         (DIALOG, "KB_CLICK_NOTFOUND:Save")):          # outside the open dialog: write mode never finds it
            with self.subTest(why):
                r = run_dom(self.PROBE + self.CLICK_CHOOSE + "probe();const a=go('Save'," + json.dumps(self.ALLOW) +
                            ");let x;try{await fetch('/api/sched/',{method:'POST'});x='sent';}catch(e){x='blocked';}"
                            "fire(window.cur);return [a,x];", dom)
                err = why if why.startswith("KB_CLICK_NOTFOUND") else f"KB_CLICK_REFUSED:{why}"
                self.assertEqual(r["out"], [{"err": err}, "blocked"])
        # a plain Save click in write mode (no allow) arms nothing either
        r = run_dom(self.PROBE + self.CLICK_CHOOSE + "probe();const a=go('Save');"
                    "fire(window.cur);let x;try{await fetch('/api/sched/',{method:'POST'});x='sent';}catch(e){x='blocked';}return [a.ok,x];",
                    {"dialog": {"text": "Notify Participants? Save", "buttons": ["Save", "Cancel"]}})
        self.assertEqual(r["out"], [True, "blocked"])

    def test_bad_requests_in_allow_are_refused(self):
        for allow, why in (({**ALLOW_DEFAULT, "method": "DELETE"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "method": "GET"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "path": "https://evil.example/x"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "path": "/a/?x=1"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "path": ""}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "dlg": ""}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "query": "x=1"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "query": "?a b"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "method": "get"}, "allow_bad_request"),
                           ({**ALLOW_DEFAULT, "dlg": "Some other dialog"}, "allow_dialog_not_seen")):
            with self.subTest(allow):
                r = run_dom(self.PROBE + self.CLICK_CHOOSE + "probe();const a=go(\"No, don't send notifications\"," +
                            json.dumps(allow) + ");fire(window.cur);return [a,await fetch('/api/sched/',{method:'POST'})"
                            ".then(()=>'sent',e=>e.message)];")
                self.assertEqual(r["out"], [{"err": f"KB_CLICK_REFUSED:{why}"}, "KB_WRITE_BLOCKED"])

    def test_the_next_kbclick_or_kbopt_without_allow_disarms(self):
        r = run_dom(self.PROBE + self.CLICK_CHOOSE + """
          probe();go("No, don't send notifications",""" + json.dumps(self.ALLOW) + """);
          go('Save');                                                    // any later kbclick without `allow` disarms
          fire(window.cur);
          let x;try{await fetch('/api/sched/',{method:'POST'});x='sent';}catch(e){x='blocked';}
          return x;""")
        self.assertEqual(r["out"], "blocked")
        r = run_dom(self.PROBE + self.CLICK_CHOOSE + """
          probe();go("No, don't send notifications",""" + json.dumps(self.ALLOW) + """);
          fire(window.cur);                                              // armed ...
          try{(0,eval)(OPT)({option:'x'});}catch(e){}                    // ... a kbopt call kills it
          let x;try{await fetch('/api/sched/',{method:'POST'});x='sent';}catch(e){x='blocked';}
          return x;""")
        self.assertEqual(r["out"], "blocked")

    def test_a_probe_between_arming_and_the_request_keeps_it(self):
        r = run_dom(self.PROBE + self.CLICK_CHOOSE + """
          probe();go("No, don't send notifications",""" + json.dumps(self.ALLOW) + """);
          fire(window.cur);probe();probe();
          const one=await fetch('/api/sched/',{method:'POST'});return one.status;""")
        self.assertEqual(r["out"], 201)

    def test_the_python_refusal_mirror_agrees(self):
        a = dict(self.ALLOW)
        seen = "notify participants? send emails"
        text = "Notify Participants? Send emails"
        cases = [(a, "No, don't send notifications", True, text, seen, "")]
        cases += [({**a, "method": "DELETE"}, "No", True, text, seen, "allow_bad_request"),
                  ({**a, "path": "/a/?x"}, "No", True, text, seen, "allow_bad_request"),
                  (a, "No", True, text, "", "allow_dialog_not_seen"), (a, "No", True, "", seen, "allow_no_dialog"),
                  (a, "No", False, text, seen, "allow_outside_dialog"), (a, "Save", True, text, seen, "allow_on_save"),
                  (a, "Create", True, text, seen, "allow_on_save"), (a, "No", True, text, seen, ""),
                  (a, "No", True, text, seen, "")]
        for allow, el, inside, dtext, sn, want in cases:
            self.assertEqual(C.allow_refusal(allow, el, inside, dtext, sn), want, (allow, el))
        self.assertEqual(C.allow_refusal(a, "No", True, text, seen, top=False), "allow_not_top")


ALLOW_DEFAULT = ArmedOnlyByKbclick.ALLOW


@unittest.skipUnless(NODE, "node not installed")
class MirrorAgrees(unittest.TestCase):
    def test_request_rule(self):
        cases = [(None, None), ("GET", None), ("get", None), (" Get ", None), ("POST", None), ("put", None),
                 ("PATCH", None), ("DELETE", None), ("HEAD", None), ("OPTIONS", None), (None, "POST"),
                 (None, "GET"), ("GET", "POST"), ("POST", "GET"), ("", "DELETE")]
        scen = "const out=[];for(const [m,rq] of " + json.dumps(cases) + """){
          const i=rq?{url:'/q',method:rq}:'/q';const o=m?{method:m}:undefined;
          try{await fetch(i,o);out.push(false);}catch(e){out.push(true);}}
          return out;"""
        got = run(scen)["out"]
        self.assertEqual(got, [C.request_blocked(m, rq) for m, rq in cases])
        self.assertEqual([C.request_blocked(m) for m in ("GET", "get", "POST", "PUT", "PATCH", "DELETE", "HEAD")],
                         [False, False, True, True, True, True, True])        # strictly GET

    def test_form_rule(self):
        cases = [("get", None), ("post", None), ("GET", None), ("dialog", None), ("", None), ("get", "post"),
                 ("post", "get"), ("post", ""), ("get", "dialog")]
        scen = "const out=[];for(const [m,s] of " + json.dumps(cases) + """){
          const before=window.__kbBlocked||0;
          new Form(m).requestSubmit(s?{formMethod:s}:undefined);
          out.push((window.__kbBlocked||0)>before);}
          return out;"""
        got = run(scen)["out"]
        self.assertEqual(got, [C.form_blocked(m, s) for m, s in cases])


# ── hostile same-page code (0.28.1): nothing readable holds a raw function or a switch ─────
@unittest.skipUnless(NODE, "node not installed")
class HostilePageCode(unittest.TestCase):
    """What an in-page guard CAN promise: the app's normal behaviour and an agent's mistakes
    cannot reach the raw fetch / XHR.send, rewrite an XHR's recorded method / URL, move the
    allowance's clock or generation, or arm / disarm without the token. (A page that is
    deliberately hostile can still do other things - see SKILL.md Limits.)"""

    def test_no_readable_property_holds_a_raw_function(self):
        r = run_dom(ARM + """
          const X=XMLHttpRequest.prototype;const seen=[];
          for(const o of [window.fetch,X.send,X.open]){
            for(const k of Object.getOwnPropertyNames(o)){const d=Object.getOwnPropertyDescriptor(o,k);
              if(typeof d.value==='function')seen.push(k);}}
          let a='none',b='none';
          try{window.fetch.__kbRaw.call(window,'/api/x',{method:'DELETE'});a='sent';}catch(e){a='threw';}
          try{X.send.__kbRaw.call(Object.assign(new XMLHttpRequest(),{m:'DELETE',u:'/x'}));b='sent';}catch(e){b='threw';}
          return [seen,a,b,typeof window.fetch.__kbRaw,typeof X.send.__kbRaw];""")
        seen, a, b, ta, tb = r["out"]
        self.assertEqual(sorted(set(seen)), ["__kbA"])            # the arm capability, and nothing else callable
        self.assertEqual((a, b, ta, tb), ("threw", "threw", "undefined", "undefined"))
        self.assertEqual(r["log"], [])

    def test_the_capability_needs_the_token_and_cannot_be_used_to_disarm_without_it(self):
        r = run_dom(ARM + """
          const f=window.fetch.__kbA;
          const a=f('guess',{el:window.cur,mt:'DELETE',pth:'/api/x',qs:'',ttl:1e9});
          const b=f('guess',null);                                   // a wrong token cannot disarm either
          const c=await tryFetch('/api/x','DELETE');
          const d=await tryFetch('/api/sched/');
          return [a,b,c,d];""")
        self.assertEqual(r["out"], [False, False, "KB_WRITE_BLOCKED", 201])

    def test_the_xhr_method_and_url_cannot_be_rewritten_after_open(self):
        r = run_dom(ARM + """
          const go=(m,u,fix)=>{const x=new XMLHttpRequest();x.open(m,u);fix(x);try{x.send();return 'sent';}catch(e){return e.message;}};
          const a=go('POST','/api/other/',x=>{x.__kbm='GET';});     // the old field: no longer consulted
          const b=go('DELETE','/api/other/',x=>{x.__kbm='GET';x.__kbu='/api/sched/';});
          const c=go('POST','/api/other/',x=>{x.__kbu='/api/sched/';});   // must not burn the allowance
          const d=go('POST','/api/sched/',()=>{});                        // the armed request is still there
          return [a,b,c,d];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED"] * 3 + ["sent"])
        self.assertEqual(r["log"], ["xhr POST /api/sched/"])

    def test_the_clock_and_the_generation_are_not_page_visible(self):
        r = run_dom(PRE + """
          let t=Date.now();clock(()=>t);
          arm();
          t+=10001;                                                  // past the 10 s window ...
          Date.now=()=>t-20000;                                      // ... the page rewinds the clock it can see
          const late=await tryFetch('/api/sched/');
          window.__kbGen=0;window.__kbGen=1;
          return [late,typeof window.__kbGen];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "number"])    # blocked; and writing the old global opens nothing
        r = run_dom(PRE + """
          arm();window.__kbGen=12345;
          return [typeof window.__kbGen,await tryFetch('/api/sched/')];""")
        self.assertEqual(r["out"], ["number", 201])                   # the page's own write is its own variable
        r = run_dom("return typeof window.__kbGen;")
        self.assertEqual(r["out"], "undefined")                       # the guard / kbclick never create it

    def test_a_later_kbclick_or_kbopt_disarms_through_the_capability(self):
        r = run_dom(PRE + """
          arm();                                                     // armed and clicked
          try{(0,eval)(OPT)({option:'nothing'});}catch(e){}          // kbopt starts by disarming
          const a=await tryFetch('/api/sched/');
          arm();
          try{k({side:'nav',text:NO,write:['Save',NO]});}catch(e){}  // a kbclick without allow disarms too
          const b=await tryFetch('/api/sched/');
          return [a,b];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "KB_WRITE_BLOCKED"])
        self.assertEqual(r["allowed"], [])

    def test_a_getter_that_changes_its_answer_is_read_once(self):
        r = run_dom("""
          let n=0;const o={get method(){return n++?'DELETE':'GET';}};
          await fetch('/api/x',o);                                   // read once: GET, and that is what is sent
          let m=0;const q={get url(){return m++?'/api/evil':'/api/ok';},method:'GET'};
          await fetch(q);
          return [n,m];""")
        self.assertEqual(r["log"], ["fetch GET /api/x", "fetch GET /api/ok"])
        self.assertEqual(r["out"], [1, 1])
        self.assertEqual(r["blocked"], 0)

    def test_the_url_global_cannot_be_swapped_for_the_allowance(self):
        r = run_dom(ARM + """
          window.URL=function(){return {origin:location.origin,pathname:'/api/sched/',search:''};};
          return [await tryFetch('/api/evil/'),await tryFetch('/api/sched/')];""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", 201])


@unittest.skipUnless(NODE, "node not installed")
class Parses(unittest.TestCase):
    def check(self, src):
        r = subprocess.run([NODE, "--check", "-"], input=src, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guard_and_every_source(self):
        self.check(C.guard_source())
        self.check(C.fields_source())
        self.check(C.install_action()["text"])
        self.check("(" + C.probe_source() + ")")
        self.check("(" + C.click_source() + ")")
        self.check("(" + C.option_source() + ")")
        self.check(C.route_check_source(["/a/"], "/users/login/").replace("await ", "(async()=>{await ", 1) + "})()")

    def test_install_stores_all_three_functions(self):
        t = C.install_action()["text"]
        for key in ("kbp", "kbc", "kbo"):
            self.assertIn(f"sessionStorage.setItem('{key}'", t)




class RunToken(unittest.TestCase):
    """plan-calls and kb_data brief are separate processes driving the same tab:
    both must bake the same arm token, read from <shots>/.arm_token."""

    def test_one_token_per_run_folder(self):
        import tempfile
        saved = C.ARM_TOKEN
        self.addCleanup(setattr, C, "ARM_TOKEN", saved)
        with tempfile.TemporaryDirectory() as d:
            t1 = C.use_run_token(d)
            C.ARM_TOKEN = "0" * 32                        # a different process's random token
            t2 = C.use_run_token(d)
            self.assertEqual(t1, t2)
            self.assertIn(t1, C.guard_source())
            self.assertRegex(t1, r"^[0-9a-f]{32}$")
        with tempfile.TemporaryDirectory() as d2:
            self.assertNotEqual(C.use_run_token(d2), t1)  # another run gets its own


if __name__ == "__main__":
    unittest.main()
